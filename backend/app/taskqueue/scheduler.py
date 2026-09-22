"""Scheduler assíncrono (único processo): um worker por aparelho, concorrência entre aparelhos,
serialização dentro de cada um. Uma instância lenta ou bloqueada não segura as demais.

Limites independentes: `max_active_devices` (workers simultâneos) e `max_ai_concurrency` (chamadas ao modelo).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from ..config import Config
from ..db import dumps, loads
from ..devices.manager import DeviceManager, DeviceRuntime, Limiter
from ..models import (ActionStatus, AttemptStatus, ControlOwner, DeliveryLevel, InstanceCurrent, InstanceState,
                      ObjectiveStatus, Plan, PlanStep, RunStatus, StepStatus)
from ..planning.provider import AIProvider, AppContext
from ..util import iso_in, now, now_iso, parse_iso
from .executor import Outcome, StepExecutor, StepOutcome
from .flows import FlowStore
from .foreach import expand
from .repository import MOTIVO_REJEICAO, RENOVAR_POSSE_S, Repository

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
        self._manual_since: dict[str, float] = {}
        self._posse_renovada = 0.0               # monotonic da última renovação de posse
        # Terceira porta do despacho (aparelho pronto, app pronto, sessão pronta). Preenchida pelo AppState:
        # o scheduler não conhece o domínio de perfil, só a forma da porta.
        self.session_gate: Callable[[DeviceRuntime], tuple[str, Callable[[], Any]] | None] | None = None       # aparelho → quando o usuário devolveu o controle
        # Resolvedor da porta do APP, no mesmo molde da de sessão: (aparelho, pacote, objetivo) → None quando não há
        # entrega pendente; `(motivo, trabalho)` quando dá para resolver instalando; `(motivo, None)` quando só uma
        # pessoa resolve. Injetado pelo AppState: o scheduler não conhece o domínio de release.
        self.app_resolver: Callable[[DeviceRuntime, str, Any], tuple[str, Callable[[], Any] | None] | None] | None = None
        # Entrega imediata ("instalar em todos agora"): [(aparelho, trabalho)] ainda por entregar. É uma SEGUNDA fonte
        # de demanda para o MESMO rodízio e o MESMO dono por aparelho — não um mecanismo paralelo. Injetado pelo AppState.
        self.rollout_source: Callable[[], list[tuple[str, Callable[[], Any]]]] | None = None
        # (objetivo, etapa, execução) → veredito de política/limite; None quando pode seguir. Injetado pelo AppState.
        # Assíncrona porque esta porta pode precisar ESCREVER o texto da etapa antes de liberá-la: a geração com a
        # persona do perfil é uma chamada de modelo. É o único ponto com o perfil resolvido e ainda nada digitado.
        self.policy_gate: Callable[[Any, Any, Any], Awaitable[Any]] | None = None
        # Execução saiu do ar (terminou ou foi cancelada): quem guarda estado POR execução limpa o seu aqui.
        self.on_run_settled: Callable[[str], Any] | None = None
        # Manutenção do worker: `None` quando aceita; senão a frase do motivo. Injetado pelo AppState a partir de
        # WorkerRegistry.aceita_trabalho — o scheduler não conhece o registro de workers, só a forma da porta.
        self.worker_gate: Callable[[str], str | None] | None = None
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
        self._manter_posse()
        for run in self.repo.active_runs():
            if run["cancel_requested"]:
                self._finish_cancel(run)
                continue
            if not run["pause_requested"]:
                self.repo.promote(run["id"])
        entrega = self.rollout_source() if self.rollout_source else []
        if s.auto_start_devices or entrega:
            # antes do despacho: o teto de workers não pode esconder quem espera vaga. Com o rodízio desligado, só a
            # entrega imediata — que uma pessoa pediu de propósito — liga aparelho; tarefa comum segue bloqueando.
            self._rotate(s, entrega=[iid for iid, _ in entrega], tarefas=s.auto_start_devices)
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
            if rt.store:
                # Defesa em profundidade: `RunService.create` já recusa a loja como alvo. Se um objetivo antigo apontar
                # para uma instância que DEPOIS virou loja, ele para aqui com o motivo, em vez de operar a Play Store.
                self._block(obj, "Este aparelho é a loja (Play Store): ele não executa tarefas.",
                            "Refaça a execução escolhendo um aparelho do parque.")
                continue
            if rt.state != InstanceState.online:
                waits = {InstanceState.booting} | (WAKEABLE | {InstanceState.stopping} if s.auto_start_devices else set())
                if rt.state not in waits or (rt.external and rt.state != InstanceState.booting):   # externo: ninguém o liga
                    self._block(obj, f"O aparelho não está online (estado: {rt.state.value}).",
                                "Inicie a instância e use “Tentar novamente” neste item.")
                continue
            porta_app = self._app_gate(obj, rt)
            if porta_app is not None:
                motivo_app, entrega = porta_app
                if entrega is None:
                    self._block(obj, motivo_app,
                                "Resolva o aplicativo deste aparelho (instalar ou verificar) e retome o item.")
                elif self.run_device_job(rt, entrega, label="entrega do aplicativo"):
                    self.repo.note_waiting(obj["id"], f"instalando o aplicativo antes da tarefa — {motivo_app}")
                continue                      # este tick é da instalação; a tarefa espera o app ficar pronto
            porta = self.session_gate(rt) if self.session_gate else None
            if porta is not None:
                motivo, trabalho = porta
                if trabalho is None:
                    # Só uma pessoa resolve (desafio de segurança, conta errada, credencial recusada).
                    self._block(obj, motivo, "Resolva a sessão deste perfil no painel e retome o item.")
                elif self.run_device_job(rt, trabalho, label="autenticação do Instagram"):
                    self.repo.note_waiting(obj["id"], f"verificando a sessão do Instagram — {motivo}")
                continue                      # este tick é do login; a tarefa espera a sessão ficar pronta
            if self._waits_for_pathfinder(obj, iid):
                continue
            if rt.worker_id and self.worker_gate:
                motivo_worker = self.worker_gate(rt.worker_id)
                if motivo_worker is not None:
                    # O worker está em manutenção (ou não conectado): o objetivo espera, sem virar waiting_user —
                    # ninguém decide nada, só aguarda a manutenção terminar.
                    self.repo.note_waiting(obj["id"], motivo_worker)
                    continue
            if not self.devices.ai_begin(rt):
                continue                        # usuário no controle ou chamada anterior ainda ocupando o aparelho
            self.workers[iid] = asyncio.create_task(self._work(obj["id"], rt), name=f"worker-{iid}")
        if entrega:
            # Aparelho com trabalho aberto recebe o app pela PORTA, antes do próximo objetivo pendente — nunca por aqui,
            # que poderia trocar o app no meio de um objetivo em andamento.
            com_trabalho = self.repo.instances_with_open_work()
            for iid, trabalho in entrega:
                if iid in self.workers or iid in com_trabalho or len(self.workers) >= s.max_active_devices:
                    continue
                rt = self.devices.devices.get(iid)
                if rt is not None and rt.state == InstanceState.online:
                    self.run_device_job(rt, trabalho, label="entrega do aplicativo")

    # ------------------------------------------------------------------ trabalho exclusivo fora do laço de etapas
    def run_device_job(self, rt: DeviceRuntime, factory: Callable[[], Any], *, label: str) -> bool:
        """Roda um trabalho que precisa do aparelho inteiro — instalar um APK, autenticar — com as MESMAS guardas do
        executor: registrado em `workers`, então o despacho não concorre, o rodízio não despeja e o encerramento
        cancela. Devolve False quando o aparelho já está ocupado."""
        if rt.id in self.workers:
            return False
        if rt.worker_id and self.worker_gate and self.worker_gate(rt.worker_id) is not None:
            # Mesma guarda do despacho de objetivos: instalação de app e autenticação também são trabalho, e um
            # worker em manutenção não recebe nada novo — nem isso.
            return False
        if not self.devices.ai_begin(rt):
            return False
        self.workers[rt.id] = asyncio.create_task(self._device_job(rt, factory, label), name=f"job-{rt.id}")
        return True

    async def _device_job(self, rt: DeviceRuntime, factory: Callable[[], Any], label: str) -> None:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - o trabalho reporta o próprio erro; aqui só não pode derrubar o laço
            log.exception("%s em %s", label, rt.id)
            self.repo.bus.emit("log", f"{rt.id}: {label} falhou — {exc}", level="error", instance_id=rt.id)
        finally:
            self.workers.pop(rt.id, None)
            self.devices.ai_end(rt)
            self.wake()

    def _app_gate(self, obj: Any, rt: DeviceRuntime) -> tuple[str, Callable[[], Any] | None] | None:
        """Segunda das três portas do despacho: aparelho pronto, **app pronto**, sessão pronta.

        A pergunta ao resolvedor vem primeiro, e só depois "tem linha?": sem linha em `device_app_state` mas com
        versão promovida do app daquele aparelho, o resolvedor adota a versão e a entrega acontece aqui. Sem
        release gerenciada nenhuma o resolvedor devolve `None` e o caminho antigo (app instalado à mão, como o de
        QA) segue valendo sem mudança.

        Devolve `None` quando pode despachar; `(motivo, trabalho)` quando há uma versão distribuída ainda por
        instalar neste aparelho — o trabalho instala e a tarefa espera; `(motivo, None)` quando só uma pessoa resolve.
        A entrega pendente é conferida ANTES de "está pronto": o app pronto pode ser justamente a versão antiga."""
        run = self.repo.run_row(obj["run_id"])
        if run is None:
            return None
        try:
            app, _ = self._app_context(run, rt)
        except KeyError:
            return None
        if not app.package:
            return None
        # A pergunta ao resolvedor vem ANTES de "tem linha?": um aparelho que entrou no parque depois da
        # distribuição não tem linha nenhuma, e era exatamente ele que recebia tarefa de um app que não está
        # instalado. Com versão promovida do app dele, o resolvedor adota a versão e entrega aqui.
        if self.app_resolver is not None:
            entrega = self.app_resolver(rt, app.package, obj)
            if entrega is not None:
                return entrega
        row = self.repo.db.one("SELECT state, detail FROM device_app_state WHERE instance_id=? AND package_name=?",
                               (rt.id, app.package))
        if row is None:
            return None
        if row["state"] in ("ready", "installed"):
            return None
        return (f"O aplicativo não está pronto neste aparelho (estado: {row['state']})." + (
            f" {row['detail']}" if row["detail"] else ""), None)

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
    def _rotate(self, s: Any, *, entrega: list[str] | None = None, tarefas: bool = True) -> None:
        """Liga aparelhos parados que têm tarefa na fila (FIFO) enquanto houver vaga; sem vaga, desliga UM aparelho
        ocioso por tick. `max_online_devices` é o contador de vagas; a guarda de RAM do boot continua valendo.

        `entrega` são aparelhos com instalação imediata pendente: contam como demanda do mesmo jeito (o item da fila
        deles é `None`), depois das tarefas. `tarefas=False` = rodízio desligado: só a entrega liga aparelho."""
        devs = self.devices
        now_m = time.monotonic()
        demand: list[tuple[DeviceRuntime, Any]] = []
        for obj in (self.repo.dispatchable_objectives() if tarefas else []):
            rt = devs.devices.get(obj["instance_id"])
            if (rt is not None and not rt.external and not rt.store and rt.state in WAKEABLE
                    and all(rt is not d for d, _ in demand)):
                demand.append((rt, obj))
        for iid in entrega or []:
            rt = devs.devices.get(iid)
            if (rt is not None and not rt.external and not rt.store and rt.state in WAKEABLE
                    and all(rt is not d for d, _ in demand)):
                demand.append((rt, None))
        free = s.max_online_devices - devs.slots_used()
        waiting: list[tuple[DeviceRuntime, Any]] = []
        for rt, obj in demand:
            porque = f"tarefa na fila ({obj['run_id'][-6:]})" if obj is not None else "entrega do aplicativo"
            if free > 0 and devs.request_start(rt, porque):
                free -= 1
            else:
                waiting.append((rt, obj))
        busy = self.repo.instances_with_open_work() if (waiting or s.idle_stop_s) else set()
        busy |= set(entrega or [])             # quem acabou de ligar para receber o app não cede a vaga antes de recebê-lo
        pinned = self.repo.instances_needing_user() if (waiting or s.idle_stop_s) else set()

        def evictable(d: DeviceRuntime, idle_for: float) -> bool:
            return (d.state == InstanceState.online and d.id not in self.workers and d.control == ControlOwner.none
                    and not d.external and not d.store          # a loja é desligada por quem a ligou, nunca pelo rodízio:
                    # o usuário digita na JANELA do emulador, e daqui não se vê foco nem controle — ela cairia no meio do login
                    and not d.takeover_requested and not d.focused and not d.executor.has_zombie
                    and d.id not in busy and d.id not in pinned
                    and now_m - d.online_since_mono >= s.min_online_dwell_s and now_m - d.last_activity_mono >= idle_for)

        # Aparelho em `stopping` é uma vaga JÁ prometida: todo desligamento termina em `stopped` ou `hibernated`, e
        # os dois liberam vaga. Mas `slots_used()` conta `stopping` como ocupado e `evictable` só aceita `online`,
        # então, sem descontar as paradas em voo, o tick seguinte (1 s depois) não enxerga a vaga a caminho, escolhe
        # OUTRA vítima, e o rodízio esvazia o parque inteiro para atender UM aparelho na fila — cada vítima pagando
        # snapshot na saída e boot na volta.
        em_voo = sum(1 for d in devs.devices.values()
                     if not d.external and not d.store and d.state == InstanceState.stopping)
        if waiting and free + em_voo < len(waiting):
            victims = sorted((d for d in devs.devices.values() if evictable(d, 0)), key=lambda d: d.last_activity_mono)
            if victims:
                devs.request_stop(victims[0], f"vaga para {waiting[0][0].id}")
            for rt, obj in waiting:
                why = ("aguardando vaga" if victims else "aguardando vaga — nenhum aparelho ligado pode ser desligado agora "
                       "(em uso, em foco no painel ou com item que precisa de você)")
                if obj is not None:
                    cedendo = f", {em_voo} cedendo a vaga" if em_voo else ""
                    self.repo.note_waiting(obj["id"],
                                           f"{why} ({devs.slots_used()}/{s.max_online_devices} ligados{cedendo})")
                # o cartão do aparelho desligado também mostra o motivo
                card = "tarefa na fila — aguardando vaga" if obj is not None else "entrega do aplicativo — aguardando vaga"
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
                n_items = sum(len(v) for v in (loads(obj["collected"], {}) or {}).values())
                parado = int(obj["paused_s"] or 0)        # tempo represado por limite não conta como demora
                if started and (now() - started).total_seconds() - parado > (
                        self.get_settings().objective_timeout_s + 240 * n_items):
                    self._fail_objective(obj, "Tempo total do objetivo esgotado.")
                    break
                porta = await self.policy_gate(obj, srow, run) if self.policy_gate else None
                if porta is not None:
                    # Antes de assumir a etapa: nenhuma tentativa consumida, nenhuma chamada de modelo gasta.
                    self._hold(obj, srow, porta)
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
            self._settle_run(run_id)
            self._learn_flow(run_id)
            self.wake()

    def _settle_run(self, run_id: str) -> None:
        """Execução terminou: solta o que era guardado só por causa dela."""
        run = self.repo.run_row(run_id)
        terminais = (RunStatus.completed.value, RunStatus.completed_with_issues.value, RunStatus.failed.value,
                     RunStatus.cancelled.value)
        if run is None or run["status"] not in terminais:
            return
        self._pathfinders.pop(run_id, None)
        if self.on_run_settled is not None:
            try:
                self.on_run_settled(run_id)
            except Exception:  # noqa: BLE001 - limpeza nunca derruba o fim da execução
                log.exception("limpeza de fim de execução %s", run_id)

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

    def _hold(self, obj: Any, srow: Any, veredito: Any) -> None:
        """Represa a etapa sem gastar tentativa: `retry_wait` com hora marcada, ou bloqueio para uma pessoa.

        O tempo de espera entra em `objectives.paused_s` na hora de represar, para o prazo do objetivo não correr
        contra quem está apenas respeitando o próprio limite.
        """
        if veredito.is_wait:
            espera = max(0, int(((parse_iso(veredito.retry_at) or now()) - now()).total_seconds()))
            self.repo.transition_step(srow["id"], StepStatus.retry_wait, detail=veredito.reason,
                                      next_retry_at=veredito.retry_at,
                                      message=f"Etapa '{srow['title']}': represada — {veredito.reason}")
            self.repo.db.execute("UPDATE objectives SET paused_s=paused_s+?, blocked_kind='limit' WHERE id=?",
                                 (espera, obj["id"]))
            self.repo.note_waiting(obj["id"], f"aguardando o limite do perfil — {veredito.reason}")
            return
        self.repo.db.execute("UPDATE objectives SET blocked_kind='policy' WHERE id=? AND blocked_kind IS DISTINCT FROM 'approval'",
                             (obj["id"],))
        self._block(obj, veredito.reason, veredito.hint or "Ajuste a política deste perfil e retome o item.")

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
            if out.items is not None:
                self._expand_for_each(obj, step, out.items)
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
        if out.plan_defect:                  # refazer o MESMO plano falharia igual (e custaria igual) em todo aparelho
            self._fail_objective(obj, f"Etapa '{step.title}': {detail}")
            self._hold_siblings(obj, step)
            return
        if not self._try_recover(obj, step, detail or "falha"):
            if not self._skip_failed_item(obj, step, detail or "falha"):
                self._fail_objective(obj, f"Etapa '{step.title}' falhou: {detail}")

    def _hold_siblings(self, obj: Any, step: Any) -> None:
        """Defeito do plano visto por um aparelho: os que ainda NÃO começaram não gastam IA para falhar igual."""
        reason = (f"Não iniciado: em {obj['instance_id']} a etapa '{step.title}' mostrou um defeito do plano "
                  "(pós-condição não comprovável pela tela).")
        for o in self.repo.db.query("SELECT * FROM objectives WHERE run_id=? AND id<>? AND status=?",
                                    (obj["run_id"], obj["id"], ObjectiveStatus.pending.value)):
            if o["instance_id"] not in self.workers:
                self._block(o, reason, "Refaça o comando de forma mais específica (ex.: alvos nomeados). "
                                       "“Tentar novamente” executa este item mesmo assim.")

    # ------------------------------------------------------------------ repetição sobre lista lida da tela
    def _collected(self, objective_id: str) -> dict[str, list[str]]:
        return loads(self.repo.objective_row(objective_id)["collected"], {}) or {}

    def _expand_for_each(self, obj: Any, step: Any, items: list[str]) -> None:
        """A coleta terminou: as etapas-modelo `for_each` viram uma cópia por item (nova versão do plano)."""
        repo = self.repo
        collected = {**self._collected(obj["id"]), step.key: items}
        repo.db.execute("UPDATE objectives SET collected=? WHERE id=?", (dumps(collected), obj["id"]))
        plan = Plan.model_validate_json(repo.run_row(obj["run_id"])["plan"])
        done = {r["key"] for r in repo.db.query("SELECT key FROM steps WHERE objective_id=? AND status='succeeded'",
                                                (obj["id"],))}
        steps = [s for s in expand(plan.steps, collected) if s.key not in done]
        if steps:
            repo.revise_plan(obj["id"], f"Expandido para {len(items)} item(ns) lidos em '{step.title}'", steps)

    def _skip_failed_item(self, obj: Any, step: Any, detail: str) -> bool:
        """Etapa de UM item falhou de vez (sem efeito disparado): pula só o resto DESTE item; os demais seguem."""
        item = (step.variables or {}).get("item")
        idx = (step.variables or {}).get("item_index")
        if not item or not idx:
            return False
        repo = self.repo
        for r in repo.db.query("SELECT id, variables FROM steps WHERE objective_id=? AND plan_version=? AND status IN "
                               "('pending','ready','retry_wait')", (obj["id"], step.plan_version)):
            if (loads(r["variables"], {}) or {}).get("item_index") == idx:
                repo.transition_step(r["id"], StepStatus.cancelled, detail=f"item “{item}” falhou antes desta etapa")
        repo.decision(f"{obj['instance_id']}: item “{item}” falhou em '{step.title}' ({detail}); os demais itens seguem",
                      run_id=obj["run_id"], instance_id=obj["instance_id"], step_id=step.id)
        return True

    def _settle_items(self, o: Any) -> bool:
        """Objetivo com itens: quando não resta nada executável, fecha a conta. Falha parcial NUNCA vira sucesso."""
        repo = self.repo
        rows = repo.db.query("SELECT id, key, status, depends_on, variables FROM steps WHERE objective_id=? AND plan_version=?",
                             (o["id"], o["plan_version"]))
        if not any(r["variables"] for r in rows) or any(r["status"] in (
                "ready", "running", "verifying", "retry_wait", "waiting_user", "uncertain") for r in rows):
            return False
        dead = {r["key"] for r in rows if r["status"] in ("failed", "cancelled")}
        if not dead:
            return False
        changed = True
        while changed:                                # quem dependia de item que falhou também não roda
            changed = False
            for r in rows:
                if r["status"] == "pending" and r["key"] not in dead and set(loads(r["depends_on"], [])) & dead:
                    repo.transition_step(r["id"], StepStatus.cancelled, detail="dependia de item que falhou")
                    dead.add(r["key"])
                    changed = True
        if any(r["status"] == "pending" and r["key"] not in dead for r in rows):
            return False                              # ainda há etapa que pode ser promovida
        # a conta é por ITEM e atravessa versões do plano (uma recuperação recomeça só com o que faltava)
        names: dict[str, str] = {}
        keys: dict[str, set[str]] = {}
        proven: set[str] = set()
        for r in repo.db.query("SELECT key, status, variables FROM steps WHERE objective_id=? AND variables IS NOT NULL",
                               (o["id"],)):
            v = loads(r["variables"], {}) or {}
            if v.get("item_index"):
                names[v["item_index"]] = v.get("item", "?")
                keys.setdefault(v["item_index"], set()).add(r["key"])
                if r["status"] == "succeeded":
                    proven.add(r["key"])
        by_item = {i: (names[i], keys[i] <= proven) for i in names}
        bad = [name for name, ok in by_item.values() if not ok]
        detail = (f"{len(by_item) - len(bad)} de {len(by_item)} itens concluídos e comprovados; falharam: "
                  + ", ".join(bad)[:300] + ". “Tentar novamente” refaz só os que falharam.")
        repo.set_objective(o["id"], ObjectiveStatus.failed, detail=detail, blocked_reason=detail, level="error")
        return True

    def _fail_objective(self, obj: Any, detail: str) -> None:
        # O motivo real, e não "etapa anterior falhou": o objetivo também morre por prazo total esgotado, e aí
        # nenhuma etapa falhou. Quem abre a etapa cancelada precisa ler o que de fato aconteceu.
        self.repo.cancel_open_steps(obj["run_id"], objective_id=obj["id"], reason=detail)
        self.repo.set_objective(obj["id"], ObjectiveStatus.failed, detail=detail, blocked_reason=detail, level="error")

    def _maybe_complete(self, objective_id: str) -> None:
        o = self.repo.objective_row(objective_id)
        if o["status"] not in (ObjectiveStatus.running.value, ObjectiveStatus.pending.value):
            return
        if self._settle_items(o):
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
        plan = plan.model_copy(update={"steps": expand(plan.steps, self._collected(objective_id))})
        by_key = {s.key: s for s in plan.steps}
        proven = {r["key"]: bool(r["side_effect"]) for r in self.repo.db.query(
            "SELECT key, side_effect FROM steps WHERE objective_id=? AND status='succeeded'", (objective_id,))}
        # Rejeitada é DECISÃO, não lacuna: recriar a chave reabriria, com OUTRO texto, uma aprovação que alguém já
        # recusou — e a recusa, que a tela promete ser definitiva, não sobreviveria à primeira falha de qualquer
        # outra etapa do mesmo objetivo.
        # Só a rejeição, e não `cancelled` em geral: `_skip_failed_item` também cancela (o resto de um item que
        # falhou) e essas TÊM de voltar, senão “Tentar novamente” deixa de refazer justamente os itens que
        # falharam, que é o que ele promete. As chaves de um bloco `for_each` já vêm com o sufixo do item
        # (`_i1`, `_i2`), então fechar a conta de um alvo nunca fecha a dos irmãos.
        decidido = {r["key"] for r in self.repo.db.query(
            "SELECT key FROM steps WHERE objective_id=? AND status='cancelled' AND status_detail LIKE ?",
            (objective_id, MOTIVO_REJEICAO + "%"))} - set(proven)
        needed: set[str] = set()

        def visit(key: str) -> None:
            if key in needed or key not in by_key or key in decidido:
                return
            if proven.get(key):            # efeito externo já comprovado: fronteira
                return
            needed.add(key)
            for dep in by_key[key].depends_on:
                visit(dep)

        for s in plan.steps:
            if s.key not in proven and s.key not in decidido:
                visit(s.key)
        return [s.model_copy(update={"depends_on": [d for d in s.depends_on if d in needed]})
                for s in plan.steps if s.key in needed]

    def _try_recover(self, obj: Any, step: Any, detail: str) -> bool:
        fired, _ = self.repo.commit_state(step.id)
        if step.side_effect and fired:
            return False
        o = self.repo.objective_row(obj["id"])
        recoveries = self.repo.db.scalar("SELECT COUNT(*) FROM plan_versions WHERE objective_id=? AND reason LIKE ?",
                                         (obj["id"], "Recuperação automática%"))
        if recoveries >= MAX_PLAN_REVISIONS:          # expandir um for_each não conta como recuperação
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

    def _manter_posse(self) -> None:
        """Renova a posse do que é meu e adota o que outro backend abandonou.

        As duas coisas no mesmo relógio porque são o mesmo assunto visto dos dois lados: enquanto eu renovo,
        ninguém me adota; quando eu paro, alguém me adota. O `_tick` roda a cada segundo, e isto não — uma escrita
        por segundo por aparelho ativo seria desperdício num lease de `POSSE_TTL_S`.
        """
        agora = time.monotonic()
        if agora - self._posse_renovada < RENOVAR_POSSE_S:
            return
        self._posse_renovada = agora
        self.repo.renew_claims()
        try:
            self.adotar_abandonadas()
        except Exception:  # noqa: BLE001 - adoção falha não pode derrubar o tick
            log.exception("falha ao adotar etapas abandonadas")

    # ------------------------------------------------------------------ reinício do backend
    def reconcile_after_restart(self) -> None:
        """Etapas que estavam em execução quando o backend caiu: nada é reexecutado às cegas.

        - ações `intended` viram `unknown` (podem ter chegado ao aparelho);
        - a tentativa é marcada `interrupted` e não consome tentativa;
        - a etapa volta para `ready`: o executor reobserva a tela; se for etapa com efeito já disparado,
          ele SÓ verifica (reconciliação) e, sem prova, marca `uncertain`.
        """
        rows = self.repo.interrupted_steps()
        self._reconciliar(rows, "backend reiniciado")
        if rows:
            self.repo.bus.emit("log", f"Backend reiniciado: {len(rows)} etapa(s) interrompida(s) serão reconciliadas"
                                      " pela tela.", level="warn")
        self.wake()

    def adotar_abandonadas(self) -> int:
        """Etapas de outro backend cujo lease venceu: ele caiu, e o trabalho não pode ficar parado para sempre.

        Deliberadamente NÃO é o mesmo caminho do reinício. Aqui a única prova de que o dono morreu é ele ter parado
        de renovar por mais de `POSSE_TTL_S`, então a etapa é adotada em nome próprio ANTES de ser reconciliada —
        sem isso, dois backends poderiam reconciliar a mesma etapa ao mesmo tempo.
        """
        rows = self.repo.abandoned_steps()
        if not rows:
            return 0
        # Só reconcilia o que ele GANHOU: com dois backends vivos disputando a etapa de um terceiro que morreu, o
        # `take_over` é que decide, e o perdedor não pode reconciliar por cima de quem ganhou.
        adotadas = [s for s in rows if self.repo.take_over(s)]
        if not adotadas:
            return 0
        self._reconciliar(adotadas, "o backend que executava esta etapa parou de responder")
        self.repo.bus.emit("log", f"{len(adotadas)} etapa(s) abandonada(s) por outro servidor foram adotadas e serão"
                                  " reconciliadas pela tela.", level="warn")
        return len(adotadas)

    def _reconciliar(self, rows: list[Any], causa: str) -> None:
        """Nada é reexecutado às cegas: a etapa volta para `ready` e o executor REOBSERVA antes de agir."""
        repo = self.repo
        for s in rows:
            with repo.db.tx():
                for a in repo.db.query(
                        "SELECT a.id, a.tool FROM actions a JOIN attempts t ON t.id=a.attempt_id"
                        " WHERE t.step_id=? AND a.status='intended'", (s["id"],)):
                    repo.db.execute("UPDATE actions SET status=?, effect_possible=1, error=?, done_at=? WHERE id=?",
                                    (ActionStatus.unknown.value, f"{causa}; resultado da ação desconhecido",
                                     now_iso(), a["id"]))
                repo.db.execute(
                    "UPDATE attempts SET status=?, finished_at=?, error=COALESCE(error, ?), recovery=? WHERE step_id=? AND status='running'",
                    (AttemptStatus.interrupted.value, now_iso(), f"Tentativa interrompida: {causa}",
                     "Reconciliar pelo estado real da tela antes de continuar", s["id"]))
                repo.refund_attempt(s["id"])
            fired, _ = repo.commit_state(s["id"])
            repo.transition_step(s["id"], StepStatus.ready, level="warn",
                                 detail=f"{causa}: " + ("efeito externo possivelmente disparado — só verificar"
                                                        if fired else "reobservar a tela e continuar"))
