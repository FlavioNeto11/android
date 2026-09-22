"""Criação e configuração dos AVDs. Cada AVD vive em <avd_home>/<nome>.avd com userdata próprio:
esse diretório É o isolamento (dados de apps, contas e sessões) e persiste entre reinícios."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..config import AndroidCfg, Config
from .sdk import SdkTools

AVD_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")


class AvdError(RuntimeError):
    pass


#: `image.sysdir.1` guarda o caminho da imagem dentro do SDK, com separador do sistema:
#: `system-images\android-34\google_apis_playstore\x86_64\`. É de lá que saem nível de API, variante e ABI.
_SYSDIR = re.compile(r"system-images[\\/]+android-(?P<api>\d+)[\\/]+(?P<tag>[^\\/]+)[\\/]+(?P<abi>[^\\/]+)")


def ler_config_ini(path: Path) -> dict[str, str]:
    """`config.ini` do AVD como dicionário. Arquivo ausente ou ilegível devolve vazio — não se sabe não é erro."""
    try:
        texto = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    saida: dict[str, str] = {}
    for linha in texto.splitlines():
        if "=" in linha and not linha.lstrip().startswith("#"):
            chave, valor = linha.split("=", 1)
            saida[chave.strip()] = valor.strip()
    return saida


def capacidades_do_avd(avd_home: Path, name: str) -> dict[str, Any]:
    """O que o AVD diz sobre si mesmo: imagem, nível de API, ABIs e se a imagem tem Play Store.

    Lido do disco, sem `adb` e sem emulador no ar — é o que permite ao worker DECLARAR capacidade no `hello`,
    antes de qualquer aparelho subir. O que não estiver no arquivo fica `None`: "não se sabe" não é "não tem".
    """
    cfg = ler_config_ini(avd_home / f"{name}.avd" / "config.ini")
    if not cfg:
        return {}
    sysdir = cfg.get("image.sysdir.1", "")
    m = _SYSDIR.search(sysdir)
    api = int(m.group("api")) if m else None
    tag = (m.group("tag") if m else cfg.get("tag.id", "")) or ""
    abi = (m.group("abi") if m else "") or cfg.get("abi.type", "") or cfg.get("hw.cpu.arch", "")
    # A Play Store só existe na variante `google_apis_playstore`; `google_apis` tem GMS mas não a loja, e
    # `default` (AOSP) não tem nem uma coisa nem outra. Para o pré-voo o que importa é haver GMS.
    tem_gms: bool | None = None
    if tag:
        tem_gms = tag.startswith("google")
    elif (ps := cfg.get("PlayStore.enabled", "")):
        tem_gms = ps.strip().lower() in ("yes", "true", "1")
    imagem = f"system-images;android-{api};{tag};{abi}" if m else (cfg.get("image.sysdir.1") or None)
    return {"kind": "emulator", "system_image": imagem, "api_level": api,
            "abis": [abi] if abi else [], "play_store": tem_gms}


def capacidades_da_imagem(spec: str) -> dict[str, Any]:
    """O que o NOME da imagem do SDK já diz: `system-images;android-34;google_apis;x86_64`.

    É declaração, não observação — vale para o AVD que ainda não existe, que é justamente o caso em que alguém
    precisa saber da incompatibilidade antes de criar o aparelho e descobrir tarde demais.
    """
    partes = [p for p in (spec or "").split(";") if p]
    if len(partes) < 4:
        return {}
    api = partes[1].removeprefix("android-")
    return {"api_level": int(api) if api.isdigit() else None, "abis": [partes[3]],
            "play_store": partes[2].startswith("google")}


def _size_bytes(text: str) -> int:
    m = re.match(r"^\s*(\d+)\s*([kKmMgG]?)[bB]?\s*$", text or "")
    if not m:
        return 0
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[m.group(2).lower()]


class AvdManager:
    def __init__(self, cfg: Config, tools: SdkTools):
        self.cfg = cfg
        self.tools = tools

    def exists(self, name: str) -> bool:
        home = self.cfg.avd_home
        return (home / f"{name}.ini").exists() and (home / f"{name}.avd" / "config.ini").exists()

    def create(self, name: str, android: AndroidCfg) -> None:
        if not AVD_NAME_RE.match(name):
            raise AvdError("nome de AVD inválido")
        if not self.tools.avdmanager.exists():
            raise AvdError(f"avdmanager não encontrado em {self.tools.avdmanager}")
        if not self.tools.system_image_dir(android.system_image).exists():
            raise AvdError(
                f"System image '{android.system_image}' não instalada. Rode scripts/install-prereqs.ps1 "
                f"ou: sdkmanager \"{android.system_image}\"")
        self.cfg.avd_home.mkdir(parents=True, exist_ok=True)
        res = self.tools.run([self.tools.avdmanager, "create", "avd", "-n", name, "-k", android.system_image, "--force"],
                             timeout=180, input_text="no\n")
        if res.returncode != 0 or not self.exists(name):
            raise AvdError(f"avdmanager falhou: {(res.stderr or res.stdout).strip()[-400:]}")
        self.apply_hardware(name, android)

    def apply_hardware(self, name: str, a: AndroidCfg) -> None:
        overrides = {
            "hw.lcd.width": str(a.width), "hw.lcd.height": str(a.height), "hw.lcd.density": str(a.density),
            "hw.ramSize": str(a.ram_mb), "hw.cpu.ncore": str(a.cores),
            "hw.gpu.enabled": "yes", "hw.gpu.mode": a.gpu_mode,
            "hw.keyboard": "yes", "hw.mainKeys": "no", "showDeviceFrame": "no",
            "hw.audioInput": "no", "hw.audioOutput": "no", "hw.sdCard": "no",
            "hw.camera.back": "none", "hw.camera.front": "none",
            "disk.dataPartition.size": a.data_partition, "vm.heapSize": "256M",
            # sempre cold boot: os dados persistem na partição de dados; snapshots de RAM custariam GBs por instância
            "fastboot.forceColdBoot": "no" if a.hibernation else "yes", "fastboot.forceFastBoot": "no",
            "firstboot.bootFromDownloadableSnapshot": "no", "firstboot.bootFromLocalSnapshot": "no",
            "firstboot.saveToLocalSnapshot": "no",
        }
        if "google_apis_playstore" in a.system_image:
            # `avdmanager create` sem perfil de aparelho (`-d`) pode gravar `PlayStore.enabled=false` mesmo com a
            # imagem certa, e aí o emulador sobe sem tratar a imagem como de loja. Forçar aqui é barato e idempotente.
            overrides["PlayStore.enabled"] = "yes"
        path = self.cfg.avd_home / f"{name}.avd" / "config.ini"
        lines = path.read_text(encoding="utf-8").splitlines()
        seen: set[str] = set()
        out: list[str] = []
        for ln in lines:
            key = ln.split("=", 1)[0].strip()
            if key == "disk.dataPartition.size" and _size_bytes(ln.split("=", 1)[1]) > _size_bytes(a.data_partition):
                # o emulador aumenta a partição no 1º boot (mínimo da imagem); encolher de volta muda o hardware a cada
                # sessão e invalida o snapshot da hibernação — e partição de dados não se encolhe mesmo
                out.append(ln)
                seen.add(key)
                continue
            if key in overrides:
                out.append(f"{key}={overrides[key]}")
                seen.add(key)
            else:
                out.append(ln)
        out.extend(f"{k}={v}" for k, v in overrides.items() if k not in seen)
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
