"""Portas do contexto de habilidades (§7). Pertencem a quem CONSOME; quem implementa não as importa para cumpri-las
(tipagem estrutural), e a composição liga as partes.

- `SkillRegistry`: o que a execução (`_plan`), a prévia de custo e o pré-voo consultam. Um registro, dois backends
  (decisão 1, ADR-034): habilidade publicada → fluxo ativo → nada.
- `SkillSource`: o que o registro exige de cada backend. `SqlSkillRepository` e `LegacyFlowAdapter` cumprem.
- `SkillRepository`: escrita, só no backend SQL. O legado é só leitura: `flows` continua sendo escrito por quem
  sempre o escreveu, e habilidade nunca escreve fluxo (a não ser o status, na adoção).
- `DocumentValidator`: o schema `automation/v1alpha1` + o compilador, que são de outra peça. O repositório pergunta
  a ele o que o documento declara (metadados, comando-modelo, apps) e se ele compila sem erro.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.modules.skills.domain.document import JsonObject
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import (DocumentFacts, Provenance, ResolvedSkill, SkillDefinition,
                                                SkillSummary, SkillVersion)


class SkillSource(Protocol):
    def resolve(self, command: str, profile_ids: Sequence[str | None] | None) -> ResolvedSkill | None: ...
    def get(self, ref: SkillRef) -> SkillVersion: ...
    def published(self, skill_id: str) -> SkillVersion | None: ...
    def definition(self, skill_id: str) -> SkillDefinition | None: ...
    def list(self, *, app_id: str | None = None, state: SkillState | None = None) -> list[SkillSummary]: ...


class SkillRegistry(Protocol):
    """Resolve na ordem: habilidade publicada → fluxo ativo → nada (o planejador fica com o comando)."""

    def resolve(self, command: str, profile_ids: Sequence[str | None] | None) -> ResolvedSkill | None: ...
    def get(self, ref: SkillRef) -> SkillVersion: ...
    def published(self, skill_id: str) -> SkillVersion | None: ...
    def definition(self, skill_id: str) -> SkillDefinition | None: ...
    def list(self, *, app_id: str | None = None, state: SkillState | None = None) -> list[SkillSummary]: ...


class SkillRepository(Protocol):
    def create_draft(self, skill_id: str, doc: JsonObject, *, source: Provenance, by: str | None = None,
                     parent_version: int | None = None) -> SkillVersion: ...
    def update_draft(self, ref: SkillRef, doc: JsonObject) -> SkillVersion: ...
    def transition(self, ref: SkillRef, to: SkillState, *, by: str, reason: str,
                   manual: bool = False) -> SkillVersion: ...


class DocumentValidator(Protocol):
    def inspect(self, document: JsonObject) -> DocumentFacts: ...
