"""Quem fala com o bot e não é o dono (item 28.18; regras C-08 a C-11 de `docs/dominios/canais.md`; emenda de 04/10 ao
ADR-071).

O caminho, num chat PRIVADO que não é o do dono:
1. Chat novo: a apresentação da ANA e a pergunta do nome, uma vez (C-10). Nada além disso.
2. A resposta seguinte é o nome. O dono é avisado no chat DELE (o único lugar onde o nome pode ir) e responde ao aviso
   com sim ou não. Enquanto ele não decide, o chat não recebe mais nada.
3. Com o "sim", o chat vira convidado autorizado (C-09): `/ajuda`, `/status` (só contagens, sem nome de aparelho) e o
   resto vira "recebido; passei ao dono", com um aviso ao dono limitado por hora. Convidado nunca executa, nunca decide
   e não recebe dado de persona, conta, e-mail, telefone, IP ou segredo. Com o "não", silêncio.

Grupos e canais: nada se lê nem se responde. Pôr ou tirar o bot de um grupo avisa o dono (C-11).

O texto de quem não é o dono nunca vai a `canal_entradas` (lá a linha é gravada sem texto, para o dedupe e o offset).
No histórico (`canal_contato_eventos`) ele entra redigido e cortado; o que parece credencial entra só como `retido`.

Desligado de fábrica (`avisos.entrada.convidados.enabled`): desligado, quem não é o dono segue gravado sem texto e
sem resposta, como no 28.15.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable

from app.config import Config
from app.contracts.identidade import APRESENTACAO_DA_IA, NOME_DA_IA
from app.modules.avisos.application.entrega import FalhaDeEnvio
from app.modules.avisos.domain.mensagem import Aviso, chave_do_fato
from app.modules.avisos.infrastructure.contatos_sql import ContatosDoCanal

log = logging.getLogger("poc.avisos.convidados")

#: A família do fato dos avisos sobre convidados (`canal_enviadas.fato`): `convidado:<chat>:novo` é o que o dono
#: decide com sim ou não; `convidado:<chat>:msg-<update>` e `convidado:<chat>:grupo-<update>` só informam.
FAMILIA = "convidado"
TIPO_NOVO = "telegram.convidado_novo"
TIPO_MENSAGEM = "telegram.convidado_mensagem"
TIPO_GRUPO = "telegram.grupo"

PERGUNTA_DO_NOME = (f"Olá! Eu sou a {APRESENTACAO_DA_IA} (sou uma IA, não uma pessoa). Antes de seguir: como você se "
                    "chama?")
AGUARDANDO_O_DONO = "Obrigada! Avisei o dono. Assim que ele autorizar, eu sigo com você por aqui."
AUTORIZADO = (f"Pronto: o dono autorizou. Pode falar comigo por aqui. Eu sou a {NOME_DA_IA}, uma IA: o que você pedir "
              "passa pelo dono antes, e /ajuda mostra o que vale neste chat.")
AJUDA_DO_CONVIDADO = (f"Por aqui você fala com a {NOME_DA_IA}, a IA da Central.\n"
                      "/status: se a Central está no ar\n"
                      "Pedidos e perguntas chegam ao dono; nada é executado a partir deste chat.")
RECEBIDO = "Recebido; passei ao dono."
SO_DO_DONO = "Esse comando é só do dono. /ajuda mostra o que vale aqui."
SEGREDO = "Isso parece senha ou código: não guardei e não repassei. Não mande senha por aqui."

Envio = Callable[[str, str], Awaitable[str | None]]          # (chat, texto) → message_id
Apagar = Callable[[str, str], Awaitable[bool]]               # (chat, message_id) → apagou?

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
# O nome não é lugar de contato: e-mail, @conta e IP saem (os mesmos padrões do `sanear` do Trello).
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CONTA = re.compile(r"(?<![\w@])@\w{2,}")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def limpar_nome(texto: str, maximo: int, redigir: Callable[[str], str]) -> str:
    """Uma linha, sem caractere de controle, redigida (e-mail, @conta, IP, número longo saem) e cortada."""
    linha = _CONTROLE.sub(" ", unicodedata.normalize("NFKC", texto).split("\n", 1)[0])
    sem_contato = _IP.sub("", _CONTA.sub("", _EMAIL.sub("", redigir(linha))))
    limpo = " ".join(sem_contato.split())
    return limpo[:maximo].strip()


class ConvidadosDoTelegram:
    def __init__(self, cfg: Config, contatos: ContatosDoCanal, *, avisar_dono: Callable[[Aviso], bool] | None,
                 recusa: Callable[[str], bool], redigir: Callable[[str], str], status: Callable[[], str]):
        self.cfg = cfg
        self.contatos = contatos
        self._avisar_dono = avisar_dono
        self._recusa = recusa
        self._redigir = redigir
        self._status = status

    @property
    def ligado(self) -> bool:
        e = self.cfg.file.avisos.entrada
        return bool(e.enabled and e.convidados.enabled)

    def trata(self, tipo: str, chat: str | None, privado: bool, do_dono: bool, chat_do_dono: str) -> bool:
        """Esta update é do caminho do convidado? Mensagem num chat privado que não é o dono, ou o bot posto ou tirado de
        um chat que não é o do dono. O resto (o dono, grupo, botão) segue o caminho de sempre."""
        if not self.ligado or not chat or chat == chat_do_dono:
            return False
        return tipo == "membro" or (tipo == "mensagem" and privado and not do_dono)

    # ------------------------------------------------------------------ o que chegou
    async def tratar(self, *, tipo: str, chat: str, privado: bool, texto: str, perfil: dict[str, object] | None,
                     update_id: str, ref_mensagem: str | None, enviar: Envio, apagar: Apagar) -> None:
        if tipo == "membro":
            await self._membro(chat, privado, texto, update_id)
            return
        if perfil and perfil.get("is_bot") is True:
            return
        cfg = self.cfg.file.avisos.entrada.convidados
        contato = self.contatos.obter(chat)
        if contato is None:
            if self.contatos.novos_desde(3600) >= cfg.novos_por_hora:
                log.warning("convidados: teto de chats novos por hora; um chat novo ficou sem resposta")
                return
            self.contatos.criar(chat, perfil)
            contato = self.contatos.obter(chat)
            if contato is None:
                return
        else:
            self.contatos.tocar(chat, perfil)
        if self._recusa(texto):
            self.contatos.evento(chat, "retido", None, len(texto))
            if contato.estado != "recusado":
                if ref_mensagem is not None:
                    await self._apagar(apagar, chat, ref_mensagem)
                await self._enviar(enviar, chat, SEGREDO)
            return
        if len(texto) > self.cfg.file.avisos.entrada.max_chars:
            self.contatos.evento(chat, "retido", "longa demais", len(texto))
            return
        self.contatos.evento(chat, "mensagem", self._redigir(texto), len(texto))
        if contato.estado == "aguardando_nome":
            await self._aguardando_nome(chat, contato.nome_pedido, texto, enviar)
        elif contato.estado == "autorizado":
            await self._autorizado(chat, contato.nome_informado or "", texto, update_id, enviar)
        # `aguardando_dono` e `recusado`: só o histórico, nenhuma resposta (C-10, C-11).

    async def _aguardando_nome(self, chat: str, nome_pedido: bool, texto: str, enviar: Envio) -> None:
        if not nome_pedido:
            # A 1ª mensagem do chat (ou a pergunta que não chegou a sair): não é o nome; a resposta é a pergunta.
            if await self._enviar(enviar, chat, PERGUNTA_DO_NOME):
                self.contatos.marcar_nome_pedido(chat)
                self.contatos.evento(chat, "pergunta_nome")
            return
        if texto.strip().startswith("/"):
            return                                  # `/start` de novo não é nome
        nome = limpar_nome(texto, self.cfg.file.avisos.entrada.convidados.max_nome, self._redigir)
        if not nome or not self.contatos.gravar_nome(chat, nome):
            return
        self.contatos.evento(chat, "nome_informado", nome)
        # C-10: o nome vai SÓ ao chat do dono (decisão dele, 03/10 22:41Z), no aviso que ele responde com sim ou não.
        self._aviso(chat, Aviso(
            chave=chave_do_fato(FAMILIA, chat, "novo"), tipo=TIPO_NOVO,
            titulo=f"{NOME_DA_IA}: alguém novo quer falar pelo Telegram",
            corpo=f"Disse que se chama “{nome}”.\nResponda a esta mensagem com sim (autoriza) ou não.", link=None))
        await self._enviar(enviar, chat, AGUARDANDO_O_DONO)

    async def _autorizado(self, chat: str, nome: str, texto: str, update_id: str, enviar: Envio) -> None:
        t = texto.strip()
        if t.startswith("/"):
            cmd = t[1:].split(maxsplit=1)[0].split("@", 1)[0].lower() if len(t) > 1 else ""
            if cmd in ("ajuda", "start", "help"):
                await self._enviar(enviar, chat, AJUDA_DO_CONVIDADO)
            elif cmd in ("status", "estado"):
                await self._enviar(enviar, chat, self._status())
            else:
                await self._enviar(enviar, chat, SO_DO_DONO)
            return
        # Pergunta ou pedido: passa ao dono (C-09), com teto por hora para um chat não inundar o do dono.
        cfg = self.cfg.file.avisos.entrada.convidados
        if self.contatos.eventos_desde(chat, "aviso_dono", 3600, detalhe=TIPO_MENSAGEM) >= cfg.avisos_por_hora:
            return
        quem = nome or "Um convidado"
        corpo = self._redigir(t)[:300]
        self._aviso(chat, Aviso(
            chave=chave_do_fato(FAMILIA, chat, f"msg-{update_id}"), tipo=TIPO_MENSAGEM,
            titulo=f"{NOME_DA_IA}: {quem} escreveu pelo Telegram",
            corpo=f"“{corpo}”\nNada foi executado: convidado não executa. Para atender, peça pelo painel.", link=None))
        await self._enviar(enviar, chat, RECEBIDO)

    async def _membro(self, chat: str, privado: bool, status: str, update_id: str) -> None:
        """O bot posto (`member`, `administrator`) ou tirado (`left`, `kicked`) de um chat. No privado é a pessoa
        bloqueando ou desbloqueando o bot: só o histórico. Em grupo, o dono é avisado (C-11), sem o nome do grupo."""
        posto = status in ("member", "administrator", "creator")
        tirado = status in ("left", "kicked")
        if not (posto or tirado):
            return
        if privado:
            if self.contatos.obter(chat) is not None:
                self.contatos.evento(chat, "bot_posto" if posto else "bot_tirado")
            return
        self.contatos.evento(chat, "bot_posto" if posto else "bot_tirado")
        acao = "posto num grupo" if posto else "tirado de um grupo"
        self._aviso(chat, Aviso(
            chave=chave_do_fato(FAMILIA, chat, f"grupo-{update_id}"), tipo=TIPO_GRUPO,
            titulo=f"{NOME_DA_IA}: o bot foi {acao}",
            corpo=f"Grupos e canais são ignorados: a {NOME_DA_IA} não lê nem responde lá.", link=None))

    # ------------------------------------------------------------------ a decisão do dono
    async def decidir(self, chat: str, autorizar: bool, enviar: Envio) -> str:
        """O "sim" ou o "não" do dono ao aviso `convidado:<chat>:novo`. Devolve o que responder ao dono."""
        contato = self.contatos.obter(chat)
        if contato is None or contato.estado == "aguardando_nome":
            return "Não achei esse pedido de contato."
        novo = "autorizado" if autorizar else "recusado"
        if not self.contatos.decidir(chat, novo):
            return "Já estava decidido assim."
        self.contatos.evento(chat, novo)
        if autorizar:
            await self._enviar(enviar, chat, AUTORIZADO)
            return f"Autorizado: a pessoa já pode falar com a {NOME_DA_IA}; pedidos dela chegam a você e nada é executado."
        return f"Recusado: a {NOME_DA_IA} não responde mais a esse chat."

    # ------------------------------------------------------------------ envio
    def _aviso(self, chat: str, aviso: Aviso) -> None:
        if self._avisar_dono is not None and self._avisar_dono(aviso):
            self.contatos.evento(chat, "aviso_dono", aviso.tipo)

    async def _enviar(self, enviar: Envio, chat: str, texto: str) -> bool:
        try:
            await enviar(chat, texto)
            return True
        except FalhaDeEnvio as falha:
            log.warning("convidados: resposta não saiu (%s)", falha.motivo)
            return False

    async def _apagar(self, apagar: Apagar, chat: str, ref: str) -> None:
        try:
            await apagar(chat, ref)
        except FalhaDeEnvio:
            pass
