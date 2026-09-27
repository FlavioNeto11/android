"""`ResourceProvider` de `device.state` (design §7, §8, §11): leitura, `diff`, `plan` e, com um `CommandBus`, `apply`,
`verify` e `reconcile`.

Lê `instances.desired_state` do banco e o observado do runtime em memória (`DeviceRuntimeView`), sem efeito
nenhum: nada de `publish`, `set_desired_state` ou sonda.

Aplicar é pedir `start`/`wake` pelo canal de comandos — no despacho, `pedir_ciclo_de_vida`, o mesmo pedido que o
rodízio faz ao worker que hospeda o aparelho, com o mesmo pré-voo do painel. Nunca liga "por fora": sem o verbo
declarado pelo worker, o despacho não abre comando. A capacidade do rodízio (quantos no ar) continua sendo do `_tick`,
que é quem chama o `apply` quando ele for ligado; aqui só não se inventa outro caminho.

O observado é AO VIVO (memória do monitor), então a prova de um `start` incerto é o aparelho `online` + `ready`
agora — a mesma régua de `commands/reconciler.py`, um degrau mais estrita (pronto, não só no ar).
"""
from __future__ import annotations

from collections.abc import Mapping

from app.db import Database
from app.modules.fleet.application.ports import DeviceRuntimeView
from app.modules.fleet.domain.resources import (DeviceObserved, DeviceState, DeviceVerb, ReadinessPhase,
                                                diff_device_state, plan_device_state)
from app.shared.commands import CommandBus, CommandRef, RunRef
from app.shared.convergence import (ReconcileOutcome, ResourceVerification, apply_action, reconcile_resource,
                                    verify_resource)
from app.shared.resources import (Drift, ObservedState, ResourceAction, ResourceKind, ResourceRef, ResourceSpec,
                                  Target, known)

#: Os verbos que este recurso pede — é por eles que o `reconcile` procura os próprios incertos.
VERBS: tuple[str, ...] = tuple(v.value for v in DeviceVerb)


def _texto(valor: object) -> str | None:
    if valor is None or isinstance(valor, str):
        return valor
    raise TypeError(f"esperado texto ou nulo, veio {type(valor).__name__}")


def _parametros(observado: ObservedState, verbo: str) -> Mapping[str, str] | str:
    """Ciclo de vida não leva parâmetro: o despacho pede com `confirm=True`, como o rodízio."""
    return {}


class DeviceStateProvider:
    kind = ResourceKind.device_state

    def __init__(self, db: Database, runtimes: Mapping[str, DeviceRuntimeView], *,
                 bus: CommandBus | None = None) -> None:
        self._db = db
        self._runtimes = runtimes
        #: Sem canal, o provider é só leitura (o `PlanReport` não precisa de mais).
        self._bus = bus

    def read_current_state(self, ref: ResourceRef, target: Target) -> DeviceObserved:
        linha = self._db.one("SELECT desired_state FROM instances WHERE id=?", (target.instance_id,))
        if linha is None:
            return DeviceObserved(ref=ref, target=target, registered=False)
        desejado = _texto(linha["desired_state"])
        rt = self._runtimes.get(target.instance_id)
        if rt is None:
            # Processo sem o runtime (papel `api`, ou aparelho que o gerenciador ainda não carregou): não há
            # observado para ler, e o domínio diz `unknown`.
            return DeviceObserved(ref=ref, target=target, registered=True, desired_state=desejado)
        return DeviceObserved(
            ref=ref, target=target, registered=True, runtime=True, state=known(DeviceState, rt.state),
            state_detail=rt.state_detail, readiness=known(ReadinessPhase, rt.readiness_phase),
            desired_state=desejado, store=rt.store, external=rt.external,
            worker_verbs=tuple(sorted(rt.worker_verbs or ())), snapshot_valid=rt.snapshot_valid)

    def diff(self, desired: ResourceSpec, observed: ObservedState) -> Drift:
        return diff_device_state(desired, observed)

    def plan(self, drift: Drift) -> list[ResourceAction]:
        return plan_device_state(drift)

    def _canal(self) -> CommandBus:
        if self._bus is None:
            raise RuntimeError("DeviceStateProvider sem CommandBus: só leitura")
        return self._bus

    def apply(self, action: ResourceAction, *, spec: ResourceSpec, run_ref: RunRef | None = None) -> CommandRef:
        return apply_action(self._canal(), self, action, spec=spec, params_of=_parametros, run_ref=run_ref)

    def verify(self, spec: ResourceSpec, target: Target) -> ResourceVerification:
        return verify_resource(self, spec, target)

    def reconcile(self, spec: ResourceSpec, target: Target) -> ReconcileOutcome:
        return reconcile_resource(self._canal(), self, spec, target, verbs=VERBS, proof_time=None)
