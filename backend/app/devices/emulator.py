"""Processo do emulador: inicia destacado (sobrevive a reinícios do backend), readota pelo PID
gravado e encerra SOMENTE processos que este projeto iniciou."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import psutil

from ..config import AndroidCfg, Config
from .adb import Adb
from .sdk import NEW_GROUP, NO_WINDOW, SdkTools


class EmulatorError(RuntimeError):
    pass


def build_args(tools: SdkTools, avd_name: str, console_port: int, a: AndroidCfg, *, wipe_data: bool) -> list[str]:
    args = [str(tools.emulator), "-avd", avd_name, "-port", str(console_port), "-no-window", "-no-audio",
            "-no-boot-anim", "-no-snapshot", "-gpu", a.gpu_mode, "-accel", "on", "-no-metrics"]
    if wipe_data:
        args.append("-wipe-data")
    args.extend(a.extra_emulator_args)
    return args


def start_process(cfg: Config, tools: SdkTools, avd_name: str, console_port: int, a: AndroidCfg,
                  *, wipe_data: bool = False) -> int:
    if not tools.emulator.exists():
        raise EmulatorError(f"emulator não encontrado em {tools.emulator}")
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = cfg.logs_dir / f"emulator-{avd_name}.log"
    logf = open(log_path, "ab", buffering=0)  # noqa: SIM115 - herdado pelo processo filho
    try:
        proc = subprocess.Popen(build_args(tools, avd_name, console_port, a, wipe_data=wipe_data),
                                stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=tools.env(), creationflags=NO_WINDOW | NEW_GROUP, close_fds=True)
    finally:
        logf.close()
    return proc.pid


def is_our_emulator(pid: int | None, avd_name: str) -> bool:
    """Confere se o PID gravado ainda é o emulador DESTE AVD (PIDs são reciclados pelo SO)."""
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        cmd = p.cmdline()
        name = p.name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return ("emulator" in name or "qemu" in name) and avd_name in cmd


def qemu_child(pid: int) -> psutil.Process | None:
    try:
        for ch in psutil.Process(pid).children(recursive=True):
            if "qemu" in ch.name().lower():
                return ch
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return None


def process_usage(pid: int) -> tuple[float, float] | None:
    """(rss_mb, cpu_percent) somando o launcher e o qemu filho."""
    try:
        procs = [psutil.Process(pid)]
        procs += procs[0].children(recursive=True)
        rss = sum(p.memory_info().rss for p in procs) / (1024 * 1024)
        cpu = sum(p.cpu_percent(interval=None) for p in procs)
        return round(rss, 1), round(cpu, 1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def stop_process(adb: Adb, pid: int | None, avd_name: str, *, grace_s: float = 25) -> str:
    """Pede desligamento pelo console (`emu kill`); só força o término do PID que nós iniciamos."""
    try:
        adb.emu_kill()
    except Exception:  # noqa: BLE001 - segue para a verificação por PID
        pass
    if not is_our_emulator(pid, avd_name):
        return "solicitado via console (processo não iniciado por este projeto: não forçado)"
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not is_our_emulator(pid, avd_name):
            return "encerrado via console"
        time.sleep(1)
    try:
        parent = psutil.Process(pid)  # type: ignore[arg-type]
        for ch in parent.children(recursive=True):
            ch.terminate()
        parent.terminate()
        psutil.wait_procs([parent], timeout=10)
    except psutil.NoSuchProcess:
        pass
    return "encerrado à força após tempo limite"


def read_log_tail(path: Path, max_bytes: int = 4000) -> str:
    try:
        data = path.read_bytes()[-max_bytes:]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""
