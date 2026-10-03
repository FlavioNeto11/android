"""29.29: a D2-a (uma conta por app em cada aparelho) vale também quando a pessoa GANHA a conta tendo um vínculo sem
app. Nível de prova: `simulated` (harness na porta 5640, aparelhos falsos do QA).

O furo: o vínculo sem app serve a todo app em que a persona tem conta (`profiles_of_instance`). Uma pessoa SEM conta,
vinculada sem app a um aparelho que já tem o Instagram de outra persona, não tinha app nenhum a conferir no vínculo;
ao ganhar a conta depois (`create_profile` com `persona_id` e sem `instance_id`, ou `add_account` de outro app), as
duas passavam a servir o mesmo app no mesmo aparelho. Agora a recusa vem ANTES de criar qualquer linha: o 409
`conta_do_app_ja_no_aparelho` quer dizer "nada foi criado".
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.models import PersonaCreate, PersonaDeviceBody, ProfileAccountCreate, ProfileCreate
from app.state import AppState

from tests.conftest import Harness
from tests.test_sessao_declarada import CORREIO
from tests.test_sessao_por_conta import IID, correio_registrado, estado

__all__ = ["correio_registrado"]

pytestmark = pytest.mark.asyncio

OUTRO = "android-02"


def _cliente(harness: Harness) -> httpx.AsyncClient:
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _contagens(s: AppState) -> tuple[int, int, int]:
    """(perfis, contas, credenciais): o que um cadastro que falhasse pela metade deixaria para trás."""
    return (int(s.db.scalar("SELECT COUNT(*) FROM instagram_profiles") or 0),
            int(s.db.scalar("SELECT COUNT(*) FROM profile_accounts") or 0),
            int(s.db.scalar("SELECT COUNT(*) FROM account_credentials") or 0))


def _quem_serve(s: AppState, instance_id: str, app_id: str = "instagram") -> list[str]:
    return sorted(str(b["profile_id"]) for b in s.social_repo.profiles_of_instance(instance_id, app_id))


def _pessoa_sem_conta_vinculada_sem_app(s: AppState, nome: str, *aparelhos: str) -> str:
    pid = s.social.create_persona(PersonaCreate(name=nome)).id
    for iid in aparelhos:
        s.social.bind_device(pid, PersonaDeviceBody(instance_id=iid))
    return pid


def _correio_em_apps(s: AppState) -> None:
    if s.db.scalar("SELECT id FROM apps WHERE package=?", (CORREIO,)) is None:
        s.db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('correio', 'Correio de Exemplo', ?, 0)",
                     (CORREIO,))


async def test_reproducao_pessoa_sem_conta_vinculada_sem_app_nao_ganha_o_instagram_de_aparelho_ocupado(
        harness: Harness) -> None:
    s = estado(harness)
    pid_c = s.social.create_profile(ProfileCreate(username="caio.vivo", password="Vivo#Senha1", instance_id=IID)).id
    pid_a = _pessoa_sem_conta_vinculada_sem_app(s, "Ana Sem Conta", IID)
    antes = _contagens(s)
    async with _cliente(harness) as c:
        r = await c.post("/api/instagram/profiles", json={"username": "ana.depois", "password": "Depois#Senha1",
                                                          "persona_id": pid_a})
    assert r.status_code == 409, r.text
    corpo = r.json()["detail"]
    assert corpo["code"] == "conta_do_app_ja_no_aparelho" and pid_c in corpo["message"] and IID in corpo["message"]
    # Nada foi criado: nem a conta, nem o @, nem a senha no cofre; a pessoa continua sem conta.
    assert _contagens(s) == antes
    assert s.social_repo.profile_by_username("ana.depois") is None
    assert not s.social_repo.persona_row(pid_a)["username"]
    assert _quem_serve(s, IID) == [pid_c]


async def test_a_pessoa_sem_conta_vinculada_sem_app_a_aparelho_sem_outro_instagram_ganha_a_conta(
        harness: Harness) -> None:
    s = estado(harness)
    pid_a = _pessoa_sem_conta_vinculada_sem_app(s, "Ana Sem Conta", OUTRO)
    s.social.create_profile(ProfileCreate(username="ana.livre", password="Livre#Senha1", persona_id=pid_a))
    assert s.social_repo.conta_ancora(pid_a) is not None
    assert _quem_serve(s, OUTRO) == [pid_a]


async def test_o_conflito_em_QUALQUER_aparelho_de_vinculo_sem_app_recusa_ate_com_instance_id_livre(
        harness: Harness) -> None:
    """Dois vínculos sem app: um aparelho livre e um ocupado. O `instance_id` do cadastro (o livre) não esconde o
    conflito do outro vínculo."""
    s = estado(harness)
    pid_c = s.social.create_profile(ProfileCreate(username="caio.vivo", password="Vivo#Senha1", instance_id=IID)).id
    pid_a = _pessoa_sem_conta_vinculada_sem_app(s, "Ana Sem Conta", OUTRO, IID)
    antes = _contagens(s)
    async with _cliente(harness) as c:
        r = await c.post("/api/instagram/profiles", json={"username": "ana.dois", "persona_id": pid_a,
                                                          "instance_id": OUTRO})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conta_do_app_ja_no_aparelho", r.text
    assert _contagens(s) == antes
    assert _quem_serve(s, IID) == [pid_c] and _quem_serve(s, OUTRO) == []


async def test_vinculo_com_app_da_pessoa_nao_e_reconferido_ao_ganhar_a_conta(harness: Harness) -> None:
    """Só o vínculo SEM app muda de sentido quando a conta nasce; o COM app já foi conferido no vínculo."""
    s = estado(harness)
    pid_c = s.social.create_profile(ProfileCreate(username="caio.vivo", password="Vivo#Senha1", instance_id=IID)).id
    pid_a = s.social.create_persona(PersonaCreate(name="Ana Sem Conta")).id
    # Vínculo para outro app (o `builtin` da loja não vale: um app qualquer que exista).
    s.db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('chrome-d2a', 'Chrome', 'com.android.chrome', 0)")
    s.social.bind_device(pid_a, PersonaDeviceBody(instance_id=IID, app_id="chrome-d2a"))
    s.social.create_profile(ProfileCreate(username="ana.chrome", persona_id=pid_a))
    assert _quem_serve(s, IID) == [pid_c]


async def test_conta_de_outro_app_tambem_confere_o_vinculo_sem_app(
        harness: Harness, correio_registrado: None) -> None:
    """`add_account`: o vínculo sem app passa a servir o app da conta nova; se outra persona já serve esse app no
    aparelho, 409 sem criar a conta nem guardar a senha."""
    s = estado(harness)
    _correio_em_apps(s)
    pid_x = s.social.create_persona(PersonaCreate(name="Xavier")).id
    s.social.add_account(pid_x, ProfileAccountCreate(app_id="correio", handle="xavier.correio",
                                                     password=SecretStr("Correio#Senha1"), consent=True))
    s.social.bind_device(pid_x, PersonaDeviceBody(instance_id=IID, app_id="correio"))
    pid_y = _pessoa_sem_conta_vinculada_sem_app(s, "Yara", IID)
    antes = _contagens(s)
    async with _cliente(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{pid_y}/accounts",
                         json={"app_id": "correio", "handle": "yara.correio", "password": "Correio#Senha2",
                               "consent": True})
    assert r.status_code == 409, r.text
    corpo: dict[str, Any] = r.json()["detail"]
    assert corpo["code"] == "conta_do_app_ja_no_aparelho" and pid_x in corpo["message"]
    assert _contagens(s) == antes
    assert s.social_repo.list_accounts(pid_y) == []
    assert _quem_serve(s, IID, "correio") == [pid_x]


async def test_conta_de_outro_app_em_aparelho_sem_outra_persona_naquele_app_entra(
        harness: Harness, correio_registrado: None) -> None:
    """O outro Instagram no aparelho não impede o correio: a recusa é por APP."""
    s = estado(harness)
    _correio_em_apps(s)
    pid_c = s.social.create_profile(ProfileCreate(username="caio.vivo", password="Vivo#Senha1", instance_id=IID)).id
    pid_y = _pessoa_sem_conta_vinculada_sem_app(s, "Yara", IID)
    s.social.add_account(pid_y, ProfileAccountCreate(app_id="correio", handle="yara.correio",
                                                     password=SecretStr("Correio#Senha2"), consent=True))
    assert _quem_serve(s, IID, "correio") == [pid_y] and _quem_serve(s, IID) == [pid_c]
