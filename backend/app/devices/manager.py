"""Gerência das instâncias: ciclo de vida do emulador, captura de frames, sessão de automação,
lease de controle (IA × usuário) e entradas manuais — tudo coordenado pelo executor exclusivo."""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import io
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import psutil
from PIL import Image

from ..automation.appium_driver import AndroidDeviceIO, AppiumSession
from ..automation.appium_server import AppiumServer
from ..automation.driver import DeviceIO, DriverError, DriverTimeout, FalhaDeLeitura, sessao_perdida
from ..automation.hierarchy import MOTIVO_LOJA, RegraDeTelaSensivel, UiTree, parse_hierarchy
from ..config import AndroidCfg, Config
from ..db import INTEGRITY_ERRORS, Database, dumps, loads
from ..events import EventBus
from ..metricas import metricas
from ..modules.applications.infrastructure.registry import capabilities_of
from ..models import (AutomationInfo, ConnectivityInfo, ControlOwner, ReadinessInfo, EmulatorMetric, FrameInfo, InstanceCurrent,
                      InstanceDTO, InstancePorts, InstanceResources, InstanceState, ManualInput, Metrics, RendererInfo,
                      RepairPauseInfo)
# A porta do aprendizado (ADR-054): só o contrato, do kernel — `devices` não conhece a fila nem o livro.
from ..shared.costuras import SEM_COSTURAS_DE_GESTO, CosturaDeControle, TomadaDeControle, avisar
from ..util import new_token, now, now_iso, parse_iso, to_iso
from . import emulator as emu
from .adb import Adb, AdbError, AdbTimeout
from .publicacao import assinatura_material
from .apps_de_fundo import AjusteDosApps
from .apps_de_fundo import validar_lista as validar_apps_de_fundo
from .emulator_backend import EmulatorBackend, RealEmulatorBackend
from .avd import AvdError, AvdManager, capacidades_da_imagem, capacidades_do_avd
from .executor import DeviceExecutor
from .installer import LAUNCH_DEADLINE_S, wait_for_focus
from . import conectividade, prontidao
from .stream import backoff_s, stream_status
from .verbs import verbos_suportados
from .sdk import SdkTools
# A regra de codificação mora num lugar só, importado também pelo agente do worker (`observe_local`): as duas pontas
# têm de produzir os MESMOS bytes para a mesma tela. Os nomes com `_` ficam como apelidos do módulo — o código daqui os
# chama pelo global do módulo, e os testes que simulam uma codificação lenta trocam `manager._codificar`.
from .codificacao import (THUMB_WIDTH, Codificado as _Codificado, codificar as _codificar,  # noqa: F401
                          dimensoes_do_modelo, tamanho_png as _tamanho_png)
from .conta_observada import evidencia_legivel

log = logging.getLogger("poc.devices")
#: Idade máxima da última classificação de tela para a imagem TARDIA de evidência dispensar uma nova leitura da
#: hierarquia. A evidência fica guardada: mais velha que isto, a tela pode ter mudado para uma sensível sem ninguém ver.
CLASSIFICACAO_FRESCA_S = 2.0
MANUAL_LEASE_TTL_S = 600
#: Interesse em prévia (contrato C2): o painel renova antes de vencer. Abaixo de 5 s, uma aba lenta piscaria entre
#: ao vivo e suspensa; acima de 60 s, uma aba que fechou sem avisar manteria o aparelho sendo capturado à toa.
TTL_INTERESSE_MIN_S = 5.0
TTL_INTERESSE_MAX_S = 60.0
TTL_INTERESSE_PADRAO_S = 20.0
#: Prazo do frame com a IA no controle (`dto`, e `AI_FRAME_MAX_AGE_MS` no painel — o teste confere que são iguais).
#: A prévia cede a vez à IA e o frame chega UMA vez por ciclo dela: árvore, modelo, ação, assentamento. O limite do
#: foco (6 s) acusava "desatualizado" a cada ciclo — entre duas ações da IA no android-06, em r-20260928195344-02ee9e
#: e r-20260928165254-e31953, o menor intervalo foi 5,8 s e a mediana 21 s (39 intervalos). 30 s é a folga de 2,5×
#: da prévia sobre um ciclo sadio estimado em ~12 s: passar disso é a IA de fato sem olhar a tela, e aí o aviso vale.
FRAME_MAX_AGE_IA_S = 30.0
# Saúde do convidado: de quanto em quanto tempo sondar um aparelho no ar, e quantas falhas SEGUIDAS de sessão de
# automação bastam para parar de repetir calado e dizer que o aparelho está quebrado.
INTERVALO_DA_SONDA_S = 30
#: Quanto esperar, depois do boot e do preparo, pela PRIMEIRA resposta positiva do framework antes de declarar o
#: aparelho no ar. Um Android saudável responde na primeira sonda; o congelado de 25/09/2026 nunca respondeu.
RESPOSTA_POS_BOOT_S = 60.0
#: Piso do orçamento de prontidão quando o boot/wake já gastou quase todo o prazo: sem ele, um boot lento que chega à
#: interface no limite teria uma única sonda de 1 s. Um Android saudável responde os três degraus em < 2 s (medido).
#: É UMA RODADA INTEIRA: com 20 s fixos e o display a 20 s (`prontidao.PRAZO_S`), um display lento mas vivo teria só
#: ~4 s no piso — o mesmo falso negativo que o prazo novo do display corrige.
RESPOSTA_MIN_S = prontidao.prazo_da_rodada()
#: Prazo do `prepare_for_automation` no executor do aparelho (o adb dentro dele desiste em 40 s).
PRAZO_DO_PREPARO_S = 60.0
#: Teto da espera pelo fim de um preparo "zumbi" (o executor desistiu de esperar; a chamada segue viva na thread do
#: aparelho). Cortado também pelo orçamento de quem chama. Não termina a tempo = não pronto — nunca espera infinita.
ESPERA_DO_PREPARO_ZUMBI_S = 30.0
#: Espaçamento entre uma tentativa INCERTA de readoção (operação com efeito estourou o prazo) e a próxima no MESMO
#: guest. Sem ele o `_wait_boot` recomeçava ~1 s depois, justamente na janela em que um efeito atrasado cai e em que a
#: forense de 25/09 viu 3 s de recuperação parcial (android-04, 26/09: preparo estourado às 17:31:02, `online` às
#: 17:31:15). O mesmo ritmo da readoção periódica do externo (30 s), que já espaçava as tentativas dele.
ESPERA_APOS_TENTATIVA_INCERTA_S = 30.0
# Relógio do convidado: condição PRÓPRIA, fora da prontidão (K-031). Medir é só leitura; corrigir é
# `cmd alarm set-time` (efeito com instante absoluto) e fica fora do portão, conferido e reconferido: um set-time que
# estourou o prazo e caiu atrasado é desfeito na próxima reconferência.
RELOGIO_TOLERANCIA_S = 2
INTERVALO_DO_RELOGIO_S = 300.0
#: Reconferência antecipada quando o último acerto é incerto (estourou), não convergiu ou a medida falhou.
INTERVALO_DO_RELOGIO_PENDENTE_S = 60.0
RELOGIO_PREFIXO = "Relógio do aparelho"
# Com a IA operando o aparelho, o acerto do relógio ESPERA ela soltar (`clock_state="adiado"`): o `set-time` vai para a
# fila do aparelho e dispara TIME_SET em todo app — 100 acertos no android-06 em 28/09, no meio de
# r-20260928165254-e31953 e r-20260928195344-02ee9e. Acima deste desvio o próprio relógio ameaça login e TLS mais que
# um acerto no meio da etapa, e um aparelho com objetivos em sequência talvez nunca tenha folga: aí acerta assim mesmo.
RELOGIO_ADIAVEL_MAX_S = 120
#: Tarefas que só fazem sentido com o aparelho NO AR. Toda saída do ar (parar, hibernar, perder, soltar, degradar)
#: cancela TODAS: a do relógio, esquecida, acertava a hora durante o snapshot/stop ou publicava aviso depois de o
#: estado já ter limpado a atenção (revisão do PR #12). Uma lista só, para a próxima tarefa nova não ficar de fora.
TAREFAS_DO_NO_AR = ("capture", "automation", "arrumacao", "clock")
FALHAS_DE_SESSAO_PARA_DEGRADAR = 3
# Sondas SEGUIDAS sem resposta do adb que viram doença. Uma só é falta de informação (adb lento); três em 90 s num
# aparelho `online` é o android-12 de 23/09: convidado travado por dentro, e a sonda dizendo "não sei" para sempre.
SONDAS_MUDAS_PARA_DEGRADAR = 3
# Pressão do convidado que vira aviso (sem degradar): load acima de 4× as vCPUs ou menos de 8 % de RAM livre, em
# duas sondas seguidas. Medido: load 22 em 2 vCPUs e 87 MB livres de 1,5 GB era o aparelho "que não subia".
PRESSAO_LOAD_POR_CPU = 4.0
PRESSAO_RAM_LIVRE_MIN = 0.08
PRESSAO_SONDAS = 2
#: Interrupção acumulada no convidado OCIOSO (irq + softirq sobre o total de CPU, entre duas sondas de saúde). Um
#: aparelho recém-ligado fica em ~2%; acima disto, sem ninguém operando, é o estado que derrubou as execuções de
#: 28/09 (21% e 90% medidos, com leitura de tela de 10–45 s e ANR em série). Três sondas seguidas (~90 s).
IRQ_OCIOSO_MAX = 0.15
IRQ_SONDAS = 3
#: No máximo um reinício por interrupção a cada 6 h por aparelho: se não resolver, é outra doença, e o aviso fica.
IRQ_REINICIO_INTERVALO_S = 6 * 3600
IRQ_PREFIXO = "Convidado com interrupções acumuladas"
PRESSAO_PREFIXO = "Convidado sob pressão"
#: Fallback silencioso do renderizador (29.11): pedido `host`, selecionado `swiftshader`. O emulador não reclama, e o
#: app que não roda no SwiftShader (o Outlook, medido em 30/09/2026) derrubaria o processo do emulador.
RENDERIZADOR_PREFIXO = "Renderizador trocado pelo emulador"
# Remediação automática: intervalo mínimo entre pedidos. A ESCADA (quantos restarts, quando resetar, quando voltar
# a tentar) mora em `despacho.remediar`, contada no histórico de comandos — sobrevive a reinício do backend.
MAX_REINICIOS_DE_REMEDIACAO = 2
REINICIO_COOLDOWN_S = 600

#: O estado que cada verbo de ciclo de vida PROMETE quando termina bem. Uma tabela só, com duas perguntas em
#: cima dela: o caminho do worker aplica no central o mesmo efeito do caminho local (`aplicar_desfecho_remoto`),
#: e a reconciliação de comando `uncertain` pergunta ao estado real se aquilo aconteceu. Se fossem duas tabelas,
#: "verificado pelo estado real" e "aplicado depois do worker" poderiam discordar sobre o que é sucesso.
ESTADO_ALVO: dict[str, InstanceState] = {
    "start": InstanceState.online,
    "wake": InstanceState.online,
    "restart": InstanceState.online,
    "reset": InstanceState.online,
    "stop": InstanceState.stopped,
    "hibernate": InstanceState.hibernated,
    "create": InstanceState.stopped,
}
#: A DECISÃO que cada verbo registra sobre o aparelho, que sobrevive a reinício e manda no monitor. `create` não
#: decide nada sobre estar no ar; teclas e app não mexem no ciclo de vida.
DESEJO_DO_VERBO: dict[str, str] = {
    "start": InstanceState.online.value, "wake": InstanceState.online.value,
    "restart": InstanceState.online.value, "reset": InstanceState.online.value,
    "stop": InstanceState.stopped.value, "hibernate": InstanceState.stopped.value,
}


def sem_snapshot(porque: str) -> str:
    """A frase única de "hibernar não hibernou", nos dois caminhos (aqui e no agente do worker).

    Regra única do achado #155: pedir "Hibernar" e receber um desligamento comum NÃO é sucesso. O aparelho de
    fato desligou — então também não é recusa —, mas o próximo boot será a frio, que é exatamente o que a
    hibernação existia para evitar. O comando vira `failed` com este motivo, e o painel nunca diz "Hibernada".
    """
    return f"desligado sem snapshot: {porque}; o próximo boot será a frio"


class ControlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class InstanceBusy(Exception):
    pass


@dataclass(slots=True)
class Frame:
    info: FrameInfo
    mono: float
    jpeg_full: bytes
    jpeg_thumb: bytes
    #: Tela sensível (contrato C4): o frame é só um MARCADOR — sem imagem (`jpeg_* == b""`), com o tamanho da tela.
    #: Existe para tirar do ar a imagem anterior e para a prévia dizer "tela sensível" em vez de "desatualizada".
    sensitive: bool = False


@dataclass(slots=True)
class Observation:
    frame_id: str
    ts: str
    width: int
    height: int
    jpeg: bytes | None          # None quando a imagem foi omitida (ver `image_omitted`)
    tree: UiTree
    package: str | None
    sensitive: bool
    # Adendo v0.20, contrato C1 (todos opcionais): quando a hierarquia e a imagem foram lidas (`image_at=None` = sem
    # imagem), POR QUE não há imagem (`sensitive` | `policy`; omitir não é falha de captura), de onde veio e de
    # qual geração do runtime do aparelho.
    tree_at: str | None = None
    image_at: str | None = None
    image_omitted: str | None = None
    source: str = "central_adb"
    runtime_gen: int | None = None
    # Item 31.24 (C-2, migração 088): quanto ESTA observação gastou lendo a hierarquia e adquirindo a imagem, em ms, os
    # mesmos números que já iam para `observacao.ms`. O executor os grava na decisão que esta observação alimentou.
    # `None` = não medido (observação montada fora de `observe`, dublê de teste); sem imagem, `ms_imagem` fica `None`.
    ms_arvore: float | None = None
    ms_imagem: float | None = None


@dataclass(slots=True)
class _Interesse:
    """O que UMA conexão do painel está olhando. `grade=None` = conexão que nunca mandou `watch` (painel antigo):
    vale como grade em todos os aparelhos enquanto durar, sem prazo (`ate=None`)."""
    grade: frozenset[str] | None
    foco: str | None
    ate: float | None


class Limiter:
    """Semáforo redimensionável em tempo de execução, com alcance escolhido por uso.

    - `boot_parallelism` (ligar emulador): **por processo**, e isso está certo. O recurso protegido é a RAM e a
      CPU DESTA máquina. Torná-lo global seria o erro oposto — duas máquinas de 64 GB esperando uma pela outra
      para ligar aparelho. Este uso não recebe `vagas`.
    - `max_ai_concurrency` (chamadas ao modelo): **do sistema inteiro**, porque o recurso protegido é a taxa de
      chamadas contra o provedor. Por processo, limite 3 em dois backends virava seis chamadas simultâneas
      (achados #35 e #94). Este uso recebe `vagas` — um lease por linha em `ai_slots` (`taskqueue/ai_slots.py`),
      no mesmo molde da posse de etapa.

    O semáforo local continua valendo nos dois casos: ele é a fila JUSTA deste processo (quem chegou antes entra
    antes) e evita mandar N tarefas sondarem o banco quando só cabem 3. O lease é que decide o teto global.
    """

    def __init__(self, limit: int, *, vagas: Any = None):
        self._limit = max(1, limit)
        self._active = 0
        self._cond = asyncio.Condition()
        #: `VagasDeIA` ou `None`. Duck-typing de propósito: `devices` não conhece `taskqueue`.
        self._vagas = vagas
        #: Vagas tomadas no banco, uma por entrada ativa no `async with`. São intercambiáveis, então quem sai
        #: devolve a última — não precisa saber qual era "a dele".
        self._slots: list[int] = []

    def set_limit(self, limit: int) -> None:
        self._limit = max(1, limit)

    @property
    def active(self) -> int:
        return self._active

    @property
    def limit(self) -> int:
        """Teto atual (achado #93, ponto 1): o cartão do aparelho lê `active`/`limit` para "aguardando vaga de
        IA (n de M em uso)" em vez de um texto sem número por trás."""
        return self._limit

    async def __aenter__(self) -> "Limiter":
        async with self._cond:
            await self._cond.wait_for(lambda: self._active < self._limit)
            self._active += 1
        if self._vagas is not None:
            try:
                self._slots.append(await self._vagas.adquirir(self._limit))
            except BaseException:
                # Cancelamento enquanto espera vaga no banco: o lugar no semáforo local tem de voltar, senão
                # cada cancelamento encolheria o limite deste processo em um, para sempre.
                async with self._cond:
                    self._active -= 1
                    self._cond.notify_all()
                raise
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._vagas is not None and self._slots:
            self._vagas.liberar(self._slots.pop())
        async with self._cond:
            self._active -= 1
            self._cond.notify_all()


def _col(row: Any, nome: str) -> Any:
    """Coluna que pode não existir naquela linha (banco de uma versão anterior, consulta parcial)."""
    try:
        return row[nome] if nome in row.keys() else None
    except (KeyError, IndexError, AttributeError):
        return None


@dataclass(frozen=True)
class PausaDeReparo:
    """O aparelho está em experimento ou manutenção: o central NÃO emite `restart`/`reset` automáticos para ele
    (a escada de reparo e o reinício por saúde). Comando de pessoa e o `restart` da rede (o produto) passam."""

    until: datetime
    since: datetime
    reason: str
    by: str


class DeviceRuntime:
    def __init__(self, cfg: Config, tools: SdkTools, row: Any, io_factory: Callable[["DeviceRuntime"], DeviceIO] | None):
        self.cfg = cfg
        self.id: str = row["id"]
        self.index: int = row["idx"]
        self.avd_name: str = row["avd_name"]
        self.console_port: int = row["console_port"]
        # O endereço de ADB vem da INSTÂNCIA quando ela o tem (migração 024: instância dinâmica, criada a partir
        # de um aparelho anunciado por um worker) e do `config.yaml` no caminho antigo. Um lugar só, com o banco
        # na frente: acrescentar aparelho deixa de exigir edição de YAML e reinício.
        ext = (_col(row, "external_serial") or cfg.file.instances.external.get(self.id) or "").strip()
        if ext and not re.match(r"^[A-Za-z0-9_.:\-]{3,80}$", ext):
            raise ValueError(f"instances.external.{self.id}: serial ADB inválido")
        self.external = bool(ext)
        #: `config` (declarada no YAML) ou `dynamic` (adotada em tempo de execução, sem reiniciar o backend).
        self.origin: str = _col(row, "origin") or "config"
        #: Porta local DAQUI que o túnel encaminha, e porta de ADB do lado do worker. Nulas no caminho antigo,
        #: em que o mapa do túnel vive nos argumentos da tarefa agendada.
        self.tunnel_port: int | None = _col(row, "tunnel_port")
        self.remote_adb_port: int | None = _col(row, "remote_adb_port")
        #: Sobreposição do `android` para ESTA instância (migração 050): o que a pessoa pediu ao provisionar pela
        #: plataforma (`system_image`, `ram_mb`…). Vazio na instância do YAML, que continua lendo
        #: `instances.overrides` da configuração. Quem lê é `DeviceManager.android_de`, nunca `cfg.instance_android`.
        self.android_overrides: dict[str, object] = dict(loads(_col(row, "android_overrides"), {}) or {})
        #: Divergência entre as fontes de inventário (achado #47). Em memória de propósito: é REDERIVADA a cada
        #: `hello`/batida, e afirmação sobre worker desconectado descreve um passado que ninguém confirmou.
        self.inventory_state: str | None = None
        self.inventory_detail: str | None = None
        # Aparelho-loja: o inverso do externo. O ciclo de vida é nosso (liga, desliga), mas ele NUNCA recebe tarefa,
        # nunca é despejado pelo rodízio e não abre sessão de automação. Quem decide qualquer uma dessas coisas lê daqui.
        self.store = cfg.store_id == self.id
        #: Árvore da última observação (a de quando a etapa foi comprovada): vira memória do perfil sem outro dump.
        self.last_tree: Any = None
        #: `(atividade, time.monotonic())` quando a ÚLTIMA observação com conta travada achou a atividade de desafio do
        #: Instagram em foco (`dumpsys window`); `None` quando a janela em foco era outra. É o sinal forte do 29.23.
        self.atividade_de_desafio: tuple[str, float] | None = None
        #: Sessão de treinamento aberta neste aparelho (item 13.1). A verdade é `training_sessions`; isto só evita
        #: consultar o banco a cada toque.
        self.training_session_id: str | None = None
        self.serial = ext or f"emulator-{self.console_port}"
        self.ports = InstancePorts(system=row["system_port"], mjpeg=row["mjpeg_port"],
                                   chromedriver=row["chromedriver_port"])
        self.pid: int | None = row["emulator_pid"]
        self.boot_seconds: float | None = row["boot_seconds"]
        self.state = InstanceState.stopped
        self.state_detail: str | None = None
        # Estado DESEJADO, separado do observado. Nulo = nenhuma decisão registrada. É o que distingue "caiu
        # sozinho, reconecte" de "alguém mandou parar, deixe parado" — sem ele o monitor desfazia o "Parar".
        self.desired_state: str | None = row["desired_state"] if "desired_state" in row.keys() else None
        # Máquina que hospeda este aparelho. Nulo = esta (o worker local).
        self.worker_id: str | None = row["worker_id"] if "worker_id" in row.keys() else None
        # Verbos que o worker declarou saber executar neste aparelho. Preenchido quando o worker conecta; é o que
        # faz um aparelho de outra máquina ganhar ciclo de vida de verdade.
        self.worker_verbs: list[str] | None = None
        # Captura na origem (`observe_local`): o `CapturaNaOrigem` do registro de workers, ligado quando o worker
        # que hospeda este aparelho conecta (`bind_worker_captura`). Ligado não é usado: a cada captura se pergunta
        # se o canal VIVO negociou a feature — sem isso, a imagem sai pelo ADB do túnel, como sempre.
        self.captura_remota: Any = None
        # Capacidades DECLARADAS deste aparelho: o que ele é, não só que verbo aceita (migração 019). Nulo = não
        # se sabe — e o que não se sabe nunca vira recusa. São lidas do aparelho por ADB quando ele entra no ar,
        # declaradas pelo worker que o hospeda, ou deduzidas do AVD desta máquina, nessa ordem de confiança.
        # Identidade FÍSICA do aparelho por trás deste id lógico (migração 020). Nula = nunca se observou — e o
        # que não se sabe nunca invalida nada. Ver `conferir_identidade`.
        self.physical_id: str | None = _col(row, "physical_id")
        self.device_kind: str | None = _col(row, "device_kind")
        self.system_image: str | None = _col(row, "system_image")
        self.api_level: int | None = _col(row, "api_level")
        self.abis: list[str] = list(loads(_col(row, "abis"), []) or [])
        bruto = _col(row, "play_store")
        self.play_store: bool | None = None if bruto is None else bool(bruto)
        self.executor = DeviceExecutor(self.id)
        # Trilha PRÓPRIA das sondas (saúde, pressão, internet): leituras curtas e só leitura. Na mesma thread da
        # captura e da automação elas nunca tinham vez — android-09, 25/09/2026: captura estourando 25 s em série
        # e a sessão do Appium falhando mantinham a fila cheia, e o aparelho congelado seguiu `online` 8+ min sem
        # uma sonda sequer. Captura é observação; saúde é controle.
        self.sonda = DeviceExecutor(f"{self.id}-sonda")
        #: Degrau da escada de prontidão (`models.ReadinessInfo`).
        self.readiness_phase: str = "not_running"
        self.readiness_detail: str = ""
        self.readiness_since: str | None = None
        self.adb = Adb(tools, self.serial)
        self.session = AppiumSession(cfg.file.appium, self.serial, self.ports.system, self.ports.mjpeg,
                                     self.ports.chromedriver)
        self.io: DeviceIO = io_factory(self) if io_factory else AndroidDeviceIO(self.adb, self.session)
        self.automation = AutomationInfo()
        self.automation_retry_mono: float = time.monotonic()
        # Falhas SEGUIDAS ao abrir a sessão de automação. Sem este contador o central repetia a mesma tentativa a
        # cada 90 s para sempre — 147 a 160 eventos por aparelho em 4 h, medido em 21/09/2026 — sem que o estado
        # do aparelho saísse de `online` nem aparecesse um `attention` para alguém agir.
        self.automation_failures = 0
        self.automation_last_error: str | None = None
        # Saúde do CONVIDADO (o Android de dentro), separada do transporte (o adb). Ver `conferir_saude`.
        self.health_checked_mono: float = 0.0
        # Internet DENTRO do convidado (`devices/conectividade.py`). Volta a `unknown` a cada entrada no ar: boot,
        # acordar e reset nunca herdam o resultado anterior.
        self.connectivity: ConnectivityInfo = ConnectivityInfo()
        self.connectivity_mono: float = 0.0
        # Relógio do convidado (`conferir_relogio_do_convidado`): `unknown` | `ok` | `incerto` (o acerto estourou:
        # pode cair atrasado) | `fora` (o acerto não convergiu) | `adiado` (fora da tolerância com a IA operando o
        # aparelho: o acerto espera `ai_end`). Em memória: volta a `unknown` a cada entrada no ar.
        self.clock_state: str = "unknown"
        self.clock_skew_s: int | None = None
        self.clock_checked_mono: float = 0.0
        #: Desde quando o adb responde `device` num aparelho EXTERNO cujo boot ainda não concluiu (0 = não está nisso).
        self.boot_externo_desde: float = 0.0
        self.health_failures = 0
        self.pressure_strikes = 0
        #: Ticks de CPU (total, irq+softirq) da última sonda de saúde, para medir a fração de interrupção entre duas.
        self.cpu_ticks: tuple[float, float] | None = None
        self.irq_strikes = 0
        self.irq_frac: float | None = None
        self.irq_restart_mono: float = 0.0
        # Remediação automática (`restart` pelo worker) depois de degradar: teto e intervalo, para o central nunca
        # virar um laço de reinício em cima de um aparelho que não volta.
        self.restart_attempts = 0
        self.restart_backoff_until: float = 0.0
        #: Pausa do reparo AUTOMÁTICO deste aparelho (`PausaDeReparo`), sempre com prazo. Gravada em `settings`
        #: (`repair_pauses`) e relida em `DeviceManager._runtime` (25.13): antes ficava só aqui, e o restart do backend no
        #: meio de um experimento a apagava antes do prazo (K-082).
        self.repair_pause: PausaDeReparo | None = None
        # controle
        self.control = ControlOwner.none
        self.control_since: str | None = None
        self.lease_id: str | None = None
        self.lease_expires_mono: float = 0
        self.takeover_requested = False
        self.pending_lease_id: str | None = None
        #: 14.13: o que o log já conta deste aparelho, a assinatura material do último `instance.updated` e o controle
        #: do último `control.changed` ou `instance.updated` (`devices/publicacao.py`). None = nada ainda: a primeira
        #: publicação persiste.
        self.dto_publicado: str | None = None
        self.controle_anunciado: tuple[str, bool] | None = None
        #: 29.66: o último DTO que foi ao log (o comparado, ver `publish`), lido do banco uma vez depois do reinício.
        self.dto_persistido: dict[str, object] | None = None
        self.dto_persistido_lido = False
        # frames
        self.frame: Frame | None = None
        self.frame_seq = 0
        # Saúde da captura (ver `devices/stream.py`): falhas SEGUIDAS e a última mensagem. Zera a cada frame novo.
        self.capture_failures = 0
        self.capture_error: str | None = None
        self.capture_error_at: str | None = None
        self.recent_frames: OrderedDict[str, tuple[float, int, int]] = OrderedDict()
        self.focus_until_mono: float = 0
        #: Foco vindo do `watch` de alguma conexão (contrato C2). Quem sabe é o registro de interesse do
        #: gerenciador, que se instala aqui; a grade NÃO entra, para não manter o aparelho acordado.
        self.foco_por_interesse: Callable[[], bool] = lambda: False
        self.capture_now = asyncio.Event()
        #: Geração do runtime: sobe a cada entrada no ar (boot, acordar, readoção). O que se sabe da TELA (a
        #: classificação abaixo) só vale dentro da mesma geração — a tela de senha de antes do reinício não diz nada.
        self.geracao = 0
        #: `(geração, sensível, monotônico)` da ÚLTIMA hierarquia lida deste aparelho, por qualquer caminho (`arvore()`
        #: é o funil único). É o que a captura de prévia usa para não mostrar tela sensível sem pagar uma hierarquia
        #: por frame.
        self.classificacao: tuple[int, bool, float] | None = None
        #: Tamanho da tela por orientação (`portrait`/`landscape`), visto nos frames DESTA geração: é de onde a
        #: observação sem imagem tira largura e altura (contrato C1) — nunca de outro aparelho.
        self.dimensoes: dict[str, tuple[int, int]] = {}
        self.dimensoes_geracao = 0
        # diversos
        self.attention: str | None = None
        self.current: InstanceCurrent | None = None
        self.resources: InstanceResources | None = None
        #: Renderizador do emulador (29.11). `renderer_configured` é o `gpu_mode` PEDIDO: o daqui (`android_de`) ou o
        #: que o agente da outra máquina declarou. `renderer` é o que o emulador SELECIONOU, lido do log a cada entrada
        #: no ar (ou declarado na batida do worker); `renderer_log` diz onde esse log mora. Em memória de propósito:
        #: tudo é rederivado da configuração, do log e da batida, e nada disto descreve um processo que já morreu.
        self.renderer_configured: str | None = None
        self.renderer: emu.Renderizador | None = None
        self.renderer_log: str | None = None
        self.wipe_next_boot = False
        # rodízio (ligar sob demanda / ceder vaga)
        self.online_since_mono: float = time.monotonic()
        self.last_activity_mono: float = time.monotonic()
        self.start_backoff_until: float = 0.0
        self.start_refusals = 0
        #: Só o caminho remoto usa: um `stop`/`hibernate` que o worker não aceitou abrir não pode ser repedido a
        #: cada tick — seria uma recusa por segundo no histórico daquele aparelho.
        self.stop_backoff_until: float = 0.0
        self.app_versions: dict[str, str] = {}
        self.ui_variant: str | None = None            # idioma + faixa de densidade: parte da identidade da receita
        self.external_checked_mono = 0.0
        self.boot_log_offset = 0
        # 29.55 (c): a última subida parou no diálogo de relatório de falha. Enquanto valer, o reparo automático não
        # sobe degrau neste aparelho (a escada chegou ao terceiro degrau num aparelho com conta, 03/10 19:00Z); cai na
        # próxima subida, de pessoa ou do rodízio.
        self.bloqueio_de_crash = False
        self.snapshot_failures = 0
        self.snapshot_unsupported = False           # o emulador recusou o snapshot deste AVD 2× seguidas: para de salvar
        # 1ª sessão depois de criar/resetar o AVD: o hardware dessa sessão (initPath, partição de dados recém-criada)
        # difere do das seguintes, então um snapshot tirado nela NUNCA carrega (medido) — nessa sessão só desliga.
        self.fresh_data = False
        self.snapshot_valid = bool(row["snapshot_valid"]) if "snapshot_valid" in row.keys() else False
        self.snapshot_hw: str | None = row["snapshot_hw"] if "snapshot_hw" in row.keys() else None
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.op_lock = asyncio.Lock()      # operações de ciclo de vida (start/stop/reset) não se sobrepõem
        self.spawn_lock = threading.Lock()

    @property
    def focused(self) -> bool:
        """Alguém está com ESTE aparelho aberto em foco (`focus` antigo ou `watch.focus`). É o que o rodízio e a
        hibernação respeitam; interesse de grade fica de fora de propósito (contrato C2)."""
        return time.monotonic() < self.focus_until_mono or self.foco_por_interesse()

    @property
    def renderizador_pedido(self) -> str | None:
        """O `gpu_mode` pedido, pelo nome canônico (`swiftshader_indirect` → `swiftshader`)."""
        return emu.normalizar_renderizador(self.renderer_configured)

    @property
    def renderizador_efetivo(self) -> str | None:
        """O renderizador que vale para decidir se um app roda aqui (`devices/compatibilidade.py`): o SELECIONADO
        quando conhecido; senão o PEDIDO. `auto` sem selecionado é "não se sabe": é pedir "o que houver"."""
        if self.renderer is not None:
            return emu.normalizar_renderizador(self.renderer.gles)
        pedido = self.renderizador_pedido
        return None if pedido == "auto" else pedido


def janela_completa(pacote: str | None, atividade: str | None) -> str | None:
    """`pacote/atividade` completo e minúsculo, como o `app.yaml` declara: o `dumpsys` às vezes abrevia a atividade
    (`.challenge.X` = `<pacote>.challenge.X`)."""
    if not pacote or not atividade:
        return None
    nome = f"{pacote}{atividade}" if atividade.startswith(".") else atividade
    return f"{pacote}/{nome}".lower()


class DeviceManager:
    def __init__(self, cfg: Config, db: Database, bus: EventBus, tools: SdkTools, appium: AppiumServer,
                 *, settings_getter: Callable[[], Any], io_factory: Callable[[DeviceRuntime], DeviceIO] | None = None,
                 emulator: EmulatorBackend | None = None):
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.tools = tools
        self.avd = AvdManager(cfg, tools)
        # Achado #165: a MÁQUINA entra por aqui, não por `if self.io_factory` no meio da decisão. É o que permite
        # a guarda de capacidade ser exercitada pelo mesmo código em teste e em produção.
        self.emulator: EmulatorBackend = emulator or RealEmulatorBackend()
        self.appium = appium
        self.get_settings = settings_getter
        self.io_factory = io_factory
        self.devices: dict[str, DeviceRuntime] = {}
        #: Interesse em prévia por conexão do painel (contrato C2) e o relógio dele — injetável no teste, para provar
        #: TTL vencido movendo o relógio em vez de dormir.
        self._interesses: dict[str, _Interesse] = {}
        self.relogio: Callable[[], float] = time.monotonic
        #: Regras de tela sensível declaradas pelo parque (`config.yaml: sensitive_screens`), compiladas uma vez.
        #: Ficam aqui porque TODA leitura de hierarquia passa por este gerenciador — se ficassem em cada chamador,
        #: a próxima leitura nova nasceria sem elas, calada. Ver `arvore()`.
        self.regras_sensiveis: tuple[RegraDeTelaSensivel, ...] = tuple(
            RegraDeTelaSensivel(package=r.package, resource_ids=tuple(r.resource_ids), texts=tuple(r.texts),
                                why=r.why)
            for r in cfg.file.sensitive_screens)
        self.boot_limiter = Limiter(cfg.file.limits.boot_parallelism)
        #: RAM reservada por boot ADMITIDO nesta máquina (instance_id → MB), gravada por `_recusa_por_capacidade`
        #: no instante em que admite — antes de qualquer `await`. Sem ela, com `boot_parallelism` 2, dois `_boot`
        #: entravam juntos na guarda, nenhum tinha PID ainda, nenhum via o outro, e os dois passavam numa RAM que
        #: comportava um só. Sai no fim do `_boot` (`_soltar_reserva`).
        self._reservas: dict[str, int] = {}
        #: Até quando cada reserva pode valer (relógio monotônico): o prazo do boot, contado da admissão — o mesmo
        #: instante em que um boot bem-sucedido já a teria soltado. É o que vence a órfã (igual ao agente).
        self._reservas_ate: dict[str, float] = {}
        #: Reservas de boot que terminou sem prova de que o emulador morreu (cancelado ou fora do prazo com o PID
        #: vivo): o processo segue alocando RAM sem estar contado em lugar nenhum. Valor = o MAIOR RSS já visto
        #: daquele processo: o laço de métricas zera `resources` de quem não está `online`/`booting` (o órfão fica
        #: em `error`), mas a RAM que ele já tem continua fora da RAM livre — descontá-la de novo seria contar duas
        #: vezes. Ficam até se saber (PID sumiu, processo morreu, aparelho online) ou até o prazo vencer.
        self._reservas_orfas: dict[str, float] = {}
        self.on_device_free: Callable[[], None] = lambda: None   # o scheduler se inscreve aqui
        #: O controle manual voltou para o aparelho (devolvido ou expirado). Quem sabe se o perfil vinculado
        #: estava esperando uma pessoa (desafio, conta errada) é a camada social, então ela se inscreve aqui —
        #: é o que cumpre a promessa do texto de desafio (`textos.desafio` do `sessao.yaml`: "devolva o controle: a verificação recomeça sozinha"),
        #: hoje só palavra (achado #106).
        self.on_control_released: Callable[[DeviceRuntime], None] = lambda rt: None
        #: Modo treinamento (item 13.1): recebe cada entrada manual já executada, com a árvore da tela de ANTES.
        self.on_training_input: Callable[[DeviceRuntime, dict[str, Any], Any], None] | None = None
        #: Aprendizado (ADR-054, A2): uma pessoa pediu o aparelho com a IA no meio de uma etapa. Só os ids — nem
        #: árvore, nem texto, nem coordenada; as entradas manuais seguem fora (`aprendizado.takeover_gravar: false`).
        #: Injetado pelo AppState; no-op por padrão.
        self.costura_de_controle: CosturaDeControle = SEM_COSTURAS_DE_GESTO
        #: Os dados do aparelho foram apagados (reset, wipe). Quem sabe o que estava instalado é a camada de
        #: releases, então ela se inscreve aqui — senão o central continuaria afirmando "app pronto" num
        #: aparelho vazio, e "Distribuir" responderia "já está nesta versão".
        #: Vale também para a troca do aparelho físico por trás do id (`conferir_identidade`): o que se sabia do disco
        #: deixa de valer, mas NADA foi apagado — por isso este gancho não pode afirmar "disco apagado".
        self.on_device_wiped: Callable[[str, str], None] = lambda instance_id, motivo: None
        #: O disco do aparelho FOI apagado de fato: o emulador desta máquina subiu com `-wipe-data`, ou o agente da
        #: outra máquina respondeu `succeeded` a um `reset`. Só aqui a conta travada deixa de estar logada (ADR-055).
        #: Separado de `on_device_wiped` porque a troca de identidade física passava pelo mesmo gancho e resolvia o
        #: marcador com "dados do aparelho apagados" sem reset nem pessoa (revisão do pacote quarentena, 29/09).
        self.on_disk_erased: Callable[[str, str], None] = lambda instance_id, motivo: None
        #: O aparelho degradou e o estado DESEJADO dele é `online`: alguém precisa tentar reiniciá-lo. Quem sabe
        #: abrir um comando rastreável é a camada da API, então ela se inscreve aqui — o gerenciador não conhece
        #: comandos. Sem isto, o convidado morto ficava morto até alguém olhar o painel.
        self.on_remediation_needed: Callable[[str, str], None] = lambda instance_id, motivo: None
        #: Reinício a frio por SAÚDE do convidado ocioso (interrupções acumuladas). Só `restart`, nunca a escada de
        #: reparo: a escada chega a `reset`, que apagaria a conta real logada no aparelho. Devolve o id do comando.
        self.on_health_restart: Callable[[str, str], str | None] = lambda instance_id, motivo: None
        #: A conta travada logada no aparelho (o @ do marcador aberto, migração 054), ou `None`. Injetado pelo
        #: AppState, que conhece o domínio social. Aparelho com conta travada fica fora do reinício de saúde e o
        #: cartão diz por quê (ADR-055).
        self.conta_travada_em: Callable[[str], str | None] = lambda instance_id: None
        #: O RODÍZIO precisa ligar/desligar um aparelho que mora em OUTRA máquina. O gerenciador não fala com o
        #: agente (quem despacha é a camada da API, pelo mesmo caminho rastreável do painel), então ela se
        #: inscreve aqui. Devolve o id do comando aberto, ou `None` quando não deu para abrir (verbo recusado,
        #: worker em manutenção, comando já em voo naquele aparelho). Sem isto, `request_start`/`request_stop`
        #: só sabiam operar emulador desta máquina — e o parque remoto ficava ligado para sempre.
        self.on_lifecycle_request: Callable[[str, str, str], str | None] = \
            lambda instance_id, verb, motivo: None
        #: Aquela máquina salva snapshot? Quem sabe é o registro de workers (declaração do agente no `Hello`), e
        #: é o que separa "hibernar" de "desligar" quando o rodízio cede a vaga de um aparelho remoto.
        self.worker_hibernates: Callable[[str], bool] = lambda worker_id: False
        #: O aparelho entrou no ar. Quem guarda afirmação com validade sobre o disco dele (estado do app) se
        #: inscreve aqui para reobservar o que ficou velho — "lido do aparelho, nunca presumido" só vale se
        #: alguém relê.
        self.on_device_online: Callable[[str], None] = lambda instance_id: None
        #: Estado do túnel do worker que hospeda este aparelho ('up' | 'down' | None quando não se sabe ou o
        #: aparelho não é remoto) — achado #179. Quem sabe é o `AppState` (sonda as portas locais do túnel), e
        #: se inscreve aqui para o motivo de "sem ADB" distinguir túnel fora de aparelho desligado no worker.
        self.transport_state_of: Callable[[str], str | None] = lambda worker_id: None
        #: O que o WORKER vê do processo daquele aparelho na máquina dele — achado #61. Devolve
        #: `(nome_do_worker, conectado, estado_do_processo | None)`, ou `None` quando o worker nem existe.
        #: "Desligado de propósito lá" e "sem conexão ADB daqui" são realidades diferentes, e a frase precisa
        #: dizer qual das duas é; quem sabe é o `AppState`, que tem o registro de workers.
        self.worker_process_of: Callable[[str, str], tuple[str, bool, str | None] | None] = \
            lambda worker_id, instance_id: None
        self._bg: list[asyncio.Task[Any]] = []
        self.last_metrics: Metrics | None = None
        self.boots: list[tuple[str, str]] = []      # (instância, warm|cold) — só no modo de teste

    # ------------------------------------------------------------------ bootstrap
    def _runtime(self, row: Any) -> DeviceRuntime:
        rt = DeviceRuntime(self.cfg, self.tools, row, self.io_factory)
        rt.foco_por_interesse = functools.partial(self._foco_por_interesse, rt)
        rt.repair_pause = self._pausa_gravada(rt.id)
        return rt

    def seed(self) -> None:
        """Carrega os aparelhos DESTE backend — e só eles.

        O filtro por hospedeiro (`instances.hosted_by`, migração 027) é o item 5.1: antes daqui, um segundo
        backend no mesmo banco carregava TODO `cfg.instance_ids()` como se fosse seu, e passava a gravar na mesma
        linha de `instances` (pid do emulador, snapshot, sessão do Appium, estado desejado) do aparelho que quem
        hospeda estava operando. Com o rodízio ligado ele ainda CRIAVA no próprio disco um AVD novo com o mesmo id
        lógico — um aparelho vazio, sem a sessão do perfil.

        Quem não hospeda (`ROLE=api`) não carimba nada: carimbar roubaria os aparelhos do scheduler da mesma
        máquina, e depois ninguém os ligaria.
        """
        c = self.cfg.file.instances
        meu = self.cfg.owner_id
        hospeda = self.cfg.hospeda_aparelhos
        with self.db.tx():
            for i, iid in enumerate(self.cfg.instance_ids(), start=1):
                exists = self.db.one("SELECT id FROM instances WHERE id=?", (iid,))
                if exists:
                    continue
                self.db.execute(
                    "INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
                    " app_id, account_label, hosted_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (iid, i, iid, c.base_console_port + 2 * (i - 1), c.base_system_port + (i - 1),
                     c.base_mjpeg_port + (i - 1), c.base_chromedriver_port + (i - 1),
                     None if iid == self.cfg.store_id else
                     (c.default_app if self.db.one("SELECT id FROM apps WHERE id=?", (c.default_app,)) else None),
                     c.accounts.get(iid), meu if hospeda else None))
            if hospeda:
                # Adoção do que já existia antes da coluna: o primeiro backend a subir assume o que está na
                # configuração DELE e ainda não tem dono. Nunca por cima de outro (`hosted_by IS NULL`) — quem
                # hospeda um aparelho não perde a posse dele porque um segundo backend listou o mesmo id.
                for iid in self.cfg.instance_ids():
                    self.db.execute("UPDATE instances SET hosted_by=? WHERE id=? AND hosted_by IS NULL", (meu, iid))
            if self.cfg.store_id:
                # `seed` só insere o que falta: uma instância que JÁ existia e virou loja ainda carregaria o app e o
                # rótulo de conta de quando era aparelho de tarefa. A loja não opera app nenhum.
                self.db.execute("UPDATE instances SET app_id=NULL, account_label=NULL WHERE id=?"
                                " AND (app_id IS NOT NULL OR account_label IS NOT NULL)", (self.cfg.store_id,))
        for row in self.db.query("SELECT * FROM instances ORDER BY idx"):
            # Aposentada (migração 050): a linha fica pelo histórico, o aparelho não. Antes do teste de origem de
            # propósito — uma instância do YAML nunca se aposenta, mas se um dia a linha dela tiver `retired_at`,
            # o arranque não pode ressuscitá-la em silêncio.
            if _col(row, "retired_at"):
                continue
            # Instância DINÂMICA (migração 024) não está em `cfg.instance_ids()` — ela nasceu de um aparelho
            # anunciado por um worker, e é justamente o ponto de não precisar editar `config.yaml`.
            if not (row["id"] in self.cfg.instance_ids() or _col(row, "origin") == "dynamic"):
                continue
            dono = _col(row, "hosted_by")
            if dono is not None and dono != meu:
                # Está na minha configuração, mas quem tem o emulador é outro backend. IGNORAR é o comportamento
                # certo — nunca bloquear o objetivo alheio, nunca criar um AVD com o mesmo id aqui.
                log.info("instância %s é hospedada por %s: ignorada neste backend", row["id"], dono)
                continue
            self.devices[row["id"]] = self._runtime(row)
        for rt in self.devices.values():
            # O que já dá para saber com o parque inteiro DESLIGADO — que é o estado de quem vai agendar uma
            # execução e precisa da recusa explicada antes, não no meio.
            # `publicar=False`: `seed()` roda no construtor do `AppState`, antes de existir laço de eventos
            # ao qual o barramento esteja preso — um evento aqui morreria sem ninguém para entregá-lo.
            self.capacidades_do_avd_local(rt, publicar=False)

    async def start(self) -> None:
        await asyncio.gather(*(self._adopt(rt) for rt in self.devices.values()))
        self._bg.append(asyncio.create_task(self._monitor_loop(), name="device-monitor"))
        self._bg.append(asyncio.create_task(self._metrics_loop(), name="metrics"))

    async def shutdown(self) -> None:
        """Encerra as tarefas do backend. Emuladores continuam rodando (preservam apps e sessões)."""
        for t in self._bg:
            t.cancel()
        for rt in self.devices.values():          # primeiro para TODAS as tarefas; só depois fecha sessões
            for t in rt.tasks.values():
                t.cancel()

        async def _close(rt: DeviceRuntime) -> None:
            try:
                await asyncio.wait_for(asyncio.to_thread(rt.session.close), timeout=20)
            except Exception:  # noqa: BLE001 - sessão presa não pode impedir o encerramento
                log.warning("%s: a sessão de automação não fechou a tempo", rt.id)
            rt.executor.shutdown()
            rt.sonda.shutdown()

        await asyncio.gather(*(_close(rt) for rt in self.devices.values()))

    def _invalidate_session(self, rt: DeviceRuntime, motivo: str) -> None:
        """Sessão do Instagram é CACHE do que se observou: qualquer coisa que mexa no disco do aparelho a invalida.

        Fica aqui, e não no domínio social, porque quem sabe que o disco mudou é o gerenciador de aparelhos.
        `on_session_invalidated` é preenchido pelo AppState; sem ele, nada acontece.
        """
        hook = getattr(self, "on_session_invalidated", None)
        if hook is None:
            return
        try:
            hook(rt.id, motivo)
        except Exception:  # noqa: BLE001 - invalidar sessão nunca pode derrubar o ciclo do aparelho
            log.exception("%s: falha ao invalidar a sessão", rt.id)

    def get(self, instance_id: str) -> DeviceRuntime:
        rt = self.devices.get(instance_id)
        if rt is None:
            raise KeyError(instance_id)
        return rt

    # ------------------------------------------------------------------ DTO/eventos
    def dto(self, rt: DeviceRuntime) -> InstanceDTO:
        # A conta travada logada (054) vem na MESMA consulta: o DTO é montado a cada publicação do aparelho, e uma
        # segunda ida ao banco por cartão dobraria o custo de um caminho quente.
        row = self.db.one("SELECT app_id, account_label, account_evidence, account_evidence_ts,"
                          " (SELECT d.handle FROM device_locked_accounts d WHERE d.instance_id = instances.id"
                          " AND d.resolved_at IS NULL ORDER BY d.id LIMIT 1) AS locked_account"
                          " FROM instances WHERE id=?", (rt.id,))
        conta_vista = evidencia_legivel(row["account_label"], row["account_evidence"])
        s = self.get_settings()
        frame = None
        # O prazo do frame acompanha o ritmo da captura: foco (inclusive controle manual) captura mais rápido.
        foco = rt.focused or rt.control == ControlOwner.user
        interval = s.capture_focus_interval_s if foco else s.capture_grid_interval_s
        max_age = max(s.frame_max_age_ms / 1000, interval * 2.5)
        pausada = self._previa_pausada(rt)
        # Com a IA no controle a prévia não captura por conta própria (`_volta_da_previa`): o frame vem de cada
        # observação da IA, e o prazo passa a ser o do ciclo dela. Sem isto o Foco ficava cinza, com "Desatualizado",
        # durante boa parte de toda execução — e o texto culpava a captura por um estado que é de propósito.
        ia = self.ia_no_controle(rt) and not pausada
        if ia:
            max_age = max(max_age, FRAME_MAX_AGE_IA_S)
        if rt.frame is not None:
            frame = rt.frame.info.model_copy(update={"stale": (time.monotonic() - rt.frame.mono) > max_age})
        # `worker_verbs is None` com `worker_id` preenchido = o worker desconectou (`bind_worker(None)`).
        stream = stream_status(
            device_state=rt.state.value, worker_bound=bool(rt.worker_id), worker_connected=rt.worker_verbs is not None,
            frame_ts=rt.frame.info.ts if rt.frame else None,
            frame_age_s=(time.monotonic() - rt.frame.mono) if rt.frame else None, max_age_s=max_age,
            capture_failures=rt.capture_failures, last_error=rt.capture_error, last_error_at=rt.capture_error_at,
            # Sem ninguém olhando não há captura: frame velho aqui é economia (`paused`), não atraso (`stale`).
            paused=pausada, ia_no_controle=ia)
        return InstanceDTO(
            id=rt.id, index=rt.index, avd_name=rt.avd_name, serial=rt.serial, console_port=rt.console_port,
            ports=rt.ports, state=rt.state, state_detail=rt.state_detail, pid=rt.pid, boot_seconds=rt.boot_seconds,
            # A evidência sai legível (I2 do deploy 9): prova por seletor nunca chega crua ao painel, e a que não observa
            # conta nenhuma (só o seletor, sem o rótulo) vale como "não observada", com o carimbo junto.
            app_id=row["app_id"], account_label=row["account_label"], account_evidence=conta_vista,
            account_evidence_ts=row["account_evidence_ts"] if conta_vista else None, locked_account=row["locked_account"],
            control=rt.control, control_since=rt.control_since,
            control_pending=rt.takeover_requested, automation=rt.automation, frame=frame, stream=stream,
            # Fora do ar a última sonda é história: "healthy" num aparelho hibernado seria afirmação sem prova.
            readiness=ReadinessInfo(phase=rt.readiness_phase, detail=rt.readiness_detail,  # type: ignore[arg-type]
                                    since=rt.readiness_since),
            connectivity=rt.connectivity if rt.state == InstanceState.online else ConnectivityInfo(
                detail=f"Aparelho em '{rt.state.value}': a internet só é verificada com ele no ar."),
            current=rt.current,
            # O aviso do renderizador é DERIVADO, e não gravado em `rt.attention`: aquele campo é de quem chegar por
            # último (o despacho escreve "Bloqueado: …" e depois o zera; o controle manual também), e um fato que vale
            # enquanto o processo do emulador viver sumiria no primeiro desses. Assim ele cede a vez a qualquer outro
            # assunto do cartão e volta sozinho.
            attention=rt.attention or self._aviso_do_renderizador(rt), repair_pause=self.pausa_dto(rt),
            resources=rt.resources,
            renderer=self._renderizador_dto(rt),
            kind="store" if rt.store else "external" if rt.external else "emulator", worker_id=rt.worker_id,
            # O painel precisa saber o que este aparelho aceita ANTES de oferecer o botão. Sem isto, o cartão de um
            # aparelho de outra máquina oferecia Parar, Hibernar e "Resetar dados…" com a mesma aparência de um
            # emulador local — e nenhuma dessas ações acontecia.
            supported_verbs=sorted(verbos_suportados(rt)),
            device_kind=rt.device_kind, system_image=rt.system_image, api_level=rt.api_level,
            abis=list(rt.abis or []), play_store=rt.play_store,
            inventory_state=rt.inventory_state, inventory_detail=rt.inventory_detail, origin=rt.origin,
            tunnel_port=rt.tunnel_port, remote_adb_port=rt.remote_adb_port)

    def list_dtos(self) -> list[InstanceDTO]:
        return [self.dto(rt) for rt in self.devices.values()]

    def publish(self, rt: DeviceRuntime, message: str | None = None, level: str = "info") -> None:
        # 14.13: fato vai ao log (`instance.updated`); telemetria e troca de controle já anunciada vão ao painel como
        # `instance.progress`, efêmero. A regra e a medida estão em `devices/publicacao.py`.
        dto = self.dto(rt).model_dump(mode="json")
        comparado = self._dto_para_comparar(rt, dto)
        assinatura = assinatura_material(comparado)
        controle = (rt.control.value, rt.takeover_requested)
        # Mensagem explícita é registro ("inventário conferido", "adotado do worker…"): vai ao log mesmo sem campo novo.
        persistir = (message is not None or level != "info" or assinatura != rt.dto_publicado
                     or controle != rt.controle_anunciado)
        self.bus.emit("instance.updated" if persistir else "instance.progress", message or f"{rt.id}: {rt.state.value}",
                      level=level, instance_id=rt.id, data={"instance": dto})
        if persistir:
            rt.dto_publicado, rt.controle_anunciado, rt.dto_persistido = assinatura, controle, comparado

    def _dto_para_comparar(self, rt: DeviceRuntime, dto: dict[str, object]) -> dict[str, object]:
        """29.66: o que muda só porque o worker ainda não falou não é fato novo.

        Medido em 04/10: 1108 de 1114 trocas de `supported_verbs`, `renderer` e `abis` caíam em horas de reinício do
        backend ou reconexão do worker. No seed, o aparelho do worker entra pelo túnel antes do `hello`: os verbos são os
        de "externo sem worker" e o renderizador é nulo; segundos depois o `hello` devolve os de sempre. Nessa janela
        (worker do aparelho conhecido e desconectado), os dois campos valem, PARA A COMPARAÇÃO, o último DTO gravado; o
        painel recebe o DTO real. Fora da janela, o renderizador com `gles`/`vulkan` ainda desconhecidos (nulos) também
        não apaga o que já se sabia: a sonda os preenche de novo depois de cada reinício.
        """
        ref = self._ultimo_dto_persistido(rt)
        if not ref:
            return dto
        comparado = dict(dto)
        if rt.worker_id and rt.worker_verbs is None:
            comparado["supported_verbs"], comparado["renderer"] = ref.get("supported_verbs"), ref.get("renderer")
        rend, rend_ref = comparado.get("renderer"), ref.get("renderer")
        if isinstance(rend, dict) and isinstance(rend_ref, dict):
            comparado["renderer"] = {**rend, **{k: rend_ref.get(k) for k in ("gles", "vulkan")
                                               if rend.get(k) is None and rend_ref.get(k) is not None}}
        return comparado

    def _ultimo_dto_persistido(self, rt: DeviceRuntime) -> dict[str, object] | None:
        if rt.dto_persistido is None and not rt.dto_persistido_lido:
            rt.dto_persistido_lido = True              # uma leitura por aparelho e processo, achando ou não
            linhas = self.db.query("SELECT data FROM events WHERE kind='instance.updated' AND instance_id=?"
                                   " ORDER BY id DESC LIMIT 1", (rt.id,))
            if linhas and linhas[0]["data"]:
                try:
                    instancia = loads(linhas[0]["data"], {}).get("instance")
                except (ValueError, AttributeError):
                    instancia = None
                if isinstance(instancia, dict):
                    rt.dto_persistido = instancia
        return rt.dto_persistido

    def _set_state(self, rt: DeviceRuntime, state: InstanceState, detail: str | None = None,
                   *, level: str = "info", attention: str | None = None) -> None:
        if state == InstanceState.online and rt.state != InstanceState.online:
            rt.app_versions.clear()
            rt.ui_variant = None
            rt.online_since_mono = rt.last_activity_mono = time.monotonic()
            rt.start_refusals, rt.start_backoff_until = 0, 0.0
            rt.bloqueio_de_crash = False     # 29.55: no ar de novo, a marca do diálogo de relatório de falha cai
            # Aparelho que ENTRA no ar começa de novo. Sem isto, um emulador degradado por 3 falhas de sessão
            # voltava do reinício com o contador em 3, e a primeira recusa pós-boot — que é comum, e por isso o
            # executor tenta três vezes com 8 s de intervalo — o degradava de novo na hora.
            rt.automation_failures = rt.health_failures = 0
            rt.automation_last_error = None
            rt.capture_failures, rt.capture_error, rt.capture_error_at = 0, None, None
            rt.connectivity, rt.connectivity_mono = ConnectivityInfo(), 0.0
            # A conferência de ENTRADA do relógio é da arrumação (`_arrumar_depois_de_entrar`); a periódica conta
            # a partir daqui, para as duas não correrem juntas.
            rt.clock_state, rt.clock_skew_s, rt.clock_checked_mono = "unknown", None, time.monotonic()
            self._nova_geracao(rt)
        elif rt.state == InstanceState.online and state != InstanceState.online:
            # Também na SAÍDA do ar: a readoção (`_adopt`, inclusive a imediata depois de um `start`/`wake` remoto)
            # põe o aparelho `online` sem passar por aqui, e herdaria o que se sabia da tela do Android de antes.
            self._nova_geracao(rt)
            # O renderizador selecionado era o DAQUELE processo. O pedido fica: é configuração, não observação.
            rt.renderer = None
        if state == InstanceState.online:
            # `ready` herda o detalhe da escada que acabou de passar (`android_responsive`: "servicemanager,
            # system_server e display responderam"). Era sobrescrito pelo texto do PR #5 ("o framework respondeu à
            # sonda") — visto em produção no android-04 e no android-09. Sem escada nesta passagem, não se afirma sonda.
            provado = rt.readiness_phase in ("android_responsive", "ready") and rt.readiness_detail
            self._prontidao(rt, "ready",
                            rt.readiness_detail if provado else "no ar sem rodada de prontidão nesta passagem")
        elif state in (InstanceState.stopped, InstanceState.hibernated, InstanceState.absent):
            self._prontidao(rt, "not_running", "")
        rt.state, rt.state_detail = state, detail
        if attention is not None or state in (InstanceState.online, InstanceState.stopped, InstanceState.hibernated):
            rt.attention = attention
        self.publish(rt, f"{rt.id}: {state.value}" + (f" — {detail}" if detail else ""), level)

    @staticmethod
    def _nova_geracao(rt: DeviceRuntime) -> None:
        """O que se sabia da tela (sensível ou não, e o tamanho) era do Android de antes."""
        rt.geracao += 1
        rt.classificacao = None
        rt.dimensoes = {}

    # ------------------------------------------------------------------ saúde do convidado
    @staticmethod
    def _prontidao(rt: DeviceRuntime, fase: str, detalhe: str) -> None:
        if rt.readiness_phase != fase or rt.readiness_detail != detalhe:
            rt.readiness_phase, rt.readiness_detail, rt.readiness_since = fase, detalhe, now_iso()

    async def _sondar_prontidao(self, rt: DeviceRuntime, restante_s: float | None = None) -> prontidao.Prontidao:
        """Uma rodada da escada de prontidão (`devices/prontidao.py`) — a MESMA definição que o worker usa para
        fechar `start`/`wake`. Roda na trilha de sonda: a fila da captura não a atrasa, e ela não atrasa a captura.
        Portão de entrada no ar; a sonda periódica pós-`ready` continua sendo `conferir_saude`."""
        # Teto externo da thread: a rodada nunca passa do restante (+ os pisos de 1 s de cada sonda) nem do pior caso.
        teto = prontidao.prazo_da_rodada() if restante_s is None else min(prontidao.prazo_da_rodada(), restante_s + 3.0)
        try:
            return await rt.sonda.run(functools.partial(prontidao.avaliar, rt.io, restante_s=restante_s, rotulo=rt.id),
                                      timeout=teto + 5.0, label="prontidão")
        except (DriverError, AdbError) as exc:
            return prontidao.Prontidao("mudo", None, str(exc)[:160])

    async def _preparar_e_revalidar(self, rt: DeviceRuntime, anterior: prontidao.Prontidao,
                                    restante_s: float | None = None) -> prontidao.Prontidao:
        """Roda o `prepare_for_automation` e devolve a prontidão que VALE depois dele.

        Contrato temporal: pronto não é "os três subsistemas responderam em algum momento"; é "responderam DEPOIS do
        último sinal de não-resposta", nesta tentativa (limitação entre tentativas: `devices/prontidao.py`). Um
        preparo que ESTOURA o prazo foi o que o worker viu no wake de 25/09/2026, 0,3 s antes de fechar `succeeded`
        — e o efeito dele no aparelho fica incerto (`AdbTimeout`, `devices/adb.py`), então ESTA tentativa não fica
        pronta (`_tentativa_incerta`).
        Erro rápido é retorno conhecido, mas `AdbError` não distingue "o comando recusou" de "o aparelho sumiu"
        (`shell()` levanta para QUALQUER saída não-zero, `device offline` inclusive): a prontidão `anterior` deixa de
        valer e uma rodada nova e completa decide.
        """
        # Orçamento GLOBAL de quem chama: o que o preparo e a espera pelo zumbi gastarem sai da rodada nova.
        fim = None if restante_s is None else time.monotonic() + restante_s
        rt.adb.apps_de_fundo = self.apps_de_fundo_de(rt)
        try:     # ajustes idempotentes (sem animações, tela ligada, sem teclado virtual sobre a tela, apps de fundo)
            ajuste = await rt.executor.run(rt.adb.prepare_for_automation, timeout=PRAZO_DO_PREPARO_S, label="prepare")
        except (DriverError, AdbError) as exc:
            return await self._revalidar_depois_de(rt, exc, "o preparo", fim)
        self._registrar_apps_de_fundo(rt, ajuste)
        return anterior

    async def _aguardar_zumbi(self, rt: DeviceRuntime, exc: DriverTimeout, oque: str,
                              fim: float | None) -> prontidao.Prontidao | None:
        """O EXECUTOR desistiu de esperar `oque`, mas a chamada pode seguir viva na thread do aparelho ("zumbi",
        `devices/executor.py`), ainda mexendo nele. Uma sonda que passasse agora seria anterior ao fim dela — o
        contrato temporal exige esperar o fim DE VERDADE (`drain`, com teto; sem matar thread). `None` = terminou;
        senão, a prontidão "mudo" que diz que não terminou a tempo."""
        self._prontidao(rt, "boot_completed", f"{oque} não respondeu e segue em execução; aguardando o fim dele")
        espera = ESPERA_DO_PREPARO_ZUMBI_S if fim is None else min(ESPERA_DO_PREPARO_ZUMBI_S,
                                                                   max(0.0, fim - time.monotonic()))
        log.warning("%s: %s estourou o prazo do executor (%s); esperando até %.0f s pela chamada terminar",
                    rt.id, oque, exc, espera)
        if await rt.executor.drain(max_wait_s=espera):
            return None
        return prontidao.Prontidao("mudo", None, f"{oque} anterior não terminou em {espera:.0f} s (chamada ainda em "
                                                 "execução no aparelho)", incerta=True)

    async def _tentativa_incerta(self, rt: DeviceRuntime, exc: AdbTimeout | DriverTimeout, oque: str,
                                 fim: float | None) -> prontidao.Prontidao:
        """`oque` (o preparo: operação COM EFEITO) estourou o prazo. `AdbTimeout` encerra só o cliente adb
        local: o efeito no aparelho segue incerto (o mesmo motivo de o desfecho de comando virar `uncertain`,
        `devices/adb.py`); `drain` depois de um `DriverTimeout` prova só que a thread local acabou. Nenhuma rodada
        nesta tentativa prova que a prontidão é POSTERIOR ao último efeito — e a forense de 25/09 mostrou uma
        recuperação parcial de 3 s logo depois do timeout do preparo, antes de o Android travar de vez. Então ESTA
        tentativa não fica pronta; quem chama tem a próxima (readoção periódica, boot a frio depois do wake,
        escada de reparo; no worker, o reconciliador do central fecha o `start` incerto se o aparelho subir).
        O zumbi local ainda é esperado (com teto) para a próxima tentativa não concorrer com ele. Todo efeito que resta
        no caminho de prontidão é idempotente (K-031): o tardio repete o que já vale. `incerta=True` faz a readoção
        espaçar a próxima tentativa no mesmo guest (`ESPERA_APOS_TENTATIVA_INCERTA_S`)."""
        if isinstance(exc, DriverTimeout):
            nao_terminou = await self._aguardar_zumbi(rt, exc, oque, fim)
            if nao_terminou is not None:
                return nao_terminou
        log.warning("%s: %s estourou o prazo (%s); efeito incerto no aparelho — esta tentativa não fica pronta",
                    rt.id, oque, exc)
        return prontidao.Prontidao("mudo", None, f"{oque} estourou o prazo e o efeito dele no aparelho é incerto; "
                                                 "esta tentativa não fica pronta", incerta=True)

    async def _revalidar_depois_de(self, rt: DeviceRuntime, exc: Exception, oque: str,
                                   fim: float | None) -> prontidao.Prontidao:
        """`oque` falhou DEPOIS de uma prontidão positiva: ela deixa de valer. Estouro de prazo → `_tentativa_incerta`
        (não fica pronta). Erro rápido é retorno conhecido, mas pode ser benigno ou `device offline` (o `AdbError`
        não distingue): decide uma rodada nova e completa, na hora. `fim` é o prazo absoluto de quem chama (`None`:
        só o teto de cada etapa)."""
        if isinstance(exc, (AdbTimeout, DriverTimeout)):
            return await self._tentativa_incerta(rt, exc, oque, fim)
        log.warning("%s: %s falhou (%s); a prontidão anterior não vale mais — nova rodada completa", rt.id, oque, exc)
        self._prontidao(rt, "boot_completed", f"{oque} falhou; confirmando a prontidão de novo")
        return await self._sondar_prontidao(rt, None if fim is None else max(0.0, fim - time.monotonic()))

    async def _esperar_prontidao(self, rt: DeviceRuntime, prazo_s: float) -> prontidao.Prontidao:
        """Rodadas até `ok`/`morto`/`erro` ou o prazo — que é o orçamento GLOBAL: rodadas não somam prazos em série.
        `erro` (falha de código na sonda) não se repete: a próxima rodada quebraria igual."""
        limite = time.monotonic() + prazo_s
        while True:
            p = await self._sondar_prontidao(rt, limite - time.monotonic())
            if p.estado != "mudo" or time.monotonic() >= limite:
                return p
            await asyncio.sleep(min(5.0, max(0.0, limite - time.monotonic())))

    def _entrando_no_ar(self, rt: DeviceRuntime, detalhe: str) -> None:
        """Processo e adb no ar, Android ainda não pronto: é `booting` com o motivo — nem `online`, nem `error`."""
        if rt.state != InstanceState.booting or rt.state_detail != detalhe:
            self._set_state(rt, InstanceState.booting, detalhe)

    async def _sondar_convidado(self, rt: DeviceRuntime) -> tuple[str, str]:
        """`ok` (framework responde), `mudo` (o adb não respondeu a tempo: não se sabe) ou `morto` (serviços do
        sistema `not found`). É o degrau ANDROID_RESPONSIVE: `service check` passa pelo binder e pelo
        `servicemanager`, então prova o framework — não só o `adbd`, que responde mesmo com o Android congelado."""
        try:
            vivo = await rt.sonda.run(rt.io.framework_alive, timeout=30, label="saúde do convidado")
        except (DriverError, AdbError) as exc:
            return "mudo", str(exc)[:160]
        return ("ok", "") if vivo else ("morto", "serviços do sistema ausentes")

    async def conferir_saude(self, rt: DeviceRuntime) -> str | None:
        """O Android DE DENTRO está vivo? `None` = sim (ou não deu para saber); a frase = o motivo do degradado.

        `online` sempre significou "o adb responde". Não é a mesma coisa: com o `system_server` morto o adb
        responde `device`, `sys.boot_completed` continua `1`, e nada — Instagram, Appium, instalação — funciona
        ali. Três dos quatro remotos ligados estavam assim em 21/09/2026, com o painel dizendo `online`,
        `attention` nulo, e o escalonador despachando tarefa para eles.

        Um adb que não responde a tempo NÃO é um convidado morto: é falta de informação, e devolve `None`. Só a
        resposta explícita `not found` degrada — quem mente por excesso de zelo continua mentindo.
        """
        estado, erro = await self._sondar_convidado(rt)
        if estado == "mudo":
            # Uma sonda muda é falta de informação. Três seguidas num aparelho `online` são o android-12 de 23/09:
            # o convidado travado por dentro, o adb levando 20–40 s, e esta função devolvendo `None` para sempre.
            rt.health_failures += 1
            log.info("%s: não foi possível conferir a saúde do convidado agora (%s) — %dª sonda muda",
                     rt.id, erro, rt.health_failures)
            if rt.health_failures < SONDAS_MUDAS_PARA_DEGRADAR:
                return None
            return (f"O aparelho não responde ao ADB há {rt.health_failures} sondas seguidas: o Android de dentro "
                    "está travado ou sobrecarregado (o processo do emulador continua vivo). Reinicie o aparelho.")
        if estado == "ok":
            rt.health_failures = 0
            await self._conferir_pressao(rt)
            return None
        rt.health_failures += 1
        return ("O Android deste aparelho está sem os serviços de sistema (o `system_server` caiu): o adb responde, "
                "mas nenhum app abre, instala ou automatiza. Reinicie o aparelho.")

    async def _conferir_pressao(self, rt: DeviceRuntime) -> None:
        """Convidado vivo mas SOB PRESSÃO vira aviso no cartão — sem degradar: tarefa vai demorar, não falhar.
        Se a sessão falhar de fato, o caminho normal degrada e remedia. Sem nenhuma pressão, o aviso some."""
        try:
            p = await rt.sonda.run(rt.io.guest_pressure, timeout=15, label="pressão do convidado")
        except (DriverError, AdbError, AttributeError, TypeError):
            return
        if not p:
            return
        self._conferir_interrupcoes(rt, p)
        total = float(p.get("mem_total_mb") or 0)
        livre = float(p.get("mem_available_mb") or 0)
        load1 = float(p.get("load1") or 0)
        ncpu = max(1.0, float(p.get("ncpu") or 1))
        por_cpu = load1 > PRESSAO_LOAD_POR_CPU * ncpu
        por_ram = total > 0 and livre < PRESSAO_RAM_LIVRE_MIN * total
        pressionado = por_cpu or por_ram
        rt.pressure_strikes = rt.pressure_strikes + 1 if pressionado else 0
        nosso = bool(rt.attention and rt.attention.startswith(PRESSAO_PREFIXO))
        if rt.pressure_strikes >= PRESSAO_SONDAS and rt.state == InstanceState.online and (rt.attention is None or nosso):
            # O texto sai do ramo que disparou. Antes era um só, sempre "Mais RAM resolve": no android-06 de 28/09
            # (r-20260928195344-02ee9e) o load passava de 4× as 2 vCPU com RAM sobrando — o ramo de RAM disparou 0
            # vezes em ~2230 avisos — e o aviso mandava o dono para o remédio errado.
            numeros = f"load {load1:.1f} em {ncpu:.0f} vCPU, {livre:.0f} MB livres de {total:.0f} MB"
            if por_cpu and por_ram:
                recurso, remedio = "CPU e RAM", ("Mais RAM para esta imagem (perfil por imagem) e mais vCPU "
                                                 "(android.cores) ou menos aparelhos ligados ao mesmo tempo aliviam.")
            elif por_cpu:
                recurso, remedio = "CPU", ("Falta processador, não memória: mais vCPU (android.cores) ou menos "
                                           "aparelhos ligados ao mesmo tempo nesta máquina aliviam.")
            else:
                recurso, remedio = "RAM", "Mais RAM para esta imagem resolve (perfil por imagem)."
            texto = (f"{PRESSAO_PREFIXO} de {recurso}: {numeros}. Tarefas vão demorar; se a sessão falhar, o reparo "
                     f"automático entra. {remedio}")
            if rt.attention != texto:
                rt.attention = texto
                self.publish(rt, f"{rt.id}: {texto}", level="warn")
        elif not pressionado and nosso:
            rt.attention = None
            self.publish(rt, f"{rt.id}: convidado voltou ao normal")

    def _conferir_interrupcoes(self, rt: DeviceRuntime, p: dict[str, float]) -> None:
        """Fração de CPU em interrupção entre duas sondas; sustentada acima do teto com o aparelho OCIOSO, pede um
        reinício a frio (o que devolveu o android-06 e o android-04 ao normal em 28/09).

        Ocioso = ninguém no controle (nem a IA nem uma pessoa): nunca se reinicia um aparelho no meio de uma tarefa
        ou de uma sessão manual. Com alguém operando, a fração é medida e guardada, mas o contador não sobe.
        """
        total, irq = p.get("cpu_total_ticks"), p.get("cpu_irq_ticks")
        if total is None or irq is None:
            return
        anterior, rt.cpu_ticks = rt.cpu_ticks, (float(total), float(irq))
        if anterior is None or total - anterior[0] <= 0 or irq < anterior[1]:
            return                              # primeira leitura, ou o convidado reiniciou entre as duas
        frac = rt.irq_frac = (irq - anterior[1]) / (total - anterior[0])
        # Ocioso de verdade: ninguém no controle E a carga baixa. Logo depois do boot o convidado passa de 15% em irq
        # com load 25–27 (medido às 18:47 de 28/09, ~100 s no ar) — é o trabalho de subir, não a doença; com a regra
        # só por controle, o reinício automático pediria outro reinício (ou diria que "não resolveu") a cada boot.
        carga, ncpu = float(p.get("load1") or 0), max(1.0, float(p.get("ncpu") or 1))
        ocioso = rt.control == ControlOwner.none and rt.state == InstanceState.online and carga <= ncpu
        self._gravar_interrupcao(rt, frac, p, ocioso)
        rt.irq_strikes = rt.irq_strikes + 1 if (ocioso and rt.irq_frac >= IRQ_OCIOSO_MAX) else 0
        if rt.irq_strikes < IRQ_SONDAS:
            return
        agora = time.monotonic()
        if rt.irq_restart_mono and agora - rt.irq_restart_mono < IRQ_REINICIO_INTERVALO_S:
            texto = (f"{IRQ_PREFIXO}: {rt.irq_frac:.0%} da CPU em interrupção com o aparelho ocioso, e o reinício a "
                     "frio de menos de 6 h não resolveu. Tarefas neste aparelho vão falhar por lentidão.")
            if rt.attention is None or (rt.attention or "").startswith(IRQ_PREFIXO):
                if rt.attention != texto:
                    rt.attention = texto
                    self.publish(rt, f"{rt.id}: {texto}", level="warn")
            return
        if (travada := self.conta_travada_em(rt.id)) is not None:
            # Quarentena (ADR-055): conta travada logada. Reiniciar é religar o app de uma conta morta; o aviso
            # fica, e quem decide o aparelho é o dono.
            rt.irq_strikes = 0
            self.marcar_atencao(rt, f"{IRQ_PREFIXO}: {rt.irq_frac:.0%} da CPU em interrupção com o aparelho ocioso, "
                                    f"mas a conta @{travada} está travada e logada nele (quarentena): o reinício "
                                    "automático não mexe neste aparelho. Precisa do dono.")
            return
        motivo = (f"{IRQ_PREFIXO}: {rt.irq_frac:.0%} da CPU em interrupção com o aparelho ocioso "
                  f"({rt.irq_strikes} sondas seguidas); reinício a frio preventivo (não apaga dados)")
        rt.irq_strikes = 0
        rt.irq_restart_mono = agora
        cid = self.on_health_restart(rt.id, motivo)
        self.publish(rt, f"{rt.id}: {motivo}" + (f" — comando {cid}" if cid else " — reinício não aberto"),
                     level="warn")

    def _gravar_interrupcao(self, rt: DeviceRuntime, frac: float, p: dict[str, float], ocioso: bool) -> None:
        """Cada fração medida vira uma linha `measurements(kind='irq')`: a causa do acúmulo (21–90% com dias no ar,
        ~2% depois do reinício) não é conhecida, e em memória a série morria com o processo. Controle e interesse
        (prévia em grade/foco) vão junto porque são hipóteses; o acerto de relógio (TIME_SET) já tem linha própria
        (`kind='clock'`, com a instância) para cruzar pelo horário. Os ticks acumulados desde o boot do convidado
        também: dão a fração média desde o boot e, divididos por vCPU × 100 (USER_HZ), o tempo no ar — sem outra
        leitura de `/proc`.

        Gravar é observação: uma falha do banco aqui não pode tirar a regra do reinício nem o aviso de pressão
        (que vêm depois desta chamada), por isso é engolida com registro. Load e vCPU vão como lidos: ausente
        fica nulo na linha, não zero (a regra do ocioso é que trata o ausente como carga baixa)."""
        try:
            self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "irq", dumps({
                "instance_id": rt.id, "irq_frac": round(frac, 4), "load1": p.get("load1"), "ncpu": p.get("ncpu"),
                "ocioso": ocioso, "controle": rt.control.value, "interesse": self.nivel_de_interesse(rt),
                "cpu_total_ticks": p.get("cpu_total_ticks"), "cpu_irq_ticks": p.get("cpu_irq_ticks")})))
        except Exception:  # noqa: BLE001 - medir nunca pode derrubar a sonda de saúde
            log.exception("%s: não foi possível gravar a fração de interrupção", rt.id)

    async def conferir_conectividade(self, rt: DeviceRuntime) -> ConnectivityInfo:
        """Sonda a internet DENTRO do aparelho e guarda o resultado. Nunca muda `state`: sem internet o aparelho
        segue `online` (instalar por adb, abrir app e tela funcionam); só o aviso do cartão diz o que falta.

        Adb mudo é `unknown`, não "sem internet" — e o aviso anterior fica como estava."""
        try:
            r = await rt.sonda.run(rt.io.connectivity_probe, timeout=70, label="sonda de internet")
            info = conectividade.classificar(**r, checked_at=now_iso())
        except (DriverError, AdbError, AttributeError, TypeError, asyncio.TimeoutError) as exc:
            info = conectividade.desconhecida(str(exc) or type(exc).__name__, now_iso())
        anterior = rt.connectivity.state
        rt.connectivity, rt.connectivity_mono = info, time.monotonic()
        nosso = bool(rt.attention and rt.attention.startswith(conectividade.AVISO_PREFIXO))
        if (info.state in ("unavailable", "degraded") and rt.state == InstanceState.online
                and (rt.attention is None or nosso) and rt.attention != info.detail):
            rt.attention = info.detail
            self.publish(rt, f"{rt.id}: {info.detail}", level="warn")
        elif info.state == "healthy" and nosso:
            rt.attention = None
            self.publish(rt, f"{rt.id}: internet voltou")
        elif info.state != anterior:                 # só mudança vira evento: a sonda periódica não polui o log
            self.publish(rt, f"{rt.id}: internet {info.state}")
        return info

    @staticmethod
    def _deve_sondar_saude(rt: DeviceRuntime, now_m: float) -> bool:
        """Sem `executor.queue_depth` de propósito: com a fila sempre ocupada (captura estourando prazo), a sonda
        nunca rodava e o aparelho congelado seguia `online`. A trilha `rt.sonda` é outra thread."""
        sonda = rt.tasks.get("health")
        return (rt.state == InstanceState.online and not rt.store
                and now_m - rt.health_checked_mono > INTERVALO_DA_SONDA_S and (sonda is None or sonda.done()))

    @staticmethod
    def _deve_sondar_conectividade(rt: DeviceRuntime, now_m: float) -> bool:
        rede = rt.tasks.get("connectivity")
        return (rt.state == InstanceState.online and not rt.store and (rede is None or rede.done())
                and (rt.connectivity.state == "unknown" and now_m - rt.connectivity_mono > INTERVALO_DA_SONDA_S
                     or now_m - rt.connectivity_mono > conectividade.INTERVALO_S))

    async def _sondar_conectividade(self, rt: DeviceRuntime) -> None:
        try:
            if rt.state == InstanceState.online:
                await self.conferir_conectividade(rt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sonda nunca pode derrubar o monitor
            log.exception("%s: erro na sonda de internet", rt.id)

    # ------------------------------------------------------------------ relógio do convidado (condição própria)
    async def conferir_relogio_do_convidado(self, rt: DeviceRuntime) -> None:
        """Mede o desvio do relógio do convidado e, se passar da tolerância, acerta e CONFERE. Nunca mexe em `state`
        nem em `readiness`: relógio errado é condição própria (`clock_state`, aviso no cartão), não "system_server ou
        display mudos" (K-031).

        O acerto (`cmd alarm set-time`) leva um instante absoluto: se estourar o prazo, a transação pode cair depois e
        ATRASAR o convidado. Isso é o `incerto` — e é a reconferência (antecipada para
        `INTERVALO_DO_RELOGIO_PENDENTE_S`) que mede de novo e desfaz o que tiver caído atrasado. Medir é só leitura, na trilha de sonda; acertar é efeito,
        na fila do aparelho."""
        if self.io_factory is not None:           # testes: aparelho falso, sem adb real (o mesmo desvio da identidade)
            return
        rt.clock_checked_mono = time.monotonic()
        try:
            desvio = await rt.sonda.run(rt.adb.clock_skew_s, timeout=20, label="relógio")
        except (DriverError, AdbError, ValueError) as exc:
            rt.clock_state = "unknown"             # não deu para medir: não se sabe, e não se corrige às cegas
            log.info("%s: não foi possível medir o relógio agora (%s)", rt.id, exc)
            return
        if abs(desvio) <= RELOGIO_TOLERANCIA_S:
            self._relogio_certo(rt, desvio)
            return
        if rt.state != InstanceState.online:
            # A medida levou tempo: se o aparelho saiu do ar nesse meio, acertar a hora cairia no stop/snapshot.
            return
        if self.ia_no_controle(rt) and abs(desvio) <= RELOGIO_ADIAVEL_MAX_S:
            # Objetivo em execução: o acerto entraria na fila do aparelho entre duas ações da IA e dispararia TIME_SET
            # em todo app. Medir (trilha de sonda) continua; acertar fica para `ai_end`, que antecipa a reconferência.
            # Não é "não convergiu": sem aviso no cartão.
            rt.clock_state, rt.clock_skew_s = "adiado", desvio
            log.info("%s: relógio %+d s fora da tolerância; acerto adiado até a IA soltar o aparelho", rt.id, desvio)
            return
        try:
            antes, depois = await rt.executor.run(rt.adb.sync_clock, timeout=45, label="acertar relógio")
        except (DriverTimeout, AdbTimeout) as exc:
            rt.clock_state, rt.clock_skew_s = "incerto", desvio
            log.warning("%s: acertar o relógio (%+d s) estourou o prazo (%s); o efeito pode cair atrasado — a "
                        "reconferência mede de novo em %.0f s", rt.id, desvio, exc, INTERVALO_DO_RELOGIO_PENDENTE_S)
            return
        except (DriverError, AdbError, ValueError) as exc:
            rt.clock_state, rt.clock_skew_s = "fora", desvio
            log.warning("%s: acertar o relógio (%+d s) falhou (%s)", rt.id, desvio, exc)
            self._aviso_do_relogio(rt, desvio)
            return
        self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "clock", dumps({
            "instance_id": rt.id, "clock_skew_before_after_s": [antes, depois]})))
        if abs(depois) <= RELOGIO_TOLERANCIA_S:
            log.info("%s: relógio acertado (%+d s → %+d s)", rt.id, antes, depois)
            self._relogio_certo(rt, depois)
            return
        rt.clock_state, rt.clock_skew_s = "fora", depois
        self._aviso_do_relogio(rt, depois)

    def _relogio_certo(self, rt: DeviceRuntime, desvio: int) -> None:
        rt.clock_state, rt.clock_skew_s = "ok", desvio
        if rt.attention and rt.attention.startswith(RELOGIO_PREFIXO):
            rt.attention = None
            self.publish(rt, f"{rt.id}: relógio do aparelho voltou ao certo")

    def _aviso_do_relogio(self, rt: DeviceRuntime, desvio: int) -> None:
        """Só ocupa o cartão vazio ou o que já é dele — nunca atropela um aviso de outro assunto — e só com o aparelho
        no ar: fora dele o aviso ficaria no cartão de um aparelho parado, falando de um relógio que ninguém mede."""
        if rt.state != InstanceState.online:
            return
        if rt.attention is None or rt.attention.startswith(RELOGIO_PREFIXO):
            sentido = "atrasado" if desvio < 0 else "adiantado"
            self.marcar_atencao(rt, f"{RELOGIO_PREFIXO} está {abs(desvio)} s {sentido} "
                                    "em relação ao servidor e o acerto automático não convergiu. Login, TLS e códigos "
                                    "com hora podem falhar; reinicie o aparelho ou acerte a hora nele.")

    def _deve_conferir_relogio(self, rt: DeviceRuntime, now_m: float) -> bool:
        tarefa = rt.tasks.get("clock")
        intervalo = INTERVALO_DO_RELOGIO_S if rt.clock_state == "ok" else INTERVALO_DO_RELOGIO_PENDENTE_S
        return (self.io_factory is None and rt.state == InstanceState.online and (tarefa is None or tarefa.done())
                and now_m - rt.clock_checked_mono > intervalo)

    async def _sondar_relogio(self, rt: DeviceRuntime) -> None:
        try:
            if rt.state == InstanceState.online:
                await self.conferir_relogio_do_convidado(rt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sonda nunca pode derrubar o monitor
            log.exception("%s: erro na conferência do relógio", rt.id)

    async def _sondar_saude(self, rt: DeviceRuntime) -> None:
        """A sonda periódica do monitor, em tarefa própria. Degrada o aparelho quando o convidado está morto."""
        try:
            if rt.state != InstanceState.online:
                return                       # o estado mudou enquanto a sonda esperava o aparelho responder
            if (doente := await self.conferir_saude(rt)) is not None:
                self._degradar(rt, doente)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sonda nunca pode derrubar o monitor
            log.exception("%s: erro na sonda de saúde", rt.id)

    def _degradar(self, rt: DeviceRuntime, motivo: str) -> None:
        """O aparelho está no ar e inútil: o estado passa a dizer isso, e a remediação é pedida.

        Não reaproveita `_on_device_lost` de propósito: lá o processo do emulador sumiu e o PID é zerado. Aqui o
        processo está VIVO — quem morreu foi o Android de dentro —, e apagar o PID faria o monitor parar de
        vigiar um emulador que continua consumindo a máquina.
        """
        self._prontidao(rt, "boot_completed", motivo[:200])      # o framework é que não serve: degrau abaixo de ready
        try:
            atual = asyncio.current_task()
        except RuntimeError:                       # chamado de fora de um laço (teste síncrono)
            atual = None
        for name in TAREFAS_DO_NO_AR:
            t = rt.tasks.pop(name, None)
            # Nunca cancela a PRÓPRIA tarefa: `ensure_automation` roda como a tarefa "automation" e degrada de
            # dentro dela. Cancelar-se aqui trocaria o `return False` de quem chamou por um `CancelledError`.
            if t and t is not atual:
                t.cancel()
        rt.frame = None
        rt.automation = AutomationInfo(state="error", detail=motivo)
        # Repetir o MESMO estado não vira evento. O monitor volta a este aparelho a cada 30 s, e era exatamente
        # essa repetição que enchia o diário de ~70 eventos por hora por aparelho sem dizer nada de novo.
        if rt.state == InstanceState.error and rt.attention == motivo:
            return
        self._set_state(rt, InstanceState.error, motivo, level="error", attention=motivo)
        self._pedir_reparo(rt, motivo)

    def _pedir_reparo(self, rt: DeviceRuntime, motivo: str) -> bool:
        """Chama quem decide o DEGRAU (`despacho.remediar`), respeitando só o intervalo mínimo. O teto antigo em memória
        (`restart_attempts`) parava para sempre depois de 2 e sumia num reinício do backend; agora a contagem é a
        do histórico de comandos, e quem esgotou a escada volta a ser tentado em ciclos — nunca esquecido."""
        if rt.desired_state != InstanceState.online.value:
            return False                 # ninguém pediu este aparelho no ar; reiniciá-lo seria decisão nossa
        if rt.control != ControlOwner.none:
            return False                 # alguém (pessoa ou IA) está com o aparelho: reiniciar por baixo, nunca
        if rt.bloqueio_de_crash:
            return False                 # 29.55 (c): parou no diálogo de relatório de falha; o motivo é claro e é da pessoa
        if time.monotonic() < rt.restart_backoff_until:
            return False
        rt.restart_attempts += 1
        rt.restart_backoff_until = time.monotonic() + REINICIO_COOLDOWN_S
        try:
            self.on_remediation_needed(rt.id, motivo)
        except Exception:  # noqa: BLE001 - pedir remediação nunca pode derrubar o monitor
            log.exception("%s: falha ao pedir o reparo automático", rt.id)
        return True

    def adiar_reparo(self, rt: DeviceRuntime, segundos: float) -> None:
        """Quem esgotou a escada não é abandonado: volta a ser tentado depois deste prazo."""
        rt.restart_backoff_until = time.monotonic() + segundos

    # ------------------------------------------------------------------ pausa do reparo automático (experimento/manutenção)
    @staticmethod
    def pausa_de_reparo(rt: DeviceRuntime) -> PausaDeReparo | None:
        """A pausa em vigor deste aparelho, ou `None`. Puro: o prazo vencido vale como "sem pausa" aqui mesmo, sem
        depender de o monitor ter limpado (quem limpa e avisa é `_expirar_pausa_de_reparo`)."""
        p = rt.repair_pause
        return p if p is not None and now() < p.until else None

    def pausar_reparo(self, rt: DeviceRuntime, ttl_s: int, motivo: str, quem: str) -> PausaDeReparo:
        """Liga a pausa deste aparelho (e só deste) por `ttl_s`, trocando a anterior se houver. O prazo é obrigatório:
        quem chama valida (`RepairPauseBody`); aqui `ttl_s <= 0` é erro de programação."""
        if ttl_s <= 0:
            raise ValueError("a pausa do reparo precisa de prazo (ttl_s > 0)")
        agora = now()
        rt.repair_pause = PausaDeReparo(until=agora + timedelta(seconds=ttl_s), since=agora, reason=motivo, by=quem)
        self._gravar_pausa(rt.id, rt.repair_pause)
        self.bus.emit("instance.repair_pause", f"{rt.id}: reparo automático PAUSADO por {ttl_s} s — {motivo} ({quem})",
                      level="warn", instance_id=rt.id,
                      data={"paused": True, "until": to_iso(rt.repair_pause.until), "reason": motivo, "by": quem})
        self.publish(rt)
        return rt.repair_pause

    def retomar_reparo(self, rt: DeviceRuntime, quem: str, *, motivo: str = "pausa encerrada") -> bool:
        """Desliga a pausa antes do prazo. `False` se não havia nenhuma."""
        if rt.repair_pause is None:
            return False
        rt.repair_pause = None
        self._gravar_pausa(rt.id, None)
        self.bus.emit("instance.repair_pause", f"{rt.id}: reparo automático RETOMADO — {motivo} ({quem})", level="info",
                      instance_id=rt.id, data={"paused": False, "reason": motivo, "by": quem})
        self.publish(rt)
        return True

    #: Chave de `settings` com as pausas em vigor, por aparelho (25.13). Sem migração: `settings` é o lugar de estado
    #: pequeno e global do backend (como `limits`).
    CHAVE_DAS_PAUSAS = "repair_pauses"

    def _pausas_gravadas(self) -> dict[str, object]:
        mapa = loads(self.db.scalar("SELECT value FROM settings WHERE key=?", (self.CHAVE_DAS_PAUSAS,)), {})
        return mapa if isinstance(mapa, dict) else {}

    def _gravar_pausa(self, instance_id: str, pausa: PausaDeReparo | None) -> None:
        """Lê, muda só a linha deste aparelho e regrava: os aparelhos de outro backend no mesmo banco ficam como estão.
        A gravação nunca derruba a pausa em memória: falhar aqui só volta ao comportamento antigo (some no restart)."""
        try:
            mapa = self._pausas_gravadas()
            if pausa is None:
                mapa.pop(instance_id, None)
            else:
                mapa[instance_id] = {"until": to_iso(pausa.until), "since": to_iso(pausa.since), "reason": pausa.reason,
                                     "by": pausa.by}
            self.db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET"
                            " value=excluded.value", (self.CHAVE_DAS_PAUSAS, dumps(mapa)))
        except Exception:  # noqa: BLE001 — persistência da pausa é proteção extra, nunca motivo para falhar o pedido
            log.exception("%s: a pausa do reparo não foi gravada (vale até o próximo restart)", instance_id)

    def _pausa_gravada(self, instance_id: str) -> PausaDeReparo | None:
        """A pausa gravada deste aparelho, se ainda vale. A vencida não volta (o monitor a limparia na hora)."""
        try:
            p = self._pausas_gravadas().get(instance_id)
            if not isinstance(p, dict):
                return None
            until, since = parse_iso(p.get("until")), parse_iso(p.get("since"))
            if until is None or since is None or now() >= until:
                return None
            return PausaDeReparo(until=until, since=since, reason=str(p.get("reason") or ""), by=str(p.get("by") or ""))
        except Exception:  # noqa: BLE001 — gravação ilegível vale como "sem pausa": é o comportamento de antes
            log.exception("%s: pausa do reparo gravada ilegível; ignorada", instance_id)
            return None

    def _expirar_pausa_de_reparo(self, rt: DeviceRuntime) -> None:
        """O monitor limpa a pausa vencida e diz, uma vez, que o reparo volta ao normal."""
        if rt.repair_pause is not None and now() >= rt.repair_pause.until:
            self.retomar_reparo(rt, "sistema", motivo="prazo da pausa venceu")

    def pausa_dto(self, rt: DeviceRuntime) -> RepairPauseInfo | None:
        p = self.pausa_de_reparo(rt)
        if p is None:
            return None
        return RepairPauseInfo(until=to_iso(p.until), since=to_iso(p.since), reason=p.reason, by=p.by,
                               remaining_s=max(0, int((p.until - now()).total_seconds())))

    def marcar_atencao(self, rt: DeviceRuntime, texto: str) -> None:
        if rt.attention != texto:
            rt.attention = texto
            self.publish(rt, f"{rt.id}: {texto}", level="warn")

    # ------------------------------------------------------------------ identidade física
    def conferir_identidade(self, rt: DeviceRuntime, identidade: str | None) -> bool:
        """Compara a impressão digital OBSERVADA com a gravada. `True` quando o aparelho por baixo do id mudou.

        Tudo o que o central afirma sobre o disco de um aparelho é gravado por `instance_id` lógico. Trocar o
        endereço por trás desse id — foi o que aconteceu com android-09/10 em 19/09/2026 — mantinha como verdade o
        que se observara no aparelho ANTIGO: o painel dizia Instagram `ready` num AVD criado no dia seguinte, com o
        app nem instalado. Com perfil vinculado, o mesmo mecanismo manteria "Conectado" para uma conta que não
        existe ali.

        `None` (não deu para observar) nunca invalida nada: falta de informação não é prova de troca.
        """
        if not identidade:
            return False
        anterior = rt.physical_id
        if anterior == identidade:
            return False
        rt.physical_id = identidade
        self.db.execute("UPDATE instances SET physical_id=?, physical_id_at=?, observed_serial=? WHERE id=?",
                        (identidade, now_iso(), rt.serial, rt.id))
        if anterior is None:
            return False        # primeira leitura: passa a haver identidade, e nada do que se sabia fica falso
        self._esquecer_o_que_o_disco_tinha(
            rt, "o aparelho físico por trás deste id mudou; o que se sabia do disco anterior deixou de valer")
        self.bus.emit("log", f"{rt.id}: o aparelho por trás deste id mudou — estado do app, sessão e evidência de "
                             f"conta foram invalidados.", level="warn", instance_id=rt.id)
        if (travada := self.conta_travada_em(rt.id)) is not None:
            # Quarentena (ADR-055): trocar o aparelho por trás do id não é prova de que a conta travada saiu dali —
            # nenhum disco foi apagado e nenhuma pessoa decidiu. O marcador fica (sem reativação automática, ADR-029)
            # e o cartão diz por quê; quem confere o aparelho novo e resolve é o dono.
            self.marcar_atencao(rt, f"Em quarentena: o aparelho físico por trás deste id mudou, mas o disco não foi "
                                    f"apagado e a conta @{travada} segue registrada como travada e logada aqui. Nada "
                                    "o toca até o dono conferir e resolver.")
        return True

    async def observar_identidade(self, rt: DeviceRuntime) -> str | None:
        """Lê do APARELHO a impressão digital dele. `None` quando não dá para saber.

        `ro.serialno` sozinho NÃO serve: medido em 21/09/2026, dois emuladores diferentes respondiam o mesmo
        `EMULATOR37X1X11X0`. O que distingue é a máquina que hospeda mais o que o convidado diz de si — o nome do
        AVD (`ro.boot.qemu.avd_name`) num emulador, modelo e serial num aparelho físico.
        """
        if self.io_factory is not None or rt.state != InstanceState.online:
            return None
        partes: list[str] = [rt.worker_id or "local"]
        for prop in ("ro.boot.qemu.avd_name", "ro.kernel.qemu.avd_name", "ro.product.model", "ro.serialno"):
            try:
                valor = await rt.executor.run(rt.adb.getprop, prop, timeout=20, label="identidade")
            except (DriverError, AdbError) as exc:
                log.info("%s: não foi possível ler a identidade agora (%s)", rt.id, exc)
                return None
            if valor:
                partes.append(f"{prop}={valor}")
        return "|".join(partes) if len(partes) > 1 else None

    async def reconhecer_aparelho(self, rt: DeviceRuntime) -> None:
        """Tarefa de "quem é você?", disparada quando o aparelho entra no ar."""
        try:
            self.conferir_identidade(rt, await self.observar_identidade(rt))
        except Exception:  # noqa: BLE001 - reconhecer é observação: nunca pode derrubar o ciclo do aparelho
            log.exception("%s: falha ao conferir a identidade física", rt.id)

    # ------------------------------------------------------------------ adoção / monitor
    async def _adopt(self, rt: DeviceRuntime) -> None:
        if self.io_factory is not None:       # testes: aparelho falso, sem SDK/ADB/Appium
            if rt.snapshot_valid:
                rt.state, rt.state_detail = InstanceState.hibernated, "hibernado (snapshot salvo)"
                return
            # T.2 (achado #165: a adoção segue de fora do que esta fatia cobriu, mas sem PID aqui NENHUM
            # aparelho falso do harness — todos "adotados" ao subir — chegava a ver a elegibilidade de
            # hibernação de `stop_instance` (que exige `rt.pid is not None`). A mesma `_spawn` do boot real.
            self._spawn(rt, self.android_de(rt), wipe=False, from_snapshot=False)
            await self._registrar_renderizador(rt)
            rt.state = InstanceState.online
            rt.automation = AutomationInfo(state="ready", detail="driver de teste")
            return
        if rt.external:
            await self._adopt_external(rt)
            return
        if not self.avd.exists(rt.avd_name):
            rt.state = InstanceState.absent
            return
        if not self.tools.found():
            rt.state, rt.state_detail = InstanceState.stopped, "Android SDK não encontrado"
            return
        alive = emu.is_our_emulator(rt.pid, rt.avd_name)
        try:
            state = await rt.executor.run(rt.adb.state, timeout=12, label="adb get-state")
        except (DriverError, AdbError):
            state = None
        p_readocao: prontidao.Prontidao | None = None
        if state == "device":
            booted = False
            try:
                booted = await rt.executor.run(rt.adb.boot_completed, timeout=12)
            except (DriverError, AdbError):
                pass
            estado = "mudo"
            if booted:
                # `boot_completed` continua 1 com o Android morto — ou congelado — por dentro: a sonda vem ANTES de
                # declarar online, e só a resposta positiva conta. Mudo não é "vivo por falta de prova".
                p_readocao = await self._sondar_prontidao(rt)
                if p_readocao.pronto:
                    # O preparo vem depois da sonda; se ele estourar, esta prontidão não vale mais (contrato temporal).
                    p_readocao = await self._preparar_e_revalidar(rt, p_readocao)
                estado = p_readocao.estado
                if estado == "morto":
                    self._degradar(rt, "O Android deste aparelho está sem os serviços de sistema (o `system_server` "
                                       "caiu): o adb responde, mas nenhum app abre, instala ou automatiza. Reinicie o "
                                       "aparelho.")
                    return
            if booted and estado == "ok":
                await self._registrar_renderizador(rt)      # readoção: o processo é o de antes, e o log dele também
                rt.state = InstanceState.online
                self._prontidao(rt, "ready", "readotado: servicemanager, system_server e display responderam")
                rt.automation_failures = rt.health_failures = 0      # readoção é uma entrada no ar como outra
                rt.automation_last_error = None
                rt.state_detail = "readotado após reinício do backend" if alive else "emulador externo (não iniciado por este projeto)"
                self._start_online_tasks(rt)
                return
        if alive or state == "device":
            # adb `device` NESTE serial é um emulador na NOSSA porta de console, mesmo que o PID gravado não seja dele
            # (backend reiniciado, emulador iniciado por fora). Marcar `stopped` e zerar o PID com ele no ar deixava a
            # próxima subida colidir na porta (revisão pós-merge do PR #7, achado 7): é `booting`, e o `_wait_boot`
            # decide. O PID velho sai antes — lido pelo `_wait_boot`, ele viraria "o emulador encerrou no boot".
            if not alive and rt.pid:
                self._save_pid(rt, None)
            rt.state, rt.state_detail = InstanceState.booting, "emulador em inicialização (readotado)"
            espera = 0.0
            if p_readocao is not None and p_readocao.incerta:
                # A tentativa acabou de estourar uma operação com efeito neste guest: a próxima vem espaçada, não ~1 s
                # depois (revisão pós-merge do PR #7, achado 4).
                espera = ESPERA_APOS_TENTATIVA_INCERTA_S
                self._prontidao(rt, "boot_completed", f"{p_readocao.detalhe()}; próxima tentativa em {espera:.0f} s")
            rt.tasks["boot"] = asyncio.create_task(self._wait_boot(rt, time.monotonic(), adopted=True,
                                                                   espera_inicial_s=espera))
        else:
            rt.state = InstanceState.hibernated if rt.snapshot_valid else InstanceState.stopped
            if rt.snapshot_valid:
                rt.state_detail = "hibernado (snapshot salvo)"
            if rt.pid:
                self._save_pid(rt, None)

    def _transport_hint(self, rt: DeviceRuntime) -> str:
        """Achado #179: 'túnel fora', 'emulador desligado no worker' e 'ADB não responde' eram a mesma frase.
        Quando se sabe que o TRANSPORTE está fora, o motivo passa a apontar a causa em vez do sintoma."""
        if not rt.worker_id:
            return ""
        estado = self.transport_state_of(rt.worker_id)
        if estado == "down":
            return f" — túnel para o worker {rt.worker_id} está fora (scripts/worker-tunnel.ps1)"
        return ""

    #: Estados de processo que o worker reporta e que significam "não está rodando lá".
    PROCESSO_PARADO = frozenset({"stopped", "absent", "exited", "hibernated"})

    def _motivo_do_externo_parado(self, rt: DeviceRuntime, adb_state: str | None) -> str:
        """Achado #61: 'aparelho externo X não está conectado ao ADB' era a ÚNICA frase para três realidades.

        Quem desligou o emulador de propósito pelo painel via um problema de conectividade; quem perdeu o
        servidor via a mesma coisa; e quem tinha o emulador de pé com o ADB inalcançável, idem. Com o processo
        que o worker reporta em mãos, cada caso ganha a sua frase — e o servidor aparece pelo nome, porque de
        um aparelho não dava para descobrir em que máquina ele roda.
        """
        sintoma = (f"aparelho externo {rt.serial} não está conectado ao ADB"
                   + (f" (estado: {adb_state})" if adb_state else ""))
        if not rt.worker_id:
            return sintoma + self._transport_hint(rt)
        info = self.worker_process_of(rt.worker_id, rt.id)
        if info is None:
            return (f"{sintoma} — o servidor {rt.worker_id} não está inscrito"
                    + self._transport_hint(rt))
        nome, conectado, processo = info
        if not conectado:
            return f"servidor {nome} fora do ar — o estado do emulador lá é desconhecido"
        if not processo:
            return f"servidor {nome} está no ar, mas não reporta este aparelho" + self._transport_hint(rt)
        if processo in self.PROCESSO_PARADO:
            return f"emulador desligado em {nome}" + (" (hibernado)" if processo == "hibernated" else "")
        if processo == "unknown":
            # Aparelho que o agente não gere (físico, contêiner) ou sonda que falhou: "unknown" caía no ramo de
            # baixo e virava "emulador ligado … não alcança" sobre algo que ninguém afirmou estar ligado.
            return f"servidor {nome} não sabe o estado do emulador deste aparelho" + self._transport_hint(rt)
        # O estado que o ADB devolveu É informação (`offline` = o convidado travou; `unauthorized` = chave nova) e
        # era descartado neste ramo. Com ele, "não alcança" deixa de soar como problema de rede.
        adb = f" (adb: {adb_state})" if adb_state else ""
        return (f"emulador ligado em {nome} ({processo}), mas o Android lá não responde ao ADB daqui{adb}"
                + self._transport_hint(rt))

    async def _adopt_external(self, rt: DeviceRuntime) -> None:
        """Aparelho que o projeto não controla (celular físico, contêiner, outro emulador): só verifica se o ADB o vê."""
        try:
            await rt.executor.run(rt.adb.connect, timeout=25, label="adb connect")
            state = await rt.executor.run(rt.adb.state, timeout=12, label="adb get-state")
        except (DriverError, AdbError):
            state = None
        if state == "device":
            # A escada inteira, na ordem: adb `device` → `boot_completed` → framework respondendo. O adb responde
            # `device` ANTES de o `system_server` registrar os serviços (android-09, 25/09/2026: `error` "system_server
            # caiu" num boot normal), e `boot_completed=1` vem restaurado do snapshot mesmo com o Android congelado
            # (wake do android-09, mesmo dia: `succeeded` e `online` com `service check`/`screencap` travando). Dentro
            # do prazo de boot, qualquer degrau que falte é `booting` com o motivo; passado o prazo, é degradado. O
            # marcador `boot_externo_desde` só zera com resposta positiva ou com o adb deixando de ser `device` —
            # zerá-lo ao degradar faria a readoção seguinte recomeçar o prazo e oscilar entre `error` e `booting`.
            try:
                subiu = await rt.executor.run(rt.adb.boot_completed, timeout=12, label="boot_completed")
            except (DriverError, AdbError):
                subiu = False
            agora = time.monotonic()
            rt.boot_externo_desde = rt.boot_externo_desde or agora
            dentro_do_prazo = agora - rt.boot_externo_desde < self.android_de(rt).boot_timeout_s
            if not subiu and dentro_do_prazo:
                self._prontidao(rt, "adb_device", "adb responde; boot ainda não concluído")
                self._entrando_no_ar(rt, f"Android ainda subindo em {rt.serial} (boot não concluído)")
                return
            restante = self.android_de(rt).boot_timeout_s - (agora - rt.boot_externo_desde)
            p = await self._sondar_prontidao(rt, max(5.0, restante))
            estado = p.estado
            if estado == "mudo" and dentro_do_prazo:
                self._prontidao(rt, "boot_completed", p.detalhe())
                self._entrando_no_ar(rt, f"Android subiu em {rt.serial}; {p.detalhe()}")
                return
            if estado != "ok":
                if estado == "morto":
                    motivo = ("O Android deste aparelho está sem os serviços de sistema (o `system_server` caiu): o "
                              "adb responde, mas nenhum app abre, instala ou automatiza. Reinicie o aparelho.")
                elif estado == "erro":
                    # Não é o aparelho: a sonda quebrou (a pilha está no log). Dizer "congelado" mandaria reiniciar
                    # um Android que ninguém chegou a consultar.
                    motivo = f"A prontidão de {rt.serial} não pôde ser avaliada: {p.detalhe()}. Veja o log do central."
                else:
                    motivo = (f"O Android de {rt.serial} não ficou pronto em {agora - rt.boot_externo_desde:.0f} s depois "
                              f"de subir ({p.detalhe()}): o processo e o adb estão no ar, mas o sistema não responde "
                              "(congelado). Reinicie o aparelho.")
                self._degradar(rt, motivo)
                return
            self._prontidao(rt, "android_responsive", p.detalhe())
            # Contrato temporal: um preparo que estoura DEPOIS da sonda invalida a prontidão; decide uma rodada nova.
            p = await self._preparar_e_revalidar(rt, p, max(5.0, restante))
            if not p.pronto:
                if p.estado == "mudo" and dentro_do_prazo:
                    self._prontidao(rt, "boot_completed", p.detalhe())
                    self._entrando_no_ar(rt, f"Android subiu em {rt.serial}; {p.detalhe()}")
                    return
                self._degradar(rt, (f"O Android de {rt.serial} deixou de responder depois de subir ({p.detalhe()}): o "
                                    "processo e o adb estão no ar, mas o sistema não responde. Reinicie o aparelho."))
                return
            rt.boot_externo_desde = 0.0
            self._prontidao(rt, "android_responsive", p.detalhe())
            if rt.state != InstanceState.online:
                rt.health_failures = rt.automation_failures = 0
                self._set_state(rt, InstanceState.online, f"aparelho externo via ADB ({rt.serial})")
                self._start_online_tasks(rt)
                self.on_device_free()
        else:
            rt.boot_externo_desde = 0.0
            novo = self._motivo_do_externo_parado(rt, state)
            # O motivo de um aparelho de worker MUDA sem o estado mudar: o servidor cai, o emulador de lá é
            # ligado, o túnel volta. Repetir a frase antiga deixava o cartão contraditório — título dizendo
            # "servidor fora do ar" e detalhe dizendo "emulador desligado". Republica só quando o texto muda,
            # que é o que evita um evento a cada volta do monitor.
            if rt.state != InstanceState.stopped or not rt.state_detail or novo != rt.state_detail:
                self._set_state(rt, InstanceState.stopped, novo)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(6)
            now_m = time.monotonic()
            # Cópia: o laço suspende em `await`, e o dicionário muda no meio — aparelho provisionado ou adotado
            # entra, aposentado sai (migração 050). Iterar o vivo mataria o monitor com "dictionary changed size".
            for rt in list(self.devices.values()):
                try:
                    if (rt.state == InstanceState.online and rt.pid
                            and not self.emulator.process_alive(rt.pid, rt.avd_name)):
                        self._on_device_lost(rt, "O processo do emulador encerrou inesperadamente.")
                    if rt.external and now_m - rt.external_checked_mono > 30 and not rt.executor.queue_depth:
                        rt.external_checked_mono = now_m          # cabo solto / Wi-Fi caiu / voltou: o estado acompanha
                        if rt.state == InstanceState.online:
                            estado_adb = await rt.executor.run(rt.adb.state, timeout=12, label="adb get-state")
                            if estado_adb != "device":
                                # Com worker, "sumiu do ADB" é sintoma: quem sabe a causa é o processo lá (#61).
                                self._on_device_lost(rt, self._motivo_do_externo_parado(rt, estado_adb) if rt.worker_id
                                                     else f"Aparelho externo {rt.serial} sumiu do ADB."
                                                          + self._transport_hint(rt))
                        elif (rt.state in (InstanceState.stopped, InstanceState.error)
                              or rt.state == InstanceState.booting and rt.boot_externo_desde):   # boot visto pelo adb
                            # Só readota quando ninguém mandou parar. Sem esta guarda, um "Parar" era desfeito em
                            # ≤36 s e o cartão continuava dizendo "desligado" — a interface mentia duas vezes.
                            if rt.desired_state != InstanceState.stopped.value:
                                await self._adopt_external(rt)
                            else:
                                # Parado de propósito: não readota, mas o motivo acompanha o servidor (29.36) — a
                                # primeira batida depois da reconexão troca "não sabe o estado" por "desligado".
                                self._recalcular_motivo_do_parado(rt)
                    # Aparelho parado em `error` com `desired_state=online` e ninguém no controle: o reparo é pedido
                    # de novo quando o prazo passa (600 s entre degraus; 6 h depois de esgotar a escada). Vale para
                    # o local também — antes só o externo era readotado, e o local em `error` ficava assim até
                    # alguém clicar.
                    if (rt.state == InstanceState.error and rt.attention and rt.desired_state == InstanceState.online.value
                            and rt.control == ControlOwner.none and now_m > rt.restart_backoff_until
                            and not rt.executor.queue_depth):
                        self._pedir_reparo(rt, rt.attention)
                    self._expirar_pausa_de_reparo(rt)
                    if rt.control == ControlOwner.user and now_m > rt.lease_expires_mono:
                        self._end_user_control(rt, "Controle manual expirou por inatividade e foi devolvido.")
                    # Saúde do CONVIDADO em quem já está no ar — local e remoto. O android-03, emulador desta
                    # máquina, acumulou 235 falhas iguais num só dia também dizendo `online`: o defeito nunca foi
                    # exclusivo de aparelho de outra máquina. Roda na trilha própria (`rt.sonda`): nunca espera a
                    # fila da captura/automação e nunca entra na frente de uma execução.
                    if self._deve_sondar_saude(rt, now_m):
                        rt.health_checked_mono = now_m
                        # Em tarefa própria: a sonda fala com o aparelho e num convidado sobrecarregado um
                        # `adb shell` leva 7 a 16 s (medido). Esperá-la aqui prenderia o monitor INTEIRO — a
                        # expiração de controle manual dos outros aparelhos junto.
                        rt.tasks["health"] = asyncio.create_task(self._sondar_saude(rt), name=f"health-{rt.id}")
                    # Internet do convidado: logo depois de entrar no ar (estado `unknown`) e depois a cada
                    # INTERVALO_S. Mesma regra da sonda de saúde: tarefa própria, trilha própria.
                    if self._deve_sondar_conectividade(rt, now_m):
                        rt.connectivity_mono = now_m
                        rt.tasks["connectivity"] = asyncio.create_task(self._sondar_conectividade(rt),
                                                                       name=f"connectivity-{rt.id}")
                    # Relógio do convidado: reconferência periódica (K-031). É ela que desfaz um `cmd alarm set-time`
                    # que estourou o prazo e caiu atrasado — por isso o acerto pôde sair do portão de prontidão.
                    if self._deve_conferir_relogio(rt, now_m):
                        rt.clock_checked_mono = now_m
                        rt.tasks["clock"] = asyncio.create_task(self._sondar_relogio(rt), name=f"clock-{rt.id}")
                    # sessão de automação que falhou ao abrir: nova tentativa espaçada, sem depender de uma execução
                    if (rt.state == InstanceState.online and rt.automation.state == "error" and self.io_factory is None
                            and now_m - rt.automation_retry_mono > self._espera_da_proxima_sessao(rt)):
                        prev = rt.tasks.get("automation")
                        if prev is None or prev.done():
                            rt.automation_retry_mono = now_m
                            rt.tasks["automation"] = asyncio.create_task(self.ensure_automation(rt),
                                                                         name=f"automation-{rt.id}")
                except Exception:  # noqa: BLE001
                    log.exception("monitor %s", rt.id)

    def _on_device_lost(self, rt: DeviceRuntime, why: str) -> None:
        for name in TAREFAS_DO_NO_AR:
            t = rt.tasks.pop(name, None)
            if t:
                t.cancel()
        rt.frame = None
        rt.automation = AutomationInfo()
        self._save_pid(rt, None)
        self._set_state(rt, InstanceState.error, why, level="error", attention=why)

    async def _metrics_loop(self) -> None:
        psutil.cpu_percent(interval=None)
        while True:
            await asyncio.sleep(3)
            vm = psutil.virtual_memory()
            ems: list[EmulatorMetric] = []
            for rt in list(self.devices.values()):      # cópia: mesma razão do monitor (o laço suspende)
                if rt.pid and rt.state in (InstanceState.online, InstanceState.booting):
                    usage = await asyncio.to_thread(emu.process_usage, rt.pid)
                    if usage:
                        rt.resources = InstanceResources(rss_mb=usage[0], cpu_percent=usage[1])
                        ems.append(EmulatorMetric(instance_id=rt.id, pid=rt.pid, rss_mb=usage[0], cpu_percent=usage[1]))
                else:
                    rt.resources = None
            self.last_metrics = Metrics(ts=now_iso(), cpu_percent=psutil.cpu_percent(interval=None),
                                        mem_total_gb=round(vm.total / 2**30, 1),
                                        mem_available_gb=round(vm.available / 2**30, 1),
                                        mem_used_percent=vm.percent, emulators=ems)
            self.bus.emit("metrics", "metrics", data={"metrics": self.last_metrics.model_dump(mode="json")})

    # ------------------------------------------------------------------ ciclo de vida
    def _save_pid(self, rt: DeviceRuntime, pid: int | None) -> None:
        rt.pid = pid
        self.db.execute("UPDATE instances SET emulator_pid=?, emulator_started_at=? WHERE id=?",
                        (pid, now_iso() if pid else None, rt.id))

    def bind_worker(self, worker_id: str, verbs: list[str] | None) -> list[str]:
        """Liga (ou desliga) as capacidades declaradas por um worker aos aparelhos que ele hospeda.

        `verbs=None` é a desconexão: o aparelho volta a aceitar só o que o transporte alcança, e o painel para de
        oferecer botão de ciclo de vida para uma máquina que não está lá. Devolve quem mudou, para virar evento.
        """
        mudados = []
        for rt in self.devices.values():
            if rt.worker_id != worker_id:
                continue
            # 29.36: a conexão e a queda mudam o MOTIVO do aparelho parado sem mudar o estado. Sem isto, o texto
            # da queda ("servidor fora do ar") ficava preso depois da reconexão em quem foi parado de propósito:
            # o monitor não readota `desired_state=stopped`, e era a readoção que trocava a frase.
            if self._recalcular_motivo_do_parado(rt):
                if rt.id not in mudados:
                    mudados.append(rt.id)
            if rt.worker_verbs == verbs:
                continue
            rt.worker_verbs = verbs
            if rt.id not in mudados:
                mudados.append(rt.id)
            self.publish(rt)
        return mudados

    def _recalcular_motivo_do_parado(self, rt: DeviceRuntime) -> bool:
        """Reescreve o detalhe de um aparelho de worker PARADO com o motivo de agora, sem readotar. Devolve `True`
        quando o texto mudou (e o estado foi republicado).

        Só `stopped`: `error` tem frase própria (a do reparo), e o aparelho no ar não tem motivo de parado. Sem o
        estado do ADB de propósito: quem chama não sondou, e o motivo que importa aqui é o do servidor e do processo
        lá (item 29.36, RA-21: 5 de 6 aparelhos do notebook diziam "fora do ar" com o servidor conectado).
        """
        if not (rt.external and rt.worker_id and rt.state == InstanceState.stopped):
            return False
        novo = self._motivo_do_externo_parado(rt, None)
        if novo == rt.state_detail:
            return False
        self._set_state(rt, InstanceState.stopped, novo)
        return True

    # ------------------------------------------------------------------ capacidades declaradas
    def registrar_capacidades(self, rt: DeviceRuntime, campos: dict[str, Any], *, fonte: str,
                              publicar: bool = True, observado: bool = False) -> bool:
        """Grava o que se soube sobre o aparelho. Devolve `True` quando algo mudou.

        Só sobrescreve o que veio COM valor: uma batida que não sabe o nível de API não apaga o que o ADB já
        tinha lido. Conservador de propósito — apagar capacidade conhecida faria o pré-voo voltar a deixar passar
        o que ele acabara de aprender a recusar.
        """
        mapa = {"device_kind": campos.get("kind", campos.get("device_kind")),
                "system_image": campos.get("system_image"), "api_level": campos.get("api_level"),
                "abis": campos.get("abis"), "play_store": campos.get("play_store")}
        mudou = False
        for nome, valor in mapa.items():
            if valor in (None, [], ""):
                continue
            if getattr(rt, nome) == valor:
                continue
            if nome == "abis" and rt.abis and not observado:
                # 29.66: o aparelho vence a declaração. O ADB lê `ro.product.cpu.abilist` (x86_64 e a tradução
                # arm64-v8a); o worker declara o `abi.type` do config.ini (só x86_64). As duas fontes trocavam o valor a
                # cada reinício do backend. A declaração só preenche o que ninguém leu.
                continue
            setattr(rt, nome, valor)
            mudou = True
        if not mudou:
            return False
        self.db.execute(
            "UPDATE instances SET device_kind=?, system_image=?, api_level=?, abis=?, play_store=?,"
            " capabilities_at=? WHERE id=?",
            (rt.device_kind, rt.system_image, rt.api_level, dumps(rt.abis or []),
             None if rt.play_store is None else int(rt.play_store), now_iso(), rt.id))
        log.info("%s: capacidades atualizadas por %s (api=%s abis=%s gms=%s)", rt.id, fonte, rt.api_level,
                 rt.abis, rt.play_store)
        if publicar:
            self.publish(rt)
        return True

    def capacidades_do_worker(self, worker_id: str, devices: list[Any]) -> None:
        """O que o agente DECLAROU sobre os aparelhos dele, do `hello` ou da batida.

        É a metade que faltava do pré-voo para aparelho de outra máquina: sem ela o central não tinha como dizer
        "este pacote é só ARM e aquele aparelho não traduz" antes de agendar — descobria no meio, como
        `INSTALL_FAILED_NO_MATCHING_ABIS`.
        """
        for d in devices or []:
            rt = self.devices.get(getattr(d, "instance_id", None) or "")
            if rt is None or rt.worker_id != worker_id:
                continue
            self.registrar_capacidades(rt, {
                "kind": getattr(d, "kind", None), "system_image": getattr(d, "system_image", None),
                "api_level": getattr(d, "api_level", None), "abis": list(getattr(d, "abis", None) or []),
                "play_store": getattr(d, "play_store", None)}, fonte=f"declaração do worker {worker_id}")
            self._renderizador_declarado(rt, worker_id, d)

    # ------------------------------------------------------------------ renderizador do emulador (29.11)
    async def _registrar_renderizador(self, rt: DeviceRuntime) -> None:
        """Lê do log do emulador o renderizador que ele SELECIONOU nesta subida e o guarda no aparelho.

        Chamado a cada entrada no ar (boot, reinício, acordar, readoção), ANTES de declarar `online`, quando a linha
        já foi escrita há muito. Só para emulador DESTA máquina: o log do aparelho de um worker mora lá, e quem o lê
        é o agente (`_renderizador_declarado`). Argumento aceito não é renderizador usado (medido em 30/09/2026): o
        `-gpu` diz o que se pediu, e só o log diz o que o emulador fez com o pedido.
        """
        if rt.external:
            return
        try:
            rt.renderer_configured = self.android_de(rt).gpu_mode
            rt.renderer_log = str(self.cfg.logs_dir / f"emulator-{rt.avd_name}.log")
            rt.renderer = await asyncio.to_thread(emu.ler_renderizador, self.cfg.logs_dir, rt.avd_name)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - saber o renderizador nunca pode impedir o aparelho de entrar no ar
            log.exception("%s: falha ao ler o renderizador selecionado pelo emulador", rt.id)
            rt.renderer = None
            return
        self._anunciar_fallback(rt)

    def _renderizador_declarado(self, rt: DeviceRuntime, worker_id: str, d: object) -> None:
        """O renderizador de um aparelho de OUTRA máquina, como o agente dela o declarou no `hello` ou na batida.

        Diferente das capacidades (`registrar_capacidades`), o SELECIONADO é substituído pelo que veio, inclusive
        por nada: ele descreve o processo que está no ar lá, e com o emulador desligado não há o que afirmar. O
        PEDIDO só muda quando vem com valor — agente antigo não declara, e isso não apaga o que já se sabia.
        """
        if not rt.external:
            return                              # emulador daqui: quem sabe é o log desta máquina
        antes = (rt.renderer_configured, rt.renderer)
        pedido, gles = getattr(d, "gpu_mode", None), getattr(d, "gpu_gles", None)
        if pedido:
            rt.renderer_configured = str(pedido)
        rt.renderer = emu.Renderizador(gles=str(gles), vulkan=getattr(d, "gpu_vulkan", None) or None) if gles else None
        avd = getattr(d, "avd_name", None) or rt.avd_name
        rt.renderer_log = f"logs/emulator-{avd}.log, na pasta de trabalho do worker {worker_id}"
        if (rt.renderer_configured, rt.renderer) != antes:
            self._anunciar_fallback(rt)
            self.publish(rt)

    @staticmethod
    def _texto_do_fallback(rt: DeviceRuntime) -> str | None:
        if rt.renderer is None or not emu.houve_fallback(rt.renderer_configured, rt.renderer.gles):
            return None
        vulkan = f" (Vulkan: `{rt.renderer.vulkan}`)" if rt.renderer.vulkan else ""
        # Duas causas com o mesmo retrato, e daqui não se distingue uma da outra: o `gpu_mode` mudou DEPOIS da subida
        # (vale no próximo reinício — foi o caso de todo o parque em 30/09, depois da troca para `host`) ou o
        # emulador trocou de renderizador sem avisar. O texto diz as duas, em vez de afirmar a segunda.
        return (f"{RENDERIZADOR_PREFIXO}: este aparelho pede `{rt.renderer_configured}` (gpu_mode) e o emulador no "
                f"ar está com `{rt.renderer.gles}`{vulkan}. Se o gpu_mode mudou depois da subida, vale no próximo "
                f"reinício; se não, o emulador trocou de renderizador sem avisar (veja a linha `emuglConfig_init` em "
                f"{rt.renderer_log}). App que não roda nesse renderizador não é instalado nem aberto aqui.")

    def _aviso_do_renderizador(self, rt: DeviceRuntime) -> str | None:
        """O fallback silencioso como atenção do aparelho — só com ele no ar: fora dele não há emulador selecionando
        nada, e o aviso ficaria no cartão de um aparelho parado."""
        return self._texto_do_fallback(rt) if rt.state == InstanceState.online else None

    def _anunciar_fallback(self, rt: DeviceRuntime) -> None:
        """Uma linha no histórico quando o fallback é DESCOBERTO. O cartão o mostra enquanto durar (`dto`)."""
        if (texto := self._texto_do_fallback(rt)) is not None:
            log.warning("%s: %s", rt.id, texto)
            self.bus.emit("log", f"{rt.id}: {texto}", level="warn", instance_id=rt.id)

    @staticmethod
    def _renderizador_dto(rt: DeviceRuntime) -> RendererInfo | None:
        if rt.renderer_configured is None and rt.renderer is None:
            return None                         # não é emulador que se conheça: nada a afirmar
        gles = rt.renderer.gles if rt.renderer else None
        return RendererInfo(configured=rt.renderer_configured, gles=gles,
                            vulkan=rt.renderer.vulkan if rt.renderer else None,
                            fallback=emu.houve_fallback(rt.renderer_configured, gles))

    # ------------------------------------------------------------------ inventário aparelho ↔ worker
    def conferir_inventario(self, worker_id: str, devices: list[Any]) -> list[str]:
        """Confronta as TRÊS fontes de inventário a cada `hello`/batida (achado #47).

        O ciclo de vida vai por `instance_id` ao worker (que resolve para o AVD dele), enquanto ADB e Appium vão
        pela porta do túnel. Um erro em qualquer uma das pontas fazia `reset`/`stop` agir num AVD e a tela em
        outro, sem alarme — e os dados para detectar isso (`hello.devices[].instance_id` e `.adb_port`) já
        trafegavam e eram DESCARTADOS. Aqui eles passam a valer:

        1. instância amarrada a W que W não declara → alguém amarrou o aparelho à máquina errada;
        2. aparelho que W declara como instância de OUTRA máquina → o `worker.yaml` de lá está desatualizado;
        3. porta de ADB declarada diferente da que o túnel encaminha → o mapa do túnel aponta para outro aparelho.

        Divergência vira `inventory_state='divergent'` com motivo, some do painel quando se resolve, e faz os
        verbos destrutivos serem recusados (`verbos_suportados` não muda; quem recusa é a API, com a explicação).
        Devolve a lista de motivos, para quem chamou registrar.
        """
        if not devices:
            # Declaração VAZIA não é "não hospedo nada": é ausência de declaração, e o que não se sabe nunca vira
            # recusa. O worker local bate o coração com `devices=[]` de propósito (`workers/local.py`: o central
            # já conhece os aparelhos desta máquina), e agente de protocolo antigo pode calar o inventário. Sem
            # esta guarda, a primeira batida do próprio central condenaria todos os aparelhos locais.
            return []
        declarados = {getattr(d, "instance_id", None): d for d in devices
                      if getattr(d, "instance_id", None)}
        problemas: list[str] = []
        mudou_o_mapa = False
        for rt in self.devices.values():
            if rt.worker_id != worker_id:
                d = declarados.get(rt.id)
                if d is not None:
                    self._marcar_divergencia(rt, problemas, f"o worker {worker_id} declara hospedar {rt.id}, mas "
                                                            f"esta instância está amarrada a "
                                                            f"{rt.worker_id or 'nenhuma máquina'}")
                continue
            d = declarados.get(rt.id)
            if d is None:
                self._marcar_divergencia(rt, problemas, f"o worker {worker_id} não declara hospedar {rt.id}: um "
                                                        "comando para este aparelho falharia lá dentro")
                continue
            porta = getattr(d, "adb_port", None)
            if rt.remote_adb_port and porta and int(porta) != int(rt.remote_adb_port):
                self._marcar_divergencia(rt, problemas, f"o worker declara a porta de ADB {porta} para {rt.id} e "
                                                        f"o túnel encaminha para {rt.remote_adb_port}: o mapa "
                                                        "aponta para outro aparelho")
                continue
            if self._adotar_mapa_do_config(rt, porta):
                mudou_o_mapa = True
            if rt.inventory_state is not None:
                rt.inventory_state, rt.inventory_detail = None, None
                self.publish(rt, f"{rt.id}: inventário conferido com o worker {worker_id}")
        if mudou_o_mapa:
            # O arquivo de mapa só serve se estiver COMPLETO: trocar a tarefa do túnel para `-MapaArquivo` com
            # ele pela metade derrubaria os aparelhos que já funcionavam.
            self.escrever_mapa_do_tunel(worker_id)
        return problemas

    def _adotar_mapa_do_config(self, rt: DeviceRuntime, adb_port: Any) -> bool:
        """Preenche `remote_adb_port`/`tunnel_port` do que já vinha do `config.yaml`. Devolve `True` se mudou.

        As instâncias declaradas no YAML nasceram sem essas colunas: a porta local mora no `instances.external`
        (`127.0.0.1:15555`) e a porta remota só existia nos argumentos da tarefa agendada do túnel. Sem este
        preenchimento, `mapa_do_tunel` devolveria só os aparelhos ADOTADOS — e quem seguisse a documentação nova
        trocando `-Mapa` por `-MapaArquivo` perderia os seis que já estavam de pé. A porta remota é a que o
        agente DECLARA; a local é a que este servidor já usa para falar com ele.
        """
        if rt.remote_adb_port or not adb_port or not rt.external:
            return False
        _, _, local = (rt.serial or "").rpartition(":")
        if not local.isdigit():
            return False
        rt.remote_adb_port, rt.tunnel_port = int(adb_port), int(local)
        self.db.execute("UPDATE instances SET remote_adb_port=?, tunnel_port=? WHERE id=?",
                        (rt.remote_adb_port, rt.tunnel_port, rt.id))
        log.info("%s: mapa do túnel registrado a partir da declaração do worker (%s -> %s)",
                 rt.id, rt.tunnel_port, rt.remote_adb_port)
        return True

    def _marcar_divergencia(self, rt: DeviceRuntime, problemas: list[str], motivo: str) -> None:
        problemas.append(motivo)
        if rt.inventory_state == "divergent" and rt.inventory_detail == motivo:
            return                                  # já dito: não repete evento a cada batida
        rt.inventory_state, rt.inventory_detail = "divergent", motivo
        self.bus.emit("log", f"{rt.id}: inventário divergente — {motivo}.", level="warn", instance_id=rt.id)
        self.publish(rt, f"{rt.id}: inventário divergente")

    # ------------------------------------------------------------------ instância dinâmica
    #: Primeira porta local oferecida ao túnel. Segue o que o parque já usa (15555 ↔ 5555), e a alocação pula o
    #: que estiver ocupado — inclusive o que o `config.yaml` declara, que continua valendo.
    TUNEL_PORTA_BASE = 15555

    def portas_de_tunel_em_uso(self) -> set[int]:
        usadas = {int(r["tunnel_port"]) for r in self.db.query(
            "SELECT tunnel_port FROM instances WHERE tunnel_port IS NOT NULL")}
        for endereco in self.cfg.file.instances.external.values():
            _, _, porta = (endereco or "").rpartition(":")
            if porta.isdigit():
                usadas.add(int(porta))
        return usadas

    def alocar_porta_de_tunel(self) -> int:
        usadas = self.portas_de_tunel_em_uso()
        porta = self.TUNEL_PORTA_BASE
        while porta in usadas:
            porta += 1
        return porta

    def adotar_aparelho(self, worker_id: str, *, serial: str, adb_port: int, instance_id: str | None = None,
                        avd_name: str | None = None) -> DeviceRuntime:
        """Um aparelho anunciado por um worker vira instância AGORA — sem editar `config.yaml`, sem reiniciar.

        Era a Etapa 4 do parque distribuído, e sem ela "conectar servidores novos e executar os mesmos comandos"
        significava: editar dois blocos de YAML, reinstalar a tarefa do túnel com o mapa novo, reiniciar o
        backend e fazer um PUT de `worker_id` por instância. A porta local do túnel é alocada por ESTE servidor,
        que é quem conhece as portas já em uso.

        O `DeviceRuntime` entra no dicionário vivo na mesma chamada: o aparelho aparece no painel sem reinício.
        """
        iid = (instance_id or "").strip() or self._proximo_id_dinamico()
        if iid in self.devices:
            raise ValueError(f"a instância '{iid}' já existe")
        c = self.cfg.file.instances
        idx = int(self.db.scalar("SELECT COALESCE(MAX(idx), 0) FROM instances") or 0) + 1
        porta = self.alocar_porta_de_tunel()
        self.db.execute(
            "INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
            " worker_id, external_serial, tunnel_port, remote_adb_port, origin, hosted_by)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,'dynamic',?)",
            (iid, idx, avd_name or serial, c.base_console_port + 2 * (idx - 1), c.base_system_port + (idx - 1),
             c.base_mjpeg_port + (idx - 1), c.base_chromedriver_port + (idx - 1), worker_id,
             # O túnel que alcança este aparelho é DESTE backend: quem o hospeda é quem o adotou (migração 027).
             f"127.0.0.1:{porta}", porta, int(adb_port), self.cfg.owner_id))
        row = self.db.one("SELECT * FROM instances WHERE id=?", (iid,))
        rt = self._runtime(row)
        self.devices[iid] = rt
        self.bus.emit("log", f"{iid}: aparelho {serial} do worker {worker_id} adotado como instância "
                             f"(túnel 127.0.0.1:{porta} → {adb_port}).", instance_id=iid)
        self.publish(rt, f"{iid}: adotado do worker {worker_id}")
        return rt

    def provisionar(self, *, app_id: str | None, android_overrides: dict[str, object],
                    worker_verbs: list[str] | None, requested_by: str) -> DeviceRuntime:
        """Um aparelho NOVO neste servidor, criado pela plataforma (migração 050): a linha e o runtime, agora.

        O AVD não nasce aqui — nasce pelo verbo `create`, pelo caminho de sempre (cerca, outbox, desfecho), que é o
        que torna a criação rastreável e cancelável. Esta função é a parte que faltava ANTES dele: até aqui `create`
        só criava o AVD de instância já declarada no YAML ou adotada de um worker.

        Molde de `adotar_aparelho`: `origin='dynamic'` (o `seed()` recarrega no próximo arranque), id pelo próximo
        livre depois de `instances.count` e idx pelo `MAX(idx) + 1` — ids e portas nunca colidem com os do YAML, nem
        com os de uma instância aposentada (a linha dela fica). `worker_id = hosted_by = owner_id`, como o worker
        local carimba em todo aparelho desta máquina. O que a pessoa pediu de Android vai em `android_overrides`, e
        é `android_de` quem o mescla na criação e no boot.
        """
        sobreposicao = self.conferir_sobreposicao_android(android_overrides)
        c = self.cfg.file.instances
        meu = self.cfg.owner_id
        try:
            with self.db.tx():
                iid = self._proximo_id_dinamico()
                idx = int(self.db.scalar("SELECT COALESCE(MAX(idx), 0) FROM instances") or 0) + 1
                self.db.execute(
                    "INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port,"
                    " chromedriver_port, app_id, worker_id, origin, hosted_by, android_overrides)"
                    " VALUES (?,?,?,?,?,?,?,?,?,'dynamic',?,?)",
                    (iid, idx, iid, c.base_console_port + 2 * (idx - 1), c.base_system_port + (idx - 1),
                     c.base_mjpeg_port + (idx - 1), c.base_chromedriver_port + (idx - 1), app_id, meu, meu,
                     dumps(sobreposicao) if sobreposicao else None))
        except INTEGRITY_ERRORS as exc:
            # Dois pedidos ao mesmo tempo calcularam o mesmo id/idx: o banco recusou o segundo (PK, portas UNIQUE).
            raise ValueError("outro provisionamento acabou de acontecer neste servidor; repita o pedido") from exc
        row = self.db.one("SELECT * FROM instances WHERE id=?", (iid,))
        rt = self._runtime(row)
        # O AVD ainda não existe: é o mesmo estado que `_adopt` dá a uma instância do YAML sem AVD no disco.
        rt.state, rt.state_detail = InstanceState.absent, "provisionado; AVD ainda não criado"
        rt.worker_verbs = worker_verbs
        self.devices[iid] = rt
        self.capacidades_do_avd_local(rt, publicar=False)
        self.bus.emit("log", f"{iid}: aparelho provisionado neste servidor por {requested_by} "
                             f"(porta de console {rt.console_port}"
                             + (f", sobreposição {sorted(sobreposicao)}" if sobreposicao else "") + ").",
                      instance_id=iid)
        self.publish(rt, f"{iid}: provisionado")
        return rt

    def aposentar(self, rt: DeviceRuntime, *, requested_by: str) -> tuple[str, bool]:
        """Aposenta uma instância provisionada (migração 050): apaga o AVD do disco e tira o aparelho do parque.

        A LINHA fica, com `retired_at`: objetivos, comandos e vínculos antigos referenciam o id, e o idx/portas não
        voltam a ser distribuídos (`_proximo_id_dinamico` e `MAX(idx)` continuam vendo a linha). `app_id`,
        `account_label` e `desired_state` são zerados porque há quem conte `instances` por `app_id` sem olhar
        `retired_at` (visão geral de apps) e quem religa pelo estado desejado.

        Só com o emulador PARADO e sem processo vivo: apagar a partição de dados debaixo de um emulador no ar
        corrompe o que ele ainda vai gravar. Quem confere objetivo aberto, vínculo ativo e comando em voo é a rota
        (é ela que conhece a fila e o domínio social); aqui é o aparelho e o disco. Devolve `(retired_at,
        avd_apagado)`. No falso da suíte, o AVD é o par de arquivos que o `create` falso escreveu — apagar é real.
        """
        if rt.state in (InstanceState.booting, InstanceState.online, InstanceState.stopping):
            raise ValueError(f"{rt.id} está '{rt.state.value}': desligue o aparelho antes de aposentá-lo")
        if rt.pid and self.emulator.process_alive(rt.pid, rt.avd_name):
            raise ValueError(f"{rt.id} ainda tem o emulador no ar (pid {rt.pid}): desligue-o antes de aposentá-lo")
        for tarefa in rt.tasks.values():
            tarefa.cancel()
        apagado = self.avd.delete(rt.avd_name)          # `AvdError` sobe: sem o disco limpo não há aposentadoria
        agora = now_iso()
        with self.db.tx():
            self.db.execute("UPDATE instances SET retired_at=?, app_id=NULL, account_label=NULL, desired_state=NULL,"
                            " emulator_pid=NULL WHERE id=?", (agora, rt.id))
        self.devices.pop(rt.id, None)
        rt.executor.shutdown()
        rt.sonda.shutdown()
        self.bus.emit("log", f"{rt.id}: aparelho aposentado por {requested_by}"
                             + (" (AVD apagado do disco)." if apagado else " (não havia AVD no disco)."),
                      instance_id=rt.id)
        # Evento próprio, e não `instance.updated`: este carrega o DTO e o painel o mesclaria de volta na grade.
        self.bus.emit("instance.retired", f"{rt.id}: aposentado", instance_id=rt.id,
                      data={"instance_id": rt.id, "retired_at": agora, "avd_removed": apagado})
        return agora, apagado

    def conferir_sobreposicao_android(self, sobreposicao: dict[str, object]) -> dict[str, object]:
        """A mesma regra que `AppConfigFile._instancias_coerentes` aplica ao YAML: só chaves de `AndroidCfg`, com
        tipo e faixa válidos sobre o padrão desta máquina. Devolve a sobreposição sem os valores nulos."""
        limpa = {k: v for k, v in sobreposicao.items() if v is not None}
        estranhas = sorted(set(limpa) - set(AndroidCfg.model_fields))
        if estranhas:
            raise ValueError(f"chave(s) desconhecida(s) na sobreposição Android: {', '.join(estranhas)}")
        try:
            AndroidCfg.model_validate({**self.cfg.file.android.model_dump(), **limpa})
        except ValueError as exc:
            raise ValueError(f"sobreposição Android inválida: {exc}") from exc
        return limpa

    def apps_de_fundo_de(self, rt: DeviceRuntime) -> tuple[str, ...] | None:
        """A lista de apps de fundo que o preparo mantém desativados NESTE aparelho (`android.desativar_apps`, ver
        `devices/apps_de_fundo.py`), ou `None` para o preparo não tocar em app nenhum.

        O central é o único dono dela, inclusive nos aparelhos dos workers (preparados daqui, pelo túnel, na readoção
        de `_adopt_external`): duas fontes para o mesmo aparelho — o `config.yaml` daqui e o `worker.yaml` de lá —
        desativariam e reativariam o mesmo pacote a cada entrada no ar. O agente do worker não mexe em app.

        - celular físico (`external` sem worker): `None`. O projeto só USA esse aparelho, nunca o reconfigura;
        - a loja: `()`. Não é aparelho da automação: nada se desativa ali, e o que o preparo tinha desativado quando
          ela era aparelho de tarefa volta;
        - os outros: a lista MENOS os pacotes do catálogo de apps. O app alvo nunca é desativado — a carga já recusa o
          que a configuração declara; o que foi cadastrado depois, no banco, sai aqui, e volta se estava desativado.

        Catálogo ilegível (ou lista inválida): `None` — sem saber qual é o alvo, não se desativa nada.
        """
        if rt.external and not rt.worker_id:
            return None
        if rt.store:
            return ()
        try:
            alvos = {str(r["package"]).strip() for r in self.db.query("SELECT package FROM apps")}
            # De novo pelo validador: o override do YAML entra por `model_copy(update=)`, sem validar — a carga só
            # conferiu uma CÓPIA dele, e um " com.google.android.gm" com espaço chegaria cru e viraria "recusado".
            lista = validar_apps_de_fundo(self.android_de(rt).desativar_apps)
        except Exception:  # noqa: BLE001 - sem o catálogo, o preparo segue sem mexer em app nenhum
            log.exception("%s: não deu para montar a lista (catálogo de apps ou configuração); o preparo não mexe "
                          "nos apps de fundo", rt.id)
            return None
        return tuple(p for p in lista if p not in alvos)

    def _registrar_apps_de_fundo(self, rt: DeviceRuntime, ajuste: AjusteDosApps | None) -> None:
        """O que o preparo desativou ou reativou vira evento do aparelho (o registro que o painel e o histórico
        mostram). Passagem sem mudança não publica nada; falha e incerteza já foram ao log por `Adb`."""
        if isinstance(ajuste, AjusteDosApps) and ajuste.mudou:
            self.publish(rt, f"{rt.id}: {ajuste.resumo()}")

    def android_de(self, rt: DeviceRuntime) -> AndroidCfg:
        """Configuração Android EFETIVA deste aparelho: o padrão, o override do YAML e, por cima, o que foi pedido
        ao provisionar (`instances.android_overrides`, migração 050).

        `cfg.instance_android` só conhece o YAML, e o YAML não conhece instância criada em tempo de execução. Todo
        leitor do gerenciador (`create`, o boot, as capacidades declaradas) passa por aqui, para a imagem pedida ser
        a imagem criada — e a mesma no boot seguinte.
        """
        base = self.cfg.instance_android(rt.id)
        if not rt.android_overrides:
            return base
        return AndroidCfg.model_validate({**base.model_dump(), **rt.android_overrides})

    def _proximo_id_dinamico(self) -> str:
        prefixo = self.cfg.file.instances.id_prefix
        existentes = {r["id"] for r in self.db.query("SELECT id FROM instances")}
        i = self.cfg.file.instances.count + 1
        while f"{prefixo}{i:02d}" in existentes:
            i += 1
        return f"{prefixo}{i:02d}"

    def mapa_do_tunel(self, worker_id: str) -> str:
        """`porta-local:porta-no-worker,...` — o mapa que o túnel encaminha para aquela máquina.

        UM lugar só (achado #151): até aqui o mapa vivia nos argumentos da tarefa agendada, e acrescentar um
        aparelho exigia reinstalá-la. O arquivo que `escrever_mapa_do_tunel` grava é relido pelo túnel a cada
        volta do laço, sem `-Instalar` nenhum.
        """
        pares = self.db.query(
            "SELECT tunnel_port, remote_adb_port FROM instances WHERE worker_id=? AND tunnel_port IS NOT NULL"
            " AND remote_adb_port IS NOT NULL ORDER BY tunnel_port", (worker_id,))
        return ",".join(f"{r['tunnel_port']}:{r['remote_adb_port']}" for r in pares)

    def escrever_mapa_do_tunel(self, worker_id: str) -> Path | None:
        """Grava `data/tunnel/<worker>.map`. `None` quando não há nada a encaminhar para aquela máquina."""
        mapa = self.mapa_do_tunel(worker_id)
        if not mapa:
            return None
        destino = self.cfg.data_dir / "tunnel"
        destino.mkdir(parents=True, exist_ok=True)
        arquivo = destino / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', worker_id)}.map"
        arquivo.write_text(mapa + "\n", encoding="utf-8")
        return arquivo

    def capacidades_do_avd_local(self, rt: DeviceRuntime, *, publicar: bool = True) -> None:
        """O que ESTA máquina já sabe sem ligar nada: a imagem configurada e o `config.ini` do AVD.

        Roda no `seed`, antes de qualquer aparelho subir, porque a recusa explicada tem de existir com o parque
        inteiro desligado — que é o estado normal de quem vai agendar uma execução.
        """
        if rt.external:
            return
        # O renderizador PEDIDO também se sabe sem ligar nada (29.11): é o que a recusa de um app que não roda no
        # SwiftShader usa enquanto o emulador não disse o que selecionou.
        rt.renderer_configured = self.android_de(rt).gpu_mode
        campos: dict[str, Any] = dict(capacidades_do_avd(self.cfg.avd_home, rt.avd_name))
        if not campos.get("system_image"):
            # AVD ainda não criado: vale a imagem que a configuração MANDA usar. É declaração, não observação —
            # e é exatamente o que o operador precisa saber antes de criar o AVD e descobrir tarde demais.
            imagem = self.android_de(rt).system_image
            campos.update(kind="emulator", system_image=imagem, **capacidades_da_imagem(imagem))
        self.registrar_capacidades(rt, campos, fonte="o AVD desta máquina", publicar=publicar)

    async def ler_capacidades(self, rt: DeviceRuntime) -> None:
        """Lê do APARELHO o que ele responde sobre si. É a fonte mais confiável, e só existe com ele no ar.

        O perfil (ABI, ABIs, SDK) já era lido a cada instalação e jogado fora (`installer.DeviceProfile`); agora
        ele fica. O GMS vem de `ro.com.google.gmsversion`, que só existe em imagem com Google APIs.
        """
        if self.io_factory is not None or rt.state != InstanceState.online:
            return
        try:
            sdk = await rt.executor.run(rt.adb.getprop, "ro.build.version.sdk", timeout=20, label="api do aparelho")
            abilist = await rt.executor.run(rt.adb.getprop, "ro.product.cpu.abilist", timeout=20, label="abis")
            gms = await rt.executor.run(rt.adb.getprop, "ro.com.google.gmsversion", timeout=20, label="gms")
            # A propriedade só existe nas imagens com a Play Store: a `google_apis` traz o GMS sem ela (medido em
            # 30/09/2026: `com.google.android.gms` instalado e a propriedade vazia em android-02 e android-07). Sem
            # o pacote na conta, todo emulador daqui era "AOSP" e o pré-voo recusava o Outlook, que exige GMS.
            pacote = (await rt.executor.run(rt.adb.is_installed, "com.google.android.gms", timeout=30,
                                            label="gms instalado")) if not (gms or "").strip() else True
        except (DriverError, AdbError) as exc:
            log.info("%s: não foi possível ler as capacidades agora (%s)", rt.id, exc)
            return
        tem_gms = gms_do_aparelho(gms, pacote, externo=rt.external)
        self.registrar_capacidades(rt, {
            "kind": "emulator" if not rt.external else None,
            "api_level": int(sdk) if sdk.strip().isdigit() else None,
            "abis": [a.strip() for a in (abilist or "").split(",") if a.strip()],
            "play_store": tem_gms}, fonte="o próprio aparelho (adb)", observado=True)

    def bind_worker_captura(self, worker_id: str, captura: Any) -> list[str]:
        """Liga (ou desliga, com `None`) a captura na origem dos aparelhos daquele worker. Mesmo desenho de
        `bind_worker_appium`: chamado quando o worker conecta e quando ele sai."""
        mudados = []
        for rt in self.devices.values():
            if rt.worker_id == worker_id and rt.captura_remota is not captura:
                rt.captura_remota = captura
                mudados.append(rt.id)
        return mudados

    def _captura_remota(self, rt: DeviceRuntime) -> Any:
        """O `CapturaNaOrigem` a usar AGORA para este aparelho, ou `None` (o screencap pelo ADB, como sempre).

        Pergunta a cada captura, e não na conexão, porque o que vale é o canal vivo: o worker que reconecta com um
        agente antigo (sem a feature) volta ao ADB na captura seguinte."""
        c = rt.captura_remota
        return c if c is not None and rt.worker_id and c.disponivel(rt.worker_id) else None

    async def _capturar_na_origem(self, rt: DeviceRuntime, remota: Any, *, origem: str, timeout: float, label: str,
                                  previa: bool = False, cheia: bool = False, lado_max: int | None = None,
                                  so_dimensoes: bool = False) -> _Codificado:
        """A imagem pedida ao AGENTE (screencap e codificação na máquina dele), DENTRO do executor do aparelho.

        Dentro do executor, e não ao lado dele: a exclusividade do aparelho é a mesma de um screencap pelo ADB —
        nada de trilha paralela que capture no meio de uma ação. A thread do executor fica ocupada esperando o
        pedido, que corre no laço (canal de comando + canal de mídia). O prazo do pedido é um pouco menor que o do
        executor, para o desfecho ser o do pedido (`ErroDeCaptura`) e não o `DriverTimeout` genérico.

        O que volta passa pelas MESMAS guardas de um screencap local (quem chama confere trecho sensível, geração
        e classificação antes de usar ou publicar). Falha é `DriverError` sem efeito — igual a um screencap pelo
        túnel que não respondeu —, nunca uma volta silenciosa ao ADB no meio do pedido."""
        from ..workers.captura import ErroDeCaptura, SemCapturaNaOrigem  # noqa: PLC0415 - devices não depende de workers

        laco = asyncio.get_running_loop()
        interno = max(1.0, timeout - 2.0)

        def pedir() -> tuple[Any, float]:
            t0 = time.perf_counter()
            fut = asyncio.run_coroutine_threadsafe(
                remota.capturar(rt.worker_id or "", rt.id, rt.serial, timeout=interno, previa=previa, cheia=cheia,
                                lado_max=lado_max, so_dimensoes=so_dimensoes), laco)
            try:
                midia = fut.result(timeout=interno + 1.0)
            except concurrent.futures.TimeoutError:
                fut.cancel()
                raise ErroDeCaptura(f"o worker não entregou a imagem em {interno:.0f} s") from None
            return midia, (time.perf_counter() - t0) * 1000

        try:
            midia, ms = await rt.executor.run(pedir, timeout=timeout, label=f"{label} (na origem)",
                                              previa=origem == "previa")
        except (ErroDeCaptura, SemCapturaNaOrigem) as exc:
            # `FalhaDeLeitura`: a imagem é do agente, não do Appium — a sessão do aparelho não se recria por ela.
            raise FalhaDeLeitura(f"captura na origem falhou: {exc}") from exc
        if not so_dimensoes:
            metricas.observar("captura.ms", ms, origem=origem, via="worker", instancia=rt.id)
            metricas.observar("captura.bytes", midia.bytes_recebidos, origem=origem, via="worker")
        return _Codificado(largura=midia.largura, altura=midia.altura, cheia=midia.cheia, miniatura=midia.miniatura,
                           modelo=midia.modelo, ms=dict(midia.ms))

    def bind_worker_appium(self, worker_id: str, *, appium_mode: str, appium_url: str | None,
                           devices: list[Any]) -> list[str]:
        """`appium: local` deixa de ser só declaração: o aparelho daquele worker passa a ser dirigido pelo Appium DELE.

        Era o achado #12 — um worker que declarasse `appium: local` era aceito, aparecia na Infraestrutura com
        esse modo e continuava sendo dirigido pelo Appium central pelo túnel. Para worker em WAN (latência de ADB
        desconhecida) esta era a saída prevista, e a decisão 2 do plano é "Appium configurável por worker".

        O `udid` vem do inventário do worker, não de `rt.serial`: aqui o serial é o do túnel, e o Appium da outra
        máquina não conhece esse endereço — lá o aparelho é `emulator-55xx`.

        `appium_mode != "local"` (ou o worker desconectando) devolve o aparelho ao Appium deste servidor, que é o
        caminho provado em campo.
        """
        local = appium_mode == "local" and bool(appium_url)
        seriais = {d.instance_id: d.serial for d in (devices or []) if getattr(d, "instance_id", None)}
        mudados: list[str] = []
        for rt in self.devices.values():
            if rt.worker_id != worker_id:
                continue
            url = appium_url if local else None
            serial = seriais.get(rt.id) if local else rt.serial
            if serial is None and local:
                # O worker declarou Appium local mas não disse por qual serial ELE enxerga este aparelho: sem o
                # udid não há sessão a abrir, e inventar um mandaria o Appium dele procurar o que não existe.
                log.warning("%s: worker %s declarou appium local sem serial para este aparelho; segue no central",
                            rt.id, worker_id)
                url = None
                serial = rt.serial
            if rt.session.apontar_para(url, serial):
                self.invalidate_automation(rt, "o Appium que dirige este aparelho mudou")
                mudados.append(rt.id)
                self.publish(rt)
        return mudados

    def set_desired_state(self, rt: DeviceRuntime, desired: str | None) -> None:
        """Registra a DECISÃO sobre o aparelho, que é diferente do que se observa nele.

        Sobrevive a reinício de propósito: sem isso, o monitor voltava a ligar (ou a readotar) um aparelho que
        alguém tinha mandado parar — e o "Parar" era desfeito em ≤36 s, sem aviso.
        """
        if rt.desired_state == desired:
            return
        rt.desired_state = desired
        self.db.execute("UPDATE instances SET desired_state=? WHERE id=?", (desired, rt.id))

    def _guard_not_running_ai(self, rt: DeviceRuntime) -> None:
        if rt.control == ControlOwner.ai:
            raise InstanceBusy("A IA está executando neste aparelho. Pause/cancele a execução ou assuma o controle antes.")

    async def create(self, rt: DeviceRuntime) -> None:
        async with rt.op_lock:
            if self.avd.exists(rt.avd_name):
                return
            self._set_state(rt, InstanceState.stopped, "criando AVD…")
            try:
                await asyncio.to_thread(self.avd.create, rt.avd_name, self.android_de(rt))
            except (AvdError, OSError) as exc:
                # A falha SOBE. Engolir aqui fazia o comando do painel virar `succeeded` com o AVD inexistente:
                # o estado do aparelho dizia `absent` e o histórico dizia "criado". Quem chamou decide o desfecho.
                self._set_state(rt, InstanceState.absent, str(exc), level="error", attention=str(exc))
                raise
            self._set_state(rt, InstanceState.stopped, "AVD criado")

    async def start_instance(self, rt: DeviceRuntime) -> None:
        # A decisão é registrada mesmo quando não há nada a fazer: "eu quero este aparelho no ar" vale como
        # instrução ao monitor, e é o que autoriza a readoção automática mais tarde.
        self.set_desired_state(rt, InstanceState.online.value)
        if rt.state in (InstanceState.online, InstanceState.booting, InstanceState.stopping):
            return
        if rt.external:                        # "Iniciar" um aparelho externo = tentar (re)conectar; nada é ligado
            await self._adopt_external(rt)
            return
        rt.state, rt.state_detail = InstanceState.booting, "na fila de inicialização"
        self.publish(rt)
        rt.tasks["boot"] = asyncio.create_task(self._boot(rt), name=f"boot-{rt.id}")

    async def aguardar_boot(self, rt: DeviceRuntime, prazo_s: float) -> tuple[str, str | None]:
        """Espera o boot enfileirado chegar a um desfecho: `("online" | "failed" | "uncertain", detalhe)`.

        Existe porque `start_instance` apenas ENFILEIRA o boot e volta. Sem esta espera, o comando do painel
        virava `succeeded` em 2 ms — antes da guarda de RAM recusar, antes de o emulador subir, antes de o
        Android existir. O caminho do worker já esperava (o agente só responde depois do boot, ou `uncertain` no
        prazo); agora o mesmo verbo significa a mesma coisa nas duas máquinas.

        O prazo estourado NUNCA cancela o boot: é a resposta de quem espera, não uma ordem de desistir. O
        aparelho pode ficar pronto depois — e o comando fica `uncertain`, que é a verdade.
        """
        tarefa = rt.tasks.get("boot")
        if tarefa is not None and not tarefa.done():
            await asyncio.wait({tarefa}, timeout=prazo_s)
            if not tarefa.done():
                return "uncertain", (f"o aparelho não completou o boot em {prazo_s:.0f} s; o boot continua em "
                                     "andamento nesta máquina e nada será repetido automaticamente")
        if rt.state == InstanceState.online:
            return "online", rt.state_detail
        if rt.state in (InstanceState.booting, InstanceState.stopping):
            # O boot acabou e o aparelho não está nem no ar nem parado: alguém mexeu por fora (rodízio, parada).
            return "uncertain", rt.state_detail or "o boot terminou sem dizer o desfecho"
        return "failed", rt.state_detail or f"o aparelho terminou em '{rt.state.value}', e não online"

    def _recusa_por_capacidade(self, rt: DeviceRuntime, a: Any) -> str | None:
        """A guarda de RAM do host, isolada da subida do emulador (achado #165).

        Vive fora de `_boot` porque **ela vale para os dois caminhos**: com emulador de verdade e com o aparelho
        falso da suíte. Enquanto morava depois do `if self.io_factory`, nenhum teste passava por ela — o teste de
        recusa escrevia a própria frase da recusa e comparava com ela mesma. A memória livre vem de
        `self.emulator`, que em teste é um valor escolhido, não a RAM desta máquina.

        Devolve o motivo da recusa (e já aplica estado, espera crescente e medição), ou `None` quando cabe.
        """
        est = a.est_ram_host_mb()
        self._vencer_reservas_orfas()
        # O que os OUTROS boots desta máquina ainda vão alocar: a reserva de quem foi admitido (tenha ou não PID
        # ainda) e, para quem está bootando com PID sem reserva (adotado, ou de antes deste processo), a
        # estimativa — cada um menos o RSS que o processo já tem, que a RAM livre já descontou. Uma conta só: o
        # aparelho reservado que ganhou PID não é contado duas vezes.
        inflight = 0.0
        for d in self.devices.values():
            if d is rt:
                continue
            reserva = self._reservas.get(d.id)
            if reserva is None and not (d.pid and d.state == InstanceState.booting):
                continue
            custo = reserva if reserva is not None else est
            rss = (d.resources.rss_mb if d.resources else 0) or 0
            if d.id in self._reservas_orfas:
                rss = max(rss, self._reservas_orfas[d.id])
                self._reservas_orfas[d.id] = rss
            inflight += max(0.0, custo - rss)
        free_mb = self.emulator.free_ram_mb()
        after = free_mb - inflight - est
        if after >= a.min_free_ram_mb_after_boot:
            # Admitido: a reserva entra AGORA, antes de qualquer `await` de quem chamou. O próximo `_boot` que
            # passar por aqui (outra vaga do `boot_limiter`) já a desconta.
            self._reservas[rt.id] = est
            self._reservas_ate[rt.id] = time.monotonic() + max(a.boot_timeout_s, a.wake_timeout_s)
            self._reservas_orfas.pop(rt.id, None)
            metricas.contar("capacidade.reserva", resultado="concedida")
            return None
        metricas.contar("capacidade.reserva", resultado="recusada", motivo="ram")
        online = sum(1 for d in self.devices.values() if d.state == InstanceState.online)
        msg = (f"Capacidade do host atingida: {free_mb:.0f} MB disponíveis"
               + (f" (−{inflight:.0f} MB reservados para boots em andamento)" if inflight else "")
               + f"; esta instância precisa de ≈{est} MB e o host deve manter {a.min_free_ram_mb_after_boot} MB "
               f"livres. {online} instância(s) online. Libere memória no host ou use uma imagem mais leve.")
        rt.start_refusals += 1          # o rodízio não insiste a cada tick: espera crescente
        rt.start_backoff_until = time.monotonic() + min(120, 15 * 2 ** (rt.start_refusals - 1))
        back = InstanceState.hibernated if rt.snapshot_valid else InstanceState.stopped
        self._set_state(rt, back, msg, level="warn", attention=msg)      # o snapshot continua válido
        self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "capacity", dumps({
            "instance_id": rt.id, "refused": True, "online": online, "mem_available_mb": round(free_mb),
            "inflight_reserved_mb": round(inflight), "needed_mb": est})))
        return msg

    def _vencer_reservas_orfas(self) -> None:
        """Solta a reserva órfã cujo emulador já se sabe — sem PID, processo morto, online (contado na RAM) — ou
        cujo prazo venceu: passado o prazo do boot, o que o processo tinha a alocar já alocou (igual ao agente)."""
        agora = time.monotonic()
        for iid in list(self._reservas_orfas):
            d = self.devices.get(iid)
            if (d is None or not d.pid or d.state == InstanceState.online
                    or agora >= self._reservas_ate.get(iid, agora)
                    or not self.emulator.process_alive(d.pid, d.avd_name)):
                self._largar_reserva(iid)

    def _largar_reserva(self, instance_id: str) -> None:
        self._reservas.pop(instance_id, None)
        self._reservas_ate.pop(instance_id, None)
        self._reservas_orfas.pop(instance_id, None)

    def _soltar_reserva(self, rt: DeviceRuntime) -> None:
        """Fim do `_boot` — sucesso, falha, cancelamento ou prazo. A reserva sai, a menos que o emulador possa ter
        ficado no ar sem estar contado (PID vivo e o aparelho não online): aí ela fica ÓRFÃ, com o RSS que o
        processo já tem, e quem a solta é `_vencer_reservas_orfas`."""
        if rt.id not in self._reservas:
            return
        if rt.pid and rt.state != InstanceState.online and self.emulator.process_alive(rt.pid, rt.avd_name):
            self._reservas_orfas[rt.id] = float((rt.resources.rss_mb if rt.resources else 0) or 0)
            return
        self._largar_reserva(rt.id)

    async def _boot(self, rt: DeviceRuntime) -> None:
        a = self.android_de(rt)
        if self.io_factory is not None:       # testes: "boot" do aparelho falso
            async with rt.op_lock:
                async with self.boot_limiter:
                    # A MESMA guarda do caminho real, sob os MESMOS bloqueios (achado #165): o aparelho falso
                    # deixa de pular a decisão de capacidade, e quem escolhe a memória livre é o backend injetado.
                    if self._recusa_por_capacidade(rt, a) is not None:
                        return
                    try:
                        warm = rt.snapshot_valid
                        wipe, rt.wipe_next_boot = rt.wipe_next_boot, False
                        rt.fresh_data = wipe
                        self._set_snapshot(rt, False)
                        await asyncio.sleep(getattr(self, "fake_wake_s" if warm else "fake_boot_s", 0.05))
                        # T.2 (fatia que faltava do achado #165): só agora, com o "boot" simulado concluído, o
                        # aparelho falso ganha PID — pela MESMA `_spawn` do caminho real. Fazer isto ANTES do sono
                        # quebraria `test_cancelar_boot_local_interrompe_a_tarefa...`, que cancela em pleno boot e
                        # prova "nada ficou no ar" checando `rt.pid is None`. Só depois de terminado é que
                        # `stop_instance` passa a ver um PID — o que é o que torna a elegibilidade de hibernação
                        # (`rt.pid is not None`) exercitável pelo aparelho falso.
                        await asyncio.to_thread(self._spawn, rt, a, wipe, warm)
                        if wipe:
                            self._disco_apagado(rt, "o emulador subiu com os dados apagados (reset)")
                        self.boots.append((rt.id, "warm" if warm else "cold"))
                        await self._registrar_renderizador(rt)
                        rt.automation = AutomationInfo(state="ready", detail="driver de teste")
                        self._set_state(rt, InstanceState.online, "pronto (teste)")
                        self.on_device_free()
                    finally:
                        self._soltar_reserva(rt)
            return
        async with rt.op_lock:
            try:
                async with self.boot_limiter:
                    # Guarda de capacidade: memória disponível AGORA, menos o que os boots em andamento ainda
                    # vão alocar, precisa comportar esta instância e deixar uma folga para o host.
                    if self._recusa_por_capacidade(rt, a) is not None:
                        return
                    created = not self.avd.exists(rt.avd_name)
                    if created:
                        self._set_state(rt, InstanceState.booting, "criando AVD…")
                        await asyncio.to_thread(self.avd.create, rt.avd_name, a)
                    else:
                        await asyncio.to_thread(self.avd.apply_hardware, rt.avd_name, a)
                    t0 = time.monotonic()
                    wipe, rt.wipe_next_boot = rt.wipe_next_boot, False
                    rt.fresh_data = created or wipe
                    warm = (a.hibernation and rt.snapshot_valid and not wipe and rt.snapshot_hw == _hw_signature(a)
                            and self._snapshot_dir(rt).exists())
                    # Snapshot é de USO ÚNICO: o flag cai ANTES do spawn. Se o processo morrer no meio, o próximo boot
                    # é a frio — carregar de novo um snapshot já usado reverteria o disco (logins, mensagens).
                    self._set_snapshot(rt, False)
                    if not warm:
                        await asyncio.to_thread(self._discard_snapshot, rt)
                    log_path = self.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
                    rt.boot_log_offset = log_path.stat().st_size if log_path.exists() else 0
                    await asyncio.to_thread(self._spawn, rt, a, wipe, warm)
                    if wipe:
                        self._disco_apagado(rt, "o emulador subiu com os dados apagados (reset)")
                    self._prontidao(rt, "process_running", "emulador iniciado")
                    self._set_state(rt, InstanceState.booting, "acordando do snapshot…" if warm else
                                    "emulador iniciado" + (" (dados apagados)" if wipe else ""))
                    ok = await self._wait_boot(rt, t0, warm=warm)
                    # Parar no diálogo de relatório de falha (29.55) não diz nada do snapshot: ele fica.
                    if warm and not ok and not rt.bloqueio_de_crash:  # snapshot corrompido/incompatível: descarta e tenta UMA vez a frio
                        log.warning("%s: acordar do snapshot falhou; boot a frio", rt.id)
                        await asyncio.to_thread(self.emulator.stop_process, rt.adb, rt.pid, rt.avd_name)
                        self._save_pid(rt, None)
                        await asyncio.to_thread(self._discard_snapshot, rt)
                        t0 = time.monotonic()
                        await asyncio.to_thread(self._spawn, rt, a, False, False)
                        self._set_state(rt, InstanceState.booting, "snapshot descartado; iniciando a frio")
                        await self._wait_boot(rt, t0)
            except (AvdError, emu.EmulatorError, OSError) as exc:
                self._set_state(rt, InstanceState.error, str(exc), level="error", attention=str(exc))
            finally:
                # Qualquer saída depois da guarda — online, erro, prazo, cancelamento. Recusado não tem reserva.
                self._soltar_reserva(rt)

    async def _wait_boot(self, rt: DeviceRuntime, t0: float, *, adopted: bool = False, warm: bool = False,
                         espera_inicial_s: float = 0.0) -> bool:
        if espera_inicial_s > 0:
            # Tentativa anterior incerta no mesmo guest (`_adopt`): espaça a próxima, e o prazo de boot só começa a
            # contar depois do espaçamento.
            await asyncio.sleep(espera_inicial_s)
            t0 = time.monotonic()
        a_cfg = self.android_de(rt)
        # RA-15 (29.34, 03/10/2026): dois relógios. `t0` (o spawn) segue valendo para `boot_seconds` — spawn→online, comparável
        # com o histórico. O PRAZO tem início próprio (`inicio_prazo`). Wake: ANTES do veredito do log o prazo é o de boot
        # a frio, contado do spawn — carregar 2 GB devagar sob CPU alta ainda é mais rápido que descartar o snapshot e
        # bootar a frio (medido: 4 de 23 wakes passavam de 90 s só carregando, e o relógio de 90 s desde o spawn os
        # derrubava para o boot a frio de minutos). Só com o snapshot CARREGADO começa o `wake_timeout_s`, contado dali.
        timeout = a_cfg.boot_timeout_s
        inicio_prazo = t0
        load_s: float | None = None                # spawn → snapshot carregado (só wake que chegou ao veredito True)
        phase = "aguardando o Android iniciar"
        booted_at: float | None = None
        checked_load = False
        while True:
            agora = time.monotonic()
            elapsed = agora - t0
            if warm and not checked_load:
                # O emulador decide sozinho se carrega o snapshot e, se não carregar, segue em boot a frio (medido:
                # hardware diferente do salvo → "cannot load snapshot"). Só o LOG diz qual dos dois aconteceu; com a
                # máquina carregada a linha pode demorar, então ausência de linha não é veredito.
                verdict = self._snapshot_verdict(rt)
                if verdict is True:
                    checked_load = True
                    rt.snapshot_failures = 0
                    # É a hora em que o log foi LIDO (resolução de `boot_poll_s`), não o carimbo do emulador: o erro
                    # é de até um poll para mais, e favorece quem carregou.
                    load_s = agora - t0
                    inicio_prazo, timeout = agora, a_cfg.wake_timeout_s
                elif verdict is False:
                    checked_load = True
                    warm, timeout = False, a_cfg.boot_timeout_s
                    rt.snapshot_failures += 1
                    rt.snapshot_unsupported = rt.snapshot_failures >= 2
                    rt.state_detail = "o emulador recusou o snapshot; boot a frio"
                    self.bus.emit("log", f"{rt.id}: o emulador recusou o snapshot; seguindo em boot a frio"
                                  + (" — este AVD deixa de hibernar" if rt.snapshot_unsupported else ""),
                                  level="warn", instance_id=rt.id)
            if agora - inicio_prazo > timeout:
                if warm:
                    return False                   # quem chamou descarta o snapshot e tenta a frio
                self._set_state(rt, InstanceState.error, f"Boot excedeu {timeout}s", level="error",
                                attention="O boot não concluiu a tempo. Veja data/logs/emulator-%s.log" % rt.avd_name)
                return False
            if emu.dialogo_de_crash(self.cfg.logs_dir / f"emulator-{rt.avd_name}.log", rt.boot_log_offset):
                await self._parou_no_dialogo_de_crash(rt)
                return False
            if rt.pid and not emu.is_our_emulator(rt.pid, rt.avd_name):
                if warm:
                    return False
                tail = emu.read_log_tail(self.cfg.logs_dir / f"emulator-{rt.avd_name}.log", 600)
                self._save_pid(rt, None)
                self._set_state(rt, InstanceState.error, "O emulador encerrou durante o boot.", level="error",
                                attention=f"Emulador encerrou no boot. Final do log: {tail[-300:]}")
                return False
            try:
                if booted_at is None:
                    if await rt.executor.run(rt.adb.boot_completed, timeout=15, label="boot_completed"):
                        booted_at = time.monotonic()
                        self._prontidao(rt, "boot_completed", "boot concluído; aguardando a interface")
                        phase = "Android iniciado; aguardando a interface"
                elif await rt.executor.run(rt.adb.ui_ready, timeout=15, label="ui_ready") or \
                        (time.monotonic() - booted_at) > 120:
                    break
            except (DriverError, AdbError):
                pass
            if int(elapsed) % 10 < 2:
                rt.state_detail = f"{phase} ({elapsed:.0f}s)"
                self.publish(rt)
            await asyncio.sleep(self.cfg.file.limits.boot_poll_s)  # T.2 (achado #164): era `sleep(2)` fixo
        # ANDROID_RESPONSIVE antes de READY. `boot_completed` e a interface não bastam: o `ui_ready` que estoura
        # prazo cai no "120 s depois do boot" acima, e um framework congelado passava direto para `online`.
        # Orçamento dentro do prazo de boot/wake que já corre (piso RESPOSTA_MIN_S, teto RESPOSTA_POS_BOOT_S): três
        # sondas lentas não transformam um wake de 90 s em minutos.
        def fim_do_prazo() -> float:               # o prazo de boot/wake que já corre, com o mesmo piso da escada
            return time.monotonic() + max(RESPOSTA_MIN_S, timeout - (time.monotonic() - inicio_prazo))
        p: prontidao.Prontidao | None = None
        rt.adb.apps_de_fundo = self.apps_de_fundo_de(rt)
        try:
            ajuste = await rt.executor.run(rt.adb.prepare_for_automation, timeout=PRAZO_DO_PREPARO_S, label="prepare")
        except (AdbTimeout, DriverTimeout) as exc:
            # Efeito incerto no aparelho (e, se foi o executor, a chamada ainda viva): nenhuma escada nesta tentativa
            # prova ser posterior a ele. Wake cai no boot a frio; a frio vira `error` com a escada de reparo.
            p = await self._tentativa_incerta(rt, exc, "o preparo", fim_do_prazo())
        except (DriverError, AdbError) as exc:
            # Erro rápido: retorno conhecido, e a escada abaixo, que decide, vem DEPOIS dele.
            log.warning("%s: preparo pós-boot falhou: %s", rt.id, exc)
        else:
            self._registrar_apps_de_fundo(rt, ajuste)
        # Calculado DEPOIS do preparo: o que ele (e a espera pelo zumbi) gastou sai do orçamento da escada.
        orcamento = max(RESPOSTA_MIN_S, min(RESPOSTA_POS_BOOT_S, timeout - (time.monotonic() - inicio_prazo)))
        if p is None:
            p = await self._esperar_prontidao(rt, orcamento)
        # O relógio NÃO entra aqui (K-031). O `cmd alarm set-time` leva um instante absoluto: estourado, cai atrasado
        # e ATRASA o convidado — e, no portão, um estouro dele fazia o wake devolver False e `_boot` DESCARTAR o
        # snapshot de um aparelho bom (revisão pós-merge do PR #7, achado 1). O relógio é condição própria, conferida
        # depois de entrar no ar (`_arrumar_depois_de_entrar`) e de novo periodicamente
        # (`conferir_relogio_do_convidado`).
        estado = p.estado
        if estado != "ok":
            motivo = ("o Android subiu, mas os serviços do sistema não existem (system_server)" if estado == "morto"
                      else f"a prontidão não pôde ser avaliada ({p.detalhe()})" if estado == "erro"
                      else f"o Android subiu, mas não ficou pronto em {orcamento:.0f} s ({p.detalhe()})")
            self._prontidao(rt, "boot_completed", motivo)
            if warm:
                return False                       # quem chamou descarta o snapshot e tenta UMA vez a frio
            self._set_state(rt, InstanceState.error, motivo, level="error",
                            attention=f"{motivo[:1].upper()}{motivo[1:]}. Reinicie o aparelho.")
            return False
        self._prontidao(rt, "android_responsive", p.detalhe())
        if warm:
            self.invalidate_automation(rt, "acordou de snapshot")
        rt.boot_seconds = round(time.monotonic() - t0, 1)
        if not adopted:
            self.db.execute("UPDATE instances SET boot_seconds=? WHERE id=?", (rt.boot_seconds, rt.id))
            vm = psutil.virtual_memory()
            medida = {
                "instance_id": rt.id, "boot_seconds": rt.boot_seconds, "kind": "warm" if warm else "cold",
                "online_after": sum(1 for d in self.devices.values() if d.state == InstanceState.online) + 1,
                "mem_available_gb": round(vm.available / 2**30, 1), "image": self.android_de(rt).system_image}
            if warm:                               # RA-15: quanto levou carregar o snapshot (None = o log não disse)
                medida["load_ms"] = None if load_s is None else int(round(load_s * 1000))
            self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)",
                            (now_iso(), "boot", dumps(medida)))
        # Antes de declarar `online`: o DTO que anuncia a entrada no ar já sai com o renderizador (e com o aviso).
        await self._registrar_renderizador(rt)
        self._set_state(rt, InstanceState.online, f"{'acordou' if warm else 'pronto'} em {rt.boot_seconds:.0f}s")
        self._start_online_tasks(rt)
        self.on_device_free()
        return True

    def _start_online_tasks(self, rt: DeviceRuntime) -> None:
        if "capture" not in rt.tasks or rt.tasks["capture"].done():
            rt.tasks["capture"] = asyncio.create_task(self._capture_loop(rt), name=f"capture-{rt.id}")
        # Com o aparelho no ar, ele mesmo é a melhor fonte sobre si (nível de API, ABIs, GMS). Em tarefa própria
        # porque são três `getprop` e nada disto pode atrasar a captura nem a sessão de automação.
        if "capabilities" not in rt.tasks or rt.tasks["capabilities"].done():
            rt.tasks["capabilities"] = asyncio.create_task(self.ler_capacidades(rt), name=f"caps-{rt.id}")
        # Quem é o aparelho por trás deste id? Antes de confiar em qualquer coisa que o central AFIRME sobre o
        # disco dele (app instalado, sessão, conta), pergunta-se a ele se ainda é o mesmo de antes.
        if "identity" not in rt.tasks or rt.tasks["identity"].done():
            rt.tasks["identity"] = asyncio.create_task(self.reconhecer_aparelho(rt), name=f"identity-{rt.id}")
        # Dado velho sobre o disco: quem sabe reobservar é a camada de releases, que se inscreve aqui.
        try:
            self.on_device_online(rt.id)
        except Exception:  # noqa: BLE001 - reobservar nunca pode impedir o aparelho de entrar no ar
            log.exception("%s: falha ao agendar a reobservação do estado do app", rt.id)
        # A conferência de ENTRADA do relógio é a da arrumação abaixo; a periódica do monitor conta daqui.
        rt.clock_checked_mono = time.monotonic()
        if rt.store:
            # A loja não é automatizada: só copiamos o pacote dela por adb. Abrir sessão instalaria o servidor
            # UiAutomator2 numa imagem com Play Protect, sem ganho nenhum. A captura de tela segue por adb. A
            # arrumação vale para ela também: a Play Store precisa do relógio certo.
            if "arrumacao" not in rt.tasks or rt.tasks["arrumacao"].done():
                rt.tasks["arrumacao"] = asyncio.create_task(self._arrumar_depois_de_entrar(rt),
                                                            name=f"arrumacao-{rt.id}")
            return
        if "automation" not in rt.tasks or rt.tasks["automation"].done():
            rt.tasks["automation"] = asyncio.create_task(self._automacao_depois_de_arrumar(rt),
                                                         name=f"automation-{rt.id}")

    async def _automacao_depois_de_arrumar(self, rt: DeviceRuntime) -> bool:
        """Arrumação de entrada ANTES da sessão de automação: o `uiautomator dump` da dispensa do diálogo, com a
        sessão do UiAutomator2 já aberta, derruba a sessão."""
        await self._arrumar_depois_de_entrar(rt)
        return await self.ensure_automation(rt)

    async def _arrumar_depois_de_entrar(self, rt: DeviceRuntime) -> None:
        """O que o aparelho precisa logo depois de entrar no ar e que NÃO é prontidão (K-031): dispensar um diálogo de
        sistema que já estava na tela e conferir o relógio. Os dois são efeitos não idempotentes — por isso saíram do
        portão. Aqui, falha ou estouro deles não muda `state` nem `readiness`: vira log (diálogo) ou a condição
        própria do relógio, e a reconferência periódica cuida do que tiver caído atrasado."""
        if self.io_factory is not None:           # testes: aparelho falso, sem adb real
            return
        try:
            await self._dispensar_dialogo_de_entrada(rt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - arrumação nunca derruba a entrada no ar
            log.exception("%s: falha ao conferir o diálogo de sistema na entrada", rt.id)
        try:
            await self.conferir_relogio_do_convidado(rt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("%s: falha ao conferir o relógio na entrada", rt.id)

    async def _dispensar_dialogo_de_entrada(self, rt: DeviceRuntime) -> None:
        """O ANR do SystemUI no primeiro boot de um AVD novo fica na tela (o `hide_error_dialogs` do preparo só vale
        para os PRÓXIMOS) e trava a abertura de app. Antes era dispensado no preparo, dentro do portão; agora só
        depois da prontidão, com a confirmação do mesmo diálogo na mesma chamada do toque (`Adb.dismiss_system_dialog`).
        Ler é na trilha de sonda (só leitura); tocar, na fila do aparelho."""
        try:
            descricao = await rt.sonda.run(rt.adb.system_dialog, timeout=20, label="diálogo do sistema")
        except (DriverError, AdbError):
            return                                 # não deu para ler: não se toca em nada
        if not descricao:
            return
        try:
            tocado = await rt.executor.run(rt.adb.dismiss_system_dialog, timeout=60, label="dispensar diálogo")
        except (DriverTimeout, AdbTimeout) as exc:
            # Efeito incerto (o toque pode cair atrasado), mas FORA do portão: o aparelho segue no ar; quem abrir app
            # em seguida confere o foco (`launch_probe`/`wait_for_focus`).
            log.warning("%s: dispensar o diálogo '%s' estourou o prazo (%s); efeito incerto, fora da prontidão",
                        rt.id, descricao[:80], exc)
            return
        except (DriverError, AdbError) as exc:
            log.warning("%s: dispensar o diálogo '%s' falhou (%s)", rt.id, descricao[:80], exc)
            return
        if tocado:
            self.publish(rt, f"{rt.id}: diálogo do sistema dispensado ao entrar no ar ('{descricao[:80]}' → {tocado})")

    async def _parou_no_dialogo_de_crash(self, rt: DeviceRuntime) -> None:
        """29.55 (c): a subida parou no diálogo de consentimento de um relatório de falha pendente. Sem janela, ninguém
        responde, e esperar o prazo do boot (8 min) só para cair na escada de reparo era o incidente de 03/10 19:00Z.
        Encerra o lançador preso, diz o motivo e marca o aparelho para a escada não subir degrau por isso. A próxima
        subida tira o dump do caminho (b) antes do `Popen`."""
        rt.bloqueio_de_crash = True
        await asyncio.to_thread(self.emulator.stop_process, rt.adb, rt.pid, rt.avd_name)
        self._save_pid(rt, None)
        self._set_state(rt, InstanceState.error, "O emulador parou no diálogo de relatório de falha.", level="error",
                        attention="A subida parou no diálogo de consentimento de um relatório de falha pendente do "
                                  "emulador, e ninguém está ali para responder. O processo foi encerrado, e o reparo "
                                  "automático não sobe degrau por isso. Ligue de novo: a próxima subida tira o "
                                  f"relatório do caminho. Log: data/logs/emulator-{rt.avd_name}.log")

    def _spawn(self, rt: DeviceRuntime, a: Any, wipe: bool, from_snapshot: bool = False) -> None:
        """Inicia o emulador e grava o PID na MESMA seção crítica: um cancelamento nunca deixa processo órfão."""
        rt.bloqueio_de_crash = False
        def ao_quarentenar(movidos: list[Path]) -> None:
            # 29.55 (b): relatório de falha pendente faria a subida parar no diálogo de consentimento.
            self.bus.emit("log", f"{rt.id}: {len(movidos)} relatório(s) de falha pendente(s) do emulador foram para a "
                          f"quarentena antes da subida ({', '.join(p.name for p in movidos)})", level="warn",
                          instance_id=rt.id, data={"quarentena": [str(p) for p in movidos]})

        with rt.spawn_lock:
            pid = self.emulator.start_process(self.cfg, self.tools, rt.avd_name, rt.console_port, a,
                                              wipe_data=wipe, from_snapshot=from_snapshot,
                                              ao_quarentenar=ao_quarentenar)
            self._save_pid(rt, pid)

    # ------------------------------------------------------------------ snapshot (hibernação)
    def _snapshot_dir(self, rt: DeviceRuntime) -> Path:
        return self.cfg.avd_home / f"{rt.avd_name}.avd" / "snapshots" / emu.SNAPSHOT_NAME

    def _snapshot_verdict(self, rt: DeviceRuntime) -> bool | None:
        """True = snapshot carregado · False = recusado pelo emulador · None = o log ainda não disse."""
        path = self.cfg.logs_dir / f"emulator-{rt.avd_name}.log"
        try:
            with path.open("rb") as fh:
                fh.seek(rt.boot_log_offset)
                text = fh.read(400_000).decode("utf-8", errors="replace")
        except OSError:
            return None
        if "Successfully loaded snapshot" in text:
            return True
        if "cannot load snapshot" in text or "Failed to load snapshot" in text:
            return False
        return None

    def _discard_snapshot(self, rt: DeviceRuntime) -> None:
        self.emulator.discard_snapshot(self._snapshot_dir(rt))

    def _set_snapshot(self, rt: DeviceRuntime, valid: bool, hw: str | None = None) -> None:
        rt.snapshot_valid, rt.snapshot_hw = valid, (hw if valid else None)
        self.db.execute("UPDATE instances SET snapshot_valid=?, snapshot_hw=?, hibernated_at=? WHERE id=?",
                        (int(valid), rt.snapshot_hw, now_iso() if valid else None, rt.id))

    async def stop_instance(self, rt: DeviceRuntime, *, force: bool = False, hibernate: bool = False) -> None:
        """`hibernate=True` (com `android.hibernation`) salva um snapshot antes de desligar: o próximo start acorda em
        segundos. Se o snapshot não puder ser salvo com certeza, o aparelho apenas desliga (próximo boot a frio)."""
        if not force:
            self._guard_not_running_ai(rt)
        # `force=True` é o rodízio cedendo vaga, não uma decisão sobre o aparelho: ali o desejo continua "no ar",
        # senão hibernar por falta de vaga impediria o próprio rodízio de acordá-lo depois.
        if not force:
            self.set_desired_state(rt, InstanceState.stopped.value)
        if rt.external:                        # nunca desliga um aparelho que não é nosso: só solta a sessão
            await self._soltar_do_painel(rt, "desconectado do painel (o aparelho externo continua ligado)")
            return
        if rt.state in (InstanceState.stopped, InstanceState.absent, InstanceState.hibernated):
            if rt.state == InstanceState.hibernated and not hibernate:      # "Parar" um hibernado = descartar o snapshot
                self._set_snapshot(rt, False)
                await asyncio.to_thread(self._discard_snapshot, rt)
                self._set_state(rt, InstanceState.stopped, "snapshot descartado")
            return
        for name in ("boot", *TAREFAS_DO_NO_AR):
            t = rt.tasks.pop(name, None)
            if t:
                t.cancel()
        # T.2 (achado #165, fatia que faltava): o desvio "testes: aparelho falso" que existia aqui saiu — o que
        # decidia (hibernar ou não, com snapshot ou sem) era só a REGRA reescrita no dublê ("se hibernate E
        # fake_snapshot_ok"), não o caminho de baixo, com sua elegibilidade (`hibernation`, `snapshot_unsupported`,
        # `fresh_data`) e seu tratamento de falha ao salvar. Agora os dois caminhos correm o MESMO código; só
        # `self.emulator.save_snapshot`/`stop_process` (achado #165) e `rt.session.close()` (nunca conectado no
        # aparelho falso, não-operação) distinguem real de dublê.
        await asyncio.to_thread(_wait_lock, rt.spawn_lock)   # se o processo estava nascendo, o PID já foi gravado
        if rt.pid is None and rt.state == InstanceState.booting:
            self._set_state(rt, InstanceState.stopped, "boot cancelado antes de iniciar o emulador")
            return
        async with rt.op_lock:
            a = self.android_de(rt)
            pedido_de_hibernar, porque_sem_snapshot = hibernate, None
            hibernate = hibernate and a.hibernation and rt.state in (InstanceState.online, InstanceState.stopping) \
                and rt.pid is not None and not rt.snapshot_unsupported and not rt.fresh_data
            if pedido_de_hibernar and not hibernate:
                # Por que a hibernação nem foi tentada. Sem esta frase o aparelho só "desligava" e quem pediu
                # "Hibernar" via um toast verde de sucesso sobre um boot a frio garantido.
                porque_sem_snapshot = (
                    "hibernação desligada na configuração (android.hibernation)" if not a.hibernation else
                    "este AVD já teve o snapshot recusado pelo emulador" if rt.snapshot_unsupported else
                    "os dados acabaram de ser apagados e não há snapshot a salvar" if rt.fresh_data else
                    f"o aparelho estava em '{rt.state.value}'")
            self._set_state(rt, InstanceState.stopping, "hibernando (salvando snapshot)…" if hibernate else "encerrando o emulador…")
            await asyncio.to_thread(rt.session.close)
            rt.automation = AutomationInfo()
            saved = False
            if hibernate:
                t0 = time.monotonic()
                try:
                    await asyncio.to_thread(self._discard_snapshot, rt)
                    await rt.executor.run(self.emulator.save_snapshot, rt.adb, emu.SNAPSHOT_NAME, timeout=320,
                                          label="snapshot save")
                    saved = True
                except (DriverError, AdbError) as exc:
                    log.warning("%s: snapshot não foi salvo (%s); desligando sem hibernar", rt.id, exc)
                    porque_sem_snapshot = f"o snapshot não foi salvo ({exc})"
                self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)", (now_iso(), "hibernate", dumps({
                    "instance_id": rt.id, "saved": saved, "save_seconds": round(time.monotonic() - t0, 1)})))
            how = await asyncio.to_thread(self.emulator.stop_process, rt.adb, rt.pid, rt.avd_name)
            self._save_pid(rt, None)
            rt.frame = None
            if rt.control == ControlOwner.user:
                self._end_user_control(rt, None)
            if saved:                              # só agora, com o processo encerrado, o snapshot passa a valer
                self._set_snapshot(rt, True, _hw_signature(a))
                self._set_state(rt, InstanceState.hibernated, "hibernado (snapshot salvo)")
            else:
                await asyncio.to_thread(self._discard_snapshot, rt)
                self._set_state(rt, InstanceState.stopped,
                                sem_snapshot(porque_sem_snapshot) if porque_sem_snapshot else how)
        self.on_device_free()                  # uma vaga abriu: o scheduler pode ligar quem está esperando

    # ------------------------------------------------------------------ rodízio (N contas sobre K vagas)
    def slots_used(self) -> int:
        """Aparelhos que ocupam (ou vão ocupar) RAM do host agora."""
        return sum(1 for d in self.devices.values() if not d.external
                   and d.state in (InstanceState.online, InstanceState.booting, InstanceState.stopping))

    def slots_used_of(self, worker_id: str) -> int:
        """Vagas ocupadas NAQUELA máquina. Ao contrário de `slots_used()`, o aparelho externo conta: quando ele
        tem worker, é a RAM DELE que está sendo gasta, e a vaga é do worker — não deste host."""
        return sum(1 for d in self.devices.values() if d.worker_id == worker_id
                   and d.state in (InstanceState.online, InstanceState.booting, InstanceState.stopping))

    @staticmethod
    def gerenciado_remoto(rt: DeviceRuntime) -> bool:
        """Aparelho de OUTRA máquina cujo agente está conectado e declara ligar. É o que separa "remoto
        gerenciado" (o rodízio o liga e o desliga pelo worker) de "aparelho alheio" (celular na mesa, que
        ninguém liga daqui). `bind_worker(..., None)` zera os verbos quando o worker cai: worker fora do ar
        deixa de ser gerenciado no mesmo instante."""
        return bool(rt.external and rt.worker_id and "start" in (rt.worker_verbs or []))

    def touch(self, rt: DeviceRuntime) -> None:
        rt.last_activity_mono = time.monotonic()

    def request_start(self, rt: DeviceRuntime, why: str) -> bool:
        """Pedido do scheduler para ligar um aparelho parado. Não bloqueia; respeita a espera após recusa por RAM.

        Para o aparelho de outra máquina o pedido vira um COMANDO para o agente (`start` ou `wake`), rastreável
        como o do painel — o central não liga emulador que não é dele, mas o worker liga, e é isso que faz o
        rodízio alcançar o parque remoto.
        """
        if (rt.state not in (InstanceState.stopped, InstanceState.absent, InstanceState.hibernated)
                or time.monotonic() < rt.start_backoff_until):
            return False
        if (travada := self.conta_travada_em(rt.id)) is not None:
            # Quarentena (ADR-055): conta travada logada. O painel e o agente já recusam `start` sem a confirmação
            # da pessoa (`despacho._precheck`); o emulador DESTA máquina, que o rodízio liga direto, recusa aqui —
            # senão o aparelho do felipe subiria sozinho por causa de uma tarefa que a porta bloquearia depois.
            rt.start_backoff_until = time.monotonic() + 600
            self.marcar_atencao(rt, f"Em quarentena: a conta @{travada} está travada e logada neste aparelho; ele não "
                                    "é ligado automaticamente. Precisa do dono.")
            return False
        if rt.external:
            if not self.gerenciado_remoto(rt):
                return False
            # `wake` só quando há o que acordar: sem snapshot válido o `_precheck` da API recusa, e insistir
            # nele a cada tick encheria o histórico do aparelho de recusas em vez de ligá-lo.
            verbo = ("wake" if (rt.state == InstanceState.hibernated and rt.snapshot_valid
                                and "wake" in (rt.worker_verbs or [])) else "start")
            if self.on_lifecycle_request(rt.id, verbo, why) is None:
                # Não deu para abrir o comando (verbo recusado, manutenção, comando em voo). Espera crescente:
                # sem ela o tick seguinte pediria de novo, e de novo, a cada segundo.
                rt.start_refusals += 1
                rt.start_backoff_until = time.monotonic() + min(120, 15 * 2 ** (rt.start_refusals - 1))
                return False
            self.bus.emit("log", f"{rt.id}: ligando sob demanda na máquina do worker — {why}", instance_id=rt.id)
            # A marca vale AQUI, e não quando o agente responder: sem ela o mesmo tick (e o seguinte) veria o
            # aparelho ainda `stopped` e abriria um segundo comando. Desfecho negativo a desfaz.
            rt.state, rt.state_detail = InstanceState.booting, f"ligando na máquina do worker {rt.worker_id}"
            self.publish(rt)
            return True
        self.bus.emit("log", f"{rt.id}: ligando sob demanda — {why}", instance_id=rt.id)
        rt.state, rt.state_detail = InstanceState.booting, "na fila de inicialização (sob demanda)"
        self.publish(rt)
        rt.tasks["boot"] = asyncio.create_task(self._boot(rt), name=f"boot-{rt.id}")
        return True

    def request_stop(self, rt: DeviceRuntime, why: str) -> None:
        """Pedido do scheduler para desligar um aparelho ocioso. O estado muda JÁ, para o mesmo tick não despachar nele."""
        if rt.external:
            # Hibernar economiza o boot da volta, mas só a máquina DELE sabe se salva snapshot (a mesma pergunta
            # de `despacho._hiberna_o_hospedeiro`); sem isso, `stop`, que todo agente sabe fazer.
            verbos = rt.worker_verbs or []
            verbo = "hibernate" if ("hibernate" in verbos and self.worker_hibernates(rt.worker_id or "")) else "stop"
            if time.monotonic() < rt.stop_backoff_until:
                return
            if verbo not in verbos or self.on_lifecycle_request(rt.id, verbo, why) is None:
                rt.stop_backoff_until = time.monotonic() + 60
                return
            self.bus.emit("log", f"{rt.id}: desligando para o rodízio na máquina do worker — {why}", instance_id=rt.id)
            rt.state, rt.state_detail = InstanceState.stopping, f"cedendo a vaga — {why}"
            self.publish(rt)
            return
        self.bus.emit("log", f"{rt.id}: desligando para o rodízio — {why}", instance_id=rt.id)
        rt.state, rt.state_detail = InstanceState.stopping, f"cedendo a vaga — {why}"
        self.publish(rt)
        rt.tasks["rotate-stop"] = asyncio.create_task(
            self.stop_instance(rt, force=True, hibernate=self.android_de(rt).hibernation),
            name=f"rotate-stop-{rt.id}")

    async def restart_instance(self, rt: DeviceRuntime) -> None:
        await self.stop_instance(rt)
        await self.start_instance(rt)

    async def reset_instance(self, rt: DeviceRuntime) -> None:
        """Reset explícito: apaga dados do usuário deste AVD (apps, contas, sessões) no próximo boot."""
        if rt.external:
            raise InstanceBusy("Aparelho externo: o painel nunca apaga dados de um aparelho que não é um AVD do projeto.")
        await self.stop_instance(rt)
        self._set_snapshot(rt, False)
        await asyncio.to_thread(self._discard_snapshot, rt)
        if rt.state == InstanceState.hibernated:
            self._set_state(rt, InstanceState.stopped, "snapshot descartado (reset)")
        rt.wipe_next_boot = True
        self._esquecer_o_que_o_disco_tinha(rt, "o aparelho foi resetado; os dados do app foram apagados")
        self.bus.emit("log", f"{rt.id}: dados apagados a pedido do usuário (reset).", level="warn", instance_id=rt.id)
        await self.start_instance(rt)

    # ------------------------------------------------------------------ efeitos do caminho remoto no central
    def _esquecer_o_que_o_disco_tinha(self, rt: DeviceRuntime, motivo: str) -> None:
        """Tudo o que o central AFIRMAVA sobre o disco do aparelho deixa de valer: evidência de conta, sessão do
        perfil e app instalado. Antes, o reset invalidava a sessão e deixava `device_app_state` em `ready` — o
        próximo objetivo era despachado para um aparelho vazio e "Distribuir" respondia "já está nesta versão".
        Vale para os dois caminhos: aqui e no desfecho que vem do agente da outra máquina."""
        self.db.execute("UPDATE instances SET account_evidence=NULL, account_evidence_ts=NULL WHERE id=?", (rt.id,))
        self._invalidate_session(rt, motivo)
        try:
            self.on_device_wiped(rt.id, motivo)
        except Exception:  # noqa: BLE001 - esquecer o app nunca pode derrubar o ciclo do aparelho
            log.exception("%s: falha ao marcar o app como ausente depois do wipe", rt.id)

    def _disco_apagado(self, rt: DeviceRuntime, motivo: str) -> None:
        """O disco FOI apagado (não só "deixou de valer"): avisa quem precisa saber que a conta logada saiu dali.

        Chamado depois do `_spawn` com `-wipe-data`, e não no `reset_instance`: `wipe_next_boot` só existe na
        memória, e um backend reiniciado entre o reset e o boot subiria o disco ANTIGO — com a conta travada ainda
        logada — sobre um marcador já dado por resolvido."""
        try:
            self.on_disk_erased(rt.id, motivo)
        except Exception:  # noqa: BLE001 - registrar o wipe nunca pode derrubar o boot nem o desfecho do worker
            log.exception("%s: falha ao registrar que o disco foi apagado", rt.id)

    async def _soltar_do_painel(self, rt: DeviceRuntime, detalhe: str,
                                estado: InstanceState = InstanceState.stopped) -> None:
        """Solta o que o CENTRAL mantinha aberto no aparelho (captura e sessão de automação) e assume o estado
        informado, sem tocar no processo do emulador — quem o desliga é o dono da máquina dele."""
        for name in TAREFAS_DO_NO_AR:
            t = rt.tasks.pop(name, None)
            if t:
                t.cancel()
        await asyncio.to_thread(rt.session.close)
        rt.automation, rt.frame = AutomationInfo(), None
        self._set_state(rt, estado, detalhe)

    async def _readotar_agora(self, rt: DeviceRuntime) -> None:
        """Readoção IMEDIATA depois de um `start`/`wake` que o agente concluiu. Sem isto o aparelho remoto só era
        reencontrado pelo monitor (até 30 s depois), e nesse intervalo o painel mostrava "desligado" sobre um
        aparelho já no ar."""
        if self.io_factory is not None:        # testes: aparelho falso, sem SDK/ADB/Appium (mesmo desvio de `_adopt`)
            rt.state, rt.state_detail = InstanceState.online, "no ar na máquina do worker"
            rt.automation = AutomationInfo(state="ready", detail="driver de teste")
            self.publish(rt)
            return
        await self._adopt_external(rt) if rt.external else await self._adopt(rt)

    async def aplicar_desfecho_remoto(self, rt: DeviceRuntime, verb: str, outcome: str,
                                      data: dict[str, Any] | None = None) -> None:
        """O que o agente fez NA MÁQUINA DELE vira, aqui, o mesmo efeito que o caminho local produziria.

        Sem isto a mesma operação tinha dois significados conforme onde o aparelho morava: depois de um `stop`
        remoto bem-sucedido o central acusava `error: sumiu do ADB` (alarme falso, com attention), seguia
        tentando `adb connect` a cada 30 s num aparelho desligado de propósito, `hibernate` nunca virava
        `hibernated` (então o painel oferecia "Iniciar", boot a frio, ignorando o snapshot) e `reset` apagava os
        dados sem invalidar sessão, evidência de conta ou estado do app.

        Só o desfecho `succeeded` aplica efeito: `failed`/`uncertain` não autorizam afirmar nada sobre o aparelho
        — aí quem descreve o estado é a observação (monitor e batida), que é a regra honesta.
        """
        if outcome != "succeeded":
            # Uma exceção, e ela não afirma nada sobre o aparelho: quando o RODÍZIO pediu o start, foi o CENTRAL
            # que escreveu "ligando" (`request_start`) antes de qualquer prova. Desfazer a própria marca é
            # corrigir uma afirmação nossa, não descrever a máquina do outro — e sem isso o aparelho ficaria
            # `booting` para sempre, invisível para o rodízio e ocupando a vaga do worker.
            if verb in ("start", "wake") and rt.external and rt.state == InstanceState.booting:
                rt.start_refusals += 1
                rt.start_backoff_until = time.monotonic() + min(120, 15 * 2 ** (rt.start_refusals - 1))
                back = InstanceState.hibernated if rt.snapshot_valid else InstanceState.stopped
                self._set_state(rt, back, f"não subiu na máquina do worker ({outcome})", level="warn")
                self.on_device_free()
            elif verb in ("stop", "hibernate") and rt.external and rt.state == InstanceState.stopping:
                # Mesma correção, do outro lado: a vaga que o rodízio deu por prometida não foi cedida. Volta
                # para `online` — é o estado de quem pedimos que parasse e não parou, e é o único que o monitor
                # CONFERE de verdade (`adb get-state` a cada 30 s), então uma revanche do mundo real o corrige.
                self._set_state(rt, InstanceState.online, f"não desligou na máquina do worker ({outcome})",
                                level="warn")
            if verb in ("start", "wake") and (data or {}).get("motivo") == emu.MOTIVO_DIALOGO_DE_CRASH:
                # 29.55 (c): o agente encerrou a subida presa no diálogo de relatório de falha. O motivo é claro e
                # é da pessoa: a escada não sobe degrau por isso, e a próxima subida já tira o dump do caminho.
                # Depois do `_set_state` acima, que limpa a atenção ao voltar para `stopped`.
                rt.bloqueio_de_crash = True
                self.marcar_atencao(rt, "A subida parou, na máquina do worker, no diálogo de consentimento de um "
                                        "relatório de falha pendente do emulador. O agente encerrou o processo; a "
                                        "próxima subida tira o relatório do caminho.")
            return
        dados = data or {}
        if verb in ("stop", "hibernate"):
            hibernou = verb == "hibernate" and bool(dados.get("hibernated"))
            if hibernou:
                self._set_snapshot(rt, True, "worker")
                await self._soltar_do_painel(rt, "hibernado na máquina do worker (snapshot salvo)",
                                             InstanceState.hibernated)
            else:
                # `stop` bem-sucedido é decisão, não perda: o cartão diz por que o aparelho está fora, e o
                # monitor não tenta readotá-lo (a decisão ficou gravada em `desired_state`).
                self._set_snapshot(rt, False)
                await self._soltar_do_painel(rt, "desligado a pedido; o processo foi parado na máquina do worker")
            self.on_device_free()
            return
        if verb == "reset":
            self._set_snapshot(rt, False)
            self._esquecer_o_que_o_disco_tinha(rt, "o aparelho foi resetado na máquina do worker; os dados do app "
                                                   "foram apagados")
            self._disco_apagado(rt, "o agente da outra máquina concluiu o reset: os dados do aparelho foram apagados")

    async def readotar_depois_do_worker(self, rt: DeviceRuntime, verb: str, outcome: str,
                                        data: dict[str, object] | None = None) -> None:
        """A readoção fica SEPARADA de `aplicar_desfecho_remoto` porque ela fala com o aparelho (adb connect,
        preparo) e pode levar dezenas de segundos. Os efeitos de estado precisam valer antes de o desfecho ser
        publicado — é o que impede o alarme falso —, mas prender o `finished_at` do comando (e o cadeado que dá
        exclusividade ao aparelho) à latência do ADB seria trocar uma mentira por uma espera."""
        if outcome == "succeeded" and verb in ("start", "wake", "restart", "reset"):
            if self._o_aparelho_subiu_de_novo(verb, data):
                # O boot novo deste aparelho de worker (sem processo local, sem `emulator_started_at`): o marco que a
                # rede compara com a medição (item 29.22). `_adopt` põe o aparelho `online` sem passar pelo `_set_state`.
                rt.online_since_mono = time.monotonic()
                await self._esquecer_a_sessao_de_antes_do_boot(rt)
            await self._readotar_agora(rt)
            # Aparelho que o central NUNCA viu cair (o restart do worker é mais rápido que o monitor de 30 s) segue `online`,
            # e a readoção não o "coloca no ar" de novo (`_adopt_external` só chama `_start_online_tasks` na TRANSIÇÃO para
            # online): sem isto a sessão recriada só viria numa leitura ou execução. `ensure_automation` dá a exclusão:
            # quem chegar depois (leitura, tarefa) vê `starting` e espera, não abre uma segunda sessão.
            if (self.io_factory is None and rt.state == InstanceState.online and rt.automation.state == "none"
                    and not rt.store and ("automation" not in rt.tasks or rt.tasks["automation"].done())):
                rt.tasks["automation"] = asyncio.create_task(self._automacao_depois_de_arrumar(rt),
                                                             name=f"automation-{rt.id}")

    @staticmethod
    def _o_aparelho_subiu_de_novo(verb: str, data: dict[str, object] | None) -> bool:
        """`restart` e `reset` do agente desligam e religam a frio (`_v_restart`/`_v_reset`): processo e instrumentation
        novos, sempre. `start` e `wake` NÃO: com o emulador já no ar o agente responde `started: False` e não toca em nada
        — a sessão que o central tem segue valendo (e pode estar no meio de uma tarefa). Só quando o agente AFIRMA
        `started: True` (boot a frio ou volta do snapshot, como o `_boot` local, que também descarta a sessão ao
        acordar) o que havia antes deixou de valer; sem a afirmação, não se invalida o que ninguém provou morto."""
        if verb in ("restart", "reset"):
            return True
        return bool((data or {}).get("started"))

    async def _esquecer_a_sessao_de_antes_do_boot(self, rt: DeviceRuntime) -> None:
        """O que o caminho local faz ao parar/religar o aparelho e o caminho remoto não fazia: o reboot feito pelo worker
        mata a instrumentation do UiAutomator2, mas a sessão do Appium daqui seguia "pronta" (android-09, 01/10/2026: a
        hierarquia deu 503 por horas depois de um restart que o central não viu como queda).

        Só marca a sessão como inexistente. Quem FECHA é `ensure_automation`, dentro do estado `starting` (`session.close`,
        `delete_stale`, `remove_forward`, `connect`): fechar daqui, numa thread fora da trilha do aparelho, podia cair
        no meio de um `connect` de outra coroutine. O contador de falhas é da vida anterior do aparelho."""
        for _ in range(240):                       # sessão em abertura é de antes do boot ou de agora: espera, não atropela
            if rt.automation.state != "starting":
                break
            await asyncio.sleep(1)
        if rt.automation.state == "starting":
            return
        rt.automation_failures, rt.automation_last_error = 0, None
        self.invalidate_automation(rt, "o aparelho reiniciou na máquina do worker; a sessão de antes do boot não vale mais")

    # ------------------------------------------------------------------ automação
    @staticmethod
    def _espera_da_proxima_sessao(rt: DeviceRuntime) -> float:
        """90 s, dobrando a cada falha seguida, até 24 min. Fixo em 90 s, uma sessão que nunca vai abrir custava
        40 tentativas por hora — cada uma com um `adb connect`, um Appium e um evento persistido."""
        return 90.0 * (2 ** min(rt.automation_failures, 4))

    async def ensure_automation(self, rt: DeviceRuntime) -> bool:
        if rt.state != InstanceState.online:
            return False
        if self.io_factory is not None:
            return True
        if rt.automation.state == "ready" and rt.session.connected:
            return True
        if rt.automation.state == "starting":
            for _ in range(240):
                await asyncio.sleep(1)
                if rt.automation.state != "starting":
                    break
            return rt.automation.state == "ready"
        rt.automation = AutomationInfo(state="starting", detail="abrindo sessão UiAutomator2…")
        self.publish(rt)
        if not self.appium.is_up():
            ok = await asyncio.to_thread(self.appium.start)
            if not ok:
                rt.automation = AutomationInfo(state="error", detail=self.appium.detail)
                self.publish(rt, level="warn")
                return False
        try:
            await asyncio.to_thread(rt.session.close)
            stale = self.db.scalar("SELECT appium_session_id FROM instances WHERE id=?", (rt.id,))
            await asyncio.to_thread(rt.session.delete_stale, stale)
            for port in (rt.ports.system, rt.ports.mjpeg):      # forwards órfãos de um Appium que morreu
                await asyncio.to_thread(rt.adb.remove_forward, port)
            await rt.executor.run(rt.session.connect, timeout=300, label="appium connect")
            self.db.execute("UPDATE instances SET appium_session_id=? WHERE id=?", (rt.session.session_id, rt.id))
            rt.automation = AutomationInfo(state="ready", detail=f"systemPort {rt.ports.system}")
            rt.automation_failures, rt.automation_last_error = 0, None
            # O teto de remediação só se rearma quando o aparelho PROVA que voltou a servir. Rearmá-lo ao ficar
            # `online` deixaria um aparelho que sobe e morre reiniciando para sempre.
            rt.restart_attempts = 0
            self.publish(rt, f"{rt.id}: sessão de automação pronta")
            return True
        except Exception as exc:  # noqa: BLE001
            detalhe = str(exc).splitlines()[0][:300]
            rt.automation = AutomationInfo(state="error", detail=detalhe)
            rt.automation_failures += 1
            # A MESMA falha repetida não vira evento novo: era isso que gravava ~150 linhas por aparelho por
            # tarde dizendo a mesma coisa. A primeira ocorrência de cada motivo continua aparecendo.
            if detalhe != rt.automation_last_error:
                rt.automation_last_error = detalhe
                self.publish(rt, f"{rt.id}: falha ao abrir sessão de automação ({rt.automation_failures}ª)",
                             level="warn")
            else:
                log.info("%s: falha ao abrir sessão de automação (%dª, mesmo motivo)", rt.id, rt.automation_failures)
            if rt.automation_failures >= FALHAS_DE_SESSAO_PARA_DEGRADAR:
                # N falhas seguidas: o aparelho está no ar e não serve para automação nenhuma. Dizer isso é o que
                # o tira do escalonamento e põe a frase no cartão — antes, ele seguia `online` e `attention` nulo.
                self._degradar(rt, f"A sessão de automação falhou {rt.automation_failures} vezes seguidas neste "
                                   f"aparelho e ele não está utilizável: {detalhe}")
            return False

    def invalidate_automation(self, rt: DeviceRuntime, why: str) -> None:
        rt.automation = AutomationInfo(state="none", detail=why)

    # ------------------------------------------------------------------ interesse em prévia (contrato C2)
    def interesse_legado(self, conexao_id: str) -> None:
        """Painel que ainda não mandou `watch` (cliente antigo): vale como grade em TODOS os aparelhos enquanto a
        conexão durar — é o comportamento de antes, e uma aba antiga custa o de antes até recarregar.

        NÃO acorda a captura: toda conexão começa assim, e o painel novo manda o primeiro `watch` logo depois. Acordar
        aqui transformaria cada recarga de página (ou queda de rede) numa rajada de screencap no parque inteiro. O
        painel antigo recebe o frame no ritmo da grade, que é o de antes."""
        self._interesses[conexao_id] = _Interesse(grade=None, foco=None, ate=None)

    def registrar_interesse(self, conexao_id: str, grid: Any, focus: Any, ttl_s: Any) -> None:
        """`{"type": "watch", ...}` de uma conexão: SUBSTITUI o interesse dela (não acumula). O que chega do
        navegador é validado aqui — lista de ids conhecidos, com teto; foco que não é aparelho vira nenhum; TTL
        entre 5 e 60 s. `grid` vazio e `focus` nulo = a aba ficou oculta: esta conexão não olha mais nada."""
        try:
            ttl = min(TTL_INTERESSE_MAX_S, max(TTL_INTERESSE_MIN_S, float(ttl_s)))
        except (TypeError, ValueError):
            ttl = TTL_INTERESSE_PADRAO_S
        ids = [i for i in (grid if isinstance(grid, list) else []) if isinstance(i, str) and i in self.devices]
        foco = focus if isinstance(focus, str) and focus in self.devices else None
        antes = self._niveis()
        self._interesses[conexao_id] = _Interesse(grade=frozenset(ids[:len(self.devices)]), foco=foco,
                                                  ate=self.relogio() + ttl)
        if foco is not None:
            rt = self.devices[foco]
            # Paridade com o `focus` antigo: olhar o aparelho sob controle manual mantém o lease vivo. Não concede
            # controle a ninguém — só não deixa expirar o de quem já o tem enquanto a tela está aberta.
            if rt.control == ControlOwner.user:
                rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S
        self._acordar_quem_ganhou(antes)

    def soltar_interesse(self, conexao_id: str) -> None:
        """A conexão fechou: o interesse dela some. O laço de cada aparelho percebe na próxima volta e, sem mais
        ninguém olhando, passa a `paused`."""
        self._interesses.pop(conexao_id, None)

    def nivel_de_interesse(self, rt: DeviceRuntime) -> str | None:
        """`foco`, `grade` ou `None` (ninguém olhando). Foco = `focus` antigo, `watch.focus` de alguma conexão ou
        controle manual (quem controla está vendo a tela); grade = algum painel com o aparelho na grade visível."""
        if rt.focused or rt.control == ControlOwner.user:
            return "foco"
        agora = self.relogio()
        for i in self._interesses.values():
            if (i.ate is None or i.ate > agora) and (i.grade is None or rt.id in i.grade):
                return "grade"
        return None

    def _foco_por_interesse(self, rt: DeviceRuntime) -> bool:
        agora = self.relogio()
        return any(i.foco == rt.id and (i.ate is None or i.ate > agora) for i in self._interesses.values())

    def _niveis(self) -> dict[str, str | None]:
        return {rt.id: self.nivel_de_interesse(rt) for rt in self.devices.values()}

    def _acordar_quem_ganhou(self, antes: dict[str, str | None]) -> None:
        """Retomada IMEDIATA: aparelho que passou a ser olhado (ou subiu de grade para foco) captura já, sem esperar
        a próxima volta do laço. Vários painéis no mesmo aparelho continuam sendo UMA aquisição por ciclo."""
        ordem = {None: 0, "grade": 1, "foco": 2}
        for rt in self.devices.values():
            if ordem[self.nivel_de_interesse(rt)] > ordem[antes.get(rt.id)]:
                rt.capture_now.set()

    # ------------------------------------------------------------------ frames
    async def _capture_loop(self, rt: DeviceRuntime) -> None:
        """Prévia sob demanda (contrato C2): só se captura aparelho que algum painel está olhando ou que está sob
        controle manual. Sem interesse, o laço acorda no ritmo da grade só para perceber a volta do interesse (ou a
        troca de `preview_mode` para `always`, que vale sem reinício) — e não toca no aparelho."""
        pausada_antes = self._previa_pausada(rt)
        pedido = False
        while rt.state == InstanceState.online:
            _, espera = await self._volta_da_previa(rt, pedido=pedido)
            pausada = self._previa_pausada(rt)
            if pausada != pausada_antes:
                # O painel só recebe o estado da tela por `instance.updated`: a troca `paused` ↔ ao vivo sai aqui, na
                # TRANSIÇÃO — nunca por mensagem `watch`, que o painel renova a cada poucos segundos.
                pausada_antes = pausada
                self.publish(rt)
            try:
                # `capture_now` (interesse novo, foco, entrada manual) fura a espera — inclusive o recuo de falhas.
                await asyncio.wait_for(rt.capture_now.wait(), timeout=espera)
                pedido = True
            except asyncio.TimeoutError:
                pedido = False
            rt.capture_now.clear()

    async def _volta_da_previa(self, rt: DeviceRuntime, *, pedido: bool = False) -> tuple[str, float]:
        """UMA volta do laço: decide se captura e captura. Devolve o que aconteceu e quanto esperar até a próxima.

        `pedido` = a volta foi acordada por `capture_now` (interesse novo, entrada manual): captura mesmo com frame
        fresco. Separada do laço para o teste exercitar a regra volta a volta, sem esperar o relógio.
        """
        s = self.get_settings()
        nivel = self.nivel_de_interesse(rt)
        interval = s.capture_focus_interval_s if nivel == "foco" else s.capture_grid_interval_s
        resultado = "ocupado"
        try:
            idade = None if rt.frame is None else time.monotonic() - rt.frame.mono
            # a captura compartilha o executor com as ações: se há trabalho na fila, não entra na frente
            overdue = idade is None or idade > interval * 2
            antigo_capturaria = rt.executor.queue_depth == 0 or overdue
            if (s.preview_mode == "always" or nivel is not None) and self.ia_no_controle(rt):
                # A IA está operando o aparelho: a prévia NÃO põe screencap próprio na fila dele — nem "atrasada",
                # nem pedida. A thread do aparelho é uma só; em r-20260928195344-02ee9e (android-06, 2 vCPU
                # saturadas) a prévia do foco fez 850–890 screencaps em 15 min, cada um na frente da próxima ação,
                # e o `drain` da etapa esperando por eles. Quem publica o frame agora é a própria observação da IA
                # (`_previa_pela_observacao`), logo depois da árvore: o painel vê a tela no ritmo em que a IA a olha.
                # Vale nos dois modos: o laço antigo (`always`) disputava do mesmo jeito.
                resultado = "ia_no_controle"
                if antigo_capturaria:
                    metricas.contar("captura.evitada", motivo="ia_no_controle")
            elif s.preview_mode == "always":
                if antigo_capturaria:                     # o laço antigo, como volta atrás sem reinício
                    resultado = await self._ciclo_de_previa(rt)
            elif nivel is None:
                resultado = "sem_interesse"
                # Conta só a volta em que o laço antigo TERIA capturado: contar as que ele também pularia (fila
                # ocupada) inflaria a economia.
                if antigo_capturaria:
                    metricas.contar("captura.evitada", motivo="sem_interesse")
            elif not pedido and idade is not None and idade < interval:
                # Frame fresco (a observação da IA acabou de publicar): a prévia não captura por cima dela.
                resultado = "frame_recente"
                if antigo_capturaria:
                    metricas.contar("captura.evitada", motivo="frame_recente")
            elif antigo_capturaria:
                resultado = await self._ciclo_de_previa(rt)
        except DriverError as exc:
            log.debug("%s: captura falhou: %s", rt.id, exc)
            metricas.contar("captura.total", origem="previa", resultado="falha")
            self._falha_de_captura(rt, f"{type(exc).__name__}: {exc}")
            resultado = "falha"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("%s: erro na captura", rt.id)
            metricas.contar("captura.total", origem="previa", resultado="falha")
            self._falha_de_captura(rt, type(exc).__name__)
            resultado = "falha"
        # Recuo depois de falhas seguidas (`devices/stream.py`).
        return resultado, backoff_s(interval, rt.capture_failures)

    def _previa_pausada(self, rt: DeviceRuntime) -> bool:
        """Contrato C3: `on_demand` e ninguém olhando. Não é `stale` nem erro — é a economia funcionando."""
        return self.get_settings().preview_mode == "on_demand" and self.nivel_de_interesse(rt) is None

    async def _screencap(self, rt: DeviceRuntime, *, origem: str, timeout: float, label: str) -> bytes:
        """Screencap pelo executor do aparelho, medido DENTRO da thread dele: o tempo na fila não é do aparelho."""
        def medir() -> tuple[bytes, float]:
            t0 = time.perf_counter()
            png = rt.io.screenshot_png()
            return png, (time.perf_counter() - t0) * 1000
        # `previa`: o `drain` da etapa não espera a captura da prévia (`DeviceExecutor.drain`).
        png, ms = await rt.executor.run(medir, timeout=timeout, label=label, previa=origem == "previa")
        # `instancia`: a lentidão de r-20260928195344-02ee9e era de UM convidado (android-06), diluída na distribuição
        # do parque. Cardinalidade: aparelhos (dezenas) × origem (3) × via (2) — longe do teto de `metricas`.
        metricas.observar("captura.ms", ms, origem=origem, instancia=rt.id)
        metricas.observar("captura.bytes", len(png), origem=origem)
        return png

    async def _ciclo_de_previa(self, rt: DeviceRuntime) -> str:
        """UMA captura de prévia, já com a regra de tela sensível (contrato C4). Devolve o que aconteceu —
        `capturada`, `sensivel`, `pausada` ou `descartada` — para o teste exercitar a regra sem esperar o relógio.

        A prévia não lê a hierarquia a cada frame (custaria uma leitura do UiAutomator por screencap). Ela usa a
        ÚLTIMA classificação desta geração, gravada por `arvore()` — o funil de toda leitura de hierarquia. Limite
        documentado: uma tela que VIRA sensível sem que ninguém leia a hierarquia (a pessoa navegando no controle
        manual, fora do modo treinamento) aparece até a próxima leitura; o contrário não acontece — uma tela que foi
        classificada sensível só volta a aparecer depois de uma leitura que diga que ela deixou de ser.
        """
        ex = rt.executor
        if ex.em_trecho_sensivel:
            metricas.contar("captura.evitada", motivo="sensivel")
            return "pausada"                      # credencial sendo digitada: nem screencap
        relida = False
        if not rt.store and self._tela_sensivel(rt):
            # A última hierarquia disse "sensível". Sem reler, a prévia ficaria presa no marcador: ninguém mais lê
            # a árvore de um aparelho parado em `waiting_user` enquanto a pessoa resolve na janela do emulador.
            try:
                xml = await ex.run(rt.io.page_source, timeout=25, label="hierarquia (prévia)", previa=True)
                self.arvore(rt, xml)
                relida = True
            except DriverTimeout:
                raise                             # executor preso: é falha de captura, não "sem sessão"
            except DriverError:
                pass                              # sem sessão: continua sensível, e o screencap prova que responde
        if relida and self._tela_sensivel(rt) and rt.frame is not None:
            # A hierarquia acabou de responder e confirmou a tela sensível: o marcador se renova sem screencap.
            self._marcar_sensivel(rt, rt.frame.info.width, rt.frame.info.height)
            metricas.contar("captura.evitada", motivo="sensivel")
            return "sensivel"
        geracao, antes = rt.geracao, ex.trechos_sensiveis
        remota = self._captura_remota(rt)
        if remota is None:
            png = await self._screencap(rt, origem="previa", timeout=25, label="screencap")
        else:
            # Tela JÁ sabida sensível (a releitura falhou — sem sessão —, ou é a VM-loja): a origem manda só o
            # tamanho, e nenhum pixel sai da máquina do worker. É a mesma garantia de `observe` numa tela sensível.
            so_dimensoes = self._previa_sensivel(rt)
            cod = await self._capturar_na_origem(rt, remota, origem="previa", timeout=25, label="screencap",
                                                 previa=not so_dimensoes, so_dimensoes=so_dimensoes)
        if ex.em_trecho_sensivel or ex.trechos_sensiveis != antes:
            metricas.contar("captura.total", origem="previa", resultado="descartada")
            return "descartada"                   # a digitação da credencial começou enquanto o screencap esperava
        sensivel = self._previa_sensivel(rt)      # relida DEPOIS do screencap: uma hierarquia pode ter chegado
        if remota is None:
            frame = await self.publish_frame(rt, png, sensivel=sensivel, geracao=geracao)
        elif sensivel or so_dimensoes:
            # Sem imagem para publicar (pedido só de tamanho) o marcador fica, mesmo que uma hierarquia nova tenha
            # dito que a tela deixou de ser sensível: a próxima volta captura.
            # O mesmo marcador de `publish_frame`: o tamanho da tela e nenhuma imagem. Os bytes vindos da origem
            # são descartados sem decodificar.
            frame = self._marcar_sensivel(rt, cod.largura, cod.altura, capturou=True)
        else:
            # A imagem já codificada na origem entra pela MESMA porta da local (`_publicar_imagem`), que confere
            # geração e tela sensível no instante de publicar.
            frame = self._publicar_imagem(rt, geracao, cod.largura, cod.altura, cod.cheia or b"",
                                          cod.miniatura or b"")
        resultado = "descartada" if frame is None else "sensivel" if frame.sensitive else "ok"
        metricas.contar("captura.total", origem="previa", resultado=resultado)
        return "capturada" if resultado == "ok" else resultado

    def _tela_sensivel(self, rt: DeviceRuntime) -> bool:
        """A última hierarquia DESTA geração classificou a tela como sensível."""
        return rt.classificacao is not None and rt.classificacao[0] == rt.geracao and rt.classificacao[1]

    def _previa_sensivel(self, rt: DeviceRuntime) -> bool:
        """A prévia não mostra esta tela. Na VM-loja toda tela é sensível (`MOTIVO_LOJA`), com ou sem hierarquia —
        e sem sessão de automação ela quase nunca tem uma. Consequência aceita no contrato C4: a prévia da loja é
        sempre o marcador; operá-la pelo painel fica às cegas (tecla e texto seguem aceitos pelo `frame_id`), e o
        caminho é a janela do emulador, como a senha da conta Google (ADR-009). Voltar a mostrar a loja é tirar o
        `rt.store` desta linha — decisão do dono, não desta camada."""
        return rt.store or self._tela_sensivel(rt)

    def _falha_de_captura(self, rt: DeviceRuntime, motivo: str) -> None:
        """Registra a falha e publica só na PRIMEIRA da série: é ela que muda o estado da tela para o painel."""
        rt.capture_failures += 1
        rt.capture_error, rt.capture_error_at = motivo[:300], now_iso()
        if rt.capture_failures == 1:
            self.publish(rt, f"{rt.id}: captura de tela falhou — {rt.capture_error}", "warn")

    async def publish_frame(self, rt: DeviceRuntime, png: bytes, *, sensivel: bool = False,
                            geracao: int | None = None) -> Frame | None:
        """Publica o frame da prévia a partir de um screencap. Tela sensível vira MARCADOR: só o cabeçalho do PNG é
        lido (tamanho), nada é decodificado nem codificado, e `GET /frame` responde 404 `sensitive_screen` (C4).

        `geracao`: a do runtime no instante do screencap (padrão: a de agora). `None` de volta = frame descartado
        porque o aparelho mudou de geração durante a codificação (ver `_publicar_imagem`)."""
        if sensivel:
            w, h = _tamanho_png(png)
            return self._marcar_sensivel(rt, w, h, capturou=True)
        geracao = rt.geracao if geracao is None else geracao
        t0 = time.perf_counter()
        full, thumb, w, h = await asyncio.to_thread(_encode_frame, png)
        metricas.observar("codificacao.ms", (time.perf_counter() - t0) * 1000, tipo="previa")
        return self._publicar_imagem(rt, geracao, w, h, full, thumb)

    def _publicar_imagem(self, rt: DeviceRuntime, geracao: int, w: int, h: int, full: bytes,
                         thumb: bytes) -> Frame | None:
        """Publica uma imagem JÁ codificada, conferindo o estado NO INSTANTE de publicar (revisão F8).

        A codificação roda fora do executor do aparelho, numa thread do pool — que é dividido com banco, evidência e
        métricas, então esperar ali é real. Enquanto ela corria, uma hierarquia pode ter classificado a tela como
        sensível (e publicado o marcador), a digitação de uma credencial pode ter começado, ou o aparelho pode ter
        saído e voltado ao ar. A decisão tomada ANTES de codificar não vale mais: sensível agora → a imagem é jogada
        fora e fica (ou entra) o marcador; outra geração → a imagem é do Android de antes e nada é publicado."""
        if rt.geracao != geracao:
            return None
        if self._previa_sensivel(rt) or rt.executor.em_trecho_sensivel:
            if rt.frame is not None and rt.frame.sensitive:
                return rt.frame                   # o marcador de quem classificou já está no ar
            return self._marcar_sensivel(rt, w, h, capturou=True)
        return self._registrar_frame(rt, w, h, full, thumb)

    def _marcar_sensivel(self, rt: DeviceRuntime, w: int, h: int, *, capturou: bool = False) -> Frame:
        """Marcador de tela sensível. `capturou`: veio de um screencap que funcionou (só o cabeçalho foi lido); por
        leitura de hierarquia, NÃO — e aí a falha registrada da captura continua valendo."""
        return self._registrar_frame(rt, w, h, b"", b"", sensivel=True, capturou=capturou)

    def _registrar_frame(self, rt: DeviceRuntime, w: int, h: int, full: bytes, thumb: bytes, *,
                         sensivel: bool = False, capturou: bool = True) -> Frame:
        info = FrameInfo(id=self._novo_frame_id(rt), ts=now_iso(), width=w, height=h,
                         orientation="landscape" if w > h else "portrait", stale=False, sensitive=sensivel)
        frame = Frame(info=info, mono=time.monotonic(), jpeg_full=full, jpeg_thumb=thumb, sensitive=sensivel)
        rt.frame = frame
        self._lembrar_dimensoes(rt, w, h)
        # "Captura recuperada" é afirmação sobre o SCREENCAP: só um screencap que funcionou a sustenta. O marcador
        # publicado por uma leitura de hierarquia não prova nada sobre ele (o android-09 congelado de 25/09 seguia
        # respondendo hierarquia com o screencap estourando), então não apaga a falha registrada.
        if capturou:
            voltou = rt.capture_failures > 0
            rt.capture_failures, rt.capture_error, rt.capture_error_at = 0, None, None
            if voltou:
                self.publish(rt, f"{rt.id}: captura de tela recuperada")
        # O marcador também entra: o controle manual por tecla/texto (e a loja, decisão 4 do dono) referencia o
        # `frame_id` da tela que está no aparelho, mesmo sem imagem para mostrar.
        rt.recent_frames[info.id] = (frame.mono, w, h)
        while len(rt.recent_frames) > 6:
            rt.recent_frames.popitem(last=False)
        self.bus.emit("frame", "frame", instance_id=rt.id,
                      data={"instance_id": rt.id, "frame": info.model_dump(mode="json")})
        return frame

    def set_focus(self, instance_id: str | None, ttl_s: float = 15) -> None:
        for rt in self.devices.values():
            if rt.id == instance_id:
                was = rt.focused
                rt.focus_until_mono = time.monotonic() + ttl_s
                if rt.control == ControlOwner.user:
                    rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S
                if not was:
                    rt.capture_now.set()

    def arvore(self, rt: DeviceRuntime, xml: str, *, max_elements: int = 1500) -> UiTree:
        """UM lugar onde a hierarquia vira `UiTree` — e, por consequência, UM lugar que aplica os critérios de
        tela sensível. Espalhá-los pelos chamadores é como o critério antigo ficou preso a `password=true`: cada
        leitura nova nascia sem o resto, e ninguém percebia.

        Na VM-loja não há o que classificar: toda tela ali é da conta Google do parque.

        Por ser o funil, é também aqui que a PRÉVIA aprende que a tela ficou sensível: a classificação fica no
        runtime, e a imagem publicada antes sai do ar na hora — ela pode já mostrar esta tela.
        """
        tree = self._classificar(rt, xml, max_elements=max_elements)
        if tree.sensitive and rt.frame is not None and not rt.frame.sensitive:
            self._marcar_sensivel(rt, rt.frame.info.width, rt.frame.info.height)
        return tree

    def _classificar(self, rt: DeviceRuntime, xml: str, *, max_elements: int = 1500) -> UiTree:
        tree = parse_hierarchy(xml, max_elements=max_elements, regras=self.regras_sensiveis,
                               sempre_sensivel=MOTIVO_LOJA if rt.store else None)
        rt.classificacao = (rt.geracao, tree.sensitive, time.monotonic())
        return tree

    #: Por quanto tempo a leitura vale como sinal (s): cobre a volta do detector até a marcação, nada além.
    VALIDADE_DA_ATIVIDADE_S = 120.0

    async def _ler_atividade_de_desafio(self, rt: DeviceRuntime) -> None:
        """Só roda quando a árvore já parece conta travada (uma leitura do foco, sem custo no resto). Falha ao ler =
        sem sinal forte: a conta fica bloqueada para a pessoa, nunca retirada por incerteza."""
        try:
            pacote, atividade = await rt.executor.run(rt.io.current_focus, timeout=15, label="janela em foco")
        except Exception as exc:  # noqa: BLE001 - sem leitura, sem sinal
            log.info("%s: sem ler o foco da trava (%s)", rt.id, exc)
            rt.atividade_de_desafio = None
            return
        janela = janela_completa(pacote, atividade)
        # Sinal forte só quando a janela é uma das que o PRÓPRIO app declara como conta perdida (`app.yaml`,
        # `atividades_de_conta_perdida`): o núcleo não conhece nome de atividade de app nenhum (ADR-052).
        declaradas = capabilities_of(pacote).lost_account_activities if pacote else ()
        rt.atividade_de_desafio = (janela, time.monotonic()) if janela and janela in declaradas else None

    def tem_atividade_de_desafio(self, instance_id: str) -> bool:
        """A atividade de desafio do Instagram estava em foco na última observação de conta travada deste aparelho?"""
        rt = self.devices.get(instance_id)
        visto = rt.atividade_de_desafio if rt is not None else None
        return visto is not None and time.monotonic() - visto[1] <= self.VALIDADE_DA_ATIVIDADE_S

    async def observe(self, rt: DeviceRuntime, *, timeout: float,
                      imagem: bool | Callable[[UiTree], bool] = True, lado_max: int | None = None) -> Observation:
        """Observação: hierarquia PRIMEIRO, imagem só quando pedida (contrato C1 do adendo v0.20).

        `imagem`: `True` (padrão, o comportamento de antes: imagem sempre, JPEG cheio e prévia publicada), `False`
        (só a árvore) ou uma função da árvore — quem chama decide com a árvore na mão, e só então a imagem é
        adquirida, logo em seguida e pelo mesmo executor. `lado_max`: a imagem sai já no tamanho que o modelo vê
        (lado maior), sem o JPEG cheio de passagem; a prévia só é codificada se alguém está olhando.

        Tela sensível nunca tem imagem (`image_omitted="sensitive"`) e vira marcador na prévia (contrato C4); sem
        imagem por escolha de quem chamou, `image_omitted="policy"`. Omitir NÃO é falha de captura. Sem imagem, a
        largura e a altura vêm do último frame desta geração na orientação que a hierarquia declara, ou de
        `wm size` — nunca de outro aparelho; sem nenhuma dessas, a imagem é adquirida (é o jeito de saber).
        """
        ex = rt.executor
        t0 = time.perf_counter()
        xml = await ex.run(rt.io.page_source, timeout=timeout, label="hierarquia")
        ms_arvore = (time.perf_counter() - t0) * 1000
        metricas.observar("observacao.ms", ms_arvore, parte="arvore", instancia=rt.id)
        tree_at = now_iso()
        tree = self._classificar(rt, xml)
        rt.last_tree = tree
        if tree.conta_travada is not None:
            await self._ler_atividade_de_desafio(rt)
        pkg = next((p for p in tree.packages if p != "com.android.systemui"), None)
        sensivel = tree.sensitive or rt.store or ex.em_trecho_sensivel
        quer = False if sensivel else (bool(imagem(tree)) if callable(imagem) else bool(imagem))
        dims = None if quer else await self._dimensoes_sem_imagem(rt, xml, timeout=timeout)
        if dims is None and not sensivel:
            quer = True
        if quer:
            t_imagem = time.perf_counter()
            com_imagem = await self._observar_imagem(rt, tree, pkg, tree_at, timeout=timeout, lado_max=lado_max,
                                                     previa_sempre=imagem is True)
            com_imagem.ms_arvore, com_imagem.ms_imagem = ms_arvore, (time.perf_counter() - t_imagem) * 1000
            return com_imagem
        if dims is not None:
            metricas.contar("captura.evitada", motivo="sensivel" if sensivel else "politica")
        if sensivel:
            capturou = dims is None
            if dims is None:
                # Último recurso para o tamanho: o screencap, do qual só o CABEÇALHO é lido — os pixels da tela
                # sensível nunca são decodificados, codificados nem guardados.
                remota = self._captura_remota(rt)
                if remota is None:
                    dims = _tamanho_png(await ex.run(rt.io.screenshot_png, timeout=timeout,
                                                     label="tamanho da tela"))
                else:
                    # Na origem, nem o PNG sai da máquina do worker: só a largura e a altura voltam.
                    so = await self._capturar_na_origem(rt, remota, origem="observacao", timeout=timeout,
                                                        label="tamanho da tela", so_dimensoes=True)
                    dims = (so.largura, so.altura)
                self._lembrar_dimensoes(rt, *dims)
            frame_id = self._marcar_sensivel(rt, *dims, capturou=capturou).info.id
        else:
            # Só de árvore: o modelo não recebe imagem. O PAINEL pode receber — com a IA no controle a prévia não
            # captura por conta própria, e é daqui que o frame sai (`_previa_pela_observacao`).
            frame_id = await self._previa_pela_observacao(rt) or self._novo_frame_id(rt)
        return Observation(frame_id=frame_id, ts=tree_at, width=dims[0], height=dims[1], jpeg=None, tree=tree,
                           package=pkg, sensitive=tree.sensitive, tree_at=tree_at, image_at=None,
                           image_omitted="sensitive" if sensivel else "policy", runtime_gen=rt.geracao,
                           ms_arvore=ms_arvore)

    async def _previa_pela_observacao(self, rt: DeviceRuntime) -> str | None:
        """O frame do painel a partir de uma observação SÓ DE ÁRVORE da IA. Devolve o id do frame publicado, ou
        `None` (nada a publicar agora, ou a captura não deu).

        Com a IA no controle a prévia cede a vez (`_volta_da_previa`): sem isto o Foco congelaria justamente quando o
        dono está olhando — no Instagram a árvore é rica e a maioria das observações sai sem imagem. Só quando a
        prévia teria capturado (alguém olhando, ou `preview_mode=always`) e o frame venceu no ritmo do nível — o
        mesmo critério do `frame_recente` do laço. Roda logo depois da árvore, na sequência da própria observação:
        não é uma entrada a mais disputando a fila com a próxima ação. `_ciclo_de_previa` traz junto as regras de
        tela sensível e a captura na origem.

        Falha aqui é falha da PRÉVIA, registrada como a do laço, e nunca da observação: a IA já tem a árvore, e virar
        erro da etapa um screencap que era só para o painel é o que r-20260928165254-e31953 e
        r-20260928195344-02ee9e mostraram. Um zumbi deixado aqui se comporta como o do laço (`has_zombie`)."""
        if not self.ia_no_controle(rt) or self._previa_pausada(rt):
            return None
        s = self.get_settings()
        interval = s.capture_focus_interval_s if self.nivel_de_interesse(rt) == "foco" else s.capture_grid_interval_s
        if rt.frame is not None and time.monotonic() - rt.frame.mono < interval:
            return None                           # o painel já está em dia: nada de screencap a mais
        try:
            resultado = await self._ciclo_de_previa(rt)
        except DriverError as exc:
            log.debug("%s: captura da prévia pela observação falhou: %s", rt.id, exc)
            metricas.contar("captura.total", origem="previa", resultado="falha")
            self._falha_de_captura(rt, f"{type(exc).__name__}: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 - a prévia nunca derruba a observação da IA
            log.exception("%s: erro na captura da prévia pela observação", rt.id)
            metricas.contar("captura.total", origem="previa", resultado="falha")
            self._falha_de_captura(rt, type(exc).__name__)
            return None
        return rt.frame.info.id if resultado == "capturada" and rt.frame is not None else None

    async def completar_imagem(self, rt: DeviceRuntime, obs: Observation, *, timeout: float,
                               lado_max: int | None = None) -> Observation:
        """A imagem de uma observação que saiu só com a árvore, quando a necessidade aparece DEPOIS (a receita
        divergiu; o verificador vai julgar pela visão). Mesma árvore, imagem adquirida em seguida pelo mesmo
        executor, sem ação no meio. Tela sensível — nesta árvore ou numa leitura mais nova — continua sem imagem."""
        if obs.image_omitted != "policy" or self._previa_sensivel(rt) or rt.executor.em_trecho_sensivel:
            return obs
        t_imagem = time.perf_counter()
        completa = await self._observar_imagem(rt, obs.tree, obs.package, obs.tree_at or obs.ts, timeout=timeout,
                                               lado_max=lado_max, previa_sempre=False)
        # A árvore é a mesma da observação de antes; a imagem é só a desta chamada (31.24).
        completa.ms_arvore, completa.ms_imagem = obs.ms_arvore, (time.perf_counter() - t_imagem) * 1000
        return completa

    async def imagem_tardia(self, rt: DeviceRuntime, *, timeout: float) -> tuple[bytes, str] | None:
        """Imagem para EVIDÊNCIA de uma observação que não tinha (política): adquirida agora, com o próprio horário,
        e nunca usada para coordenada. `None` quando a tela é (ou pode ser) sensível: a classificação precisa ser
        desta geração e recente — senão a hierarquia é relida antes, porque evidência fica guardada."""
        ex = rt.executor
        c = rt.classificacao
        if c is None or c[0] != rt.geracao or time.monotonic() - c[2] > CLASSIFICACAO_FRESCA_S:
            self.arvore(rt, await ex.run(rt.io.page_source, timeout=timeout, label="hierarquia (evidência)"))
        if self._previa_sensivel(rt) or ex.em_trecho_sensivel:
            return None
        geracao, antes = rt.geracao, ex.trechos_sensiveis
        remota = self._captura_remota(rt)
        if remota is None:
            png = await self._screencap(rt, origem="evidencia", timeout=timeout, label="screenshot (evidência)")
        else:
            cod = await self._capturar_na_origem(rt, remota, origem="evidencia", timeout=timeout,
                                                 label="screenshot (evidência)", cheia=True)
        quando = now_iso()
        # Geração conferida como em `_observar_imagem`: o aparelho que saiu do ar no meio da captura deixa uma
        # imagem do Android de ANTES — e `_nova_geracao` apaga a classificação, então `_previa_sensivel` sozinho
        # diria "não sensível" e a imagem viraria evidência desta etapa com o horário de agora.
        if (rt.geracao != geracao or ex.em_trecho_sensivel or ex.trechos_sensiveis != antes
                or self._previa_sensivel(rt)):
            metricas.contar("captura.total", origem="evidencia", resultado="descartada")
            return None
        if remota is None:
            t0 = time.perf_counter()
            cod = await asyncio.to_thread(_codificar, png, previa=False, cheia=True, lado_max=None)
            metricas.observar("codificacao.ms", (time.perf_counter() - t0) * 1000, tipo="evidencia")
        if rt.geracao != geracao or ex.em_trecho_sensivel or self._previa_sensivel(rt):
            metricas.contar("captura.total", origem="evidencia", resultado="descartada")
            return None                           # ficou sensível (ou saiu do ar) enquanto codificava
        self._lembrar_dimensoes(rt, cod.largura, cod.altura)
        metricas.contar("captura.total", origem="evidencia", resultado="ok")
        return cod.cheia or b"", quando

    async def _observar_imagem(self, rt: DeviceRuntime, tree: UiTree, pkg: str | None, tree_at: str, *,
                               timeout: float, lado_max: int | None, previa_sempre: bool) -> Observation:
        """A imagem de uma observação, LOGO depois da árvore: um screencap, UMA decodificação e só as codificações
        que alguém vai consumir — a do modelo (`lado_max`) ou a cheia (quem pediu `imagem=True`), e a da prévia
        (cheia + miniatura) se há interesse. Bytes que coincidem são reaproveitados, nunca recodificados."""
        ex = rt.executor
        geracao, antes = rt.geracao, ex.trechos_sensiveis
        remota = self._captura_remota(rt)
        t0 = time.perf_counter()
        if remota is None:
            png = await self._screencap(rt, origem="observacao", timeout=timeout, label="screenshot")
        else:
            # Na origem, a codificação vem junto: quais codificar se decide ANTES (na local, depois do screencap).
            previa = previa_sempre or not self._previa_pausada(rt)
            cod = await self._capturar_na_origem(rt, remota, origem="observacao", timeout=timeout,
                                                 label="screenshot", previa=previa, cheia=lado_max is None,
                                                 lado_max=lado_max)
        metricas.observar("observacao.ms", (time.perf_counter() - t0) * 1000, parte="imagem", instancia=rt.id)
        image_at = now_iso()
        if ex.em_trecho_sensivel or ex.trechos_sensiveis != antes:
            # A digitação de uma credencial começou enquanto o screencap esperava na fila: a imagem é descartada.
            metricas.contar("captura.total", origem="observacao", resultado="descartada")
            w, h = _tamanho_png(png) if remota is None else (cod.largura, cod.altura)
            frame = self._marcar_sensivel(rt, w, h, capturou=True)
            return Observation(frame_id=frame.info.id, ts=tree_at, width=w, height=h, jpeg=None, tree=tree,
                               package=pkg, sensitive=tree.sensitive, tree_at=tree_at, image_at=None,
                               image_omitted="sensitive", runtime_gen=rt.geracao)
        if remota is None:
            previa = previa_sempre or not self._previa_pausada(rt)
            cod = await asyncio.to_thread(_codificar, png, previa=previa, cheia=lado_max is None,
                                          lado_max=lado_max)
        for tipo, ms in cod.ms.items():
            # Na origem, o tempo é o da máquina do worker: rótulo próprio, para não misturar com o do central.
            metricas.observar("codificacao.ms", ms, tipo=tipo, **({"via": "worker"} if remota is not None else {}))
        # A codificação correu fora do executor (ver `_publicar_imagem`): o que valia antes dela pode não valer mais.
        if rt.geracao != geracao:
            metricas.contar("captura.total", origem="observacao", resultado="descartada")
            raise DriverError("o aparelho saiu do ar e voltou durante a observação; observe de novo",
                              effect_possible=False)
        if self._previa_sensivel(rt) or ex.em_trecho_sensivel:
            # Uma hierarquia lida DEPOIS do screencap classificou a tela como sensível: a imagem não vai a lugar
            # nenhum (nem ao modelo). A decisão segue pela árvore desta observação.
            metricas.contar("captura.total", origem="observacao", resultado="sensivel")
            frame = rt.frame if rt.frame is not None and rt.frame.sensitive else \
                self._marcar_sensivel(rt, cod.largura, cod.altura, capturou=True)
            return Observation(frame_id=frame.info.id, ts=tree_at, width=cod.largura, height=cod.altura, jpeg=None,
                               tree=tree, package=pkg, sensitive=tree.sensitive, tree_at=tree_at, image_at=None,
                               image_omitted="sensitive", runtime_gen=rt.geracao)
        metricas.contar("captura.total", origem="observacao", resultado="ok")
        self._lembrar_dimensoes(rt, cod.largura, cod.altura)
        if previa:
            frame = self._publicar_imagem(rt, geracao, cod.largura, cod.altura, cod.cheia or b"", cod.miniatura or b"")
            frame_id = frame.info.id if frame is not None else self._novo_frame_id(rt)
        else:
            frame_id = self._novo_frame_id(rt)
        return Observation(frame_id=frame_id, ts=image_at, width=cod.largura, height=cod.altura,
                           jpeg=cod.modelo if lado_max is not None else cod.cheia, tree=tree, package=pkg,
                           sensitive=tree.sensitive, tree_at=tree_at, image_at=image_at, runtime_gen=rt.geracao,
                           source="worker_local" if remota is not None else "central_adb")

    def _novo_frame_id(self, rt: DeviceRuntime) -> str:
        rt.frame_seq += 1
        return f"{rt.id}-{rt.frame_seq}-{int(time.time() * 1000)}"

    def _lembrar_dimensoes(self, rt: DeviceRuntime, w: int, h: int) -> None:
        """Tamanho da tela por orientação, desta geração: é o que a observação sem imagem usa."""
        if rt.dimensoes_geracao != rt.geracao:
            rt.dimensoes, rt.dimensoes_geracao = {}, rt.geracao
        rt.dimensoes["landscape" if w > h else "portrait"] = (w, h)

    async def _dimensoes_sem_imagem(self, rt: DeviceRuntime, xml: str, *, timeout: float) -> tuple[int, int] | None:
        """Tamanho da tela SEM imagem (contrato C1): o do último frame desta geração na orientação que a própria
        hierarquia declara, ou `wm size`. `None` = não se sabe, e quem chamou adquire a imagem."""
        orientacao = _orientacao_da_hierarquia(xml)
        conhecidas = rt.dimensoes if rt.dimensoes_geracao == rt.geracao else {}
        if orientacao is not None:
            if orientacao in conhecidas:
                return conhecidas[orientacao]
            outra = "portrait" if orientacao == "landscape" else "landscape"
            if outra in conhecidas:
                w, h = conhecidas[outra]
                return h, w
        elif len(conhecidas) == 1:                # sem rotação na hierarquia: só vale se só uma orientação foi vista
            return next(iter(conhecidas.values()))
        if orientacao is None:
            return None
        fisico = await self._wm_size(rt, timeout=timeout)
        if fisico is None:
            return None
        w, h = min(fisico), max(fisico)           # `wm size` é o tamanho físico, na orientação natural (retrato)
        dims = (h, w) if orientacao == "landscape" else (w, h)
        self._lembrar_dimensoes(rt, *dims)
        return dims

    async def _wm_size(self, rt: DeviceRuntime, *, timeout: float) -> tuple[int, int] | None:
        # No harness, `rt.adb` é o adb DE VERDADE apontado para um serial que não existe: nunca chamá-lo em teste.
        fn = getattr(rt.io, "wm_size", None) if self.io_factory is not None else rt.adb.wm_size
        if fn is None:
            return None
        try:
            return await rt.executor.run(fn, timeout=min(timeout, 15), label="tamanho da tela (wm size)")
        except DriverTimeout:
            raise
        except (DriverError, AdbError):
            return None

    # ------------------------------------------------------------------ controle (IA × usuário)
    def ai_begin(self, rt: DeviceRuntime) -> bool:
        """O worker da IA pede o aparelho. Negado se o usuário tem (ou pediu) o controle."""
        if rt.control != ControlOwner.none or rt.takeover_requested or rt.executor.has_zombie:
            return False
        rt.control, rt.control_since = ControlOwner.ai, now_iso()
        self._control_event(rt, "IA assumiu o aparelho")
        return True

    def ia_no_controle(self, rt: DeviceRuntime) -> bool:
        """A IA está operando o aparelho: objetivo em execução ou trabalho exclusivo (instalar, autenticar) — o
        escalonador segura `ai_begin` … `ai_end` pela duração inteira. Enquanto isso, nem a prévia do painel
        (`_volta_da_previa`) nem o acerto do relógio (`conferir_relogio_do_convidado`) entram na fila do aparelho por
        conta própria. Pública para o escalonador perguntar o mesmo."""
        return rt.control == ControlOwner.ai

    def ai_end(self, rt: DeviceRuntime) -> None:
        self.touch(rt)
        if rt.clock_state == "adiado":
            # O acerto que esperou a IA: o monitor (volta de 6 s) reconfere já, em vez de esperar o intervalo pendente.
            rt.clock_checked_mono = 0.0
        if rt.control != ControlOwner.ai:
            return
        if rt.takeover_requested and rt.pending_lease_id:
            self._grant_user(rt, rt.pending_lease_id)
        else:
            rt.control, rt.control_since = ControlOwner.none, None
            self._control_event(rt, "IA liberou o aparelho")

    def _grant_user(self, rt: DeviceRuntime, lease_id: str) -> None:
        rt.control, rt.control_since = ControlOwner.user, now_iso()
        rt.lease_id, rt.pending_lease_id, rt.takeover_requested = lease_id, None, False
        rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S
        rt.attention = "Controle manual ativo — a execução automática deste aparelho está suspensa."
        self._control_event(rt, "Controle manual concedido ao usuário")

    def _control_event(self, rt: DeviceRuntime, message: str) -> None:
        self.bus.emit("control.changed", f"{rt.id}: {message}", instance_id=rt.id,
                      data={"instance_id": rt.id, "control": rt.control.value, "pending": rt.takeover_requested})
        # 14.13: o fato do controle já está no log; o DTO que segue só vai persistido se algo MAIS mudou.
        rt.controle_anunciado = (rt.control.value, rt.takeover_requested)
        self.publish(rt)

    def request_control(self, rt: DeviceRuntime, *, por: str | None = None) -> tuple[str, str]:
        """`por`: quem pediu (a rota passa o autor da sessão), levado ao sinal `tomou_controle` (ADR-054)."""
        if rt.control == ControlOwner.user and rt.lease_id:
            rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S
            return "granted", rt.lease_id
        if rt.control == ControlOwner.ai:
            # a IA termina a ação em andamento e cede num ponto seguro
            primeiro_pedido = not rt.pending_lease_id
            pendente = rt.pending_lease_id or new_token()
            rt.pending_lease_id = pendente
            rt.takeover_requested = True
            self._control_event(rt, "Usuário pediu o controle; aguardando a IA concluir a ação atual")
            atual = rt.current
            if primeiro_pedido and atual is not None and atual.run_id and atual.step_id:
                # Uma tomada, um aviso (o clique repetido enquanto a IA cede não é outra tomada); sem etapa em curso
                # (trabalho exclusivo: instalar, autenticar) não há o que aprender.
                avisar(self.costura_de_controle.tomou_controle,
                       TomadaDeControle(rt.id, atual.run_id, atual.objective_id, atual.step_id, quem=por))
            return "pending", pendente
        lease = new_token()
        self._grant_user(rt, lease)
        return "granted", lease

    def release_control(self, rt: DeviceRuntime, lease_id: str) -> None:
        if rt.takeover_requested and rt.pending_lease_id == lease_id:
            rt.takeover_requested, rt.pending_lease_id = False, None
            self._control_event(rt, "Pedido de controle cancelado")
            return
        if rt.control != ControlOwner.user or rt.lease_id != lease_id:
            raise ControlError("not_controller", "Este lease não controla o aparelho.")
        self._end_user_control(rt, "Usuário devolveu o controle; a IA vai observar a tela novamente antes de agir")

    def _end_user_control(self, rt: DeviceRuntime, message: str | None) -> None:
        rt.control, rt.control_since, rt.lease_id = ControlOwner.none, None, None
        rt.attention = None
        if message:
            self._control_event(rt, message)
        self.on_device_free()
        self.on_control_released(rt)

    def _check_lease(self, rt: DeviceRuntime, lease_id: str) -> None:
        if rt.control != ControlOwner.user or rt.lease_id != lease_id:
            raise ControlError("not_controller", "Assuma o controle do aparelho antes de interagir.")
        rt.lease_expires_mono = time.monotonic() + MANUAL_LEASE_TTL_S

    async def manual_input(self, rt: DeviceRuntime, inp: ManualInput) -> None:
        self._check_lease(rt, inp.lease_id)
        self.touch(rt)
        if rt.state != InstanceState.online:
            raise ControlError("offline", "O aparelho não está online.")
        seen = rt.recent_frames.get(inp.frame_id)
        s = self.get_settings()
        if seen is None or rt.frame is None:
            raise ControlError("stale_frame", "A interação se refere a um frame que o backend não reconhece mais.")
        mono, fw, fh = seen
        if (time.monotonic() - mono) * 1000 > max(s.frame_max_age_ms, s.capture_focus_interval_s * 3000):
            raise ControlError("stale_frame", "O frame exibido está antigo demais para uma ação segura.")
        if (fw, fh) != (rt.frame.info.width, rt.frame.info.height):
            raise ControlError("frame_mismatch", "A orientação/tamanho da tela mudou desde o frame exibido.")

        def pt(x: float | None, y: float | None) -> tuple[int, int]:
            if x is None or y is None or not (0 <= x < fw and 0 <= y < fh):
                raise ControlError("bad_coordinates", "Coordenadas fora da tela do aparelho.")
            return int(x), int(y)

        # Modo treinamento: a tela de ANTES do toque é o que diz QUAL elemento a pessoa escolheu. Custa uma leitura
        # de hierarquia por entrada (~0,5 s) — só enquanto grava, e a tela avisa que o treinamento é mais lento.
        arvore_antes = await self._arvore_para_treino(rt) if rt.training_session_id else None

        t = inp.type
        if t == "tap":
            x, y = pt(inp.x, inp.y)
            await rt.executor.run(self._manual(rt).tap, x, y, timeout=20, label="toque manual")
            desc = f"toque em ({x},{y})"
        elif t == "long_press":
            x, y = pt(inp.x, inp.y)
            await rt.executor.run(self._manual(rt).long_press, x, y, inp.duration_ms or 800, timeout=25, label="toque longo manual")
            desc = f"toque longo em ({x},{y})"
        elif t == "swipe":
            x, y = pt(inp.x, inp.y)
            x2, y2 = pt(inp.x2, inp.y2)
            await rt.executor.run(self._manual(rt).swipe, x, y, x2, y2, inp.duration_ms or 300, timeout=25, label="arraste manual")
            desc = f"arraste ({x},{y})→({x2},{y2})"
        elif t == "key":
            if not inp.key:
                raise ControlError("bad_input", "Tecla não informada.")
            await rt.executor.run(self._manual(rt).press_key, inp.key, timeout=20, label="tecla manual")
            desc = f"tecla {inp.key}"
        else:  # text — o conteúdo digitado nunca vai para o log (pode ser credencial)
            text = inp.text or ""
            if not text:
                raise ControlError("bad_input", "Texto vazio.")
            if rt.store:
                # Decisão 4 do plano (dono, 24/09): a loja abre e opera como os outros aparelhos, e o texto pelo
                # painel passa a valer nela. O que continua SEM passar pelo backend é a SENHA da conta Google: campo
                # de senha em foco (ou tela sensível) recusa e manda digitar na janela do emulador. Sem conseguir
                # ler a tela, o que tem cara de senha ou de código também é recusado — o resto é digitado.
                await self._recusar_senha_na_loja(rt, text)
            try:
                if rt.session.connected or self.io_factory is not None:
                    await rt.executor.run(lambda: rt.io.type_text(text, clear_first=False), timeout=30, label="digitação manual")
                else:
                    await rt.executor.run(rt.adb.input_text_ascii, text, timeout=30, label="digitação manual")
            except AdbError as exc:
                raise ControlError("bad_input", str(exc)) from exc
            desc = f"digitação de {len(text)} caractere(s)"
        self.bus.emit("log", f"{rt.id}: entrada manual — {desc}", instance_id=rt.id)
        rt.capture_now.set()
        if rt.training_session_id and self.on_training_input is not None:
            try:
                self.on_training_input(rt, {"type": t, "x": inp.x, "y": inp.y, "x2": inp.x2, "y2": inp.y2,
                                            "key": inp.key, "text": inp.text if t == "text" else None}, arvore_antes)
            except Exception:  # noqa: BLE001 - gravar é acessório: a entrada já aconteceu no aparelho
                log.exception("%s: entrada não gravada no treinamento", rt.id)

    async def _arvore_para_treino(self, rt: DeviceRuntime) -> Any:
        try:
            xml = await rt.executor.run(rt.io.page_source, timeout=15, label="hierarquia (treinamento)")
            return self.arvore(rt, xml, max_elements=400)
        except Exception as exc:  # noqa: BLE001 - sem árvore a entrada ainda é gravada, só sem o elemento
            log.info("%s: hierarquia indisponível para o treinamento: %s", rt.id, exc)
            return None

    async def _recusar_senha_na_loja(self, rt: DeviceRuntime, text: str) -> None:
        from ..security.redaction import parece_senha_ou_codigo  # noqa: PLC0415
        arvore = await self._arvore_para_treino(rt)
        foco = next((e for e in (arvore.elements if arvore is not None else []) if e.focused), None)
        # Toda tela da loja já é "sensível" (nunca vai à IA), então o critério aqui é o CAMPO: senha em foco.
        senha = (foco is not None and foco.password) or (arvore is None and parece_senha_ou_codigo(text))
        if senha:
            raise ControlError("store_password_blocked",
                               "Campo de senha na loja: digite a senha da conta Google direto na janela do emulador — "
                               "ela nunca passa pelo backend. Os demais textos podem ser digitados pelo painel.")

    def _manual(self, rt: DeviceRuntime) -> Any:
        """Entradas manuais: ADB `input` (independe do Appium). Nos testes, o aparelho falso."""
        return rt.io if self.io_factory is not None else _AdbInput(rt.adb)

    async def quick_key(self, rt: DeviceRuntime, key: str) -> None:
        self._guard_not_running_ai(rt)
        # Pelo mesmo caminho da entrada manual: nos testes, o `rt.adb` é o adb REAL, e uma tecla por ele chegava ao
        # emulador de verdade com o mesmo serial — um HOME da suíte já derrubou uma prova de abertura em andamento.
        await rt.executor.run(self._manual(rt).press_key, key, timeout=20, label=f"tecla {key}")
        rt.capture_now.set()

    # ------------------------------------------------------------------ apps
    def resolve_apk(self, apk_path: str) -> Path:
        """Só instala APKs que estejam dentro dos diretórios permitidos (config paths.apk_dirs)."""
        p = self.cfg.path(apk_path).resolve()
        if p.suffix.lower() != ".apk" or not p.is_file():
            raise ValueError(f"APK não encontrado: {apk_path}")
        if not any(p.is_relative_to(d) for d in self.cfg.apk_dirs):
            allowed = ", ".join(self.cfg.file.paths.apk_dirs)
            raise ValueError(f"Caminho de APK fora dos diretórios permitidos ({allowed}).")
        return p

    async def force_stop_app(self, rt: DeviceRuntime, package: str) -> None:
        """Estado conhecido para a recuperação automática: encerra o app (um app travado/sem desenhar a tela não
        se recupera sozinho — visto num aparelho recém-ligado com pouca memória)."""
        fn = getattr(rt.io, "force_stop", None) if self.io_factory is not None else rt.adb.force_stop
        if fn is None:
            return
        try:
            await rt.executor.run(fn, package, timeout=30, label="encerrar app")
            self.bus.emit("log", f"{rt.id}: {package} encerrado para recomeçar de um estado conhecido", instance_id=rt.id)
        except (DriverError, AdbError) as exc:
            log.warning("%s: force-stop de %s falhou: %s", rt.id, package, exc)

    async def app_version(self, rt: DeviceRuntime, package: str) -> str:
        """Versão instalada do app NESTE aparelho (chave das receitas). Em cache até instalar outro APK ou religar."""
        if package not in rt.app_versions:
            rt.app_versions[package] = await rt.executor.run(rt.io.app_version, package, timeout=25, label="versão do app")
        return rt.app_versions[package]

    async def variant_of(self, rt: DeviceRuntime) -> str:
        """Variante de interface deste aparelho: `idioma/densidade` (ex.: `en-US/xhdpi`).

        Entra na identidade da receita porque a MESMA etapa, no mesmo app e na mesma versão, tem tela diferente em
        idioma diferente. Sem isso a quarentena seria global: um aparelho em outro idioma tiraria de circulação a
        receita que funciona para todos os demais.
        """
        if rt.ui_variant is None:
            locale = (await rt.executor.run(rt.adb.getprop, "ro.product.locale", timeout=20,
                                            label="idioma do aparelho")).strip()
            density = await rt.executor.run(rt.adb.wm_density, timeout=20, label="densidade do aparelho")
            rt.ui_variant = f"{locale or 'desconhecido'}/{_density_bucket(density)}"
        return rt.ui_variant

    async def open_app(self, rt: DeviceRuntime, app: Any, *, pela_execucao: bool = False) -> tuple[bool, str]:
        """Abre o app e CONFERE que ele chegou ao primeiro plano. Devolve `(abriu, detalhe)`.

        O retorno do `am start` é positivo mesmo quando o app cai na abertura — era por isso que "Abrir app"
        virava `succeeded` sem prova nenhuma. A sonda é a mesma do instalador (`wait_for_focus`, janela em foco =
        pacote); sem comprovação quem chamou grava `uncertain` com o motivo, nunca sucesso.

        `pela_execucao`: quem abre é a própria execução que segura o aparelho (o ponto de partida da prova, 30.42).
        A trava contra a IA protege o aparelho de um comando de fora no meio da execução; aplicada à própria
        execução, recusava toda abertura (medido no central em 04/10: 3 de 3 provas com "não confirmado").
        """
        if not pela_execucao:
            self._guard_not_running_ai(rt)
        # Outro app na frente (o secundário que ficou aberto, medido no android-01 em 26/09: dois "Abrir app" do
        # Instagram seguidos terminaram `uncertain` e, depois de um HOME, ele abriu na hora): volta à tela inicial
        # antes do `am start`. Custa uma leitura de foco; com o foco no próprio app ou em transição, nada muda.
        try:
            dono, _ = await rt.executor.run(rt.adb.current_focus, timeout=15, label="janela em foco")
            if dono and dono != app["package"]:
                await rt.executor.run(rt.adb.keyevent, "home", timeout=20, label="tela inicial")
        except (AdbError, DriverError) as exc:
            log.info("%s: sem ler o foco antes de abrir %s (%s); abre assim mesmo", rt.id, app["package"], exc)
        # `am start -n pkg/.Activity` aceita nome relativo; sem activity usa o launcher do pacote
        await rt.executor.run(rt.adb.start_app, app["package"], app["activity"] or None, timeout=40, label="abrir app")
        rt.capture_now.set()
        if rt.training_session_id and self.on_training_input is not None:
            try:
                self.on_training_input(rt, {"type": "open_app", "app_id": app["id"]}, None)
            except Exception:  # noqa: BLE001
                log.exception("%s: abertura de app não gravada no treinamento", rt.id)
        if self.io_factory is not None:        # testes: aparelho falso, sem janela de verdade para sondar
            return True, "driver de teste"
        pacote = app["package"]
        if await wait_for_focus(rt, pacote, deadline_s=LAUNCH_DEADLINE_S):
            return True, f"{pacote} está em primeiro plano"
        return False, (f"o pedido de abertura foi aceito, mas {pacote} não apareceu em primeiro plano em "
                       f"{LAUNCH_DEADLINE_S:.0f} s; pode estar abrindo, ou ter caído na abertura")

    async def list_packages(self, rt: DeviceRuntime) -> list[str]:
        return await rt.executor.run(rt.adb.list_packages, timeout=40, label="listar pacotes")

    async def hierarchy(self, rt: DeviceRuntime) -> UiTree:
        if not await self.ensure_automation(rt):
            raise DriverError(rt.automation.detail or "Sessão de automação indisponível", effect_possible=False)
        lida = rt.automation                       # a "geração" da sessão que esta leitura usa (cada transição troca o objeto)
        try:
            xml = await rt.executor.run(rt.io.page_source, timeout=40, label="hierarquia")
        except DriverError as exc:
            # `ensure_automation` confia no "pronta": uma sessão que morreu por baixo (reboot, instrumentation encerrada) devolvia
            # 503 para sempre. Leitura que recebe o erro DE SESSÃO PERDIDA recria UMA vez e relê; UI ocupada ou outro erro
            # seguem como estavam. Só quando a plataforma ainda acredita "pronta": se já está em `error`, quem retenta é
            # o monitor (espaçado), e uma leitura não gasta o orçamento de falhas que degrada o aparelho.
            # `rt.automation is not lida`: outra coroutine (tarefa, monitor, readoção) já trocou a sessão enquanto esta
            # leitura esperava; o erro é da sessão ANTIGA e invalidar agora derrubaria a nova, que está boa.
            if (not sessao_perdida(exc) or rt.automation is not lida or rt.automation.state != "ready"
                    or rt.state != InstanceState.online):
                raise
            self.invalidate_automation(rt, str(exc).splitlines()[0][:300])
            if not await self.ensure_automation(rt):
                raise DriverError(rt.automation.detail or "Sessão de automação indisponível", effect_possible=False) from exc
            xml = await rt.executor.run(rt.io.page_source, timeout=40, label="hierarquia")
        return self.arvore(rt, xml, max_elements=400)


def _hw_signature(a: Any) -> str:
    """Um snapshot só carrega no MESMO hardware/imagem em que foi salvo."""
    import hashlib

    raw = "|".join(str(v) for v in (a.system_image, a.ram_efetiva(), a.cores, a.width, a.height, a.density,
                                    a.gpu_mode, a.data_partition, *a.args_extras_efetivos()))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _wait_lock(lock: threading.Lock) -> None:
    with lock:
        pass


class _AdbInput:
    def __init__(self, adb: Adb):
        self.tap, self.long_press, self.swipe, self.press_key = adb.tap, adb.long_press, adb.swipe, adb.keyevent


_ROTACAO = re.compile(r'<hierarchy\b[^>]*?\brotation="(\d)"')


def _orientacao_da_hierarquia(xml: str) -> str | None:
    """A orientação que o próprio dump declara (`<hierarchy rotation="N">`): 1 e 3 são paisagem. `None` sem o
    atributo."""
    m = _ROTACAO.search(xml[:1000])
    if m is None:
        return None
    return "landscape" if m.group(1) in ("1", "3") else "portrait"


def _encode_frame(png: bytes) -> tuple[bytes, bytes, int, int]:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    full = io.BytesIO()
    img.save(full, "JPEG", quality=72, optimize=False)
    th = img.resize((THUMB_WIDTH, max(1, round(h * THUMB_WIDTH / w)))) if w > THUMB_WIDTH else img
    thumb = io.BytesIO()
    th.save(thumb, "JPEG", quality=62)
    return full.getvalue(), thumb.getvalue(), w, h


def _density_bucket(density: int | None) -> str:
    """A faixa da tela, para a identidade da receita. A tabela é a MESMA que escolhe o split de densidade.

    Havia duas tabelas com limites diferentes neste repositório (esta e a de `devices/installer.py`), e elas
    discordavam: 190 dpi era `mdpi` para o split e `hdpi` para a receita; 400 dpi, `xhdpi` num lugar e `xxhdpi`
    no outro. Agora há uma só — `installer.density_bucket`, pelos pontos médios das faixas do Android.
    """
    from .installer import density_bucket

    return density_bucket(density, desconhecida="desconhecida")


def gms_do_aparelho(propriedade: str | None, pacote_instalado: bool, *, externo: bool) -> bool | None:
    """O aparelho tem o Google Play Services? `ro.com.google.gmsversion` OU o pacote `com.google.android.gms`.

    A propriedade sozinha dizia "AOSP" para a imagem `google_apis`, que tem o GMS e não tem a propriedade. Nenhum
    dos dois num emulador NOSSO prova AOSP; num aparelho de outra máquina (físico, outra distribuição) a resposta
    honesta é "não se sabe".
    """
    if (propriedade or "").strip() or pacote_instalado:
        return True
    return None if externo else False
