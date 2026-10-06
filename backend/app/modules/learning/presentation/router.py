"""O roteador do aprendizado, montado em `main.py`. A ORDEM importa: as rotas específicas dos pacotes seguintes
(`/api/aprendizado/alcance`, `/apps`, `/falhas`, `/sinais`, `/licoes/previa`, `/export`, `/voz/previa`, `/intencao`,
`/execucao/{run_id}/intencao`, `/metricas`, `/revisoes`, `/validacoes`, `/api/runs/{id}/feedback`) entram
ANTES do livro, cuja rota genérica `{kind}/{ref}` casaria com elas e recusaria o `kind` com 422."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.learning.presentation import (alcance, apps, falhas, feedback, intencao, licoes, livro, metricas,
                                               telas, validacoes, voz)

router = APIRouter()
for especifico in (alcance.router, apps.router, falhas.router, feedback.router, intencao.router, licoes.router,
                   metricas.router, telas.router, validacoes.router, voz.router):
    router.include_router(especifico)
router.include_router(livro.router)
