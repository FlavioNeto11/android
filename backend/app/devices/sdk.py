"""Localização das ferramentas do Android SDK e execução de subprocessos sem shell."""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from ..config import Config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
IS_WINDOWS = os.name == "nt"

#: 29.47 (03/10/2026): o que passa do ambiente do backend (ou do agente) aos processos filhos — adb, emulador,
#: avdmanager, Appium, o sing-box da rede, as sondas do Diagnóstico, o PowerShell da leitura do firewall e o `icacls`
#: das trancas de arquivo (o do agente inclusive). `tests/test_ambiente_dos_filhos.py` recusa lançamento novo no
#: `backend/app` sem `env=` ou com cópia de `os.environ`. Lista de PERMISSÃO, não de proibição: o processo
#: carrega os segredos do `.env` (chaves das IAs, do cofre, do worker) e nenhum filho usa algum deles; o qemu herdava
#: `TYPESAFE_API_KEY` (K-078, rodada por adição). Variável nova de que um filho precise entra aqui, pelo nome.
AMBIENTE_PERMITIDO = frozenset({
    # Windows: achar o sistema, o perfil do usuário, as pastas temporárias e o processador
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "USERNAME",
    "USERDOMAIN", "COMPUTERNAME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ALLUSERSPROFILE",
    "PUBLIC", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432", "OS", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL", "PROCESSOR_REVISION", "PSMODULEPATH",
    # o driver de GPU do host, carregado dentro do qemu com `-gpu host`, pode procurar a pasta dele por aqui
    "DRIVERDATA",
    # POSIX: o agente pode rodar fora do Windows
    "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
    # o Java do avdmanager, do sdkmanager, do apksigner e do driver do Appium
    "JAVA_HOME",
})
#: `ANDROID_*` (SDK, AVD, `ANDROID_USER_HOME`, `ANDROID_EMULATOR_*`, a porta do servidor adb) e `ADB_*`, do adb.
PREFIXOS_PERMITIDOS = ("ANDROID_", "ADB_")
#: A segunda trava: nem pela lista passa um nome de segredo nosso, ou com cara de segredo (um `ANDROID_X_TOKEN` futuro).
PREFIXOS_PROIBIDOS = ("TYPESAFE_", "OPENAI_", "ANTHROPIC_", "GEMINI_", "FARM_")
_CARA_DE_SEGREDO = re.compile(r"(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|CREDENTIALS?)(_|$)")


def _nome_de_segredo(chave: str) -> bool:
    return chave.startswith(PREFIXOS_PROIBIDOS) or bool(_CARA_DE_SEGREDO.search(chave))


def ambiente_dos_filhos(origem: Mapping[str, str] | None = None) -> dict[str, str]:
    """O ambiente de um processo filho: só o que a lista de permissão deixa e nada com nome de segredo. O valor de
    uma variável nunca é olhado; a decisão é pelo nome (no Windows sem diferença de caixa, como o próprio sistema)."""
    env: dict[str, str] = {}
    for nome, valor in (os.environ if origem is None else origem).items():
        chave = nome.upper()
        if _nome_de_segredo(chave):
            continue
        if chave in AMBIENTE_PERMITIDO or chave.startswith(PREFIXOS_PERMITIDOS):
            env[nome] = valor
    return env


def sem_segredos(origem: Mapping[str, str] | None = None) -> dict[str, str]:
    """Só a segunda trava: o ambiente inteiro menos os nomes de segredo. Para o git e o ripgrep do Context Retrieval,
    que dependem de `GIT_*`, `SSH_*` e da configuração do usuário, e que a lista de permissão quebraria sem ganho: o
    que importa é que nenhuma chave do `.env` chegue a eles. Como em `ambiente_dos_filhos`, só o nome decide."""
    return {nome: valor for nome, valor in (os.environ if origem is None else origem).items()
            if not _nome_de_segredo(nome.upper())}


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
        env = ambiente_dos_filhos()
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
