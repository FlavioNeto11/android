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
from contextvars import ContextVar
from typing import Any, Callable

import psutil

from ..config import Config
from ..devices import emulator as emu
from ..devices.adb import Adb, AdbError
from ..devices.avd import AvdError, AvdManager
from ..devices.sdk import SdkTools
from ..workers.protocol import MARCA_DE_FILA
from .settings import DeviceSpec, WorkerSettings

log = logging.getLogger("poc.worker")

SNAPSHOT = emu.SNAPSHOT_NAME
#: Verbos que o agente executa. O central continua dono dos verbos de ADB puro.
VERBS = ("create", "start", "wake", "stop", "hibernate", "restart", "reset")
#: Intervalo entre duas sondagens do boot. Constante (e não número solto) para o teste do escalonamento não
#: precisar esperar em tempo real o que já está provado em campo.
INTERVALO_SONDA_S = 3.0

#: O que este comando JÁ fez no aparelho, ou `None` enquanto não tocou em nada. É o que separa `cancelled` de
#: `uncertain` quando o central manda cancelar: `013_commands.sql` define `cancelled` como cancelamento
#: CONFIRMADO, e responder isso com uma thread ainda criando AVD ou com o emulador no ar seria mentira. Fica num
#: ContextVar porque cada comando roda na sua própria tarefa (o agente cria uma por despacho) e cada tarefa
#: recebe uma cópia do contexto — um atributo único misturaria dois comandos simultâneos.
EFEITO_INICIADO: ContextVar[str | None] = ContextVar("efeito_iniciado", default=None)


def marcar_efeito(descricao: str) -> None:
    """Grava que o aparelho foi TOCADO. Chamado imediatamente antes da ação irreversível, nunca depois."""
    EFEITO_INICIADO.set(descricao)


async def ponto_seguro() -> None:
    """Ponto em que um cancelamento pendente é entregue ANTES de a próxima ação começar.

    `asyncio.to_thread` não é interrompível no meio: quem cancela durante um `emu.start_process` não para nada,
    só abandona a espera. Então o cancelamento tem de ser percebido AQUI, entre as etapas — é o que torna
    possível cancelar um `start` de 540 s enquanto ele espera vaga na fila de boot, que é o caso real.
    """
    await asyncio.sleep(0)


class VerbRefused(RuntimeError):
    """Recusa ANTES de agir — então nada aconteceu no aparelho, e o central pode dizer isso com certeza."""


class VerbUncertain(RuntimeError):
    """Agiu e não se sabe o desfecho. Nunca é convertido em sucesso nem em falha."""


class VerbFailed(RuntimeError):
    """Agiu, o desfecho é CONHECIDO e é negativo. Diferente de `VerbRefused` (nada aconteceu no aparelho) e de
    `VerbUncertain` (não se sabe). Hoje: hibernar que desligou sem salvar snapshot."""


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
            await asyncio.sleep(INTERVALO_SONDA_S)
            try:
                # `adb.state()` é subprocess com timeout de 8 s: no laço de eventos ele travava o agente inteiro
                # a cada sondagem — sem batida, sem responder ping, sem tratar Ack/Cancel (achado #37).
                if await asyncio.to_thread(adb.state) == "device" and await asyncio.to_thread(adb.boot_completed):
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
        if await asyncio.to_thread(self.avd.exists, spec.avd_name):
            return {"created": False, "detail": "o AVD já existia"}
        self.progress(f"criando o AVD {spec.avd_name}")
        await ponto_seguro()
        try:
            marcar_efeito(f"a criação do AVD {spec.avd_name} já tinha começado nesta máquina")
            await asyncio.to_thread(self.avd.create, spec.avd_name, self._android())
        except AvdError as exc:
            raise VerbRefused(str(exc)) from exc
        return {"created": True}

    async def _v_start(self, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        # `estado()` varre processos com psutil e `avd.exists` toca o disco: fora do laço, os dois.
        estado, _ = await asyncio.to_thread(self.estado, spec)
        if estado == "running":
            return {"started": False, "detail": "o emulador já estava no ar"}
        if not await asyncio.to_thread(self.avd.exists, spec.avd_name):
            raise VerbRefused(f"o AVD {spec.avd_name} não existe nesta máquina; peça 'create' antes")
        # `from_snapshot` no resultado é AFIRMAÇÃO sobre o que aconteceu, não repetição do que foi pedido: com
        # hibernação desligada o emulador sobe com `-no-snapshot` (emulator.py) e a resposta dizia `true` do
        # mesmo jeito. Só continua verdadeiro o que a máquina consegue cumprir.
        do_snapshot = (bool(params.get("from_snapshot")) and bool(self._android().hibernation)
                       and await asyncio.to_thread(self.snapshot_existe, spec))
        # Último instante antes da fila: um cancelamento pedido até aqui é entregue com o aparelho intacto. A
        # espera na fila em si já é interrompível (é um `await` no semáforo) — e ela é o trecho MAIS longo de um
        # `start` em lote: com `boot_parallelism=1`, o 4º de seis só começa depois de 312 s.
        await ponto_seguro()
        if self._boot.locked():
            # A fila existe (ela evita o ANR de quatro boots juntos), mas o central não a enxergava: o tempo
            # parado aqui era contado como tempo de boot e virava "resultado incerto" sem nada ter falhado. Dito
            # em voz alta, o central e quem olha o painel sabem que o aparelho está ESPERANDO, não travando.
            self.progress(f"{spec.avd_name} está {MARCA_DE_FILA} deste worker (um emulador por vez)")
        async with self._boot:            # um boot por vez: quatro juntos travaram os quatro em ANR
            # A guarda de RAM é reavaliada DENTRO da fila, imediatamente antes de subir: avaliada fora, N starts
            # simultâneos passavam todos pela mesma leitura de memória livre e só o primeiro tinha a RAM que a
            # conta prometia.
            self._guarda_de_ram()
            await ponto_seguro()          # último instante em que o aparelho ainda não foi tocado
            self.progress(f"subindo {spec.avd_name} na porta {spec.console_port}")
            marcar_efeito(f"o emulador {spec.avd_name} já tinha sido iniciado nesta máquina")
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

    def snapshot_existe(self, spec: DeviceSpec) -> bool:
        """Há snapshot salvo deste AVD NESTA máquina? É a pergunta que separa acordar de ligar a frio."""
        return (self.cfg.avd_home / f"{spec.avd_name}.avd" / "snapshots" / SNAPSHOT).exists()

    async def _v_wake(self, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        """Acordar é subir A PARTIR DO SNAPSHOT — e só isso. Sem hibernação ligada ou sem snapshot salvo, isto é
        RECUSADO: era aqui que o agente fazia um boot a frio de minutos e respondia `from_snapshot: true`, ou
        seja, sucesso com dado falso. Quem quer ligar a frio pede `start`, que é o verbo dessa operação.
        """
        if not self._android().hibernation:
            raise VerbRefused("hibernação desligada neste worker (android.hibernation): 'acordar' subiria a frio "
                              "e não a partir do snapshot; peça 'start'")
        if not await asyncio.to_thread(self.snapshot_existe, spec):
            raise VerbRefused(f"não há snapshot salvo de {spec.avd_name} nesta máquina; peça 'start' para um boot "
                              "a frio")
        return await self._v_start(spec, {**params, "from_snapshot": True})

    async def _v_stop(self, spec: DeviceSpec, _p: dict[str, Any]) -> dict[str, Any]:
        estado, _ = await asyncio.to_thread(self.estado, spec)
        if estado in ("stopped", "absent"):
            return {"stopped": False, "detail": "já estava desligado"}
        self.progress(f"desligando {spec.avd_name}")
        await ponto_seguro()
        marcar_efeito(f"o desligamento de {spec.avd_name} já tinha começado nesta máquina")
        detalhe = await asyncio.to_thread(emu.stop_process, self.adb_for(spec), self.pids.get(spec.avd_name),
                                          spec.avd_name)
        self.pids.pop(spec.avd_name, None)
        return {"stopped": True, "detail": detalhe}

    async def _v_hibernate(self, spec: DeviceSpec, _p: dict[str, Any]) -> dict[str, Any]:
        estado, _ = await asyncio.to_thread(self.estado, spec)
        if estado != "running":
            raise VerbRefused("hibernar exige o emulador no ar")
        self.progress(f"salvando snapshot de {spec.avd_name}")
        await ponto_seguro()
        try:
            marcar_efeito(f"o snapshot de {spec.avd_name} já estava sendo salvo nesta máquina")
            await asyncio.to_thread(self.adb_for(spec).snapshot_save, SNAPSHOT)
        except AdbError as exc:
            # Snapshot que não salvou com CERTEZA não vira hibernação: desliga e RECUSA o sucesso. Era `succeeded`
            # com `hibernated: False` no corpo — e o painel, que lê o estado do comando, dizia "Hibernada". Regra
            # única com o caminho local (`devices.manager.sem_snapshot`): isto é `failed`, com o motivo.
            await self._v_stop(spec, {})
            raise VerbFailed(f"desligado sem snapshot: o snapshot não foi salvo ({exc}); "
                             "o próximo boot será a frio") from exc
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
