"""Corpo de `POST /api/skills/resolve` (fase I): a prévia da RESOLVE, sem criar execução.

É contrato com o painel, e por isso mora em `contracts` (só pydantic). A resposta é a forma de
`IntentResolution.as_dict()` mais `gated_by_config` — os interruptores que moldaram a resposta, porque a mesma frase
resolve diferente com `skills.enabled` ou `ai.flows` mudados.

Os limites seguem os de `RunCreate` (`models.py`): o que não caberia numa execução não precisa de prévia.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=4000)
    #: Os aparelhos da execução pretendida: o escopo da habilidade é conferido com o perfil vinculado a cada um,
    #: como no planejamento. Sem aparelhos nem perfis, é a prévia sem escopo (qualquer habilidade serve).
    instance_ids: list[str] = Field(default_factory=list, max_length=64)
    profile_ids: list[str] = Field(default_factory=list, max_length=64)
