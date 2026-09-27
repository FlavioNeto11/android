"""`ResourceProvider`s de `account.binding` e `app.session` (design §7, §8, §11): a leitura, o `diff` e o `plan`; com
um `CommandBus`, também `apply`, `verify` e `reconcile`.

Só `SELECT` em `device_profile_bindings`, `instagram_profiles`, `instances`, `instagram_sessions`,
`instagram_credentials` e `profile_accounts`. A porta de sessão de hoje (`AppState._session_gate`) não serve de
leitura: `_porta_da_localidade` grava a sessão e a localidade, e as lambdas que ela devolve autenticam. As regras de
LEITURA dela são refeitas aqui, sem efeito:

* localidade trocada = a de `SocialRepository.localidade` (vínculo fotografado × `instances` de agora; vínculo sem
  `locality_at` nunca acusa troca — falta de registro não é prova);
* sessão vencida = `social.repository.sessao_vencida`, com o relógio e a validade injetados;
* tela não reconhecida = `unknown_streak` no teto `session_unknown_retry_cap` (achado #104).

Da credencial só sai "tem, utilizável ou recusada" — nunca o `secret_ref`, nunca o identificador de login.

Qual app tem provedor de sessão determinístico é conhecimento do catálogo (`planning/catalog`), que fica ABAIXO de
identity no grafo de contextos: quem compõe injeta a pergunta (`tem_provedor_de_sessao`), e este módulo não o importa.

Com um `CommandBus` (segunda parte da fase H):

* `account.binding` não aplica nada — vincular é decisão de pessoa (o `plan` só devolve `ask`, e `apply` recusa ação
  de pessoa); `verify` relê o vínculo, e `reconcile` não tem comando a fechar;
* `app.session` aplica pelo canal de comandos o que a porta de sessão já faz: `session.connect` é o trabalho da porta
  (`ensure_session(automatic=True)`, com a credencial do cofre pelo canal sensível, ADR-025) e `session.verify` é a
  releitura da tela (`observe_only=True`, sem digitar nada). Desafio, 2FA e CAPTCHA continuam com a pessoa (o `diff`
  os põe em `blocked`, ADR-009/029). A prova de um `uncertain` é a sessão verificada DEPOIS do comando.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from app.db import Database, Row
from app.modules.identity.domain.resources import (AccountSessionStatus, AppAccount, BindingObserved,
                                                   CredentialState, ProfileStatus, ProviderSession, SessionObserved,
                                                   SessionStatus, SessionVerb, diff_account_binding,
                                                   diff_app_session, plan_account_binding, plan_app_session)
from app.shared.commands import CommandBus, CommandRef, RunRef
from app.shared.convergence import (ReconcileOutcome, ResourceVerification, apply_action, reconcile_resource,
                                    verify_resource)
from app.shared.resources import (Drift, ObservedState, ResourceAction, ResourceKind, ResourceRef, ResourceSpec,
                                  Target, known)
from app.util import now, to_iso

#: `instagram_profiles.offline_policy` que autoriza reautenticar noutro servidor (`models.OfflinePolicy`).
REAUTH_ELSEWHERE = "reauth_elsewhere"
#: Os verbos que `app.session` pede — é por eles que o `reconcile` procura os próprios incertos.
SESSION_VERBS: tuple[str, ...] = tuple(v.value for v in SessionVerb)


def _texto(valor: object) -> str | None:
    if valor is None or isinstance(valor, str):
        return valor
    raise TypeError(f"esperado texto ou nulo, veio {type(valor).__name__}")


def _vinculo(db: Database, instance_id: str) -> Row | None:
    return db.one("SELECT profile_id, bound_at, worker_id, physical_id, locality_at FROM device_profile_bindings"
                  " WHERE instance_id=? AND active=1", (instance_id,))


def _sem_parametros(observado: ObservedState, verbo: str) -> Mapping[str, str] | str:
    """Nunca chamado: vincular não tem verbo (o `apply` recusa a ação de pessoa antes)."""
    return f"'{verbo}' não é um comando de vínculo: vincular perfil é decisão de pessoa"


def _parametros_de_sessao(observado: ObservedState, verbo: str) -> Mapping[str, str] | str:
    """O perfil LIDO AGORA (o vinculado ao aparelho) e o app: é sobre ele que a porta de sessão trabalha."""
    if not isinstance(observado, SessionObserved) or observado.profile_id is None or observado.ref.target is None:
        return "nenhum perfil vinculado ao aparelho"
    return {"profile_id": observado.profile_id, "app_id": observado.ref.target}


def _sessao_lida_em(observado: ObservedState) -> str | None:
    if not isinstance(observado, SessionObserved):
        return None
    if observado.provider is not None:
        return observado.provider.verified_at
    return observado.account.verified_at if observado.account is not None else None


class AccountBindingProvider:
    kind = ResourceKind.account_binding

    def __init__(self, db: Database, *, bus: CommandBus | None = None) -> None:
        self._db = db
        #: Só `reconcile` o usa (quem hospeda); vincular nunca vira comando.
        self._bus = bus

    def read_current_state(self, ref: ResourceRef, target: Target) -> BindingObserved:
        vinculo = _vinculo(self._db, target.instance_id)
        if vinculo is None:
            return BindingObserved(ref=ref, target=target)
        perfil = _texto(vinculo["profile_id"])
        nome = self._db.scalar("SELECT username FROM instagram_profiles WHERE id=?", (perfil,))
        return BindingObserved(ref=ref, target=target, profile_id=perfil, username=_texto(nome),
                               bound_at=_texto(vinculo["bound_at"]))

    def diff(self, desired: ResourceSpec, observed: ObservedState) -> Drift:
        return diff_account_binding(desired, observed)

    def plan(self, drift: Drift) -> list[ResourceAction]:
        return plan_account_binding(drift)

    def _canal(self) -> CommandBus:
        if self._bus is None:
            raise RuntimeError("AccountBindingProvider sem CommandBus: só leitura")
        return self._bus

    def apply(self, action: ResourceAction, *, spec: ResourceSpec, run_ref: RunRef | None = None) -> CommandRef:
        """Sempre recusa (`ValueError`): o `plan` deste recurso só tem ação de pessoa."""
        return apply_action(self._canal(), self, action, spec=spec, params_of=_sem_parametros, run_ref=run_ref)

    def verify(self, spec: ResourceSpec, target: Target) -> ResourceVerification:
        return verify_resource(self, spec, target)

    def reconcile(self, spec: ResourceSpec, target: Target) -> ReconcileOutcome:
        return reconcile_resource(self._canal(), self, spec, target, verbs=(), proof_time=None)


class AppSessionProvider:
    kind = ResourceKind.app_session

    def __init__(self, db: Database, *, tem_provedor_de_sessao: Callable[[str], bool], session_max_age_s: int,
                 unknown_retry_cap: int, agora: Callable[[], datetime] = now, bus: CommandBus | None = None) -> None:
        self._db = db
        self._tem_provedor = tem_provedor_de_sessao
        self._validade_s = session_max_age_s
        self._teto = unknown_retry_cap
        self._agora = agora
        #: Sem canal, o provider é só leitura (o `PlanReport` não precisa de mais).
        self._bus = bus

    def read_current_state(self, ref: ResourceRef, target: Target) -> SessionObserved:
        app = self._db.one("SELECT id, package FROM apps WHERE id=?", (ref.target,)) if ref.target else None
        if app is None:
            return SessionObserved(ref=ref, target=target, app_registered=False)
        vinculo = _vinculo(self._db, target.instance_id)
        if vinculo is None:
            return SessionObserved(ref=ref, target=target, app_registered=True)
        perfil_id = str(vinculo["profile_id"])
        perfil = self._db.one("SELECT username, status, offline_policy FROM instagram_profiles WHERE id=?",
                              (perfil_id,))
        provedor = self._provedor(perfil_id) if self._tem_provedor(str(app["package"])) else None
        return SessionObserved(
            ref=ref, target=target, app_registered=True, profile_id=perfil_id,
            username=_texto(perfil["username"]) if perfil is not None else None,
            profile_status=known(ProfileStatus, perfil["status"] or ProfileStatus.active.value)
            if perfil is not None else None,
            locality_moved=self._localidade_mudou(vinculo, target.instance_id),
            reauth_elsewhere=perfil is not None and perfil["offline_policy"] == REAUTH_ELSEWHERE,
            provider=provedor,
            account=None if provedor is not None else self._conta(perfil_id, str(app["id"])))

    def _localidade_mudou(self, vinculo: Row, instance_id: str) -> bool:
        if vinculo["locality_at"] is None:
            return False                       # vínculo anterior à 023: falta de registro não acusa troca
        agora = self._db.one("SELECT worker_id, physical_id FROM instances WHERE id=?", (instance_id,))
        worker, fisico = (agora["worker_id"], agora["physical_id"]) if agora is not None else (None, None)
        mudou_de_maquina = vinculo["worker_id"] != worker
        mudou_de_aparelho = bool(vinculo["physical_id"] and fisico and vinculo["physical_id"] != fisico)
        return bool(mudou_de_maquina or mudou_de_aparelho)

    def _provedor(self, perfil_id: str) -> ProviderSession:
        cred = self._db.scalar("SELECT status FROM instagram_credentials WHERE profile_id=?", (perfil_id,))
        credencial = (CredentialState.missing if cred is None
                      else CredentialState.invalid if cred == "invalid" else CredentialState.usable)
        s = self._db.one("SELECT status, instance_id, observed_username, verified_at, unknown_streak"
                         " FROM instagram_sessions WHERE profile_id=?", (perfil_id,))
        if s is None:
            return ProviderSession(None, credential=credencial)
        status = known(SessionStatus, s["status"])
        verificada = _texto(s["verified_at"])
        vencida = (status is SessionStatus.session_ready and self._validade_s > 0
                   and (not verificada or verificada < to_iso(self._agora() - timedelta(seconds=self._validade_s))))
        no_teto = status is SessionStatus.unknown and int(s["unknown_streak"] or 0) >= self._teto
        return ProviderSession(status, instance_id=_texto(s["instance_id"]),
                               observed_username=_texto(s["observed_username"]), verified_at=verificada,
                               stale=vencida, unknown_capped=no_teto, credential=credencial)

    def _conta(self, perfil_id: str, app_id: str) -> AppAccount | None:
        c = self._db.one("SELECT status, session_status, session_verified_at FROM profile_accounts"
                         " WHERE profile_id=? AND app_id=?", (perfil_id, app_id))
        if c is None:
            return None
        return AppAccount(active=c["status"] == "active",
                          session_status=known(AccountSessionStatus, c["session_status"]),
                          verified_at=_texto(c["session_verified_at"]))

    def diff(self, desired: ResourceSpec, observed: ObservedState) -> Drift:
        return diff_app_session(desired, observed)

    def plan(self, drift: Drift) -> list[ResourceAction]:
        return plan_app_session(drift)

    def _canal(self) -> CommandBus:
        if self._bus is None:
            raise RuntimeError("AppSessionProvider sem CommandBus: só leitura")
        return self._bus

    def apply(self, action: ResourceAction, *, spec: ResourceSpec, run_ref: RunRef | None = None) -> CommandRef:
        return apply_action(self._canal(), self, action, spec=spec, params_of=_parametros_de_sessao,
                            run_ref=run_ref)

    def verify(self, spec: ResourceSpec, target: Target) -> ResourceVerification:
        return verify_resource(self, spec, target)

    def reconcile(self, spec: ResourceSpec, target: Target) -> ReconcileOutcome:
        return reconcile_resource(self._canal(), self, spec, target, verbs=SESSION_VERBS, proof_time=_sessao_lida_em)
