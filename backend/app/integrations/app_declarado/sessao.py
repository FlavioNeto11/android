"""Sessão de um app declarado: login e conferência da conta, código determinístico, fora do laço da IA (ADR-052).

É o motor de sessão de TODO app com conta gerenciada. O que é de cada app — pacote, rótulo, sinais do botão de entrar
e do "agora não", o que dispensa uma tela benigna, a aba de perfil, a tabela de desfechos depois do envio, os textos
de ajuda e os ajustes padrão — vem do conhecimento declarado (`conhecimento.ConhecimentoDeSessao`, lido de
`app/conhecimento/apps/<pacote>/`). Este módulo não conhece app nenhum.

Por que fora do laço da IA: o executor para em qualquer tela com campo de senha, e o ator é instruído a nunca digitar
credencial. Este motor fala direto com o driver, pela fila exclusiva do aparelho, e a senha passa só pelo canal de
entrada sensível — que não gera argumento de ação nem histórico para o modelo (ADR-040: a credencial que a pessoa
guardou, com o consentimento dela, só no app daquela conta).

O que ele nunca faz: repetir o envio por causa de timeout, tentar de novo depois de senha comprovadamente errada,
seguir com a conta errada, ou tentar resolver um desafio de segurança (ADR-009/ADR-029: desafio, 2FA e CAPTCHA são
da pessoa).

Ler a conta é o que separa "o app abriu" de "a conta certa está aberta": nenhuma ação social acontece sem isso
confirmado, e conta errada nunca continua em silêncio. Nunca se presume sucesso pelo retorno do Appium: observa-se de
novo e classifica-se pelo que está na tela.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from ...automation import conhecimento_de_telas as telas
from ...automation.conhecimento_de_telas import TelaReconhecida
from ...automation.driver import DriverError
from ...automation.hierarchy import UiElement, UiTree
from ...devices.installer import LAUNCH_POLL_S, wait_for_focus
from ...models import SessionStatus
from ...modules.identity.application.session_rules import bloquear_por_desafio, emit_needs_person_change
from ...security.sensitive_input import SensitiveInputError, SensitiveInputUnavailable
from ...util import now, now_iso, parse_iso
from .conhecimento import CONFERIR_CONTA, Ajustes, ConhecimentoDeSessao
from .formulario import LoginForm

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from ...config import Config
    from ...devices.manager import DeviceManager, DeviceRuntime
    from ...events import EventBus
    from ...security.secret_store import SecretStore
    from ...security.sensitive_input import SensitiveInputChannel
    from ...social.repository import SocialRepository

# `poc.conta`, e não `poc.sessao`: esse é o logger da sessão do PAINEL (`security/sessions.py`, login do dono).
log = logging.getLogger("poc.conta")

AUTOMATION_TRIES = 3
AUTOMATION_WAIT_S = 8.0
FILL_TRIES = 3                # tentativas de pôr o usuário no campo; só se envia com o valor conferido
#: Intervalo entre as leituras depois do envio. Só observa; nunca reenvia.
OBSERVAR_DEPOIS_DO_ENVIO_S = 2.0
#: Tipos de tela (vocabulário de `automation/conhecimento_de_telas.py`) que só uma pessoa resolve.
TIPOS_DE_DESAFIO = frozenset({"desafio", "dois_fatores"})
#: Começo do aviso que ESTE motor põe no cartão do aparelho quando o app não fica na frente. É por ele que o motor
#: reconhece o aviso como seu: só ocupa o cartão vazio ou o que já é dele, e só tira o que é dele.
AVISO_FORA_DA_FRENTE = "App fora do primeiro plano"


class Outcome(StrEnum):
    SESSION_READY = "session_ready"            # conta certa aberta
    INVALID_CREDENTIAL = "invalid_credential"  # a tela disse que a senha está errada
    AUTH_CHALLENGE = "auth_challenge"          # 2FA, captcha, confirmação: só uma pessoa resolve
    WRONG_ACCOUNT = "wrong_account"            # abriu, mas é outra conta
    RETRYABLE = "retryable"                    # falhou antes de qualquer efeito; pode tentar de novo
    UNCERTAIN = "uncertain"                    # não deu para saber; NÃO repete sozinho

    @property
    def terminal(self) -> bool:
        """Desfecho que nunca gera nova tentativa automática — é o que evita bloquear a conta."""
        return self in (Outcome.INVALID_CREDENTIAL, Outcome.AUTH_CHALLENGE, Outcome.WRONG_ACCOUNT)


@dataclass(slots=True)
class Verdict:
    """O que a tela disse depois do envio. `etapa` é o nome gravado na tentativa (a fase local filtra por ele)."""

    outcome: Outcome
    detail: str
    observed_username: str | None = None
    tela: str = telas.DESCONHECIDA
    etapa: str = "classified"


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


@dataclass(slots=True)
class AccountCheck:
    observed: str | None
    matches: bool
    detail: str
    outro_app: bool = False          # a leitura terminou com OUTRO app na frente (o nosso caiu ou não voltou)


Observar = Callable[[], Awaitable[tuple[UiTree, str | None]]]
Tocar = Callable[[int, int], Awaitable[None]]


def _nome_da_tela(r: TelaReconhecida) -> str:
    """O nome da tela no diagnóstico. A não reconhecida sai como `unknown`, a grafia que o detalhe das tentativas e o
    log sempre gravaram (antes do motor genérico o nome vinha de um enum do app, e a fase local lê esse histórico)."""
    return "unknown" if r.tela == telas.DESCONHECIDA else r.tela


def _texto_da_tela(tree: UiTree) -> str:
    return "\n".join(f"{e.text} {e.desc}".strip() for e in tree.elements if e.text or e.desc)


# ---------------------------------------------------------------------------------------------------- desfechos
def classificar_depois_do_envio(k: ConhecimentoDeSessao, tree: UiTree, *, package: str | None,
                                expected_username: str, locale: str | None = None) -> Verdict:
    """Lê a tela depois do envio e decide pela tabela declarada (`depois_do_envio`): a primeira regra que casar vence.

    Nunca se repete o envio por causa de timeout — só uma falha COMPROVADA antes de qualquer efeito autoriza nova
    tentativa, e essa não passa por aqui. `expected_username` é comparado sem diferenciar maiúsculas.
    """
    sig = k.telas.sinais_de(locale)
    texto = _texto_da_tela(tree)
    r = k.reconhecer(tree, package=package, locale=locale)
    for regra in k.depois_do_envio:
        if regra.sinal is not None:
            casou = bool(sig[regra.sinal].search(texto))
        elif regra.tipos:
            casou = r.tipo in regra.tipos
        else:
            casou = r.tela in regra.telas
        if not casou:
            continue
        if regra.desfecho == CONFERIR_CONTA:
            return _conferir_conta_depois_do_envio(k, tree, r, expected_username)
        detalhe = f"{regra.detalhe} (tela: {_nome_da_tela(r)})" if regra.anexar_tela else regra.detalhe
        return Verdict(Outcome(regra.desfecho), detalhe, tela=r.tela, etapa=regra.etapa)
    return Verdict(Outcome.UNCERTAIN, f"não foi possível classificar a tela ({r.razao})", tela=r.tela)


def _conferir_conta_depois_do_envio(k: ConhecimentoDeSessao, tree: UiTree, r: TelaReconhecida,
                                    esperado: str) -> Verdict:
    observado = k.conta_observada(tree)
    if observado and observado.lower() != esperado.lower():
        return Verdict(Outcome.WRONG_ACCOUNT, f"a conta aberta é @{observado}, e a esperada é @{esperado}",
                       observed_username=observado, tela=r.tela)
    if observado:
        return Verdict(Outcome.SESSION_READY, f"@{observado} confirmado na tela", observed_username=observado,
                       tela=r.tela)
    # Entrou no app mas a conta ainda não apareceu: quem confirma é a leitura pela aba de perfil, não este palpite.
    return Verdict(Outcome.UNCERTAIN, "o app abriu, mas a conta ainda não foi confirmada na tela", tela=r.tela)


# ---------------------------------------------------------------------------------------------------- conta aberta
async def ler_conta(k: ConhecimentoDeSessao, observe: Observar, tap: Tocar, *, expected: str,
                    locale: str | None) -> AccountCheck:
    """Tenta ler a conta; abre a aba de perfil declarada e lê de lá.

    `observe` devolve `(UiTree, package)`; `tap` recebe (x, y). Nada aqui digita nem toca em nada além de dispensas
    de recusa e da aba de perfil, que é navegação sem efeito externo.
    """
    # Depois de entrar, o app pode empilhar telas na frente: "Salvar dados de login?", dicas e passos de onboarding.
    # Nenhuma delas tem barra de perfil, então não adianta procurar a conta ali. O laço abre caminho: dispensa o que
    # estiver na frente, vai até a aba de perfil e só então lê.
    #
    # A identidade sai SÓ da tela de perfil declarada: numa tela de conteúdo, o mesmo campo de cabeçalho pode mostrar
    # o autor do que está em foco, e ler dali acusava "conta errada" na conta certa (caso real no `sessao.yaml` do
    # primeiro app).
    tree: UiTree | None = None
    package: str | None = None
    espera = k.conta.espera_s
    for _ in range(k.conta.passos_max):
        tree, package = await observe()
        dispensar = k.botao_de_nao_salvar_login(tree, locale) or k.botao_de_dispensa(tree)
        if dispensar is not None:
            await tap(*dispensar.center)
            await asyncio.sleep(espera)
            continue

        # A conta só é lida DEPOIS de tocar na NOSSA aba de perfil. Ler o perfil em que se caiu não serve: o perfil
        # de outra pessoa tem o mesmo cabeçalho, e foi assim que @vinijr virou "conta errada" no aparelho de @felipe.
        alvo = k.aba_de_perfil(tree)
        if alvo is None:
            break
        await tap(*alvo)
        await asyncio.sleep(espera)
        tree, package = await observe()
        if k.reconhecer(tree, package=package, locale=locale).tela == k.conta.tela_de_perfil:
            achado = k.conta_no_cabecalho(tree)
            if achado:
                return _check(achado, expected)

    motivo, outro_app = "tela desconhecida", False
    if tree is not None:
        reconhecida = k.reconhecer(tree, package=package, locale=locale)
        motivo, outro_app = reconhecida.razao, reconhecida.outro_app
    return AccountCheck(None, False, f"a conta não pôde ser lida na tela ({motivo})", outro_app)


def _check(observed: str, expected: str) -> AccountCheck:
    ok = observed.lower() == expected.lower()
    return AccountCheck(observed, ok,
                        f"@{observed} confirmado na tela" if ok
                        else f"a conta aberta é @{observed}, e a esperada é @{expected}")


# ---------------------------------------------------------------------------------------------------- o provedor
class SessaoDeclarada:
    """`SessionProvider` de um app declarado: garante a conta do perfil aberta no aparelho, pelo conhecimento dele."""

    def __init__(self, conhecimento: ConhecimentoDeSessao, cfg: Config | None, devices: DeviceManager,
                 repo: SocialRepository, secrets: SecretStore, sensitive: SensitiveInputChannel, bus: EventBus):
        self.conhecimento = conhecimento
        self.cfg = cfg
        self.devices = devices
        self.repo = repo
        self.secrets = secrets
        self.sensitive = sensitive
        self.bus = bus
        self.focus_poll_s = LAUNCH_POLL_S          # intervalo entre leituras de foco enquanto o app abre

    @property
    def package(self) -> str:
        """O pacote que este provedor abre e confere (`SessionProvider.package`)."""
        return self.conhecimento.app

    @property
    def ajustes(self) -> Ajustes:
        """Os padrões do app com o que a instalação sobrescreveu (`Config.ajustes_de_sessao`). Lidos a CADA uso, não
        guardados na construção: a configuração é a mesma instância que o resto do backend lê e ajusta."""
        if self.cfg is None:
            return self.conhecimento.ajustes
        return self.conhecimento.ajustes.com(self.cfg.ajustes_de_sessao(self.package))

    # ------------------------------------------------------------------ entrada principal
    async def ensure_session(self, rt: DeviceRuntime, profile_id: str, *, force_login: bool = False,
                             automatic: bool = False, observe_only: bool = False) -> AuthResult:
        """Garante que a conta do perfil está aberta neste aparelho. Reaproveita sessão sempre que possível.

        `automatic=True` é a chamada do agendador: nela, estado que depende de pessoa (desafio de segurança, conta
        errada) nem chega a tocar no aparelho. A chamada explícita do portal sempre reobserva, que é como o usuário
        retoma depois de resolver o desafio à mão.

        `observe_only=True` é "Verificar conta": lê a tela e nada mais. Num aparelho deslogado ele PARA na tela de
        login em vez de autenticar — antes, quem apertava "Verificar" gastava uma tentativa de login real sem saber.
        """
        k = self.conhecimento
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
        estado = k.reconhecer(tree, package=package, locale=locale)

        # Depois de entrar, o app pode intercalar dicas e passos de onboarding que o tapam. São benignas e o botão
        # usado (só de RECUSA, pelo dado) não concede nada — mas enquanto estiverem na frente, a tela não é
        # classificável e a conta não tem como ser lida. Dispensa no máximo algumas, para não virar laço.
        for _ in range(k.dispensa.intersticiais_max):
            if estado.tela != telas.DESCONHECIDA:
                break
            botao = k.botao_de_dispensa(tree)
            if botao is None:
                break
            await self._tap(rt, *botao.center)
            await asyncio.sleep(float(self.ajustes.settle_s))
            tree, package = await self._observe(rt)
            estado = k.reconhecer(tree, package=package, locale=locale)

        # Fora do estado conhecido (conversa aberta, post, comentários, busca) ou numa tela desconhecida: volta ao
        # estado que o conhecimento declara ANTES de concluir qualquer coisa. Execução e31953: o app retomou uma
        # conversa do perfil, a tela não casava com nenhum sinal e a checagem chamou uma pessoa em 1 minuto — a
        # conta estava logada o tempo todo. Voltar e reabrir o app não têm efeito externo. Intersticial não entra:
        # tem tratamento próprio (a leitura da conta o dispensa).
        passos: list[str] = []
        if estado.tela == telas.DESCONHECIDA or (k.telas.autenticada(estado.tela)
                                                 and not k.telas.em_casa(estado.tela)
                                                 and estado.tipo != "intersticial"):
            async def voltar() -> None:
                await rt.executor.run(rt.io.press_key, "back", timeout=30, label="voltar")
                await asyncio.sleep(float(self.ajustes.settle_s))

            tree, package, estado, passos = await telas.voltar_ao_estado_conhecido(
                k.telas, observar=lambda: self._observe(rt), voltar=voltar, reabrir=lambda: self._open_app(rt),
                reconhecer=lambda t, p: k.reconhecer(t, package=p, locale=locale))
            if passos:
                log.info("%s: estado conhecido do app — %s → %s", rt.id, " → ".join(passos), _nome_da_tela(estado))
        # O app esteve na frente nesta chamada? "voltar" só é dado sobre uma tela DELE (fora de casa), então quem
        # termina no launcher depois de um "voltar" viu o app — a tela dele é que não foi reconhecida, e o voltar saiu
        # dela (a raiz). Só a outra forma de acabar no launcher é "o app não chegou ao primeiro plano".
        viu_o_app = not estado.outro_app or "voltar" in passos

        # 1) Já autenticado? Reaproveitar é o caminho normal: ninguém digita senha à toa.
        if k.telas.autenticada(estado.tela) and not force_login:
            check = await ler_conta(k, lambda: self._observe(rt), lambda x, y: self._tap(rt, x, y),
                                    expected=username, locale=locale)
            # Antes de conta certa/errada: `outro_app` só vem quando nada foi lido. E o aviso do cartão só sai DEPOIS
            # da leitura — tirá-lo ao ver o app e repô-lo quando ele cai publicaria "voltou / caiu" a cada tentativa.
            if check.outro_app:
                return self._fora_do_primeiro_plano(rt, profile_id,
                                                    f"{check.detail}; o app saiu da frente durante a leitura")
            self._app_voltou_a_frente(rt)
            if check.matches:
                self._save(profile_id, rt.id, SessionStatus.session_ready, observed=check.observed,
                           verified_at=now_iso(), detail=check.detail)
                return AuthResult(Outcome.SESSION_READY, check.detail, check.observed, SessionStatus.session_ready)
            if check.observed:
                return await self._wrong_account(rt, profile_id, username, check.observed, locale)
            # entrou, mas a conta não pôde ser lida: não é sucesso nem motivo para digitar senha
            self._save(profile_id, rt.id, SessionStatus.unknown, detail=check.detail, reobserved=True)
            return AuthResult(Outcome.UNCERTAIN, check.detail, session_status=SessionStatus.unknown)
        if viu_o_app:
            self._app_voltou_a_frente(rt)

        if estado.tipo in TIPOS_DE_DESAFIO:
            return self._challenge(profile_id, rt.id, estado.razao)

        # 2) Deslogado: fazer login.
        if estado.tipo != "login":
            # Revisão do pacote: o launcher só é "fora do primeiro plano" quando o app NÃO esteve na frente. Uma tela
            # do próprio app não reconhecida, da qual o voltar sai do app (a raiz — um feed cujos sinais mudaram numa
            # atualização), termina no launcher e é exatamente o achado #104: tem de somar até o teto, senão a porta
            # reabre o app e relê a tela a cada tick, para sempre.
            if estado.outro_app and not viu_o_app:
                return self._fora_do_primeiro_plano(
                    rt, profile_id, f"{self.conhecimento.rotulo} não chegou ao primeiro plano ({estado.razao})")
            detail = (f"{self.conhecimento.rotulo} não voltou ao estado conhecido: o voltar saiu do app "
                      f"({estado.razao})" if estado.outro_app
                      else f"o app não está na tela de login nem autenticado ({estado.razao})")
            self._save(profile_id, rt.id, SessionStatus.unknown, detail=detail, reobserved=True)
            return AuthResult(Outcome.UNCERTAIN, detail)

        if observe_only:
            # "Verificar conta" só observa. Autenticar aqui gastaria uma tentativa de login REAL num clique que o
            # painel anuncia como leitura — e é o botão sugerido logo depois de um desafio resolvido à mão.
            detail = "o aparelho está deslogado; use Conectar para autenticar"
            self._save(profile_id, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

        form = estado.formulario if isinstance(estado.formulario, LoginForm) else None
        return await self._login(rt, profile_id, username, form, locale)

    # ------------------------------------------------------------------ login
    async def _login(self, rt: DeviceRuntime, profile_id: str, username: str, form: LoginForm | None,
                     locale: str | None) -> AuthResult:
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
                atual = self.conhecimento.reconhecer(tree, package=package, locale=locale).formulario
                if isinstance(atual, LoginForm) and atual.complete:
                    form = atual
            except DriverError:
                pass                                       # sem a releitura, segue com as coordenadas iniciais

            await self._fill_password(rt, cred["secret_ref"])
        except SensitiveInputUnavailable as exc:
            # Não é falha de credencial: nem a senha foi enviada, nem o app foi consultado. Contar aqui gastava o
            # teto de tentativas (e podia acionar o cooldown) por um motivo de infraestrutura do PRÓPRIO backend —
            # achado #105. O perfil grava o motivo (o painel não explicava por que não conectou) e a sessão fica
            # `auth_required`, não silenciosa.
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
        botao: UiElement = form.submit  # type: ignore[assignment]  # `form.complete` garante o botão
        try:
            await self._tap(rt, *botao.center)
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

    async def _watch_after_submit(self, rt: DeviceRuntime, username: str, locale: str | None) -> Verdict:
        """Observa até a tela decidir. Nunca reenvia: só olha."""
        prazo = now().timestamp() + float(self.ajustes.submit_wait_s)
        ultimo = Verdict(Outcome.UNCERTAIN, "a tela não mudou depois do envio")
        while now().timestamp() < prazo:
            await asyncio.sleep(OBSERVAR_DEPOIS_DO_ENVIO_S)
            try:
                tree, package = await self._observe(rt)
            except DriverError:
                continue
            ultimo = classificar_depois_do_envio(self.conhecimento, tree, package=package,
                                                 expected_username=username, locale=locale)
            if ultimo.outcome is not Outcome.UNCERTAIN:
                return ultimo
        return ultimo

    # ------------------------------------------------------------------ conta errada
    async def _wrong_account(self, rt: DeviceRuntime, profile_id: str, esperado: str, observado: str,
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
        textos = self.conhecimento.textos
        # A etapa nomeia o diálogo genérico de erro (declarada na regra): é o que a fase local filtra no histórico
        # (sem senha, sem token).
        self.repo.finish_auth_attempt(profile_id, attempt, outcome=verdict.outcome.value, detail=verdict.detail,
                                      stage=verdict.etapa)
        status = self._status_for(verdict.outcome)
        detalhe = f"{textos.desafio} ({verdict.detail})" if verdict.outcome is Outcome.AUTH_CHALLENGE \
            else verdict.detail
        self._save(profile_id, instance_id, status, observed=verdict.observed_username,
                   verified_at=now_iso() if verdict.outcome is Outcome.SESSION_READY else None,
                   detail=detalhe)
        if verdict.outcome is Outcome.AUTH_CHALLENGE:
            self.bus.emit("log", f"{instance_id}: {textos.desafio}", level="warn", instance_id=instance_id)
        if verdict.outcome is Outcome.SESSION_READY:
            self.repo.mark_credential(profile_id, status="active", failed_attempts=0, blocked_until=None)
            self.repo.touch_credential(profile_id)
            return
        if verdict.outcome is Outcome.INVALID_CREDENTIAL:
            # Senha comprovadamente errada: bloqueia nova tentativa automática até a credencial mudar.
            self.repo.mark_credential(profile_id, status="invalid", blocked_until=None)
            self.bus.emit("log", f"{instance_id}: {textos.credencial_recusada.format(usuario=username)}",
                          level="error", instance_id=instance_id)
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
        ajustes = self.ajustes
        falhas = (cred["failed_attempts"] or 0) + 1
        if falhas >= int(ajustes.max_auth_attempts):
            espera = now().timestamp() + float(ajustes.auth_cooldown_s)
            self.repo.mark_credential(profile_id, status="active", failed_attempts=falhas, blocked_until=_iso(espera))
            self.bus.emit("log", f"{instance_id}: {falhas} tentativas de login sem sucesso; aguardando "
                                 f"{ajustes.auth_cooldown_s}s antes de tentar de novo", level="warn",
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
            return Outcome.AUTH_CHALLENGE, (sess["detail"] or self.conhecimento.textos.desafio_pendente)
        if sess["status"] == SessionStatus.wrong_account.value:
            return Outcome.WRONG_ACCOUNT, (sess["detail"] or "o aparelho está logado em outra conta")
        return None

    def _challenge(self, profile_id: str, instance_id: str, motivo: str) -> AuthResult:
        detail = self.conhecimento.textos.desafio
        self._save(profile_id, instance_id, SessionStatus.auth_challenge, detail=detail)
        self.bus.emit("log", f"{instance_id}: {detail} ({motivo})", level="warn", instance_id=instance_id)
        return AuthResult(Outcome.AUTH_CHALLENGE, detail, session_status=SessionStatus.auth_challenge)

    def _fora_do_primeiro_plano(self, rt: DeviceRuntime, profile_id: str, detail: str) -> AuthResult:
        """OUTRO app na frente (o launcher, quase sempre): o app não chegou ao primeiro plano, ou caiu no meio da
        leitura da conta.

        Não é "tela não reconhecida": a tela que está na frente não é do app — ou nenhuma tela dele foi lida, ou a
        que foi lida era de casa e o app caiu depois. Contar isso no `unknown_streak` (achado #104) transformava
        lentidão do aparelho em caso de pessoa — r-20260928195344-02ee9e: o convidado do android-06 (2 vCPU) saturado
        demorava a trazer o app à frente, e três leituras do launcher bloqueavam o perfil pedindo que alguém
        "identificasse a tela". A sessão fica `unknown` SEM somar (a sequência de telas do app é interrompida), a
        porta tenta de novo sozinha, e o caso vai para a saúde do APARELHO: o cartão (`attention`) e o histórico —
        este uma vez por entrada no estado, não a cada tentativa.

        O cartão segue a regra dos outros avisos dele (pressão do convidado, relógio, internet): só ocupa o cartão
        vazio ou o que já é deste aviso. No android-06 a pressão do convidado costuma estar lá, e ela é a causa — o
        aviso daqui não a atropela; o histórico registra do mesmo jeito.
        """
        anterior = self.repo.session_row(profile_id, rt.id)
        self._save(profile_id, rt.id, SessionStatus.unknown, detail=detail)
        if anterior is None or anterior["detail"] != detail:
            self.bus.emit("log", f"{rt.id}: {detail} — é o aparelho (lento ou o app caindo na abertura), não uma tela "
                                 "desconhecida; a conferência da conta tenta de novo sozinha", level="warn",
                          instance_id=rt.id)
        if rt.attention is None or rt.attention.startswith(AVISO_FORA_DA_FRENTE):
            self.devices.marcar_atencao(rt, f"{AVISO_FORA_DA_FRENTE}: {detail}. A conferência da conta tenta de novo "
                                            "sozinha; se continuar, o aparelho está lento demais para o app ou o app "
                                            "cai ao abrir — reinicie o aparelho.")
        return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.unknown)

    def _app_voltou_a_frente(self, rt: DeviceRuntime) -> None:
        """O app esteve na frente nesta chamada: o aviso "fora do primeiro plano" deste motor sai do cartão. Sem isto,
        UMA abertura lenta deixava o alarme no cartão para sempre (só um reinício o limpava). Aviso de outro assunto
        fica: não é deste motor."""
        if rt.attention is not None and rt.attention.startswith(AVISO_FORA_DA_FRENTE):
            rt.attention = None
            self.devices.publish(rt, f"{rt.id}: {self.conhecimento.rotulo} voltou a abrir na frente")

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
            return self.conhecimento.textos.senha_recusada
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
            # A regra do PERFIL (ADR-029), com o rótulo do app declarado nas mensagens.
            bloquear_por_desafio(self.repo, self.bus, profile_id=profile_id, instance_id=instance_id,
                                 anterior_status=anterior["status"] if anterior is not None else None, detail=detail,
                                 app_label=self.conhecimento.rotulo)
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
    async def _ensure_automation(self, rt: DeviceRuntime) -> bool:
        for tentativa in range(AUTOMATION_TRIES):
            if await self.devices.ensure_automation(rt):
                return True
            if tentativa < AUTOMATION_TRIES - 1:
                await asyncio.sleep(AUTOMATION_WAIT_S)
        return False

    async def _open_app(self, rt: DeviceRuntime) -> None:
        pacote, ajustes = self.package, self.ajustes
        try:
            await rt.executor.run(rt.adb.start_app, pacote, None, timeout=60, label=f"abrir {self.conhecimento.rotulo}")
        except Exception:  # noqa: BLE001 - abrir pode falhar; a classificação da tela decide o que fazer
            log.warning("%s: não foi possível abrir %s", rt.id, pacote)
        # Sem esperar o app aparecer, uma abertura a frio seria classificada como "outro app em primeiro plano" e a
        # conexão sairia incerta sem motivo real. Se não aparecer no prazo, a classificação diz o que está na tela.
        if not await wait_for_focus(rt, pacote, deadline_s=float(ajustes.open_timeout_s), poll_s=self.focus_poll_s):
            log.warning("%s: %s não chegou ao primeiro plano em %.0f s", rt.id, pacote, float(ajustes.open_timeout_s))
        await asyncio.sleep(float(ajustes.settle_s))

    async def _observe(self, rt: DeviceRuntime) -> tuple[UiTree, str | None]:
        # Só árvore e pacote: o login determinístico nunca manda imagem ao modelo, então não há screencap nem
        # codificação a pagar aqui (contrato C1 do adendo v0.20: árvore primeiro, imagem só quando pedida).
        obs = await self.devices.observe(rt, timeout=float(self.ajustes.verify_timeout_s), imagem=False)
        return obs.tree, obs.package

    async def _locale(self, rt: DeviceRuntime) -> str | None:
        try:
            idioma: str | None = await rt.executor.run(rt.adb.getprop, "ro.product.locale", timeout=15, label="idioma")
            return idioma
        except Exception:  # noqa: BLE001
            return None

    async def _tap(self, rt: DeviceRuntime, x: int, y: int) -> None:
        await rt.executor.run(rt.io.tap, x, y, timeout=30, label="toque")

    async def _fill_username(self, rt: DeviceRuntime, form: LoginForm, valor: str, locale: str | None) -> bool:
        """Preenche o campo de usuário e CONFERE o que ficou lá. Devolve False se não bater — e aí nada é enviado.

        Tenta mais de uma vez de propósito. `mobile: type` digita no elemento em FOCO, e há campo de usuário com
        variantes: quando ele está vazio com placeholder (a que apareceu depois de reinstalar o primeiro app operado),
        o primeiro toque às vezes não foca e a digitação cai no vazio — o login abortava em "username_mismatch" com o
        campo em branco. A cada tentativa a tela é relida, então a posição usada é a ATUAL (o teclado sobe e empurra
        tudo). A conferência continua valendo para todas: só se envia com o campo exatamente igual ao esperado.
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
            atual = self.conhecimento.formulario_de_login(tree, locale)
            if atual is None or atual.username is None:
                return False
            if (atual.username.text or "").strip().lstrip("@").lower() == esperado:
                return True
            if tentativa + 1 < FILL_TRIES:
                log.warning("%s: o usuário não ficou no campo (tentativa %d); relendo a tela e repetindo",
                            rt.id, tentativa + 1)
            form = atual                      # posições novas: a tela pode ter subido com o teclado
        return False

    async def _fill_password(self, rt: DeviceRuntime, secret_ref: str) -> None:
        """A senha só existe entre o cofre e o driver, por um caminho que não gera ação nem histórico."""
        async def observe_tree() -> UiTree:
            tree, _ = await self._observe(rt)
            return tree

        await self.sensitive.fill(call=rt.executor.run, io=rt.io, observe=observe_tree,
                                  locate=self.conhecimento.campo_de_senha,
                                  secret=lambda: self.secrets.get_secret(secret_ref))


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{int(timestamp % 1 * 1000):03d}Z"
