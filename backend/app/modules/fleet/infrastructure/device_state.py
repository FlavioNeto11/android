"""`ResourceProvider` de `device.state` (design §7, §8, §11): a leitura, o `diff` e o `plan`.

Lê `instances.desired_state` do banco e o observado do runtime em memória (`DeviceRuntimeView`), sem efeito
nenhum: nada de `publish`, `set_desired_state` ou sonda. `apply` (pedir ao rodízio, por `commands`), `verify` e
`reconcile` são a segunda parte da fase H.
"""
from __future__ import annotations

from collections.abc import Mapping

from app.db import Database
from app.modules.fleet.application.ports import DeviceRuntimeView
from app.modules.fleet.domain.resources import (DeviceObserved, DeviceState, ReadinessPhase, diff_device_state,
                                                plan_device_state)
from app.shared.resources import (Drift, ObservedState, ResourceAction, ResourceKind, ResourceRef, ResourceSpec,
                                  Target, known)


def _texto(valor: object) -> str | None:
    if valor is None or isinstance(valor, str):
        return valor
    raise TypeError(f"esperado texto ou nulo, veio {type(valor).__name__}")


class DeviceStateProvider:
    kind = ResourceKind.device_state

    def __init__(self, db: Database, runtimes: Mapping[str, DeviceRuntimeView]) -> None:
        self._db = db
        self._runtimes = runtimes

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
