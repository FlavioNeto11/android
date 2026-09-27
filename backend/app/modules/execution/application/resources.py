"""Os recursos declarativos de uma execução (design §11, §14.2): ler, relatar, aplicar, verificar e reconciliar, pelos
`ResourceProvider`s de cada tipo.

A ordem é a das portas do despacho (`KIND_ORDER`: aparelho, app, vínculo, sessão), porque é nessa ordem que as coisas
acontecem — não se instala num aparelho desligado, nem se entra na conta de um app que não está lá. Por isso
`apply_next` aplica UMA coisa por aparelho e para: a primeira linha que não está certa. A próxima passada, relendo o
mundo, segue dali. É o mesmo passo a passo das portas do `_tick`, só que declarado.

Nada aqui é ligado ao runtime ainda: `_plan` usa só o relatório (sem efeito), e ninguém chama `apply_next` fora dos
testes. Pôr o `_tick` atrás disto é mudar quem dispara, e isso fica para quando o dono decidir.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.modules.execution.application.ports import ResourceProvider
from app.modules.execution.domain.plan_report import PlanReport, ResourceLine, build_plan_report
from app.modules.skills.domain.refs import SkillRef
from app.shared.commands import CommandRef, RunRef
from app.shared.convergence import ReconcileOutcome, ResourceVerification
from app.shared.resources import DriftStatus, ObservedState, ResourceKind, ResourceRef, ResourceSpec, Target


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyStep:
    """O que uma passada de `apply_next` fez num aparelho."""

    target: Target
    #: A linha que parou a passada; `None` quando todos os recursos já estão no estado desejado (ou ficam onde estão).
    line: ResourceLine | None
    #: O comando aberto ou reencontrado; `None` quando a linha não tem comando (em curso, pessoa, sem leitura).
    command: CommandRef | None
    detail: str


def _unicos(specs: Iterable[ResourceSpec]) -> list[ResourceSpec]:
    vistos: dict[ResourceRef, ResourceSpec] = {}
    for s in specs:
        vistos.setdefault(s.ref, s)
    return list(vistos.values())


class ResourceConvergence:
    def __init__(self, providers: Mapping[ResourceKind, ResourceProvider]) -> None:
        self._providers = dict(providers)

    def _provider(self, spec: ResourceSpec) -> ResourceProvider:
        provider = self._providers.get(spec.ref.kind)
        if provider is None:
            raise LookupError(f"nenhum provider para {spec.ref.kind.value}")
        return provider

    def read(self, specs: Sequence[ResourceSpec], targets: Sequence[Target]) -> list[ObservedState]:
        """Uma leitura por (recurso, alvo), sem efeito. Tipo sem provider fica sem leitura: o relatório o mostra como
        `not_read`, nunca como certo."""
        return [self._providers[s.ref.kind].read_current_state(s.ref, t)
                for t in targets for s in _unicos(specs) if s.ref.kind in self._providers]

    def report(self, specs: Sequence[ResourceSpec], targets: Sequence[Target], *, skill: SkillRef | None = None,
               skill_hash: str | None = None) -> PlanReport:
        return build_plan_report(specs, targets, self.read(specs, targets), skill=skill, skill_hash=skill_hash)

    def apply_next(self, specs: Sequence[ResourceSpec], target: Target, *,
                   run_ref: RunRef | None = None) -> ApplyStep:
        for line in self.report(specs, [target]).lines:
            drift = line.drift
            if drift.status in (DriftStatus.in_sync, DriftStatus.held):
                continue
            acao = next((a for a in line.actions if a.verb is not None), None)
            if acao is None:
                return ApplyStep(target=target, line=line, command=None,
                                 detail=f"{drift.ref.label()} ({drift.status.value}): {drift.detail}")
            comando = self._provider(drift.spec).apply(acao, spec=drift.spec, run_ref=run_ref)
            return ApplyStep(target=target, line=line, command=comando,
                             detail=f"{drift.ref.label()}: {comando.reason or acao.reason}")
        return ApplyStep(target=target, line=None, command=None, detail="todos os recursos no estado desejado")

    def verify(self, specs: Sequence[ResourceSpec], target: Target) -> list[ResourceVerification]:
        return [self._provider(s).verify(s, target) for s in _unicos(specs)]

    def reconcile(self, specs: Sequence[ResourceSpec], target: Target) -> list[ReconcileOutcome]:
        return [self._provider(s).reconcile(s, target) for s in _unicos(specs)]
