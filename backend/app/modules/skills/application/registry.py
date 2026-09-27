"""O registro de habilidades: um só, com dois backends (decisão 1 da §19, ADR-034).

Precedência da RESOLVE: habilidade publicada, depois fluxo ativo, depois nada (e o planejador fica com o comando).
Cada backend obedece o próprio interruptor, lido a CADA chamada (a configuração muda com o processo no ar):
- `skills.enabled` (padrão `false`, decisão P1) liga as habilidades novas;
- `ai.flows` continua mandando no fluxo legado exatamente como hoje em `RunService._plan` e `apps_exigidos`.
Com os dois desligados, a resposta é sempre "nada", como na produção de hoje com `ai.flows: false`.

Só a RESOLVE obedece aos interruptores. Ler uma versão pelo nome (`get`) serve à trilha de execuções passadas e
não pode sumir porque alguém desligou o interruptor depois.

O registro não escreve: publicar, adotar um fluxo e desfazer a adoção são do repositório, numa transação só.

Fase I: quem executa não pergunta mais `resolve` (o primeiro que casa), e sim `candidates` (todos os da força mais
alta), por meio do `IntentResolver` — empate vira pergunta, nunca escolha por id. `resolve` fica como a precedência
crua, a mesma de antes, para quem só quer saber "casa com quê".
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from app.modules.skills.application.intent_ports import SkillCandidateSource
from app.modules.skills.application.ports import SkillSource
from app.modules.skills.domain.intent import SkillMatch
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.refs import SkillRef, is_legacy_skill_id
from app.modules.skills.domain.versions import ResolvedSkill, SkillDefinition, SkillSummary, SkillVersion


class CompositeSkillRegistry:
    def __init__(self, skills: SkillCandidateSource, legacy: SkillCandidateSource, *,
                 skills_enabled: Callable[[], bool], flows_enabled: Callable[[], bool]) -> None:
        self._skills = skills
        self._legacy = legacy
        self._skills_enabled = skills_enabled
        self._flows_enabled = flows_enabled

    def candidates(self, command: str, profile_ids: Sequence[str | None] | None) -> tuple[SkillMatch, ...]:
        """Os candidatos da RESOLVE, com a precedência de sempre: habilidades publicadas que casam inteiras (todas da
        força mais alta); senão, o fluxo ativo (o do `FlowStore.match`, um só: a ordem por uso é a de hoje); senão,
        as habilidades que casam com um `{nome}` vazio — que só servem para perguntar, e perdem para qualquer
        casamento inteiro, inclusive o de um fluxo."""
        parciais: tuple[SkillMatch, ...] = ()
        if self._skills_enabled():
            achadas = tuple(self._skills.candidates(command, profile_ids))
            if achadas and achadas[0].complete:
                return achadas
            parciais = achadas
        if self._flows_enabled():
            fluxo = tuple(self._legacy.candidates(command, profile_ids))
            if fluxo:
                return fluxo
        return parciais

    def resolve(self, command: str, profile_ids: Sequence[str | None] | None) -> ResolvedSkill | None:
        """`profile_ids`: os perfis dos aparelhos da execução; `None` = prévia sem aparelhos (qualquer escopo).

        A precedência crua: o primeiro candidato completo. Não desempata nem valida tipo — isso é do
        `IntentResolver`, que é o que a execução, a prévia e o pré-voo perguntam."""
        primeiro = next(iter(self.candidates(command, profile_ids)), None)
        return primeiro.skill if primeiro is not None and primeiro.complete else None

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
