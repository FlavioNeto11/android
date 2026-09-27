"""Erros e avisos do compilador de skills (design §12.6).

Cada problema sai como `{code, message, path, severity}` — nunca como exceção, nunca como 500. `path` é um ponteiro
JSON (RFC 6901) no documento que a pessoa (ou o LLM) escreveu, para o painel apontar o campo; `message` é em
português e diz o que fazer. O vocabulário é fechado: código novo entra aqui, com uma fixture inválida em
`tests/fixtures/dsl/v1alpha1/invalidos/` (o teste reprova código sem fixture).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Code(StrEnum):
    # documento
    E_SCHEMA = "E_SCHEMA"
    E_API_VERSION = "E_API_VERSION"
    E_KIND = "E_KIND"
    # identificadores
    E_ID_FORMAT = "E_ID_FORMAT"
    E_NODE_ID_FORMAT = "E_NODE_ID_FORMAT"
    E_NODE_ID_DUPLICATE = "E_NODE_ID_DUPLICATE"
    E_NODE_ID_TOO_LONG = "E_NODE_ID_TOO_LONG"
    E_NODE_ID_COLLISION = "E_NODE_ID_COLLISION"
    # comando e parâmetros
    E_COMMAND_AMBIGUOUS = "E_COMMAND_AMBIGUOUS"
    E_COMMAND_PARAMETER_MISMATCH = "E_COMMAND_PARAMETER_MISMATCH"
    E_COMMAND_RESERVED = "E_COMMAND_RESERVED"
    E_UNKNOWN_PARAMETER = "E_UNKNOWN_PARAMETER"
    E_MISSING_ARGUMENT = "E_MISSING_ARGUMENT"
    E_SECRET_PARAMETER = "E_SECRET_PARAMETER"
    # capability e governança
    E_UNKNOWN_CAPABILITY = "E_UNKNOWN_CAPABILITY"
    E_CAPABILITY_INTERNAL = "E_CAPABILITY_INTERNAL"
    E_CAPABILITY_REQUIRED = "E_CAPABILITY_REQUIRED"
    E_MISSING_BINDING = "E_MISSING_BINDING"
    E_SIDE_EFFECT_MISMATCH = "E_SIDE_EFFECT_MISMATCH"
    E_RETRY_ON_EFFECT = "E_RETRY_ON_EFFECT"
    E_VERIFICATION_WEAKENED = "E_VERIFICATION_WEAKENED"
    E_FIELD_RESERVED = "E_FIELD_RESERVED"
    # grafo
    E_DEPENDENCY_UNKNOWN = "E_DEPENDENCY_UNKNOWN"
    E_DEPENDENCY_CYCLE = "E_DEPENDENCY_CYCLE"
    E_FOREACH_SOURCE = "E_FOREACH_SOURCE"
    E_FOREACH_NOT_CONTIGUOUS = "E_FOREACH_NOT_CONTIGUOUS"
    E_FOREACH_NO_ITEM = "E_FOREACH_NO_ITEM"
    E_COLLECT_WITH_EFFECT = "E_COLLECT_WITH_EFFECT"
    # expressões
    E_OUTPUT_REF_UNSUPPORTED = "E_OUTPUT_REF_UNSUPPORTED"
    E_EXPRESSION = "E_EXPRESSION"
    E_RAW_PLACEHOLDER = "E_RAW_PLACEHOLDER"
    E_SECRET_INLINE = "E_SECRET_INLINE"
    E_WHEN_UNSUPPORTED = "E_WHEN_UNSUPPORTED"
    E_WHEN_DEPENDENCY = "E_WHEN_DEPENDENCY"
    # composição
    E_SKILL_NOT_FOUND = "E_SKILL_NOT_FOUND"
    E_SKILL_VERSION_UNPINNED = "E_SKILL_VERSION_UNPINNED"
    E_SKILL_CYCLE = "E_SKILL_CYCLE"
    E_COMPOSITION_DEPTH = "E_COMPOSITION_DEPTH"
    # apps, estratégias e recursos
    E_APP_UNKNOWN = "E_APP_UNKNOWN"
    E_STRATEGY_UNKNOWN = "E_STRATEGY_UNKNOWN"
    E_STRATEGY_UNAVAILABLE = "E_STRATEGY_UNAVAILABLE"
    E_RESOURCE_UNKNOWN_KIND = "E_RESOURCE_UNKNOWN_KIND"
    E_RESOURCE_CONFLICT = "E_RESOURCE_CONFLICT"
    # publicação e última porta
    E_DUPLICATE_COMMAND = "E_DUPLICATE_COMMAND"
    E_PLAN_INVALID = "E_PLAN_INVALID"
    # avisos: não impedem `candidate`
    W_PARAMETER_NO_EXAMPLE = "W_PARAMETER_NO_EXAMPLE"
    W_NO_VALIDATION_CASE = "W_NO_VALIDATION_CASE"
    W_PARAMETER_UNUSED = "W_PARAMETER_UNUSED"


class Severity(StrEnum):
    error = "error"
    warning = "warning"


@dataclass(frozen=True, slots=True)
class CompileIssue:
    code: Code
    message: str
    #: Ponteiro JSON no documento compilado ("" = o documento inteiro).
    path: str = ""

    @property
    def severity(self) -> Severity:
        return Severity.warning if self.code.startswith("W_") else Severity.error

    def as_dict(self) -> dict[str, str]:
        """Forma do corpo de erro que o painel já lê (`{code, message}`), com o caminho e a severidade."""
        return {"code": self.code.value, "message": self.message, "path": self.path,
                "severity": self.severity.value}


def pointer(*partes: str | int) -> str:
    """Ponteiro JSON (RFC 6901): `~` vira `~0` e `/` vira `~1` dentro de cada parte."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in partes)
