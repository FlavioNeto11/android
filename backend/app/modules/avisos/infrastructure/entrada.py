"""A conversa de volta pelo Telegram (item 28.15, ADR-071): o chat do dono fala com a Central como no painel.

O laço:
1. só no líder da trava `avisos`, e só com `avisos.enabled` + `avisos.entrada.enabled`;
2. `getUpdates` com o offset do banco (`MAX(update_id) + 1`), em long-poll;
3. grava cada update ANTES de tratar. A de outro chat, a longa demais e a que parece credencial são gravadas SEM o
   texto e não são tratadas;
4. trata as `recebida` por regra fixa (`application/entrada.rotear`), com o operador `telegram:dono` no ContextVar
   da sessão. `quem()` e `autor_do_gesto()` o leem, então a auditoria e o sinal contam como gesto de PESSOA;
5. conta na thread o desfecho das execuções que a conversa criou.

Toda ação passa por `PortasDaCentral`, que chama os MESMOS serviços das rotas do painel: a prévia de alvos é
obrigatória (botão Executar, decisão (c)), e approval_required, o pré-voo, a rede e os tetos valem iguais. Nada aqui
chama IA.

409 no `getUpdates` é outro consumidor do bot (a caixa provisória da orquestradora até a troca): a entrada espera
`espera_conflito_s` e vira problema na saúde, sem disputar.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Config
from app.models import Problem
from app.modules.avisos.adapters.telegram import CanalTelegram, ConflitoDeConsumidor
from app.modules.avisos.application.entrada import (
    AJUDA,
    Intencao,
    casar_ref,
    rotear,
    texto_para_o_extrator,
)
from app.modules.avisos.application.entrega import FalhaDeEnvio
from app.modules.avisos.infrastructure.entrada_sql import MensagensDoTelegram
from app.security.sessions import OPERADOR
from app.taskqueue.travas import AVISOS

log = logging.getLogger("poc.avisos.entrada")

#: Quem age pela conversa, na auditoria (decisão (b)): legível e sem o chat_id.
OPERADOR_DO_TELEGRAM = "telegram:dono"
RESPOSTA_CREDENCIAL = ("Isso parece senha ou código: não guardei e não repassei. Senha fica na conta da persona; "
                       "grave pelo painel.")
RESPOSTA_LONGA = "Mensagem longa demais para a conversa (limite de {n} caracteres): não guardei. Use o painel."
#: Só dígitos (com espaço ou hífen entre eles), de 4 a 8: o formato de um código de verificação.
_CODIGO = re.compile(r"[\s-]*(?:\d[\s-]?){4,8}[\s-]*")
#: Pausa entre voltas quando a entrada está desligada ou sem liderança (o long-poll é a espera de verdade).
OCIOSO_S = 15.0
Linha = dict[str, object]


class RecusaDaCentral(Exception):
    """O serviço do painel recusou (o pré-voo, a aprovação já decidida, a execução que não espera mais resposta). A
    mensagem é a mesma que o painel mostraria, e volta à conversa como texto."""


@dataclass(frozen=True)
class Previa:
    """A prévia de alvos do painel, resumida para uma linha e para o botão Executar."""

    alvos: list[dict[str, object]]
    perguntas: list[str]
    comando: str
    avisos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pendencia:
    tipo: str          # 'aprovacao' | 'pergunta'
    ident: str
    resumo: str


class PortasDaCentral(Protocol):
    """O que a conversa pode fazer, pelos MESMOS serviços das rotas do painel."""

    def status(self) -> str: ...
    def pendencias(self) -> list[Pendencia]: ...
    def aprovacoes_pendentes(self) -> list[str]: ...
    def execucoes_esperando(self) -> list[str]: ...
    def decidir(self, approval_id: str, verbo: str) -> str: ...
    def responder(self, run_id: str, texto: str) -> tuple[str, str]: ...
    def previa(self, texto: str, instance_ids: list[str] | None = None) -> Previa: ...
    def criar(self, texto: str, alvos: list[dict[str, object]], chave: str) -> tuple[str, str]: ...
    def online(self) -> list[str]: ...
    def desfecho(self, run_id: str) -> str | None: ...


def parece_codigo(texto: str) -> bool:
    return bool(_CODIGO.fullmatch(texto))


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _mapa(v: object) -> Mapping[str, object] | None:
    return v if isinstance(v, Mapping) else None


def _json(bruto: object) -> dict[str, object]:
    if not isinstance(bruto, str) or not bruto:
        return {}
    try:
        v = json.loads(bruto)
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


@dataclass(frozen=True)
class _Update:
    update_id: int
    tipo: str                          # 'mensagem' | 'botao' | 'outro'
    chat: str
    texto: str
    message_id: int | None
    responde_a: int | None
    callback_id: str | None


def _ler_update(u: Mapping[str, object]) -> _Update | None:
    update_id = _int(u.get("update_id"))
    if update_id is None:
        return None
    msg, cb = _mapa(u.get("message")), _mapa(u.get("callback_query"))
    tipo = "mensagem" if msg is not None else "botao" if cb is not None else "outro"
    if cb is not None:
        msg = _mapa(cb.get("message"))      # no botão, a mensagem do bot que levava o teclado
    chat = _mapa(msg.get("chat")) if msg is not None else None
    texto = (cb.get("data") if cb is not None else msg.get("text") if msg is not None else None) or ""
    responde = _mapa(msg.get("reply_to_message")) if msg is not None and cb is None else None
    return _Update(update_id=update_id, tipo=tipo, chat=str(chat.get("id")) if chat and chat.get("id") is not None else "",
                   texto=str(texto), message_id=_int(msg.get("message_id")) if msg is not None else None,
                   responde_a=_int(responde.get("message_id")) if responde is not None else None,
                   callback_id=str(cb.get("id")) if cb is not None and cb.get("id") is not None else None)


class ServicoDeEntrada:
    def __init__(self, cfg: Config, repo: MensagensDoTelegram, portas: PortasDaCentral, *,
                 lider: Callable[[str], int | None], recusa: Callable[[str], bool], redigir: Callable[[str], str],
                 canal: CanalTelegram | None = None, chat_id: str | None = None,
                 dormir: Callable[[float], Awaitable[None]] | None = None):
        self.cfg = cfg
        self.repo = repo
        self.portas = portas
        self._lider = lider
        self._recusa = recusa
        self._redigir = redigir
        self._canal = canal
        self._chat_injetado = chat_id
        self._dormir = dormir or asyncio.sleep
        self._conflito_desde: float | None = None
        self._limitada_avisada = 0.0

    # ------------------------------------------------------------------ configuração e saúde
    @property
    def ligada(self) -> bool:
        a = self.cfg.file.avisos
        return bool(a.enabled and a.entrada.enabled)

    def _segredos(self) -> tuple[str, str]:
        env = self.cfg.env
        token = env.telegram_bot_token.get_secret_value() if env.telegram_bot_token else ""
        chat = env.telegram_chat_id.get_secret_value() if env.telegram_chat_id else ""
        return token.strip(), (self._chat_injetado or chat).strip()

    def canal(self) -> CanalTelegram | None:
        if not self.ligada:
            return None
        token, chat = self._segredos()
        if self._canal is not None:
            return self._canal if chat else None
        if not token or not chat:
            return None
        return CanalTelegram(token, chat, timeout_s=self.cfg.file.avisos.timeout_s)

    def problemas(self) -> list[Problem]:
        if not self.ligada or self._conflito_desde is None:
            return []
        return [Problem(
            code="telegram_entrada_conflito",
            message="A conversa pelo Telegram está parada: outro processo lê o mesmo bot (409 no getUpdates).",
            hint="Só um consumidor por bot. Pare a outra leitura (a caixa provisória da orquestradora, outra réplica) "
                 "ou desligue avisos.entrada.enabled; a Central tenta de novo sozinha a cada minuto.")]

    # ------------------------------------------------------------------ uma volta
    async def uma_volta(self) -> int:
        """Recebe, grava e trata. Devolve quantas updates chegaram (0 quando pulou)."""
        canal = self.canal()
        if canal is None or self._lider(AVISOS) is None:
            return 0
        cfg = self.cfg.file.avisos.entrada
        try:
            brutas = await canal.receber(self.repo.proximo_offset(), espera_s=cfg.long_poll_s)
        except ConflitoDeConsumidor:
            if self._conflito_desde is None:
                log.warning("telegram: outro consumidor do bot (409); a entrada espera")
                self._conflito_desde = time.monotonic()
            await self._dormir(cfg.espera_conflito_s)
            return 0
        self._conflito_desde = None
        if self._lider(AVISOS) is None:
            return 0        # perdeu a liderança no long-poll: nada gravado = nada confirmado; o novo líder relê
        if not self.repo.ajuda_ja_enviada():
            await self._enviar(canal, "A Central agora atende por aqui.\n" + AJUDA, origem="ajuda")
        chat = self._segredos()[1]
        for bruta in brutas:
            u = _ler_update(bruta)
            if u is not None:
                await self._gravar(canal, u, chat)
        for linha in self.repo.a_tratar():
            await self._tratar(canal, linha)
        await self._contar_desfechos(canal)
        return len(brutas)

    def _linha(self, u: _Update, do_chat: bool, texto: str | None, estado: str = "recebida",
               erro: str | None = None) -> bool:
        return self.repo.gravar(update_id=u.update_id, tipo=u.tipo, do_chat=do_chat, message_id=u.message_id,
                                responde_a=u.responde_a, texto=texto, tamanho=len(u.texto), estado=estado, erro=erro)

    async def _gravar(self, canal: CanalTelegram, u: _Update, chat: str) -> None:
        do_chat = bool(chat) and u.chat == chat
        if not do_chat or u.tipo == "outro":
            self._linha(u, do_chat, None, "ignorada")
            return
        if u.callback_id is not None:
            try:
                await canal.confirmar_botao(u.callback_id)              # tira o relógio do botão; não decide nada
            except FalhaDeEnvio:
                pass
        limite = self.cfg.file.avisos.entrada.max_chars
        if len(u.texto) > limite:
            if self._linha(u, True, None, "recusada", "mensagem longa demais"):
                await self._enviar(canal, RESPOSTA_LONGA.format(n=limite), origem="resposta", responde_a=u.message_id)
            return
        if u.tipo == "mensagem" and self._parece_segredo(u.texto):
            if self._linha(u, True, None, "recusada", "parece credencial ou código"):
                await self._enviar(canal, RESPOSTA_CREDENCIAL, origem="resposta", responde_a=u.message_id)
            return
        self._linha(u, True, u.texto)

    def _parece_segredo(self, texto: str) -> bool:
        if self._recusa(texto) or parece_codigo(texto):
            return True
        partes = texto.strip().split(maxsplit=2)          # "/responder <id> 123456": a resposta é o que importa
        return len(partes) == 3 and partes[0].lower().startswith("/responder") and parece_codigo(partes[2])

    # ------------------------------------------------------------------ tratar
    async def _tratar(self, canal: CanalTelegram, linha: Linha) -> None:
        token = OPERADOR.set(OPERADOR_DO_TELEGRAM)
        try:
            if linha["tipo"] == "botao":
                await self._botao(canal, linha)
                return
            cfg = self.cfg.file.avisos.entrada
            if self.repo.do_chat_na_janela(60, ate_id=self._id(linha)) > cfg.limite_por_min:
                self.repo.marcar(self._id(linha), "limitada", de=("recebida",))
                if time.monotonic() - self._limitada_avisada > 60:
                    self._limitada_avisada = time.monotonic()
                    await self._responder(canal, linha, f"Mais de {cfg.limite_por_min} mensagens por minuto: esta não "
                                                        "foi tratada. Espere um pouco e mande de novo.")
                return
            await self._agir(canal, linha, self._intencao(linha))
        except RecusaDaCentral as recusa:
            texto = self._redigir(str(recusa))[:500]
            self.repo.marcar(self._id(linha), "falhou", erro="recusada pela Central", resposta=texto,
                             de=("recebida", "pergunta"))
            await self._responder(canal, linha, texto)
        except Exception as exc:  # uma mensagem ruim não para a conversa
            log.exception("telegram: mensagem %s", linha.get("id"))
            self.repo.marcar(self._id(linha), "falhou", erro=type(exc).__name__, de=("recebida",))
        finally:
            OPERADOR.reset(token)

    @staticmethod
    def _id(linha: Mapping[str, object]) -> int:
        return int(str(linha["id"]))

    def _intencao(self, linha: Linha) -> Intencao:
        responde_a = _int(linha.get("responde_a"))
        enviada = self.repo.enviada(responde_a) if responde_a is not None else None
        # Reply a mensagem do bot que a Central não registrou é da orquestradora (decisão (e)); reply a uma mensagem
        # da própria pessoa não é reply ao bot.
        ao_bot = responde_a is not None and not self.repo.da_pessoa(responde_a)
        fato = enviada.get("fato") if enviada is not None else None
        return rotear(str(linha.get("texto") or ""), responde_ao_bot=ao_bot, registrada=enviada is not None,
                      fato=str(fato) if fato else None)

    async def _agir(self, canal: CanalTelegram, linha: Linha, i: Intencao) -> None:
        if i.tipo == "vazia":
            self.repo.marcar(self._id(linha), "ignorada", intencao=i.tipo, de=("recebida",))
        elif i.tipo == "orquestradora":
            self.repo.marcar(self._id(linha), "orquestradora", intencao=i.tipo, destino="orquestradora",
                             de=("recebida",))
            await self._responder(canal, linha, "Recado guardado para a orquestradora (não executado).")
        elif i.tipo == "ajuda":
            await self._feita(canal, linha, i, AJUDA)
        elif i.tipo == "desconhecida":
            await self._feita(canal, linha, i, f"{i.motivo or 'Não entendi.'} /ajuda mostra os comandos.")
        elif i.tipo == "status":
            await self._feita(canal, linha, i, self.portas.status())
        elif i.tipo == "pendencias":
            await self._feita(canal, linha, i, self._texto_pendencias())
        elif i.tipo in ("aprovar", "vetar"):
            await self._decidir(canal, linha, i)
        elif i.tipo == "responder":
            await self._responder_pergunta(canal, linha, i)
        elif i.tipo in ("para", "livre"):
            texto = texto_para_o_extrator(i.alvo or "", i.texto) if i.tipo == "para" else i.texto
            await self._previa(canal, linha, i, texto, None)

    async def _feita(self, canal: CanalTelegram, linha: Linha, i: Intencao, texto: str, *, alvo: str | None = None,
                     run_id: str | None = None) -> None:
        self.repo.marcar(self._id(linha), "feita", intencao=i.tipo, destino="central", alvo=alvo, run_id=run_id,
                         resposta=self._redigir(texto), de=("recebida", "pergunta", "executando"))
        await self._responder(canal, linha, texto)

    def _texto_pendencias(self) -> str:
        itens = self.portas.pendencias()
        if not itens:
            return "Nada espera você agora."
        linhas = [f"{len(itens)} esperando você:"]
        for p in itens[:15]:
            rotulo = "aprovar" if p.tipo == "aprovacao" else "responder"
            linhas.append(f"- {p.ident[-6:]} ({rotulo}): {self._redigir(p.resumo)[:200]}")
        if len(itens) > 15:
            linhas.append(f"... e mais {len(itens) - 15} no painel.")
        linhas.append("Decida com /aprovar <id>, /vetar <id> ou /responder <id> <texto>, ou responda ao aviso.")
        return "\n".join(linhas)

    @staticmethod
    def _um_id(ref: str, ids: list[str], o_que: str) -> tuple[str | None, str]:
        achados = casar_ref(ref, ids)
        if not achados:
            return None, f"Não achei {o_que} com o id {ref}. /pendencias mostra os ids."
        if len(achados) > 1:
            return None, f"O id {ref} serve para mais de um ({', '.join(a[-8:] for a in achados)}): use mais caracteres."
        return achados[0], ""

    async def _decidir(self, canal: CanalTelegram, linha: Linha, i: Intencao) -> None:
        aid, erro = self._um_id(i.ref or "", self.portas.aprovacoes_pendentes(), "aprovação pendente")
        if aid is None:
            await self._feita(canal, linha, i, erro)
            return
        texto = self.portas.decidir(aid, "approve" if i.tipo == "aprovar" else "reject")
        await self._feita(canal, linha, i, texto, alvo=f"approval:{aid}")

    async def _responder_pergunta(self, canal: CanalTelegram, linha: Linha, i: Intencao) -> None:
        rid, erro = self._um_id(i.ref or "", self.portas.execucoes_esperando(), "execução esperando resposta")
        if rid is None:
            await self._feita(canal, linha, i, erro)
            return
        nova, curta = self.portas.responder(rid, i.texto)
        await self._feita(canal, linha, i, f"Respondida: a execução segue em {curta}. Conto aqui quando terminar.",
                          alvo=f"run:{rid}", run_id=nova)

    # ------------------------------------------------------------------ prévia e botões
    async def _previa(self, canal: CanalTelegram, linha: Linha, i: Intencao, texto: str,
                      instance_ids: list[str] | None) -> None:
        ident = self._id(linha)
        p = self.portas.previa(texto, instance_ids)
        if p.perguntas or not p.alvos:
            online = self.portas.online()[:8]
            pergunta = " ".join(p.perguntas) or "Para qual aparelho?"
            if not online:
                await self._feita(canal, linha, i, f"{pergunta} Nenhum aparelho online agora; tente pelo painel.")
                return
            self.repo.marcar(ident, "pergunta", intencao=i.tipo, destino="central", previa={"texto": texto},
                             de=("recebida", "pergunta"))
            await self._responder(canal, linha, pergunta, origem="previa",
                                  botoes=[(a, f"a:{ident}:{a}") for a in online])
            return
        resumo = ", ".join(str(a.get("instance_id") or "a escolher")
                           + (f" · {a.get('profile_id')}" if a.get("profile_id") else "") for a in p.alvos)
        avisos = f"\n{' '.join(p.avisos)}" if p.avisos else ""
        self.repo.marcar(ident, "pergunta", intencao=i.tipo, destino="central",
                         previa={"texto": texto, "alvos": p.alvos}, de=("recebida", "pergunta"))
        await self._responder(canal, linha, f"Prévia: {resumo}\nPedido: {self._redigir(p.comando)[:300]}{avisos}",
                              origem="previa", botoes=[("Executar", f"x:{ident}"), ("Cancelar", f"c:{ident}")])

    async def _botao(self, canal: CanalTelegram, linha: Linha) -> None:
        """O toque num botão da prévia. A linha do botão só registra; quem muda é a linha da mensagem original, e só
        a partir de `pergunta` (o segundo toque perde no `UPDATE ... WHERE estado='pergunta'`)."""
        acao, _, resto = str(linha.get("texto") or "").partition(":")
        alvo_id, _, extra = resto.partition(":")
        original = self.repo.linha(int(alvo_id)) if alvo_id.isdigit() else None
        self.repo.marcar(self._id(linha), "feita", intencao=f"botao:{acao}", destino="central",
                         alvo=f"mensagem:{alvo_id}", de=("recebida",))
        mid = _int(linha.get("message_id"))
        if mid is not None:
            try:
                await canal.tirar_botoes(mid)             # só a tela: a garantia é o estado da linha original
            except FalhaDeEnvio:
                pass
        if original is None or original.get("estado") != "pergunta":
            await self._responder(canal, linha, "Esta prévia já foi tratada.")
            return
        previa = _json(original.get("previa"))
        i = Intencao(str(original.get("intencao") or "livre"))
        oid = self._id(original)
        if acao == "c":
            if self.repo.marcar(oid, "cancelada", de=("pergunta",)):
                await self._responder(canal, original, "Cancelado: nada foi executado.")
        elif acao == "a" and extra:
            await self._previa(canal, original, i, str(previa.get("texto") or ""), [extra])
        elif acao == "x" and isinstance(previa.get("alvos"), list):
            if not self.repo.marcar(oid, "executando", de=("pergunta",)):
                return
            alvos = [a for a in previa["alvos"] if isinstance(a, dict)]  # type: ignore[union-attr]
            try:
                run_id, curta = self.portas.criar(str(previa.get("texto") or ""), alvos,
                                                  f"telegram:{original['update_id']}")
            except RecusaDaCentral as recusa:
                self.repo.marcar(oid, "falhou", erro="recusada pela Central", de=("executando",))
                await self._responder(canal, original, f"Não criei a execução: {self._redigir(str(recusa))[:300]}")
                return
            except Exception:
                log.exception("telegram: criar a execução da mensagem %s", oid)
                self.repo.marcar(oid, "falhou", erro="erro interno ao criar", de=("executando",))
                await self._responder(canal, original, "Não criei a execução: erro interno (está no log da Central).")
                return
            await self._feita(canal, original, i, f"Execução {curta} criada. Conto aqui quando terminar.",
                              run_id=run_id)

    # ------------------------------------------------------------------ desfecho na thread
    async def _contar_desfechos(self, canal: CanalTelegram) -> None:
        for linha in self.repo.esperando_desfecho():
            texto = self.portas.desfecho(str(linha["run_id"]))
            if texto is None:
                continue
            await self._responder(canal, linha, self._redigir(texto), origem="resultado")
            self.repo.marcar_desfecho(self._id(linha))

    # ------------------------------------------------------------------ saída
    async def _responder(self, canal: CanalTelegram, linha: Mapping[str, object], texto: str, *,
                         origem: str = "resposta", botoes: list[tuple[str, str]] | None = None) -> None:
        await self._enviar(canal, texto, origem=origem, responde_a=_int(linha.get("message_id")), botoes=botoes,
                           mensagem_id=self._id(linha))

    async def _enviar(self, canal: CanalTelegram, texto: str, *, origem: str, responde_a: int | None = None,
                      botoes: list[tuple[str, str]] | None = None, mensagem_id: int | None = None) -> None:
        try:
            enviada = await canal.responder(texto, responde_a=responde_a, botoes=botoes)
        except FalhaDeEnvio as falha:
            log.warning("telegram: resposta não saiu (%s)", falha.motivo)
            return
        self.repo.registrar_enviada(enviada, origem, mensagem_id=mensagem_id)

    # ------------------------------------------------------------------ laço
    async def laco(self) -> None:
        while True:
            try:
                # O long-poll é a espera; sem canal (segredo faltando) ou sem liderança, a volta não espera nada.
                if not self.ligada or (await self.uma_volta() == 0
                                       and (self.canal() is None or self._lider(AVISOS) is None)):
                    await self._dormir(OCIOSO_S)
            except asyncio.CancelledError:
                raise
            except FalhaDeEnvio as falha:
                log.warning("telegram: entrada (%s)", falha.motivo)
                await self._dormir(5)
            except Exception:  # a conversa nunca derruba o processo
                log.exception("telegram: laço da entrada")
                await self._dormir(5)
