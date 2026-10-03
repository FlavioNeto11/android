"""30.34: liga a autopublicação em sombra ao Livro. O serviço fica pendurado no Livro (as métricas e o relatório o acham
pelo tipo) e o laço é registrado à parte, como o do curador: o `AppState` o sobe sob a trava de líder.

De fábrica o modo é `off` e nada roda; o modo é lido a cada volta, então mudar o config com o processo no ar vale na
próxima. A volta só lê o livro e grava sinais (sem IA, sem aparelho): roda numa thread, fora do loop."""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime

from app.config import AutopublicacaoCfg
from app.db import Database
from app.modules.learning.application.autopublicacao import ResultadoDaVolta, ServicoDeAutopublicacao
from app.modules.learning.application.ports import CatalogoDeRisco, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.autopublicacao import ModoDaAutopublicacao
from app.modules.learning.infrastructure.autopublicacao_sql import LivroDaSombraSql
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql

log = logging.getLogger("poc.aprendizado")


class LacoDaAutopublicacao:
    nome = "autopublicacao"

    def __init__(self, servico: ServicoDeAutopublicacao, intervalo_s: Callable[[], float]) -> None:
        self.servico = servico
        self._intervalo_s = intervalo_s
        # A volta grava sinais numa thread: o desligamento (`AppState`, que chama `parar`) espera ela acabar antes de
        # fechar o banco, como no laço do curador.
        self._trava = threading.Lock()
        self._parado = False
        self._ociosa = threading.Event()
        self._ociosa.set()

    def _volta(self, lider: Callable[[], int | None]) -> ResultadoDaVolta | None:
        with self._trava:
            if self._parado:
                return None
            self._ociosa.clear()
        try:
            if self.servico.modo is ModoDaAutopublicacao.OFF or lider() is None:
                return None
            return self.servico.uma_volta()
        finally:
            self._ociosa.set()

    def parar(self, timeout_s: float) -> bool:
        """Nenhuma volta nova; espera até `timeout_s` a que está em curso. `True` se a thread já está ociosa."""
        with self._trava:
            self._parado = True
        return self._ociosa.wait(max(0.0, timeout_s))

    async def laco(self, lider: Callable[[], int | None]) -> None:
        while True:
            await asyncio.sleep(max(60.0, self._intervalo_s()))
            try:
                await asyncio.to_thread(self._volta, lider)
            except Exception:  # noqa: BLE001 - a sombra nunca derruba o processo
                log.exception("aprendizado: autopublicação em sombra")


def ligar(servico: LearningService, repo: RepositorioDeAprendizado, db: Database, *,
          config: Callable[[], AutopublicacaoCfg], relogio: Callable[[], datetime],
          catalogo: CatalogoDeRisco | None) -> ServicoDeAutopublicacao:
    auto = ServicoDeAutopublicacao(servico, repo, RegistroDeRevisoesSql(db), DossiesSql(db, servico, repo, catalogo),
                                   LivroDaSombraSql(db, repo), modo=lambda: ModoDaAutopublicacao(config().modo),
                                   relogio=relogio)
    servico.anexar(auto)
    servico.registrar_laco(LacoDaAutopublicacao(auto, lambda: float(config().intervalo_s)))
    return auto


__all__ = ["LacoDaAutopublicacao", "ligar"]
