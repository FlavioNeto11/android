"""Executor exclusivo por dispositivo.

Uma única thread por aparelho: ações da IA, entradas manuais e capturas de tela passam todas por
aqui, então nunca há duas chamadas concorrentes no mesmo aparelho. Um timeout NÃO libera o
aparelho: a chamada anterior continua ocupando a thread ("zumbi") e `drain()` só retorna quando
ela realmente terminar.
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

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def has_zombie(self) -> bool:
        with self._lock:
            self._zombies = {f for f in self._zombies if not f.done()}
            return bool(self._zombies)

    async def run(self, fn: Callable[..., T], *args: Any, timeout: float, label: str = "") -> T:
        fut: Future[T] = self._pool.submit(fn, *args)
        with self._lock:
            self._pending.add(fut)
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

    async def drain(self, poll_s: float = 0.5, max_wait_s: float | None = None) -> bool:
        """Aguarda terminar toda chamada pendente/zumbi. True se o aparelho ficou livre."""
        waited = 0.0
        while True:
            with self._lock:
                busy = any(not f.done() for f in self._pending | self._zombies)
            if not busy:
                return True
            if max_wait_s is not None and waited >= max_wait_s:
                return False
            await asyncio.sleep(poll_s)
            waited += poll_s

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
