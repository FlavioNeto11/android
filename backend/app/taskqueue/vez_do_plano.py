"""31.237: o 1º plano de uma operação sai sozinho; os planos irmãos esperam por ele para ler o cache do prompt.

Medido na onda 2 (07/10, `ai_calls` em `mode=ro`): os 3 alvos de uma operação planejaram no mesmo instante, e cada plano
GRAVOU o mesmo prefixo estável (`cache_write` 3.243, `cache_read` 0), a US$ 0,018 o plano. Na onda 1, o plano que leu o
prefixo do cache (3.066) custou US$ 0,0113. Gravar no cache custa mais que a entrada cheia; ler custa uma fração.

A regra: por operação, o 1º plano a chegar é o primeiro; os que chegam enquanto ele roda esperam o fim dele (com ou sem
sucesso) e então seguem juntos, já com o prefixo no cache. A espera tem teto (`ai.espera_do_plano_irmao_s`): vencido
o teto, o irmão segue como antes. `0` desliga. Fora de operação, nada muda. Não ocupa vaga de IA enquanto espera: a
espera é antes da chamada.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

log = logging.getLogger("poc.runs")

Papel = Literal["sozinho", "primeiro", "irmao", "irmao_sem_esperar"]


class VezDoPlano:
    def __init__(self) -> None:
        #: o plano que aquece o cache de cada operação, enquanto roda
        self._aquecendo: dict[str, asyncio.Event] = {}

    @asynccontextmanager
    async def vez(self, operacao_id: str | None, espera_s: float) -> AsyncIterator[Papel]:
        if not operacao_id or espera_s <= 0:
            yield "sozinho"
            return
        evento = self._aquecendo.get(operacao_id)
        if evento is None:
            evento = self._aquecendo[operacao_id] = asyncio.Event()
            try:
                yield "primeiro"
            finally:
                # quem chegar depois do fim já acha o prefixo no cache: segue sem esperar ninguém
                evento.set()
                self._aquecendo.pop(operacao_id, None)
            return
        try:
            await asyncio.wait_for(evento.wait(), timeout=espera_s)
        except TimeoutError:
            log.info("operação %s: o plano irmão não esperou o 1º além de %.0f s (31.237)", operacao_id, espera_s)
            yield "irmao_sem_esperar"
            return
        yield "irmao"


__all__ = ["Papel", "VezDoPlano"]
