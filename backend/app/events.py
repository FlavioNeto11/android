"""Barramento de eventos: persiste (quando aplicável) e transmite aos WebSockets conectados.

Eventos persistidos recebem um id monotônico; o cliente reconecta informando o último id visto e
recebe apenas o que perdeu. Eventos efêmeros (frame, metrics) não são gravados.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .db import Database, dumps, loads
from .models import EventRecord
from .util import now_iso

log = logging.getLogger("poc.events")

EPHEMERAL_KINDS = {"frame", "metrics", "health.updated", "apps.updated", "settings.updated"}


class EventBus:
    def __init__(self, db: Database):
        self.db = db
        self._subscribers: set[asyncio.Queue[EventRecord]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- publicação -----------------------------------------------------------
    def emit(
        self,
        kind: str,
        message: str,
        *,
        level: str = "info",
        run_id: str | None = None,
        instance_id: str | None = None,
        objective_id: str | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> EventRecord:
        ts = now_iso()
        event_id: int | None = None
        if kind not in EPHEMERAL_KINDS:
            cur = self.db.execute(
                "INSERT INTO events(ts, kind, level, run_id, instance_id, objective_id, step_id, attempt_id, message, data)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, kind, level, run_id, instance_id, objective_id, step_id, attempt_id, message,
                 dumps(data) if data is not None else None),
            )
            event_id = cur.lastrowid
        rec = EventRecord(id=event_id, ts=ts, kind=kind, level=level, run_id=run_id, instance_id=instance_id,  # type: ignore[arg-type]
                          objective_id=objective_id, step_id=step_id, attempt_id=attempt_id, message=message, data=data)
        self._broadcast(rec)
        if level != "info":
            log.log(logging.WARNING if level == "warn" else logging.ERROR, "%s | %s", kind, message)
        return rec

    def _broadcast(self, rec: EventRecord) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(rec)
        else:  # chamado de uma thread de executor
            loop.call_soon_threadsafe(self._deliver, rec)

    def _deliver(self, rec: EventRecord) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                # consumidor lento: efêmeros são descartados; se perder um persistido, a assinatura é
                # removida e o handler do WebSocket (que consulta is_subscribed) pede resync ao cliente.
                if rec.id is not None:
                    self._subscribers.discard(q)

    # -- assinatura -----------------------------------------------------------
    def subscribe(self) -> asyncio.Queue[EventRecord]:
        q: asyncio.Queue[EventRecord] = asyncio.Queue(maxsize=2000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[EventRecord]) -> None:
        self._subscribers.discard(q)

    def is_subscribed(self, q: asyncio.Queue[EventRecord]) -> bool:
        return q in self._subscribers

    # -- leitura ---------------------------------------------------------------
    def last_id(self) -> int:
        return int(self.db.scalar("SELECT COALESCE(MAX(id), 0) FROM events") or 0)

    def since(self, after_id: int, *, run_id: str | None = None, limit: int = 5000) -> list[EventRecord]:
        if run_id:
            rows = self.db.query("SELECT * FROM events WHERE id > ? AND run_id = ? ORDER BY id LIMIT ?",
                                 (after_id, run_id, limit))
        else:
            rows = self.db.query("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))
        return [row_to_event(r) for r in rows]

    def count_since(self, after_id: int) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM events WHERE id > ?", (after_id,)) or 0)

    def purge_older_than(self, iso_ts: str) -> int:
        cur = self.db.execute(
            "DELETE FROM events WHERE ts < ? AND (run_id IS NULL OR run_id NOT IN "
            "(SELECT id FROM runs WHERE finished_at IS NULL))", (iso_ts,))
        return cur.rowcount


def row_to_event(r: Any) -> EventRecord:
    return EventRecord(id=r["id"], ts=r["ts"], kind=r["kind"], level=r["level"], run_id=r["run_id"],
                       instance_id=r["instance_id"], objective_id=r["objective_id"], step_id=r["step_id"],
                       attempt_id=r["attempt_id"], message=r["message"], data=loads(r["data"]))
