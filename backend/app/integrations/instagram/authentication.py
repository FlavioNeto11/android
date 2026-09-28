"""Autenticação do Instagram: código determinístico, fora do laço da IA.

Por que fora: o executor para em qualquer tela com campo de senha, e o ator é instruído a nunca digitar credencial.
Este módulo fala direto com o driver, pela fila exclusiva do aparelho, e a senha passa só pelo canal de entrada
sensível — que não gera argumento de ação nem histórico para o modelo.

O que ele nunca faz: repetir o envio por causa de timeout, tentar de novo depois de senha comprovadamente errada,
seguir com a conta errada, ou tentar resolver um desafio de segurança.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ...automation.driver import DriverError
from ...devices.installer import LAUNCH_POLL_S, wait_for_focus
from ...models import SessionStatus
from ...modules.identity.application.session_rules import (bloquear_por_desafio, emit_needs_person_change,
                                                          motivo_do_bloqueio_por_desafio)
from ...security.sensitive_input import SensitiveInputError, SensitiveInputUnavailable
from ...util import now, now_iso, parse_iso
from . import navigation, verification
from .navigation import Screen
from .reconciliation import LOGIN_ERROR_DETAIL, Outcome, Verdict, classify_after_submit

log = logging.getLogger("poc.instagram")

# A pessoa precisa saber o que fazer, venha o desafio de onde vier: antes ou depois do envio.
CHALLENGE_HELP = ("O Instagram exige confirmação adicional. Assuma o controle do aparelho, resolva na tela e devolva "
                  "o controle: a verificação recomeça sozinha.")
# Estados de sessão que só uma pessoa resolve — os mesmos que `state.py` usa para bloquear o agendador
# automático. Quem decide o evento da fila "Aguardando intervenção" é `session_rules.PRECISA_DE_PESSOA` (o mesmo
# conjunto, por valor); este fica para quem importa daqui.
PRECISA_DE_PESSOA = (SessionStatus.auth_challenge, SessionStatus.wrong_account)
AUTOMATION_TRIES = 3
AUTOMATION_WAIT_S = 8.0
INTERSTITIAL_TRIES = 4        # teto de dicas/onboarding dispensados por vez: fecha o caminho, sem virar laço
FILL_TRIES = 3                # tentativas de pôr o usuário no campo; só se envia com o valor conferido


@dataclass(slots=True)
class AuthResult:
    outcome: Outcome
    detail: str
    observed_username: str | None = None
    session_status: SessionStatus = SessionStatus.unknown
    attempted_login: bool = False

    @property
    def ready(self) -> bool:
        return self.outcome is Outcome.SESSION_READY


# `bloquear_por_desafio` e `emit_needs_person_change` moram em `modules/identity/application/session_rules.py`
# (fase K1): são regras do PERFIL, que o núcleo também aplica. Reexportados daqui para quem ainda importa deste módulo.
#: O rótulo deste app nas mensagens das regras do perfil.
ROTULO = "Instagram"
#: Por que o perfil foi bloqueado sozinho (o texto de sempre, agora montado pela regra do perfil com o rótulo do app).
MOTIVO_BLOQUEIO_POR_DESAFIO = motivo_do_bloqueio_por_desafio(ROTULO)


class InstagramAuthenticator:
    def __init__(self, cfg: Any, devices: Any, repo: Any, secrets: Any, sensitive: Any, bus: Any):
        self.cfg = cfg
        self.devices = devices
        self.repo = repo
        self.secrets = secrets
        self.sensitive = sensitive
        self.bus = bus
        self.focus_poll_s = LAUNCH_POLL_S          # intervalo entre leituras de foco enquanto o app abre

    @property
    def conf(self) -> Any:
        return self.cfg.file.instagram

    @property
    def package(self) -> str:
        """O pacote que este provedor abre e confere (`SessionProvider.package`)."""
        return str(self.conf.package)

    # ------------------------------------------------------------------ entrada principal
    async def ensure_session(self, rt: Any, profile_id: str, *, force_login: bool = False,
                             automatic: bool = False, observe_only: bool = False) -> AuthResult:
        """Garante que a conta do perfil está aberta neste aparelho. Reaproveita sessão sempre que possível.

        `automatic=True` é a chamada do agendador: nela, estado que depende de pessoa (desafio de segurança, conta
        errada) nem chega a tocar no aparelho. A chamada explícita do portal sempre reobserva, que é como o usuário
        retoma depois de resolver o desafio à mão.

        `observe_only=True` é "Verificar conta": lê a tela e nada mais. Num aparelho deslogado ele PARA na tela de
        login em vez de autenticar — antes, quem apertava "Verificar" gastava uma tentativa de login real sem saber.
        """
        profile = self.repo.profile_row(profile_id)
        if profile is None:
            return AuthResult(Outcome.UNCERTAIN, "perfil não encontrado")
        username = profile["username"]

        if automatic and (parado := self._needs_person(profile_id, rt.id)):
            return AuthResult(parado[0], parado[1], session_status=self._status_for(parado[0]))
        bloqueio = self._blocked_reason(profile_id)
        if bloqueio:
            self._save(profile_id, rt.id, SessionStatus.auth_required, detail=bloqueio)
            return AuthResult(Outcome.INVALID_CREDENTIAL, bloqueio, session_status=SessionStatus.auth_required)

        if not await self._ensure_automation(rt):
            return AuthResult(Outcome.RETRYABLE, "a sessão de automação do aparelho não ficou pronta")

        locale = await self._locale(rt)
        await self._open_app(rt)
        tree, package = await self._observe(rt)
        estado = navigation.classify(tree, package=package, locale=locale)

        # Depois de entrar, o Instagram intercala dicas e passos de onboarding que tapam o app ("Got it", "Skip").
        # São benignas e o botão usado não concede nada — mas enquanto estiverem na frente, a tela não é
        # classificável e a conta não tem como ser lida. Dispensa no máximo algumas, para não virar laço.
        for _ in range(INTERSTITIAL_TRIES):
            if estado.screen is not Screen.UNKNOWN:
                break
            botao = navigation.dismiss_button(tree)
            if botao is None:
                break
            await self._tap(rt, *botao.center)
            await asyncio.sleep(float(self.conf.settle_s))
            tree, package = await self._observe(rt)
            estado = navigation.classify(tree, package=package, locale=locale)

        # 1) Já autenticado? Reaproveitar é o caminho normal: ninguém digita senha à toa.
        if verification.is_logged_in(estado.screen) and not force_login:
            check = await verification.read_account(
                lambda: self._observe(rt), lambda x, y: self._tap(rt, x, y), expected=username, locale=locale)
            if check.matches:
                self._save(profile_id, rt.id, SessionStatus.session_ready, observed=check.observed,
                           verified_at=now_iso(), detail=check.detail)
                return AuthResult(Outcome.SESSION_READY, check.detail, check.observed, SessionStatus.session_ready)
            if check.observed:
                return await self._wrong_account(rt, profile_id, username, check.observed, locale)
            # entrou, mas a conta não pôde ser lida: não é sucesso nem motivo para digitar senha
            self._save(profile_id, rt.id, SessionStatus.unknown, detail=check.detail, reobserved=True)
            return AuthResult(Outcome.UNCERTAIN, check.detail, session_status=SessionStatus.unknown)

        if estado.screen in (Screen.CHALLENGE, Screen.TWO_FACTOR):
            return self._challenge(profile_id, rt.id, estado.reason)

        # 2) Deslogado: fazer login.
        if estado.screen is not Screen.LOGIN:
            detail = f"o app não está na tela de login nem autenticado ({estado.reason})"
            self._save(profile_id, rt.id, SessionStatus.unknown, detail=detail, reobserved=True)
            return AuthResult(Outcome.UNCERTAIN, detail)

        if observe_only:
            # "Verificar conta" só observa. Autenticar aqui gastaria uma tentativa de login REAL num clique que o
            # painel anuncia como leitura — e é o botão sugerido logo depois de um desafio resolvido à mão.
            detail = "o aparelho está deslogado; use Conectar para autenticar"
            self._save(profile_id, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

        return await self._login(rt, profile_id, username, estado.form, locale)

    # ------------------------------------------------------------------ login
    async def _login(self, rt: Any, profile_id: str, username: str, form: Any, locale: str | None) -> AuthResult:
        cred = self.repo.credential_row(profile_id)
        if cred is None:
            detail = "não há credencial cadastrada para este perfil"
            self._save(profile_id, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.INVALID_CREDENTIAL, detail, session_status=SessionStatus.auth_required)
        if form is None or not form.complete:
            detail = "o formulário de login não pôde ser identificado com segurança nesta tela"
            self._save(profile_id, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

        attempt = self.repo.start_auth_attempt(profile_id, rt.id, stage="form_found")
        try:
            conferido = await self._fill_username(rt, form, cred["login_identifier"] or username, locale)
            if not conferido:
                detail = "o campo de usuário não ficou com o valor esperado; envio abortado"
                self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=detail,
                                              stage="username_mismatch")
                self._count_failure(profile_id, rt.id)
                self._save(profile_id, rt.id, SessionStatus.auth_required, detail=detail)
                return AuthResult(Outcome.RETRYABLE, detail, session_status=SessionStatus.auth_required)

            # O teclado sobe ao focar o usuário e empurra a tela para cima; as posições lidas com o formulário vazio
            # deixam de valer. Relê e passa a usar as coordenadas ATUAIS de senha e de Entrar. Sem isto, o toque em
            # Entrar cai no vão abaixo do botão e o login nunca é enviado — visto no aparelho real: campos
            # preenchidos, nenhuma mensagem de erro, parado na tela de login.
            try:
                tree, package = await self._observe(rt)
                atual = navigation.classify(tree, package=package, locale=locale)
                if atual.form and atual.form.complete:
                    form = atual.form
            except DriverError:
                pass                                       # sem a releitura, segue com as coordenadas iniciais

            await self._fill_password(rt, form, cred["secret_ref"])
        except SensitiveInputUnavailable as exc:
            # Não é falha de credencial: nem a senha foi enviada, nem o Instagram foi consultado. Contar aqui
            # gastava o teto de tentativas (e podia acionar o cooldown) por um motivo de infraestrutura do
            # PRÓPRIO backend — achado #105. O perfil grava o motivo (o painel hoje não explicava por que não
            # conectou) e a sessão fica `auth_required`, não silenciosa.
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=str(exc),
                                          stage="sensitive_channel_blocked")
            self._save(profile_id, rt.id, SessionStatus.auth_required, detail=str(exc))
            return AuthResult(Outcome.RETRYABLE, str(exc), session_status=SessionStatus.auth_required)
        except SensitiveInputError as exc:
            # Mensagem fixa por construção: nunca carrega o que foi digitado.
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=str(exc),
                                          stage="fill_failed")
            self._count_failure(profile_id, rt.id)
            return AuthResult(Outcome.RETRYABLE, str(exc))
        except (DriverError, KeyError) as exc:
            detail = f"não foi possível preencher o formulário: {type(exc).__name__}"
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=detail,
                                          stage="fill_failed")
            self._count_failure(profile_id, rt.id)
            return AuthResult(Outcome.RETRYABLE, detail)

        # Envio: registrado ANTES de acontecer. Depois disso, timeout nunca autoriza repetir.
        self.repo.finish_auth_attempt(profile_id, attempt, outcome="", detail=None, stage="submitting")
        fired = True
        try:
            await self._tap(rt, *form.submit.center)
        except DriverError as exc:
            if not exc.effect_possible:
                fired = False
            log.warning("%s: erro ao tocar em Entrar (efeito possível=%s)", rt.id, exc.effect_possible)
        if not fired:
            detail = "o toque em Entrar não chegou ao aparelho; nada foi enviado"
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=detail,
                                          stage="submit_not_delivered")
            self._count_failure(profile_id, rt.id)
            return AuthResult(Outcome.RETRYABLE, detail, attempted_login=True)

        verdict = await self._watch_after_submit(rt, username, locale)
        self._apply_verdict(profile_id, rt.id, attempt, verdict, username)
        return AuthResult(verdict.outcome, verdict.detail, verdict.observed_username,
                          self._status_for(verdict.outcome), attempted_login=True)

    async def _watch_after_submit(self, rt: Any, username: str, locale: str | None) -> Verdict:
        """Observa até a tela decidir. Nunca reenvia: só olha."""
        prazo = now().timestamp() + float(self.conf.submit_wait_s)
        ultimo = Verdict(Outcome.UNCERTAIN, "a tela não mudou depois do envio")
        while now().timestamp() < prazo:
            await asyncio.sleep(2.0)
            try:
                tree, package = await self._observe(rt)
            except DriverError:
                continue
            ultimo = classify_after_submit(tree, package=package, expected_username=username, locale=locale)
            if ultimo.outcome is not Outcome.UNCERTAIN:
                return ultimo
        return ultimo

    # ------------------------------------------------------------------ conta errada
    async def _wrong_account(self, rt: Any, profile_id: str, esperado: str, observado: str,
                             locale: str | None) -> AuthResult:
        # Achado #115: a troca automática nunca foi implementada (o seletor de contas do app nunca era operado)
        # e a configuração que a prometia não aparecia em lugar nenhum fora do código — sugeria um recurso que
        # não existia. Conta errada é SEMPRE intervenção humana; nenhum caminho digita senha nem troca de conta
        # sozinho aqui.
        detail = (f"a conta aberta é @{observado}, e a esperada é @{esperado}. A troca de conta é sempre manual — "
                  "assuma o controle do aparelho e faça login na conta certa (ou 'Sair da conta', que apaga os "
                  "dados do app).")
        self._save(profile_id, rt.id, SessionStatus.wrong_account, observed=observado, detail=detail)
        self.bus.emit("log", f"{rt.id}: {detail}", level="warn", instance_id=rt.id)
        return AuthResult(Outcome.WRONG_ACCOUNT, detail, observado, SessionStatus.wrong_account)

    # ------------------------------------------------------------------ persistência e limites
    def _apply_verdict(self, profile_id: str, instance_id: str, attempt: int, verdict: Verdict,
                       username: str) -> None:
        # A etapa nomeia o diálogo genérico de erro: é o que a fase local filtra no histórico (sem senha, sem token).
        etapa = "login_error_dialog" if verdict.detail.startswith(LOGIN_ERROR_DETAIL) else "classified"
        self.repo.finish_auth_attempt(profile_id, attempt, outcome=verdict.outcome.value, detail=verdict.detail,
                                      stage=etapa)
        status = self._status_for(verdict.outcome)
        detalhe = f"{CHALLENGE_HELP} ({verdict.detail})" if verdict.outcome is Outcome.AUTH_CHALLENGE             else verdict.detail
        self._save(profile_id, instance_id, status, observed=verdict.observed_username,
                   verified_at=now_iso() if verdict.outcome is Outcome.SESSION_READY else None,
                   detail=detalhe)
        if verdict.outcome is Outcome.AUTH_CHALLENGE:
            self.bus.emit("log", f"{instance_id}: {CHALLENGE_HELP}", level="warn", instance_id=instance_id)
        if verdict.outcome is Outcome.SESSION_READY:
            self.repo.mark_credential(profile_id, status="active", failed_attempts=0, blocked_until=None)
            self.repo.touch_credential(profile_id)
            return
        if verdict.outcome is Outcome.INVALID_CREDENTIAL:
            # Senha comprovadamente errada: bloqueia nova tentativa automática até a credencial mudar.
            self.repo.mark_credential(profile_id, status="invalid", blocked_until=None)
            self.bus.emit("log", f"{instance_id}: credencial de @{username} recusada pelo Instagram; automação "
                                 "bloqueada até a senha ser alterada no portal", level="error",
                          instance_id=instance_id)
            return
        # Daqui para baixo, a SENHA JÁ FOI ENVIADA e o desfecho não foi sucesso. Todo caso conta para o teto — não
        # só o RETRYABLE. Contar apenas ele deixava o único freio contra bloquear a conta sem efeito no caminho que
        # importa: um modo de falha que se repete (UNCERTAIN, desafio, conta errada) gerava envios reais sem limite.
        # Medido: seis logins reais num mesmo perfil em ~4h30 com o contador parado em 1 de 3.
        self._count_failure(profile_id, instance_id)

    def _count_failure(self, profile_id: str, instance_id: str) -> None:
        """Toda falha repetível conta para o teto. Sem isso, um erro que se repete viraria laço infinito de login."""
        cred = self.repo.credential_row(profile_id)
        if cred is None:
            return
        falhas = (cred["failed_attempts"] or 0) + 1
        if falhas >= int(self.conf.max_auth_attempts):
            espera = now().timestamp() + float(self.conf.auth_cooldown_s)
            self.repo.mark_credential(profile_id, status="active", failed_attempts=falhas, blocked_until=_iso(espera))
            self.bus.emit("log", f"{instance_id}: {falhas} tentativas de login sem sucesso; aguardando "
                                 f"{self.conf.auth_cooldown_s}s antes de tentar de novo", level="warn",
                          instance_id=instance_id)
        else:
            self.repo.mark_credential(profile_id, status="active", failed_attempts=falhas, blocked_until=None)

    def _needs_person(self, profile_id: str, instance_id: str) -> tuple[Outcome, str] | None:
        """Estado que só uma pessoa resolve: o agendador não insiste, para não virar laço nem bloquear a conta.
        A sessão é do par (conta, aparelho): a lida é a DESTE aparelho."""
        sess = self.repo.session_row(profile_id, instance_id)
        if sess is None:
            return None
        if sess["status"] == SessionStatus.auth_challenge.value:
            return Outcome.AUTH_CHALLENGE, (sess["detail"] or "o Instagram exige confirmação adicional")
        if sess["status"] == SessionStatus.wrong_account.value:
            return Outcome.WRONG_ACCOUNT, (sess["detail"] or "o aparelho está logado em outra conta")
        return None

    def _challenge(self, profile_id: str, instance_id: str, motivo: str) -> AuthResult:
        detail = CHALLENGE_HELP
        self._save(profile_id, instance_id, SessionStatus.auth_challenge, detail=detail)
        self.bus.emit("log", f"{instance_id}: {detail} ({motivo})", level="warn", instance_id=instance_id)
        return AuthResult(Outcome.AUTH_CHALLENGE, detail, session_status=SessionStatus.auth_challenge)

    def _blocked_reason(self, profile_id: str) -> str | None:
        cred = self.repo.credential_row(profile_id)
        if cred is None:
            return "não há credencial cadastrada para este perfil"
        if cred["consent_at"] is None:
            # ADR-040: o consentimento é por conta e vale para o provedor como para o `type_secret`. Sem a marca, a
            # senha fica guardada e não é digitada por ninguém.
            return ("a senha guardada ainda não tem o consentimento para a automação digitá-la; marque-o na conta "
                    "do perfil")
        if cred["status"] == "invalid":
            return ("a senha guardada foi recusada pelo Instagram; altere-a no portal para liberar a autenticação "
                    "automática")
        ate = parse_iso(cred["blocked_until"])
        if ate and ate > now():
            return f"aguardando o intervalo entre tentativas (até {cred['blocked_until']})"
        return None

    def _save(self, profile_id: str, instance_id: str, status: SessionStatus, *, observed: str | None = None,
              verified_at: str | None = None, detail: str | None = None, reobserved: bool = False) -> None:
        anterior = self.repo.session_row(profile_id, instance_id)
        self.repo.set_session(profile_id, status=status, instance_id=instance_id, observed_username=observed,
                              verified_at=verified_at, detail=detail, reobserved=reobserved)
        if status is SessionStatus.session_ready:
            self.repo.update_profile(profile_id, {"last_verified_at": now_iso()})
        if status is SessionStatus.auth_challenge:
            bloquear_por_desafio(self.repo, self.bus, profile_id=profile_id, instance_id=instance_id,
                                 anterior_status=anterior["status"] if anterior is not None else None, detail=detail,
                                 app_label=ROTULO)
        emit_needs_person_change(self.bus, profile_id=profile_id, instance_id=instance_id, status=status,
                                 anterior_status=anterior["status"] if anterior is not None else None,
                                 detail=detail)

    @staticmethod
    def _status_for(outcome: Outcome) -> SessionStatus:
        return {
            Outcome.SESSION_READY: SessionStatus.session_ready,
            Outcome.INVALID_CREDENTIAL: SessionStatus.auth_required,
            Outcome.AUTH_CHALLENGE: SessionStatus.auth_challenge,
            Outcome.WRONG_ACCOUNT: SessionStatus.wrong_account,
        }.get(outcome, SessionStatus.unknown)

    # ------------------------------------------------------------------ operações no aparelho
    async def _ensure_automation(self, rt: Any) -> bool:
        for tentativa in range(AUTOMATION_TRIES):
            if await self.devices.ensure_automation(rt):
                return True
            if tentativa < AUTOMATION_TRIES - 1:
                await asyncio.sleep(AUTOMATION_WAIT_S)
        return False

    async def _open_app(self, rt: Any) -> None:
        try:
            await rt.executor.run(rt.adb.start_app, self.conf.package, None, timeout=60, label="abrir Instagram")
        except Exception:  # noqa: BLE001 - abrir pode falhar; a classificação da tela decide o que fazer
            log.warning("%s: não foi possível abrir %s", rt.id, self.conf.package)
        # Sem esperar o app aparecer, uma abertura a frio seria classificada como "outro app em primeiro plano" e a
        # conexão sairia incerta sem motivo real. Se não aparecer no prazo, a classificação diz o que está na tela.
        if not await wait_for_focus(rt, self.conf.package, deadline_s=float(self.conf.open_timeout_s),
                                    poll_s=self.focus_poll_s):
            log.warning("%s: %s não chegou ao primeiro plano em %.0f s", rt.id, self.conf.package,
                        float(self.conf.open_timeout_s))
        await asyncio.sleep(float(self.conf.settle_s))

    async def _observe(self, rt: Any) -> tuple[Any, str | None]:
        # Só árvore e pacote: o login determinístico nunca manda imagem ao modelo, então não há screencap nem
        # codificação a pagar aqui (contrato C1 do adendo v0.20: árvore primeiro, imagem só quando pedida).
        obs = await self.devices.observe(rt, timeout=float(self.conf.verify_timeout_s), imagem=False)
        return obs.tree, obs.package

    async def _locale(self, rt: Any) -> str | None:
        try:
            return await rt.executor.run(rt.adb.getprop, "ro.product.locale", timeout=15, label="idioma")
        except Exception:  # noqa: BLE001
            return None

    async def _tap(self, rt: Any, x: int, y: int) -> None:
        await rt.executor.run(rt.io.tap, x, y, timeout=30, label="toque")

    async def _fill_username(self, rt: Any, form: Any, valor: str, locale: str | None) -> bool:
        """Preenche o campo de usuário e CONFERE o que ficou lá. Devolve False se não bater — e aí nada é enviado.

        Tenta mais de uma vez de propósito. `mobile: type` digita no elemento em FOCO, e o campo do Instagram tem
        variantes: quando ele está vazio com placeholder (a que aparece depois de reinstalar o app), o primeiro
        toque às vezes não foca e a digitação cai no vazio — o login abortava em "username_mismatch" com o campo em
        branco. A cada tentativa a tela é relida, então a posição usada é a ATUAL (o teclado sobe e empurra tudo).
        A conferência continua valendo para todas: só se envia com o campo exatamente igual ao esperado.
        """
        esperado = valor.strip().lstrip("@").lower()
        for tentativa in range(FILL_TRIES):
            alvo = form.username
            if alvo is None:
                return False
            await self._tap(rt, *alvo.center)
            await rt.executor.run(lambda: rt.io.type_text(valor, clear_first=True), timeout=30, label="usuário")
            await asyncio.sleep(0.6)

            tree, package = await self._observe(rt)
            atual = navigation.login_form(tree, locale)
            if atual is None or atual.username is None:
                return False
            if (atual.username.text or "").strip().lstrip("@").lower() == esperado:
                return True
            if tentativa + 1 < FILL_TRIES:
                log.warning("%s: o usuário não ficou no campo (tentativa %d); relendo a tela e repetindo",
                            rt.id, tentativa + 1)
            form = atual                      # posições novas: a tela pode ter subido com o teclado
        return False

    async def _fill_password(self, rt: Any, form: Any, secret_ref: str) -> None:
        """A senha só existe entre o cofre e o driver, por um caminho que não gera ação nem histórico."""
        async def observe_tree() -> Any:
            tree, _ = await self._observe(rt)
            return tree

        def locate(tree: Any) -> Any:
            return navigation.password_field(tree)

        await self.sensitive.fill(call=rt.executor.run, io=rt.io, observe=observe_tree, locate=locate,
                                  secret=lambda: self.secrets.get_secret(secret_ref))


def _iso(timestamp: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{int(timestamp % 1 * 1000):03d}Z"
