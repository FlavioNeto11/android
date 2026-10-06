"""31.150 (K-106): o fluxo de prova religado para uso real. Puro: sem banco nem relógio.

Todo fluxo que nasce de uma prova (`nascido_de_prova`, 31.130) termina desligado, e a volta era o `PUT /api/flows/{id}`
genérico, com o motivo opcional: o Livro não distinguia "fluxo de prova em uso real" de "esquecido ligado". Agora, ligar
um fluxo de prova exige o motivo, e a trilha grava "religado para uso real: <motivo>". `em_uso_real_desde` é lido da
trilha: a data dessa linha enquanto ela for a última do fluxo. Desligar de novo (pessoa ou sistema) apaga o selo; a
marca de origem (`nascido_de_prova`) nunca se apaga.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.modules.learning.domain.livro import ESTADO_DO_FLUXO

#: O começo do motivo na trilha (`learning_transitions.reason`) quando uma pessoa religa um fluxo de prova.
RELIGADO_PARA_USO_REAL = "religado para uso real: "
#: O estado do livro de um fluxo ligado (`livro.ESTADO_DO_FLUXO["active"]`).
LIGADO = ESTADO_DO_FLUXO["active"].value


def motivo_do_religamento(motivo: str) -> str:
    return RELIGADO_PARA_USO_REAL + " ".join(motivo.split())


def em_uso_real_desde(transicoes: Iterable[tuple[str | None, str | None, str | None]]) -> str | None:
    """`(to_state, reason, decided_at)` do fluxo, da mais antiga para a mais nova. A data do religamento se ele for a
    última transição; `None` se nunca foi religado ou se algo mudou o estado depois."""
    ultima: tuple[str | None, str | None, str | None] | None = None
    for t in transicoes:
        ultima = t
    if ultima is None or ultima[0] != LIGADO or not (ultima[1] or "").startswith(RELIGADO_PARA_USO_REAL):
        return None
    return ultima[2]
