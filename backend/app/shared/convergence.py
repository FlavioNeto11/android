"""`apply`, `verify` e `reconcile` de um recurso declarativo (design §11, §14.5): as regras comuns aos quatro tipos.

Cada provider sabe LER o seu recurso e montar os parâmetros do comando; o que vale para todos mora aqui, uma vez:

* **`apply` só por `commands`** (R10), pelo `CommandBus`. Nunca um efeito que o sistema não tenha hoje: o verbo é o
  que o `plan` escolheu, e o `plan` só escolhe verbo que existe (`shared/resources.py`).
* **Aplicar duas vezes não abre dois comandos.** A chave é determinística por (aparelho, recurso, verbo) e numerada:
  enquanto o último comando dela está em voo, o pedido o devolve; o banco (chave única) desempata a corrida.
* **`uncertain` nunca se repete** (R3/R4): o pedido devolve o incerto em vez de abrir outro. Quem o fecha é a prova
  (`reconcile`) ou uma pessoa.
* **`on_missing` decide quem dispara.** `apply` dispara a convergência; `wait` a deixa com o mecanismo de hoje (o
  rodízio e as portas do `_tick`), sem comando daqui; `ask` já não tem verbo (pessoa). Ler (`observe`) vale para os
  três: é a resposta a `unknown`, e não muda o alvo.
* **O mundo pode ter mudado entre planejar e aplicar**: antes de pedir, relê e replaneja; ação que o recurso não pede
  mais não vira comando.
* **`verify` só prova com leitura positiva**: `in_sync` do `diff` sobre o que acabou de ser lido. `unknown` continua
  `unknown`; divergir não é falha.
* **`reconcile` é do hospedeiro** (R11, `so_meu`) e só fecha `uncertain` como SUCESSO, com prova observada DEPOIS do
  comando. Ausência de prova não fecha nada — nem como falha.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from app.shared.commands import OPEN, UNSETTLED, CommandBus, CommandRef, RunRef, key_prefix, sequence_of
from app.shared.resources import (ActionPurpose, Drift, DriftStatus, ObservedState, OnMissing, ResourceAction,
                                  ResourceRef, ResourceSpec, Target)

#: `commands.requested_by` dos pedidos feitos por um recurso. Nunca `system`: é por ele que a escada de reparo conta
#: os próprios degraus (`CommandStore.remediacoes_recentes`), e um `start` de recurso viraria degrau de reparo.
REQUESTED_BY = "recursos"


class VerifyStatus(StrEnum):
    #: Leitura positiva: o observado é o desejado (`in_sync`).
    proved = "proved"
    #: Leu, e ainda não é o desejado (diverge, em curso, fica na versão que tem, só pessoa). Não é falha.
    not_proved = "not_proved"
    #: Não se sabe: nada foi lido, ou o lido não sustenta afirmação nenhuma.
    unknown = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceVerification:
    status: VerifyStatus
    drift: Drift

    @property
    def detail(self) -> str:
        return self.drift.detail


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconcileOutcome:
    ref: ResourceRef
    target: Target
    #: `False`: este backend não hospeda o aparelho, e nada foi lido nem fechado (R11).
    hosted: bool
    detail: str
    verify: ResourceVerification | None = None
    #: `uncertain` que a prova fechou como `succeeded`.
    settled: tuple[CommandRef, ...] = ()
    #: `uncertain` que continuam esperando prova ou pessoa.
    still_uncertain: tuple[CommandRef, ...] = ()


class ResourceReader(Protocol):
    """A parte sem efeito de um provider (parte 1 da fase H)."""

    def read_current_state(self, ref: ResourceRef, target: Target) -> ObservedState: ...

    def diff(self, desired: ResourceSpec, observed: ObservedState) -> Drift: ...

    def plan(self, drift: Drift) -> list[ResourceAction]: ...


#: (observado, verbo) → parâmetros do comando, ou a frase de por que não dá para pedir (ex.: sem versão promovida).
ParamsOf = Callable[[ObservedState, str], Mapping[str, str] | str]
#: Quando o observado foi lido do aparelho (ISO, a mesma forma de `commands.created_at`); `None` = não se sabe.
ProofTime = Callable[[ObservedState], str | None]


def outcome_of(drift: Drift) -> ResourceVerification:
    if drift.status is DriftStatus.in_sync and drift.observed is not None:
        return ResourceVerification(status=VerifyStatus.proved, drift=drift)
    if drift.status is DriftStatus.unknown:
        return ResourceVerification(status=VerifyStatus.unknown, drift=drift)
    return ResourceVerification(status=VerifyStatus.not_proved, drift=drift)


def verify_resource(reader: ResourceReader, spec: ResourceSpec, target: Target) -> ResourceVerification:
    """Relê e compara. Nada de memória do que foi pedido: só o estado observado prova."""
    return outcome_of(reader.diff(spec, reader.read_current_state(spec.ref, target)))


def _sem_comando(action: ResourceAction, verb: str, motivo: str) -> CommandRef:
    return CommandRef(instance_id=action.target.instance_id, verb=verb, reason=motivo)


def apply_action(bus: CommandBus, reader: ResourceReader, action: ResourceAction, *, spec: ResourceSpec,
                 params_of: ParamsOf, run_ref: RunRef | None, requested_by: str = REQUESTED_BY) -> CommandRef:
    """Pede o comando da ação, uma vez só. Devolve o comando aberto ou reencontrado, ou por que nenhum foi aberto."""
    if action.ref != spec.ref:
        raise ValueError(f"a ação é de {action.ref.label()}, e o recurso é {spec.ref.label()}")
    verbo = action.verb
    if verbo is None or action.purpose is ActionPurpose.ask:
        raise ValueError("ação de pessoa não vira comando")
    iid = action.target.instance_id
    if action.purpose is ActionPurpose.converge and spec.on_missing is OnMissing.wait:
        return _sem_comando(action, verbo, f"{spec.ref.label()} pede '{OnMissing.wait.value}': quem converge é o "
                                           "despacho de hoje (rodízio e portas do app e da sessão), não o recurso")
    prefixo = key_prefix(iid, spec.ref.kind.value, spec.ref.target, verbo)
    anterior = bus.latest(iid, verbo, prefixo)
    if anterior is not None and anterior.status is not None and anterior.status in OPEN | UNSETTLED:
        motivo = ("o comando anterior ficou sem desfecho e não se repete: só a prova ou uma pessoa o fecham"
                  if anterior.status in UNSETTLED else "o comando anterior ainda está em curso")
        return replace(anterior, deduplicated=True, reason=motivo)
    observado = reader.read_current_state(spec.ref, action.target)
    drift = reader.diff(spec, observado)
    if not any(a.purpose is action.purpose and a.verb == verbo for a in reader.plan(drift)):
        return _sem_comando(action, verbo, f"o recurso não pede mais '{verbo}': {drift.detail}")
    params = params_of(observado, verbo)
    if isinstance(params, str):
        return _sem_comando(action, verbo, params)
    chave = f"{prefixo}#{sequence_of(anterior.idempotency_key if anterior else None) + 1}"
    motivo = f"{spec.ref.label()}: {action.reason}" + (f" (execução {run_ref.label()})" if run_ref else "")
    return bus.request(iid, verbo, params, requested_by=requested_by, run_ref=run_ref, idempotency_key=chave,
                       reason=motivo)


def reconcile_resource(bus: CommandBus, reader: ResourceReader, spec: ResourceSpec, target: Target, *,
                       verbs: tuple[str, ...], proof_time: ProofTime | None) -> ReconcileOutcome:
    """Fecha os `uncertain` deste recurso que o estado observado prova. `proof_time=None`: o observado é ao vivo (o
    runtime em memória), então toda leitura é posterior ao comando."""
    iid = target.instance_id
    if not bus.hosts(iid):
        return ReconcileOutcome(ref=spec.ref, target=target, hosted=False,
                                detail=f"{iid} é hospedado por outro backend: reconciliar é de quem o hospeda")
    incertos = [c for v in verbs for c in bus.unsettled(iid, v, key_prefix(iid, spec.ref.kind.value,
                                                                            spec.ref.target, v))]
    observado = reader.read_current_state(spec.ref, target)
    resultado = outcome_of(reader.diff(spec, observado))
    if not incertos:
        return ReconcileOutcome(ref=spec.ref, target=target, hosted=True, verify=resultado,
                                detail="nenhum comando deste recurso ficou sem desfecho")
    if resultado.status is not VerifyStatus.proved:
        return ReconcileOutcome(ref=spec.ref, target=target, hosted=True, verify=resultado,
                                still_uncertain=tuple(incertos),
                                detail=f"sem prova, o incerto continua incerto: {resultado.detail}")
    momento = proof_time(observado) if proof_time is not None else None
    fechados: list[CommandRef] = []
    restantes: list[CommandRef] = []
    for c in incertos:
        # A prova tem de ser POSTERIOR ao comando: uma leitura de antes dele não diz nada sobre o que ele fez.
        fresca = proof_time is None or (momento is not None and c.created_at is not None and momento >= c.created_at)
        if c.command_id is None or not fresca:
            restantes.append(c)
            continue
        fechados.append(bus.settle(c.command_id, proof=f"verificado pelo estado real de {spec.ref.label()} em {iid}: "
                                                       f"{resultado.detail}"))
    detalhe = (f"{len(fechados)} comando(s) incerto(s) fechado(s) pela prova"
               + (f"; {len(restantes)} sem leitura posterior a eles" if restantes else ""))
    return ReconcileOutcome(ref=spec.ref, target=target, hosted=True, verify=resultado, settled=tuple(fechados),
                            still_uncertain=tuple(restantes), detail=detalhe)
