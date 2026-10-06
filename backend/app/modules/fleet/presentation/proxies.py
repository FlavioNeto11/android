"""Os proxies do aparelho (`/api/proxies*`): listar, cadastrar, remover e aplicar (ou prever, com `dry_run`) um proxy nos aparelhos. Saíram
de `api.py` no 15.15 F4 (corte 10, F4j) sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra, depois do das rotas de Instagram. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response

from app.devices.proxy import ProxyApplyBody, ProxyError, ProxyInput, aplicar, criar, listar, remover
from app.modules.fleet.presentation.comum import quem

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _proxy_error(exc: ProxyError) -> HTTPException:
    return _err(exc.status, exc.code, exc.message)


@router.get("/proxies", response_model=None)
async def list_proxies(request: Request) -> object:
    return listar(_st(request))


@router.post("/proxies", status_code=201, response_model=None)
async def create_proxy(request: Request, body: ProxyInput) -> object:
    return criar(_st(request), body, quem(request))


@router.delete("/proxies/{proxy_id}", status_code=204)
async def delete_proxy(request: Request, proxy_id: str) -> Response:
    try:
        remover(_st(request), proxy_id)
    except ProxyError as exc:
        raise _proxy_error(exc) from exc
    return Response(status_code=204)


@router.post("/proxies/apply", response_model=None)
async def apply_proxy(request: Request, body: ProxyApplyBody) -> object:
    """Pede um proxy (ou nenhum, com `proxy_id` nulo) para os aparelhos. `dry_run` = prévia, nada é gravado."""
    try:
        devices = aplicar(_st(request), body)
    except ProxyError as exc:
        raise _proxy_error(exc) from exc
    return {"accepted": not body.dry_run, "dry_run": body.dry_run, "devices": devices}
