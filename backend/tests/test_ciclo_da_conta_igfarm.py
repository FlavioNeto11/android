"""31.333: `GET /api/instagram/contas/{id}/ciclo`, o que aconteceu com a conta que o igfarm criou (criada, registrada, cada
contato com o app e retirada). Só leitura e sem segredo.

Por quê: a conta B da bifurcação de 10/10/2026 nasceu, foi tocada duas vezes pelo app (modal vazio +18 min; "Can't find
account" +56 min) e sumiu; sem esta visão, o intervalo "criada → 1º contato → desfecho" só existia espalhado em tabelas.

Nível de prova: `simulated` (aparelho e Instagram falsos; tentativas gravadas pelo próprio repositório de sessão).
"""
from __future__ import annotations

import httpx
import pytest

from app.main import create_app
from app.models import PersonaCreate
from app.modules.email_do_parque.application.servico import ConfigEmail, EmailDoParque
from app.modules.identity.domain.ponte_igfarm import minutos_entre
from app.social.contas_nossas import registrar_lapide

from .conftest import Harness


DOM = "nvit.com.br"
SENHA_EMAIL = "Senha-do-email-9f3a"
SENHA_IG = "Senha-do-insta-71cc"
CRIADA = "2026-10-09T11:30:00Z"


def _cliente(harness: Harness) -> httpx.AsyncClient:
    harness.state.email_parque = EmailDoParque(ConfigEmail(DOM, (DOM,), False, "instagram"), None)
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _persona(h: Harness) -> str:
    return h.state.social.create_persona(PersonaCreate(
        name="Ana Lima", first_name="Ana", last_name="Lima", birth_date="1990-05-17", locale="pt-BR")).id


async def _registrar(c: httpx.AsyncClient, pid: str) -> dict[str, object]:
    r = await c.post("/api/instagram/contas", json={
        "persona_id": pid, "dominio": DOM, "email": f"ana.lima1234@{DOM}", "email_senha": SENHA_EMAIL,
        "instagram_username": "ana.ceramica", "instagram_senha": SENHA_IG, "igfarm_account_id": "ig-777",
        "criada_em": CRIADA})
    assert r.status_code == 201, r.text
    dado: dict[str, object] = r.json()
    return dado


def _tentativa(h: Harness, pid: str, conta: str, inicio: str, desfecho: str, detalhe: str, etapa: str = "classified") -> None:
    h.state.db.execute(
        "INSERT INTO authentication_attempts(profile_id, instance_id, started_at, finished_at, outcome, stage, detail,"
        " account_id) VALUES (?,?,?,?,?,?,?,?)", (pid, "android-02", inicio, inicio, desfecho, etapa, detalhe, conta))


@pytest.mark.asyncio
async def test_sem_contato_o_ciclo_mostra_so_a_criacao(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        conta = await _registrar(c, pid)
        r = await c.get(f"/api/instagram/contas/{conta['account_id']}/ciclo")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["estado"] == "ativa" and d["retirada_em"] is None and d["contatos"] == []
        assert d["minutos_ate_o_primeiro_contato"] is None and d["ultimo_desfecho"] is None
        assert d["criada_em"] == CRIADA or d["criada_em"].startswith("2026-10-09T11:30")
        # O id do igfarm também serve.
        assert (await c.get("/api/instagram/contas/ig-777/ciclo")).json()["account_id"] == conta["account_id"]


@pytest.mark.asyncio
async def test_contatos_em_ordem_com_minutos_desde_a_criacao_e_o_desfecho_novo(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        conta = await _registrar(c, pid)
        aid = str(conta["account_id"])
        _tentativa(harness, pid, aid, "2026-10-09T11:48:00.000Z", "uncertain",
                   "não foi possível classificar a tela (nenhum sinal conhecido na tela)")
        _tentativa(harness, pid, aid, "2026-10-09T12:26:30.000Z", "conta_nao_encontrada",
                   "o app informou que não existe conta com o identificador usado (identificador a***@nvit.com.br)")
        d = (await c.get(f"/api/instagram/contas/{aid}/ciclo")).json()
    assert [t["desfecho"] for t in d["contatos"]] == ["uncertain", "conta_nao_encontrada"]
    assert [t["minutos_desde_a_criacao"] for t in d["contatos"]] == [18.0, 56.5]
    assert d["minutos_ate_o_primeiro_contato"] == 18.0
    assert d["ultimo_desfecho"] == "conta_nao_encontrada"
    assert d["estado"] == "ativa", "conta_nao_encontrada não retira a conta"


@pytest.mark.asyncio
async def test_conta_retirada_mostra_estado_e_hora_e_as_tentativas_sobrevivem(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        conta = await _registrar(c, pid)
        aid = str(conta["account_id"])
        _tentativa(harness, pid, aid, "2026-10-09T11:52:00.000Z", "auth_challenge", "o Instagram exige confirmação adicional")
        registrar_lapide(harness.state.db, app_id="instagram", handle="ana.ceramica", profile_id=pid)
        d = (await c.get(f"/api/instagram/contas/{aid}/ciclo")).json()
    assert d["estado"] == "retirada" and d["retirada_em"]
    assert d["contatos"][0]["desfecho"] == "auth_challenge" and d["minutos_ate_o_primeiro_contato"] == 22.0


@pytest.mark.asyncio
async def test_conta_desconhecida_e_404_e_nada_de_segredo_na_resposta(harness: Harness) -> None:
    pid = _persona(harness)
    async with _cliente(harness) as c:
        conta = await _registrar(c, pid)
        assert (await c.get("/api/instagram/contas/nao-existe/ciclo")).status_code == 404
        corpo = (await c.get(f"/api/instagram/contas/{conta['account_id']}/ciclo")).text
    for proibido in (SENHA_EMAIL, SENHA_IG, "proxy", "ip_criacao", "secret"):
        assert proibido not in corpo


def test_minutos_entre_aceita_z_e_offset_e_recusa_lixo() -> None:
    assert minutos_entre("2026-10-10T18:40:29.780086+00:00", "2026-10-10T18:58:53.383Z") == 18.4
    assert minutos_entre("2026-10-10T18:40:00Z", "2026-10-10T18:40:30Z") == 0.5
    assert minutos_entre(None, "2026-10-10T18:40:30Z") is None
    assert minutos_entre("ontem", "2026-10-10T18:40:30Z") is None
