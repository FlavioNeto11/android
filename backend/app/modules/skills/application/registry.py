"""O registro de habilidades: um só, com dois backends (decisão 1 da §19, ADR-037).

Precedência da RESOLVE: habilidade publicada, depois fluxo ativo, depois nada (e o planejador fica com o comando).
Cada backend obedece o próprio interruptor, lido a CADA chamada (a configuração muda com o processo no ar):
- `skills.enabled` (padrão `false`, decisão P1) liga as habilidades novas;
- `ai.flows` continua mandando no fluxo legado exatamente como hoje em `RunService._plan` e `apps_exigidos`.
Com os dois desligados, a resposta é sempre "nada", como na produção de hoje com `ai.flows: false`.

Só a RESOLVE obedece aos interruptores. Ler uma versão pelo nome (`get`) serve à trilha de execuções passadas e
não pode sumir porque alguém desligou o interruptor depois.

O registro não escreve: publicar, adotar um fluxo e desfazer a adoção são do repositório, numa transação só.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from app.modules.skills.application.ports import SkillSource
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef, is_legacy_skill_id
from app.modules.skills.domain.versions import ResolvedSkill, SkillDefinition, SkillSummary, SkillVersion


class CompositeSkillRegistry:
    def __init__(self, skills: SkillSource, legacy: SkillSource, *, skills_enabled: Callable[[], bool],
                 flows_enabled: Callable[[], bool]) -> None:
        self._skills = skills
        self._legacy = legacy
        self._skills_enabled = skills_enabled
        self._flows_enabled = flows_enabled

    def resolve(self, command: str, profile_ids: Sequence[str | None] | None) -> ResolvedSkill | None:
        """`profile_ids`: os perfis dos aparelhos da execução; `None` = prévia sem aparelhos (qualquer escopo)."""
        if self._skills_enabled():
            achada = self._skills.resolve(command, profile_ids)
            if achada is not None:
                return achada
        if self._flows_enabled():
            return self._legacy.resolve(command, profile_ids)
        return None

    def _source(self, skill_id: str) -> SkillSource:
        return self._legacy if is_legacy_skill_id(skill_id) else self._skills

    def get(self, ref: SkillRef) -> SkillVersion:
        return self._source(ref.skill_id).get(ref)

    def published(self, skill_id: str) -> SkillVersion | None:
        return self._source(skill_id).published(skill_id)

    def definition(self, skill_id: str) -> SkillDefinition | None:
        return self._source(skill_id).definition(skill_id)

    def list(self, *, app_id: str | None = None, state: SkillState | None = None) -> list[SkillSummary]:
        return [*self._skills.list(app_id=app_id, state=state), *self._legacy.list(app_id=app_id, state=state)]
