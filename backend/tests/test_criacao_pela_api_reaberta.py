"""Decisão do dono (11/10/2026): quem CRIA a conta é o igfarm, pela API dele. Isso reabre o que o 31.335 (10/10) tinha aposentado.

`contas.criacao_pela_api_do_igfarm` volta a ser `true` por padrão: `GET /api/instagram/personas-pendentes` entrega as pessoas sem
conta. `false` segue sendo a chave que o desliga (409 `criacao_pela_api_aposentada`, sem reservar, sugerir nem gerar foto paga).
O consentimento da credencial da conta que o igfarm criou tem rota própria: `POST /api/instagram/contas/{id}/consentimento`.

Nível de prova: `simulated` (gerador de imagem simulado, leitor de e-mail falso). Nenhuma chamada paga nem IMAP real.
"""
from __future__ import annotations

import httpx
import pytest

from app.config import ContasCfg
from app.main import create_app
from app.models import PersonaCreate
from app.modules.email_do_parque.application.servico import ConfigEmail, EmailDoParque

from .conftest import Harness

DOM = "nvit.com.br"
URL = "/api/instagram/personas-pendentes"


def _cliente(harness: Harness, *, criacao_pela_api: bool | None = None) -> httpx.AsyncClient:
    harness.state.email_parque = EmailDoParque(ConfigEmail(DOM, (DOM,), False, "instagram"), None)
    if criacao_pela_api is not None:
        harness.cfg.file.contas.criacao_pela_api_do_igfarm = criacao_pela_api
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _persona(h: Harness) -> str:
    return h.state.social.create_persona(PersonaCreate(
        name="Ana Lima", first_name="Ana", last_name="Lima", birth_date="1990-05-17", locale="pt-BR",
        gender="feminino", summary="Ana gosta de ceramica")).id


def test_o_padrao_da_flag_e_ligado() -> None:
    assert ContasCfg().criacao_pela_api_do_igfarm is True


@pytest.mark.asyncio
async def test_desligada_pela_chave_a_lista_recusa_e_nao_reserva_nem_sugere(harness: Harness) -> None:
    _persona(harness)
    chamadas_antes = len(getattr(harness.ai, "calls", []))
    async with _cliente(harness, criacao_pela_api=False) as c:
        for params in ({"dominio": DOM}, {"dominio": DOM, "reservar": "true"}):
            r = await c.get(URL, params=params)
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "criacao_pela_api_aposentada"
    db = harness.state.db
    assert db.query("SELECT 1 FROM persona_reservas") == [], "nem sugestão persistente nem reserva"
    assert db.query("SELECT 1 FROM events WHERE kind='identity.persona.reservada'") == []
    assert len(getattr(harness.ai, "calls", [])) == chamadas_antes, "nenhuma chamada de IA (a foto é paga)"


@pytest.mark.asyncio
async def test_com_o_padrao_a_lista_responde(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        r = await c.get(URL, params={"dominio": DOM, "reservar": "false"})
        assert r.status_code == 200, r.text
        assert [p["persona_id"] for p in r.json()] == [pid]


def _corpo(pid: str) -> dict[str, object]:
    return {"persona_id": pid, "dominio": DOM, "email": f"ana.lima1234@{DOM}", "email_senha": "Senha-do-email-9f3a",
            "instagram_username": "ana.ceramica", "instagram_senha": "Senha-do-insta-71cc", "igfarm_account_id": "ig-1",
            "criada_em": "2026-10-09T11:30:00Z"}


@pytest.mark.asyncio
async def test_registro_ciclo_e_codigo_valem_com_a_chave_ligada_ou_desligada(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness, criacao_pela_api=False) as c:
        r = await c.post("/api/instagram/contas", json=_corpo(pid))
        assert r.status_code == 201, r.text
        conta = r.json()["account_id"]
        assert (await c.get(f"/api/instagram/contas/{conta}/ciclo")).status_code == 200
        # O código depende do leitor de e-mail: sem IMAP é 503 (a rota existe e responde), nunca 404 de rota aposentada.
        assert (await c.get(f"/api/instagram/contas/{conta}/codigo")).status_code in (404, 503)


@pytest.mark.asyncio
async def test_consentimento_da_conta_do_igfarm_e_idempotente_e_aceita_os_dois_ids(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        conta = (await c.post("/api/instagram/contas", json=_corpo(pid))).json()["account_id"]
        # o registro já guarda a senha com o consentimento de quem registra; a rota confirma sem regravar
        r = await c.post(f"/api/instagram/contas/{conta}/consentimento")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["account_id"] == conta and d["igfarm_account_id"] == "ig-1" and d["consent"] is True
        assert d["ja_consentida"] is True and d["consent_at"] and d["consent_by"]
        # conta cujo consentimento foi limpo (ex.: o painel): a rota o refaz como `igfarm`, uma vez só
        harness.state.db.execute("UPDATE account_credentials SET consent_at=NULL, consent_by=NULL WHERE account_id=?", (conta,))
        r2 = await c.post("/api/instagram/contas/ig-1/consentimento")        # pelo id do igfarm
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["ja_consentida"] is False and d2["consent_by"] == "igfarm" and d2["account_id"] == conta
        d3 = (await c.post(f"/api/instagram/contas/{conta}/consentimento")).json()
        assert d3["ja_consentida"] is True and d3["consent_at"] == d2["consent_at"], "idempotente: não regrava"
        assert "Senha-do" not in r.text + r2.text


@pytest.mark.asyncio
async def test_consentimento_de_conta_que_nao_e_do_igfarm_e_404(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.post("/api/instagram/contas/acc-que-nao-e-do-igfarm/consentimento")
        assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_consentimento_sem_senha_guardada_e_409(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        conta = (await c.post("/api/instagram/contas", json=_corpo(pid))).json()["account_id"]
        harness.state.db.execute("DELETE FROM account_credentials WHERE account_id=?", (conta,))
        r = await c.post(f"/api/instagram/contas/{conta}/consentimento")
        assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_toda_chamada_do_consentimento_deixa_evento_sem_segredo(harness: Harness) -> None:
    """31.344: a prova real do 31.342 é a 1ª chamada do igfarm; a idempotente e a recusada também deixam rastro."""
    pid = _persona(harness)
    db = harness.state.db
    async with _cliente(harness) as c:
        conta = (await c.post("/api/instagram/contas", json=_corpo(pid))).json()["account_id"]
        assert (await c.post(f"/api/instagram/contas/{conta}/consentimento")).status_code == 200      # já consentida pelo registro
        db.execute("UPDATE account_credentials SET consent_at=NULL, consent_by=NULL WHERE account_id=?", (conta,))
        assert (await c.post("/api/instagram/contas/ig-1/consentimento")).status_code == 200          # refeita
        assert (await c.post("/api/instagram/contas/acc-desconhecida/consentimento")).status_code == 404
    linhas = db.query("SELECT ts, message, data FROM events WHERE kind='identity.consentimento_igfarm' ORDER BY id")
    assert len(linhas) == 3
    dados = [__import__("json").loads(r["data"]) for r in linhas]
    assert [d["resultado"] for d in dados] == ["ja_consentida", "consentida", "recusada"]
    assert dados[0]["account_id"] == conta and dados[0]["igfarm_account_id"] == "ig-1" and dados[0]["ja_consentida"] is True
    assert dados[1]["conta_id"] == "ig-1" and dados[1]["account_id"] == conta and dados[1]["ja_consentida"] is False
    assert dados[2]["codigo"] == "not_found" and all(r["ts"] for r in linhas)
    todo = " ".join(str(dict(r)) for r in linhas)
    assert "Senha-do" not in todo and f"@{DOM}" not in todo and "ana.ceramica" not in todo
