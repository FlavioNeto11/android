"""Canal Telegram do aviso fora do painel (item 28.11) e da conversa de volta (item 28.15, ADR-071), por HTTPS, para a
Bot API.

Chamadas: `sendMessage` (o aviso, a resposta, a prévia com botões), `getUpdates` (a descoberta do `chat_id` e, no
28.15, o long-poll da entrada com offset), `answerCallbackQuery` e `editMessageReplyMarkup` (o toque num botão) e
`deleteMessage` (a mensagem da pessoa com cara de credencial).
Sem webhook e sem rota de entrada: o central PERGUNTA ao Telegram, nunca é chamado por ele.

O token é o segredo: ele vai NA URL (`/bot<token>/…`, é o formato da Bot API), e por isso a URL nunca é registrada,
nem inteira nem em pedaço. Todo erro que sai daqui é montado a partir do NOME da exceção e do código HTTP, jamais do
`str()` dela (o de `httpx` costuma trazer a URL) e a descrição que o Telegram devolve passa por `_limpar`, que tira o
token mesmo que ele apareça ali. A redação central (`security.redaction`) é a segunda camada, não a primeira.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from app.modules.avisos.application.entrega import FalhaDeEnvio
from app.modules.avisos.domain.mensagem import texto_da_mensagem
from app.security.redaction import redact

API = "https://api.telegram.org"
#: Limite do Telegram para o texto de uma mensagem; o aviso é bem menor, o corte é só um cinto.
TEXTO_MAX = 4000
DESCRICAO_MAX = 160
#: Códigos em que repetir a mesma chamada não muda nada: token recusado, bot sem acesso ao chat, chat inexistente.
DEFINITIVOS = frozenset({400, 401, 403, 404})


#: O que a entrada pede ao `getUpdates`: a mensagem e o toque no botão inline (a prévia de alvos, decisão (c)).
UPDATES_DA_ENTRADA = '["message","callback_query"]'
#: Folga do cliente HTTP sobre o long-poll: o Telegram segura a conexão por `timeout` segundos antes de responder.
FOLGA_DO_LONG_POLL_S = 15.0


class TokenAusente(Exception):
    """Sem `TELEGRAM_BOT_TOKEN`. A mensagem diz o que fazer; não há valor a esconder."""


class ConflitoDeConsumidor(FalhaDeEnvio):
    """409 no `getUpdates`: outro processo lê o mesmo bot (a caixa provisória da orquestradora, outra réplica sem a
    trava). O Telegram só admite um consumidor; a entrada espera e vira problema na saúde, não disputa."""


def _message_id(dados: Mapping[str, object]) -> int | None:
    resultado = dados.get("result")
    mid = resultado.get("message_id") if isinstance(resultado, Mapping) else None
    return mid if isinstance(mid, int) else None


@dataclass(frozen=True)
class ChatEncontrado:
    """Um chat que escreveu ao bot. É o que o dono vê para confirmar qual `chat_id` gravar: nada de texto da mensagem."""

    id: int
    tipo: str
    titulo: str | None
    username: str | None


def _limpar(texto: str, token: str) -> str:
    """Descrição curta, de uma linha e sem o token (que a Bot API não devolve, mas a garantia é nossa)."""
    sem = (redact(texto) or "").replace(token, "***") if token else (redact(texto) or "")
    return " ".join(sem.split())[:DESCRICAO_MAX]


def _json(resposta: httpx.Response) -> Mapping[str, object]:
    try:
        corpo = resposta.json()
    except ValueError:
        return {}
    return corpo if isinstance(corpo, Mapping) else {}


def _espera_pedida(resposta: httpx.Response, corpo: Mapping[str, object]) -> float | None:
    """`Retry-After` (cabeçalho) ou `parameters.retry_after` (corpo), o que vier; limitado a um número sensato."""
    bruto: object = resposta.headers.get("retry-after")
    if bruto is None:
        params = corpo.get("parameters")
        bruto = params.get("retry_after") if isinstance(params, Mapping) else None
    try:
        s = float(str(bruto))
    except (TypeError, ValueError):
        return 30.0                               # 429 sem número legível: espera um padrão, não repete na hora
    return min(max(s, 1.0), 3600.0)


class CanalTelegram:
    def __init__(self, token: str, chat_id: str = "", *, client: httpx.AsyncClient | None = None, timeout_s: float = 10.0):
        if not token:
            raise TokenAusente("TELEGRAM_BOT_TOKEN ausente: cadastre o token do bot no .env (docs/operacao.md)")
        self._token = token
        self._chat_id = chat_id
        self._client = client
        self._timeout = timeout_s

    def _url(self, metodo: str) -> str:
        return f"{API}/bot{self._token}/{metodo}"

    async def _chamar(self, metodo: str, *, json: Mapping[str, object] | None = None,
                      params: Mapping[str, str | int] | None = None, timeout_s: float | None = None) -> httpx.Response:
        """Uma chamada, com o erro de rede já convertido em `FalhaDeEnvio` sem URL nem token."""
        cliente = self._client or httpx.AsyncClient()
        try:
            return await cliente.request("POST" if json is not None else "GET", self._url(metodo),
                                         json=json, params=params, timeout=timeout_s or self._timeout)
        except httpx.TimeoutException:
            raise FalhaDeEnvio("tempo esgotado ao falar com o Telegram") from None
        except httpx.HTTPError as exc:
            raise FalhaDeEnvio(f"falha de rede ao falar com o Telegram ({type(exc).__name__})") from None
        finally:
            if self._client is None:
                await cliente.aclose()

    def _falha(self, resposta: httpx.Response) -> FalhaDeEnvio:
        corpo = _json(resposta)
        status = resposta.status_code
        descricao = corpo.get("description")
        detalhe = f": {_limpar(descricao, self._token)}" if isinstance(descricao, str) and descricao else ""
        if status == 429:
            return FalhaDeEnvio(f"Telegram pediu para esperar (429){detalhe}", espera_s=_espera_pedida(resposta, corpo),
                                status=status)
        return FalhaDeEnvio(f"Telegram recusou ({status}){detalhe}", definitiva=status in DEFINITIVOS, status=status)

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> int | None:
        return await self.responder(texto_da_mensagem(titulo, corpo, link))

    async def responder(self, texto: str, *, responde_a: int | None = None,
                        botoes: list[tuple[str, str]] | None = None) -> int | None:
        """Uma mensagem ao chat configurado; devolve o `message_id` (o registro da 085). `responde_a` põe a mensagem
        na thread da pessoa; `botoes` são (rótulo, callback_data) numa linha de teclado inline."""
        corpo: dict[str, object] = {"chat_id": self._chat_id, "text": texto[:TEXTO_MAX],
                                    "disable_web_page_preview": True}
        if responde_a is not None:
            corpo["reply_parameters"] = {"message_id": responde_a, "allow_sending_without_reply": True}
        if botoes:
            corpo["reply_markup"] = {"inline_keyboard": [[{"text": r, "callback_data": d} for r, d in botoes]]}
        resposta = await self._chamar("sendMessage", json=corpo)
        dados = _json(resposta)
        if resposta.status_code != 200 or dados.get("ok") is not True:
            raise self._falha(resposta)
        return _message_id(dados)

    async def receber(self, offset: int, *, espera_s: int) -> list[Mapping[str, object]]:
        """Long-poll do `getUpdates` COM offset (confirma tudo abaixo dele). O `offset` vem do banco
        (`MAX(update_id) + 1`), e a update só é confirmada depois de gravada. 409 = outro consumidor do bot."""
        resposta = await self._chamar("getUpdates", params={
            "offset": offset, "timeout": espera_s, "limit": 50, "allowed_updates": UPDATES_DA_ENTRADA},
            timeout_s=espera_s + FOLGA_DO_LONG_POLL_S)
        if resposta.status_code == 409:
            raise ConflitoDeConsumidor("outro processo lê este bot (409 no getUpdates)", definitiva=True)
        dados = _json(resposta)
        if resposta.status_code != 200 or dados.get("ok") is not True:
            raise self._falha(resposta)
        resultado = dados.get("result")
        return [u for u in resultado if isinstance(u, Mapping)] if isinstance(resultado, list) else []

    async def confirmar_botao(self, callback_id: str, texto: str = "") -> None:
        """`answerCallbackQuery`: tira o relógio do botão no aplicativo da pessoa. Falhar aqui não desfaz a ação."""
        resposta = await self._chamar("answerCallbackQuery", json={"callback_query_id": callback_id,
                                                                   "text": texto[:190]})
        if resposta.status_code != 200 or _json(resposta).get("ok") is not True:
            raise self._falha(resposta)

    async def tirar_botoes(self, message_id: int) -> None:
        """Some com o teclado da prévia depois do toque: o segundo toque não acha botão (a idempotência por estado é a
        garantia; isto é só a tela)."""
        resposta = await self._chamar("editMessageReplyMarkup", json={
            "chat_id": self._chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
        if resposta.status_code != 200 or _json(resposta).get("ok") is not True:
            raise self._falha(resposta)

    async def apagar(self, message_id: int) -> bool:
        """`deleteMessage` da mensagem da pessoa que tinha cara de credencial (contrato dos canais, §7). Em chat privado
        o bot apaga a mensagem recebida (até 48 h). Devolve se apagou: o Telegram recusa com 400 o que não pode apagar,
        e aí quem responde pede ao dono que apague."""
        resposta = await self._chamar("deleteMessage", json={"chat_id": self._chat_id, "message_id": message_id})
        return resposta.status_code == 200 and _json(resposta).get("ok") is True

    async def descobrir_chats(self) -> list[ChatEncontrado]:
        """Os chats que escreveram ao bot. `getUpdates` SEM `offset`: nada é confirmado, então rodar isto de novo
        mostra a mesma lista (confirmar descartaria os updates). Só devolve id, tipo, título e @username."""
        resposta = await self._chamar("getUpdates", params={"limit": 100, "timeout": 0})
        corpo = _json(resposta)
        if resposta.status_code != 200 or corpo.get("ok") is not True:
            falha = self._falha(resposta)
            raise FalhaDeEnvio(falha.motivo, definitiva=True)
        achados: dict[int, ChatEncontrado] = {}
        resultado = corpo.get("result")
        for update in resultado if isinstance(resultado, list) else []:
            if not isinstance(update, Mapping):
                continue
            for campo in ("message", "edited_message", "channel_post", "my_chat_member"):
                item = update.get(campo)
                chat = item.get("chat") if isinstance(item, Mapping) else None
                if isinstance(chat, Mapping) and isinstance(chat.get("id"), int):
                    achados.setdefault(int(chat["id"]), _chat(chat))     # o 1º update do chat basta
        return list(achados.values())


def _chat(chat: Mapping[str, object]) -> ChatEncontrado:
    titulo = chat.get("title")
    if not isinstance(titulo, str):
        nome = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if isinstance(p, str))
        titulo = nome or None
    usuario = chat.get("username")
    return ChatEncontrado(id=int(str(chat["id"])), tipo=str(chat.get("type") or "?"), titulo=titulo,
                          username=usuario if isinstance(usuario, str) else None)
