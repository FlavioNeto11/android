"""Persistência dos comandos. Toda mudança de estado passa por aqui, e só por transição válida."""
from __future__ import annotations

import logging
from typing import Any

from ..db import Database, INTEGRITY_ERRORS, Row, dumps
from ..models import CommandDTO, CommandState
from ..util import now_iso, truncate
from .states import COMMAND_OPEN, check_transition

log = logging.getLogger("poc.commands")


class CommandStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ escrita
    def create(self, *, command_id: str, instance_id: str, verb: str, idempotency_key: str,
               params: dict[str, Any] | None = None, requested_by: str = "panel") -> tuple[Row, bool]:
        """Grava o comando. Chave repetida devolve o comando ORIGINAL e `True` — nunca um efeito novo.

        É o que torna seguro o cliente reenviar quando não sabe se a primeira requisição chegou.
        """
        fence = int(self.db.scalar(
            "SELECT COALESCE(MAX(fence), 0) + 1 FROM commands WHERE instance_id=?", (instance_id,)) or 1)
        try:
            with self.db.tx():
                self.db.execute(
                    "INSERT INTO commands(id, instance_id, verb, params, idempotency_key, state, fence,"
                    " requested_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (command_id, instance_id, verb, dumps(params) if params else None, idempotency_key,
                     CommandState.created.value, fence, requested_by, now_iso()))
        except INTEGRITY_ERRORS:
            row = self.db.one("SELECT * FROM commands WHERE idempotency_key=?", (idempotency_key,))
            assert row is not None
            return row, True
        row = self.db.one("SELECT * FROM commands WHERE id=?", (command_id,))
        assert row is not None
        return row, False

    def transition(self, command_id: str, target: CommandState, *, reason: str | None = None,
                   result: dict[str, Any] | None = None, worker_id: str | None = None) -> Row:
        """Muda o estado conferindo a tabela de transições, e estampa a hora do marco correspondente."""
        marcas = {
            CommandState.dispatched: "dispatched_at",
            CommandState.acked: "acked_at",
            CommandState.running: "started_at",
        }
        with self.db.tx():
            row = self.db.one("SELECT * FROM commands WHERE id=?", (command_id,))
            if row is None:
                raise KeyError(f"comando desconhecido: {command_id}")
            check_transition(row["state"], target)
            campos = ["state=?"]
            valores: list[Any] = [target.value]
            if (marca := marcas.get(target)) is not None:
                campos.append(f"{marca}=?")
                valores.append(now_iso())
            # Terminal e `uncertain` fecham o relógio: mesmo sem saber o efeito, o trabalho automático acabou.
            if target not in COMMAND_OPEN:
                campos.append("finished_at=?")
                valores.append(now_iso())
            if reason is not None:
                campos.append("reason=?")
                valores.append(truncate(reason, 400))
            if result is not None:
                campos.append("result=?")
                valores.append(dumps(result))
            if worker_id is not None:
                campos.append("worker_id=?")
                valores.append(worker_id)
            valores.append(command_id)
            self.db.execute(f"UPDATE commands SET {', '.join(campos)} WHERE id=?", tuple(valores))
            novo = self.db.one("SELECT * FROM commands WHERE id=?", (command_id,))
        assert novo is not None
        return novo

    # ------------------------------------------------------------------ leitura
    def get(self, command_id: str) -> Row | None:
        return self.db.one("SELECT * FROM commands WHERE id=?", (command_id,))

    def recent(self, instance_id: str | None = None, limit: int = 50) -> list[Row]:
        if instance_id:
            return self.db.query("SELECT * FROM commands WHERE instance_id=? ORDER BY created_at DESC LIMIT ?",
                                 (instance_id, limit))
        return self.db.query("SELECT * FROM commands ORDER BY created_at DESC LIMIT ?", (limit,))

    def open_commands(self) -> list[Row]:
        marcadores = ",".join("?" for _ in COMMAND_OPEN)
        return self.db.query(
            f"SELECT * FROM commands WHERE state IN ({marcadores}) ORDER BY created_at",
            tuple(s.value for s in COMMAND_OPEN))

    # ------------------------------------------------------------------ reconciliação
    def reconcile_after_restart(self) -> list[Row]:
        """No boot, todo comando em voo recebe um desfecho honesto.

        `created` nunca foi despachado: nada aconteceu, então `failed` e ponto. Os demais podem ter agido sem que
        soubéssemos o resultado — vão para `uncertain`, que ninguém repete sozinho. É o mesmo princípio que
        `scheduler.reconcile_after_restart` já aplica às ações da IA (`intended` → `unknown`).
        """
        mudados = []
        for row in self.open_commands():
            estado = CommandState(row["state"])
            if estado is CommandState.created:
                alvo, motivo = CommandState.failed, "o backend reiniciou antes de despachar; nada foi executado"
            else:
                alvo, motivo = CommandState.uncertain, "o backend reiniciou durante o comando; resultado desconhecido"
            try:
                mudados.append(self.transition(row["id"], alvo, reason=motivo))
            except Exception:  # noqa: BLE001 - reconciliação nunca impede o boot
                log.exception("reconciliação do comando %s", row["id"])
        return mudados


def command_dto(row: Row) -> CommandDTO:
    return CommandDTO(
        id=row["id"], instance_id=row["instance_id"], worker_id=row["worker_id"], verb=row["verb"],
        state=CommandState(row["state"]), fence=row["fence"], requested_by=row["requested_by"],
        reason=row["reason"], attempt=row["attempt"], created_at=row["created_at"],
        dispatched_at=row["dispatched_at"], acked_at=row["acked_at"], started_at=row["started_at"],
        finished_at=row["finished_at"])
