"""Composição da aplicação: cria e liga os módulos (dispositivos, automação, planejamento, fila, eventos)."""
from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from .automation.appium_server import AppiumServer
from .automation.driver import DeviceIO
from .commands.reconciler import reconciliar_incertos
from .commands.store import CommandStore, command_dto
from .devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from .workers.local import LocalWorker
from .workers.registry import HEARTBEAT_S, WorkerRegistry
from .config import Config, LimitsCfg
from .db import Database, dumps, loads
from .devices.manager import DeviceManager, DeviceRuntime
from .devices.sdk import SdkTools
from .events import EventBus
from .models import (AiStatus, AppiumStatus, Health, InstalledAppState, InstanceState, OFFLINE_POLICY_PADRAO,
                     Problem, SdkStatus, SessionStatus)
from .devices.installer import AppInstaller
from .integrations.instagram.authentication import InstagramAuthenticator
from .integrations.instagram.navigation import comentario_de, conteudo_visivel
from .planning.capabilities import capability_of, texto_a_gerar
from .planning.catalog import capabilities_of, session_provider_of
from .planning.provider import AIProvider, build_provider
from .releases.inspector import ApkInspector
from .security import local_secret
from .security.secret_store import SecretStore, build_key_provider
from .releases.repository import ReleaseRepository
from .releases.service import InstalacaoIncerta, ReleaseService
from .security.sensitive_input import SensitiveInputChannel
from .social.repository import SocialRepository, sessao_vencida
from .social.approvals import (ApprovalService, ApprovalStore, definir_texto, guardar_rascunho, ler_rascunho,
                               textos_irmaos)
from .social.policy import PolicyEngine, Verdict
from .social.service import SocialError, SocialService
from .taskqueue.repository import Repository
from .taskqueue.scheduler import Scheduler
from .taskqueue.service import RunService
from .util import now, to_iso

log = logging.getLogger("poc")
VERSION = "0.1.0"


@lru_cache(maxsize=4)
def commit_em_execucao(raiz: Path) -> str | None:
    """O commit que ESTE processo carregou, lido do `.git` — sem chamar `git`.

    Existe porque `version` é uma constante no código e não respondia a pergunta que o deploy faz: *este processo é
    o código novo?* O parque rodou por um dia um backend anterior às migrações 016/017 e nada no `/api/health`
    dizia isso. Cacheado porque o commit não muda enquanto o processo vive — trocar o código exige reiniciar.

    Sem subprocesso de propósito: `git` pode não estar no PATH da conta que roda o serviço, e um `/api/health` que
    falha por causa disso troca uma resposta útil por um erro. Ler dois arquivos de texto sempre funciona.
    """
    git = raiz / ".git"
    try:
        cabeca = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not cabeca.startswith("ref:"):
        return cabeca[:40] or None            # HEAD destacado: o próprio sha
    ref = cabeca.partition(":")[2].strip()
    try:
        return (git / ref).read_text(encoding="utf-8").strip()[:40] or None
    except OSError:
        pass
    try:                                       # ref empacotada (`git gc` move refs para packed-refs)
        for linha in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, nome = linha.partition(" ")
            if nome.strip() == ref:
                return sha.strip()[:40] or None
    except OSError:
        pass
    return None

# Que tipo de escrita é cada ação do catálogo. Muda o enquadramento do texto: responder alguém não é o mesmo que
# comentar uma publicação nem que puxar conversa do zero.
_TIPO_DE_TEXTO = {
    "CREATE_COMMENT": "post_comment",
    "REPLY_COMMENT": "comment_reply",
    "SEND_MESSAGE": "dm_initiate",
}
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


class AppState:
    def __init__(self, cfg: Config, *, provider: AIProvider | None = None,
                 io_factory: Callable[[DeviceRuntime], DeviceIO] | None = None, manage_appium: bool = True):
        self.cfg = cfg
        cfg.ensure_dirs()
        # Regravado a cada subida, de propósito: um segredo que vazou deixa de servir no próximo restart, e quem
        # precisa dele (`scripts/stop.ps1`) lê o arquivo na hora de usar. Ver `security/local_secret.py`.
        local_secret.garantir(cfg.data_dir)
        self.db = Database(cfg.db_dsn)
        self.db.migrate()
        self.bus = EventBus(self.db)
        self.tools = SdkTools(cfg)
        self.settings = SettingsStore(self.db, cfg.file.limits)
        self.appium = AppiumServer(cfg, self.tools)
        # Único caminho por onde uma credencial chega ao aparelho; recusa operar sem mascaramento comprovado.
        self.sensitive_input = SensitiveInputChannel(lambda: self.appium.log_masking_active)
        self.manage_appium = manage_appium
        self._seed_apps()
        self.devices = DeviceManager(cfg, self.db, self.bus, self.tools, self.appium,
                                     settings_getter=self.settings.get, io_factory=io_factory)
        self.devices.seed()
        self.provider: AIProvider = provider or build_provider(cfg)
        self.repo = Repository(self.db, self.bus, cfg.evidence_dir, owner_id=cfg.owner_id)
        # Comando do painel como entidade: sem isto a ação era um 202 sem registro, e a interface chamava de
        # sucesso o que só tinha sido aceito.
        self.commands = CommandStore(self.db)
        # Workers: as máquinas que hospedam aparelhos. O executor local é um deles, não um caminho paralelo.
        self.workers = WorkerRegistry(self.db, on_change=self._publish_worker)
        # O central como worker. Conectado em `start()`, quando já existe laço de eventos para o canal em
        # processo: é ele que faz o ciclo de vida dos aparelhos desta máquina passar pelo MESMO despacho do
        # agente remoto (`workers/local.py`).
        self.local_worker = LocalWorker(self)
        self.workers.local_worker_id = self.cfg.owner_id
        # Release de APK como artefato: importar/inspecionar/validar/catalogar, e instalar com estado observado.
        # `owner_id`: a operação de app aberta AQUI fica marcada como nossa. Sem isso, com dois
        # backends no mesmo banco, o que sobe marcava como interrompidas as instalações vivas do outro.
        self.release_repo = ReleaseRepository(self.db, owner_id=cfg.owner_id)
        self.releases = ReleaseService(cfg, self.release_repo, ApkInspector(self.tools), self.bus)
        # Prazos do instalador pela configuração: ajustar ao que se mediu no worker remoto deixa de
        # exigir edição de código.
        self.installer = AppInstaller.from_config(self.devices, cfg)
        # Cofre de credenciais: chave mestra fora do banco (DPAPI no Windows, ambiente como alternativa).
        self.secrets = SecretStore(self.db, build_key_provider(
            data_dir=cfg.data_dir, env_material=cfg.env.instagram_credentials_master_key))
        self.social_repo = SocialRepository(self.db)
        # Validade do "Conectado": o repositório monta o DTO do perfil e é ele que marca a sessão como dado velho.
        self.social_repo.session_max_age_s = cfg.file.instagram.session_max_age_s
        self.social = SocialService(self.social_repo, self.secrets, self.bus,
                                    known_instances=lambda: list(self.devices.devices),
                                    store_instance=lambda: self.cfg.store_id,
                                    provider=self.provider,
                                    # geração social fora de execução: entra no relatório de custo sem run/objetivo
                                    usage_sink=lambda u: self.repo.add_usage(None, None, u))
        # Login determinístico, fora do laço da IA: a senha só passa pelo canal de entrada sensível.
        self.instagram = InstagramAuthenticator(cfg, self.devices, self.social_repo, self.secrets,
                                                self.sensitive_input, self.bus)
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
        self.policies = PolicyEngine(self.social_repo)
        self.approvals = ApprovalStore(self.db)
        self.approval_service = ApprovalService(self.approvals, self.repo, self.scheduler)
        # O executor grava no histórico do perfil o efeito que dispara — é o que alimenta limites e memória.
        self.scheduler.executor.social = self.social
        self.scheduler.executor.approvals = self.approvals
        # Login/desafio visto NO MEIO da execução corrige o estado do perfil. Sem isto o painel seguia dizendo
        # "Conectado" para uma conta presa num desafio, e o login automático nunca disparava.
        self.scheduler.executor.on_auth_needed = self._sessao_desmentida
        self.scheduler.policy_gate = self._policy_gate
        # O lock de escrita é por execução: some junto com ela, senão o dicionário cresceria para sempre.
        self.scheduler.on_run_settled = lambda run_id: self._draft_locks.pop(run_id, None)
        # Wipe, perda do aparelho ou qualquer coisa que mexa no disco invalida a sessão observada.
        self.devices.on_session_invalidated = self._invalidate_sessions
        # Apagar os dados do aparelho apaga também o app: sem isto o central seguia dizendo "pronto" para um
        # aparelho vazio, a porta do app deixava passar e "Distribuir" recusava reinstalar.
        self.devices.on_device_wiped = self._forget_app_state
        # Aparelho no ar e inútil (Android morto por dentro, sessão que não abre) com `desired_state=online`:
        # alguém pede o reinício. O gerenciador não conhece comandos; quem os abre é a camada da API.
        self.devices.on_remediation_needed = self._remediar_aparelho
        # O rodízio passa a ligar e desligar aparelho de outra máquina — pelo worker, como um comando do painel.
        self.devices.on_lifecycle_request = self._pedir_ciclo_de_vida
        self.devices.worker_hibernates = self.workers.hiberna
        # "Estado lido do aparelho, nunca presumido" só vale se alguém relê: aparelho que entra no ar com
        # afirmação velha sobre o disco tem o app reobservado antes de a porta deixar qualquer tarefa passar.
        self.devices.on_device_online = self._reobservar_se_velho
        # Instalar, atualizar, voltar de versão ou reinstalar também mexe no disco — e a matriz de invalidação diz
        # que nesses casos a sessão passa a ser "não verificada", nunca "perdida sem olhar".
        self.releases.on_app_changed = self._sessao_apos_mudanca_de_app
        # Toda mudança de estado do app por aparelho vira evento persistido: é o que faz "O que está instalado"
        # se atualizar sozinha em vez de prometer um resultado que só aparecia recarregando a página.
        self.release_repo.on_app_state_changed = self._publicar_estado_do_app
        self.runs = RunService(self.repo, self.scheduler, self.devices, self.provider, profiles=self.social)
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
            # Aparelho que entrou no parque depois da distribuição adota aqui a versão promovida do app dele.
            self.aplicar_versao_promovida(rt)
        except Exception:  # noqa: BLE001 - adotar a versão desejada nunca pode impedir o aparelho de subir
            log.exception("%s: falha ao adotar a versão promovida", instance_id)
        # Dado velho (a afirmação passou da validade) e dado SEM DESFECHO (instalação interrompida por reinício
        # ou por timeout de transporte) se resolvem do mesmo jeito: relendo o aparelho.
        pacotes = list(dict.fromkeys(self.pacotes_com_dado_velho(instance_id)
                                     + self.pacotes_sem_desfecho(instance_id)))
        if not pacotes:
            return

        async def reler() -> None:
            for package in pacotes:
                try:
                    await self.releases.verify_on(rt, package, self.installer)
                except Exception as exc:  # noqa: BLE001 - reobservar é observação: falhar não derruba o aparelho
                    log.info("%s: não foi possível reobservar %s agora (%s)", instance_id, package, exc)

        self.scheduler.run_device_job(rt, reler, label="reobservação do estado do app")

    def _worker_capacity(self, worker_id: str) -> Any:
        """Vagas e recursos daquela máquina. Para ESTE servidor, quem manda nas vagas é a configuração viva
        (`max_online_devices`), e não o `max_slots` que o worker local gravou quando subiu: o operador muda o
        limite em tempo de execução, e o rodízio tem de obedecer no mesmo tick."""
        cap = self.workers.capacidade(worker_id)
        if cap is not None and worker_id == self.cfg.owner_id:
            cap.max_slots = max(1, int(self.settings.get().max_online_devices or 1))
        return cap

    def _pedir_ciclo_de_vida(self, instance_id: str, verb: str, motivo: str) -> str | None:
        """O rodízio pede `start`/`wake`/`stop`/`hibernate` num aparelho de outra máquina. Mesma importação
        tardia de `_remediar_aparelho`: a regra mora aqui, o comando nasce na API."""
        from .api import pedir_ciclo_de_vida

        try:
            return pedir_ciclo_de_vida(self, instance_id, verb, motivo, requested_by="scheduler")
        except Exception:  # noqa: BLE001 - o rodízio nunca pode derrubar o tick do scheduler
            log.exception("%s: falha ao pedir '%s' ao worker", instance_id, verb)
            return None

    def _remediar_aparelho(self, instance_id: str, motivo: str) -> None:
        """Abre o `restart` de remediação. A importação é tardia porque `api` depende de `state`, não o contrário —
        o mesmo desenho de `reconciliar_incertos`: a regra mora aqui, o comando nasce lá."""
        from .api import remediar_reiniciando

        try:
            if remediar_reiniciando(self, instance_id, motivo) is None:
                log.info("%s: degradado, mas não há reinício automático a pedir", instance_id)
        except Exception:  # noqa: BLE001 - remediar nunca pode derrubar o monitor de aparelhos
            log.exception("%s: falha ao abrir o reinício de remediação", instance_id)

    def _invalidate_sessions(self, instance_id: str, motivo: str) -> None:
        n = self.social_repo.invalidate_sessions_of_instance(instance_id, reason=motivo)
        if n:
            self.bus.emit("log", f"{instance_id}: sessão do Instagram invalidada — {motivo}", level="warn",
                          instance_id=instance_id)

    def _sessao_apos_mudanca_de_app(self, instance_id: str, package: str, motivo: str) -> None:
        """Mexer no disco de UM app invalida a sessão DAQUELE app — não a de qualquer outro.

        Instalar, atualizar ou voltar de versão o QA Messenger marcava a sessão do Instagram como "não
        verificada" com o motivo "o aplicativo foi instalado neste aparelho": reobservação forçada e painel
        poluído por um app que não tem conta nenhuma. Quem decide é o registro (`planning/catalog`): só o pacote
        cujo provedor de sessão é o Instagram chega à invalidação. Apagar o disco do APARELHO inteiro (wipe)
        continua invalidando sem perguntar — ali o dado do perfil foi mesmo embora.
        """
        if session_provider_of(package) != "instagram":
            return
        self._invalidate_sessions(instance_id, motivo)

    # Estados de sessão que só uma pessoa resolve: insistir sozinho viraria laço e poderia bloquear a conta.
    _SESSAO_PRECISA_DE_PESSOA = (SessionStatus.auth_challenge.value, SessionStatus.wrong_account.value)
    # O que a tela viu durante a execução → o que a sessão passa a valer. `auth_required` NÃO é um destes estados
    # que travam: é justamente o que devolve o caso ao autenticador automático, que tem a credencial no cofre.
    _SESSAO_PELO_QUE_A_TELA_VIU = {
        "auth_required": SessionStatus.auth_required,
        "auth_challenge": SessionStatus.auth_challenge,
        "wrong_account": SessionStatus.wrong_account,
    }

    def _sessao_desmentida(self, instance_id: str, kind: str, detail: str) -> None:
        """A tela do aparelho contradisse o que a sessão afirmava. O cache é corrigido, com evento.

        Só mexe em perfil VINCULADO àquele aparelho: aparelho sem perfil (o QA Messenger, o caminho antigo) não
        tem sessão para desmentir, e a mesma tela de senha ali não significa nada sobre Instagram nenhum.
        """
        status = self._SESSAO_PELO_QUE_A_TELA_VIU.get(kind)
        if status is None:
            return
        profile_id = self.social_repo.profile_id_for_instance(instance_id)
        if profile_id is None:
            return
        atual = self.social_repo.session_row(profile_id)
        if atual is not None and atual["status"] == status.value:
            return
        self.social_repo.set_session(profile_id, status=status, instance_id=instance_id,
                                     verified_at=to_iso(now()), detail=detail[:300])
        self.bus.emit("log", f"{instance_id}: a sessão do perfil passou a '{status.value}' — {detail}",
                      level="warn", instance_id=instance_id)

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
        binding = self.social_repo.binding_row(profile_id)
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
                                                      physical_id=rt.physical_id)
            return None
        onde = binding["worker_id"] or "este servidor"
        motivo = (f"os dados deste perfil vivem em {onde} e {rt.id} aponta hoje para outro servidor"
                  if mudou_de_maquina else
                  f"o aparelho físico por trás de {rt.id} mudou desde o vínculo deste perfil")
        detalhe = f"{motivo}; a sessão gravada no disco anterior não está aqui"
        # A porta é consultada a cada volta do agendador enquanto o item estiver bloqueado. Reescrever a sessão e
        # emitir o mesmo aviso a cada tick encheria o histórico do aparelho com a mesma linha — o mesmo cuidado
        # que `_sessao_desmentida` já toma. Só o que MUDA é registrado.
        atual = self.social_repo.session_row(profile_id)
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
        self.social_repo.registrar_localidade(profile_id, worker_id=rt.worker_id, physical_id=rt.physical_id)
        if novidade:
            self.bus.emit("log", f"{rt.id}: {motivo} — o perfil autoriza reautenticar em outro servidor.",
                          level="warn", instance_id=rt.id)
        return None

    def _session_gate(self, rt: DeviceRuntime, package: str | None = None) -> tuple[str, Any | None] | None:
        """Terceira porta do despacho: aparelho pronto, app pronto, **sessão pronta**.

        Devolve `None` quando pode despachar; `(motivo, trabalho)` quando dá para resolver sozinho autenticando; e
        `(motivo, None)` quando depende de uma pessoa — aí o item fica bloqueado no painel, sem worker nenhum.

        A porta é POR APP: só abre quando o pacote do item é o de um app que declara provedor de sessão no
        registro (`planning/catalog`). Sem isto, uma tarefa de QA Messenger num aparelho com perfil do Instagram
        vinculado passava pela porta do Instagram — e um desafio de segurança numa conta que a tarefa nem ia
        tocar bloqueava o item, ou pior: o sistema abria o Instagram e tentava autenticar antes da tarefa de
        outro aplicativo.

        Aparelho sem perfil vinculado não tem porta: o QA Messenger e o caminho antigo seguem iguais.
        """
        if package is not None and capabilities_of(package).session_provider != "instagram":
            return None
        profile_id = self.social_repo.profile_id_for_instance(rt.id)
        if profile_id is None:
            return None
        if (recusa := self._porta_da_localidade(rt, profile_id)) is not None:
            return recusa
        session = self.social_repo.session_row(profile_id)
        if session and session["status"] == SessionStatus.session_ready.value and session["instance_id"] == rt.id:
            if not self.sessao_vencida(session):
                return None
            # Vencida: NÃO é "deslogado". Antes da tarefa, relê a tela — `observe_only` nunca tenta autenticar, e
            # num aparelho ainda logado a conferência devolve `session_ready` com data nova e a tarefa segue.
            return ("a verificação desta sessão passou da validade; o aparelho vai ser relido antes da tarefa",
                    lambda: self.instagram.ensure_session(rt, profile_id, observe_only=True))
        motivo = (session["detail"] if session and session["detail"]
                  else "a sessão deste perfil ainda não foi verificada")
        if session and session["status"] in self._SESSAO_PRECISA_DE_PESSOA:
            return motivo, None
        cred = self.social_repo.credential_row(profile_id)
        if cred is None or cred["status"] == "invalid":
            return ("a credencial deste perfil não está utilizável; cadastre a senha no portal"
                    if cred is None else motivo), None
        if not self.sensitive_input.available():
            # Achado #105: sem o canal comprovado, `ensure_session(automatic=True)` ia digitar o usuário e
            # levantar SensitiveInputUnavailable ao chegar na senha — sempre, a cada objetivo que topasse este
            # aparelho, insistindo enquanto o problema é de infraestrutura (Appium sem mascaramento comprovado),
            # não da conta. Bloqueia aqui, com a mesma dica que o health já mostra, em vez de deixar o agendador
            # bater na mesma parede a cada tick.
            return ("o canal de preenchimento de credencial está indisponível (mascaramento de log do Appium "
                    "não comprovado); reinicie pelo scripts/stop.ps1 + start.ps1", None)
        return motivo, (lambda: self.instagram.ensure_session(rt, profile_id, automatic=True))

    # ------------------------------------------------------------------ entrega do aplicativo ao parque
    # Estados em que uma entrega FALHOU. Daqui ninguém tenta de novo sozinho: instalar é mexer no disco do aparelho, e
    # repetir às cegas o que acabou de falhar é o "retry cego" que o projeto proíbe. Quem retenta é uma pessoa.
    _ENTREGA_FALHOU = ("install_failed", "verify_failed", "incompatible", "version_drift")
    _ENTREGA_AUTOMATICA = ("missing", "installed", "ready")

    def aplicar_versao_promovida(self, rt: DeviceRuntime) -> str | None:
        """A versão PROMOVIDA de um app é estado desejado do parque, não um ato pontual sobre quem existia na hora.

        "Distribuir" percorre os aparelhos daquele instante. android-12..15 foram criados um dia depois da
        distribuição do Instagram: ficaram sem linha em `device_app_state`, a porta do app não opinava, e uma
        tarefa de Instagram era despachada para um aparelho sem o aplicativo — o `open_app` falhava dentro da
        execução, consumindo tentativas. Aqui o aparelho que entra DEPOIS (ou que só agora foi vinculado ao app)
        passa a ter a mesma versão desejada dos irmãos, sem ninguém clicar em nada.

        Devolve o id da release adotada, ou `None` quando não há o que adotar. Nunca rearma entrega que falhou:
        repetir às cegas o que acabou de falhar é decisão de pessoa.
        """
        if rt.store:
            return None
        app_id = self.db.scalar("SELECT app_id FROM instances WHERE id=?", (rt.id,))
        package = self.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,)) if app_id else None
        if not package:
            return None                       # aparelho sem app vinculado: o caminho antigo segue igual
        rel = self.releases.promoted_release(str(package))
        if rel is None or rel.status.value != "installable":
            return None
        linha = self.release_repo.release_row(rel.id)
        if linha is None or motivo_incompativel(requisitos_de_release(linha), capacidades_de(rt),
                                                aparelho=rt.id) is not None:
            return None                       # mandar instalar o que não roda ali seria falha permanente
        row = self.release_repo.app_state(rt.id, str(package))
        if row is not None and (row["desired_release_id"] == rel.id or row["installed_release_id"] == rel.id
                                or row["state"] in self._ENTREGA_FALHOU or row["pending_op"]):
            return None
        self.release_repo.upsert_app_state(rt.id, str(package), desired_release_id=rel.id)
        self.bus.emit("log", f"{rt.id}: passa a ter como desejada a versão promovida de {package} "
                             f"({rel.version_name} · {rel.version_code}).", level="info", instance_id=rt.id)
        return rel.id

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
        row = self.release_repo.app_state(rt.id, package)
        if row is None or not row["desired_release_id"]:
            # Aparelho que nunca recebeu distribuição daquele app: se existe versão promovida e este aparelho é
            # do app, ela passa a ser a desejada AQUI, antes da tarefa — em vez de a tarefa ir para um aparelho
            # sem o aplicativo e o `open_app` falhar lá dentro.
            if self.aplicar_versao_promovida(rt):
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
        """
        try:
            return await self.releases.install_on(rt, release_id, self.installer)
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
            raise

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
            pendentes = [r for r in candidatos if r["instance_id"] not in nao_ligaveis]
            if not pendentes:
                self._entrega_imediata.discard(rid)
                prontos = sum(1 for r in linhas if r["installed_release_id"] == rid)
                falhas = sum(1 for r in linhas if r["state"] in self._ENTREGA_FALHOU)
                esperando = (f", {len(nao_ligaveis)} aguardando ser ligados em outro servidor "
                             f"({', '.join(sorted(nao_ligaveis))})" if nao_ligaveis else "")
                self.bus.emit("log", f"Entrega imediata encerrada: {prontos} aparelho(s) na versão, {falhas} com falha"
                                     + esperando
                                     + ("" if entregavel else " — a versão deixou de poder ser entregue") + ".",
                              level="warn" if falhas or nao_ligaveis or not entregavel else "info",
                              data={"release_id": rid})
                continue
            package = rel["package_name"]
            for r in pendentes:
                rt = self.devices.devices[r["instance_id"]]
                saida.append((rt.id, lambda rt=rt, package=package, rid=rid: self._entregar(rt, package, rid)))
        return saida

    def distribute(self, release_id: str, *, eager: bool = False) -> list[dict[str, Any]]:
        """Distribui uma versão PROMOVIDA ao parque. Canário primeiro: sem prova, não há o que distribuir.

        Grava a versão desejada em cada aparelho de tarefa. Quem está ligado e livre instala já; quem está desligado
        ou ocupado fica pendente e recebe pela porta do app, ao pegar a próxima tarefa daquele pacote. Pedir de novo
        é a nova tentativa explícita para quem tinha falhado.

        `eager` = "instalar em todos agora": além disso, os aparelhos pendentes viram demanda do rodízio, que os liga
        dentro das vagas, instala e cede a vaga ao próximo — sem esperar tarefa.
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
        saida: list[dict[str, Any]] = []
        for rt in self.devices.devices.values():
            if rt.store:
                continue                          # a loja é a fonte: nela o app vem da Play Store
            if (porque := motivo_incompativel(requisitos, capacidades_de(rt), aparelho=rt.id)) is not None:
                # A versão desejada NÃO é gravada: mandar instalar o que não roda ali deixaria o aparelho em
                # falha permanente de entrega, e o operador sem saber por quê. A explicação sai com o resultado.
                saida.append({"id": rt.id, "outcome": "incompatible", "reason": porque})
                continue
            row = self.release_repo.app_state(rt.id, package)
            if row and row["installed_release_id"] == release_id and row["state"] in ("ready", "installed"):
                self.release_repo.upsert_app_state(rt.id, package, desired_release_id=release_id)
                saida.append({"id": rt.id, "outcome": "already", "reason": "já está nesta versão"})
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
                from .api import _despachar_trabalho  # noqa: PLC0415 - `api` depende de `state`, não o contrário

                cmd = _despachar_trabalho(
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
            return None
        profile_id = obj["profile_id"] or self.social_repo.profile_id_for_instance(obj["instance_id"])
        rt = self.devices.devices.get(obj["instance_id"])
        pacote = self.scheduler._app_context(run, rt)[0].package if rt else None  # noqa: SLF001
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
        veredito = self.policies.check(profile_id, cap, run_id=obj["run_id"])
        if not veredito.allowed:
            return veredito
        # O texto é escrito AQUI, com a persona deste perfil, antes de qualquer digitação e antes da aprovação —
        # senão a pessoa aprovaria um rascunho que não é o que vai ser enviado.
        parado = await self._draft_gate(obj, srow, cap, profile_id, rt=rt, pacote=pacote)
        if parado is not None:
            return parado
        srow = self.repo.step_row(srow["id"]) or srow          # relê: o texto pode ter acabado de entrar
        if veredito.needs_approval:
            return self._approval_gate(obj, srow, cap, profile_id)
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
            tipo = _TIPO_DE_TEXTO.get(cap.key, "dm_initiate")
            alvo = bindings.get("username") or bindings.get("target")
            arvore = await self._ler_tela(rt, pacote)
            tela = conteudo_visivel(arvore) if arvore is not None else ""
            # Responder é diferente de comentar: aqui existe uma fala DIRIGIDA a esta conta, e é ela que fundamenta
            # tanto a resposta quanto o que o perfil passa a saber sobre a pessoa. Só deste bloco sai memória.
            recebido = (comentario_de(arvore, alvo or "") if arvore is not None and tipo == "comment_reply" else "")
            try:
                # `persist=False` de propósito: interação é TENTATIVA, e um rascunho não é. `pending` conta para o
                # limite ("uma ação que talvez tenha saído já mexeu com a conta"), então gravar aqui gastaria a
                # cota antes de digitar nada e contaria duas vezes o que fosse enviado — quem registra o efeito é
                # o commit.
                draft, _interacao = await self.social.draft_response(
                    profile_id, kind=tipo, brief=briefing, persist=False, incoming=recebido,
                    counterparty=alvo, screen=tela,
                    # Escrever é uma chamada de modelo DENTRO de uma execução: passa pelo mesmo caminho das
                    # outras, com limite de simultâneas, teto de orçamento conferido antes de gastar e custo
                    # lançado no objetivo certo.
                    runner=lambda f: self.scheduler.executor._ai(  # noqa: SLF001
                        obj["run_id"], obj["id"], f, step_id=srow["id"], role="social"),
                    avoid=textos_irmaos(self.db, obj["run_id"], srow["id"]))
            except SocialError as exc:
                # Sem texto não se digita nada. Isso é espera por uma pessoa, não falha da etapa: o briefing
                # continua lá e uma nova tentativa pode gerar.
                orcamento = exc.code == "ai_budget"
                return Verdict(allowed=False, policy=cap.default_policy,
                               reason=f"não foi possível escrever o texto desta etapa: {exc}",
                               # Cada motivo com a sua saída: mandar conferir a chave quando o que acabou foi o
                               # orçamento faria a pessoa procurar defeito onde não há e bater na mesma parede.
                               hint=("Aumente o orçamento de IA em Configuração (chamadas por objetivo ou tokens "
                                     "por execução) e retome o item." if orcamento else
                                     "Confira o provedor de IA e a persona do perfil, e retome o item."))
            if draft.refused or not (draft.content or "").strip():
                return Verdict(allowed=False, policy=cap.default_policy,
                               reason="a persona se recusou a escrever este texto",
                               hint=f"{draft.rationale or 'sem justificativa'}. Reescreva a intenção e retome o item.")
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

    def _approval_gate(self, obj: Any, srow: Any, cap: Any, profile_id: str) -> Any:
        """Ação que exige aprovação: a decisão da pessoa acontece ANTES de digitar qualquer coisa.

        É por isso que a porta fica aqui e não no meio da etapa: etapa concluída é estado terminal, então não
        haveria como "editar e refazer" depois que o texto já foi digitado e enviado.
        """
        pedido = self.approvals.for_step(srow["id"])
        if pedido is None:
            bindings = loads(srow["bindings"], {}) or {}
            pedido = self.approvals.open(
                profile_id=profile_id, capability=cap.key, summary=srow["title"],
                target=bindings.get("username") or bindings.get("target"), content=bindings.get("content"),
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
                       reason=f"{srow['title']} precisa de aprovação antes de acontecer",
                       hint="Abra Aprovações e escolha aprovar, editar ou rejeitar.")

    def _seed_apps(self) -> None:
        for a in self.cfg.file.apps:
            if self.db.one("SELECT id FROM apps WHERE id=?", (a.id,)):
                continue
            self.db.execute(
                "INSERT INTO apps(id, name, package, activity, apk_path, nav_hints, known_selectors, builtin) VALUES (?,?,?,?,?,?,?,?)",
                (a.id, a.name, a.package, a.activity, a.apk_path, a.nav_hints,
                 dumps(a.known_selectors) if a.known_selectors else None, int(a.builtin)))

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        self.bus.bind_loop(asyncio.get_running_loop())
        if self.manage_appium and self.cfg.file.appium.autostart:
            ok = await asyncio.to_thread(self.appium.start)
            log.info("Appium: %s (%s)", "ok" if ok else "indisponível", self.appium.detail)
        await self.devices.start()
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
        self._bg.append(asyncio.create_task(self._retention_loop(), name="retention"))
        self._bg.append(asyncio.create_task(self._worker_reaper_loop(), name="worker-reaper"))
        self._bg.append(asyncio.create_task(self._health_loop(), name="health"))
        self.bus.emit("log", f"Backend iniciado (v{VERSION}). Provedor de IA: {self.provider.name}"
                      + (" — MODO SIMULADO" if self.provider.simulated else ""))

    async def stop(self) -> None:
        for t in self._bg:
            t.cancel()
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

    async def _retention_loop(self) -> None:
        while True:
            try:
                s = self.settings.get()
                cutoff = to_iso(now() - timedelta(days=s.log_retention_days))
                removed = self.bus.purge_older_than(cutoff)
                ev_cut = to_iso(now() - timedelta(days=s.evidence_retention_days))
                old = self.db.query("SELECT DISTINCT run_id FROM evidence WHERE ts < ? AND run_id IN "
                                    "(SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?)", (ev_cut, ev_cut))
                for r in old:
                    shutil.rmtree(self.cfg.evidence_dir / r["run_id"], ignore_errors=True)
                    self.db.execute("DELETE FROM evidence WHERE run_id=?", (r["run_id"],))
                rotated = self.cfg.logs_dir / "appium.log.1"
                if rotated.exists() and to_iso(now() - timedelta(days=s.log_retention_days)) > to_iso(
                        datetime.fromtimestamp(rotated.stat().st_mtime, tz=UTC)):
                    rotated.unlink(missing_ok=True)      # o log do Appium também tem prazo de validade
                if removed or old:
                    log.info("retenção: %s eventos e %s execuções com evidências removidos", removed, len(old))
            except Exception:  # noqa: BLE001
                log.exception("retenção")
            await asyncio.sleep(6 * 3600)

    # ------------------------------------------------------------------ saúde
    def ai_status(self) -> AiStatus:
        """`provider.status()` só sabe da chave; o disjuntor de conta (crédito/credencial recusados em tempo de
        execução) vive no executor — combina os dois para health(), /api/ai e a aba IA lerem uma fonte só."""
        status = self.provider.status()
        breaker = self.scheduler.executor.ai_breaker
        if breaker is not None:
            status = status.model_copy(update={"account_blocked": True, "account_blocked_reason": breaker.message})
        return status

    def health(self) -> Health:
        problems: list[Problem] = []
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
        ai = self.ai_status()
        if not ai.configured:
            problems.append(Problem(code="ai_not_configured", message="Provedor de IA sem chave.",
                                    hint="Defina ANTHROPIC_API_KEY no .env e reinicie o backend. Gerenciamento e controle manual continuam disponíveis."))
        breaker = self.scheduler.executor.ai_breaker
        if breaker is not None:
            code = "ai_billing" if breaker.kind == "billing" else "ai_auth_failed"
            problems.append(Problem(code=code, message=f"{breaker.message} (execução {breaker.run_id}, {breaker.at}).",
                                    hint="Disjuntor de conta de IA acionado: a execução foi pausada automaticamente e "
                                         "nenhuma tentativa foi gasta. Corrija e retome a execução para soltar."))
        vault = self.secrets.status()
        if vault == "locked":
            problems.append(Problem(code="secret_store_locked",
                                    message="O cofre de credenciais está travado nesta máquina/usuário.",
                                    hint="As credenciais cifradas foram preservadas. Recadastre a senha de cada perfil "
                                         "pelo portal para voltar a usar autenticação automática."))
        elif vault == "unavailable":
            problems.append(Problem(code="secret_store_unavailable",
                                    message="Sem chave mestra para proteger credenciais.",
                                    hint="Defina INSTAGRAM_CREDENTIALS_MASTER_KEY no .env. Gerenciamento e controle "
                                         "manual seguem funcionando."))
        if ai.simulated:
            problems.append(Problem(code="ai_simulated", message="MODO SIMULADO ativo: nenhuma IA é consultada.",
                                    hint="Use AI_PROVIDER=anthropic no .env para o provedor real."))
        diag = self._diag_cache
        accel = diag["acceleration"]["detail"] if diag else None
        if diag and not diag["acceleration"]["usable"]:
            problems.append(Problem(code="no_acceleration", message="Aceleração de virtualização indisponível.",
                                    hint="No Windows, habilite 'Windows Hypervisor Platform' (WHPX) e reinicie."))
        hard = {"sdk_missing", "no_acceleration"}
        status = "error" if any(p.code in hard for p in problems) else ("degraded" if problems else "ok")
        emu_version = next((t["version"] for t in (diag or {}).get("tools", []) if t["name"] == "Android Emulator"), None)
        return Health(status=status, version=VERSION, commit=commit_em_execucao(self.cfg.root),
                      migration=self.ultima_migracao(), ai=ai,
                      appium=AppiumStatus(running=appium_up, port=self.cfg.file.appium.port, detail=self.appium.detail),
                      sdk=SdkStatus(found=sdk_ok, root=str(self.cfg.sdk_root), emulator_version=emu_version, accel=accel),
                      problems=problems,
                      features={"hibernation": self.cfg.file.android.hibernation, "recipes": self.cfg.file.ai.recipes,
                                "flows": self.cfg.file.ai.flows, "image_policy": self.cfg.file.ai.image_policy,
                                "system_image": self.cfg.file.android.system_image})

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
