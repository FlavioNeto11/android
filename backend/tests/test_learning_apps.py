"""A visão por app do aprendizado (Fase 30, itens 30.1 e 30.2).

30.2, a chave de app canônica no Livro: fluxo e habilidade guardam o `app_id` (da loja ou já um pacote), e o livro os
mostra pelo PACOTE; o que não resolve vai ao balde `nao_resolvido` (visível, com o id cru), nunca some; a memória é
da persona e fica fora do eixo de app.

30.1, a visão por app como composição de leitura: lista de apps (registro ∪ loja ∪ pacotes do livro), o aprendido, o
declarado e o absorvido por app, e a camada de uso em runtime.

Nível de prova: `simulated` (banco de teste, registro de apps FALSO injetado; nenhum pacote do repositório é lido).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.vocabulario import APP_NAO_RESOLVIDO, LivroKind
from app.modules.learning.infrastructure.fontes import FontesSql, ResolvedorDeApp
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router

from .fake_skills import TS, perfil
from .fake_skills import banco as banco_migrado

AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
# Pacotes SINTÉTICOS do teste: o código de produção não tem pacote nenhum (ADR-052), e o teste não depende dos reais.
LOJA = "com.exemplo.loja"            # app da loja (tabela `apps`) com id curto "loja"
SOMENTE_DECLARADO = "com.exemplo.declarado"
SOMENTE_LOJA = "com.exemplo.sodaloja"
SOMENTE_APRENDIDO = "com.exemplo.aprendido"


def _fluxo(db: Database, fid: str, app_id: str | None, *, status: str = "active") -> None:
    plano = {"summary": fid, "parameters": {}, "steps": [
        {"key": "a", "title": "a", "goal": "a", "side_effect": False,
         "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
               " VALUES (?,?,?,?,?,?,?,?,?)",
               (fid, fid, f"cmd {fid}", f"cmd {fid}", json.dumps(plano), app_id, status, TS, "run"))


def _habilidade(db: Database, sid: str, app_id: str | None, *, estado: str = "published",
                exigidos: tuple[str, ...] = ()) -> None:
    db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)",
               (sid, f"Habilidade {sid}", app_id, TS, TS))
    db.execute("INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash, match_key,"
               " source_kind, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f"{sid}@1", sid, 1, estado, "{}", "h", f"cmd {sid}", "teaching", TS, TS))
    for a in exigidos:
        db.execute("INSERT INTO skill_version_apps(version_id, app_id) VALUES (?,?)", (f"{sid}@1", a))


def _loja(db: Database, id_: str, pacote: str) -> None:
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)", (id_, f"App {id_}", pacote))


class Mundo:
    def __init__(self, db: Database, registro: list[str]) -> None:
        self.db = db
        _loja(db, "loja", LOJA)
        _loja(db, "outra", SOMENTE_LOJA)
        _fluxo(db, "f-id-da-loja", "loja")                      # id da loja → pacote
        _fluxo(db, "f-ja-e-pacote", SOMENTE_DECLARADO)          # já é um pacote do REGISTRO (sem linha em `apps`)
        _fluxo(db, "f-desconhecido", "app-que-nao-existe")      # nada resolve → balde, com o id cru
        _fluxo(db, "f-sem-app", None)                           # sem app e sem exigidos → balde, sem id cru
        _fluxo(db, "f-exigido", None)                           # sem principal, um exigido que resolve
        db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES ('f-exigido','outra')")
        _fluxo(db, "f-ambiguo", None)                           # dois exigidos de pacotes diferentes → balde
        db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES ('f-ambiguo','loja')")
        db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES ('f-ambiguo','outra')")
        _habilidade(db, "h.da.loja", "loja")
        _habilidade(db, "h.desconhecida", "sumiu")
        _habilidade(db, "h.por.versao", None, exigidos=("outra",))     # `skill_version_apps` resolve quando é único
        perfil(db, "p1")
        for i in range(2):
            db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at,"
                       " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (f"m{i}", "p1", "@ana", f"lembranca {i}", "operator", f"fp{i}", TS, TS))
        repo = SqlLearningRepository(db, precos=dict)
        self.fontes = FontesSql(db, pacotes_do_registro=lambda: registro)
        self.servico = LearningService(repo, self.fontes, TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "apps.sqlite3")
    yield Mundo(db, [SOMENTE_DECLARADO])
    db.close()


def _por_ref(entradas: list[EntradaDoLivro]) -> dict[str, EntradaDoLivro]:
    return {e.ref: e for e in entradas}


# ------------------------------------------------------------------ 30.2: a chave canônica
def test_resolvedor_de_app_ordem_e_ambiguidade() -> None:
    r = ResolvedorDeApp({"a": "com.x.a", "b": "com.x.b"}, ["com.reg.c"])
    assert r.pacote("a") == "com.x.a"                       # id da loja → pacote
    assert r.pacote("com.x.b") == "com.x.b"                 # já é um pacote de `apps`
    assert r.pacote("com.reg.c") == "com.reg.c"             # já é um pacote do registro
    assert r.pacote("zzz") is None and r.pacote(None) is None and r.pacote("") is None
    assert r.resolver(None, ["a"]) == "com.x.a"             # principal ausente: um exigido único
    assert r.resolver(None, ["a", "b"]) is None             # dois pacotes: ambíguo, não adivinha
    assert r.resolver(None, ["a", "com.x.a"]) == "com.x.a"  # dois ids, mesmo pacote: não é ambíguo
    assert r.resolver("zzz", ["b"]) == "com.x.b"            # principal desconhecido, exigido resolve
    assert r.resolver("a", ["b"]) == "com.x.a"              # o principal vale mais que o exigido


def test_fluxo_e_habilidade_saem_pelo_pacote_ou_pelo_balde(mundo: Mundo) -> None:
    fluxos = _por_ref(mundo.fontes.fluxos())
    assert fluxos["f-id-da-loja"].app == LOJA and fluxos["f-id-da-loja"].app_ref is None
    assert fluxos["f-ja-e-pacote"].app == SOMENTE_DECLARADO
    assert fluxos["f-desconhecido"].app == APP_NAO_RESOLVIDO and fluxos["f-desconhecido"].app_ref == "app-que-nao-existe"
    assert fluxos["f-sem-app"].app == APP_NAO_RESOLVIDO and fluxos["f-sem-app"].app_ref is None
    assert fluxos["f-exigido"].app == SOMENTE_LOJA
    assert fluxos["f-ambiguo"].app == APP_NAO_RESOLVIDO
    habilidades = _por_ref(mundo.fontes.habilidades())
    assert habilidades["h.da.loja@1"].app == LOJA
    assert habilidades["h.desconhecida@1"].app == APP_NAO_RESOLVIDO and habilidades["h.desconhecida@1"].app_ref == "sumiu"
    assert habilidades["h.por.versao@1"].app == SOMENTE_LOJA
    # O detalhe de um item usa a mesma chave que a lista.
    assert mundo.fontes.fluxo("f-exigido") is not None and mundo.fontes.fluxo("f-exigido").app == SOMENTE_LOJA
    assert mundo.fontes.habilidade("h.desconhecida@1").app == APP_NAO_RESOLVIDO


def test_o_balde_e_filtravel_e_a_memoria_fica_fora_do_eixo_de_app(mundo: Mundo) -> None:
    balde = mundo.servico.livro(app=APP_NAO_RESOLVIDO)
    assert {e.ref for e in balde.itens} == {"f-desconhecido", "f-sem-app", "f-ambiguo", "h.desconhecida@1"}
    assert all(e.kind is not LivroKind.MEMORIA for e in balde.itens)          # memória nunca no balde
    do_pacote = mundo.servico.livro(app=LOJA)
    assert {e.ref for e in do_pacote.itens} == {"f-id-da-loja", "h.da.loja@1"}
    memoria = [e for e in mundo.servico.livro().itens if e.kind is LivroKind.MEMORIA]
    assert len(memoria) == 1 and memoria[0].app is None and memoria[0].app_ref is None and memoria[0].count == 2


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_rota_do_livro_filtra_o_balde_e_mostra_o_id_cru(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get(f"/api/aprendizado?app={APP_NAO_RESOLVIDO}")
    assert r.status_code == 200, r.text
    itens = {i["ref"]: i for i in r.json()["itens"]}
    assert set(itens) == {"f-desconhecido", "f-sem-app", "f-ambiguo", "h.desconhecida@1"}
    assert itens["f-desconhecido"]["app"] == APP_NAO_RESOLVIDO and itens["f-desconhecido"]["app_ref"] == "app-que-nao-existe"
    do_pacote = (await cliente.get(f"/api/aprendizado?app={LOJA}")).json()["itens"]
    assert {i["ref"] for i in do_pacote} == {"f-id-da-loja", "h.da.loja@1"} and all(i["app_ref"] is None for i in do_pacote)
