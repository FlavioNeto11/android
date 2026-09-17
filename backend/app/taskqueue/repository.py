"""Persistência da fila: execuções, objetivos por dispositivo, etapas, tentativas, ações e evidências.

Tudo o que o scheduler decide é gravado ANTES de ser executado; as transições passam por
`states.check_transition` e as operações críticas (assumir etapa + registrar tentativa) são uma
única transação.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from ..db import Database, dumps, loads
from ..events import EventBus
from ..models import (RUN_TERMINAL, ActionDTO, ActionStatus, AttemptDTO, AttemptStatus, DecisionDTO, DeliveryLevel,
                      EvidenceDTO, ObjectiveDTO, ObjectiveStatus, Plan, PlanStep, PlanVersionDTO, Postcondition,
                      RunCounts, RunCreate, RunDetail, RunStatus, RunSummary, StepDTO, StepResult, StepStatus)
from ..planning.provider import Usage
from ..util import new_run_id, now_iso, truncate
from .states import STEP_ACTIVE, STEP_OPEN, check_transition

TEMPLATE_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def resolve_templates(text: str | None, variables: dict[str, str]) -> str | None:
    """Substitui apenas variáveis conhecidas ({instance_id}, {run_id}, parâmetros…); o resto fica como está."""
    if text is None:
        return None
    return TEMPLATE_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


class Repository:
    def __init__(self, db: Database, bus: EventBus, evidence_dir: Path):
        self.db = db
        self.bus = bus
        self.evidence_dir = evidence_dir

    # ================================================================== execuções
    def create_run(self, req: RunCreate, *, simulated: bool) -> tuple[sqlite3.Row, bool]:
        """Cria a execução. A chave de idempotência é UNIQUE: repetição devolve a mesma execução."""
        run_id = new_run_id()
        try:
            with self.db.tx():
                self.db.execute(
                    "INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, req.idempotency_key, req.command.strip(), req.mode, RunStatus.planning.value,
                     int(simulated), dumps(req.instance_ids), now_iso()))
        except sqlite3.IntegrityError:
            row = self.db.one("SELECT * FROM runs WHERE idempotency_key=?", (req.idempotency_key,))
            assert row is not None
            return row, False
        row = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        assert row is not None
        self.emit_run(run_id, f"Execução {run_id} criada; planejando…")
        return row, True

    def run_row(self, run_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))

    def set_run_status(self, run_id: str, status: RunStatus, detail: str | None = None, *, message: str | None = None,
                       level: str = "info") -> None:
        fields, params = ["status=?", "status_detail=?"], [status.value, detail]
        if status == RunStatus.running:
            fields.append("started_at=COALESCE(started_at, ?)")
            params.append(now_iso())
        if status in RUN_TERMINAL:
            fields.append("finished_at=COALESCE(finished_at, ?)")
            params.append(now_iso())
        self.db.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id=?", (*params, run_id))
        self.emit_run(run_id, message or f"Execução {run_id}: {status.value}", level=level)

    def save_plan(self, run_id: str, plan: Plan) -> None:
        self.db.execute("UPDATE runs SET plan=? WHERE id=?", (plan.model_dump_json(), run_id))

    def materialize(self, run_id: str, plan: Plan, instances: list[dict[str, Any]]) -> None:
        """Persiste objetivos e etapas (versão 1) de cada aparelho ANTES de qualquer execução."""
        with self.db.tx():
            for inst in instances:
                iid = inst["instance_id"]
                oid = f"{run_id}:{iid}"
                if self.db.one("SELECT id FROM objectives WHERE id=?", (oid,)):
                    continue
                base = {"instance_id": iid, "run_id": run_id, "account_label": inst.get("account_label") or ""}
                params = {k: resolve_templates(v, base) or "" for k, v in plan.parameters.items()}
                self.db.execute(
                    "INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters) VALUES (?,?,?,?,?,?)",
                    (oid, run_id, iid, ObjectiveStatus.pending.value, 1, dumps(params)))
                self._insert_steps(run_id, oid, iid, 1, plan.steps, {**params, **base}, "Plano inicial")

    def _insert_steps(self, run_id: str, oid: str, iid: str, version: int, steps: list[PlanStep],
                      variables: dict[str, str], reason: str) -> None:
        resolved: list[PlanStep] = []
        for s in steps:
            post = s.postcondition.model_copy(update={
                "value": resolve_templates(s.postcondition.value, variables),
                "description": resolve_templates(s.postcondition.description, variables)})
            resolved.append(s.model_copy(update={
                "title": resolve_templates(s.title, variables), "goal": resolve_templates(s.goal, variables),
                "precondition": resolve_templates(s.precondition, variables), "postcondition": post,
                "commit_guard": [resolve_templates(g, variables) or "" for g in s.commit_guard]}))
        self.db.execute("INSERT INTO plan_versions(objective_id, version, reason, steps, created_at) VALUES (?,?,?,?,?)",
                        (oid, version, reason, dumps([s.model_dump(mode="json") for s in resolved]), now_iso()))
        for seq, s in enumerate(resolved, start=1):
            self.db.execute(
                "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
                " side_effect, commit_guard, precondition, postcondition, timeout_s, max_attempts, status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{run_id}:{iid}:v{version}:{s.key}", run_id, oid, iid, version, seq, s.key, s.title, s.goal,
                 dumps(s.depends_on), int(s.side_effect), dumps(s.commit_guard), s.precondition,
                 s.postcondition.model_dump_json(), s.timeout_s, s.max_attempts, StepStatus.pending.value))

    # ================================================================== etapas
    def step_row(self, step_id: str) -> sqlite3.Row:
        row = self.db.one("SELECT * FROM steps WHERE id=?", (step_id,))
        if row is None:
            raise KeyError(step_id)
        return row

    def transition_step(self, step_id: str, target: StepStatus, *, detail: str | None = None,
                        result: StepResult | None = None, next_retry_at: str | None = None,
                        message: str | None = None, level: str = "info") -> None:
        with self.db.tx():
            row = self.step_row(step_id)
            check_transition(row["status"], target)
            fields = ["status=?", "status_detail=?", "next_retry_at=?"]
            params: list[Any] = [target.value, truncate(detail, 600), next_retry_at]
            if result is not None:
                fields.append("result=?")
                params.append(result.model_dump_json())
            if target in (StepStatus.succeeded, StepStatus.failed, StepStatus.cancelled, StepStatus.skipped,
                          StepStatus.uncertain, StepStatus.waiting_user):
                fields.append("finished_at=?")
                params.append(now_iso())
            self.db.execute(f"UPDATE steps SET {', '.join(fields)} WHERE id=?", (*params, step_id))
        self.emit_step(step_id, message or f"Etapa '{row['title']}': {target.value}" + (f" — {detail}" if detail else ""),
                       level=level)

    def promote(self, run_id: str) -> int:
        """pending→ready quando as dependências estão comprovadas; retry_wait→ready quando vence o intervalo."""
        changed = 0
        now = now_iso()
        rows = self.db.query(
            "SELECT s.* FROM steps s JOIN objectives o ON o.id = s.objective_id AND o.plan_version = s.plan_version"
            " WHERE s.run_id=? AND s.status IN ('pending','retry_wait') ORDER BY s.seq", (run_id,))
        for r in rows:
            if r["status"] == "retry_wait":
                if (r["next_retry_at"] or "") <= now:
                    self.transition_step(r["id"], StepStatus.ready, message=f"Etapa '{r['title']}': nova tentativa liberada")
                    changed += 1
                continue
            deps = loads(r["depends_on"], [])
            if deps:
                ok = self.db.scalar(
                    f"SELECT COUNT(*) FROM steps WHERE objective_id=? AND plan_version=? AND status='succeeded'"
                    f" AND key IN ({','.join('?' * len(deps))})", (r["objective_id"], r["plan_version"], *deps))
                # dependência comprovada em versão anterior do plano também vale
                if ok < len(deps):
                    ok = self.db.scalar(
                        f"SELECT COUNT(DISTINCT key) FROM steps WHERE objective_id=? AND status='succeeded'"
                        f" AND key IN ({','.join('?' * len(deps))})", (r["objective_id"], *deps))
                if ok < len(deps):
                    continue
            self.transition_step(r["id"], StepStatus.ready)
            changed += 1
        return changed

    def next_ready_step(self, objective_id: str) -> sqlite3.Row | None:
        return self.db.one(
            "SELECT s.* FROM steps s JOIN objectives o ON o.id=s.objective_id AND o.plan_version=s.plan_version"
            " WHERE s.objective_id=? AND s.status='ready' ORDER BY s.seq LIMIT 1", (objective_id,))

    def claim_step(self, step_id: str) -> sqlite3.Row | None:
        """Assume a etapa e registra a tentativa numa única transação.

        Garante: (1) só uma etapa ativa por aparelho (dono único do executor); (2) a mesma tentativa
        nunca é disparada duas vezes (id estável `<step>:a<n>` com UNIQUE).
        """
        with self.db.tx():
            row = self.db.one("SELECT * FROM steps WHERE id=?", (step_id,))
            if row is None or row["status"] != StepStatus.ready.value or row["attempts"] >= row["max_attempts"]:
                return None
            busy = self.db.scalar("SELECT COUNT(*) FROM steps WHERE instance_id=? AND status IN ('running','verifying')",
                                  (row["instance_id"],))
            if busy:
                return None
            # `attempts` conta tentativas CONSUMIDAS; o número da tentativa vem do histórico (interrupções
            # por pausa/controle manual/reinício não consomem tentativa, mas ficam registradas)
            number = int(self.db.scalar("SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE step_id=?", (step_id,)))
            attempt_id = f"{step_id}:a{number}"
            cur = self.db.execute(
                "UPDATE steps SET status='running', attempts=attempts+1, started_at=COALESCE(started_at, ?),"
                " status_detail=NULL WHERE id=? AND status='ready'", (now_iso(), step_id))
            if cur.rowcount != 1:
                return None
            try:
                self.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                                (attempt_id, step_id, number, AttemptStatus.running.value, now_iso()))
            except sqlite3.IntegrityError:
                raise RuntimeError(f"tentativa {attempt_id} já existe — disparo duplicado bloqueado") from None
        self.emit_step(step_id, f"Etapa '{row['title']}': tentativa {number} iniciada")
        attempt = self.db.one("SELECT * FROM attempts WHERE id=?", (attempt_id,))
        self.emit_attempt(attempt_id, row)
        return attempt

    def note_attempt(self, attempt_id: str, *, error: str | None = None, recovery: str | None = None) -> None:
        """Anota erro original/recuperação numa tentativa ainda em andamento."""
        self.db.execute("UPDATE attempts SET error=COALESCE(?, error), recovery=COALESCE(?, recovery) WHERE id=?",
                        (truncate(error, 800), truncate(recovery, 800), attempt_id))

    def refund_attempt(self, step_id: str) -> None:
        """Interrupção sem culpa da etapa (pausa, controle manual, reinício): não consome tentativa."""
        self.db.execute("UPDATE steps SET attempts=MAX(attempts-1, 0) WHERE id=?", (step_id,))

    def finish_attempt(self, attempt_id: str, status: AttemptStatus, *, error: str | None = None,
                       recovery: str | None = None, observed: str | None = None) -> None:
        self.db.execute(
            "UPDATE attempts SET status=?, finished_at=?, error=COALESCE(?, error), recovery=COALESCE(?, recovery),"
            " observed_result=COALESCE(?, observed_result) WHERE id=?",
            (status.value, now_iso(), truncate(error, 800), truncate(recovery, 800), truncate(observed, 800), attempt_id))
        step = self.db.one("SELECT s.* FROM steps s JOIN attempts a ON a.step_id=s.id WHERE a.id=?", (attempt_id,))
        self.emit_attempt(attempt_id, step)

    # ================================================================== ações (diário intenção → resultado)
    def log_intent(self, attempt_id: str, tool: str, args: dict[str, Any], rationale: str | None,
                   *, side_effect: bool) -> int:
        seq = int(self.db.scalar("SELECT COALESCE(MAX(seq),0)+1 FROM actions WHERE attempt_id=?", (attempt_id,)))
        cur = self.db.execute(
            "INSERT INTO actions(attempt_id, seq, tool, args, rationale, status, side_effect, intent_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (attempt_id, seq, tool, dumps(args), truncate(rationale, 400), ActionStatus.intended.value,
             int(side_effect), now_iso()))
        action_id = int(cur.lastrowid or 0)
        self.emit_action(action_id)
        return action_id

    def finish_action(self, action_id: int, status: ActionStatus, *, result: dict[str, Any] | None = None,
                      error: str | None = None, effect_possible: bool = False) -> None:
        self.db.execute("UPDATE actions SET status=?, done_at=?, result=?, error=?, effect_possible=? WHERE id=?",
                        (status.value, now_iso(), dumps(result) if result is not None else None, truncate(error, 600),
                         int(effect_possible), action_id))
        self.emit_action(action_id)

    def commit_state(self, step_id: str) -> tuple[bool, bool]:
        """(efeito_disparado, resultado_desconhecido) considerando TODAS as tentativas da etapa."""
        rows = self.db.query(
            "SELECT a.status, a.effect_possible FROM actions a JOIN attempts t ON t.id=a.attempt_id"
            " WHERE t.step_id=? AND a.side_effect=1", (step_id,))
        fired = any(r["status"] in ("done", "unknown", "intended") or r["effect_possible"] for r in rows)
        unknown = any(r["status"] in ("unknown", "intended") for r in rows)
        return fired, unknown

    # ================================================================== evidências / uso de IA
    def add_evidence(self, *, run_id: str, instance_id: str, step_id: str | None, attempt_id: str | None, kind: str,
                     note: str | None, data: bytes | None = None, ext: str = "jpg", redacted: bool = False) -> int:
        ts = now_iso()
        path: str | None = None
        if data is not None and not redacted:
            folder = self.evidence_dir / run_id / instance_id
            folder.mkdir(parents=True, exist_ok=True)
            safe_step = re.sub(r"[^A-Za-z0-9_.-]", "_", (attempt_id or step_id or "run").split(":", 2)[-1])
            file = folder / f"{ts.replace(':', '').replace('.', '')}_{safe_step}_{kind}.{ext}"
            file.write_bytes(data)
            path = str(file.relative_to(self.evidence_dir))
        cur = self.db.execute(
            "INSERT INTO evidence(run_id, instance_id, step_id, attempt_id, ts, kind, note, path, redacted)"
            " VALUES (?,?,?,?,?,?,?,?,?)", (run_id, instance_id, step_id, attempt_id, ts, kind, truncate(note, 600),
                                            path, int(redacted)))
        eid = int(cur.lastrowid or 0)
        ev = self.evidence_dto(self.db.one("SELECT * FROM evidence WHERE id=?", (eid,)))
        self.bus.emit("evidence.added", f"Evidência registrada: {note or kind}", run_id=run_id, instance_id=instance_id,
                      step_id=step_id, attempt_id=attempt_id, data={"evidence": ev.model_dump(mode="json")})
        return eid

    def add_usage(self, run_id: str, objective_id: str | None, usage: Usage) -> None:
        if not usage.calls and not usage.input_tokens:
            return
        self.db.execute("UPDATE runs SET ai_input_tokens=ai_input_tokens+?, ai_output_tokens=ai_output_tokens+? WHERE id=?",
                        (usage.input_tokens, usage.output_tokens, run_id))
        if objective_id:
            self.db.execute(
                "UPDATE objectives SET ai_calls=ai_calls+?, ai_input_tokens=ai_input_tokens+?,"
                " ai_output_tokens=ai_output_tokens+? WHERE id=?",
                (usage.calls, usage.input_tokens, usage.output_tokens, objective_id))

    def decision(self, text: str, *, run_id: str, instance_id: str | None = None, step_id: str | None = None) -> None:
        self.bus.emit("decision", text, run_id=run_id, instance_id=instance_id, step_id=step_id, data={"text": text})

    # ================================================================== objetivos
    def objective_row(self, objective_id: str) -> sqlite3.Row:
        row = self.db.one("SELECT * FROM objectives WHERE id=?", (objective_id,))
        if row is None:
            raise KeyError(objective_id)
        return row

    def set_objective(self, objective_id: str, status: ObjectiveStatus, *, detail: str | None = None,
                      blocked_reason: str | None = None, needs: str | None = None,
                      delivery_level: DeliveryLevel | None = None, message: str | None = None,
                      level: str = "info") -> None:
        fields = ["status=?", "status_detail=?", "blocked_reason=?", "needs=?"]
        params: list[Any] = [status.value, truncate(detail, 600), truncate(blocked_reason, 600), truncate(needs, 600)]
        if status == ObjectiveStatus.running:
            fields.append("started_at=COALESCE(started_at, ?)")
            params.append(now_iso())
            fields.append("finished_at=NULL")
        if status in (ObjectiveStatus.succeeded, ObjectiveStatus.failed, ObjectiveStatus.cancelled,
                      ObjectiveStatus.uncertain, ObjectiveStatus.waiting_user):
            fields.append("finished_at=?")
            params.append(now_iso())
        if delivery_level is not None:
            fields.append("delivery_level=?")
            params.append(delivery_level.value)
        self.db.execute(f"UPDATE objectives SET {', '.join(fields)} WHERE id=?", (*params, objective_id))
        row = self.objective_row(objective_id)
        self.bus.emit("objective.updated", message or f"{row['instance_id']}: objetivo {status.value}"
                      + (f" — {detail}" if detail else ""), level=level, run_id=row["run_id"],
                      instance_id=row["instance_id"], objective_id=objective_id,
                      data={"objective": self.objective_dto(row).model_dump(mode="json")})

    def emit_objective(self, objective_id: str, message: str | None = None) -> None:
        """Publica o objetivo sem mudar o estado (progresso de etapas, nível de entrega)."""
        row = self.objective_row(objective_id)
        dto = self.objective_dto(row)
        self.bus.emit("objective.updated", message or f"{row['instance_id']}: {dto.steps_done}/{dto.steps_total} etapas comprovadas",
                      run_id=row["run_id"], instance_id=row["instance_id"], objective_id=objective_id,
                      data={"objective": dto.model_dump(mode="json")})

    def add_effect(self, objective_id: str, text: str) -> None:
        row = self.objective_row(objective_id)
        effects = loads(row["effects"], [])
        effects.append(f"{now_iso()} — {text}")
        self.db.execute("UPDATE objectives SET effects=? WHERE id=?", (dumps(effects), objective_id))

    def revise_plan(self, objective_id: str, reason: str, steps: list[PlanStep]) -> int:
        """Nova versão do plano para o objetivo: etapas abertas da versão atual viram `skipped`, as já
        comprovadas permanecem no histórico e as novas nascem com ids estáveis da nova versão."""
        with self.db.tx():
            obj = self.objective_row(objective_id)
            version = obj["plan_version"] + 1
            for r in self.db.query("SELECT id, status FROM steps WHERE objective_id=? AND plan_version=?",
                                   (objective_id, obj["plan_version"])):
                if r["status"] in ("pending", "ready", "retry_wait", "waiting_user"):
                    self.db.execute("UPDATE steps SET status='skipped', status_detail=?, finished_at=? WHERE id=?",
                                    (f"plano revisado (v{version})", now_iso(), r["id"]))
            self.db.execute("UPDATE objectives SET plan_version=? WHERE id=?", (version, objective_id))
            params = loads(obj["parameters"], {})
            account = self.db.scalar("SELECT account_label FROM instances WHERE id=?", (obj["instance_id"],)) or ""
            base = {"instance_id": obj["instance_id"], "run_id": obj["run_id"], "account_label": account}
            self._insert_steps(obj["run_id"], objective_id, obj["instance_id"], version, steps, {**params, **base}, reason)
        self.bus.emit("plan.revised", f"{obj['instance_id']}: plano revisado (v{version}) — {reason}", level="warn",
                      run_id=obj["run_id"], instance_id=obj["instance_id"], objective_id=objective_id,
                      data={"objective_id": objective_id, "version": version, "reason": reason})
        return version

    def recompute_run(self, run_id: str) -> RunStatus | None:
        """Deriva o estado da execução a partir dos objetivos. Só sucesso comprovado conta como sucesso."""
        run = self.run_row(run_id)
        if run is None or run["status"] in ("planning", "needs_input", "planned"):
            return None
        counts = self._counts(run_id)
        total = sum(counts.model_dump().values())
        used = int(self.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=? AND started_at IS NOT NULL", (run_id,)) or 0)
        self.db.execute("UPDATE runs SET instances_used=? WHERE id=?", (used, run_id))
        active = counts.running + counts.pending
        current = RunStatus(run["status"])
        if active == 0 and total > 0:
            if run["cancel_requested"] and counts.cancelled:
                new = RunStatus.cancelled
            elif counts.succeeded == total:
                new = RunStatus.completed
            else:
                new = RunStatus.completed_with_issues
            if counts.waiting_user or counts.uncertain:
                # há itens aguardando decisão do usuário: a execução segue "em aberto" para permitir retomada
                new = RunStatus.completed_with_issues
            detail = self._status_detail(counts, total)
            if new != current or detail != run["status_detail"]:
                self.set_run_status(run_id, new, detail, message=f"Execução {run_id} finalizada: {detail}",
                                    level="info" if new == RunStatus.completed else "warn")
            return new
        if current in RUN_TERMINAL and active > 0:          # retomada de itens
            self.db.execute("UPDATE runs SET finished_at=NULL WHERE id=?", (run_id,))
            self.set_run_status(run_id, RunStatus.paused if run["pause_requested"] else RunStatus.running,
                                "itens retomados")
            return RunStatus.running
        self.emit_run(run_id, None)
        return current

    @staticmethod
    def _status_detail(c: RunCounts, total: int) -> str:
        parts = [f"{c.succeeded} de {total} com sucesso comprovado"]
        for n, label in ((c.failed, "falha"), (c.waiting_user, "bloqueio aguardando usuário"),
                         (c.uncertain, "resultado incerto"), (c.cancelled, "cancelado")):
            if n:
                parts.append(f"{n} {label}")
        return "; ".join(parts)

    def _counts(self, run_id: str) -> RunCounts:
        c = RunCounts()
        for r in self.db.query("SELECT status, COUNT(*) n FROM objectives WHERE run_id=? GROUP BY status", (run_id,)):
            setattr(c, r["status"], r["n"])
        return c

    # ================================================================== consultas do scheduler
    def active_runs(self) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM runs WHERE status IN ('running','cancelling') ORDER BY created_at")

    def dispatchable_objectives(self) -> list[sqlite3.Row]:
        """Objetivos com etapa pronta, de execuções em andamento e não pausadas — a execução mais antiga primeiro."""
        return self.db.query(
            "SELECT o.*, r.created_at AS run_created FROM objectives o JOIN runs r ON r.id=o.run_id"
            " WHERE r.status='running' AND r.pause_requested=0 AND r.cancel_requested=0"
            " AND o.status IN ('pending','running')"
            " AND EXISTS (SELECT 1 FROM steps s WHERE s.objective_id=o.id AND s.plan_version=o.plan_version AND s.status='ready')"
            " ORDER BY r.created_at, o.instance_id")

    def interrupted_steps(self) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM steps WHERE status IN ('running','verifying') ORDER BY run_id, seq")

    def cancel_open_steps(self, run_id: str, *, objective_id: str | None = None, reason: str) -> int:
        q = "SELECT id, status FROM steps WHERE run_id=? AND status IN ('pending','ready','retry_wait','waiting_user')"
        params: list[Any] = [run_id]
        if objective_id:
            q += " AND objective_id=?"
            params.append(objective_id)
        n = 0
        for r in self.db.query(q, tuple(params)):
            self.transition_step(r["id"], StepStatus.cancelled, detail=reason)
            n += 1
        return n

    # ================================================================== DTOs e eventos
    def run_summary(self, row: sqlite3.Row, *, deduplicated: bool | None = None) -> RunSummary:
        ids = loads(row["instance_ids"], [])
        counts = self._counts(row["id"])
        total = sum(counts.model_dump().values())
        return RunSummary(
            id=row["id"], short_id=row["id"].rsplit("-", 1)[-1], command=row["command"], status=RunStatus(row["status"]),
            simulated=bool(row["simulated"]), instance_ids=ids, instances_requested=len(ids),
            instances_used=row["instances_used"], created_at=row["created_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], counts=counts, progress=(counts.succeeded / total) if total else 0.0,
            status_detail=row["status_detail"], deduplicated=deduplicated)

    def objective_dto(self, row: sqlite3.Row) -> ObjectiveDTO:
        done, total = self._step_progress(row["id"], row["plan_version"])
        return ObjectiveDTO(
            id=row["id"], run_id=row["run_id"], instance_id=row["instance_id"], status=ObjectiveStatus(row["status"]),
            status_detail=row["status_detail"], blocked_reason=row["blocked_reason"], needs=row["needs"],
            plan_version=row["plan_version"], parameters=loads(row["parameters"], {}), steps_done=done, steps_total=total,
            delivery_level=DeliveryLevel(row["delivery_level"]) if row["delivery_level"] else None,
            effects=loads(row["effects"], []), started_at=row["started_at"], finished_at=row["finished_at"],
            ai_calls=row["ai_calls"], ai_input_tokens=row["ai_input_tokens"], ai_output_tokens=row["ai_output_tokens"])

    def _step_progress(self, objective_id: str, version: int) -> tuple[int, int]:
        r = self.db.one("SELECT SUM(status='succeeded') d, COUNT(*) t FROM steps WHERE objective_id=? AND plan_version=?",
                        (objective_id, version))
        return int(r["d"] or 0), int(r["t"] or 0)

    @staticmethod
    def step_dto(r: sqlite3.Row) -> StepDTO:
        return StepDTO(
            id=r["id"], run_id=r["run_id"], objective_id=r["objective_id"], instance_id=r["instance_id"],
            plan_version=r["plan_version"], seq=r["seq"], key=r["key"], title=r["title"], goal=r["goal"],
            depends_on=loads(r["depends_on"], []), side_effect=bool(r["side_effect"]),
            commit_guard=loads(r["commit_guard"], []), precondition=r["precondition"],
            postcondition=Postcondition.model_validate_json(r["postcondition"]), timeout_s=r["timeout_s"],
            max_attempts=r["max_attempts"], attempts=r["attempts"], status=StepStatus(r["status"]),
            status_detail=r["status_detail"], next_retry_at=r["next_retry_at"], started_at=r["started_at"],
            finished_at=r["finished_at"], result=StepResult.model_validate_json(r["result"]) if r["result"] else None)

    @staticmethod
    def action_dto(r: sqlite3.Row) -> ActionDTO:
        return ActionDTO(id=r["id"], attempt_id=r["attempt_id"], seq=r["seq"], tool=r["tool"], args=loads(r["args"], {}),
                         rationale=r["rationale"], status=ActionStatus(r["status"]), side_effect=bool(r["side_effect"]),
                         intent_at=r["intent_at"], done_at=r["done_at"], result=loads(r["result"]), error=r["error"])

    def attempt_dto(self, r: sqlite3.Row, *, with_actions: bool = True) -> AttemptDTO:
        actions = ([self.action_dto(a) for a in self.db.query("SELECT * FROM actions WHERE attempt_id=? ORDER BY seq",
                                                             (r["id"],))] if with_actions else [])
        return AttemptDTO(id=r["id"], step_id=r["step_id"], number=r["number"], status=AttemptStatus(r["status"]),
                          started_at=r["started_at"], finished_at=r["finished_at"], error=r["error"],
                          recovery=r["recovery"], observed_result=r["observed_result"], actions=actions)

    @staticmethod
    def evidence_dto(r: sqlite3.Row) -> EvidenceDTO:
        has_file = bool(r["path"]) and not r["redacted"]
        return EvidenceDTO(id=r["id"], run_id=r["run_id"], instance_id=r["instance_id"], step_id=r["step_id"],
                           attempt_id=r["attempt_id"], ts=r["ts"], kind=r["kind"], note=r["note"],
                           url=f"/api/evidence/{r['id']}" if has_file else None, redacted=bool(r["redacted"]))

    def run_detail(self, run_id: str) -> RunDetail | None:
        row = self.run_row(run_id)
        if row is None:
            return None
        summary = self.run_summary(row)
        objectives = [self.objective_dto(o) for o in
                      self.db.query("SELECT * FROM objectives WHERE run_id=? ORDER BY instance_id", (run_id,))]
        steps = [self.step_dto(s) for s in
                 self.db.query("SELECT * FROM steps WHERE run_id=? ORDER BY instance_id, plan_version, seq", (run_id,))]
        attempts = [self.attempt_dto(a) for a in self.db.query(
            "SELECT a.* FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=? ORDER BY a.started_at", (run_id,))]
        evidence = [self.evidence_dto(e) for e in self.db.query("SELECT * FROM evidence WHERE run_id=? ORDER BY id", (run_id,))]
        versions = [PlanVersionDTO(objective_id=v["objective_id"], version=v["version"], reason=v["reason"],
                                   created_at=v["created_at"], steps=[PlanStep.model_validate(s) for s in loads(v["steps"], [])])
                    for v in self.db.query(
                        "SELECT v.* FROM plan_versions v JOIN objectives o ON o.id=v.objective_id WHERE o.run_id=?"
                        " ORDER BY v.objective_id, v.version", (run_id,))]
        decisions = [DecisionDTO(ts=d["ts"], instance_id=d["instance_id"], text=d["message"]) for d in self.db.query(
            "SELECT ts, instance_id, message FROM events WHERE run_id=? AND kind='decision' ORDER BY id", (run_id,))]
        return RunDetail(**summary.model_dump(), plan=Plan.model_validate_json(row["plan"]) if row["plan"] else None,
                         objectives=objectives, steps=steps, attempts=attempts, evidence=evidence,
                         plan_versions=versions, decisions=decisions)

    def emit_run(self, run_id: str, message: str | None, *, level: str = "info") -> None:
        row = self.run_row(run_id)
        if row is None:
            return
        summary = self.run_summary(row)
        self.bus.emit("run.updated", message or f"Execução {run_id}: {summary.status.value}", level=level, run_id=run_id,
                      data={"run": summary.model_dump(mode="json")})

    def emit_step(self, step_id: str, message: str, *, level: str = "info") -> None:
        r = self.step_row(step_id)
        self.bus.emit("step.updated", f"{r['instance_id']}: {message}", level=level, run_id=r["run_id"],
                      instance_id=r["instance_id"], objective_id=r["objective_id"], step_id=step_id,
                      data={"step": self.step_dto(r).model_dump(mode="json")})

    def emit_attempt(self, attempt_id: str, step: sqlite3.Row | None) -> None:
        a = self.db.one("SELECT * FROM attempts WHERE id=?", (attempt_id,))
        if a is None or step is None:
            return
        self.bus.emit("attempt.updated", f"{step['instance_id']}: tentativa {a['number']} — {a['status']}",
                      run_id=step["run_id"], instance_id=step["instance_id"], objective_id=step["objective_id"],
                      step_id=step["id"], attempt_id=attempt_id,
                      data={"attempt": self.attempt_dto(a, with_actions=False).model_dump(mode="json")})

    def emit_action(self, action_id: int) -> None:
        r = self.db.one(
            "SELECT a.*, s.run_id, s.instance_id, s.objective_id, s.id AS sid FROM actions a"
            " JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id WHERE a.id=?", (action_id,))
        if r is None:
            return
        dto = self.action_dto(r)
        label = dto.rationale or dto.tool
        self.bus.emit("action.logged", f"{r['instance_id']}: {dto.tool} [{dto.status.value}] — {label}",
                      level="warn" if dto.status in (ActionStatus.failed, ActionStatus.unknown, ActionStatus.rejected) else "info",
                      run_id=r["run_id"], instance_id=r["instance_id"], objective_id=r["objective_id"], step_id=r["sid"],
                      attempt_id=r["attempt_id"],
                      data={"action": dto.model_dump(mode="json"), "instance_id": r["instance_id"], "step_id": r["sid"]})
