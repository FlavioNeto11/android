"""RESOLVE + COMPILE de uma execução (design §14.1): comando → intenção resolvida → o `Plan` que o runtime de hoje roda.

É o que `RunService._plan`, `apps_exigidos` e `GET /api/flows/match` perguntam — os três pela MESMA porta, para que a
estimativa do painel e o pré-voo não divirjam do que a execução faz (decisão P2). `POST /api/skills/resolve` pergunta
só a RESOLVE (`resolve_intent`), pela mesma cadeia.

- RESOLVE pelo `IntentResolver` (fase I): modelos (habilidade publicada atrás de `skills.enabled` → fluxo ativo atrás
  de `ai.flows`) → tipos → semântica → LLM, estas duas com o provedor nulo (sem IA). Três respostas:
  - resolvida: segue para a compilação, com os valores já normalizados pelo tipo;
  - pergunta (parâmetro vazio ou inválido, ou empate): `RunPlan` sem plano e com `questions` — a execução vai para
    `needs_input` com a pergunta, e o planejador NÃO é chamado (a pessoa quis uma habilidade; o planejador por fora
    seria escolher às cegas, e pago);
  - nada casou: `None`, e o planejador fica com o comando, como sempre.
- Conteúdo legado (`schema_version` 0: o fluxo, ou a v1 de um fluxo adotado) é passagem direta, `legacy_plan`: o
  plano de `flow:<id>@1` é o MESMO que `FlowStore.match` devolvia (fluxo não tem tipo; o valor passa como veio).
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

from ..application.intent_ports import IntentDisambiguator, SemanticIntentClassifier, SkillCandidates
from ..application.intent_resolver import IntentRequest, IntentResolver, ParameterExtractor
from ..domain.compiler import SkillLookup
from ..domain.errors import Code, CompileIssue
from ..domain.intent import IntentResolution, MissingInfo, ResolutionStatus
from ..domain.lifecycle import InvalidDocument
from ..domain.refs import SkillRef
from ..domain.versions import SCHEMA_LEGACY_PLAN, ResolvedSkill
from .legacy_flows import legacy_plan
from .lowering import SkillPlanCompiler
from .profile_links import profile_links_for


@dataclass(frozen=True, slots=True)
class RunPlan:
    """O comando casou (ou quase casou). `plan` é o plano da execução, ou `None` com o que o impediu: `issues` (não
    compilou) ou `questions` (a RESOLVE precisa de resposta da pessoa)."""

    resolution: IntentResolution
    plan: Plan | None
    issues: tuple[CompileIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.plan is not None

    @property
    def resolved(self) -> ResolvedSkill | None:
        """A habilidade de que se fala; `None` só no empate (várias candidatas, nenhuma escolhida)."""
        return self.resolution.skill

    @property
    def questions(self) -> tuple[MissingInfo, ...]:
        return self.resolution.questions

    @property
    def ref(self) -> SkillRef | None:
        return self.resolved.ref if self.resolved is not None else None

    @property
    def legacy_flow_id(self) -> str | None:
        """O fluxo legado que resolveu o comando (`flow:<id>@1`). A v1 de um fluxo ADOTADO é da skill, não do fluxo."""
        return self.ref.legacy_flow_id if self.ref is not None else None

    @property
    def skill_id(self) -> str | None:
        """`runs.skill_id`: nulo para o fluxo legado (a trilha antiga já o diz por `runs.flow_id`)."""
        return None if self.ref is None or self.ref.is_legacy else self.ref.skill_id

    @property
    def skill_version(self) -> int | None:
        return None if self.ref is None or self.ref.is_legacy else self.ref.version

    @property
    def skill_hash(self) -> str | None:
        """sha256 do conteúdo executado — vale também para o fluxo, onde é calculado na leitura (§15.1)."""
        return self.resolved.version.content_hash if self.resolved is not None else None

    @property
    def name(self) -> str | None:
        return self.resolved.definition.name if self.resolved is not None else None


class SkillRunPlanner:
    def __init__(self, registry: SkillCandidates, pacote_do_app: Callable[[str], str | None],
                 skills: SkillLookup | None = None, *, classifier: SemanticIntentClassifier | None = None,
                 disambiguator: IntentDisambiguator | None = None) -> None:
        """`classifier`/`disambiguator`: as etapas 3 e 4 da RESOLVE. Sem eles, o provedor nulo (sem IA)."""
        self._compilador = SkillPlanCompiler(pacote_do_app, skills)
        extrator = ParameterExtractor(lambda app_id: profile_links_for(pacote_do_app(app_id)) if app_id else ())
        self._resolver = IntentResolver.standard(registry, extrator, classifier=classifier,
                                                 disambiguator=disambiguator)

    @property
    def compiler(self) -> SkillPlanCompiler:
        """O compilador que a execução usa. O descompilador da conversão de fluxo confere o documento por ESTE, e não
        por outro montado à parte: "converteu equivalente" e "compila igual na execução" não divergem por caminho."""
        return self._compilador

    def resolve_intent(self, command: str, profile_ids: Sequence[str | None] | None) -> IntentResolution:
        """`profile_ids`: os perfis dos aparelhos da execução; `None` = prévia sem aparelhos (qualquer escopo)."""
        return self._resolver.resolve(IntentRequest(command, tuple(profile_ids) if profile_ids is not None else None))

    def plan(self, resolution: IntentResolution) -> RunPlan:
        """A intenção resolvida vira o plano. Pergunta pendente, ou empate, é `RunPlan` sem plano e sem compilar."""
        if resolution.intent is None:
            return RunPlan(resolution, None)
        resolvida = resolution.intent.skill
        versao = resolvida.version
        if versao.schema_version == SCHEMA_LEGACY_PLAN:
            try:
                return RunPlan(resolution, legacy_plan(resolvida))
            except (InvalidDocument, ValidationError) as exc:
                return RunPlan(resolution, None, (CompileIssue(Code.E_PLAN_INVALID, f"{versao.ref}: {exc}"),))
        r = self._compilador.compilar(versao.document(), version=versao.ref.version,
                                      parameters=dict(resolvida.parameters))
        if not r.ok or r.executable is None:
            return RunPlan(resolution, None, r.errors or r.issues)
        return RunPlan(resolution, r.executable.plan, r.warnings)

    def for_command(self, command: str, profile_ids: Sequence[str | None] | None) -> RunPlan | None:
        resolucao = self.resolve_intent(command, profile_ids)
        return None if resolucao.status is ResolutionStatus.NO_MATCH else self.plan(resolucao)
