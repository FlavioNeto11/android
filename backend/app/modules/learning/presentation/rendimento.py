"""31.191: `GET /api/aprendizado/receitas/{id}/rendimento`, o rendimento de UMA receita (adendo v1.110). Só leitura,
sem IA; o leitor é o do 31.177 (`infrastructure/rendimento_sql.py`), com a mesma régua do uso real.

Entra em `router.py` ANTES do livro, como as outras rotas específicas."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.db import Database
from app.modules.learning.infrastructure.rendimento_sql import LeitorDoRendimento

router = APIRouter(prefix="/api/aprendizado")


@router.get("/receitas/{recipe_id}/rendimento", response_model=None)
def rendimento_da_receita(request: Request, recipe_id: int) -> dict[str, object]:
    """Por uso (`real`, `prova`, `simulada`): as etapas que a receita conduziu sem IA, as em que caiu na IA e as outras;
    as reproduções, o último uso e o custo de IA evitado (nulo sem referência de custo na retenção)."""
    poc: object = getattr(request.app.state, "poc", None)
    db = getattr(poc, "db", None)
    agendador = getattr(poc, "scheduler", None)
    receitas = getattr(getattr(agendador, "executor", None), "recipes", None)
    fluxos = getattr(agendador, "flows", None)
    cfg = getattr(poc, "cfg", None)
    if not isinstance(db, Database) or receitas is None or fluxos is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "O aprendizado ainda não foi composto."})
    leitor = LeitorDoRendimento(db, liberada=receitas.liberada_fora_do_ensino,
                                em_uso_real_desde=fluxos.em_uso_real_desde,
                                precos=lambda: getattr(getattr(getattr(cfg, "file", None), "ai", None), "prices", {}) or {})
    rendimento = leitor.da_receita(recipe_id)
    if rendimento is None:
        raise HTTPException(404, detail={"code": "receita_desconhecida", "message": "Receita não encontrada."})
    return {**rendimento, "gerado_em": db.agora_iso()}
