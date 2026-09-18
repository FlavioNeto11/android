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
        self.apkanalyzer = root / "cmdline-tools" / "latest" / "bin" / f"apkanalyzer{bat}"
        # build-tools tem uma pasta por versão; fixar uma quebraria em qualquer máquina com outra instalada.
        build = self._latest_build_tools()
        self.build_tools = build
        self.aapt2 = (build / f"aapt2{exe}") if build else root / "build-tools" / f"aapt2{exe}"
        self.apksigner = (build / f"apksigner{bat}") if build else root / "build-tools" / f"apksigner{bat}"

    def _latest_build_tools(self) -> Path | None:
        """Maior versão instalada de build-tools, comparada por número e não por texto (36.0.0 > 9.0.0)."""
        base = self.root / "build-tools"
        if not base.is_dir():
            return None

        def key(p: Path) -> tuple[int, ...]:
            parts = []
            for chunk in p.name.split("."):
                digits = "".join(c for c in chunk if c.isdigit())
                parts.append(int(digits) if digits else 0)
            return tuple(parts)

        versions = sorted((d for d in base.iterdir() if d.is_dir()), key=key)
        return versions[-1] if versions else None

    def found(self) -> bool:
        return self.adb.exists() and self.emulator.exists()

    def can_inspect_apk(self) -> bool:
        """Sem estas duas não dá para extrair metadados nem impressão da assinatura de um APK."""
        return self.aapt2.exists() and self.apksigner.exists()

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
