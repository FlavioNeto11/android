"""As rotas do vínculo N:N persona × aparelho (migração 051; design persona-e-parque §7, §11.1). Nível de prova:
`simulated` (harness na porta 5640, aparelhos falsos do QA).

O que se prova, pelo HTTP:
- `POST /personas/{id}/devices` soma um aparelho sem mover ninguém; `PersonaDTO.instance_id` é o PRINCIPAL e
  `devices[]` traz todos, com app, marca de principal, estado e a sessão da conta naquele aparelho;
- `PUT …/devices/{iid}/primary` troca o principal; `DELETE …/devices/{iid}` desvincula e promove o que sobra;
- duas contas do MESMO app no mesmo aparelho → 409 `conta_do_app_ja_no_aparelho` (D2-a); apps diferentes convivem;
- `GET /instances/{id}/personas` é a outra direção; a loja e aparelho desconhecido recusam com código;
- `operational-context` do aparelho lista TODAS as personas dele; o do perfil vai pelo principal.
"""
from __future__ import annotations

import secrets as pysecrets
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.models import PersonaDeviceBody, ProfileCreate

from .conftest import Harness

pytestmark = pytest.mark.asyncio


def _cliente(harness: Harness) -> httpx.AsyncClient:
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _erro(r: httpx.Response) -> dict[str, Any]:
    corpo = r.json()["detail"]
    assert isinstance(corpo, dict) and corpo.get("code"), r.text
    return corpo


def _perfil(harness: Harness, instance: str | None) -> str:
    nome = f"nn.{pysecrets.token_hex(3)}"
    return harness.state.social.create_profile(ProfileCreate(username=nome, instance_id=instance,
                                                             first_name="Pessoa", last_name=nome)).id


async def test_persona_em_dois_aparelhos_com_principal_e_lista_por_aparelho(harness: Harness) -> None:
    a = _perfil(harness, "android-01")
    async with _cliente(harness) as c:
        r = await c.post(f"/api/personas/{a}/devices", json={"instance_id": "android-02", "app_id": "instagram"})
        assert r.status_code == 201, r.text
        dto = r.json()
        assert dto["instance_id"] == "android-01"                       # o principal continua o primeiro
        assert [(d["instance_id"], d["app_id"], d["is_primary"]) for d in dto["devices"]] == [
            ("android-01", "instagram", True), ("android-02", "instagram", False)]
        assert all(d["state"] is not None and d["session"] is not None for d in dto["devices"])
        assert dto["devices"][1]["session"]["status"] == "unknown"     # sessão nova NAQUELE aparelho

        r = await c.put(f"/api/personas/{a}/devices/android-02/primary")
        assert r.status_code == 200 and r.json()["instance_id"] == "android-02"
        assert sum(d["is_primary"] for d in r.json()["devices"]) == 1

        # O contexto do perfil vai pelo principal; o do aparelho lista quem está nele.
        r = await c.get(f"/api/instagram/profiles/{a}/operational-context")
        assert r.status_code == 200 and r.json()["instance_id"] == "android-02"
        r = await c.get("/api/instances/android-01/personas")
        assert r.status_code == 200 and [p["profile_id"] for p in r.json()] == [a]
        assert r.json()[0]["app_id"] == "instagram" and r.json()[0]["is_primary"] is False
        assert r.json()[0]["has_avatar"] in (True, False)               # 29.26: o painel só pede a foto se houver

        r = await c.delete(f"/api/personas/{a}/devices/android-02")
        assert r.status_code == 200, r.text
        assert r.json()["instance_id"] == "android-01"                  # o que sobrou virou principal
        assert [d["instance_id"] for d in r.json()["devices"]] == ["android-01"]
        assert (await c.delete(f"/api/personas/{a}/devices/android-02")).status_code == 404
        assert _erro(await c.put(f"/api/personas/{a}/devices/android-03/primary"))["code"] == "not_bound"


async def test_duas_contas_do_mesmo_app_no_mesmo_aparelho_recusam_e_apps_diferentes_convivem(harness: Harness) -> None:
    a = _perfil(harness, "android-01")
    b = _perfil(harness, None)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/personas/{b}/devices", json={"instance_id": "android-01", "app_id": "instagram"})
        assert r.status_code == 409 and _erro(r)["code"] == "conta_do_app_ja_no_aparelho"
        assert harness.state.social.get_profile(a).instance_id == "android-01"   # ninguém foi movido
        assert harness.state.social.get_profile(b).instance_id is None

        r = await c.post(f"/api/personas/{b}/devices", json={"instance_id": "android-01", "app_id": "qa-messenger"})
        assert r.status_code == 201, r.text
        assert r.json()["instance_id"] == "android-01" and r.json()["devices"][0]["is_primary"] is True
        r = await c.get("/api/instances/android-01/personas")
        assert sorted((p["profile_id"], p["app_id"]) for p in r.json()) == sorted([(a, "instagram"), (b, "qa-messenger")])
        r = await c.get("/api/instances/android-01/operational-context")
        assert sorted(p["profile_id"] for p in r.json()["profiles"]) == sorted([a, b])

        assert _erro(await c.post(f"/api/personas/{b}/devices", json={"instance_id": "nao-existe"}))["code"] == "unknown_instance"
        assert _erro(await c.post(f"/api/personas/{b}/devices", json={"instance_id": "android-02", "app_id": "x"}))["code"] == "unknown_app"
        assert (await c.post("/api/personas/nao-existe/devices", json={"instance_id": "android-02"})).status_code == 404
        assert (await c.get("/api/instances/nao-existe/personas")).status_code == 404


async def test_cadastro_no_aparelho_de_outra_conta_do_app_recusa_sem_criar_nada(harness: Harness) -> None:
    """D2-a conferida ANTES de qualquer linha: o 409 do cadastro quer dizer "nada foi criado"."""
    _perfil(harness, "android-01")
    nome = f"nn.{pysecrets.token_hex(3)}"
    async with _cliente(harness) as c:
        r = await c.post("/api/instagram/profiles", json={"username": nome, "instance_id": "android-01"})
        assert r.status_code == 409 and _erro(r)["code"] == "conta_do_app_ja_no_aparelho"
    assert harness.state.social_repo.profile_by_username(nome) is None


async def test_sessao_em_outro_aparelho_da_persona_por_instance_id(harness: Harness) -> None:
    """Conectar/verificar/sair e o contexto aceitam `?instance_id=` de OUTRO aparelho vinculado (o principal é só o
    padrão); aparelho não vinculado → 409 `sem_vinculo`."""
    a = _perfil(harness, "android-01")
    async with _cliente(harness) as c:
        assert (await c.post(f"/api/personas/{a}/devices", json={"instance_id": "android-02",
                                                                 "app_id": "instagram"})).status_code == 201
        r = await c.get(f"/api/instagram/profiles/{a}/operational-context", params={"instance_id": "android-02"})
        assert r.status_code == 200 and r.json()["instance_id"] == "android-02"
        r = await c.post(f"/api/instagram/profiles/{a}/verify", params={"instance_id": "android-03"})
        assert r.status_code == 409 and _erro(r)["code"] == "sem_vinculo"
        r = await c.get(f"/api/instagram/profiles/{a}/operational-context", params={"instance_id": "android-03"})
        assert r.status_code == 409 and _erro(r)["code"] == "sem_vinculo"
        conta = harness.state.social_repo.conta_ancora(a)
        r = await c.post(f"/api/instagram/profiles/{a}/accounts/{conta['id']}/session/logout",
                         params={"instance_id": "android-03"})
        assert r.status_code == 409 and _erro(r)["code"] == "sem_vinculo"


async def test_treino_grava_a_persona_escolhida_e_recusa_a_nao_vinculada_ou_ambigua(harness: Harness) -> None:
    """`TrainingRecorder` recebe a persona da escolha; sem escolha usa a ÚNICA do aparelho para o app; duas sem
    escolha → 409 `persona_ambigua`; escolhida sem vínculo ali → 409 `persona_nao_vinculada`."""
    from app.models import ControlOwner
    from app.training.recorder import TrainingError

    st = harness.state
    a = _perfil(harness, "android-01")
    b = _perfil(harness, None)
    st.social.bind_device(b, PersonaDeviceBody(instance_id="android-01", app_id="qa-messenger"))
    rt = st.devices.get("android-01")
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    status, lease = st.devices.request_control(rt)
    assert status == "granted" and rt.control == ControlOwner.user
    with pytest.raises(TrainingError) as exc:
        st.training.start("android-01", intent="ensinar", lease_id=lease)
    assert exc.value.code == "persona_ambigua"
    fora = _perfil(harness, "android-02")
    with pytest.raises(TrainingError) as exc:
        st.training.start("android-01", intent="ensinar", lease_id=lease, profile_id=fora)
    assert exc.value.code == "persona_nao_vinculada"
    gravacao = st.training.start("android-01", intent="ensinar", lease_id=lease, app_id="qa-messenger")
    assert st.db.scalar("SELECT profile_id FROM training_sessions WHERE id=?", (gravacao["id"],)) == b
    st.training.stop(gravacao["id"], discard=True)
    gravacao = st.training.start("android-01", intent="ensinar", lease_id=lease, profile_id=a)
    assert st.db.scalar("SELECT profile_id FROM training_sessions WHERE id=?", (gravacao["id"],)) == a
