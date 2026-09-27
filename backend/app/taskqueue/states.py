"""Máquina de estados das etapas. Qualquer transição fora desta tabela é rejeitada.

A tabela mora no domínio de execução (`app/modules/execution/domain/states.py`), junto com as da execução, do
objetivo e da tentativa; aqui ela é a MESMA, só que com os enums de `app.models`, que é como a fila a consome. A
etapa já era imposta antes das outras e continua sendo: é `check_transition`, chamada por
`Repository.transition_step`.
"""
from __future__ import annotations

from ..models import StepStatus as S
from ..modules.execution.domain.states import STEP_TRANSITIONS as _TABELA_DO_DOMINIO

STEP_TRANSITIONS: dict[S, set[S]] = {S(de): {S(para) for para in destinos}
                                     for de, destinos in _TABELA_DO_DOMINIO.items()}

STEP_ACTIVE = {S.running, S.verifying}
STEP_TERMINAL = {S.succeeded, S.failed, S.cancelled, S.skipped}
STEP_OPEN = {S.pending, S.ready, S.running, S.verifying, S.retry_wait}


class InvalidTransition(Exception):
    pass


def check_transition(current: S | str, target: S | str) -> None:
    cur, tgt = S(current), S(target)
    if tgt not in STEP_TRANSITIONS[cur]:
        raise InvalidTransition(f"transição inválida de etapa: {cur.value} → {tgt.value}")
