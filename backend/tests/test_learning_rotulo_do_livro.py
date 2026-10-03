"""RA-19, fatia A: a lista padrão do livro (`GET /api/aprendizado`) esconde os apps de teste (`apps.category='qa'`),
com o filtro `rotulo` (`produto` | `qa` | `todos`) e a contagem do que ficou escondido. O acervo de teste não sai do
livro: a visão por app e as filas de decisão continuam lendo tudo.

Nível de prova: `simulated` (banco de teste, sem aparelho nem IA).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.vocabulario import Rotulo
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router

from .fake_skills import TS
from .fake_skills import banco as banco_migrado

QA = "com.pocqa.messenger"
PRODUTO = "com.exemplo.produto"
AGORA = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _receita(db: Database, pacote: str, passo: str) -> str:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"kind": "rid", "rid": "b"}], "commit": False}]
    return str(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, replay_ok, replay_fail, consecutive_fail, shadow_agree, shadow_total,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pacote, "1.0", "sig", "pt/1", f"h-{passo}", passo, 1, "active", json.dumps(acoes), None, 0, 0, 0, 0, 0, TS)))


def _fluxo(db: Database, fid: str, app_id: str) -> None:
    plano = {"summary": fid, "parameters": {}, "steps": [
        {"key": "a", "title": "a", "goal": "a", "side_effect": False,
         "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
               " VALUES (?,?,?,?,?,?,?,?,?)",
               (fid, fid, f"cmd {fid}", f"cmd {fid}", json.dumps(plano), app_id, "active", TS, "run"))


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        # o QA embutido (como o `state.py` o semeia) e um app de produto
        db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa','QA Messenger',?,1,'qa')", (QA,))
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('prod','Produto',?,0)", (PRODUTO,))
        self.receitas_qa = [_receita(db, QA, f"qa-{i}") for i in range(3)]
        self.receita_produto = _receita(db, PRODUTO, "curtir")
        _fluxo(db, "f-qa", "qa")
        _fluxo(db, "f-produto", "prod")
        repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "rotulo_do_livro.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico, cfg=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _refs(corpo: dict[str, object]) -> set[tuple[str, str]]:
    itens = corpo["itens"]
    assert isinstance(itens, list)
    return {(str(i["kind"]), str(i["ref"])) for i in itens}


def test_pacotes_de_teste_sao_os_de_categoria_qa(mundo: Mundo) -> None:
    assert FontesSql(mundo.db).pacotes_de_teste() == frozenset({QA})


def test_servico_sem_rotulo_e_o_livro_inteiro(mundo: Mundo) -> None:
    """Quem lê o livro por dentro (visão por app, contagem da barra, saúde) não passa rótulo e vê tudo."""
    inteiro = mundo.servico.livro()
    assert len(inteiro.itens) == 6 and inteiro.ocultos == 0
    produto = mundo.servico.livro(rotulo=Rotulo.PRODUTO)
    assert {e.app for e in produto.itens} == {PRODUTO} and produto.ocultos == 4
    so_qa = mundo.servico.livro(rotulo=Rotulo.QA)
    assert {e.app for e in so_qa.itens} == {QA} and so_qa.ocultos == 2
    # a contagem acompanha o que é mostrado
    assert sum(n for por_estado in produto.contagem.values() for n in por_estado.values()) == 2


async def test_lista_padrao_esconde_o_app_de_teste_e_diz_quantos(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/api/aprendizado")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["rotulo"] == "produto" and corpo["ocultos"] == 4 and corpo["total"] == 2
    assert _refs(corpo) == {("receita", mundo.receita_produto), ("fluxo", "f-produto")}


@pytest.mark.parametrize(("rotulo", "total", "ocultos"), [("produto", 2, 4), ("qa", 4, 2), ("todos", 6, 0)])
async def test_filtro_rotulo(cliente: httpx.AsyncClient, rotulo: str, total: int, ocultos: int) -> None:
    corpo = (await cliente.get("/api/aprendizado", params={"rotulo": rotulo})).json()
    assert (corpo["rotulo"], corpo["total"], corpo["ocultos"]) == (rotulo, total, ocultos)


async def test_app_escolhido_mostra_o_que_ele_tem_mesmo_sendo_de_teste(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    """Escolher o QA Messenger no filtro de app não pode voltar vazio por causa do padrão."""
    corpo = (await cliente.get("/api/aprendizado", params={"app": QA})).json()
    assert corpo["rotulo"] == "todos" and corpo["ocultos"] == 0 and corpo["total"] == 4
    # o rótulo explícito continua valendo com o app
    corpo = (await cliente.get("/api/aprendizado", params={"app": QA, "rotulo": "produto"})).json()
    assert corpo["total"] == 0 and corpo["ocultos"] == 4


async def test_rotulo_desconhecido_e_422(cliente: httpx.AsyncClient) -> None:
    assert (await cliente.get("/api/aprendizado", params={"rotulo": "papel"})).status_code == 422
