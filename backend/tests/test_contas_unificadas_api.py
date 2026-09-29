"""A Conta na API (ADR-040): DTO com credencial, consentimento e sessão por conta; rotas por conta e os apelidos por
perfil que resolvem a conta âncora. Nível de prova: `simulated` (harness na porta 5640, aparelhos falsos do QA).

O que se prova:
- `ProfileAccountDTO` traz `login_identifier` (não é segredo), `credential` aninhado com `consent_at`, `session` com
  `stale` e `session_actions` calculadas pelo PACOTE da conta; nenhum campo escalar de credencial (a varredura de
  `test_social_profiles.py` continua valendo para ele);
- as rotas `…/accounts/{aid}/session/{connect|verify|logout}`, `…/accounts/{aid}/auth-attempts`,
  `…/accounts/{aid}/credential` (PUT/DELETE) e `…/credential/consent` existem e recusam com código HTTP honesto;
- os apelidos `/{pid}/connect|verify|logout|credential|auth-attempts` operam a conta âncora;
- a marcação de sessão da pessoa é da conta NO aparelho vinculado; sem aparelho, 409.
"""
from __future__ import annotations

import secrets as pysecrets
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.models import ProfileAccountCreate, ProfileAccountPatch, ProfileCreate
from app.social.service import SocialError

from .conftest import Harness

DONO = "painel:teste"


def _cliente(harness: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _valor() -> str:
    return "Tst-" + pysecrets.token_urlsafe(9)


def _perfil(harness: Harness, instance: str | None, **campos: Any) -> str:
    nome = f"conta.api.{pysecrets.token_hex(3)}"
    return harness.state.social.create_profile(ProfileCreate(username=nome, instance_id=instance, **campos)).id


def _erro(r: httpx.Response) -> dict[str, Any]:
    corpo = r.json()["detail"]
    assert isinstance(corpo, dict) and corpo.get("code"), r.text
    return corpo


def test_dto_da_conta_traz_credencial_consentimento_e_sessao(harness: Harness) -> None:
    s = harness.state
    valor = _valor()
    pid = s.social.create_profile(ProfileCreate(username="dto.conta", instance_id="android-01",
                                                email="dto@exemplo.test", password=SecretStr(valor))).id
    ig = next(c for c in s.social.list_accounts(pid) if c.app_id == "instagram")
    assert ig.login_identifier == "dto@exemplo.test" and ig.credential.configured and ig.credential_configured
    assert ig.credential.consent_by == "cadastro do perfil" and ig.consent_at == ig.credential.consent_at
    assert ig.session.status.value == "unknown" and ig.session_status == "unknown" and ig.automated_login
    assert ig.session_actions is not None and ig.session_actions.phase in ("app_unknown", "app_missing", "unknown")
    assert valor not in ig.model_dump_json()
    # a conta de um app sem provedor: sessão marcada pela pessoa, no aparelho vinculado, no vocabulário único
    qa = s.social.add_account(pid, ProfileAccountCreate(app_id="qa-messenger", handle="qa-user-01"))
    assert not qa.automated_login and not qa.credential.configured and qa.login_identifier is None
    marcada = s.social.update_account(pid, qa.id, ProfileAccountPatch(session_status="session_ready"))
    assert marcada.session.status.value == "session_ready" and marcada.session.instance_id == "android-01"
    assert marcada.session_verified_at and not marcada.session.stale
    saiu = s.social.update_account(pid, qa.id, ProfileAccountPatch(session_status="auth_required"))
    assert saiu.session_status == "auth_required" and saiu.session.verified_at is None
    assert s.db.scalar("SELECT COUNT(*) FROM account_sessions WHERE account_id=?", (qa.id,)) == 1
    # a cópia da 037 não é mais escrita
    assert s.db.scalar("SELECT session_status FROM profile_accounts WHERE id=?", (qa.id,)) == "unknown"


def test_marcar_sessao_sem_aparelho_e_409(harness: Harness) -> None:
    s = harness.state
    pid = _perfil(harness, None)
    qa = s.social.add_account(pid, ProfileAccountCreate(app_id="qa-messenger", handle="qa-user-02"))
    with pytest.raises(SocialError) as erro:
        s.social.update_account(pid, qa.id, ProfileAccountPatch(session_status="session_ready"))
    assert erro.value.code == "no_binding" and erro.value.status == 409


async def test_rotas_por_conta_e_apelidos_por_perfil(harness: Harness) -> None:
    s = harness.state
    valor = _valor()
    async with _cliente(harness) as c:
        # perfil sem aparelho: as rotas de sessão POR CONTA recusam como as por perfil (409 no_binding)
        pid = _perfil(harness, None, email="rotas@exemplo.test")
        contas = (await c.get(f"/api/instagram/profiles/{pid}/accounts")).json()
        ig = next(x for x in contas if x["app_id"] == "instagram")
        assert {"login_identifier", "credential", "consent_at", "session", "session_actions", "host"} <= set(ig)
        for acao in ("connect", "verify", "logout"):
            r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/session/{acao}")
            assert r.status_code == 409 and _erro(r)["code"] == "no_binding", (acao, r.text)
        # conta inexistente
        assert (await c.get(f"/api/instagram/profiles/{pid}/accounts/acc-nao-existe/auth-attempts")).status_code == 404
        # credencial por conta: 409 sem consentimento, 200 com; o valor não volta
        r = await c.put(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/credential", json={"password": valor})
        assert r.status_code == 409 and _erro(r)["code"] == "consentimento_de_credencial" and valor not in r.text
        r = await c.put(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/credential",
                        json={"password": valor, "consent": True})
        assert r.status_code == 200 and r.json()["credential"]["configured"] and valor not in r.text
        assert r.json()["credential"]["consent_by"] and r.json()["login_identifier"] == "rotas@exemplo.test"
        # consentir sem redigitar: idempotente; sem senha guardada, 409
        assert (await c.post(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/credential/consent")).status_code == 200
        r = await c.delete(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/credential")
        assert r.status_code == 200 and not r.json()["credential"]["configured"]
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/credential/consent")
        assert r.status_code == 409 and _erro(r)["code"] == "no_credential"
        # tentativas por conta: lista (vazia) — e o apelido por perfil lê a mesma conta âncora
        assert (await c.get(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/auth-attempts")).json() == []
        assert (await c.get(f"/api/instagram/profiles/{pid}/auth-attempts")).json() == []
        s.social_repo.start_auth_attempt(pid, "android-09", stage="form_found")
        por_conta = (await c.get(f"/api/instagram/profiles/{pid}/accounts/{ig['id']}/auth-attempts")).json()
        por_perfil = (await c.get(f"/api/instagram/profiles/{pid}/auth-attempts")).json()
        assert len(por_conta) == 1 and por_conta == por_perfil and por_conta[0]["account_id"] == ig["id"]

        # conta de app SEM provedor: conectar/verificar recusam com `no_session_provider`; sair também
        com = _perfil(harness, "android-01")
        r = await c.post(f"/api/instagram/profiles/{com}/accounts", json={"app_id": "qa-messenger", "handle": "qa-user-01"})
        assert r.status_code == 201, r.text
        qa = r.json()
        for acao in ("connect", "verify"):
            r = await c.post(f"/api/instagram/profiles/{com}/accounts/{qa['id']}/session/{acao}")
            assert r.status_code == 409, (acao, r.text)
            assert _erro(r)["code"] in ("no_session_provider", "no_credential", "app_not_verified"), r.text
        # senha na conta do QA com consentimento pela rota; depois o consentimento aparece no DTO
        r = await c.put(f"/api/instagram/profiles/{com}/accounts/{qa['id']}/credential",
                        json={"password": valor, "login_identifier": "qa-user-01", "consent": True})
        assert r.status_code == 200 and r.json()["consent_at"] and valor not in r.text
        # o apelido por perfil da credencial opera a conta âncora (Instagram), não a do QA
        r = await c.put(f"/api/instagram/profiles/{com}/credential", json={"password": valor, "consent": True})
        assert r.status_code == 200 and r.json()["credential"]["configured"]
        contas = {x["app_id"]: x for x in (await c.get(f"/api/instagram/profiles/{com}/accounts")).json()}
        assert contas["instagram"]["credential"]["configured"] and contas["qa-messenger"]["credential"]["configured"]
        r = await c.delete(f"/api/instagram/profiles/{com}/credential")
        assert r.status_code == 200 and not r.json()["credential"]["configured"]
        contas = {x["app_id"]: x for x in (await c.get(f"/api/instagram/profiles/{com}/accounts")).json()}
        assert not contas["instagram"]["credential"]["configured"] and contas["qa-messenger"]["credential"]["configured"]
        # o contexto operacional do aparelho traz as contas do perfil, sem valor nenhum
        ctx = (await c.get("/api/instances/android-01/operational-context")).json()
        assert [a["app_id"] for a in ctx["profiles"][0]["accounts"]] == ["instagram", "qa-messenger"]
        assert valor not in str(ctx)
    for tabela in ("events", "profile_accounts", "account_credentials"):
        for linha in s.db.query(f"SELECT * FROM {tabela}"):                    # noqa: S608 - nome fixo do teste
            assert valor not in str(dict(linha)), tabela


async def test_conta_de_site_nao_conecta_pelo_login_gerenciado(harness: Harness) -> None:
    """Item 23.4/23.6: a conta com `host` é de portal, pelo navegador. A porta de sessão e o despacho nunca a acham
    (a conta do app é a sem site), então conectar e verificar recusam já na rota (409 `conta_de_site`), sem trabalho
    no aparelho — antes a rota abria o login gerenciado dela e a Custom Tab seguia o site dela."""
    s = harness.state
    pid = _perfil(harness, "android-01")
    site = s.social.add_account(pid, ProfileAccountCreate(app_id="instagram", handle="conta.do.site",
                                                          host="www.instagram.com", password=SecretStr(_valor()),
                                                          consent=True))
    async with _cliente(harness) as c:
        for acao in ("connect", "verify"):
            r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{site.id}/session/{acao}")
            assert r.status_code == 409 and _erro(r)["code"] == "conta_de_site", (acao, r.text)
    assert s.social_repo.auth_attempts(pid, account_id=site.id) == []
