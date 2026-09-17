"""Servidor Appium local (um processo, N sessões), preso a 127.0.0.1. O backend só encerra o
servidor que ele mesmo iniciou; se já houver um Appium respondendo na porta, ele é reutilizado."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

from ..config import Config
from ..devices.sdk import NEW_GROUP, NO_WINDOW, SdkTools

log = logging.getLogger("poc.appium")


class AppiumServer:
    def __init__(self, cfg: Config, tools: SdkTools):
        self.cfg = cfg
        self.tools = tools
        self.pid: int | None = None
        self.detail: str | None = None

    @property
    def url(self) -> str:
        a = self.cfg.file.appium
        return f"http://{a.host}:{a.port}"

    def is_up(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/status", timeout=timeout) as resp:  # noqa: S310 - loopback
                return bool(json.loads(resp.read()).get("value", {}).get("ready", False))
        except (urllib.error.URLError, OSError, ValueError):
            return False

    @property
    def _pid_file(self) -> Path:
        return self.cfg.data_dir / "appium.pid"

    def _own_orphan(self) -> int | None:
        """Appium deixado por um backend anterior deste projeto que morreu sem desligar: só é readotado se o
        PID gravado ainda existir E a linha de comando apontar para o Appium de tools/appium."""
        try:
            pid = int(self._pid_file.read_text(encoding="ascii").strip())
            cmd = " ".join(psutil.Process(pid).cmdline()).lower()
        except (OSError, ValueError, psutil.Error):
            return None
        entry = str(self.cfg.path(self.cfg.file.appium.dir) / "node_modules" / "appium").lower()
        return pid if entry in cmd else None

    def start(self, wait_s: float = 60) -> bool:
        if self.is_up():
            self.pid = self._own_orphan()
            self.detail = (f"readotado: iniciado por este projeto (pid {self.pid})" if self.pid
                           else "reutilizando servidor externo já em execução (não será encerrado por este projeto)")
            return True
        a = self.cfg.file.appium
        appium_dir = self.cfg.path(a.dir)
        entry = appium_dir / "node_modules" / "appium" / "index.js"
        node = shutil.which("node")
        if not node or not entry.exists():
            self.detail = (f"Appium não instalado em {appium_dir}. Rode scripts/install-prereqs.ps1 "
                           "(ou `npm ci` em tools/appium).")
            return False
        self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        env = self.tools.env()
        env["APPIUM_HOME"] = str(appium_dir)
        logf = open(self.cfg.logs_dir / "appium.log", "ab", buffering=0)  # noqa: SIM115
        try:
            proc = subprocess.Popen(
                [node, str(entry), "server", "--address", a.host, "--port", str(a.port),
                 "--log-level", "info", "--log-timestamp", "--local-timezone"],
                cwd=str(appium_dir), env=env, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW | NEW_GROUP)
        finally:
            logf.close()
        self.pid = proc.pid
        self._pid_file.write_text(str(proc.pid), encoding="ascii")
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.is_up():
                self.detail = f"iniciado por este projeto (pid {self.pid})"
                return True
            if proc.poll() is not None:
                self.detail = f"Appium encerrou ao iniciar (código {proc.returncode}); veja data/logs/appium.log"
                self.pid = None
                return False
            time.sleep(1)
        self.detail = "Appium não respondeu a tempo; veja data/logs/appium.log"
        return False

    def stop(self) -> None:
        """Encerra apenas o processo que este backend iniciou."""
        pid, self.pid = self.pid, None
        if not pid:
            return
        try:
            parent = psutil.Process(pid)
            for ch in parent.children(recursive=True):
                ch.terminate()
            parent.terminate()
            psutil.wait_procs([parent], timeout=8)
        except psutil.NoSuchProcess:
            pass
        self._pid_file.unlink(missing_ok=True)
