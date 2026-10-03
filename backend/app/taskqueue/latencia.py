"""Latência por etapa (item 31.24, migração 088): o vocabulário das esperas e os cronômetros da tentativa.

Só mede. Nada aqui decide, espera ou chama o aparelho ou a IA: o executor e o repositório marcam o tempo do que já
fazem (`time.monotonic` dentro do processo; hora de parede só no que atravessa reinício, as `esperas`). Os limites de
cada coluna estão no cabeçalho da migração 088, e a leitura é `scripts/latencia-por-etapa.py`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

#: `objectives.wait_reason` → `esperas.motivo`. A vaga de IA (`ai_capacity`) e a resposta do modelo (`model_response`)
#: ficam de fora de propósito: já são `ai_calls.vaga_ms` e `ai_calls.ms`, e cada chamada de IA abriria e fecharia duas
#: linhas aqui. Motivo tipado novo que não esteja no mapa também não abre espera (a leitura o vê como "sem dono", que
#: é o sinal certo para o mapa crescer).
MOTIVO_POR_WAIT_REASON: Final[dict[str, str]] = {
    "device_slot": "aparelho",
    "profile_limit": "perfil",
    "rede": "rede",
    "pathfinder": "caminho",
}
#: O objetivo parado esperando a pessoa (`waiting_user`) é uma espera também: era o "pendurado" de 7–8 h da leitura de
#: 03/10 (r-20261002221213-8d1c0a), que só a pessoa (ou o cancelamento) encerra.
MOTIVO_PESSOA: Final = "pessoa"
MOTIVOS_DE_ESPERA: Final[tuple[str, ...]] = (*MOTIVO_POR_WAIT_REASON.values(), MOTIVO_PESSOA)


def motivo_da_espera(status: object, wait_reason: object) -> str | None:
    """O motivo da espera de um objetivo com este estado e esta espera tipada, ou `None` (não está esperando, ou espera
    o que `ai_calls` já mede). O estado vem antes: um objetivo em `waiting_user` espera a pessoa, qualquer que seja a
    espera tipada que tenha sobrado."""
    if status == "waiting_user":
        return MOTIVO_PESSOA
    return MOTIVO_POR_WAIT_REASON.get(wait_reason) if isinstance(wait_reason, str) else None


def ms_desde(inicio: float) -> int:
    """Milissegundos de `time.monotonic` desde `inicio`, inteiros (a coluna é INTEGER)."""
    return max(0, round((time.monotonic() - inicio) * 1000))


@dataclass(slots=True)
class TemposDaTentativa:
    """C-4 (migração 088): o que a tentativa gastou com o juiz e a evidência, somado ao longo dela e gravado UMA vez, no
    mesmo UPDATE da trilha da 045 (`note_attempt_strategy`). Zero = medido, não houve."""
    juiz_espera_ms: int = 0
    verificacao_ms: int = 0
    evidencia_ms: int = 0


__all__ = ["MOTIVOS_DE_ESPERA", "MOTIVO_PESSOA", "MOTIVO_POR_WAIT_REASON", "TemposDaTentativa", "motivo_da_espera",
           "ms_desde"]
