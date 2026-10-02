"""Canal Telegram do aviso fora do painel (item 28.11): só SAÍDA, por HTTPS, para a Bot API.

Duas chamadas, nada mais: `sendMessage` (o aviso) e `getUpdates` (a descoberta do `chat_id`, uma vez, quando o dono
manda `/start` ao bot). Sem webhook e sem rota de entrada: o central nunca recebe nada do Telegram.

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


class TokenAusente(Exception):
    """Sem `TELEGRAM_BOT_TOKEN`. A mensagem diz o que fazer; não há valor a esconder."""


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
                      params: Mapping[str, str | int] | None = None) -> httpx.Response:
        """Uma chamada, com o erro de rede já convertido em `FalhaDeEnvio` sem URL nem token."""
        cliente = self._client or httpx.AsyncClient()
        try:
            return await cliente.request("POST" if json is not None else "GET", self._url(metodo),
                                         json=json, params=params, timeout=self._timeout)
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
            return FalhaDeEnvio(f"Telegram pediu para esperar (429){detalhe}", espera_s=_espera_pedida(resposta, corpo))
        return FalhaDeEnvio(f"Telegram recusou ({status}){detalhe}", definitiva=status in DEFINITIVOS)

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> None:
        resposta = await self._chamar("sendMessage", json={
            "chat_id": self._chat_id, "text": texto_da_mensagem(titulo, corpo, link)[:TEXTO_MAX],
            "disable_web_page_preview": True})
        if resposta.status_code != 200 or _json(resposta).get("ok") is not True:
            raise self._falha(resposta)

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
