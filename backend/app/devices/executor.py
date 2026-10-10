"""Executor exclusivo por dispositivo.

Uma única thread por aparelho: ações da IA, entradas manuais e capturas de tela passam todas por
aqui, então nunca há duas chamadas concorrentes no mesmo aparelho. Um timeout NÃO libera o
aparelho: a chamada anterior continua ocupando a thread ("zumbi") e `drain()` só retorna quando
ela realmente terminar — menos as da PRÉVIA do painel (`previa=True`), que são leitura de tela e não
são da etapa: `drain()` não as espera.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from ..automation.driver import DriverTimeout

T = TypeVar("T")


class DeviceExecutor:
    def __init__(self, name: str):
        self.name = name
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"dev-{name}")
        self._lock = threading.Lock()
        self._pending: set[Future[Any]] = set()
        self._zombies: set[Future[Any]] = set()
        # Futuros da PRÉVIA do painel (por identidade), marcados em `run(previa=True)`. `drain()` não os espera: em
        # r-20260928195344-02ee9e (android-06 saturado) a etapa, depois de um timeout, esperava também os screencaps
        # da prévia do foco — que não paravam de chegar — até estourar o teto e virar "aparelho travado".
        self._da_previa: set[object] = set()

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def has_zombie(self) -> bool:
        with self._lock:
            self._zombies = {f for f in self._zombies if not f.done()}
            return bool(self._zombies)

    async def run(self, fn: Callable[..., T], *args: Any, timeout: float, label: str = "",
                  previa: bool = False) -> T:
        """`previa=True`: chamada da prévia do painel (screencap, releitura da hierarquia) — só leitura, e não da
        etapa. Divide a fila como qualquer outra; a diferença é que `drain()` não espera por ela."""
        fut: Future[T] = self._pool.submit(fn, *args)
        with self._lock:
            self._pending.add(fut)
            if previa:
                self._da_previa.add(fut)
        fut.add_done_callback(self._forget)
        try:
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(fut)), timeout)
        except asyncio.TimeoutError as exc:
            if not fut.cancel():  # já estava rodando: segue ocupando o aparelho
                with self._lock:
                    self._zombies.add(fut)
            raise DriverTimeout(f"{label or getattr(fn, '__name__', 'chamada')} excedeu {timeout:.0f}s") from exc

    def _forget(self, fut: Future[Any]) -> None:
        with self._lock:
            self._pending.discard(fut)
            self._zombies.discard(fut)
            self._da_previa.discard(fut)

    async def drain(self, poll_s: float = 0.5, max_wait_s: float | None = None) -> bool:
        """Aguarda terminar toda chamada pendente/zumbi DA ETAPA. True se nenhuma delas segue no aparelho.

        As da prévia ficam de fora (`run(previa=True)`): são leitura, não carregam efeito que a etapa precise ver
        terminar, e a prévia pode seguir enfileirando enquanto se espera — esperá-las era esperar até o teto. Uma
        delas ainda na thread atrasa a próxima chamada, mas não a decisão de liberar; e o zumbi dela continua
        contando em `has_zombie`, que é a porta de `ai_begin`."""
        waited = 0.0
        while True:
            with self._lock:
                busy = any(not f.done() for f in self._pending | self._zombies if f not in self._da_previa)
            if not busy:
                return True
            if max_wait_s is not None and waited >= max_wait_s:
                return False
            await asyncio.sleep(poll_s)
            waited += poll_s

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
