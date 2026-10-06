"""Comando remoto, lado do central (`remote_exec`, item 29.154, ADR-079): a fila, o registro e a auditoria.

O que o desenho (`.claude/handoffs/android/desenho-29-154.md`, §§ 2, 4, 5 e 10) fixa e que mora aqui:

- **Quem manda é uma sessão nomeada.** A rota entrega o `operador` já conferido; este módulo nunca aceita nome vindo do
  corpo do pedido (`requested_by` = sessão).
- **Desligado de fábrica nos três lugares:** `comando_remoto.ativo` no config do central, o interruptor POR WORKER no
  painel e `comando_remoto: true` no `worker.yaml` do agente. O central também fica de fora (`LocalWorker`).
- **Auditoria é pré-condição.** O evento `worker.comando` sai ANTES de o comando ser despachado; se o evento falha, o
  comando não corre.
- **A linha crua só existe em memória**, entre o pedido e o envio: no banco e no evento só a versão redigida. Linha com
  cara de credencial é recusada na hora e fica só como linha de auditoria, sem o texto.
- **`uncertain` nunca é repetido.** Canal ou agente que cai no meio de um comando deixa o registro `uncertain`; o
  reinício do central faz o mesmo com o que ficou em voo.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, TypedDict, cast

from pydantic import ValidationError

from ..contracts.worker.protocol import (EXEC_SAIDA_MAX_BYTES, FEATURE_COMANDO_REMOTO, Exec, ExecAck, ExecCancel,
                                         ExecResult, ExecResultAck)
from ..security.redaction import cortar_saida, linha_de_comando_suspeita, redact
from ..util import now_iso

if TYPE_CHECKING:
    from ..db import Database
    from .registry import WorkerLink, WorkerRegistry

log = logging.getLogger("poc.comando_remoto")

#: Folga depois do prazo do comando para o desfecho chegar antes de o central dar o comando por `uncertain`.
GRACA_DO_DESFECHO_S = 30.0
ESTADOS_ABERTOS = ("created", "dispatched", "running")
ESTADOS_FINAIS = ("succeeded", "failed", "timed_out", "cancelled", "uncertain", "rejected")
LINHA_NO_EVENTO_MAX = 500
MARCA_LINHA_RECUSADA = "[linha recusada: parece levar credencial; o texto não foi guardado]"


class Registro(TypedDict):
    """Uma linha de `worker_comandos` (migração 123)."""

    id: str
    worker_id: str
    requested_by: str
    idempotency_key: str
    modo: str
    linha_redigida: str
    pasta: str | None
    timeout_s: float
    state: str
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    truncated: int
    duration_ms: int | None
    reason: str | None
    created_at: str
    dispatched_at: str | None
    finished_at: str | None


class ErroDeComando(Exception):
    """Recusa com código estável (`code`) e o status HTTP que a rota deve usar."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class LimitesDoComandoRemoto:
    """Os números do config (`comando_remoto:` em `config.yaml`); preenchido por `AppState` na subida."""

    def __init__(self, ativo: bool = False, fila_max: int = 4, por_minuto_por_operador: int = 30,
                 retencao_dias: int = 30, max_por_worker: int = 200) -> None:
        self.ativo = ativo
        self.fila_max = fila_max
        self.por_minuto_por_operador = por_minuto_por_operador
        self.retencao_dias = retencao_dias
        self.max_por_worker = max_por_worker


class ComandoRemotoDoCentral:
    def __init__(self, registry: "WorkerRegistry") -> None:
        self.registry = registry
        self.db: "Database" = registry.db
        self.cfg = LimitesDoComandoRemoto()
        #: `bus.emit`; `None` = ninguém ligou a auditoria, e então NADA corre.
        self.emitir: Callable[..., object] | None = None
        #: A linha crua de quem ainda não foi despachado (e o link por onde foi, depois). Só memória.
        self._crus: dict[str, Exec] = {}
        self._link_do_envio: dict[str, "WorkerLink"] = {}
        self._vigias: dict[str, asyncio.Task[None]] = {}
        self._pedidos_do_operador: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ interruptores
    def features_extras(self, worker_id: str) -> frozenset[str]:
        """Acrescenta `remote_exec` ao que este central aceita de UM worker: só com os dois interruptores do lado do
        central ligados e fora o próprio servidor."""
        if self.cfg.ativo and worker_id != self.registry.local_worker_id and self.ligado_no_worker(worker_id):
            return frozenset({FEATURE_COMANDO_REMOTO})
        return frozenset()

    def ligado_no_worker(self, worker_id: str) -> bool:
        row = self.db.one("SELECT ligado FROM worker_comando_remoto WHERE worker_id=?", (worker_id,))
        return bool(row and row["ligado"])

    def estado_do_interruptor(self, worker_id: str) -> dict[str, object]:
        link = self.registry.live.get(worker_id)
        return {"worker_id": worker_id, "central_ativo": bool(self.cfg.ativo),
                "worker_ligado": self.ligado_no_worker(worker_id),
                "agente_anuncia": FEATURE_COMANDO_REMOTO in self.registry.anunciadas.get(worker_id, frozenset()),
                "negociado": bool(link is not None and FEATURE_COMANDO_REMOTO in link.features_aceitas),
                "e_o_central": worker_id == self.registry.local_worker_id}

    def definir_interruptor(self, worker_id: str, ligado: bool, por: str) -> dict[str, object]:
        if self.db.one("SELECT id FROM workers WHERE id=?", (worker_id,)) is None:
            raise ErroDeComando(404, "worker_inexistente", f"worker '{worker_id}' não existe")
        if worker_id == self.registry.local_worker_id:
            raise ErroDeComando(409, "central_fora", "o comando remoto não vale para o próprio servidor central")
        if self.db.one("SELECT worker_id FROM worker_comando_remoto WHERE worker_id=?", (worker_id,)) is None:
            self.db.execute("INSERT INTO worker_comando_remoto (worker_id, ligado, changed_by, changed_at) "
                            "VALUES (?, ?, ?, ?)", (worker_id, int(ligado), por, now_iso()))
        else:
            self.db.execute("UPDATE worker_comando_remoto SET ligado=?, changed_by=?, changed_at=? WHERE worker_id=?",
                            (int(ligado), por, now_iso(), worker_id))
        self._auditar("worker.comando.interruptor",
                      f"Comando remoto {'LIGADO' if ligado else 'desligado'} no worker {worker_id} por {por}.",
                      data={"worker_id": worker_id, "ligado": ligado, "por": por})
        # As features só se negociam no `hello`/`welcome`: renegociar agora (o agente reconecta em segundos) é o que
        # faz o interruptor valer ao vivo, nos dois sentidos.
        self.registry.renegociar(worker_id)
        return self.estado_do_interruptor(worker_id)

    # ------------------------------------------------------------------ auditoria
    def _auditar(self, kind: str, message: str, *, level: str = "info", data: dict[str, object] | None = None) -> None:
        if self.emitir is None:
            raise ErroDeComando(500, "auditoria_indisponivel", "a auditoria do comando remoto não está ligada")
        self.emitir(kind, message, level=level, data=data or {})

    def _dados_do_evento(self, row: Registro) -> dict[str, object]:
        return {"id": row["id"], "worker_id": row["worker_id"], "requested_by": row["requested_by"],
                "linha": (row["linha_redigida"] or "")[:LINHA_NO_EVENTO_MAX], "pasta": row["pasta"],
                "state": row["state"], "exit_code": row["exit_code"], "duration_ms": row["duration_ms"],
                "truncated": bool(row["truncated"]), "timed_out": row["state"] == "timed_out",
                "reason": row["reason"]}

    # ------------------------------------------------------------------ pedir
    def _limitar_por_operador(self, operador: str) -> None:
        agora = time.monotonic()
        janela = self._pedidos_do_operador.setdefault(operador, deque())
        while janela and agora - janela[0] > 60.0:
            janela.popleft()
        if len(janela) >= self.cfg.por_minuto_por_operador:
            raise ErroDeComando(429, "limite_por_minuto",
                                f"mais de {self.cfg.por_minuto_por_operador} comandos por minuto para este operador")
        janela.append(agora)

    async def pedir(self, worker_id: str, operador: str | None, *, linha: str | None, argv: list[str] | None,
                    pasta: str | None, timeout_s: float | None, idempotency_key: str | None) -> Registro:
        if not operador:
            raise ErroDeComando(401, "sem_operador", "o comando remoto exige uma sessão de operador nomeada")
        if not self.cfg.ativo:
            raise ErroDeComando(409, "comando_remoto_desligado", "o interruptor do central está desligado")
        if self.db.one("SELECT id FROM workers WHERE id=?", (worker_id,)) is None:
            raise ErroDeComando(404, "worker_inexistente", f"worker '{worker_id}' não existe")
        if worker_id == self.registry.local_worker_id:
            raise ErroDeComando(409, "central_fora", "o comando remoto não vale para o próprio servidor central")
        if not self.ligado_no_worker(worker_id):
            raise ErroDeComando(409, "comando_remoto_desligado", "o interruptor deste worker está desligado")
        if not self.registry.aceitou(worker_id, FEATURE_COMANDO_REMOTO):
            raise ErroDeComando(409, "worker_sem_remote_exec",
                                "o agente deste worker não negociou remote_exec nesta conexão")
        chave = idempotency_key or f"cmd-{secrets.token_hex(12)}"
        if (antes := self.db.one("SELECT * FROM worker_comandos WHERE idempotency_key=?", (chave,))) is not None:
            if antes["worker_id"] != worker_id or antes["requested_by"] != operador:
                raise ErroDeComando(409, "chave_em_uso", "esta idempotency_key já foi usada em outro pedido")
            return cast(Registro, dict(antes))       # mesma chave, mesmo comando: repetir não repete o efeito
        exec_id = f"exc-{secrets.token_hex(8)}"
        try:
            pedido = Exec(exec_id=exec_id, linha=linha, argv=argv, pasta=pasta,
                          **({"timeout_s": timeout_s} if timeout_s is not None else {}))
        except (ValidationError, ValueError) as exc:
            raise ErroDeComando(422, "pedido_invalido", _resumo_do_erro(exc)) from exc
        self._limitar_por_operador(operador)
        texto = pedido.linha if pedido.linha is not None else " ".join(pedido.argv or [])
        modo = "linha" if pedido.linha is not None else "argv"
        agora = now_iso()
        if (linha_de_comando_suspeita(texto) or linha_de_comando_suspeita(pedido.pasta)
                or linha_de_comando_suspeita(idempotency_key)):
            # Auditoria da recusa SEM o texto: quem pediu, quando e para onde.
            chave = f"cmd-{secrets.token_hex(12)}"     # a chave enviada também pode ter sido o segredo
            row = self._inserir(exec_id, worker_id, operador, chave, modo, MARCA_LINHA_RECUSADA, None,
                                pedido.timeout_s, "rejected", agora,
                                reason="a linha parece levar credencial; segredo não vai por comando remoto")
            self._auditar("worker.comando", f"Comando recusado em {worker_id} por {operador}: parece levar credencial.",
                          level="warn", data=self._dados_do_evento(row))
            raise ErroDeComando(422, "linha_com_credencial", row["reason"])
        if self._abertos(worker_id) >= self.cfg.fila_max + 1:
            raise ErroDeComando(429, "fila_cheia", f"o worker já tem {self.cfg.fila_max} comandos na fila")
        redigida = redact(texto) or texto
        row = self._inserir(exec_id, worker_id, operador, chave, modo, redigida, pasta, pedido.timeout_s, "created",
                            agora)
        try:
            self._auditar("worker.comando", f"Comando pedido em {worker_id} por {operador}.",
                          data=self._dados_do_evento(row))
        except Exception as exc:  # noqa: BLE001 - sem auditoria, o comando NÃO corre
            self._encerrar(exec_id, "rejected", reason="a auditoria falhou; o comando não foi despachado")
            if isinstance(exc, ErroDeComando):
                raise
            raise ErroDeComando(500, "auditoria_indisponivel", "a auditoria do comando falhou") from exc
        self._crus[exec_id] = pedido
        self._podar(worker_id)
        await self._drenar(worker_id)
        return self._ler(exec_id) or row

    def _inserir(self, exec_id: str, worker_id: str, operador: str, chave: str, modo: str, linha: str,
                 pasta: str | None, timeout_s: float, estado: str, agora: str, *, reason: str | None = None
                 ) -> Registro:
        self.db.execute(
            "INSERT INTO worker_comandos (id, worker_id, requested_by, idempotency_key, modo, linha_redigida, pasta, "
            "timeout_s, state, reason, created_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (exec_id, worker_id, operador, chave, modo, linha, pasta, float(timeout_s), estado, reason, agora,
             agora if estado in ESTADOS_FINAIS else None))
        row = self._ler(exec_id)
        assert row is not None
        return row

    def _ler(self, exec_id: str) -> Registro | None:
        row = self.db.one("SELECT * FROM worker_comandos WHERE id=?", (exec_id,))
        return cast(Registro, dict(row)) if row is not None else None

    def _abertos(self, worker_id: str) -> int:
        marcas = ",".join("?" for _ in ESTADOS_ABERTOS)
        return int(self.db.scalar(f"SELECT COUNT(*) FROM worker_comandos WHERE worker_id=? AND state IN ({marcas})",
                                  (worker_id, *ESTADOS_ABERTOS)) or 0)

    def _podar(self, worker_id: str) -> None:
        """Retenção: nada de uma saída guardada para sempre. Só poda o que já terminou."""
        corte = (datetime.now(timezone.utc) - timedelta(days=self.cfg.retencao_dias)).strftime("%Y-%m-%dT%H:%M:%S")
        marcas = ",".join("?" for _ in ESTADOS_FINAIS)
        self.db.execute(f"DELETE FROM worker_comandos WHERE worker_id=? AND state IN ({marcas}) AND created_at < ?",
                        (worker_id, *ESTADOS_FINAIS, corte))
        self.db.execute(
            f"DELETE FROM worker_comandos WHERE worker_id=? AND state IN ({marcas}) AND id NOT IN "
            "(SELECT id FROM worker_comandos WHERE worker_id=? ORDER BY created_at DESC LIMIT ?)",
            (worker_id, *ESTADOS_FINAIS, worker_id, self.cfg.max_por_worker))

    def _encerrar(self, exec_id: str, estado: str, *, reason: str | None = None, **campos: object) -> Registro | None:
        """Põe o registro num estado final (uma vez só: o que já terminou não muda)."""
        colunas = {"state": estado, "reason": reason, "finished_at": now_iso(), **campos}
        sets = ", ".join(f"{c}=?" for c in colunas)
        marcas = ",".join("?" for _ in ESTADOS_ABERTOS)
        self.db.execute(f"UPDATE worker_comandos SET {sets} WHERE id=? AND state IN ({marcas})",
                        (*colunas.values(), exec_id, *ESTADOS_ABERTOS))
        return self._ler(exec_id)

    # ------------------------------------------------------------------ envio
    async def _drenar(self, worker_id: str) -> None:
        """Despacha o mais antigo da fila se o worker está livre. Um comando por vez por máquina."""
        async with self._lock:
            if self._abertos_em_voo(worker_id):
                return
            row = self.db.one("SELECT id FROM worker_comandos WHERE worker_id=? AND state='created' "
                              "ORDER BY created_at, id LIMIT 1", (worker_id,))
            if row is None:
                return
            exec_id = row["id"]
            pedido = self._crus.get(exec_id)
            link = self.registry.live.get(worker_id)
            if pedido is None:
                self._finalizar_com_evento(exec_id, "rejected", "o central reiniciou antes de despachar")
                return
            if link is None or FEATURE_COMANDO_REMOTO not in link.features_aceitas:
                self._crus.pop(exec_id, None)
                self._finalizar_com_evento(exec_id, "rejected", "o worker desconectou antes do envio")
                return
            self.db.execute("UPDATE worker_comandos SET state='dispatched', dispatched_at=? WHERE id=? AND state='created'",
                            (now_iso(), exec_id))
            self._link_do_envio[exec_id] = link
            try:
                await link.send(pedido.model_dump())
            except Exception:  # noqa: BLE001 - o canal caiu NO envio: pode ter chegado, então é incerto
                self._crus.pop(exec_id, None)
                self._finalizar_com_evento(exec_id, "uncertain", "o canal caiu durante o envio ao agente")
            else:
                self._crus.pop(exec_id, None)
                self._vigias[exec_id] = asyncio.ensure_future(self._vigiar(exec_id, worker_id, pedido.timeout_s))

    def _abertos_em_voo(self, worker_id: str) -> bool:
        return self.db.one("SELECT id FROM worker_comandos WHERE worker_id=? AND state IN ('dispatched','running') "
                           "LIMIT 1", (worker_id,)) is not None

    async def _vigiar(self, exec_id: str, worker_id: str, timeout_s: float) -> None:
        try:
            await asyncio.sleep(timeout_s + GRACA_DO_DESFECHO_S)
        except asyncio.CancelledError:
            return
        row = self._ler(exec_id)
        if row is not None and row["state"] in ESTADOS_ABERTOS:
            self._finalizar_com_evento(exec_id, "uncertain", "o agente não devolveu o desfecho no prazo")
            await self._drenar(worker_id)

    def _finalizar_com_evento(self, exec_id: str, estado: str, reason: str | None, **campos: object) -> None:
        row = self._encerrar(exec_id, estado, reason=reason, **campos)
        if row is not None and row["state"] == estado:
            with contextlib.suppress(Exception):
                self._auditar("worker.comando", f"Comando {exec_id} em {row['worker_id']}: {estado}.",
                              level="info" if estado == "succeeded" else "warn", data=self._dados_do_evento(row))

    # ------------------------------------------------------------------ o que volta do agente
    def on_ack(self, worker_id: str, msg: ExecAck) -> None:
        row = self._ler(msg.exec_id)
        if row is None or row["worker_id"] != worker_id:
            return
        if msg.recusa:
            self._finalizar_com_evento(msg.exec_id, "rejected", (redact(msg.recusa) or "recusado pelo agente")[:300])
        else:
            self.db.execute("UPDATE worker_comandos SET state='running' WHERE id=? AND state='dispatched'",
                            (msg.exec_id,))

    async def on_result(self, worker_id: str, msg: ExecResult) -> None:
        """O desfecho. Sempre confirma ao agente (`ExecResultAck`), inclusive o que já estava encerrado: é o que o
        deixa tirar o desfecho do diário dele."""
        link = self.registry.live.get(worker_id)
        row = self._ler(msg.exec_id)
        if row is not None and row["worker_id"] == worker_id:
            if vigia := self._vigias.pop(msg.exec_id, None):
                vigia.cancel()
            if row["state"] in ESTADOS_ABERTOS:
                # Redação de novo, do lado de cá: não se confia só no agente. Redige o texto inteiro e só então corta.
                saida = _cortar_redigido(msg.stdout)
                erro = _cortar_redigido(msg.stderr)
                self._finalizar_com_evento(
                    msg.exec_id, msg.estado, (redact(msg.error) or "")[:300] or None, exit_code=msg.exit_code,
                    stdout=saida[0], stderr=erro[0], truncated=int(msg.truncated or saida[1] or erro[1]),
                    duration_ms=msg.duration_ms)
        if link is not None:
            with contextlib.suppress(Exception):
                await link.send(ExecResultAck(exec_id=msg.exec_id).model_dump())
        await self._drenar(worker_id)

    def on_link_lost(self, worker_id: str, link: "WorkerLink") -> None:
        """O canal caiu: o que estava em voo por ELE vira `uncertain`; o que esperava na fila, `rejected` (nunca foi
        enviado). Link velho terminando tarde não encosta no que pertence ao link novo."""
        for row in self.db.query("SELECT id, state FROM worker_comandos WHERE worker_id=? AND state IN "
                                 "('created','dispatched','running')", (worker_id,)):
            exec_id = row["id"]
            if row["state"] == "created":
                self._crus.pop(exec_id, None)
                self._finalizar_com_evento(exec_id, "rejected", "o worker desconectou antes do envio")
            elif self._link_do_envio.get(exec_id) is link:
                self._link_do_envio.pop(exec_id, None)
                if vigia := self._vigias.pop(exec_id, None):
                    vigia.cancel()
                self._finalizar_com_evento(exec_id, "uncertain", "o canal com o worker caiu durante o comando")

    def reconciliar_na_subida(self) -> int:
        """Reinício do central: o que ficou em voo ou na fila não tem mais dono em memória. Em voo = `uncertain`
        (o agente pode ter rodado), na fila = `rejected` (nunca foi enviado)."""
        n = 0
        for row in self.db.query("SELECT id, state FROM worker_comandos WHERE state IN ('created','dispatched','running')"):
            if row["state"] == "created":
                self._encerrar(row["id"], "rejected", reason="o central reiniciou antes de despachar")
            else:
                self._encerrar(row["id"], "uncertain", reason="o central reiniciou durante o comando")
            n += 1
        return n

    # ------------------------------------------------------------------ cancelar e ler
    async def cancelar(self, worker_id: str, exec_id: str, operador: str | None) -> Registro:
        if not operador:
            raise ErroDeComando(401, "sem_operador", "o comando remoto exige uma sessão de operador nomeada")
        row = self._ler(exec_id)
        if row is None or row["worker_id"] != worker_id:
            raise ErroDeComando(404, "comando_inexistente", "comando não encontrado neste worker")
        if row["state"] in ESTADOS_FINAIS:
            raise ErroDeComando(409, "ja_encerrado", f"o comando já terminou ({row['state']})")
        if row["state"] == "created":
            self._crus.pop(exec_id, None)
            self._finalizar_com_evento(exec_id, "cancelled", f"cancelado por {operador} antes de ser despachado")
        else:
            link = self.registry.live.get(worker_id)
            if link is None:
                raise ErroDeComando(409, "worker_desconectado", "sem canal com o worker para cancelar")
            await link.send(ExecCancel(exec_id=exec_id).model_dump())
            self._auditar("worker.comando.cancelamento", f"Cancelamento do comando {exec_id} pedido por {operador}.",
                          data={"id": exec_id, "worker_id": worker_id, "por": operador})
        return self._ler(exec_id) or row

    def listar(self, worker_id: str, limite: int = 50) -> list[Registro]:
        limite = max(1, min(int(limite), 200))
        return [cast(Registro, dict(r)) for r in self.db.query(
            "SELECT * FROM worker_comandos WHERE worker_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (worker_id, limite))]

    def obter(self, worker_id: str, exec_id: str) -> Registro:
        row = self._ler(exec_id)
        if row is None or row["worker_id"] != worker_id:
            raise ErroDeComando(404, "comando_inexistente", "comando não encontrado neste worker")
        return row


def _cortar_redigido(texto: str) -> tuple[str, bool]:
    """Redige o texto INTEIRO e só depois corta no teto do contrato (começo e fim)."""
    return cortar_saida(redact(texto) or "", EXEC_SAIDA_MAX_BYTES)


def _resumo_do_erro(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:4])
    return str(exc)[:300]


def dto(row: Registro, *, com_saida: bool = True) -> dict[str, object]:
    """O que a API devolve de um comando. A linha é SEMPRE a redigida."""
    saida = {"id": row["id"], "worker_id": row["worker_id"], "requested_by": row["requested_by"], "modo": row["modo"],
             "linha": row["linha_redigida"], "pasta": row["pasta"], "timeout_s": row["timeout_s"],
             "state": row["state"], "exit_code": row["exit_code"], "truncated": bool(row["truncated"]),
             "duration_ms": row["duration_ms"], "reason": row["reason"], "created_at": row["created_at"],
             "dispatched_at": row["dispatched_at"], "finished_at": row["finished_at"]}
    if com_saida:
        saida["stdout"] = row["stdout"] or ""
        saida["stderr"] = row["stderr"] or ""
    return saida
