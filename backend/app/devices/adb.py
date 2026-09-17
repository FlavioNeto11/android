"""ADB sempre direcionado a um serial específico (`adb -s <serial> …`). Chamadas bloqueantes:
devem rodar na thread do executor do dispositivo, nunca no event loop."""
from __future__ import annotations

import re
import subprocess

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
            raise AdbError(f"adb {' '.join(args[:3])} excedeu {timeout}s em {self.serial}") from exc

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

    def remove_forward(self, port: int) -> None:
        """Remove um `adb forward` antigo desta porta (fica preso quando o Appium morre sem fechar a sessão).
        A porta é exclusiva desta instância, então não há outro dono legítimo; ausência do forward não é erro."""
        self._run(["forward", "--remove", f"tcp:{int(port)}"], timeout=10)

    def abi_list(self) -> str:
        return self._run(["shell", "getprop ro.product.cpu.abilist"], timeout=8).stdout.strip()

    def emu_kill(self) -> None:
        self._run(["emu", "kill"], timeout=15)


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
