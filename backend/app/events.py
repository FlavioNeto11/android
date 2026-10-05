"""Barramento de eventos: persiste (quando aplicável) e transmite aos WebSockets conectados.

Eventos persistidos recebem um id monotônico; o cliente reconecta informando o último id visto e
recebe apenas o que perdeu. Eventos efêmeros (frame, metrics) não são gravados.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .db import Database, dumps, loads
from .security.redaction import redact, redact_obj
from .models import EventRecord
from .util import now_iso

log = logging.getLogger("poc.events")

#: `worker.metrics` (achados #17/#143): a batida do worker chega a cada 10 s só para atualizar CPU/RAM/disco
#: na tela; isso não é fato que precise sobreviver a uma reconexão — o snapshot seguinte já traz o valor atual.
#: Persistir isto enchia o log (57% dos eventos eram batida) e empurrava para fora da janela de replay o que
#: de fato importa (comando, transição de estado). `worker.updated` continua persistido, mas só quando algo
#: OBSERVÁVEL muda (estado, detalhe, inventário de aparelhos) — ver `workers/registry.py::on_heartbeat`.
EPHEMERAL_KINDS = {"frame", "metrics", "worker.metrics", "health.updated", "apps.updated", "settings.updated",
                   # Loja de apps: cada transição do proxy de cada aparelho. A verdade fica em `device_proxy_state`;
                   # persistir encheria o log com três eventos por aparelho a cada aplicação.
                   "proxy.updated",
                   # 14.13: o DTO do aparelho quando só mudou telemetria ou o controle já anunciado por
                   # `control.changed` (`devices/publicacao.py`). O fato segue em `instance.updated`.
                   "instance.progress"}

#: RA-11 (reavaliação de 03/10): a telemetria do parque mora no mesmo balde da auditoria. `instance.updated` leva o DTO
#: inteiro do aparelho a cada mudança (32 mil linhas e 48 MiB em 14 dias, 03/10) e empurrava para fora da janela de
#: replay do painel o que importa. Ela vence em `TELEMETRIA_RETENCAO_H`, e o resto do log segue em
#: `log_retention_days`. Só sem execução (`run_id` nulo, que é o caso de todas, medido em 03/10): a linha do tempo de uma
#: execução não perde nada.
TELEMETRIA_KINDS = ("instance.updated",)
TELEMETRIA_RETENCAO_H = 48
#: Linhas por DELETE da purga. A de 02/10 levou 41 mil eventos num comando só, e no SQLite um DELETE desse tamanho segura
#: a escrita do banco inteiro enquanto roda; em lotes, cada comando é curto e os outros escritores passam entre eles.
PURGA_LOTE = 2000


#: De quanto em quanto tempo uma réplica olha o banco atrás do que as OUTRAS publicaram.
REPLICA_POLL_S = 1.0


class EventBus:
    def __init__(self, db: Database, *, origin: str | None = None):
        self.db = db
        #: Quem sou eu. Vai gravado em cada evento e é o que permite a outra réplica saber o que NÃO é dela.
        self.origin = origin
        self._subscribers: set[asyncio.Queue[EventRecord]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._replica_cursor: int | None = None

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
        # Rede de segurança: evento é persistido E transmitido a todo navegador conectado. A defesa principal é
        # não deixar o segredo chegar aqui; esta é a segunda camada.
        message = redact(message) or ""
        data = redact_obj(data)
        ts = now_iso()
        event_id: int | None = None
        if kind not in EPHEMERAL_KINDS:
            event_id = self.db.inserted_id(
                "INSERT INTO events(ts, kind, level, run_id, instance_id, objective_id, step_id, attempt_id,"
                " message, data, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, kind, level, run_id, instance_id, objective_id, step_id, attempt_id, message,
                 dumps(data) if data is not None else None, self.origin),
            )
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

    # -- réplicas --------------------------------------------------------------
    def replicar_agora(self, limit: int = 500) -> list[EventRecord]:
        """Entrega aos assinantes DESTE processo o que as OUTRAS réplicas publicaram desde a última olhada.

        O defeito que isto corrige (item 5.6): o `EventBus` transmite só para os WebSockets ligados ao próprio
        processo. Com dois backends no mesmo PostgreSQL, o painel ligado na réplica B não via NADA do que a
        réplica A fazia — um objetivo inteiro executava com a tela parada, e só uma reconexão (que pede o
        histórico por `since`) mostrava o que tinha acontecido.

        O filtro `origin <> eu` é o que evita a entrega dupla: o evento local já saiu pelo caminho direto do
        `emit`. Evento sem origem (anterior à migração 029) conta como alheio — numa máquina só o laço nem
        roda, e num banco misto é melhor mostrar de novo do que nunca.

        Devolve o que foi entregue, para o teste poder afirmar o que atravessou.
        """
        if self._replica_cursor is None:
            self._replica_cursor = self.last_id()
            return []
        rows = self.db.query(
            "SELECT * FROM events WHERE id > ? AND (origin IS NULL OR origin <> ?) ORDER BY id LIMIT ?",
            (self._replica_cursor, self.origin or "", limit))
        entregues = [row_to_event(r) for r in rows]
        for rec in entregues:
            # `_broadcast` decide se entrega direto ou salta para o laço: este método roda tanto no laço (teste,
            # chamada manual) quanto numa thread de executor (o laço abaixo). Sem laço ligado — barramento de
            # teste puro — a entrega é direta, que é o único caminho possível ali.
            self._deliver(rec) if self._loop is None else self._broadcast(rec)
        if rows:
            self._replica_cursor = int(rows[-1]["id"])
        else:
            # Sem nada alheio, o cursor ainda precisa andar: senão a mesma janela (que pode ser só de eventos
            # locais) é relida para sempre e o laço nunca alcança o presente.
            self._replica_cursor = max(self._replica_cursor, self.last_id())
        return entregues

    async def replicar_sempre(self, intervalo: float = REPLICA_POLL_S) -> None:
        """Laço do item 5.6. A leitura vai para uma thread: com PostgreSQL ela é ida e volta de rede, e o laço
        de eventos do backend não pode parar de servir o painel por causa dela."""
        while True:
            try:
                await asyncio.to_thread(self.replicar_agora)
            except Exception:  # noqa: BLE001 - a replicação nunca pode derrubar o backend
                log.exception("replicação de eventos entre réplicas")
            await asyncio.sleep(intervalo)

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

    def purge_older_than(self, iso_ts: str, *, kinds: tuple[str, ...] = (), so_sem_execucao: bool = False,
                         lote: int = PURGA_LOTE) -> int:
        """Apaga os eventos anteriores a `iso_ts`, menos os de execução ainda aberta, em lotes de `lote` linhas.

        `kinds` restringe a purga a esses tipos (a classe de telemetria do RA-11); `so_sem_execucao` poupa todo evento
        com `run_id`, aberto ou fechado. A execução em `awaiting_person` (29.93) tem `finished_at` (o fim do trabalho
        automático) e continua aberta, esperando a pessoa: a linha do tempo dela fica."""
        filtro = ("ts < ? AND (run_id IS NULL OR run_id NOT IN (SELECT id FROM runs WHERE finished_at IS NULL"
                  " OR status = 'awaiting_person'))")
        args: list[object] = [iso_ts]
        if kinds:
            filtro += f" AND kind IN ({','.join('?' for _ in kinds)})"
            args += list(kinds)
        if so_sem_execucao:
            filtro += " AND run_id IS NULL"
        total = 0
        while True:
            n = int(self.db.execute(f"DELETE FROM events WHERE id IN (SELECT id FROM events WHERE {filtro} ORDER BY id"
                                    " LIMIT ?)", (*args, lote)).rowcount or 0)
            total += n
            if n < lote:
                return total


def row_to_event(r: Any) -> EventRecord:
    return EventRecord(id=r["id"], ts=r["ts"], kind=r["kind"], level=r["level"], run_id=r["run_id"],
                       instance_id=r["instance_id"], objective_id=r["objective_id"], step_id=r["step_id"],
                       attempt_id=r["attempt_id"], message=r["message"], data=loads(r["data"]))
