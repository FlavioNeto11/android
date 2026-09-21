"""Configuração do agente: um YAML pequeno na máquina do worker, e a credencial num arquivo à parte.

Separado da configuração do central de propósito — o worker não precisa saber de IA, banco, limites nem perfis.
O que ele precisa é onde está o SDK, onde ficam os AVDs, quais aparelhos ele hospeda, e para quem ligar.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..config import AppConfigFile, Config, EnvSettings
from ..workers.protocol import AppiumMode

#: A credencial permanente NÃO fica no YAML: ela é gravada pelo próprio agente, com permissão restrita, depois de
#: trocar o token de inscrição. Assim o arquivo que alguém edita à mão nunca contém segredo.
CREDENTIAL_FILE = "worker-credential.json"


class DeviceSpec(BaseModel):
    """Um aparelho que este worker hospeda, e a instância do parque que ele serve."""

    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=3, max_length=40)
    avd_name: str = Field(min_length=1, max_length=60)
    console_port: int = Field(ge=5554, le=5680)
    #: Aparelho físico ou contêiner: o agente não cria nem liga, só opera por ADB.
    managed: bool = True

    @property
    def serial(self) -> str:
        return f"emulator-{self.console_port}"

    @property
    def adb_port(self) -> int:
        return self.console_port + 1


class WorkerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Endereço do servidor CENTRAL. O worker liga para lá — nunca o contrário.
    server: str = Field(default="http://127.0.0.1:8000", max_length=200)
    worker_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    sdk_root: str = r"C:\Android\Sdk"
    work_dir: str = r"C:\farm"
    appium: AppiumMode = "central"
    appium_url: str | None = None
    max_slots: int = Field(default=1, ge=1, le=64)
    devices: list[DeviceSpec] = []
    #: Perfil de hardware dos AVDs que ESTE worker cria. Espelha `android` do central; o padrão é o perfil enxuto
    #: já medido (`-lowram` + 1536 MB ≈ 2,4 GB em repouso).
    android: dict[str, Any] = {}
    #: Quantos emuladores podem estar BOOTANDO ao mesmo tempo. Subir quatro de uma vez travou os quatro em ANR
    #: (medido em 19/09); um a um subiram limpos em 104–192 s.
    boot_parallelism: int = Field(default=1, ge=1, le=8)
    #: Guarda da máquina: quanto deve sobrar de RAM depois de subir mais um aparelho.
    ram_per_device_mb: int = Field(default=1800, ge=256, le=16384)
    min_free_ram_mb: int = Field(default=4096, ge=512, le=131072)

    @property
    def paths(self) -> dict[str, str]:
        base = Path(self.work_dir)
        return {"data_dir": str(base), "avd_home": str(base / "avd"), "logs_dir": str(base / "logs"),
                "evidence_dir": str(base / "evidence"), "apk_dirs": [str(base / "apks")],  # type: ignore[dict-item]
                "apk_inbox": str(base / "apks" / "inbox"), "apk_catalog": str(base / "apks")}

    def to_config(self) -> Config:
        """Monta o `Config` que `SdkTools`, `AvdManager` e `emulator` esperam.

        Reaproveitar o mesmo tipo é o que permite usar aqueles módulos sem alteração: eles pedem `cfg.sdk_root`,
        `cfg.avd_home`, `cfg.logs_dir` e `cfg.file.android`, e nada além disso.
        """
        arquivo = AppConfigFile.model_validate({
            "paths": self.paths,
            "android": {**{"sdk_root": self.sdk_root}, **self.android},
            # Uma instância por aparelho declarado, só para o `count` ser coerente; o agente não usa esse bloco.
            "instances": {"count": max(1, len(self.devices))},
            "appium": {"autostart": False},
        })
        env = EnvSettings(_env_file=None, AI_PROVIDER="simulated")  # type: ignore[call-arg]
        cfg = Config(arquivo, env, root=Path(self.work_dir))
        cfg.ensure_dirs()
        return cfg

    def device(self, instance_id: str) -> DeviceSpec | None:
        return next((d for d in self.devices if d.instance_id == instance_id), None)

    # ------------------------------------------------------------------ credencial
    def credential_path(self) -> Path:
        return Path(self.work_dir) / CREDENTIAL_FILE

    def read_credential(self) -> str | None:
        caminho = self.credential_path()
        if not caminho.exists():
            return None
        try:
            return json.loads(caminho.read_text(encoding="utf-8")).get("credential") or None
        except (OSError, ValueError):
            return None

    def write_credential(self, credential: str) -> None:
        caminho = self.credential_path()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps({"worker_id": self.worker_id, "credential": credential}), encoding="utf-8")
        if os.name != "nt":
            caminho.chmod(0o600)          # no Windows a herança de ACL da pasta de trabalho é quem protege


def load_settings(path: str | Path) -> WorkerSettings:
    dados = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return WorkerSettings.model_validate(dados)


def host_os() -> tuple[str, str]:
    return platform.system().lower(), platform.version()
