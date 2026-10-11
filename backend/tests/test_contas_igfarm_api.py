"""`POST /api/instagram/contas` e `GET /api/instagram/contas/{id}/codigo` (ponte android <-> igfarm, migracao 132).
Nivel de prova: `simulated` (leitor de e-mail falso, gerador de imagem simulado). Nenhuma IMAP nem IA reais."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.main import create_app
from app.models import PersonaCreate
from app.modules.email_do_parque.application.ports import Mensagem
from app.modules.email_do_parque.application.servico import ConfigEmail, EmailDoParque
from app.social.contas_nossas import registrar_lapide

from .conftest import Harness

pytestmark = pytest.mark.asyncio

DOM = "nvit.com.br"
SENHA_EMAIL = "Senha-do-email-9f3a"
SENHA_IG = "Senha-do-insta-71cc"
AGORA = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


class LeitorFalso:
    def __init__(self, mensagens: list[Mensagem]) -> None:
        self.mensagens = mensagens

    async def buscar(self, *, destinatario: str, remetente: str, desde: datetime) -> list[Mensagem]:
        return [m for m in self.mensagens if destinatario in m.destinatarios]


def _cliente(harness: Harness, leitor: LeitorFalso | None = None) -> httpx.AsyncClient:
    harness.state.email_parque = EmailDoParque(ConfigEmail(DOM, (DOM,), leitor is not None, "instagram"), leitor,
                                               agora=lambda: AGORA)
    harness.cfg.file.contas.criacao_pela_api_do_igfarm = True
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _persona(h: Harness, nome: str = "Ana", sobrenome: str = "Lima") -> str:
    return h.state.social.create_persona(PersonaCreate(
        name=f"{nome} {sobrenome}", first_name=nome, last_name=sobrenome, birth_date="1990-05-17",
        locale="pt-BR")).id


def _corpo(pid: str, **extra: object) -> dict[str, object]:
    corpo: dict[str, object] = {
        "persona_id": pid, "dominio": DOM, "email": f"ana.lima1234@{DOM}", "email_senha": SENHA_EMAIL,
        "instagram_username": "ana.ceramica", "instagram_senha": SENHA_IG, "igfarm_account_id": "ig-777",
        "criada_em": "2026-10-09T11:30:00Z"}
    corpo.update(extra)
    return corpo


def _tudo_do_banco(h: Harness) -> str:
    linhas: list[str] = []
    for tabela in ("events", "caixas_email", "contas_igfarm", "persona_reservas", "account_credentials"):
        linhas += [str(dict(r)) for r in h.state.db.query(f"SELECT * FROM {tabela}")]  # noqa: S608
    return "\n".join(linhas)


async def test_registra_conta_credencial_caixa_e_marca_do_igfarm(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    pid = _persona(harness)
    caplog.set_level(logging.DEBUG)
    async with _cliente(harness) as c:
        r = await c.post("/api/instagram/contas", json=_corpo(pid))
        assert r.status_code == 201, r.text
        dto = r.json()
        assert dto["senhas"] == "••••" and dto["criada"] is True and dto["idempotente"] is False
        assert dto["persona_id"] == pid and dto["instagram_username"] == "ana.ceramica"
        assert dto["igfarm_account_id"] == "ig-777" and dto["email"] == f"ana.lima1234@{DOM}"
        assert SENHA_EMAIL not in r.text and SENHA_IG not in r.text
    db = harness.state.db
    perfil = db.one("SELECT * FROM instagram_profiles WHERE id=?", (pid,))
    assert perfil["username"] == "ana.ceramica" and perfil["email"] == f"ana.lima1234@{DOM}"
    cred = db.one("SELECT * FROM account_credentials WHERE account_id=?", (dto["account_id"],))
    assert cred["login_identifier"] == f"ana.lima1234@{DOM}" and cred["consent_by"] == "igfarm" and cred["consent_at"]
    assert harness.state.social.secrets.get_secret(cred["secret_ref"]) == SENHA_IG
    caixa = db.one("SELECT * FROM caixas_email WHERE account_id=?", (dto["account_id"],))
    assert harness.state.social.secrets.get_secret(caixa["secret_ref"]) == SENHA_EMAIL
    assert caixa["endereco"] == f"ana.lima1234@{DOM}" and caixa["secret_ref"] != cred["secret_ref"]
    ig = db.one("SELECT * FROM contas_igfarm WHERE account_id=?", (dto["account_id"],))
    assert ig["igfarm_account_id"] == "ig-777" and ig["profile_id"] == pid
    evento = db.one("SELECT * FROM events WHERE kind='identity.conta.registrada'")
    assert evento is not None
    # Senha nunca no banco em texto (events, caixa, credencial), nem no log.
    assert SENHA_EMAIL not in _tudo_do_banco(harness) and SENHA_IG not in _tudo_do_banco(harness)
    assert SENHA_EMAIL not in caplog.text and SENHA_IG not in caplog.text


async def test_repetir_o_registro_e_idempotente(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        r1 = await c.post("/api/instagram/contas", json=_corpo(pid))
        r2 = await c.post("/api/instagram/contas", json=_corpo(pid, instagram_username="@Ana.Ceramica"))
        assert (r1.status_code, r2.status_code) == (201, 200), r2.text
        assert r2.json()["idempotente"] is True and r2.json()["account_id"] == r1.json()["account_id"]
        assert r2.json()["senhas"] == "••••"
    db = harness.state.db
    for tabela in ("contas_igfarm", "caixas_email", "profile_accounts", "account_credentials"):
        assert db.scalar(f"SELECT COUNT(*) FROM {tabela}") == 1, tabela  # noqa: S608
    assert db.scalar("SELECT COUNT(*) FROM events WHERE kind='identity.conta.registrada'") == 1


async def test_o_fluxo_completo_da_reserva_ao_registro_limpa_a_reserva(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        p = (await c.get("/api/instagram/personas-pendentes", params={"dominio": DOM, "reservar": "true"})).json()[0]
        assert p["persona_id"] == pid
        r = await c.post("/api/instagram/contas", json=_corpo(
            pid, email=p["email_sugerido"], instagram_username=p["username_sugerido"]))
        assert r.status_code == 201, r.text
        assert harness.state.db.scalar("SELECT COUNT(*) FROM persona_reservas") == 0
        # Com conta, a pessoa nao e mais pendente.
        assert (await c.get("/api/instagram/personas-pendentes", params={"dominio": DOM})).json() == []


async def test_validacoes(harness: Harness) -> None:
    pid, outra = _persona(harness), _persona(harness, "Bia", "Reis")
    async with _cliente(harness) as c:
        r = await c.post("/api/instagram/contas", json=_corpo("ig-nao-existe"))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
        r = await c.post("/api/instagram/contas", json=_corpo(pid, dominio="gmail.com", email="a@gmail.com"))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "dominio_nao_permitido"
        r = await c.post("/api/instagram/contas", json=_corpo(pid, email="ana@outro.com.br"))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "dominio_divergente"
        r = await c.post("/api/instagram/contas", json=_corpo(pid, instagram_username="a..b"))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "username_invalido"
        r = await c.post("/api/instagram/contas", json=_corpo(pid, extra_campo="x"))
        assert r.status_code == 422
        assert SENHA_IG not in r.text and SENHA_EMAIL not in r.text            # o 422 nao devolve o corpo

        registrar_lapide(harness.state.db, app_id="com.instagram.android", handle="velha.conta", profile_id=None)
        r = await c.post("/api/instagram/contas", json=_corpo(pid, instagram_username="velha.conta"))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "conta_retirada"

        assert (await c.post("/api/instagram/contas", json=_corpo(pid))).status_code == 201
        # Outra pessoa, mesmo @ -> duplicado; mesmo e-mail com outro @ -> e-mail em uso.
        r = await c.post("/api/instagram/contas", json=_corpo(outra, email=f"bia@{DOM}"))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_username"
        r = await c.post("/api/instagram/contas", json=_corpo(outra, instagram_username="bia.reis"))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "email_em_uso"
        # A mesma pessoa registrando OUTRO @: ja tem conta.
        r = await c.post("/api/instagram/contas", json=_corpo(pid, instagram_username="outro.arroba"))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "persona_in_use"
    assert harness.state.db.scalar("SELECT COUNT(*) FROM contas_igfarm") == 1


async def test_codigo_da_conta_com_leitor_falso(harness: Harness) -> None:
    pid = _persona(harness)
    alvo = f"ana.lima1234@{DOM}"
    msgs = [Mensagem(remetente="security@mail.instagram.com", destinatarios=(alvo,), assunto="123456 e seu codigo",
                     corpo="", recebida_em=AGORA - timedelta(minutes=5)),
            Mensagem(remetente="security@mail.instagram.com", destinatarios=(alvo,), assunto="654321 e seu codigo",
                     corpo="", recebida_em=AGORA - timedelta(minutes=1)),
            Mensagem(remetente="security@mail.instagram.com", destinatarios=(f"outra@{DOM}",), assunto="999999",
                     corpo="", recebida_em=AGORA)]
    async with _cliente(harness, LeitorFalso(msgs)) as c:
        conta = (await c.post("/api/instagram/contas", json=_corpo(pid))).json()
        r = await c.get(f"/api/instagram/contas/{conta['account_id']}/codigo")
        assert r.status_code == 200 and r.json()["codigo"] == "654321"
        assert "instagram" in r.json()["remetente"]
        r = await c.get(f"/api/instagram/contas/{conta['igfarm_account_id']}/codigo")
        assert r.status_code == 200
        r = await c.get("/api/instagram/contas/acc-nao-existe/codigo")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    async with _cliente(harness, LeitorFalso([])) as c:
        r = await c.get(f"/api/instagram/contas/{conta['account_id']}/codigo")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_codigo"
    async with _cliente(harness, None) as c:
        r = await c.get(f"/api/instagram/contas/{conta['account_id']}/codigo")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "email_indisponivel"


async def test_apagar_a_persona_leva_caixa_e_marca_do_igfarm(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        assert (await c.post("/api/instagram/contas", json=_corpo(pid))).status_code == 201
    harness.state.db.execute("DELETE FROM instagram_profiles WHERE id=?", (pid,))
    for tabela in ("contas_igfarm", "caixas_email", "persona_reservas"):
        assert harness.state.db.scalar(f"SELECT COUNT(*) FROM {tabela}") == 0  # noqa: S608
