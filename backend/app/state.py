"""Composição da aplicação: cria e liga os módulos (dispositivos, automação, planejamento, fila, eventos)."""
from __future__ import annotations

import asyncio
import logging
import shutil
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
from .models import AiStatus, AppiumStatus, Health, InstalledAppState, Problem, SdkStatus, SessionStatus
from .devices.installer import AppInstaller
from .integrations.instagram.authentication import InstagramAuthenticator
from .integrations.instagram.navigation import comentario_de, conteudo_visivel
from .planning.capabilities import capability_of, texto_a_gerar
from .planning.provider import AIProvider, build_provider
from .releases.inspector import ApkInspector
from .security import local_secret
from .security.secret_store import SecretStore, build_key_provider
from .releases.repository import ReleaseRepository
from .releases.service import ReleaseService
from .security.sensitive_input import SensitiveInputChannel
from .social.repository import SocialRepository
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
        self.release_repo = ReleaseRepository(self.db)
        self.releases = ReleaseService(cfg, self.release_repo, ApkInspector(self.tools), self.bus)
        self.installer = AppInstaller(self.devices)
        # Cofre de credenciais: chave mestra fora do banco (DPAPI no Windows, ambiente como alternativa).
        self.secrets = SecretStore(self.db, build_key_provider(
            data_dir=cfg.data_dir, env_material=cfg.env.instagram_credentials_master_key))
        self.social_repo = SocialRepository(self.db)
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
        # Manutenção suspende novas atribuições: o scheduler pergunta ao registro antes de tirar um objetivo do lugar.
        self.scheduler.worker_gate = self.workers.aceita_trabalho
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
        self.scheduler.policy_gate = self._policy_gate
        # O lock de escrita é por execução: some junto com ela, senão o dicionário cresceria para sempre.
        self.scheduler.on_run_settled = lambda run_id: self._draft_locks.pop(run_id, None)
        # Wipe, perda do aparelho ou qualquer coisa que mexa no disco invalida a sessão observada.
        self.devices.on_session_invalidated = self._invalidate_sessions
        # Apagar os dados do aparelho apaga também o app: sem isto o central seguia dizendo "pronto" para um
        # aparelho vazio, a porta do app deixava passar e "Distribuir" recusava reinstalar.
        self.devices.on_device_wiped = self._forget_app_state
        # Instalar, atualizar, voltar de versão ou reinstalar também mexe no disco — e a matriz de invalidação diz
        # que nesses casos a sessão passa a ser "não verificada", nunca "perdida sem olhar".
        self.releases.on_app_changed = self._invalidate_sessions
        self.runs = RunService(self.repo, self.scheduler, self.devices, self.provider, profiles=self.social)
        self._diag_cache: dict[str, Any] | None = None
        self._bg: list[asyncio.Task[Any]] = []

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
                reconciliar_incertos(self)
            except Exception:  # noqa: BLE001 - a sonda nunca pode derrubar o backend
                log.exception("reconciliação de comandos incertos")

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

    def _invalidate_sessions(self, instance_id: str, motivo: str) -> None:
        n = self.social_repo.invalidate_sessions_of_instance(instance_id, reason=motivo)
        if n:
            self.bus.emit("log", f"{instance_id}: sessão do Instagram invalidada — {motivo}", level="warn",
                          instance_id=instance_id)

    # Estados de sessão que só uma pessoa resolve: insistir sozinho viraria laço e poderia bloquear a conta.
    _SESSAO_PRECISA_DE_PESSOA = (SessionStatus.auth_challenge.value, SessionStatus.wrong_account.value)

    def _session_gate(self, rt: DeviceRuntime) -> tuple[str, Any | None] | None:
        """Terceira porta do despacho: aparelho pronto, app pronto, **sessão pronta**.

        Devolve `None` quando pode despachar; `(motivo, trabalho)` quando dá para resolver sozinho autenticando; e
        `(motivo, None)` quando depende de uma pessoa — aí o item fica bloqueado no painel, sem worker nenhum.

        Aparelho sem perfil vinculado não tem porta: o QA Messenger e o caminho antigo seguem iguais.
        """
        profile_id = self.social_repo.profile_id_for_instance(rt.id)
        if profile_id is None:
            return None
        session = self.social_repo.session_row(profile_id)
        if session and session["status"] == SessionStatus.session_ready.value and session["instance_id"] == rt.id:
            return None
        motivo = (session["detail"] if session and session["detail"]
                  else "a sessão deste perfil ainda não foi verificada")
        if session and session["status"] in self._SESSAO_PRECISA_DE_PESSOA:
            return motivo, None
        cred = self.social_repo.credential_row(profile_id)
        if cred is None or cred["status"] == "invalid":
            return ("a credencial deste perfil não está utilizável; cadastre a senha no portal"
                    if cred is None else motivo), None
        return motivo, (lambda: self.instagram.ensure_session(rt, profile_id, automatic=True))

    # ------------------------------------------------------------------ entrega do aplicativo ao parque
    # Estados em que uma entrega FALHOU. Daqui ninguém tenta de novo sozinho: instalar é mexer no disco do aparelho, e
    # repetir às cegas o que acabou de falhar é o "retry cego" que o projeto proíbe. Quem retenta é uma pessoa.
    _ENTREGA_FALHOU = ("install_failed", "verify_failed", "incompatible", "version_drift")
    _ENTREGA_AUTOMATICA = ("missing", "installed", "ready")

    def _app_resolver(self, rt: DeviceRuntime, package: str, obj: Any) -> tuple[str, Any | None] | None:
        """Resolvedor da porta do app: há uma versão distribuída ainda por instalar neste aparelho?

        `None` = nada pendente, a porta decide só pelo estado. É o que faz o rodízio entregar o app sem ninguém pedir:
        só 4 aparelhos ficam ligados por vez, e os demais recebem a versão na próxima vez que pegarem uma tarefa
        daquele pacote — ANTES da tarefa.
        """
        row = self.release_repo.app_state(rt.id, package)
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
            return None                          # installing/verifying sem dono: a porta bloqueia pelo estado, como antes
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
            pendentes = [r for r in linhas if r["installed_release_id"] != rid
                         and r["state"] in self._ENTREGA_AUTOMATICA and r["instance_id"] in self.devices.devices
                         and not self.devices.devices[r["instance_id"]].store]
            if not pendentes:
                self._entrega_imediata.discard(rid)
                prontos = sum(1 for r in linhas if r["installed_release_id"] == rid)
                falhas = sum(1 for r in linhas if r["state"] in self._ENTREGA_FALHOU)
                self.bus.emit("log", f"Entrega imediata encerrada: {prontos} aparelho(s) na versão, {falhas} com falha"
                                     + ("" if entregavel else " — a versão deixou de poder ser entregue") + ".",
                              level="warn" if falhas or not entregavel else "info", data={"release_id": rid})
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
                saida.append({"id": rt.id, "outcome": "pending",
                              "reason": (f"está {rt.state.value}: o rodízio vai ligá-lo para instalar agora" if eager else
                                         f"está {rt.state.value}: instala ao entrar em serviço, antes da tarefa")})
            elif self.scheduler.run_device_job(rt, lambda rt=rt: self._entregar(rt, package, release_id),
                                               label="entrega do aplicativo"):
                saida.append({"id": rt.id, "outcome": "started", "reason": "instalando agora"})
            else:
                saida.append({"id": rt.id, "outcome": "pending",
                              "reason": "ocupado agora: instala quando pegar a próxima tarefa"})
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
        self.social.reconcile_pending_effects()      # efeito disparado sem desfecho observado vira incerto
        for cmd in self.commands.reconcile_after_restart():
            # Sai como evento para a interface poder mostrar "isto ficou sem desfecho", em vez de o comando
            # simplesmente desaparecer do histórico quando o processo cai.
            self.bus.emit("command.updated", f"{cmd['instance_id']}: {cmd['verb']} — {cmd['reason']}",
                          level="warn", instance_id=cmd["instance_id"],
                          data={"command": command_dto(cmd).model_dump()})
        self._bg.append(asyncio.create_task(self._retention_loop(), name="retention"))
        self._bg.append(asyncio.create_task(self._worker_reaper_loop(), name="worker-reaper"))
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
