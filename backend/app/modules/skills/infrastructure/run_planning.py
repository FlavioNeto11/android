"""RESOLVE + COMPILE de uma execução (design §14.1): comando → skill resolvida → o `Plan` que o runtime de hoje roda.

É o que `RunService._plan`, `apps_exigidos` e `GET /api/flows/match` perguntam — os três pela MESMA porta, para que a
estimativa do painel e o pré-voo não divirjam do que a execução faz (decisão P2).

- Resolve pelo `SkillRegistry`: habilidade publicada (atrás de `skills.enabled`) → fluxo ativo (atrás de `ai.flows`)
  → nada, e aí o planejador fica com o comando, como sempre.
- Conteúdo legado (`schema_version` 0: o fluxo, ou a v1 de um fluxo adotado) é passagem direta, `legacy_plan`: o
  plano de `flow:<id>@1` é o MESMO que `FlowStore.match` devolvia.
- Conteúdo da DSL passa pelo compilador com os valores do comando (§12.1, a segunda compilação). Falha de compilação
  NUNCA vira plano parcial: volta como `issues`, e quem chamou decide (a execução vai para `needs_input`).
- A trilha (045): skill nova grava `skill_id`/`skill_version`/`skill_hash`; fluxo legado grava só o `skill_hash`
  (calculado), com `runs.flow_id` como sempre — `skill_id` nulo e `flow_id` preenchido querem dizer `flow:<id>@1`.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from app.models import Plan

from ..application.ports import SkillRegistry
from ..domain.compiler import SkillLookup
from ..domain.errors import Code, CompileIssue
from ..domain.lifecycle import InvalidDocument
from ..domain.refs import SkillRef
from ..domain.versions import SCHEMA_LEGACY_PLAN, ResolvedSkill
from .legacy_flows import legacy_plan
from .lowering import SkillPlanCompiler


@dataclass(frozen=True, slots=True)
class RunPlan:
    """O comando casou com `resolved`. `plan` é o plano da execução, ou `None` com os `issues` que o impediram."""

    resolved: ResolvedSkill
    plan: Plan | None
    issues: tuple[CompileIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.plan is not None

    @property
    def ref(self) -> SkillRef:
        return self.resolved.ref

    @property
    def legacy_flow_id(self) -> str | None:
        """O fluxo legado que resolveu o comando (`flow:<id>@1`). A v1 de um fluxo ADOTADO é da skill, não do fluxo."""
        return self.ref.legacy_flow_id

    @property
    def skill_id(self) -> str | None:
        """`runs.skill_id`: nulo para o fluxo legado (a trilha antiga já o diz por `runs.flow_id`)."""
        return None if self.ref.is_legacy else self.ref.skill_id

    @property
    def skill_version(self) -> int | None:
        return None if self.ref.is_legacy else self.ref.version

    @property
    def skill_hash(self) -> str:
        """sha256 do conteúdo executado — vale também para o fluxo, onde é calculado na leitura (§15.1)."""
        return self.resolved.version.content_hash

    @property
    def name(self) -> str:
        return self.resolved.definition.name


class SkillRunPlanner:
    def __init__(self, registry: SkillRegistry, pacote_do_app: Callable[[str], str | None],
                 skills: SkillLookup | None = None) -> None:
        self._registry = registry
        self._compilador = SkillPlanCompiler(pacote_do_app, skills)

    def resolve(self, command: str, profile_ids: Sequence[str | None] | None) -> ResolvedSkill | None:
        """`profile_ids`: os perfis dos aparelhos da execução; `None` = prévia sem aparelhos (qualquer escopo)."""
        return self._registry.resolve(command, profile_ids)

    def plan(self, resolved: ResolvedSkill) -> RunPlan:
        versao = resolved.version
        if versao.schema_version == SCHEMA_LEGACY_PLAN:
            try:
                return RunPlan(resolved, legacy_plan(resolved))
            except (InvalidDocument, ValidationError) as exc:
                return RunPlan(resolved, None, (CompileIssue(Code.E_PLAN_INVALID, f"{versao.ref}: {exc}"),))
        r = self._compilador.compilar(versao.document(), version=versao.ref.version,
                                      parameters=dict(resolved.parameters))
        if not r.ok or r.executable is None:
            return RunPlan(resolved, None, r.errors or r.issues)
        return RunPlan(resolved, r.executable.plan, r.warnings)

    def for_command(self, command: str, profile_ids: Sequence[str | None] | None) -> RunPlan | None:
        resolvida = self.resolve(command, profile_ids)
        return self.plan(resolvida) if resolvida is not None else None
