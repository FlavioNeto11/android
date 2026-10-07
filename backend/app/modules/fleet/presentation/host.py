"""As amostras do host (`GET /api/host/amostras`, 31.180, adendo v1.102): a série do amostrador permanente (29.156) para a
tela do Portal. Só leitura, atrás do login. Montado em `main.py` junto dos outros routers da frota; não importa `app.api`
(ciclo): o estado é `request.app.state.poc` (`AppState` só em `TYPE_CHECKING`).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request

from app.modules.fleet.infrastructure.amostras_do_host import ler_amostras, pasta_das_amostras

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


@router.get("/host/amostras", response_model=None)
async def amostras_do_host(request: Request, horas: int = Query(24, ge=1, le=168)) -> dict[str, object]:
    """As amostras das últimas `horas` (1 a 168: a retenção do amostrador é de 7 dias), da mais antiga à mais nova, no
    contrato do painel do Portal (`{items: [...]}`). 404 `sem_amostras` quando não há CSV dos dias da janela."""
    agora = datetime.now(UTC)
    amostras = ler_amostras(pasta_das_amostras(_st(request).cfg.data_dir), horas, agora)
    if amostras is None:
        raise HTTPException(status_code=404, detail={"code": "sem_amostras", "message": "Não há amostras do host nesta "
                                                     "janela: o amostrador (29.156) não está gravando."})
    return {"items": amostras}
