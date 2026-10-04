"""Item 28.10, F1 — a colaboração entre pedidos pela API (`/api/pedidos`), só a ESTRUTURA.

Prova `simulated` (`arquivo::teste`): `TestClient`, SQLite, relógio falso e provedor de IA simulado, como `test_pedidos_api.py`.
Cobre: desligada recusa `pai_id`, `papel` e `dependencias` (422 `colaboracao_desligada`) e o resto fica como era; ligada
cria pai, filhos e dependência e o detalhe os mostra; os 422 principais; a prévia valida sem gravar; cancelar o pai cancela
os filhos; o pai encerrado pelo sistema encerra os filhos; e o laço NÃO muda (a ocorrência do filho é materializada e
despachada como a de um pedido comum). Os códigos `linhagem` e a maior parte do `ciclo` só se alcançam no domínio na F1
(`test_pedidos_colaboracao_dominio.py`): o `pai_id` é imutável e um pedido novo não tem quem aponte para ele.
"""
from __future__ import annotations

import json

import pytest_asyncio
from starlette.testclient import TestClient

from app.config import ColaboracaoCfg, PedidosCfg
from app.main import create_app
from app.modules.pedidos.domain.previa import id_do_pedido
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.taskqueue.travas import Lideranca

from .conftest import Harness
from .test_pedidos_api import DIARIO, OBJETIVO, _corpo
from .test_travas import Relogio

AGORA = {"tipo": "agora", "spec": {}}


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    harness.state.pedidos.relogio = Relogio()
    return harness


def _ligar(h: Harness, **kw) -> None:
    h.state.pedidos_api.cfg.colaboracao = ColaboracaoCfg(enabled=True, **kw)


def _cliente(h: Harness) -> TestClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return TestClient(app, client=("127.0.0.1", 123))


def _post(c: TestClient, chave: str, **kw):
    """Cria (e ativa) um pedido pela prévia confirmada; devolve a resposta da criação."""
    previa = c.post("/api/pedidos/previa", json=_corpo(**kw)).json()
    assert previa["valido"], previa["bloqueios"]
    return c.post("/api/pedidos", json={**_corpo(**kw), "idempotency_key": chave, "confirmacao": previa["confirmacao"]})


def _tentar(c: TestClient, chave: str, pai: str | None = None, **kw):
    """Vai direto à criação (sem a prévia confirmada): para os casos em que a recusa é o que se quer ver."""
    if pai is not None:
        kw.setdefault("orcamento_total_usd", 2.0)
        kw["pai_id"] = pai
    return c.post("/api/pedidos", json={**_corpo(**kw), "idempotency_key": chave})


def _pai(c: TestClient, chave: str = "pai-de-teste-0001", **kw) -> str:
    r = _post(c, chave, orcamento_total_usd=10.0, **kw)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _filho(c: TestClient, pai: str, chave: str, **kw):
    kw.setdefault("orcamento_total_usd", 2.0)
    return _post(c, chave, pai_id=pai, **kw)


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


# =============================================================================================== desligada
async def test_desligada_recusa_pai_papel_e_dependencias_e_o_resto_fica_como_hoje(h: Harness) -> None:
    c = _cliente(h)
    for extra in ({"pai_id": "qualquer"}, {"papel": "pesquisador"}, {"dependencias": [{"de": "x", "tipo": "depois_de"}]}):
        r = c.post("/api/pedidos/previa", json=_corpo(**extra))
        assert r.status_code == 200 and not r.json()["valido"]
        assert r.json()["bloqueios"][0]["codigo"] == "colaboracao_desligada"
        r = c.post("/api/pedidos", json={**_corpo(**extra), "idempotency_key": "desligada-0001"})
        assert r.status_code == 422 and _codigo(r) == "colaboracao_desligada", r.text
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedidos") == 0
    p = _post(c, "comum-0000001")
    assert p.status_code == 201 and p.json()["papel"] is None and p.json()["pai_id"] is None
    d = c.get(f"/api/pedidos/{p.json()['id']}").json()
    assert d["filhos"] == [] and d["dependencias"] == []


# =============================================================================================== ligada
async def test_ligada_cria_pai_filhos_e_dependencia_e_o_detalhe_mostra(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c, papel="porta_voz")
    a = _filho(c, pai, "filho-a-000001", papel="pesquisador", dependencias=[{"de": pai, "tipo": "depois_de"}])
    assert a.status_code == 201, a.text
    a_id = a.json()["id"]
    assert a.json()["pai_id"] == pai and a.json()["papel"] == "pesquisador"
    b = _filho(c, pai, "filho-b-000001", papel="checador", dependencias=[{"de": a_id, "tipo": "precisa_de_resultado"}])
    assert b.status_code == 201, b.text
    b_id = b.json()["id"]
    d = c.get(f"/api/pedidos/{pai}").json()
    assert d["papel"] == "porta_voz"
    # o relógio do harness é fixo: `criado_em` empata e a ordem é a do desempate por id, então compara-se como conjunto
    assert {(f["id"], f["papel"], f["estado"]) for f in d["filhos"]} == {(a_id, "pesquisador", "ativo"), (b_id, "checador", "ativo")}
    assert all(f["titulo"] for f in d["filhos"])
    assert {(x["de"], x["para"], x["tipo"]) for x in d["dependencias"]} == {
        (pai, a_id, "depois_de"), (a_id, b_id, "precisa_de_resultado")}
    assert c.get(f"/api/pedidos/{b_id}").json()["filhos"] == []
    lista = c.get("/api/pedidos").json()["items"]
    assert {i["id"]: i["papel"] for i in lista}[a_id] == "pesquisador"


async def test_repetir_a_chave_devolve_o_mesmo_e_outra_estrutura_e_conflito(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c)
    primeiro = _filho(c, pai, "filho-rep-0001", papel="redator")
    assert primeiro.status_code == 201
    de_novo = c.post("/api/pedidos", json={**_corpo(orcamento_total_usd=2.0, pai_id=pai, papel="redator"),
                                           "idempotency_key": "filho-rep-0001"})
    assert de_novo.status_code == 200 and de_novo.json()["deduplicated"] and de_novo.json()["id"] == primeiro.json()["id"]
    outro = c.post("/api/pedidos", json={**_corpo(orcamento_total_usd=2.0, pai_id=pai, papel="checador"),
                                         "idempotency_key": "filho-rep-0001"})
    assert outro.status_code == 409 and _codigo(outro) == "idempotency_conflict"


# =============================================================================================== os 422
async def test_recusas_de_estrutura(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c)
    assert _codigo(_tentar(c, "f-sem-pai-0001", pai="nao-existe-no-banco")) == "pai_inexistente"

    a = _filho(c, pai, "filho-a-000002").json()["id"]
    neto = c.post("/api/pedidos", json={**_corpo(orcamento_total_usd=1.0, pai_id=a), "idempotency_key": "neto-0000001"})
    assert neto.status_code == 422 and _codigo(neto) == "profundidade_excedida"

    autodep = id_do_pedido("auto-dep-00001")
    r = c.post("/api/pedidos", json={**_corpo(orcamento_total_usd=1.0, pai_id=pai,
                                              dependencias=[{"de": autodep, "tipo": "depois_de"}]),
                                     "idempotency_key": "auto-dep-00001"})
    assert r.status_code == 422 and _codigo(r) == "ciclo", "um pedido não depende de si mesmo"

    outra = _pai(c, "outra-arvore-001")
    fora = _tentar(c, "filho-fora-0001", pai, dependencias=[{"de": outra, "tipo": "depois_de"}])
    assert fora.status_code == 422 and _codigo(fora) == "dependencia_fora_da_familia"
    inex = _tentar(c, "filho-inex-0001", pai, dependencias=[{"de": "fantasma", "tipo": "depois_de"}])
    assert _codigo(inex) == "dependencia_fora_da_familia"

    _filho(c, pai, "porta-voz-00001", papel="porta_voz")
    dup = _tentar(c, "porta-voz-00002", pai, papel="porta_voz", orcamento_total_usd=1.0)
    assert dup.status_code == 422 and _codigo(dup) == "porta_voz_duplicado"

    estourou = _tentar(c, "orc-estoura-001", pai, orcamento_total_usd=50.0)
    assert estourou.status_code == 422 and _codigo(estourou) == "orcamento_do_pai"
    sem = c.post("/api/pedidos", json={**_corpo(pai_id=pai), "idempotency_key": "orc-sem-000001"})
    assert sem.status_code == 422 and _codigo(sem) == "orcamento_do_pai" and "declarar" in sem.json()["detail"]["message"]
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedidos WHERE pai_id=?", (pai,)) == 2, "nada das recusas foi gravado"


async def test_o_sexto_filho_e_recusado_com_o_limite_da_config(h: Harness) -> None:
    _ligar(h, max_filhos=2)
    c = _cliente(h)
    pai = _pai(c)
    for i in range(2):
        assert _filho(c, pai, f"filho-n-00000{i}", orcamento_total_usd=1.0).status_code == 201
    r = _tentar(c, "filho-n-000009", pai, orcamento_total_usd=1.0)
    assert r.status_code == 422 and _codigo(r) == "filhos_demais"


async def test_pai_terminal_nao_ganha_filho(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c)
    assert c.post(f"/api/pedidos/{pai}/cancelar", json={"confirmar": True}).status_code == 200
    r = _tentar(c, "filho-tarde-001", pai)
    assert r.status_code == 422 and _codigo(r) == "pai_terminal"


async def test_papel_e_tipo_fora_do_vocabulario_sao_422_da_forma(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    assert c.post("/api/pedidos/previa", json=_corpo(papel="chefe")).status_code == 422
    assert c.post("/api/pedidos/previa", json=_corpo(dependencias=[{"de": "x", "tipo": "talvez"}])).status_code == 422
    r = c.post("/api/pedidos/previa", json=_corpo(dependencias=[{"de": "x", "tipo": "depois_de"}]))
    assert r.json()["bloqueios"][0]["codigo"] == "dependencia_fora_da_familia", "raiz não tem família"


# =============================================================================================== prévia
async def test_previa_valida_a_estrutura_sem_gravar(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c)
    antes = h.state.db.scalar("SELECT COUNT(*) FROM pedidos")
    ok = c.post("/api/pedidos/previa", json=_corpo(pai_id=pai, orcamento_total_usd=2.0, papel="redator")).json()
    assert ok["valido"] and ok["bloqueios"] == []
    ruim = c.post("/api/pedidos/previa", json=_corpo(pai_id=pai, orcamento_total_usd=99.0)).json()
    assert not ruim["valido"] and ruim["bloqueios"][0]["codigo"] == "orcamento_do_pai" and ruim["confirmacao"] is None
    sem_pai = c.post("/api/pedidos/previa", json=_corpo(pai_id="fantasma", orcamento_total_usd=2.0)).json()
    assert sem_pai["bloqueios"][0]["codigo"] == "pai_inexistente"
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedidos") == antes
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedido_dependencias") == 0


# =============================================================================================== cascata
async def test_cancelar_o_pai_cancela_os_filhos_vivos(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c)
    vivo = _filho(c, pai, "filho-vivo-0001", gatilhos=[DIARIO]).json()["id"]
    pausado = _filho(c, pai, "filho-paus-0001", orcamento_total_usd=1.0).json()["id"]
    assert c.post(f"/api/pedidos/{pausado}/pausar", json={}).status_code == 200
    ja_cancelado = _filho(c, pai, "filho-canc-0001", orcamento_total_usd=1.0).json()["id"]
    c.post(f"/api/pedidos/{ja_cancelado}/cancelar", json={"confirmar": True})
    sem = c.post(f"/api/pedidos/{pai}/cancelar", json={})
    assert sem.status_code == 409 and sem.json()["detail"]["code"] == "confirmacao_necessaria"
    r = c.post(f"/api/pedidos/{pai}/cancelar", json={"confirmar": True})
    assert r.status_code == 200 and r.json()["filhos_cancelados"] == 2
    for i in (pai, vivo, pausado, ja_cancelado):
        assert c.get(f"/api/pedidos/{i}").json()["estado"] == "cancelado"
    motivos = {o["motivo"] for o in h.state.db.query("SELECT motivo FROM pedido_ocorrencias WHERE pedido_id=?", (vivo,))}
    assert motivos <= {"o pedido pai foi cancelado"}
    assert c.post(f"/api/pedidos/{pai}/cancelar", json={"confirmar": True}).json()["sem_mudanca"] is True


async def test_cancelar_um_filho_nao_toca_o_pai_nem_os_irmaos(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c)
    a = _filho(c, pai, "filho-a-000003").json()["id"]
    b = _filho(c, pai, "filho-b-000003", orcamento_total_usd=1.0).json()["id"]
    c.post(f"/api/pedidos/{a}/cancelar", json={"confirmar": True})
    assert c.get(f"/api/pedidos/{pai}").json()["estado"] == "ativo" and c.get(f"/api/pedidos/{b}").json()["estado"] == "ativo"


async def test_pai_encerrado_pelo_sistema_encerra_os_filhos_com_o_motivo_pai(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    pai = _pai(c)
    ativo = _filho(c, pai, "filho-ativ-0001", gatilhos=[DIARIO]).json()["id"]
    rasc = c.post("/api/pedidos", json={**_corpo(orcamento_total_usd=1.0, pai_id=pai), "idempotency_key": "filho-rasc-0001"}).json()["id"]
    assert c.get(f"/api/pedidos/{rasc}").json()["estado"] == "rascunho"
    concl = _filho(c, pai, "filho-conc-0001", orcamento_total_usd=1.0).json()["id"]
    h.state.db.execute("UPDATE pedidos SET estado='concluido' WHERE id=?", (concl,))
    # o laço (ou o prazo) encerra o pai: a marca passa pelo funil de publicação, onde a cascata roda
    assert h.state.pedidos.repo.mudar_estado_do_pedido(pai, "ativo", "encerrado", "2026-10-04T10:00:00.000Z",
                                                       encerrado_motivo="prazo")
    h.state.pedidos_api.publicar(h.state.pedidos.repo.descarregar())
    assert c.get(f"/api/pedidos/{pai}").json()["encerrado_motivo"] == "prazo"
    for i in (ativo, rasc):
        f = c.get(f"/api/pedidos/{i}").json()
        assert f["estado"] == "encerrado" and f["encerrado_motivo"] == "pai"
    assert c.get(f"/api/pedidos/{concl}").json()["estado"] == "concluido", "o que já terminou fica como está"
    motivos = {o["motivo"] for o in h.state.db.query("SELECT motivo FROM pedido_ocorrencias WHERE pedido_id=?", (ativo,))}
    assert motivos <= {"o pedido pai foi encerrado"}
    # repetir é seguro
    h.state.pedidos_api.publicar([("pedido", pai, False)])
    assert c.get(f"/api/pedidos/{ativo}").json()["estado"] == "encerrado"


# =============================================================================================== o laço não muda
async def test_o_laco_trata_a_ocorrencia_do_filho_como_a_de_um_pedido_comum(h: Harness) -> None:
    _ligar(h)
    c = _cliente(h)
    r = Relogio()
    h.state.pedidos.relogio = r
    laco = LacoDePedidos(h.state.db, h.state.runs, Lideranca(h.state.db, dono="laco-colab", relogio=r),
                         PedidosCfg(enabled=True, prazo_inicio_s=604_800), relogio=r)
    laco.notificar = h.state.pedidos_api.publicar
    pai = _pai(c, gatilhos=[DIARIO])
    # o filho depende do pai (`precisa_de_resultado`) e é porta-voz: na F1 nada disso segura ou limita a ocorrência
    f = _filho(c, pai, "filho-laco-0001", gatilhos=[AGORA], papel="porta_voz",
               dependencias=[{"de": pai, "tipo": "precisa_de_resultado"}])
    assert f.status_code == 201, f.text
    fid = f.json()["id"]
    res = laco.uma_volta()
    assert res.despachadas >= 1
    [o] = h.state.db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=?", (fid,))
    assert o["estado"] == "despachada" and o["run_id"], "despachada apesar da dependência: a F1 não olha para ela"
    run = h.state.db.one("SELECT pedido_id, ocorrencia_id FROM runs WHERE id=?", (o["run_id"],))
    assert (run["pedido_id"], run["ocorrencia_id"]) == (fid, o["id"])
    kinds = [json.loads(e["data"])["ocorrencia"]["pedido_id"] for e in h.state.db.query(
        "SELECT * FROM events WHERE kind='pedido.ocorrencia.updated' ORDER BY id")]
    assert fid in kinds
