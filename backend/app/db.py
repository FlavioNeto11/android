"""SQLite em modo WAL, uma conexão serializada por lock e transações curtas.

Um único processo de backend é dono do banco (ver README). As operações são rápidas (ms), por isso
são chamadas diretamente; o lock permite uso seguro também a partir das threads dos executores.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import now_iso

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._tx_depth = 0

    # -- infraestrutura -------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transação curta e atômica (BEGIN IMMEDIATE). Reentrante."""
        with self._lock:
            outer = self._tx_depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield self._conn
                if outer:
                    self._conn.execute("COMMIT")
            except BaseException:
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            finally:
                self._tx_depth -= 1

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.one(sql, params)
        return row[0] if row else None

    # -- migrações -------------------------------------------------------------
    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            done = {r[0] for r in self._conn.execute("SELECT version FROM schema_migrations")}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.stem in done:
                    continue
                script = f.read_text(encoding="utf-8")
                version = f.stem.replace("'", "")
                try:
                    # executescript entende comentários e múltiplas instruções; a migração é atômica.
                    self._conn.executescript(
                        "BEGIN IMMEDIATE;\n" + script + "\nINSERT INTO schema_migrations(version, applied_at) "
                        f"VALUES ('{version}', '{now_iso()}');\nCOMMIT;"
                    )
                except BaseException:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise
                applied.append(f.stem)
        return applied


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
