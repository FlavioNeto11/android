"""30.38 (b): a leitura dos pedidos de validação (`GET /api/aprendizado/validacoes`, adendo v1.02). Banco migrado pela
fábrica do harness (SQLite, ou PostgreSQL com `TEST_DATABASE_URL`); sem IA. Nível de prova: `simulated`.

O que se prova:
- a ordem (mais novos primeiro), o filtro por estado, a paginação por `antes` e o limite;
- a contagem por estado de TODOS os pedidos (zero no estado sem nenhum), o total e o `modo` do despachante;
- o motivo vai em código e em texto humano (a tabela cobre todo o vocabulário), o app com o nome e o comando cortado;
- estado fora do vocabulário é 422.
"""
from __future__ import annotations

import httpx

from app.main import create_app
from app.modules.learning.application.validacao import COMANDO_NA_LISTA
from app.modules.learning.domain.validacao import MOTIVO_HUMANO, EstadoDoPedido, Motivo, motivo_humano

from .conftest import Harness

QA = "com.pocqa.messenger"


def _pedido(h: Harness, pid: str, criado: str, estado: str, *, motivo: str | None = None, comando: str = "abrir o QA",
            usd: float = 0.0, teto: float | None = None, run_id: str | None = None) -> None:
    assert h.state is not None
    h.state.db.execute(
        "INSERT INTO learning_validations(id, created_at, updated_at, review_id, item_ref, item_kind, scope_app, grupo,"
        " falta, run_origem, comando, aparelho_excluido, estado, motivo, run_id, aparelho, usd, expira_em, teto_usd)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, criado, criado, f"rv-{pid}", f"receita:{pid}", "receita", QA, "qa", "[]", "r-origem", comando, "android-01",
         estado, motivo, run_id, "android-02" if run_id else None, usd, "2026-10-06T12:00:00Z", teto))


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_todo_motivo_tem_texto_humano() -> None:
    assert set(MOTIVO_HUMANO) == set(Motivo)
    assert all(texto.strip() and texto != m.value for m, texto in MOTIVO_HUMANO.items())
    assert motivo_humano(None) is None
    assert motivo_humano("motivo_de_versao_futura") == "motivo_de_versao_futura"


async def test_a_listagem_ordena_filtra_pagina_e_conta(harness: Harness) -> None:
    _pedido(harness, "lv-1", "2026-10-03T10:00:00Z", "feita", usd=0.0512, teto=0.10, run_id="r-v1")
    _pedido(harness, "lv-2", "2026-10-03T11:00:00Z", "recusada", motivo="sem_evidencia", run_id="r-v2")
    _pedido(harness, "lv-3", "2026-10-03T12:00:00Z", "pendente", motivo="sem_aparelho", comando="x" * 500)
    async with _cliente(harness) as c:
        tudo = (await c.get("/api/aprendizado/validacoes")).json()
        so_recusadas = (await c.get("/api/aprendizado/validacoes", params={"estado": "recusada"})).json()
        pagina = (await c.get("/api/aprendizado/validacoes", params={"limite": 1, "antes": "2026-10-03T12:00:00Z"})).json()
        invalido = await c.get("/api/aprendizado/validacoes", params={"estado": "inventado"})

    assert [i["id"] for i in tudo["itens"]] == ["lv-3", "lv-2", "lv-1"]
    assert tudo["contagem"] == {e.value: {"feita": 1, "recusada": 1, "pendente": 1}.get(e.value, 0) for e in EstadoDoPedido}
    assert tudo["total"] == 3
    assert tudo["modo"] == "off"                                  # de fábrica: o painel explica a pausa
    por_id = {i["id"]: i for i in tudo["itens"]}
    assert por_id["lv-2"]["motivo"] == "sem_evidencia"
    assert por_id["lv-2"]["motivo_humano"] == MOTIVO_HUMANO[Motivo.SEM_EVIDENCIA]
    assert por_id["lv-1"]["motivo_humano"] is None
    assert len(por_id["lv-3"]["comando"]) == COMANDO_NA_LISTA
    assert por_id["lv-1"]["usd"] == 0.0512 and por_id["lv-1"]["teto_usd"] == 0.10
    assert por_id["lv-1"]["run_id"] == "r-v1" and por_id["lv-1"]["aparelho"] == "android-02"
    assert por_id["lv-1"]["app"] == QA and "app_nome" in por_id["lv-1"]

    assert [i["id"] for i in so_recusadas["itens"]] == ["lv-2"]
    assert so_recusadas["total"] == 3                             # a contagem é de todos, não do filtro
    assert [i["id"] for i in pagina["itens"]] == ["lv-2"]
    assert invalido.status_code == 422


async def test_a_listagem_filtra_por_item_e_por_execucao(harness: Harness) -> None:
    """30.43: a seção "Validações" do item (`item`) e o veredito no Resumo da execução (`run`) usam a rota de sempre."""
    _pedido(harness, "lv-1", "2026-10-03T10:00:00Z", "feita", run_id="r-v1")
    _pedido(harness, "lv-2", "2026-10-03T11:00:00Z", "recusada", motivo="sem_caminho", run_id="r-v2")
    _pedido(harness, "lv-3", "2026-10-03T12:00:00Z", "recusada", motivo="sem_caminho")
    async with _cliente(harness) as c:
        do_item = (await c.get("/api/aprendizado/validacoes", params={"item": "receita:lv-2"})).json()
        da_execucao = (await c.get("/api/aprendizado/validacoes", params={"run": "r-v1"})).json()
        nenhum = (await c.get("/api/aprendizado/validacoes", params={"run": "r-nao-existe"})).json()
    assert [i["id"] for i in do_item["itens"]] == ["lv-2"] and do_item["itens"][0]["run_id"] == "r-v2"
    assert [i["id"] for i in da_execucao["itens"]] == ["lv-1"]
    assert nenhum["itens"] == [] and nenhum["total"] == 3       # a contagem segue sendo de todos


def _evidencia(h: Harness, item_ref: str, stance: str, run_id: str, detail: str, quando: str) -> None:
    assert h.state is not None
    h.state.db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, detail, observed_at)"
                       " VALUES (?,?,?,?,?,?,?)", (item_ref, stance, f"run:{run_id}", run_id, 0, detail, quando))


async def test_o_pedido_feito_cuja_evidencia_foi_reclassificada_traz_a_invalida(harness: Harness) -> None:
    """30.45: o caso da 5f2de5 (04/10). O pedido fechou `feita` às 20:51Z; às 00:14Z a reclassificação do 30.42 deixou
    a irmã `invalida` da mesma execução. O pedido segue `feita` (o log só cresce), e a listagem traz a `invalida` para
    o veredito no Resumo e o histórico do item não dizerem "a favor"."""
    _pedido(harness, "lv-1", "2026-10-03T20:50:00Z", "feita", run_id="r-v1")
    _pedido(harness, "lv-2", "2026-10-03T21:00:00Z", "feita", run_id="r-v2")
    _evidencia(harness, "receita:lv-1", "for", "r-v1", "[3251ec6f2171] prova: 6/6 etapas comprovadas", "2026-10-03T20:51:45Z")
    _evidencia(harness, "receita:lv-1", "invalida", "r-v1",
               "[3251ec6f2171] invalida:efeito_repetido — o efeito saiu 2 vezes (reclassificada)", "2026-10-04T00:14:05Z")
    _evidencia(harness, "receita:lv-2", "invalida", "r-outra", "[3251ec6f2171] invalida:ponto_de_partida — x", "2026-10-04T00:15:00Z")
    async with _cliente(harness) as c:
        da_execucao = (await c.get("/api/aprendizado/validacoes", params={"run": "r-v1"})).json()
        tudo = (await c.get("/api/aprendizado/validacoes")).json()
    [p] = da_execucao["itens"]
    assert p["estado"] == "feita"
    assert p["invalida_depois"] == {"motivo": "efeito_repetido", "motivo_humano": MOTIVO_HUMANO[Motivo.EFEITO_REPETIDO]}
    # a `invalida` de OUTRA execução do mesmo item não conta para este pedido
    assert {i["id"]: i["invalida_depois"] for i in tudo["itens"]}["lv-2"] is None


async def test_a_invalida_sem_detalhe_legivel_le_como_sem_evidencia(harness: Harness) -> None:
    """O lado seguro do 30.42: a linha que não diz o porquê não vira "a favor" nem inventa um motivo."""
    _pedido(harness, "lv-1", "2026-10-03T20:50:00Z", "feita", run_id="r-v1")
    _evidencia(harness, "receita:lv-1", "invalida", "r-v1", "", "2026-10-04T00:14:05Z")
    async with _cliente(harness) as c:
        [p] = (await c.get("/api/aprendizado/validacoes", params={"run": "r-v1"})).json()["itens"]
    assert p["invalida_depois"]["motivo"] == "sem_evidencia"
