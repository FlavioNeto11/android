"""Casos de validação e observações (§10.7, 043) e a regra de `candidate → validated` (P4).

Mesmo desenho de `app_release_validations` (011): a transição lê OBSERVAÇÃO registrada — com prova, aparelho e
horário —, nunca um booleano. `not_run` é a ausência de linha, e ausência não aprova nada.

Regra P4 (decisão do coordenador, 27/09): um caso `device` só conta para `validated` com observação `proof=real`;
para os demais tipos, `simulated` basta. Uma versão sem prova real ainda pode ser validada por uma PESSOA, com motivo,
e isso fica registrado na transição — o que o repositório faz, não esta função.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.skills.domain.document import JsonObject
from app.modules.skills.domain.refs import SkillRef


class CaseKind(StrEnum):
    REPLAY = "replay"
    SIMULATED = "simulated"
    DEVICE = "device"
    NEGATIVE = "negative"                  # espera recusa: "caixa de entrada sem campo de escrita não prova OPEN_THREAD"


class CaseStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class Proof(StrEnum):
    REAL = "real"
    SIMULATED = "simulated"


class Outcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ValidationCase:
    id: str
    skill_id: str
    name: str
    kind: CaseKind
    since_version: int | None
    until_version: int | None
    parameters: JsonObject
    preconditions: JsonObject
    expected: JsonObject
    source_kind: str | None
    source_ref: str | None
    status: CaseStatus

    def applies_to(self, version: int) -> bool:
        """Vale para a versão `version`? Ativo e dentro da faixa (a regressão da v3 roda o que a v2 passou)."""
        return (self.status is CaseStatus.ACTIVE
                and (self.since_version is None or self.since_version <= version)
                and (self.until_version is None or version <= self.until_version))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    id: int
    case_id: str
    ref: SkillRef
    proof: Proof
    outcome: Outcome
    run_id: str | None
    instance_id: str | None
    physical_id: str | None
    app_version: str | None
    variant: str | None
    detail: str | None
    observed_at: str
    observed_by: str | None


@dataclass(frozen=True, slots=True)
class ValidationVerdict:
    ready: bool
    pending: tuple[str, ...]


def validation_verdict(version: int, cases: Sequence[ValidationCase],
                       results: Sequence[ValidationResult]) -> ValidationVerdict:
    """`candidate → validated` pelo sistema: há caso na faixa, e o ÚLTIMO resultado de cada um é `passed`.

    Para caso `device`, "último" é o último com `proof=real`: um `simulated` posterior não o substitui (nem para
    aprovar, nem para reprovar). `results` são os desta versão; a ordem é a de gravação (`id`).
    """
    aplicaveis = sorted((c for c in cases if c.applies_to(version)), key=lambda c: c.id)
    if not aplicaveis:
        return ValidationVerdict(False, ("nenhum caso de validação ativo vale para esta versão",))
    pendencias: list[str] = []
    for caso in aplicaveis:
        vistos = [r for r in sorted(results, key=lambda r: r.id) if r.case_id == caso.id]
        if caso.kind is CaseKind.DEVICE:
            vistos = [r for r in vistos if r.proof is Proof.REAL]
        if not vistos:
            falta = "sem observação com prova real" if caso.kind is CaseKind.DEVICE else "não executado"
            pendencias.append(f"{caso.id} ({caso.name}): {falta}")
        elif vistos[-1].outcome is not Outcome.PASSED:
            pendencias.append(f"{caso.id} ({caso.name}): último resultado {vistos[-1].outcome}")
    return ValidationVerdict(not pendencias, tuple(pendencias))
