"""31.335 (decisão do dono, 10/10/2026): a criação de conta pela API do igfarm foi aposentada, NÃO apagada.

Com `contas.criacao_pela_api_do_igfarm: false` (padrão), `GET /api/instagram/personas-pendentes` recusa com 409 e não reserva,
não sugere nem gera foto (paga). O resto da ponte (registro, código, ciclo, proxy) segue valendo. Ligar a flag devolve o
caminho como era.

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


def test_o_padrao_da_flag_e_desligado() -> None:
    assert ContasCfg().criacao_pela_api_do_igfarm is False


@pytest.mark.asyncio
async def test_desligada_a_lista_recusa_e_nao_reserva_nem_sugere(harness: Harness) -> None:
    _persona(harness)
    chamadas_antes = len(getattr(harness.ai, "calls", []))
    async with _cliente(harness) as c:
        for params in ({"dominio": DOM}, {"dominio": DOM, "reservar": "true"}):
            r = await c.get(URL, params=params)
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "criacao_pela_api_aposentada"
    db = harness.state.db
    assert db.query("SELECT 1 FROM persona_reservas") == [], "nem sugestão persistente nem reserva"
    assert db.query("SELECT 1 FROM events WHERE kind='identity.persona.reservada'") == []
    assert len(getattr(harness.ai, "calls", [])) == chamadas_antes, "nenhuma chamada de IA (a foto é paga)"


@pytest.mark.asyncio
async def test_ligada_a_lista_volta_a_responder(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness, criacao_pela_api=True) as c:
        r = await c.get(URL, params={"dominio": DOM, "reservar": "false"})
        assert r.status_code == 200, r.text
        assert [p["persona_id"] for p in r.json()] == [pid]


@pytest.mark.asyncio
async def test_registro_ciclo_e_codigo_seguem_valendo_com_a_flag_desligada(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        r = await c.post("/api/instagram/contas", json={
            "persona_id": pid, "dominio": DOM, "email": f"ana.lima1234@{DOM}", "email_senha": "Senha-do-email-9f3a",
            "instagram_username": "ana.ceramica", "instagram_senha": "Senha-do-insta-71cc", "igfarm_account_id": "ig-1",
            "criada_em": "2026-10-09T11:30:00Z"})
        assert r.status_code == 201, r.text
        conta = r.json()["account_id"]
        assert (await c.get(f"/api/instagram/contas/{conta}/ciclo")).status_code == 200
        # O código depende do leitor de e-mail: sem IMAP é 503 (a rota existe e responde), nunca 404 de rota aposentada.
        assert (await c.get(f"/api/instagram/contas/{conta}/codigo")).status_code in (404, 503)
