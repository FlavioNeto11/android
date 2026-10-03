"""Diagnóstico do host onde os emuladores rodam (este backend roda no mesmo host).
O diagnóstico completo do Windows (recursos DISM, bcdedit) é feito por scripts/diagnose.ps1, cujo
resultado (data/diagnostics-host.json) é anexado aqui quando existir."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from ..config import Config
from ..db import Database, loads
from ..util import now_iso
from .sdk import NO_WINDOW, SdkTools, ambiente_dos_filhos


def _run(cmd: list[str], timeout: float = 25) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             env=ambiente_dos_filhos(), creationflags=NO_WINDOW)   # 29.47: sem os segredos
        return ((res.stdout or "") + (res.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"erro: {exc}"


def _tool(name: str, cmd: list[str] | None, pattern: str | None = None, path: str | None = None) -> dict[str, Any]:
    exe = path or (shutil.which(cmd[0]) if cmd else None)
    if not exe or (path and not Path(path).exists()):
        return {"name": name, "found": False, "version": None, "path": None}
    out = _run(cmd) if cmd else ""
    m = re.search(pattern, out) if pattern else None
    return {"name": name, "found": True, "version": (m.group(1) if m else out.splitlines()[0][:120] if out else None),
            "path": exe}


def medicoes_recentes(db: Database, limite: int = 60) -> list[dict[str, Any]]:
    """As últimas medições para o Diagnóstico. As janelas de métricas de desempenho (kind='metricas', uma a cada
    15 min) e a fração de interrupção do convidado (kind='irq', uma por sonda de saúde: duas por minuto por
    aparelho) têm rota própria (GET /api/desempenho); listadas aqui, empurrariam boot, relógio e capacidade para
    fora das 60 linhas."""
    return [{"ts": r["ts"], "kind": r["kind"], **loads(r["data"], {})}
            for r in db.query("SELECT * FROM measurements WHERE kind NOT IN ('metricas', 'irq') ORDER BY id DESC"
                              " LIMIT ?", (limite,))]


def collect(cfg: Config, tools: SdkTools, db: Database) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disks = {}          # valores simples (texto) para o painel exibir em tabela chave/valor
    for label, p in (("disk_project", cfg.root), ("disk_sdk", cfg.sdk_root if cfg.sdk_root.exists() else cfg.root)):
        try:
            du = shutil.disk_usage(p)
            disks[label] = f"{p} — {du.free / 2**30:.0f} GB livres de {du.total / 2**30:.0f} GB"
        except OSError:
            disks[label] = f"{p} — indisponível"
    cpu_name = platform.processor()
    hypervisor_present = None
    if os.name == "nt":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "$c=Get-CimInstance Win32_Processor|Select -First 1;"
                    "$s=Get-CimInstance Win32_ComputerSystem;"
                    "\"$($c.Name)|$($s.HypervisorPresent)|$($s.Model)\""], timeout=30)
        parts = out.split("|")
        if len(parts) >= 2:
            cpu_name, hypervisor_present = parts[0].strip(), parts[1].strip().lower() == "true"

    accel_out = _run([str(tools.emulator), "-accel-check"], timeout=40) if tools.emulator.exists() else "emulator ausente"
    accel_ok = "is installed and usable" in accel_out or re.search(r"accel:\s*0", accel_out) is not None
    accel_line = next((ln.strip() for ln in accel_out.splitlines() if "usable" in ln or "WHPX" in ln or "AEHD" in ln
                       or "KVM" in ln or "HAXM" in ln or "Hypervisor" in ln), accel_out[:200])

    appium_pkg = cfg.path(cfg.file.appium.dir) / "node_modules" / "appium" / "package.json"
    ua2_pkg = cfg.path(cfg.file.appium.dir) / "node_modules" / "appium-uiautomator2-driver" / "package.json"

    def pkg_version(p: Path) -> str | None:
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            return None

    images = []
    si = cfg.sdk_root / "system-images"
    if si.exists():
        for api in si.iterdir():
            for tag in api.iterdir() if api.is_dir() else []:
                for abi in tag.iterdir() if tag.is_dir() else []:
                    images.append(f"system-images;{api.name};{tag.name};{abi.name}")

    tool_list = [
        _tool("Python", [sys.executable, "--version"], r"Python (\S+)"),
        _tool("Java", ["java", "-version"], r'version "([^"]+)"'),
        _tool("Node.js", ["node", "--version"], r"v?(\S+)"),
        _tool("adb", [str(tools.adb), "version"], r"Android Debug Bridge version (\S+)", path=str(tools.adb)),
        _tool("Android Emulator", [str(tools.emulator), "-version"], r"emulator version (\S+)", path=str(tools.emulator)),
        {"name": "avdmanager/sdkmanager", "found": tools.avdmanager.exists(), "version": None, "path": str(tools.avdmanager)},
        {"name": "Appium", "found": appium_pkg.exists(), "version": pkg_version(appium_pkg), "path": str(appium_pkg.parent)},
        {"name": "Driver UiAutomator2", "found": ua2_pkg.exists(), "version": pkg_version(ua2_pkg), "path": str(ua2_pkg.parent)},
    ]

    emus = [p for p in psutil.process_iter(["name", "memory_info"]) if "qemu-system" in (p.info["name"] or "").lower()]
    rss = [p.info["memory_info"].rss / 2**30 for p in emus if p.info["memory_info"]]
    per_instance = round(sum(rss) / len(rss), 2) if rss else 3.3
    reserve_gb = 4.0
    fit_more = max(0, int((vm.available / 2**30 - reserve_gb) // per_instance))
    capacity = {
        "running_emulators": len(emus),
        "per_instance_gb": per_instance,
        "per_instance_source": "medido nos emuladores em execução" if rss else "estimativa (imagem Android 34, piso de 2560 MB)",
        "mem_available_gb": round(vm.available / 2**30, 1),
        "reserve_gb": reserve_gb,
        "additional_instances_that_fit": fit_more,
        "estimated_max_simultaneous": min(cfg.file.instances.count, len(emus) + fit_more),
        "configured_instances": cfg.file.instances.count,
        "note": f"A configuração mantém {cfg.file.instances.count} instâncias; o limite efetivo é a memória livre "
                "do host neste momento.",
    }
    measurements = medicoes_recentes(db)
    def read_json(name: str) -> Any:
        f = cfg.data_dir / name
        try:
            return json.loads(f.read_text(encoding="utf-8-sig")) if f.exists() else None
        except ValueError:
            return None

    image_probes = []                                    # scripts/probe-image.ps1 (custo real de cada imagem)
    probe_file = cfg.logs_dir / "image-probe-results.jsonl"
    if probe_file.exists():
        for ln in probe_file.read_text(encoding="utf-8-sig").splitlines():
            try:
                p = json.loads(ln)
                image_probes.append({k: p.get(k) for k in ("image", "android_release", "requested_ram_mb", "guest_memtotal",
                                                           "qemu_ws_gb", "qemu_private_gb", "first_boot_s")})
            except ValueError:
                continue
    host_script = read_json("diagnostics-host.json")     # scripts/diagnose.ps1
    scale_test = read_json("scale-test-results.json")    # scripts/scale-test.ps1
    return {
        "collected_at": now_iso(),
        "measured_on": "este host (o backend roda na mesma máquina dos emuladores)",
        "host": {"os": f"{platform.system()} {platform.release()} ({platform.version()})", "arch": platform.machine(),
                 "cpu": cpu_name, "cores_physical": psutil.cpu_count(logical=False), "cores_logical": psutil.cpu_count(),
                 "mem_total_gb": round(vm.total / 2**30, 1), "mem_available_gb": round(vm.available / 2**30, 1),
                 "swap_total_gb": round(sw.total / 2**30, 1), **disks},
        "tools": tool_list,
        "sdk": {"root": str(cfg.sdk_root), "found": tools.found(), "system_images": sorted(images),
                "configured_image": cfg.file.android.system_image,
                "configured_image_installed": tools.system_image_dir(cfg.file.android.system_image).exists(),
                "override_images": [{"instance_id": iid, "image": img,
                                     "installed": tools.system_image_dir(img).exists()}
                                    for iid, img in cfg.override_images().items()]},
        "acceleration": {"usable": bool(accel_ok), "detail": accel_line, "hypervisor_present": hypervisor_present,
                         "raw": accel_out[:600],
                         "guidance": "No Windows com Hyper-V ativo, o acelerador é o WHPX (Windows Hypervisor Platform). "
                                     "AEHD só funciona com Hyper-V desligado. Ver developer.android.com/studio/run/emulator-acceleration"},
        "capacity": capacity,
        "measurements": measurements,
        "scale_test": scale_test,
        "image_probes": image_probes,
        "host_script": host_script,
    }
