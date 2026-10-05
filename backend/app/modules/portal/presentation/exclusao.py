"""Exclusão de contatos do site a pedido do titular (29.83, ADR-075): as duas rotas da seção "Site e privacidade" da
Configuração.

Atrás de sessão, e mais do que o portão: o `guarda` deixa passar o loopback sem credencial e o Bearer (que é
anônimo), e aqui quem aperta "Apagar definitivamente" tem de ser uma PESSOA com nome, na sessão dela. Sem
`request.state.operador`, 401; nenhuma automação chama estas rotas. Nenhuma delas entra na exceção do portão: a única
rota pública do portal segue sendo `POST /api/portal/contato`.

A busca é `POST` (e não `GET ?telefone=`) para o telefone não ir para a URL nem para o log de acesso.
"""
from __future__ import annotations

import json
import time
from collections.abc import Mapping

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.modules.portal.application.exclusao import MuitasBuscas, PedidoInvalido
from app.modules.portal.montagem import Portal
from app.shared.costuras import autor_do_gesto
from app.util import now

ROTA_DA_BUSCA = "/api/portal/contatos/busca"
ROTA_DA_EXCLUSAO = "/api/portal/contatos/excluir"

#: Os dois corpos são pequenos (um telefone; até 50 ids): o teto barra o resto antes de ler.
CORPO_MAX_BYTES = 4096

router = APIRouter()

_SEM_SESSAO = {"detail": {"code": "sessao_exigida",
                          "message": "Esta ação é de uma pessoa logada no painel: entre com o seu nome e tente de novo."}}


def _invalido(erro: PedidoInvalido) -> JSONResponse:
    return JSONResponse({"detail": {"code": erro.code, "message": str(erro)}}, status_code=422)


async def _preparar(request: Request) -> tuple[Portal, Mapping[str, object]] | Response:
    """O portal, a pessoa e o corpo JSON; ou a resposta de recusa."""
    if getattr(request.state, "operador", None) is None:
        return JSONResponse(_SEM_SESSAO, status_code=401)
    portal = getattr(getattr(request.app.state, "poc", None), "portal", None)
    if not isinstance(portal, Portal):
        return Response(status_code=404)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return Response(status_code=415)
    corpo = bytearray()
    async for pedaco in request.stream():
        corpo += pedaco
        if len(corpo) > CORPO_MAX_BYTES:
            return Response(status_code=413)
    try:
        dados = json.loads(corpo)
    except (ValueError, UnicodeDecodeError):
        return _invalido(PedidoInvalido("corpo_invalido", "O corpo tem de ser JSON."))
    if not isinstance(dados, dict):
        return _invalido(PedidoInvalido("corpo_invalido", "O corpo tem de ser um objeto JSON."))
    return portal, dados


@router.post(ROTA_DA_BUSCA)
async def buscar_contatos(request: Request) -> Response:
    preparo = await _preparar(request)
    if isinstance(preparo, Response):
        return preparo
    portal, dados = preparo
    try:
        contatos = await run_in_threadpool(portal.exclusao.buscar, dados.get("telefone"),
                                           operador=autor_do_gesto(request.state.operador), agora_s=time.monotonic())
    except PedidoInvalido as erro:
        return _invalido(erro)
    except MuitasBuscas as erro:
        return JSONResponse(
            {"detail": {"code": "muitas_buscas",
                        "message": "Muitas buscas nesta hora. Espere um pouco e tente de novo."}},
            status_code=429, headers={"Retry-After": str(erro.espera_s)})
    return JSONResponse({"contatos": contatos}, headers={"Cache-Control": "no-store"})


@router.post(ROTA_DA_EXCLUSAO)
async def excluir_contatos(request: Request) -> Response:
    preparo = await _preparar(request)
    if isinstance(preparo, Response):
        return preparo
    portal, dados = preparo
    try:
        pedido = await run_in_threadpool(portal.exclusao.preparar, dados)
    except PedidoInvalido as erro:
        return _invalido(erro)
    agora = now()
    resultado = await portal.exclusao.decidir(pedido, agora)
    resultado = await run_in_threadpool(portal.exclusao.concluir, pedido, resultado,
                                        executado_por=autor_do_gesto(request.state.operador), agora=agora)
    return JSONResponse(resultado.corpo(), headers={"Cache-Control": "no-store"})
