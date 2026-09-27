"""O canal de comandos visto pelo kernel (design §7, `CommandBus`; regra R10): verbo de aparelho vai SÓ por `commands`,
com cerca, outbox e diário — nunca por um terceiro caminho.

Mora no kernel pelo mesmo motivo de `shared/resources.py` (D5): quem PEDE comando são os providers de recurso de três
contextos pares (fleet, applications, identity) e o contexto de execução, que já depende dos três (`plan_report.py`).
Posta em `modules/execution/application/ports.py`, a porta faria um provider do parque importar execução, e os
contextos fechariam um ciclo (`test_arquitetura.py::test_contextos_novos_formam_um_dag`). O `ports.py` de execução a
reexporta, ao lado de `ResourceProvider`.

Aqui só há valor e forma: o vocabulário dos estados de comando, a referência a um comando e o `Protocol` do canal.
Quem implementa (`modules/execution/infrastructure/command_bus.py`) fala com `commands/despacho.py`.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class CommandStatus(StrEnum):
    """`models.CommandState`, repetido porque o kernel não vê `app.models`; o teste confere a igualdade."""

    created = "created"
    dispatched = "dispatched"
    acked = "acked"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    uncertain = "uncertain"
    rejected = "rejected"
    cancel_requested = "cancel_requested"
    cancelled = "cancelled"


#: Ainda em voo: o desfecho vem dele (`commands/states.COMMAND_OPEN`).
OPEN: frozenset[CommandStatus] = frozenset({CommandStatus.created, CommandStatus.dispatched, CommandStatus.acked,
                                           CommandStatus.running, CommandStatus.cancel_requested})
#: Terminou sem que se saiba o efeito: ninguém repete, só a prova ou uma pessoa fecham (R3/R4).
UNSETTLED: frozenset[CommandStatus] = frozenset({CommandStatus.uncertain})


@dataclass(frozen=True, slots=True, kw_only=True)
class RunRef:
    """Qual execução (e qual objetivo dela) pediu o comando — a trilha, não a autoridade."""

    run_id: str
    objective_id: str | None = None

    def label(self) -> str:
        return f"{self.run_id}/{self.objective_id}" if self.objective_id else self.run_id


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRef:
    """O comando que o pedido abriu (ou reencontrou), ou por que nenhum foi aberto.

    `command_id` nulo quer dizer que NADA foi gravado nem tocado: a recusa aconteceu antes do comando (aparelho
    ocupado, fora do ar, `on_missing: wait`, o recurso já não pedia a ação). É diferente de `rejected`, que é um
    comando gravado e recusado pelo pré-voo do despacho.
    """

    instance_id: str
    verb: str
    command_id: str | None = None
    status: CommandStatus | None = None
    idempotency_key: str | None = None
    created_at: str | None = None
    #: O comando já existia: o pedido o reencontrou em vez de abrir outro.
    deduplicated: bool = False
    reason: str | None = None

    @property
    def requested(self) -> bool:
        return self.command_id is not None


class CommandBus(Protocol):
    """Verbos de aparelho, sempre por `commands` + outbox + cerca (R10). Síncrono como o despacho de hoje: o pedido
    grava o comando e agenda o trabalho; o desfecho fecha o próprio comando, nunca quem pediu."""

    def request(self, instance_id: str, verb: str, params: Mapping[str, str], *, requested_by: str,
                run_ref: RunRef | None, idempotency_key: str, reason: str) -> CommandRef:
        """Abre o comando com esta chave de idempotência, ou devolve o que já a tem. Recusa sem gravar nada quando o
        pedido não pode seguir (aparelho ocupado, fora do ar, de outra réplica sem caminho)."""
        ...

    def latest(self, instance_id: str, verb: str, key_prefix: str) -> CommandRef | None:
        """O comando mais recente deste verbo cuja chave começa com `key_prefix#`."""
        ...

    def unsettled(self, instance_id: str, verb: str, key_prefix: str) -> list[CommandRef]:
        """Os `uncertain` deste verbo cuja chave começa com `key_prefix#`, do mais antigo ao mais novo."""
        ...

    def settle(self, command_id: str, *, proof: str) -> CommandRef:
        """`uncertain` → `succeeded`, com a frase que prova. Só quem viu o estado observado chama (R4)."""
        ...

    def hosts(self, instance_id: str) -> bool:
        """Este backend hospeda o aparelho (`so_meu`: `instances.hosted_by` nulo ou o meu)? Reconciliar é dele (R11)."""
        ...


#: `InstanceActionBody.idempotency_key` aceita até 120 caracteres; o prefixo longo vira hash, com folga para `#n`.
_PREFIXO_MAX = 100


def key_prefix(instance_id: str, kind: str, target: str | None, verb: str) -> str:
    """Prefixo da chave de idempotência de um recurso: (aparelho, tipo, alvo, verbo). A chave é `prefixo#n`.

    Determinística de propósito: dois pedidos simultâneos do mesmo `n` colidem na chave única de `commands` e o
    segundo recebe o comando do primeiro (`CommandStore.create`) — o banco é quem garante "um comando só".
    """
    prefixo = f"res:{instance_id}:{kind}:{target or '-'}:{verb}"
    if len(prefixo) <= _PREFIXO_MAX:
        return prefixo
    return "res:" + hashlib.sha256(prefixo.encode("utf-8")).hexdigest()[:40]


def sequence_of(key: str | None) -> int:
    """O `n` de `prefixo#n`; 0 quando a chave não tem a forma (comando de outro caminho)."""
    if not key or "#" not in key:
        return 0
    cauda = key.rsplit("#", 1)[1]
    return int(cauda) if cauda.isdigit() else 0
