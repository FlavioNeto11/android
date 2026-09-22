"""Gerência das instâncias: ciclo de vida do emulador, captura de frames, sessão de automação,
lease de controle (IA × usuário) e entradas manuais — tudo coordenado pelo executor exclusivo."""
from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import psutil
from PIL import Image

from ..automation.appium_driver import AndroidDeviceIO, AppiumSession
from ..automation.appium_server import AppiumServer
from ..automation.driver import DeviceIO, DriverError
from ..automation.hierarchy import UiTree, parse_hierarchy
from ..config import Config
from ..db import Database, dumps
from ..events import EventBus
from ..models import (AutomationInfo, ControlOwner, EmulatorMetric, FrameInfo, InstanceCurrent, InstanceDTO,
                      InstancePorts, InstanceResources, InstanceState, ManualInput, Metrics)
from ..util import new_token, now_iso
from . import emulator as emu
from .adb import Adb, AdbError
from .avd import AvdError, AvdManager
from .executor import DeviceExecutor
from .installer import LAUNCH_DEADLINE_S, wait_for_focus
from .verbs import verbos_suportados
from .sdk import SdkTools

log = logging.getLogger("poc.devices")

THUMB_WIDTH = 360
MANUAL_LEASE_TTL_S = 600

#: O estado que cada verbo de ciclo de vida PROMETE quando termina bem. Uma tabela só, com duas perguntas em
#: cima dela: o caminho do worker aplica no central o mesmo efeito do caminho local (`aplicar_desfecho_remoto`),
#: e a reconciliação de comando `uncertain` pergunta ao estado real se aquilo aconteceu. Se fossem duas tabelas,
#: "verificado pelo estado real" e "aplicado depois do worker" poderiam discordar sobre o que é sucesso.
ESTADO_ALVO: dict[str, InstanceState] = {
    "start": InstanceState.online,
    "wake": InstanceState.online,
    "restart": InstanceState.online,
    "reset": InstanceState.online,
    "stop": InstanceState.stopped,
    "hibernate": InstanceState.hibernated,
    "create": InstanceState.stopped,
}
#: A DECISÃO que cada verbo registra sobre o aparelho, que sobrevive a reinício e manda no monitor. `create` não
#: decide nada sobre estar no ar; teclas e app não mexem no ciclo de vida.
DESEJO_DO_VERBO: dict[str, str] = {
    "start": InstanceState.online.value, "wake": InstanceState.online.value,
    "restart": InstanceState.online.value, "reset": InstanceState.online.value,
    "stop": InstanceState.stopped.value, "hibernate": InstanceState.stopped.value,
}


def sem_snapshot(porque: str) -> str:
    """A frase única de "hibernar não hibernou", nos dois caminhos (aqui e no agente do worker).

    Regra única do achado #155: pedir "Hibernar" e receber um desligamento comum NÃO é sucesso. O aparelho de
    fato desligou — então também não é recusa —, mas o próximo boot será a frio, que é exatamente o que a
    hibernação existia para evitar. O comando vira `failed` com este motivo, e o painel nunca diz "Hibernada".
    """
    return f"desligado sem snapshot: {porque}; o próximo boot será a frio"


class ControlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class InstanceBusy(Exception):
    pass


@dataclass(slots=True)
class Frame:
    info: FrameInfo
    mono: float
    jpeg_full: bytes
    jpeg_thumb: bytes


@dataclass(slots=True)
class Observation:
    frame_id: str
    ts: str
    width: int
    height: int
    jpeg: bytes | None          # None quando a tela é sensível (campo de senha)
    tree: UiTree
    package: str | None
    sensitive: bool


class Limiter:
    """Semáforo redimensionável em tempo de execução. **O alcance é este processo**, e isso não é igual nos dois usos.

    - `boot_parallelism` (ligar emulador): por processo está **certo**. O recurso protegido é a RAM e a CPU DESTA
      máquina. Torná-lo global seria o erro oposto — duas máquinas de 64 GB esperando uma pela outra para ligar
      aparelho.
    - `max_ai_concurrency` (chamadas ao modelo): por processo está **errado** assim que existir um segundo backend.
      O recurso protegido é orçamento, que é global: limite 3 em dois backends viram seis chamadas simultâneas.
      Consertar exige lease no banco, no mesmo molde da posse de etapa (`claimed_by`). Ainda não feito, e
      registrado em `docs/banco.md`.
    """

    def __init__(self, limit: int):
        self._limit = max(1, limit)
        self._active = 0
        self._cond = asyncio.Condition()

    def set_limit(self, limit: int) -> None:
        self._limit = max(1, limit)

    @property
    def active(self) -> int:
        return self._active

    async def __aenter__(self) -> "Limiter":
        async with self._cond:
            await self._cond.wait_for(lambda: self._active < self._limit)
            self._active += 1
        return self

    async def __aexit__(self, *exc: Any) -> None:
        async with self._cond:
            self._active -= 1
            self._cond.notify_all()


class DeviceRuntime:
    def __init__(self, cfg: Config, tools: SdkTools, row: Any, io_factory: Callable[["DeviceRuntime"], DeviceIO] | None):
        self.cfg = cfg
        self.id: str = row["id"]
        self.index: int = row["idx"]
        self.avd_name: str = row["avd_name"]
        self.console_port: int = row["console_port"]
        ext = (cfg.file.instances.external.get(self.id) or "").strip()
        if ext and not re.match(r"^[A-Za-z0-9_.:\-]{3,80}$", ext):
            raise ValueError(f"instances.external.{self.id}: serial ADB inválido")
        self.external = bool(ext)
        # Aparelho-loja: o inverso do externo. O ciclo de vida é nosso (liga, desliga), mas ele NUNCA recebe tarefa,
        # nunca é despejado pelo rodízio e não abre sessão de automação. Quem decide qualquer uma dessas coisas lê daqui.
        self.store = cfg.store_id == self.id
        self.serial = ext or f"emulator-{self.console_port}"
        self.ports = InstancePorts(system=row["system_port"], mjpeg=row["mjpeg_port"],
                                   chromedriver=row["chromedriver_port"])
        self.pid: int | None = row["emulator_pid"]
        self.boot_seconds: float | None = row["boot_seconds"]
        self.state = InstanceState.stopped
        self.state_detail: str | None = None
        # Estado DESEJADO, separado do observado. Nulo = nenhuma decisão registrada. É o que distingue "caiu
        # sozinho, reconecte" de "alguém mandou parar, deixe parado" — sem ele o monitor desfazia o "Parar".
        self.desired_state: str | None = row["desired_state"] if "desired_state" in row.keys() else None
        # Máquina que hospeda este aparelho. Nulo = esta (o worker local).
        self.worker_id: str | None = row["worker_id"] if "worker_id" in row.keys() else None
        # Verbos que o worker declarou saber executar neste aparelho. Preenchido quando o worker conecta; é o que
        # faz um aparelho de outra máquina ganhar ciclo de vida de verdade.
        self.worker_verbs: list[str] | None = None
        self.executor = DeviceExecutor(self.id)
        self.adb = Adb(tools, self.serial)
        self.session = AppiumSession(cfg.file.appium, self.serial, self.ports.system, self.ports.mjpeg,
                                     self.ports.chromedriver)
        self.io: DeviceIO = io_factory(self) if io_factory else AndroidDeviceIO(self.adb, self.session)
        self.automation = AutomationInfo()
        self.automation_retry_mono: float = time.monotonic()
        # controle
        self.control = ControlOwner.none
        self.control_since: str | None = None
        self.lease_id: str | None = None
        self.lease_expires_mono: float = 0
        self.takeover_requested = False
        self.pending_lease_id: str | None = None
        # frames
        self.frame: Frame | None = None
        self.frame_seq = 0
        self.recent_frames: OrderedDict[str, tuple[float, int, int]] = OrderedDict()
        self.focus_until_mono: float = 0
        self.capture_now = asyncio.Event()
        # diversos
        self.attention: str | None = None
        self.current: InstanceCurrent | None = None
        self.resources: InstanceResources | None = None
        self.wipe_next_boot = False
        # rodízio (ligar sob demanda / ceder vaga)
        self.online_since_mono: float = time.monotonic()
        self.last_activity_mono: float = time.monotonic()
        self.start_backoff_until: float = 0.0
        self.start_refusals = 0
        self.app_versions: dict[str, str] = {}
        self.ui_variant: str | None = None            # idioma + faixa de densidade: parte da identidade da receita
        self.external_checked_mono = 0.0
        self.boot_log_offset = 0
        self.snapshot_failures = 0
        self.snapshot_unsupported = False           # o emulador recusou o snapshot deste AVD 2× seguidas: para de salvar
        # 1ª sessão depois de criar/resetar o AVD: o hardware dessa sessão (initPath, partição de dados recém-criada)
        # difere do das seguintes, então um snapshot tirado nela NUNCA carrega (medido) — nessa sessão só desliga.
        self.fresh_data = False
        self.snapshot_valid = bool(row["snapshot_valid"]) if "snapshot_valid" in row.keys() else False
        self.snapshot_hw: str | None = row["snapshot_hw"] if "snapshot_hw" in row.keys() else None
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.op_lock = asyncio.Lock()      # operações de ciclo de vida (start/stop/reset) não se sobrepõem
        self.spawn_lock = threading.Lock()

    @property
    def focused(self) -> bool:
        return time.monotonic() < self.focus_until_mono


class DeviceManager:
    def __init__(self, cfg: Config, db: Database, bus: EventBus, tools: SdkTools, appium: AppiumServer,
                 *, settings_getter: Callable[[], Any], io_factory: Callable[[DeviceRuntime], DeviceIO] | None = None):
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.tools = tools
        self.avd = AvdManager(cfg, tools)
        self.appium = appium
        self.get_settings = settings_getter
        self.io_factory = io_factory
        self.devices: dict[str, DeviceRuntime] = {}
        self.boot_limiter = Limiter(cfg.file.limits.boot_parallelism)
        self.on_device_free: Callable[[], None] = lambda: None   # o scheduler se inscreve aqui
        #: Os dados do aparelho foram apagados (reset, wipe). Quem sabe o que estava instalado é a camada de
        #: releases, então ela se inscreve aqui — senão o central continuaria afirmando "app pronto" num
        #: aparelho vazio, e "Distribuir" responderia "já está nesta versão".
        self.on_device_wiped: Callable[[str, str], None] = lambda instance_id, motivo: None
        self._bg: list[asyncio.Task[Any]] = []
        self.last_metrics: Metrics | None = None
        self.boots: list[tuple[str, str]] = []      # (instância, warm|cold) — só no modo de teste

    # ------------------------------------------------------------------ bootstrap
    def seed(self) -> None:
        c = self.cfg.file.instances
        with self.db.tx():
            for i, iid in enumerate(self.cfg.instance_ids(), start=1):
                exists = self.db.one("SELECT id FROM instances WHERE id=?", (iid,))
                if exists:
                    continue
                self.db.execute(
                    "INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
                    " app_id, account_label) VALUES (?,?,?,?,?,?,?,?,?)",
                    (iid, i, iid, c.base_console_port + 2 * (i - 1), c.base_system_port + (i - 1),
                     c.base_mjpeg_port + (i - 1), c.base_chromedriver_port + (i - 1),
                     None if iid == self.cfg.store_id else
                     (c.default_app if self.db.one("SELECT id FROM apps WHERE id=?", (c.default_app,)) else None),
                     c.accounts.get(iid)))
            if self.cfg.store_id:
                # `seed` só insere o que falta: uma instância que JÁ existia e virou loja ainda carregaria o app e o
                # rótulo de conta de quando era aparelho de tarefa. A loja não opera app nenhum.
                self.db.execute("UPDATE instances SET app_id=NULL, account_label=NULL WHERE id=?"
                                " AND (app_id IS NOT NULL OR account_label IS NOT NULL)", (self.cfg.store_id,))
        for row in self.db.query("SELECT * FROM instances ORDER BY idx"):
            if row["id"] in self.cfg.instance_ids():
                self.devices[row["id"]] = DeviceRuntime(self.cfg, self.tools, row, self.io_factory)

    async def start(self) -> None:
        await asyncio.gather(*(self._adopt(rt) for rt in self.devices.values()))
        self._bg.append(asyncio.create_task(self._monitor_loop(), name="device-monitor"))
        self._bg.append(asyncio.create_task(self._metrics_loop(), name="metrics"))

    async def shutdown(self) -> None:
        """Encerra as tarefas do backend. Emuladores continuam rodando (preservam apps e sessões)."""
        for t in self._bg:
            t.cancel()
        for rt in self.devices.values():          # primeiro para TODAS as tarefas; só depois fecha sessões
            for t in rt.tasks.values():
                t.cancel()

        async def _close(rt: DeviceRuntime) -> None:
            try:
                await asyncio.wait_for(asyncio.to_thread(rt.session.close), timeout=20)
            except Exception:  # noqa: BLE001 - sessão presa não pode impedir o encerramento
                log.warning("%s: a sessão de automação não fechou a tempo", rt.id)
            rt.executor.shutdown()

        await asyncio.gather(*(_close(rt) for rt in self.devices.values()))

    def _invalidate_session(self, rt: DeviceRuntime, motivo: str) -> None:
        """Sessão do Instagram é CACHE do que se observou: qualquer coisa que mexa no disco do aparelho a invalida.

        Fica aqui, e não no domínio social, porque quem sabe que o disco mudou é o gerenciador de aparelhos.
        `on_session_invalidated` é preenchido pelo AppState; sem ele, nada acontece.
        """
        hook = getattr(self, "on_session_invalidated", None)
        if hook is None:
            return
        try:
            hook(rt.id, motivo)
        except Exception:  # noqa: BLE001 - invalidar sessão nunca pode derrubar o ciclo do aparelho
            log.exception("%s: falha ao invalidar a sessão", rt.id)

    def get(self, instance_id: str) -> DeviceRuntime:
        rt = self.devices.get(instance_id)
        if rt is None:
            raise KeyError(instance_id)
        return rt

    # ------------------------------------------------------------------ DTO/eventos
    def dto(self, rt: DeviceRuntime) -> InstanceDTO:
        row = self.db.one("SELECT app_id, account_label, account_evidence, account_evidence_ts FROM instances WHERE id=?",
                          (rt.id,))
        s = self.get_settings()
        frame = None
        if rt.frame is not None:
            interval = s.capture_focus_interval_s if rt.focused else s.capture_grid_interval_s
            max_age = max(s.frame_max_age_ms / 1000, interval * 2.5)
            frame = rt.frame.info.model_copy(update={"stale": (time.monotonic() - rt.frame.mono) > max_age})
        return InstanceDTO(
            id=rt.id, index=rt.index, avd_name=rt.avd_name, serial=rt.serial, console_port=rt.console_port,
            ports=rt.ports, state=rt.state, state_detail=rt.state_detail, pid=rt.pid, boot_seconds=rt.boot_seconds,
            app_id=row["app_id"], account_label=row["account_label"], account_evidence=row["account_evidence"],
            account_evidence_ts=row["account_evidence_ts"], control=rt.control, control_since=rt.control_since,
            control_pending=rt.takeover_requested, automation=rt.automation, frame=frame, current=rt.current,
            attention=rt.attention, resources=rt.resources,
            kind="store" if rt.store else "external" if rt.external else "emulator", worker_id=rt.worker_id,
            # O painel precisa saber o que este aparelho aceita ANTES de oferecer o botão. Sem isto, o cartão de um
            # aparelho de outra máquina oferecia Parar, Hibernar e "Resetar dados…" com a mesma aparência de um
            # emulador local — e nenhuma dessas ações acontecia.
            supported_verbs=sorted(verbos_suportados(rt)))

    def list_dtos(self) -> list[InstanceDTO]:
        return [self.dto(rt) for rt in self.devices.values()]

    def publish(self, rt: DeviceRuntime, message: str | None = None, level: str = "info") -> None:
        self.bus.emit("instance.updated", message or f"{rt.id}: {rt.state.value}", level=level, instance_id=rt.id,
                      data={"instance": self.dto(rt).model_dump(mode="json")})

    def _set_state(self, rt: DeviceRuntime, state: InstanceState, detail: str | None = None,
                   *, level: str = "info", attention: str | None = None) -> None:
        if state == InstanceState.online and rt.state != InstanceState.online:
            rt.app_versions.clear()
            rt.ui_variant = None
            rt.online_since_mono = rt.last_activity_mono = time.monotonic()
            rt.start_refusals, rt.start_backoff_until = 0, 0.0
        rt.state, rt.state_detail = state, detail
        if attention is not None or state in (InstanceState.online, InstanceState.stopped, InstanceState.hibernated):
            rt.attention = attention
        self.publish(rt, f"{rt.id}: {state.value}" + (f" — {detail}" if detail else ""), level)

    # ------------------------------------------------------------------ adoção / monitor
    async def _adopt(self, rt: DeviceRuntime) -> None:
        if self.io_factory is not None:       # testes: aparelho falso, sem SDK/ADB/Appium
            if rt.snapshot_valid:
                rt.state, rt.state_detail = InstanceState.hibernated, "hibernado (snapshot salvo)"
                return
            rt.state = InstanceState.online
            rt.automation = AutomationInfo(state="ready", detail="driver de teste")
            return
        if rt.external:
            await self._adopt_external(rt)
            return
        if not self.avd.exists(rt.avd_name):
            rt.state = InstanceState.absent
            return
        if not self.tools.found():
            rt.state, rt.state_detail = InstanceState.stopped, "Android SDK não encontrado"
            return
        alive = emu.is_our_emulator(rt.pid, rt.avd_name)
        try:
            state = await rt.executor.run(rt.adb.state, timeout=12, label="adb get-state")
        except (DriverError, AdbError):
            state = None
        if state == "device":
            booted = False
            try:
                booted = await rt.executor.run(rt.adb.boot_completed, timeout=12)
            except (DriverError, AdbError):
                pass
            if booted:
                try:     # ajustes idempotentes (sem animações, tela ligada, sem teclado virtual sobre a tela)
                    await rt.executor.run(rt.adb.prepare_for_automation, timeout=60, label="prepare")
                except (DriverError, AdbError) as exc:
                    log.warning("%s: preparo na readoção falhou: %s", rt.id, exc)
                rt.state = InstanceState.online
                rt.state_detail = "readotado após reinício do backend" if alive else "emulador externo (não iniciado por este projeto)"
                self._start_online_tasks(rt)
                return
        if alive:
            rt.state, rt.state_detail = InstanceState.booting, "emulador em inicialização (readotado)"
            rt.tasks["boot"] = asyncio.create_task(self._wait_boot(rt, time.monotonic(), adopted=True))
        else:
            rt.state = InstanceState.hibernated if rt.snapshot_valid else InstanceState.stopped
            if rt.snapshot_valid:
                rt.state_detail = "hibernado (snapshot salvo)"
            if rt.pid:
                self._save_pid(rt, None)

    async def _adopt_external(self, rt: DeviceRuntime) -> None:
        """Aparelho que o projeto não controla (celular físico, contêiner, outro emulador): só verifica se o ADB o vê."""
        try:
            await rt.executor.run(rt.adb.connect, timeout=25, label="adb connect")
            state = await rt.executor.run(rt.adb.state, timeout=12, label="adb get-state")
        except (DriverError, AdbError):
            state = None
        if state == "device":
            try:
                await rt.executor.run(rt.adb.prepare_for_automation, timeout=60, label="prepare")
            except (DriverError, AdbError) as exc:
                log.warning("%s: preparo do aparelho externo falhou: %s", rt.id, exc)
            if rt.state != InstanceState.online:
                self._set_state(rt, InstanceState.online, f"aparelho externo via ADB ({rt.serial})")
                self._start_online_tasks(rt)
                self.on_device_free()
        elif rt.state != InstanceState.stopped or not rt.state_detail:
            self._set_state(rt, InstanceState.stopped, f"aparelho externo {rt.serial} não está conectado ao ADB"
                            + (f" (estado: {state})" if state else ""))

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(6)
            now_m = time.monotonic()
            for rt in self.devices.values():
                try:
                    if rt.state == InstanceState.online and rt.pid and not emu.is_our_emulator(rt.pid, rt.avd_name):
                        self._on_device_lost(rt, "O processo do emulador encerrou inesperadamente.")
                    if rt.external and now_m - rt.external_checked_mono > 30 and not rt.executor.queue_depth:
                        rt.external_checked_mono = now_m          # cabo solto / Wi-Fi caiu / voltou: o estado acompanha
                        if rt.state == InstanceState.online:
                            if await rt.executor.run(rt.adb.state, timeout=12, label="adb get-state") != "device":
                                self._on_device_lost(rt, f"Aparelho externo {rt.serial} sumiu do ADB.")
                        elif rt.state in (InstanceState.stopped, InstanceState.error):
                            # Só readota quando ninguém mandou parar. Sem esta guarda, um "Parar" era desfeito em
                            # ≤36 s e o cartão continuava dizendo "desligado" — a interface mentia duas vezes.
                            if rt.desired_state != InstanceState.stopped.value:
                                await self._adopt_external(rt)
                    if rt.control == ControlOwner.user and now_m > rt.lease_expires_mono:
                        self._end_user_control(rt, "Controle manual expirou por inatividade e foi devolvido.")
                    # sessão de automação que falhou ao abrir: nova tentativa espaçada, sem depender de uma execução
                    if (rt.state == InstanceState.online and rt.automation.state == "error" and self.io_factory is None
                            and now_m - rt.automation_retry_mono > 90):
                        prev = rt.tasks.get("automation")
                        if prev is None or prev.done():
                            rt.automation_retry_mono = now_m
                            rt.tasks["automation"] = asyncio.create_task(self.ensure_automation(rt),
                                                                         name=f"automation-{rt.id}")
                except Exception:  # noqa: BLE001
                    log.exception("monitor %s", rt.id)

    def _on_device_lost(self, rt: DeviceRuntime, why: str) -> None:
        for name in ("capture", "automation"):
            t = rt.tasks.pop(name, None)
            if t:
                t.cancel()
        rt.frame = None
        rt.automation = AutomationInfo()
        self._save_pid(rt, None)
        self._set_state(rt, InstanceState.error, why, level="error", attention=why)

    async def _metrics_loop(self) -> None:
        psutil.cpu_percent(interval=None)
        while True:
            await asyncio.sleep(3)
            vm = psutil.virtual_memory()
            ems: list[EmulatorMetric] = []
            for rt in self.devices.values():
                if rt.pid and rt.state in (InstanceState.online, InstanceState.booting):
                    usage = await asyncio.to_thread(emu.process_usage, rt.pid)
                    if usage:
                        rt.resources = InstanceResources(rss_mb=usage[0], cpu_percent=usage[1])
                        ems.append(EmulatorMetric(instance_id=rt.id, pid=rt.pid, rss_mb=usage[0], cpu_percent=usage[1]))
                else:
                    rt.resources = None
            self.last_metrics = Metrics(ts=now_iso(), cpu_percent=psutil.cpu_percent(interval=None),
                                        mem_total_gb=round(vm.total / 2**30, 1),
                                        mem_available_gb=round(vm.available / 2**30, 1),
                                        mem_used_percent=vm.percent, emulators=ems)
            self.bus.emit("metrics", "metrics", data={"metrics": self.last_metrics.model_dump(mode="json")})

    # ------------------------------------------------------------------ ciclo de vida
    def _save_pid(self, rt: DeviceRuntime, pid: int | None) -> None:
        rt.pid = pid
        self.db.execute("UPDATE instances SET emulator_pid=?, emulator_started_at=? WHERE id=?",
                        (pid, now_iso() if pid else None, rt.id))

    def bind_worker(self, worker_id: str, verbs: list[str] | None) -> list[str]:
        """Liga (ou desliga) as capacidades declaradas por um worker aos aparelhos que ele hospeda.

        `verbs=None` é a desconexão: o aparelho volta a aceitar só o que o transporte alcança, e o painel para de
        oferecer botão de ciclo de vida para uma máquina que não está lá. Devolve quem mudou, para virar evento.
        """
        mudados = []
        for rt in self.devices.values():
            if rt.worker_id != worker_id or rt.worker_verbs == verbs:
                continue
            rt.worker_verbs = verbs
            mudados.append(rt.id)
            self.publish(rt)
        return mudados

    def set_desired_state(self, rt: DeviceRuntime, desired: str | None) -> None:
        """Registra a DECISÃO sobre o aparelho, que é diferente do que se observa nele.

        Sobrevive a reinício de propósito: sem isso, o monitor voltava a ligar (ou a readotar) um aparelho que
        alguém tinha mandado parar — e o "Parar" era desfeito em ≤36 s, sem aviso.
        """
        if rt.desired_state == desired:
            return
        rt.desired_state = desired
        self.db.execute("UPDATE instances SET desired_state=? WHERE id=?", (desired, rt.id))

    def _guard_not_running_ai(self, rt: DeviceRuntime) -> None:
        if rt.control == ControlOwner.ai:
            raise InstanceBusy("A IA está executando neste aparelho. Pause/cancele a execução ou assuma o controle antes.")

    async def create(self, rt: DeviceRuntime) -> None:
        async with rt.op_lock:
            if self.avd.exists(rt.avd_name):
                return
            self._set_state(rt, InstanceState.stopped, "criando AVD…")
            try:
                await asyncio.to_thread(self.avd.create, rt.avd_name, self.cfg.instance_android(rt.id))
            except (AvdError, OSError) as exc:
                # A falha SOBE. Engolir aqui fazia o comando do painel virar `succeeded` com o AVD inexistente:
                # o estado do aparelho dizia `absent` e o histórico dizia "criado". Quem chamou decide o desfecho.
                self._set_state(rt, InstanceState.absent, str(exc), level="error", attention=str(exc))
                raise
            self._set_state(rt, InstanceState.stopped, "AVD criado")

    async def start_instance(self, rt: DeviceRuntime) -> None:
        # A decisão é registrada mesmo quando não há nada a fazer: "eu quero este aparelho no ar" vale como
        # instrução ao monitor, e é o que autoriza a readoção automática mais tarde.
        self.set_desired_state(rt, InstanceState.online.value)
        if rt.state in (InstanceState.online, InstanceState.booting, InstanceState.stopping):
            return
        if rt.external:                        # "Iniciar" um aparelho externo = tentar (re)conectar; nada é ligado
            await self._adopt_external(rt)
            return
        rt.state, rt.state_detail = InstanceState.booting, "na fila de inicialização"
        self.publish(rt)
        rt.tasks["boot"] = asyncio.create_task(self._boot(rt), name=f"boot-{rt.id}")

    async def aguardar_boot(self, rt: DeviceRuntime, prazo_s: float) -> tuple[str, str | None]:
        """Espera o boot enfileirado chegar a um desfecho: `("online" | "failed" | "uncertain", detalhe)`.

        Existe porque `start_instance` apenas ENFILEIRA o boot e volta. Sem esta espera, o comando do painel
        virava `succeeded` em 2 ms — antes da guarda de RAM recusar, antes de o emulador subir, antes de o
        Android existir. O caminho do worker já esperava (o agente só responde depois do boot, ou `uncertain` no
        prazo); agora o mesmo verbo significa a mesma coisa nas duas máquinas.

        O prazo estourado NUNCA cancela o boot: é a resposta de quem espera, não uma ordem de desistir. O
        aparelho pode ficar pronto depois — e o comando fica `uncertain`, que é a verdade.
        """
        tarefa = rt.tasks.get("boot")
        if tarefa is not None and not tarefa.done():
            await asyncio.wait({tarefa}, timeout=prazo_s)
            if not tarefa.done():
                return "uncertain", (f"o aparelho não completou o boot em {prazo_s:.0f} s; o boot continua em "
                                     "andamento nesta máquina e nada será repetido automaticamente")
        if rt.state == InstanceState.online:
            return "online", rt.state_detail
        if rt.state in (InstanceState.booting, InstanceState.stopping):
            # O boot acabou e o aparelho não está nem no ar nem parado: alguém mexeu por fora (rodízio, parada).
            return "uncertain", rt.state_detail or "o boot terminou sem dizer o desfecho"
        return "failed", rt.state_detail or f"o aparelho terminou em '{rt.state.value}', e não online"

    async def _boot(self, rt: DeviceRuntime) -> None:
        a = self.cfg.instance_android(rt.id)
        if self.io_factory is not None:       # testes: "boot" do aparelho falso
            if (recusa := getattr(self, "fake_boot_refusal", None)) is not None:
                # Espelha a guarda de capacidade do caminho real: o estado VOLTA e o motivo fica no aparelho.
                async with rt.op_lock:
                    back = InstanceState.hibernated if rt.snapshot_valid else InstanceState.stopped
                    self._set_state(rt, back, recusa, level="warn", attention=recusa)
                return
            async with rt.op_lock:
                async with self.boot_limiter:
                    warm = rt.snapshot_valid
                    self._set_snapshot(rt, False)
                    await asyncio.sleep(getattr(self, "fake_wake_s" if warm else "fake_boot_s", 0.05))
                    self.boots.append((rt.id, "warm" if warm else "cold"))
                    rt.automation = AutomationInfo(state="ready", detail="driver de teste")
                    self._set_state(rt, InstanceState.online, "pronto (teste)")
                    self.on_device_free()
            return
        async with rt.op_lock:
            try:
                async with self.boot_limiter:
                    # Guarda de capacidade: memória disponível AGORA, menos o que os boots em andamento ainda
                    # vão alocar, precisa comportar esta instância e deixar uma folga para o host.
                    est = a.est_instance_ram_mb or (a.ram_mb + 1100)
                    inflight = sum(max(0.0, est - ((d.resources.rss_mb if d.resources else 0) or 0))
                                   for d in self.devices.values()
                                   if d is not rt and d.pid and d.state == InstanceState.booting)
                    free_mb = psutil.virtual_memory().available / 2**20
                    after = free_mb - inflight - est
                    if after < a.min_free_ram_mb_after_boot:
                        online = sum(1 for d in self.devices.values() if d.state == InstanceState.online)
                        msg = (f"Capacidade do host atingida: {free_mb:.0f} MB disponíveis"
                               + (f" (−{inflight:.0f} MB reservados para boots em andamento)" if inflight else "")
                               + f"; esta instância precisa de ≈{est} MB e o host deve manter {a.min_free_ram_mb_after_boot} MB "
                               f"livres. {online} instância(s) online. Libere memória no host ou use uma imagem mais leve.")
                        rt.start_refusals += 1          # o rodízio não insiste a cada tick: espera crescente
                        rt.start_backoff_until = time.monotonic() + min(120, 15 * 2 ** (rt.start_refusals - 1))
                        back = InstanceState.hibernated if rt.snapshot_valid else InstanceState.stopped
                        self._set_state(rt, back, msg, level="warn", attention=msg)      # o snapshot continua válido
                        self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "capacity", dumps({
                            "instance_id": rt.id, "refused": True, "online": online, "mem_available_mb": round(free_mb),
                            "inflight_reserved_mb": round(inflight), "needed_mb": est})))
                        return
                    created = not self.avd.exists(rt.avd_name)
                    if created:
                        self._set_state(rt, InstanceState.booting, "criando AVD…")
                        await asyncio.to_thread(self.avd.create, rt.avd_name, a)
                    else:
                        await asyncio.to_thread(self.avd.apply_hardware, rt.avd_name, a)
                    t0 = time.monotonic()
                    wipe, rt.wipe_next_boot = rt.wipe_next_boot, False
                    rt.fresh_data = created or wipe
                    warm = (a.hibernation and rt.snapshot_valid and not wipe and rt.snapshot_hw == _hw_signature(a)
                            and self._snapshot_dir(rt).exists())
                    # Snapshot é de USO ÚNICO: o flag cai ANTES do spawn. Se o processo morrer no meio, o próximo boot
                    # é a frio — carregar de novo um snapshot já usado reverteria o disco (logins, mensagens).
                    self._set_snapshot(rt, False)
                    if not warm:
                        await asyncio.to_thread(self._discard_snapshot, rt)
                    log_path = self.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
                    rt.boot_log_offset = log_path.stat().st_size if log_path.exists() else 0
                    await asyncio.to_thread(self._spawn, rt, a, wipe, warm)
                    self._set_state(rt, InstanceState.booting, "acordando do snapshot…" if warm else
                                    "emulador iniciado" + (" (dados apagados)" if wipe else ""))
                    ok = await self._wait_boot(rt, t0, warm=warm)
                    if warm and not ok:            # snapshot corrompido/incompatível: descarta e tenta UMA vez a frio
                        log.warning("%s: acordar do snapshot falhou; boot a frio", rt.id)
                        await asyncio.to_thread(emu.stop_process, rt.adb, rt.pid, rt.avd_name)
                        self._save_pid(rt, None)
                        await asyncio.to_thread(self._discard_snapshot, rt)
                        t0 = time.monotonic()
                        await asyncio.to_thread(self._spawn, rt, a, False, False)
                        self._set_state(rt, InstanceState.booting, "snapshot descartado; iniciando a frio")
                        await self._wait_boot(rt, t0)
            except (AvdError, emu.EmulatorError, OSError) as exc:
                self._set_state(rt, InstanceState.error, str(exc), level="error", attention=str(exc))

    async def _wait_boot(self, rt: DeviceRuntime, t0: float, *, adopted: bool = False, warm: bool = False) -> bool:
        a_cfg = self.cfg.instance_android(rt.id)
        timeout = a_cfg.wake_timeout_s if warm else a_cfg.boot_timeout_s
        phase = "aguardando o Android iniciar"
        booted_at: float | None = None
        checked_load = False
        while True:
            elapsed = time.monotonic() - t0
            if warm and not checked_load:
                # O emulador decide sozinho se carrega o snapshot e, se não carregar, segue em boot a frio (medido:
                # hardware diferente do salvo → "cannot load snapshot"). Só o LOG diz qual dos dois aconteceu; com a
                # máquina carregada a linha pode demorar, então ausência de linha não é veredito.
                verdict = self._snapshot_verdict(rt)
                if verdict is True:
                    checked_load = True
                    rt.snapshot_failures = 0
                elif verdict is False:
                    checked_load = True
                    warm, timeout = False, a_cfg.boot_timeout_s
                    rt.snapshot_failures += 1
                    rt.snapshot_unsupported = rt.snapshot_failures >= 2
                    rt.state_detail = "o emulador recusou o snapshot; boot a frio"
                    self.bus.emit("log", f"{rt.id}: o emulador recusou o snapshot; seguindo em boot a frio"
                                  + (" — este AVD deixa de hibernar" if rt.snapshot_unsupported else ""),
                                  level="warn", instance_id=rt.id)
            if elapsed > timeout:
                if warm:
                    return False                   # quem chamou descarta o snapshot e tenta a frio
                self._set_state(rt, InstanceState.error, f"Boot excedeu {timeout}s", level="error",
                                attention="O boot não concluiu a tempo. Veja data/logs/emulator-%s.log" % rt.avd_name)
                return False
            if rt.pid and not emu.is_our_emulator(rt.pid, rt.avd_name):
                if warm:
                    return False
                tail = emu.read_log_tail(self.cfg.logs_dir / f"emulator-{rt.avd_name}.log", 600)
                self._save_pid(rt, None)
                self._set_state(rt, InstanceState.error, "O emulador encerrou durante o boot.", level="error",
                                attention=f"Emulador encerrou no boot. Final do log: {tail[-300:]}")
                return False
            try:
                if booted_at is None:
                    if await rt.executor.run(rt.adb.boot_completed, timeout=15, label="boot_completed"):
                        booted_at = time.monotonic()
                        phase = "Android iniciado; aguardando a interface"
                elif await rt.executor.run(rt.adb.ui_ready, timeout=15, label="ui_ready") or \
                        (time.monotonic() - booted_at) > 120:
                    break
            except (DriverError, AdbError):
                pass
            if int(elapsed) % 10 < 2:
                rt.state_detail = f"{phase} ({elapsed:.0f}s)"
                self.publish(rt)
            await asyncio.sleep(2)
        try:
            await rt.executor.run(rt.adb.prepare_for_automation, timeout=60, label="prepare")
        except (DriverError, AdbError) as exc:
            log.warning("%s: preparo pós-boot falhou: %s", rt.id, exc)
        skew: tuple[int, int] | None = None
        if warm:                                   # o relógio do guest acorda no passado: acerta antes de qualquer tarefa
            try:
                skew = await rt.executor.run(rt.adb.sync_clock, timeout=60, label="acertar relógio")
            except (DriverError, AdbError, ValueError) as exc:
                log.warning("%s: não foi possível acertar o relógio após acordar: %s", rt.id, exc)
            self.invalidate_automation(rt, "acordou de snapshot")
        rt.boot_seconds = round(time.monotonic() - t0, 1)
        if not adopted:
            self.db.execute("UPDATE instances SET boot_seconds=? WHERE id=?", (rt.boot_seconds, rt.id))
            vm = psutil.virtual_memory()
            self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "boot", dumps({
                "instance_id": rt.id, "boot_seconds": rt.boot_seconds, "kind": "warm" if warm else "cold",
                "clock_skew_before_after_s": list(skew) if skew else None,
                "online_after": sum(1 for d in self.devices.values() if d.state == InstanceState.online) + 1,
                "mem_available_gb": round(vm.available / 2**30, 1), "image": self.cfg.instance_android(rt.id).system_image})))
        self._set_state(rt, InstanceState.online, f"{'acordou' if warm else 'pronto'} em {rt.boot_seconds:.0f}s")
        self._start_online_tasks(rt)
        self.on_device_free()
        return True

    def _start_online_tasks(self, rt: DeviceRuntime) -> None:
        if "capture" not in rt.tasks or rt.tasks["capture"].done():
            rt.tasks["capture"] = asyncio.create_task(self._capture_loop(rt), name=f"capture-{rt.id}")
        if rt.store:
            # A loja não é automatizada: só copiamos o pacote dela por adb. Abrir sessão instalaria o servidor
            # UiAutomator2 numa imagem com Play Protect, sem ganho nenhum. A captura de tela segue por adb.
            return
        if "automation" not in rt.tasks or rt.tasks["automation"].done():
            rt.tasks["automation"] = asyncio.create_task(self.ensure_automation(rt), name=f"automation-{rt.id}")

    def _spawn(self, rt: DeviceRuntime, a: Any, wipe: bool, from_snapshot: bool = False) -> None:
        """Inicia o emulador e grava o PID na MESMA seção crítica: um cancelamento nunca deixa processo órfão."""
        with rt.spawn_lock:
            pid = emu.start_process(self.cfg, self.tools, rt.avd_name, rt.console_port, a, wipe_data=wipe,
                                    from_snapshot=from_snapshot)
            self._save_pid(rt, pid)

    # ------------------------------------------------------------------ snapshot (hibernação)
    def _snapshot_dir(self, rt: DeviceRuntime) -> Path:
        return self.cfg.avd_home / f"{rt.avd_name}.avd" / "snapshots" / emu.SNAPSHOT_NAME

    def _snapshot_verdict(self, rt: DeviceRuntime) -> bool | None:
        """True = snapshot carregado · False = recusado pelo emulador · None = o log ainda não disse."""
        path = self.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
        try:
            with path.open("rb") as fh:
                fh.seek(rt.boot_log_offset)
                text = fh.read(400_000).decode("utf-8", errors="replace")
        except OSError:
            return None
        if "Successfully loaded snapshot" in text:
            return True
        if "cannot load snapshot" in text or "Failed to load snapshot" in text:
            return False
        return None

    def _discard_snapshot(self, rt: DeviceRuntime) -> None:
        shutil.rmtree(self._snapshot_dir(rt), ignore_errors=True)

    def _set_snapshot(self, rt: DeviceRuntime, valid: bool, hw: str | None = None) -> None:
        rt.snapshot_valid, rt.snapshot_hw = valid, (hw if valid else None)
        self.db.execute("UPDATE instances SET snapshot_valid=?, snapshot_hw=?, hibernated_at=? WHERE id=?",
                        (int(valid), rt.snapshot_hw, now_iso() if valid else None, rt.id))

    async def stop_instance(self, rt: DeviceRuntime, *, force: bool = False, hibernate: bool = False) -> None:
        """`hibernate=True` (com `android.hibernation`) salva um snapshot antes de desligar: o próximo start acorda em
        segundos. Se o snapshot não puder ser salvo com certeza, o aparelho apenas desliga (próximo boot a frio)."""
        if not force:
            self._guard_not_running_ai(rt)
        # `force=True` é o rodízio cedendo vaga, não uma decisão sobre o aparelho: ali o desejo continua "no ar",
        # senão hibernar por falta de vaga impediria o próprio rodízio de acordá-lo depois.
        if not force:
            self.set_desired_state(rt, InstanceState.stopped.value)
        if rt.external:                        # nunca desliga um aparelho que não é nosso: só solta a sessão
            await self._soltar_do_painel(rt, "desconectado do painel (o aparelho externo continua ligado)")
            return
        if rt.state in (InstanceState.stopped, InstanceState.absent, InstanceState.hibernated):
            if rt.state == InstanceState.hibernated and not hibernate:      # "Parar" um hibernado = descartar o snapshot
                self._set_snapshot(rt, False)
                await asyncio.to_thread(self._discard_snapshot, rt)
                self._set_state(rt, InstanceState.stopped, "snapshot descartado")
            return
        for name in ("boot", "capture", "automation"):
            t = rt.tasks.pop(name, None)
            if t:
                t.cancel()
        if self.io_factory is not None:       # testes: aparelho falso
            async with rt.op_lock:
                rt.automation = AutomationInfo()
                if hibernate and getattr(self, "fake_snapshot_ok", True):
                    self._set_snapshot(rt, True, "fake")
                    self._set_state(rt, InstanceState.hibernated, "hibernado (teste)")
                else:
                    self._set_state(rt, InstanceState.stopped, sem_snapshot("o console não confirmou o snapshot")
                                    if hibernate else "desligado (teste)")
            self.on_device_free()
            return
        await asyncio.to_thread(_wait_lock, rt.spawn_lock)   # se o processo estava nascendo, o PID já foi gravado
        if rt.pid is None and rt.state == InstanceState.booting:
            self._set_state(rt, InstanceState.stopped, "boot cancelado antes de iniciar o emulador")
            return
        async with rt.op_lock:
            a = self.cfg.instance_android(rt.id)
            pedido_de_hibernar, porque_sem_snapshot = hibernate, None
            hibernate = hibernate and a.hibernation and rt.state in (InstanceState.online, InstanceState.stopping) \
                and rt.pid is not None and not rt.snapshot_unsupported and not rt.fresh_data
            if pedido_de_hibernar and not hibernate:
                # Por que a hibernação nem foi tentada. Sem esta frase o aparelho só "desligava" e quem pediu
                # "Hibernar" via um toast verde de sucesso sobre um boot a frio garantido.
                porque_sem_snapshot = (
                    "hibernação desligada na configuração (android.hibernation)" if not a.hibernation else
                    "este AVD já teve o snapshot recusado pelo emulador" if rt.snapshot_unsupported else
                    "os dados acabaram de ser apagados e não há snapshot a salvar" if rt.fresh_data else
                    f"o aparelho estava em '{rt.state.value}'")
            self._set_state(rt, InstanceState.stopping, "hibernando (salvando snapshot)…" if hibernate else "encerrando o emulador…")
            await asyncio.to_thread(rt.session.close)
            rt.automation = AutomationInfo()
            saved = False
            if hibernate:
                t0 = time.monotonic()
                try:
                    await asyncio.to_thread(self._discard_snapshot, rt)
                    await rt.executor.run(rt.adb.snapshot_save, emu.SNAPSHOT_NAME, timeout=320, label="snapshot save")
                    saved = True
                except (DriverError, AdbError) as exc:
                    log.warning("%s: snapshot não foi salvo (%s); desligando sem hibernar", rt.id, exc)
                    porque_sem_snapshot = f"o snapshot não foi salvo ({exc})"
                self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "hibernate", dumps({
                    "instance_id": rt.id, "saved": saved, "save_seconds": round(time.monotonic() - t0, 1)})))
            how = await asyncio.to_thread(emu.stop_process, rt.adb, rt.pid, rt.avd_name)
            self._save_pid(rt, None)
            rt.frame = None
            if rt.control == ControlOwner.user:
                self._end_user_control(rt, None)
            if saved:                              # só agora, com o processo encerrado, o snapshot passa a valer
                self._set_snapshot(rt, True, _hw_signature(a))
                self._set_state(rt, InstanceState.hibernated, "hibernado (snapshot salvo)")
            else:
                await asyncio.to_thread(self._discard_snapshot, rt)
                self._set_state(rt, InstanceState.stopped,
                                sem_snapshot(porque_sem_snapshot) if porque_sem_snapshot else how)
        self.on_device_free()                  # uma vaga abriu: o scheduler pode ligar quem está esperando

    # ------------------------------------------------------------------ rodízio (N contas sobre K vagas)
    def slots_used(self) -> int:
        """Aparelhos que ocupam (ou vão ocupar) RAM do host agora."""
        return sum(1 for d in self.devices.values() if not d.external
                   and d.state in (InstanceState.online, InstanceState.booting, InstanceState.stopping))

    def touch(self, rt: DeviceRuntime) -> None:
        rt.last_activity_mono = time.monotonic()

    def request_start(self, rt: DeviceRuntime, why: str) -> bool:
        """Pedido do scheduler para ligar um aparelho parado. Não bloqueia; respeita a espera após recusa por RAM."""
        if (rt.external or rt.state not in (InstanceState.stopped, InstanceState.absent, InstanceState.hibernated)
                or time.monotonic() < rt.start_backoff_until):
            return False
        self.bus.emit("log", f"{rt.id}: ligando sob demanda — {why}", instance_id=rt.id)
        rt.state, rt.state_detail = InstanceState.booting, "na fila de inicialização (sob demanda)"
        self.publish(rt)
        rt.tasks["boot"] = asyncio.create_task(self._boot(rt), name=f"boot-{rt.id}")
        return True

    def request_stop(self, rt: DeviceRuntime, why: str) -> None:
        """Pedido do scheduler para desligar um aparelho ocioso. O estado muda JÁ, para o mesmo tick não despachar nele."""
        self.bus.emit("log", f"{rt.id}: desligando para o rodízio — {why}", instance_id=rt.id)
        rt.state, rt.state_detail = InstanceState.stopping, f"cedendo a vaga — {why}"
        self.publish(rt)
        rt.tasks["rotate-stop"] = asyncio.create_task(
            self.stop_instance(rt, force=True, hibernate=self.cfg.instance_android(rt.id).hibernation),
            name=f"rotate-stop-{rt.id}")

    async def restart_instance(self, rt: DeviceRuntime) -> None:
        await self.stop_instance(rt)
        await self.start_instance(rt)

    async def reset_instance(self, rt: DeviceRuntime) -> None:
        """Reset explícito: apaga dados do usuário deste AVD (apps, contas, sessões) no próximo boot."""
        if rt.external:
            raise InstanceBusy("Aparelho externo: o painel nunca apaga dados de um aparelho que não é um AVD do projeto.")
        await self.stop_instance(rt)
        self._set_snapshot(rt, False)
        await asyncio.to_thread(self._discard_snapshot, rt)
        if rt.state == InstanceState.hibernated:
            self._set_state(rt, InstanceState.stopped, "snapshot descartado (reset)")
        rt.wipe_next_boot = True
        self._esquecer_o_que_o_disco_tinha(rt, "o aparelho foi resetado; os dados do app foram apagados")
        self.bus.emit("log", f"{rt.id}: dados apagados a pedido do usuário (reset).", level="warn", instance_id=rt.id)
        await self.start_instance(rt)

    # ------------------------------------------------------------------ efeitos do caminho remoto no central
    def _esquecer_o_que_o_disco_tinha(self, rt: DeviceRuntime, motivo: str) -> None:
        """Tudo o que o central AFIRMAVA sobre o disco do aparelho deixa de valer: evidência de conta, sessão do
        perfil e app instalado. Antes, o reset invalidava a sessão e deixava `device_app_state` em `ready` — o
        próximo objetivo era despachado para um aparelho vazio e "Distribuir" respondia "já está nesta versão".
        Vale para os dois caminhos: aqui e no desfecho que vem do agente da outra máquina."""
        self.db.execute("UPDATE instances SET account_evidence=NULL, account_evidence_ts=NULL WHERE id=?", (rt.id,))
        self._invalidate_session(rt, motivo)
        try:
            self.on_device_wiped(rt.id, motivo)
        except Exception:  # noqa: BLE001 - esquecer o app nunca pode derrubar o ciclo do aparelho
            log.exception("%s: falha ao marcar o app como ausente depois do wipe", rt.id)

    async def _soltar_do_painel(self, rt: DeviceRuntime, detalhe: str,
                                estado: InstanceState = InstanceState.stopped) -> None:
        """Solta o que o CENTRAL mantinha aberto no aparelho (captura e sessão de automação) e assume o estado
        informado, sem tocar no processo do emulador — quem o desliga é o dono da máquina dele."""
        for name in ("capture", "automation"):
            t = rt.tasks.pop(name, None)
            if t:
                t.cancel()
        await asyncio.to_thread(rt.session.close)
        rt.automation, rt.frame = AutomationInfo(), None
        self._set_state(rt, estado, detalhe)

    async def _readotar_agora(self, rt: DeviceRuntime) -> None:
        """Readoção IMEDIATA depois de um `start`/`wake` que o agente concluiu. Sem isto o aparelho remoto só era
        reencontrado pelo monitor (até 30 s depois), e nesse intervalo o painel mostrava "desligado" sobre um
        aparelho já no ar."""
        if self.io_factory is not None:        # testes: aparelho falso, sem SDK/ADB/Appium (mesmo desvio de `_adopt`)
            rt.state, rt.state_detail = InstanceState.online, "no ar na máquina do worker"
            rt.automation = AutomationInfo(state="ready", detail="driver de teste")
            self.publish(rt)
            return
        await self._adopt_external(rt) if rt.external else await self._adopt(rt)

    async def aplicar_desfecho_remoto(self, rt: DeviceRuntime, verb: str, outcome: str,
                                      data: dict[str, Any] | None = None) -> None:
        """O que o agente fez NA MÁQUINA DELE vira, aqui, o mesmo efeito que o caminho local produziria.

        Sem isto a mesma operação tinha dois significados conforme onde o aparelho morava: depois de um `stop`
        remoto bem-sucedido o central acusava `error: sumiu do ADB` (alarme falso, com attention), seguia
        tentando `adb connect` a cada 30 s num aparelho desligado de propósito, `hibernate` nunca virava
        `hibernated` (então o painel oferecia "Iniciar", boot a frio, ignorando o snapshot) e `reset` apagava os
        dados sem invalidar sessão, evidência de conta ou estado do app.

        Só o desfecho `succeeded` aplica efeito: `failed`/`uncertain` não autorizam afirmar nada sobre o aparelho
        — aí quem descreve o estado é a observação (monitor e batida), que é a regra honesta.
        """
        if outcome != "succeeded":
            return
        dados = data or {}
        if verb in ("stop", "hibernate"):
            hibernou = verb == "hibernate" and bool(dados.get("hibernated"))
            if hibernou:
                self._set_snapshot(rt, True, "worker")
                await self._soltar_do_painel(rt, "hibernado na máquina do worker (snapshot salvo)",
                                             InstanceState.hibernated)
            else:
                # `stop` bem-sucedido é decisão, não perda: o cartão diz por que o aparelho está fora, e o
                # monitor não tenta readotá-lo (a decisão ficou gravada em `desired_state`).
                self._set_snapshot(rt, False)
                await self._soltar_do_painel(rt, "desligado a pedido; o processo foi parado na máquina do worker")
            self.on_device_free()
            return
        if verb == "reset":
            self._set_snapshot(rt, False)
            self._esquecer_o_que_o_disco_tinha(rt, "o aparelho foi resetado na máquina do worker; os dados do app "
                                                   "foram apagados")

    async def readotar_depois_do_worker(self, rt: DeviceRuntime, verb: str, outcome: str) -> None:
        """A readoção fica SEPARADA de `aplicar_desfecho_remoto` porque ela fala com o aparelho (adb connect,
        preparo) e pode levar dezenas de segundos. Os efeitos de estado precisam valer antes de o desfecho ser
        publicado — é o que impede o alarme falso —, mas prender o `finished_at` do comando (e o cadeado que dá
        exclusividade ao aparelho) à latência do ADB seria trocar uma mentira por uma espera."""
        if outcome == "succeeded" and verb in ("start", "wake", "restart", "reset"):
            await self._readotar_agora(rt)

    # ------------------------------------------------------------------ automação
    async def ensure_automation(self, rt: DeviceRuntime) -> bool:
        if rt.state != InstanceState.online:
            return False
        if self.io_factory is not None:
            return True
        if rt.automation.state == "ready" and rt.session.connected:
            return True
        if rt.automation.state == "starting":
            for _ in range(240):
                await asyncio.sleep(1)
                if rt.automation.state != "starting":
                    break
            return rt.automation.state == "ready"
        rt.automation = AutomationInfo(state="starting", detail="abrindo sessão UiAutomator2…")
        self.publish(rt)
        if not self.appium.is_up():
            ok = await asyncio.to_thread(self.appium.start)
            if not ok:
                rt.automation = AutomationInfo(state="error", detail=self.appium.detail)
                self.publish(rt, level="warn")
                return False
        try:
            await asyncio.to_thread(rt.session.close)
            stale = self.db.scalar("SELECT appium_session_id FROM instances WHERE id=?", (rt.id,))
            await asyncio.to_thread(rt.session.delete_stale, stale)
            for port in (rt.ports.system, rt.ports.mjpeg):      # forwards órfãos de um Appium que morreu
                await asyncio.to_thread(rt.adb.remove_forward, port)
            await rt.executor.run(rt.session.connect, timeout=300, label="appium connect")
            self.db.execute("UPDATE instances SET appium_session_id=? WHERE id=?", (rt.session.session_id, rt.id))
            rt.automation = AutomationInfo(state="ready", detail=f"systemPort {rt.ports.system}")
            self.publish(rt, f"{rt.id}: sessão de automação pronta")
            return True
        except Exception as exc:  # noqa: BLE001
            rt.automation = AutomationInfo(state="error", detail=str(exc).splitlines()[0][:300])
            self.publish(rt, f"{rt.id}: falha ao abrir sessão de automação", level="warn")
            return False

    def invalidate_automation(self, rt: DeviceRuntime, why: str) -> None:
        rt.automation = AutomationInfo(state="none", detail=why)

    # ------------------------------------------------------------------ frames
    async def _capture_loop(self, rt: DeviceRuntime) -> None:
        while rt.state == InstanceState.online:
            s = self.get_settings()
            interval = s.capture_focus_interval_s if rt.focused else s.capture_grid_interval_s
            try:
                # a captura compartilha o executor com as ações: se há trabalho na fila, não entra na frente
                overdue = rt.frame is None or (time.monotonic() - rt.frame.mono) > interval * 2
                if rt.executor.queue_depth == 0 or overdue:
                    png = await rt.executor.run(rt.io.screenshot_png, timeout=25, label="screencap")
                    await self.publish_frame(rt, png)
            except DriverError as exc:
                log.debug("%s: captura falhou: %s", rt.id, exc)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("%s: erro na captura", rt.id)
            try:
                await asyncio.wait_for(rt.capture_now.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            rt.capture_now.clear()

    async def publish_frame(self, rt: DeviceRuntime, png: bytes) -> Frame:
        full, thumb, w, h = await asyncio.to_thread(_encode_frame, png)
        rt.frame_seq += 1
        info = FrameInfo(id=f"{rt.id}-{rt.frame_seq}-{int(time.time() * 1000)}", ts=now_iso(), width=w, height=h,
                         orientation="landscape" if w > h else "portrait", stale=False)
        frame = Frame(info=info, mono=time.monotonic(), jpeg_full=full, jpeg_thumb=thumb)
        rt.frame = frame
        rt.recent_frames[info.id] = (frame.mono, w, h)
        while len(rt.recent_frames) > 6:
            rt.recent_frames.popitem(last=False)
        self.bus.emit("frame", "frame", instance_id=rt.id,
                      data={"instance_id": rt.id, "frame": info.model_dump(mode="json")})
        return frame

    def set_focus(self, instance_id: str | None, ttl_s: float = 15) -> None:
        for rt in self.devices.values():
            if rt.id == instance_id:
                was = rt.focused
                rt.focus_until_mono = time.monotonic() + ttl_s
                if rt.control == ControlOwner.user:
                    rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S
                if not was:
                    rt.capture_now.set()

    async def observe(self, rt: DeviceRuntime, *, timeout: float) -> Observation:
        """Observação para a IA: screenshot + hierarquia do MESMO aparelho, em sequência no executor."""
        png = await rt.executor.run(rt.io.screenshot_png, timeout=timeout, label="screenshot")
        xml = await rt.executor.run(rt.io.page_source, timeout=timeout, label="hierarquia")
        frame = await self.publish_frame(rt, png)
        tree = parse_hierarchy(xml)
        pkg = next((p for p in tree.packages if p != "com.android.systemui"), None)
        return Observation(frame_id=frame.info.id, ts=frame.info.ts, width=frame.info.width, height=frame.info.height,
                           jpeg=None if tree.sensitive else frame.jpeg_full, tree=tree, package=pkg,
                           sensitive=tree.sensitive)

    # ------------------------------------------------------------------ controle (IA × usuário)
    def ai_begin(self, rt: DeviceRuntime) -> bool:
        """O worker da IA pede o aparelho. Negado se o usuário tem (ou pediu) o controle."""
        if rt.control != ControlOwner.none or rt.takeover_requested or rt.executor.has_zombie:
            return False
        rt.control, rt.control_since = ControlOwner.ai, now_iso()
        self._control_event(rt, "IA assumiu o aparelho")
        return True

    def ai_end(self, rt: DeviceRuntime) -> None:
        self.touch(rt)
        if rt.control != ControlOwner.ai:
            return
        if rt.takeover_requested and rt.pending_lease_id:
            self._grant_user(rt, rt.pending_lease_id)
        else:
            rt.control, rt.control_since = ControlOwner.none, None
            self._control_event(rt, "IA liberou o aparelho")

    def _grant_user(self, rt: DeviceRuntime, lease_id: str) -> None:
        rt.control, rt.control_since = ControlOwner.user, now_iso()
        rt.lease_id, rt.pending_lease_id, rt.takeover_requested = lease_id, None, False
        rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S
        rt.attention = "Controle manual ativo — a execução automática deste aparelho está suspensa."
        self._control_event(rt, "Controle manual concedido ao usuário")

    def _control_event(self, rt: DeviceRuntime, message: str) -> None:
        self.bus.emit("control.changed", f"{rt.id}: {message}", instance_id=rt.id,
                      data={"instance_id": rt.id, "control": rt.control.value, "pending": rt.takeover_requested})
        self.publish(rt)

    def request_control(self, rt: DeviceRuntime) -> tuple[str, str]:
        if rt.control == ControlOwner.user and rt.lease_id:
            rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S
            return "granted", rt.lease_id
        if rt.control == ControlOwner.ai:
            # a IA termina a ação em andamento e cede num ponto seguro
            if not rt.pending_lease_id:
                rt.pending_lease_id = new_token()
            rt.takeover_requested = True
            self._control_event(rt, "Usuário pediu o controle; aguardando a IA concluir a ação atual")
            return "pending", rt.pending_lease_id
        lease = new_token()
        self._grant_user(rt, lease)
        return "granted", lease

    def release_control(self, rt: DeviceRuntime, lease_id: str) -> None:
        if rt.takeover_requested and rt.pending_lease_id == lease_id:
            rt.takeover_requested, rt.pending_lease_id = False, None
            self._control_event(rt, "Pedido de controle cancelado")
            return
        if rt.control != ControlOwner.user or rt.lease_id != lease_id:
            raise ControlError("not_controller", "Este lease não controla o aparelho.")
        self._end_user_control(rt, "Usuário devolveu o controle; a IA vai observar a tela novamente antes de agir")

    def _end_user_control(self, rt: DeviceRuntime, message: str | None) -> None:
        rt.control, rt.control_since, rt.lease_id = ControlOwner.none, None, None
        rt.attention = None
        if message:
            self._control_event(rt, message)
        self.on_device_free()

    def _check_lease(self, rt: DeviceRuntime, lease_id: str) -> None:
        if rt.control != ControlOwner.user or rt.lease_id != lease_id:
            raise ControlError("not_controller", "Assuma o controle do aparelho antes de interagir.")
        rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S

    async def manual_input(self, rt: DeviceRuntime, inp: ManualInput) -> None:
        self._check_lease(rt, inp.lease_id)
        self.touch(rt)
        if rt.state != InstanceState.online:
            raise ControlError("offline", "O aparelho não está online.")
        seen = rt.recent_frames.get(inp.frame_id)
        s = self.get_settings()
        if seen is None or rt.frame is None:
            raise ControlError("stale_frame", "A interação se refere a um frame que o backend não reconhece mais.")
        mono, fw, fh = seen
        if (time.monotonic() - mono) * 1000 > max(s.frame_max_age_ms, s.capture_focus_interval_s * 3000):
            raise ControlError("stale_frame", "O frame exibido está antigo demais para uma ação segura.")
        if (fw, fh) != (rt.frame.info.width, rt.frame.info.height):
            raise ControlError("frame_mismatch", "A orientação/tamanho da tela mudou desde o frame exibido.")

        def pt(x: float | None, y: float | None) -> tuple[int, int]:
            if x is None or y is None or not (0 <= x < fw and 0 <= y < fh):
                raise ControlError("bad_coordinates", "Coordenadas fora da tela do aparelho.")
            return int(x), int(y)

        t = inp.type
        if t == "tap":
            x, y = pt(inp.x, inp.y)
            await rt.executor.run(self._manual(rt).tap, x, y, timeout=20, label="toque manual")
            desc = f"toque em ({x},{y})"
        elif t == "long_press":
            x, y = pt(inp.x, inp.y)
            await rt.executor.run(self._manual(rt).long_press, x, y, inp.duration_ms or 800, timeout=25, label="toque longo manual")
            desc = f"toque longo em ({x},{y})"
        elif t == "swipe":
            x, y = pt(inp.x, inp.y)
            x2, y2 = pt(inp.x2, inp.y2)
            await rt.executor.run(self._manual(rt).swipe, x, y, x2, y2, inp.duration_ms or 300, timeout=25, label="arraste manual")
            desc = f"arraste ({x},{y})→({x2},{y2})"
        elif t == "key":
            if not inp.key:
                raise ControlError("bad_input", "Tecla não informada.")
            await rt.executor.run(self._manual(rt).press_key, inp.key, timeout=20, label="tecla manual")
            desc = f"tecla {inp.key}"
        else:  # text — o conteúdo digitado nunca vai para o log (pode ser credencial)
            text = inp.text or ""
            if not text:
                raise ControlError("bad_input", "Texto vazio.")
            if rt.store:
                # Na loja, o que se digita é a conta Google. Sem sessão de automação, este caminho cairia em
                # `adb shell input text '<senha>'` — o segredo na linha de comando do host, visível a qualquer
                # processo que leia argv. Toques e teclas seguem liberados; texto, só na janela do emulador.
                raise ControlError("store_text_blocked",
                                   "Na loja, o texto é digitado direto na janela do emulador — nunca pelo painel. "
                                   "Assim a conta Google não passa pelo backend nem pela linha de comando do adb.")
            try:
                if rt.session.connected or self.io_factory is not None:
                    await rt.executor.run(lambda: rt.io.type_text(text, clear_first=False), timeout=30, label="digitação manual")
                else:
                    await rt.executor.run(rt.adb.input_text_ascii, text, timeout=30, label="digitação manual")
            except AdbError as exc:
                raise ControlError("bad_input", str(exc)) from exc
            desc = f"digitação de {len(text)} caractere(s)"
        self.bus.emit("log", f"{rt.id}: entrada manual — {desc}", instance_id=rt.id)
        rt.capture_now.set()

    def _manual(self, rt: DeviceRuntime) -> Any:
        """Entradas manuais: ADB `input` (independe do Appium). Nos testes, o aparelho falso."""
        return rt.io if self.io_factory is not None else _AdbInput(rt.adb)

    async def quick_key(self, rt: DeviceRuntime, key: str) -> None:
        self._guard_not_running_ai(rt)
        # Pelo mesmo caminho da entrada manual: nos testes, o `rt.adb` é o adb REAL, e uma tecla por ele chegava ao
        # emulador de verdade com o mesmo serial — um HOME da suíte já derrubou uma prova de abertura em andamento.
        await rt.executor.run(self._manual(rt).press_key, key, timeout=20, label=f"tecla {key}")
        rt.capture_now.set()

    # ------------------------------------------------------------------ apps
    def resolve_apk(self, apk_path: str) -> Path:
        """Só instala APKs que estejam dentro dos diretórios permitidos (config paths.apk_dirs)."""
        p = self.cfg.path(apk_path).resolve()
        if p.suffix.lower() != ".apk" or not p.is_file():
            raise ValueError(f"APK não encontrado: {apk_path}")
        if not any(p.is_relative_to(d) for d in self.cfg.apk_dirs):
            allowed = ", ".join(self.cfg.file.paths.apk_dirs)
            raise ValueError(f"Caminho de APK fora dos diretórios permitidos ({allowed}).")
        return p

    async def force_stop_app(self, rt: DeviceRuntime, package: str) -> None:
        """Estado conhecido para a recuperação automática: encerra o app (um app travado/sem desenhar a tela não
        se recupera sozinho — visto num aparelho recém-ligado com pouca memória)."""
        fn = getattr(rt.io, "force_stop", None) if self.io_factory is not None else rt.adb.force_stop
        if fn is None:
            return
        try:
            await rt.executor.run(fn, package, timeout=30, label="encerrar app")
            self.bus.emit("log", f"{rt.id}: {package} encerrado para recomeçar de um estado conhecido", instance_id=rt.id)
        except (DriverError, AdbError) as exc:
            log.warning("%s: force-stop de %s falhou: %s", rt.id, package, exc)

    async def app_version(self, rt: DeviceRuntime, package: str) -> str:
        """Versão instalada do app NESTE aparelho (chave das receitas). Em cache até instalar outro APK ou religar."""
        if package not in rt.app_versions:
            rt.app_versions[package] = await rt.executor.run(rt.io.app_version, package, timeout=25, label="versão do app")
        return rt.app_versions[package]

    async def variant_of(self, rt: DeviceRuntime) -> str:
        """Variante de interface deste aparelho: `idioma/densidade` (ex.: `en-US/xhdpi`).

        Entra na identidade da receita porque a MESMA etapa, no mesmo app e na mesma versão, tem tela diferente em
        idioma diferente. Sem isso a quarentena seria global: um aparelho em outro idioma tiraria de circulação a
        receita que funciona para todos os demais.
        """
        if rt.ui_variant is None:
            locale = (await rt.executor.run(rt.adb.getprop, "ro.product.locale", timeout=20,
                                            label="idioma do aparelho")).strip()
            density = await rt.executor.run(rt.adb.wm_density, timeout=20, label="densidade do aparelho")
            rt.ui_variant = f"{locale or 'desconhecido'}/{_density_bucket(density)}"
        return rt.ui_variant

    async def install_apk(self, rt: DeviceRuntime, app: Any) -> None:
        self._guard_not_running_ai(rt)
        rt.app_versions.clear()
        if rt.state != InstanceState.online:
            raise InstanceBusy("O aparelho precisa estar online para instalar.")
        if not app["apk_path"]:
            raise ValueError("Este app não tem APK configurado (use um app já instalado ou informe o caminho).")
        apk = self.resolve_apk(app["apk_path"])
        self.bus.emit("log", f"{rt.id}: instalando {app['name']}…", instance_id=rt.id)
        try:
            await rt.executor.run(rt.adb.install, str(apk), timeout=300, label="instalar APK")
        except (AdbError, DriverError) as exc:
            msg = f"{rt.id}: instalação de {app['name']} falhou — {exc}"
            rt.attention = str(exc)
            self.bus.emit("log", msg, level="error", instance_id=rt.id)
            self.publish(rt)
            raise
        self.bus.emit("log", f"{rt.id}: {app['name']} instalado.", instance_id=rt.id)

    async def open_app(self, rt: DeviceRuntime, app: Any) -> tuple[bool, str]:
        """Abre o app e CONFERE que ele chegou ao primeiro plano. Devolve `(abriu, detalhe)`.

        O retorno do `am start` é positivo mesmo quando o app cai na abertura — era por isso que "Abrir app"
        virava `succeeded` sem prova nenhuma. A sonda é a mesma do instalador (`wait_for_focus`, janela em foco =
        pacote); sem comprovação quem chamou grava `uncertain` com o motivo, nunca sucesso.
        """
        self._guard_not_running_ai(rt)
        # `am start -n pkg/.Activity` aceita nome relativo; sem activity usa o launcher do pacote
        await rt.executor.run(rt.adb.start_app, app["package"], app["activity"] or None, timeout=40, label="abrir app")
        rt.capture_now.set()
        if self.io_factory is not None:        # testes: aparelho falso, sem janela de verdade para sondar
            return True, "driver de teste"
        pacote = app["package"]
        if await wait_for_focus(rt, pacote, deadline_s=LAUNCH_DEADLINE_S):
            return True, f"{pacote} está em primeiro plano"
        return False, (f"o pedido de abertura foi aceito, mas {pacote} não apareceu em primeiro plano em "
                       f"{LAUNCH_DEADLINE_S:.0f} s; pode estar abrindo, ou ter caído na abertura")

    async def list_packages(self, rt: DeviceRuntime) -> list[str]:
        return await rt.executor.run(rt.adb.list_packages, timeout=40, label="listar pacotes")

    async def hierarchy(self, rt: DeviceRuntime) -> UiTree:
        if not await self.ensure_automation(rt):
            raise DriverError(rt.automation.detail or "Sessão de automação indisponível", effect_possible=False)
        xml = await rt.executor.run(rt.io.page_source, timeout=40, label="hierarquia")
        return parse_hierarchy(xml, max_elements=400)


def _hw_signature(a: Any) -> str:
    """Um snapshot só carrega no MESMO hardware/imagem em que foi salvo."""
    import hashlib

    raw = "|".join(str(v) for v in (a.system_image, a.ram_mb, a.cores, a.width, a.height, a.density, a.gpu_mode,
                                    a.data_partition, *a.extra_emulator_args))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _wait_lock(lock: threading.Lock) -> None:
    with lock:
        pass


class _AdbInput:
    def __init__(self, adb: Adb):
        self.tap, self.long_press, self.swipe, self.press_key = adb.tap, adb.long_press, adb.swipe, adb.keyevent


def _encode_frame(png: bytes) -> tuple[bytes, bytes, int, int]:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    full = io.BytesIO()
    img.save(full, "JPEG", quality=72, optimize=False)
    th = img.resize((THUMB_WIDTH, max(1, round(h * THUMB_WIDTH / w)))) if w > THUMB_WIDTH else img
    thumb = io.BytesIO()
    th.save(thumb, "JPEG", quality=62)
    return full.getvalue(), thumb.getvalue(), w, h


# Faixas padrão do Android. A densidade exata varia por aparelho; a FAIXA é o que muda o desenho da tela.
_DENSITY_BUCKETS = ((140, "ldpi"), (180, "mdpi"), (260, "hdpi"), (340, "xhdpi"), (500, "xxhdpi"))


def _density_bucket(density: int | None) -> str:
    if not density:
        return "desconhecida"
    return next((nome for limite, nome in _DENSITY_BUCKETS if density < limite), "xxxhdpi")
