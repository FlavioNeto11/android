"""Aparelho → App → Perfil → Sessão: cada camada é independente e nenhuma implica a seguinte.

Motivado pelo android-06 (André Carvalho): "Conectar" e "Verificar conta" apareciam antes de o Instagram existir
no aparelho, "Desatualizado" dizia a mesma coisa para cinco situações, e "Instalar versão promovida" não dizia
qual app nem qual versão. Tudo aqui é prova `simulated`: sem aparelho, sem worker, sem Instagram real.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.automation.hierarchy import UiTree, parse_hierarchy
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
