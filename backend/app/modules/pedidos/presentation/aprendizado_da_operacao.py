"""`GET /api/operacoes/{operacao_id}/aprendizado` (prova30 A3): as 10 perguntas do aprendizado do dono para UMA operação.

Só leitura e sem IA. Mora no módulo de pedidos porque a memória da operação mora aqui (125); a operação em si (124,
`/api/operacoes`) é da Jev, e este roteador só acrescenta um caminho de dois segmentos que não colide com o `/{id}`
dela. Composto na hora a partir do banco (nada no `state.py`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.db import Database
from app.modules.pedidos.domain.aprendizado_da_operacao import responder
from app.modules.pedidos.infrastructure.aprendizado_da_operacao import LeitorDoAprendizadoDaOperacao

router = APIRouter(prefix="/api/operacoes")


def _db(request: Request) -> Database:
    db = getattr(getattr(request.app.state, "poc", None), "db", None)
    if not isinstance(db, Database):
        raise HTTPException(503, detail={"code": "not_ready", "message": "O banco ainda não foi composto."})
    return db


@router.get("/{operacao_id}/aprendizado")
def aprendizado_da_operacao(request: Request, operacao_id: str, persona: str | None = None,
                            simulados: bool = False) -> dict[str, object]:
    """As 10 respostas com origem, evidência, confiança e frescor por item. `persona` (um `profile_id`) deixa o que é
    dela e o que é da operação inteira; `simulados` inclui a evidência e os sinais simulados (fora por padrão)."""
    db = _db(request)
    itens = LeitorDoAprendizadoDaOperacao(db).ler(operacao_id, simulados=simulados)
    if itens is None:
        raise HTTPException(404, detail={"code": "operacao_desconhecida",
                                         "message": "Operação sem execução nem memória (ou a 124 ainda não está no banco)."})
    return {"operacao_id": operacao_id, "gerado_em": db.agora_iso(), "simulados": simulados,
            **responder(itens, agora=db.agora_iso(), persona=persona)}
