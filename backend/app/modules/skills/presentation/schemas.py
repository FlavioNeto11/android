"""Corpos das rotas do treinamento que nasceram depois do fatiamento de `app.models` (29.x): corpo novo nasce na
apresentação do contexto (`tests/test_models_fatiado.py`). `TrainingStartBody` e `TrainingSaveBody` seguem em
`app.models`, pelo motivo de lá."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TrainingUndoBody(BaseModel):
    """Desfazer a última entrada da gravação VIVA (31.90-D). `lease_id` é o do controle atual; `seq`, opcional, é o
    número da entrada que a pessoa viu como última (outra chegou antes do pedido: 409 `entrada_mudou`)."""

    model_config = ConfigDict(extra="forbid")
    lease_id: str = Field(min_length=1, max_length=120)
    seq: int | None = Field(default=None, ge=1)


class TrainingStopBody(BaseModel):
    """Encerrar ou descartar o treinamento (31.92). Numa gravação VIVA exige o controle do aparelho: `lease_id` é o do
    controle atual. Gravação órfã ou já gravada dispensa."""

    model_config = ConfigDict(extra="forbid")
    lease_id: str | None = Field(default=None, min_length=1, max_length=120)


class TrainingDeFalhaBody(BaseModel):
    """Abre uma sessão de ensino a partir de uma etapa que falhou (31.111 F1). O aparelho é o da etapa; o resto é como o
    início de um treinamento: o controle da pessoa (`lease_id`) é obrigatório e nada acontece sozinho."""

    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=120)
    step_id: str = Field(min_length=1, max_length=300)
    lease_id: str = Field(min_length=1, max_length=120)
    #: Sem ele, o texto é derivado do título da etapa; a pessoa pode reescrever.
    intent: str | None = Field(default=None, min_length=1, max_length=400)
    app_id: str | None = Field(default=None, max_length=120)
    profile_id: str | None = Field(default=None, max_length=120)


class EscopoDoFluxoBody(BaseModel):
    """Ampliar ou restringir a quem a habilidade (fluxo) vale, depois de salva (31.88 F2). Vazio nos dois = todos."""

    model_config = ConfigDict(extra="forbid")
    profile_ids: list[str] = Field(default_factory=list, max_length=500)
    group_ids: list[str] = Field(default_factory=list, max_length=100)
