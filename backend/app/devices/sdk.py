"""Localização das ferramentas do Android SDK e execução de subprocessos sem shell."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..config import Config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
IS_WINDOWS = os.name == "nt"


class SdkTools:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        root = cfg.sdk_root
        exe = ".exe" if IS_WINDOWS else ""
        bat = ".bat" if IS_WINDOWS else ""
        self.root = root
        self.adb = root / "platform-tools" / f"adb{exe}"
        self.emulator = root / "emulator" / f"emulator{exe}"
        self.avdmanager = root / "cmdline-tools" / "latest" / "bin" / f"avdmanager{bat}"
        self.sdkmanager = root / "cmdline-tools" / "latest" / "bin" / f"sdkmanager{bat}"

    def found(self) -> bool:
        return self.adb.exists() and self.emulator.exists()

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["ANDROID_HOME"] = str(self.root)
        env["ANDROID_SDK_ROOT"] = str(self.root)
        env["ANDROID_AVD_HOME"] = str(self.cfg.avd_home)
        return env

    def system_image_dir(self, package: str) -> Path:
        return self.root.joinpath(*package.split(";"))

    def run(self, args: list[str | os.PathLike[str]], *, timeout: float = 60, input_text: str | None = None,
            check: bool = False) -> subprocess.CompletedProcess[str]:
        """Executa um comando (lista de argumentos, nunca shell) e devolve stdout/stderr como texto."""
        return subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, input=input_text, env=self.env(),
                              creationflags=NO_WINDOW, check=check)
