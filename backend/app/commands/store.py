"""Persistência dos comandos. Toda mudança de estado passa por aqui, e só por transição válida."""
from __future__ import annotations

import logging
from typing import Any

from ..db import Database, INTEGRITY_ERRORS, Row, dumps, loads
from ..models import CommandDTO, CommandState
from ..util import now_iso, truncate
from .states import COMMAND_OPEN, COMMAND_UNSETTLED, check_transition

log = logging.getLogger("poc.commands")

#: Primeira chave da trava consultiva da cerca no PostgreSQL (forma de DUAS chaves int4, cujo espaço não se
#: sobrepõe ao da forma de uma chave bigint que a migração usa em `db._LOCK_MIGRACAO`). A segunda é o
#: `hashtext` do aparelho: colisão entre dois aparelhos só serializa os dois por milissegundos.
_TRAVA_DA_CERCA = 728_193_005


class CommandStore:
    def __init__(self, db: Database, *, owner_id: str | None = None, outbox: Any = None):
        self.db = db
        #: Quem sou eu para a reconciliação de partida. `None` = não filtra (uso em teste e em ferramentas).
        self.owner_id = owner_id
        #: Fila durável de entregas devidas (item 5.6). `None` = sem outbox, e a reconciliação volta a ser a de
        #: antes: todo comando em voo ganha um desfecho, porque não há como saber que ele ainda seria entregue.
        self.outbox = outbox
        #: Comandos que ESTE processo está executando agora (`api._do_action`). A entrega é ao menos uma vez: uma
        #: reentrega (JetStream, dreno do outbox) que chegue com a primeira ainda viva é ignorada aqui, em vez de
        #: despachar o mesmo comando duas vezes ou fechar como `cancelled` um comando que está rodando.
        self.em_execucao: set[str] = set()

    # ------------------------------------------------------------------ leitura para a escada de reparo
    def remediacoes_recentes(self, instance_id: str, *, janela_h: float = 24.0) -> list[Row]:
        """Os pedidos AUTOMÁTICOS (`requested_by='system'`) deste aparelho na janela, do mais antigo ao mais novo.

        É a memória da escada de reparo. Contar em memória (como era) parava para sempre depois de 2 e esquecia
        tudo num reinício do backend — e o backend reinicia justamente quando o parque está mal."""
        from datetime import timedelta  # noqa: PLC0415
        from ..util import now, to_iso  # noqa: PLC0415
        desde = to_iso(now() - timedelta(hours=janela_h))
        return self.db.query(
            "SELECT id, verb, state, reason, created_at, finished_at FROM commands WHERE instance_id=? AND"
            " requested_by='system' AND state != 'rejected' AND created_at >= ? ORDER BY created_at ASC",
            (instance_id, desde))                      # pedido recusado no pré-voo não fez nada: não é degrau

    # ------------------------------------------------------------------ escrita
    def _travar_cerca(self, instance_id: str) -> None:
        """Serializa, por aparelho, quem lê `MAX(fence)` e grava a cerca seguinte. Só vale DENTRO de `db.tx()`.

        Sem isto, duas transações liam o mesmo `MAX` e gravavam a MESMA cerca em dois comandos — e cerca repetida
        é autoridade dupla: o agente não tem como saber qual dos dois é o atual. Não há `UNIQUE(instance_id,
        fence)` de propósito: banco antigo pode ter a duplicata, e a migração quebraria no deploy.

        * SQLite: nada a fazer aqui. `tx()` abre com `BEGIN IMMEDIATE` (trava de escrita do arquivo, entre
          processos) e segura o `RLock` da conexão (entre threads deste processo).
        * PostgreSQL: `READ COMMITTED` deixa duas transações lerem o mesmo `MAX`. A trava consultiva de
          transação (`pg_advisory_xact_lock`) é solta sozinha no COMMIT/ROLLBACK e vale mesmo quando o aparelho
          ainda não tem linha em `instances` — o que um `SELECT ... FOR UPDATE` não cobriria.
        """
        if self.db.dialect == "postgres":
            self.db.execute("SELECT pg_advisory_xact_lock(CAST(? AS INTEGER), hashtext(CAST(? AS TEXT)))",
                            (_TRAVA_DA_CERCA, instance_id))

    def create(self, *, command_id: str, instance_id: str, verb: str, idempotency_key: str,
               params: dict[str, Any] | None = None, requested_by: str = "panel",
               host_worker_id: str | None = None) -> tuple[Row, bool]:
        """Grava o comando. Chave repetida devolve o comando ORIGINAL e `True` — nunca um efeito novo.

        É o que torna seguro o cliente reenviar quando não sabe se a primeira requisição chegou.

        A cerca é `MAX(fence) + 1` do aparelho, lida DENTRO da transação e sob `_travar_cerca`. Lida fora (como
        era), dois pedidos simultâneos para o mesmo aparelho — duas réplicas, ou duas threads — saíam com a
        mesma cerca.
        """
        try:
            with self.db.tx():
                self._travar_cerca(instance_id)
                fence = int(self.db.scalar(
                    "SELECT COALESCE(MAX(fence), 0) + 1 FROM commands WHERE instance_id=?", (instance_id,)) or 1)
                self.db.execute(
                    "INSERT INTO commands(id, instance_id, verb, params, idempotency_key, state, fence,"
                    " requested_by, created_at, host_worker_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (command_id, instance_id, verb, dumps(params) if params else None, idempotency_key,
                     CommandState.created.value, fence, requested_by, now_iso(), host_worker_id))
        except INTEGRITY_ERRORS:
            row = self.db.one("SELECT * FROM commands WHERE idempotency_key=?", (idempotency_key,))
            assert row is not None
            return row, True
        row = self.db.one("SELECT * FROM commands WHERE id=?", (command_id,))
        assert row is not None
        return row, False

    def elevar_cerca(self, command_id: str, piso: int) -> int:
        """Sobe a cerca de um comando ainda NÃO despachado para acima de `piso`, e devolve a cerca que vale.

        `piso` é a maior cerca que o worker já executou naquele aparelho. Depois de restaurar um banco antigo,
        `MAX(fence) + 1` volta para trás e o agente recusaria o despacho como ordem vencida (K-004). Mexer na
        cerca só é seguro em `created`: ela ainda não saiu do central, então ninguém a viu. A nova é maior que o
        piso E que qualquer cerca do aparelho no banco, para a ordem entre os comandos daqui continuar valendo.
        """
        with self.db.tx():
            row = self.db.one("SELECT instance_id, fence, state FROM commands WHERE id=?", (command_id,))
            if row is None:
                raise KeyError(f"comando desconhecido: {command_id}")
            atual = int(row["fence"])
            if atual > piso or row["state"] != CommandState.created.value:
                return atual
            # A mesma trava de `create`: sem ela, no PostgreSQL, um `create` simultâneo lia o mesmo `MAX` e as
            # duas cercas empatavam.
            self._travar_cerca(row["instance_id"])
            maior = int(self.db.scalar("SELECT COALESCE(MAX(fence), 0) FROM commands WHERE instance_id=?",
                                       (row["instance_id"],)) or 0)
            nova = max(piso, maior) + 1
            self.db.execute("UPDATE commands SET fence=? WHERE id=?", (nova, command_id))
        log.warning("cerca do comando %s (%s) subiu de %d para %d: o worker já executou a cerca %d neste aparelho "
                    "(banco restaurado?)", command_id, row["instance_id"], atual, nova, piso)
        return nova

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

    def open_commands(self, *, hospedados_por: str | None = None) -> list[Row]:
        """Comandos ainda em voo. Com `hospedados_por`, só os de aparelhos que AQUELE backend hospeda.

        O filtro existe para a reconciliação de partida (item 5.1, achado #26): sem ele, o segundo backend a
        subir marcava como `uncertain` os comandos que o primeiro estava executando naquele instante. Aparelho
        sem dono registrado (banco anterior à migração 027) continua sendo de quem perguntar — é o comportamento
        de antes, e com um backend só nada muda.
        """
        marcadores = ",".join("?" for _ in COMMAND_OPEN)
        q = f"SELECT c.* FROM commands c WHERE c.state IN ({marcadores})"      # noqa: S608 - marcadores, não dados
        params: list[Any] = [s.value for s in COMMAND_OPEN]
        if hospedados_por is not None:
            q += (" AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id=c.instance_id"
                  " AND i.hosted_by IS NOT NULL AND i.hosted_by<>?)")
            params.append(hospedados_por)
        return self.db.query(q + " ORDER BY c.created_at", tuple(params))

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

        **O que tem entrega pendente no outbox fica de fora** (item 5.6). Linha `pending` significa que a ordem
        nunca saiu: o comando não está sem desfecho, está na fila. Dar-lhe um desfecho aqui seria apagar a fila
        no boot — e era exatamente isso que acontecia antes de o outbox existir, com `created` virando `failed`
        e `dispatched` virando `uncertain` para comandos que ninguém chegou a enviar. Quem os executa é o dreno
        (`AppState._drenar_outbox`), logo depois desta reconciliação.
        """
        mudados = []
        na_fila = self.outbox.pending_ids() if self.outbox is not None else set()
        for row in self.open_commands(hospedados_por=self.owner_id):
            if row["id"] in na_fila:
                continue
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
        id=row["id"], instance_id=row["instance_id"], worker_id=row["worker_id"],
        host_worker_id=row["host_worker_id"] if "host_worker_id" in row.keys() else None, verb=row["verb"],
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
