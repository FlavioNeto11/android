"""Recursos declarativos (design §11, fase H, primeira parte): `diff` e `plan` puros dos quatro tipos de recurso.

O que cada grupo de teste protege, em uma frase:

* **vocabulários**: o domínio repete os estados do legado (não pode importar `app.models`) — e continua igual a eles;
* **só comando que existe**: todo verbo que um `plan` emite é um verbo de `commands/despacho.py`, com risco descrito;
* **tabelas de decisão**: cada linha é um caso do despacho de hoje (porta do app, porta de sessão, rodízio), com o
  status, o código e as ações esperadas;
* **`unknown` nunca vira certo**: a resposta a ele é só leitura (ou nada), nunca convergência nem `in_sync`;
* **idempotência**: estado observado = desejado ⇒ zero ações, e a segunda passada sobre o estado convergido também.

Tudo sem banco e sem aparelho: é o domínio puro. A leitura está em `test_leitura_de_recursos.py`.
"""
from __future__ import annotations

from dataclasses import replace
from typing import get_args

import pytest

from app import models
from app.commands.despacho import APP_COMMAND_VERBS, LIFECYCLE_ACTIONS
from app.contracts.skills import v1alpha1
from app.modules.applications.domain import resources as apps
from app.modules.applications.domain.resources import (AppCode, InstallationObserved, InstallState, ReleaseChannel,
                                                       ReleaseView, diff_app_installation, plan_app_installation)
from app.modules.fleet.domain import resources as fleet
from app.modules.fleet.domain.resources import (DeviceCode, DeviceObserved, DeviceState, ReadinessPhase,
                                                diff_device_state, plan_device_state)
from app.modules.identity.domain import resources as ident
from app.modules.identity.domain.resources import (AccountSessionStatus, AppAccount, BindingCode, BindingObserved,
                                                   CredentialState, ProfileStatus, ProviderSession, SessionCode,
                                                   SessionObserved, SessionStatus, diff_account_binding,
                                                   diff_app_session, plan_account_binding, plan_app_session)
from app.shared.resources import (NOT_READ, UNSUPPORTED_DESIRED, ActionPurpose, Drift, DriftStatus, ObservedState,
                                  OnMissing, ResourceAction, ResourceKind, ResourceRef, ResourceSpec, Target)
from app.state import AppState

ALVO = Target("android-01")
DEVICE = ResourceSpec.of("device.state", None, "online")
INSTALACAO = ResourceSpec.of("app.installation", "instagram", {"release": "promoted"})
VINCULO = ResourceSpec.of("account.binding", None, "bound", "ask")
SESSAO = ResourceSpec.of("app.session", "instagram", {"session": "ready", "account": "bound_profile"}, "ask")
SESSAO_APPLY = ResourceSpec.of("app.session", "instagram", {"session": "ready"}, "apply")
OUTLOOK = ResourceSpec.of("app.session", "outlook", {"session": "ready"})

#: Tudo o que o `plan` de cada tipo emitiu nas tabelas abaixo: conferido contra os comandos que existem.
_VERBOS_EMITIDOS: set[str] = set()


def _acoes(acoes: list[ResourceAction]) -> list[tuple[str, str | None]]:
    _VERBOS_EMITIDOS.update(a.verb for a in acoes if a.verb is not None)
    return [(a.purpose.value, a.verb) for a in acoes]


# =============================================================================================== vocabulários
def test_vocabularios_do_dominio_sao_os_do_legado_e_os_da_dsl() -> None:
    assert {k.value for k in ResourceKind} == set(get_args(v1alpha1.ResourceKind))
    assert {m.value for m in OnMissing} == set(get_args(v1alpha1.ResourceSpec.model_fields["on_missing"].annotation))
    assert {s.value for s in DeviceState} == {s.value for s in models.InstanceState}
    assert {p.value for p in ReadinessPhase} == set(get_args(models.ReadinessInfo.model_fields["phase"].annotation))
    assert {s.value for s in InstallState} == {s.value for s in models.InstalledAppState}
    assert {c.value for c in ReleaseChannel} == {c.value for c in models.ReleaseChannel}
    assert {s.value for s in SessionStatus} == {s.value for s in models.SessionStatus}
    # 049: um vocabulário só para app com e sem provedor. O que a PESSOA pode marcar numa conta sem provedor é um
    # subconjunto dele (desafio e conta errada são observados, não marcados).
    assert AccountSessionStatus is SessionStatus
    assert set(get_args(models.ProfileAccountPatch.model_fields["session_status"].annotation.__args__[0])) <= {
        s.value for s in SessionStatus}
    assert {s.value for s in ProfileStatus} == set(
        get_args(models.ProfilePatch.model_fields["status"].annotation.__args__[0]))
    # Entrega que falhou: o mesmo conjunto da porta do app, que nunca repete sozinha.
    assert {s.value for s in apps.FAILED} == set(AppState._ENTREGA_FALHOU)


def test_todo_verbo_e_de_um_comando_que_existe_e_tem_risco_descrito() -> None:
    conhecidos = set(LIFECYCLE_ACTIONS) | set(APP_COMMAND_VERBS)
    verbos = {v.value for v in fleet.DeviceVerb} | {v.value for v in apps.AppVerb} | {v.value for v in ident.SessionVerb}
    assert verbos <= conhecidos, f"verbo sem comando: {verbos - conhecidos}"
    riscos = fleet.VERB_RISKS | apps.VERB_RISKS | ident.VERB_RISKS
    assert set(riscos) == verbos


# =============================================================================================== device.state
def _dev(**kw: object) -> DeviceObserved:
    base: dict[str, object] = {"ref": DEVICE.ref, "target": ALVO, "registered": True, "runtime": True,
                               "state": DeviceState.online, "readiness": ReadinessPhase.ready}
    base.update(kw)
    return DeviceObserved(**base)  # type: ignore[arg-type]


CASOS_APARELHO = [
    ("pronto", {}, DriftStatus.in_sync, DeviceCode.ready, []),
    ("fora do inventário", {"registered": False, "runtime": False, "state": None}, DriftStatus.blocked,
     DeviceCode.not_registered, [("ask", None)]),
    ("aparelho-loja", {"store": True}, DriftStatus.blocked, DeviceCode.store_device, [("ask", None)]),
    ("sem runtime neste processo", {"runtime": False, "state": None, "readiness": None}, DriftStatus.unknown,
     DeviceCode.unobserved, []),
    ("estado não reconhecido", {"state": None}, DriftStatus.unknown, DeviceCode.unobserved, []),
    ("online subindo a escada", {"readiness": ReadinessPhase.boot_completed}, DriftStatus.pending,
     DeviceCode.readiness_climbing, []),
    ("ligando", {"state": DeviceState.booting}, DriftStatus.pending, DeviceCode.transitioning, []),
    ("desligando", {"state": DeviceState.stopping}, DriftStatus.pending, DeviceCode.transitioning, []),
    ("em erro", {"state": DeviceState.error, "state_detail": "boot falhou"}, DriftStatus.blocked, DeviceCode.error,
     [("ask", None)]),
    ("parado", {"state": DeviceState.stopped}, DriftStatus.diverged, DeviceCode.stopped, [("converge", "start")]),
    ("ausente", {"state": DeviceState.absent}, DriftStatus.diverged, DeviceCode.stopped, [("converge", "start")]),
    ("parado por decisão: o rodízio liga mesmo assim", {"state": DeviceState.stopped, "desired_state": "stopped"},
     DriftStatus.diverged, DeviceCode.stopped_by_decision, [("converge", "start")]),
    ("hibernado com snapshot", {"state": DeviceState.hibernated, "snapshot_valid": True}, DriftStatus.diverged,
     DeviceCode.hibernated, [("converge", "wake")]),
    ("hibernado sem snapshot", {"state": DeviceState.hibernated}, DriftStatus.diverged, DeviceCode.hibernated_cold,
     [("converge", "start")]),
    ("remoto sem worker", {"state": DeviceState.stopped, "external": True}, DriftStatus.pending,
     DeviceCode.worker_unavailable, []),
    ("remoto que o worker liga", {"state": DeviceState.stopped, "external": True, "worker_verbs": ("start",)},
     DriftStatus.diverged, DeviceCode.stopped, [("converge", "start")]),
    ("remoto hibernado sem `wake` no worker", {"state": DeviceState.hibernated, "external": True,
                                              "snapshot_valid": True, "worker_verbs": ("start",)},
     DriftStatus.diverged, DeviceCode.hibernated_cold, [("converge", "start")]),
]


@pytest.mark.parametrize(("caso", "campos", "status", "codigo", "acoes"), CASOS_APARELHO,
                         ids=[c[0] for c in CASOS_APARELHO])
def test_device_state(caso: str, campos: dict[str, object], status: DriftStatus, codigo: str,
                      acoes: list[tuple[str, str | None]]) -> None:
    drift = diff_device_state(DEVICE, _dev(**campos))
    assert (drift.status, drift.code) == (status, codigo), drift.detail
    assert _acoes(plan_device_state(drift)) == acoes


def test_device_state_so_persegue_online() -> None:
    drift = diff_device_state(ResourceSpec.of("device.state", None, "stopped"), _dev())
    assert (drift.status, drift.code) == (DriftStatus.unsupported, UNSUPPORTED_DESIRED)
    assert _acoes(plan_device_state(drift)) == [("ask", None)]


# =============================================================================================== app.installation
PROMOVIDA = ReleaseView("r-447", "447.0", 447, ReleaseChannel.promoted, True)
ANTIGA = ReleaseView("r-440", "440.0", 440, ReleaseChannel.promoted, True)
CANARIO = ReleaseView("r-450", "450.0", 450, ReleaseChannel.canary, True)
VOLTADA = ReleaseView("r-450v", "450.0", 450, ReleaseChannel.rolled_back, True)
GEMEA = ReleaseView("r-447b", "447.0", 447, ReleaseChannel.promoted, True)
CANDIDATA_447 = ReleaseView("r-447c", "447.0", 447, ReleaseChannel.candidate, True)


def _inst(**kw: object) -> InstallationObserved:
    base: dict[str, object] = {"ref": INSTALACAO.ref, "target": ALVO, "app_registered": True,
                               "package": "com.instagram.android", "main_app": True, "inspected": True,
                               "state": InstallState.ready, "observed_version_code": 447, "installed": PROMOVIDA,
                               "promoted": PROMOVIDA}
    base.update(kw)
    return InstallationObserved(**base)  # type: ignore[arg-type]


CASOS_APP = [
    ("na promovida", {}, DriftStatus.in_sync, AppCode.current, []),
    ("`installed` também é presente", {"state": InstallState.installed}, DriftStatus.in_sync, AppCode.current, []),
    ("promovida gêmea de mesmo número", {"installed": GEMEA}, DriftStatus.in_sync, AppCode.current, []),
    ("app não cadastrado", {"app_registered": False, "package": None}, DriftStatus.blocked, AppCode.app_unknown,
     [("ask", None)]),
    ("nunca inspecionado: lê, não instala", {"inspected": False, "state": None, "installed": None,
                                            "observed_version_code": None},
     DriftStatus.unknown, AppCode.unobserved, [("observe", "app.verify")]),
    ("estado desconhecido", {"state": None}, DriftStatus.unknown, AppCode.unrecognized_state,
     [("observe", "app.verify")]),
    ("instalação incerta (verifying sem dono)", {"state": InstallState.verifying}, DriftStatus.unknown,
     AppCode.no_outcome, [("observe", "app.verify")]),
    ("verifying com dono é espera", {"state": InstallState.verifying, "pending_op": "verify"}, DriftStatus.pending,
     AppCode.in_progress, []),
    ("instalando", {"state": InstallState.installing, "pending_op": "install"}, DriftStatus.pending,
     AppCode.in_progress, []),
    ("entrega falhou", {"state": InstallState.install_failed, "detail": "adb caiu"}, DriftStatus.blocked,
     AppCode.delivery_failed, [("ask", None)]),
    ("prova de abertura falhou", {"state": InstallState.verify_failed}, DriftStatus.blocked, AppCode.delivery_failed,
     [("ask", None)]),
    ("rebaixamento recusado", {"state": InstallState.version_drift, "drift_kind": "downgrade_refused"},
     DriftStatus.blocked, AppCode.downgrade_refused, [("ask", None)]),
    ("ausente no app principal: instala a promovida", {"state": InstallState.missing, "installed": None,
                                                       "observed_version_code": None},
     DriftStatus.diverged, AppCode.missing, [("converge", "app.install")]),
    ("ausente em quem não é do app: Distribuir é de pessoa", {"state": InstallState.missing, "main_app": False,
                                                              "installed": None, "observed_version_code": None},
     DriftStatus.blocked, AppCode.not_distributed, [("ask", None)]),
    ("ausente sem promovida", {"state": InstallState.missing, "installed": None, "observed_version_code": None,
                               "promoted": None},
     DriftStatus.blocked, AppCode.no_promoted_release, [("ask", None)]),
    ("presente sem promovida: fica", {"promoted": None}, DriftStatus.held, AppCode.no_promoted_release, []),
    ("promovida não entregável conta como nenhuma", {"promoted": replace(PROMOVIDA, installable=False),
                                                     "installed": ANTIGA, "observed_version_code": 440},
     DriftStatus.held, AppCode.no_promoted_release, []),
    ("atrasado", {"installed": ANTIGA, "observed_version_code": 440}, DriftStatus.diverged, AppCode.outdated,
     [("converge", "app.install")]),
    ("atualizado pela Play Store por fora do catálogo", {"installed": ANTIGA, "observed_version_code": 447},
     DriftStatus.diverged, AppCode.other_build, [("converge", "app.install")]),
    ("mesmo número, build candidato", {"installed": CANDIDATA_447}, DriftStatus.diverged, AppCode.other_build,
     [("converge", "app.install")]),
    ("mais novo em canário: não rebaixa", {"installed": CANARIO, "observed_version_code": 450}, DriftStatus.held,
     AppCode.newer_than_promoted, []),
    ("mais novo por fora do catálogo: não rebaixa", {"installed": None, "observed_version_code": 460},
     DriftStatus.held, AppCode.newer_than_promoted, []),
    ("mais novo e voltado: rebaixa", {"installed": VOLTADA, "observed_version_code": 450}, DriftStatus.diverged,
     AppCode.abandoned_version, [("converge", "app.install")]),
    ("presente sem versão lida", {"installed": None, "observed_version_code": None}, DriftStatus.unknown,
     AppCode.no_version, [("observe", "app.verify")]),
]


@pytest.mark.parametrize(("caso", "campos", "status", "codigo", "acoes"), CASOS_APP, ids=[c[0] for c in CASOS_APP])
def test_app_installation(caso: str, campos: dict[str, object], status: DriftStatus, codigo: str,
                          acoes: list[tuple[str, str | None]]) -> None:
    drift = diff_app_installation(INSTALACAO, _inst(**campos))
    assert (drift.status, drift.code) == (status, codigo), drift.detail
    assert _acoes(plan_app_installation(drift)) == acoes


@pytest.mark.parametrize("desejado", ["installed", {"release": "canary"}, {"release": "promoted", "x": "y"}])
def test_app_installation_so_persegue_a_promovida(desejado: str | dict[str, str]) -> None:
    drift = diff_app_installation(ResourceSpec.of("app.installation", "instagram", desejado), _inst())
    assert drift.status is DriftStatus.unsupported
    sem_alvo = diff_app_installation(ResourceSpec.of("app.installation", None, {"release": "promoted"}), _inst())
    assert sem_alvo.status is DriftStatus.unsupported


# =============================================================================================== account.binding
def _vinc(**kw: object) -> BindingObserved:
    base: dict[str, object] = {"ref": VINCULO.ref, "target": ALVO, "profile_id": "p-lucas", "username": "lucas",
                               "bound_at": "2026-09-20T10:00:00.000Z"}
    base.update(kw)
    return BindingObserved(**base)  # type: ignore[arg-type]


CASOS_VINCULO = [
    ("vinculado", {}, DriftStatus.in_sync, BindingCode.bound, []),
    ("vinculado ao perfil pedido", {"target": Target("android-01", "p-lucas")}, DriftStatus.in_sync,
     BindingCode.bound, []),
    ("sem vínculo", {"profile_id": None, "username": None}, DriftStatus.diverged, BindingCode.unbound,
     [("ask", None)]),
    ("vinculado a outro perfil", {"target": Target("android-01", "p-bruno")}, DriftStatus.diverged,
     BindingCode.bound_to_other, [("ask", None)]),
]


@pytest.mark.parametrize(("caso", "campos", "status", "codigo", "acoes"), CASOS_VINCULO,
                         ids=[c[0] for c in CASOS_VINCULO])
def test_account_binding(caso: str, campos: dict[str, object], status: DriftStatus, codigo: str,
                         acoes: list[tuple[str, str | None]]) -> None:
    drift = diff_account_binding(VINCULO, _vinc(**campos))
    assert (drift.status, drift.code) == (status, codigo), drift.detail
    assert _acoes(plan_account_binding(drift)) == acoes


@pytest.mark.parametrize("on_missing", ["wait", "apply", "ask"])
def test_vincular_e_sempre_de_pessoa(on_missing: str) -> None:
    spec = ResourceSpec.of("account.binding", None, "bound", on_missing)
    drift = diff_account_binding(spec, _vinc(ref=spec.ref, profile_id=None))
    assert _acoes(plan_account_binding(drift)) == [("ask", None)]


# =============================================================================================== app.session
PRONTA = ProviderSession(SessionStatus.session_ready, "android-01", "lucas", "2026-09-27T10:00:00.000Z",
                         credential=CredentialState.usable)


def _sess(spec: ResourceSpec = SESSAO, **kw: object) -> SessionObserved:
    base: dict[str, object] = {"ref": spec.ref, "target": ALVO, "app_registered": True, "profile_id": "p-lucas",
                               "username": "lucas", "profile_status": ProfileStatus.active, "provider": PRONTA}
    base.update(kw)
    return SessionObserved(**base)  # type: ignore[arg-type]


CASOS_SESSAO = [
    ("pronta", SESSAO, {}, DriftStatus.in_sync, SessionCode.ready, []),
    ("pronta, conta com arroba e maiúscula", SESSAO, {"provider": replace(PRONTA, observed_username="@Lucas")},
     DriftStatus.in_sync, SessionCode.ready, []),
    ("app não cadastrado", SESSAO, {"app_registered": False}, DriftStatus.blocked, SessionCode.app_unknown,
     [("ask", None)]),
    ("sem perfil vinculado", SESSAO, {"profile_id": None, "username": None, "profile_status": None},
     DriftStatus.blocked, SessionCode.no_profile, [("ask", None)]),
    ("outro perfil que o pedido", SESSAO, {"target": Target("android-01", "p-bruno")}, DriftStatus.blocked,
     SessionCode.other_profile, [("ask", None)]),
    ("perfil bloqueado (ADR-029)", SESSAO, {"profile_status": ProfileStatus.blocked}, DriftStatus.blocked,
     SessionCode.profile_inactive, [("ask", None)]),
    ("status de perfil desconhecido", SESSAO, {"profile_status": None}, DriftStatus.blocked,
     SessionCode.profile_inactive, [("ask", None)]),
    ("localidade mudou, política espera", SESSAO, {"locality_moved": True}, DriftStatus.blocked,
     SessionCode.locality_moved, [("ask", None)]),
    ("localidade mudou, política reautentica", SESSAO_APPLY, {"locality_moved": True, "reauth_elsewhere": True},
     DriftStatus.diverged, SessionCode.relocated, [("converge", "session.connect")]),
    ("nunca verificada: relê", SESSAO, {"provider": ProviderSession(None, credential=CredentialState.usable)},
     DriftStatus.unknown, SessionCode.unobserved, [("observe", "session.verify")]),
    ("unknown: relê, não autentica", SESSAO_APPLY,
     {"provider": ProviderSession(SessionStatus.unknown, credential=CredentialState.usable)},
     DriftStatus.unknown, SessionCode.unobserved, [("observe", "session.verify")]),
    ("tela não reconhecida no teto", SESSAO,
     {"provider": ProviderSession(SessionStatus.unknown, unknown_capped=True)},
     DriftStatus.blocked, SessionCode.unrecognized_screen, [("ask", None)]),
    ("desafio de segurança", SESSAO_APPLY, {"provider": replace(PRONTA, status=SessionStatus.auth_challenge)},
     DriftStatus.blocked, SessionCode.challenge, [("ask", None)]),
    ("conta errada", SESSAO_APPLY, {"provider": replace(PRONTA, status=SessionStatus.wrong_account)},
     DriftStatus.blocked, SessionCode.wrong_account, [("ask", None)]),
    ("deslogada, com credencial: login", SESSAO_APPLY, {"provider": replace(PRONTA, status=SessionStatus.auth_required)},
     DriftStatus.diverged, SessionCode.logged_out, [("converge", "session.connect")]),
    ("deslogada, on_missing ask: pessoa", SESSAO, {"provider": replace(PRONTA, status=SessionStatus.auth_required)},
     DriftStatus.diverged, SessionCode.logged_out, [("ask", None)]),
    ("deslogada, sem credencial", SESSAO_APPLY,
     {"provider": replace(PRONTA, status=SessionStatus.auth_required, credential=CredentialState.missing)},
     DriftStatus.blocked, SessionCode.no_credential, [("ask", None)]),
    ("deslogada, credencial recusada", SESSAO_APPLY,
     {"provider": replace(PRONTA, status=SessionStatus.auth_required, credential=CredentialState.invalid)},
     DriftStatus.blocked, SessionCode.credential_invalid, [("ask", None)]),
    ("pronta noutro aparelho", SESSAO, {"provider": replace(PRONTA, instance_id="android-02")}, DriftStatus.unknown,
     SessionCode.other_device, [("observe", "session.verify")]),
    ("pronta mas vencida", SESSAO, {"provider": replace(PRONTA, stale=True)}, DriftStatus.unknown, SessionCode.stale,
     [("observe", "session.verify")]),
    ("pronta sem conta lida na tela", SESSAO, {"provider": replace(PRONTA, observed_username=None)},
     DriftStatus.unknown, SessionCode.account_unproven, [("observe", "session.verify")]),
    ("pronta sem conta lida, sem exigir conta", SESSAO_APPLY, {"provider": replace(PRONTA, observed_username=None)},
     DriftStatus.in_sync, SessionCode.ready, []),
    ("pronta com a conta de outro", SESSAO, {"provider": replace(PRONTA, observed_username="bruno")},
     DriftStatus.blocked, SessionCode.wrong_account, [("ask", None)]),
    # app sem provedor de sessão (profile_accounts)
    ("sem provedor: marcada pronta", OUTLOOK,
     {"provider": None, "account": AppAccount(True, AccountSessionStatus.session_ready)},
     DriftStatus.in_sync, SessionCode.ready, []),
    ("sem provedor: sem conta", OUTLOOK, {"provider": None, "account": None}, DriftStatus.blocked,
     SessionCode.no_account, [("ask", None)]),
    ("sem provedor: conta desativada", OUTLOOK,
     {"provider": None, "account": AppAccount(False, AccountSessionStatus.session_ready)},
     DriftStatus.blocked, SessionCode.account_disabled, [("ask", None)]),
    ("sem provedor: saiu", OUTLOOK, {"provider": None, "account": AppAccount(True, AccountSessionStatus.auth_required)},
     DriftStatus.blocked, SessionCode.account_logged_out, [("ask", None)]),
    ("sem provedor: espera pessoa", OUTLOOK,
     {"provider": None, "account": AppAccount(True, AccountSessionStatus.needs_person)},
     DriftStatus.blocked, SessionCode.needs_person, [("ask", None)]),
    ("sem provedor: não se sabe, e nada lê", OUTLOOK,
     {"provider": None, "account": AppAccount(True, AccountSessionStatus.unknown)},
     DriftStatus.unknown, SessionCode.account_unknown, []),
]


@pytest.mark.parametrize(("caso", "spec", "campos", "status", "codigo", "acoes"), CASOS_SESSAO,
                         ids=[c[0] for c in CASOS_SESSAO])
def test_app_session(caso: str, spec: ResourceSpec, campos: dict[str, object], status: DriftStatus, codigo: str,
                     acoes: list[tuple[str, str | None]]) -> None:
    drift = diff_app_session(spec, _sess(spec, **campos))
    assert (drift.status, drift.code) == (status, codigo), drift.detail
    assert _acoes(plan_app_session(drift)) == acoes


@pytest.mark.parametrize("desejado", ["ready", {"session": "logged_out"}, {"session": "ready", "account": "x"},
                                      {"session": "ready", "extra": "1"}, {"account": "bound_profile"}])
def test_app_session_so_persegue_sessao_pronta(desejado: str | dict[str, str]) -> None:
    spec = ResourceSpec.of("app.session", "instagram", desejado)
    assert diff_app_session(spec, _sess(spec)).status is DriftStatus.unsupported


# =============================================================================================== regras comuns
_DIFF_PLAN = {
    ResourceKind.device_state: (diff_device_state, plan_device_state),
    ResourceKind.app_installation: (diff_app_installation, plan_app_installation),
    ResourceKind.account_binding: (diff_account_binding, plan_account_binding),
    ResourceKind.app_session: (diff_app_session, plan_app_session),
}


def _todos() -> list[tuple[ResourceSpec, ObservedState]]:
    return ([(DEVICE, _dev(**c[1])) for c in CASOS_APARELHO] + [(INSTALACAO, _inst(**c[1])) for c in CASOS_APP]
            + [(VINCULO, _vinc(**c[1])) for c in CASOS_VINCULO] + [(c[1], _sess(c[1], **c[2])) for c in CASOS_SESSAO])


def test_unknown_nunca_vira_certo_nem_convergencia() -> None:
    """A resposta a `unknown` é ler (ou nada). Converger só sai de `diverged`; `in_sync` só com observação."""
    for spec, obs in _todos():
        diff, plan = _DIFF_PLAN[spec.ref.kind]
        drift = diff(spec, obs)
        acoes = plan(drift)
        if drift.status is DriftStatus.unknown:
            assert all(a.purpose is ActionPurpose.observe for a in acoes), drift.detail
        if any(a.purpose is ActionPurpose.converge for a in acoes):
            assert drift.status is DriftStatus.diverged, drift.detail
        if any(a.purpose is ActionPurpose.observe for a in acoes):
            assert drift.status is DriftStatus.unknown, drift.detail
        if drift.status is DriftStatus.in_sync:
            assert drift.observed is obs and acoes == []


def test_idempotencia_mesma_entrada_mesmo_plano_e_em_sincronia_nada() -> None:
    for spec, obs in _todos():
        diff, plan = _DIFF_PLAN[spec.ref.kind]
        primeiro, segundo = diff(spec, obs), diff(spec, obs)
        assert primeiro == segundo and plan(primeiro) == plan(segundo)
        if primeiro.status in (DriftStatus.in_sync, DriftStatus.held, DriftStatus.pending):
            assert plan(primeiro) == []


@pytest.mark.parametrize(("spec", "antes", "depois"), [
    (DEVICE, _dev(state=DeviceState.stopped), _dev()),
    (DEVICE, _dev(state=DeviceState.hibernated, snapshot_valid=True), _dev()),
    (INSTALACAO, _inst(installed=ANTIGA, observed_version_code=440), _inst()),
    (INSTALACAO, _inst(state=InstallState.verifying), _inst()),
    (SESSAO_APPLY, _sess(SESSAO_APPLY, provider=replace(PRONTA, status=SessionStatus.auth_required)),
     _sess(SESSAO_APPLY)),
    (SESSAO, _sess(provider=replace(PRONTA, stale=True)), _sess()),
], ids=["ligar", "acordar", "atualizar", "reler-instalacao", "entrar", "reler-sessao"])
def test_segunda_passada_depois_de_convergir_nao_gera_acao(spec: ResourceSpec, antes: ObservedState,
                                                          depois: ObservedState) -> None:
    diff, plan = _DIFF_PLAN[spec.ref.kind]
    assert plan(diff(spec, antes)) != []
    assert plan(diff(spec, depois)) == []
    assert plan(diff(spec, depois)) == []


def test_on_missing_ask_troca_convergencia_por_pessoa_e_bloqueio_e_sempre_de_pessoa() -> None:
    parado = _dev(state=DeviceState.stopped)
    for on_missing, esperado in (("wait", [("converge", "start")]), ("apply", [("converge", "start")]),
                                 ("ask", [("ask", None)])):
        spec = ResourceSpec.of("device.state", None, "online", on_missing)
        assert _acoes(plan_device_state(diff_device_state(spec, replace(parado, ref=spec.ref)))) == esperado
    falhou = _inst(state=InstallState.install_failed)
    for on_missing in ("wait", "apply", "ask"):
        spec = ResourceSpec.of("app.installation", "instagram", {"release": "promoted"}, on_missing)
        assert _acoes(plan_app_installation(diff_app_installation(spec, replace(falhou, ref=spec.ref)))) == [
            ("ask", None)]


def test_drift_nao_lido_nao_tem_acao_em_nenhum_tipo() -> None:
    for spec in (DEVICE, INSTALACAO, VINCULO, SESSAO):
        drift = Drift(spec=spec, target=ALVO, status=DriftStatus.unknown, code=NOT_READ, detail="não lido")
        assert _DIFF_PLAN[spec.ref.kind][1](drift) == []


def test_tipo_trocado_e_erro_de_programacao() -> None:
    with pytest.raises(TypeError):
        diff_device_state(DEVICE, _inst())
    with pytest.raises(TypeError):
        diff_app_installation(DEVICE, _inst())
    with pytest.raises(ValueError):
        plan_app_session(diff_device_state(DEVICE, _dev()))


def test_spec_normaliza_e_recusa_o_que_nao_conhece() -> None:
    a = ResourceSpec.of("app.session", "instagram", {"session": "ready", "account": "bound_profile"}, "ask")
    b = ResourceSpec.of("app.session", "instagram", {"account": "bound_profile", "session": "ready"}, "ask")
    assert a == b and a.desired == (("account", "bound_profile"), ("session", "ready"))
    assert a.desired_text() == "account=bound_profile, session=ready" and DEVICE.desired_map() == {}
    assert ResourceRef(ResourceKind.app_session, "instagram").label() == "app.session(instagram)"
    with pytest.raises(ValueError):
        ResourceSpec.of("device.power", None, "on")
    with pytest.raises(ValueError):
        ResourceSpec.of("device.state", None, "online", "retry")


def test_acao_de_pessoa_nao_tem_verbo_e_de_sistema_sempre_tem() -> None:
    with pytest.raises(ValueError):
        ResourceAction(ref=DEVICE.ref, target=ALVO, purpose=ActionPurpose.ask, verb="start", reason="x")
    with pytest.raises(ValueError):
        ResourceAction(ref=DEVICE.ref, target=ALVO, purpose=ActionPurpose.converge, verb=None, reason="x")


def test_verbos_emitidos_pelas_tabelas_existem() -> None:
    """O que as tabelas acima de fato emitem — os seis verbos, e nenhum fora dos comandos que existem."""
    for spec, obs in _todos():
        diff, plan = _DIFF_PLAN[spec.ref.kind]
        _acoes(plan(diff(spec, obs)))
    assert _VERBOS_EMITIDOS == {"start", "wake", "app.install", "app.verify", "session.connect", "session.verify"}
    assert _VERBOS_EMITIDOS <= set(LIFECYCLE_ACTIONS) | set(APP_COMMAND_VERBS)
