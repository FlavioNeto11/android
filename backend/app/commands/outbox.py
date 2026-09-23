"""Outbox de comandos: a entrega DEVIDA, gravada na mesma transação que aceita o comando.

O defeito que isto corrige (achado #30): o "transporte" era `asyncio.create_task(_do_action(...))` logo depois
de gravar o estado do comando. Entre gravar e agendar não há durabilidade nenhuma — o processo que cai ali
deixa um comando que o banco diz ter sido aceito e que ninguém nunca enviou. No boot seguinte a reconciliação
o chamava de `uncertain`, que é falso nos dois sentidos: nada foi executado, e `uncertain` é justamente o
estado que ninguém repete sozinho.

Com a linha `pending` gravada na MESMA transação em que o comando é aceito, a promessa vira verificável:

    existe linha pendente  ⇔  a entrega é devida  ⇔  quem subir de novo a executa.

**Ao menos uma vez, nunca exatamente uma vez.** Entre publicar a ordem e marcar a linha como `sent` existe uma
janela; um processo que caia nela publica de novo no boot seguinte. Quem impede o efeito duplo não é esta
tabela, é o diário do agente (`worker/diario.py`): `command_id` já executado tem o desfecho guardado
devolvido em vez do verbo reexecutado. É por isso que o outbox é pré-requisito do transporte entre réplicas e
não um substituto do diário.
"""
from __future__ import annotations

import logging
from typing import Any

from ..db import Database, INTEGRITY_ERRORS, Row, dumps, loads
from ..util import now_iso

log = logging.getLogger("poc.commands")

PENDING = "pending"
SENT = "sent"


class CommandOutbox:
    """Fila durável de entregas devidas. Só sabe de linhas — quem publica é o transporte."""

    def __init__(self, db: Database, *, owner_id: str | None = None, transport: str = "websocket"):
        self.db = db
        #: Quem sou eu para o dreno de partida. `None` = não filtra (uso em teste e em ferramentas).
        self.owner_id = owner_id
        self.transport = transport
        #: Ordens que ESTE processo está publicando agora. Não é durabilidade — é só evitar que o laço de
        #: repetição publique de novo o que o despacho do painel ainda não terminou de publicar.
        self.publicando: set[str] = set()

    # ------------------------------------------------------------------ escrita
    def enqueue(self, *, command_id: str, instance_id: str, verb: str, worker_id: str | None,
                payload: dict[str, Any], transport: str | None = None) -> None:
        """Grava a entrega devida. Chamar DENTRO da transação que aceita o comando (`db.tx()` é reentrante).

        Chave repetida é silenciosa de propósito: a mesma ordem aceita duas vezes (reenvio com a mesma chave de
        idempotência) devolve o comando original e não deve criar uma segunda entrega.
        """
        try:
            self.db.execute(
                "INSERT INTO command_outbox(command_id, instance_id, worker_id, verb, payload, state, transport,"
                " attempts, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (command_id, instance_id, worker_id, verb, dumps(payload), PENDING,
                 transport or self.transport, 0, now_iso()))
        except INTEGRITY_ERRORS:
            log.debug("entrega de %s já estava no outbox", command_id)

    def get(self, command_id: str) -> Row | None:
        return self.db.one("SELECT * FROM command_outbox WHERE command_id=?", (command_id,))

    def mark_sent(self, command_id: str) -> bool:
        """A ordem SAIU: `pending` → `sent`, contando a tentativa. Devolve `False` se já não estava pendente.

        Chamada DEPOIS de publicar, e não antes, de propósito. Marcar antes tornaria a queda entre marcar e
        publicar uma perda silenciosa — o comando ficaria `sent` sem nunca ter saído, e é essa perda que o
        outbox existe para não ter. Marcando depois, a mesma queda deixa a linha `pending` e o dreno de partida
        publica de novo: ao menos uma vez, que é o contrato desta fila.

        Uma reentrega não repete o efeito. No central, a máquina de estados recusa (`_do_action` só transita de
        `dispatched`, `_do_action_no_worker` só de `created`, e um comando já fechado não está em nenhum dos
        dois); no agente, o diário devolve o desfecho guardado do `command_id` já visto.

        `commands.attempt` sobe junto: a coluna existe desde a migração 013 e nunca era incrementada, então
        "quantas vezes esta ordem saiu" não tinha resposta no banco.
        """
        with self.db.tx():
            cur = self.db.execute(
                "UPDATE command_outbox SET state=?, sent_at=?, attempts=attempts+1 WHERE command_id=? AND state=?",
                (SENT, now_iso(), command_id, PENDING))
            if cur.rowcount != 1:
                return False
            self.db.execute("UPDATE commands SET attempt=attempt+1 WHERE id=?", (command_id,))
        return True

    def discard(self, command_id: str) -> bool:
        """A entrega deixou de ser devida (recusa no pré-voo, cancelamento antes do envio). Sem linha pendente,
        o dreno de partida não ressuscita o que já tem desfecho."""
        cur = self.db.execute("DELETE FROM command_outbox WHERE command_id=? AND state=?", (command_id, PENDING))
        return bool(cur.rowcount)

    def purge_settled(self, cutoff_iso: str) -> int:
        """Linhas entregues de comandos já fechados não são histórico — o histórico é `commands`."""
        cur = self.db.execute(
            "DELETE FROM command_outbox WHERE state=? AND sent_at < ? AND command_id IN"
            " (SELECT id FROM commands WHERE finished_at IS NOT NULL)", (SENT, cutoff_iso))
        return cur.rowcount

    # ------------------------------------------------------------------ leitura
    def is_pending(self, command_id: str) -> bool:
        return bool(self.db.scalar("SELECT 1 FROM command_outbox WHERE command_id=? AND state=?",
                                   (command_id, PENDING)))

    def pending_ids(self) -> set[str]:
        return {r["command_id"] for r in self.db.query("SELECT command_id FROM command_outbox WHERE state=?",
                                                       (PENDING,))}

    def pending(self, *, hospedados_por: str | None = None, limit: int = 500) -> list[Row]:
        """As entregas devidas, mais antiga primeiro.

        `hospedados_por` filtra pelo aparelho que AQUELE backend hospeda, exatamente como
        `CommandStore.open_commands`: sem isso, o segundo backend a subir drenaria o outbox do primeiro e
        mandaria executar, na máquina errada, ordens de aparelhos que não são dele. Aparelho sem dono registrado
        continua sendo de quem perguntar — é o comportamento de antes, e com um backend só nada muda.
        """
        q = "SELECT o.* FROM command_outbox o WHERE o.state=?"    # noqa: S608 - marcadores, não dados
        params: list[Any] = [PENDING]
        if hospedados_por is not None:
            q += (" AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id=o.instance_id"
                  " AND i.hosted_by IS NOT NULL AND i.hosted_by<>?)")
            params.append(hospedados_por)
        q += " ORDER BY o.created_at, o.command_id LIMIT ?"
        params.append(limit)
        return self.db.query(q, tuple(params))

    @staticmethod
    def payload_of(row: Row) -> dict[str, Any]:
        return loads(row["payload"], {}) or {}
