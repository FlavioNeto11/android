"""Corpos das rotas do treinamento que nasceram depois do fatiamento de `app.models` (29.x): corpo novo nasce na
apresentação do contexto (`tests/test_models_fatiado.py`). `TrainingStartBody` e `TrainingSaveBody` seguem em
`app.models`, pelo motivo de lá."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TrainingStopBody(BaseModel):
    """Encerrar ou descartar o treinamento (31.92). Numa gravação VIVA exige o controle do aparelho: `lease_id` é o do
    controle atual. Gravação órfã ou já gravada dispensa."""

    model_config = ConfigDict(extra="forbid")
    lease_id: str | None = Field(default=None, min_length=1, max_length=120)
