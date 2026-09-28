"""Recursos `account.binding` e `app.session` (design §11).

**`account.binding` (`bound`)**: há um perfil com vínculo ativo neste aparelho (`device_profile_bindings`) e, quando a
execução escolheu um perfil, é ele. Vincular é decisão de pessoa: divergência vira sempre `ask`, qualquer que seja o
`on_missing`.

**`app.session` (`{session: ready}`, com `account: bound_profile` opcional)**: a conta do perfil vinculado está
entrada no app, neste aparelho. A ordem é a da porta de sessão (`AppState._session_gate`):

1. perfil fora de `active` não recebe tarefa (ADR-029: o desafio bloqueia, e só pessoa reativa);
2. localidade trocada (os dados do perfil ficaram em outra máquina ou outro aparelho físico) é de pessoa, a menos que
   a política do perfil autorize reautenticar em outro lugar (`reauth_elsewhere`) — aí é um login;
3. `session_ready` NESTE aparelho e dentro da validade é o único "pronto". Vencida, de outro aparelho ou sem a conta
   lida na tela é `unknown`, e a resposta é `session.verify` (reler a tela, sem digitar nada — o `observe_only` de
   hoje). A porta atual, sem sessão verificada, já tenta o login; o plano relê primeiro, de propósito;
4. desafio de segurança, conta errada e tela não reconhecida `session_unknown_retry_cap` vezes seguidas são de pessoa
   (ADR-009: CAPTCHA, 2FA e desafio nunca se automatizam);
5. `auth_required` converge por `session.connect`, e só com credencial utilizável no cofre (ADR-025); sem ela, pessoa.

A sessão é da CONTA num aparelho (`account_sessions`, 049; ADR-040), com um vocabulário só para app com e sem
provedor. O Instagram tem provedor de sessão determinístico, e é ele quem grava a sessão da conta. Os demais apps não
têm login automático: a sessão é o que o operador (ou a IA, observando a tela) marcou, e nada a converge sozinho —
`unknown` ali fica sem ação.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from app.shared.resources import (UNSUPPORTED_DESIRED, Drift, DriftStatus, ObservedState, ResourceAction,
                                  ResourceKind, ResourceSpec, plan_by_rules)

#: (status, código, detalhe) → `Drift` do par que está sendo comparado.
_FazDrift = Callable[[DriftStatus, str, str], Drift]

BINDING = ResourceKind.account_binding
SESSION = ResourceKind.app_session
DESIRED_BOUND = "bound"
#: Chaves do desejado de `app.session` e o único valor que cada uma aceita.
SESSION_KEYS: dict[str, str] = {"session": "ready", "account": "bound_profile"}


class ProfileStatus(StrEnum):
    """`instagram_profiles.status` (008)."""

    active = "active"
    blocked = "blocked"
    disabled = "disabled"


class SessionStatus(StrEnum):
    """`models.SessionStatus`: a sessão de uma conta num aparelho (`account_sessions.status`, 049). Vocabulário
    ÚNICO para app com provedor (quem grava é o provedor) e sem (quem grava é a pessoa ou a IA olhando a tela):
    o antigo `logged_out` da 037 é `auth_required`; `needs_person` é o que só uma pessoa resolve sem ser desafio
    nem conta errada."""

    unknown = "unknown"
    auth_required = "auth_required"
    auth_challenge = "auth_challenge"
    wrong_account = "wrong_account"
    session_ready = "session_ready"
    needs_person = "needs_person"


#: O nome antigo do vocabulário dos apps sem provedor (037). É o MESMO enum desde a 049: uma sessão, um vocabulário.
AccountSessionStatus = SessionStatus


class CredentialState(StrEnum):
    #: Nenhuma credencial guardada para a conta.
    missing = "missing"
    #: Guardada, consentida e não recusada.
    usable = "usable"
    #: Recusada pela plataforma (`account_credentials.status = invalid`).
    invalid = "invalid"
    #: Guardada, mas a pessoa ainda não consentiu que a automação a digite (`consent_at` nulo; ADR-040).
    unconsented = "unconsented"


class SessionVerb(StrEnum):
    """Comandos de sessão que existem (`commands/despacho.APP_COMMAND_VERBS`)."""

    connect = "session.connect"
    verify = "session.verify"


VERB_RISKS: dict[str, str] = {
    SessionVerb.connect.value: ("autentica digitando a credencial do cofre pelo canal sensível (ADR-025); desafio, "
                                "2FA e CAPTCHA param com a pessoa (ADR-009)"),
    SessionVerb.verify.value: "só relê a tela do app; não digita nada",
}


class BindingCode(StrEnum):
    bound = "bound"
    unbound = "unbound"
    bound_to_other = "bound_to_other"


class SessionCode(StrEnum):
    ready = "ready"
    app_unknown = "app_unknown"
    no_profile = "no_profile"
    other_profile = "other_profile"
    profile_inactive = "profile_inactive"
    locality_moved = "locality_moved"
    relocated = "relocated"
    unobserved = "unobserved"
    other_device = "other_device"
    stale = "stale"
    account_unproven = "account_unproven"
    unrecognized_screen = "unrecognized_screen"
    challenge = "challenge"
    wrong_account = "wrong_account"
    logged_out = "logged_out"
    no_credential = "no_credential"
    credential_invalid = "credential_invalid"
    no_consent = "no_consent"
    no_account = "no_account"
    account_disabled = "account_disabled"
    account_logged_out = "account_logged_out"
    needs_person = "needs_person"
    account_unknown = "account_unknown"


_SESSION_CONVERGE: dict[str, str] = {c.value: SessionVerb.connect.value for c in (
    SessionCode.logged_out, SessionCode.relocated)}
_SESSION_OBSERVE: dict[str, str] = {c.value: SessionVerb.verify.value for c in (
    SessionCode.unobserved, SessionCode.other_device, SessionCode.stale, SessionCode.account_unproven)}


# ---------------------------------------------------------------------------------------------------- vínculo
@dataclass(frozen=True, slots=True, kw_only=True)
class BindingObserved(ObservedState):
    #: Perfil com vínculo ATIVO neste aparelho; `None` = nenhum.
    profile_id: str | None = None
    username: str | None = None
    bound_at: str | None = None

    def facts(self) -> tuple[tuple[str, str], ...]:
        if self.profile_id is None:
            return (("perfil vinculado", "nenhum"),)
        return (("perfil vinculado", f"{self.profile_id} (@{self.username or '?'})"),
                ("desde", self.bound_at or "?"))


def diff_account_binding(spec: ResourceSpec, observed: ObservedState) -> Drift:
    if spec.ref.kind is not BINDING or not isinstance(observed, BindingObserved):
        raise TypeError(f"diff de {BINDING.value} recebeu {spec.ref.kind.value}/{type(observed).__name__}")
    o, iid = observed, observed.target.instance_id
    querido = o.target.profile_id

    def drift(status: DriftStatus, code: str, detail: str) -> Drift:
        return Drift(spec=spec, target=o.target, status=status, code=code, detail=detail, observed=o)

    if spec.desired != DESIRED_BOUND:
        return drift(DriftStatus.unsupported, UNSUPPORTED_DESIRED,
                     f"{BINDING.value} só sabe perseguir '{DESIRED_BOUND}', e o pedido foi '{spec.desired_text()}'")
    if o.profile_id is None:
        return drift(DriftStatus.diverged, BindingCode.unbound,
                     f"nenhum perfil está vinculado a {iid}" + (f" (a execução pediu {querido})" if querido else "")
                     + "; vincular é decisão de pessoa")
    if querido is not None and o.profile_id != querido:
        return drift(DriftStatus.diverged, BindingCode.bound_to_other,
                     f"{iid} está vinculado a {o.profile_id}, e a execução pediu {querido}; revincular é decisão de "
                     "pessoa")
    return drift(DriftStatus.in_sync, BindingCode.bound, f"{iid} vinculado a {o.profile_id}")


def plan_account_binding(drift: Drift) -> list[ResourceAction]:
    return plan_by_rules(drift, kind=BINDING, converge={}, observe={}, always_ask=True)


# ---------------------------------------------------------------------------------------------------- sessão
@dataclass(frozen=True, slots=True)
class ProviderSession:
    """A sessão da conta de um app com provedor determinístico (`account_sessions` + a credencial da conta), já com
    as regras de tempo aplicadas por quem leu: `stale` (validade do "Conectado") e `unknown_capped` (teto do achado
    #104)."""

    #: `None` = sem linha (nunca verificada), ou valor que não é um estado conhecido.
    status: SessionStatus | None
    instance_id: str | None = None
    observed_username: str | None = None
    verified_at: str | None = None
    stale: bool = False
    unknown_capped: bool = False
    credential: CredentialState = CredentialState.missing


@dataclass(frozen=True, slots=True)
class AppAccount:
    """A conta do perfil num app sem provedor (`profile_accounts` + a sessão dela neste aparelho)."""

    active: bool
    #: `None` = sem sessão registrada neste aparelho, ou valor que não é um estado conhecido.
    session_status: SessionStatus | None
    verified_at: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionObserved(ObservedState):
    app_registered: bool
    #: Perfil com vínculo ativo neste aparelho.
    profile_id: str | None = None
    username: str | None = None
    #: `None` = valor que não é um estado conhecido.
    profile_status: ProfileStatus | None = None
    #: Os dados do perfil ficaram noutra máquina ou noutro aparelho físico (a comparação de `_porta_da_localidade`,
    #: feita aqui sem gravar nada).
    locality_moved: bool = False
    #: A política do perfil autoriza reautenticar noutro lugar (`offline_policy = reauth_elsewhere`).
    reauth_elsewhere: bool = False
    #: Preenchido quando o app tem provedor de sessão determinístico (hoje só o Instagram).
    provider: ProviderSession | None = None
    #: A conta do perfil neste app, quando NÃO há provedor. `None` = o perfil não tem conta aqui.
    account: AppAccount | None = None

    def facts(self) -> tuple[tuple[str, str], ...]:
        pares = [("perfil vinculado", f"{self.profile_id} (@{self.username or '?'})" if self.profile_id else "nenhum"),
                 ("status do perfil", self.profile_status.value if self.profile_status is not None else "-")]
        if self.locality_moved:
            pares.append(("localidade", "mudou desde o vínculo"))
        if (p := self.provider) is not None:
            pares += [("sessão", p.status.value if p.status is not None else "nunca verificada"),
                      ("aparelho da sessão", p.instance_id or "-"),
                      ("verificada em", (p.verified_at or "-") + (" (vencida)" if p.stale else "")),
                      ("credencial", p.credential.value)]
        elif (a := self.account) is not None:
            pares += [("conta no app", "ativa" if a.active else "desativada"),
                      ("sessão", a.session_status.value if a.session_status is not None else "desconhecida")]
        else:
            pares.append(("conta no app", "nenhuma"))
        return tuple(pares)


def _desejado_de_sessao(spec: ResourceSpec) -> bool:
    """`{session: ready}` e, opcionalmente, `account: bound_profile` — nada além disso."""
    mapa = spec.desired_map()
    return (not isinstance(spec.desired, str) and mapa.get("session") == SESSION_KEYS["session"]
            and all(SESSION_KEYS.get(k) == v for k, v in mapa.items()))


def _mesma_conta(observada: str | None, perfil: str | None) -> bool:
    def norma(u: str) -> str:
        return u.strip().lstrip("@").lower()
    return observada is not None and perfil is not None and norma(observada) == norma(perfil)


def diff_app_session(spec: ResourceSpec, observed: ObservedState) -> Drift:
    if spec.ref.kind is not SESSION or not isinstance(observed, SessionObserved):
        raise TypeError(f"diff de {SESSION.value} recebeu {spec.ref.kind.value}/{type(observed).__name__}")
    o, iid, app = observed, observed.target.instance_id, spec.ref.target

    def drift(status: DriftStatus, code: str, detail: str) -> Drift:
        return Drift(spec=spec, target=o.target, status=status, code=code, detail=detail, observed=o)

    if not app or not _desejado_de_sessao(spec):
        return drift(DriftStatus.unsupported, UNSUPPORTED_DESIRED,
                     f"{SESSION.value} precisa de um app alvo e de 'session=ready' (com 'account=bound_profile' "
                     f"opcional); pedido: alvo '{app or '-'}', '{spec.desired_text()}'")
    if not o.app_registered:
        return drift(DriftStatus.blocked, SessionCode.app_unknown, f"o app '{app}' não está cadastrado")
    if o.profile_id is None:
        return drift(DriftStatus.blocked, SessionCode.no_profile,
                     f"nenhum perfil vinculado a {iid}: sem perfil não há conta para entrar em {app}")
    if o.target.profile_id is not None and o.target.profile_id != o.profile_id:
        return drift(DriftStatus.blocked, SessionCode.other_profile,
                     f"{iid} está vinculado a {o.profile_id}, e a execução pediu {o.target.profile_id}")
    if o.profile_status is not ProfileStatus.active:
        estado = o.profile_status.value if o.profile_status is not None else "desconhecido"
        return drift(DriftStatus.blocked, SessionCode.profile_inactive,
                     f"o perfil @{o.username or o.profile_id} está '{estado}': nenhuma tarefa é despachada para ele "
                     "até uma pessoa reativá-lo")
    if (p := o.provider) is None:
        return _sessao_sem_provedor(o, app, drift)
    if o.locality_moved:
        if not o.reauth_elsewhere:
            return drift(DriftStatus.blocked, SessionCode.locality_moved,
                         f"os dados do perfil não estão em {iid} (mudou de máquina ou de aparelho), e a política dele "
                         "é esperar; reautenticar aqui é decisão de pessoa")
        return _login(p, SessionCode.relocated, drift,
                      f"os dados do perfil ficaram noutro lugar e a política autoriza reautenticar em {iid}")
    if p.status is None or p.status is SessionStatus.unknown:
        if p.unknown_capped:
            return drift(DriftStatus.blocked, SessionCode.unrecognized_screen,
                         f"a tela de {app} em {iid} não foi reconhecida em várias leituras seguidas; assuma o controle "
                         "para identificá-la")
        return drift(DriftStatus.unknown, SessionCode.unobserved,
                     f"a sessão do perfil em {app} ainda não foi verificada em {iid}")
    if p.status is SessionStatus.auth_challenge:
        return drift(DriftStatus.blocked, SessionCode.challenge,
                     f"desafio de segurança em {app}: só uma pessoa resolve (ADR-009/029)")
    if p.status is SessionStatus.wrong_account:
        return drift(DriftStatus.blocked, SessionCode.wrong_account, f"{iid} está com outra conta entrada em {app}")
    if p.status is SessionStatus.auth_required:
        return _login(p, SessionCode.logged_out, drift, f"a conta do perfil não está entrada em {app}")
    # session_ready
    if p.instance_id != iid:
        return drift(DriftStatus.unknown, SessionCode.other_device,
                     f"a sessão verificada é de {p.instance_id or 'outro aparelho'}, não de {iid}")
    if p.stale:
        return drift(DriftStatus.unknown, SessionCode.stale,
                     f"a verificação da sessão em {iid} passou da validade; a tela precisa ser relida")
    if "account" in spec.desired_map() and not _mesma_conta(p.observed_username, o.username):
        if p.observed_username is None:
            return drift(DriftStatus.unknown, SessionCode.account_unproven,
                         f"a sessão em {iid} não registrou qual conta estava na tela")
        return drift(DriftStatus.blocked, SessionCode.wrong_account,
                     f"a conta na tela de {app} é @{p.observed_username}, não a do perfil (@{o.username})")
    return drift(DriftStatus.in_sync, SessionCode.ready, f"sessão pronta em {app} em {iid}")


def _login(p: ProviderSession, code: SessionCode, drift: _FazDrift, motivo: str) -> Drift:
    """Entrar na conta converge só com credencial utilizável no cofre — guardada, consentida e não recusada
    (ADR-025/040); sem ela, é de pessoa."""
    if p.credential is CredentialState.usable:
        return drift(DriftStatus.diverged, code, f"{motivo}; o login usaria a credencial do cofre")
    if p.credential is CredentialState.invalid:
        return drift(DriftStatus.blocked, SessionCode.credential_invalid,
                     f"{motivo}, e a credencial guardada foi recusada; cadastre a senha de novo")
    if p.credential is CredentialState.unconsented:
        return drift(DriftStatus.blocked, SessionCode.no_consent,
                     f"{motivo}, e a senha guardada ainda não tem o consentimento para a automação digitá-la; "
                     "marque-o na conta")
    return drift(DriftStatus.blocked, SessionCode.no_credential,
                 f"{motivo}, e não há credencial guardada para a conta; cadastre a senha no portal")


def _sessao_sem_provedor(o: SessionObserved, app: str, drift: _FazDrift) -> Drift:
    """App sem login automático: vale o que foi marcado na sessão da conta, e nada converge sozinho."""
    a, iid = o.account, o.target.instance_id
    if a is None:
        return drift(DriftStatus.blocked, SessionCode.no_account, f"o perfil não tem conta cadastrada em {app}")
    if not a.active:
        return drift(DriftStatus.blocked, SessionCode.account_disabled, f"a conta do perfil em {app} está desativada")
    if a.session_status is SessionStatus.session_ready:
        return drift(DriftStatus.in_sync, SessionCode.ready, f"sessão marcada como pronta em {app}")
    if a.session_status is SessionStatus.auth_required:
        return drift(DriftStatus.blocked, SessionCode.account_logged_out,
                     f"a conta do perfil saiu de {app}, e este app não tem login automático; entre pelo aparelho "
                     f"{iid}")
    if a.session_status in (SessionStatus.needs_person, SessionStatus.auth_challenge, SessionStatus.wrong_account):
        return drift(DriftStatus.blocked, SessionCode.needs_person, f"a sessão em {app} espera uma pessoa")
    return drift(DriftStatus.unknown, SessionCode.account_unknown,
                 f"não se sabe se a conta do perfil está entrada em {app}; nenhum comando lê a sessão deste app")


def plan_app_session(drift: Drift) -> list[ResourceAction]:
    return plan_by_rules(drift, kind=SESSION, converge=_SESSION_CONVERGE, observe=_SESSION_OBSERVE)
