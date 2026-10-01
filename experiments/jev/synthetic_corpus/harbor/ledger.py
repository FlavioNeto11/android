"""Append-only ledger of job changes, observed by the dashboard."""
from typing import Callable

Listener = Callable[[str, dict], None]


class Ledger:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self.entries: list[tuple[str, dict]] = []

    def attach_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def announce(self, kind: str, payload: dict) -> None:
        """The only place that emits ledger events; every change goes through here."""
        self.entries.append((kind, payload))
        for listener in self._listeners:
            listener(kind, payload)
