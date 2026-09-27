"""Recurso `device.state` (design §11): o aparelho no ar e pronto.

A tabela de decisão imita o despacho de hoje, sem mudar a ordem nem o critério:

* pronto = `online` + prontidão `ready` (`devices/prontidao.py`); `online` sozinho não basta desde o wake remoto de
  25/09 (framework congelado com o aparelho "online");
* parado (`stopped`, `absent`) converge por `start` e hibernado por `wake`, os dois pelo RODÍZIO (`request_start` →
  `pedir_ciclo_de_vida`), nunca "por fora" dele. `wake` só com snapshot válido, e no remoto só se o worker declara o
  verbo — a mesma escolha de `DeviceManager.request_start`;
* `desired_state='stopped'` (alguém mandou parar) NÃO bloqueia: o rodízio de hoje liga sob demanda mesmo assim, e a
  fase H não muda comportamento. Vira um código próprio, que o relatório mostra como risco;
* aparelho de outra máquina sem worker que o ligue é espera (o despacho espera o worker antes de bloquear);
* sem runtime neste processo não há estado observado: `unknown`, e não existe comando que "leia" o aparelho — o
  monitor é quem observa. Sem ação, e nunca `in_sync`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.shared.resources import (UNSUPPORTED_DESIRED, Drift, DriftStatus, ObservedState, ResourceAction,
                                  ResourceKind, ResourceSpec, plan_by_rules)

KIND = ResourceKind.device_state
#: O único estado desejado que este recurso sabe perseguir.
DESIRED_ONLINE = "online"


class DeviceState(StrEnum):
    """`models.InstanceState`, repetido porque o domínio não vê `app.models` (D2); o teste confere a igualdade."""

    absent = "absent"
    stopped = "stopped"
    hibernated = "hibernated"
    booting = "booting"
    online = "online"
    stopping = "stopping"
    error = "error"


class ReadinessPhase(StrEnum):
    """A escada de prontidão (`models.ReadinessInfo.phase`)."""

    not_running = "not_running"
    process_running = "process_running"
    adb_device = "adb_device"
    boot_completed = "boot_completed"
    android_responsive = "android_responsive"
    ready = "ready"


class DeviceVerb(StrEnum):
    """Verbos de ciclo de vida que convergem este recurso (`commands/despacho.LIFECYCLE_ACTIONS`)."""

    start = "start"
    wake = "wake"


#: O que cada verbo arrisca, para o relatório do plano.
VERB_RISKS: dict[str, str] = {
    DeviceVerb.start.value: "ligar ocupa uma vaga do rodízio e RAM da máquina que hospeda o aparelho",
    DeviceVerb.wake.value: "acordar do snapshot ocupa uma vaga do rodízio e RAM da máquina que hospeda o aparelho",
}


class DeviceCode(StrEnum):
    ready = "ready"
    not_registered = "not_registered"
    store_device = "store_device"
    unobserved = "unobserved"
    readiness_climbing = "readiness_climbing"
    transitioning = "transitioning"
    worker_unavailable = "worker_unavailable"
    stopped = "stopped"
    stopped_by_decision = "stopped_by_decision"
    hibernated = "hibernated"
    hibernated_cold = "hibernated_cold"
    error = "error"


_CONVERGE: dict[str, str] = {
    DeviceCode.stopped.value: DeviceVerb.start.value,
    DeviceCode.stopped_by_decision.value: DeviceVerb.start.value,
    DeviceCode.hibernated.value: DeviceVerb.wake.value,
    DeviceCode.hibernated_cold.value: DeviceVerb.start.value,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceObserved(ObservedState):
    #: Há linha em `instances`.
    registered: bool
    #: Este processo tem o runtime do aparelho (o estado observado vive em memória, não no banco).
    runtime: bool = False
    #: `None` também quando o valor lido não é um estado conhecido: o que não se reconhece não vira certeza.
    state: DeviceState | None = None
    state_detail: str | None = None
    readiness: ReadinessPhase | None = None
    #: `instances.desired_state` (014): a DECISÃO registrada. Nulo = nenhuma.
    desired_state: str | None = None
    #: Aparelho-loja: nunca executa tarefa.
    store: bool = False
    #: Aparelho de outra máquina, ligado pelo worker que o hospeda.
    external: bool = False
    #: Verbos que o worker declara neste aparelho (vazio = worker fora do ar). Só conta no externo.
    worker_verbs: tuple[str, ...] = ()
    snapshot_valid: bool = False

    def facts(self) -> tuple[tuple[str, str], ...]:
        pares = [("registrado", "sim" if self.registered else "não"),
                 ("estado", self.state.value if self.state is not None else "desconhecido"),
                 ("prontidão", self.readiness.value if self.readiness is not None else "desconhecida"),
                 ("desejado registrado", self.desired_state or "nenhum")]
        if self.external:
            pares.append(("verbos do worker", ",".join(self.worker_verbs) or "nenhum"))
        if self.store:
            pares.append(("loja", "sim"))
        return tuple(pares)


def _drift(spec: ResourceSpec, observed: DeviceObserved, status: DriftStatus, code: str, detail: str) -> Drift:
    return Drift(spec=spec, target=observed.target, status=status, code=code, detail=detail, observed=observed)


def diff_device_state(spec: ResourceSpec, observed: ObservedState) -> Drift:
    if spec.ref.kind is not KIND or not isinstance(observed, DeviceObserved):
        raise TypeError(f"diff de {KIND.value} recebeu {spec.ref.kind.value}/{type(observed).__name__}")
    o, iid = observed, observed.target.instance_id
    if spec.desired != DESIRED_ONLINE:
        return _drift(spec, o, DriftStatus.unsupported, UNSUPPORTED_DESIRED,
                      f"{KIND.value} só sabe perseguir '{DESIRED_ONLINE}', e o pedido foi '{spec.desired_text()}'")
    if not o.registered:
        return _drift(spec, o, DriftStatus.blocked, DeviceCode.not_registered,
                      f"o aparelho {iid} não existe no inventário (instances)")
    if o.store:
        return _drift(spec, o, DriftStatus.blocked, DeviceCode.store_device,
                      f"{iid} é o aparelho-loja (Play Store): ele não executa tarefas")
    if not o.runtime or o.state is None:
        return _drift(spec, o, DriftStatus.unknown, DeviceCode.unobserved,
                      f"não há estado observado de {iid} neste processo; quem o observa é o monitor do parque")
    estado = o.state
    if estado is DeviceState.online:
        if o.readiness is ReadinessPhase.ready:
            return _drift(spec, o, DriftStatus.in_sync, DeviceCode.ready, f"{iid} no ar e pronto")
        degrau = o.readiness.value if o.readiness is not None else "desconhecido"
        return _drift(spec, o, DriftStatus.pending, DeviceCode.readiness_climbing,
                      f"{iid} no ar, subindo a escada de prontidão (degrau: {degrau})")
    if estado in (DeviceState.booting, DeviceState.stopping):
        return _drift(spec, o, DriftStatus.pending, DeviceCode.transitioning,
                      f"{iid} em '{estado.value}': o desfecho vem da operação em curso")
    if estado is DeviceState.error:
        return _drift(spec, o, DriftStatus.blocked, DeviceCode.error,
                      f"{iid} em erro" + (f": {o.state_detail}" if o.state_detail else ""))
    # parado, ausente ou hibernado: quem liga é o rodízio
    if o.external and DeviceVerb.start.value not in o.worker_verbs:
        return _drift(spec, o, DriftStatus.pending, DeviceCode.worker_unavailable,
                      f"{iid} é de outra máquina e o worker dela não está conectado ou não declara ligá-lo")
    if estado is DeviceState.hibernated:
        acorda = o.snapshot_valid and (not o.external or DeviceVerb.wake.value in o.worker_verbs)
        if acorda:
            return _drift(spec, o, DriftStatus.diverged, DeviceCode.hibernated,
                          f"{iid} hibernado: o rodízio o acorda do snapshot")
        return _drift(spec, o, DriftStatus.diverged, DeviceCode.hibernated_cold,
                      f"{iid} hibernado sem snapshot utilizável: o rodízio o liga do zero")
    if o.desired_state == DeviceState.stopped.value:
        return _drift(spec, o, DriftStatus.diverged, DeviceCode.stopped_by_decision,
                      f"{iid} em '{estado.value}' porque alguém mandou parar; o rodízio o liga mesmo assim sob demanda")
    return _drift(spec, o, DriftStatus.diverged, DeviceCode.stopped, f"{iid} em '{estado.value}': o rodízio o liga")


def plan_device_state(drift: Drift) -> list[ResourceAction]:
    """Nenhum comando lê o aparelho (o monitor observa sozinho): `unknown` não tem ação."""
    return plan_by_rules(drift, kind=KIND, converge=_CONVERGE, observe={})
