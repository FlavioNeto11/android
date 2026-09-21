"""Execução dos verbos NA máquina do worker, reaproveitando a camada de dispositivo do projeto.

Recorte deliberado: o agente cuida do **ciclo de vida** (criar AVD, ligar, desligar, reiniciar, hibernar, acordar,
resetar). Os verbos que são só ADB — instalar APK, abrir app, teclas — continuam saindo do servidor central pelo
túnel, porque aquele caminho está provado em campo (APK de 71 KB e de 20 MB, `adb forward`, screenshot, page
source) e porque mandar o catálogo de APK para cada máquina seria trocar um problema resolvido por um novo.

O que o worker faz é exatamente o que o túnel NÃO consegue carregar.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import psutil

from ..config import Config
from ..devices import emulator as emu
from ..devices.adb import Adb, AdbError
from ..devices.avd import AvdError, AvdManager
from ..devices.sdk import SdkTools
from .settings import DeviceSpec, WorkerSettings

log = logging.getLogger("poc.worker")

SNAPSHOT = emu.SNAPSHOT_NAME
#: Verbos que o agente executa. O central continua dono dos verbos de ADB puro.
VERBS = ("create", "start", "wake", "stop", "hibernate", "restart", "reset")


class VerbRefused(RuntimeError):
    """Recusa ANTES de agir — então nada aconteceu no aparelho, e o central pode dizer isso com certeza."""


class VerbUncertain(RuntimeError):
    """Agiu e não se sabe o desfecho. Nunca é convertido em sucesso nem em falha."""


class WorkerExecutor:
    def __init__(self, settings: WorkerSettings, cfg: Config, *, progress: Callable[[str], None] | None = None):
        self.settings = settings
        self.cfg = cfg
        self.tools = SdkTools(cfg)
        self.avd = AvdManager(cfg, self.tools)
        self.progress = progress or (lambda _m: None)
        #: PID do emulador que ESTE agente iniciou, por AVD. Só matamos o que subimos.
        self.pids: dict[str, int] = {}
        #: Um boot por vez por padrão: subir quatro juntos travou os quatro em ANR (medido em 19/09).
        self._boot = asyncio.Semaphore(settings.boot_parallelism)

    # ------------------------------------------------------------------ utilidades
    def adb_for(self, spec: DeviceSpec) -> Adb:
        return Adb(self.tools, spec.serial)

    def _android(self) -> Any:
        return self.cfg.file.android

    def _guarda_de_ram(self) -> None:
        """A máquina se protege sozinha: o central exclui aparelho externo de `slots_used()` de propósito."""
        vm = psutil.virtual_memory()
        livre_mb = int(vm.available / (1024 * 1024))
        sobraria = livre_mb - self.settings.ram_per_device_mb
        if sobraria < self.settings.min_free_ram_mb:
            raise VerbRefused(f"RAM insuficiente neste worker: sobrariam {sobraria} MB e o mínimo é "
                              f"{self.settings.min_free_ram_mb} MB")

    def pid_do_avd(self, avd_name: str) -> int | None:
        """Acha o processo do emulador daquele AVD, mesmo que não tenha sido este agente a iniciá-lo.

        Importa porque os emuladores podem já estar no ar quando o agente sobe (tarefa agendada, reinício do
        serviço). Sem isto, o agente perguntaria ao `adb` o que o sistema operacional já sabe — e `adb` é caro e
        disputa a conexão com o servidor central pelo túnel.
        """
        if (conhecido := self.pids.get(avd_name)) and emu.is_our_emulator(conhecido, avd_name):
            return conhecido
        for p in psutil.process_iter(["name"]):
            nome = (p.info.get("name") or "").lower()
            if "qemu" not in nome and "emulator" not in nome:
                continue
            try:
                if avd_name in p.cmdline():
                    self.pids[avd_name] = p.pid
                    return p.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return None

    def estado(self, spec: DeviceSpec) -> tuple[str, str | None]:
        """Estado do PROCESSO, sem tocar no `adb`. Barato, previsível e sem disputa.

        A separação é deliberada, e custou uma medição para ficar clara: o servidor central já fala `adb` com
        estes aparelhos pelo túnel, e um segundo servidor adb aqui disputa a mesma porta 5555 de cada emulador.
        Com o agente sondando seis aparelhos a cada batida, a latência do central saiu de 114 ms (medido em
        19/09, sem adb no worker) para vários segundos.

        Então o recorte é: **o worker sabe do processo; o central sabe do Android.** `running` quer dizer "o
        emulador está no ar", não "pronto para automação" — quem decide isso é o central, que tem o `adb`.
        `adb` só é usado aqui durante uma operação de ciclo de vida, que é curta e acontece justamente quando o
        central não está dirigindo aquele aparelho.
        """
        if not spec.managed:
            # Aparelho que este agente não gere (físico, contêiner): ele não tem processo para olhar.
            return "unknown", "aparelho não gerido por este agente"
        if self.pid_do_avd(spec.avd_name) is not None:
            return "running", None
        if not self.avd.exists(spec.avd_name):
            return "absent", "AVD não existe nesta máquina"
        return "stopped", None

    async def _espera_boot(self, spec: DeviceSpec, *, deadline_s: float) -> None:
        adb = self.adb_for(spec)
        limite = time.monotonic() + deadline_s
        while time.monotonic() < limite:
            await asyncio.sleep(3)
            try:
                if adb.state() == "device" and await asyncio.to_thread(adb.boot_completed):
                    # Ajustes idempotentes e dispensa de diálogo do sistema: AVD recém-criado dá ANR no 1º boot.
                    try:
                        await asyncio.to_thread(adb.prepare_for_automation)
                    except AdbError as exc:
                        log.warning("%s: preparo falhou: %s", spec.instance_id, exc)
                    return
            except AdbError:
                continue
        raise VerbUncertain(f"o aparelho não completou o boot em {deadline_s:.0f} s; estado desconhecido")

    # ------------------------------------------------------------------ verbos
    async def run(self, verb: str, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        if verb not in VERBS:
            raise VerbRefused(f"este worker não executa '{verb}'")
        if not spec.managed and verb in ("create", "start", "stop", "hibernate", "restart", "reset", "wake"):
            raise VerbRefused(f"{spec.instance_id} é aparelho não gerido por este agente (managed=false)")
        metodo = getattr(self, f"_v_{verb}")
        return await metodo(spec, params)

    async def _v_create(self, spec: DeviceSpec, _p: dict[str, Any]) -> dict[str, Any]:
        if self.avd.exists(spec.avd_name):
            return {"created": False, "detail": "o AVD já existia"}
        self.progress(f"criando o AVD {spec.avd_name}")
        try:
            await asyncio.to_thread(self.avd.create, spec.avd_name, self._android())
        except AvdError as exc:
            raise VerbRefused(str(exc)) from exc
        return {"created": True}

    async def _v_start(self, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        estado, _ = self.estado(spec)
        if estado == "running":
            return {"started": False, "detail": "o emulador já estava no ar"}
        if not self.avd.exists(spec.avd_name):
            raise VerbRefused(f"o AVD {spec.avd_name} não existe nesta máquina; peça 'create' antes")
        self._guarda_de_ram()
        do_snapshot = bool(params.get("from_snapshot"))
        async with self._boot:            # um boot por vez: quatro juntos travaram os quatro em ANR
            self.progress(f"subindo {spec.avd_name} na porta {spec.console_port}")
            pid = await asyncio.to_thread(
                emu.start_process, self.cfg, self.tools, spec.avd_name, spec.console_port, self._android(),
                wipe_data=bool(params.get("wipe_data")), from_snapshot=do_snapshot)
            self.pids[spec.avd_name] = pid
            await self._espera_boot(spec, deadline_s=float(params.get("boot_timeout_s") or 480))
        try:
            await asyncio.to_thread(self.adb_for(spec).sync_clock)
        except AdbError:
            pass                          # relógio atrasado depois de snapshot não impede a operação
        return {"started": True, "pid": pid, "from_snapshot": do_snapshot}

    async def _v_wake(self, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        """Acordar é subir a partir do snapshot. Sem snapshot é boot a frio — dito, não escondido."""
        return await self._v_start(spec, {**params, "from_snapshot": True})

    async def _v_stop(self, spec: DeviceSpec, _p: dict[str, Any]) -> dict[str, Any]:
        estado, _ = self.estado(spec)
        if estado in ("stopped", "absent"):
            return {"stopped": False, "detail": "já estava desligado"}
        self.progress(f"desligando {spec.avd_name}")
        detalhe = await asyncio.to_thread(emu.stop_process, self.adb_for(spec), self.pids.get(spec.avd_name),
                                          spec.avd_name)
        self.pids.pop(spec.avd_name, None)
        return {"stopped": True, "detail": detalhe}

    async def _v_hibernate(self, spec: DeviceSpec, _p: dict[str, Any]) -> dict[str, Any]:
        estado, _ = self.estado(spec)
        if estado != "running":
            raise VerbRefused("hibernar exige o emulador no ar")
        self.progress(f"salvando snapshot de {spec.avd_name}")
        try:
            await asyncio.to_thread(self.adb_for(spec).snapshot_save, SNAPSHOT)
        except AdbError as exc:
            # Snapshot que não salvou com CERTEZA não vira hibernação: desliga e diz que o próximo boot é a frio.
            await self._v_stop(spec, {})
            return {"hibernated": False, "detail": f"snapshot não salvo ({exc}); desligado para boot a frio"}
        await self._v_stop(spec, {})
        return {"hibernated": True, "snapshot": SNAPSHOT}

    async def _v_restart(self, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        await self._v_stop(spec, {})
        return {"restarted": True, **await self._v_start(spec, {**params, "from_snapshot": False})}

    async def _v_reset(self, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        """Apaga os dados do aparelho. `-wipe-data` e snapshot nunca andam juntos — a camada já recusa."""
        await self._v_stop(spec, {})
        saida = await self._v_start(spec, {**params, "wipe_data": True, "from_snapshot": False})
        return {"reset": True, **saida}
