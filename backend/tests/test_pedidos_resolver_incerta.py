"""Item 28.21 — a pessoa resolve uma ocorrência `incerta` (`POST /api/pedidos/{id}/ocorrencias/{oid}/resolver`).

Prova `simulated` (`arquivo::teste`): laço de pedidos de verdade, `RunService` com o planejamento desligado, aparelhos
falsos e relógio escrito à mão; a ocorrência vira `incerta` pelo MESMO caminho do 28.5 (falha com efeito possível), então
o pedido chega a `aguardando_pessoa` com a mesma pendência que o achado do 28.12 mostrou presa. NADA aqui prova o ambiente
real. O achado: `retomar` recusava com 409 `pendencia_aberta` para sempre, porque a incerta só saía das pendências quando
uma ocorrência POSTERIOR concluía, e um pedido parado não materializa nada; a única saída era cancelar.

Cobre: retomar recusado antes; nota obrigatória (vazia, em branco ou ausente); quem, quando e a nota gravados com a
ocorrência ainda `incerta`; pendências vazias e retomar ativo; repetir idempotente; ocorrência não incerta (409), de outro
pedido ou inexistente (404); o evento `pedido.ocorrencia.updated`; o operador da sessão; e o 401 sem credencial.
"""
from __future__ import annotations

import pytest_asyncio
from starlette.testclient import TestClient

from app.main import create_app
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.util import to_iso

from .conftest import Harness
from .test_pedidos_laco import _agora, _ocs
from .test_pedidos_retentativa import _acao, _falhar_a_ultima, _laco
from .test_sessao_do_painel import NOME, _cliente as _cliente_httpx, _expor
from .test_travas import Relogio


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    return harness


def _cliente(h: Harness) -> TestClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state          # sem `with TestClient`, o lifespan não roda: o estado entra à mão
    return TestClient(app, client=("127.0.0.1", 123))


def _incerta(h: Harness) -> tuple[Relogio, LacoDePedidos, str]:
    """Um pedido `aguardando_pessoa` por uma ocorrência `incerta` (falha com efeito possível, como o 28.5 fecha)."""
    r, db = Relogio(), h.state.db
    h.state.pedidos.relogio = r          # o `resolvida_em` sai do relógio do serviço de pedidos
    laco, _ = _laco(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    run_id = _falhar_a_ultima(db)
    _acao(db, run_id, tool="tap", status="done", efeito=1, commit=1)
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "incerta" and db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "aguardando_pessoa"
    return r, laco, o["id"]


def _url(oid: str, pid: str = "ped1") -> str:
    return f"/api/pedidos/{pid}/ocorrencias/{oid}/resolver"


async def test_retomar_e_recusado_enquanto_a_incerta_nao_foi_resolvida(h: Harness) -> None:
    _, _, oid = _incerta(h)
    c = _cliente(h)
    r = c.post("/api/pedidos/ped1/retomar", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pendencia_aberta"
    assert [(x["tipo"], x["ref"]) for x in r.json()["detail"]["pendencias"]] == [("ocorrencia_incerta", oid)]


async def test_a_nota_e_obrigatoria_vazia_em_branco_ou_ausente(h: Harness) -> None:
    _, _, oid = _incerta(h)
    c = _cliente(h)
    for corpo in ({"nota": ""}, {"nota": "   \n "}, {}):
        r = c.post(_url(oid), json=corpo)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "nota_obrigatoria", corpo
    assert c.post(_url(oid), json={"nota": "x" * 501}).status_code == 422, "o teto de 500 caracteres da quarentena"
    assert h.state.db.one("SELECT resolvida_em FROM pedido_ocorrencias WHERE id=?", (oid,))["resolvida_em"] is None


async def test_resolver_grava_quem_quando_e_a_nota_e_a_ocorrencia_continua_incerta(h: Harness) -> None:
    r, _, oid = _incerta(h)
    r.avancar(120)
    c = _cliente(h)
    resp = c.post(f"/api/pedidos/ped1/ocorrencias/{oid}/resolver", json={"nota": "  Conferi no aparelho: a mensagem não saiu.  "})
    assert resp.status_code == 200, resp.text
    o = resp.json()
    assert o["id"] == oid and o["estado"] == "incerta", "conferir não muda o estado"
    assert o["resolvida_nota"] == "Conferi no aparelho: a mensagem não saiu.", "a nota sai sem as bordas em branco"
    assert o["resolvida_por"] == "panel", "sem sessão, o rótulo de sempre da trilha"
    assert o["resolvida_em"] == to_iso(r.t), "o relógio do serviço, não o do banco"
    linha = h.state.db.one("SELECT estado, resolvida_em, resolvida_por, resolvida_nota FROM pedido_ocorrencias WHERE id=?", (oid,))
    assert (linha["estado"], linha["resolvida_em"], linha["resolvida_por"]) == ("incerta", o["resolvida_em"], "panel")
    assert len(h.state.db.query("SELECT id FROM runs WHERE pedido_id='ped1'")) == 1, "nada foi reexecutado"
    # a leitura do pedido e a lista de ocorrências levam os campos novos
    listada = next(x for x in c.get("/api/pedidos/ped1/ocorrencias").json()["items"] if x["id"] == oid)
    assert listada["resolvida_nota"] == o["resolvida_nota"] and listada["estado"] == "incerta"
    recente = next(x for x in c.get("/api/pedidos/ped1").json()["ocorrencias_recentes"] if x["id"] == oid)
    assert recente["resolvida_por"] == "panel"


async def test_depois_de_resolver_nao_ha_pendencia_e_retomar_volta_ao_ativo(h: Harness) -> None:
    _, _, oid = _incerta(h)
    c = _cliente(h)
    assert len(c.get("/api/pedidos/ped1").json()["pendencias"]) == 1
    assert c.post(_url(oid), json={"nota": "conferido"}).status_code == 200
    d = c.get("/api/pedidos/ped1").json()
    assert d["estado"] == "aguardando_pessoa" and d["pendencias"] == [], "o pedido segue esperando até alguém retomar"
    assert c.get("/api/snapshot").json()["pedidos"]["aguardando_pessoa"][0]["pendencias"] == []
    livre = c.post("/api/pedidos/ped1/retomar", json={})
    assert livre.status_code == 200 and livre.json()["pedido"]["estado"] == "ativo" and livre.json()["sem_mudanca"] is False


async def test_resolver_uma_incerta_nao_libera_a_pendencia_de_outra_natureza(h: Harness) -> None:
    """Só a incerta sai da lista: uma pergunta aberta de uma execução do mesmo pedido continua segurando o retomar."""
    _, _, oid = _incerta(h)
    db = h.state.db
    db.execute("UPDATE runs SET status='needs_input' WHERE pedido_id='ped1'")
    c = _cliente(h)
    assert c.post(_url(oid), json={"nota": "conferido"}).status_code == 200
    r = c.post("/api/pedidos/ped1/retomar", json={})
    assert r.status_code == 409 and [x["tipo"] for x in r.json()["detail"]["pendencias"]] == ["pergunta"]


async def test_repetir_e_idempotente_e_nao_reescreve_quem_quando_nem_a_nota(h: Harness) -> None:
    r, _, oid = _incerta(h)
    c = _cliente(h)
    primeira = c.post(_url(oid), json={"nota": "primeira conferência"}).json()
    r.avancar(600)
    repetida = c.post(_url(oid), json={"nota": "outra nota, que não deve valer"})
    assert repetida.status_code == 200
    assert repetida.json() == primeira, "200 com o que foi gravado na primeira vez"
    assert len(h.state.db.query("SELECT id FROM events WHERE kind='pedido.ocorrencia.updated' AND data LIKE ?",
                                ('%primeira conferência%',))) == 1, "repetir não reemite o evento"


async def test_ocorrencia_que_nao_esta_incerta_e_409(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    h.state.pedidos.relogio = r
    laco, _ = _laco(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [o] = _ocs(db)
    assert o["estado"] == "despachada"
    c = _cliente(h)
    resp = c.post(_url(o["id"]), json={"nota": "conferido"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "invalid_state"
    assert resp.json()["detail"]["estado"] == "despachada"
    assert db.one("SELECT resolvida_em FROM pedido_ocorrencias WHERE id=?", (o["id"],))["resolvida_em"] is None


async def test_ocorrencia_de_outro_pedido_ou_inexistente_e_404(h: Harness) -> None:
    r, _, oid = _incerta(h)
    db = h.state.db
    _agora(db, "ped2", r)
    c = _cliente(h)
    for url in (_url(oid, "ped2"), _url("nao-existe"), _url(oid, "ped-que-nao-existe")):
        resp = c.post(url, json={"nota": "conferido"})
        assert resp.status_code == 404, url
        assert resp.json()["detail"]["code"] == "not_found"
    assert db.one("SELECT resolvida_em FROM pedido_ocorrencias WHERE id=?", (oid,))["resolvida_em"] is None, "nada gravado"


async def test_resolver_emite_o_evento_da_ocorrencia_com_os_campos_novos(h: Harness) -> None:
    _, _, oid = _incerta(h)
    antes = h.state.db.scalar("SELECT COUNT(*) FROM events WHERE kind='pedido.ocorrencia.updated'")
    c = _cliente(h)
    assert c.post(_url(oid), json={"nota": "conferido no aparelho"}).status_code == 200
    ev = h.state.db.query("SELECT * FROM events WHERE kind='pedido.ocorrencia.updated' ORDER BY id")
    assert len(ev) == antes + 1
    assert "conferido no aparelho" in ev[-1]["data"] and ev[-1]["level"] == "warn", "segue incerta: o nível não mudou"


async def test_com_sessao_o_resolvida_por_e_o_nome_do_operador(h: Harness) -> None:
    _, _, oid = _incerta(h)
    async with _cliente_httpx(h, base="http://127.0.0.1") as c:
        assert (await c.post("/api/login", json={"operator": NOME})).status_code == 200
        r = await c.post(_url(oid), json={"nota": "conferido"})
    assert r.status_code == 200, r.text
    assert r.json()["resolvida_por"] == NOME


async def test_sem_credencial_a_rota_responde_401_e_nada_e_gravado(h: Harness) -> None:
    _, _, oid = _incerta(h)
    _expor(h)
    async with _cliente_httpx(h, base="http://parque.local") as c:
        r = await c.post(_url(oid), json={"nota": "conferido"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthorized"
    assert h.state.db.one("SELECT resolvida_em FROM pedido_ocorrencias WHERE id=?", (oid,))["resolvida_em"] is None


async def test_a_regra_antiga_fica_uma_posterior_concluida_tambem_libera_a_incerta(h: Harness) -> None:
    """Sem resolver: se uma ocorrência POSTERIOR concluiu (o pedido seguiu por outro caminho), a incerta sai das pendências."""
    _, _, oid = _incerta(h)
    db = h.state.db
    db.execute("INSERT INTO pedido_ocorrencias(id, pedido_id, pedido_versao, gatilho_id, previsto_para, chave, origem,"
               " estado, criada_em, terminada_em) VALUES ('oc-posterior', 'ped1', 1, NULL, '2999-01-01T00:00:00Z',"
               " 'chave-posterior', 'manual', 'concluida', '2999-01-01T00:00:00.000Z', '2999-01-01T00:00:00.000Z')")
    assert _cliente(h).get("/api/pedidos/ped1").json()["pendencias"] == [], oid
