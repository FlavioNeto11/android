"""31.181: `GET /api/aprendizado/alcance?app=<app_id>`, quem pode usar o quê hoje num app, por persona vinculada. Só
leitura, sem IA (adendo v1.107).

Entra em `router.py` ANTES do livro, como as outras rotas específicas."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.db import Database
from app.modules.learning.infrastructure.alcance_sql import LeitorDoAlcance

router = APIRouter(prefix="/api/aprendizado")


def _leitor(request: Request) -> tuple[LeitorDoAlcance, object]:
    poc: object = getattr(request.app.state, "poc", None)
    db = getattr(poc, "db", None)
    agendador = getattr(poc, "scheduler", None)
    receitas = getattr(getattr(agendador, "executor", None), "recipes", None)
    fluxos = getattr(agendador, "flows", None)
    if not isinstance(db, Database) or receitas is None or fluxos is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "O aprendizado ainda não foi composto."})
    # As regras de quem decide na execução: a do 30.81 (receita do ensino) e a do escopo do fluxo.
    return LeitorDoAlcance(db, receita_presa=lambda linha, persona: bool(receitas._restrita_ao_ensino(linha, persona)),
                           fluxo_no_escopo=lambda fluxo, persona: bool(fluxos._no_escopo(fluxo, [persona]))), poc


@router.get("/alcance", response_model=None)
def alcance(request: Request, app: str = Query(..., min_length=1, max_length=80)) -> dict[str, object]:
    """Por persona vinculada ao app: cada receita ativa e cada fluxo (ligado ou candidato), com `pode` e o `motivo` do
    não (`presa_a_quem_ensinou`, `fora_do_escopo`, `fluxo_nao_ligado`)."""
    leitor, poc = _leitor(request)
    if leitor.db.one("SELECT id FROM apps WHERE id=?", (app,)) is None:
        raise HTTPException(404, detail={"code": "app_desconhecido", "message": "App não cadastrado."})
    pacote_do_app = getattr(poc, "_pacote_do_app_id", None)
    pacote = pacote_do_app(app) if callable(pacote_do_app) else None
    return {**leitor.ler(app, pacote if isinstance(pacote, str) else None), "gerado_em": leitor.db.agora_iso()}
