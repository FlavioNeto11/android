"""O relógio parado do portal (T.2, achado #164): os testes do portal trocam `state.portal.relogio` por este, e a hora
das rotas, do token da página, do laço do contato e do prazo do vigia passa a ser a que o teste manda.

Parado na hora em que foi criado (por padrão, a hora real daquele instante, para as linhas que o teste grava com
`now()` continuarem perto), e só anda por `avancar`. A hora de parede e a monotônica andam juntas.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.util import now


class RelogioParado:
    def __init__(self, inicio: datetime | None = None) -> None:
        self._agora = inicio or now()
        self._monotonico = 1000.0

    def agora(self) -> datetime:
        return self._agora

    def epoch_s(self) -> float:
        return self._agora.timestamp()

    def monotonico_s(self) -> float:
        return self._monotonico

    def avancar(self, segundos: float) -> None:
        self._agora += timedelta(seconds=segundos)
        self._monotonico += segundos
