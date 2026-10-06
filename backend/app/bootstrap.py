"""A montagem do `AppState` (15.15 K, F5c): `montar` é o corpo do antigo `AppState.__init__`, e as `montar_*` são os blocos que só dependem de `cfg`, do banco e de peças já montadas. O `AppState` declara os atributos no nível da classe (mypy e quem lê `state.<atributo>` os enxergam como sempre) e o `__init__` só chama `montar`, na MESMA ordem de construção de antes: nenhuma construção mudou de lugar na sequência, nem argumento, nem regra.

Este módulo não importa `app.state` em tempo de execução (ciclo): `AppState` só aparece em `TYPE_CHECKING`. `SettingsStore` e o ligador do espelho do Trello moram aqui porque só a montagem os constrói, e `state.py` os importava só para isso.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import asyncio
import time
from collections.abc import Callable
from typing import Callable

from . import marca_de_partida
from .automation.appium_server import AppiumServer
from .automation.driver import DeviceIO
from .commands.limpeza_ao_retirar import LimpezaAoRetirar
from .commands.outbox import CommandOutbox
from .commands.store import CommandStore
from .commands.transport import build_transport
from .config import Config, LimitsCfg
from .convergencia import Convergencia
from .db import Database, dumps, loads
from .decisoes_inversas import inversas_das_filas
from .devices.captura_pontual import capturar_para_o_dono
from .devices.installer import AppInstaller
from .devices.manager import DeviceManager, DeviceRuntime
from .devices.rede_convergencia import ConvergenciaDeRede
from .devices.rede_saida_central import SaidaDoCentral
from .devices.rede_servidor import ServidorDeRede
from .devices.sdk import SdkTools
from .events import EventBus
from .gates import Portoes
from .modules.applications.infrastructure.app_repository import AppRepository
from .modules.avisos.infrastructure.anexos import ArmazemDeAnexos
from .modules.avisos.infrastructure.anexos_leitura import LeitorDeAnexo
from .modules.avisos.infrastructure.canais_frota import CanaisDaFrota
from .modules.avisos.infrastructure.contatos_sql import ContatosDoCanal
from .modules.avisos.infrastructure.convidados import ConvidadosDoTelegram
from .modules.avisos.infrastructure.entrada import parece_codigo, ServicoDeEntrada
from .modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from .modules.avisos.infrastructure.espelho import EspelhoDoTrello, FontesDaCentral
from .modules.avisos.infrastructure.espelho_sql import CartoesDoTrello, CursorDoTrello
from .modules.avisos.infrastructure.faxina_sql import FaxinaDosCanais
from .modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from .modules.avisos.infrastructure.portas_da_central import nomes_e_dados_da_persona, PortasReais
from .modules.avisos.infrastructure.servico import ServicoDeAvisos
from .modules.avisos.infrastructure.trello_leitor import ComentariosDoTrello, LeitorDoTrello
from .modules.avisos.infrastructure.trello_webhook import CadastroDoWebhook, PortaDoWebhook
from .modules.decisoes.application.desfazer import DesfazerDecisoes
from .modules.decisoes.infrastructure.adaptador_sql import AdaptadorDeDecisoes
from .modules.decisoes.infrastructure.estado_sql import EstadoDasDecisoes
from .modules.decisoes.infrastructure.registro_sql import RegistroSql as RegistroDeDecisoes
from .modules.decisoes.infrastructure.resumo_sql import ResumoDasDecisoes
from .modules.decisoes.infrastructure.servico import ServicoDeDecisoes
from .modules.identity.application.sessions import SessionProviders
from .modules.identity.infrastructure.persona_images import compor_servico_de_imagens, imagens_dto
from .modules.identity.infrastructure.sessions import SessionDeps, SessionProviderFactory
from .modules.learning import esquecer_conta
from .modules.learning.application.falhas import ServicoDeFalhas
from .modules.learning.infrastructure import ligar_intencao, ligar_validacao, ligar_voz
from .modules.learning.infrastructure.curador_do_hub import CuradorDoHub
from .modules.learning.infrastructure.ligar_costuras import costuras_do_livro
from .modules.learning.infrastructure.montagem import montar_aprendizado
from .modules.learning.infrastructure.segredo import TriagemDeCredencial
from .modules.learning.infrastructure.validacoes_sql import RegistroDeValidacoesSql
from .modules.pedidos.infrastructure.laco import LacoDePedidos
from .modules.pedidos.infrastructure.saldo import motivo_de_adiamento
from .modules.pedidos.infrastructure.servico import PedidosApi
from .modules.portal.montagem import Portal
from .modules.skills.application.registry import CompositeSkillRegistry
from .modules.skills.application.teaching import TeachingService
from .modules.skills.infrastructure.document_validator import DslDocumentValidator, LockedVersions
from .modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from .modules.skills.infrastructure.run_planning import SkillRunPlanner
from .modules.skills.infrastructure.secret_screen import RedactionSecretScreen
from .modules.skills.infrastructure.sql_repository import SqlSkillRepository
from .modules.skills.infrastructure.sql_teaching_repository import SqlTeachingRepository
from .planning import costs
from .planning.anthropic_provider import AnthropicProvider
from .planning.catalog import capabilities_of, pacote_ancora, session_factory_of
from .planning.decisao_fechada import construir_porta, observador_de_sombra, RepositorioDeSombra
from .planning.decisao_fechada.curador import CuradorComTriagemEmSombra, TriagemDoCurador
from .planning.decisao_fechada.intencao import ConsumidorDeIntencao
from .planning.provider import AIProvider, build_provider
from .porta_do_plano import aprovar_pelo_canal, AprovarPlanoBody, ItemAprovado, previa_da_porta, previa_para_o_canal
from .releases.inspector import ApkInspector
from .releases.repository import ReleaseRepository
from .releases.service import ReleaseService
from .saude import SaudeDoSistema
from .security import local_secret
from .security.secret_store import build_key_provider, SecretStore
from .security.sensitive_input import SensitiveInputChannel
from .security.sessions import PanelSessions, PortaoDeLogin
from .social.approvals import ApprovalService, ApprovalStore
from .social.persona_batch import LotesDePersona
from .social.policy import PolicyEngine
from .social.repository import SocialRepository
from .social.service import SocialService
from .storage import build_storage, DISK, DiskStorage, Storage
from .taskqueue.flows import preencher_refs_publicas
from .taskqueue.repository import Repository
from .taskqueue.scheduler import Scheduler
from .taskqueue.service import RunService
from .taskqueue.sombra_intencao import catalogo_de, SombraDaIntencao
from .taskqueue.travas import Lideranca
from .training.generalizer import ProviderSkillGeneralizer
from .util import now
from .vitrine import _apps_changed
from .workers.local import LocalWorker
from .workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from .state import AppState

# O tipo de texto que cada capability escreve e as leituras de conversa (que viram fala de outra pessoa) são do
# APP: moram no manifesto dele (`AppDefinition.text_kinds`/`conversation_reads`, lidos do `app.yaml` do pacote,
# ADR-052). Quem pergunta é `_draft_gate` e `_registrar_leitura`.
_RELER_LIMITES_S = 2.0


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

    def update(self, patch: dict[str, object]) -> LimitsCfg:
        self._value = LimitsCfg.model_validate({**self._value.model_dump(), **patch})
        self.db.execute("INSERT INTO settings(key, value) VALUES ('limits', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (dumps(self._value.model_dump()),))
        self._lido_em = time.monotonic()      # acabei de escrever: o que tenho em mão é o mais novo que existe
        return self._value


class _FontesDoEspelhoDoTrello:
    """Liga o espelho do Trello (32.2) às portas PÚBLICAS dos módulos donos: `PedidosApi.listar` (o mesmo serviço da rota
    `GET /api/pedidos`) e `RegistroDeValidacoesSql.vivos()` (o registro do Livro, 082). Só ids e estados saem daqui; o
    `avisos` não importa `pedidos` nem `learning`."""

    ESTADOS_DO_PEDIDO = ["ativo", "pausado", "aguardando_pessoa"]

    def __init__(self, pedidos: Callable[[], PedidosApi], validacoes: RegistroDeValidacoesSql):
        self._pedidos = pedidos
        self._validacoes = validacoes

    def pedidos_abertos(self) -> list[tuple[str, str]]:
        abertos: list[tuple[str, str]] = []
        cursor: str | None = None
        while True:
            pagina = self._pedidos().listar(estado=self.ESTADOS_DO_PEDIDO, autonomia=None, tipo=None, profile_id=None,
                                            q=None, pede_atencao=False, ordem="criado", limit=200, cursor=cursor)
            itens = pagina.get("items")
            if isinstance(itens, list):
                abertos += [(str(i["id"]), str(i["estado"])) for i in itens if isinstance(i, dict)]
            proximo = pagina.get("proximo_cursor")
            if not isinstance(proximo, str) or not proximo:
                return abertos
            cursor = proximo

    def livro_em_validacao(self) -> list[tuple[str, str, str]]:
        return [(v.id, v.item_ref, v.estado.value) for v in self._validacoes.vivos()]


def montar_armazenamento(cfg: Config) -> tuple[Storage, Storage]:
    """O storage de evidências (item 5.7: disco local por omissão, S3-compatível por bandeira; a chave gravada em `evidence.path` é chave de
    storage e é a mesma nas duas pontas) e o dos avatares de perfil, sob `avatars/<id>.jpg`. Em disco a raiz dos avatares é `data/`, então o
    arquivo continua onde sempre esteve; fora do disco, eles vão para o mesmo storage (no disco de uma réplica responderiam 404 na outra)."""
    storage = build_storage(
        cfg.env.evidence_storage, evidence_dir=cfg.evidence_dir, bucket=cfg.env.s3_bucket,
        endpoint_url=cfg.env.s3_endpoint_url, region=cfg.env.s3_region,
        access_key=cfg.env.s3_access_key_id.get_secret_value() if cfg.env.s3_access_key_id else None,
        secret_key=cfg.env.s3_secret_access_key.get_secret_value() if cfg.env.s3_secret_access_key else None)
    avatares = DiskStorage(cfg.data_dir) if storage.name == DISK else storage
    return storage, avatares


def montar_lideranca_e_canais(cfg: Config, db: Database) -> tuple[Lideranca, CanaisDaFrota, ArmazemDeAnexos]:
    """A trava de líder dos laços de fundo (28.1: com dois backends com scheduler no mesmo banco, só um roda saldos, curadoria e retenção), os
    canais que este backend liga (28.37: a saúde acusa quando o líder da trava `avisos` não liga um deles) e os anexos dos canais (28.24: o
    arquivo em `data/anexos/`, fora do Git, pelo sha256; a faxina do 28.16 os apaga)."""
    lideranca = Lideranca(db, dono=cfg.owner_id)
    canais_da_frota = CanaisDaFrota(db, cfg, dono=cfg.owner_id, roda=cfg.roda_scheduler)
    anexos_canal = ArmazemDeAnexos(db, cfg.data_dir / "anexos")
    return lideranca, canais_da_frota, anexos_canal


def montar_decisoes(cfg: Config, db: Database, avisos: ServicoDeAvisos,
                    lider: Callable[[str], int | None]) -> tuple[RegistroDeDecisoes, ServicoDeDecisoes]:
    """O que a plataforma decide sozinha (28.25): o registro único, o adaptador que recolhe os produtores e o resumo agrupado (no máximo uma
    mensagem por janela) pelo mesmo caminho dos avisos. O desfazer entra pelas rotas."""
    registro = RegistroDeDecisoes(db)
    estado = EstadoDasDecisoes(db)
    servico = ServicoDeDecisoes(
        cfg, AdaptadorDeDecisoes(db, registro, estado, redigir=TriagemDeCredencial().redigir),
        ResumoDasDecisoes(db, estado, enfileirar=avisos.enfileirar_aviso,
                          pode_avisar=lambda: avisos.ligado and avisos.canal() is not None,
                          redigir=TriagemDeCredencial().redigir),
        lider=lider)
    return registro, servico


def montar(self: AppState, cfg: Config, *, provider: AIProvider | None, io_factory: Callable[[DeviceRuntime], DeviceIO] | None, manage_appium: bool, emulator: object) -> None:
    """O corpo do antigo `AppState.__init__`: constrói as peças, na ordem, e as pendura em `self`."""
    self.cfg = cfg
    cfg.ensure_dirs()
    # Regravado a cada subida, de propósito: um segredo que vazou deixa de servir no próximo restart, e quem
    # precisa dele (`scripts/stop.ps1`) lê o arquivo na hora de usar. Ver `security/local_secret.py`.
    local_secret.garantir(cfg.data_dir)
    self.db = Database(cfg.db_dsn)
    # 29.131: a marca da partida (29.124) anda a cada migração aplicada e entre os passos longos daqui; sem o id
    # do supervisor, `gravar` não faz nada.
    pasta_da_marca = marca_de_partida.pasta_do_supervisor(cfg.data_dir)
    self.db.migrate(ao_aplicar=lambda _v: marca_de_partida.gravar(pasta_da_marca, marca_de_partida.MIGRANDO))
    marca_de_partida.gravar(pasta_da_marca, marca_de_partida.MIGRANDO)
    # 30.83: a referência pública aleatória dos fluxos de antes da migração 116 (o SQL portátil não sorteia).
    preencher_refs_publicas(self.db)
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
    marca_de_partida.gravar(pasta_da_marca, marca_de_partida.APARELHOS)
    self.provider = provider or build_provider(cfg)
    # Storage de evidências e avatares (item 5.7): `bootstrap.montar_armazenamento`.
    self.storage, self.avatares = montar_armazenamento(cfg)
    self.repo = Repository(self.db, self.bus, cfg.evidence_dir, owner_id=cfg.owner_id, storage=self.storage)
    # Comando do painel como entidade: sem isto a ação era um 202 sem registro, e a interface chamava de
    # sucesso o que só tinha sido aceito.
    # `owner_id`: a reconciliação de partida mexe só nos comandos de aparelho que ESTE backend hospeda.
    # Outbox: a entrega DEVIDA gravada na mesma transação que aceita o comando (item 5.6). Sem ela, uma
    # queda entre gravar `dispatched` e agendar a tarefa perdia o comando para sempre.
    self.outbox = CommandOutbox(self.db, owner_id=cfg.owner_id, transport=cfg.env.command_transport)
    # Trava de líder dos laços de fundo (28.1), canais da frota (28.37) e anexos dos canais (28.24): `bootstrap.montar_lideranca_e_canais`.
    self.lideranca, self.canais_da_frota, self.anexos_canal = montar_lideranca_e_canais(cfg, self.db)
    # Aviso fora do painel (28.11): espelho da caixa de Pendências no Telegram. Desligado de fábrica.
    self.avisos = ServicoDeAvisos(cfg, self.bus, FilaDeAvisos(self.db), self.lideranca, lider=self._lider,
                                  redigir=TriagemDeCredencial().redigir,
                                  faxina_canais=FaxinaDosCanais(self.db, pasta_anexos=self.anexos_canal.pasta),
                                  nomes_de_persona=lambda: nomes_e_dados_da_persona(self.db))
    # O contato do site institucional (29.77, ADR-075): grava antes de avisar e entrega pela Canais. Desligado de fábrica.
    self.portal = Portal(cfg, self.db, self.avisos)
    # O que a plataforma decide sozinha (28.25): `bootstrap.montar_decisoes`.
    self.decisoes_registro, self.decisoes = montar_decisoes(cfg, self.db, self.avisos, self._lider)
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
    # As vagas deste servidor são a configuração viva: o rodízio obedece no mesmo tick, e o painel lê o mesmo
    # número (29.82).
    self.workers.vagas_do_host = lambda: int(self.settings.get().max_online_devices or 1)
    # Comando remoto (29.154): os números vêm do config (valem na subida) e a auditoria é o barramento de eventos.
    cr = cfg.file.comando_remoto
    self.workers.comandos.cfg.ativo = bool(cr.ativo)
    self.workers.comandos.cfg.fila_max = cr.fila_max
    self.workers.comandos.cfg.por_minuto_por_operador = cr.por_minuto_por_operador
    self.workers.comandos.cfg.retencao_dias = cr.retencao_dias
    self.workers.comandos.cfg.max_por_worker = cr.max_por_worker
    self.workers.comandos.emitir = self.bus.emit
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
    self.social_repo.variaveis_da_persona = self.repo.variaveis_da_persona     # 31.113 F3
    # Validade do "Conectado": o repositório monta o DTO do perfil e é ele que marca a sessão como dado velho.
    self.social_repo.session_max_age_s = cfg.file.contas.session_max_age_s
    # Teto do `unknown_streak` na GRAVAÇÃO (o mesmo que a porta de sessão lê): nenhuma releitura soma acima dele.
    self.social_repo.teto_de_reobservacao = lambda: self.settings.get().session_unknown_retry_cap
    #: (perfil, aparelho, conta) → quando começou a última releitura que a porta de sessão pediu para um teto velho
    #: de `unknown_streak`. É a trava de UMA releitura por janela (entrada no ar do aparelho, validade): a releitura
    #: que quebra não grava nada, e sem a trava o tick seguinte a reagendaria para sempre (achado #104).
    self._releituras_do_teto = {}
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
    self.sessoes = SessionProviders(
        session_factory_of, lambda fabrica: fabrica(dependencias))
    # O hub de IA (item 7.1) é construído antes do banco existir — é ele que decide quem atende cada função.
    # O repositório e os limites chegam aqui: é com eles que o teto em US$ é conferido e que a troca de
    # provedor vira linha da execução em vez de só um modelo diferente numa linha de custo.
    if hasattr(self.provider, "attach"):
        self.provider.attach(repo=self.repo, settings_getter=self.settings.get)
    self.scheduler = Scheduler(cfg, self.repo, self.devices, self.provider, self.settings.get)
    self.scheduler.session_gate = self._session_gate
    # Conta bloqueada que sai (29.23): a persona volta a `active`, então o agendador nunca VÊ o `blocked` que dispara
    # o disjuntor de conta (ADR-055); a retirada o aciona direto, na hora.
    self.social.sinal_de_desafio = lambda iid: self.devices.tem_atividade_de_desafio(iid)
    self.social.ao_retirar_conta = (
        lambda pid, _conta, estava: self.scheduler.disjuntor_de_conta(pid) if estava else None)
    # O rastro textual da conta no Livro (o @ e o id em texto) sai na MESMA transação da retirada (contrato combinado
    # com o Aprendizado, 29.23): uma falha ali desfaz a retirada inteira, nada pela metade.
    self.social.limpezas_ao_retirar.append(esquecer_conta)
    # 29.27 (emenda do ADR-068): conta retirada de app que declara `limpar_ao_retirar` leva os dados do app embora dos
    # aparelhos onde estava logada (`pm clear` só desse pacote), numa tarefa de fundo; a quarentena resolve ao fim.
    self.limpeza_ao_retirar = LimpezaAoRetirar(self)
    self.social.ao_limpar_aparelhos = self.limpeza_ao_retirar.agendar
    # A porta do app passa a se resolver sozinha quando há versão distribuída por instalar naquele aparelho.
    self.scheduler.app_resolver = self._app_resolver
    # A mesma verdade sobre o app, só que SEM efeito e ANTES de planejar: é o pedaço do pré-voo que conhece
    # release e estado do aplicativo, que o serviço de execução não conhece.
    self.scheduler.app_preflight = self._app_preflight
    # Manutenção suspende novas atribuições: o scheduler pergunta ao registro antes de tirar um objetivo do lugar.
    self.scheduler.worker_gate = self.workers.aceita_trabalho
    # Rede por aparelho (ADR-056, item 25.4): o servidor sing-box do central (processo do usuário, gerenciado
    # aqui) e a convergência que aplica, confere e desfaz a rede de cada aparelho num ponto seguro. A porta da
    # rede (contrato C4) é dela: com política exigida, a tarefa espera e o que falta é disparado antes.
    self.rede_servidor = ServidorDeRede(
        self.db, self.secrets, lambda: self.cfg.file.rede.servidor, pasta=self.cfg.data_dir / "rede" / "servidor",
        binario=lambda: self.cfg.path(self.cfg.file.rede.servidor.binario))
    # Aparelho de outra máquina (worker da LAN, celular): chega ao servidor pela LAN e depende do firewall (25.7).
    self.rede_servidor.eh_remoto = lambda iid: bool(getattr(self.devices.devices.get(iid), "external", False))
    self.rede_convergencia = ConvergenciaDeRede(self)
    # A saída do PRÓPRIO central (item 29.20): a referência para acusar o aparelho que sai pela rede da casa.
    self.rede_saida_central = SaidaDoCentral(lambda: self.cfg.file.rede.sonda)
    self.scheduler.rede_gate = self.rede_convergencia.motivo_de_espera
    # A queda do túnel no meio de um objetivo (item 25.6): relida de dentro do worker, entre as etapas.
    self.scheduler.rede_releitura = self.rede_convergencia.reler_entre_etapas
    # Vagas e recursos POR MÁQUINA entram na decisão do rodízio: o teto deixa de ser um número global.
    self.scheduler.worker_capacity = self._worker_capacity
    # "Instalar em todos agora": releases cuja entrega uma pessoa pediu para JÁ. Em memória de propósito — um
    # reinício no meio não perde nada (a versão desejada está no banco); só a pressa: volta-se ao modo padrão.
    self._entrega_imediata = set()
    # Uma escrita por vez DENTRO de cada execução. A lista de "não repita" é lida do que os irmãos já
    # escreveram: com os oito aparelhos gerando ao mesmo tempo, todos leem a lista vazia e voltam com a mesma
    # frase — exatamente o defeito que esta série existe para consertar. Execuções diferentes seguem juntas.
    self._draft_locks = {}
    self.convergencia = Convergencia(self)  # 15.15 F5b: a entrega de apps ao parque mora em `convergencia.py`; os métodos de baixo delegam
    self.portoes = Portoes(self)         # 15.15 F5a: os portões do despacho moram em `gates.py`; os métodos de baixo delegam
    self.scheduler.rollout_source = self._rollout_pending
    self.policies = PolicyEngine(self.social_repo, self.settings.get)
    self.excecoes = self.social.excecoes          # 30.65: a porta prende; o `open_effect` gasta
    self.approvals = ApprovalStore(self.db)
    self.approval_service = ApprovalService(self.approvals, self.repo, self.scheduler, excecoes=self.excecoes)
    # O executor grava no histórico do perfil o efeito que dispara — é o que alimenta limites e memória.
    self.scheduler.executor.social = self.social
    self.scheduler.executor.approvals = self.approvals
    # Achado #109: aprovação pendente de uma etapa não sobrevive ao objetivo cancelado/abandonado.
    self.scheduler.expirar_aprovacoes_do_objetivo = self.approvals.expire_for_objective
    # Login/desafio visto NO MEIO da execução corrige o estado do perfil. Sem isto o painel seguia dizendo
    # "Conectado" para uma conta presa num desafio, e o login automático nunca disparava.
    self.scheduler.executor.on_auth_needed = self._sessao_desmentida
    self.scheduler.policy_gate = self._policy_gate
    # O lock de escrita é por execução: some junto com ela, senão o dicionário cresceria para sempre. E o digest do
    # aprendizado (ADR-054) é encadeado aqui, numa thread: o fim da execução nunca espera nem cai por causa dele.
    self.scheduler.on_run_settled = self._execucao_assentada
    # 29.93: a parada esperando a pessoa solta o mesmo que a main soltava ao parar, sem o digest; e a execução que
    # fecha sem worker (saída dessa espera, cancelamento órfão do 29.103) não tem quem chame o `_settle_run`: o
    # repositório a assenta com o MESMO gancho, se ganhar a marca `assentada_em` (#382, migração 113).
    self.scheduler.on_run_parada = self._execucao_parada
    self.repo.ao_assentar_sem_worker = self._execucao_assentada
    self.scheduler.on_items_collected = self._registrar_leitura
    # Wipe, perda do aparelho ou qualquer coisa que mexa no disco invalida a sessão observada.
    self.devices.on_session_invalidated = self._invalidate_sessions
    # Devolver o controle manual, num aparelho cujo perfil esperava uma pessoa, dispara a reobservação —
    # é o que o texto de desafio do app (`sessao.yaml`) promete e, sem isto, o código não fazia (achado #106).
    self.devices.on_control_released = self._controle_devolvido
    # 29.143: a tomada explícita encerra a gravação de quem ensinava (sem salvar nem descartar) antes do lease novo.
    self.devices.on_lease_taken = self._controle_tomado
    # Modo treinamento (item 13.1): cada entrada manual do Foco, com a tela de antes, vai para a gravação.
    from .training.recorder import TrainingRecorder  # noqa: PLC0415
    # A persona do treino é a que a pessoa escolheu; sem escolha, a ÚNICA do aparelho para o app (com duas, o
    # gravador recusa: não adivinha de quem é a demonstração).
    self.training = TrainingRecorder(
        self.db, self.bus, self.devices,
        lambda iid, app: [str(v["profile_id"]) for v in self.social_repo.profiles_of_instance(iid, app)],
        owner_id=cfg.owner_id, variaveis_da_persona=self.repo.variaveis_da_persona)
    self.devices.on_training_input = self.training.record
    from .training.skills import TrainingSkills  # noqa: PLC0415
    self.skills = TrainingSkills(self)
    # Apagar os dados do aparelho apaga também o app: sem isto o central seguia dizendo "pronto" para um
    # aparelho vazio, a porta do app deixava passar e "Distribuir" recusava reinstalar.
    self.devices.on_device_wiped = self._dados_do_aparelho_perdidos
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
    # Aprendizado contínuo (ADR-054): o livro de aprendizado, o D1 e a régua durável. Nenhuma IA no pipeline: digest
    # quando a execução assenta, curadoria a cada `aprendizado.curadoria_s`, retenção junto da do resto.
    # As lojas do scheduler ganham o D1 (fluxo nasce candidato; receita com efeito para em `validated`) e a trilha.
    # Porta `DecisaoFechada` (Fase 31, ADR-069): desligada por padrão e com decisor NULO de fábrica. Nasce antes do
    # aprendizado porque a triagem do curador em sombra (31.8) embrulha o curador do hub. A sombra grava só ids e
    # categorias (migração 074); a retenção dela corre junto da do resto (`_purgar_demais_tabelas`).
    self.decisao_sombra = RepositorioDeSombra(self.db)
    self.decisao_fechada = construir_porta(cfg.file.ai.decisao_fechada, observador=observador_de_sombra(self.decisao_sombra),
                                           decisor=self._decisor_da_porta(cfg))
    # 30.12: o curador por IA passa pelo hub (papel `plan`, origem `curador`, fatia do 31.6); `off` de fábrica.
    # 31.8: a triagem do Jev observa cada parecer em sombra (consumidor `curador`, inerte de fábrica) e devolve o
    # parecer do curador intacto: nada do Jev volta ao Livro.
    self._curador_do_hub = CuradorDoHub(self.provider, self.db,
                                        registrar_uso=lambda u: self.repo.add_usage(None, None, u),
                                        precos=lambda: self.cfg.file.ai.prices)
    self._triagem_do_curador = TriagemDoCurador(self.decisao_fechada)
    self.learning = montar_aprendizado(
        self.db, config=lambda: self.cfg.file.aprendizado,
        retencao_de_logs_dias=lambda: int(self.settings.get().log_retention_days),
        precos=lambda: self.cfg.file.ai.prices, habilidades=self.skill_repo, fluxos=self.scheduler.flows,
        receitas=self.scheduler.executor.recipes,
        decidir=lambda texto, run_id: self.repo.decision(texto, run_id=run_id), eventos=self.bus,
        curador_de_ia=CuradorComTriagemEmSombra(self._curador_do_hub, self._triagem_do_curador))
    # 31.111 F4: o ensino que nasce de uma falha lê a causa provável da tentativa (30.13), sem IA.
    falhas = self.learning.extensao(ServicoDeFalhas)
    self.training.diagnostico_da_falha = falhas.diagnostico_para_o_ensino if falhas is not None else None
    # 31.50: o lembrete de vencimento diz a etapa pelo nome do catálogo, não pela chave da capability.
    self.avisos.nome_da_capability = lambda capability: self.learning.nome_da_capability(None, capability)
    # O desfazer das decisões automáticas (28.25): a inversa de cada fila entra aqui, fora do módulo (ver o docstring).
    self.decisoes_desfazer = DesfazerDecisoes(
        self.decisoes_registro,
        inversas_das_filas(self.learning, run_do_objetivo=lambda oid: self.db.scalar(
            "SELECT run_id FROM objectives WHERE id=?", (oid,))),
        dias=lambda: float(self.cfg.file.avisos.decisoes_automaticas.desfazer_dias))
    self._digestoes = set()
    #: O laço de eventos do backend, guardado no `start`: o assentamento sem worker (29.93) pode vir de uma thread
    #: (o vencimento roda em `to_thread`; a rota síncrona de resolver, no threadpool) e é agendado nele.
    self._laco_principal = None
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
    # A conversa de volta pelo Telegram (28.15, ADR-071): o mesmo bot dos avisos recebe; desligada de fábrica
    # (`avisos.entrada.enabled`). As portas chamam os MESMOS serviços das rotas do painel.
    triagem = TriagemDeCredencial()
    # A IA lê a imagem do dono (28.24, F3): provedor da Anthropic FORA do hub, mas com o gasto conferido NO hub antes
    # (teto do dia e saldo da conta) e o custo em `ai_calls` (`origem='canais'`). Simulado: texto fixo, sem chamada.
    conferir = getattr(self.provider, "conferir_gasto", None)
    self.leitor_de_anexos = LeitorDeAnexo(
        cfg, self.db, self.anexos_canal, descritor=lambda: AnthropicProvider(cfg), redigir=triagem.redigir,
        conferir_gasto=None if conferir is None else (lambda: conferir(run_id=None, origem="canais", conta="anthropic")),
        registrar_uso=lambda u: self.repo.add_usage(None, None, u),
        simulado=lambda: (cfg.env.ai_provider or "anthropic").strip().lower() == "simulated")
    portas_da_central = PortasReais(db=self.db, runs=self.runs, aprovacoes=self.approval_service, saude=self.health,
                                    online=lambda: [d.id for d in self.devices.list_dtos()
                                                    if str(d.state) == "online" and d.kind != "store"],
                                    capturar=lambda alvo: capturar_para_o_dono(self.devices, alvo),
                                    leitor_de_anexos=self.leitor_de_anexos,
                                    # 31.113 F3: o canal recebe a prévia com o marcador da persona.
                                    previa_da_porta=lambda rid: previa_para_o_canal(self, previa_da_porta(self, rid)),
                                    aprovar_plano=lambda rid, pares, por, vista_em=None: aprovar_pelo_canal(
                                        self, rid, AprovarPlanoBody(aprovar=[ItemAprovado(step_id=s, chave=c)
                                                                             for s, c in pares],
                                                                    vista_em=vista_em), por=por),
                                    ler_imagem=self.avatares.get)
    # Quem fala com o bot e não é o dono (28.18): apresentação, nome, o dono decide; desligado de fábrica
    # (`avisos.entrada.convidados.enabled`). A recusa de credencial é a mesma da conversa do dono.
    convidados = ConvidadosDoTelegram(
        cfg, ContatosDoCanal(self.db, canal="telegram"), avisar_dono=self.avisos.enfileirar_aviso,
        recusa=lambda t: triagem.recusa(t) or parece_codigo(t), redigir=triagem.redigir,
        status=portas_da_central.status_para_convidado)
    self.telegram_entrada = ServicoDeEntrada(
        cfg, EntradasDoCanal(self.db, canal="telegram"), portas_da_central,
        lider=self._lider, recusa=triagem.recusa, redigir=triagem.redigir, convidados=convidados,
        anexos=self.anexos_canal)
    # O espelho do Trello (32.2, ADR-072): reconciliador no líder da trava `avisos`; desligado de fábrica
    # (`trello.enabled`). Lê as MESMAS pendências do Telegram e do painel.
    self.trello_espelho = EspelhoDoTrello(
        cfg, CartoesDoTrello(self.db, self.db.agora),
        FontesDaCentral(portas_da_central.pendencias,
                        _FontesDoEspelhoDoTrello(lambda: self.pedidos_api, RegistroDeValidacoesSql(self.db)),
                        lambda: cfg.file.avisos.url_painel),
        lider=self._lider, versao=self._versao_do_deploy, custos=self._linhas_de_custo, relogio=self.db.agora)
    # O leitor do Trello (32.2, passo 4): as actions do quadro viram comandos do dono, pela MESMA conversa do Telegram; só o
    # dono comanda, aprovar pede confirmação fora do Trello. Desligado de fábrica (`trello.enabled`). Convidado que pede
    # algo vira um aviso ao dono pela fila existente.
    self.trello_cadastro = CadastroDoWebhook(cfg)
    self.trello_leitor = LeitorDoTrello(
        cfg, EntradasDoCanal(self.db, canal="trello"), CartoesDoTrello(self.db, self.db.agora),
        CursorDoTrello(self.db, self.db.agora), portas_da_central, lider=self._lider, recusa=triagem.recusa,
        redigir=triagem.redigir, avisar_dono=self.avisos.enfileirar_aviso, relogio=self.db.agora,
        cadastro=self.trello_cadastro)
    # 28.30: o sim do dono no Telegram ao comentário dele só vale se o comentário no Trello ainda é o mesmo.
    self.telegram_entrada.conversa.comentarios = ComentariosDoTrello(EntradasDoCanal(self.db, canal="trello"),
                                                                     self.trello_leitor.cliente)
    # O webhook do Trello (32.2, §8): a rota só confere a assinatura e ANOTA o id da action; o líder a relê pela API.
    # Desligado de fábrica (`trello.webhook.enabled`); a reconciliação do leitor cobre sozinha.
    self.trello_webhook = PortaDoWebhook(cfg, EntradasDoCanal(self.db, canal="trello"),
                                         acordar=self.trello_leitor.acordar)
    # O catálogo da cadeia de intenção (habilidades publicadas e fluxos ativos, respeitando `skills.enabled` e
    # `ai.flows`), lido na hora. Compartilhado pela sombra da intenção (31.9) e pelo rótulo de intenção do Aprendizado
    # (30.25): os dois medem contra o MESMO catálogo.
    self.catalogo_da_cadeia = lambda: catalogo_de(
        lambda estado: self.skill_registry.list(state=estado), self.skill_registry.definition,
        skills_ligadas=self.cfg.file.skills.enabled, fluxos_ligados=self.cfg.file.ai.flows)
    # Sombra da intenção (31.9, ADR-069): R2 e R3 fora da cadeia, depois do `_plan`. Com a config padrão é inerte.
    self.runs.sombra_intencao = SombraDaIntencao(
        ConsumidorDeIntencao(self.decisao_fechada, self.decisao_sombra), resolver=self.skill_planner.resolve_intent,
        catalogo=self.catalogo_da_cadeia)
    self.runs.sombra_intencao.ligar_apps(self.decisao_fechada, self.decisao_sombra, self.apps.listar)  # R5 (31.13), travada
    # Rótulo de intenção (30.25): um minerador no digest da execução assentada, sem gancho novo e sem IA.
    ligar_intencao.ligar(self.learning, self.db, dados=self.runs.dados_da_intencao,
                         resolver=self.skill_planner.resolve_intent, catalogo=self.catalogo_da_cadeia)
    # 30.31: a validação automática do "pedir evidência" do curador precisa da fila de execuções e do parque
    # (`off` de fábrica). O despachante só roda com o central saudável e sem execução em curso.
    ligar_validacao.ligar(self.learning, self.db, fila=self.runs, parque=self.scheduler,
                          # 30.81: o ensinado que espera a prova não é o fluxo ativo do comando
                          fluxo_ativo_para=lambda comando: self.scheduler.flows.ativo_para(comando) is not None,
                          saudavel=lambda: not self.health().problems,
                          config=lambda: self.cfg.file.aprendizado.validacao,
                          precos=lambda: self.cfg.file.ai.prices, relogio=now,
                          # 30.36: o plano do fluxo ativo, para a receita sem caminho não gastar uma execução
                          plano_ativo_para=lambda comando: (m[1] if (m := self.scheduler.flows.ativo_para(comando))
                                                            else None))
    # Laço de pedidos persistentes (28.4). O objeto existe sempre (o gancho de fim de execução e a API do 28.9 o
    # chamam sem conferir); a TAREFA só sobe com `pedidos.enabled` e `roda_scheduler` (ver `start`).
    self.pedidos = LacoDePedidos(
        self.db, self.runs, self.lideranca, cfg.file.pedidos,
        # 28.6: o custo da ocorrência sai de `ai_calls` pela MESMA conta do painel de uso (`/api/usage`) e do teto.
        custo_da_execucao=lambda run_id: costs.spent_usd(self.db, cfg.file.ai.prices, run_id=run_id),
        # 28.6: saldo da conta de IA abaixo do mínimo (ADR-051) ADIA o despacho; é a mesma leitura de /api/ai/balances.
        adiar_por_saldo=lambda: motivo_de_adiamento(self.db, cfg, cfg.file.pedidos.saldo_minimo_usd),
        # 28.5/28.6: o `AvisoDTO` da pausa por falhas seguidas, da ocorrência incerta e do orçamento a 80% é GRAVADO em
        # `pedido_avisos` (072) e só então sai como `pedido.aviso` (contrato 28.9), que o canal de fora (28.11) assina no
        # barramento. É o MESMO caminho dos avisos da API (`PedidosApi.registrar_aviso`): chave igual, um evento só.
        avisar=lambda aviso: self.pedidos_api.registrar_aviso(aviso))
    # API de pedidos (28.9): prévia, criação, ações, leitura e os eventos `pedido.*` (as marcas do laço e das ações).
    self.pedidos_api = PedidosApi(self.db, self.pedidos, self.runs, self.bus.emit, cfg.file.pedidos,
                                  membro_trello_dono=cfg.file.trello.membro_dono)
    self.pedidos.notificar = self.pedidos_api.publicar
    # Costuras do aprendizado (ADR-054, A2): o executor pede as lições do ator e avisa cada tentativa fechada; o
    # serviço de execução pede as do planejador e avisa os gestos (resolver, repetir, cancelar, responder); o
    # gerenciador, a tomada de controle; o ensino, a correção; a rota de comandos (`api.py`, por `self.costuras`),
    # o comando incerto resolvido. Sem isto tudo é no-op — e nada delas decide desfecho, verificação ou guarda.
    self.costuras = costuras_do_livro(self.learning, self.db)
    self.scheduler.executor.costuras = self.costuras
    self.runs.costuras = self.costuras
    self.devices.costura_de_controle = self.costuras
    self.teaching.costura_de_ensino = self.costuras
    # Voz e preferências (ADR-054, A9): a voz que o dono publicou entra no contexto social (mesmo perfil, mesma
    # ação); a escolha repetida num empate de habilidades vira a etapa de preferência da RESOLVE. Os dois passos
    # da curadoria (varrer aprovações decididas, minerar respostas e escolhas) entram aqui. Sem IA.
    ligar_voz.pendurar(self.learning, self.db, contextos=self.social.contexts, planejador=self.skill_planner)
    # ADR-025: a credencial fornecida para a execução só chega ao aparelho pelo canal sensível, do cofre ao driver.
    self.scheduler.executor.secrets = self.secrets
    self.scheduler.executor.sensitive_input = self.sensitive_input
    self.scheduler.executor.persona_images = self.persona_images      # 29.30: mídia da publicação própria
    self._diag_cache = None
    self._bg = []
    #: 29.78: voltas completas dos laços de faxina (rodando ou não, conforme a trava). A primeira é na subida;
    #: o harness dos testes só entrega o backend depois dela, senão a faxina apagava o que o teste acabou de gravar.
    self.voltas_de_faxina = {"retencao": 0, "expiracao": 0}
    self.saude = SaudeDoSistema(self)
    #: Última leitura da sonda do túnel por worker (achado #179): worker_id -> 'up' | 'down'.
    self._transport_cache = {}
    self.devices.transport_state_of = self._transport_state_of
    self.devices.worker_process_of = self._worker_process_of
