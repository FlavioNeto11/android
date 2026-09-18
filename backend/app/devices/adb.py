"""ADB sempre direcionado a um serial específico (`adb -s <serial> …`). Chamadas bloqueantes:
devem rodar na thread do executor do dispositivo, nunca no event loop."""
from __future__ import annotations

import re
import subprocess
import time

from .sdk import NO_WINDOW, SdkTools

PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
ACTIVITY_RE = re.compile(r"^[A-Za-z0-9_.$]+$")

KEYCODES = {"back": 4, "home": 3, "recents": 187, "enter": 66, "delete": 67, "wakeup": 224, "menu": 82}


class AdbError(RuntimeError):
    pass


class Adb:
    def __init__(self, tools: SdkTools, serial: str):
        self.tools = tools
        self.serial = serial

    # -- base -----------------------------------------------------------------
    def _run(self, args: list[str], *, timeout: float = 30, binary: bool = False) -> subprocess.CompletedProcess:
        cmd = [str(self.tools.adb), "-s", self.serial, *args]
        try:
            return subprocess.run(cmd, capture_output=True, timeout=timeout, env=self.tools.env(),
                                  creationflags=NO_WINDOW, text=not binary,
                                  **({} if binary else {"encoding": "utf-8", "errors": "replace"}))
        except subprocess.TimeoutExpired as exc:
            # Só o subcomando entra na mensagem: os argumentos podem carregar conteúdo digitado, e esta mensagem
            # vira evento, log e corpo de resposta HTTP.
            raise AdbError(f"adb {args[0] if args else '?'} excedeu {timeout}s em {self.serial}") from exc

    def shell(self, command: str, *, timeout: float = 30) -> str:
        """`command` é montado apenas a partir de constantes e valores validados (nunca texto livre)."""
        res = self._run(["shell", command], timeout=timeout)
        if res.returncode != 0:
            raise AdbError((res.stderr or res.stdout or "").strip() or f"adb shell falhou ({res.returncode})")
        return res.stdout

    # -- estado -----------------------------------------------------------------
    def state(self) -> str | None:
        res = self._run(["get-state"], timeout=8)
        return res.stdout.strip() if res.returncode == 0 else None

    def boot_completed(self) -> bool:
        res = self._run(["shell", "getprop sys.boot_completed"], timeout=8)
        return res.returncode == 0 and res.stdout.strip() == "1"

    def ui_ready(self) -> bool:
        """Launcher no ar (não FallbackHome) e sem keyguard — antes disso o screenshot sai preto."""
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus|isKeyguardShowing'"], timeout=10).stdout
        if "FallbackHome" in out or "mCurrentFocus=null" in out or "mCurrentFocus" not in out:
            return False
        return "isKeyguardShowing=true" not in out

    def current_focus(self) -> tuple[str | None, str | None]:
        out = self._run(["shell", "dumpsys window | grep -E 'mCurrentFocus'"], timeout=10).stdout
        m = re.search(r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^/\s}]+)/([^\s}]+)\}", out)
        if m:
            return m.group(1), m.group(2)
        return None, None

    def prepare_for_automation(self) -> None:
        """Ajustes idempotentes pós-boot: sem animações, tela sempre ligada, sem keyguard, sem diálogos de ANR."""
        self.shell(
            "settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
            "settings put global animator_duration_scale 0; settings put system screen_off_timeout 2147483647; "
            "svc power stayon true; settings put global hide_error_dialogs 1; "
            # com teclado físico presente (hw.keyboard=yes) o teclado virtual não cobre botões da tela
            "settings put secure show_ime_with_hard_keyboard 0; "
            "locksettings set-disabled true; input keyevent 224; wm dismiss-keyguard",
            timeout=40,
        )

    def wm_size(self) -> tuple[int, int] | None:
        m = re.search(r"(\d+)x(\d+)", self._run(["shell", "wm size"], timeout=8).stdout)
        return (int(m.group(1)), int(m.group(2))) if m else None

    # -- tela -------------------------------------------------------------------
    def screencap_png(self, *, timeout: float = 20) -> bytes:
        res = self._run(["exec-out", "screencap", "-p"], timeout=timeout, binary=True)
        if res.returncode != 0 or not res.stdout.startswith(b"\x89PNG"):
            raise AdbError(f"screencap falhou em {self.serial}")
        return res.stdout

    # -- entrada -------------------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}", timeout=15)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}", timeout=20)

    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
        self.swipe(x, y, x, y, duration_ms)

    def keyevent(self, key: str) -> None:
        self.shell(f"input keyevent {KEYCODES[key]}", timeout=15)

    def input_text_ascii(self, text: str) -> None:
        """Fallback sem Appium: apenas ASCII imprimível. Texto vai entre aspas simples para o shell do aparelho."""
        if not text.isascii() or any(ord(c) < 32 for c in text):
            raise AdbError("Sem sessão de automação, só é possível digitar texto ASCII simples.")
        escaped = text.replace("\\", "\\\\").replace("'", "'\\''").replace("%", "\\%").replace(" ", "%s")
        self.shell(f"input text '{escaped}'", timeout=20)

    # -- apps ---------------------------------------------------------------------
    def install(self, apk_path: str, *, timeout: float = 240) -> str:
        res = self._run(["install", "-r", "-g", apk_path], timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "Success" not in out:
            raise AdbError(_explain_install_failure(out))
        return out.strip()

    def install_multiple(self, apk_paths: list[str], *, timeout: float = 600) -> str:
        """Conjunto de splits é unidade atômica: ou entra inteiro, ou o `pm` recusa e nada é aplicado."""
        if not apk_paths:
            raise AdbError("nenhum APK informado para instalar")
        res = self._run(["install-multiple", "-r", "-g", *apk_paths], timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "Success" not in out:
            raise AdbError(_explain_install_failure(out))
        return out.strip()

    def uninstall(self, package: str, *, timeout: float = 120) -> None:
        _check_package(package)
        res = self._run(["uninstall", package], timeout=timeout)
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "Success" not in out:
            raise AdbError((out.strip() or "desinstalação falhou")[:300])

    def clear_data(self, package: str) -> None:
        """Apaga os dados do app — inclusive a sessão. Nunca é efeito colateral de outra operação."""
        _check_package(package)
        out = self.shell(f"pm clear {package}", timeout=60)
        if "Success" not in out:
            raise AdbError((out.strip() or "pm clear falhou")[:300])

    def pm_path(self, package: str) -> list[str]:
        """Caminhos do base e de cada split instalado. Lista vazia = pacote ausente."""
        _check_package(package)
        res = self._run(["shell", f"pm path {package}"], timeout=20)
        return sorted(ln.split(":", 1)[1].strip() for ln in (res.stdout or "").splitlines() if ln.startswith("package:"))

    def package_info(self, package: str) -> dict[str, object] | None:
        """Estado REALMENTE instalado, lido do aparelho. `None` quando o pacote não está lá."""
        _check_package(package)
        out = self._run(["shell", f"dumpsys package {package}"], timeout=30).stdout or ""
        version_name = re.search(r"versionName=(\S+)", out)
        version_code = re.search(r"versionCode=(\d+)", out)
        if not (version_name or version_code):
            return None
        splits = re.search(r"splits=\[([^\]]*)\]", out)
        first = re.search(r"firstInstallTime=(.+)", out)
        last = re.search(r"lastUpdateTime=(.+)", out)
        return {
            "version_name": version_name.group(1) if version_name else None,
            "version_code": int(version_code.group(1)) if version_code else None,
            "splits": [s.strip() for s in splits.group(1).split(",") if s.strip()] if splits else [],
            "first_install_time": first.group(1).strip() if first else None,
            "last_update_time": last.group(1).strip() if last else None,
            "paths": self.pm_path(package),
        }

    def getprop(self, name: str) -> str:
        if not re.match(r"^[A-Za-z0-9._]{1,60}$", name):
            raise AdbError("propriedade inválida")
        return self._run(["shell", f"getprop {name}"], timeout=10).stdout.strip()

    def wm_density(self) -> int | None:
        out = self._run(["shell", "wm density"], timeout=10).stdout or ""
        m = re.search(r"Physical density:\s*(\d+)", out)
        override = re.search(r"Override density:\s*(\d+)", out)
        chosen = override or m
        return int(chosen.group(1)) if chosen else None

    def is_installed(self, package: str) -> bool:
        _check_package(package)
        return f"package:{package}" in self._run(["shell", f"pm list packages {package}"], timeout=20).stdout.split()

    def list_packages(self, third_party_only: bool = False) -> list[str]:
        flag = " -3" if third_party_only else ""
        out = self._run(["shell", f"pm list packages{flag}"], timeout=30).stdout
        return sorted(ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("package:"))

    def start_app(self, package: str, activity: str | None = None) -> None:
        _check_package(package)
        if activity:
            if not ACTIVITY_RE.match(activity):
                raise AdbError("activity inválida")
            comp = f"{package}/{activity}"
            out = self.shell(f"am start -n {comp}", timeout=30)
        else:
            out = self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", timeout=30)
        if "Error" in out or "No activities found" in out:
            raise AdbError(f"Não foi possível abrir {package}: {out.strip()[:200]}")

    def app_version(self, package: str) -> str:
        """versionName(versionCode) do pacote instalado — chave das receitas aprendidas para este app."""
        _check_package(package)
        out = self._run(["shell", f"dumpsys package {package} | grep -E 'versionName|versionCode' | head -n 2"], timeout=20).stdout
        name = re.search(r"versionName=(\S+)", out)
        code = re.search(r"versionCode=(\d+)", out)
        if not (name or code):
            raise AdbError(f"{package} não está instalado")
        return f"{name.group(1) if name else '?'}({code.group(1) if code else '?'})"

    def connect(self) -> str:
        """`adb connect host:porta` para aparelhos externos por rede. Serial USB não precisa."""
        if not re.match(r"^[A-Za-z0-9_.\-]+:\d{2,5}$", self.serial):
            return "serial USB"
        res = subprocess.run([str(self.tools.adb), "connect", self.serial], capture_output=True, text=True, timeout=20,
                             env=self.tools.env(), creationflags=NO_WINDOW)
        return ((res.stdout or "") + (res.stderr or "")).strip()[:160]

    def force_stop(self, package: str) -> None:
        _check_package(package)
        self.shell(f"am force-stop {package}", timeout=20)

    def remove_forward(self, port: int) -> None:
        """Remove um `adb forward` antigo desta porta (fica preso quando o Appium morre sem fechar a sessão).
        A porta é exclusiva desta instância, então não há outro dono legítimo; ausência do forward não é erro."""
        self._run(["forward", "--remove", f"tcp:{int(port)}"], timeout=10)

    def abi_list(self) -> str:
        return self._run(["shell", "getprop ro.product.cpu.abilist"], timeout=8).stdout.strip()

    def emu_kill(self) -> None:
        self._run(["emu", "kill"], timeout=15)

    def snapshot_save(self, name: str, *, timeout: float = 300) -> None:
        """Snapshot explícito pelo console do emulador. Só vale se o console responder OK."""
        self._run(["shell", "sync"], timeout=30)
        res = self._run(["emu", "avd", "snapshot", "save", name], timeout=timeout)
        out = ((res.stdout or "") + (res.stderr or "")).strip()
        if res.returncode != 0 or "OK" not in out.upper().split():
            raise AdbError(f"snapshot save falhou: {out[:200] or res.returncode}")

    def clock_skew_s(self) -> int:
        return int(self.shell("date +%s", timeout=10).strip()) - int(time.time())

    def sync_clock(self) -> tuple[int, int]:
        """Depois de acordar de um snapshot o relógio do guest continua no passado (medido: −31 s após 20 s
        hibernado, sem autocorreção). Acerta pelo host. Devolve (desvio_antes, desvio_depois) em segundos."""
        before = self.clock_skew_s()
        if abs(before) <= 2:
            return before, before
        self._run(["shell", f"cmd alarm set-time {int(time.time() * 1000)}"], timeout=10)
        after = self.clock_skew_s()
        if abs(after) > 2:                                  # imagens sem `cmd alarm set-time`: via root (google_apis permite)
            self._run(["root"], timeout=15)
            self._run(["wait-for-device"], timeout=20)
            now = time.localtime()
            self._run(["shell", time.strftime("date %m%d%H%M%Y.%S", now)], timeout=10)
            after = self.clock_skew_s()
        return before, after


def _check_package(package: str) -> None:
    if not PACKAGE_RE.match(package):
        raise AdbError("package name inválido")


def _explain_install_failure(out: str) -> str:
    hints = {
        "INSTALL_FAILED_NO_MATCHING_ABIS": "o APK não contém bibliotecas nativas para x86_64 (a imagem do emulador). "
                                           "Use um APK universal/x86_64 ou uma imagem compatível.",
        "INSTALL_FAILED_OLDER_SDK": "o APK exige uma versão de Android mais nova que a da imagem.",
        "INSTALL_FAILED_MISSING_SHARED_LIBRARY": "o APK depende de bibliotecas ausentes (ex.: Google Play Services). "
                                                 "Use uma imagem google_apis/google_apis_playstore.",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "sem espaço na partição de dados do AVD.",
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "já existe uma versão com assinatura diferente; desinstale antes.",
    }
    for code, hint in hints.items():
        if code in out:
            return f"{code}: {hint}"
    tail = out.strip().splitlines()[-1] if out.strip() else "sem saída"
    return f"Instalação falhou: {tail[:300]}"
