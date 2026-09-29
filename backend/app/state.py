"""Composição da aplicação: cria e liga os módulos (dispositivos, automação, planejamento, fila, eventos)."""
from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Awaitable, Mapping
from typing import Any, Callable

import psutil

from .automation.appium_server import AppiumServer
from .automation.driver import DeviceIO
from .automation.hierarchy import SUBTIPO_CONTA_TRAVADA
from .commands import despacho
from .commands.reconciler import reconciliar_incertos
from .commands.outbox import CommandOutbox
from .commands.states import COMMAND_TERMINAL
from .storage import DISK, DiskStorage, build_storage

from .commands.store import CommandStore, command_dto
from .commands.transport import build_transport
from .devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from .workers.local import LocalWorker
from .workers.registry import HEARTBEAT_S, WorkerRegistry
from .config import Config, LimitsCfg
from .db import Database, Row, dumps, loads
from .devices.manager import DeviceManager, DeviceRuntime
from .devices.sdk import SdkTools
from .events import EventBus
from .metricas import metricas
from .modules.applications.infrastructure.app_repository import AppRepository
from .modules.identity.application.ports import SessionProvider
from .modules.identity.application.session_rules import (CREDENCIAL_EM_REVISAO, bloquear_por_desafio,
                                                         emit_needs_person_change, motivo_do_login_parado,
                                                         registrar_conta_travada)
from .modules.identity.application.sessions import SessionProviders
from .modules.identity.infrastructure.sessions import SessionDeps, SessionProviderFactory
from .modules.skills.application.registry import CompositeSkillRegistry
from .modules.skills.application.teaching import TeachingService
from .modules.skills.infrastructure.document_validator import DslDocumentValidator, LockedVersions
from .modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from .modules.skills.infrastructure.run_planning import SkillRunPlanner
from .modules.skills.infrastructure.secret_screen import RedactionSecretScreen
from .modules.skills.infrastructure.sql_repository import SqlSkillRepository
from .modules.skills.infrastructure.sql_teaching_repository import SqlTeachingRepository
from .models import (AiStatus, AppiumStatus, DatabaseStatus, Health, InstalledAppState, InstanceState,
                     OFFLINE_POLICY_PADRAO, PersonaCreate, PersonaDTO, Problem, SdkStatus, SessionStatus)
from .devices.installer import AppInstaller
from .planning import conciliacao, saldos
from .planning.capabilities import (Capability, alvo_da_acao, capability_of, contraparte, load_catalog,
                                    texto_a_gerar)
from .planning.catalog import capabilities_of, pacote_ancora, screen_reader_of, session_factory_of
from .planning.provider import AIProvider, build_provider
from .modules.identity.infrastructure.persona_images import (compor_servico_de_imagens, identidade_para_foto,
                                                              imagens_dto, status_de_imagem)
from .releases.catalog import ReleaseValidationError
from .releases.inspector import ApkInspector
from .security import local_secret
from .security.secret_store import SecretStore, build_key_provider
from .security.sessions import PanelSessions, PortaoDeLogin
from .releases.repository import ReleaseRepository
from .releases.service import InstalacaoIncerta, ReleaseService
from .security.sensitive_input import SensitiveInputChannel
from .social.repository import SocialRepository, frase_da_quarentena, sessao_vencida
from .social.approvals import (ApprovalService, ApprovalStore, definir_texto, guardar_rascunho, ler_rascunho,
                               textos_irmaos)
from .social.persona_batch import LotesDePersona
from .social.policy import UMA_CONTA_POR_ALVO, PolicyEngine, Verdict
from .social.service import SocialError, SocialService, thread_de_dm
from .taskqueue.repository import Repository
from .taskqueue.scheduler import Scheduler
from .taskqueue.service import RunService
from .training.generalizer import ProviderSkillGeneralizer
from .util import now, now_iso, parse_iso, to_iso
from .vitrine import (_apps_changed, alvos_da_distribuicao, laco_de_convergencia, previa_de_entrega,
                      trabalho_ao_ligar)

#: Depois de uma entrega de app que falhou, quanto esperar até a próxima tentativa automática (uma por dia).
RETENTATIVA_DE_ENTREGA_S = 24 * 3600
from .version import VERSION, commit_em_execucao  # noqa: F401 - reexportado

log = logging.getLogger("poc")
#: Intervalo do livro-caixa das contas de IA (conciliação + fechamento diário). O relatório de uso da Anthropic é
#: horário e o da OpenAI diário: 10 min basta para a hora cheia aparecer logo, sem martelar a API de administração.
SALDOS_INTERVALO_S = 600
# `VERSION` e `commit_em_execucao` moram em `version.py` e são reexportados aqui: o agente do worker
# precisa dos dois e não pode importar `state` (ele traz banco, IA e a aplicação inteira).

# O tipo de texto que cada capability escreve e as leituras de conversa (que viram fala de outra pessoa) são do
# APP: moram no manifesto dele (`AppDefinition.text_kinds`/`conversation_reads`, lidos do `app.yaml` do pacote,
# ADR-052). Quem pergunta é `_draft_gate` e `_registrar_leitura`.
# Teto para ler a tela antes de escrever. Curto porque é contexto opcional: a etapa seguinte observa a tela de
# qualquer jeito, e segurar o aparelho esperando uma sessão que está subindo custaria muito mais do que vale.
_TELA_TIMEOUT_S = 15.0


#: De quanto em quanto tempo os limites são relidos do banco. O `get()` é chamado em todo tick do scheduler e em
#: cada etapa, então ler sempre custaria uma consulta por segundo sem necessidade; nunca reler significaria que um
#: limite alterado em OUTRO backend nunca chegaria aqui. Alteração feita neste processo vale na hora, sem esperar.
_RELER_LIMITES_S = 2.0

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


class SettingsStore:
    """Limites editáveis em tempo de execução, persistidos no banco (semente: config.yaml).

    O valor é relido periodicamente porque o banco — não a memória deste processo — é a fonte da verdade. Antes o
    `get()` devolvia para sempre o que foi lido no arranque: com dois backends no mesmo banco, baixar
    `max_ai_concurrency` num deles deixaria o outro gastando no limite antigo até alguém reiniciá-lo.
    """

    def __init__(self, db: Database, defaults: LimitsCfg):
        self.db = db
        self._defaults = defaults
        self._value = self._ler()
        self._lido_em = time.monotonic()

    def _ler(self) -> LimitsCfg:
        stored = loads(self.db.scalar("SELECT value FROM settings WHERE key='limits'"), {}) or {}
        return LimitsCfg.model_validate({**self._defaults.model_dump(), **stored})

    def get(self) -> LimitsCfg:
        agora = time.monotonic()
        if agora - self._lido_em >= _RELER_LIMITES_S:
            self._lido_em = agora
            try:
                self._value = self._ler()
            except Exception:  # noqa: BLE001 - banco momentaneamente indisponível não pode derrubar o despacho
                log.exception("falha ao reler limites; seguindo com os últimos conhecidos")
        return self._value

    def update(self, patch: dict[str, Any]) -> LimitsCfg:
        self._value = LimitsCfg.model_validate({**self._value.model_dump(), **patch})
        self.db.execute("INSERT INTO settings(key, value) VALUES ('limits', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (dumps(self._value.model_dump()),))
        self._lido_em = time.monotonic()      # acabei de escrever: o que tenho em mão é o mais novo que existe
        return self._value


class RelogioDivergente(RuntimeError):
    """O relógio desta máquina está longe demais do relógio do banco para este backend virar um SEGUNDO dono.

    Item 5.3 (achado #32): o vencimento de um lease é escrito por quem assume e lido por quem pergunta. Um
    backend adiantado enxerga como vencido o lease de uma etapa em plena execução, adota a etapa e passa a operar
    o MESMO aparelho que o dono legítimo — exatamente o que o lease existe para impedir. Enquanto existe um
    backend só, isso não faz diferença e o desvio é apenas avisado; a partir do segundo, subir é pior que não subir.
    """


def _col_app(row: Any) -> str | None:
    """`steps.app_id` (item 12.1) quando existe na linha — a etapa pode rodar num app diferente do plano."""
    try:
        return row["app_id"]
    except (KeyError, IndexError, TypeError):
        return None


class AppState:
    def __init__(self, cfg: Config, *, provider: AIProvider | None = None,
                 io_factory: Callable[[DeviceRuntime], DeviceIO] | None = None, manage_appium: bool = True,
                 emulator: Any = None):
        self.cfg = cfg
        cfg.ensure_dirs()
        # Regravado a cada subida, de propósito: um segredo que vazou deixa de servir no próximo restart, e quem
        # precisa dele (`scripts/stop.ps1`) lê o arquivo na hora de usar. Ver `security/local_secret.py`.
        local_secret.garantir(cfg.data_dir)
        self.db = Database(cfg.db_dsn)
        self.db.migrate()
        # `origin`: quem publicou. É o que permite a OUTRA réplica saber o que não é dela e entregar aos
        # WebSockets ligados nela (item 5.6) — sem isso, o painel de uma réplica não via nada da outra.
        self.bus = EventBus(self.db, origin=cfg.owner_id)
        self.tools = SdkTools(cfg)
        self.settings = SettingsStore(self.db, cfg.file.limits)
        #: Quem está operando o painel (item 9.1). Mora no estado, e não num global, porque a suíte sobe vários
        #: `AppState` no mesmo processo e uma sessão de um teste não pode valer no banco de outro.
        self.sessions = PanelSessions(self.db)
        self.portao_de_login = PortaoDeLogin()
        self.appium = AppiumServer(cfg, self.tools)
        # Único caminho por onde uma credencial chega ao aparelho; recusa operar sem mascaramento comprovado.
        self.sensitive_input = SensitiveInputChannel(lambda: self.appium.log_masking_active)
        self.manage_appium = manage_appium
        #: Último desvio medido contra o relógio do banco, em segundos (item 5.3). Publicado em `/api/health`.
        self._clock_skew_s = 0.0
        #: Dono da escrita na tabela `apps` (cadastro pelo painel, subida pelo `config.yaml`, app que chega por versão).
        self.apps = AppRepository(self.db)
        self._seed_apps()
        # `emulator`: a MÁQUINA por trás do ciclo de vida do emulador (achado #165). `None` = a de verdade.
        self.devices = DeviceManager(cfg, self.db, self.bus, self.tools, self.appium,
                                     settings_getter=self.settings.get, io_factory=io_factory, emulator=emulator)
        self.devices.seed()
        self.provider: AIProvider = provider or build_provider(cfg)
        # Storage de evidências (item 5.7): disco local por omissão, S3-compatível por bandeira. A chave gravada
        # em `evidence.path` passa a ser chave de storage, e é a mesma nas duas pontas.
        self.storage = build_storage(
            cfg.env.evidence_storage, evidence_dir=cfg.evidence_dir, bucket=cfg.env.s3_bucket,
            endpoint_url=cfg.env.s3_endpoint_url, region=cfg.env.s3_region,
            access_key=cfg.env.s3_access_key_id.get_secret_value() if cfg.env.s3_access_key_id else None,
            secret_key=cfg.env.s3_secret_access_key.get_secret_value() if cfg.env.s3_secret_access_key else None)
        #: Avatares de perfil, sob a chave `avatars/<id>.jpg` — pelo mesmo motivo das evidências: no disco de
        #: uma réplica, eles respondem 404 na outra. Em disco a raiz é `data/`, então o arquivo continua
        #: exatamente onde sempre esteve (`data/avatars/<id>.jpg`): nada a mover.
        self.avatares = DiskStorage(cfg.data_dir) if self.storage.name == DISK else self.storage
        self.repo = Repository(self.db, self.bus, cfg.evidence_dir, owner_id=cfg.owner_id, storage=self.storage)
        # Comando do painel como entidade: sem isto a ação era um 202 sem registro, e a interface chamava de
        # sucesso o que só tinha sido aceito.
        # `owner_id`: a reconciliação de partida mexe só nos comandos de aparelho que ESTE backend hospeda.
        # Outbox: a entrega DEVIDA gravada na mesma transação que aceita o comando (item 5.6). Sem ela, uma
        # queda entre gravar `dispatched` e agendar a tarefa perdia o comando para sempre.
        self.outbox = CommandOutbox(self.db, owner_id=cfg.owner_id, transport=cfg.env.command_transport)
        self.transport = build_transport(cfg.env.command_transport, owner_id=cfg.owner_id or "local",
                                         url=cfg.env.nats_url)
        self.commands = CommandStore(self.db, owner_id=cfg.owner_id, outbox=self.outbox)
        # Workers: as máquinas que hospedam aparelhos. O executor local é um deles, não um caminho paralelo.
        self.workers = WorkerRegistry(self.db, on_change=self._publish_worker,
                                      on_metrics=self._publish_worker_metrics)
        # O central como worker. Conectado em `start()`, quando já existe laço de eventos para o canal em
        # processo: é ele que faz o ciclo de vida dos aparelhos desta máquina passar pelo MESMO despacho do
        # agente remoto (`workers/local.py`).
        self.local_worker = LocalWorker(self)
        self.workers.local_worker_id = self.cfg.owner_id
        # Release de APK como artefato: importar/inspecionar/validar/catalogar, e instalar com estado observado.
        # `owner_id`: a operação de app aberta AQUI fica marcada como nossa. Sem isso, com dois
        # backends no mesmo banco, o que sobe marcava como interrompidas as instalações vivas do outro.
        self.release_repo = ReleaseRepository(self.db, owner_id=cfg.owner_id)
        # O catálogo de APK entra no MESMO storage compartilhado quando ele existe (achado #89): sem isso, as
        # linhas de `app_releases` apontam para arquivos que só existem na máquina que importou, e o segundo
        # backend vê a release como `installable` e falha ao instalar. Em disco, `None`: catálogo local, como
        # sempre — e a mensagem de `files_for_install` passa a dizer "ausente NESTE servidor".
        self.releases = ReleaseService(cfg, self.release_repo, ApkInspector(self.tools), self.bus,
                                       catalog_storage=None if self.storage.name == DISK else self.storage)
        self._seed_builtin_release()
        # Loja de apps: o app que o import cadastra sozinho aparece no painel na hora, pelo mesmo anúncio do cadastro.
        def _anunciar_apps() -> None:
            _apps_changed(self)

        self.releases.ao_cadastrar_app = _anunciar_apps
        # Prazos do instalador pela configuração: ajustar ao que se mediu no worker remoto deixa de
        # exigir edição de código.
        self.installer = AppInstaller.from_config(self.devices, cfg)
        # Cofre de credenciais: chave mestra fora do banco (DPAPI no Windows, ambiente como alternativa).
        self.secrets = SecretStore(self.db, build_key_provider(
            data_dir=cfg.data_dir, env_material=cfg.env.credentials_master_key))
        self.social_repo = SocialRepository(self.db)
        # Validade do "Conectado": o repositório monta o DTO do perfil e é ele que marca a sessão como dado velho.
        self.social_repo.session_max_age_s = cfg.file.contas.session_max_age_s
        # Teto do `unknown_streak` na GRAVAÇÃO (o mesmo que a porta de sessão lê): nenhuma releitura soma acima dele.
        self.social_repo.teto_de_reobservacao = lambda: self.settings.get().session_unknown_retry_cap
        #: (perfil, aparelho) → quando começou a última releitura que a porta de sessão pediu para um teto velho de
        #: `unknown_streak`. É a trava de UMA releitura por janela (entrada no ar do aparelho, validade): a releitura
        #: que quebra não grava nada, e sem a trava o tick seguinte a reagendaria para sempre (achado #104).
        self._releituras_do_teto: dict[tuple[str, str], str] = {}
        # O pacote da conta vem do REGISTRO de apps (o app âncora do perfil, ADR-052 fatia 4), como no logout: é por
        # ele que o perfil diz se o app está no aparelho antes de oferecer Conectar.
        ancora = pacote_ancora()
        self.social_repo.app_package = ancora
        self.social_repo.app_name = capabilities_of(ancora).label if ancora else ""
        # Imagens da persona (048): gerador (simulado por omissão), storage dos avatares, custo em `ai_calls` e o
        # teto do dia dos limites. O DTO da pessoa lista as imagens por esta ligação, sem o repositório conhecer o
        # serviço.
        self.persona_images = compor_servico_de_imagens(cfg, db=self.db, storage=self.avatares, bus=self.bus,
                                                        settings_getter=self.settings.get)
        self.social_repo.imagens_de = lambda pid: imagens_dto(self.persona_images.listar(pid))
        # `PersonaDTO.devices[].state` (051): o estado vivo de cada aparelho da persona vem do runtime, não do banco.
        self.social_repo.estado_do_aparelho = (
            lambda iid: rt.state.value if (rt := self.devices.devices.get(iid)) is not None else None)
        self.social = SocialService(self.social_repo, self.secrets, self.bus,
                                    known_instances=lambda: list(self.devices.devices),
                                    store_instance=lambda: self.cfg.store_id,
                                    provider=self.provider,
                                    # geração social fora de execução: entra no relatório de custo sem run/objetivo
                                    usage_sink=lambda u: self.repo.add_usage(None, None, u),
                                    # efeito social pendente de aparelho ALHEIO não é meu para marcar como incerto
                                    owner_id=cfg.owner_id)
        # Personas em lote (v0.34): estado em memória; cada criação passa pela MESMA porta de `POST /personas` (com a
        # foto automática), e a tarefa entra em `_bg` para o `stop()` cancelá-la como as demais.
        self.lotes_de_persona = LotesDePersona(self.social, self.bus, criar=self.criar_persona,
                                               ao_agendar=lambda t: self._bg.append(t))
        # Provedores de sessão POR PACOTE (fase K1): cada app com conta gerenciada traz no manifesto a fábrica do
        # seu (o do Instagram é o login determinístico, fora do laço da IA, com a senha só pelo canal sensível).
        # Fabricado na primeira pergunta, com as dependências DESTA composição, e o mesmo para todo mundo depois.
        dependencias = SessionDeps(cfg=cfg, devices=self.devices, repo=self.social_repo, secrets=self.secrets,
                                   sensitive_input=self.sensitive_input, bus=self.bus)
        self.sessoes: SessionProviders[SessionProviderFactory] = SessionProviders(
            session_factory_of, lambda fabrica: fabrica(dependencias))
        # O hub de IA (item 7.1) é construído antes do banco existir — é ele que decide quem atende cada função.
        # O repositório e os limites chegam aqui: é com eles que o teto em US$ é conferido e que a troca de
        # provedor vira linha da execução em vez de só um modelo diferente numa linha de custo.
        if hasattr(self.provider, "attach"):
            self.provider.attach(repo=self.repo, settings_getter=self.settings.get)
        self.scheduler = Scheduler(cfg, self.repo, self.devices, self.provider, self.settings.get)
        self.scheduler.session_gate = self._session_gate
        # A porta do app passa a se resolver sozinha quando há versão distribuída por instalar naquele aparelho.
        self.scheduler.app_resolver = self._app_resolver
        # A mesma verdade sobre o app, só que SEM efeito e ANTES de planejar: é o pedaço do pré-voo que conhece
        # release e estado do aplicativo, que o serviço de execução não conhece.
        self.scheduler.app_preflight = self._app_preflight
        # Manutenção suspende novas atribuições: o scheduler pergunta ao registro antes de tirar um objetivo do lugar.
        self.scheduler.worker_gate = self.workers.aceita_trabalho
        # Vagas e recursos POR MÁQUINA entram na decisão do rodízio: o teto deixa de ser um número global.
        self.scheduler.worker_capacity = self._worker_capacity
        # "Instalar em todos agora": releases cuja entrega uma pessoa pediu para JÁ. Em memória de propósito — um
        # reinício no meio não perde nada (a versão desejada está no banco); só a pressa: volta-se ao modo padrão.
        self._entrega_imediata: set[str] = set()
        # Uma escrita por vez DENTRO de cada execução. A lista de "não repita" é lida do que os irmãos já
        # escreveram: com os oito aparelhos gerando ao mesmo tempo, todos leem a lista vazia e voltam com a mesma
        # frase — exatamente o defeito que esta série existe para consertar. Execuções diferentes seguem juntas.
        self._draft_locks: dict[str, asyncio.Lock] = {}
        self.scheduler.rollout_source = self._rollout_pending
        self.policies = PolicyEngine(self.social_repo, self.settings.get)
        self.approvals = ApprovalStore(self.db)
        self.approval_service = ApprovalService(self.approvals, self.repo, self.scheduler)
        # O executor grava no histórico do perfil o efeito que dispara — é o que alimenta limites e memória.
        self.scheduler.executor.social = self.social
        self.scheduler.executor.approvals = self.approvals
        # Achado #109: aprovação pendente de uma etapa não sobrevive ao objetivo cancelado/abandonado.
        self.scheduler.expirar_aprovacoes_do_objetivo = self.approvals.expire_for_objective
        # Login/desafio visto NO MEIO da execução corrige o estado do perfil. Sem isto o painel seguia dizendo
        # "Conectado" para uma conta presa num desafio, e o login automático nunca disparava.
        self.scheduler.executor.on_auth_needed = self._sessao_desmentida
        self.scheduler.policy_gate = self._policy_gate
        # O lock de escrita é por execução: some junto com ela, senão o dicionário cresceria para sempre.
        self.scheduler.on_run_settled = lambda run_id: self._draft_locks.pop(run_id, None)
        self.scheduler.on_items_collected = self._registrar_leitura
        # Wipe, perda do aparelho ou qualquer coisa que mexa no disco invalida a sessão observada.
        self.devices.on_session_invalidated = self._invalidate_sessions
        # Devolver o controle manual, num aparelho cujo perfil esperava uma pessoa, dispara a reobservação —
        # é o que o texto de desafio do app (`sessao.yaml`) promete e, sem isto, o código não fazia (achado #106).
        self.devices.on_control_released = self._controle_devolvido
        # Modo treinamento (item 13.1): cada entrada manual do Foco, com a tela de antes, vai para a gravação.
        from .training.recorder import TrainingRecorder  # noqa: PLC0415
        # A persona do treino é a que a pessoa escolheu; sem escolha, a ÚNICA do aparelho para o app (com duas, o
        # gravador recusa: não adivinha de quem é a demonstração).
        self.training = TrainingRecorder(
            self.db, self.bus, self.devices,
            lambda iid, app: [str(v["profile_id"]) for v in self.social_repo.profiles_of_instance(iid, app)])
        self.devices.on_training_input = self.training.record
        from .training.skills import TrainingSkills  # noqa: PLC0415
        self.skills = TrainingSkills(self)
        # Apagar os dados do aparelho apaga também o app: sem isto o central seguia dizendo "pronto" para um
        # aparelho vazio, a porta do app deixava passar e "Distribuir" recusava reinstalar.
        self.devices.on_device_wiped = self._forget_app_state
        # Só o disco apagado DE FATO tira a conta travada do aparelho (ADR-055); a troca de identidade física não.
        self.devices.on_disk_erased = self._disco_apagado
        # Aparelho no ar e inútil (Android morto por dentro, sessão que não abre) com `desired_state=online`:
        # alguém pede o reinício. O gerenciador não conhece comandos; quem os abre é a camada da API.
        self.devices.on_remediation_needed = self._remediar_aparelho
        self.devices.on_health_restart = self._reiniciar_por_saude
        # Quarentena (ADR-055): o gerenciador pergunta, antes do reinício de saúde, se há conta travada logada.
        self.devices.conta_travada_em = self._conta_travada_em
        # O rodízio passa a ligar e desligar aparelho de outra máquina — pelo worker, como um comando do painel.
        self.devices.on_lifecycle_request = self._pedir_ciclo_de_vida
        self.devices.worker_hibernates = self.workers.hiberna
        # "Estado lido do aparelho, nunca presumido" só vale se alguém relê: aparelho que entra no ar com
        # afirmação velha sobre o disco tem o app reobservado antes de a porta deixar qualquer tarefa passar.
        self.devices.on_device_online = self._reobservar_se_velho
        # Instalar, atualizar, voltar de versão ou reinstalar também mexe no disco — e a matriz de invalidação diz
        # que nesses casos a sessão passa a ser "não verificada", nunca "perdida sem olhar".
        self.releases.on_app_changed = self._sessao_apos_mudanca_de_app
        # A prova de abertura de um app que não é o principal do aparelho termina com HOME + force-stop: senão ele
        # fica na frente e o "Abrir app" do principal espera 90 s em vão (medido no android-01 em 26/09).
        self.releases.pacote_principal_de = self._pacote_do_aparelho
        # Toda mudança de estado do app por aparelho vira evento persistido: é o que faz "O que está instalado"
        # se atualizar sozinha em vez de prometer um resultado que só aparecia recarregando a página.
        self.release_repo.on_app_state_changed = self._publicar_estado_do_app
        # Habilidades versionadas (fase G; ADR-034): um registro, dois backends, cada um atrás do seu interruptor
        # (`skills.enabled` e `ai.flows`, lidos a cada chamada). `skill_registry`, e não `skills`: `self.skills` já é o
        # modo treinamento. A trava de composição lê as versões do próprio repositório, que precisa do validador —
        # por isso o `get` tardio.
        travas = LockedVersions(lambda ref: self.skill_repo.get(ref))
        validador = DslDocumentValidator(self._pacote_do_app_id, travas)
        self.skill_repo = SqlSkillRepository(self.db, validador, adoption_enabled=lambda: self.cfg.file.skills.enabled)
        self.skill_registry = CompositeSkillRegistry(
            self.skill_repo, LegacyFlowAdapter(self.db, self.scheduler.flows),
            skills_enabled=lambda: self.cfg.file.skills.enabled, flows_enabled=lambda: self.cfg.file.ai.flows)
        self.skill_planner = SkillRunPlanner(self.skill_registry, self._pacote_do_app_id, travas)
        # Ensino v2 (fase F, §13): as rotas ficam atrás de `skills.enabled`; o generalizador é o `generalize` do
        # provedor (simulado: regras fixas; real: uma chamada paga do planejador, contada em `ai_calls`).
        self.teaching = TeachingService(
            SqlTeachingRepository(self.db), self.skill_repo, validador,
            ProviderSkillGeneralizer(self.provider, self.db,
                                     record_usage=lambda u: self.repo.add_usage(None, None, u)),
            RedactionSecretScreen(), enabled=lambda: self.cfg.file.skills.enabled,
            on_updated=lambda tid, msg: self.bus.emit("teaching.updated", f"Ensino: {msg}",
                                                      data={"teaching_id": tid}))
        self.runs = RunService(self.repo, self.scheduler, self.devices, self.provider, profiles=self.social,
                               secrets=self.secrets, skills=self.skill_planner)
        # ADR-025: a credencial fornecida para a execução só chega ao aparelho pelo canal sensível, do cofre ao driver.
        self.scheduler.executor.secrets = self.secrets
        self.scheduler.executor.sensitive_input = self.sensitive_input
        self._diag_cache: dict[str, Any] | None = None
        self._bg: list[asyncio.Task[Any]] = []
        self._last_health: dict[str, Any] | None = None
        #: Última leitura da sonda do túnel por worker (achado #179): worker_id -> 'up' | 'down'.
        self._transport_cache: dict[str, str] = {}
        self.devices.transport_state_of = self._transport_state_of
        self.devices.worker_process_of = self._worker_process_of

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

    def _disco_apagado(self, instance_id: str, motivo: str) -> None:
        """O disco foi apagado de fato (boot com `-wipe-data` ou reset concluído pelo agente): a conta travada não está
        mais logada ali (ADR-055). Numa quarentena o reset só chega com a confirmação explícita da pessoa, e manter o
        marcador travaria para sempre um aparelho já limpo. O PERFIL segue bloqueado: reativar é decisão de pessoa."""
        self.social_repo.resolver_conta_travada(instance_id, por="reset do aparelho",
                                                nota=f"dados do aparelho apagados — {motivo}")

    def _conta_travada_em(self, instance_id: str) -> str | None:
        """O @ da conta travada logada no aparelho (marcador aberto, 054), ou `None`."""
        marcador = self.social_repo.conta_travada_no_aparelho(instance_id)
        return str(marcador["handle"]) if marcador is not None else None

    def quarentena(self, instance_id: str) -> str | None:
        """Por que o aparelho está em quarentena (conta travada logada, ADR-055), ou `None`. A mesma frase do 409
        do painel, para a porta do despacho, a entrega e a distribuição contarem a mesma história."""
        marcador = self.social_repo.conta_travada_no_aparelho(instance_id)
        return frase_da_quarentena(marcador) if marcador is not None else None

    def _contas_travadas_no_ar(self) -> list[str]:
        """`aparelho (@conta)` de cada marcador aberto em aparelho LIGADO — no ar, subindo ou degradado no ar."""
        no_ar = (InstanceState.online, InstanceState.booting, InstanceState.error)
        saida: list[str] = []
        for m in self.social_repo.contas_travadas_abertas():
            rt = self.devices.devices.get(str(m["instance_id"]))
            if rt is not None and rt.state in no_ar:
                saida.append(f"{rt.id} (@{m['handle']})")
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
        limite em tempo de execução, e o rodízio tem de obedecer no mesmo tick."""
        cap = self.workers.capacidade(worker_id)
        if cap is not None and worker_id == self.cfg.owner_id:
            cap.max_slots = max(1, int(self.settings.get().max_online_devices or 1))
        return cap

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

    def _invalidate_sessions(self, instance_id: str, motivo: str) -> None:
        n = self.social_repo.invalidate_sessions_of_instance(instance_id, reason=motivo)
        if n:
            rotulo = capabilities_of(self.social_repo.app_package).label
            self.bus.emit("log", f"{instance_id}: sessão do {rotulo} invalidada — {motivo}", level="warn",
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
        self._invalidate_sessions(instance_id, motivo)

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

    def _sessao_desmentida(self, instance_id: str, kind: str, detail: str, *, subtipo: str | None = None) -> None:
        """A tela do aparelho contradisse o que a sessão afirmava. O cache é corrigido, com evento.

        Só mexe em perfil VINCULADO àquele aparelho: aparelho sem perfil (o QA Messenger, o caminho antigo) não
        tem sessão para desmentir, e a mesma tela de senha ali não significa nada sobre Instagram nenhum.

        Qual perfil: o do OBJETIVO em curso no aparelho (é a conta dele que a tela contradisse); sem objetivo, o
        único vinculado. Com duas personas e nenhum objetivo, nada é desmentido — escolher uma seria inventar.

        `subtipo` (ADR-055) só acompanha `auth_challenge`: `conta_travada` bloqueia o perfil e vai à quarentena;
        `codigo` (código de login/2FA) e `verificacao` (relatada pela IA) só pedem uma pessoa. Sem subtipo é o
        chamador antigo, para quem desafio sempre foi conta travada (ADR-029).
        """
        status = self._SESSAO_PELO_QUE_A_TELA_VIU.get(kind)
        if status is None:
            return
        profile_id = self._perfil_do_objetivo_em_curso(instance_id) or self.social_repo.perfil_unico_da_instancia(
            instance_id)
        if profile_id is None:
            return
        atual = self.social_repo.session_row(profile_id, instance_id)
        anterior = atual["status"] if atual is not None else None
        travada = status is SessionStatus.auth_challenge and subtipo in (None, SUBTIPO_CONTA_TRAVADA)
        # A sessão já no mesmo estado não é notícia — EXCETO a trava: uma sessão parada num código (sem bloqueio) não
        # pode impedir o bloqueio quando a verificação de conta travada aparece depois.
        if anterior == status.value and not travada:
            return
        if anterior != status.value:
            self.social_repo.set_session(profile_id, status=status, instance_id=instance_id,
                                         verified_at=to_iso(now()), detail=detail[:300])
            self.bus.emit("log", f"{instance_id}: a sessão do perfil passou a '{status.value}' — {detail}",
                          level="warn", instance_id=instance_id,
                          data={"profile_id": profile_id, "status": status.value, "subtipo": subtipo})
        if travada:
            # O mesmo bloqueio que o provedor de sessão aplica ao gravar (ADR-029): desafio visto no meio de uma
            # execução é o mesmo aviso de conta travada. Sem o "anterior": quem impede o aviso repetido é o próprio
            # perfil já `blocked` (ver acima o caso do código seguido da trava).
            bloqueou = bloquear_por_desafio(self.social_repo, self.bus, profile_id=profile_id, instance_id=instance_id,
                                            anterior_status=None, detail=detail[:300],
                                            app_label=capabilities_of(self.social_repo.app_package).label)
            if bloqueou or anterior != status.value:
                # Protegida por dentro: a quarentena falhar não pode impedir o evento da fila logo abaixo (o `except`
                # do executor engolia o erro, e o dono não era avisado).
                perfil = self.social_repo.profile_row(profile_id)
                registrar_conta_travada(self.social_repo, self.bus, profile_id=profile_id, instance_id=instance_id,
                                        handle=str(perfil["username"] if perfil is not None else profile_id),
                                        evidencia=detail[:300], visto_por="execução")
        # Mesmo evento dedicado que o provedor de sessão emite ao gravar (achado #106): a tela contradizendo a sessão
        # NO MEIO de uma execução é outro caminho para o mesmo estado que só uma pessoa resolve, e a fila
        # "Aguardando intervenção" do painel precisa saber por aqui também.
        emit_needs_person_change(self.bus, profile_id=profile_id, instance_id=instance_id, status=status,
                                 anterior_status=atual["status"] if atual is not None else None,
                                 detail=detail[:300])

    def _controle_devolvido(self, rt: DeviceRuntime) -> None:
        """Devolver o controle encerra o treinamento que estava gravando e reobserva a sessão do perfil."""
        try:
            self.training.stop_for_instance(rt.id)
        except Exception:  # noqa: BLE001 - a gravação nunca pode impedir a devolução do controle
            log.exception("%s: não foi possível encerrar o treinamento ao devolver o controle", rt.id)
        self._reobservar_apos_intervencao(rt)

    def _reobservar_apos_intervencao(self, rt: DeviceRuntime) -> None:
        """O controle manual voltou para o aparelho (devolvido ou expirado). Se o perfil vinculado estava
        esperando uma pessoa, relê a tela sozinho — sem digitar nada — em vez de deixar o perfil preso em
        'Ação necessária' até alguém lembrar de clicar 'Verificar conta' (achado #106).

        `observe_only=True`: nunca autentica, só classifica o que está na tela agora. Passa por
        `run_device_job`, então usa as mesmas guardas do despacho normal (exclusividade, rodízio, manutenção
        do worker) e nunca compete com uma tarefa já em andamento.

        Com mais de uma persona no aparelho (vínculo N:N), reobserva CADA uma que esperava uma pessoa — num só
        trabalho, em sequência, porque `run_device_job` é um por aparelho.
        """
        if self.quarentena(rt.id) is not None:
            # Quarentena (ADR-055): quem devolve o controle acabou de olhar o desafio de uma conta travada. Reler a
            # sessão abriria o app dela de novo, fora de qualquer porta; o aparelho espera a decisão do dono.
            return
        provedor = self.provedor_do_perfil()
        if provedor is None:
            return
        perfis = [str(v["profile_id"]) for v in self.social_repo.profiles_of_instance(rt.id)
                  if (s := self.social_repo.session_row(str(v["profile_id"]), rt.id)) is not None
                  and s["status"] in self._SESSAO_PARA_REOBSERVAR]
        if not perfis:
            return

        async def reobservar_todas() -> None:
            for pid in perfis:
                await provedor.ensure_session(rt, pid, observe_only=True)

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

    def sessao_vencida(self, session: Any) -> bool:
        """A sessão `session_ready` passou da validade? Verificação sem data conta como vencida.

        O estado de sessão é cache do que se observou UMA vez; sem validade ele nunca deixava de valer. Havia
        oito perfis `session_ready` com `verified_at` de três dias antes, e a porta despachava por todos eles.
        Uma regra só, no repositório: o que a porta recusa é o mesmo que o cartão do perfil marca como velho.
        """
        return sessao_vencida(session, self.social_repo.session_max_age_s)

    def _porta_da_localidade(self, rt: DeviceRuntime, profile_id: str) -> tuple[str, Any | None] | None:
        """Antes da porta de sessão: os dados deste perfil ainda vivem NESTE aparelho? (item 4.4 / E9)

        A sessão do Instagram mora na partição de dados do aparelho, no disco de uma máquina. O vínculo fotografa
        qual máquina e qual aparelho físico (migração 023); aqui compara-se com o que o id lógico vale AGORA. Um
        PUT em `instances.worker_id` ou uma linha nova em `instances.external` reaponta o id para outro
        computador — e até aqui a sessão seguia `session_ready` em cache e a tarefa era despachada para um
        aparelho onde aquela conta nunca fez login.

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
        atual = self.social_repo.session_row(profile_id, rt.id)
        novidade = atual is None or atual["status"] != SessionStatus.unknown.value or atual["detail"] != detalhe
        if novidade:
            self.social_repo.set_session(profile_id, status=SessionStatus.unknown, instance_id=rt.id,
                                         detail=detalhe)
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
        if (recusa := self._porta_da_localidade(rt, profile_id)) is not None:
            return recusa
        # A sessão é da conta NESTE aparelho (`account_sessions`, 049): lida pelo par, não "a do perfil".
        session = self.social_repo.session_row(profile_id, rt.id)
        if session and session["status"] == SessionStatus.session_ready.value and session["instance_id"] == rt.id:
            if not self.sessao_vencida(session):
                return None
            # Vencida: NÃO é "deslogado". Antes da tarefa, relê a tela — `observe_only` nunca tenta autenticar, e
            # num aparelho ainda logado a conferência devolve `session_ready` com data nova e a tarefa segue.
            return ("a verificação desta sessão passou da validade; o aparelho vai ser relido antes da tarefa",
                    lambda: provedor.ensure_session(rt, profile_id, observe_only=True))
        motivo = (session["detail"] if session and session["detail"]
                  else "a sessão deste perfil ainda não foi verificada")
        if session and session["status"] in self._SESSAO_PRECISA_DE_PESSOA:
            return motivo, None
        teto = self.settings.get().session_unknown_retry_cap
        if (session and session["status"] == SessionStatus.unknown.value
                and int(session["unknown_streak"] or 0) >= teto):
            # Achado #104: sem este teto, uma tela que `classify()` nunca reconhece (sinal ausente da tabela,
            # onboarding fora do mapa) reabria o app e reobservava a cada tick, sem parar e sem aviso. Depois de
            # `teto` reobservações seguidas com o mesmo resultado, para de insistir sozinho — vira caso de
            # pessoa, como um desafio.
            if (releitura := self._releitura_do_teto(rt, profile_id, session, provedor)) is not None:
                return releitura
            return (f"{motivo} (tela não reconhecida em {session['unknown_streak']} tentativas seguidas; "
                    "assuma o controle do aparelho para identificar a tela)"), None
        cred = self.social_repo.credential_row(profile_id)
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
        return motivo, (lambda: provedor.ensure_session(rt, profile_id, automatic=True))

    def _releitura_do_teto(self, rt: DeviceRuntime, profile_id: str, session: Row,
                           provedor: SessionProvider) -> tuple[str, Callable[[], Awaitable[None]]] | None:
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
        chave = (profile_id, rt.id)
        ultima = self._releituras_do_teto.get(chave)
        if ultima is not None and not (entrou and ultima < entrou) and not (limite and ultima < limite):
            return None

        async def reler() -> None:
            # Marca ao começar, não ao pedir: se o aparelho estiver ocupado, `run_device_job` recusa o trabalho e a
            # releitura continua devida no próximo tick.
            self._releituras_do_teto[chave] = now_iso()
            await provedor.ensure_session(rt, profile_id, observe_only=True)

        return ("a tela não reconhecida foi registrada antes de o aparelho entrar no ar (ou passou da validade); o "
                "aparelho vai ser relido antes da tarefa"), reler

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
    def tem_o_app(row: Any) -> bool:
        """O aparelho TEM este app: instalado por release conhecida, visto pelo `pm`, ou com versão já pedida.

        É o limite da convergência (ADR-026): atualizar quem tem, nunca espalhar o app para quem não tem — isso
        continua sendo "Distribuir", explícito. A exceção é o app principal do aparelho (`instances.app_id`), que já
        era estado desejado dele antes desta decisão (`aplicar_versao_promovida`, android-12..15).
        """
        return bool(row is not None and (row["installed_release_id"] or row["observed_version_code"] is not None
                                         or row["desired_release_id"]))

    def _entregavel(self, release_id: str | None) -> bool:
        rel = self.release_repo.release_row(release_id) if release_id else None
        return rel is not None and rel["status"] == "installable" and rel["channel"] == "promoted"

    def release_no_aparelho(self, row: Any) -> Any:
        """A release que ESTÁ no aparelho: a registrada como instalada; sem ela, a desejada, se o número que o
        aparelho respondeu é o dela.

        Revisão do PR #13: quando a instalação chega ao aparelho mas a prova de abertura falha, `install_on` guarda
        `observed_version_code` e a desejada, e limpa `installed_release_id`. Olhando só a instalada, um aparelho
        rodando a versão VOLTADA parecia ter "uma versão mais nova instalada por fora do catálogo" e nunca voltava."""
        if row is None:
            return None
        if row["installed_release_id"]:
            return self.release_repo.release_row(row["installed_release_id"])
        if row["observed_version_code"] is None:
            return None
        observado = int(row["observed_version_code"])
        if row["desired_release_id"]:
            desejada = self.release_repo.release_row(row["desired_release_id"])
            if desejada is not None and int(desejada["version_code"]) == observado:
                return desejada
        # A desejada já pode ter mudado (a convergência passou a perseguir a promovida): o catálogo diz de qual
        # release é o número observado. Voltada primeiro — é a que decide rebaixar com `-d` —, depois pelo id.
        candidatas = self.db.query("SELECT * FROM app_releases WHERE package_name=? AND version_code=?",
                                   (row["package_name"], observado))
        candidatas = sorted(candidatas, key=lambda r: (r["channel"] not in self._CANAIS_ABANDONADOS, str(r["id"])))
        return candidatas[0] if candidatas else None

    def fora_da_convergencia(self, row: Any, alvo: Any) -> str | None:
        """Por que ESTE aparelho fica na versão que tem, em vez de perseguir a promovida `alvo`. `None` = persegue.

        Dois casos, e só dois:

        * ele tem uma versão MAIS NOVA que ninguém rejeitou — a que está em prova no canário, ou uma instalada por
          fora do catálogo. Rebaixar sozinho desfaria a prova; o rebaixamento automático só acontece quando a
          versão instalada foi voltada (`_CANAIS_ABANDONADOS`);
        * ele já está numa versão PROMOVIDA de mesmo número. A produção tem duas promovidas 1.0.0/1 do app de QA
          (dois builds): trocar uma pela outra seria reinstalar o parque inteiro — e invalidar sessões — para ficar
          na mesma versão. "Atualizado" é pelo número, como na vitrine (`outdated`).
        """
        if row is None:
            return None
        instalada = self.release_no_aparelho(row)
        codigo_da_release = int(instalada["version_code"]) if instalada is not None else None
        # O que o aparelho respondeu manda (a Play Store pode ter atualizado o app por fora da release registrada).
        codigo = int(row["observed_version_code"]) if row["observed_version_code"] is not None else codigo_da_release
        if codigo is None:
            return None
        alvo_codigo = int(alvo["version_code"])
        abandonada = instalada is not None and instalada["channel"] in self._CANAIS_ABANDONADOS \
            and codigo == codigo_da_release
        if codigo > alvo_codigo and not abandonada:
            origem = (f"a {instalada['version_name']}, em '{instalada['channel']}'"
                      if instalada is not None and codigo == codigo_da_release
                      else f"o código {codigo}, instalado por fora do catálogo")
            return (f"tem uma versão mais nova que a promovida ({origem}); o parque não rebaixa sozinho uma versão "
                    "que ninguém voltou")
        if codigo == alvo_codigo and instalada is not None and instalada["id"] != alvo["id"] \
                and instalada["channel"] == "promoted" and instalada["status"] == "installable":
            return f"já está numa versão promovida de mesmo número ({instalada['version_name']} · {codigo})"
        return None

    def _ultima_tentativa_de_entrega(self, instance_id: str, package: str) -> datetime | None:
        """Quando a entrega deste app neste aparelho foi tentada pela última vez — o relógio da nova tentativa diária.

        Só o histórico de comandos não basta: a entrega da varredura e do "entrou no ar" roda por `run_device_job`,
        que não abre comando. Com o último comando de app de três dias atrás, cada passada da varredura (60 s)
        rearmaria e repetiria a mesma falha — o retry cego que o projeto proíbe. A prova de instalação
        (`app_release_validations`, stage `install`) é gravada a cada tentativa que chega ao aparelho, e `_entregar`
        grava a que falha antes disso.
        """
        marcas: list[datetime] = []
        # Fora os comandos de OUTRO app e os recusados antes de tocar no aparelho: com o máximo de todo `app.*` do
        # aparelho, a atividade do app principal adiava para sempre a nova tentativa de um secundário (revisão do
        # PR #13). Os comandos de app gravam `package` nos parâmetros (`_abrir_comando_de_app`); o que não diz o
        # pacote (comando antigo, sem parâmetros) continua contando — não dá para saber de quem é, e ignorá-lo faria
        # "sem tentativa no histórico" e nunca mais tentar.
        ultima = self.db.scalar("SELECT MAX(created_at) FROM commands WHERE instance_id=? AND verb LIKE 'app.%'"
                                " AND state <> 'rejected' AND (params LIKE ? OR params IS NULL"
                                " OR params NOT LIKE '%\"package\":%')",
                                (instance_id, f'%"package":"{package}"%'))
        if ultima:
            marcas.append(parse_iso(str(ultima)))
        prova = self.db.one(
            "SELECT v.observed_at FROM app_release_validations v JOIN app_releases r ON r.id = v.release_id"
            " WHERE v.instance_id=? AND r.package_name=? AND v.stage='install' ORDER BY v.id DESC LIMIT 1",
            (instance_id, package))
        if prova is not None and prova["observed_at"]:
            marcas.append(parse_iso(str(prova["observed_at"])))
        return max(marcas) if marcas else None

    def aplicar_versao_promovida(self, rt: DeviceRuntime, package: str | None = None) -> str | None:
        """A versão PROMOVIDA de um app é estado desejado do parque, não um ato pontual sobre quem existia na hora.

        "Distribuir" percorre os aparelhos daquele instante. android-12..15 foram criados um dia depois da
        distribuição do Instagram: ficaram sem linha em `device_app_state`, a porta do app não opinava, e uma
        tarefa de Instagram era despachada para um aparelho sem o aplicativo — o `open_app` falhava dentro da
        execução, consumindo tentativas. Aqui o aparelho que entra DEPOIS (ou que só agora foi vinculado ao app)
        passa a ter a mesma versão desejada dos irmãos, sem ninguém clicar em nada.

        ADR-026 (decisão do dono, 26/09: "todos devem ficar atualizados sempre"): vale para TODO app que o aparelho
        tem, não só o principal. `package=None` é o app principal (o comportamento de antes); um pacote secundário só
        é adotado por quem já o tem (`tem_o_app`) — espalhar o app continua sendo "Distribuir".

        Devolve o id da release adotada, ou `None` quando não há o que adotar. Entrega que falhou é tentada de novo
        no máximo uma vez por dia; recusa de voltar de versão nem isso — a saída dela apaga dados, é de pessoa.
        """
        if rt.store:
            return None
        principal = self._pacote_do_aparelho(rt.id)
        package = package or principal
        if not package:
            return None                       # aparelho sem app vinculado: o caminho antigo segue igual
        row = self.release_repo.app_state(rt.id, package)
        if package != principal and not self.tem_o_app(row):
            return None                       # nunca instala um app em quem não o tem
        rel = self.releases.promoted_release(package)
        if rel is None or rel.status.value != "installable":
            return None
        linha = self.release_repo.release_row(rel.id)
        if linha is None or motivo_incompativel(requisitos_de_release(linha), capacidades_de(rt),
                                                aparelho=rt.id) is not None:
            return None                       # mandar instalar o que não roda ali seria falha permanente
        if row is not None and row["pending_op"]:
            return None
        if self.fora_da_convergencia(row, linha) is not None:
            # Fica na versão que tem. Um desejo que apontava para uma versão que não pode mais ser entregue (a
            # voltada, a da quarentena) bloquearia a porta do app com "não pode mais ser entregue": ele passa a
            # ser a versão instalada, que é onde o aparelho vai ficar.
            if row["installed_release_id"] and row["desired_release_id"] != row["installed_release_id"] \
                    and row["desired_release_id"] and not self._entregavel(row["desired_release_id"]):
                self.release_repo.upsert_app_state(rt.id, package, desired_release_id=row["installed_release_id"])
            return None
        if row is not None and row["state"] in self._ENTREGA_FALHOU and row["drift_kind"] != "downgrade_refused" \
                and row["desired_release_id"] and row["desired_release_id"] != rel.id \
                and not self._entregavel(row["desired_release_id"]):
            # A falha foi da entrega de uma versão que o parque ABANDONOU (voltada, em quarentena): perseguir a
            # promovida é um alvo novo, não a repetição do que falhou — a trava diária não vale aqui (revisão do
            # PR #13: sem isto, o aparelho rodando a versão voltada ficava nela até alguém intervir).
            self.release_repo.upsert_app_state(
                rt.id, package, desired_release_id=rel.id, drift_kind=None,
                state="installed" if row["observed_version_code"] is not None else "missing",
                detail="a versão desejada saiu do parque; passa a perseguir a promovida")
            self.bus.emit("log", f"{rt.id}: {package} sai de uma versão que o parque abandonou e persegue a promovida "
                                 f"({rel.version_name} · {rel.version_code}).", level="info", instance_id=rt.id)
            return rel.id
        if row is not None and row["state"] in self._ENTREGA_FALHOU:
            if row["drift_kind"] == "downgrade_refused":
                return None               # o Android recusou voltar sem apagar os dados: quem decide é uma pessoa
            # Falha de entrega deixava de ser tentada para sempre. Continua sem "retry cego": a nova tentativa é UMA
            # por dia, contada desde a última tentativa de entrega deste app neste aparelho — o suficiente para um
            # aparelho que falhou por adb lento ou por convidado em thrash convergir sozinho depois que o motivo passou.
            quando = self._ultima_tentativa_de_entrega(rt.id, package)
            if quando is None or (now() - quando).total_seconds() < RETENTATIVA_DE_ENTREGA_S:
                return None               # sem tentativa no histórico não há "um dia depois" a contar
            self.release_repo.upsert_app_state(
                rt.id, package, desired_release_id=rel.id, drift_kind=None,
                state="installed" if row["observed_version_code"] is not None else "missing",
                detail="nova tentativa diária de entrega (a anterior falhou)")
            self.bus.emit("log", f"{rt.id}: nova tentativa diária de entregar {package} ({rel.version_name}); a "
                                 f"anterior terminou em '{row['state']}'.", level="warn", instance_id=rt.id)
            return rel.id
        if row is not None and row["desired_release_id"] == rel.id:
            return None
        if row is not None and row["installed_release_id"] == rel.id:
            if row["desired_release_id"] and not self._entregavel(row["desired_release_id"]):
                # Já está na promovida, e o desejo apontava para uma versão voltada: só alinha, nada a instalar.
                self.release_repo.upsert_app_state(rt.id, package, desired_release_id=rel.id)
            return None
        self.release_repo.upsert_app_state(rt.id, package, desired_release_id=rel.id)
        self.bus.emit("log", f"{rt.id}: passa a ter como desejada a versão promovida de {package} "
                             f"({rel.version_name} · {rel.version_code}).", level="info", instance_id=rt.id)
        return rel.id

    def adotar_promovidas(self, rt: DeviceRuntime) -> list[str]:
        """`aplicar_versao_promovida` para o app principal E para cada app que o aparelho tem (ADR-026).

        Só grava a versão desejada; quem instala é o trabalho do "entrou no ar", a varredura de 60 s ou a porta do
        app, pelas vias de sempre. Devolve os pacotes que passaram a ter uma versão a receber.
        """
        if rt.store:
            return []
        pacotes = [p for p in [self._pacote_do_aparelho(rt.id)] if p]
        pacotes += [r["package_name"] for r in self.db.query(
            "SELECT package_name FROM device_app_state WHERE instance_id=?", (rt.id,))]
        adotados: list[str] = []
        for package in dict.fromkeys(pacotes):
            try:
                if self.aplicar_versao_promovida(rt, package):
                    adotados.append(package)
            except Exception:  # noqa: BLE001 - um app com problema não impede os outros de convergir
                log.exception("%s: falha ao adotar a versão promovida de %s", rt.id, package)
        return adotados

    def _app_preflight(self, rt: DeviceRuntime) -> dict[str, str] | None:
        """Pré-voo do aplicativo: motivo para a tarefa não poder acontecer neste aparelho, sem tocar em nada.

        `None` = não impede. Inclui o caso "não se sabe": aparelho cujo aplicativo nunca foi observado não vira
        recusa aqui — quem o observa é a reobservação de quando ele entra no ar (`_reobservar_se_velho`), e o que
        ela apurar passa a valer na próxima criação. O que não se sabe nunca fecha a porta.

        Também não é recusa a entrega PENDENTE: versão promovida por instalar é resolvida pela porta do app,
        antes da tarefa. Recusa é só o que exige uma pessoa.
        """
        package = self._pacote_do_aparelho(rt.id)
        if not package:
            return None
        row = self.release_repo.app_state(rt.id, package)
        if row is None:
            return None
        # A ordem é a MESMA da porta (`_app_resolver` e depois `_app_gate`), de propósito: a recusa antes de
        # agendar e o bloqueio no meio contam a mesma história, com as mesmas palavras, e quem lê não precisa
        # traduzir uma na outra.
        desejada = row["desired_release_id"]
        if desejada and desejada != row["installed_release_id"]:
            rel = self.release_repo.release_row(desejada)
            if rel is None or rel["status"] != "installable" or rel["channel"] != "promoted":
                estado = f"{rel['status']}/{rel['channel']}" if rel is not None else "versão ausente do catálogo"
                return {"code": "app_no_release",
                        "motivo": f"a versão distribuída para este aparelho não pode mais ser entregue ({estado}).",
                        "acao": "Promova uma versão entregável na tela de Versões e repita a execução."}
            if row["state"] in self._ENTREGA_FALHOU:
                return {"code": "app_failed",
                        "motivo": "a entrega do aplicativo falhou neste aparelho e não é repetida sozinha: "
                                  f"{row['detail'] or row['state']}.",
                        "acao": "Use Distribuir de novo na tela de Versões e repita a execução."}
            if row["state"] in self._ENTREGA_AUTOMATICA:
                return None                      # entregável e sem falha: a porta do app instala antes da tarefa
        if row["state"] not in ("ready", "installed"):
            return {"code": "app_missing" if row["state"] == "missing" else "app_not_ready",
                    "motivo": f"o aplicativo não está pronto neste aparelho (estado: {row['state']})"
                              + (f": {row['detail']}" if row["detail"] else "") + ".",
                    "acao": "Distribua uma versão promovida para ele na tela de Versões e repita a execução."}
        return None

    def _pacote_do_app_id(self, app_id: str) -> str | None:
        """Id de app (o que a skill escreve) → pacote (o que o catálogo conhece). A tabela `apps` é por instalação."""
        row = self.apps.obter(app_id)
        return row["package"] if row is not None else None

    def _pacote_do_aparelho(self, instance_id: str) -> str | None:
        """O pacote do app que ESTE aparelho opera. Nulo quando o aparelho não tem app definido."""
        row = self.db.one("SELECT a.package FROM instances i JOIN apps a ON a.id = i.app_id WHERE i.id=?",
                          (instance_id,))
        return row["package"] if row else None

    def _reler_antes_da_tarefa(self, rt: DeviceRuntime, package: str, row: Any) -> tuple[str, Any | None] | None:
        """`verifying` SEM dono não bloqueia o item: ele manda reler o aparelho antes da tarefa.

        Este é o outro lado do achado #85. Com a linha parada em `verifying`, a porta do app bloqueava o
        objetivo com "O aplicativo não está pronto neste aparelho (estado: verifying)" — `waiting_user`, que
        ninguém retoma. Nem quando a releitura automática, mais tarde, resolvesse a linha para `ready`: o item
        já estava parado esperando uma pessoa.

        Aqui a releitura vira o TRABALHO da porta, no mesmo molde da sessão vencida: a tarefa espera, o aparelho
        é relido, e o tick seguinte despacha (ou bloqueia com o motivo verdadeiro: `missing`, `version_drift`).
        Operação com dono (`pending_op`) continua sem ser tocada — ali alguém ainda está trabalhando.
        """
        if row["state"] != InstalledAppState.verifying.value or row["pending_op"]:
            return None                          # installing, ou verifying com dono: a porta bloqueia pelo estado
        return ("o estado deste aplicativo ficou sem desfecho e vai ser relido do aparelho antes da tarefa",
                lambda: self.releases.verify_on(rt, package, self.installer))

    def _app_resolver(self, rt: DeviceRuntime, package: str, obj: Any) -> tuple[str, Any | None] | None:
        """Resolvedor da porta do app: há uma versão distribuída ainda por instalar neste aparelho?

        `None` = nada pendente, a porta decide só pelo estado. É o que faz o rodízio entregar o app sem ninguém pedir:
        só 4 aparelhos ficam ligados por vez, e os demais recebem a versão na próxima vez que pegarem uma tarefa
        daquele pacote — ANTES da tarefa.
        """
        # Quarentena (ADR-055): a porta do app vem ANTES da de sessão e termina na prova de ABERTURA do app —
        # instalar aqui abriria o app da conta travada. Bloqueia para uma pessoa, sem gravar versão desejada.
        if (quarentena := self.quarentena(rt.id)) is not None:
            return quarentena, None
        row = self.release_repo.app_state(rt.id, package)
        if row is None or not row["desired_release_id"] or not self._entregavel(row["desired_release_id"]):
            # Aparelho que nunca recebeu distribuição daquele app: se existe versão promovida e este aparelho é
            # do app, ela passa a ser a desejada AQUI, antes da tarefa — em vez de a tarefa ir para um aparelho
            # sem o aplicativo e o `open_app` falhar lá dentro. ADR-026: o mesmo para o desejo que apontava para uma
            # versão voltada ou em quarentena — o aparelho persegue a promovida em vez de bloquear a tarefa.
            self.aplicar_versao_promovida(rt, package)
            row = self.release_repo.app_state(rt.id, package)
        # Estado SEM DESFECHO vem antes de tudo: ele não depende de haver versão distribuída. Sem esta ordem, o
        # aparelho parado em `verifying` caía no `return None` abaixo e a porta o bloqueava pelo estado.
        if row is not None and (releitura := self._reler_antes_da_tarefa(rt, package, row)) is not None:
            return releitura
        desejada = row["desired_release_id"] if row else None
        if not desejada or desejada == row["installed_release_id"]:
            return None
        rel = self.release_repo.release_row(desejada)
        if rel is None:
            return None
        rotulo = f"{rel['version_name']} ({rel['version_code']})"
        if rel["status"] != "installable" or rel["channel"] != "promoted":
            # Sem esta conferência, `install_on` recusaria sem mudar estado nenhum e o mesmo job voltaria a cada tick.
            return (f"A versão {rotulo} foi distribuída para este aparelho, mas não pode mais ser entregue "
                    f"(arquivo: {rel['status']}, ciclo de vida: {rel['channel']}).", None)
        if row["state"] in self._ENTREGA_FALHOU:
            return (f"A entrega da versão {rotulo} falhou neste aparelho e não é repetida sozinha: "
                    f"{row['detail'] or row['state']}. Use Distribuir de novo para tentar outra vez.", None)
        if row["state"] not in self._ENTREGA_AUTOMATICA:
            return None                          # installing (ou verifying com dono): alguém ainda trabalha nisto
        if obj["status"] != "pending":
            # Objetivo JÁ em andamento: trocar o app no meio mataria a navegação dele. Ele termina na versão que
            # tem; a entrega acontece antes do próximo objetivo.
            return None
        return (f"versão {rotulo} distribuída para o parque",
                lambda: self._entregar(rt, package, desejada))

    async def _entregar(self, rt: DeviceRuntime, package: str, release_id: str) -> Any:
        """Instala uma versão distribuída. Invólucro de `install_on` com UMA garantia a mais: falha sempre vira estado.

        `install_on` pode levantar antes de tocar no estado (arquivo ausente no catálogo, hash que não confere, adb
        que não responde ao ler o perfil). Sem registrar isso, a porta veria o aparelho "pronto para tentar" e
        dispararia o mesmo job a cada tick, para sempre.

        ADR-026: quando a versão instalada foi voltada e a promovida é MENOR, a entrega é um
        rebaixamento — vai com `-d`, preservando os dados, como o `rollback`. O Android pode recusar; a recusa fica
        nomeada (`downgrade_refused`) e não se repete sozinha: reinstalar resolve, mas apaga a sessão.
        """
        from .devices.installer import DowngradeRefused  # noqa: PLC0415

        if (quarentena := self.quarentena(rt.id)) is not None:
            # Última linha da quarentena (ADR-055): quem chega aqui por um caminho que não conferiu antes não
            # instala nem abre o app. Levanta ANTES do `try`: não é falha de entrega, e não vira `install_failed`.
            raise ReleaseValidationError(quarentena + ".")
        inicio = parse_iso(to_iso(now()))       # na resolução do banco (ms): comparável com `observed_at`
        rebaixar =self._rebaixa_do_parque(rt.id, package, release_id)
        try:
            return await self.releases.install_on(rt, release_id, self.installer, allow_downgrade=rebaixar)
        except InstalacaoIncerta:
            # Resultado DESCONHECIDO, não falha: `install_on` já deixou a linha em `verifying` sem operação
            # pendente, que é o estado com saída (a releitura automática resolve). Carimbar `install_failed`
            # aqui era justamente o defeito: um timeout de leitura virava estado pegajoso que só saía com
            # "Distribuir de novo" — o que REINSTALA um app que já estava instalado e funcionando.
            raise
        except Exception as exc:
            row = self.release_repo.app_state(rt.id, package)
            if row is None or row["state"] not in self._ENTREGA_FALHOU:
                self.release_repo.upsert_app_state(rt.id, package, state="install_failed", pending_op=None,
                                                   pending_op_at=None, detail=str(exc)[:300])
            if isinstance(exc, DowngradeRefused):
                self.release_repo.upsert_app_state(
                    rt.id, package, drift_kind="downgrade_refused",
                    detail=("A versão deste aparelho saiu do parque e o Android recusou voltar para a promovida "
                            if rebaixar else "O Android recusou instalar esta versão por cima de uma mais nova ")
                    + f"preservando os dados: {exc} Reinstalar resolve, mas apaga a sessão.")
            # O relógio da nova tentativa diária (`_ultima_tentativa_de_entrega`): a falha que acontece ANTES do
            # `adb install` (perfil, compatibilidade lida do aparelho, arquivo do catálogo) não deixou prova.
            prova = self.db.one("SELECT observed_at FROM app_release_validations WHERE release_id=? AND instance_id=?"
                                " AND stage='install' ORDER BY id DESC LIMIT 1", (release_id, rt.id))
            if prova is None or not prova["observed_at"] or parse_iso(str(prova["observed_at"])) < inicio:
                self.release_repo.record_validation(release_id, rt.id, stage="install", ok=False,
                                                    detail=str(exc)[:300])
            raise

    def _rebaixa_do_parque(self, instance_id: str, package: str, release_id: str) -> bool:
        """A entrega de `release_id` é o parque voltando de uma versão que ele abandonou (voltada)?"""
        row = self.release_repo.app_state(instance_id, package)
        instalada = self.release_no_aparelho(row)
        alvo = self.release_repo.release_row(release_id)
        return bool(instalada is not None and alvo is not None and instalada["channel"] in self._CANAIS_ABANDONADOS
                    and int(instalada["version_code"]) > int(alvo["version_code"]))

    def _rollout_pending(self) -> list[tuple[str, Any]]:
        """(aparelho, trabalho) de cada entrega imediata ainda por fazer. Chamado a cada tick: barato quando vazio.

        Usa as MESMAS travas da porta do app: só quem está em estado de entrega automática entra; quem falhou sai da
        fila e espera uma pessoa. Quando não sobra ninguém, a entrega imediata daquela release se encerra sozinha.
        """
        if not self._entrega_imediata:
            return []
        saida: list[tuple[str, Any]] = []
        for rid in list(self._entrega_imediata):
            rel = self.release_repo.release_row(rid)
            entregavel = rel is not None and rel["status"] == "installable" and rel["channel"] == "promoted"
            linhas = self.db.query("SELECT instance_id, state, installed_release_id FROM device_app_state"
                                   " WHERE desired_release_id=?", (rid,)) if entregavel else []
            candidatos = [r for r in linhas if r["installed_release_id"] != rid
                          and r["state"] in self._ENTREGA_AUTOMATICA and r["instance_id"] in self.devices.devices
                          and not self.devices.devices[r["instance_id"]].store]
            # Aparelho de outra máquina que está fora do ar NÃO é pendência desta entrega: o rodízio daqui não o
            # liga (`_rotate` exclui `rt.external`), então ele seguraria o conjunto aberto para sempre — duas
            # consultas por segundo, o aviso "Entrega imediata encerrada" nunca saindo, até alguém ligar o
            # aparelho à mão ou o backend reiniciar. A versão desejada continua gravada nele: quando voltar,
            # recebe pela porta do app, antes da tarefa, que é o caminho não-imediato de sempre.
            nao_ligaveis = [r["instance_id"] for r in candidatos
                            if self.devices.devices[r["instance_id"]].external
                            and self.devices.devices[r["instance_id"]].state != InstanceState.online]
            # Aparelho em quarentena (ADR-055) também não é pendência: ninguém entrega nada nele até uma pessoa
            # decidir, e ele seguraria a entrega imediata aberta para sempre.
            em_quarentena = [r["instance_id"] for r in candidatos if self.quarentena(r["instance_id"]) is not None]
            pendentes = [r for r in candidatos if r["instance_id"] not in nao_ligaveis
                         and r["instance_id"] not in em_quarentena]
            if not pendentes:
                self._entrega_imediata.discard(rid)
                prontos = sum(1 for r in linhas if r["installed_release_id"] == rid)
                falhas = sum(1 for r in linhas if r["state"] in self._ENTREGA_FALHOU)
                esperando = (f", {len(nao_ligaveis)} aguardando ser ligados em outro servidor "
                             f"({', '.join(sorted(nao_ligaveis))})" if nao_ligaveis else "")
                if em_quarentena:
                    esperando += (f", {len(em_quarentena)} em quarentena por conta travada "
                                  f"({', '.join(sorted(em_quarentena))})")
                self.bus.emit("log", f"Entrega imediata encerrada: {prontos} aparelho(s) na versão, {falhas} com falha"
                                     + esperando
                                     + ("" if entregavel else " — a versão deixou de poder ser entregue") + ".",
                              level="warn" if falhas or nao_ligaveis or em_quarentena or not entregavel else "info",
                              data={"release_id": rid})
                continue
            package = rel["package_name"]
            for r in pendentes:
                rt = self.devices.devices[r["instance_id"]]
                saida.append((rt.id, lambda rt=rt, package=package, rid=rid: self._entregar(rt, package, rid)))
        return saida

    def distribute(self, release_id: str, *, eager: bool = False, instance_ids: list[str] | None = None,
                   count: int | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
        """Distribui uma versão PROMOVIDA ao parque. Canário primeiro: sem prova, não há o que distribuir.

        Grava a versão desejada em cada aparelho de tarefa. Quem está ligado e livre instala já; quem está desligado
        ou ocupado fica pendente e recebe pela porta do app, ao pegar a próxima tarefa daquele pacote. Pedir de novo
        é a nova tentativa explícita para quem tinha falhado.

        `eager` = "instalar em todos agora": além disso, os aparelhos pendentes viram demanda do rodízio, que os liga
        dentro das vagas, instala e cede a vaga ao próximo — sem esperar tarefa.

        Loja de apps (26/09): `instance_ids` restringe aos aparelhos escolhidos e `count` deixa o backend escolher N
        (ver `vitrine.escolher_para_distribuir`); sem os dois, o parque inteiro. `dry_run` é a prévia: o mesmo
        julgamento aparelho por aparelho, sem gravar nem instalar nada — o painel mostra ANTES de confirmar.
        """
        from .releases.catalog import ReleaseValidationError

        rel = self.release_repo.release_row(release_id)
        if rel is None:
            raise ReleaseValidationError("Release não encontrada.")
        if rel["status"] != "installable":
            raise ReleaseValidationError(f"A release está em '{rel['status']}' e não pode ser instalada.")
        if rel["channel"] != "promoted":
            raise ReleaseValidationError(
                f"Só se distribui versão PROMOVIDA; esta está em '{rel['channel']}'. Coloque-a em prova num aparelho, "
                "confira que instalou e abriu, promova — e então distribua.")
        package = rel["package_name"]
        requisitos = requisitos_de_release(rel)
        alvos = alvos_da_distribuicao(self, rel, instance_ids=instance_ids, count=count)
        saida: list[dict[str, Any]] = []
        for rt in alvos:
            if (quarentena := self.quarentena(rt.id)) is not None:
                # Conta travada logada (ADR-055): o aparelho fica como está, sem versão desejada — instalar termina
                # na prova de abertura do app. Quando uma pessoa resolver, a convergência o alcança.
                saida.append({"id": rt.id, "outcome": "kept", "reason": quarentena})
                continue
            if (porque := motivo_incompativel(requisitos, capacidades_de(rt), aparelho=rt.id)) is not None:
                # A versão desejada NÃO é gravada: mandar instalar o que não roda ali deixaria o aparelho em
                # falha permanente de entrega, e o operador sem saber por quê. A explicação sai com o resultado.
                saida.append({"id": rt.id, "outcome": "incompatible", "reason": porque})
                continue
            row = self.release_repo.app_state(rt.id, package)
            if row and row["installed_release_id"] == release_id and row["state"] in ("ready", "installed"):
                if not dry_run:
                    self.release_repo.upsert_app_state(rt.id, package, desired_release_id=release_id)
                saida.append({"id": rt.id, "outcome": "already", "reason": "já está nesta versão"})
                continue
            if dry_run:
                saida.append(previa_de_entrega(self, rt, row, eager=eager))
                continue
            campos: dict[str, Any] = {"desired_release_id": release_id}
            if row and row["state"] in self._ENTREGA_FALHOU:
                # Nova tentativa pedida por uma pessoa: rearma a ÚNICA tentativa automática.
                campos.update(state="installed" if row["observed_version_code"] is not None else "missing",
                              drift_kind=None, detail="nova tentativa de entrega pedida")
            self.release_repo.upsert_app_state(rt.id, package, **campos)
            if rt.state.value != "online":
                # A promessa tem de ser a do CÓDIGO: `_rotate` exclui `rt.external` da demanda, então ninguém
                # daqui liga um aparelho de outra máquina. Dizer "o rodízio vai ligá-lo para instalar agora" era
                # afirmar o que não vai acontecer — e, pior, prendia a entrega imediata aberta para sempre.
                if rt.external:
                    motivo = (f"está em outro servidor e {rt.state.value}: ninguém aqui o liga. Ligue-o pela "
                              "Infraestrutura; a versão já está marcada e instala quando ele voltar")
                elif eager:
                    motivo = f"está {rt.state.value}: o rodízio vai ligá-lo para instalar agora"
                else:
                    motivo = f"está {rt.state.value}: instala ao entrar em serviço, antes da tarefa"
                saida.append({"id": rt.id, "outcome": "pending", "reason": motivo})
            else:
                # UM COMANDO POR APARELHO: a entrega deixa de ser um `202 {"accepted": true}` coletivo cujo
                # desfecho só aparecia relendo `GET /api/app-state`. Cada aparelho ganha id acompanhável, e o
                # timeout do adb vira `uncertain` em vez de falha pegajosa.
                cmd = despacho._despachar_trabalho(
                    self, rt, "app.distribute", lambda rt=rt: self._entregar(rt, package, release_id),
                    label="entrega do aplicativo", params={"release_id": release_id, "package": package},
                    recusar_ocupado=False,
                    ocupado="ocupado agora: instala quando pegar a próxima tarefa")
                if cmd.get("accepted"):
                    saida.append({"id": rt.id, "outcome": "started", "reason": "instalando agora",
                                  "command_id": cmd["command_id"]})
                else:
                    saida.append({"id": rt.id, "outcome": "pending", "reason": cmd["reason"],
                                  "command_id": cmd["command_id"]})
        if dry_run:
            return saida                          # prévia: nada foi gravado, nada a acordar nem a anunciar
        if eager:
            self._entrega_imediata.add(release_id)
            self.scheduler.wake()
        self.bus.emit("log", f"{package} {rel['version_name']} ({rel['version_code']}) distribuída"
                             f"{' — instalar em todos agora' if eager else ''}: "
                             + ", ".join(f"{d['id']}={d['outcome']}" for d in saida),
                      data={"release_id": release_id})
        return saida

    async def _policy_gate(self, obj: Any, srow: Any, run: Any) -> Any:
        """Quarta porta, e a única que depende da ETAPA: política e limite da capability para este perfil.

        Devolve `None` quando pode seguir. Etapa sem capability (app sem catálogo) nunca passa por aqui — o QA
        Messenger e o caminho livre seguem exatamente como antes.
        """
        capability = srow["capability"] if "capability" in srow.keys() else None
        if not capability:
            # Item 13.2: etapa com EFEITO externo sem ação do catálogo, num app que TEM catálogo, passaria por fora de
            # política, aprovação, limite e coordenação de frota (uma habilidade treinada ou um plano livre que
            # atravessa apps). Não passa: pede a ação do catálogo.
            if srow["side_effect"]:
                rt0 = self.devices.devices.get(obj["instance_id"])
                app0 = self.scheduler._app_context(run, rt0, _col_app(srow))[0] if rt0 else None  # noqa: SLF001
                if app0 is not None and app0.package and load_catalog(app0.package) is not None:
                    return Verdict(allowed=False, policy="manual_only",
                                   reason=f"etapa com efeito externo em {app0.name or app0.package} sem a ação do "
                                          "catálogo — ela passaria por fora da política e dos limites do perfil",
                                   hint="Refaça a habilidade escolhendo a ação do catálogo desta etapa (ou replaneje).")
            return None
        profile_id = obj["profile_id"] or self.social_repo.perfil_unico_da_instancia(obj["instance_id"])
        rt = self.devices.devices.get(obj["instance_id"])
        app_da_etapa = self.scheduler._app_context(run, rt, _col_app(srow))[0] if rt else None  # noqa: SLF001
        pacote = app_da_etapa.package if app_da_etapa else None
        cap = capability_of(pacote, capability)
        if cap is None:
            return None
        if not profile_id:
            # Sem perfil não há voz para escrever nem política para aprovar. Deixar passar seria pior do que
            # parecer: como o texto deixou de ser congelado no plano, a etapa chega ao ator SEM `content` e SEM a
            # guarda que dependia dele — o modelo inventaria a frase e publicaria, sem aval de ninguém. Antes
            # desta série o texto literal segurava esse caso; hoje quem segura é esta porta.
            if cap.needs_draft and texto_a_gerar(loads(srow["bindings"], {}) or {}) is not None:
                return Verdict(allowed=False, policy=cap.default_policy,
                               reason="este aparelho não tem perfil vinculado: não há voz para escrever o texto "
                                      "desta etapa nem política para aprová-lo",
                               hint="Vincule um perfil a este aparelho (ou peça o texto exato no comando, com "
                                    "“envie exatamente…”) e retome o item.")
            return None
        # Alvo desta etapa, para a coordenação de frota (achado #114, ADR-055): o argumento que a AÇÃO declara no
        # catálogo (`Capability.counterparty`), normalizado. Antes era `username` cru — curtir e comentar não o têm,
        # e a porta de frota recebia `None` e liberava tudo; `@Ana` e `@ana` eram duas pessoas.
        bindings = (loads(srow["bindings"], {}) or {}) if "bindings" in srow.keys() else {}
        alvo = contraparte(cap, bindings)
        # O mesmo pedido, nesta execução, a outras contas sobre o mesmo alvo (o caso de 19/09: uma execução, sete
        # contas, uma pessoa). A porta de frota conta o que JÁ aconteceu; os objetivos irmãos chegam aqui juntos,
        # antes de qualquer um disparar, e passariam todos. Decide-se pela execução, de forma determinística.
        irmaos = self._mesmo_pedido_noutras_contas(obj, cap, profile_id, alvo)
        confirmacao = ""
        if irmaos:
            contas = len({profile_id, *(dono for _o, _a, dono in irmaos)})
            escolhido_id, escolhido_aparelho = min([(obj["id"], obj["instance_id"]),
                                                    *((o, a) for o, a, _d in irmaos)])
            if cap.limit_bucket in UMA_CONTA_POR_ALVO and escolhido_id != obj["id"]:
                return Verdict(
                    allowed=False, policy=cap.default_policy,
                    reason=(f"esta execução manda o mesmo pedido ({cap.key}) a {contas} contas sobre {alvo}; em "
                            "seguir, mensagem e comentário vale uma conta por alvo (ADR-055) — segue só a de "
                            f"{escolhido_aparelho}, e esta foi recusada"),
                    hint="Nada foi feito por esta conta. Para outro alvo, faça um pedido separado.")
            confirmacao = (f"confirmação exigida: esta execução manda o mesmo pedido ({cap.key}) a {contas} contas "
                           f"sobre {alvo}" + (" — só esta conta segue; as outras foram recusadas"
                                              if cap.limit_bucket in UMA_CONTA_POR_ALVO else ""))
            if self.approvals.for_step(srow["id"]) is None:        # uma vez por etapa, não a cada retomada
                self.repo.decision(f"{obj['instance_id']}: {confirmacao}", run_id=obj["run_id"],
                                   instance_id=obj["instance_id"], step_id=srow["id"])
        veredito = self.policies.check(profile_id, cap, run_id=obj["run_id"], counterparty=alvo,
                                       app_id=app_da_etapa.id if app_da_etapa else None)
        if not veredito.allowed:
            return veredito
        # O texto é escrito AQUI, com a persona deste perfil, antes de qualquer digitação e antes da aprovação —
        # senão a pessoa aprovaria um rascunho que não é o que vai ser enviado.
        parado = await self._draft_gate(obj, srow, cap, profile_id, rt=rt, pacote=pacote)
        if parado is not None:
            return parado
        srow = self.repo.step_row(srow["id"]) or srow          # relê: o texto pode ter acabado de entrar
        # Aprovação por política, por DM fria (o porquê vem no `reason` do veredito que libera) ou pela confirmação
        # do mesmo pedido a várias contas — nenhum grupo nem perfil afrouxa as duas últimas.
        if veredito.needs_approval or confirmacao:
            motivo = "; ".join(m for m in (veredito.reason, confirmacao) if m)
            return self._approval_gate(obj, srow, cap, profile_id, motivo=motivo)
        return None

    def _mesmo_pedido_noutras_contas(self, obj: Row, cap: Capability, profile_id: str,
                                     alvo: str | None) -> list[tuple[str, str, str]]:
        """`(objetivo, aparelho, perfil)` das OUTRAS contas desta execução com a mesma ação sobre o mesmo alvo.

        Só conta objetivo vivo (falhou ou foi cancelado não age mais), etapa da versão atual do plano dele e não
        cancelada, e alvo já concreto (uma cópia de `for_each` ainda com `{item}` não é alvo de ninguém). A mesma
        persona em dois aparelhos não é "outra conta": o aviso disso é da prévia de alvos."""
        if not alvo or not cap.side_effect or not cap.limit_bucket:
            return []
        linhas = self.db.query(
            "SELECT o.id AS objetivo, o.instance_id, o.profile_id, s.bindings FROM steps s"
            " JOIN objectives o ON o.id = s.objective_id"
            " WHERE s.run_id=? AND s.capability=? AND s.plan_version=o.plan_version AND s.status<>'cancelled'"
            " AND o.id<>? AND o.status NOT IN ('failed','cancelled')",
            (obj["run_id"], cap.key, obj["id"]))
        achados: dict[str, tuple[str, str]] = {}
        for r in linhas:
            dono = r["profile_id"] or self.social_repo.perfil_unico_da_instancia(r["instance_id"])
            if dono and dono != profile_id and contraparte(cap, loads(r["bindings"], {}) or {}) == alvo:
                achados[str(r["objetivo"])] = (str(r["instance_id"]), str(dono))
        return sorted((o, a, d) for o, (a, d) in achados.items())

    def _registrar_leitura(self, obj: Any, step: Any, items: list[str]) -> None:
        """Uma etapa de leitura de conversa terminou: o que a outra pessoa disse entra no HISTÓRICO do perfil.

        É a metade que faltava do caminho de mensagem direta (achado #108). Antes desta porta, `READ_MESSAGES`
        lia a conversa, mostrava os itens na evidência da etapa e jogava tudo fora: nenhuma interação de entrada
        era gravada em perfil nenhum, e por isso `GET /api/instagram/profiles/{id}/memory` era `[]` nos oito.

        O alvo é quem a conversa ABRIU. `READ_MESSAGES` não tem `username` — a etapa que tem é a `OPEN_THREAD`
        de que ela depende, e é dali que o nome sai. Sem alvo não se grava nada: fala sem dono não tem a quem
        ser atribuída.

        Que capability é leitura de conversa é declaração do APP da etapa (`AppDefinition.conversation_reads`):
        a mesma chave noutro app não diz nada sobre fala de ninguém.
        """
        capability = getattr(step, "capability", None)
        if not capability or not items:
            return
        tipo = capabilities_of(self._pacote_da_etapa(obj, step)).conversation_read(capability)
        if not tipo:
            return
        profile_id = obj["profile_id"] or self.social_repo.perfil_unico_da_instancia(obj["instance_id"])
        if not profile_id:
            return
        alvo = self._alvo_da_conversa(obj, step)
        if not alvo:
            log.info("etapa %s leu %d mensagem(ns) sem alvo identificável: nada gravado", step.id, len(items))
            return
        gravadas = self.social.record_inbound(
            profile_id, texts=items, counterparty=alvo, type=tipo, run_id=obj["run_id"], objective_id=obj["id"],
            step_id=step.id, instance_id=obj["instance_id"],
            evidence=f"lida pela etapa '{step.title}' no aparelho {obj['instance_id']}")
        if gravadas:
            self.bus.emit("log", f"{obj['instance_id']}: {len(gravadas)} fala(s) de {alvo} entraram no histórico "
                                 f"do perfil", run_id=obj["run_id"], instance_id=obj["instance_id"],
                          objective_id=obj["id"])

    def _pacote_da_etapa(self, obj: Mapping[str, object], step: object) -> str | None:
        """O pacote do app desta etapa: o dela, senão o do plano, senão o do aparelho (`Scheduler._app_context`).
        Sem execução ou sem aparelho para perguntar, o app da conta do perfil — o único que grava fala hoje."""
        run = self.repo.run_row(str(obj["run_id"]))
        rt = self.devices.devices.get(str(obj["instance_id"]))
        if run is not None and rt is not None:
            try:
                app, _ = self.scheduler._app_context(run, rt, getattr(step, "app_id", None))  # noqa: SLF001
            except KeyError:
                app = None
            if app is not None and app.package:
                return app.package
        return self.social_repo.app_package

    def _alvo_da_conversa(self, obj: Any, step: Any) -> str | None:
        """De quem é a conversa que esta etapa leu: o `username` dela, ou o da etapa de que ela depende.

        A repetição sobre lista resolve `{item}` nos argumentos ANTES de a etapa rodar, então no banco o
        `username` já está concreto — não há `{item}` para expandir aqui.
        """
        proprio = (getattr(step, "bindings", None) or {}).get("username")
        if proprio:
            return str(proprio)
        for chave in (getattr(step, "depends_on", None) or []):
            row = self.db.one("SELECT bindings FROM steps WHERE objective_id=? AND key=? ORDER BY plan_version DESC"
                              " LIMIT 1", (obj["id"], chave))
            alvo = (loads(row["bindings"], {}) or {}).get("username") if row else None
            if alvo:
                return str(alvo)
        return None

    async def _draft_gate(self, obj: Any, srow: Any, cap: Any, profile_id: str, *, rt: Any = None,
                          pacote: str | None = None) -> Any:
        """Escreve o texto desta etapa na voz DESTE perfil, quando ele ainda não está fechado.

        O mesmo plano roda em vários aparelhos. Se o texto vier congelado do planejador, oito contas publicam a
        mesma frase — foi o que aconteceu em r-20260918181035-7bfa38. Aqui cada perfil escreve a sua versão a
        partir do briefing, com persona, memória e histórico próprios.
        """
        if not cap.needs_draft:
            return None
        bindings = loads(srow["bindings"], {}) or {}
        briefing = texto_a_gerar(bindings)
        if briefing is None:                                   # texto exato pedido no comando
            return None
        # Uma escrita por vez dentro desta execução: a lista de "não repita" é lida do que os irmãos JÁ
        # escreveram, e com todos gerando ao mesmo tempo todos leriam a lista vazia. O lock é por execução, então
        # aparelhos de execuções diferentes continuam escrevendo em paralelo.
        async with self._draft_locks.setdefault(obj["run_id"], asyncio.Lock()):
            # Esta porta é atravessada de novo toda vez que o objetivo é retomado — e é exatamente o que acontece
            # depois de alguém aprovar. Sem esta marca, o gate reescrevia o texto: a pessoa lia e aprovava uma
            # frase, e o aparelho digitava outra, gerada depois. Rascunho guardado é rascunho fechado.
            #
            # O pedido de aprovação conta como a mesma prova, e é o que protege as etapas rascunhadas ANTES desta
            # coluna existir (`draft_meta` nulo): se existe pedido, aquele texto já foi mostrado a alguém.
            #
            # A conferência é feita DENTRO do lock: quem esperou na fila pode ter esperado justamente por si.
            if ler_rascunho(self.db, srow["id"]) or self.approvals.for_step(srow["id"]) is not None:
                return None
            # O tipo de texto e as leituras de tela são do APP da etapa (o manifesto dele no registro de apps).
            tipo = capabilities_of(pacote).text_kind(cap.key) or "dm_initiate"
            leitor = screen_reader_of(pacote)
            # Quem recebe o texto é o alvo que a AÇÃO declara (ADR-055): no comentário, o autor da publicação.
            alvo = alvo_da_acao(cap, bindings)
            arvore = await self._ler_tela(rt, pacote)
            tela = leitor.visible_content(arvore) if leitor is not None and arvore is not None else ""
            # Responder é diferente de comentar: aqui existe uma fala DIRIGIDA a esta conta, e é ela que fundamenta
            # tanto a resposta quanto o que o perfil passa a saber sobre a pessoa. Só deste bloco sai memória.
            recebido = (leitor.comment_of(arvore, alvo or "")
                        if leitor is not None and arvore is not None and tipo == "comment_reply" else "")
            fio = None
            if tipo == "dm_initiate":
                # O caminho de DM não tinha lado de "recebido": o que a pessoa respondia entrava como texto de
                # tela, não virava fala dela, e por isso nunca virava memória (achado #108). A fala vem de duas
                # fontes, nesta ordem de confiança: o que ESTÁ ESCRITO na conversa aberta com atribuição de autor,
                # e o que uma etapa de leitura já gravou neste fio e ainda não foi respondido.
                fio = thread_de_dm(alvo)
                recebido = (leitor.message_of(arvore, alvo or "")
                            if leitor is not None and arvore is not None else "") or \
                    self.social.last_incoming(profile_id, counterparty=alvo, thread_key=fio)
                if recebido:
                    tipo = "dm_reply"
            try:
                # `persist=False` de propósito: interação é TENTATIVA, e um rascunho não é. `pending` conta para o
                # limite ("uma ação que talvez tenha saído já mexeu com a conta"), então gravar aqui gastaria a
                # cota antes de digitar nada e contaria duas vezes o que fosse enviado — quem registra o efeito é
                # o commit.
                draft, _interacao = await self.social.draft_response(
                    profile_id, kind=tipo, brief=briefing, persist=False, incoming=recebido,
                    # O fio é o que traz a conversa ao prompt: sem ele, `<resumo_da_conversa>` nunca aparecia
                    # para quem estava escrevendo, por mais mensagens que já tivessem sido trocadas.
                    counterparty=alvo, screen=tela, thread_key=fio,
                    # Escrever é uma chamada de modelo DENTRO de uma execução: passa pelo mesmo caminho das
                    # outras, com limite de simultâneas, teto de orçamento conferido antes de gastar e custo
                    # lançado no objetivo certo.
                    runner=lambda f: self.scheduler.executor._ai(  # noqa: SLF001
                        obj["run_id"], obj["id"], f, step_id=srow["id"], role="social"),
                    avoid=textos_irmaos(self.db, obj["run_id"], srow["id"]))
            except SocialError as exc:
                # Sem texto não se digita nada. Isso é espera por uma pessoa, não falha da etapa: o briefing
                # continua lá e uma nova tentativa pode gerar.
                # Cada motivo com a sua saída (achado #93, ponto 3): mandar conferir a chave quando o que acabou
                # foi o orçamento, ou quando o provedor RECUSOU por política (nada a ver com chave nem persona),
                # faria a pessoa procurar defeito onde não há e bater na mesma parede.
                dica = ("Aumente o orçamento de IA em Configuração (chamadas por objetivo ou tokens por execução) "
                       "e retome o item." if exc.code == "ai_budget" else
                       "O provedor recusou por política — repetir tende a dar o mesmo resultado. Reescreva a "
                       "intenção deste texto (ou o comando) e retome o item." if exc.code == "ai_refusal" else
                       "Confira o provedor de IA e a persona do perfil, e retome o item.")
                return Verdict(allowed=False, policy=cap.default_policy,
                               reason=f"não foi possível escrever o texto desta etapa: {exc}", hint=dica)
            if draft.refused or not (draft.content or "").strip():
                # O motivo da recusa vem antes da justificativa: é ele que diz o que mudar na intenção (ex.: o
                # texto atribuía um recado a um terceiro, ADR-055) — a justificativa só explica a escolha do texto.
                return Verdict(allowed=False, policy=cap.default_policy,
                               reason="a persona se recusou a escrever este texto",
                               hint=f"{draft.refusal_reason or draft.rationale or 'sem justificativa'}. Reescreva a "
                                    "intenção e retome o item.")
            # Texto e marca na MESMA transação: um crash entre os dois deixaria a etapa com texto novo e sem
            # marca, e a retomada geraria outro por cima — pago, e por cima do que já estava escrito.
            with self.db.tx():
                definir_texto(self.db, srow["id"], draft.content)
                # O que o rascunho percebeu não cabe em `bindings` (que é prompt do ator) e morreria aqui.
                # Guardado na etapa, sobrevive à espera por aprovação e a um reinício, e o commit o anexa à
                # interação — é assim que `learn_from` finalmente tem o que aprender.
                guardar_rascunho(self.db, srow["id"], {
                    "memory_candidates": [c.model_dump() for c in draft.memory_candidates],
                    "rationale": draft.rationale or "",
                    # Fica registrado o que o perfil TINHA À VISTA ao escrever — é o que explica o texto depois,
                    # numa auditoria. Não vai para o histórico como fala de ninguém: é tela, não é conversa.
                    "screen_seen": tela[:400],
                    "incoming": recebido,
                })
        self.bus.emit("log", f"{obj['instance_id']}: texto escrito na voz do perfil — {draft.content[:60]}",
                      run_id=obj["run_id"], instance_id=obj["instance_id"], objective_id=obj["id"])
        return None

    async def _ler_tela(self, rt: Any, pacote: str | None) -> Any:
        """O que está escrito na tela do aparelho agora — para o texto falar do que está ali.

        Nunca é obrigatório: se o aparelho não responder, se a sessão de automação não estiver de pé ou se a tela
        for de outro app, o rascunho segue sem ela. Ler a tela é bônus de contexto, não porta.

        O teto de tempo é próprio e curto de propósito: `ensure_automation` espera até 240 s por uma sessão que
        está subindo, e segurar o aparelho quatro minutos por um contexto opcional seria péssimo negócio. A etapa
        seguinte vai observar a tela de qualquer jeito.

        Devolve a árvore, e não o texto: quem escreve um comentário quer o que está na publicação, quem responde
        alguém quer a fala DAQUELA pessoa. São leituras diferentes da mesma tela, e ler duas vezes seria o dobro
        do custo pelo mesmo instante.
        """
        if rt is None:
            return None
        # Ler a tela NUNCA sobe a sessão de automação. Se subisse, o teto curto daqui cancelaria `ensure_automation`
        # no meio — e `CancelledError` não é `Exception`, então o aparelho ficaria marcado como "starting" para
        # sempre, estado que o monitor não re-tenta. O passo seguinte então esperaria 240 s por vez, três vezes,
        # com o aparelho preso. Contexto opcional não pode custar isso: se a sessão não está de pé, escreve sem.
        if rt.automation.state != "ready" or not rt.session.connected:
            return None
        try:
            arvore = await asyncio.wait_for(self.devices.hierarchy(rt), timeout=_TELA_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - qualquer falha de aparelho só custa contexto, não trava a etapa
            log.info("%s: não deu para ler a tela para o rascunho (%s)", getattr(rt, "id", "?"), exc)
            return None
        # Depois de revisão de plano o app é reiniciado ANTES desta porta: a tela pode ser a de início do Android.
        # Ler o launcher e mandar ao modelo como "o que está na tela" seria pior do que não ler nada.
        if pacote and pacote not in arvore.packages:
            return None
        return arvore

    def _approval_gate(self, obj: Any, srow: Any, cap: Any, profile_id: str, *, motivo: str = "") -> Any:
        """Ação que exige aprovação: a decisão da pessoa acontece ANTES de digitar qualquer coisa.

        É por isso que a porta fica aqui e não no meio da etapa: etapa concluída é estado terminal, então não
        haveria como "editar e refazer" depois que o texto já foi digitado e enviado.

        `motivo` é o porquê de a aprovação ser exigida além da política (DM fria, o mesmo pedido a várias contas —
        ADR-055): vai no resumo do pedido e no motivo da espera, para quem decide saber o que está confirmando.
        """
        pedido = self.approvals.for_step(srow["id"])
        bindings = loads(srow["bindings"], {}) or {}
        # O alvo normalizado é a chave da reserva de frota (`SocialRepository.fleet_targeting`).
        alvo = contraparte(cap, bindings) or alvo_da_acao(cap, bindings)
        if pedido is None:
            # Etapa revisada (recuperação automática, “Tentar novamente”) tem id novo: sem isto, o que a pessoa já
            # aprovou na versão anterior virava pedido novo e o objetivo voltava a esperá-la. Só vale a decisão
            # sobre a mesma etapa, com o mesmo alvo e o mesmo texto, cujo efeito ainda não saiu.
            pedido = self.approvals.acompanhar_revisao(
                srow["id"], profile_id=profile_id, capability=cap.key, target=alvo, content=bindings.get("content"),
                disparou=lambda etapa: self.repo.commit_state(etapa)[0])
            if pedido is not None:
                self.repo.decision(
                    f"{obj['instance_id']}: a decisão {pedido.id} ({pedido.status}) sobre '{srow['title']}' numa "
                    f"versão anterior do plano vale para a etapa revisada (v{srow['plan_version']}): mesma ação, mesmo "
                    "alvo e mesmo texto, e o efeito ainda não tinha saído.",
                    run_id=obj["run_id"], instance_id=obj["instance_id"], step_id=srow["id"])
        if pedido is None:
            pedido = self.approvals.open(
                profile_id=profile_id, capability=cap.key,
                summary=f"{srow['title']} — {motivo}" if motivo else srow["title"],
                target=alvo, content=bindings.get("content"),
                run_id=obj["run_id"], objective_id=obj["id"], step_id=srow["id"])
            self.bus.emit("approval.pending", f"{obj['instance_id']}: {srow['title']} aguarda aprovação",
                          level="warn", run_id=obj["run_id"], instance_id=obj["instance_id"],
                          objective_id=obj["id"], data={"approval": pedido.to_dict()})
        if pedido.status in ("approved", "edited"):
            return None
        if pedido.status == "rejected":
            return Verdict(allowed=False, policy="approval_required",
                           reason="esta ação foi rejeitada por quem aprova",
                           hint="Nada será enviado neste alvo. Retome o item se quiser planejar outra coisa.")
        self.db.execute("UPDATE objectives SET blocked_kind='approval' WHERE id=?", (obj["id"],))
        return Verdict(allowed=False, policy="approval_required",
                       reason=f"{srow['title']} precisa de aprovação antes de acontecer"
                              + (f" ({motivo})" if motivo else ""),
                       hint="Abra Aprovações e escolha aprovar, editar ou rejeitar.")

    def _seed_apps(self) -> None:
        """Os apps do `config.yaml` entram no registro na subida; o que já existe (mesmo id) fica como está."""
        for a in self.cfg.file.apps:
            if self.apps.obter(a.id) is not None:
                continue
            self.apps.criar(app_id=a.id, name=a.name, package=a.package, activity=a.activity, apk_path=a.apk_path,
                            nav_hints=a.nav_hints, known_selectors=a.known_selectors, builtin=a.builtin)

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
        self.bus.bind_loop(asyncio.get_running_loop())
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
            # os quinze diziam `qa-user-NN` da configuração, e o android-04 com o felipe logado enganou um experimento.
            self.social_repo.sincronizar_rotulos()
            # Antes do scheduler e da reconciliação: a partir daqui o ciclo de vida local tem para quem ir, e um
            # comando despachado sem o worker local no ar seria recusado com "não está conectado".
            await self.local_worker.conectar()
            await self.scheduler.start()
            self.runs.resume_planning_after_restart()
            self.releases.reconcile_after_restart()      # instalação interrompida nunca é repetida às cegas
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
            self._bg.append(asyncio.create_task(self._laco_do_outbox(), name="outbox"))
            self._bg.append(asyncio.create_task(self._retention_loop(), name="retention"))
            self._bg.append(asyncio.create_task(self._worker_reaper_loop(), name="worker-reaper"))
            self._bg.append(asyncio.create_task(self._saldos_loop(), name="saldos-de-ia"))
            # Loja de apps: o que ficou pendente em aparelho ligado e livre é entregue na varredura.
            self._bg.append(asyncio.create_task(laco_de_convergencia(self), name="loja-convergencia"))
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
            if self.manage_appium:
                await asyncio.to_thread(self.appium.stop)
            self.db.close()

    def _check_health(self) -> None:
        """Recalcula `health()` e emite `health.updated` só quando o resultado mudou desde a última checagem
        (achado #65). Método separado do laço para ser testável sem `asyncio.sleep`."""
        h = self.health()
        dump = h.model_dump(mode="json")
        if dump != self._last_health:
            self._last_health = dump
            self.bus.emit("health.updated", f"ambiente: {h.status}", level="warn" if h.status != "ok" else "info",
                          data={"health": dump})

    async def _health_loop(self) -> None:
        """`health()` faz um GET síncrono ao Appium (`is_up`, timeout de 1 s) — roda em thread para não travar
        o laço de eventos do resto do backend enquanto o Appium não responde."""
        while True:
            await asyncio.sleep(HEALTH_POLL_S)
            try:
                await asyncio.to_thread(self._check_health)
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

    async def _retention_loop(self) -> None:
        while True:
            try:
                s = self.settings.get()
                cutoff = to_iso(now() - timedelta(days=s.log_retention_days))
                removed = self.bus.purge_older_than(cutoff)
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
                if removed or old or outras or arquivos:
                    log.info("retenção: %s eventos, %s execuções com evidências, %s linhas de outras tabelas e "
                             "%s arquivo(s) removidos", removed, len(old), outras, arquivos)
            except Exception:  # noqa: BLE001
                log.exception("retenção")
            await asyncio.sleep(6 * 3600)

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
        total += self.db.execute(
            "DELETE FROM commands WHERE state IN ({}) AND finished_at IS NOT NULL AND finished_at < ?".format(
                ",".join("?" for _ in COMMAND_TERMINAL)),
            (*[s.value for s in COMMAND_TERMINAL], cutoff)).rowcount
        total += self.db.execute("DELETE FROM ai_calls WHERE ts < ?", (cutoff,)).rowcount
        total += self.db.execute("DELETE FROM measurements WHERE ts < ?", (cutoff,)).rowcount
        enroll_cut = to_iso(now() - timedelta(days=7))
        total += self.db.execute(
            "DELETE FROM worker_enrollments WHERE created_at < ? AND (used_at IS NOT NULL OR expires_at < ?)",
            (enroll_cut, to_iso(now()))).rowcount
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
            "(SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?)", (ev_cut, ev_cut))
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

    async def _saldos_loop(self) -> None:
        """Livro-caixa das contas de IA (ADR-051): a cada `SALDOS_INTERVALO_S` concilia com o relatório oficial do
        provedor e fecha o dia das contas com âncora velha. Sem isto a conciliação só andava quando alguém abria o
        painel — e o roteador e a saúde leem o que ela deixou."""
        while True:
            try:
                await conciliacao.atualizar(self.db, self.cfg, forcar=True)
                if await asyncio.to_thread(saldos.fechar_dia, self.db, self.cfg):
                    await conciliacao.atualizar(self.db, self.cfg, forcar=True)     # linha de base da âncora nova
            except Exception:  # noqa: BLE001 - relatório fora do ar não derruba o processo; a saúde mostra
                log.exception("livro-caixa das contas de IA")
            await asyncio.sleep(SALDOS_INTERVALO_S)

    def _problemas_de_saldo(self) -> list[Problem]:
        """Só conta EM USO vira problema: uma conta sem função nem imagem apontada para ela não para nada."""
        try:
            contas = self.saldos_de_ia()
        except Exception:  # noqa: BLE001 - a saúde nunca cai por causa do relatório de saldo
            log.exception("não foi possível calcular os saldos de IA")
            return []
        out: list[Problem] = []
        for c in contas:
            if not c.em_uso:
                continue
            usa = ", ".join(c.roles + (["imagem"] if c.image else []))
            if c.state in ("blocked", "exhausted"):
                out.append(Problem(code="ai_balance_blocked", message=f"{c.label}: {c.message}",
                                   hint=f"Usada por: {usa}. Recarregue no console ({c.console}) e registre a recarga "
                                        "em Configuração › IA (ou POST /api/ai/balances/<conta>/recharge)."))
            elif c.state == "low":
                out.append(Problem(code="ai_balance_low", message=f"{c.label}: {c.message}",
                                   hint=f"Usada por: {usa}. Recarregue antes de chegar ao limite de bloqueio."))
            elif c.state == "unknown":
                out.append(Problem(code="ai_balance_unknown", message=f"{c.label}: sem leitura de saldo registrada.",
                                   hint=f"Usada por: {usa}. Registre o saldo do console em Configuração › IA."))
            elif c.stale:
                out.append(Problem(code="ai_balance_stale", message=f"{c.label}: {c.message}",
                                   hint="O relatório oficial de uso do provedor não respondeu nos últimos "
                                        f"{saldos.CONCILIACAO_VELHA_MIN} min: o gasto de fora da plataforma não está "
                                        "entrando. Confira a chave de administrador no .env."))
        return out

    # ------------------------------------------------------------------ saúde
    def ai_status(self) -> AiStatus:
        """`provider.status()` só sabe da chave; o disjuntor de conta (crédito/credencial recusados em tempo de
        execução) vive no executor — combina os dois para health(), /api/ai e a aba IA lerem uma fonte só."""
        status = self.provider.status()
        breaker = self.scheduler.executor.ai_breaker
        if breaker is not None:
            status = status.model_copy(update={"account_blocked": True, "account_blocked_reason": breaker.message})
        try:
            contas = [c.as_dict() for c in self.saldos_de_ia()]
        except Exception:  # noqa: BLE001 - o status da IA nunca cai por causa do relatório de saldo
            log.exception("não foi possível calcular os saldos de IA")
            contas = []
        # O gerador de imagem não é papel do hub: entra aqui, ao lado, para a aba IA dizer quem é e se está pronto.
        return status.model_copy(update={"image": status_de_imagem(self.persona_images, self.cfg),
                                         "balances": contas})

    def _saude_do_banco(self) -> tuple[DatabaseStatus, list[Problem]]:
        """O banco responde? E o esquema dele ainda é o que estes arquivos de migração geram?

        Achado #33: `health()` não fazia nenhuma consulta. Com o PostgreSQL fora do ar — reinício, rede que
        piscou, sessão derrubada — o processo respondia `degraded/ok` alegremente enquanto toda operação falhava,
        e `migration` aparecia como `null` porque `ultima_migracao()` engole exceção. Aqui a pergunta é explícita
        e o silêncio vira `database_down`.

        Achado #169: e, já que a conexão está de pé, é o momento de conferir que nenhuma migração já aplicada foi
        editada no lugar — foi exatamente o que aconteceu com a 008 e ninguém viu por um mês.
        """
        problemas: list[Problem] = []
        alcancavel = self.db.alcancavel()
        if not alcancavel:
            problemas.append(Problem(
                code="database_down",
                message=f"O banco ({self.db.dialect}) não respondeu.",
                hint="Confira se o serviço do banco está no ar e alcançável desta máquina. A conexão é reaberta "
                     "sozinha na próxima consulta que der certo — não é preciso reiniciar o backend."))
        elif (mudaram := self.db.divergencias()):
            problemas.append(Problem(
                code="migration_changed",
                message="Migração já aplicada foi alterada no arquivo: " + ", ".join(sorted(mudaram)) + ".",
                hint="O esquema DESTE banco é o que a versão antiga do arquivo gerava, e um banco novo nasceria "
                     "diferente. Migração aplicada não se edita: crie a próxima migração com a diferença. Se a "
                     "mudança foi só de comentário, o alarme some quando o arquivo voltar ao que era."))
        return DatabaseStatus(dialect=self.db.dialect, reachable=alcancavel, target=self._banco_sem_segredo()), problemas

    def _banco_sem_segredo(self) -> str:
        """`postgres://host:porta/base` — o DSN sem usuário nem senha. A saúde é lida pelo painel e vai para
        relatório; o endereço ajuda a saber em que banco o processo está, a credencial não pode viajar junto."""
        if self.db.dialect != "postgres":
            return "sqlite"
        try:
            from urllib.parse import urlsplit

            partes = urlsplit(self.db.dsn)
            return f"postgres://{partes.hostname or '?'}:{partes.port or 5432}{partes.path}"
        except Exception:               # noqa: BLE001 - endereço é enfeite; nunca derruba a saúde
            return "postgres"

    def _problema_de_capacidade_local(self) -> Problem | None:
        """Quanto cabe AGORA (RAM livre) contra o alvo decidido (`max_online_devices`) — mesma conta do portão de
        boot real, para o aviso e a recusa nunca discordarem (achado #146, item 10.3)."""
        try:
            alvo = int(getattr(self.settings.get(), "max_online_devices", 0) or 0)
            if alvo <= 0:
                return None
            a = self.cfg.file.android
            est_mb = a.est_ram_host_mb()
            online = sum(1 for d in self.devices.devices.values() if d.state == InstanceState.online)
            # Arredondado ANTES de decidir: RAM livre "verdadeira" oscila alguns MB de uma leitura para a outra
            # só por causa de cache de página do SO, e o achado #65 já corrigiu `/health` para só emitir quando
            # o resultado muda de verdade — um número bruto aqui faria a mesma checagem "mudar" a cada 30 s sem
            # nada de fato ter mudado. 100 MB é grosso o bastante para nunca balançar sozinho.
            free_mb = round(psutil.virtual_memory().available / 2**20 / 100) * 100
            fit_more = max(0, int((free_mb - a.min_free_ram_mb_after_boot) // est_mb))
            estimated_max = online + fit_more
            if estimated_max >= alvo:
                return None
            return Problem(
                code="capacity_local",
                message=f"RAM livre agora só sustenta ≈{estimated_max} aparelho(s) local(is) simultâneo(s), "
                        f"abaixo do alvo configurado ({alvo}, `limits.max_online_devices`): ≈{free_mb:.0f} MB "
                        f"livres, ≈{est_mb} MB por instância, {a.min_free_ram_mb_after_boot} MB de folga exigida.",
                hint="Outro processo está usando a RAM do host (confira o WSL — `.wslconfig` — e outros "
                     "contêineres/VMs) ou o alvo local está otimista para esta máquina. O rodízio vai recusar "
                     "boot antes de estourar; isto só antecipa o aviso.")
        except Exception:  # noqa: BLE001 - aviso de capacidade nunca pode derrubar a saúde
            log.exception("cálculo de capacidade local")
            return None

    def _ia_em_fallback(self, janela_min: int = 30) -> list[dict[str, Any]]:
        """Chamadas recentes que o provedor principal da função não atendeu (`ai_calls.fallback`), por função."""
        desde = to_iso(now() - timedelta(minutes=janela_min))
        try:
            return self.db.query(
                "SELECT role, requested_model, fallback, count(*) AS n FROM ai_calls "
                "WHERE ts >= ? AND fallback IS NOT NULL AND fallback <> '' "
                "GROUP BY role, requested_model, fallback ORDER BY n DESC", (desde,))
        except Exception:  # noqa: BLE001 - a saúde nunca cai por causa de um relatório
            log.exception("não foi possível ler os fallbacks recentes de IA")
            return []

    def health(self) -> Health:
        problems: list[Problem] = []
        banco, problemas_do_banco = self._saude_do_banco()
        problems.extend(problemas_do_banco)
        sdk_ok = self.tools.found()
        if not sdk_ok:
            problems.append(Problem(code="sdk_missing", message=f"Android SDK não encontrado em {self.cfg.sdk_root}.",
                                    hint="Rode scripts/install-prereqs.ps1 ou ajuste android.sdk_root / ANDROID_SDK_ROOT."))
        # A conferência da imagem só olhava a PADRÃO. Uma imagem de override ausente (a da loja, com Play Store) só
        # aparecia como erro no primeiro boot daquele aparelho — nunca aqui, onde dá tempo de resolver antes.
        for iid, imagem in (self.cfg.override_images().items() if sdk_ok else ()):
            if not self.tools.system_image_dir(imagem).exists():
                problems.append(Problem(
                    code="system_image_missing",
                    message=f"A imagem de sistema de {iid} não está instalada: {imagem}.",
                    hint=f'Instale com: sdkmanager "{imagem}" (ou scripts/install-prereqs.ps1 -ImageTags …). '
                         "Os demais aparelhos seguem funcionando."))
        # Aparelho no ar e INÚTIL (Android morto por dentro, sessão que nunca abre) entra na saúde do sistema.
        # Antes, três dos quatro aparelhos remotos ligados estavam assim e `/api/health` só listava o Appium.
        degradados = [rt.id for rt in self.devices.devices.values()
                      if rt.state == InstanceState.error and rt.attention]
        if degradados:
            problems.append(Problem(
                code="devices_degraded",
                message=f"{len(degradados)} aparelho(s) respondem ao ADB mas não estão utilizáveis: "
                        + ", ".join(sorted(degradados)) + ".",
                hint="Veja o motivo no cartão de cada um (Infraestrutura). Reinicie o aparelho — de preferência a "
                     "frio — e confira se a sessão de automação abre."))
        # Item 10.3 (achado #146): o alvo local já está decidido e escrito (`limits.max_online_devices`) — o que
        # faltava era o central AVISAR quando a RAM livre agora não cobre esse alvo, em vez de deixar o rodízio
        # descobrir aos trancos (recusando boot por boot). Mesma conta do portão real de boot
        # (`devices/manager.py::_boot`): `est_instance_ram_mb` por instância e `min_free_ram_mb_after_boot` de
        # folga — para o número bater com o que de fato recusa ou aceita um boot, não uma estimativa à parte.
        problema_capacidade = self._problema_de_capacidade_local()
        if problema_capacidade is not None:
            problems.append(problema_capacidade)
        # ADR-055: conta travada logada em aparelho LIGADO. O android-04 passou horas no ar com o felipe no desafio
        # e a saúde não dizia nada; um aparelho assim é um risco à conta enquanto estiver de pé.
        if (travadas := self._contas_travadas_no_ar()):
            problems.append(Problem(
                code="locked_account_on_device",
                message=f"{len(travadas)} aparelho(s) ligado(s) com conta travada logada: " + ", ".join(travadas)
                        + ".",
                hint="O aparelho está em quarentena: nada o toca além de parar ou hibernar. O desafio é com a pessoa "
                     "(ADR-009); decida o destino do aparelho — reset ou religar só com a confirmação explícita "
                     "(confirm_locked_account)."))
        # Achado #179: o túnel SSH é o único transporte do ADB remoto e do canal do agente. Sem este problema
        # dedicado, a queda dele só aparecia como sintomas espalhados (aparelhos "sem ADB", worker "sem batida"),
        # sem nada apontando a causa comum.
        tuneis_fora = [w for w in self.workers.dtos() if w.transport_state == "down"]
        if tuneis_fora:
            problems.append(Problem(
                code="tunnel_down",
                message=(f"{len(tuneis_fora)} túnel(is) fora: " + ", ".join(w.name for w in tuneis_fora) + "."),
                hint="A porta LOCAL do túnel está recusando conexão — o worker remoto pode estar de pé; é o "
                     "transporte que caiu. Veja data/logs/tunel-*.log; a tarefa agendada "
                     "farm-tunel-<worker> (scripts/worker-tunnel.ps1) reconecta sozinha."))
        # Medido: `config/config.yaml` recriado do exemplo trouxe `worker_port: 0` e o backend subiu com o canal do
        # worker atendendo na porta principal — onde um `-R` do túnel expõe a API inteira à máquina do worker. O
        # padrão `0` é o certo para parque numa máquina só; com worker REMOTO inscrito ele vira problema de saúde.
        remotos = [w for w in self.workers.dtos() if not w.local]
        if remotos and not int(self.cfg.file.server.worker_port or 0):
            problems.append(Problem(
                code="worker_channel_shared",
                message=(f"{len(remotos)} worker(s) remoto(s) inscrito(s) e o listener dedicado do canal do worker "
                         "está desligado (server.worker_port: 0)."),
                hint="Ligue server.worker_port (ex.: 8010) em config/config.yaml e reinicie; aponte o -R do túnel "
                     "para ela. Com o canal na porta principal, o túnel deixa a API REST ao alcance do worker."))
        appium_up = self.appium.is_up(timeout=1.0)
        if not appium_up:
            problems.append(Problem(code="appium_down", message=self.appium.detail or "Servidor Appium não está respondendo.",
                                    hint="Verifique tools/appium (npm ci) e data/logs/appium.log; o controle manual segue funcionando."))
        elif not self.appium.log_masking_active:
            # Sem mascaramento comprovado, o Appium gravaria em claro tudo o que for digitado — inclusive senha.
            problems.append(Problem(code="appium_log_masking_off",
                                    message="Mascaramento de log do Appium não comprovado nesta sessão.",
                                    hint="Reinicie pelo scripts/stop.ps1 + start.ps1 para o backend subir o Appium com as "
                                         "regras de mascaramento. Preenchimento de credencial fica bloqueado até lá."))
        if self._clock_skew_s > self.cfg.max_clock_skew_s:
            problems.append(Problem(
                code="clock_skew",
                message=f"O relógio desta máquina está {self._clock_skew_s:.1f}s longe do relógio do banco.",
                hint="A posse de etapa entre backends depende deste relógio: um backend adiantado adota etapa em "
                     "plena execução de outro. Sincronize por NTP. Com outro backend hospedando aparelhos neste "
                     "banco, este processo teria recusado subir."))
        ai = self.ai_status()
        if not ai.configured:
            problems.append(Problem(code="ai_not_configured", message="Provedor de IA sem chave.",
                                    hint="Defina ANTHROPIC_API_KEY no .env e reinicie o backend. Gerenciamento e controle manual continuam disponíveis."))
        # Teto de gasto em US$ (item 7.2): o aviso sai em 80 % e o bloqueio em 100 %, com o número na frente —
        # até aqui o custo só existia num relatório que ninguém abre antes de a conta zerar.
        limite_dia = float(getattr(self.settings.get(), "ai_max_usd_per_day", 0) or 0)
        if limite_dia > 0 and ai.spend_today_usd is not None:
            gasto = ai.spend_today_usd
            if gasto >= limite_dia:
                problems.append(Problem(
                    code="ai_budget_day", message=f"Teto de gasto de IA do dia atingido: "
                                                  f"US$ {gasto:.2f} de US$ {limite_dia:.2f}.",
                    hint="Nenhuma chamada nova de IA será feita hoje. Aumente ai_max_usd_per_day em "
                         "Configuração › Limites para liberar."))
            elif gasto >= limite_dia * 0.8:
                problems.append(Problem(
                    code="ai_budget_day_warning",
                    message=f"Gasto de IA do dia em US$ {gasto:.2f} de US$ {limite_dia:.2f} "
                            f"({gasto / limite_dia:.0%} do teto).",
                    hint="Em 100 % as chamadas de IA passam a ser recusadas até o dia virar (UTC) ou o teto subir."))
        breaker = self.scheduler.executor.ai_breaker
        if breaker is not None:
            code = {"billing": "ai_billing", "balance": "ai_balance_blocked"}.get(breaker.kind, "ai_auth_failed")
            problems.append(Problem(code=code, message=f"{breaker.message} (execução {breaker.run_id}, {breaker.at}).",
                                    hint="Disjuntor de conta de IA acionado: a execução foi pausada automaticamente e "
                                         "nenhuma tentativa foi gasta. Corrija e retome a execução para soltar."))
        problems.extend(self._problemas_de_saldo())
        # Backlog B15 (bateria de 25/09): o Ollama estava fora do ar, as 89 decisões foram para o fallback — e a saúde
        # dizia `ok`. O fallback continua sendo o comportamento certo; o que faltava era ele aparecer.
        for linha in self._ia_em_fallback():
            problems.append(Problem(
                code="ai_fallback_em_uso",
                message=(f"IA em fallback: {linha['n']} chamada(s) de '{linha['role']}' nos últimos 30 min não foram "
                         f"atendidas por {linha['requested_model']} e caíram em {linha['fallback']}."),
                hint="O provedor principal dessa função não respondeu (modelo local fora do ar, por exemplo: o Ollama "
                     "sobe no login do usuário, não no boot). A execução segue pelo fallback declarado, com o custo e "
                     "a qualidade dele. Suba o provedor principal ou declare o fallback como principal em ai.roles."))
        vault = self.secrets.status()
        if vault == "locked":
            problems.append(Problem(code="secret_store_locked",
                                    message="O cofre de credenciais está travado nesta máquina/usuário.",
                                    hint="As credenciais cifradas foram preservadas. Recadastre a senha de cada perfil "
                                         "pelo portal para voltar a usar autenticação automática."))
        elif vault == "unavailable":
            problems.append(Problem(code="secret_store_unavailable",
                                    message="Sem chave mestra para proteger credenciais.",
                                    hint="Defina CREDENTIALS_MASTER_KEY no .env (o nome antigo, "
                                         "INSTAGRAM_CREDENTIALS_MASTER_KEY, continua valendo). Gerenciamento e "
                                         "controle manual seguem funcionando."))
        # Achado #126: dizer `ready` era metade da verdade. O cofre abre — mas abre com a chave DESTE backend, e o
        # que está guardado no banco pode ter sido cifrado por outro. Sem este problema, o sintoma era login
        # automático falhando de forma intermitente, sem nada na saúde apontando a causa.
        elif (estranhas := self.secrets.chaves_estranhas()):
            problems.append(Problem(
                code="secret_store_foreign_key",
                message=("Há credenciais no banco cifradas com outra chave mestra: "
                         + ", ".join(estranhas) + f" (a deste backend é {self.secrets.provider.key_id})."),
                hint="Outro backend gravou credencial neste banco com a chave mestra dele — este aqui não abre "
                     "essas senhas, e recadastrá-las por aqui faria o outro parar de abrir. Use a MESMA "
                     "CREDENTIALS_MASTER_KEY nos dois backends, ou rode `python -m app.security.rekey` para "
                     "recifrar o cofre inteiro para uma chave só."))
        if ai.simulated:
            problems.append(Problem(code="ai_simulated", message="MODO SIMULADO ativo: nenhuma IA é consultada.",
                                    hint="Use AI_PROVIDER=anthropic no .env para o provedor real."))
        diag = self._diag_cache
        accel = diag["acceleration"]["detail"] if diag else None
        if diag and not diag["acceleration"]["usable"]:
            problems.append(Problem(code="no_acceleration", message="Aceleração de virtualização indisponível.",
                                    hint="No Windows, habilite 'Windows Hypervisor Platform' (WHPX) e reinicie."))
        # `database_down` é duro: sem banco não há fila, nem posse de etapa, nem histórico — nada do que este
        # processo faz sobrevive, e chamar isso de "degradado" seria o mesmo engano do achado #33.
        hard = {"sdk_missing", "no_acceleration", "database_down"}
        status = "error" if any(p.code in hard for p in problems) else ("degraded" if problems else "ok")
        emu_version = next((t["version"] for t in (diag or {}).get("tools", []) if t["name"] == "Android Emulator"), None)
        return Health(status=status, version=VERSION, commit=commit_em_execucao(self.cfg.root),
                      migration=self.ultima_migracao(), database=banco, ai=ai,
                      appium=AppiumStatus(running=appium_up, port=self.cfg.file.appium.port, detail=self.appium.detail),
                      sdk=SdkStatus(found=sdk_ok, root=str(self.cfg.sdk_root), emulator_version=emu_version, accel=accel),
                      problems=problems,
                      features={"hibernation": self.cfg.file.android.hibernation, "recipes": self.cfg.file.ai.recipes,
                                "flows": self.cfg.file.ai.flows, "image_policy": self.cfg.file.ai.image_policy,
                                "system_image": self.cfg.file.android.system_image,
                                # Fase F: o painel só oferece o ensino v2 e a lista de habilidades com isto ligado.
                                "skills": self.cfg.file.skills.enabled})

    def ultima_migracao(self) -> str | None:
        """A migração mais recente aplicada NESTE banco. Lido a cada chamada: é uma linha e responde "o esquema que
        este processo está usando é o que o código espera?" — a pergunta do deploy, e a única prova de que a
        subida migrou de verdade."""
        try:
            return self.db.scalar("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
        except Exception:  # noqa: BLE001 - saúde nunca falha por causa de um enfeite dela
            return None

    async def diagnostics(self, refresh: bool = False) -> dict[str, Any]:
        from .devices import diagnostics

        if refresh or self._diag_cache is None:
            self._diag_cache = await asyncio.to_thread(diagnostics.collect, self.cfg, self.tools, self.db)
        return self._diag_cache
