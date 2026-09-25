"""Aparelho → App → Perfil → Sessão: cada camada é independente e nenhuma implica a seguinte.

Motivado pelo android-06 (André Carvalho): "Conectar" e "Verificar conta" apareciam antes de o Instagram existir
no aparelho, "Desatualizado" dizia a mesma coisa para cinco situações, e "Instalar versão promovida" não dizia
qual app nem qual versão. Tudo aqui é prova `simulated`: sem aparelho, sem worker, sem Instagram real.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.config import AndroidCfg
from app.devices.conectividade import AVISO_PREFIXO, classificar, ler_sonda
from app.devices.emulator import build_args
from app.devices.stream import BACKOFF_MAX_S, backoff_s, stream_status
from app.integrations.instagram.reconciliation import LOGIN_ERROR_DETAIL, Outcome, classify_after_submit
from app.main import create_app
from app.models import AppOnDevice, InstanceState, SessionStatus
from app.social.sessao_gate import acoes_de_sessao

from .conftest import Harness

PKG = "com.instagram.android"


def _gate(state: str | None, *, cred: bool = True, status: str | None = None, aberta: bool = False,
          instance: str | None = "android-06"):
    app = AppOnDevice(package=PKG, state=state) if instance else None
    return acoes_de_sessao(instance_id=instance, app=app, app_name="Instagram", credential_configured=cred,
                           session_status=status, session_open=aberta)


# ------------------------------------------------------------------ portões de sessão (função pura)
def test_app_ausente_nao_oferece_acao_de_sessao() -> None:
    for estado in ("missing", "install_failed", "incompatible"):
        g = _gate(estado)
        assert g.phase == "app_missing"
        assert not g.connect.allowed and not g.verify.allowed and not g.logout.allowed
        assert "não está instalado" in (g.connect.reason or "")
        assert g.inspect_app.allowed, "a saída de 'ausente' é reler o aparelho (ou instalar)"


def test_app_nunca_inspecionado_nao_e_app_instalado() -> None:
    g = _gate(None)
    assert g.phase == "app_unknown"
    assert not g.connect.allowed and not g.verify.allowed
    assert g.inspect_app.allowed


def test_instalacao_em_curso_trava_sessao_e_inspecao() -> None:
    g = _gate("installing")
    assert g.phase == "app_installing"
    assert not g.connect.allowed and not g.inspect_app.allowed


def test_persona_vinculada_nao_equivale_a_sessao() -> None:
    sem = _gate(None, instance=None)
    assert sem.phase == "no_device" and not sem.connect.allowed
    vinculada = _gate("ready", status=None)
    assert vinculada.phase == "unknown", "vínculo + app não dizem nada da sessão"
    assert vinculada.phase != "authenticated"


def test_conta_configurada_nao_equivale_a_sessao() -> None:
    g = _gate("installed", cred=True, status="auth_required")
    assert g.phase == "logged_out" and g.connect.allowed
    sem_senha = _gate("installed", cred=False, status=None)
    assert sem_senha.phase == "no_credential"
    assert not sem_senha.connect.allowed and sem_senha.verify.allowed, "verificar só observa: não precisa de senha"


def test_autenticado_so_com_sessao_confirmada_na_tela() -> None:
    assert _gate("ready", status=SessionStatus.session_ready).phase == "authenticated"
    # comando de conexão em voo: é "autenticando", nunca "autenticado"
    g = _gate("ready", status="auth_required", aberta=True)
    assert g.phase == "authenticating" and not g.connect.allowed
    assert _gate("ready", status="auth_challenge").phase == "challenge"
    assert _gate("ready", status="wrong_account").phase == "wrong_account"


# ------------------------------------------------------------------ tela ao vivo (função pura)
def _stream(**kw: Any):
    base = dict(device_state="online", worker_bound=True, worker_connected=True, frame_ts="2026-09-25T10:00:00Z",
                frame_age_s=2.0, max_age_s=5.0, capture_failures=0, last_error=None, last_error_at=None)
    base.update(kw)
    return stream_status(**base)


def test_stream_stale_e_diferente_de_device_offline() -> None:
    assert _stream().status == "live"
    velho = _stream(frame_age_s=60.0)
    assert velho.status == "stale", "aparelho online sem frame novo NÃO é offline"
    assert _stream(device_state="stopped", frame_age_s=60.0).status == "device_offline"
    assert _stream(device_state="hibernated").status == "device_hibernated"


def test_stream_distingue_worker_fora_e_erro_de_captura() -> None:
    assert _stream(worker_connected=False, frame_age_s=60.0).status == "worker_offline"
    erro = _stream(frame_age_s=60.0, capture_failures=3, last_error="DriverError: timeout")
    assert erro.status == "capture_error" and erro.last_capture_error == "DriverError: timeout"
    assert _stream(frame_age_s=None, frame_ts=None).status == "no_frame"
    assert _stream(frame_age_s=None, frame_ts=None, capture_failures=1).status == "capture_error"


def test_recuo_da_captura_tem_teto() -> None:
    assert backoff_s(1.0, 0) == 1.0
    assert backoff_s(1.0, 1) == 2.0 and backoff_s(1.0, 2) == 4.0
    assert backoff_s(1.0, 50) == BACKOFF_MAX_S


async def test_falha_de_captura_aparece_no_dto_e_zera_com_frame_novo(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    rt.state = InstanceState.online
    s.devices._falha_de_captura(rt, "DriverError: screencap timeout")
    dto = s.devices.dto(rt)
    assert dto.stream is not None and dto.stream.consecutive_capture_failures == 1
    assert dto.stream.status == "capture_error"
    png = _png()
    await s.devices.publish_frame(rt, png)
    dto = s.devices.dto(rt)
    assert dto.stream.status == "live" and dto.stream.consecutive_capture_failures == 0


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 16), "white").save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ login: erro genérico é fato, não sucesso
def _tree(*textos: str) -> UiTree:
    nos = "".join(f'<node text="{t}" package="{PKG}" bounds="[0,{i * 60}][720,{i * 60 + 50}]"/>'
                  for i, t in enumerate(textos))
    return parse_hierarchy(f'<hierarchy><node package="{PKG}" bounds="[0,0][720,1280]">{nos}</node></hierarchy>')


def test_dialogo_unable_to_log_in_nao_vira_sucesso_nem_senha_errada() -> None:
    v = classify_after_submit(_tree("Unable to log in", "An unexpected error occurred. Please try logging in again.",
                                    "OK"), package=PKG, expected_username="andre.carvalho")
    assert v.outcome is Outcome.UNCERTAIN
    assert v.detail.startswith(LOGIN_ERROR_DETAIL)
    v_pt = classify_after_submit(_tree("Não foi possível entrar", "Ocorreu um erro inesperado."), package=PKG,
                                 expected_username="andre.carvalho", locale="pt")
    assert v_pt.outcome is Outcome.UNCERTAIN and v_pt.detail.startswith(LOGIN_ERROR_DETAIL)


# ------------------------------------------------------------------ contrato HTTP
def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_verificar_conta_recusa_sem_app_e_contexto_mostra_as_camadas(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    rt.state = InstanceState.online
    async with _cliente(harness) as c:
        criado = await c.post("/api/instagram/profiles", json={"username": "andre.carvalho", "first_name": "André"})
        assert criado.status_code == 201, criado.text
        pid = criado.json()["id"]
        s.social_repo.bind(pid, "android-01", reason="teste")

        perfil = (await c.get(f"/api/instagram/profiles/{pid}")).json()
        assert perfil["session_actions"]["phase"] == "app_unknown"
        assert perfil["app_on_device"]["package"] == PKG and perfil["app_on_device"]["state"] is None

        r = await c.post(f"/api/instagram/profiles/{pid}/verify")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "app_not_verified", r.text

        s.release_repo.upsert_app_state("android-01", PKG, state="missing", detail="não instalado")
        r = await c.post(f"/api/instagram/profiles/{pid}/verify")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "app_not_installed", r.text
        r = await c.post(f"/api/instagram/profiles/{pid}/logout")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "app_not_installed", r.text

        ctx = await c.get("/api/instances/android-01/operational-context")
        assert ctx.status_code == 200, ctx.text
        corpo = ctx.json()
        assert corpo["device"]["state"] == "online" and corpo["stream"]["status"] in ("no_frame", "live")
        insta = next(a for a in corpo["apps"] if a["package"] == PKG)
        assert insta["presence"] == "absent", "app ausente não aparece como instalado"
        assert corpo["profiles"][0]["session_actions"]["phase"] == "app_missing"
        assert corpo["profiles"][0]["session"]["status"] != "session_ready"

        pelo_perfil = await c.get(f"/api/instagram/profiles/{pid}/operational-context")
        assert pelo_perfil.status_code == 200 and pelo_perfil.json()["instance_id"] == "android-01"


async def test_contexto_nunca_carrega_segredo(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    if s.secrets.status() != "ready":
        raise AssertionError("o harness deveria ter cofre pronto; sem ele este teste não prova nada")
    async with _cliente(harness) as c:
        criado = await c.post("/api/instagram/profiles", json={"username": "perfil.segredo", "first_name": "S",
                                                                "password": "SenhaQueNaoPodeVazar#1"})
        assert criado.status_code == 201, criado.text
        pid = criado.json()["id"]
        s.social_repo.bind(pid, "android-01", reason="teste")
        textos = [json.dumps((await c.get(u)).json()) for u in
                  (f"/api/instagram/profiles/{pid}", "/api/instances/android-01/operational-context",
                   f"/api/instagram/profiles/{pid}/operational-context")]
    for t in textos:
        assert "SenhaQueNaoPodeVazar" not in t


# ---------------------------------------------------------------- internet do convidado (android-06, 25/09/2026)
# Medido: `online`, "pronto em 114 s", e nenhum nome resolvia (DNS 10.0.2.3 morto, Wi-Fi virtual desabilitado).

def test_sonda_de_rede_le_as_quatro_respostas_e_recusa_saida_incompleta() -> None:
    assert ler_sonda("R=1\nV=0\nD=0\nT=0\n") == {"route": True, "validated": False, "dns": False, "tcp_443": False}
    with pytest.raises(ValueError):
        ler_sonda("R=1\nV=2\n")                   # adb cortou a saída: não dá para saber, não é "sem internet"


def test_classificacao_da_internet_separa_dns_de_rota_e_de_validacao() -> None:
    ok = dict(route=True, dns=True, tcp_443=True, validated=True, checked_at="t")
    assert classificar(**ok).state == "healthy"
    sem_dns = classificar(**{**ok, "dns": False, "tcp_443": False, "validated": False})
    assert sem_dns.state == "unavailable" and "DNS não responde" in sem_dns.detail
    assert sem_dns.detail.startswith(AVISO_PREFIXO)
    assert classificar(**{**ok, "route": False}).state == "unavailable"
    assert classificar(**{**ok, "tcp_443": False}).state == "degraded"
    assert classificar(**{**ok, "validated": False}).state == "degraded"


def test_dns_do_emulador_e_configuravel_por_maquina_e_validado() -> None:
    a = AndroidCfg(dns_servers=["192.168.1.1", " 8.8.8.8 "])
    args = build_args(SimpleNamespace(emulator="emulator"), "avd", 5554, a, wipe_data=True)
    i = args.index("-dns-server")
    assert args[i + 1] == "192.168.1.1,8.8.8.8" and "-wipe-data" in args
    assert "-dns-server" not in build_args(SimpleNamespace(emulator="emulator"), "avd", 5554, AndroidCfg(), wipe_data=False)
    with pytest.raises(ValidationError):
        AndroidCfg(dns_servers=["dns.exemplo"])


async def test_sem_internet_segue_online_com_aviso_e_volta_sozinho(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    assert s.devices.dto(rt).connectivity.state == "unknown", "entrar no ar não prova internet"
    harness.fakes["android-01"].internet = {"dns": False, "tcp_443": False, "validated": False}
    info = await s.devices.conferir_conectividade(rt)
    dto = s.devices.dto(rt)
    assert info.state == "unavailable" and dto.state == InstanceState.online
    assert dto.attention and dto.attention.startswith(AVISO_PREFIXO)
    harness.fakes["android-01"].internet = None
    await s.devices.conferir_conectividade(rt)
    dto = s.devices.dto(rt)
    assert dto.connectivity.state == "healthy" and dto.attention is None
    # novo boot: o resultado anterior não é herdado
    s.devices._set_state(rt, InstanceState.stopped, "teste")
    s.devices._set_state(rt, InstanceState.online, "teste")
    assert s.devices.dto(rt).connectivity.state == "unknown"


async def test_conectar_sem_internet_recusa_409_e_contexto_mostra_a_rede(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    s.release_repo.upsert_app_state("android-01", PKG, state="ready", detail="teste")
    harness.fakes["android-01"].internet = {"dns": False, "tcp_443": False, "validated": False}
    async with _cliente(harness) as c:
        criado = await c.post("/api/instagram/profiles", json={"username": "sem.rede", "first_name": "S",
                                                                "password": "x-teste-1"})
        pid = criado.json()["id"]
        s.social_repo.bind(pid, "android-01", reason="teste")
        r = await c.post(f"/api/instagram/profiles/{pid}/connect")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "device_no_internet", r.text
        ctx = (await c.get("/api/instances/android-01/operational-context")).json()
        assert ctx["connectivity"]["state"] == "unavailable" and ctx["device"]["state"] == "online"


async def test_internet_de_aparelho_fora_do_ar_nao_e_afirmada(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    s.devices._set_state(rt, InstanceState.online, "teste")
    await s.devices.conferir_conectividade(rt)
    assert s.devices.dto(rt).connectivity.state == "healthy"
    s.devices._set_state(rt, InstanceState.hibernated, "teste")
    assert s.devices.dto(rt).connectivity.state == "unknown", "resultado velho não vale para aparelho hibernado"


def test_hibernar_sem_declaracao_do_worker_diz_a_causa() -> None:
    from app.devices.verbs import motivo_nao_suportado
    rt = SimpleNamespace(worker_verbs=["create", "start", "stop"], worker_id="worker-lan-01", serial="127.0.0.1:15555",
                         external=True, store=False)
    motivo = motivo_nao_suportado(rt, "hibernate")
    assert motivo and "android.hibernation" in motivo and "worker-lan-01" in motivo


async def test_despacho_exige_internet_so_do_app_que_precisa(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """online ≠ internet: tarefa de Instagram espera a rede; tarefa de app local segue (android-06, 25/09/2026)."""
    from app.models import ConnectivityInfo
    from app.planning.catalog import capabilities_of

    assert capabilities_of(PKG).requires_internet is True
    assert capabilities_of("com.android.settings").requires_internet is False   # sem registro: neutro, local
    s = harness.state
    assert s is not None
    sch, rt = s.scheduler, s.devices.get("android-01")
    monkeypatch.setattr(sch, "_app_gate", lambda obj, rt, pacote=None: None)      # isola: só a porta de rede
    monkeypatch.setattr(sch, "session_gate", None)
    notas: list[str] = []
    monkeypatch.setattr(sch.repo, "note_waiting", lambda oid, detail, wait_reason=None: notas.append(detail))
    obj = {"id": "obj-rede"}
    for estado in ("unknown", "degraded", "unavailable"):
        rt.connectivity = ConnectivityInfo(state=estado, detail=f"sem internet: {estado}")
        assert sch._portas_do_app(obj, rt, PKG) is True, estado
        assert "aguardando internet" in notas[-1]
        assert sch._portas_do_app(obj, rt, "com.android.settings") is False, "tarefa local não espera a rede"
        assert sch._portas_do_app(obj, rt, None) is False
    rt.connectivity = ConnectivityInfo(state="healthy", detail="ok")
    assert sch._portas_do_app(obj, rt, PKG) is False


async def test_boot_remoto_em_andamento_e_booting_nao_system_server_caido(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """android-09 via worker-lan-01 (25/09/2026): adb `device` antes do boot concluir → `service check` `not found`
    → `error` "o system_server caiu", e `online` sozinho 46 s depois. Boot em andamento não é doença."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    monkeypatch.setattr(rt, "external", True)
    s.devices._set_state(rt, InstanceState.stopped, "emulador desligado no worker")
    subiu = {"v": False}
    monkeypatch.setattr(rt.adb, "connect", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rt.adb, "state", lambda *a, **k: "device")
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: subiu["v"])
    monkeypatch.setattr(rt.adb, "prepare_for_automation", lambda *a, **k: None)
    fake = harness.fakes["android-01"]
    fake.guest_dead = True                          # serviços ainda não registrados: o que o boot parece por dentro
    await s.devices._adopt_external(rt)
    assert rt.state == InstanceState.booting and "subindo" in (rt.state_detail or "")
    assert rt.attention is None or "system_server" not in rt.attention
    subiu["v"], fake.guest_dead = True, False
    await s.devices._adopt_external(rt)
    assert rt.state == InstanceState.online and rt.boot_externo_desde == 0.0


async def test_boot_remoto_que_nao_termina_no_prazo_ainda_e_sondado(harness: Harness,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    monkeypatch.setattr(rt, "external", True)
    s.devices._set_state(rt, InstanceState.stopped, "x")
    monkeypatch.setattr(rt.adb, "connect", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(rt.adb, "state", lambda *a, **k: "device")
    monkeypatch.setattr(rt.adb, "boot_completed", lambda *a, **k: False)
    harness.fakes["android-01"].guest_dead = True
    # "Subindo" desde antes do prazo de boot, medido a partir de AGORA: um valor fixo (1.0) dependia do relógio
    # monotônico do host já passar do prazo, e o runner do CI recém-ligado não passa.
    rt.boot_externo_desde = time.monotonic() - s.cfg.instance_android(rt.id).boot_timeout_s - 1
    await s.devices._adopt_external(rt)
    assert rt.state != InstanceState.online and rt.state != InstanceState.booting, "travado subindo vira degradado"
