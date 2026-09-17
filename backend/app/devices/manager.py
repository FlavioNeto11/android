"""Gerência das instâncias: ciclo de vida do emulador, captura de frames, sessão de automação,
lease de controle (IA × usuário) e entradas manuais — tudo coordenado pelo executor exclusivo."""
from __future__ import annotations

import asyncio
import io
import logging
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
from .sdk import SdkTools

log = logging.getLogger("poc.devices")

THUMB_WIDTH = 360
MANUAL_LEASE_TTL_S = 600


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
    """Semáforo redimensionável em tempo de execução."""

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
        self.serial = f"emulator-{self.console_port}"
        self.ports = InstancePorts(system=row["system_port"], mjpeg=row["mjpeg_port"],
                                   chromedriver=row["chromedriver_port"])
        self.pid: int | None = row["emulator_pid"]
        self.boot_seconds: float | None = row["boot_seconds"]
        self.state = InstanceState.stopped
        self.state_detail: str | None = None
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
        self._bg: list[asyncio.Task[Any]] = []
        self.last_metrics: Metrics | None = None

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
                     c.default_app if self.db.one("SELECT id FROM apps WHERE id=?", (c.default_app,)) else None,
                     c.accounts.get(iid)))
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
        for rt in self.devices.values():
            for t in rt.tasks.values():
                t.cancel()
            await asyncio.to_thread(rt.session.close)
            rt.executor.shutdown()

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
            attention=rt.attention, resources=rt.resources)

    def list_dtos(self) -> list[InstanceDTO]:
        return [self.dto(rt) for rt in self.devices.values()]

    def publish(self, rt: DeviceRuntime, message: str | None = None, level: str = "info") -> None:
        self.bus.emit("instance.updated", message or f"{rt.id}: {rt.state.value}", level=level, instance_id=rt.id,
                      data={"instance": self.dto(rt).model_dump(mode="json")})

    def _set_state(self, rt: DeviceRuntime, state: InstanceState, detail: str | None = None,
                   *, level: str = "info", attention: str | None = None) -> None:
        rt.state, rt.state_detail = state, detail
        if attention is not None or state in (InstanceState.online, InstanceState.stopped):
            rt.attention = attention
        self.publish(rt, f"{rt.id}: {state.value}" + (f" — {detail}" if detail else ""), level)

    # ------------------------------------------------------------------ adoção / monitor
    async def _adopt(self, rt: DeviceRuntime) -> None:
        if self.io_factory is not None:       # testes: aparelho falso, sem SDK/ADB/Appium
            rt.state = InstanceState.online
            rt.automation = AutomationInfo(state="ready", detail="driver de teste")
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
            rt.state = InstanceState.stopped
            if rt.pid:
                self._save_pid(rt, None)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(6)
            now_m = time.monotonic()
            for rt in self.devices.values():
                try:
                    if rt.state == InstanceState.online and rt.pid and not emu.is_our_emulator(rt.pid, rt.avd_name):
                        self._on_device_lost(rt, "O processo do emulador encerrou inesperadamente.")
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
                self._set_state(rt, InstanceState.absent, str(exc), level="error", attention=str(exc))
                return
            self._set_state(rt, InstanceState.stopped, "AVD criado")

    async def start_instance(self, rt: DeviceRuntime) -> None:
        if rt.state in (InstanceState.online, InstanceState.booting, InstanceState.stopping):
            return
        rt.state, rt.state_detail = InstanceState.booting, "na fila de inicialização"
        self.publish(rt)
        rt.tasks["boot"] = asyncio.create_task(self._boot(rt), name=f"boot-{rt.id}")

    async def _boot(self, rt: DeviceRuntime) -> None:
        a = self.cfg.instance_android(rt.id)
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
                        self._set_state(rt, InstanceState.stopped, msg, level="warn", attention=msg)
                        self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "capacity", dumps({
                            "instance_id": rt.id, "refused": True, "online": online, "mem_available_mb": round(free_mb),
                            "inflight_reserved_mb": round(inflight), "needed_mb": est})))
                        return
                    if not self.avd.exists(rt.avd_name):
                        self._set_state(rt, InstanceState.booting, "criando AVD…")
                        await asyncio.to_thread(self.avd.create, rt.avd_name, a)
                    else:
                        await asyncio.to_thread(self.avd.apply_hardware, rt.avd_name, a)
                    t0 = time.monotonic()
                    wipe, rt.wipe_next_boot = rt.wipe_next_boot, False
                    await asyncio.to_thread(self._spawn, rt, a, wipe)
                    self._set_state(rt, InstanceState.booting, "emulador iniciado" + (" (dados apagados)" if wipe else ""))
                    await self._wait_boot(rt, t0)
            except (AvdError, emu.EmulatorError, OSError) as exc:
                self._set_state(rt, InstanceState.error, str(exc), level="error", attention=str(exc))

    async def _wait_boot(self, rt: DeviceRuntime, t0: float, *, adopted: bool = False) -> None:
        timeout = self.cfg.instance_android(rt.id).boot_timeout_s
        phase = "aguardando o Android iniciar"
        booted_at: float | None = None
        while True:
            elapsed = time.monotonic() - t0
            if elapsed > timeout:
                self._set_state(rt, InstanceState.error, f"Boot excedeu {timeout}s", level="error",
                                attention="O boot não concluiu a tempo. Veja data/logs/emulator-%s.log" % rt.avd_name)
                return
            if rt.pid and not emu.is_our_emulator(rt.pid, rt.avd_name):
                tail = emu.read_log_tail(self.cfg.logs_dir / f"emulator-{rt.avd_name}.log", 600)
                self._save_pid(rt, None)
                self._set_state(rt, InstanceState.error, "O emulador encerrou durante o boot.", level="error",
                                attention=f"Emulador encerrou no boot. Final do log: {tail[-300:]}")
                return
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
        rt.boot_seconds = round(time.monotonic() - t0, 1)
        if not adopted:
            self.db.execute("UPDATE instances SET boot_seconds=? WHERE id=?", (rt.boot_seconds, rt.id))
            vm = psutil.virtual_memory()
            self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "boot", dumps({
                "instance_id": rt.id, "boot_seconds": rt.boot_seconds,
                "online_after": sum(1 for d in self.devices.values() if d.state == InstanceState.online) + 1,
                "mem_available_gb": round(vm.available / 2**30, 1), "image": self.cfg.instance_android(rt.id).system_image})))
        self._set_state(rt, InstanceState.online, f"pronto em {rt.boot_seconds:.0f}s")
        self._start_online_tasks(rt)
        self.on_device_free()

    def _start_online_tasks(self, rt: DeviceRuntime) -> None:
        if "capture" not in rt.tasks or rt.tasks["capture"].done():
            rt.tasks["capture"] = asyncio.create_task(self._capture_loop(rt), name=f"capture-{rt.id}")
        if "automation" not in rt.tasks or rt.tasks["automation"].done():
            rt.tasks["automation"] = asyncio.create_task(self.ensure_automation(rt), name=f"automation-{rt.id}")

    def _spawn(self, rt: DeviceRuntime, a: Any, wipe: bool) -> None:
        """Inicia o emulador e grava o PID na MESMA seção crítica: um cancelamento nunca deixa processo órfão."""
        with rt.spawn_lock:
            pid = emu.start_process(self.cfg, self.tools, rt.avd_name, rt.console_port, a, wipe_data=wipe)
            self._save_pid(rt, pid)

    async def stop_instance(self, rt: DeviceRuntime, *, force: bool = False) -> None:
        if not force:
            self._guard_not_running_ai(rt)
        if rt.state in (InstanceState.stopped, InstanceState.absent):
            return
        for name in ("boot", "capture", "automation"):
            t = rt.tasks.pop(name, None)
            if t:
                t.cancel()
        await asyncio.to_thread(_wait_lock, rt.spawn_lock)   # se o processo estava nascendo, o PID já foi gravado
        if rt.pid is None and rt.state == InstanceState.booting:
            self._set_state(rt, InstanceState.stopped, "boot cancelado antes de iniciar o emulador")
            return
        async with rt.op_lock:
            self._set_state(rt, InstanceState.stopping, "encerrando o emulador…")
            await asyncio.to_thread(rt.session.close)
            rt.automation = AutomationInfo()
            how = await asyncio.to_thread(emu.stop_process, rt.adb, rt.pid, rt.avd_name)
            self._save_pid(rt, None)
            rt.frame = None
            if rt.control == ControlOwner.user:
                self._end_user_control(rt, None)
            self._set_state(rt, InstanceState.stopped, how)

    async def restart_instance(self, rt: DeviceRuntime) -> None:
        await self.stop_instance(rt)
        await self.start_instance(rt)

    async def reset_instance(self, rt: DeviceRuntime) -> None:
        """Reset explícito: apaga dados do usuário deste AVD (apps, contas, sessões) no próximo boot."""
        await self.stop_instance(rt)
        rt.wipe_next_boot = True
        self.db.execute("UPDATE instances SET account_evidence=NULL, account_evidence_ts=NULL WHERE id=?", (rt.id,))
        self.bus.emit("log", f"{rt.id}: dados apagados a pedido do usuário (reset).", level="warn", instance_id=rt.id)
        await self.start_instance(rt)

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
        tree = parse_hierarchy(xml, max_elements=self.cfg.file.ai.max_hierarchy_elements)
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
        await rt.executor.run(rt.adb.keyevent, key, timeout=20, label=f"tecla {key}")
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

    async def install_apk(self, rt: DeviceRuntime, app: Any) -> None:
        self._guard_not_running_ai(rt)
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

    async def open_app(self, rt: DeviceRuntime, app: Any) -> None:
        self._guard_not_running_ai(rt)
        # `am start -n pkg/.Activity` aceita nome relativo; sem activity usa o launcher do pacote
        await rt.executor.run(rt.adb.start_app, app["package"], app["activity"] or None, timeout=40, label="abrir app")
        rt.capture_now.set()

    async def list_packages(self, rt: DeviceRuntime) -> list[str]:
        return await rt.executor.run(rt.adb.list_packages, timeout=40, label="listar pacotes")

    async def hierarchy(self, rt: DeviceRuntime) -> UiTree:
        if not await self.ensure_automation(rt):
            raise DriverError(rt.automation.detail or "Sessão de automação indisponível", effect_possible=False)
        xml = await rt.executor.run(rt.io.page_source, timeout=40, label="hierarquia")
        return parse_hierarchy(xml, max_elements=400)


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
