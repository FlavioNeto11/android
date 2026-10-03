"""A conversa de volta pelos canais externos (item 28.15, ADR-071): o dono fala com a Central como no painel.

O contrato comum dos canais (`docs/design/canais-externos.md`) separa duas metades:
- a TRADUÇÃO, própria de cada canal, do que chegou numa `Recebida` (aqui, a update do Telegram: `_ler_update`; o
  Trello do 32.2 traduz a action dele) e a `SaidaDaConversa` dele (responder, apagar, botões);
- a parte COMUM (`registrar` e `tratar_pendentes`): identidade, dedupe, credencial, limite, gramática e as portas do
  painel. Ela não conhece Telegram: o mesmo comando vindo de outro canal passa pelas mesmas políticas.

O laço do Telegram:
1. só no líder da trava `avisos`, e só com `avisos.enabled` + `avisos.entrada.enabled`;
2. `getUpdates` com o offset do banco (`MAX(ordem) + 1`), em long-poll. Na 1ª subida (canal sem nenhuma linha) o que o
   Telegram guardou antes é DESCARTADO, não tratado: um "/aprovar" ou um "sim" de ontem não pode executar;
3. grava cada update ANTES de tratar (uma que não grava vira `falhou` sem texto, para o offset andar). A de outro chat
   ou outra pessoa, a longa demais, a que parece credencial e a resposta a uma pergunta que pede credencial são gravadas
   SEM o texto e não são tratadas; as com cara de credencial ainda são apagadas do chat quando o Telegram deixa;
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
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.security.sessions import OPERADOR
from app.taskqueue.travas import AVISOS

log = logging.getLogger("poc.avisos.entrada")

#: Quem age pela conversa, na auditoria (decisão (b)): legível e sem o chat_id.
OPERADOR_DO_TELEGRAM = "telegram:dono"
#: A credencial é recusada sem guardar e apagada do canal quando ele deixa (contrato dos canais, §7); a resposta nunca
#: ecoa o texto.
RESPOSTA_CREDENCIAL = ("Isso parece senha ou código: não guardei, não repassei e apaguei a mensagem do chat. Senha "
                       "fica na conta da persona; grave pelo painel.")
RESPOSTA_CREDENCIAL_SEM_APAGAR = ("Isso parece senha ou código: não guardei e não repassei, mas não consegui apagar a "
                                  "mensagem daqui. Apague-a do chat. Senha fica na conta da persona; grave pelo painel.")
#: A resposta a uma execução que pergunta por senha, código, 2FA ou token é credencial qualquer que seja a forma do texto
#: (ADR-040: credencial só pelo canal sensível). Decide pelo contexto, então "kiwi2024!" também cai aqui.
_ORIENTACAO = "A senha fica guardada na conta da persona; o código de verificação se digita no aparelho."
RESPOSTA_PERGUNTA_CREDENCIAL = ("Essa execução pergunta por senha, código ou verificação: não guardei, não repassei e "
                                "apaguei a mensagem do chat. " + _ORIENTACAO)
#: O curto recusado pode ter sido um pedido de verdade: a resposta diz como mandá-lo.
_SE_ERA_PEDIDO = " Se era um pedido, escreva com mais detalhe (ou use /para <aparelho> <objetivo>)."
RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR = ("Essa execução pergunta por senha, código ou verificação: não guardei e não "
                                           "repassei, mas não consegui apagar a mensagem daqui. Apague-a do chat. "
                                           + _ORIENTACAO)
RESPOSTA_VENCIDA = "Esta prévia venceu; mande o pedido de novo."
RESPOSTA_PRESA = ("A criação deste pedido foi interrompida antes de terminar. Confira no painel se a execução existe e, "
                  "se não, mande o pedido de novo.")
RESPOSTA_ANTIGAS = ("{quantas} há mais de {min} min (a Central estava fora do ar) e não {foi} tratada{s}: nada foi "
                   "aprovado, vetado nem executado por {ela}. Mande de novo o que ainda valer.")
RESPOSTA_LONGA = "Mensagem longa demais para a conversa (limite de {n} caracteres): não guardei. Use o painel."
#: Só dígitos (com espaço ou hífen entre eles), de 4 a 8: o formato de um código de verificação.
_CODIGO = re.compile(r"[\s-]*(?:\d[\s-]?){4,8}[\s-]*")
#: Com uma pergunta de senha aberta, o texto livre (ou o recado) com até isto de palavras é tratado como a senha.
MAX_PALAVRAS_SENSIVEL = 3
#: A causa provável de cada recusa definitiva do `getUpdates` (E7), para a saúde orientar sem mandar trocar o token
#: quando o problema é outro. O 404 da Bot API é o bot que ela não acha pelo token (token malformado ou de outro bot).
_CAUSA_DA_RECUSA = {
    400: "pedido inválido ao getUpdates (parâmetro ou offset); não é o token",
    401: "token revogado ou trocado no BotFather",
    403: "o bot foi bloqueado ou removido da conversa",
    404: "token malformado ou de um bot que não existe mais",
}
#: Pausa entre voltas quando a entrada está desligada ou sem liderança (o long-poll é a espera de verdade).
OCIOSO_S = 15.0
#: Quanto tempo uma linha pode ficar em `executando` sem execução criada antes de ser dada como interrompida.
PRESA_S = 300.0
#: Espera padrão depois de uma falha de rede ou 5xx na leitura (o 429 manda a dele; o definitivo, `espera_conflito_s`).
ESPERA_FALHA_S = 5.0
Linha = dict[str, object]


#: O código do erro do caminho comum (`RunError`, 409) quando a resposta a uma pergunta é credencial. É final.
CREDENCIAL_NA_RESPOSTA = "credencial_na_resposta"


class RecusaDaCentral(Exception):
    """O serviço do painel recusou (o pré-voo, a aprovação já decidida, a execução que não espera mais resposta). A
    mensagem é a mesma que o painel mostraria, e volta à conversa como texto. `codigo` é o do erro do serviço, quando há."""

    def __init__(self, mensagem: str, codigo: str | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


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
    def decidir(self, approval_id: str, verbo: str, nota: str | None = None) -> str: ...
    def responder(self, run_id: str, texto: str) -> tuple[str, str]: ...
    def previa(self, texto: str, instance_ids: list[str] | None = None) -> Previa: ...
    def criar(self, texto: str, alvos: list[dict[str, object]], chave: str) -> tuple[str, str]: ...
    def online(self) -> list[str]: ...
    def desfecho(self, run_id: str) -> str | None: ...
    def ha_pergunta_sensivel_aberta(self) -> bool:
        """Alguma execução espera resposta a uma pergunta que pede senha, código, 2FA ou token. Leitura isolada: o caminho
        comum (29.52) vai expô-la, e esta porta passa a só repassá-la."""
        ...

    def pergunta_de(self, ref: str) -> str:
        """O que a(s) execução(ões) indicada(s) por `ref` (id inteiro ou o fim dele) pergunta(m): o `status_detail` e as
        perguntas, em texto. Vazio quando não há. Serve só para a triagem de credencial; nada daqui volta ao chat."""
        ...


@dataclass(frozen=True)
class Recebida:
    """O que chegou por um canal, já traduzido e ANTES da gramática (o contrato comum, §1). As referências são texto:
    o Telegram numera, o Trello não. `do_dono` é a identidade conferida pela tradução (o chat configurado; o membro
    dono do quadro); quem não é o dono é gravado sem texto e não é tratado."""

    id_externo: str                    # a chave do dedupe no canal: o `update_id`; o id da action
    ordem: int | None                  # a ordem da fonte quando ela numera (o offset do Telegram); senão None
    tipo: str                          # 'mensagem' | 'botao' | 'outro' (o Trello traz os dele)
    do_dono: bool
    texto: str
    ref_mensagem: str | None = None    # a mensagem da pessoa (no botão, a do bot que levava o teclado)
    responde_a: str | None = None      # a mensagem do canal a que ela responde (o fato do aviso)
    botao_id: str | None = None        # o toque num botão, para o canal tirar o relógio (`answerCallbackQuery`)
    #: Quando a pessoa escreveu (epoch, do canal). O toque num botão não traz hora própria: a idade da prévia o protege.
    escrita_em: float | None = None


class SaidaDaConversa(Protocol):
    """O que a parte comum pede ao canal para responder. Cada canal cumpre a sua: `SaidaDoTelegram` aqui; o Trello
    comenta no cartão e não apaga conteúdo do dono (`apagar` devolve False e a resposta pede que ele apague)."""

    async def responder(self, texto: str, *, responde_a: str | None = None,
                        botoes: list[tuple[str, str]] | None = None) -> str | None: ...
    async def confirmar_botao(self, botao_id: str) -> None: ...
    async def tirar_botoes(self, ref_mensagem: str) -> None: ...
    async def apagar(self, ref_mensagem: str) -> bool: ...


class SaidaDoTelegram:
    """`SaidaDaConversa` sobre o `CanalTelegram`: só converte as referências (texto na parte comum, inteiro na Bot API)."""

    def __init__(self, canal: CanalTelegram):
        self.canal = canal

    async def responder(self, texto: str, *, responde_a: str | None = None,
                        botoes: list[tuple[str, str]] | None = None) -> str | None:
        mid = await self.canal.responder(texto, responde_a=_num(responde_a), botoes=botoes)
        return str(mid) if mid is not None else None

    async def confirmar_botao(self, botao_id: str) -> None:
        await self.canal.confirmar_botao(botao_id)

    async def tirar_botoes(self, ref_mensagem: str) -> None:
        mid = _num(ref_mensagem)
        if mid is not None:
            await self.canal.tirar_botoes(mid)

    async def apagar(self, ref_mensagem: str) -> bool:
        mid = _num(ref_mensagem)
        return mid is not None and await self.canal.apagar(mid)


def parece_codigo(texto: str) -> bool:
    return bool(_CODIGO.fullmatch(texto))


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _num(v: object) -> int | None:
    """A referência em texto do canal de volta a inteiro (o `message_id` da Bot API)."""
    if isinstance(v, str) and v.lstrip("-").isdigit():
        return int(v)
    return _int(v)


def _texto(v: object) -> str | None:
    return str(v) if v is not None else None


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


def _ler_update(u: Mapping[str, object], chat_do_dono: str) -> Recebida | None:
    """A TRADUÇÃO do Telegram: uma update vira `Recebida`. A identidade é o chat privado configurado, com o mesmo
    `from.id` (só o dono; qualquer outro é gravado sem texto e sem resposta)."""
    update_id = _int(u.get("update_id"))
    if update_id is None:
        return None
    msg, cb = _mapa(u.get("message")), _mapa(u.get("callback_query"))
    tipo = "mensagem" if msg is not None else "botao" if cb is not None else "outro"
    de = _mapa((cb if cb is not None else msg or {}).get("from"))     # quem apertou o botão / escreveu
    if cb is not None:
        msg = _mapa(cb.get("message"))      # no botão, a mensagem do bot que levava o teclado
    chat = _mapa(msg.get("chat")) if msg is not None else None
    chat_id = str(chat.get("id")) if chat and chat.get("id") is not None else ""
    autor = str(de.get("id")) if de and de.get("id") is not None else ""
    # Dono = conversa PRIVADA com o chat configurado E o autor é esse mesmo id. Num grupo ou canal o chat.id configurado
    # pode até coincidir, mas qualquer membro escreve nele: só o `from.id` identifica a pessoa.
    privado = chat is not None and chat.get("type") == "private"
    texto = (cb.get("data") if cb is not None else msg.get("text") if msg is not None else None) or ""
    responde = _mapa(msg.get("reply_to_message")) if msg is not None and cb is None else None
    return Recebida(id_externo=str(update_id), ordem=update_id, tipo=tipo,
                    do_dono=bool(chat_do_dono) and privado and chat_id == chat_do_dono and autor == chat_do_dono,
                    texto=str(texto),
                    ref_mensagem=_texto(_int(msg.get("message_id"))) if msg is not None else None,
                    responde_a=_texto(_int(responde.get("message_id"))) if responde is not None else None,
                    botao_id=str(cb.get("id")) if cb is not None and cb.get("id") is not None else None,
                    # no botão, `message.date` é a hora da mensagem do BOT, não a do toque: fica sem hora
                    escrita_em=float(d) if cb is None and msg is not None and (d := _int(msg.get("date"))) else None)


class ServicoDeEntrada:
    def __init__(self, cfg: Config, repo: EntradasDoCanal, portas: PortasDaCentral, *,
                 lider: Callable[[str], int | None], recusa: Callable[[str], bool], redigir: Callable[[str], str],
                 canal: CanalTelegram | None = None, chat_id: str | None = None,
                 dormir: Callable[[float], Awaitable[None]] | None = None, operador: str = OPERADOR_DO_TELEGRAM,
                 relogio: Callable[[], float] | None = None):
        self.cfg = cfg
        self.repo = repo
        self.portas = portas
        self._lider = lider
        self._recusa = recusa
        self._redigir = redigir
        self._canal = canal
        self._chat_injetado = chat_id
        self._dormir = dormir or asyncio.sleep
        #: Quem age, como VALOR (`telegram:dono` hoje; `trello:<id>` no 32.2): vai ao ContextVar da sessão.
        self.operador = operador
        self._conflito_desde: float | None = None
        #: O Telegram recusou a leitura de vez (400/401/403/404): o motivo, já redigido, e o status que o explica.
        self._recusada: str | None = None
        self._recusada_status: int | None = None
        self._limitada_avisada = 0.0
        #: hora de parede (epoch), para comparar com a hora em que a pessoa escreveu; injetável nos testes
        self._agora = relogio or time.time
        #: mensagens antigas desta volta (escritas com a Central fora): o dono é avisado uma vez no fim da volta
        self._antigas = 0

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
        if not self.ligada:
            return []
        achados: list[Problem] = []
        if self._conflito_desde is not None:
            achados.append(Problem(
                code="telegram_entrada_conflito",
                message="A conversa pelo Telegram está parada: outro processo lê o mesmo bot (409 no getUpdates).",
                hint="Só um consumidor por bot, e um webhook ativo no bot também dá 409 (confira com getWebhookInfo; "
                     "deleteWebhook o desfaz). Pare a outra leitura (a caixa provisória da orquestradora, outra réplica) "
                     "ou desligue avisos.entrada.enabled; a Central tenta de novo sozinha a cada minuto. "
                     "`python scripts/avisos-telegram.py descobrir` mostra o que o bot enxerga (rode-o com a entrada "
                     "desligada: ele também lê o getUpdates)."))
        if self._recusada is not None:
            # E7: cada status tem a sua causa; "token revogado" para um 400 mandava o dono trocar o token à toa.
            invalido = self._recusada_status == 400
            causa = _CAUSA_DA_RECUSA.get(self._recusada_status or 0, "recusa não prevista do Telegram")
            o_que_fazer = ("O token não é a causa: veja o log da Central (poc.avisos.entrada) " if invalido else
                           "Confira TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env (docs/operacao.md §15) ")
            achados.append(Problem(
                code="telegram_entrada_pedido_invalido" if invalido else "telegram_entrada_recusada",
                message=f"A conversa pelo Telegram está parada: o Telegram recusou a leitura do bot ({self._recusada}).",
                hint=f"Causa provável: {causa}. {o_que_fazer}ou desligue avisos.entrada.enabled; a Central tenta de "
                     "novo sozinha a cada minuto."))
        return achados

    # ------------------------------------------------------------------ uma volta
    async def uma_volta(self) -> int:
        """Recebe, grava e trata. Devolve quantas updates chegaram (0 quando pulou)."""
        canal = self.canal()
        if canal is None or self._lider(AVISOS) is None:
            return 0
        cfg = self.cfg.file.avisos.entrada
        try:
            if self.repo.canal_vazio():
                await self._descartar_historico(canal)
            brutas = await canal.receber(self.repo.proximo_offset(), espera_s=cfg.long_poll_s)
        except ConflitoDeConsumidor:
            if self._conflito_desde is None:
                log.warning("telegram: outro consumidor do bot (409); a entrada espera")
                self._conflito_desde = time.monotonic()
            await self._dormir(cfg.espera_conflito_s)
            return 0
        self._conflito_desde = None
        self._recusada = None
        self._recusada_status = None
        if self._lider(AVISOS) is None:
            return 0        # perdeu a liderança no long-poll: nada gravado = nada confirmado; o novo líder relê
        saida = SaidaDoTelegram(canal)
        if not self.repo.ajuda_ja_enviada():
            await self._enviar(saida, "A Central agora atende por aqui.\n" + AJUDA, origem="ajuda")
        chat = self._segredos()[1]
        for bruta in brutas:
            r = _ler_update(bruta, chat)
            if r is None:
                continue
            try:
                await self.registrar(saida, r)
            except Exception as exc:  # noqa: BLE001 - uma update ruim não pode travar a conversa (o offset teria de andar)
                # Sem a linha o offset não anda e a MESMA update voltaria a cada volta, para sempre. Registra o mínimo
                # (sem texto) como `falhou`; só o id vai ao log, porque a exceção do banco pode trazer o texto.
                log.error("telegram: update %s não pôde ser registrada (%s)", r.id_externo, type(exc).__name__)
                self._linha(r, None, "falhou", f"erro ao registrar ({type(exc).__name__})")
        if self._antigas:
            n, self._antigas = self._antigas, 0
            minutos = round(cfg.idade_max_s / 60)
            texto = RESPOSTA_ANTIGAS.format(
                quantas="1 mensagem foi escrita" if n == 1 else f"{n} mensagens foram escritas", min=minutos,
                foi="foi" if n == 1 else "foram", s="" if n == 1 else "s", ela="ela" if n == 1 else "elas")
            await self._enviar(saida, texto, origem="resposta")
        await self.tratar_pendentes(saida)
        return len(brutas)

    async def _descartar_historico(self, canal: CanalTelegram) -> None:
        """A 1ª subida do canal (nenhuma linha): o que o Telegram guardou (até 24 h) é histórico, e um "/aprovar" ou um
        "sim" antigo não pode executar. `offset=-1` devolve só a última update e faz o Telegram esquecer as anteriores; a
        linha-marco grava a ordem dela, então o offset passa dela, e tira o canal de "vazio" para a 1ª mensagem de
        verdade não ser descartada por engano."""
        velhas = await canal.receber(-1, espera_s=0)
        ordens = [o for o in (_int(u.get("update_id")) for u in velhas) if o is not None]
        self.repo.gravar_inicio(max(ordens) if ordens else None)
        log.info("telegram: 1ª subida do canal; o histórico do chat foi descartado (%s)",
                 f"até a update {max(ordens)}" if ordens else "fila vazia")

    # ------------------------------------------------------------------ a parte comum aos canais
    async def tratar_pendentes(self, saida: SaidaDaConversa) -> None:
        """Trata as `recebida` do canal e conta na conversa o desfecho das execuções que ela criou."""
        await self._reparar_presas(saida)
        for linha in self.repo.a_tratar():
            await self._tratar(saida, linha)
        await self._contar_desfechos(saida)

    async def _reparar_presas(self, saida: SaidaDaConversa) -> None:
        """Uma queda entre marcar `executando` e criar a execução deixaria a linha ali para sempre (o botão já foi
        consumido). Passada a idade, vira `falhou` e o dono é avisado de que o pedido não se completou."""
        for linha in self.repo.presas_em_execucao(PRESA_S):
            if self.repo.marcar(self._id(linha), "falhou", erro="interrompida antes de criar a execução",
                                de=("executando",)):
                log.warning("telegram: mensagem %s presa em executando; marcada como falhou", linha.get("id"))
                await self._responder(saida, linha, RESPOSTA_PRESA)

    def _linha(self, r: Recebida, texto: str | None, estado: str = "recebida", erro: str | None = None) -> bool:
        return self.repo.gravar(id_externo=r.id_externo, ordem=r.ordem, tipo=r.tipo, do_dono=r.do_dono,
                                ref_mensagem=r.ref_mensagem, responde_a=r.responde_a, texto=texto,
                                tamanho=len(r.texto), estado=estado, erro=erro)

    async def registrar(self, saida: SaidaDaConversa, r: Recebida) -> None:
        """Grava o que chegou, já com as políticas que não esperam a gramática: quem não é o dono, o longo demais e o
        que parece credencial ficam SEM o texto e não são tratados. Releitura (mesmo `id_externo`) não faz nada."""
        if not r.do_dono or r.tipo == "outro":
            self._linha(r, None, "ignorada")
            return
        if r.botao_id is not None:
            try:
                await saida.confirmar_botao(r.botao_id)                 # tira o relógio do botão; não decide nada
            except FalhaDeEnvio:
                pass
        limite = self.cfg.file.avisos.entrada.max_chars
        if len(r.texto) > limite:
            if r.tipo == "mensagem" and self._parece_segredo(r.texto):
                # Longa e com cara de senha (uma chave, um bloco): além de recusada, sai do chat como a curta.
                await self._recusar_credencial(saida, r, "parece credencial ou código", RESPOSTA_CREDENCIAL,
                                               RESPOSTA_CREDENCIAL_SEM_APAGAR)
            elif self._linha(r, None, "recusada", "mensagem longa demais"):
                await self._enviar(saida, RESPOSTA_LONGA.format(n=limite), origem="resposta", responde_a=r.ref_mensagem)
            return
        if r.tipo == "mensagem" and self._parece_segredo(r.texto):
            await self._recusar_credencial(saida, r, "parece credencial ou código", RESPOSTA_CREDENCIAL,
                                           RESPOSTA_CREDENCIAL_SEM_APAGAR)
            return
        por_contexto = self._responde_a_pergunta_de_credencial(r) if r.tipo == "mensagem" else None
        if por_contexto is not None:
            extra = _SE_ERA_PEDIDO if por_contexto == "curta" else ""
            await self._recusar_credencial(saida, r, "a pergunta da execução pede credencial",
                                           RESPOSTA_PERGUNTA_CREDENCIAL + extra,
                                           RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR + extra)
            return
        if r.escrita_em is not None and self._agora() - r.escrita_em > self.cfg.file.avisos.entrada.idade_max_s:
            # E2: o Telegram guarda até 24 h, e a Central fora por horas traria um "/aprovar" ou um "sim" velho que
            # executaria sem ninguém olhar. Vale em TODA subida, não só na 1ª (o descarte do histórico é só a 1ª).
            if self._linha(r, None, "ignorada", "antiga: escrita com a Central fora do ar"):
                self._antigas += 1
            return
        self._linha(r, r.texto)

    async def _recusar_credencial(self, saida: SaidaDaConversa, r: Recebida, motivo: str, ok: str,
                                  sem_apagar: str) -> None:
        """Grava SEM o texto (só o tamanho), apaga a mensagem do chat quando o canal deixa e responde sem ecoar nada."""
        if not self._linha(r, None, "recusada", motivo):
            return                                                      # releitura: já recusada (e apagada) antes
        apagou = False
        if r.ref_mensagem is not None:
            try:
                apagou = await saida.apagar(r.ref_mensagem)
            except FalhaDeEnvio:
                apagou = False
        ident = self.repo.id_de(r.id_externo)
        if ident is not None:
            self.repo.marcar(ident, "recusada",
                             erro=f"{motivo}; " + ("apagada do chat" if apagou else "não apagada do chat"))
        await self._enviar(saida, ok if apagou else sem_apagar, origem="resposta",
                           responde_a=None if apagou else r.ref_mensagem, entrada_id=ident)

    async def _recusar_ja_gravada(self, saida: SaidaDaConversa, linha: Linha) -> None:
        """A linha já estava gravada com o texto (a triagem do canal não pegou, o serviço comum pegou): tira o texto,
        apaga a mensagem do chat quando dá, marca `recusada` e responde sem ecoar nada."""
        ident = self._id(linha)
        self.repo.apagar_texto(ident)
        apagou = False
        mid = _texto(linha.get("ref_mensagem"))
        if mid is not None:
            try:
                apagou = await saida.apagar(mid)
            except FalhaDeEnvio:
                apagou = False
        self.repo.marcar(ident, "recusada", erro="a Central recusou a resposta como credencial; "
                         + ("apagada do chat" if apagou else "não apagada do chat"), de=("recebida", "pergunta"))
        await self._enviar(saida, RESPOSTA_PERGUNTA_CREDENCIAL if apagou else RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR,
                           origem="resposta", responde_a=None if apagou else mid, entrada_id=ident)

    def _responde_a_pergunta_de_credencial(self, r: Recebida) -> str | None:
        """É uma RESPOSTA (reply ao aviso de pergunta ou `/responder <id> <texto>`) a uma execução cuja pergunta pede
        senha, código, 2FA, token ou verificação? Decide pelo contexto, não pela forma do texto: "contexto" quando é a
        resposta a essa pergunta, "curta" quando é texto curto com ela aberta (pode ter sido um pedido), None quando
        passa. Na dúvida (a pergunta não pôde ser lida), recusa: o dono responde pelo painel."""
        i = self._intencao({"texto": r.texto, "responde_a": r.responde_a})
        if i.tipo in ("livre", "orquestradora"):
            # E6: o texto livre E o recado à orquestradora (reply a mensagem que a Central não mandou, ou `/orq`) são
            # repassados com o texto; com uma pergunta de senha aberta, o curto é tratado como a senha.
            return "curta" if self._curta_com_pergunta_sensivel(i.texto) else None
        if i.tipo == "desconhecida" and r.texto.strip().lower().startswith("/responder"):
            # "/responder kiwi2024" sem id não vira resposta, mas a linha guardaria o texto
            return "curta" if self._curta_com_pergunta_sensivel(r.texto.strip()[len("/responder"):]) else None
        if i.tipo != "responder" or not i.ref:
            return None
        try:
            # O vocabulário é o da triagem de credencial (o mesmo do caminho comum), não uma lista do canal.
            return "contexto" if self._recusa(self.portas.pergunta_de(i.ref)) else None
        except Exception as exc:  # noqa: BLE001 - sem ler a pergunta não há como afirmar que a resposta é inofensiva
            log.warning("telegram: pergunta da execução não lida (%s); a resposta é recusada por precaução",
                        type(exc).__name__)
            return "contexto"

    def _curta_com_pergunta_sensivel(self, texto: str) -> bool:
        """A senha digitada como mensagem NORMAL (sem reply ao aviso e sem `/responder <id>`) enquanto uma execução
        espera credencial. Um pedido de verdade é uma frase; uma senha é curta, e às vezes com espaço ("kiwi 2024", E6
        da revisão): até `MAX_PALAVRAS_SENSIVEL` palavras é recusado, e a resposta pede o pedido com mais detalhe. O que
        a triagem reconhece já foi recusado antes, com ou sem pergunta aberta. Sem poder ler as perguntas, falha
        fechada: o curto é recusado."""
        if not texto.split() or len(texto.split()) > MAX_PALAVRAS_SENSIVEL:
            return False
        try:
            return self.portas.ha_pergunta_sensivel_aberta()
        except Exception as exc:  # noqa: BLE001 - sem ler as perguntas, a palavra só é tratada como credencial
            log.warning("telegram: perguntas abertas não lidas (%s); a palavra solta é recusada por precaução",
                        type(exc).__name__)
            return True

    def _parece_segredo(self, texto: str) -> bool:
        if self._recusa(texto) or parece_codigo(texto):
            return True
        partes = texto.strip().split(maxsplit=2)          # "/responder <id> 123456": a resposta é o que importa
        return len(partes) == 3 and partes[0].lower().startswith("/responder") and parece_codigo(partes[2])

    # ------------------------------------------------------------------ tratar
    async def _tratar(self, saida: SaidaDaConversa, linha: Linha) -> None:
        token = OPERADOR.set(self.operador)
        try:
            if linha["tipo"] == "botao":
                await self._botao(saida, linha)
                return
            cfg = self.cfg.file.avisos.entrada
            if self.repo.do_dono_na_janela(60, ate_id=self._id(linha)) > cfg.limite_por_min:
                self.repo.marcar(self._id(linha), "limitada", de=("recebida",))
                if time.monotonic() - self._limitada_avisada > 60:
                    self._limitada_avisada = time.monotonic()
                    await self._responder(saida, linha, f"Mais de {cfg.limite_por_min} mensagens por minuto: esta não "
                                                        "foi tratada. Espere um pouco e mande de novo.")
                return
            await self._agir(saida, linha, self._intencao(linha))
        except RecusaDaCentral as recusa:
            texto = self._redigir(str(recusa))[:500]
            self.repo.marcar(self._id(linha), "falhou", erro="recusada pela Central", resposta=texto,
                             de=("recebida", "pergunta"))
            await self._responder(saida, linha, texto)
        except Exception as exc:  # uma mensagem ruim não para a conversa
            log.exception("telegram: mensagem %s", linha.get("id"))
            self.repo.marcar(self._id(linha), "falhou", erro=type(exc).__name__, de=("recebida",))
        finally:
            OPERADOR.reset(token)

    @staticmethod
    def _id(linha: Mapping[str, object]) -> int:
        return int(str(linha["id"]))

    def _intencao(self, linha: Linha) -> Intencao:
        texto = str(linha.get("texto") or "")
        responde_a = _texto(linha.get("responde_a"))
        enviada = self.repo.enviada(responde_a) if responde_a is not None else None
        # Regra do CANAL (decisão (e)): reply a uma mensagem do bot que a Central não mandou é da orquestradora; reply
        # a uma mensagem da própria pessoa não é reply ao bot. A gramática comum só conhece o `/orq`.
        if responde_a is not None and enviada is None and not self.repo.da_pessoa(responde_a) and texto.strip():
            return Intencao("orquestradora", texto=texto.strip())
        fato = enviada.get("fato") if enviada is not None else None
        return rotear(texto, fato=str(fato) if fato else None)

    async def _agir(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        if i.tipo == "vazia":
            self.repo.marcar(self._id(linha), "ignorada", intencao=i.tipo, de=("recebida",))
        elif i.tipo == "orquestradora":
            self.repo.marcar(self._id(linha), "orquestradora", intencao=i.tipo, destino="orquestradora",
                             de=("recebida",))
            await self._responder(saida, linha, "Recado guardado para a orquestradora (não executado).")
        elif i.tipo == "ajuda":
            await self._feita(saida, linha, i, AJUDA)
        elif i.tipo == "desconhecida":
            await self._feita(saida, linha, i, f"{i.motivo or 'Não entendi.'} /ajuda mostra os comandos.")
        elif i.tipo == "status":
            await self._feita(saida, linha, i, self.portas.status())
        elif i.tipo == "pendencias":
            await self._feita(saida, linha, i, self._texto_pendencias())
        elif i.tipo in ("aprovar", "vetar"):
            await self._decidir(saida, linha, i)
        elif i.tipo == "responder":
            await self._responder_pergunta(saida, linha, i)
        elif i.tipo in ("para", "livre"):
            texto = texto_para_o_extrator(i.alvo or "", i.texto) if i.tipo == "para" else i.texto
            await self._previa(saida, linha, i, texto, None)

    async def _feita(self, saida: SaidaDaConversa, linha: Linha, i: Intencao, texto: str, *, alvo: str | None = None,
                     run_id: str | None = None) -> None:
        self.repo.marcar(self._id(linha), "feita", intencao=i.tipo, destino="central", alvo=alvo, run_id=run_id,
                         resposta=self._redigir(texto), de=("recebida", "pergunta", "executando"))
        await self._responder(saida, linha, texto)

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

    async def _decidir(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        aid, erro = self._um_id(i.ref or "", self.portas.aprovacoes_pendentes(), "aprovação pendente")
        if aid is None:
            await self._feita(saida, linha, i, erro)
            return
        texto = self.portas.decidir(aid, "approve" if i.tipo == "aprovar" else "reject", i.texto or None)
        await self._feita(saida, linha, i, texto, alvo=f"approval:{aid}")

    async def _responder_pergunta(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        rid, erro = self._um_id(i.ref or "", self.portas.execucoes_esperando(), "execução esperando resposta")
        if rid is None:
            await self._feita(saida, linha, i, erro)
            return
        try:
            nova, curta = self.portas.responder(rid, i.texto)
        except RecusaDaCentral as recusa:
            if recusa.codigo != CREDENCIAL_NA_RESPOSTA:
                raise
            # O caminho comum viu credencial onde o canal não viu (a forma do texto, p. ex.): final, sem nova tentativa.
            await self._recusar_ja_gravada(saida, linha)
            return
        await self._feita(saida, linha, i, f"Respondida: a execução segue em {curta}. Conto aqui quando terminar.",
                          alvo=f"run:{rid}", run_id=nova)

    # ------------------------------------------------------------------ prévia e botões
    async def _previa(self, saida: SaidaDaConversa, linha: Linha, i: Intencao, texto: str,
                      instance_ids: list[str] | None) -> None:
        ident = self._id(linha)
        p = self.portas.previa(texto, instance_ids)
        if p.perguntas or not p.alvos:
            online = self.portas.online()[:8]
            pergunta = " ".join(p.perguntas) or "Para qual aparelho?"
            if not online:
                await self._feita(saida, linha, i, f"{pergunta} Nenhum aparelho online agora; tente pelo painel.")
                return
            self.repo.marcar(ident, "pergunta", intencao=i.tipo, destino="central", previa={"texto": texto},
                             de=("recebida", "pergunta"))
            await self._responder(saida, linha, pergunta, origem="previa",
                                  botoes=[(a, f"a:{ident}:{a}") for a in online])
            return
        resumo = ", ".join(str(a.get("instance_id") or "a escolher")
                           + (f" · {a.get('profile_id')}" if a.get("profile_id") else "") for a in p.alvos)
        avisos = f"\n{' '.join(p.avisos)}" if p.avisos else ""
        self.repo.marcar(ident, "pergunta", intencao=i.tipo, destino="central",
                         previa={"texto": texto, "alvos": p.alvos}, de=("recebida", "pergunta"))
        await self._responder(saida, linha, f"Prévia: {resumo}\nPedido: {self._redigir(p.comando)[:300]}{avisos}",
                              origem="previa", botoes=[("Executar", f"x:{ident}"), ("Cancelar", f"c:{ident}")])

    async def _botao(self, saida: SaidaDaConversa, linha: Linha) -> None:
        """O toque num botão da prévia. A linha do botão só registra; quem muda é a linha da mensagem original, e só
        a partir de `pergunta` (o segundo toque perde no `UPDATE ... WHERE estado='pergunta'`)."""
        acao, _, resto = str(linha.get("texto") or "").partition(":")
        alvo_id, _, extra = resto.partition(":")
        original = self.repo.linha(int(alvo_id)) if alvo_id.isdigit() else None
        self.repo.marcar(self._id(linha), "feita", intencao=f"botao:{acao}", destino="central",
                         alvo=f"mensagem:{alvo_id}", de=("recebida",))
        mid = _texto(linha.get("ref_mensagem"))
        if mid is not None:
            try:
                await saida.tirar_botoes(mid)             # só a tela: a garantia é o estado da linha original
            except FalhaDeEnvio:
                pass
        if original is None or original.get("estado") != "pergunta":
            await self._responder(saida, linha, "Esta prévia já foi tratada.")
            return
        previa = _json(original.get("previa"))
        i = Intencao(str(original.get("intencao") or "livre"))
        oid = self._id(original)
        if acao == "c":
            if self.repo.marcar(oid, "cancelada", de=("pergunta",)):
                await self._responder(saida, original, "Cancelado: nada foi executado.")
        elif acao == "a" and extra:
            await self._previa(saida, original, i, str(previa.get("texto") or ""), [extra])
        elif acao == "x" and isinstance(previa.get("alvos"), list):
            if self.repo.idade_s(original) > self.cfg.file.avisos.entrada.ttl_previa_s:
                # O pedido pode já não valer (o aparelho mudou, a aprovação passou): quem manda de novo vê a prévia atual.
                if self.repo.marcar(oid, "cancelada", erro="prévia venceu", de=("pergunta",)):
                    await self._responder(saida, original, RESPOSTA_VENCIDA)
                return
            if not self.repo.marcar(oid, "executando", de=("pergunta",)):
                return
            alvos = [a for a in previa["alvos"] if isinstance(a, dict)]  # type: ignore[union-attr]
            try:
                run_id, curta = self.portas.criar(str(previa.get("texto") or ""), alvos,
                                                  f"{self.repo.canal}:{original['id_externo']}")
            except RecusaDaCentral as recusa:
                self.repo.marcar(oid, "falhou", erro="recusada pela Central", de=("executando",))
                await self._responder(saida, original, f"Não criei a execução: {self._redigir(str(recusa))[:300]}")
                return
            except Exception:
                log.exception("telegram: criar a execução da mensagem %s", oid)
                self.repo.marcar(oid, "falhou", erro="erro interno ao criar", de=("executando",))
                await self._responder(saida, original, "Não criei a execução: erro interno (está no log da Central).")
                return
            await self._feita(saida, original, i, f"Execução {curta} criada. Conto aqui quando terminar.",
                              run_id=run_id)

    # ------------------------------------------------------------------ desfecho na thread
    async def _contar_desfechos(self, saida: SaidaDaConversa) -> None:
        for linha in self.repo.esperando_desfecho():
            texto = self.portas.desfecho(str(linha["run_id"]))
            if texto is None:
                continue
            await self._responder(saida, linha, self._redigir(texto), origem="resultado")
            self.repo.marcar_desfecho(self._id(linha))

    # ------------------------------------------------------------------ saída
    async def _responder(self, saida: SaidaDaConversa, linha: Mapping[str, object], texto: str, *,
                         origem: str = "resposta", botoes: list[tuple[str, str]] | None = None) -> None:
        await self._enviar(saida, texto, origem=origem, responde_a=_texto(linha.get("ref_mensagem")), botoes=botoes,
                           entrada_id=self._id(linha))

    async def _enviar(self, saida: SaidaDaConversa, texto: str, *, origem: str, responde_a: str | None = None,
                      botoes: list[tuple[str, str]] | None = None, entrada_id: int | None = None) -> None:
        try:
            enviada = await saida.responder(texto, responde_a=responde_a, botoes=botoes)
        except FalhaDeEnvio as falha:
            log.warning("telegram: resposta não saiu (%s)", falha.motivo)
            return
        self.repo.registrar_enviada(enviada, origem, entrada_id=entrada_id)

    # ------------------------------------------------------------------ laço
    def _esperar_apos(self, falha: FalhaDeEnvio) -> float:
        """Quanto esperar depois que a LEITURA falhou. 429: o que o Telegram pediu. Definitiva (400/401/403/404, cada
        um com a sua causa em `_CAUSA_DA_RECUSA`): vira problema na saúde e espera como o 409, sem martelar a Bot API a cada 5 s. O resto
        (rede, 5xx) tenta de novo logo."""
        if falha.definitiva:
            if self._recusada is None:
                log.error("telegram: o Telegram recusou a leitura do bot (%s)", falha.motivo)
            self._recusada = falha.motivo
            self._recusada_status = falha.status
            return max(self.cfg.file.avisos.entrada.espera_conflito_s, ESPERA_FALHA_S)
        log.warning("telegram: entrada (%s)", falha.motivo)
        return max(falha.espera_s, 1.0) if falha.espera_s is not None else ESPERA_FALHA_S

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
                await self._dormir(self._esperar_apos(falha))
            except Exception:  # a conversa nunca derruba o processo
                log.exception("telegram: laço da entrada")
                await self._dormir(5)
