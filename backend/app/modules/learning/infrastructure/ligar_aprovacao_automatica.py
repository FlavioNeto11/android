"""30.55: liga a aprovação automática ao Livro. O serviço fica pendurado no Livro (a rota e as métricas o acham pelo
tipo) e o laço é registrado à parte, como o da autopublicação: o `AppState` o sobe sob a trava de líder.

De fábrica o modo é `off` e nada roda; o modo é lido a cada volta, então mudar o config com o processo no ar vale na
próxima. A volta só lê o livro, grava sinais e, em `on`, transições (sem IA, sem aparelho): roda numa thread."""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime

from app.config import AprovacaoAutomaticaCfg
from app.db import Database
from app.modules.learning.application.aprovacao_automatica import ResultadoDaVolta, ServicoDeAprovacaoAutomatica
from app.modules.learning.application.ports import CatalogoDeRisco, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.aprovacao_automatica import ModoDaAprovacao
from app.modules.learning.infrastructure.aprovacao_automatica_sql import LivroDaAprovacaoSql
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql

log = logging.getLogger("poc.aprendizado")

#: A espera antes da primeira volta: o bastante para o processo assentar, bem menos que o intervalo (a orquestradora lê
#: o primeiro ciclo da sombra logo depois do deploy).
PRIMEIRA_VOLTA_S = 90.0


class LacoDaAprovacaoAutomatica:
    nome = "aprovacao_automatica"

    def __init__(self, servico: ServicoDeAprovacaoAutomatica, intervalo_s: Callable[[], float]) -> None:
        self.servico = servico
        self._intervalo_s = intervalo_s
        # A volta grava numa thread: o desligamento (`AppState`, que chama `parar`) espera ela acabar antes de fechar o
        # banco, como nos laços do curador e da autopublicação.
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
            if self.servico.modo is ModoDaAprovacao.OFF or lider() is None:
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
        espera = PRIMEIRA_VOLTA_S
        while True:
            await asyncio.sleep(espera)
            espera = max(60.0, self._intervalo_s())
            try:
                await asyncio.to_thread(self._volta, lider)
            except Exception:  # noqa: BLE001 - a régua nunca derruba o processo
                log.exception("aprendizado: aprovação automática")


def ligar(servico: LearningService, repo: RepositorioDeAprendizado, db: Database, *,
          config: Callable[[], AprovacaoAutomaticaCfg], relogio: Callable[[], datetime],
          catalogo: CatalogoDeRisco | None) -> ServicoDeAprovacaoAutomatica:
    aprovacao = ServicoDeAprovacaoAutomatica(servico, repo, RegistroDeRevisoesSql(db),
                                             DossiesSql(db, servico, repo, catalogo), LivroDaAprovacaoSql(db, repo),
                                             modo=lambda: ModoDaAprovacao(config().modo), relogio=relogio)
    servico.anexar(aprovacao)
    servico.registrar_laco(LacoDaAprovacaoAutomatica(aprovacao, lambda: float(config().intervalo_s)))
    return aprovacao


__all__ = ["PRIMEIRA_VOLTA_S", "LacoDaAprovacaoAutomatica", "ligar"]
