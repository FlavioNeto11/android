"""Composição da aplicação: cria e liga os módulos (dispositivos, automação, planejamento, fila, eventos)."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .automation.appium_server import AppiumServer
from .automation.driver import DeviceIO
from .config import Config, LimitsCfg
from .db import Database, dumps, loads
from .devices.manager import DeviceManager, DeviceRuntime
from .devices.sdk import SdkTools
from .events import EventBus
from .models import AppiumStatus, Health, Problem, SdkStatus, SessionStatus
from .devices.installer import AppInstaller
from .integrations.instagram.authentication import InstagramAuthenticator
from .planning.capabilities import capability_of
from .planning.provider import AIProvider, build_provider
from .releases.inspector import ApkInspector
from .security.secret_store import SecretStore, build_key_provider
from .releases.repository import ReleaseRepository
from .releases.service import ReleaseService
from .security.sensitive_input import SensitiveInputChannel
from .social.repository import SocialRepository
from .social.approvals import ApprovalService, ApprovalStore
from .social.policy import PolicyEngine, Verdict
from .social.service import SocialService
from .taskqueue.repository import Repository
from .taskqueue.scheduler import Scheduler
from .taskqueue.service import RunService
from .util import now, to_iso

log = logging.getLogger("poc")
VERSION = "0.1.0"


class SettingsStore:
    """Limites editáveis em tempo de execução, persistidos no SQLite (semente: config.yaml)."""

    def __init__(self, db: Database, defaults: LimitsCfg):
        self.db = db
        stored = loads(db.scalar("SELECT value FROM settings WHERE key='limits'"), {}) or {}
        self._value = LimitsCfg.model_validate({**defaults.model_dump(), **stored})

    def get(self) -> LimitsCfg:
        return self._value

    def update(self, patch: dict[str, Any]) -> LimitsCfg:
        self._value = LimitsCfg.model_validate({**self._value.model_dump(), **patch})
        self.db.execute("INSERT INTO settings(key, value) VALUES ('limits', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (dumps(self._value.model_dump()),))
        return self._value


class AppState:
    def __init__(self, cfg: Config, *, provider: AIProvider | None = None,
                 io_factory: Callable[[DeviceRuntime], DeviceIO] | None = None, manage_appium: bool = True):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.db = Database(cfg.db_path)
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
        self.repo = Repository(self.db, self.bus, cfg.evidence_dir)
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
        self.policies = PolicyEngine(self.social_repo)
        self.approvals = ApprovalStore(self.db)
        self.approval_service = ApprovalService(self.approvals, self.repo, self.scheduler)
        # O executor grava no histórico do perfil o efeito que dispara — é o que alimenta limites e memória.
        self.scheduler.executor.social = self.social
        self.scheduler.policy_gate = self._policy_gate
        # Wipe, perda do aparelho ou qualquer coisa que mexa no disco invalida a sessão observada.
        self.devices.on_session_invalidated = self._invalidate_sessions
        # Instalar, atualizar, voltar de versão ou reinstalar também mexe no disco — e a matriz de invalidação diz
        # que nesses casos a sessão passa a ser "não verificada", nunca "perdida sem olhar".
        self.releases.on_app_changed = self._invalidate_sessions
        self.runs = RunService(self.repo, self.scheduler, self.devices, self.provider, profiles=self.social)
        self._diag_cache: dict[str, Any] | None = None
        self._bg: list[asyncio.Task[Any]] = []

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

    def _policy_gate(self, obj: Any, srow: Any, run: Any) -> Any:
        """Quarta porta, e a única que depende da ETAPA: política e limite da capability para este perfil.

        Devolve `None` quando pode seguir. Etapa sem capability (app sem catálogo) nunca passa por aqui — o QA
        Messenger e o caminho livre seguem exatamente como antes.
        """
        capability = srow["capability"] if "capability" in srow.keys() else None
        if not capability:
            return None
        profile_id = obj["profile_id"] or self.social_repo.profile_id_for_instance(obj["instance_id"])
        if not profile_id:
            return None
        rt = self.devices.devices.get(obj["instance_id"])
        pacote = self.scheduler._app_context(run, rt)[0].package if rt else None  # noqa: SLF001
        cap = capability_of(pacote, capability)
        if cap is None:
            return None
        veredito = self.policies.check(profile_id, cap, run_id=obj["run_id"])
        if not veredito.allowed:
            return veredito
        if veredito.needs_approval:
            return self._approval_gate(obj, srow, cap, profile_id)
        return None

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
        await self.scheduler.start()
        self.runs.resume_planning_after_restart()
        self.releases.reconcile_after_restart()      # instalação interrompida nunca é repetida às cegas
        self.social.reconcile_pending_effects()      # efeito disparado sem desfecho observado vira incerto
        self._bg.append(asyncio.create_task(self._retention_loop(), name="retention"))
        self.bus.emit("log", f"Backend iniciado (v{VERSION}). Provedor de IA: {self.provider.name}"
                      + (" — MODO SIMULADO" if self.provider.simulated else ""))

    async def stop(self) -> None:
        for t in self._bg:
            t.cancel()
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
        ai = self.provider.status()
        if not ai.configured:
            problems.append(Problem(code="ai_not_configured", message="Provedor de IA sem chave.",
                                    hint="Defina ANTHROPIC_API_KEY no .env e reinicie o backend. Gerenciamento e controle manual continuam disponíveis."))
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
        return Health(status=status, version=VERSION, ai=ai,
                      appium=AppiumStatus(running=appium_up, port=self.cfg.file.appium.port, detail=self.appium.detail),
                      sdk=SdkStatus(found=sdk_ok, root=str(self.cfg.sdk_root), emulator_version=emu_version, accel=accel),
                      problems=problems,
                      features={"hibernation": self.cfg.file.android.hibernation, "recipes": self.cfg.file.ai.recipes,
                                "flows": self.cfg.file.ai.flows, "image_policy": self.cfg.file.ai.image_policy,
                                "system_image": self.cfg.file.android.system_image})

    async def diagnostics(self, refresh: bool = False) -> dict[str, Any]:
        from .devices import diagnostics

        if refresh or self._diag_cache is None:
            self._diag_cache = await asyncio.to_thread(diagnostics.collect, self.cfg, self.tools, self.db)
        return self._diag_cache
