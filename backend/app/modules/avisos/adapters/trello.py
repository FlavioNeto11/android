"""Cliente REST do Trello do dono (item 32.2, ADR-072; desenho em `docs/design/trello-integracao.md`, §1 e §7.2).

A autenticação é a chave do Power-Up e o token do dono, e vai SÓ no cabeçalho
`Authorization: OAuth oauth_consumer_key="…", oauth_token="…"`: nunca na URL (nem no caminho, nem na query), nunca em
log. Por isso o cliente não usa `/1/tokens/{token}/…`: os webhooks se leem por `webhook(id)` e `webhooks_do_membro()`.
Todo erro que sai daqui é montado a partir do NOME da exceção e do código HTTP, jamais do `str()`
dela (o de `httpx` costuma trazer a URL), e o texto que o Trello devolve passa por `_limpar`, que tira a chave e o token
mesmo que apareçam ali. A redação central (`security.redaction`) é a segunda camada, não a primeira.

Limites do Trello: 100 requisições por 10 s por token e 300 por chave. O balde daqui deixa passar 60 por 10 s (folga para
o outro consumidor da mesma chave), e o 429 espera o que o Trello pediu e repete até `MAX_REPETICOES` vezes.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import quote

import httpx

from app.security.redaction import redact

API = "https://api.trello.com"
#: Filtro padrão da reconciliação do quadro: o comentário, o cartão movido de lista e o cartão novo.
FILTRO_PADRAO = "commentCard,updateCard:idList,createCard"
#: O balde: 60 requisições por 10 s (o limite do Trello por token é 100).
BALDE_LIMITE = 60
BALDE_JANELA_S = 10.0
#: Quantas vezes o mesmo pedido é repetido depois de um 429; persistindo, `FalhaDoTrello(status=429)`.
MAX_REPETICOES = 3
#: Espera do 429 quando o Trello não diz quanto, e o teto da espera (um cabeçalho absurdo não trava o laço).
ESPERA_429_PADRAO_S = 10.0
ESPERA_429_MAX_S = 60.0
DESCRICAO_MAX = 160
#: O que o cliente devolve de um webhook (a descrição é a marca `central-de-aparelhos:<quadro>` do cadastro).
CAMPOS_DO_WEBHOOK = ("id", "description", "idModel", "callbackURL", "active")

#: O JSON do Trello, sem tipos ricos por enquanto: um objeto ou uma lista.
Json = dict[str, object] | list[object]


class FalhaDoTrello(Exception):
    """Falha ao falar com o Trello. `motivo` é um texto curto e sem URL, chave nem token. `definitiva` = repetir o mesmo
    pedido não muda nada (credencial recusada, recurso inexistente); `status` é o código HTTP, quando houve resposta."""

    def __init__(self, motivo: str, *, status: int | None = None, definitiva: bool = False):
        super().__init__(motivo)
        self.motivo = motivo
        self.status = status
        self.definitiva = definitiva


class BaldeDeRequisicoes:
    """Janela deslizante: no máximo `limite` retiradas em qualquer intervalo de `janela_s`. Relógio e sono injetáveis (o
    teste anda com um relógio falso, sem dormir de verdade)."""

    def __init__(self, limite: int = BALDE_LIMITE, janela_s: float = BALDE_JANELA_S, *,
                 relogio: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._limite = limite
        self._janela = janela_s
        self._relogio = relogio
        self._dormir = dormir
        self._marcas: deque[float] = deque()
        self._trava = asyncio.Lock()

    async def tomar(self) -> None:
        """Espera, se preciso, até haver vaga na janela e a ocupa."""
        async with self._trava:
            while True:
                agora = self._relogio()
                while self._marcas and agora - self._marcas[0] >= self._janela:
                    self._marcas.popleft()
                if len(self._marcas) < self._limite:
                    self._marcas.append(agora)
                    return
                await self._dormir(max(self._janela - (agora - self._marcas[0]), 0.001))


def _limpar(texto: str, *segredos: str) -> str:
    """Descrição curta, de uma linha e sem a chave nem o token (o Trello não os devolve, mas a garantia é nossa)."""
    sem = redact(texto) or ""
    for segredo in segredos:
        if segredo:
            sem = sem.replace(segredo, "***")
    return " ".join(sem.split())[:DESCRICAO_MAX]


def _so_webhook(bruto: Mapping[str, object]) -> dict[str, object]:
    """Reduz um webhook aos campos que a Central usa; o resto (e qualquer campo de token) fica para trás."""
    return {k: bruto[k] for k in CAMPOS_DO_WEBHOOK if k in bruto}


def _numero(bruto: object) -> float | None:
    try:
        return float(str(bruto).strip())
    except (TypeError, ValueError):
        return None


def _espera_do_429(resposta: httpx.Response) -> float:
    """`Retry-After` (segundos); senão o maior `x-rate-limit-*-interval-ms` (a janela do Trello); senão 10 s."""
    s = _numero(resposta.headers.get("retry-after"))
    if s is None:
        intervalos = [ms / 1000.0 for nome, valor in resposta.headers.items()
                      if nome.lower().startswith("x-rate-limit-") and nome.lower().endswith("-interval-ms")
                      if (ms := _numero(valor)) is not None]
        s = max(intervalos) if intervalos else ESPERA_429_PADRAO_S
    return min(max(s, 0.0), ESPERA_429_MAX_S)


class ClienteTrello:
    def __init__(self, chave: str, token: str, *, client: httpx.AsyncClient | None = None, timeout_s: float = 20.0,
                 balde: BaldeDeRequisicoes | None = None,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not chave or not token:
            raise ValueError("TRELLO_API_KEY e TRELLO_TOKEN são obrigatórios (docs/operacao.md)")
        self._chave = chave
        self._token = token
        self._client = client
        self._timeout = timeout_s
        self._dormir = dormir
        self._balde = balde or BaldeDeRequisicoes(dormir=dormir)

    # ------------------------------------------------------------------ transporte
    def _cabecalhos(self) -> dict[str, str]:
        return {"Authorization": f'OAuth oauth_consumer_key="{self._chave}", oauth_token="{self._token}"',
                "Accept": "application/json"}

    def _falha(self, resposta: httpx.Response) -> FalhaDoTrello:
        status = resposta.status_code
        detalhe = _limpar(resposta.text[:400], self._chave, self._token)
        motivo = f"Trello recusou ({status})" + (f": {detalhe}" if detalhe else "")
        # 5xx: o Trello caiu, vale tentar de novo depois. 4xx (credencial, recurso inexistente, pedido inválido):
        # o mesmo pedido dará a mesma resposta.
        return FalhaDoTrello(motivo, status=status, definitiva=400 <= status < 500)

    async def _enviar(self, metodo: str, caminho: str, params: Mapping[str, str | int] | None,
                      corpo: Mapping[str, object] | None) -> httpx.Response:
        cliente = self._client or httpx.AsyncClient()
        try:
            return await cliente.request(metodo, f"{API}{caminho}", params=params, json=corpo,
                                         headers=self._cabecalhos(), timeout=self._timeout)
        except httpx.TimeoutException:
            raise FalhaDoTrello("tempo esgotado ao falar com o Trello") from None
        except httpx.HTTPError as exc:
            raise FalhaDoTrello(f"falha de rede ao falar com o Trello ({type(exc).__name__})") from None
        finally:
            if self._client is None:
                await cliente.aclose()

    async def _pedir(self, metodo: str, caminho: str, *, params: Mapping[str, str | int] | None = None,
                     corpo: Mapping[str, object] | None = None) -> Json:
        for tentativa in range(MAX_REPETICOES + 1):
            await self._balde.tomar()
            resposta = await self._enviar(metodo, caminho, params, corpo)
            if resposta.status_code == 429:
                if tentativa == MAX_REPETICOES:
                    raise FalhaDoTrello("Trello pediu para esperar (429) e continuou pedindo", status=429)
                await self._dormir(_espera_do_429(resposta))
                continue
            if resposta.status_code >= 400:
                raise self._falha(resposta)
            if not resposta.content.strip():
                return {}
            try:
                json = resposta.json()
            except ValueError:
                raise FalhaDoTrello("resposta do Trello não é JSON", status=resposta.status_code) from None
            if not isinstance(json, (dict, list)):
                raise FalhaDoTrello("resposta do Trello com formato inesperado", status=resposta.status_code)
            return json
        raise AssertionError("inalcançável")  # pragma: no cover - o laço sempre devolve ou levanta

    @staticmethod
    def _objeto(resposta: Json, o_que: str) -> dict[str, object]:
        if not isinstance(resposta, dict):
            raise FalhaDoTrello(f"resposta do Trello sem {o_que}")
        return resposta

    # ------------------------------------------------------------------ leitura
    async def acao(self, id_acao: str) -> Json:
        """`GET /1/actions/{id}`: a verdade sobre uma action (autor, cartão, quadro, data, texto)."""
        return await self._pedir("GET", f"/1/actions/{quote(id_acao, safe='')}")

    async def acoes_do_quadro(self, quadro: str, desde: str | None = None, filtro: str = FILTRO_PADRAO,
                              limite: int = 1000) -> Json:
        """`GET /1/boards/{id}/actions`: as actions do quadro desde `desde` (data ISO ou id de action; `None` = as mais
        novas). O Trello devolve da mais nova para a mais velha."""
        params: dict[str, str | int] = {"filter": filtro, "limit": limite}
        if desde:
            params["since"] = desde
        return await self._pedir("GET", f"/1/boards/{quote(quadro, safe='')}/actions", params=params)

    # ------------------------------------------------------------------ cartões
    async def criar_cartao(self, lista: str, nome: str, desc: str) -> Json:
        return await self._pedir("POST", "/1/cards", corpo={"idList": lista, "name": nome, "desc": desc})

    async def atualizar_cartao(self, card: str, *, nome: str | None = None, desc: str | None = None,
                               lista: str | None = None) -> Json:
        campos = {"name": nome, "desc": desc, "idList": lista}
        corpo = {k: v for k, v in campos.items() if v is not None}
        if not corpo:
            raise ValueError("atualizar_cartao sem nenhum campo a mudar")
        return await self._pedir("PUT", f"/1/cards/{quote(card, safe='')}", corpo=corpo)

    async def arquivar_cartao(self, card: str) -> Json:
        return await self._pedir("PUT", f"/1/cards/{quote(card, safe='')}", corpo={"closed": True})

    async def comentar(self, card: str, texto: str) -> str:
        """Comenta no cartão e devolve o id da action do comentário (a `ref_mensagem` da `canal_enviadas`)."""
        resposta = self._objeto(await self._pedir("POST", f"/1/cards/{quote(card, safe='')}/actions/comments",
                                                  corpo={"text": texto}), "o comentário criado")
        id_acao = resposta.get("id")
        if not isinstance(id_acao, str) or not id_acao:
            raise FalhaDoTrello("o Trello não devolveu o id do comentário")
        return id_acao

    # ------------------------------------------------------------------ webhooks (§8 do desenho)
    async def webhook(self, id_webhook: str) -> dict[str, object]:
        """`GET /1/webhooks/{id}`: um webhook (só os campos de `CAMPOS_DO_WEBHOOK`)."""
        resposta = self._objeto(await self._pedir("GET", f"/1/webhooks/{quote(id_webhook, safe='')}"), "o webhook")
        return _so_webhook(resposta)

    async def webhooks_do_membro(self) -> list[dict[str, object]]:
        """`GET /1/members/me/tokens?webhooks=true`: os webhooks de todos os tokens do dono, achatados numa lista. A rota
        devolve dados de TOKEN (identificador, permissões, validade); nada disso sai daqui: só os campos de
        `CAMPOS_DO_WEBHOOK` de cada webhook. O token próprio nunca vai na URL."""
        tokens = await self._pedir("GET", "/1/members/me/tokens", params={"webhooks": "true"})
        if not isinstance(tokens, list):
            raise FalhaDoTrello("resposta do Trello sem a lista de tokens")
        saida: list[dict[str, object]] = []
        for token in tokens:
            filhos = token.get("webhooks") if isinstance(token, dict) else None
            saida += [_so_webhook(w) for w in filhos or [] if isinstance(w, dict)]
        return saida

    async def criar_webhook(self, id_modelo: str, callback_url: str, descricao: str) -> Json:
        """O Trello faz um HEAD na `callback_url` antes de criar; sem 200, o webhook não nasce."""
        return await self._pedir("POST", "/1/webhooks", corpo={
            "idModel": id_modelo, "callbackURL": callback_url, "description": descricao})

    async def apagar_webhook(self, id_webhook: str) -> Json:
        return await self._pedir("DELETE", f"/1/webhooks/{quote(id_webhook, safe='')}")
