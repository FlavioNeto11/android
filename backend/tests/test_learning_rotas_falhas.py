"""Rotas do "o que mais falha" (ADR-054, decisão 7; pacote A3), em nível HTTP.

- `GET /api/aprendizado/falhas?dias=&app=&camada=&limite=&simulados=&retroativo=&formato=json|md`: o relatório, com a
  chave estável `fk-*` por linha (a mesma do JSON, do md e da linha gravada), sem nunca gravar `failure_kind`;
- `GET /api/aprendizado/backlog/{id}`: a linha, gravada ou ainda só no relatório;
- `PATCH /api/aprendizado/backlog/{id}`: estado, item do plano, commit e nota; `fixed`/`reopened` são medida
  (409), `fixed_pending_proof` exige o commit (422), nota com cara de credencial é 409 sem gravar nada.

As rotas específicas entram ANTES do livro (`{kind}/{ref}` casaria `backlog/{id}` e recusaria o `kind`).

Nível de prova: `simulated` (banco de teste; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.presentation.router import router as learning_router

from .fake_skills import banco as banco_migrado
from .test_learning_backlog import PACOTE, Mundo, dias, falhando, montar


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db: Database = banco_migrado(tmp_path, "rotas-falhas.sqlite3")
    m = montar(db)
    for i in range(3):
        falhando(f"r-anr{i}", dias(2 + i * 0.1), db, erro="Tempo da etapa esgotado (180s); o app parou de responder "
                 "(ANR) — o aparelho estava sobrecarregado", chamadas=2)
    for i in range(3):
        falhando(f"r-prazo{i}", dias(1 + i * 0.1), db, acao="SEND_DM", erro="Tempo da etapa esgotado (180s)")
    yield m
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.livro)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_relatorio_em_json_com_chave_estavel(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/api/aprendizado/falhas?dias=14")
    assert r.status_code == 200, r.text
    corpo = r.json()
    tipos = {i["tipo"]: i for i in corpo["itens"]}
    assert set(tipos) == {"app_anr", "prazo_da_etapa"}                  # o ANR que venceu o prazo é ANR
    anr = tipos["app_anr"]
    assert anr["id"].startswith("fk-") and len(anr["id"]) == 13
    assert anr["app"] == PACOTE and anr["capability"] == "OPEN_POST" and anr["camada"] == "aparelho"
    assert anr["capability_nome"] == "Abrir a publicação"              # o nome do catálogo, para o grupo do painel
    assert anr["ocorrencias"] == 3 and anr["retroativas"] == 3 and anr["estado"] == "open"
    assert abs(anr["usd_perdido"] - 0.06) < 1e-9 and anr["onde_alterar"]["arquivos"]
    assert anr["onde_alterar"]["prova"] and anr["exemplos"][0]["run_id"].startswith("r-anr")
    assert corpo["janela"]["dias"] == 14 and corpo["filtros"]["retroativo"] is True
    assert corpo["outro"]["alerta"] is False and "saude" in corpo and "verificacao" in corpo
    assert mundo.db.scalar("SELECT COUNT(*) FROM attempts WHERE failure_kind IS NOT NULL") == 0
    # Mesmo id em outra leitura, filtrado por camada e por app.
    so_aparelho = (await cliente.get("/api/aprendizado/falhas?camada=aparelho&app=" + PACOTE)).json()
    assert {i["id"] for i in so_aparelho["itens"]} == {anr["id"], tipos["prazo_da_etapa"]["id"]}
    assert (await cliente.get("/api/aprendizado/falhas?camada=ia_ator")).json()["itens"] == []
    assert (await cliente.get("/api/aprendizado/falhas?retroativo=0")).json()["itens"] == []
    assert len((await cliente.get("/api/aprendizado/falhas?limite=1")).json()["itens"]) == 1
    for ruim in ("dias=0", "dias=400", "formato=xml", "camada=nada", "limite=0"):
        assert (await cliente.get(f"/api/aprendizado/falhas?{ruim}")).status_code == 422, ruim


async def test_relatorio_em_markdown(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    js = (await cliente.get("/api/aprendizado/falhas")).json()
    r = await cliente.get("/api/aprendizado/falhas?dias=14&formato=md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    md = r.text
    assert md.startswith("# O que mais falha")
    assert "retroativo" in md                                          # o legado classificado na leitura é dito
    linhas = [x for x in md.splitlines() if x.startswith("| fk-")]
    assert [x.split("|")[1].strip() for x in linhas][:2] == [i["id"] for i in js["itens"]][:2]
    for secao in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6."):
        assert secao in md, secao


async def test_backlog_get_e_patch(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    itens = (await cliente.get("/api/aprendizado/falhas")).json()["itens"]
    fk = next(i["id"] for i in itens if i["tipo"] == "app_anr")
    r = await cliente.get(f"/api/aprendizado/backlog/{fk}")
    assert r.status_code == 200, r.text
    assert r.json()["registrado"] is False and r.json()["linha"]["state"] == "open"
    assert r.json()["grupo"]["ocorrencias"] == 3
    assert (await cliente.get("/api/aprendizado/backlog/fk-0000000000")).status_code == 404
    # PATCH: o que só a medida decide é recusa; o commit é obrigatório para a prova; nota com credencial não entra.
    rota = f"/api/aprendizado/backlog/{fk}"
    assert (await cliente.patch(rota, json={"state": "fixed"})).status_code == 409
    assert (await cliente.patch(rota, json={"state": "reopened"})).status_code == 409
    assert (await cliente.patch(rota, json={"state": "fixed_pending_proof"})).status_code == 422
    assert (await cliente.patch(rota, json={"state": "planned", "x": 1})).status_code == 422
    segredo = await cliente.patch(rota, json={"state": "triaged", "notes": "a senha do lucas é hunter2"})
    assert segredo.status_code == 409 and segredo.json()["detail"]["code"] == "note_looks_secret"
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_backlog") == 0
    assert (await cliente.patch("/api/aprendizado/backlog/fk-0000000000", json={"state": "triaged"})).status_code == 404
    ok = await cliente.patch(rota, json={"state": "planned", "plan_item": "20.3", "notes": "ANR do convidado"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["linha"]["state"] == "planned" and ok.json()["linha"]["plan_item"] == "20.3"
    assert ok.json()["linha"]["updated_by"] == "panel" and ok.json()["registrado"] is True
    prova = await cliente.patch(rota, json={"state": "fixed_pending_proof", "fixed_in_commit": "0123abc"})
    assert prova.status_code == 200, prova.text
    linha = prova.json()["linha"]
    assert linha["fixed_in_commit"] == "0123abc" and linha["fixed_at"] and linha["baseline"]["ocorrencias"] == 3
    assert linha["verification"]["commit_implantado"] == "abc1234def"
    assert (await cliente.get(rota)).json()["linha"]["state"] == "fixed_pending_proof"
    # O livro continua respondendo pelas rotas genéricas dele (a ordem do roteador).
    assert (await cliente.get("/api/aprendizado/pendentes")).status_code == 200


async def test_sem_o_servico_de_falhas_a_rota_diz_503() -> None:
    app = FastAPI()
    app.include_router(learning_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/aprendizado/falhas")).status_code == 503
        assert (await c.get("/api/aprendizado/backlog/fk-0000000000")).status_code == 503
        assert (await c.patch("/api/aprendizado/backlog/fk-0000000000", json={"state": "open"})).status_code == 503
