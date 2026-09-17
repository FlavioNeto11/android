"""Operações de execução pedidas pelo painel: criar/planejar, iniciar, pausar, continuar, cancelar,
retomar itens elegíveis, resolver bloqueios e gerar o relatório final."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..db import loads
from ..devices.manager import DeviceManager
from ..models import (RUN_TERMINAL, InstanceState, ObjectiveDTO, ObjectiveStatus, ResolveBody, RunCreate, RunStatus,
                      RunSummary, StepResult, StepStatus)
from ..planning.provider import AIError, AIProvider, AppContext, PlanRequest
from .repository import Repository
from .scheduler import Scheduler

log = logging.getLogger("poc.runs")


class RunError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class RunService:
    def __init__(self, repo: Repository, scheduler: Scheduler, devices: DeviceManager, provider: AIProvider):
        self.flows = scheduler.flows
        self.repo = repo
        self.scheduler = scheduler
        self.devices = devices
        self.provider = provider
        self._planning: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------ criar + planejar
    def create(self, req: RunCreate) -> RunSummary:
        unknown = [i for i in req.instance_ids if i not in self.devices.devices]
        if unknown:
            raise RunError("unknown_instance", f"Instância(s) desconhecida(s): {', '.join(unknown)}", 400)
        status = self.provider.status()
        if not status.configured:
            raise RunError("ai_not_configured", status.notice, 503)
        row, created = self.repo.create_run(req, simulated=self.provider.simulated)
        if created:
            self._spawn_planning(row["id"])
        return self.repo.run_summary(self.repo.run_row(row["id"]), deduplicated=not created)

    def _spawn_planning(self, run_id: str) -> None:
        if run_id in self._planning and not self._planning[run_id].done():
            return
        self._planning[run_id] = asyncio.create_task(self._plan(run_id), name=f"plan-{run_id}")

    def resume_planning_after_restart(self) -> None:
        for r in self.repo.db.query("SELECT id FROM runs WHERE status='planning'"):
            self.repo.bus.emit("log", f"Execução {r['id']}: planejamento interrompido pelo reinício; replanejando.",
                               level="warn", run_id=r["id"])
            self._spawn_planning(r["id"])

    async def _plan(self, run_id: str) -> None:
        repo = self.repo
        run = repo.run_row(run_id)
        assert run is not None
        ids: list[str] = loads(run["instance_ids"], [])
        instances = []
        for iid in ids:
            r = repo.db.one("SELECT app_id, account_label FROM instances WHERE id=?", (iid,))
            instances.append({"instance_id": iid, "account_label": r["account_label"] if r else None,
                              "app_id": r["app_id"] if r else None})
        apps = [AppContext(a["id"], a["name"], a["package"], a["activity"], a["nav_hints"], loads(a["known_selectors"]))
                for a in repo.db.query("SELECT * FROM apps ORDER BY name")]
        try:
            known = self.flows.match(run["command"]) if self.scheduler.cfg.file.ai.flows else None
            if known is not None:                      # comando repetido: o plano já existe, o planejador não é chamado
                flow, plan = known
                repo.db.execute("UPDATE runs SET flow_id=? WHERE id=?", (flow["id"], run_id))
                self.flows.used(flow["id"])
                repo.decision(f"Plano reaproveitado do fluxo “{flow['name']}” (sem chamada ao planejador)", run_id=run_id)
            else:
                async with self.scheduler.ai_limiter:
                    plan, usage = await self.provider.plan(PlanRequest(command=run["command"], run_id=run_id,
                                                                       instances=instances, apps=apps))
                repo.add_usage(run_id, None, usage)
        except AIError as exc:
            repo.set_run_status(run_id, RunStatus.failed, f"Planejamento falhou: {exc}", level="error")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("planejamento %s", run_id)
            repo.set_run_status(run_id, RunStatus.failed, f"Erro interno no planejamento: {exc}", level="error")
            return
        repo.save_plan(run_id, plan)
        repo.decision(f"Plano ({'SIMULADO' if plan.planner.simulated else plan.planner.model}): {plan.summary} — "
                      f"{len(plan.steps)} etapa(s): " + " → ".join(s.title for s in plan.steps), run_id=run_id)
        if plan.missing or not plan.steps:
            questions = " | ".join(m.question for m in plan.missing) or "O plano veio sem etapas."
            repo.set_run_status(run_id, RunStatus.needs_input, questions, level="warn",
                                message=f"Execução {run_id}: faltam informações — {questions}")
            return
        repo.materialize(run_id, plan, instances)      # persistido ANTES de executar
        run = repo.run_row(run_id)
        if run and run["cancel_requested"]:
            repo.set_run_status(run_id, RunStatus.cancelled, "Cancelada durante o planejamento")
            return
        if run and run["mode"] == "execute":
            self.start(run_id)
        else:
            repo.set_run_status(run_id, RunStatus.planned, "Plano pronto para inspeção",
                                message=f"Execução {run_id}: plano pronto; aguardando início")

    # ------------------------------------------------------------------ controles
    def _run(self, run_id: str) -> Any:
        run = self.repo.run_row(run_id)
        if run is None:
            raise RunError("not_found", "Execução não encontrada.", 404)
        return run

    def start(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        if run["status"] not in (RunStatus.planned.value, RunStatus.planning.value):
            raise RunError("invalid_state", f"A execução está em '{run['status']}' e não pode ser iniciada.")
        if not self.repo.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run_id,)):
            raise RunError("no_plan", "A execução ainda não tem plano materializado.")
        self.repo.set_run_status(run_id, RunStatus.running, None, message=f"Execução {run_id} iniciada")
        # com o rodízio ligado, aparelho parado não bloqueia: o scheduler o liga quando houver vaga
        ok_states = {InstanceState.online, InstanceState.booting}
        if self.scheduler.get_settings().auto_start_devices:
            ok_states |= {InstanceState.stopped, InstanceState.absent, InstanceState.stopping, InstanceState.hibernated}
        offline = [o for o in self.repo.db.query("SELECT * FROM objectives WHERE run_id=?", (run_id,))
                   if self.devices.devices[o["instance_id"]].state not in ok_states
                   or (self.devices.devices[o["instance_id"]].external
                       and self.devices.devices[o["instance_id"]].state != InstanceState.online)]
        for o in offline:
            self.repo.set_objective(o["id"], ObjectiveStatus.waiting_user, level="warn",
                                    detail="O aparelho não estava online no início da execução.",
                                    blocked_reason="Aparelho offline", needs="Inicie a instância e retome este item.")
        self.repo.recompute_run(run_id)
        self.scheduler.wake()
        return self.repo.run_summary(self._run(run_id))

    def pause(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        if run["status"] != RunStatus.running.value:
            raise RunError("invalid_state", "Só é possível pausar uma execução em andamento.")
        self.repo.db.execute("UPDATE runs SET pause_requested=1 WHERE id=?", (run_id,))
        self.repo.set_run_status(run_id, RunStatus.paused, "Pausada: nenhum novo despacho; ações em curso terminam no ponto seguro",
                                 message=f"Execução {run_id} pausada")
        return self.repo.run_summary(self._run(run_id))

    def resume(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        if run["status"] != RunStatus.paused.value:
            raise RunError("invalid_state", "A execução não está pausada.")
        self.repo.db.execute("UPDATE runs SET pause_requested=0 WHERE id=?", (run_id,))
        self.repo.set_run_status(run_id, RunStatus.running, None,
                                 message=f"Execução {run_id} retomada; cada aparelho reobserva a tela antes de agir")
        self.scheduler.wake()
        return self.repo.run_summary(self._run(run_id))

    def cancel(self, run_id: str) -> RunSummary:
        run = self._run(run_id)
        status = RunStatus(run["status"])
        if status in RUN_TERMINAL and status != RunStatus.completed_with_issues:
            raise RunError("invalid_state", "A execução já terminou.")
        self.repo.db.execute("UPDATE runs SET cancel_requested=1, pause_requested=0 WHERE id=?", (run_id,))
        if status in (RunStatus.planned, RunStatus.needs_input, RunStatus.planning):
            self.repo.cancel_open_steps(run_id, reason="execução cancelada")
            for o in self.repo.db.query("SELECT id FROM objectives WHERE run_id=?", (run_id,)):
                self.repo.set_objective(o["id"], ObjectiveStatus.cancelled, detail="Cancelado antes de iniciar.")
            self.repo.set_run_status(run_id, RunStatus.cancelled, "Cancelada antes de iniciar")
        else:
            self.repo.set_run_status(run_id, RunStatus.cancelling,
                                     "Cancelando: trabalho futuro interrompido; o que já foi feito permanece registrado",
                                     level="warn")
            self.scheduler.wake()
        return self.repo.run_summary(self._run(run_id))

    # ------------------------------------------------------------------ retomadas
    def _requeue(self, obj: Any, reason: str) -> None:
        run = self._run(obj["run_id"])
        steps = self.scheduler.recovery_steps(run, obj["id"])
        if not steps:
            raise RunError("nothing_to_retry", "Não há etapas pendentes para refazer neste item.")
        self.repo.revise_plan(obj["id"], reason, steps)
        self.repo.db.execute("UPDATE objectives SET started_at=NULL, finished_at=NULL WHERE id=?", (obj["id"],))
        self.repo.set_objective(obj["id"], ObjectiveStatus.pending, detail=reason,
                                message=f"{obj['instance_id']}: item retomado — {reason}")
        rt = self.devices.devices.get(obj["instance_id"])
        if rt:
            rt.attention = None
            self.devices.publish(rt)

    def retry_failed(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        if run["cancel_requested"]:
            raise RunError("invalid_state", "Execução cancelada não pode ser retomada.")
        retried, skipped = [], []
        for o in self.repo.db.query("SELECT * FROM objectives WHERE run_id=? ORDER BY instance_id", (run_id,)):
            st = o["status"]
            if st in ("failed", "waiting_user"):
                rt = self.devices.devices.get(o["instance_id"])
                startable = self.scheduler.get_settings().auto_start_devices and rt is not None and rt.state in (
                    InstanceState.stopped, InstanceState.absent, InstanceState.booting, InstanceState.stopping,
                    InstanceState.hibernated)
                if rt is None or (rt.state != InstanceState.online and not startable):
                    skipped.append({"objective_id": o["id"], "reason": "aparelho não está online"})
                    continue
                try:
                    self._requeue(o, "Retomado pelo usuário (itens elegíveis)")
                    retried.append(o["id"])
                except RunError as exc:
                    skipped.append({"objective_id": o["id"], "reason": exc.message})
            elif st == "uncertain":
                skipped.append({"objective_id": o["id"], "reason": "resultado incerto exige decisão individual "
                                                                   "(confirmar, repetir ou abandonar)"})
            elif st == "cancelled":
                skipped.append({"objective_id": o["id"], "reason": "cancelado"})
        if retried:
            self.repo.db.execute("UPDATE runs SET pause_requested=0, finished_at=NULL WHERE id=?", (run_id,))
            self.repo.recompute_run(run_id)
            self.scheduler.wake()
        return {"retried": retried, "skipped": skipped}

    def resolve(self, run_id: str, objective_id: str, body: ResolveBody) -> ObjectiveDTO:
        self._run(run_id)
        try:
            obj = self.repo.objective_row(objective_id)
        except KeyError:
            raise RunError("not_found", "Objetivo não encontrado.", 404) from None
        if obj["run_id"] != run_id or obj["status"] not in ("waiting_user", "uncertain", "failed"):
            raise RunError("invalid_state", "Este item não está aguardando decisão.")
        note = f" Nota: {body.note}" if body.note else ""
        if body.resolution == "abandon":
            self.repo.cancel_open_steps(run_id, objective_id=objective_id, reason="abandonado pelo usuário")
            self.repo.set_objective(objective_id, ObjectiveStatus.failed, detail="Abandonado pelo usuário." + note,
                                    blocked_reason=obj["blocked_reason"])
        elif body.resolution == "retry":
            self._requeue(obj, "Usuário decidiu repetir este item." + note)
        else:  # confirm_done — vale como decisão do usuário, não como comprovação automática
            blocking = self.repo.db.one(
                "SELECT * FROM steps WHERE objective_id=? AND plan_version=? AND status IN ('uncertain','waiting_user','failed')"
                " ORDER BY seq LIMIT 1", (objective_id, obj["plan_version"]))
            if blocking is None:
                raise RunError("invalid_state", "Não há etapa aguardando confirmação.")
            if blocking["status"] == "failed":
                raise RunError("invalid_state", "Uma etapa que falhou não pode ser confirmada; use repetir ou abandonar.")
            self.repo.transition_step(blocking["id"], StepStatus.succeeded, detail="Confirmado manualmente pelo usuário." + note,
                                      result=StepResult(verified=False, evidence_text="Confirmado manualmente pelo usuário." + note))
            self.repo.set_objective(objective_id, ObjectiveStatus.running,
                                    detail="Usuário confirmou a etapa; seguindo com as demais." + note)
            self.scheduler._maybe_complete(objective_id)  # noqa: SLF001
        self.repo.db.execute("UPDATE runs SET finished_at=NULL WHERE id=? AND cancel_requested=0", (run_id,))
        self.repo.recompute_run(run_id)
        self.scheduler.wake()
        rt = self.devices.devices.get(obj["instance_id"])
        if rt:
            rt.attention = None
            self.devices.publish(rt)
        return self.repo.objective_dto(self.repo.objective_row(objective_id))

    # ------------------------------------------------------------------ relatório
    def report(self, run_id: str) -> dict[str, Any]:
        detail = self.repo.run_detail(run_id)
        if detail is None:
            raise RunError("not_found", "Execução não encontrada.", 404)
        per_instance = []
        for o in detail.objectives:
            steps = [s for s in detail.steps if s.objective_id == o.id and s.plan_version == o.plan_version]
            manual = [s.title for s in detail.steps if s.objective_id == o.id and s.result and not s.result.verified]
            proven = [f"{s.title}: {s.result.evidence_text}" for s in detail.steps
                      if s.objective_id == o.id and s.status == StepStatus.succeeded and s.result and s.result.verified]
            per_instance.append({
                "instance_id": o.instance_id, "status": o.status.value, "detail": o.status_detail,
                "proven": o.status == ObjectiveStatus.succeeded and not manual, "delivery_level": o.delivery_level,
                "blocked_reason": o.blocked_reason, "needs": o.needs, "effects": o.effects,
                "proven_steps": proven, "manually_confirmed_steps": manual,
                "open_steps": [s.title for s in steps if s.status not in (StepStatus.succeeded,)],
                "plan_versions": o.plan_version, "ai_calls": o.ai_calls,
                "ai_tokens": o.ai_input_tokens + o.ai_output_tokens})
        totals = detail.counts.model_dump()
        untested = [p["instance_id"] for p in per_instance if p["status"] in ("pending", "cancelled")]
        md = [f"# Relatório da execução {detail.id}" + (" (MODO SIMULADO — sem uso de IA)" if detail.simulated else ""),
              "", f"Comando: {detail.command}", f"Estado: {detail.status.value} — {detail.status_detail or ''}",
              f"Instâncias solicitadas: {detail.instances_requested} · utilizadas: {detail.instances_used}", "",
              "| Instância | Resultado | Entrega | Detalhe |", "|---|---|---|---|"]
        label = {"succeeded": "SUCESSO comprovado", "failed": "FALHA", "waiting_user": "BLOQUEADO (aguarda usuário)",
                 "uncertain": "INCERTO (requer revisão)", "cancelled": "CANCELADO", "running": "em andamento",
                 "pending": "não iniciado"}
        for p in per_instance:
            res = label.get(p["status"], p["status"])
            if p["status"] == "succeeded" and p["manually_confirmed_steps"]:
                res = "SUCESSO com etapa confirmada manualmente"
            md.append(f"| {p['instance_id']} | {res} | {p['delivery_level'] or '—'} | "
                      f"{(p['blocked_reason'] or p['detail'] or '').replace('|', '/')} |")
        md += ["", "Somente itens com SUCESSO comprovado contam como concluídos. Itens bloqueados, incertos, "
                   "cancelados ou não iniciados NÃO contam como sucesso."]
        return {"run": RunSummary(**detail.model_dump(include=set(RunSummary.model_fields))).model_dump(mode="json"),
                "totals": totals, "per_instance": per_instance, "untested": untested, "markdown": "\n".join(md)}
