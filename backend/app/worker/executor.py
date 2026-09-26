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
from ..devices import prontidao
from ..devices.adb import Adb, AdbError, AdbTimeout
from ..devices.avd import AvdError, AvdManager
from ..devices.sdk import SdkTools
from ..workers.protocol import MARCA_DE_FILA
from .settings import DeviceSpec, WorkerSettings

log = logging.getLogger("poc.worker")

SNAPSHOT = emu.SNAPSHOT_NAME
#: Verbos que o agente executa. O central continua dono dos verbos de ADB puro.
#:
#: `emulator_log` não mexe no aparelho: ele devolve a cauda do log do emulador DESTA máquina. Existe porque o
#: log fica em `work_dir/logs/emulator-<avd>.log` no worker, fora do alcance de quem opera o painel — quando um
#: boot remoto terminava `uncertain`, o operador recebia uma frase e nada mais.
VERBS = ("create", "start", "wake", "stop", "hibernate", "restart", "reset", "emulator_log")

#: Verbos cujo fracasso vale um log. São os que sobem o emulador: é no log dele que está o motivo.
VERBOS_COM_LOG = ("start", "wake", "restart", "reset")
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


class VerbError(RuntimeError):
    """Base dos desfechos negativos. `dados` é o que ACOMPANHA o motivo até o `result.data` do comando — hoje a
    cauda do log do emulador, que sem isto ficaria só nesta máquina."""

    def __init__(self, *args: Any, dados: dict[str, Any] | None = None):
        super().__init__(*args)
        self.dados = dados


class VerbRefused(VerbError):
    """Recusa ANTES de agir — então nada aconteceu no aparelho, e o central pode dizer isso com certeza."""


class VerbUncertain(VerbError):
    """Agiu e não se sabe o desfecho. Nunca é convertido em sucesso nem em falha."""


class VerbFailed(VerbError):
    """Agiu, o desfecho é CONHECIDO e é negativo. Diferente de `VerbRefused` (nada aconteceu no aparelho) e de
    `VerbUncertain` (não se sabe). Hoje: hibernar que desligou sem salvar snapshot."""


class FilaDeBoot:
    """Fila de boot com teto AJUSTÁVEL em tempo de execução.

    Era um `asyncio.Semaphore(boot_parallelism)` fixo no arranque: mudar o número exigia editar o `worker.yaml` e
    reiniciar o agente. Com os limites decididos no painel (tela Limites → Por servidor), o teto muda com o
    agente de pé. Baixar o teto não interrompe quem já está ligando — só segura os próximos.
    """

    def __init__(self, limite: int):
        self._limite = max(1, int(limite))
        self._ativos = 0
        self._cond = asyncio.Condition()

    @property
    def limite(self) -> int:
        return self._limite

    def ocupada(self) -> bool:
        """Quem chegar agora vai esperar? (era `Semaphore.locked()`)."""
        return self._ativos >= self._limite

    async def definir(self, limite: int) -> None:
        async with self._cond:
            self._limite = max(1, int(limite))
            self._cond.notify_all()

    async def __aenter__(self) -> "FilaDeBoot":
        async with self._cond:
            await self._cond.wait_for(lambda: self._ativos < self._limite)
            self._ativos += 1
        return self

    async def __aexit__(self, *exc: Any) -> None:
        async with self._cond:
            self._ativos -= 1
            self._cond.notify_all()


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
        self._boot = FilaDeBoot(settings.boot_parallelism)
        #: O que o `worker.yaml` desta máquina diz. É o que o `hello` declara e para onde um limite "voltar ao da
        #: máquina" (campo `None` na mensagem `limits`) retorna — o `settings` em si passa a carregar o EFETIVO.
        self.do_arquivo: dict[str, int] = {"max_slots": settings.max_slots,
                                           "boot_parallelism": settings.boot_parallelism,
                                           "min_free_ram_mb": settings.min_free_ram_mb}

    async def aplicar_limites(self, max_slots: int | None, boot_parallelism: int | None,
                              min_free_ram_mb: int | None) -> dict[str, int]:
        """Aplica os limites que o dono decidiu no painel. `None` = o valor do `worker.yaml`.

        As guardas de vagas e de RAM leem `self.settings` a cada `start`, então mudar aqui vale a partir do próximo
        boot; a fila de boot é redimensionada na hora.
        """
        efetivo = {"max_slots": max_slots or self.do_arquivo["max_slots"],
                   "boot_parallelism": boot_parallelism or self.do_arquivo["boot_parallelism"],
                   "min_free_ram_mb": (min_free_ram_mb if min_free_ram_mb is not None
                                       else self.do_arquivo["min_free_ram_mb"])}
        self.settings.max_slots = efetivo["max_slots"]
        self.settings.min_free_ram_mb = efetivo["min_free_ram_mb"]
        self.settings.boot_parallelism = efetivo["boot_parallelism"]
        await self._boot.definir(efetivo["boot_parallelism"])
        return efetivo

    # ------------------------------------------------------------------ utilidades
    def adb_for(self, spec: DeviceSpec) -> Adb:
        return Adb(self.tools, spec.serial)

    def _android(self) -> Any:
        return self.cfg.file.android

    def _android_de(self, spec: DeviceSpec) -> Any:
        """A configuração de emulador DESTE aparelho: a global do worker, com o que o aparelho declarou por cima.

        Antes só existia a global, e ela ia para `avd.create` e `emu.start_process` de todos os aparelhos deste
        worker. Uma VM de LOJA precisa de imagem com Play Store, janela e RAM próprios — e por isso a loja não
        podia ser remota por construção, não por decisão. Sem override declarado nada muda: devolve a própria
        configuração global, o mesmo objeto de antes.
        """
        mudancas = {campo: valor for campo, valor in (
            ("system_image", spec.system_image), ("ram_mb", spec.ram_mb), ("window", spec.window),
        ) if valor is not None}
        return self._android().model_copy(update=mudancas) if mudancas else self._android()

    def _guarda_de_vagas(self, spec: DeviceSpec) -> None:
        """A máquina se protege sozinha, parte 2: `max_slots` é o que ESTE worker declarou aceitar manter ligado,
        e até aqui ele não passava de número exibido no painel do central.

        A conta é pelo PROCESSO (o que `estado()` sabe), não pelo que o central acha: o agente pode ter subido
        com emuladores já no ar, e é a RAM desta máquina que paga a conta em qualquer um dos casos.
        """
        ligados = [d for d in self.settings.devices
                   if d.instance_id != spec.instance_id and d.managed and self.pid_do_avd(d.avd_name) is not None]
        if len(ligados) >= self.settings.max_slots:
            raise VerbRefused(f"este worker aceita {self.settings.max_slots} aparelho(s) ligado(s) ao mesmo tempo "
                              f"e já tem {len(ligados)}: {', '.join(d.instance_id for d in ligados)}")

    def _custo_de_ram(self, spec: DeviceSpec) -> int:
        """Quanta RAM do host este aparelho vai custar ao subir.

        `ram_per_device_mb` é a conta do aparelho PADRÃO deste worker. Um aparelho que declara `ram_mb` próprio
        (a VM da loja é o caso: imagem com Play Store, com janela) custa outra coisa, e cobrar dele a conta do
        padrão deixaria a guarda aprovar um boot que o host não aguenta. A sobrecarga do processo é preservada:
        é a diferença entre a conta do worker e a RAM do emulador padrão, medida, não chutada.
        """
        if spec.ram_mb is None:
            # Sem número explícito, a conta é a do perfil da imagem (medida), não o chute de `ram_per_device_mb`.
            padrao = self._android()
            return int(padrao.est_ram_host_mb()) if not padrao.ram_mb else self.settings.ram_per_device_mb
        sobrecarga = max(0, self.settings.ram_per_device_mb - int(self._android().ram_efetiva()))
        return spec.ram_mb + sobrecarga

    def _guarda_de_ram(self, spec: DeviceSpec) -> None:
        """A máquina se protege sozinha: o central exclui aparelho externo de `slots_used()` de propósito."""
        vm = psutil.virtual_memory()
        livre_mb = int(vm.available / (1024 * 1024))
        custo = self._custo_de_ram(spec)
        sobraria = livre_mb - custo
        if sobraria < self.settings.min_free_ram_mb:
            raise VerbRefused(f"RAM insuficiente neste worker: {spec.instance_id} custa {custo} MB, sobrariam "
                              f"{sobraria} MB e o mínimo é {self.settings.min_free_ram_mb} MB")

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
        """`start`/`wake` só voltam quando o Android está PRONTO pela definição única de `devices/prontidao.py`:
        adb `device` → `boot_completed` → servicemanager → system_server → display.

        Wake do android-09 (25/09/2026): `boot_completed=1` restaurado do snapshot, o preparo ficou 40 s mudo e o
        comando fechou `succeeded` 0,3 s depois — só com `service check`, o PR #5 teria fechado igual. Agora:
        preparo que ESTOURA o prazo (`AdbTimeout`) deixa o efeito dele no aparelho incerto (o adb encerra só o
        cliente local) e esta tentativa fecha `uncertain` na hora, mesmo que as sondas respondam logo depois — a
        forense viu 3 s de recuperação parcial antes do travamento. Erro rápido (`AdbError`) continua aviso: a escada,
        que vem depois dele, decide. Pronto só com os três subsistemas respondendo, dentro do prazo deste verbo;
        senão `uncertain` com o degrau em que parou.
        """
        adb = self.adb_for(spec)
        limite = time.monotonic() + deadline_s
        preparado = False
        ultimo = "o adb não chegou a `device` com o boot concluído"
        while time.monotonic() < limite:
            await asyncio.sleep(INTERVALO_SONDA_S)
            try:
                # `adb.state()` é subprocess com timeout de 8 s: no laço de eventos ele travava o agente inteiro
                # a cada sondagem — sem batida, sem responder ping, sem tratar Ack/Cancel (achado #37).
                if not (await asyncio.to_thread(adb.state) == "device" and await asyncio.to_thread(adb.boot_completed)):
                    continue
            except AdbError:
                continue
            if not preparado:
                preparado = True
                try:
                    await asyncio.to_thread(adb.prepare_for_automation)
                except AdbTimeout as exc:
                    # Não é "o comando recusou": o Android não respondeu, e o efeito do preparo segue incerto no
                    # aparelho. Nenhuma sonda desta tentativa prova ser posterior a ele.
                    raise VerbUncertain(f"o preparo não respondeu ({exc}): o boot concluiu e o processo está no ar, "
                                        "mas o efeito do preparo no aparelho é incerto e esta tentativa não fecha "
                                        "pronta. Estado desconhecido") from exc
                except AdbError as exc:
                    log.warning("%s: preparo falhou: %s", spec.instance_id, exc)
            p = await asyncio.to_thread(prontidao.avaliar, adb, restante_s=limite - time.monotonic())
            if p.pronto:
                return
            ultimo = p.detalhe()
        raise VerbUncertain(f"o aparelho não completou o boot em {deadline_s:.0f} s — {ultimo}; o processo pode estar "
                            "no ar, mas o Android não responde. Estado desconhecido")

    # ------------------------------------------------------------------ verbos
    async def run(self, verb: str, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        if verb not in VERBS:
            raise VerbRefused(f"este worker não executa '{verb}'")
        if not spec.managed and verb in ("create", "start", "stop", "hibernate", "restart", "reset", "wake"):
            raise VerbRefused(f"{spec.instance_id} é aparelho não gerido por este agente (managed=false)")
        metodo = getattr(self, f"_v_{verb}")
        try:
            return await metodo(spec, params)
        except (VerbUncertain, VerbFailed) as exc:
            # Um boot que falhou ou ficou incerto leva junto a cauda do log do emulador. É o que fecha a
            # distância entre as duas máquinas: o log mora aqui, e quem precisa dele está no painel do central.
            # `VerbRefused` fica de fora de propósito — recusa é ANTES de agir, e não há emulador de que falar.
            if verb in VERBOS_COM_LOG and spec.managed and not exc.dados:
                exc.dados = await asyncio.to_thread(self.cauda_do_log, spec)
            raise

    def cauda_do_log(self, spec: DeviceSpec) -> dict[str, Any]:
        """O fim do log do emulador deste AVD, já sem o que parecer segredo (`devices/emulator.redigir`)."""
        return emu.log_do_emulador(self.cfg.logs_dir, spec.avd_name)

    async def _v_emulator_log(self, spec: DeviceSpec, params: dict[str, Any]) -> dict[str, Any]:
        """Lê o log e não toca no aparelho. Sem este verbo, o log do emulador remoto não tinha contraparte
        nenhuma: o plano, o commit do agente e a docstring do pacote `workers` o citavam como entregue, e o
        operador só via a frase do desfecho."""
        limite = int(params.get("max_bytes") or emu.LOG_MAX_BYTES)
        return await asyncio.to_thread(emu.log_do_emulador, self.cfg.logs_dir, spec.avd_name,
                                       max(512, min(limite, emu.LOG_MAX_BYTES)))

    async def _v_create(self, spec: DeviceSpec, _p: dict[str, Any]) -> dict[str, Any]:
        if await asyncio.to_thread(self.avd.exists, spec.avd_name):
            return {"created": False, "detail": "o AVD já existia"}
        self.progress(f"criando o AVD {spec.avd_name}")
        await ponto_seguro()
        try:
            marcar_efeito(f"a criação do AVD {spec.avd_name} já tinha começado nesta máquina")
            await asyncio.to_thread(self.avd.create, spec.avd_name, self._android_de(spec))
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
        # Vagas antes da fila, como recusa RÁPIDA: quando a máquina já está cheia, esperar a fila inteira para
        # recusar depois prenderia o comando pelo prazo todo. A decisão que VALE é a de dentro do semáforo.
        await asyncio.to_thread(self._guarda_de_vagas, spec)
        # `from_snapshot` no resultado é AFIRMAÇÃO sobre o que aconteceu, não repetição do que foi pedido: com
        # hibernação desligada o emulador sobe com `-no-snapshot` (emulator.py) e a resposta dizia `true` do
        # mesmo jeito. Só continua verdadeiro o que a máquina consegue cumprir.
        do_snapshot = (bool(params.get("from_snapshot")) and bool(self._android().hibernation)
                       and await asyncio.to_thread(self.snapshot_existe, spec))
        # Último instante antes da fila: um cancelamento pedido até aqui é entregue com o aparelho intacto. A
        # espera na fila em si já é interrompível (é um `await` no semáforo) — e ela é o trecho MAIS longo de um
        # `start` em lote: com `boot_parallelism=1`, o 4º de seis só começa depois de 312 s.
        await ponto_seguro()
        if self._boot.ocupada():
            # A fila existe (ela evita o ANR de quatro boots juntos), mas o central não a enxergava: o tempo
            # parado aqui era contado como tempo de boot e virava "resultado incerto" sem nada ter falhado. Dito
            # em voz alta, o central e quem olha o painel sabem que o aparelho está ESPERANDO, não travando.
            self.progress(f"{spec.avd_name} está {MARCA_DE_FILA} deste worker "
                          f"({self._boot.limite} emulador(es) ligando por vez)")
        async with self._boot:            # um boot por vez: quatro juntos travaram os quatro em ANR
            # As duas guardas são reavaliadas DENTRO da fila, imediatamente antes de subir: avaliadas fora, N
            # starts simultâneos passavam todos pela mesma leitura (de memória livre, de processos no ar) e só o
            # primeiro tinha a vaga e a RAM que a conta prometia. A de vagas vai para outra thread porque conta
            # PROCESSO (`psutil.process_iter`), e varredura no laço de eventos é o achado #37.
            await asyncio.to_thread(self._guarda_de_vagas, spec)
            self._guarda_de_ram(spec)
            await ponto_seguro()          # último instante em que o aparelho ainda não foi tocado
            # O hardware do AVD é reaplicado em TODO start, como o central faz (`manager._boot`): antes só entrava
            # ao criar o AVD, e mudar `ram_mb` no worker.yaml não mudava nada nos aparelhos existentes — o
            # android-12 seguiu com 1536 MB depois de a configuração pedir mais.
            android = self._android_de(spec)
            if await asyncio.to_thread(self.avd.exists, spec.avd_name):
                try:
                    await asyncio.to_thread(self.avd.apply_hardware, spec.avd_name, android)
                except (AvdError, OSError) as exc:
                    # Um `config.ini` ilegível não impede o start: quem julga um AVD quebrado é o emulador, com a
                    # mensagem dele. Aqui só se perde a atualização de hardware, e isso fica no log.
                    log.warning("%s: não deu para reaplicar o hardware do AVD %s (%s)", spec.instance_id,
                                spec.avd_name, exc)
            self.progress(f"subindo {spec.avd_name} na porta {spec.console_port}")
            marcar_efeito(f"o emulador {spec.avd_name} já tinha sido iniciado nesta máquina")
            pid = await asyncio.to_thread(
                emu.start_process, self.cfg, self.tools, spec.avd_name, spec.console_port, android,
                wipe_data=bool(params.get("wipe_data")), from_snapshot=do_snapshot)
            self.pids[spec.avd_name] = pid
            prazo = float(params.get("boot_timeout_s") or 480)
            fim = time.monotonic() + prazo
            await self._espera_boot(spec, deadline_s=prazo)
        adb = self.adb_for(spec)
        try:
            await asyncio.to_thread(adb.sync_clock)
        except AdbTimeout as exc:
            # Contrato temporal (`devices/prontidao.py`): estouro DEPOIS da escada — `date`/`cmd alarm set-time` (este
            # passa pelo `system_server`) sem resposta, com efeito incerto no aparelho. Esta tentativa não fecha pronta.
            raise VerbUncertain(f"o Android parou de responder logo depois de ficar pronto: o acerto do relógio não "
                                f"respondeu ({exc}) e o efeito dele no aparelho é incerto; o processo está no ar. "
                                "Estado desconhecido") from exc
        except AdbError as exc:
            # Erro rápido depois da escada: retorno conhecido, mas pode ser `device offline` (o `AdbError` não
            # distingue). Relógio atrasado não impede a operação; a prontidão de antes é que não vale mais: decide
            # uma rodada nova, dentro do mesmo prazo do verbo.
            log.warning("%s: acerto do relógio falhou (%s); exigindo a prontidão de novo", spec.instance_id, exc)
            p = await asyncio.to_thread(prontidao.avaliar, adb, restante_s=fim - time.monotonic())
            if not p.pronto:
                raise VerbUncertain(f"o Android parou de responder logo depois de ficar pronto: o acerto do relógio "
                                    f"falhou ({exc}) e depois {p.detalhe()}; o processo pode estar no ar, mas o "
                                    "Android não responde. Estado desconhecido") from exc
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
        # O mesmo guard de `_v_wake`: sem hibernação ligada, "hibernar" desligaria o emulador SEM salvar snapshot
        # e responderia sucesso — e o `wake` seguinte subiria a frio. O agente já não anuncia o verbo neste caso
        # (`agent._hello`), mas um despacho vindo de central antigo ainda chegaria aqui.
        if not self._android().hibernation:
            raise VerbRefused("hibernação desligada neste worker (android.hibernation): não há snapshot a salvar; "
                              "peça 'stop'")
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
