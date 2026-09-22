"""Máquina de estados dos comandos. Qualquer transição fora desta tabela é rejeitada.

Mesmo desenho de `taskqueue/states.py` de propósito: um comando do painel não merece menos rigor que uma etapa
da IA, e quem já conhece uma tabela lê a outra sem aprender nada novo.
"""
from __future__ import annotations

from ..models import CommandState as C

COMMAND_TRANSITIONS: dict[C, set[C]] = {
    # Recusa acontece ANTES de despachar: `rejected` é terminal e prova que o aparelho não foi tocado.
    # `failed` também cabe aqui: o processo pode cair entre gravar e despachar, e aí nada aconteceu — dizer
    # `uncertain` nesse caso seria alarme falso.
    # `cancel_requested` também sai daqui: um comando do painel pode ser cancelado ANTES de sair pelo socket, e
    # quem despacha fecha como `cancelled` ao ver o pedido. Ir direto a `cancelled` na rota seria afirmar que
    # nada saiu enquanto a tarefa de despacho ainda pode estar no meio do envio — o pedido é que fica registrado.
    C.created: {C.dispatched, C.rejected, C.failed, C.cancel_requested, C.cancelled},
    # Despachado sem ACK é o estado mais perigoso: se o processo cair aqui, não sabemos se chegou → `uncertain`.
    # `succeeded` direto de `dispatched`/`acked` é DELIBERADO: desde que `running` só é carimbado no primeiro
    # progresso do worker, um verbo curto (`stop`, `home`) pode terminar sem nunca relatar progresso. Ir a
    # `succeeded` deixando `started_at` nulo é o registro honesto — melhor que o carimbo falso de `running` no
    # mesmo milissegundo do despacho, que era o defeito.
    C.dispatched: {C.acked, C.running, C.succeeded, C.failed, C.uncertain, C.cancel_requested, C.cancelled},
    C.acked: {C.running, C.succeeded, C.failed, C.uncertain, C.cancel_requested, C.cancelled},
    C.running: {C.succeeded, C.failed, C.uncertain, C.cancel_requested},
    # Pedir cancelamento não encerra nada: o worker ainda pode concluir, falhar, ou confirmar o cancelamento.
    C.cancel_requested: {C.cancelled, C.succeeded, C.failed, C.uncertain},
    # `uncertain` só sai por decisão de alguém (ou por verificação do estado real), nunca sozinho.
    C.uncertain: {C.succeeded, C.failed, C.cancelled},
    C.succeeded: set(),
    C.failed: set(),
    C.rejected: set(),
    C.cancelled: set(),
}

#: Comando que ainda pode mudar de estado por conta própria — o que a reconciliação do boot precisa olhar.
COMMAND_OPEN = {C.created, C.dispatched, C.acked, C.running, C.cancel_requested}
#: Acabou, para sempre.
COMMAND_TERMINAL = {C.succeeded, C.failed, C.rejected, C.cancelled}
#: Terminou sem que se saiba o efeito. Não é terminal: alguém ainda decide.
COMMAND_UNSETTLED = {C.uncertain}


class InvalidCommandTransition(Exception):
    pass


def check_transition(current: C | str, target: C | str) -> None:
    cur, tgt = C(current), C(target)
    if tgt not in COMMAND_TRANSITIONS[cur]:
        raise InvalidCommandTransition(f"transição inválida de comando: {cur.value} → {tgt.value}")
