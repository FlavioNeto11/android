"""A rota do webhook do Trello (item 32.2, passo 5; `docs/design/trello-integracao.md`, §8). Só HTTP: lê o corpo com teto,
chama a `PortaDoWebhook` e devolve o código. A regra (assinatura, falha fechada, só o id da action) mora na porta.

A rota fica FORA do login, e só ela: `main.guarda` libera sem credencial exatamente `HEAD` e `POST` neste caminho (comparação
do caminho inteiro, sem prefixo e sem curinga). O que autentica é a assinatura do Trello. Ela não exige nem honra sessão.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from app.modules.avisos.infrastructure.trello_webhook import CABECALHO, PortaDoWebhook

log = logging.getLogger("poc.avisos.trello.webhook")

ROTA_DO_WEBHOOK_DO_TRELLO = "/api/canais/trello/webhook"
#: Os dois métodos que o portão libera neste caminho (o HEAD é a conferência do Trello ao cadastrar; o POST, a chamada).
METODOS_DO_WEBHOOK_DO_TRELLO = frozenset({"HEAD", "POST"})

router = APIRouter()


def _porta(request: Request) -> PortaDoWebhook | None:
    porta = getattr(getattr(request.app.state, "poc", None), "trello_webhook", None)
    return porta if isinstance(porta, PortaDoWebhook) else None


async def _corpo_limitado(request: Request, limite: int) -> bytes | None:
    """Os bytes crus do corpo, ou `None` se passam de `limite`. Para no primeiro byte a mais: o resto não é lido."""
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > limite:
        return None
    lido = bytearray()
    async for pedaco in request.stream():
        lido += pedaco
        if len(lido) > limite:
            return None
    return bytes(lido)


@router.head(ROTA_DO_WEBHOOK_DO_TRELLO, include_in_schema=False)
async def conferir_webhook_do_trello(request: Request) -> Response:
    porta = _porta(request)
    return Response(status_code=porta.head().status if porta is not None else 404)


@router.post(ROTA_DO_WEBHOOK_DO_TRELLO, include_in_schema=False)
async def receber_webhook_do_trello(request: Request) -> Response:
    porta = _porta(request)
    if porta is None or not porta.ligada:
        return Response(status_code=404)               # como se a rota não existisse
    if not porta.pronta():
        log.warning("trello: webhook ligado, mas sem segredo ou sem URL: recusado (veredito=sem_config)")
        return Response(status_code=401)
    corpo = await _corpo_limitado(request, porta.max_bytes)
    if corpo is None:
        log.warning("trello: webhook com corpo acima do teto (veredito=grande)")
        return Response(status_code=413)
    veredito = porta.receber(corpo, request.headers.get(CABECALHO))
    log.info("trello: webhook veredito=%s", veredito.motivo)       # nada do corpo: nem autor, nem texto, nem id
    return Response(status_code=veredito.status)
