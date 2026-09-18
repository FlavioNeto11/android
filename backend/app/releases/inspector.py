"""Inspeção de APK: os metadados vêm SEMPRE do pacote, nunca do nome do arquivo.

Ferramentas usadas (ambas do Android SDK, localizadas por `SdkTools`):
  aapt2 dump badging <apk>            -> pacote, versionCode/Name, minSdk/targetSdk, ABIs, densidades, idiomas, split
  apksigner verify --print-certs <apk> -> impressão SHA-256 do certificado que assinou o pacote
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..devices.sdk import SdkTools

# `package: name='com.x' versionCode='1' versionName='1.0' ... split='config.pt'`
_PACKAGE = re.compile(r"package:\s+name='(?P<name>[^']*)'")
_VERSION_CODE = re.compile(r"versionCode='(?P<v>[^']*)'")
_VERSION_NAME = re.compile(r"versionName='(?P<v>[^']*)'")
_SPLIT = re.compile(r"\bsplit='(?P<v>[^']*)'")
_MIN_SDK = re.compile(r"^minSdkVersion:'(?P<v>[^']*)'", re.MULTILINE)
_TARGET_SDK = re.compile(r"^targetSdkVersion:'(?P<v>[^']*)'", re.MULTILINE)
_NATIVE_CODE = re.compile(r"^native-code:\s*(?P<v>.+)$", re.MULTILINE)
_LOCALES = re.compile(r"^locales:\s*(?P<v>.+)$", re.MULTILINE)
_DENSITIES = re.compile(r"^densities:\s*(?P<v>.+)$", re.MULTILINE)
_QUOTED = re.compile(r"'([^']*)'")
_SIGNER_SHA256 = re.compile(r"Signer\s+#\d+\s+certificate\s+SHA-256\s+digest:\s*(?P<v>[0-9a-fA-F]{64})")

CHUNK = 1024 * 1024


class ApkInspectionError(RuntimeError):
    """O arquivo não é um APK legível, ou as ferramentas do SDK não estão disponíveis."""


@dataclass(slots=True)
class ApkInfo:
    """O que o pacote diz sobre si mesmo. Nada aqui vem do nome do arquivo."""

    path: Path
    package_name: str
    version_code: int
    version_name: str
    sha256: str
    size_bytes: int
    signature_sha256: str
    split_name: str | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    abis: list[str] = field(default_factory=list)
    locales: list[str] = field(default_factory=list)
    densities: list[str] = field(default_factory=list)

    @property
    def is_base(self) -> bool:
        return not self.split_name

    @property
    def role(self) -> str:
        return "base" if self.is_base else "split"


def sha256_of(path: Path) -> tuple[str, int]:
    """Hash em streaming: um APK do Instagram passa de 60 MB e não cabe confortavelmente em memória."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class ApkInspector:
    def __init__(self, tools: SdkTools):
        self.tools = tools

    def available(self) -> bool:
        return self.tools.can_inspect_apk()

    def inspect(self, path: Path) -> ApkInfo:
        if not self.available():
            raise ApkInspectionError(
                f"aapt2/apksigner não encontrados em {self.tools.root / 'build-tools'}; rode scripts/install-prereqs.ps1.")
        if not path.is_file():
            raise ApkInspectionError(f"arquivo não encontrado: {path.name}")
        badging = self._badging(path)
        digest, size = sha256_of(path)
        pkg = _PACKAGE.search(badging)
        if not pkg or not pkg.group("name"):
            raise ApkInspectionError(f"{path.name}: não foi possível ler o nome do pacote (o arquivo é mesmo um APK?)")
        header = badging.splitlines()[0] if badging else ""
        return ApkInfo(
            path=path,
            package_name=pkg.group("name"),
            version_code=_int_of(_VERSION_CODE.search(header)) or 0,
            version_name=(m.group("v") if (m := _VERSION_NAME.search(header)) else ""),
            sha256=digest,
            size_bytes=size,
            signature_sha256=self._signature(path),
            split_name=(m.group("v") if (m := _SPLIT.search(header)) else None),
            min_sdk=_int_of(_MIN_SDK.search(badging)),
            target_sdk=_int_of(_TARGET_SDK.search(badging)),
            abis=_quoted_list(_NATIVE_CODE.search(badging)),
            locales=[x for x in _quoted_list(_LOCALES.search(badging)) if x and x != "--_--"],
            densities=_quoted_list(_DENSITIES.search(badging)),
        )

    # ------------------------------------------------------------------ internos
    def _badging(self, path: Path) -> str:
        res = self.tools.run([self.tools.aapt2, "dump", "badging", str(path)], timeout=120)
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()
            raise ApkInspectionError(f"{path.name}: aapt2 não conseguiu ler o pacote "
                                     f"({tail[-1][:160] if tail else 'sem saída'})")
        return res.stdout or ""

    def _signature(self, path: Path) -> str:
        res = self.tools.run([self.tools.apksigner, "verify", "--print-certs", str(path)], timeout=120)
        out = (res.stdout or "") + (res.stderr or "")
        m = _SIGNER_SHA256.search(out)
        if not m:
            tail = out.strip().splitlines()
            raise ApkInspectionError(f"{path.name}: não foi possível ler a assinatura "
                                     f"({tail[-1][:160] if tail else 'sem saída'})")
        return m.group("v").lower()


def _int_of(match: re.Match[str] | None) -> int | None:
    if not match:
        return None
    try:
        return int(match.group("v"))
    except (TypeError, ValueError):
        return None


def _quoted_list(match: re.Match[str] | None) -> list[str]:
    return _QUOTED.findall(match.group("v")) if match else []
