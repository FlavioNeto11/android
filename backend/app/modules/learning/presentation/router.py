"""O roteador do aprendizado, montado em `main.py`. A ORDEM importa: as rotas específicas dos pacotes seguintes
(`/api/aprendizado/apps`, `/falhas`, `/sinais`, `/licoes/previa`, `/export`, `/voz/previa`, `/intencao`,
`/execucao/{run_id}/intencao`, `/metricas`, `/revisoes`, `/api/runs/{id}/feedback`) entram
ANTES do livro, cuja rota genérica `{kind}/{ref}` casaria com elas e recusaria o `kind` com 422."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.learning.presentation import apps, falhas, feedback, intencao, licoes, livro, metricas, telas, voz

router = APIRouter()
for especifico in (apps.router, falhas.router, feedback.router, intencao.router, licoes.router, metricas.router,
                   telas.router, voz.router):
    router.include_router(especifico)
router.include_router(livro.router)
