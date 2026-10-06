"""Leitura dos recursos declarativos (fase H, primeira parte): `read_current_state` dos quatro providers.

O que cada teste protege, em uma frase:

* cada provider lê o que já existe (banco e runtime em memória) e devolve o objeto do domínio — nunca a `Row`;
* ler não escreve: o banco sai byte a byte igual (contagem e conteúdo das tabelas lidas);
* as regras de leitura refeitas fora do `AppState` batem com as dele (a release no aparelho, a promovida-alvo, a
  localidade, a validade e o teto da sessão);
* o `DeviceRuntime` DE VERDADE (pelo harness) passa pela porta `DeviceRuntimeView` sem adaptador.

O banco vem de `make_config(...).db_dsn`: roda em SQLite e, com `TEST_DATABASE_URL`, no PostgreSQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.modules.applications.domain.resources import AppCode, InstallState, ReleaseChannel
from app.modules.applications.infrastructure.app_installation import AppInstallationProvider
from app.modules.applications.infrastructure.app_repository import AppRepository
from app.modules.fleet.domain.resources import DeviceCode, DeviceState, ReadinessPhase
from app.modules.fleet.infrastructure.device_state import DeviceStateProvider
from app.modules.identity.domain.resources import (BindingCode, CredentialState, ProfileStatus, SessionCode,
                                                   SessionStatus)
from app.modules.identity.infrastructure.account_session import AccountBindingProvider, AppSessionProvider
from app.planning.catalog import session_provider_of
from app.shared.resources import DriftStatus, ResourceSpec, Target
from app.util import to_iso

from .conftest import make_config

AGORA = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
IG = "com.instagram.android"
TABELAS_LIDAS = ("instances", "apps", "app_releases", "device_app_state", "instagram_profiles",
                 "device_profile_bindings", "profile_accounts", "account_sessions", "account_credentials")

DEVICE = ResourceSpec.of("device.state", None, "online")
INSTALACAO = ResourceSpec.of("app.installation", "instagram", {"release": "promoted"})
VINCULO = ResourceSpec.of("account.binding", None, "bound")
SESSAO = ResourceSpec.of("app.session", "instagram", {"session": "ready", "account": "bound_profile"})
SESSAO_OUTLOOK = ResourceSpec.of("app.session", "outlook", {"session": "ready"})


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    return db


def _instancia(db: Database, iid: str, idx: int, *, app_id: str | None = None, desired: str | None = None,
               worker_id: str | None = None, physical_id: str | None = None) -> None:
    db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
               " app_id, desired_state, worker_id, physical_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (iid, idx, iid, 5640 + 2 * idx, 8300 + idx, 9300 + idx, 9600 + idx, app_id, desired, worker_id,
                physical_id))


def _release(db: Database, rid: str, code: int, channel: str, *, status: str = "installable",
             channel_at: str = "2026-09-20T00:00:00.000Z", package: str = IG) -> None:
    db.execute("INSERT INTO app_releases(id, package_name, version_name, version_code, artifact_type,"
               " signature_sha256, catalog_dir, source_type, imported_at, status, channel, channel_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (rid, package, f"{code}.0", code, "apk", "ab" * 32, f"apks/{rid}", "upload",
                "2026-09-19T00:00:00.000Z", status, channel, channel_at))


def _estado_do_app(db: Database, iid: str, **campos: Any) -> None:
    colunas = {"instance_id": iid, "package_name": IG, **campos}
    db.execute(f"INSERT INTO device_app_state({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))})",
               tuple(colunas.values()))


def _perfil(db: Database, pid: str, username: str, *, status: str = "active", policy: str | None = None) -> None:
    db.execute("INSERT INTO instagram_profiles(id, username, status, offline_policy, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?)", (pid, username, status, policy, to_iso(AGORA), to_iso(AGORA)))
    # A conta do Instagram do perfil (049): é dela a credencial e a sessão que o leitor consulta.
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?)", (f"acc-{pid}", pid, "instagram", username, "active", to_iso(AGORA),
                                          to_iso(AGORA)))


def _vincular(db: Database, pid: str, iid: str, *, worker_id: str | None = None, physical_id: str | None = None,
              locality: bool = True) -> None:
    db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, worker_id,"
               " physical_id, locality_at) VALUES (?,?,1,?,?,?,?)",
               (pid, iid, to_iso(AGORA), worker_id, physical_id, to_iso(AGORA) if locality else None))


def _sessao(db: Database, pid: str, status: str, *, iid: str = "android-01", username: str | None = "tadeu",
            verificada: datetime | None = AGORA, streak: int = 0, conta: str | None = None) -> None:
    db.execute("INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at,"
               " updated_at, unknown_streak) VALUES (?,?,?,?,?,?,?)",
               (conta or f"acc-{pid}", iid, status, username, to_iso(verificada) if verificada else None,
                to_iso(AGORA), streak))


def _credencial(db: Database, pid: str, status: str = "active", *, consentida: bool = True) -> None:
    # Nome de referência no cofre, não um segredo: o leitor nunca o lê.
    db.execute("INSERT INTO account_credentials(account_id, login_identifier, secret_ref, key_id, status,"
               " created_at, updated_at, consent_at, consent_by) VALUES (?,?,?,?,?,?,?,?,?)",
               (f"acc-{pid}", "login-de-teste", "ref-de-teste", "chave-de-teste", status, to_iso(AGORA),
                to_iso(AGORA), to_iso(AGORA) if consentida else None, "teste" if consentida else None))


def _foto(db: Database) -> dict[str, list[tuple[object, ...]]]:
    return {t: sorted((tuple(r.values()) for r in db.query(f"SELECT * FROM {t}")), key=repr) for t in TABELAS_LIDAS}


def _sessoes(db: Database, *, validade_s: int = 3600, teto: int = 3) -> AppSessionProvider:
    return AppSessionProvider(db, tem_provedor_de_sessao=lambda pkg: session_provider_of(pkg) == "instagram",
                              session_max_age_s=validade_s, unknown_retry_cap=teto, agora=lambda: AGORA)


@dataclass
class RuntimeFalso:
    """Dublê da porta `DeviceRuntimeView` (o real é conferido no teste do harness, abaixo)."""

    state: str = "online"
    state_detail: str | None = None
    readiness_phase: str = "ready"
    store: bool = False
    external: bool = False
    worker_verbs: list[str] | None = None
    snapshot_valid: bool = False


# =============================================================================================== device.state
def test_device_state_le_o_desejado_do_banco_e_o_observado_do_runtime(banco: Database) -> None:
    _instancia(banco, "android-01", 1, desired="stopped")
    _instancia(banco, "android-02", 2)
    _instancia(banco, "android-03", 3)
    runtimes = {"android-01": RuntimeFalso(state="stopped", readiness_phase="not_running"),
                "android-02": RuntimeFalso(state="hibernated", snapshot_valid=True, external=True,
                                           worker_verbs=["wake", "start"]),
                "android-09": RuntimeFalso()}
    p = DeviceStateProvider(banco, runtimes)
    antes = _foto(banco)

    parado = p.read_current_state(DEVICE.ref, Target("android-01"))
    assert (parado.registered, parado.runtime, parado.state, parado.desired_state) == (
        True, True, DeviceState.stopped, "stopped")
    drift = p.diff(DEVICE, parado)
    assert (drift.status, drift.code) == (DriftStatus.diverged, DeviceCode.stopped_by_decision)
    assert [(a.purpose.value, a.verb) for a in p.plan(drift)] == [("converge", "start")]

    remoto = p.read_current_state(DEVICE.ref, Target("android-02"))
    assert remoto.worker_verbs == ("start", "wake") and remoto.external and remoto.snapshot_valid
    assert p.diff(DEVICE, remoto).code == DeviceCode.hibernated

    sem_runtime = p.read_current_state(DEVICE.ref, Target("android-03"))
    assert (sem_runtime.registered, sem_runtime.runtime) == (True, False)
    assert p.diff(DEVICE, sem_runtime).status is DriftStatus.unknown

    fora = p.read_current_state(DEVICE.ref, Target("android-09"))  # runtime sem linha em `instances`
    assert not fora.registered and p.diff(DEVICE, fora).code == DeviceCode.not_registered
    assert _foto(banco) == antes


def test_device_state_estado_estranho_vira_desconhecido(banco: Database) -> None:
    _instancia(banco, "android-01", 1)
    p = DeviceStateProvider(banco, {"android-01": RuntimeFalso(state="congelado", readiness_phase="talvez")})
    obs = p.read_current_state(DEVICE.ref, Target("android-01"))
    assert (obs.state, obs.readiness) == (None, None)
    assert p.diff(DEVICE, obs).status is DriftStatus.unknown


async def test_device_state_le_o_device_runtime_de_verdade(harness: Any) -> None:
    """O `DeviceManager.devices` real cumpre `Mapping[str, DeviceRuntimeView]` sem adaptador."""
    state = harness.state
    p = DeviceStateProvider(state.db, state.devices.devices)
    assert len(state.devices.devices) == 3
    for iid, rt in state.devices.devices.items():
        obs = p.read_current_state(DEVICE.ref, Target(iid))
        assert obs.registered and obs.runtime
        assert obs.state is DeviceState(rt.state.value) and obs.readiness is ReadinessPhase(rt.readiness_phase)
        assert (obs.store, obs.external, obs.snapshot_valid) == (rt.store, rt.external, rt.snapshot_valid)
        assert p.diff(DEVICE, obs).target == Target(iid)


# =============================================================================================== app.installation
def _parque_do_instagram(db: Database) -> None:
    AppRepository(db).criar(app_id="instagram", name="Instagram", package=IG)
    _release(db, "r-440", 440, "promoted", channel_at="2026-09-10T00:00:00.000Z")
    _release(db, "r-447", 447, "promoted", channel_at="2026-09-20T00:00:00.000Z")
    # Empate de número: vence a que entrou no canal por último (desempate de `releases_of_channel`).
    _release(db, "r-447a", 447, "promoted", channel_at="2026-09-18T00:00:00.000Z")
    _release(db, "r-450", 450, "rolled_back")
    _release(db, "r-460", 460, "canary")
    _release(db, "r-470", 470, "promoted", status="validated", package="com.outro.pacote")


def test_app_installation_le_estado_release_no_aparelho_e_promovida(banco: Database) -> None:
    _parque_do_instagram(banco)
    for i, iid in enumerate(("android-01", "android-02", "android-03", "android-04", "android-05", "android-06"), 1):
        _instancia(banco, iid, i, app_id="instagram" if iid != "android-05" else None)
    _estado_do_app(banco, "android-01", state="ready", installed_release_id="r-447", observed_version_code=447)
    _estado_do_app(banco, "android-02", state="ready", installed_release_id="r-440", observed_version_code=440)
    # Sem instalada registrada: o número observado é o da desejada → é ela que está lá.
    _estado_do_app(banco, "android-03", state="verify_failed", desired_release_id="r-447a",
                   observed_version_code=447)
    # Número de uma voltada, por fora da desejada: a voltada vem primeiro.
    _estado_do_app(banco, "android-04", state="ready", observed_version_code=450)
    _estado_do_app(banco, "android-05", state="missing")
    p = AppInstallationProvider(banco)
    antes = _foto(banco)

    def ler(iid: str) -> Any:
        return p.read_current_state(INSTALACAO.ref, Target(iid))

    em_dia = ler("android-01")
    assert em_dia.package == IG and em_dia.main_app and em_dia.inspected and em_dia.state is InstallState.ready
    assert em_dia.promoted is not None and em_dia.promoted.id == "r-447" and em_dia.promoted.installable
    assert em_dia.installed is not None and em_dia.installed.id == "r-447"
    assert p.diff(INSTALACAO, em_dia).status is DriftStatus.in_sync and p.plan(p.diff(INSTALACAO, em_dia)) == []

    atrasado = ler("android-02")
    assert p.diff(INSTALACAO, atrasado).code == AppCode.outdated
    assert [a.verb for a in p.plan(p.diff(INSTALACAO, atrasado))] == ["app.install"]

    desejada = ler("android-03")
    assert desejada.installed is not None and desejada.installed.id == "r-447a"
    assert p.diff(INSTALACAO, desejada).code == AppCode.delivery_failed

    voltada = ler("android-04")
    assert voltada.installed is not None and voltada.installed.channel is ReleaseChannel.rolled_back
    assert p.diff(INSTALACAO, voltada).code == AppCode.abandoned_version

    alheio = ler("android-05")
    assert not alheio.main_app and p.diff(INSTALACAO, alheio).code == AppCode.not_distributed

    nunca = ler("android-06")
    assert not nunca.inspected and nunca.state is None
    assert [(a.purpose.value, a.verb) for a in p.plan(p.diff(INSTALACAO, nunca))] == [("observe", "app.verify")]
    assert _foto(banco) == antes


def test_app_installation_app_nao_cadastrado(banco: Database) -> None:
    _instancia(banco, "android-01", 1)
    obs = AppInstallationProvider(banco).read_current_state(INSTALACAO.ref, Target("android-01"))
    assert not obs.app_registered and obs.package is None


# =============================================================================================== identity
def _identidades(db: Database) -> None:
    AppRepository(db).criar(app_id="instagram", name="Instagram", package=IG)
    AppRepository(db).criar(app_id="outlook", name="Outlook", package="com.microsoft.office.outlook")
    for i, iid in enumerate(("android-01", "android-02", "android-03", "android-04", "android-05"), 1):
        _instancia(db, iid, i, worker_id="notebook" if iid == "android-04" else None, physical_id=f"fp-{i}")
    _perfil(db, "p-tadeu", "tadeu")
    _perfil(db, "p-quillon", "quillon", status="blocked")
    _perfil(db, "p-ottilie", "ottilie")
    _perfil(db, "p-ana", "ana", policy="reauth_elsewhere")
    _vincular(db, "p-tadeu", "android-01", physical_id="fp-1")
    _vincular(db, "p-quillon", "android-02", physical_id="fp-2")
    _vincular(db, "p-ottilie", "android-03", physical_id="fp-3")
    _vincular(db, "p-ana", "android-04", worker_id=None, physical_id="fp-4")    # a 04 foi para o notebook
    _sessao(db, "p-tadeu", "session_ready")
    _sessao(db, "p-ottilie", "unknown", iid="android-03", username=None, verificada=None, streak=3)
    _credencial(db, "p-tadeu")
    _credencial(db, "p-ana", status="invalid")
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?)",
               ("c-1", "p-tadeu", "outlook", "tadeu@exemplo.test", "active", to_iso(AGORA), to_iso(AGORA)))
    # A marcação da pessoa ("saí") é a sessão da conta no aparelho, no vocabulário único (049).
    _sessao(db, "p-tadeu", "auth_required", username=None, verificada=None, conta="c-1")


def test_account_binding_le_o_vinculo_ativo(banco: Database) -> None:
    _identidades(banco)
    p = AccountBindingProvider(banco)
    antes = _foto(banco)
    tadeu = p.read_current_state(VINCULO.ref, Target("android-01"))
    assert (tadeu.profile_id, tadeu.username) == ("p-tadeu", "tadeu")
    assert p.diff(VINCULO, tadeu).code == BindingCode.bound
    pedido_outro = p.read_current_state(VINCULO.ref, Target("android-01", "p-ottilie"))
    assert p.diff(VINCULO, pedido_outro).code == BindingCode.bound_to_other
    livre = p.read_current_state(VINCULO.ref, Target("android-05"))
    assert livre.profile_id is None and [a.purpose.value for a in p.plan(p.diff(VINCULO, livre))] == ["ask"]
    assert _foto(banco) == antes


def test_app_session_le_provedor_conta_localidade_validade_e_teto(banco: Database) -> None:
    _identidades(banco)
    p = _sessoes(banco)
    antes = _foto(banco)

    def ler(spec: ResourceSpec, iid: str) -> Any:
        return p.read_current_state(spec.ref, Target(iid))

    tadeu = ler(SESSAO, "android-01")
    assert tadeu.provider is not None and tadeu.account is None and tadeu.profile_status is ProfileStatus.active
    assert (tadeu.provider.status, tadeu.provider.credential, tadeu.provider.stale) == (
        SessionStatus.session_ready, CredentialState.usable, False)
    assert p.diff(SESSAO, tadeu).status is DriftStatus.in_sync

    quillon = ler(SESSAO, "android-02")
    assert quillon.profile_status is ProfileStatus.blocked and p.diff(SESSAO, quillon).code == SessionCode.profile_inactive
    assert quillon.provider is not None and quillon.provider.status is None             # sem linha de sessão

    ottilie = ler(SESSAO, "android-03")
    assert ottilie.provider is not None and ottilie.provider.unknown_capped             # vinculado: teto 1 (29.92)
    assert p.diff(SESSAO, ottilie).code == SessionCode.unrecognized_screen

    ana = ler(SESSAO, "android-04")
    assert ana.locality_moved and ana.reauth_elsewhere
    assert ana.provider is not None and ana.provider.credential is CredentialState.invalid
    assert p.diff(SESSAO, ana).code == SessionCode.credential_invalid

    sem_perfil = ler(SESSAO, "android-05")
    assert sem_perfil.profile_id is None and p.diff(SESSAO, sem_perfil).code == SessionCode.no_profile

    outlook = ler(SESSAO_OUTLOOK, "android-01")
    assert outlook.provider is None and outlook.account is not None
    assert outlook.account.session_status is SessionStatus.auth_required
    assert p.diff(SESSAO_OUTLOOK, outlook).code == SessionCode.account_logged_out
    assert _foto(banco) == antes


def test_app_session_validade_e_teto_vem_de_quem_compoe(banco: Database) -> None:
    _identidades(banco)
    velha = _sessoes(banco, validade_s=60)                   # verificada há 0 s: dentro; AGORA+2min: vencida
    assert not velha.read_current_state(SESSAO.ref, Target("android-01")).provider.stale  # type: ignore[union-attr]
    depois = AppSessionProvider(banco, tem_provedor_de_sessao=lambda pkg: pkg == IG, session_max_age_s=60,
                                unknown_retry_cap=3, agora=lambda: AGORA + timedelta(minutes=2))
    obs = depois.read_current_state(SESSAO.ref, Target("android-01"))
    assert obs.provider is not None and obs.provider.stale
    assert [(a.purpose.value, a.verb) for a in depois.plan(depois.diff(SESSAO, obs))] == [
        ("observe", "session.verify")]
    # Validade 0 desliga o vencimento, como em `sessao_vencida`. O teto maior NÃO tira o ottilie do bloqueio: o
    # android-03 tem vínculo ativo (conta real), e aí o teto é 1, o mesmo da porta (29.92).
    frouxo = _sessoes(banco, validade_s=0, teto=10)
    assert frouxo.read_current_state(SESSAO.ref, Target("android-03")).provider.unknown_capped is True  # type: ignore[union-attr]


def test_app_session_sem_vinculo_no_aparelho_da_sessao_usa_o_teto_de_quem_compoe(banco: Database) -> None:
    """T1 da leitura do #371: o teto global (o de quem compõe) ainda vale onde a SESSÃO lida está num aparelho sem
    vínculo — o caminho do "a sessão é de outro aparelho": o ottilie está vinculado ao android-03, mas a única linha de
    sessão dele é do android-05, que não tem vínculo. Lá o teto é o global, não 1."""
    _identidades(banco)
    banco.execute("DELETE FROM account_sessions WHERE account_id='acc-p-ottilie'")
    _sessao(banco, "p-ottilie", "unknown", iid="android-05", username=None, verificada=None, streak=2)

    def capped(teto: int) -> bool | None:
        obs = _sessoes(banco, teto=teto).read_current_state(SESSAO.ref, Target("android-03"))
        assert obs.provider is not None and obs.provider.instance_id == "android-05"
        return obs.provider.unknown_capped

    assert capped(10) is False                        # duas leituras, teto 10: não para
    assert capped(3) is False                         # nem com o teto 3
    assert capped(2) is True                          # no teto global: para


def test_app_session_com_vinculo_para_no_primeiro_unknown_como_a_porta(banco: Database) -> None:
    """29.92 (U1 da leitura do #371): com vínculo ativo, UM `unknown` já é o teto, como na porta de sessão
    (`SocialRepository.teto_de_unknown`). A prévia diz "assuma o controle" e nenhum `session.verify` é planejado —
    antes, com o teto global 3, ela dizia "não verificada" e planejava a releitura enquanto a porta pedia a pessoa."""
    _identidades(banco)
    banco.execute("UPDATE account_sessions SET unknown_streak=1 WHERE account_id='acc-p-ottilie'")
    p = _sessoes(banco, teto=3)
    ottilie = p.read_current_state(SESSAO.ref, Target("android-03"))
    assert ottilie.provider is not None and ottilie.provider.unknown_capped
    drift = p.diff(SESSAO, ottilie)
    assert (drift.status, drift.code) == (DriftStatus.blocked, SessionCode.unrecognized_screen)
    assert all(a.verb != "session.verify" for a in p.plan(drift))

    # Ainda sem nenhuma leitura de tela (contador 0), a sessão segue "não verificada" e pede a releitura.
    banco.execute("UPDATE account_sessions SET unknown_streak=0 WHERE account_id='acc-p-ottilie'")
    zero = p.read_current_state(SESSAO.ref, Target("android-03"))
    assert zero.provider is not None and zero.provider.unknown_capped is False
    assert [(a.purpose.value, a.verb) for a in p.plan(p.diff(SESSAO, zero))] == [("observe", "session.verify")]


def test_app_session_teto_velho_nao_e_teto(banco: Database) -> None:
    """A mesma regra da porta de sessão (causa C8 das execuções r-20260928165254-e31953 / r-20260928195344-02ee9e):
    o teto gravado ANTES de o emulador subir, ou mais velho que a validade, não trava — pede releitura
    (`session.verify`), como o `unknown` de antes do teto. O android-01 ficou preso ~47 h por um teto de 26/09."""
    _identidades(banco)
    ottilie = _sessoes(banco).read_current_state(SESSAO.ref, Target("android-03"))
    assert ottilie.provider is not None and ottilie.provider.unknown_capped             # gravado agora: trava

    # O emulador subiu DEPOIS da gravação: o que se viu era do boot anterior.
    banco.execute("UPDATE instances SET emulator_started_at=? WHERE id='android-03'",
                  (to_iso(AGORA + timedelta(minutes=1)),))
    depois_do_boot = _sessoes(banco).read_current_state(SESSAO.ref, Target("android-03"))
    assert depois_do_boot.provider is not None and depois_do_boot.provider.unknown_capped is False
    assert [(a.purpose.value, a.verb) for a in _sessoes(banco).plan(_sessoes(banco).diff(SESSAO, depois_do_boot))] == [
        ("observe", "session.verify")]

    # Sem boot novo, mas a gravação passou da validade: também relê.
    banco.execute("UPDATE instances SET emulator_started_at=NULL WHERE id='android-03'")
    velha = AppSessionProvider(banco, tem_provedor_de_sessao=lambda pkg: pkg == IG, session_max_age_s=60,
                               unknown_retry_cap=3, agora=lambda: AGORA + timedelta(minutes=2))
    lida = velha.read_current_state(SESSAO.ref, Target("android-03"))
    assert lida.provider is not None and lida.provider.unknown_capped is False


def test_localidade_sem_registro_nunca_acusa_troca(banco: Database) -> None:
    AppRepository(banco).criar(app_id="instagram", name="Instagram", package=IG)
    _instancia(banco, "android-01", 1, worker_id="notebook")
    _perfil(banco, "p-tadeu", "tadeu")
    _vincular(banco, "p-tadeu", "android-01", locality=False)
    assert not _sessoes(banco).read_current_state(SESSAO.ref, Target("android-01")).locality_moved
