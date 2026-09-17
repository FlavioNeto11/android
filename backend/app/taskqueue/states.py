"""Máquina de estados das etapas. Qualquer transição fora desta tabela é rejeitada."""
from __future__ import annotations

from ..models import StepStatus as S

STEP_TRANSITIONS: dict[S, set[S]] = {
    S.pending: {S.ready, S.cancelled, S.skipped},
    S.ready: {S.running, S.cancelled, S.skipped, S.waiting_user},
    # running → ready: cedeu num ponto seguro (pausa/controle manual) SEM efeito externo pendente
    S.running: {S.verifying, S.retry_wait, S.waiting_user, S.failed, S.uncertain, S.cancelled, S.ready},
    S.verifying: {S.succeeded, S.retry_wait, S.waiting_user, S.failed, S.uncertain, S.cancelled, S.ready},
    S.retry_wait: {S.ready, S.cancelled, S.skipped},
    # decisões do usuário
    S.waiting_user: {S.ready, S.succeeded, S.failed, S.cancelled, S.skipped},
    S.uncertain: {S.ready, S.succeeded, S.failed, S.cancelled},
    S.failed: {S.ready},            # "tentar novamente os elegíveis"
    S.succeeded: set(),
    S.cancelled: set(),
    S.skipped: set(),
}

STEP_ACTIVE = {S.running, S.verifying}
STEP_TERMINAL = {S.succeeded, S.failed, S.cancelled, S.skipped}
STEP_OPEN = {S.pending, S.ready, S.running, S.verifying, S.retry_wait}


class InvalidTransition(Exception):
    pass


def check_transition(current: S | str, target: S | str) -> None:
    cur, tgt = S(current), S(target)
    if tgt not in STEP_TRANSITIONS[cur]:
        raise InvalidTransition(f"transição inválida de etapa: {cur.value} → {tgt.value}")
