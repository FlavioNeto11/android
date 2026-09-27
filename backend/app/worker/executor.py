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
from ..devices import recursos
from ..devices.adb import Adb, AdbError, AdbTimeout
from ..devices.avd import AvdError, AvdManager
from ..devices.sdk import SdkTools
from ..workers.protocol import MARCA_DE_FILA
from .settings import DeviceSpec, WorkerSettings

try:
    # `metricas.py` vai no pacote do agente (`backend/worker-manifest.txt`), mas o import segue opcional: uma
    # árvore montada à mão ou por instalador anterior ao K-034 pode não tê-lo, e medir nunca pode derrubar um
    # boot — sem o módulo, a contagem simplesmente não acontece nesta máquina.
    from ..metricas import metricas as _metricas
except ImportError:  # pragma: no cover - agente instalado sem `metricas.py`
    _metricas = None

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


class ReservaDeRam:
    """RAM prometida a UM boot, do instante da admissão até se saber que ela já não é promessa.

    `expira_em` é `None` enquanto o boot está em andamento. Quando o `start` termina SEM o aparelho pronto e com o
    emulador possivelmente no ar (cancelado no meio da espera, preparo que não respondeu), a reserva vira ÓRFÃ:
    ninguém derruba aquele processo, e ele continua alocando. Ela só sai quando o processo some (morreu ou foi
    parado) ou quando o prazo do boot dele vence — o mesmo instante em que um boot bem-sucedido já a teria
    soltado, e a partir do qual o que ele alocou está na memória disponível que a guarda lê.
    """

    __slots__ = ("instance_id", "custo_mb", "avd_name", "expira_em")

    def __init__(self, instance_id: str, custo_mb: int, avd_name: str | None = None):
        self.instance_id, self.custo_mb, self.avd_name = instance_id, int(custo_mb), avd_name
        self.expira_em: float | None = None

    @property
    def orfa(self) -> bool:
        return self.expira_em is not None


class ReservasDeRam:
    """Livro das reservas de boot desta máquina.

    Existe porque a guarda de RAM lia a memória disponível AGORA, e o emulador que acabou de ser admitido ainda
    não alocou nada: com `boot_parallelism` > 1, dois `start` na fila liam o mesmo número e gastavam a mesma RAM.
    A reserva vale o custo INTEIRO — sem descontar o que o processo já alocou. É conservador de propósito: no
    Windows (WHPX) o working set do emulador não é medida confiável do que o convidado já tomou, e errar para mais
    RAM é o erro barato (`devices/perfis.py`).

    É também o livro dos boots ADMITIDOS que ainda não criaram processo: a guarda de vagas os conta junto com os
    processos no ar.

    Sem trava: tudo acontece no laço de eventos, e tomar uma reserva é síncrono (ler, conferir, registrar, sem
    `await` no meio). É essa a propriedade que impede duas admissões de gastar a mesma RAM ou a mesma vaga.
    """

    def __init__(self, *, relogio: Callable[[], float] = time.monotonic) -> None:
        self._vivas: dict[int, ReservaDeRam] = {}
        self.relogio = relogio

    def _vencer(self) -> None:
        agora = self.relogio()
        for chave, r in list(self._vivas.items()):
            if r.expira_em is not None and r.expira_em <= agora:
                self._vivas.pop(chave, None)

    def total_mb(self) -> int:
        self._vencer()
        return sum(r.custo_mb for r in self._vivas.values())

    def instancias(self) -> set[str]:
        """Aparelhos com boot admitido (em andamento ou órfão): ocupam vaga mesmo sem processo ainda."""
        self._vencer()
        return {r.instance_id for r in self._vivas.values()}

    def orfas(self) -> list[ReservaDeRam]:
        self._vencer()
        return [r for r in self._vivas.values() if r.orfa]

    def tomar(self, instance_id: str, custo_mb: int, avd_name: str | None = None) -> ReservaDeRam:
        reserva = ReservaDeRam(instance_id, custo_mb, avd_name)
        self._vivas[id(reserva)] = reserva
        return reserva

    def liberar(self, reserva: ReservaDeRam | None) -> None:
        if reserva is not None:
            self._vivas.pop(id(reserva), None)

    def orfanar(self, reserva: ReservaDeRam | None, expira_em: float) -> None:
        """O boot acabou sem o aparelho pronto e com o emulador talvez no ar: a reserva fica até `expira_em`, ou
        até o processo sumir (`liberar_de`)."""
        if reserva is not None and id(reserva) in self._vivas:
            reserva.expira_em = expira_em

    def liberar_de(self, instance_id: str) -> None:
        """O processo daquele aparelho foi parado ou morreu: as órfãs dele deixam de ser promessa."""
        for chave, r in list(self._vivas.items()):
            if r.orfa and r.instance_id == instance_id:
                self._vivas.pop(chave, None)

    def __len__(self) -> int:
        self._vencer()
        return len(self._vivas)


#: Valores de rótulo de `capacidade.reserva`. Conjunto FECHADO: é também o que o central aceita da batida.
RESULTADOS_DE_RESERVA = ("concedida", "recusada")
MOTIVOS_DE_RESERVA = ("ram", "desconhecido", "vagas")


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
        #: RAM prometida a boots em andamento. Descontada pela guarda e declarada na batida (`reserved_mb`).
        self.reservas = ReservasDeRam()
        #: De onde vem a memória EFETIVA (cgroup no Linux, host no Windows). Injetável: o teste escolhe o número.
        self.medir_recursos: Callable[[], recursos.RecursosEfetivos] = recursos.medir
        #: `capacidade.reserva` desde a última batida, por `(resultado, motivo)`. A métrica do processo do agente
        #: não chega a lugar nenhum (só o central grava janela e serve /api/desempenho): é a batida que a leva
        #: (`Heartbeat.metricas`), e isto é o que ela ainda não levou.
        self._contagens: dict[tuple[str, str | None], int] = {}

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

    # ------------------------------------------------------------------ métrica da reserva
    def _contar_reserva(self, resultado: str, motivo: str | None = None) -> None:
        """`capacidade.reserva{resultado,motivo}` (adendo v0.20, C5): no registro deste processo, quando ele
        existe, e na conta que a próxima batida leva ao central."""
        if _metricas is not None:
            _metricas.contar("capacidade.reserva", resultado=resultado, motivo=motivo)
        chave = (resultado, motivo)
        self._contagens[chave] = self._contagens.get(chave, 0) + 1

    def tirar_contagens(self) -> list[dict[str, Any]]:
        """O que a batida leva (`Heartbeat.metricas`), zerando a conta. Quem não conseguiu enviar DEVOLVE
        (`devolver_contagens`): a contagem é delta, e perdida ela não volta."""
        saida = [{"nome": "capacidade.reserva",
                  "rotulos": {"resultado": r, **({"motivo": m} if m else {})}, "valor": n}
                 for (r, m), n in sorted(self._contagens.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))]
        self._contagens = {}
        return saida

    def devolver_contagens(self, contagens: list[dict[str, Any]]) -> None:
        for c in contagens:
            chave = (c["rotulos"]["resultado"], c["rotulos"].get("motivo"))
            self._contagens[chave] = self._contagens.get(chave, 0) + int(c["valor"])

    # ------------------------------------------------------------------ guardas do boot
    def _no_ar(self, spec: DeviceSpec) -> list[DeviceSpec]:
        """Os OUTROS aparelhos geridos com emulador no ar. Varre PROCESSOS: fora do laço (achado #37)."""
        return [d for d in self.settings.devices
                if d.instance_id != spec.instance_id and d.managed and self.pid_do_avd(d.avd_name) is not None]

    def _processos(self, spec: DeviceSpec,
                   orfas: list[tuple[str, str | None]]) -> tuple[list[DeviceSpec], list[str]]:
        """Numa thread: os aparelhos no ar e, das reservas órfãs, as de aparelho cujo emulador já não está no ar."""
        no_ar = self._no_ar(spec)
        vivos = {d.instance_id for d in no_ar}
        mortas = [iid for iid, avd in orfas if iid not in vivos and (not avd or self.pid_do_avd(avd) is None)]
        return no_ar, mortas

    async def _varrer(self, spec: DeviceSpec) -> list[DeviceSpec]:
        """Varre os processos fora do laço e, de volta a ele, solta as órfãs cujo emulador já não está no ar. A
        lista de órfãs é tirada no laço ANTES da thread: o livro só é lido e escrito no laço."""
        orfas = [(r.instance_id, r.avd_name) for r in self.reservas.orfas()]
        no_ar, mortas = await asyncio.to_thread(self._processos, spec, orfas)
        for iid in mortas:
            self.reservas.liberar_de(iid)
        return no_ar

    def _conferir_vagas(self, spec: DeviceSpec, no_ar: list[DeviceSpec]) -> None:
        """A máquina se protege sozinha, parte 2: `max_slots` é o que ESTE worker declarou aceitar manter ligado.

        Conta os PROCESSOS no ar (o agente pode ter subido com emuladores já ligados, e é a RAM desta máquina que
        paga a conta) E os boots já admitidos que ainda não criaram processo (o livro de reservas). Só com os
        processos, dois `start` com `boot_parallelism` > 1 passavam juntos pela guarda — o processo do primeiro
        ainda não existia quando o segundo contava — e a máquina ficava acima de `max_slots`. Síncrono, no laço:
        entre esta conta e o registro da reserva não há `await`.
        """
        ocupando = {d.instance_id for d in no_ar} | (self.reservas.instancias() - {spec.instance_id})
        if len(ocupando) >= self.settings.max_slots:
            self._contar_reserva("recusada", "vagas")
            raise VerbRefused(f"este worker aceita {self.settings.max_slots} aparelho(s) ligado(s) ao mesmo tempo "
                              f"e já tem {len(ocupando)} (no ar ou ligando): {', '.join(sorted(ocupando))}")

    def _custo_de_ram(self, spec: DeviceSpec) -> int:
        """Quanta RAM do HOST este aparelho vai custar ao subir — não a do convidado (`hw.ramSize`).

        Uma fonte só: `AndroidCfg.est_ram_host_mb()` da configuração DESTE aparelho, que vem do perfil medido da
        imagem (`devices/perfis.py`) ou, com `ram_mb` explícito, de `ram_mb + 1100` (sobrecarga do processo,
        medida). Era `ram_per_device_mb` (1800 no exemplo), abaixo de todo custo medido: a guarda aprovava boot
        que o host não aguentava, e a VM da loja com 4 GB era cobrada 4096 sem sobrecarga nenhuma.

        O `est_instance_ram_mb` do bloco `android:` global descreve o aparelho PADRÃO: um aparelho com imagem ou
        RAM próprias não o herda (a loja de 4 GB não custa os 3000 MB do aparelho de tarefa).
        `ram_per_device_mb`, quando declarado, é PISO: nunca baixa a conta abaixo do medido.
        """
        android = self._android_de(spec)
        if spec.ram_mb is not None or spec.system_image is not None:
            android = android.model_copy(update={"est_instance_ram_mb": None})
        return max(int(android.est_ram_host_mb()), int(self.settings.ram_per_device_mb or 0))

    def _guarda_de_ram(self, spec: DeviceSpec) -> int:
        """A máquina se protege sozinha: confere a RAM e devolve o custo a reservar. Quem registra a reserva é
        `_admitir`, no mesmo passo síncrono — sem `await` entre ler a memória e registrar.

        O central exclui aparelho externo de `slots_used()` de propósito, então é aqui que a conta fecha. Ela
        desconta o que já está prometido a boots em andamento (e às órfãs de boots que não terminaram prontos):
        sem isso, com `boot_parallelism` > 1, dois boots liam a mesma memória livre e gastavam a mesma RAM. A
        memória é a EFETIVA (`devices/recursos.py`): num cgroup com limite, a folga sob o limite.
        """
        try:
            disponivel = self.medir_recursos().mem_available_mb
        except Exception:  # noqa: BLE001 - medição que quebra é "não se sabe", e não se sabe não é "cabe"
            log.exception("%s: não foi possível medir a RAM desta máquina", spec.instance_id)
            disponivel = None
        if disponivel is None:
            self._contar_reserva("recusada", "desconhecido")
            raise VerbRefused(f"RAM desta máquina não pôde ser medida agora: {spec.instance_id} não foi ligado "
                              "(sem medição, o boot não é admitido)")
        custo = self._custo_de_ram(spec)
        reservado = self.reservas.total_mb()
        sobraria = disponivel - reservado - custo
        if sobraria < self.settings.min_free_ram_mb:
            self._contar_reserva("recusada", "ram")
            raise VerbRefused(f"RAM insuficiente neste worker: {spec.instance_id} custa {custo} MB, "
                              f"{disponivel} MB disponíveis"
                              + (f" (−{reservado} MB reservados para {len(self.reservas)} boot(s) em andamento)"
                                 if reservado else "")
                              + f", sobrariam {sobraria} MB e o mínimo é {self.settings.min_free_ram_mb} MB")
        return custo

    def _admitir(self, spec: DeviceSpec, no_ar: list[DeviceSpec]) -> ReservaDeRam:
        """Vagas, RAM e registro da reserva num passo SÍNCRONO. A reserva existe mesmo com a guarda de RAM
        desligada (teste): ela é também a vaga do boot admitido."""
        self._conferir_vagas(spec, no_ar)
        custo = self._guarda_de_ram(spec)
        self._contar_reserva("concedida")
        return self.reservas.tomar(spec.instance_id, int(custo or 0), spec.avd_name)

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

        O preparo só tem efeitos idempotentes (K-031: o toque no diálogo saiu dele) e é limitado pelo prazo do próprio
        `adb` (40 s). O tempo que ele gastar é DEVOLVIDO à escada, até uma rodada inteira: sem isso, um preparo lento
        que terminasse perto do fim do prazo deixava sondas de 1 s, e um display vivo mas lento virava `uncertain`
        (revisão pós-merge do PR #7, achado 6). Uma sonda lenta sozinha continua sem esticar o prazo do verbo.
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
            devolvido = 0.0
            if not preparado:
                preparado = True
                inicio_do_preparo = time.monotonic()
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
                devolvido = min(prontidao.prazo_da_rodada(), time.monotonic() - inicio_do_preparo)
            p = await asyncio.to_thread(prontidao.avaliar, adb, rotulo=spec.instance_id,
                                        restante_s=max(limite - time.monotonic(), devolvido))
            if p.pronto:
                return
            if p.estado == "erro":
                # A sonda quebrou por erro de programação (pilha no log do agente): repetir até o prazo quebraria
                # igual. O processo está no ar e o Android não foi consultado — `uncertain`, na hora.
                raise VerbUncertain(f"a prontidão não pôde ser avaliada: {p.detalhe()}; o processo está no ar. "
                                    "Estado desconhecido")
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
        self._conferir_vagas(spec, await self._varrer(spec))
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
            no_ar = await self._varrer(spec)
            # Conferir e reservar é UM passo síncrono: nenhum `await` entre contar vagas, ler a memória e registrar
            # a reserva, senão outro boot da fila leria os mesmos números.
            reserva = self._admitir(spec, no_ar)
            tocado = pronto = False
            inicio = 0.0
            prazo = float(params.get("boot_timeout_s") or 480)
            try:
                await ponto_seguro()          # último instante em que o aparelho ainda não foi tocado
                # O hardware do AVD é reaplicado em TODO start, como o central faz (`manager._boot`): antes só
                # entrava ao criar o AVD, e mudar `ram_mb` no worker.yaml não mudava nada nos aparelhos existentes
                # — o android-12 seguiu com 1536 MB depois de a configuração pedir mais.
                android = self._android_de(spec)
                if await asyncio.to_thread(self.avd.exists, spec.avd_name):
                    try:
                        await asyncio.to_thread(self.avd.apply_hardware, spec.avd_name, android)
                    except (AvdError, OSError) as exc:
                        # Um `config.ini` ilegível não impede o start: quem julga um AVD quebrado é o emulador,
                        # com a mensagem dele. Aqui só se perde a atualização de hardware, e isso fica no log.
                        log.warning("%s: não deu para reaplicar o hardware do AVD %s (%s)", spec.instance_id,
                                    spec.avd_name, exc)
                self.progress(f"subindo {spec.avd_name} na porta {spec.console_port}")
                marcar_efeito(f"o emulador {spec.avd_name} já tinha sido iniciado nesta máquina")
                # Daqui em diante pode haver processo — mesmo que esta espera seja cancelada, a thread que o cria
                # segue até o fim (`to_thread` não é interrompível no meio).
                tocado = True
                inicio = self.reservas.relogio()
                try:
                    pid = await asyncio.to_thread(
                        emu.start_process, self.cfg, self.tools, spec.avd_name, spec.console_port, android,
                        wipe_data=bool(params.get("wipe_data")), from_snapshot=do_snapshot)
                except Exception:
                    # `start_process` só levanta antes do `Popen` ou no próprio `Popen`: não há processo. Já o
                    # cancelamento (`BaseException`) não passa por aqui — a thread segue e o processo pode nascer.
                    tocado = False
                    raise
                self.pids[spec.avd_name] = pid
                await self._espera_boot(spec, deadline_s=prazo)
                pronto = True
            finally:
                if pronto or not tocado:
                    # Pronto: a memória dele já aparece como usada na próxima leitura. Nada iniciado (recusa,
                    # falha antes do emulador): não há quem consuma. Nos dois casos a promessa acaba aqui.
                    self.reservas.liberar(reserva)
                else:
                    # Cancelado na espera, preparo sem resposta, prazo estourado: ninguém derruba o emulador, e
                    # ele continua subindo e alocando. A reserva fica até o processo sumir (varredura, `stop`) ou
                    # até o prazo do boot vencer — quando um boot bem-sucedido já a teria soltado.
                    self.reservas.orfanar(reserva, inicio + prazo)
        # O relógio NÃO é mais acertado aqui (K-031): o `cmd alarm set-time` leva um instante absoluto e, estourado,
        # cai atrasado e ATRASA o convidado — depois de o central já ter readotado o aparelho. Quem cuida do relógio é
        # o central, como condição própria, depois de o aparelho entrar no ar
        # (`DeviceManager.conferir_relogio_do_convidado`), pelo mesmo adb que ele já usa para todo o resto. "O worker
        # sabe do processo; o central sabe do Android."
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
            self.reservas.liberar_de(spec.instance_id)      # sem processo, a órfã dele não é mais promessa
            return {"stopped": False, "detail": "já estava desligado"}
        self.progress(f"desligando {spec.avd_name}")
        await ponto_seguro()
        marcar_efeito(f"o desligamento de {spec.avd_name} já tinha começado nesta máquina")
        detalhe = await asyncio.to_thread(emu.stop_process, self.adb_for(spec), self.pids.get(spec.avd_name),
                                          spec.avd_name)
        self.pids.pop(spec.avd_name, None)
        self.reservas.liberar_de(spec.instance_id)
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
