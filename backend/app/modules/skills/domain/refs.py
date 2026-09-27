"""`SkillRef`: o nome inequívoco de UMA versão de habilidade, `<skill_id>@<versão>` (§5, §10.2).

Dois formatos, porque há dois backends (ADR-034):
- habilidade nova: `skill_id` é um slug estável `^[a-z][a-z0-9_.-]{2,63}$` (`ig.abrir_conversa@3`);
- fluxo legado: `flow:<flows.id>@1`. O id do fluxo NÃO segue o slug — nasce de
  `re.sub(r"[^a-z0-9]+", "-", resumo)[:40] or "fluxo"` (`flows.py:63-67`), pode começar com dígito e ser curto — e
  fluxo não tem versões: é sempre a 1. O prefixo `flow:` é reservado ao adaptador.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SKILL_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
LEGACY_PREFIX = "flow:"
#: O que um id de fluxo pode ter: qualquer coisa sem `@` (o separador da versão) e sem espaço.
_LEGACY_FLOW_ID = re.compile(r"^[^@\s]{1,120}$")


class InvalidSkillRef(ValueError):
    """Texto que não é um `SkillRef` válido."""


def is_skill_id(text: str) -> bool:
    return bool(SKILL_ID.fullmatch(text))


def is_legacy_skill_id(text: str) -> bool:
    return text.startswith(LEGACY_PREFIX) and bool(_LEGACY_FLOW_ID.fullmatch(text[len(LEGACY_PREFIX):]))


def legacy_skill_id(flow_id: str) -> str:
    if not _LEGACY_FLOW_ID.fullmatch(flow_id):
        raise InvalidSkillRef(f"id de fluxo inválido: {flow_id!r}")
    return LEGACY_PREFIX + flow_id


@dataclass(frozen=True, slots=True, order=True)
class SkillRef:
    skill_id: str
    version: int

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or self.version < 1:
            raise InvalidSkillRef(f"versão inválida: {self.version!r} (começa em 1)")
        if is_legacy_skill_id(self.skill_id):
            if self.version != 1:
                raise InvalidSkillRef(f"{self.skill_id}: fluxo legado só tem a versão 1")
        elif not is_skill_id(self.skill_id):
            raise InvalidSkillRef(f"id de habilidade inválido: {self.skill_id!r}")

    @classmethod
    def parse(cls, text: str) -> SkillRef:
        skill_id, sep, numero = text.rpartition("@")
        if not sep or not numero.isdigit():
            raise InvalidSkillRef(f"referência sem versão: {text!r} (use <habilidade>@<n>)")
        return cls(skill_id, int(numero))

    @classmethod
    def legacy(cls, flow_id: str) -> SkillRef:
        return cls(legacy_skill_id(flow_id), 1)

    @property
    def is_legacy(self) -> bool:
        return is_legacy_skill_id(self.skill_id)

    @property
    def legacy_flow_id(self) -> str | None:
        """O `flows.id` por trás de `flow:<id>@1`; `None` para habilidade nova."""
        return self.skill_id[len(LEGACY_PREFIX):] if self.is_legacy else None

    def __str__(self) -> str:
        return f"{self.skill_id}@{self.version}"
