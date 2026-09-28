"""Corpos de requisição HTTP da execução: as decisões de aprovação, uma e em lote.

Movidos literalmente de `app/models.py` (design §16, fase K: "primeiro os corpos"); `app.models` reexporta os
MESMOS objetos, e o nome de cada classe continua sendo o nome do esquema no contrato HTTP. Este módulo não
importa `app.models`: o reexport importa daqui, e o caminho de volta fecharia um ciclo de import de topo.

Ficaram em `app.models`: `RunCreate`, `DistributeSpec` e `ResolveBody`, que a fila (`taskqueue`) também
importa. Os corpos do roteamento por persona (onda C: `RunTarget`, `DevicePolicy`, `RunTargetsResolveBody`) já
nasceram aqui, e `app.models` os reexporta para `RunCreate` usá-los.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


#: Quantos aparelhos de UMA persona recebem a tarefa: `one` = a pessoa faz uma vez (padrão, D4); `primary` = o
#: aparelho principal; `all` = todos os vinculados aptos (só explícito: efeito externo repetido, risco R11).
DevicePolicy = Literal["one", "primary", "all"]


def _sem_repetir(v: list[str]) -> list[str]:
    return list(dict.fromkeys(v))


class RunTarget(BaseModel):
    """Um alvo explícito da execução (onda C): a persona e, opcionalmente, os aparelhos dela e o app da conta que a
    tarefa usa. `instance_ids` vazio = o sistema escolhe pela `device_policy`. É o que a prévia
    (`POST /runs/targets/resolve`) devolve e o que o painel ecoa para confirmar."""

    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=100)
    instance_ids: list[str] = Field(default_factory=list, max_length=64)
    app_id: str | None = Field(default=None, max_length=80)

    @field_validator("instance_ids")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return _sem_repetir(v)


class RunTargetsResolveBody(BaseModel):
    """`POST /runs/targets/resolve`: a prévia dos alvos, com a mesma seleção de `RunCreate` e sem criar nada. A
    seleção pode vir vazia: aí quem decide é o texto do comando (e os alvos voltam com origem `texto`)."""

    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=3, max_length=4000)
    instance_ids: list[str] = Field(default_factory=list, max_length=64)
    profile_ids: list[str] = Field(default_factory=list, max_length=64)
    targets: list[RunTarget] = Field(default_factory=list, max_length=64)
    device_policy: DevicePolicy = "one"

    @field_validator("instance_ids", "profile_ids")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return _sem_repetir(v)
