"""Persistência dos comandos. Toda mudança de estado passa por aqui, e só por transição válida."""
from __future__ import annotations

import logging
from typing import Any

from ..db import Database, INTEGRITY_ERRORS, Row, dumps, loads
from ..models import CommandDTO, CommandState
from ..util import now_iso, truncate
from .states import COMMAND_OPEN, COMMAND_UNSETTLED, check_transition

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

    def unsettled(self, limit: int = 200) -> list[Row]:
        """Comandos que terminaram sem que se saiba o efeito. É a fila da reconciliação e a lista que o painel
        mostra como "sem desfecho": enquanto houver linha aqui, alguém (ou a sonda) ainda deve uma resposta."""
        marcadores = ",".join("?" for _ in COMMAND_UNSETTLED)
        return self.db.query(
            f"SELECT * FROM commands WHERE state IN ({marcadores}) ORDER BY created_at DESC LIMIT ?",
            (*(s.value for s in COMMAND_UNSETTLED), limit))

    def open_commands(self) -> list[Row]:
        marcadores = ",".join("?" for _ in COMMAND_OPEN)
        return self.db.query(
            f"SELECT * FROM commands WHERE state IN ({marcadores}) ORDER BY created_at",
            tuple(s.value for s in COMMAND_OPEN))

    def open_for_instance(self, instance_id: str, *, exclude: str | None = None,
                          verbs: set[str] | None = None) -> Row | None:
        """O comando ainda em voo NAQUELE aparelho, se houver. É a pergunta do pré-voo: um aparelho, uma operação.

        `exclude` existe porque o comando novo já está gravado (em `created`) quando o pré-voo roda — ele não pode
        recusar a si mesmo. `verbs` limita a pergunta aos verbos que disputam o aparelho de verdade: uma tecla
        (`home`, `back`) não impede a seguinte, e não é disso que o aceite 9 trata.
        """
        marcadores = ",".join("?" for _ in COMMAND_OPEN)
        params: list[Any] = [instance_id, *(s.value for s in COMMAND_OPEN)]
        extra = ""
        if exclude is not None:
            extra += " AND id<>?"
            params.append(exclude)
        if verbs:
            ordenados = sorted(verbs)
            extra += f" AND verb IN ({','.join('?' for _ in ordenados)})"
            params.extend(ordenados)
        return self.db.one(
            f"SELECT * FROM commands WHERE instance_id=? AND state IN ({marcadores}){extra}"
            " ORDER BY created_at LIMIT 1", tuple(params))

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
            elif estado is CommandState.dispatched and not row["acked_at"]:
                # Enviado e sem ACK. NÃO é "falhou com certeza": o envio ter tido sucesso significa que os bytes
                # saíram, não que chegaram — e o ACK pode ter se perdido na MESMA queda. Fica `uncertain`, mas o
                # motivo registra que o worker nunca confirmou o recebimento, que é o que separa este caso do
                # seguinte para quem for decidir se repete.
                alvo, motivo = (CommandState.uncertain,
                                "o backend reiniciou depois do envio e o worker nunca confirmou o recebimento; "
                                "resultado desconhecido")
            elif row["acked_at"]:
                alvo, motivo = (CommandState.uncertain,
                                "o backend reiniciou depois de o worker confirmar o recebimento; o comando pode "
                                "ter sido executado e o resultado desconhecido")
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
        reason=row["reason"], emulator_log=_log_do_emulador(row), attempt=row["attempt"],
        created_at=row["created_at"], dispatched_at=row["dispatched_at"], acked_at=row["acked_at"],
        started_at=row["started_at"], finished_at=row["finished_at"])


def _log_do_emulador(row: Row) -> str | None:
    """A cauda do log que o executor mandou junto com o desfecho, se mandou.

    Sobe para o DTO em vez de ficar no `result` cru porque é o que o operador precisa ver quando um boot falha —
    e, no aparelho de outra máquina, era a única coisa a que ele não tinha acesso nenhum.
    """
    corpo = loads(row["result"], {}) if row["result"] else None
    cauda = (corpo or {}).get("emulator_log") if isinstance(corpo, dict) else None
    return cauda if isinstance(cauda, str) and cauda.strip() else None


def publicar_comando(bus: Any, row: Row) -> Row:
    """Todo estado de comando vai para a interface. Sem isto o desfecho existiria só no banco.

    Fica aqui, e não em `api.py`, porque quem fecha um comando não é só o handler HTTP: a reconciliação por
    sonda e a decisão humana também mudam estado, e um caminho que mudasse o banco sem publicar o evento faria
    o painel continuar mostrando "incerto" sobre um comando já resolvido.
    """
    dto = command_dto(row)
    nivel = {CommandState.succeeded: "info", CommandState.uncertain: "warn"}.get(dto.state, "info")
    if dto.state in (CommandState.failed, CommandState.rejected):
        nivel = "error"
    bus.emit("command.updated", f"{dto.instance_id}: {dto.verb} — {dto.state.value}"
             + (f" ({dto.reason})" if dto.reason else ""),
             level=nivel, instance_id=dto.instance_id, data={"command": dto.model_dump()})
    return row
