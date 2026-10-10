"""Composição da aplicação: cria e liga os módulos (dispositivos, automação, planejamento, fila, eventos)."""
from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Awaitable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Callable

from . import marca_de_partida
from .automation.appium_server import AppiumServer
from .automation.driver import DeviceIO
from .automation.hierarchy import SUBTIPO_CONTA_TRAVADA
from .commands import despacho
from .commands.limpeza_ao_retirar import LimpezaAoRetirar
from .commands.reconciler import reconciliar_incertos
from .commands.outbox import CommandOutbox
from .commands.states import COMMAND_TERMINAL
from .bootstrap import montar
from .storage import DISK

from .commands.store import CommandStore, command_dto
from .commands.transport import build_transport
from .workers.local import LocalWorker
from .workers.registry import HEARTBEAT_S, WorkerRegistry
from .config import Config, LimitsCfg
from .db import Database, Row, dumps, loads
from .devices.manager import DeviceManager, DeviceRuntime
from .devices.rede_convergencia import ConvergenciaDeRede
from .devices.rede_saida_central import SaidaDoCentral
from .devices.rede_servidor import ServidorDeRede
from .devices.sdk import SdkTools
from .events import TELEMETRIA_KINDS, TELEMETRIA_RETENCAO_H, EventBus
from .metricas import metricas
from .modules.applications.infrastructure.app_repository import AppRepository
from .modules.operacoes.infrastructure.laco import LacoDasOperacoes
from .modules.avisos.infrastructure.contatos_sql import ContatosDoCanal
from .modules.avisos.infrastructure.convidados import ConvidadosDoTelegram
from .modules.avisos.infrastructure.entrada import ServicoDeEntrada, parece_codigo
from .modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from .modules.avisos.application.espelho import LinhaDeCusto
from .modules.avisos.infrastructure.espelho import EspelhoDoTrello, FontesDaCentral
from .modules.avisos.infrastructure.espelho_sql import CartoesDoTrello, CursorDoTrello
from .devices.captura_pontual import capturar_para_o_dono
from .modules.avisos.infrastructure.anexos_leitura import LeitorDeAnexo
from .modules.avisos.infrastructure.faxina_sql import FaxinaDosCanais
from .modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from .modules.avisos.infrastructure.portas_da_central import PortasReais, nomes_e_dados_da_persona
from .porta_do_plano import (AprovarPlanoBody, ItemAprovado, aprovar_pelo_canal, previa_da_porta,
                             previa_para_o_canal)
from .modules.avisos.infrastructure.servico import ServicoDeAvisos, trava_de_avisos_em_uso
from .modules.avisos.infrastructure.vigia_do_host import VigiaDoHost
from .decisoes_inversas import inversas_das_filas
from .modules.decisoes.application.desfazer import DesfazerDecisoes
from .modules.avisos.infrastructure.trello_leitor import ComentariosDoTrello, LeitorDoTrello
from .modules.avisos.infrastructure.trello_webhook import CadastroDoWebhook, PortaDoWebhook
from .modules.context_retrieval.adapters.jev import JevSemanticProvider
from .modules.email_do_parque.application.servico import EmailDoParque
from .modules.identity.application.ports import SessionProvider
from .modules.identity.application.session_rules import (CREDENCIAL_EM_REVISAO, aplicar_desafio, conta_para_conferir,
                                                         emit_needs_person_change, motivo_do_login_parado)
from .modules.identity.application.sessions import SessionProviders
from .modules.identity.infrastructure.verificacao_periodica import VerificacaoPeriodica
from .modules.identity.infrastructure.sessions import SessionDeps, SessionProviderFactory
from .modules.learning import esquecer_conta
from .modules.learning.application.falhas import ServicoDeFalhas
from .modules.learning.infrastructure import ligar_intencao, ligar_validacao, ligar_voz
from .modules.learning.infrastructure.curador_do_hub import CuradorDoHub
from .modules.learning.infrastructure.ligar_costuras import costuras_do_livro
from .modules.learning.infrastructure.montagem import montar_aprendizado
from .modules.learning.infrastructure.segredo import TriagemDeCredencial
from .modules.learning.infrastructure.validacoes_sql import RegistroDeValidacoesSql
from .modules.skills.application.registry import CompositeSkillRegistry
from .modules.skills.application.teaching import TeachingService
from .modules.skills.infrastructure.document_validator import DslDocumentValidator, LockedVersions
from .modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from .modules.skills.infrastructure.run_planning import SkillRunPlanner
from .modules.skills.infrastructure.secret_screen import RedactionSecretScreen
from .modules.skills.infrastructure.sql_repository import SqlSkillRepository
from .modules.skills.infrastructure.sql_teaching_repository import SqlTeachingRepository
from .models import (AiStatus, Health, InstalledAppState, InstanceState,
                     OFFLINE_POLICY_PADRAO, PersonaCreate, PersonaDTO, SessionStatus)
from .devices.installer import AppInstaller
from .planning import conciliacao, costs, saldos
from .planning.decisao_fechada import (DecisorJev, RepositorioDeSombra, construir_porta, observador_de_sombra,
                                       transparencia)
from .planning.decisao_fechada.sombra import ORIGEM_NO_GASTO
from .planning.decisao_fechada.curador import CuradorComTriagemEmSombra, TriagemDoCurador
from .planning.decisao_fechada.intencao import ConsumidorDeIntencao
from .planning.capabilities import Capability
from .planning.catalog import capabilities_of, pacote_ancora, session_factory_of
from .planning.anthropic_provider import AnthropicProvider
from .planning.provider import AIProvider, build_provider
from .modules.identity.infrastructure.persona_images import (compor_servico_de_imagens, identidade_para_foto,
                                                              imagens_dto)
from .releases.catalog import ReleaseValidationError
from .releases.inspector import ApkInspector
from .security import local_secret
from .security.secret_store import SecretStore, build_key_provider
from .security.sessions import PanelSessions, PortaoDeLogin
from .saude import SaudeDoSistema
from .releases.repository import ReleaseRepository
from .releases.service import ReleaseService
from .security.sensitive_input import SensitiveInputChannel
from .social.contas_nossas import MARCADOR
from .social.repository import SocialRepository, frase_da_quarentena, sessao_vencida
from .social.approvals import Approval, ApprovalService, ApprovalStore
from .social.persona_batch import LotesDePersona
from .social.policy import PolicyEngine
from .social.service import SocialService
from .convergencia import Convergencia
from .gates import PortaDaEtapa, Portoes
from .modules.pedidos.infrastructure.laco import LacoDePedidos
from .modules.pedidos.infrastructure.saldo import motivo_de_adiamento
from .modules.pedidos.infrastructure.servico import PedidosApi
from .modules.portal.montagem import Portal
from .taskqueue.flows import preencher_refs_publicas
from .taskqueue.repository import Repository
from .taskqueue.scheduler import Scheduler
from .taskqueue.service import RunService
from .taskqueue.sombra_intencao import SombraDaIntencao, catalogo_de
from .taskqueue.travas import (AVISOS, CURADORIA, PEDIDOS, RENOVAR_TRAVA_S, RETENCAO, SALDOS, TRAVAS_DOS_LACOS,
                               TravaPerdida)
from .training.generalizer import ProviderSkillGeneralizer
from .util import iso_in, now, now_iso, parse_iso, to_iso
from .vitrine import _apps_changed, laco_de_convergencia, trabalho_ao_ligar
from .version import VERSION, commit_em_execucao  # noqa: F401 - reexportado


if TYPE_CHECKING:
    from .bootstrap import SettingsStore
    from .commands.transport import CommandTransport
    from .modules.avisos.infrastructure.anexos import ArmazemDeAnexos
    from .modules.avisos.infrastructure.canais_frota import CanaisDaFrota
    from .modules.decisoes.infrastructure.registro_sql import RegistroSql
    from .modules.decisoes.infrastructure.servico import ServicoDeDecisoes
    from .modules.identity.application.persona_images import PersonaImageService
    from .modules.learning.application.servico import LearningService
    from .modules.learning.infrastructure.ligar_costuras import CosturasDoLivro
    from .planning.decisao_fechada.intencao import EntradaDeCatalogo
    from .planning.decisao_fechada.porta import Porta
    from .social.excecoes import ExcecoesDePolitica
    from .storage import Storage
    from .taskqueue.travas import Lideranca
    from .training.recorder import TrainingRecorder
    from .training.skills import TrainingSkills

log = logging.getLogger("poc")
#: Intervalo do livro-caixa das contas de IA (conciliação + fechamento diário). O relatório de uso da Anthropic é
#: horário e o da OpenAI diário: 10 min basta para a hora cheia aparecer logo, sem martelar a API de administração.
SALDOS_INTERVALO_S = 600
#: 29.50: de quanto em quanto tempo a pergunta sem resposta é conferida. A expiração (24 h) sai até 10 min depois do prazo.
EXPIRACAO_INTERVALO_S = 600
# `VERSION` e `commit_em_execucao` moram em `version.py` e são reexportados aqui: o agente do worker
# precisa dos dois e não pode importar `state` (ele traz banco, IA e a aplicação inteira).


#: De quanto em quanto tempo `health()` é recalculada para checar se algo mudou (achado #65). O painel só refaz
#: GET /api/health na carga e em reconexões — sem este laço, um Appium que cai e volta sem o WebSocket reconectar
#: deixava a pílula "Ambiente" e o cartão da Infraestrutura afirmando o estado antigo para sempre.
HEALTH_POLL_S = 30.0
#: Janela das métricas de desempenho gravadas em `measurements` (kind='metricas'). 15 min dá 96 linhas por dia —
#: pouca coisa para a retenção de `log_retention_days` — e ainda separa manhã de tarde numa comparação.
METRICAS_JANELA_S = 900.0

#: De quanto em quanto tempo o outbox tenta de novo o que o transporte recusou (item 5.6). Curto porque o que
#: está parado aqui é um comando que uma pessoa já pediu e o painel já mostra como aceito.
OUTBOX_RETRY_S = 15.0

#: Prazo TOTAL do desligamento para o que ainda pode gravar no banco (31.9): a volta do curador, as sombras da intenção e as
#: da porta `DecisaoFechada`. Estourou, o `stop()` segue e fecha o banco (a escrita tardia falha e é logada, nunca trava o
#: encerramento); `AppState.stop` o lê na hora, para o teste encolhê-lo.
ESPERA_DE_SOMBRAS_S = 6.0
#: 31.173: releituras SEGUIDAS da sessão vencida que falham (exceção do provedor) antes de o objetivo parar com motivo.
TETO_DE_RELEITURAS_DA_SESSAO = 3


class RelogioDivergente(RuntimeError):
    """O relógio desta máquina está longe demais do relógio do banco para este backend virar um SEGUNDO dono.

    Item 5.3 (achado #32): o vencimento de um lease é escrito por quem assume e lido por quem pergunta. Um
    backend adiantado enxerga como vencido o lease de uma etapa em plena execução, adota a etapa e passa a operar
    o MESMO aparelho que o dono legítimo — exatamente o que o lease existe para impedir. Enquanto existe um
    backend só, isso não faz diferença e o desvio é apenas avisado; a partir do segundo, subir é pior que não subir.
    """


class AppState:
    # Atributos montados por `bootstrap.montar` (15.15 K, F5c B): declarados aqui para o mypy e quem lê `state.<atributo>`.
    # A ordem é a da construção; o tipo de cada um é o que o mypy já inferia do antigo `__init__`.
    cfg: Config
    db: Database
    bus: EventBus
    tools: SdkTools
    settings: SettingsStore
    sessions: PanelSessions
    portao_de_login: PortaoDeLogin
    appium: AppiumServer
    sensitive_input: SensitiveInputChannel
    manage_appium: bool
    _clock_skew_s: float
    apps: AppRepository
    devices: DeviceManager
    provider: AIProvider
    storage: Storage
    avatares: Storage
    repo: Repository
    outbox: CommandOutbox
    lideranca: Lideranca
    canais_da_frota: CanaisDaFrota
    anexos_canal: ArmazemDeAnexos
    avisos: ServicoDeAvisos
    vigia_do_host: VigiaDoHost
    verificacao_periodica: VerificacaoPeriodica
    portal: Portal
    decisoes_registro: RegistroSql
    decisoes: ServicoDeDecisoes
    transport: CommandTransport
    commands: CommandStore
    workers: WorkerRegistry
    local_worker: LocalWorker
    release_repo: ReleaseRepository
    releases: ReleaseService
    installer: AppInstaller
    secrets: SecretStore
    social_repo: SocialRepository
    _releituras_do_teto: dict[tuple[str, str, str], str]
    _releituras_falhas: dict[tuple[str, str], int]
    _alvos_preparados: set[str]
    persona_images: PersonaImageService
    social: SocialService
    lotes_de_persona: LotesDePersona
    sessoes: SessionProviders[SessionProviderFactory]
    scheduler: Scheduler
    limpeza_ao_retirar: LimpezaAoRetirar
    rede_servidor: ServidorDeRede
    rede_convergencia: ConvergenciaDeRede
    rede_saida_central: SaidaDoCentral
    _entrega_imediata: set[str]
    _draft_locks: dict[str, asyncio.Lock]
    convergencia: Convergencia
    portoes: Portoes
    policies: PolicyEngine
    excecoes: ExcecoesDePolitica
    approvals: ApprovalStore
    approval_service: ApprovalService
    training: TrainingRecorder
    skills: TrainingSkills
    skill_repo: SqlSkillRepository
    skill_registry: CompositeSkillRegistry
    skill_planner: SkillRunPlanner
    decisao_sombra: RepositorioDeSombra
    decisao_fechada: Porta
    _curador_do_hub: CuradorDoHub
    _triagem_do_curador: TriagemDoCurador
    learning: LearningService
    decisoes_desfazer: DesfazerDecisoes
    _digestoes: set[asyncio.Task[None]]
    _laco_principal: asyncio.AbstractEventLoop | None
    teaching: TeachingService
    runs: RunService
    laco_das_operacoes: LacoDasOperacoes
    leitor_de_anexos: LeitorDeAnexo
    telegram_entrada: ServicoDeEntrada
    trello_espelho: EspelhoDoTrello
    trello_cadastro: CadastroDoWebhook
    trello_leitor: LeitorDoTrello
    trello_webhook: PortaDoWebhook
    catalogo_da_cadeia: Callable[[], list[EntradaDeCatalogo]]
    pedidos: LacoDePedidos
    pedidos_api: PedidosApi
    email_parque: EmailDoParque
    costuras: CosturasDoLivro
    _diag_cache: dict[str, object] | None
    _bg: list[asyncio.Task[object]]
    voltas_de_faxina: dict[str, int]
    saude: SaudeDoSistema
    _transport_cache: dict[str, str]

    def __init__(self, cfg: Config, *, provider: AIProvider | None = None,
                 io_factory: Callable[[DeviceRuntime], DeviceIO] | None = None, manage_appium: bool = True,
                 emulator: Any = None):
        montar(self, cfg, provider=provider, io_factory=io_factory, manage_appium=manage_appium, emulator=emulator)

    def _decisor_da_porta(self, cfg: Config) -> DecisorJev | None:
        """O decisor da porta `DecisaoFechada` (31.14). `nulo` de fábrica (devolve None: a porta usa o `DecisorNulo`).

        Com `ai.decisao_fechada.decisor: jev`, o `DecisorJev` usa o transporte do adaptador de retrieval (cliente único; a
        chave é lida do ambiente na hora do POST, nunca aqui), confere o gasto no HUB antes do POST (a mesma rubrica de
        toda chamada, com a fatia do Jev e o saldo da conta dele) e registra cada chamada em `ai_calls` pela sombra. Hub sem
        `conferir_gasto` (provedor que não roteia) = nada sai. Acima de tudo, `JEV_RUNTIME_SEND_APPROVED` (aberto no 31.17)."""
        if cfg.file.ai.decisao_fechada.decisor != "jev":
            return None
        conferir = getattr(self.provider, "conferir_gasto", None)
        return DecisorJev(JevSemanticProvider(),
                          conferir_gasto=None if conferir is None else (
                              lambda pedido: conferir(run_id=pedido.run_id, origem=ORIGEM_NO_GASTO, conta="typesafe",
                                                      reservar=True)),
                          registrar=self.decisao_sombra.registrar_chamada)

    def _publish_worker(self, worker_id: str) -> None:
        """Qualquer mudança observável de worker vira evento. A tela de infraestrutura vive disto."""
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            return
        dto = self.workers.dto(linha)
        self.bus.emit("worker.updated", f"worker {dto.name}: {dto.state}"
                      + (f" — {dto.state_detail}" if dto.state_detail else ""),
                      level="warn" if dto.state in ("offline", "degraded") else "info",
                      data={"worker": dto.model_dump(mode="json")})

    def _publish_worker_metrics(self, worker_id: str, resources: Any) -> None:
        """CPU/RAM/disco de CADA batida (achados #17/#143): efêmero de propósito — o painel atualiza a barra ao
        vivo, mas nada disto precisa sobreviver a uma reconexão (o snapshot seguinte já traz o valor atual) nem
        vale a pena persistir: era isto que enchia o log de eventos (57% das linhas).

        `last_seen_at` vai junto, e vai SEMPRE — mesmo sem `resources`. Medido no painel: com a Infraestrutura
        aberta, "Último contato" subia para "há 2 min 33 s — os dados abaixo podem estar desatualizados" enquanto
        a API dizia que a batida tinha 4 s. O painel só aprendia `last_seen_at` no snapshot inicial: a batida não
        muda nada observável, então (por desenho) não há `worker.updated`, e o evento efêmero só levava recurso.
        O selo de dado velho, que existe para denunciar um worker calado, passava a acusar todo worker vivo depois
        de um minuto de tela aberta.
        """
        dados: dict[str, Any] = {"worker_id": worker_id, "last_seen_at": to_iso(now())}
        if resources is not None:
            dados["resources"] = resources.model_dump(mode="json")
        self.bus.emit("worker.metrics", "worker metrics", data=dados)

    async def _worker_reaper_loop(self) -> None:
        """Ausência de batida é o que marca offline — não o socket fechado, que cai por rede piscando.

        O mesmo laço passa a sonda pelos comandos incertos: o aparelho local pode ser readotado pelo monitor a
        qualquer momento, e sem uma passada periódica o comando só seria fechado se alguém clicasse ou se a
        batida de um worker chegasse. Antes disto, `uncertain` não tinha saída nenhuma.
        """
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                # A batida do worker LOCAL vem antes do ceifador, e na mesma volta: sem ela o central seria
                # marcado offline em três batidas e todo comando de ciclo de vida desta máquina passaria a ser
                # recusado por "worker não está conectado".
                self.local_worker.batida()
            except Exception:  # noqa: BLE001 - a batida local nunca pode derrubar o backend
                log.exception("batida do worker local")
            try:
                self.workers.reap()
            except Exception:  # noqa: BLE001 - o ceifador nunca pode derrubar o backend
                log.exception("ceifador de workers")
            try:
                await asyncio.to_thread(self._probe_transport)
            except Exception:  # noqa: BLE001 - a sonda do túnel nunca pode derrubar o backend
                log.exception("sonda do túnel")
            try:
                reconciliar_incertos(self)
            except Exception:  # noqa: BLE001 - a sonda nunca pode derrubar o backend
                log.exception("reconciliação de comandos incertos")

    def _worker_process_of(self, worker_id: str, instance_id: str) -> tuple[str, bool, str | None] | None:
        """O que o worker reporta do PROCESSO daquele aparelho — `(nome, conectado, estado)`; `None` se o worker
        não está inscrito. Consultado pelo `DeviceManager` para que "desligado de propósito lá" pare de ser
        apresentado como problema de conexão do ADB (achado #61)."""
        return self.workers.processo_de(worker_id, instance_id)

    def _transport_state_of(self, worker_id: str) -> str | None:
        """Última leitura da sonda do túnel para este worker — consultada pelo `DeviceManager` para distinguir
        'túnel fora' de 'ADB não responde' (achado #179). `None` = nunca sondado (worker sem aparelho externo
        associado, ou ainda não passou a primeira volta do laço)."""
        return self._transport_cache.get(worker_id)

    def _probe_transport(self) -> None:
        """Sonda, por TCP, as portas LOCAIS que `scripts/worker-tunnel.ps1` encaminha para cada worker remoto
        (achado #179). `ssh -L porta:127.0.0.1:remota` mantém a porta local escutando mesmo com o lado remoto
        fora do ar: conexão recusada ali É o túnel caído; conexão aceita é túnel de pé (o aparelho do outro lado
        pode estar desligado — isso é outra causa, e continua sendo detectado pela batida/ADB de sempre).

        Só sonda workers com pelo menos um aparelho `external` vinculado (`rt.worker_id`): sem isso não há porta
        local nenhuma para testar, e o estado fica `unknown` — nunca se inventa 'down' por falta de dado.
        """
        for row in self.workers.rows():
            wid = row["id"]
            if wid == self.workers.local_worker_id:
                continue
            alvos = [rt for rt in self.devices.devices.values() if rt.worker_id == wid and rt.external]
            if not alvos:
                continue
            algum_ok = False
            recusas: list[str] = []
            for rt in alvos:
                host, _, porta = rt.serial.rpartition(":")
                if not host or not porta.isdigit():
                    continue
                try:
                    with socket.create_connection((host, int(porta)), timeout=1.5):
                        algum_ok = True
                except OSError:
                    recusas.append(rt.serial)
            estado = "up" if algum_ok else "down"
            detalhe = (None if algum_ok else
                      f"porta(s) local(is) do túnel recusando conexão: {', '.join(recusas)}")
            self._transport_cache[wid] = estado
            if self.workers.marcar_transporte(wid, estado, detalhe) and estado == "down":
                self.bus.emit("log", f"worker {row['name']}: túnel fora — {detalhe} (não é a máquina remota: a "
                              "porta LOCAL do túnel é que está recusando conexão; veja data/logs/tunel-*.log e "
                              "scripts/worker-tunnel.ps1)", level="error")

    def _forget_app_state(self, instance_id: str, motivo: str) -> None:
        """Depois de um wipe, o que estava instalado deixou de existir. As linhas de `device_app_state` do
        aparelho voltam a `missing` com `installed_release_id` nulo — é o que faz a porta do app reentregar e
        "Distribuir" reinstalar, em vez de responder "já está nesta versão" sobre um aparelho vazio."""
        linhas = self.db.query("SELECT package_name FROM device_app_state WHERE instance_id=? AND state<>?",
                               (instance_id, InstalledAppState.missing.value))
        for linha in linhas:
            self.release_repo.upsert_app_state(instance_id, linha["package_name"],
                                               state=InstalledAppState.missing, installed_release_id=None,
                                               observed_version_name=None, observed_version_code=None,
                                               verified_at=None, drift_kind=None, detail=motivo)
        if linhas:
            self.bus.emit("log", f"{instance_id}: o app deixou de constar como instalado — {motivo}",
                          level="warn", instance_id=instance_id)
        # O marcador de conta travada NÃO sai aqui: este gancho também dispara quando só a identidade física mudou
        # (`conferir_identidade`), sem disco apagado nem pessoa — e resolvia o marcador com "dados apagados", o que era
        # falso e devolvia o aparelho do desafio ao uso (revisão do pacote quarentena, 29/09). Ver `_disco_apagado`.

    def _dados_do_aparelho_perdidos(self, instance_id: str, motivo: str) -> None:
        """O gancho de wipe (`on_device_wiped`): o que estava no disco deixou de valer — o app instalado e, desde o
        25.4, a rede aplicada (o cliente VPN e o always-on saíram junto; troca de identidade física também conta:
        outro aparelho atrás do id não tem a nossa configuração). Um não impede o outro."""
        try:
            self._forget_app_state(instance_id, motivo)
        finally:
            try:
                self.rede_convergencia.invalidar(instance_id, motivo)
            except Exception:  # noqa: BLE001 - invalidar a rede nunca pode derrubar o ciclo do aparelho
                log.exception("%s: falha ao invalidar a rede depois do wipe", instance_id)

    def _disco_apagado(self, instance_id: str, motivo: str) -> None:
        """O disco foi apagado de fato (boot com `-wipe-data` ou reset concluído pelo agente): a conta travada não está
        mais logada ali (ADR-055). Numa quarentena o reset só chega com a confirmação explícita da pessoa, e manter o
        marcador travaria para sempre um aparelho já limpo. O PERFIL segue bloqueado: reativar é decisão de pessoa.
        A rede aplicada também saiu (idempotente com o `on_device_wiped` do mesmo reset)."""
        self.social_repo.resolver_conta_travada(instance_id, por="reset do aparelho",
                                                nota=f"dados do aparelho apagados — {motivo}")
        try:
            self.rede_convergencia.invalidar(instance_id, motivo)
        except Exception:  # noqa: BLE001 - invalidar a rede nunca pode derrubar o boot
            log.exception("%s: falha ao invalidar a rede depois do disco apagado", instance_id)

    def _conta_travada_em(self, instance_id: str) -> str | None:
        """O @ da conta travada logada no aparelho (marcador aberto, 054), ou `None`."""
        marcador = self.social_repo.conta_travada_no_aparelho(instance_id)
        return str(marcador["handle"]) if marcador is not None else None

    def quarentena(self, instance_id: str) -> str | None:
        """Por que o aparelho está em quarentena (conta travada logada, ADR-055), ou `None`. A mesma frase do 409
        do painel, para a porta do despacho, a entrega e a distribuição contarem a mesma história."""
        marcador = self.social_repo.conta_travada_no_aparelho(instance_id)
        return (frase_da_quarentena(marcador, conta=self.social_repo.citacao_da_conta(marcador))
                if marcador is not None else None)

    def _contas_travadas_no_ar(self) -> list[str]:
        """`aparelho (@conta)` de cada marcador aberto em aparelho LIGADO — no ar, subindo ou degradado no ar."""
        no_ar = (InstanceState.online, InstanceState.booting, InstanceState.error)
        saida: list[str] = []
        for m in self.social_repo.contas_travadas_abertas():
            rt = self.devices.devices.get(str(m["instance_id"]))
            if rt is not None and rt.state in no_ar:
                rotulo = self.social_repo.rotulo_da_conta(m)
                saida.append(f"{rt.id} ({'conta retirada, bloqueada' if rotulo == MARCADOR else rotulo})")
        return saida

    def pacotes_com_dado_velho(self, instance_id: str) -> list[str]:
        """Pacotes cuja afirmação "está instalado aqui" passou da validade neste aparelho.

        A idade sai de `verified_at`. `ready`/`installed` sem `verified_at` nenhum também conta: é exatamente o
        estado de quem foi marcado por uma instalação e nunca mais foi olhado.

        O app do aparelho que NUNCA foi observado entra também: "apps instalados" só valia onde havia release
        gerenciada, e sem linha nenhuma a porta do app não opinava — era assim que uma tarefa de Instagram entrava
        num aparelho sem Instagram instalado (#51). Nunca observado é o dado mais velho que existe, e quem
        responde é o aparelho, por ADB (`verify_on` → `pm`), não uma suposição daqui.
        """
        limite = self.cfg.file.releases.verify_max_age_h
        if limite <= 0:
            return []
        corte = to_iso(now() - timedelta(hours=limite))
        linhas = self.db.query(
            "SELECT package_name, verified_at FROM device_app_state WHERE instance_id=? AND state IN (?,?)",
            (instance_id, InstalledAppState.ready.value, InstalledAppState.installed.value))
        pacotes = [r["package_name"] for r in linhas if not r["verified_at"] or r["verified_at"] < corte]
        proprio = self._pacote_do_aparelho(instance_id)
        if proprio and self.release_repo.app_state(instance_id, proprio) is None:
            pacotes.append(proprio)
        return pacotes

    #: Estados de app que o operador precisa ver em vermelho/amarelo quando chegam sozinhos.
    _ESTADO_DE_APP_RUIM = ("install_failed", "verify_failed", "incompatible", "version_drift", "missing")

    def _publicar_estado_do_app(self, dto: Any) -> None:
        """`app_state.updated`: o desfecho de instalar/verificar/voltar de versão chega à tela por evento."""
        nivel = ("error" if dto.state.value in ("install_failed", "verify_failed", "incompatible")
                 else "warn" if dto.state.value in self._ESTADO_DE_APP_RUIM or dto.state.value == "verifying"
                 else "info")
        self.bus.emit("app_state.updated",
                      f"{dto.instance_id}: {dto.package_name} — {dto.state.value}"
                      + (f" ({dto.detail})" if dto.detail else ""),
                      level=nivel, instance_id=dto.instance_id,
                      data={"app_state": dto.model_dump(mode="json")})

    def pacotes_sem_desfecho(self, instance_id: str) -> list[str]:
        """Pacotes parados em `verifying` sem ninguém para relê-los. É a dívida que o reinício deixa.

        `reconcile_after_restart` põe a instalação interrompida em `verifying` dizendo "o estado será relido do
        aparelho" — e não havia quem relesse: a única chamada de `verify_on` era a rota manual. O aparelho ficava
        bloqueado para tarefas daquele app até alguém fazer curl, ou pedir "Distribuir" (que REINSTALA em vez de
        reler). O mesmo estado é onde a incerteza de transporte deposita o que não se sabe.
        """
        linhas = self.db.query(
            "SELECT package_name FROM device_app_state WHERE instance_id=? AND state=? AND pending_op IS NULL",
            (instance_id, InstalledAppState.verifying.value))
        return [r["package_name"] for r in linhas]

    def _reverificar_interrompidas(self) -> int:
        """No start, toda instalação que ficou sem desfecho entra na fila de releitura — sem reinstalar nada.

        Chamado logo depois de `releases.reconcile_after_restart()`: é o segundo tempo do mesmo conserto. O
        aparelho que estiver fora do ar não é ligado por isto; quando entrar no ar, `_reobservar_se_velho` faz a
        mesma releitura.
        """
        n = 0
        for instance_id in list(self.devices.devices):
            rt = self.devices.devices.get(instance_id)
            if rt is None or rt.store or rt.state != InstanceState.online:
                continue
            if self.pacotes_sem_desfecho(instance_id):
                self._reobservar_se_velho(instance_id)
                n += 1
        return n

    def _reobservar_se_velho(self, instance_id: str) -> None:
        """Aparelho entrou no ar: o que o central afirma sobre o disco dele e já está velho é RELIDO.

        Era o que faltava para "estado lido do aparelho, nunca presumido" ser verdade ao longo do tempo: o
        android-09 exibia Instagram `ready` com `verified_at` de três dias antes — de quando aquele id era outro
        aparelho físico. Passa pelo `run_device_job`, então usa as mesmas guardas do despacho (exclusividade,
        rodízio, manutenção do worker) e nunca entra na frente de uma execução.
        """
        rt = self.devices.devices.get(instance_id)
        if rt is None or rt.store:
            return
        try:
            # Aparelho que entrou no parque depois da distribuição adota aqui a versão promovida do app dele — e,
            # desde o ADR-026, a de cada app que ele tem: o que foi promovido enquanto ele estava desligado chega agora.
            self.adotar_promovidas(rt)
        except Exception:  # noqa: BLE001 - adotar a versão desejada nunca pode impedir o aparelho de subir
            log.exception("%s: falha ao adotar a versão promovida", instance_id)
        # Dado velho (a afirmação passou da validade) e dado SEM DESFECHO (instalação interrompida por reinício
        # ou por timeout de transporte) se resolvem do mesmo jeito: relendo o aparelho.
        pacotes = list(dict.fromkeys(self.pacotes_com_dado_velho(instance_id)
                                     + self.pacotes_sem_desfecho(instance_id)))
        # Loja de apps (26/09): o que foi distribuído para este aparelho (o Outlook num aparelho de Instagram) e, desde
        # o ADR-026, a versão promovida de cada app que ele tem, e o proxy pedido para ele, acontecem agora, no MESMO
        # trabalho — um segundo `run_device_job` seria recusado porque o aparelho já estaria ocupado com a releitura.
        try:
            ao_ligar = trabalho_ao_ligar(self, rt)
        except Exception:  # noqa: BLE001 - entregar ao ligar nunca pode impedir o aparelho de subir
            log.exception("%s: falha ao listar as entregas pendentes", instance_id)
            ao_ligar = None
        if not pacotes and ao_ligar is None:
            return

        async def reler() -> None:
            for package in pacotes:
                try:
                    await self.releases.verify_on(rt, package, self.installer)
                except Exception as exc:  # noqa: BLE001 - reobservar é observação: falhar não derruba o aparelho
                    log.info("%s: não foi possível reobservar %s agora (%s)", instance_id, package, exc)
            if ao_ligar is not None:
                await ao_ligar()

        self.scheduler.run_device_job(rt, reler, label="reobservação do estado do app" if ao_ligar is None
                                      else "reobservação e entrega do que foi distribuído")

    def _worker_capacity(self, worker_id: str) -> Any:
        """Vagas e recursos daquela máquina. Para ESTE servidor, quem manda nas vagas é a configuração viva
        (`max_online_devices`), e não o `max_slots` que o worker local gravou quando subiu: o operador muda o
        limite em tempo de execução, e o rodízio tem de obedecer no mesmo tick. A regra mora em
        `WorkerRegistry.vagas_que_valem` (gancho `vagas_do_host`), a mesma do painel."""
        return self.workers.capacidade(worker_id)

    def _pedir_ciclo_de_vida(self, instance_id: str, verb: str, motivo: str) -> str | None:
        """O rodízio pede `start`/`wake`/`stop`/`hibernate` num aparelho de outra máquina. A decisão mora aqui; o
        comando nasce no despacho (`commands/despacho.py`), o mesmo caminho do clique no painel."""
        try:
            return despacho.pedir_ciclo_de_vida(self, instance_id, verb, motivo, requested_by="scheduler")
        except Exception:  # noqa: BLE001 - o rodízio nunca pode derrubar o tick do scheduler
            log.exception("%s: falha ao pedir '%s' ao worker", instance_id, verb)
            return None

    def _reiniciar_por_saude(self, instance_id: str, motivo: str) -> str | None:
        """`restart` rastreável pedido pela saúde do convidado ocioso (interrupções acumuladas). Só reinício: a
        escada de reparo chega a `reset`, que apagaria a conta real logada no aparelho."""
        try:
            # `saude`, nunca `system`: `system` é degrau da escada de reparo, que chega a `reset`.
            return despacho.pedir_ciclo_de_vida(self, instance_id, "restart", motivo,
                                                requested_by=despacho.REQUESTED_BY_SAUDE, nivel="warn")
        except Exception:  # noqa: BLE001 - a sonda de saúde nunca pode derrubar o monitor de aparelhos
            log.exception("%s: falha ao pedir o reinício por interrupções acumuladas", instance_id)
            return None

    def _remediar_aparelho(self, instance_id: str, motivo: str) -> None:
        """Abre o `restart` de remediação. O mesmo desenho de `reconciliar_incertos`: a regra mora aqui, o comando
        nasce no despacho (`commands/despacho.py`)."""
        try:
            if despacho.remediar_reiniciando(self, instance_id, motivo) is None:
                log.info("%s: degradado, mas não há reinício automático a pedir", instance_id)
        except Exception:  # noqa: BLE001 - remediar nunca pode derrubar o monitor de aparelhos
            log.exception("%s: falha ao abrir o reinício de remediação", instance_id)

    def provedor_do_perfil(self) -> SessionProvider | None:
        """O provedor de sessão da conta que o perfil guarda (`instagram_profiles`/`instagram_sessions`): o do app
        de `social_repo.app_package`, resolvido no registro. É a quem "Conectar", "Verificar conta", a reobservação
        e a porta sem pacote (chamador antigo) pedem a sessão do perfil."""
        return self.sessoes.for_package(self.social_repo.app_package)

    @property
    def instagram(self) -> SessionProvider | None:
        """Nome antigo do provedor de sessão do perfil (testes e chamadores legados). É o MESMO objeto que a porta de
        sessão usa: trocar um método dele troca para todos."""
        return self.provedor_do_perfil()

    def _invalidate_sessions(self, instance_id: str, motivo: str, package: str | None = None) -> None:
        """Sem `package` é o disco do APARELHO (reset, troca de máquina: o gancho do gerenciador): a sessão de toda
        conta de todo app ali deixa de valer. Com ele, só as contas daquele app (item 23.4): antes esta função
        ignorava o app e invalidava sempre a âncora — atualizar o segundo app derrubava a sessão do primeiro e
        deixava a dele de pé."""
        if package is None:
            n = self.social_repo.invalidate_sessions_of_instance(instance_id, reason=motivo, todos_os_apps=True)
            quem = "das contas deste aparelho"
        else:
            n = self.social_repo.invalidate_sessions_of_instance(instance_id, reason=motivo, package=package)
            quem = f"do {capabilities_of(package).label}"
        if n:
            self.bus.emit("log", f"{instance_id}: sessão {quem} invalidada — {motivo}", level="warn",
                          instance_id=instance_id)

    def _sessao_apos_mudanca_de_app(self, instance_id: str, package: str, motivo: str) -> None:
        """Mexer no disco de UM app invalida a sessão DAQUELE app — não a de qualquer outro.

        Instalar, atualizar ou voltar de versão o QA Messenger marcava a sessão do Instagram como "não
        verificada" com o motivo "o aplicativo foi instalado neste aparelho": reobservação forçada e painel
        poluído por um app que não tem conta nenhuma. Quem decide é o registro de apps: só o pacote que tem
        provedor de sessão (conta gerenciada) chega à invalidação. Apagar o disco do APARELHO inteiro (wipe)
        continua invalidando sem perguntar — ali o dado do perfil foi mesmo embora.
        """
        if not self.sessoes.has(package):
            return
        self._invalidate_sessions(instance_id, motivo, package)

    # Estados de sessão que só uma pessoa resolve: insistir sozinho viraria laço e poderia bloquear a conta.
    _SESSAO_PRECISA_DE_PESSOA = (SessionStatus.auth_challenge.value, SessionStatus.wrong_account.value)
    # O mesmo conjunto, mais `auth_required` e `unknown`: é o que dispara a reobservação quando o controle manual
    # volta (achado #106) — ali a pessoa pode ter acabado de logar na tela, não só resolvido um desafio.
    # `unknown` entra por causa do teto do achado #104: depois que o teto trava o perfil (precisa de pessoa), a
    # tela só volta a ser lida quando o controle manual é devolvido — sem isto o perfil ficava preso até alguém
    # lembrar de clicar "Verificar conta" a mão, mesmo já tendo resolvido a tela sozinho.
    _SESSAO_PARA_REOBSERVAR = _SESSAO_PRECISA_DE_PESSOA + (SessionStatus.auth_required.value, SessionStatus.unknown.value)
    # O que a tela viu durante a execução → o que a sessão passa a valer. `auth_required` NÃO é um destes estados
    # que travam: é justamente o que devolve o caso ao autenticador automático, que tem a credencial no cofre.
    _SESSAO_PELO_QUE_A_TELA_VIU = {
        "auth_required": SessionStatus.auth_required,
        "auth_challenge": SessionStatus.auth_challenge,
        "wrong_account": SessionStatus.wrong_account,
    }

    def _sessao_desmentida(self, instance_id: str, kind: str, detail: str, *, subtipo: str | None = None,
                           package: str | None = None) -> None:
        """A tela do aparelho contradisse o que a sessão afirmava. O cache é corrigido, com evento.

        Só mexe em perfil VINCULADO àquele aparelho: aparelho sem perfil (o QA Messenger, o caminho antigo) não
        tem sessão para desmentir, e a mesma tela de senha ali não significa nada sobre Instagram nenhum.

        Qual perfil: o do OBJETIVO em curso no aparelho (é a conta dele que a tela contradisse); sem objetivo, o
        único vinculado. Com duas personas e nenhum objetivo, nada é desmentido — escolher uma seria inventar.

        Qual CONTA (item 23.4): a da persona no app da tela — `package` quando quem viu diz, senão o app da etapa em
        curso no aparelho, senão o app âncora (o chamador antigo). A persona sem conta naquele app não tem sessão ali
        para desmentir. Antes era sempre a conta âncora: uma tela de senha do segundo app punha o primeiro em
        `auth_required`.

        `subtipo` (ADR-055) só acompanha `auth_challenge`: `conta_travada` bloqueia o perfil e vai à quarentena;
        `codigo` (código de login/2FA) e `verificacao` (relatada pela IA) só pedem uma pessoa. Sem subtipo é o
        chamador antigo, para quem desafio sempre foi conta travada (ADR-029). Isso na conta ÂNCORA; na conta de
        outro app, qualquer desafio para só ela e a trava vai à quarentena sem bloquear a persona (item 23.5).
        """
        status = self._SESSAO_PELO_QUE_A_TELA_VIU.get(kind)
        if status is None:
            return
        profile_id = self._perfil_do_objetivo_em_curso(instance_id) or self.social_repo.perfil_unico_da_instancia(
            instance_id)
        if profile_id is None:
            return
        pacote = package
        if pacote is None:
            # Deduzido só vale se for de app com login gerenciado: é só desses que o executor desmente a sessão
            # (`StepExecutor._sessao_desmentida`), e uma etapa de outro app não diz nada sobre a conta de ninguém.
            em_curso = self._pacote_em_curso(instance_id)
            pacote = em_curso if em_curso is not None and self.sessoes.has(em_curso) else self.social_repo.app_package
        conta = self.social_repo.conta_do_pacote(profile_id, pacote, criar=True)
        if conta is None:
            return
        atual = self.social_repo.account_session_row(profile_id, conta["id"], instance_id)
        anterior = atual["status"] if atual is not None else None
        travada = status is SessionStatus.auth_challenge and subtipo in (None, SUBTIPO_CONTA_TRAVADA)
        # A sessão já no mesmo estado não é notícia — EXCETO a trava: uma sessão parada num código (sem bloqueio) não
        # pode impedir o bloqueio quando a verificação de conta travada aparece depois.
        if anterior == status.value and not travada:
            return
        if anterior != status.value:
            self.social_repo.set_account_session(profile_id, conta["id"], instance_id, status=status,
                                                 verified_at=to_iso(now()), detail=detail[:300])
            self.bus.emit("log", f"{instance_id}: a sessão do perfil passou a '{status.value}' — {detail}",
                          level="warn", instance_id=instance_id,
                          data={"profile_id": profile_id, "account_id": conta["id"], "status": status.value,
                                "subtipo": subtipo})
        if status is SessionStatus.auth_challenge:
            # A MESMA regra que o provedor de sessão aplica ao gravar (item 23.5, `session_rules.aplicar_desafio`):
            # na conta âncora, a trava bloqueia o perfil (ADR-029) e vai à quarentena; na conta de outro app, qualquer
            # desafio para só ela, e a trava vai à quarentena sem bloquear a persona (P9). A quarentena é protegida
            # por dentro: falhar não pode impedir o evento da fila logo abaixo (o `except` do executor engolia o erro,
            # e o dono não era avisado). A conta travada é a daquele app.
            # O identificador da conta é o mesmo que o motor de sessão confere (`conta_para_conferir`): a conta do
            # Outlook sem @ vai à quarentena pelo e-mail dela, não pelo @ de cadastro da persona.
            perfil = self.social_repo.profile_row(profile_id)
            cred = self.social_repo.account_credential_row(profile_id, conta["id"])
            ancora = self.social_repo.eh_pacote_ancora(profile_id, pacote)
            handle = conta_para_conferir(handle=conta["handle"],
                                         login_identifier=cred["login_identifier"] if cred is not None else None,
                                         username=perfil["username"] if perfil is not None else None,
                                         ancora=ancora) or profile_id
            aplicar_desafio(self.social_repo, self.bus, profile_id=profile_id, account_id=str(conta["id"]),
                            app_id=str(conta["app_id"]), handle=handle,
                            ancora=ancora, travada=travada,
                            instance_id=instance_id, anterior_status=anterior, detail=detail[:300],
                            evidencia=detail[:300], app_label=capabilities_of(pacote).label, visto_por="execução")
        # Mesmo evento dedicado que o provedor de sessão emite ao gravar (achado #106): a tela contradizendo a sessão
        # NO MEIO de uma execução é outro caminho para o mesmo estado que só uma pessoa resolve, e a fila
        # "Aguardando intervenção" do painel precisa saber por aqui também.
        if self.social_repo.account_row(profile_id, str(conta["id"])) is None:
            return          # a trava confirmada retirou a conta (29.23): não há item de fila para uma conta que saiu
        emit_needs_person_change(self.bus, profile_id=profile_id, instance_id=instance_id, status=status,
                                 anterior_status=atual["status"] if atual is not None else None,
                                 detail=detail[:300], account_id=str(conta["id"]),
                                 anterior_no_teto=self.social_repo.unknown_no_teto(atual, instance_id))

    def _pacote_em_curso(self, instance_id: str) -> str | None:
        """O pacote do app da etapa que o worker deste aparelho executa agora (o dela, senão o do plano, senão o do
        aparelho: `Scheduler._app_context`). `None` sem objetivo em curso ou sem app que se saiba."""
        oid = self.scheduler._objetivo_do_worker.get(instance_id)  # noqa: SLF001 - o AppState é quem compõe o scheduler
        rt = self.devices.devices.get(instance_id)
        if oid is None or rt is None:
            return None
        try:
            obj = self.repo.objective_row(oid)
            run = self.repo.run_row(str(obj["run_id"]))
            passo = (self.repo.step_row(rt.current.step_id)
                     if rt.current is not None and rt.current.step_id else None)
            if run is None:
                return None
            app, _ = self.scheduler._app_context(run, rt, passo["app_id"] if passo else None)  # noqa: SLF001
        except KeyError:
            return None
        return app.package or None

    def _controle_devolvido(self, rt: DeviceRuntime) -> None:
        """Devolver o controle encerra o treinamento que estava gravando e reobserva a sessão do perfil."""
        try:
            self.training.stop_for_instance(rt.id)
        except Exception:  # noqa: BLE001 - a gravação nunca pode impedir a devolução do controle
            log.exception("%s: não foi possível encerrar o treinamento ao devolver o controle", rt.id)
        self._reobservar_apos_intervencao(rt)

    def _controle_tomado(self, rt: DeviceRuntime, novo: str, antigo: str) -> None:
        """29.143: uma gravação nunca passa de mão em mão. A de quem perdeu o controle termina aqui, marcada no log;
        o aparelho segue com uma pessoa, então não há reobservação (diferente de `_controle_devolvido`)."""
        try:
            self.training.stop_for_instance(rt.id, motivo=f"interrompida pela tomada de {novo} (estava com {antigo})")
        except Exception:  # noqa: BLE001 - a gravação nunca pode impedir a troca de controle
            log.exception("%s: não foi possível encerrar o treinamento na tomada do controle", rt.id)

    def _reobservar_apos_intervencao(self, rt: DeviceRuntime) -> None:
        """O controle manual voltou para o aparelho (devolvido ou expirado). Se o perfil vinculado estava
        esperando uma pessoa, relê a tela sozinho — sem digitar nada — em vez de deixar o perfil preso em
        'Ação necessária' até alguém lembrar de clicar 'Verificar conta' (achado #106).

        `observe_only=True`: nunca autentica, só classifica o que está na tela agora. Passa por
        `run_device_job`, então usa as mesmas guardas do despacho normal (exclusividade, rodízio, manutenção
        do worker) e nunca compete com uma tarefa já em andamento.

        Com mais de uma persona no aparelho (vínculo N:N), reobserva CADA uma que esperava uma pessoa — num só
        trabalho, em sequência, porque `run_device_job` é um por aparelho. E cada CONTA de app com login gerenciado
        (item 23.4): a pessoa pode ter resolvido a tela do segundo app, e é a sessão dele que precisa ser relida,
        pelo provedor dele.
        """
        if self.quarentena(rt.id) is not None:
            # Quarentena (ADR-055): quem devolve o controle acabou de olhar o desafio de uma conta travada. Reler a
            # sessão abriria o app dela de novo, fora de qualquer porta; o aparelho espera a decisão do dono.
            return
        pendentes: list[tuple[SessionProvider, str, str]] = []
        vistos: set[str] = set()
        for v in self.social_repo.profiles_of_instance(rt.id):
            pid = str(v["profile_id"])
            if pid in vistos:
                continue
            vistos.add(pid)
            for conta in self.social_repo.list_accounts(pid):
                provedor = self.sessoes.for_package(self.social_repo.pacote_da_conta(pid, str(conta["id"])))
                if provedor is None:
                    continue
                s = self.social_repo.account_session_row(pid, str(conta["id"]), rt.id)
                if s is not None and s["status"] in self._SESSAO_PARA_REOBSERVAR:
                    pendentes.append((provedor, pid, str(conta["id"])))
        if not pendentes:
            return

        async def reobservar_todas() -> None:
            for provedor, pid, conta_id in pendentes:
                await self._medindo_a_parada(
                    "pessoa_devolveu", rt.id, pid, conta_id,
                    lambda p=provedor, i=pid, c=conta_id: p.ensure_session(rt, i, account_id=c, observe_only=True))

        self.scheduler.run_device_job(rt, reobservar_todas, label="reobservação após devolver o controle")

    def _perfil_do_objetivo_em_curso(self, instance_id: str) -> str | None:
        """A persona do objetivo que o worker deste aparelho está executando agora, se houver."""
        oid = self.scheduler._objetivo_do_worker.get(instance_id)  # noqa: SLF001 - o AppState é quem compõe o scheduler
        if oid is None:
            return None
        try:
            obj = self.repo.objective_row(oid)
        except KeyError:
            return None
        return str(obj["profile_id"]) if obj["profile_id"] else None

    def _releitura_da_sessao(self, rt: DeviceRuntime, profile_id: str, conta_id: str | None,
                             provedor: SessionProvider) -> tuple[str, Callable[[], Awaitable[None]] | None]:
        """31.173: a releitura da sessão vencida, com TETO de falhas seguidas por conta e aparelho. Sem teto, a releitura
        que falha sempre (o UiAutomator sem a árvore da janela: android-03, 06/10 21:22Z e 21:32Z) voltava a cada
        volta do despacho, e o alvo ficava `pendente` para sempre. No teto, o objetivo para com o motivo e a contagem
        zera: retomar tenta de novo."""
        chave = (rt.id, conta_id or "")
        falhas = self._releituras_falhas.get(chave, 0)
        if falhas >= TETO_DE_RELEITURAS_DA_SESSAO:
            self._releituras_falhas.pop(chave, None)
            return (f"a sessão não pôde ser relida em {falhas} tentativas seguidas em {rt.id}; confira o aparelho e "
                    "retome o item", None)

        async def reler() -> None:
            try:
                await provedor.ensure_session(rt, profile_id, account_id=conta_id, observe_only=True)
            except Exception:
                self._releituras_falhas[chave] = self._releituras_falhas.get(chave, 0) + 1
                raise
            self._releituras_falhas.pop(chave, None)
        sufixo = f" (tentativa {falhas + 1} de {TETO_DE_RELEITURAS_DA_SESSAO})" if falhas else ""
        return ("a verificação desta sessão passou da validade; o aparelho vai ser relido antes da tarefa" + sufixo,
                reler)

    def _preparo_do_alvo(self, rt: DeviceRuntime, package: str | None,
                         obj: Row) -> tuple[str, Callable[[], Awaitable[None]]] | None:
        """31.267: antes da 1ª etapa de um alvo de operação, o app volta ao estado conhecido pelo motor de sessão
        (`ensure_session(observe_only=True)`: voltar, reabrir o app e ler a conta; sem IA, sem digitar, sem efeito).

        Achado do Aprendizado (31.262, leitura real da rodada de 07/10 12:55Z): os três aparelhos começaram com a folha
        de comentários da operação anterior aberta e gastaram 2 a 5 decisões de IA (US$ 0,03 a 0,08 por alvo) só para
        voltar. O motor sabia voltar, mas só roda quando a sessão vence, e ela estava fresca.

        Uma vez por objetivo, e só antes da 1ª tentativa: o objetivo retomado no meio (depois de uma aprovação) já está
        na tela certa, e voltar o tiraria dela. Sessão lida depois de a execução nascer já deixou o app em casa."""
        oid = str(obj["id"])
        perfil = obj["profile_id"] if "profile_id" in obj.keys() else None   # objetivo sem perfil: nada a preparar
        if oid in self._alvos_preparados or not perfil:
            return None
        run = self.repo.run_row(obj["run_id"])
        operacao_id = run["operacao_id"] if run is not None and "operacao_id" in run.keys() else None
        provedor = self.sessoes.for_package(package) if package is not None else None
        if not operacao_id or provedor is None:
            return None
        if self.db.scalar("SELECT 1 FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.objective_id=? LIMIT 1",
                          (oid,)):
            self._alvos_preparados.add(oid)
            return None
        profile_id = str(perfil)
        conta = self.social_repo.conta_do_pacote(profile_id, package)
        conta_id = str(conta["id"]) if conta is not None else None
        sessao = (self.social_repo.account_session_row(profile_id, conta_id, rt.id)
                  if conta_id is not None else None)
        if sessao is not None and sessao["verified_at"] and str(sessao["verified_at"]) >= str(run["created_at"]):
            self._alvos_preparados.add(oid)
            return None

        async def preparar() -> None:
            inicio = time.monotonic()
            pronto: bool | None = None
            try:
                pronto = (await provedor.ensure_session(rt, profile_id, account_id=conta_id, observe_only=True)).ready
            finally:
                self._alvos_preparados.add(oid)
                self.bus.emit("preparo.estado_conhecido",
                              f"{rt.id}: app devolvido ao estado conhecido antes do alvo da operação",
                              run_id=str(obj["run_id"]), instance_id=rt.id, objective_id=oid,
                              data={"operacao_id": str(operacao_id), "sessao_pronta": pronto,
                                    "ms": round((time.monotonic() - inicio) * 1000)})

        return "devolvendo o app ao estado conhecido antes do alvo da operação (31.267)", preparar

    def sessao_vencida(self, session: Any) -> bool:
        """A sessão `session_ready` passou da validade? Verificação sem data conta como vencida.

        O estado de sessão é cache do que se observou UMA vez; sem validade ele nunca deixava de valer. Havia
        oito perfis `session_ready` com `verified_at` de três dias antes, e a porta despachava por todos eles.
        Uma regra só, no repositório: o que a porta recusa é o mesmo que o cartão do perfil marca como velho.
        """
        return sessao_vencida(session, self.social_repo.session_max_age_s)

    def _porta_da_localidade(self, rt: DeviceRuntime, profile_id: str,
                             conta_id: str | None = None) -> tuple[str, Any | None] | None:
        """Antes da porta de sessão: os dados deste perfil ainda vivem NESTE aparelho? (item 4.4 / E9)

        A sessão do Instagram mora na partição de dados do aparelho, no disco de uma máquina. O vínculo fotografa
        qual máquina e qual aparelho físico (migração 023); aqui compara-se com o que o id lógico vale AGORA. Um
        PUT em `instances.worker_id` ou uma linha nova em `instances.external` reaponta o id para outro
        computador — e até aqui a sessão seguia `session_ready` em cache e a tarefa era despachada para um
        aparelho onde aquela conta nunca fez login.

        O disco é um só para todos os apps (item 23.4): a troca derruba a sessão da conta que a porta confere
        (`conta_id`; sem ele, a âncora) e a de toda outra conta da persona que tinha sessão NESTE aparelho — senão,
        com `reauth_elsewhere`, a localidade nova é registrada e a porta do outro app nunca mais veria a troca.

        Devolve `None` quando não há nada a dizer (inclusive depois de invalidar, quando a política do perfil
        manda reautenticar noutro lugar: aí quem resolve é a porta de sessão, com a credencial do cofre).
        Localidade não registrada (vínculo anterior à migração) nunca acusa troca: falta de registro não é prova.
        """
        binding = self.social_repo.binding(profile_id, rt.id)
        if binding is None or binding["locality_at"] is None:
            return None
        mudou_de_maquina = binding["worker_id"] != rt.worker_id
        mudou_de_aparelho = bool(binding["physical_id"] and rt.physical_id
                                 and binding["physical_id"] != rt.physical_id)
        if not mudou_de_maquina and not mudou_de_aparelho:
            if binding["physical_id"] is None and rt.physical_id:
                # A impressão digital costuma ser nula no instante do vínculo (o aparelho estava desligado) e só
                # é lida quando ele entra no ar. O que se soube depois passa a valer como o lugar dos dados.
                self.social_repo.registrar_localidade(profile_id, worker_id=rt.worker_id,
                                                      physical_id=rt.physical_id, instance_id=rt.id)
            return None
        onde = binding["worker_id"] or "este servidor"
        motivo = (f"os dados deste perfil vivem em {onde} e {rt.id} aponta hoje para outro servidor"
                  if mudou_de_maquina else
                  f"o aparelho físico por trás de {rt.id} mudou desde o vínculo deste perfil")
        detalhe = f"{motivo}; a sessão gravada no disco anterior não está aqui"
        # A porta é consultada a cada volta do agendador enquanto o item estiver bloqueado. Reescrever a sessão e
        # emitir o mesmo aviso a cada tick encheria o histórico do aparelho com a mesma linha — o mesmo cuidado
        # que `_sessao_desmentida` já toma. Só o que MUDA é registrado.
        if conta_id is None:
            ancora = self.social_repo.conta_ancora(profile_id, criar=True)
            conta_id = str(ancora["id"]) if ancora is not None else None
        contas = [str(c["id"]) for c in self.social_repo.list_accounts(profile_id)
                  if str(c["id"]) == conta_id
                  or self.social_repo.account_session_row(profile_id, str(c["id"]), rt.id) is not None]
        novidade = False
        for cid in contas:
            atual = self.social_repo.account_session_row(profile_id, cid, rt.id)
            if atual is None or atual["status"] != SessionStatus.unknown.value or atual["detail"] != detalhe:
                novidade = True
                self.social_repo.set_account_session(profile_id, cid, rt.id, status=SessionStatus.unknown,
                                                     detail=detalhe)
        if not contas:
            # Persona sem conta nenhuma para gravar (não há o que desmentir): o aviso sai uma vez, como antes.
            novidade = True
        linha = self.social_repo.profile_row(profile_id)
        politica = (linha["offline_policy"] if linha is not None else None) or OFFLINE_POLICY_PADRAO
        if politica != "reauth_elsewhere":
            # `wait`, o padrão: ninguém refaz login sozinho noutro lugar. Trocar de servidor é trocar de sessão, e
            # isso é decisão de pessoa — no painel, na política do perfil.
            #
            # Espera SEM PRAZO, de propósito: o pedido fala em "esperar com prazo", e um prazo que expira só faria
            # sentido se houvesse para onde ir — e ir para outro servidor é exatamente `reauth_elsewhere`, que é
            # decisão de pessoa. Um prazo aqui viraria reautenticação automática por decurso, que é o contrário.
            if novidade:
                self.bus.emit("log", f"{rt.id}: perfil bloqueado — {motivo}.", level="warn", instance_id=rt.id)
            return (f"{motivo}. Este perfil está configurado para esperar o servidor onde os dados vivem; para "
                    "usá-lo aqui, autorize a reautenticação em outro servidor na tela do perfil.", None)
        self.social_repo.registrar_localidade(profile_id, worker_id=rt.worker_id, physical_id=rt.physical_id,
                                              instance_id=rt.id)
        if novidade:
            self.bus.emit("log", f"{rt.id}: {motivo} — o perfil autoriza reautenticar em outro servidor.",
                          level="warn", instance_id=rt.id)
        return None

    def _session_gate(self, rt: DeviceRuntime, package: str | None = None,
                      profile_id: str | None = None) -> tuple[str, Any | None] | None:
        """Terceira porta do despacho: aparelho pronto, app pronto, **sessão pronta**.

        `profile_id` é a persona do OBJETIVO (o portador do perfil no despacho, design §7.8): é a sessão da conta
        DELA neste aparelho que a porta confere. Sem ele (objetivo antigo, sem perfil), vale a única persona do
        aparelho; com duas e nenhuma escolhida, a porta bloqueia — nunca escolhe por conta própria.

        Devolve `None` quando pode despachar; `(motivo, trabalho)` quando dá para resolver sozinho autenticando; e
        `(motivo, None)` quando depende de uma pessoa — aí o item fica bloqueado no painel, sem worker nenhum.

        A porta é POR APP: só abre quando o pacote do item é o de um app com provedor de sessão no registro de
        apps, e quem autentica é o provedor DAQUELE pacote. Sem isto, uma tarefa de QA Messenger num aparelho com
        perfil do Instagram vinculado passava pela porta do Instagram — e um desafio de segurança numa conta que a
        tarefa nem ia tocar bloqueava o item, ou pior: o sistema abria o Instagram e tentava autenticar antes da
        tarefa de outro aplicativo. Sem pacote (chamador antigo), é o provedor da conta do perfil, como sempre foi.

        Aparelho sem perfil vinculado não tem porta: o QA Messenger e o caminho antigo seguem iguais.
        """
        # Quarentena (ADR-055) ANTES de "aparelho sem perfil não tem porta": era exatamente o android-04 — conta
        # travada logada, sem vínculo — e a porta deixava passar qualquer tarefa, de qualquer app. Só uma pessoa
        # decide o destino de um aparelho com conta morta na tela.
        if (quarentena := self.quarentena(rt.id)) is not None:
            return quarentena, None
        provedor = self.sessoes.for_package(package) if package is not None else self.provedor_do_perfil()
        if provedor is None:
            return None
        vinculadas = [str(v["profile_id"]) for v in self.social_repo.profiles_of_instance(rt.id)]
        if profile_id is None:
            if len(vinculadas) > 1:
                return ("aparelho com mais de uma persona e execução sem persona definida; refaça a execução "
                        "dizendo por qual persona a tarefa acontece", None)
            profile_id = vinculadas[0] if vinculadas else None
        elif profile_id not in vinculadas:
            # O vínculo caiu depois de a execução ser criada: a sessão que a porta conferiria é de um aparelho que
            # a persona não tem mais. Bloqueia com o motivo em vez de autenticar a conta dela num aparelho alheio.
            return (f"a persona desta execução não está mais vinculada a {rt.id}; vincule-a de novo ou refaça a "
                    "execução", None)
        if profile_id is None:
            return None
        # Conta bloqueada pela plataforma (ou pausada pelo dono) não recebe tarefa: o status existia no banco desde
        # a migração 008 e NADA o lia — cinco perfis bloqueados seguiam elegíveis para despacho em 23/09. O
        # bloqueio é respeitado, não contornado; quem reativa é uma pessoa, na tela do perfil.
        perfil = self.social_repo.profile_row(profile_id)
        if perfil is not None and (perfil["status"] or "active") != "active":
            return (f"perfil @{perfil['username'] or perfil['display_name']} está '{perfil['status']}': nenhuma "
                    "tarefa é despachada para ele até uma pessoa reativá-lo na tela do perfil", None)
        pacote = package if package is not None else self.social_repo.app_package
        if perfil is not None and not self._tem_conta_no_app(perfil, pacote):
            # Desde a 047 a pessoa vinculada pode não ter conta NESTE app (persona criada antes da conta). Não é
            # erro nem sessão a autenticar: é "sem conta", e a tarefa espera uma pessoa cadastrar a conta.
            rotulo = capabilities_of(pacote).label if pacote else "este app"
            return (f"a pessoa vinculada ({perfil['display_name'] or perfil['id']}) não tem conta em {rotulo}; "
                    "cadastre a conta na tela do perfil antes de despachar", None)
        # A conta da persona NESTE app (item 23.4): é a sessão, a credencial e o login DELA que a porta confere e
        # pede ao provedor do pacote. Antes a porta lia sempre a conta âncora — a tarefa do segundo app passava pela
        # sessão do primeiro, e o login que ela pedia gravava lá.
        conta = self.social_repo.conta_do_pacote(profile_id, pacote)
        conta_id = str(conta["id"]) if conta is not None else None
        if conta_id is None and not self.social_repo.eh_pacote_ancora(profile_id, pacote):
            # A pessoa só tem conta de SITE naquele app (`host`), e o login gerenciado abre a do app inteiro. Sem esta
            # recusa, a localidade cairia na conta âncora e gravaria nela a sessão da porta de outro app.
            rotulo = capabilities_of(pacote).label if pacote else "este app"
            nome = (perfil["display_name"] or perfil["id"]) if perfil is not None else profile_id
            return (f"a pessoa vinculada ({nome}) não tem conta em {rotulo}; cadastre a conta na tela do perfil antes "
                    "de despachar", None)
        if (recusa := self._porta_da_localidade(rt, profile_id, conta_id)) is not None:
            return recusa
        if conta_id is None and (conta := self.social_repo.conta_do_pacote(profile_id, pacote)) is not None:
            conta_id = str(conta["id"])       # a conta âncora que a localidade acabou de criar para gravar a sessão
        # A sessão é da conta NESTE aparelho (`account_sessions`, 049): lida pelo par, não "a do perfil".
        session = (self.social_repo.account_session_row(profile_id, conta_id, rt.id)
                   if conta_id is not None else None)
        if session and session["status"] == SessionStatus.session_ready.value and session["instance_id"] == rt.id:
            if not self.sessao_vencida(session):
                return None
            # Vencida: NÃO é "deslogado". Antes da tarefa, relê a tela — `observe_only` nunca tenta autenticar, e
            # num aparelho ainda logado a conferência devolve `session_ready` com data nova e a tarefa segue.
            return self._releitura_da_sessao(rt, profile_id, conta_id, provedor)
        motivo = (session["detail"] if session and session["detail"]
                  else "a sessão deste perfil ainda não foi verificada")
        if session and session["status"] in self._SESSAO_PRECISA_DE_PESSOA:
            return motivo, None
        # 29.92: teto por aparelho. Com vínculo ativo (conta real) é 1: o primeiro `unknown` de um `ensure_session` já
        # para, porque a rodada seguinte pode cair no login e digitar a senha guardada em cima de uma tela que ninguém
        # reconheceu. A tela classificada direto como login segue para o `_login` com consentimento (ADR-040). 29.96:
        # a comparação é a do aviso e do `SessionInfo.unknown_at_cap` (`unknown_no_teto`), não uma cópia; o teto global
        # vem dos ajustes ao vivo pelo `teto_de_reobservacao` que o repositório recebe na montagem.
        if session is not None and self.social_repo.unknown_no_teto(session, rt.id):
            # Achado #104: sem este teto, uma tela que `classify()` nunca reconhece (sinal ausente da tabela,
            # onboarding fora do mapa) reabria o app e reobservava a cada tick, sem parar e sem aviso. Depois de
            # `teto` reobservações seguidas com o mesmo resultado, para de insistir sozinho — vira caso de
            # pessoa, como um desafio.
            if (releitura := self._releitura_do_teto(rt, profile_id, session, provedor, conta_id)) is not None:
                return releitura
            return (f"{motivo} (tela não reconhecida em {session['unknown_streak']} tentativas seguidas; "
                    "assuma o controle do aparelho para identificar a tela)"), None
        cred = (self.social_repo.account_credential_row(profile_id, conta_id)
                if conta_id is not None else None)
        if cred is None or cred["status"] == "invalid":
            return ("a credencial deste perfil não está utilizável; cadastre a senha no portal"
                    if cred is None else motivo), None
        if cred["status"] == CREDENCIAL_EM_REVISAO:
            # ADR-055: o login automático parou (um envio sem sucesso ou o teto diário). Devolver o trabalho de login
            # aqui faria o motor recusá-lo sem tocar no aparelho a cada volta do agendador, para sempre.
            return motivo_do_login_parado(capabilities_of(pacote).label if pacote else "app"), None
        if cred["consent_at"] is None:
            # ADR-040: o consentimento é por conta e vale para o provedor de sessão como para o `type_secret`.
            return ("a senha guardada desta conta ainda não tem o consentimento para a automação digitá-la; "
                    "marque-o na conta do perfil"), None
        if not self.sensitive_input.available():
            # Achado #105: sem o canal comprovado, `ensure_session(automatic=True)` ia digitar o usuário e
            # levantar SensitiveInputUnavailable ao chegar na senha — sempre, a cada objetivo que topasse este
            # aparelho, insistindo enquanto o problema é de infraestrutura (Appium sem mascaramento comprovado),
            # não da conta. Bloqueia aqui, com a mesma dica que o health já mostra, em vez de deixar o agendador
            # bater na mesma parede a cada tick.
            return ("o canal de preenchimento de credencial está indisponível (mascaramento de log do Appium "
                    "não comprovado); reinicie pelo scripts/stop.ps1 + start.ps1", None)
        return motivo, (lambda: provedor.ensure_session(rt, profile_id, account_id=conta_id, automatic=True))

    def _releitura_do_teto(self, rt: DeviceRuntime, profile_id: str, session: Row, provedor: SessionProvider,
                           conta_id: str | None = None) -> tuple[str, Callable[[], Awaitable[None]]] | None:
        """O teto de `unknown_streak` é de uma leitura VELHA? Então relê a tela uma vez, em vez de bloquear.

        Causa C8 das execuções r-20260928165254-e31953 / r-20260928195344-02ee9e: a porta bloqueava sem olhar o
        aparelho quando o contador gravado estava no teto. O android-01 ficou ~47 h preso por um teto de 26/09 que
        sobreviveu a dois reinícios do emulador — quatro execuções bloqueadas em 2–13 ms, sem leitura nenhuma. O teto
        diz "as últimas leituras não reconheceram a tela"; gravado antes de o aparelho entrar no ar (a tela de agora é
        outra) ou há mais que a validade da sessão (`contas.session_max_age_s`, a mesma do "Conectado"), ele não fala
        do aparelho de agora.

        A releitura é `observe_only` (nunca autentica, nunca digita) e UMA por janela: marcada ao COMEÇAR, e só
        liberada de novo por uma nova entrada no ar ou quando a própria marca passa da validade. Uma releitura que
        lança exceção não grava a sessão — sem a trava, o tick seguinte a reagendaria para sempre, que é o laço do
        achado #104 de volta. Com a leitura nova no teto (a tela do app segue não reconhecida), bloqueia como antes.
        """
        entrou = self._entrada_no_ar(rt)
        validade = self.social_repo.session_max_age_s
        limite = to_iso(now() - timedelta(seconds=validade)) if validade > 0 else None
        gravada = session["updated_at"]
        if not ((entrou and (not gravada or gravada < entrou)) or (limite and (not gravada or gravada < limite))):
            return None
        # Por CONTA: a releitura de um app não gasta a janela do outro (item 23.4).
        chave = (profile_id, rt.id, conta_id or "")
        ultima = self._releituras_do_teto.get(chave)
        if ultima is not None and not (entrou and ultima < entrou) and not (limite and ultima < limite):
            return None

        async def reler() -> None:
            # Marca ao começar, não ao pedir: se o aparelho estiver ocupado, `run_device_job` recusa o trabalho e a
            # releitura continua devida no próximo tick.
            self._releituras_do_teto[chave] = now_iso()
            await self._medindo_a_parada("releitura_sem_toque", rt.id, profile_id, conta_id,
                                         lambda: provedor.ensure_session(rt, profile_id, account_id=conta_id,
                                                                         observe_only=True))

        return ("a tela não reconhecida foi registrada antes de o aparelho entrar no ar (ou passou da validade); o "
                "aparelho vai ser relido antes da tarefa"), reler

    async def _medindo_a_parada(self, via: str, instance_id: str, profile_id: str, conta_id: str | None,
                                chamada: Callable[[], Awaitable[object]]) -> None:
        """29.92: roda a releitura e, se ela tirou do teto uma sessão parada (`unknown` no teto deste aparelho) para
        `session_ready`, conta `sessao.parada_resolvida{via}`. `releitura_sem_toque`: ninguém tocou no aparelho (a
        releitura única do teto); `pessoa_devolveu`: a pessoa assumiu e devolveu o controle. É o que decide se vale
        uma rodada automática só de observar, sem login."""
        def sessao() -> Row | None:
            return (self.social_repo.account_session_row(profile_id, conta_id, instance_id)
                    if conta_id is not None else None)

        parada = self.social_repo.unknown_no_teto(sessao(), instance_id)
        await chamada()
        depois = sessao()
        if parada and depois is not None and depois["status"] == SessionStatus.session_ready.value:
            metricas.contar("sessao.parada_resolvida", instancia=instance_id, via=via)

    def _entrada_no_ar(self, rt: DeviceRuntime) -> str | None:
        """Quando o aparelho entrou no ar pela última vez, em ISO: o mais recente entre o início do processo do
        emulador (`instances.emulator_started_at`, só dos aparelhos desta máquina) e a entrada em `online` que este
        backend viu (`online_since_mono`, que vale também para o aparelho de um worker remoto, como o android-06)."""
        iniciado = self.db.scalar("SELECT emulator_started_at FROM instances WHERE id=?", (rt.id,))
        no_ar = (to_iso(now() - timedelta(seconds=max(0.0, time.monotonic() - rt.online_since_mono)))
                 if rt.state == InstanceState.online else None)
        marcos = [str(m) for m in (iniciado, no_ar) if m]
        return max(marcos) if marcos else None

    def _tem_conta_no_app(self, perfil: Row, package: str | None) -> bool:
        """A pessoa tem conta no app de `package`? A verdade é `profile_accounts` (037); o usuário de cadastro
        (`username`) continua valendo como a conta do app do perfil, porque perfis anteriores à 037 e os criados sem o
        app registrado não têm a linha de conta — e sempre foram a conta do Instagram."""
        if self.social_repo.account_for_package(perfil["id"], package) is not None:
            return True
        return bool(package and package == self.social_repo.app_package and perfil["username"])

    # ------------------------------------------------------------------ entrega do aplicativo ao parque
    # Estados em que uma entrega FALHOU. Daqui ninguém tenta de novo sozinho: instalar é mexer no disco do aparelho, e
    # repetir às cegas o que acabou de falhar é o "retry cego" que o projeto proíbe. Quem retenta é uma pessoa.
    _ENTREGA_FALHOU = ("install_failed", "verify_failed", "incompatible", "version_drift")
    _ENTREGA_AUTOMATICA = ("missing", "installed", "ready")

    #: Canal de quem o parque SAIU de propósito: a versão foi VOLTADA ("substituída" pela volta de um aparelho). Só
    #: daqui a convergência rebaixa um aparelho; de qualquer outra versão mais nova (a que está em prova no canário, a
    #: instalada por fora do catálogo), nunca — rebaixar sozinho uma versão que ninguém voltou seria desfazer a prova.
    #: A quarentena fica de fora de propósito: ela para de espalhar a versão, e o painel promete que "nenhum aparelho
    #: muda sozinho: quem já está nela continua até você pedir a volta". Pedir a volta é o que leva o parque junto.
    _CANAIS_ABANDONADOS = ("rolled_back",)

    @staticmethod
    def tem_o_app(row: object) -> bool:
        """Delegado a `Convergencia.tem_o_app` (`convergencia.py`)."""
        return Convergencia.tem_o_app(row)

    def _entregavel(self, release_id: str | None) -> bool:
        """Delegado a `Convergencia._entregavel` (`convergencia.py`)."""
        return self.convergencia._entregavel(release_id)

    def release_no_aparelho(self, row: object) -> object:
        """Delegado a `Convergencia.release_no_aparelho` (`convergencia.py`)."""
        return self.convergencia.release_no_aparelho(row)

    def fora_da_convergencia(self, row: object, alvo: object) -> str | None:
        """Delegado a `Convergencia.fora_da_convergencia` (`convergencia.py`)."""
        return self.convergencia.fora_da_convergencia(row, alvo)

    def _ultima_tentativa_de_entrega(self, instance_id: str, package: str) -> datetime | None:
        """Delegado a `Convergencia._ultima_tentativa_de_entrega` (`convergencia.py`)."""
        return self.convergencia._ultima_tentativa_de_entrega(instance_id, package)

    def aplicar_versao_promovida(self, rt: DeviceRuntime, package: str | None = None) -> str | None:
        """Delegado a `Convergencia.aplicar_versao_promovida` (`convergencia.py`)."""
        return self.convergencia.aplicar_versao_promovida(rt, package)

    def adotar_promovidas(self, rt: DeviceRuntime) -> list[str]:
        """Delegado a `Convergencia.adotar_promovidas` (`convergencia.py`)."""
        return self.convergencia.adotar_promovidas(rt)

    def _app_preflight(self, rt: DeviceRuntime, pacotes: Sequence[str] = ()) -> dict[str, str] | None:
        """Delegado a `Convergencia._app_preflight` (`convergencia.py`)."""
        return self.convergencia._app_preflight(rt, pacotes)

    @staticmethod
    def _recusa_do_renderizador(rt: DeviceRuntime, package: str) -> str | None:
        """Delegado a `Convergencia._recusa_do_renderizador` (`convergencia.py`)."""
        return Convergencia._recusa_do_renderizador(rt, package)

    def _app_preflight_do_pacote(self, rt: DeviceRuntime, package: str) -> dict[str, str] | None:
        """Delegado a `Convergencia._app_preflight_do_pacote` (`convergencia.py`)."""
        return self.convergencia._app_preflight_do_pacote(rt, package)

    def _pacote_do_app_id(self, app_id: str) -> str | None:
        """Delegado a `Convergencia._pacote_do_app_id` (`convergencia.py`)."""
        return self.convergencia._pacote_do_app_id(app_id)

    def _pacote_do_aparelho(self, instance_id: str) -> str | None:
        """Delegado a `Convergencia._pacote_do_aparelho` (`convergencia.py`)."""
        return self.convergencia._pacote_do_aparelho(instance_id)

    def _reler_antes_da_tarefa(self, rt: DeviceRuntime, package: str, row: object) -> tuple[str, object | None] | None:
        """Delegado a `Convergencia._reler_antes_da_tarefa` (`convergencia.py`)."""
        return self.convergencia._reler_antes_da_tarefa(rt, package, row)

    def _app_resolver(self, rt: DeviceRuntime, package: str, obj: object) -> tuple[str, object | None] | None:
        """Delegado a `Convergencia._app_resolver` (`convergencia.py`)."""
        return self.convergencia._app_resolver(rt, package, obj)

    async def _entregar(self, rt: DeviceRuntime, package: str, release_id: str) -> object:
        """Delegado a `Convergencia._entregar` (`convergencia.py`)."""
        return await self.convergencia._entregar(rt, package, release_id)

    def _rebaixa_do_parque(self, instance_id: str, package: str, release_id: str) -> bool:
        """Delegado a `Convergencia._rebaixa_do_parque` (`convergencia.py`)."""
        return self.convergencia._rebaixa_do_parque(instance_id, package, release_id)

    def _rollout_pending(self) -> list[tuple[str, object]]:
        """Delegado a `Convergencia._rollout_pending` (`convergencia.py`)."""
        return self.convergencia._rollout_pending()

    def distribute(self, release_id: str, *, eager: bool = False, instance_ids: list[str] | None = None, count: int | None = None, dry_run: bool = False) -> list[dict[str, object]]:
        """Delegado a `Convergencia.distribute` (`convergencia.py`)."""
        return self.convergencia.distribute(release_id, eager=eager, instance_ids=instance_ids, count=count, dry_run=dry_run)

    async def _policy_gate(self, obj: object, srow: object, run: object) -> object:
        """Delegado a `Portoes._policy_gate` (`gates.py`)."""
        return await self.portoes._policy_gate(obj, srow, run)

    def vereditos_da_porta(self, obj: Row, srow: Row, run: Row) -> PortaDaEtapa:
        """Delegado a `Portoes.vereditos_da_porta` (`gates.py`)."""
        return self.portoes.vereditos_da_porta(obj, srow, run)

    def _registrar_leitura(self, obj: object, step: object, items: list[str]) -> None:
        """Delegado a `Portoes._registrar_leitura` (`gates.py`)."""
        return self.portoes._registrar_leitura(obj, step, items)

    def _pacote_da_etapa(self, obj: Mapping[str, object], step: object) -> str | None:
        """Delegado a `Portoes._pacote_da_etapa` (`gates.py`)."""
        return self.portoes._pacote_da_etapa(obj, step)

    def _alvo_da_conversa(self, obj: object, step: object) -> str | None:
        """Delegado a `Portoes._alvo_da_conversa` (`gates.py`)."""
        return self.portoes._alvo_da_conversa(obj, step)

    async def _draft_gate(self, obj: object, srow: object, cap: object, profile_id: str, *, rt: object = None,
                          pacote: str | None = None) -> object:
        """Delegado a `Portoes._draft_gate` (`gates.py`)."""
        return await self.portoes._draft_gate(obj, srow, cap, profile_id, rt=rt, pacote=pacote)

    async def _ler_tela(self, rt: object, pacote: str | None) -> object:
        """Delegado a `Portoes._ler_tela` (`gates.py`)."""
        return await self.portoes._ler_tela(rt, pacote)

    def _approval_gate(self, obj: object, srow: object, cap: object, profile_id: str, *, motivo: str = "",
                       excecao: str | None = None, pacote: str | None = None, app_id: str | None = None) -> object:
        """Delegado a `Portoes._approval_gate` (`gates.py`)."""
        return self.portoes._approval_gate(obj, srow, cap, profile_id, motivo=motivo, excecao=excecao, pacote=pacote, app_id=app_id)

    def _sim_do_plano_nao_vale(self, pedido: Approval, obj: Row, srow: Row, cap: Capability, profile_id: str,
                               pacote: str | None, app_id: str | None = None) -> str:
        """Delegado a `Portoes._sim_do_plano_nao_vale` (`gates.py`)."""
        return self.portoes._sim_do_plano_nao_vale(pedido, obj, srow, cap, profile_id, pacote, app_id)

    def _seed_apps(self) -> None:
        """Os apps do `config.yaml` entram no registro na subida; o que já existe (mesmo id) fica como está.

        `builtin: true` é o app de prova embutido e nasce com `category='qa'`, como a migração 041 fez com as linhas que
        já existiam (`UPDATE … WHERE builtin = 1`). Sem isto, uma instalação nova semeava o app de prova SEM categoria e
        o tier 0 de `side_effect_tier` (item 29.31) nunca valia nela."""
        for a in self.cfg.file.apps:
            if self.apps.obter(a.id) is not None:
                continue
            self.apps.criar(app_id=a.id, name=a.name, package=a.package, activity=a.activity, apk_path=a.apk_path,
                            nav_hints=a.nav_hints, known_selectors=a.known_selectors, builtin=a.builtin,
                            category="qa" if a.builtin else None)

    def _seed_builtin_release(self) -> None:
        """Garante, na subida, que todo app embutido com APK versionado já tenha release instalável e
        promovida — ver `ReleaseService.ensure_builtin_release` para o porquê e para as duas exceções que só
        valem para `builtin: true`. Roda depois de `_seed_apps()` (a linha de `apps` já existe) e depois de
        `self.releases` existir; por isso não pode morar em `_seed_apps()`, que roda antes da camada de releases
        estar de pé.

        Conveniência de primeira subida, nunca pré-condição: um app sem `apk_path` (todos os outros) nem entra
        aqui, e a falha de UM app — arquivo ausente, SDK sem aapt2/apksigner, conjunto inválido — não pode
        derrubar os demais nem a subida do backend.
        """
        for a in self.cfg.file.apps:
            if not a.builtin or not a.apk_path:
                continue
            try:
                self.releases.ensure_builtin_release(
                    package_name=a.package, apk_path=self.cfg.path(a.apk_path), app_label=a.name)
            except Exception:  # noqa: BLE001 - bootstrap de conveniência nunca impede a subida
                log.exception("%s: bootstrap da release embutida falhou", a.name)

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        self._laco_principal = asyncio.get_running_loop()
        self.bus.bind_loop(asyncio.get_running_loop())
        self._curador_do_hub.ligar_laco(asyncio.get_running_loop())
        # O transporte do despacho sobe ANTES de qualquer efeito: com a bandeira do NATS ligada e sem broker no
        # ar, a falha tem de ser na partida, alta e visível — nunca no meio de um comando de aparelho.
        await self.transport.start(lambda envelope: despacho.executar_envelope(self, envelope))
        # ANTES de qualquer efeito: um relógio errado só é detectável contra o banco, e subir com ele quando já
        # existe outro hospedeiro significa adotar etapa viva alheia (item 5.3).
        self.conferir_relogio()
        # Os 8 avatares que existiam antes da 048 viram a imagem principal de cada pessoa (idempotente; migração
        # não vê disco nem bucket). Em qualquer papel: é só banco e storage, e a segunda partida não faz nada.
        self._importar_avatares_legados()
        if self.cfg.roda_scheduler:
            if self.manage_appium and self.cfg.file.appium.autostart:
                ok = await asyncio.to_thread(self.appium.start)
                log.info("Appium: %s (%s)", "ok" if ok else "indisponível", self.appium.detail)
            await self.devices.start()
            # O `account_label` de cada aparelho passa a ser o derivado (vínculo ou conta travada; ADR-055) — em 28/09
            # os quinze diziam `qa-user-NN` da configuração, e o android-04 com o sicrano logado enganou um experimento.
            self.social_repo.sincronizar_rotulos()
            # Antes do scheduler e da reconciliação: a partir daqui o ciclo de vida local tem para quem ir, e um
            # comando despachado sem o worker local no ar seria recusado com "não está conectado".
            await self.local_worker.conectar()
            await self.scheduler.start()
            self.runs.resume_planning_after_restart()
            self.releases.reconcile_after_restart()      # instalação interrompida nunca é repetida às cegas
            self.workers.comandos.reconciliar_na_subida()   # 29.154: comando em voo vira `uncertain`, nunca repetido
            self.training.reconcile_after_restart()      # 31.80: gravação do treinamento sem gravador vira "recorded"
            # …e agora ela tem quem a releia: sem isto, `verifying` dizia "o estado será relido do aparelho" e o
            # aparelho ficava bloqueado para tarefas daquele app até alguém chamar a rota de verificação à mão.
            self._reverificar_interrompidas()
            self.social.reconcile_pending_effects()      # efeito disparado sem desfecho observado vira incerto
            for cmd in self.commands.reconcile_after_restart():
                # Sai como evento para a interface poder mostrar "isto ficou sem desfecho", em vez de o comando
                # simplesmente desaparecer do histórico quando o processo cai.
                self.bus.emit("command.updated", f"{cmd['instance_id']}: {cmd['verb']} — {cmd['reason']}",
                              level="warn", instance_id=cmd["instance_id"],
                              data={"command": command_dto(cmd).model_dump()})
            # DEPOIS da reconciliação, e só aqui: ela já deixou de fora o que tem entrega pendente, e é este
            # dreno que publica o que a queda anterior aceitou e nunca enviou (item 5.6).
            await self._drenar_outbox()
            # Antes dos laços que elas guardam: o que ficou no nome deste backend é resto da queda (mesmo `OWNER_ID`),
            # e a tomada já aqui é o que deixa a primeira volta da retenção acontecer agora e não daqui a 6 h.
            self.lideranca.soltar_da_queda()
            self._manter_travas()
            self._bg.append(asyncio.create_task(self._laco_das_travas(), name="travas-de-lider"))
            if self.cfg.file.pedidos.enabled:
                # Depois de `resume_planning_after_restart` e de `soltar_da_queda`: a primeira volta é imediata, acha
                # a execução pela chave antes de criar e retoma o que a queda deixou (pedidos-laco.md §6).
                self._bg.append(asyncio.create_task(self.pedidos.laco(), name="pedidos"))
            self._bg.append(asyncio.create_task(self._laco_do_outbox(), name="outbox"))
            self._bg.append(asyncio.create_task(self._retention_loop(), name="retention"))
            self._bg.append(asyncio.create_task(self._expiracao_loop(), name="expiracao-needs-input"))
            self._bg.append(asyncio.create_task(self._worker_reaper_loop(), name="worker-reaper"))
            self._bg.append(asyncio.create_task(self._saldos_loop(), name="saldos-de-ia"))
            # Aviso fora do painel: enfileira em qualquer réplica (chave única) e só o líder da trava `avisos` envia.
            self._bg.append(asyncio.create_task(self.avisos.laco(), name="avisos-fora-do-painel"))
            # O reenvio dos contatos do site e a retenção de 180 dias (29.77), no líder da mesma trava `avisos`.
            self._bg.append(asyncio.create_task(self.portal.laco(lambda: self._lider(AVISOS)), name="portal-contatos"))
            # O vigia da borda (29.97): de hora em hora, no mesmo líder; sem nome público não faz nada.
            self._bg.append(asyncio.create_task(self.portal.laco_da_borda(lambda: self._lider(AVISOS)),
                                                name="portal-borda"))
            # O ensaio de restauração e o disco do central (28.60, 28.58): no mesmo líder, só com o aviso ligado.
            self._bg.append(asyncio.create_task(self.vigia_do_host.laco(lambda: self._lider(AVISOS)), name="vigia-do-host"))
            self._bg.append(asyncio.create_task(self.verificacao_periodica.laco(), name="verificacao-periodica-de-sessao"))
            # O recolher das decisões automáticas (28.25) em qualquer réplica; o resumo, só no líder da trava `avisos`.
            self._bg.append(asyncio.create_task(self.decisoes.laco(), name="decisoes-automaticas"))
            # A conversa de volta (28.15): long-poll do getUpdates, só no líder da trava `avisos` (único consumidor).
            self._bg.append(asyncio.create_task(self.telegram_entrada.laco(), name="telegram-entrada"))
            # O espelho do Trello (32.2): reconciliador no líder da trava `avisos`; sem `trello.enabled` não chama nada.
            self._bg.append(asyncio.create_task(self.trello_espelho.laco(), name="trello-espelho"))
            self._bg.append(asyncio.create_task(self.trello_leitor.laco(), name="trello-leitor"))
            # Mesmo critério de réplica da retenção: só quem roda o scheduler; idempotente (chaves únicas e CAS).
            self._bg.append(asyncio.create_task(self._curadoria_loop(), name="aprendizado-curadoria"))
            self._bg.extend(asyncio.create_task(laco.laco(lambda: self._lider(CURADORIA)), name=f"aprendizado-{laco.nome}") for laco in self.learning.lacos)  # noqa: E501 - 30.11: o curador por IA, sob a trava `curadoria`
            # Loja de apps: o que ficou pendente em aparelho ligado e livre é entregue na varredura (e a rede de cada
            # aparelho converge no mesmo trabalho: `vitrine.trabalho_ao_ligar`).
            self._bg.append(asyncio.create_task(laco_de_convergencia(self), name="loja-convergencia"))
            # O servidor sing-box do central acompanha o banco (sobe com o primeiro aparelho que o pede; ADR-056).
            self._bg.append(asyncio.create_task(self.rede_convergencia.laco(), name="rede-servidor"))
            # Todo reinício do backend derruba os túneis (25.12): a medição do tráfego dos aparelhos com rede exigida é
            # pedida já, sem apagar a prova de vazamento (ver `verificar_ao_subir`).
            self.rede_convergencia.verificar_ao_subir()
            # A saída do central, medida em segundo plano (29.20); desligada com `rede.sonda.medir_central: false`.
            self._bg.append(asyncio.create_task(self.rede_saida_central.laco(), name="rede-saida-central"))
            # 31.220: avança as operações abertas sem leitura externa; desligado por padrão (`operacao_laco_s: 0`).
            self._bg.append(asyncio.create_task(self.laco_das_operacoes.laco(), name="operacoes"))
        else:
            # `ROLE=api`: esta réplica atende o painel e mais nada. Sem Appium, sem ciclo de vida de aparelho, sem
            # worker local, sem scheduler e — principalmente — sem NENHUMA reconciliação de partida: quem
            # reconcilia é quem hospeda, e um processo de API reconciliando destruiria o trabalho vivo dele.
            log.info("ROLE=api: scheduler, aparelhos e reconciliações de partida ficam com o hospedeiro.")
        self._bg.append(asyncio.create_task(self._health_loop(), name="health"))
        self._bg.append(asyncio.create_task(self._metricas_loop(), name="metricas"))
        if self.cfg.env.database_url:
            # Banco compartilhado = pode haver outra réplica publicando. Com SQLite local não há outra réplica
            # possível, e o laço seria uma consulta por segundo para nunca achar nada.
            self._bg.append(asyncio.create_task(self.bus.replicar_sempre(), name="eventos-entre-replicas"))
        self.bus.emit("log", f"Backend iniciado (v{VERSION}, papel: {self.cfg.role}). "
                             f"Provedor de IA: {self.provider.name}"
                      + (" — MODO SIMULADO" if self.provider.simulated else ""))

    # ------------------------------------------------------------------ imagens da persona (048)
    def _importar_avatares_legados(self) -> None:
        importados = 0
        for pid in self.social_repo.list_persona_ids():
            try:
                if self.persona_images.importar_legado(pid) is not None:
                    importados += 1
            except Exception:  # noqa: BLE001 - um avatar ilegível não pode impedir a partida
                log.exception("avatar legado da persona %s não pôde ser importado", pid)
        if importados:
            log.info("imagens: %d avatar(es) legado(s) registrados como imagem principal", importados)

    def agendar_imagens(self, persona_id: str, count: int) -> None:
        """Geração em segundo plano: a paga leva até minutos, e a rota responde 202. O resultado chega pelo evento
        `persona.image.updated`; a tarefa fica em `_bg`, e `stop()` a cancela como as demais."""
        async def _gerar() -> None:
            try:
                pessoa = self.social.get_persona(persona_id)
                await self.persona_images.gerar(persona_id, identidade_para_foto(pessoa), count=count)
            except Exception as exc:  # noqa: BLE001 - a falha vira aviso; a linha `failed` já está no banco quando houve
                log.exception("geração de imagem da persona %s falhou", persona_id)
                self.bus.emit("log", f"Imagem da persona {persona_id} não foi gerada: {exc}", level="warn",
                              data={"profile_id": persona_id})

        self._bg.append(asyncio.create_task(_gerar(), name=f"imagens-{persona_id}"))

    def criar_persona(self, body: PersonaCreate) -> PersonaDTO:
        """A porta ÚNICA de criação de pessoa: `POST /personas` e o lote com `create: true`. Com
        `ai.image.on_create`, as primeiras imagens saem em segundo plano pelo gerador configurado — morar só na rota
        deixaria a persona criada pelo lote sem foto."""
        pessoa = self.social.create_persona(body)
        imagens = self.persona_images
        if imagens.on_create and imagens.per_persona > 0 and imagens.generator.configured:
            self.agendar_imagens(pessoa.id, imagens.per_persona)
        return pessoa

    # ------------------------------------------------------------------ relógio
    def conferir_relogio(self) -> float:
        """Mede o desvio contra o relógio do BANCO e decide se dá para subir. Devolve o desvio em segundos.

        A regra do item 5.3: com um backend só, um relógio errado não atropela ninguém — vira aviso em
        `/api/health`. A partir do momento em que OUTRO backend hospeda aparelhos no mesmo banco, o desvio passa
        a significar adotar etapa viva alheia, e aí o certo é **recusar subir** em vez de subir e destruir.
        """
        desvio = self.db.desvio_do_relogio()
        self._clock_skew_s = desvio
        if desvio <= self.cfg.max_clock_skew_s:
            return desvio
        outros = self.db.scalar("SELECT COUNT(*) FROM instances WHERE hosted_by IS NOT NULL AND hosted_by<>?",
                                (self.cfg.owner_id,)) or 0
        if outros:
            raise RelogioDivergente(
                f"o relógio desta máquina está {desvio:.1f}s longe do relógio do banco (limite: "
                f"{self.cfg.max_clock_skew_s:.0f}s) e há outro backend hospedando aparelhos neste banco. "
                "Sincronize o relógio (NTP) e suba de novo — ou ajuste MAX_CLOCK_SKEW_S se souber o que faz.")
        log.warning("relógio %.1fs longe do banco; nenhum outro backend hospeda aparelhos aqui, então é só aviso",
                    desvio)
        return desvio

    async def _drenar_outbox(self, *, anunciar: bool = True) -> None:
        """Publica o que foi aceito e nunca saiu. É a segunda metade do outbox — sem ela, a linha `pending`
        seria só um registro do que se perdeu.

        Filtrado por quem hospeda o aparelho: sem isso, o segundo backend a subir drenaria a fila do primeiro e
        mandaria executar, na máquina errada, ordens de aparelhos que não são dele.
        """
        pendentes = self.outbox.pending(hospedados_por=self.cfg.owner_id)
        for linha in pendentes:
            try:
                await despacho._despachar(self, linha["command_id"])
            except Exception:  # noqa: BLE001 - uma entrega que falha não pode impedir as outras nem o boot
                log.exception("dreno do outbox: comando %s", linha["command_id"])
        if pendentes and anunciar:
            self.bus.emit("log", f"Fila de comandos: {len(pendentes)} entrega(s) aceita(s) antes do reinício "
                                 "foram publicadas de novo.", level="warn")

    async def _laco_do_outbox(self, intervalo: float = OUTBOX_RETRY_S) -> None:
        """Repete a entrega do que o transporte recusou. Sem este laço, um broker fora do ar por dois minutos
        deixaria o comando pendente até o próximo reinício do backend."""
        while True:
            await asyncio.sleep(intervalo)
            try:
                await self._drenar_outbox(anunciar=False)
            except Exception:  # noqa: BLE001 - a fila nunca pode derrubar o backend
                log.exception("laço do outbox")

    async def stop(self) -> None:
        for t in self._bg:
            t.cancel()
        # 31.9: nenhuma sombra nova da intenção (um plano que termine agora não agenda outra). As soltas NÃO são canceladas
        # aqui: cancelar só solta o `Task`, a thread segue e grava; elas são esperadas antes do `db.close` (ver `finally`).
        sombra_intencao, self.runs.sombra_intencao = self.runs.sombra_intencao, None
        try:
            await self.transport.close()
        except Exception:  # noqa: BLE001 - fechar o transporte nunca impede o resto do encerramento
            log.exception("encerramento do transporte de comandos")
        try:
            await self.local_worker.desconectar()
        except Exception:  # noqa: BLE001 - desligar o canal em processo nunca impede o resto do encerramento
            log.exception("encerramento do worker local")
        try:
            await self.scheduler.stop()
            await self.devices.shutdown()
        except Exception:  # noqa: BLE001 - uma falha aqui não pode deixar o Appium órfão nem o banco aberto
            log.exception("encerramento: falha ao parar scheduler/aparelhos")
        finally:
            try:
                # O sing-box do central vive enquanto o backend HOSPEDEIRO vive: sem ele, ninguém reinicia o processo
                # quando os pares mudam, e a configuração com a chave não fica no disco sem dono. A réplica de API não
                # o toca (ela nunca o subiu, e o PID na pasta seria o do hospedeiro).
                if self.cfg.roda_scheduler:
                    await self.rede_servidor.parar()
            except Exception:  # noqa: BLE001 - parar o servidor de rede nunca impede o resto do encerramento
                log.exception("encerramento do servidor de rede")
            if self.manage_appium:
                await asyncio.to_thread(self.appium.stop)
            if self._digestoes:
                # Um digest em thread ainda escrevendo não pode encontrar o banco fechado debaixo dele.
                await asyncio.wait(set(self._digestoes), timeout=10)
            try:
                await self._esperar_o_que_grava_sombra(sombra_intencao)
            except Exception:  # noqa: BLE001 - esperar a sombra nunca impede fechar o banco
                log.exception("encerramento: sombras da decisão fechada")
            try:
                # Saída limpa devolve as travas de líder na hora: o outro backend assume sem esperar o prazo.
                self.lideranca.soltar_todas()
            except Exception:  # noqa: BLE001 - devolver a trava nunca impede fechar o banco; ela vence sozinha
                log.exception("encerramento: falha ao soltar as travas de líder")
            try:
                # Num `try` próprio: se as travas falharem, a publicação dos canais ainda sai (28.37), e os outros não
                # acusam divergência por uma publicação que ficaria até envelhecer.
                self.canais_da_frota.retirar()
            except Exception:  # noqa: BLE001 - a publicação vence sozinha (FRESCA_S); nunca impede fechar o banco
                log.exception("encerramento: falha ao retirar a publicação dos canais")
            self.db.close()

    async def _esperar_o_que_grava_sombra(self, sombra_intencao: SombraDaIntencao | None) -> None:
        """Antes do `db.close`, com UM prazo (`ESPERA_DE_SOMBRAS_S`) para tudo, nesta ordem (cada passo pode alimentar o
        seguinte): a volta do curador (para entre itens; a triagem dele só chama a porta), as threads da sombra da intenção
        (que chamam a porta e casam a decisão real ao gravar), as sombras da porta e uma última rodada da porta."""
        prazo = time.monotonic() + ESPERA_DE_SOMBRAS_S

        def restante() -> float:
            return max(0.0, prazo - time.monotonic())

        for laco in self.learning.lacos:
            parar = getattr(laco, "parar", None)        # só o laço do curador por IA tem thread própria a esperar
            if parar is not None and not await asyncio.to_thread(parar, restante()):
                log.warning("encerramento: a volta do curador ainda estava no provedor; o banco fecha mesmo assim")
        if sombra_intencao is not None:
            await sombra_intencao.aguardar(restante())
            sombra_intencao.cancelar()                  # o que passou do prazo: solta o `Task`, como o `_bg`
        await asyncio.to_thread(self.decisao_fechada.aguardar_sombras, restante())
        self.decisao_fechada.encerrar()                 # nada novo a partir daqui; o que escapou entre os passos roda e é esperado
        await asyncio.to_thread(self.decisao_fechada.aguardar_sombras, restante())

    async def _health_loop(self) -> None:
        """`health()` faz um GET síncrono ao Appium (`is_up`, timeout de 1 s) — roda em thread para não travar
        o laço de eventos do resto do backend enquanto o Appium não responde."""
        while True:
            await asyncio.sleep(HEALTH_POLL_S)
            try:
                await asyncio.to_thread(self.saude.checar)
            except Exception:  # noqa: BLE001 - a saúde nunca pode derrubar o backend
                log.exception("laço de saúde")

    def gravar_janela_de_metricas(self) -> dict[str, Any] | None:
        """Fecha a janela de métricas do processo e grava UMA linha agregada (`measurements`, kind='metricas').

        Uma linha por janela, nunca por captura/ação: é o que mantém a medição fora do caminho quente e dentro da
        retenção. `owner` separa as réplicas que dividem o banco. Janela vazia não vira linha."""
        janela = metricas.fechar_janela()
        if janela is None:
            return None
        janela["owner"] = self.cfg.owner_id
        self.db.execute("INSERT INTO measurements(ts, kind, data) VALUES (?,?,?)",
                        (now_iso(), "metricas", dumps(janela)))
        return janela

    async def _metricas_loop(self, intervalo: float = METRICAS_JANELA_S) -> None:
        while True:
            await asyncio.sleep(intervalo)
            try:
                await asyncio.to_thread(self.gravar_janela_de_metricas)
            except Exception:  # noqa: BLE001 - medir nunca pode derrubar o backend
                log.exception("gravação da janela de métricas")

    # ------------------------------------------------------------------ aprendizado contínuo (ADR-054)
    def _execucao_parada(self, run_id: str) -> None:
        """A execução parou: solta o lock de escrita dela e acorda os pedidos. Sozinho, é a parada esperando a pessoa
        (`awaiting_person`, 29.93), que não digere; dentro do `_execucao_assentada`, o começo do assentamento."""
        self._draft_locks.pop(run_id, None)
        self.pedidos.ao_assentar(run_id)             # só acorda o laço de pedidos (28.4); nunca escreve aqui

    def _assentamento_agendado(self, run_id: str) -> None:
        """O assentamento que veio de outra thread, já no laço. Sem o envoltório, a exceção cairia no tratador padrão
        do asyncio, sem dizer de que execução era."""
        try:
            self._execucao_assentada(run_id)
        except Exception:  # noqa: BLE001 - o assentamento nunca derruba o laço
            log.exception("assentamento agendado da execução %s", run_id)

    def _execucao_assentada(self, run_id: str) -> None:
        """A execução saiu do ar: solta o lock de escrita dela e encadeia o digest do aprendizado numa thread."""
        try:
            laco = asyncio.get_running_loop()
        except RuntimeError:
            # 29.93: a saída da espera sem worker pode vir de uma thread (vencimento, rota síncrona). O assentamento
            # inteiro vai para o laço: o dicionário de travas e o `create_task` do digest são dele.
            principal = self._laco_principal
            if principal is not None and principal.is_running():
                principal.call_soon_threadsafe(self._assentamento_agendado, run_id)
            else:
                # Sem laço (teste sem `start`, ou thread que termina depois do desligamento): solta, e o digest desta
                # execução se perde; só o `backfill_licoes` manual o recupera. O aviso deixa a perda visível.
                self._execucao_parada(run_id)
                log.warning("aprendizado: execução %s assentou sem o laço de eventos; o digest dela não rodou", run_id)
            return
        self._execucao_parada(run_id)
        tarefa = laco.create_task(self._digerir(run_id), name=f"aprendizado-{run_id}")
        self._digestoes.add(tarefa)
        tarefa.add_done_callback(self._digestoes.discard)

    async def _digerir(self, run_id: str) -> None:
        try:
            await asyncio.to_thread(self.learning.digerir_execucao, run_id)
        except Exception:  # noqa: BLE001 - o aprendizado nunca derruba o fim de uma execução
            log.exception("aprendizado: digest da execução %s", run_id)

    # ------------------------------------------------------------------ trava de líder (item 28.1)
    def _manter_travas(self) -> None:
        try:
            # `pedidos` e `avisos` só com o laço ligado: um backend com ele desligado não pode segurar a trava e deixar
            # o ligado sem líder (28.4, 28.11).
            desligadas = {PEDIDOS} if not self.cfg.file.pedidos.enabled else set()
            if not trava_de_avisos_em_uso(self.cfg):   # o aviso OU o Trello (28.35: o espelho e o leitor também a usam)
                desligadas.add(AVISOS)
            self.lideranca.manter([n for n in TRAVAS_DOS_LACOS if n not in desligadas])
        except Exception:  # noqa: BLE001 - banco fora do ar: os laços pulam a volta, e a próxima tentativa refaz
            log.exception("travas de líder: renovação")
        try:
            self.canais_da_frota.publicar()   # só escreve quando mudou ou a cada 120 s (28.37)
        except Exception:  # noqa: BLE001 - a publicação é para a saúde; nunca atrapalha a renovação
            log.exception("canais da frota: publicação")

    async def _laco_das_travas(self) -> None:
        """Renova o mandato do líder e deixa o seguidor assumir a trava vencida. Os laços dormem de 10 min a 6 h; o
        prazo é de 2 min — sem este laço, o líder perderia a trava entre duas voltas do próprio laço."""
        while True:
            await asyncio.sleep(RENOVAR_TRAVA_S)
            self._manter_travas()

    def _lider(self, nome: str) -> int | None:
        """Token do mandato de `nome` se este backend é o líder; `None` (e a volta é pulada, sem erro) se não.

        Seguidor que vira líder (o outro caiu) só age na próxima volta do PRÓPRIO laço: até 10 min nos saldos, 15 na
        curadoria e 6 h na retenção. Aceito: são faxina e conciliação, não trabalho com prazo.
        """
        try:
            token = self.lideranca.tomar(nome)
        except Exception:  # noqa: BLE001 - sem banco não há como saber quem é o líder: pular é o lado seguro
            log.exception("trava %s: não foi possível conferir o líder", nome)
            return None
        if token is None:
            log.debug("trava %s: outro backend é o líder; volta pulada", nome)
        return token

    async def _curadoria_loop(self) -> None:
        """A régua diária durável e os passos registrados pelos pacotes seguintes, a cada `aprendizado.curadoria_s`. E,
        entre uma volta e outra, a curadoria de cada operação que encerra (`operacao.encerrada`), na hora: o fato da
        pesquisa chega ao Livro sem esperar a volta. O evento perdido (assinatura descartada, processo fora) não se
        perde: a volta periódica olha as operações encerradas da janela."""
        fila = self.bus.subscribe()
        proxima = time.monotonic() + max(60, int(self.cfg.file.aprendizado.curadoria_s))
        try:
            while True:
                if not self.bus.is_subscribed(fila):
                    fila = self.bus.subscribe()
                falta = proxima - time.monotonic()
                if falta <= 0:
                    await self._curadoria_uma_vez()
                    proxima = time.monotonic() + max(60, int(self.cfg.file.aprendizado.curadoria_s))
                    continue
                try:
                    rec = await asyncio.wait_for(fila.get(), timeout=falta)
                except asyncio.TimeoutError:
                    continue
                if rec.kind == "operacao.encerrada":
                    await self._curadoria_da_operacao(str((rec.data or {}).get("operacao_id") or ""))
        finally:
            self.bus.unsubscribe(fila)

    async def _curadoria_da_operacao(self, operacao: str) -> Mapping[str, object] | None:
        """A curadoria de UMA operação encerrada, só no líder, com o relatório no barramento
        (`aprendizado.curadoria_da_operacao`: só ids e contagens). Devolve o relatório, ou None quando não rodou."""
        if not operacao or self._lider(CURADORIA) is None:
            return None
        try:
            relatorio = await asyncio.to_thread(self.learning.curar_operacao, operacao)
        except Exception:  # noqa: BLE001 - a curadoria nunca derruba o processo
            log.exception("aprendizado: curadoria da operação %s", operacao)
            return None
        if relatorio is not None:
            fatos = relatorio.get("fatos_da_operacao")
            lista = fatos.get("nascidas") if isinstance(fatos, dict) else None
            nascidas = len(lista) if isinstance(lista, list) else 0
            self.bus.emit("aprendizado.curadoria_da_operacao",
                          f"Curadoria da operação {operacao}: {nascidas} fato(s) novo(s) no Livro.",
                          data={"operacao_id": operacao, **relatorio})
        return relatorio

    async def _curadoria_uma_vez(self) -> bool:
        """Uma volta da curadoria, só no líder. Idempotente por construção (chaves únicas e CAS): a trava é por
        eficiência. Devolve se rodou."""
        if self._lider(CURADORIA) is None:
            return False
        try:
            await asyncio.to_thread(self.learning.curar)
        except Exception:  # noqa: BLE001 - a curadoria nunca derruba o processo
            log.exception("aprendizado: curadoria")
        return True

    async def _expiracao_loop(self) -> None:
        """29.50: a primeira volta é já na subida (o que venceu com o processo parado sai agora), depois a cada
        `EXPIRACAO_INTERVALO_S`."""
        while True:
            await self._expiracao_uma_vez()
            self.voltas_de_faxina["expiracao"] += 1
            await asyncio.sleep(EXPIRACAO_INTERVALO_S)

    async def _expiracao_uma_vez(self) -> bool:
        """Uma volta da expiração das perguntas sem resposta (29.50) e do vencimento dos objetivos parados (31.43), só no
        líder da trava da retenção. É faxina do mesmo
        tipo, e uma trava nova teria de entrar em `TRAVAS_DOS_LACOS`. Idempotente: o cancelamento é condicional ao
        `needs_input`. Devolve se rodou."""
        if self._lider(RETENCAO) is None:
            return False
        try:
            expiradas = await asyncio.to_thread(self.runs.expirar_sem_resposta, now())
            if expiradas:
                log.info("expiração: %s execução(ões) sem resposta encerradas pelo sistema", len(expiradas))
        except Exception:  # a faxina nunca derruba o processo
            log.exception("expiração das perguntas sem resposta")
        try:
            # 31.43: o objetivo `waiting_user` de execução já terminada, que ninguém retomou, vence no mesmo prazo.
            vencidos = await asyncio.to_thread(self.runs.vencer_objetivos_parados, now())
            if vencidos:
                log.info("vencimento: %s objetivo(s) parados sem resposta encerrados pelo sistema", len(vencidos))
        except Exception:  # a faxina nunca derruba o processo
            log.exception("vencimento dos objetivos parados")
        try:
            # 30.61: o sim dado na prévia que venceu deixa de reservar alvo e teto (a prévia abandonada em `planned`).
            vencidos_do_plano = await asyncio.to_thread(self.approvals.vencer_do_plano, now_iso())
            if vencidos_do_plano:
                log.info("vencimento: %s sim(ns) do plano vencido(s)", vencidos_do_plano)
        except Exception:  # a faxina nunca derruba o processo
            log.exception("vencimento dos sins do plano")
        try:
            # 31.50: o lembrete do que vence nas próximas horas (uma vez por item; o texto é do montador dos avisos).
            lembrados = await asyncio.to_thread(self.runs.lembrar_antes_de_vencer, now())
            if lembrados:
                log.info("vencimento: %s lembrete(s) antes de vencer", len(lembrados))
        except Exception:  # a faxina nunca derruba o processo
            log.exception("lembrete antes do vencimento")
        return True

    async def _retention_loop(self) -> None:
        while True:
            await self._retencao_uma_vez()
            self.voltas_de_faxina["retencao"] += 1
            await asyncio.sleep(6 * 3600)

    async def _retencao_uma_vez(self) -> bool:
        """Uma volta da retenção, só no líder. Apagar o que já venceu é idempotente (a segunda volta não acha nada):
        a trava é por eficiência. Devolve se rodou."""
        if self._lider(RETENCAO) is None:
            return False
        try:
            s = self.settings.get()
            cutoff = to_iso(now() - timedelta(days=s.log_retention_days))
            # Fora do laço de eventos, como a purga de evidências: sem o índice de `events(ts)`, cada lote varre a tabela
            # (~80–100 ms com 82 mil linhas, 03/10), e a primeira volta depois do RA-11 leva ~14 lotes de telemetria.
            removed = await asyncio.to_thread(self.bus.purge_older_than, cutoff)
            # RA-11: a telemetria do parque (`instance.updated`, o DTO inteiro do aparelho) vence em 48 h, antes do
            # resto do log; só a sem execução, para a linha do tempo de uma execução não perder nada.
            telemetria = await asyncio.to_thread(
                lambda: self.bus.purge_older_than(to_iso(now() - timedelta(hours=TELEMETRIA_RETENCAO_H)),
                                                  kinds=TELEMETRIA_KINDS, so_sem_execucao=True))
            ev_cut = to_iso(now() - timedelta(days=s.evidence_retention_days))
            old = await asyncio.to_thread(self._apagar_evidencias_vencidas, ev_cut)
            # Entrega já feita de comando já fechado não é histórico — o histórico é `commands`. Sem esta
            # faxina o outbox cresceria para sempre, e a consulta do dreno de partida com ele.
            self.outbox.purge_settled(cutoff)
            # Achado #144: só `appium.log.1` vencia. `emulator-<avd>.log` passou a rotacionar do mesmo jeito
            # (`devices/emulator.py::_rotate_log`, achado #144) e sobras de `scripts/probe-image.ps1`
            # (rodado à mão, sem retenção própria: `data/logs/probe-*` e o AVD inteiro em `data/avd-probe`,
            # medido em 3,4 GB) nunca tinham prazo nenhum.
            arquivos = self._purgar_arquivos_vencidos(s.log_retention_days)
            # Achados #39/#143: até aqui só `events` e `evidence` venciam — commands, ai_calls e measurements
            # cresciam para sempre, e token de inscrição usado ficava eternamente na tabela.
            outras = self._purgar_demais_tabelas(cutoff)
            if removed or telemetria or old or outras or arquivos:
                log.info("retenção: %s eventos (+%s de telemetria), %s execuções com evidências, %s linhas de outras "
                         "tabelas e %s arquivo(s) removidos", removed, telemetria, len(old), outras, arquivos)
            # RA-11: as estatísticas do planejador de consultas ao fim da volta (`sqlite_stat1` não existia em 03/10).
            # `optimize` só refaz o que mudou e é barato; no PostgreSQL quem faz isso é o autovacuum.
            if self.db.dialect == "sqlite":
                await asyncio.to_thread(self.db.execute, "PRAGMA optimize")
        except Exception:  # noqa: BLE001
            log.exception("retenção")
        return True

    def _purgar_demais_tabelas(self, cutoff: str) -> int:
        """Retenção para o resto das tabelas que cresciam sem limite (achados #39/#143).

        Usa o mesmo `cutoff` (`log_retention_days`) de `events`, exceto `worker_enrollments`: o achado pede um
        prazo próprio e curto (7 dias) porque o token nem devia sobreviver à janela de instalação — usado ou
        vencido, ele não tem mais função nenhuma, e sete dias é só a folga para um suporte olhar "o que foi
        inscrito recentemente" sem que a linha vire lastro eterno.

        `ai_calls`/`measurements` só olham `ts`: são fatos pontuais, sem estado aberto que a purga possa cortar
        pela metade. `commands` só apaga TERMINAL (nunca `created`/`dispatched`/…) — um comando aberto não pode
        sumir da reconciliação de partida só porque é velho.
        """
        total = 0
        # ADR-054: os dias ainda inteiros que esta purga vai morder entram na régua diária ANTES de `ai_calls` vencer
        # (só lacuna; o dia agregado quando estava inteiro não é reescrito). Falha aqui não impede a purga.
        corte = parse_iso(cutoff)
        try:
            if corte is not None:
                self.learning.antes_da_purga(corte)
        except Exception:  # noqa: BLE001 - a régua é do aprendizado; a retenção do resto segue
            log.exception("aprendizado: régua diária antes da purga")
        total += self.db.apagar_em_fatias(
            "commands", "state IN ({}) AND finished_at IS NOT NULL AND finished_at < ?".format(
                ",".join("?" for _ in COMMAND_TERMINAL)),
            (*[s.value for s in COMMAND_TERMINAL], cutoff))
        # 28.6: a chamada de IA da execução de uma ocorrência de pedido AINDA ABERTA fica: o laço soma o custo dela em
        # `pedido_ocorrencias.custo_usd` no fechamento, e só depois disso a retenção pode levá-la (a ocorrência aberta
        # há mais que `log_retention_days` é rara, mas perder o custo dela seria perder o orçamento do pedido).
        total += self.db.apagar_em_fatias(
            "ai_calls", "ts < ? AND NOT EXISTS (SELECT 1 FROM pedido_ocorrencias o"
            " WHERE o.run_id = ai_calls.run_id AND o.estado IN ('despachada','rodando'))", (cutoff,))
        total += self.db.apagar_em_fatias("measurements", "ts < ?", (cutoff,))
        enroll_cut = to_iso(now() - timedelta(days=7))
        total += self.db.execute(
            "DELETE FROM worker_enrollments WHERE created_at < ? AND (used_at IS NOT NULL OR expires_at < ?)",
            (enroll_cut, to_iso(now()))).rowcount
        try:
            total += self.learning.aplicar_retencao()        # `aprendizado.retencao` (ADR-054)
        except Exception:  # noqa: BLE001 - idem
            log.exception("aprendizado: retenção")
        try:
            # Sombra da porta `DecisaoFechada` (074): prazo próprio; o agregado diário é calculado antes de purgar e fica.
            # Porta desligada E tabela vazia (uma consulta barata, `LIMIT 1`): nada a agregar nem a purgar, e a volta não
            # lê a tabela à toa. Desligada com linhas antigas ainda purga: o prazo vale mesmo sem consumidor.
            if transparencia.consumidores_ativos(self.cfg.file.ai.decisao_fechada) or self.db.one(
                    "SELECT 1 FROM decisao_fechada_sombra LIMIT 1") is not None:
                total += self.decisao_sombra.aplicar_retencao(self.cfg.file.ai.decisao_fechada.retencao_dias)
        except Exception:  # noqa: BLE001 - idem
            log.exception("decisao_fechada: retenção da sombra")
        total += self._purgar_memorias_vencidas()
        return total

    def _purgar_memorias_vencidas(self) -> int:
        """RA-11: memória com prazo (`memory_items.expires_at`) que já venceu sai do banco. `purge_expired_memories`
        existia e ninguém o chamava: a memória vencida só sumia da leitura (o filtro de `include_expired`)."""
        agora = now_iso()
        total = 0
        for r in self.db.query("SELECT DISTINCT profile_id FROM memory_items WHERE expires_at IS NOT NULL"
                               " AND expires_at <= ?", (agora,)):
            total += self.social_repo.purge_expired_memories(str(r["profile_id"]), now=agora)
        return total

    def _purgar_arquivos_vencidos(self, retention_days: int) -> int:
        """Sobras em DISCO com o mesmo prazo do log — nenhuma delas tinha retenção antes (achado #144):

        - `*.log.1` em `logs_dir`: o rotacionado de appium e de cada emulador (achado #144, `_rotate_log` nos
          dois lugares); antes só `appium.log.1` vencia.
        - `data/logs/probe-*`: cada rodada de `scripts/probe-image.ps1` (benchmark manual de imagem), que não
          tem retenção própria nenhuma.
        - `data/avd-probe/*`: o AVD inteiro que a mesma sonda cria — medido em 3,4 GB de sobra.

        Só mexe pela IDADE do arquivo (mtime), nunca pelo que está escrito nele: os três casos são artefatos que
        nada mais lê depois de rotacionados/vencidos."""
        limite = time.time() - retention_days * 86400
        removidos = 0

        def _apagar(p: Path) -> None:
            nonlocal removidos
            try:
                if p.stat().st_mtime >= limite:
                    return
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                removidos += 1
            except OSError:
                pass  # arquivo sumiu entre o glob e o apagar (outra réplica, corrida): não é erro

        if self.cfg.logs_dir.exists():
            for p in self.cfg.logs_dir.glob("*.log.1"):
                _apagar(p)
            for p in self.cfg.logs_dir.glob("probe-*"):
                _apagar(p)
        avd_probe = self.cfg.data_dir / "avd-probe"
        if avd_probe.exists():
            for p in avd_probe.iterdir():
                _apagar(p)
        return removidos

    def _apagar_evidencias_vencidas(self, ev_cut: str) -> list[str]:
        """Apaga o ARQUIVO pela interface de storage e só então a linha (item 5.7, achado #172).

        Duas mudanças, e as duas vêm do banco ser compartilhado:

        1. O arquivo é apagado por `storage.delete_prefix`, não por um `shutil.rmtree` na pasta local. Com
           `EVIDENCE_STORAGE=s3` o `rmtree` apagava nada e a linha sumia assim mesmo.
        2. Evidência em DISCO de outra réplica não é apagada daqui. Antes, a retenção de B removia do banco
           compartilhado as linhas de arquivos que estavam no disco de A: o arquivo continuava lá, ocupando
           espaço, e a prova da execução sumia do banco sem que ninguém tivesse apagado arquivo nenhum. Ela
           vence no dono, que é quem tem o arquivo para apagar junto.

        Roda em thread (`to_thread`): apagar num bucket é ida e volta de rede, e o laço não pode parar por isso.
        """
        vencidas = self.db.query(
            "SELECT DISTINCT run_id, storage, stored_by FROM evidence WHERE ts < ? AND run_id IN "
            "(SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ? AND status <> 'awaiting_person')",
            (ev_cut, ev_cut))
        limpos: list[str] = []
        for r in vencidas:
            onde = r["storage"] or DISK
            dono = r["stored_by"]
            if onde == DISK and dono and dono != self.cfg.owner_id:
                continue                      # o arquivo é do disco do outro: quem apaga é ele
            if onde != DISK and onde != self.storage.name:
                # Linha de um back-end que este processo nem tem configurado (um parque que migrou para S3 e
                # uma réplica ainda em disco). A regra é a mesma de cima: só apaga a linha quem consegue
                # apagar o arquivo — senão o objeto fica órfão no bucket e a prova some do banco.
                continue
            armazem = self.storage if onde == self.storage.name else DiskStorage(self.cfg.evidence_dir)
            try:
                armazem.delete_prefix(r["run_id"])
            except Exception:  # noqa: BLE001 - arquivo que não sai não pode impedir a linha de vencer no dono
                log.exception("retenção: falha ao apagar os arquivos de %s", r["run_id"])
            # COALESCE, e não `IS ?`: `coluna IS $1` é erro de sintaxe no PostgreSQL. Apaga só o GRUPO
            # (execução + back-end + dono) que acabou de ter o arquivo removido, nunca a linha de outro dono.
            self.db.execute("DELETE FROM evidence WHERE run_id=? AND COALESCE(storage, ?)=?"
                            " AND COALESCE(stored_by, '')=?", (r["run_id"], DISK, onde, dono or ""))
            limpos.append(str(r["run_id"]))
        return limpos

    # ------------------------------------------------------------------ saldo das contas de IA (ADR-051)
    def saldos_de_ia(self) -> list[saldos.SaldoConta]:
        return saldos.estado(self.db, self.cfg)

    def _versao_do_deploy(self) -> tuple[str | None, str | None]:
        """(commit em execução, última migração): o que o cartão de marco do Trello compara entre partidas."""
        return commit_em_execucao(self.cfg.root), self.ultima_migracao()

    def _linhas_de_custo(self) -> list[LinhaDeCusto]:
        """O custo de IA das últimas 24 h por conta (a fonte de `GET /api/usage?days=1`, `by_account`) e o saldo estimado
        de cada uma (a de `GET /api/ai/balances`), lidos pelas funções Python, sem HTTP."""
        gasto = saldos.gasto_usd_por_conta(self.db, self.cfg, iso_in(-86400))
        contas = {c.account: c for c in saldos.estado(self.db, self.cfg)}
        return [LinhaDeCusto(conta=nome, gasto_usd=round(gasto.get(nome, 0.0), 4),
                             saldo=contas[nome].estimated_balance if nome in contas else None,
                             moeda=contas[nome].currency if nome in contas else "USD",
                             estado=contas[nome].state if nome in contas else "unknown")
                for nome in sorted(set(gasto) | {n for n, c in contas.items() if c.em_uso})]

    async def _saldos_loop(self) -> None:
        """Livro-caixa das contas de IA (ADR-051): a cada `SALDOS_INTERVALO_S` concilia com o relatório oficial do
        provedor e fecha o dia das contas com âncora velha. Sem isto a conciliação só andava quando alguém abria o
        painel — e o roteador e a saúde leem o que ela deixou."""
        while True:
            await self._saldos_uma_vez()
            await asyncio.sleep(SALDOS_INTERVALO_S)

    async def _saldos_uma_vez(self) -> bool:
        """Uma volta do livro-caixa, só no líder (item 28.1). Devolve se rodou.

        A consulta ao relatório é por eficiência (é paga e lenta, e as linhas de base que ela grava são por id). O
        fechamento do dia NÃO é idempotente — dois líderes gravariam dois "fechamentos" na mesma conta —, então ele
        roda cercado pelo token tirado no começo da volta: quem perdeu o mandato no meio é recusado na escrita.

        Limite conhecido: `saldos.CONCILIACOES` é memória do processo; o seguidor não a atualiza pelo laço, e com
        chave de administrador a saúde dele mostra a conciliação velha (como já acontece na réplica `ROLE=api`).
        """
        token = self._lider(SALDOS)
        if token is None:
            return False
        try:
            await conciliacao.atualizar(self.db, self.cfg, forcar=True)
            if await asyncio.to_thread(self._fechar_dia_cercado, token):
                await conciliacao.atualizar(self.db, self.cfg, forcar=True)     # linha de base da âncora nova
        except TravaPerdida as exc:
            log.warning("livro-caixa: fechamento recusado, %s", exc)
        except Exception:  # noqa: BLE001 - relatório fora do ar não derruba o processo; a saúde mostra
            log.exception("livro-caixa das contas de IA")
        return True

    def _fechar_dia_cercado(self, token: int) -> list[str]:
        with self.lideranca.cercada(SALDOS, token):
            return saldos.fechar_dia(self.db, self.cfg)

    # ------------------------------------------------------------------ saúde
    def ai_status(self) -> AiStatus:
        return self.saude.ai_status()

    def health(self) -> Health:
        return self.saude.health()

    def ultima_migracao(self) -> str | None:
        return self.saude.ultima_migracao()

    async def diagnostics(self, refresh: bool = False) -> dict[str, Any]:
        from .devices import diagnostics

        if refresh or self._diag_cache is None:
            self._diag_cache = await asyncio.to_thread(diagnostics.collect, self.cfg, self.tools, self.db)
        return self._diag_cache
