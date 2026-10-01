"""Rota de leitura do retrieval de contexto: o contrato que o painel consome (`GET /api/context-retrieval/status`).

Só leitura e sem efeito: não consulta código, não chama provedor e não gasta nada. Entrega o modo efetivo, se o
provedor está disponível (chave configurada — nunca a chave), o que a política de envio decide para este repositório
e o resumo das métricas recentes (pedidos, cache, latência, custo, fallbacks e bloqueios de privacidade). Nunca
devolve código, a pergunta ou caminhos de arquivo.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ....config import Config
from ..wiring import estado_do_retrieval

router = APIRouter(prefix="/api")


@router.get("/context-retrieval/status")
def status(request: Request) -> dict[str, Any]:
    poc: object = getattr(request.app.state, "poc", None)
    cfg = getattr(poc, "cfg", None)
    if not isinstance(cfg, Config):
        raise HTTPException(503, detail={"code": "not_ready", "message": "A configuração ainda não foi composta."})
    return estado_do_retrieval(cfg)
