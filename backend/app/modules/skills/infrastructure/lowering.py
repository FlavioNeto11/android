"""Baixa do IR de skill para o `Plan` de hoje (design §12.1; decisão 2: o runtime consome `Plan`/`PlanStep` como hoje).

Mora na infraestrutura porque `Plan` e `CapabilityCatalog.build_step` são legado, que o domínio não vê (D2). E reusa o
`build_step` em vez de reimplementá-lo: é lá que moram a política de texto, as guardas, o `commit_selector`, a
pós-condição do catálogo e o `max_attempts=1` com efeito — a etapa de uma skill sai IDÊNTICA à que o planejador
produz para a mesma capability, e as receitas continuam casando (`PlanStep.key == node_id`).

O validador do `Plan` (`models.py`) é a última porta: o que ele recusar sai como `E_PLAN_INVALID`, nunca como 500.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from app.contracts.skills.v1alpha1 import SkillDocument
from app.models import DeliveryLevel, Plan, PlanStep, StepOrigin
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.planning.capabilities import CapabilityNode, MissingBinding, UnknownCapability, load_catalog

from ..domain.compiler import SkillCompiler, SkillLookup
from ..domain.errors import Code, CompileIssue, Severity
from ..domain.ir import NodeKind, ProcessGraph, ProcessNode, content_hash

#: `Plan.planner.provider` de todo plano compilado de skill: é por ele que a trilha sabe que não houve planejador.
PLANNER_PROVIDER = "skill"


@dataclass(frozen=True, slots=True)
class ExecutableGraph:
    """O `Plan` da execução (com a origem em cada etapa) e os dois hashes que a trilha guarda."""

    plan: Plan
    ir_hash: str
    plan_hash: str


@dataclass(frozen=True, slots=True)
class PlanCompileResult:
    issues: tuple[CompileIssue, ...]
    graph: ProcessGraph | None
    executable: ExecutableGraph | None

    @property
    def errors(self) -> tuple[CompileIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.error)

    @property
    def warnings(self) -> tuple[CompileIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.warning)

    @property
    def ok(self) -> bool:
        return self.executable is not None and not self.errors


def plan_hash(plan: Plan) -> str:
    """sha256 do JSON canônico do plano: a mesma versão com os mesmos parâmetros tem de dar o mesmo hash (§12.1)."""
    return content_hash(plan.model_dump(mode="json"))


def _resumo(exc: ValidationError) -> str:
    return "; ".join(str(e["msg"]) for e in exc.errors())


def _passo(no: ProcessNode, grafo: ProcessGraph) -> PlanStep:
    origem = StepOrigin(skill_id=no.skill_id, skill_version=no.skill_version, node_id=no.node_id,
                        strategies=[s.value for s in no.strategies])
    app_id = no.app if no.app != grafo.app else None
    vinculos = {k: v.template() for k, v in no.bindings}
    if no.kind is NodeKind.capability and no.capability is not None:
        catalogo = load_catalog(no.capability.app)
        if catalogo is None:
            raise UnknownCapability(str(no.capability))
        passo = catalogo.build_step(CapabilityNode(key=no.node_id, capability=no.capability.key,
                                                   depends_on=list(no.depends_on), bindings=vinculos,
                                                   for_each=no.for_each))
        mudancas: dict[str, object] = {"app_id": app_id, "origin": origem}
        if no.timeout_s is not None:
            mudancas["timeout_s"] = no.timeout_s
        if no.retries is not None and not passo.side_effect:   # com efeito, o `build_step` já fixou 1 (P5)
            mudancas["max_attempts"] = no.retries + 1
        if no.required_delivery_level is not None:
            mudancas["postcondition"] = passo.postcondition.model_copy(
                update={"required_delivery_level": DeliveryLevel(no.required_delivery_level)})
        return passo.model_copy(update=mudancas)
    if no.goal is None:
        raise ValueError(f"nó {no.node_id} sem capability e sem contrato")
    g = no.goal
    corpo: dict[str, object] = {
        "key": no.node_id, "title": g.title.template(), "goal": g.goal.template(),
        "depends_on": list(no.depends_on), "side_effect": no.side_effect,
        "commit_guard": [x.template() for x in g.commit_guard],
        "precondition": g.precondition.template() if g.precondition is not None else None,
        "postcondition": {"kind": g.post_kind, "value": g.post_value.template(),
                          "description": g.post_description.template(),
                          "required_delivery_level": no.required_delivery_level},
        # P5: com efeito, uma tentativa só; sem efeito, `retries` do nó ou o padrão da etapa.
        "max_attempts": 1 if no.side_effect else (no.retries + 1 if no.retries is not None else 3),
        "bindings": vinculos, "for_each": no.for_each, "app_id": app_id, "origin": origem,
    }
    if no.timeout_s is not None:
        corpo["timeout_s"] = no.timeout_s
    return PlanStep.model_validate(corpo)


def baixar(grafo: ProcessGraph,
           pacote_do_app: Callable[[str], str | None]) -> tuple[ExecutableGraph | None, tuple[CompileIssue, ...]]:
    """IR → `Plan`. Sem valores ligados (compilação de `draft → candidate`), os parâmetros ficam como `{nome}`, do
    mesmo jeito que um fluxo guarda o plano congelado."""
    passos: list[PlanStep] = []
    problemas: list[CompileIssue] = []
    for no in grafo.nodes:
        try:
            passos.append(_passo(no, grafo))
        except UnknownCapability as exc:
            problemas.append(CompileIssue(Code.E_UNKNOWN_CAPABILITY, f"nó {no.node_id}: capability {exc} fora do "
                                                                     "catálogo na baixa", ""))
        except MissingBinding as exc:
            problemas.append(CompileIssue(Code.E_MISSING_BINDING, f"nó {no.node_id}: {exc}", ""))
        except (ValidationError, ValueError) as exc:
            detalhe = _resumo(exc) if isinstance(exc, ValidationError) else str(exc)
            problemas.append(CompileIssue(Code.E_PLAN_INVALID, f"nó {no.node_id}: {detalhe}", ""))
    if problemas:
        return None, tuple(problemas)
    parametros = (dict(grafo.arguments) if grafo.arguments is not None
                  else {p.name: "{" + p.name + "}" for p in grafo.parameters})
    try:
        plano = Plan.model_validate({
            "summary": grafo.name, "app_id": grafo.app, "app_package": pacote_do_app(grafo.app),
            "required_apps": list(grafo.required_apps), "parameters": parametros,
            "success_criteria": [c.template() for c in grafo.success_criteria], "steps": passos,
            "planner": {"provider": PLANNER_PROVIDER, "model": f"skill:{grafo.skill_id}@{grafo.skill_version}",
                        "simulated": False},
        })
    except ValidationError as exc:
        return None, (CompileIssue(Code.E_PLAN_INVALID, f"o validador do Plan recusou: {_resumo(exc)}", ""),)
    return ExecutableGraph(plano, grafo.content_hash(), plan_hash(plano)), ()


class SkillPlanCompiler:
    """O `PlanCompiler` da §8: compila no domínio, baixa aqui. É o único produtor de `Plan` para skill nova — e não
    gera nem executa Python: o documento é dado, e o plano sai de `build_step` e do validador de sempre."""

    def __init__(self, pacote_do_app: Callable[[str], str | None], skills: SkillLookup | None = None) -> None:
        self._pacote_do_app = pacote_do_app
        self._compilador = SkillCompiler(CatalogCapabilityRegistry(pacote_do_app), skills)

    def compilar(self, documento: Mapping[str, object] | SkillDocument, *, version: int,
                 parameters: Mapping[str, str] | None = None,
                 occupied_match_keys: Collection[str] = ()) -> PlanCompileResult:
        r = self._compilador.compilar(documento, version=version, parameters=parameters,
                                      occupied_match_keys=occupied_match_keys)
        if r.graph is None:
            return PlanCompileResult(r.issues, None, None)
        executavel, problemas = baixar(r.graph, self._pacote_do_app)
        return PlanCompileResult(r.issues + problemas, r.graph, executavel)
