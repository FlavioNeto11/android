"""Scheduler assíncrono (único processo): um worker por aparelho, concorrência entre aparelhos,
serialização dentro de cada um. Uma instância lenta ou bloqueada não segura as demais.

Limites independentes: `max_active_devices` (workers simultâneos) e `max_ai_concurrency` (chamadas ao modelo).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from ..config import Config
from ..db import loads
from ..devices.manager import DeviceManager, DeviceRuntime, Limiter
from ..models import (ActionStatus, AttemptStatus, ControlOwner, DeliveryLevel, InstanceCurrent, InstanceState,
                      ObjectiveStatus, Plan, PlanStep, RunStatus, StepStatus)
from ..planning.provider import AIProvider, AppContext
from ..util import iso_in, now, now_iso, parse_iso
from .executor import Outcome, StepExecutor, StepOutcome
from .flows import FlowStore
from .repository import Repository

log = logging.getLogger("poc.scheduler")
MAX_PLAN_REVISIONS = 1
# estados que o rodízio pode ligar sob demanda
WAKEABLE = {InstanceState.stopped, InstanceState.absent, InstanceState.hibernated}


class Scheduler:
    def __init__(self, cfg: Config, repo: Repository, devices: DeviceManager, provider: AIProvider,
                 settings_getter: Callable[[], Any]):
        self.cfg = cfg
        self.repo = repo
        self.devices = devices
        self.provider = provider
        self.get_settings = settings_getter
        self.ai_limiter = Limiter(settings_getter().max_ai_concurrency)
        self.executor = StepExecutor(cfg, repo, devices, provider, self.ai_limiter, settings_getter)
        self.workers: dict[str, asyncio.Task[None]] = {}
        self.flows = FlowStore(repo.db)
        self._pathfinders: dict[str, tuple[str, float]] = {}     # execução → (aparelho que está aprendendo, desde)
        self._restart_app: dict[str, str] = {}                   # aparelho → package a encerrar antes da próxima etapa
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._manual_since: dict[str, float] = {}       # aparelho → quando o usuário devolveu o controle
        devices.on_device_free = self.wake

    # ------------------------------------------------------------------ ciclo
    def wake(self) -> None:
        self._wake.set()

    async def start(self) -> None:
        self.reconcile_after_restart()
        self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for t in list(self.workers.values()):
            t.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - o scheduler nunca morre por um erro isolado
                log.exception("erro no tick do scheduler")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _tick(self) -> None:
        s = self.get_settings()
        self.ai_limiter.set_limit(s.max_ai_concurrency)
        self.devices.boot_limiter.set_limit(s.boot_parallelism)
        for run in self.repo.active_runs():
            if run["cancel_requested"]:
                self._finish_cancel(run)
                continue
            if not run["pause_requested"]:
                self.repo.promote(run["id"])
        if s.auto_start_devices:
            self._rotate(s)                     # antes do despacho: o teto de workers não pode esconder quem espera vaga
        taken: set[str] = set()
        for obj in self.repo.dispatchable_objectives():
            iid = obj["instance_id"]
            if iid in taken:
                continue
            taken.add(iid)                      # a execução mais antiga tem a vez naquele aparelho
            if iid in self.workers:
                continue
            if len(self.workers) >= s.max_active_devices:
                break
            try:
                rt = self.devices.get(iid)
            except KeyError:
                self._block(obj, "Instância não existe na configuração atual.", "Ajuste a configuração e retome o item.")
                continue
            if rt.state != InstanceState.online:
                waits = {InstanceState.booting} | (WAKEABLE | {InstanceState.stopping} if s.auto_start_devices else set())
                if rt.state not in waits or (rt.external and rt.state != InstanceState.booting):   # externo: ninguém o liga
                    self._block(obj, f"O aparelho não está online (estado: {rt.state.value}).",
                                "Inicie a instância e use “Tentar novamente” neste item.")
                continue
            if self._waits_for_pathfinder(obj, iid):
                continue
            if not self.devices.ai_begin(rt):
                continue                        # usuário no controle ou chamada anterior ainda ocupando o aparelho
            self.workers[iid] = asyncio.create_task(self._work(obj["id"], rt), name=f"worker-{iid}")

    def _waits_for_pathfinder(self, obj: Any, iid: str) -> bool:
        """Desbravador: numa execução com vários aparelhos, o primeiro aprende as receitas e os demais esperam por
        ele (até `ai.pathfinder_wait_s`) para repetir sem IA. Só vale para quem ainda não começou."""
        ai = self.cfg.file.ai
        if not ai.pathfinder_wait_s or ai.recipes != "replay" or obj["status"] != ObjectiveStatus.pending.value:
            return False
        lead = self._pathfinders.get(obj["run_id"])
        if lead is None:
            self._pathfinders[obj["run_id"]] = (iid, time.monotonic())
            return False
        return lead[0] != iid and lead[0] in self.workers and time.monotonic() - lead[1] < ai.pathfinder_wait_s

    # ------------------------------------------------------------------ rodízio: N contas sobre K vagas de RAM
    def _rotate(self, s: Any) -> None:
        """Liga aparelhos parados que têm tarefa na fila (FIFO) enquanto houver vaga; sem vaga, desliga UM aparelho
        ocioso por tick. `max_online_devices` é o contador de vagas; a guarda de RAM do boot continua valendo."""
        devs = self.devices
        now_m = time.monotonic()
        demand: list[tuple[DeviceRuntime, Any]] = []
        for obj in self.repo.dispatchable_objectives():
            rt = devs.devices.get(obj["instance_id"])
            if rt is not None and not rt.external and rt.state in WAKEABLE and all(rt is not d for d, _ in demand):
                demand.append((rt, obj))
        free = s.max_online_devices - devs.slots_used()
        waiting: list[tuple[DeviceRuntime, Any]] = []
        for rt, obj in demand:
            if free > 0 and devs.request_start(rt, f"tarefa na fila ({obj['run_id'][-6:]})"):
                free -= 1
            else:
                waiting.append((rt, obj))
        busy = self.repo.instances_with_open_work() if (waiting or s.idle_stop_s) else set()
        pinned = self.repo.instances_needing_user() if (waiting or s.idle_stop_s) else set()

        def evictable(d: DeviceRuntime, idle_for: float) -> bool:
            return (d.state == InstanceState.online and d.id not in self.workers and d.control == ControlOwner.none
                    and not d.external and not d.takeover_requested and not d.focused and not d.executor.has_zombie
                    and d.id not in busy and d.id not in pinned
                    and now_m - d.online_since_mono >= s.min_online_dwell_s and now_m - d.last_activity_mono >= idle_for)

        if waiting and free <= 0:
            victims = sorted((d for d in devs.devices.values() if evictable(d, 0)), key=lambda d: d.last_activity_mono)
            if victims:
                devs.request_stop(victims[0], f"vaga para {waiting[0][0].id}")
            for rt, obj in waiting:
                why = ("aguardando vaga" if victims else "aguardando vaga — nenhum aparelho ligado pode ser desligado agora "
                       "(em uso, em foco no painel ou com item que precisa de você)")
                self.repo.note_waiting(obj["id"], f"{why} ({devs.slots_used()}/{s.max_online_devices} ligados)")
                card = "tarefa na fila — aguardando vaga"           # o cartão do aparelho desligado também mostra o motivo
                if rt.state in WAKEABLE and rt.state_detail != card:
                    rt.state_detail = card
                    devs.publish(rt)
        elif s.idle_stop_s and not waiting:
            idle = [d for d in devs.devices.values() if evictable(d, float(s.idle_stop_s))]
            if idle:
                devs.request_stop(min(idle, key=lambda d: d.last_activity_mono), f"ocioso há mais de {s.idle_stop_s}s")

    def _block(self, obj: Any, reason: str, needs: str) -> None:
        self.repo.set_objective(obj["id"], ObjectiveStatus.waiting_user, detail=reason, blocked_reason=reason, needs=needs,
                                level="warn")
        self.repo.recompute_run(obj["run_id"])

    # ------------------------------------------------------------------ worker por aparelho
    def _stop_reason(self, run_id: str, rt: DeviceRuntime) -> str | None:
        run = self.repo.run_row(run_id)
        if run is None or run["cancel_requested"]:
            return "cancel"
        if run["pause_requested"]:
            return "pause"
        if rt.takeover_requested:
            return "takeover"
        return None

    async def _work(self, objective_id: str, rt: DeviceRuntime) -> None:
        repo = self.repo
        obj = repo.objective_row(objective_id)
        run_id = obj["run_id"]
        resumed = self._manual_since.pop(rt.id, None) is not None   # o usuário controlou este aparelho há pouco
        try:
            while True:
                obj = repo.objective_row(objective_id)
                run = repo.run_row(run_id)
                if run is None or self._stop_reason(run_id, rt):
                    break
                repo.promote(run_id)
                srow = repo.next_ready_step(objective_id)
                if srow is None:
                    break
                pkg = self._restart_app.pop(rt.id, None)
                if pkg:                                   # plano revisado: recomeça com o app fechado
                    await self.devices.force_stop_app(rt, pkg)
                started = parse_iso(obj["started_at"])
                if started and (now() - started).total_seconds() > self.get_settings().objective_timeout_s:
                    self._fail_objective(obj, "Tempo total do objetivo esgotado.")
                    break
                attempt = repo.claim_step(srow["id"])
                if attempt is None:
                    break
                if obj["status"] != ObjectiveStatus.running.value:
                    repo.set_objective(objective_id, ObjectiveStatus.running,
                                       message=f"{rt.id}: objetivo em execução")
                    repo.recompute_run(run_id)
                step = repo.step_dto(repo.step_row(srow["id"]))
                self._publish_current(rt, obj, step.id)
                outcome = await self._run_guarded(run, obj, step, attempt["id"], rt, resumed)
                resumed = False
                self._apply(outcome, obj, step, attempt["id"], rt)
                self._publish_current(rt, obj, step.id)
                if outcome.outcome != Outcome.succeeded:
                    break
            self._maybe_complete(objective_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("worker %s", rt.id)
        finally:
            self.workers.pop(rt.id, None)
            rt.current = None
            if rt.takeover_requested:
                self._manual_since[rt.id] = time.monotonic()
            self.devices.ai_end(rt)
            repo.recompute_run(run_id)
            self._learn_flow(run_id)
            self.wake()

    def _learn_flow(self, run_id: str) -> None:
        """Execução terminou com TODOS comprovados → o comando vira um fluxo reaproveitável (plano congelado)."""
        if not self.cfg.file.ai.flows:
            return
        run = self.repo.run_row(run_id)
        if run is None or run["status"] != RunStatus.completed.value:
            return
        self._pathfinders.pop(run_id, None)
        try:
            flow_id = self.flows.learn_from_run(run)
        except Exception:  # noqa: BLE001 - otimização: nunca afeta o resultado da execução
            log.exception("aprender fluxo de %s", run_id)
            return
        if flow_id:
            self.repo.decision(f"Fluxo “{flow_id}” salvo: comandos iguais (com outros valores) reaproveitam este plano "
                               "sem chamar o planejador", run_id=run_id)

    async def _run_guarded(self, run: Any, obj: Any, step: Any, attempt_id: str, rt: DeviceRuntime,
                           resumed: bool) -> StepOutcome:
        app, account = self._app_context(run, rt)
        later = [r["title"] for r in self.repo.db.query(
            "SELECT title FROM steps WHERE objective_id=? AND plan_version=? AND seq>? ORDER BY seq",
            (obj["id"], step.plan_version, step.seq))]
        try:
            return await self.executor.run_step(run=run, objective=obj, step=step, attempt_id=attempt_id, rt=rt, app=app,
                                                account_label=account, remaining=later,
                                                stop_reason=lambda: self._stop_reason(run["id"], rt),
                                                resumed_after_manual=resumed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - erro inesperado: nunca conta como sucesso
            log.exception("etapa %s", step.id)
            fired, _ = self.repo.commit_state(step.id)
            return StepOutcome(Outcome.uncertain if (step.side_effect and fired) else Outcome.failed,
                               f"Erro interno ao executar a etapa: {type(exc).__name__}: {exc}")

    def _app_context(self, run: Any, rt: DeviceRuntime) -> tuple[AppContext, str | None]:
        plan = Plan.model_validate_json(run["plan"]) if run["plan"] else None
        inst = self.repo.db.one("SELECT app_id, account_label FROM instances WHERE id=?", (rt.id,))
        app_id = (plan.app_id if plan else None) or (inst["app_id"] if inst else None)
        row = self.repo.db.one("SELECT * FROM apps WHERE id=?", (app_id,)) if app_id else None
        if row is None:
            return AppContext(None, None, plan.app_package if plan else None, None, None, None), inst["account_label"] if inst else None
        return (AppContext(row["id"], row["name"], row["package"], row["activity"], row["nav_hints"],
                           loads(row["known_selectors"])), inst["account_label"] if inst else None)

    def _publish_current(self, rt: DeviceRuntime, obj: Any, step_id: str | None) -> None:
        o = self.repo.objective_row(obj["id"])
        dto = self.repo.objective_dto(o)
        s = self.repo.step_row(step_id) if step_id else None
        rt.current = InstanceCurrent(run_id=o["run_id"], objective_id=o["id"], objective_status=dto.status,
                                     step_id=s["id"] if s else None, step_title=s["title"] if s else None,
                                     step_status=StepStatus(s["status"]) if s else None,
                                     steps_done=dto.steps_done, steps_total=dto.steps_total)
        self.devices.publish(rt, f"{rt.id}: {s['title'] if s else 'objetivo'}")

    # ------------------------------------------------------------------ aplicar o resultado da etapa
    def _apply(self, out: StepOutcome, obj: Any, step: Any, attempt_id: str, rt: DeviceRuntime) -> None:
        repo = self.repo
        oid, detail = obj["id"], out.detail
        o = out.outcome
        if o == Outcome.succeeded:
            if out.delivery_level:
                repo.db.execute("UPDATE objectives SET delivery_level=? WHERE id=?", (out.delivery_level.value, oid))
            repo.emit_objective(oid)             # progresso ao vivo no painel
            return
        if o == Outcome.yielded:
            repo.refund_attempt(step.id)
            repo.finish_attempt(attempt_id, AttemptStatus.interrupted,
                                recovery={"pause": "Pausado pelo usuário num ponto seguro",
                                          "takeover": "Usuário assumiu o controle; a etapa será reobservada ao retomar"}
                                .get(detail or "", detail))
            repo.transition_step(step.id, StepStatus.ready, detail=f"interrompida ({detail}); será reobservada")
            return
        if o == Outcome.cancelled:
            repo.finish_attempt(attempt_id, AttemptStatus.cancelled, error="Cancelado pelo usuário")
            repo.transition_step(step.id, StepStatus.cancelled, detail="cancelada pelo usuário")
            return
        if o == Outcome.retry:
            repo.finish_attempt(attempt_id, AttemptStatus.failed, error=detail,
                                recovery=f"Nova tentativa automática (ação segura) em {self.get_settings().retry_backoff_s}s")
            repo.transition_step(step.id, StepStatus.retry_wait, detail=detail,
                                 next_retry_at=iso_in(self.get_settings().retry_backoff_s), level="warn")
            return
        if o == Outcome.waiting_user:
            repo.refund_attempt(step.id)
            repo.finish_attempt(attempt_id, AttemptStatus.interrupted, error=detail, recovery="Aguardando o usuário")
            repo.transition_step(step.id, StepStatus.waiting_user, detail=detail, level="warn")
            repo.set_objective(oid, ObjectiveStatus.waiting_user, detail=detail, blocked_reason=detail, needs=out.needs,
                               level="warn", message=f"{rt.id}: bloqueado — {detail}")
            rt.attention = f"Bloqueado: {detail}"
            return
        if o == Outcome.uncertain:
            repo.finish_attempt(attempt_id, AttemptStatus.uncertain, error=detail,
                                recovery="Reconciliação pela tela não comprovou o resultado; sem reenvio automático")
            repo.transition_step(step.id, StepStatus.uncertain, detail=detail, level="warn")
            repo.set_objective(oid, ObjectiveStatus.uncertain, detail=detail, blocked_reason=detail,
                               needs="Confira no aparelho se o efeito ocorreu e decida: confirmar, repetir ou abandonar. "
                                     "Nada será reenviado automaticamente.",
                               delivery_level=out.delivery_level, level="warn", message=f"{rt.id}: resultado INCERTO — {detail}")
            rt.attention = "Resultado incerto: requer revisão"
            return
        if o == Outcome.device_stuck:
            fired, _ = repo.commit_state(step.id)
            repo.finish_attempt(attempt_id, AttemptStatus.uncertain if fired else AttemptStatus.failed, error=detail)
            repo.transition_step(step.id, StepStatus.uncertain if (step.side_effect and fired) else StepStatus.failed,
                                 detail=detail, level="error")
            repo.set_objective(oid, ObjectiveStatus.uncertain if (step.side_effect and fired) else ObjectiveStatus.waiting_user,
                               detail=detail, blocked_reason=detail, needs=out.needs, level="error")
            rt.attention = "Aparelho retido: chamada anterior ainda não terminou"
            return
        # failed
        repo.finish_attempt(attempt_id, AttemptStatus.failed, error=detail)
        repo.transition_step(step.id, StepStatus.failed, detail=detail, level="error")
        if not self._try_recover(obj, step, detail or "falha"):
            self._fail_objective(obj, f"Etapa '{step.title}' falhou: {detail}")

    def _fail_objective(self, obj: Any, detail: str) -> None:
        self.repo.cancel_open_steps(obj["run_id"], objective_id=obj["id"], reason="etapa anterior falhou")
        self.repo.set_objective(obj["id"], ObjectiveStatus.failed, detail=detail, blocked_reason=detail, level="error")

    def _maybe_complete(self, objective_id: str) -> None:
        o = self.repo.objective_row(objective_id)
        if o["status"] not in (ObjectiveStatus.running.value, ObjectiveStatus.pending.value):
            return
        done, total = self.repo._step_progress(objective_id, o["plan_version"])  # noqa: SLF001
        if total and done == total:
            unverified = self.repo.db.scalar(
                "SELECT COUNT(*) FROM steps WHERE objective_id=? AND status='succeeded' AND result LIKE '%\"verified\":false%'",
                (objective_id,))
            detail = ("Todas as etapas concluídas" + (f" ({unverified} confirmada(s) manualmente pelo usuário)" if unverified
                                                      else " e comprovadas por observação da tela"))
            self.repo.set_objective(objective_id, ObjectiveStatus.succeeded, detail=detail,
                                    delivery_level=DeliveryLevel(o["delivery_level"]) if o["delivery_level"] else None,
                                    message=f"{o['instance_id']}: objetivo concluído — {detail}")

    # ------------------------------------------------------------------ recuperação / revisão de plano
    def recovery_steps(self, run: Any, objective_id: str) -> list[PlanStep]:
        """Etapas ainda não comprovadas + as dependências de navegação necessárias para refazê-las.
        Nunca atravessa (nem repete) uma etapa com efeito externo já comprovada."""
        plan = Plan.model_validate_json(run["plan"])
        by_key = {s.key: s for s in plan.steps}
        proven = {r["key"]: bool(r["side_effect"]) for r in self.repo.db.query(
            "SELECT key, side_effect FROM steps WHERE objective_id=? AND status='succeeded'", (objective_id,))}
        needed: set[str] = set()

        def visit(key: str) -> None:
            if key in needed or key not in by_key:
                return
            if proven.get(key):            # efeito externo já comprovado: fronteira
                return
            needed.add(key)
            for dep in by_key[key].depends_on:
                visit(dep)

        for s in plan.steps:
            if s.key not in proven:
                visit(s.key)
        return [s.model_copy(update={"depends_on": [d for d in s.depends_on if d in needed]})
                for s in plan.steps if s.key in needed]

    def _try_recover(self, obj: Any, step: Any, detail: str) -> bool:
        fired, _ = self.repo.commit_state(step.id)
        if step.side_effect and fired:
            return False
        o = self.repo.objective_row(obj["id"])
        if o["plan_version"] > MAX_PLAN_REVISIONS:
            return False
        run = self.repo.run_row(obj["run_id"])
        steps = self.recovery_steps(run, obj["id"])
        if not steps:
            return False
        reason = f"Recuperação automática após falha em '{step.title}': {detail}"
        self.repo.revise_plan(obj["id"], reason, steps)
        try:
            app, _ = self._app_context(run, self.devices.get(obj["instance_id"]))
            if app.package:
                self._restart_app[obj["instance_id"]] = app.package
        except KeyError:
            pass
        self.repo.decision(f"{obj['instance_id']}: {reason}. Refazendo a navegação a partir de um estado conhecido; "
                           "etapas com efeito externo já comprovadas não serão repetidas.",
                           run_id=obj["run_id"], instance_id=obj["instance_id"])
        return True

    # ------------------------------------------------------------------ cancelamento
    def _finish_cancel(self, run: Any) -> None:
        run_id = run["id"]
        for o in self.repo.db.query("SELECT * FROM objectives WHERE run_id=? AND status IN ('pending','running','waiting_user')",
                                    (run_id,)):
            if o["instance_id"] in self.workers and o["status"] == "running":
                continue                    # o worker cancela no próximo ponto seguro
            self.repo.cancel_open_steps(run_id, objective_id=o["id"], reason="execução cancelada")
            effects = loads(o["effects"], [])
            self.repo.set_objective(o["id"], ObjectiveStatus.cancelled,
                                    detail="Cancelado. " + (f"Ações com efeito externo já realizadas: {len(effects)}."
                                                            if effects else "Nenhuma ação com efeito externo foi realizada."))
        self.repo.recompute_run(run_id)

    # ------------------------------------------------------------------ reinício do backend
    def reconcile_after_restart(self) -> None:
        """Etapas que estavam em execução quando o backend caiu: nada é reexecutado às cegas.

        - ações `intended` viram `unknown` (podem ter chegado ao aparelho);
        - a tentativa é marcada `interrupted` e não consome tentativa;
        - a etapa volta para `ready`: o executor reobserva a tela; se for etapa com efeito já disparado,
          ele SÓ verifica (reconciliação) e, sem prova, marca `uncertain`.
        """
        repo = self.repo
        rows = repo.interrupted_steps()
        for s in rows:
            with repo.db.tx():
                for a in repo.db.query(
                        "SELECT a.id, a.tool FROM actions a JOIN attempts t ON t.id=a.attempt_id"
                        " WHERE t.step_id=? AND a.status='intended'", (s["id"],)):
                    repo.db.execute("UPDATE actions SET status=?, effect_possible=1, error=?, done_at=? WHERE id=?",
                                    (ActionStatus.unknown.value, "backend reiniciou durante a ação; resultado desconhecido",
                                     now_iso(), a["id"]))
                repo.db.execute(
                    "UPDATE attempts SET status=?, finished_at=?, error=COALESCE(error, ?), recovery=? WHERE step_id=? AND status='running'",
                    (AttemptStatus.interrupted.value, now_iso(), "Backend reiniciado durante a tentativa",
                     "Reconciliar pelo estado real da tela antes de continuar", s["id"]))
                repo.refund_attempt(s["id"])
            fired, _ = repo.commit_state(s["id"])
            repo.transition_step(s["id"], StepStatus.ready, level="warn",
                                 detail="backend reiniciado: " + ("efeito externo possivelmente disparado — só verificar"
                                                                 if fired else "reobservar a tela e continuar"))
        if rows:
            repo.bus.emit("log", f"Backend reiniciado: {len(rows)} etapa(s) interrompida(s) serão reconciliadas pela tela.",
                          level="warn")
        self.wake()
