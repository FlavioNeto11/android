"""Instalação de release num aparelho, com compatibilidade conferida antes e estado observado depois.

Nenhuma outra parte do projeto chama `adb install` direto: a decisão entre arquivo único e conjunto de splits, a
conferência de compatibilidade e a verificação no aparelho ficam todas aqui. Os comandos são montados como lista de
argumentos e executados sem shell, pelo `SdkTools.run` que já existe.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import InstalledAppState
from .adb import AdbError

log = logging.getLogger("poc.installer")

# Depois de abrir o app, espera-se este tempo para ver se o processo sobrevive. Instalar não prova que roda —
# especialmente com biblioteca nativa traduzida de arm64 para x86_64.
LAUNCH_SETTLE_S = 6.0


class InstallError(RuntimeError):
    """Falha de instalação já traduzida para linguagem humana."""


@dataclass(slots=True)
class DeviceProfile:
    """O que o aparelho responde sobre si mesmo. Lido, nunca suposto."""

    abi: str
    abis: list[str]
    sdk: int | None
    locale: str
    density: int | None

    @property
    def density_bucket(self) -> str:
        """Faixa de densidade, que é como os splits do Android são nomeados."""
        d = self.density or 0
        for limit, name in ((140, "ldpi"), (200, "mdpi"), (280, "hdpi"), (400, "xhdpi"), (560, "xxhdpi")):
            if d <= limit:
                return name
        return "xxxhdpi"


@dataclass(slots=True)
class Compatibility:
    verdict: str                      # compatible | incompatible | uncertain
    reason: str
    translated_abi: str | None = None

    @property
    def blocked(self) -> bool:
        return self.verdict == "incompatible"


@dataclass(slots=True)
class InstalledApp:
    """Estado observado no aparelho."""

    present: bool
    version_name: str | None = None
    version_code: int | None = None
    splits: list[str] = field(default_factory=list)
    first_install_time: str | None = None
    last_update_time: str | None = None
    paths: list[str] = field(default_factory=list)


def compatibility(*, min_sdk: int | None, abis: list[str], profile: DeviceProfile) -> Compatibility:
    """Decide ANTES de instalar. Tentar e descobrir pelo erro do ADB seria mais lento e menos informativo."""
    if min_sdk is not None and profile.sdk is not None and min_sdk > profile.sdk:
        return Compatibility("incompatible",
                            f"o pacote exige Android com SDK {min_sdk} e o aparelho tem {profile.sdk}")
    if not abis:
        return Compatibility("compatible", "o pacote não traz biblioteca nativa; qualquer ABI serve")
    if profile.abi in abis:
        return Compatibility("compatible", f"ABI nativa do aparelho ({profile.abi}) está no pacote")
    translated = next((a for a in profile.abis if a in abis), None)
    if translated:
        # Aceitar a instalação não prova que roda: a prova é a sonda de abertura.
        return Compatibility("uncertain",
                             f"só há {translated}, que este aparelho executa por tradução — confirmar abrindo o app",
                             translated_abi=translated)
    return Compatibility("incompatible",
                         f"o pacote traz {', '.join(abis)} e o aparelho aceita {', '.join(profile.abis)}")


class AppInstaller:
    """Opera um aparelho já sob posse de quem chamou (a posse é do scheduler, não deste módulo)."""

    def __init__(self, devices: Any):
        self.devices = devices

    # ------------------------------------------------------------------ leitura
    async def profile(self, rt: Any, *, timeout: float = 30) -> DeviceProfile:
        run = rt.executor.run
        abilist = await run(rt.adb.getprop, "ro.product.cpu.abilist", timeout=timeout, label="abis do aparelho")
        abi = await run(rt.adb.getprop, "ro.product.cpu.abi", timeout=timeout, label="abi do aparelho")
        sdk = await run(rt.adb.getprop, "ro.build.version.sdk", timeout=timeout, label="sdk do aparelho")
        locale = await run(rt.adb.getprop, "ro.product.locale", timeout=timeout, label="idioma do aparelho")
        density = await run(rt.adb.wm_density, timeout=timeout, label="densidade do aparelho")
        return DeviceProfile(
            abi=abi.strip(),
            abis=[a.strip() for a in (abilist or abi).split(",") if a.strip()],
            sdk=int(sdk) if sdk.strip().isdigit() else None,
            locale=locale.strip() or "",
            density=density)

    async def inspect(self, rt: Any, package: str, *, timeout: float = 40) -> InstalledApp:
        info = await rt.executor.run(rt.adb.package_info, package, timeout=timeout, label="estado do app")
        if not info:
            return InstalledApp(present=False)
        return InstalledApp(present=True, version_name=info["version_name"], version_code=info["version_code"],
                            splits=list(info["splits"]), first_install_time=info["first_install_time"],
                            last_update_time=info["last_update_time"], paths=list(info["paths"]))

    # ------------------------------------------------------------------ escrita
    async def install(self, rt: Any, *, paths: list[Path], timeout: float = 900) -> None:
        """Arquivo único usa `install`; conjunto usa `install-multiple`, que é atômico no `pm`."""
        if not paths:
            raise InstallError("Release sem arquivos para instalar.")
        args = [str(p) for p in paths]
        try:
            if len(args) == 1:
                await rt.executor.run(rt.adb.install, args[0], timeout=timeout, label="instalar APK")
            else:
                await rt.executor.run(lambda: rt.adb.install_multiple(args, timeout=timeout - 30),
                                      timeout=timeout, label="instalar conjunto de APKs")
        except AdbError as exc:
            raise InstallError(str(exc)) from None
        rt.app_versions.clear()          # a versão em cache é a chave das receitas; ela mudou

    async def uninstall(self, rt: Any, package: str, *, timeout: float = 120) -> None:
        try:
            await rt.executor.run(rt.adb.uninstall, package, timeout=timeout, label="desinstalar app")
        except AdbError as exc:
            raise InstallError(str(exc)) from None
        rt.app_versions.clear()

    # ------------------------------------------------------------------ prova de que roda
    async def launch_probe(self, rt: Any, package: str, *, settle_s: float = LAUNCH_SETTLE_S) -> tuple[bool, str]:
        """Abre o app e confere que o processo continua vivo. É isto que decide se uma ABI traduzida serve."""
        try:
            await rt.executor.run(rt.adb.start_app, package, None, timeout=60, label="abrir app")
        except AdbError as exc:
            return False, f"o app não abriu: {exc}"
        await asyncio.sleep(settle_s)
        try:
            current = await rt.executor.run(rt.adb.current_focus, timeout=30, label="janela em foco")
        except AdbError:
            current = ""
        alive = await rt.executor.run(rt.adb.is_installed, package, timeout=30, label="pacote presente")
        if not alive:
            return False, "o pacote sumiu depois de abrir"
        if package not in (current or ""):
            return False, f"o app não ficou em primeiro plano (foco: {(current or 'desconhecido')[:80]})"
        return True, "app abriu e permaneceu em primeiro plano"


def drift_of(observed: InstalledApp, *, expected_version_code: int | None,
             expected_splits: list[str] | None = None) -> tuple[InstalledAppState, str | None, str | None]:
    """Compara o que o aparelho respondeu com o que o banco esperava. O banco nunca ganha essa disputa."""
    if not observed.present:
        return InstalledAppState.missing, "app_missing", "O pacote não está instalado neste aparelho."
    if expected_version_code is None:
        return InstalledAppState.installed, None, None
    if observed.version_code != expected_version_code:
        direction = "maior" if (observed.version_code or 0) > expected_version_code else "menor"
        return (InstalledAppState.version_drift, "app_version_drift",
                f"O aparelho tem versionCode {observed.version_code} ({direction} que o esperado "
                f"{expected_version_code}); receitas da versão anterior ficam bloqueadas até reconciliar.")
    if expected_splits:
        missing = sorted(set(expected_splits) - set(observed.splits))
        if missing:
            return (InstalledAppState.version_drift, "split_mismatch",
                    f"Faltam splits no aparelho: {', '.join(missing)}.")
    return InstalledAppState.installed, None, None
