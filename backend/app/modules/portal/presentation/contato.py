"""`POST /api/portal/contato`: o formulário do site institucional (29.77, ADR-075). A ÚNICA rota de `/api/` do portal.

Sem credencial, como o webhook do Trello: o `guarda` perdoa o 401 só neste caminho e só para `POST`; `forbidden_host` e
a checagem de `Origin` seguem valendo (o site posta da mesma origem). Com `portal.contato_ligado` desligado responde 404,
como se não existisse. O corpo é JSON, com teto lido em fluxo; o que a rota responde vem de `ServicoDeContato`.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.modules.portal.montagem import Portal

ROTA_DO_CONTATO = "/api/portal/contato"
METODOS_DO_CONTATO = frozenset({"POST"})

router = APIRouter()


async def _corpo_limitado(request: Request, limite: int) -> bytes | None:
    """Os bytes crus do corpo, ou `None` se passam de `limite`. Para no primeiro byte a mais: o resto não é lido.
    (O mesmo do webhook do Trello; repetido para o portal não depender da apresentação de outro contexto.)"""
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > limite:
        return None
    lido = bytearray()
    async for pedaco in request.stream():
        lido += pedaco
        if len(lido) > limite:
            return None
    return bytes(lido)


@router.post(ROTA_DO_CONTATO, include_in_schema=False)
async def receber_contato(request: Request) -> Response:
    portal = getattr(getattr(request.app.state, "poc", None), "portal", None)
    if not isinstance(portal, Portal) or not portal.contato_ligado:
        return Response(status_code=404)
    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if tipo != "application/json":
        return Response(status_code=415)                 # antes de ler um byte do corpo
    corpo = await _corpo_limitado(request, portal.cfg.file.portal.limites.corpo_max_bytes)
    if corpo is None:
        return Response(status_code=413)
    try:
        dados = json.loads(corpo)
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"detail": {"code": "campo_invalido", "message": "Confira os campos do formulário.",
                                        "campos": []}}, status_code=422)
    cliente = str(getattr(request.state, "cliente", None) or "desconhecido")
    resposta = await run_in_threadpool(portal.contatos.receber, dados, cliente, portal.relogio.agora())
    return JSONResponse(dict(resposta.corpo), status_code=resposta.status, headers={"Cache-Control": "no-store"})
