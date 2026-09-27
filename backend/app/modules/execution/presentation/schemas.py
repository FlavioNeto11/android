"""Corpos de requisição HTTP da execução: as decisões de aprovação, uma e em lote.

Movidos literalmente de `app/models.py` (design §16, fase K: "primeiro os corpos"); `app.models` reexporta os
MESMOS objetos, e o nome de cada classe continua sendo o nome do esquema no contrato HTTP. Este módulo não
importa `app.models`: o reexport importa daqui, e o caminho de volta fecharia um ciclo de import de topo.

Ficaram em `app.models`: `RunCreate`, `DistributeSpec` e `ResolveBody`, que a fila (`taskqueue`) também
importa.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApprovalDecision(BaseModel):
    """Os três verbos do §17. `content` só faz sentido em `edit` — é o texto que realmente será enviado."""

    model_config = ConfigDict(extra="forbid")
    verb: Literal["approve", "edit", "reject"]
    content: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=400)


class ApprovalDecisionItem(ApprovalDecision):
    """Uma decisão dentro de um lote: o mesmo contrato, mais o id de quem está sendo decidido."""

    id: str = Field(min_length=1, max_length=120)


class ApprovalBatchBody(BaseModel):
    """Decidir os N textos de uma execução de uma vez, cada um com o seu verbo — aprovar uns, editar outros."""

    model_config = ConfigDict(extra="forbid")
    decisions: list[ApprovalDecisionItem] = Field(min_length=1, max_length=50)
