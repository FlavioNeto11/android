"""Relógio virtual da suíte (T.2, achado #164): PULA o tempo em vez de esperá-lo.

O problema: as ferramentas esperam o aparelho assentar com `asyncio.sleep` (0,4 s depois de tocar no campo, 0,5 s antes
de conferir a digitação, o `wait_for` de 2 s do verificador simulado...) e o aparelho falso envelhece a mensagem
("Enviando…" 0,15 s → "Enviada" → "Entregue" 0,4 s) pelo relógio REAL. Cada passo de mensagem custava ~2,9 s, e a suíte
do rodízio e das execuções era quase só isso. Encurtar o `sleep` sem mais nada não serve: o `wait_for` de 2 s existe
para o status evoluir, e 0,05 s de sono viraria "ainda enviando" e mudaria o que o teste prova.

A solução: UM relógio, dois consumidores. O `dormir` das ferramentas AVANÇA o deslocamento deste relógio (e cede o
laço uma vez) em vez de bloquear; o aparelho falso lê o mesmo relógio para envelhecer a mensagem. O tempo que o teste
"esperou" passou para o aparelho do mesmo jeito, só que de graça — a ordem dos fatos e as contagens são as de antes.

O que ele NÃO faz: não toca `time.monotonic()` do código de produção (prazos de etapa, orçamento do verificador, o
`wait` do harness). Esses seguem em tempo real; quem quer encurtar o orçamento do verificador usa
`Harness.encurtar_verificacao()`. Ligue por `harness.pular_o_tempo()`.
"""
from __future__ import annotations

import asyncio
import time


class RelogioVirtual:
    """`agora()` = relógio monotônico real + o quanto o teste já "dormiu"."""

    def __init__(self) -> None:
        self.deslocamento = 0.0
        self.dormidas: list[float] = []          # o que as ferramentas pediram: prova de que o tempo foi PULADO

    def agora(self) -> float:
        return time.monotonic() + self.deslocamento

    def avancar(self, segundos: float) -> None:
        self.deslocamento += max(0.0, float(segundos))

    async def dormir(self, segundos: float) -> None:
        """Substitui `asyncio.sleep` nas ferramentas: o tempo passa para o aparelho, o teste não espera.
        O `sleep(0)` cede o laço, para que as outras tarefas (despacho, monitor) andem entre uma espera e outra."""
        self.dormidas.append(float(segundos))
        self.avancar(segundos)
        await asyncio.sleep(0)

    @property
    def total_pulado_s(self) -> float:
        return sum(self.dormidas)
