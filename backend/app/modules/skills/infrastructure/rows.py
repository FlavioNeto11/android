"""Linha do banco → objeto do domínio. A `Row` (um `dict` sem tipo) não sai da infraestrutura.

Cada coluna é conferida no tipo ao sair: um valor estranho vira erro aqui, na borda, e não um `None` que o domínio
confundiria com "não informado".
"""
from __future__ import annotations

from app.db import Row
from app.modules.skills.domain.document import JsonObject, NotJson, content_hash, parse_json_object
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.validation import (CaseKind, CaseStatus, Outcome, Proof, ValidationCase,
                                                  ValidationResult)
from app.modules.skills.domain.versions import (Provenance, SkillDefinition, SkillSummary, SkillVersion, SourceKind,
                                                TransitionRecord)


def texto(row: Row, coluna: str) -> str:
    valor = row[coluna]
    if not isinstance(valor, str):
        raise TypeError(f"coluna {coluna}: esperado texto, veio {type(valor).__name__}")
    return valor


def texto_ou_nulo(row: Row, coluna: str) -> str | None:
    valor = row[coluna]
    if valor is None or isinstance(valor, str):
        return valor
    raise TypeError(f"coluna {coluna}: esperado texto ou nulo, veio {type(valor).__name__}")


def inteiro(row: Row, coluna: str) -> int:
    valor = row[coluna]
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise TypeError(f"coluna {coluna}: esperado inteiro, veio {type(valor).__name__}")
    return valor


def inteiro_ou_nulo(row: Row, coluna: str) -> int | None:
    return None if row[coluna] is None else inteiro(row, coluna)


def json_objeto(row: Row, coluna: str) -> JsonObject:
    bruto = texto_ou_nulo(row, coluna)
    return parse_json_object(bruto) if bruto else {}


def definicao(row: Row) -> SkillDefinition:
    return SkillDefinition(id=texto(row, "id"), name=texto(row, "name"), description=texto(row, "description"),
                           app_id=texto_ou_nulo(row, "app_id"), legacy_flow_id=texto_ou_nulo(row, "legacy_flow_id"),
                           created_by=texto_ou_nulo(row, "created_by"), created_at=texto(row, "created_at"),
                           updated_at=texto(row, "updated_at"))


def versao(row: Row, app_ids: tuple[str, ...]) -> SkillVersion:
    return SkillVersion(
        ref=SkillRef(texto(row, "skill_id"), inteiro(row, "version")), state=SkillState(texto(row, "state")),
        schema_version=inteiro(row, "schema_version"), content_json=texto(row, "content"),
        content_hash=texto(row, "content_hash"), command_template=texto_ou_nulo(row, "command_template"),
        match_key=texto_ou_nulo(row, "match_key"), app_ids=tuple(sorted(app_ids)),
        parent_version=inteiro_ou_nulo(row, "parent_version"),
        provenance=Provenance.from_json(SourceKind(texto(row, "source_kind")), texto_ou_nulo(row, "source_ref"),
                                        json_objeto(row, "provenance")),
        created_by=texto_ou_nulo(row, "created_by"), created_at=texto(row, "created_at"),
        state_at=texto(row, "state_at"), state_by=texto_ou_nulo(row, "state_by"),
        state_detail=texto_ou_nulo(row, "state_detail"))


def integra(conteudo: str, hash_gravado: str) -> bool:
    try:
        return content_hash(parse_json_object(conteudo)) == hash_gravado
    except NotJson:
        return False


def resumo(row: Row) -> SkillSummary:
    """Linha de `skill_versions` com `name`/`app_id` da definição (colunas `d_name`, `d_app_id`)."""
    return SkillSummary(ref=SkillRef(texto(row, "skill_id"), inteiro(row, "version")), name=texto(row, "d_name"),
                        app_id=texto_ou_nulo(row, "d_app_id"), state=SkillState(texto(row, "state")),
                        command_template=texto_ou_nulo(row, "command_template"),
                        schema_version=inteiro(row, "schema_version"), content_hash=texto(row, "content_hash"),
                        intact=integra(texto(row, "content"), texto(row, "content_hash")),
                        state_at=texto(row, "state_at"))


def transicao(row: Row) -> TransitionRecord:
    anterior = texto_ou_nulo(row, "from_state")
    return TransitionRecord(ref=SkillRef.parse(texto(row, "version_id")),
                            from_state=SkillState(anterior) if anterior else None,
                            to_state=SkillState(texto(row, "to_state")), reason=texto_ou_nulo(row, "reason"),
                            decided_by=texto_ou_nulo(row, "decided_by"), decided_at=texto(row, "decided_at"))


def caso(row: Row) -> ValidationCase:
    return ValidationCase(id=texto(row, "id"), skill_id=texto(row, "skill_id"), name=texto(row, "name"),
                          kind=CaseKind(texto(row, "kind")), since_version=inteiro_ou_nulo(row, "since_version"),
                          until_version=inteiro_ou_nulo(row, "until_version"),
                          parameters=json_objeto(row, "parameters"), preconditions=json_objeto(row, "preconditions"),
                          expected=json_objeto(row, "expected"), source_kind=texto_ou_nulo(row, "source_kind"),
                          source_ref=texto_ou_nulo(row, "source_ref"), status=CaseStatus(texto(row, "status")))


def resultado(row: Row) -> ValidationResult:
    return ValidationResult(id=inteiro(row, "id"), case_id=texto(row, "case_id"),
                            ref=SkillRef.parse(texto(row, "version_id")), proof=Proof(texto(row, "proof")),
                            outcome=Outcome(texto(row, "outcome")), run_id=texto_ou_nulo(row, "run_id"),
                            instance_id=texto_ou_nulo(row, "instance_id"),
                            physical_id=texto_ou_nulo(row, "physical_id"),
                            app_version=texto_ou_nulo(row, "app_version"), variant=texto_ou_nulo(row, "variant"),
                            detail=texto_ou_nulo(row, "detail"), observed_at=texto(row, "observed_at"),
                            observed_by=texto_ou_nulo(row, "observed_by"))
