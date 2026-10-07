"""O roteador do aprendizado, montado em `main.py`. A ORDEM importa: as rotas específicas dos pacotes seguintes
(`/api/aprendizado/alcance`, `/apps`, `/receitas/{id}/rendimento`, `/falhas`, `/sinais`, `/licoes/previa`,
`/export`, `/voz/previa`, `/intencao`, `/execucao/{run_id}/intencao`, `/execucao/{run_id}/ensino`, `/metricas`,
`/revisoes`, `/validacoes`, `/api/runs/{id}/feedback`) entram
ANTES do livro, cuja rota genérica `{kind}/{ref}` casaria com elas e recusaria o `kind` com 422."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.learning.presentation import (alcance, apps, ensino_da_execucao, falhas, feedback, intencao, licoes,
                                               livro, metricas, rendimento, telas, validacoes, voz)

router = APIRouter()
for especifico in (alcance.router, apps.router, ensino_da_execucao.router, falhas.router, feedback.router,
                   intencao.router, licoes.router, metricas.router, rendimento.router, telas.router, validacoes.router,
                   voz.router):
    router.include_router(especifico)
router.include_router(livro.router)
