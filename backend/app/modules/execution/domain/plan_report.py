"""PLAN estruturado (design §14.2), a parte dos RECURSOS: para cada alvo e cada recurso que a skill declara, o que está
certo, o que diverge, o que seria feito, o que se arrisca e o que só uma pessoa resolve — sem aplicar nada.

Puro: recebe os specs, os alvos e o que os providers já LERAM (`read_current_state`), e devolve o relatório. Quem
lê é a infraestrutura de cada contexto; nada aqui toca banco, aparelho ou IA, e nada do que tem efeito entra (o §14.2
lista: `_app_resolver`, `_portas_do_app`, `note_waiting`, `_draft_gate`, `_approval_gate`, as lambdas do
`session_gate`).

Três escolhas:

* **par não lido é `unknown`**, com código `not_read` e nenhuma ação: faltar a leitura nunca vira "está certo";
* **ordem fixa** por alvo (id do aparelho) e, dentro dele, a ordem das portas do `_tick` — aparelho, app, vínculo,
  sessão —, porque é nessa ordem que as ações acontecem (não se instala num aparelho desligado);
* **determinístico**: a mesma entrada, em qualquer ordem, dá o mesmo relatório e o mesmo hash (JSON canônico, o
  mesmo do conteúdo das skills — `skills/domain/document.py`).

Os demais campos do §14.2 (etapas, efeitos, aprovações, custo estimado) vêm de outras fontes e entram quando o
relatório for servido em `mode=plan`.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.modules.applications.domain import resources as apps
from app.modules.fleet.domain import resources as fleet
from app.modules.identity.domain import resources as ident
from app.modules.skills.domain.document import JsonObject, JsonValue, content_hash
from app.modules.skills.domain.ir import ProcessGraph, ResourceDecl
from app.modules.skills.domain.refs import SkillRef
from app.shared.resources import (NOT_READ, ActionPurpose, Drift, DriftStatus, ObservedState, ResourceAction,
                                  ResourceKind, ResourceRef, ResourceSpec, Target)

#: A ordem das portas do despacho (`Scheduler._tick` → `_portas_do_app`): aparelho, app, vínculo, sessão.
KIND_ORDER: tuple[ResourceKind, ...] = (ResourceKind.device_state, ResourceKind.app_installation,
                                        ResourceKind.account_binding, ResourceKind.app_session)

_DIFF: dict[ResourceKind, Callable[[ResourceSpec, ObservedState], Drift]] = {
    ResourceKind.device_state: fleet.diff_device_state,
    ResourceKind.app_installation: apps.diff_app_installation,
    ResourceKind.account_binding: ident.diff_account_binding,
    ResourceKind.app_session: ident.diff_app_session,
}
_PLAN: dict[ResourceKind, Callable[[Drift], list[ResourceAction]]] = {
    ResourceKind.device_state: fleet.plan_device_state,
    ResourceKind.app_installation: apps.plan_app_installation,
    ResourceKind.account_binding: ident.plan_account_binding,
    ResourceKind.app_session: ident.plan_app_session,
}
#: O que cada comando arrisca — de quem conhece o comando (cada contexto).
VERB_RISKS: dict[str, str] = fleet.VERB_RISKS | apps.VERB_RISKS | ident.VERB_RISKS
#: Códigos que pedem aviso mesmo quando há ação (o rodízio liga um aparelho que uma pessoa mandou parar).
WARNING_CODES: frozenset[str] = frozenset({fleet.DeviceCode.stopped_by_decision.value})


def spec_from_decl(decl: ResourceDecl) -> ResourceSpec:
    """O recurso como o compilador o deixou no IR (`ProcessGraph.resources`)."""
    desejado = decl.desired if isinstance(decl.desired, str) else dict(decl.desired)
    return ResourceSpec.of(decl.kind, decl.target, desejado, decl.on_missing)


def specs_of(graph: ProcessGraph) -> tuple[ResourceSpec, ...]:
    return tuple(spec_from_decl(d) for d in graph.resources)


@dataclass(frozen=True, slots=True)
class ResourceLine:
    """Um recurso num alvo: a diferença e as ações que a fechariam."""

    drift: Drift
    actions: tuple[ResourceAction, ...]


@dataclass(frozen=True, slots=True)
class PlanReport:
    targets: tuple[Target, ...]
    lines: tuple[ResourceLine, ...]
    skill: SkillRef | None = None
    #: `content_hash` da versão compilada, quando o relatório é de uma skill.
    skill_hash: str | None = None

    @property
    def ok(self) -> tuple[ResourceLine, ...]:
        return tuple(ln for ln in self.lines if ln.drift.status is DriftStatus.in_sync)

    @property
    def drifts(self) -> tuple[ResourceLine, ...]:
        """Tudo o que não está comprovadamente certo — inclusive `unknown`, `pending` e `held`."""
        return tuple(ln for ln in self.lines if ln.drift.status is not DriftStatus.in_sync)

    @property
    def actions(self) -> tuple[ResourceAction, ...]:
        return tuple(a for ln in self.lines for a in ln.actions)

    @property
    def human_interventions(self) -> tuple[ResourceAction, ...]:
        return tuple(a for a in self.actions if a.purpose is ActionPurpose.ask)

    @property
    def blockers(self) -> tuple[ResourceLine, ...]:
        return tuple(ln for ln in self.lines if ln.drift.status in (DriftStatus.blocked, DriftStatus.unsupported))

    @property
    def ready_to_run(self) -> bool:
        """Nada a convergir, nada a provar, nada em curso: só `in_sync` e `held` (fica na versão que tem)."""
        return all(ln.drift.status in (DriftStatus.in_sync, DriftStatus.held) for ln in self.lines)

    @property
    def risks(self) -> tuple[str, ...]:
        por_verbo: dict[str, set[str]] = {}
        for a in self.actions:
            if a.verb is not None:
                por_verbo.setdefault(a.verb, set()).add(a.target.instance_id)
        riscos = [f"{verbo} em {', '.join(sorted(alvos))}: {VERB_RISKS.get(verbo, 'risco não descrito')}"
                  for verbo, alvos in sorted(por_verbo.items())]
        for ln in self.lines:
            d = ln.drift
            onde = f"{d.target.instance_id} · {d.ref.label()}"
            if d.status is DriftStatus.unknown:
                riscos.append(f"{onde}: não comprovado — {d.detail}")
            elif d.status is DriftStatus.held or d.code in WARNING_CODES:
                riscos.append(f"{onde}: {d.detail}")
        return tuple(riscos)

    def canonical(self) -> JsonObject:
        resumo: JsonObject = {s.value: sum(1 for ln in self.lines if ln.drift.status is s) for s in DriftStatus}
        resumo.update({"actions": len(self.actions), "human_interventions": len(self.human_interventions),
                       "blockers": len(self.blockers), "ready_to_run": self.ready_to_run})
        return {
            "skill": None if self.skill is None else {"id": self.skill.skill_id, "version": self.skill.version,
                                                      "content_hash": self.skill_hash},
            "targets": [{"instance_id": t.instance_id, "profile_id": t.profile_id} for t in self.targets],
            "resources": [_linha(ln) for ln in self.lines],
            "risks": list(self.risks),
            "summary": resumo,
        }

    def content_hash(self) -> str:
        return content_hash(self.canonical())


def _linha(ln: ResourceLine) -> JsonObject:
    d = ln.drift
    observado: JsonValue = None if d.observed is None else [[k, v] for k, v in d.observed.facts()]
    acoes: list[JsonValue] = [{"purpose": a.purpose.value, "verb": a.verb, "reason": a.reason} for a in ln.actions]
    return {"kind": d.ref.kind.value, "target": d.ref.target, "instance_id": d.target.instance_id,
            "profile_id": d.target.profile_id, "desired": d.spec.desired_text(), "on_missing": d.spec.on_missing.value,
            "status": d.status.value, "code": str(d.code), "detail": d.detail, "observed": observado,
            "actions": acoes}


def _chave_do_spec(spec: ResourceSpec) -> tuple[int, str]:
    return KIND_ORDER.index(spec.ref.kind), spec.ref.target or ""


def build_plan_report(specs: Sequence[ResourceSpec], targets: Sequence[Target], observed: Iterable[ObservedState],
                      *, skill: SkillRef | None = None, skill_hash: str | None = None) -> PlanReport:
    """Monta o relatório. `observed` é o que os providers leram, um por (recurso, alvo); o que faltar é `not_read`.

    O mesmo recurso pedido duas vezes com estados diferentes é recusado — o compilador já o recusa
    (`E_RESOURCE_CONFLICT`), e aqui é a segunda camada; o repetido idêntico conta uma vez só.
    """
    unicos: dict[ResourceRef, ResourceSpec] = {}
    for s in specs:
        if unicos.setdefault(s.ref, s) != s:
            raise ValueError(f"o recurso {s.ref.label()} foi pedido com dois estados desejados diferentes")
    alvos = tuple(sorted(set(targets), key=lambda t: (t.instance_id, t.profile_id or "")))
    lidos: dict[tuple[ResourceRef, Target], ObservedState] = {}
    for o in observed:
        if lidos.setdefault((o.ref, o.target), o) is not o:
            raise ValueError(f"duas leituras de {o.ref.label()} em {o.target.instance_id}")
    linhas: list[ResourceLine] = []
    for alvo in alvos:
        for spec in sorted(unicos.values(), key=_chave_do_spec):
            obs = lidos.get((spec.ref, alvo))
            drift = (_DIFF[spec.ref.kind](spec, obs) if obs is not None else
                     Drift(spec=spec, target=alvo, status=DriftStatus.unknown, code=NOT_READ,
                           detail=f"{spec.ref.label()} não foi lido em {alvo.instance_id}"))
            linhas.append(ResourceLine(drift, tuple(_PLAN[spec.ref.kind](drift))))
    return PlanReport(targets=alvos, lines=tuple(linhas), skill=skill, skill_hash=skill_hash)
