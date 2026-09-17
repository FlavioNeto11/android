"""Composição da aplicação: cria e liga os módulos (dispositivos, automação, planejamento, fila, eventos)."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import timedelta
from typing import Any, Callable

from .automation.appium_server import AppiumServer
from .automation.driver import DeviceIO
from .config import Config, LimitsCfg
from .db import Database, dumps, loads
from .devices.manager import DeviceManager, DeviceRuntime
from .devices.sdk import SdkTools
from .events import EventBus
from .models import AppiumStatus, Health, Problem, SdkStatus
from .planning.provider import AIProvider, build_provider
from .taskqueue.repository import Repository
from .taskqueue.scheduler import Scheduler
from .taskqueue.service import RunService
from .util import now, to_iso

log = logging.getLogger("poc")
VERSION = "0.1.0"


class SettingsStore:
    """Limites editáveis em tempo de execução, persistidos no SQLite (semente: config.yaml)."""

    def __init__(self, db: Database, defaults: LimitsCfg):
        self.db = db
        stored = loads(db.scalar("SELECT value FROM settings WHERE key='limits'"), {}) or {}
        self._value = LimitsCfg.model_validate({**defaults.model_dump(), **stored})

    def get(self) -> LimitsCfg:
        return self._value

    def update(self, patch: dict[str, Any]) -> LimitsCfg:
        self._value = LimitsCfg.model_validate({**self._value.model_dump(), **patch})
        self.db.execute("INSERT INTO settings(key, value) VALUES ('limits', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (dumps(self._value.model_dump()),))
        return self._value


class AppState:
    def __init__(self, cfg: Config, *, provider: AIProvider | None = None,
                 io_factory: Callable[[DeviceRuntime], DeviceIO] | None = None, manage_appium: bool = True):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.db = Database(cfg.db_path)
        self.db.migrate()
        self.bus = EventBus(self.db)
        self.tools = SdkTools(cfg)
        self.settings = SettingsStore(self.db, cfg.file.limits)
        self.appium = AppiumServer(cfg, self.tools)
        self.manage_appium = manage_appium
        self._seed_apps()
        self.devices = DeviceManager(cfg, self.db, self.bus, self.tools, self.appium,
                                     settings_getter=self.settings.get, io_factory=io_factory)
        self.devices.seed()
        self.provider: AIProvider = provider or build_provider(cfg)
        self.repo = Repository(self.db, self.bus, cfg.evidence_dir)
        self.scheduler = Scheduler(cfg, self.repo, self.devices, self.provider, self.settings.get)
        self.runs = RunService(self.repo, self.scheduler, self.devices, self.provider)
        self._diag_cache: dict[str, Any] | None = None
        self._bg: list[asyncio.Task[Any]] = []

    def _seed_apps(self) -> None:
        for a in self.cfg.file.apps:
            if self.db.one("SELECT id FROM apps WHERE id=?", (a.id,)):
                continue
            self.db.execute(
                "INSERT INTO apps(id, name, package, activity, apk_path, nav_hints, known_selectors, builtin) VALUES (?,?,?,?,?,?,?,?)",
                (a.id, a.name, a.package, a.activity, a.apk_path, a.nav_hints,
                 dumps(a.known_selectors) if a.known_selectors else None, int(a.builtin)))

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        self.bus.bind_loop(asyncio.get_running_loop())
        if self.manage_appium and self.cfg.file.appium.autostart:
            ok = await asyncio.to_thread(self.appium.start)
            log.info("Appium: %s (%s)", "ok" if ok else "indisponível", self.appium.detail)
        await self.devices.start()
        await self.scheduler.start()
        self.runs.resume_planning_after_restart()
        self._bg.append(asyncio.create_task(self._retention_loop(), name="retention"))
        self.bus.emit("log", f"Backend iniciado (v{VERSION}). Provedor de IA: {self.provider.name}"
                      + (" — MODO SIMULADO" if self.provider.simulated else ""))

    async def stop(self) -> None:
        for t in self._bg:
            t.cancel()
        await self.scheduler.stop()
        await self.devices.shutdown()
        if self.manage_appium:
            await asyncio.to_thread(self.appium.stop)
        self.db.close()

    async def _retention_loop(self) -> None:
        while True:
            try:
                s = self.settings.get()
                cutoff = to_iso(now() - timedelta(days=s.log_retention_days))
                removed = self.bus.purge_older_than(cutoff)
                ev_cut = to_iso(now() - timedelta(days=s.evidence_retention_days))
                old = self.db.query("SELECT DISTINCT run_id FROM evidence WHERE ts < ? AND run_id IN "
                                    "(SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?)", (ev_cut, ev_cut))
                for r in old:
                    shutil.rmtree(self.cfg.evidence_dir / r["run_id"], ignore_errors=True)
                    self.db.execute("DELETE FROM evidence WHERE run_id=?", (r["run_id"],))
                if removed or old:
                    log.info("retenção: %s eventos e %s execuções com evidências removidos", removed, len(old))
            except Exception:  # noqa: BLE001
                log.exception("retenção")
            await asyncio.sleep(6 * 3600)

    # ------------------------------------------------------------------ saúde
    def health(self) -> Health:
        problems: list[Problem] = []
        sdk_ok = self.tools.found()
        if not sdk_ok:
            problems.append(Problem(code="sdk_missing", message=f"Android SDK não encontrado em {self.cfg.sdk_root}.",
                                    hint="Rode scripts/install-prereqs.ps1 ou ajuste android.sdk_root / ANDROID_SDK_ROOT."))
        appium_up = self.appium.is_up(timeout=1.0)
        if not appium_up:
            problems.append(Problem(code="appium_down", message=self.appium.detail or "Servidor Appium não está respondendo.",
                                    hint="Verifique tools/appium (npm ci) e data/logs/appium.log; o controle manual segue funcionando."))
        ai = self.provider.status()
        if not ai.configured:
            problems.append(Problem(code="ai_not_configured", message="Provedor de IA sem chave.",
                                    hint="Defina ANTHROPIC_API_KEY no .env e reinicie o backend. Gerenciamento e controle manual continuam disponíveis."))
        if ai.simulated:
            problems.append(Problem(code="ai_simulated", message="MODO SIMULADO ativo: nenhuma IA é consultada.",
                                    hint="Use AI_PROVIDER=anthropic no .env para o provedor real."))
        diag = self._diag_cache
        accel = diag["acceleration"]["detail"] if diag else None
        if diag and not diag["acceleration"]["usable"]:
            problems.append(Problem(code="no_acceleration", message="Aceleração de virtualização indisponível.",
                                    hint="No Windows, habilite 'Windows Hypervisor Platform' (WHPX) e reinicie."))
        hard = {"sdk_missing", "no_acceleration"}
        status = "error" if any(p.code in hard for p in problems) else ("degraded" if problems else "ok")
        emu_version = next((t["version"] for t in (diag or {}).get("tools", []) if t["name"] == "Android Emulator"), None)
        return Health(status=status, version=VERSION, ai=ai,
                      appium=AppiumStatus(running=appium_up, port=self.cfg.file.appium.port, detail=self.appium.detail),
                      sdk=SdkStatus(found=sdk_ok, root=str(self.cfg.sdk_root), emulator_version=emu_version, accel=accel),
                      problems=problems)

    async def diagnostics(self, refresh: bool = False) -> dict[str, Any]:
        from .devices import diagnostics

        if refresh or self._diag_cache is None:
            self._diag_cache = await asyncio.to_thread(diagnostics.collect, self.cfg, self.tools, self.db)
        return self._diag_cache
