"""A capability em CADA LINHA do Livro e de `/apps/{pacote}` (hierarquia App → Capability → Item do painel).

Regra por tipo (sem palpite: o que não se sabe é `null`):
- receita: a derivação do detalhe (`capability_da_receita`): a etapa de origem; sem ela, as etapas com o mesmo
  `step_hash` no mesmo app; várias capabilities distintas = ambígua = `null`;
- lição e tela (`learning_items`): `escopo.capability`, exceto vazio e `*` (etapa livre);
- fluxo, habilidade, memória: `null` (o fluxo é um comando inteiro, não pertence a uma capability).
A leitura é em LOTE (uma consulta por tipo, como `saudes`): dobrar as receitas não dobra as consultas.
`capability_nome` (deploy 2): o nome em português da capability, do `title` do catálogo do app sem as lacunas de
parâmetro, para o grupo do painel não mostrar o código; sem catálogo ou capability desconhecida, `null`.

Nível de prova: `simulated` (banco de teste, sem aparelho nem IA).
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
from app.modules.learning.application.apps import Declarado, VisaoPorApp
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain import conteudo as dominio
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure.declarados import LojaSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router

from .fake_skills import TS
from .fake_skills import banco as banco_migrado

PACOTE = "com.exemplo.capability"
OUTRO = "com.exemplo.outro"
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
CHAVE = (PACOTE, "1.0", "sig", "pt/1")


# ------------------------------------------------------------------ domínio puro
def test_capability_unica_descarta_vazio_e_etapa_livre() -> None:
    assert dominio.capability_unica("LIKE_POST") == "LIKE_POST"
    assert dominio.capability_unica("") is None and dominio.capability_unica(None) is None
    assert dominio.capability_unica("*") is None


def test_capability_da_linha_da_receita_so_com_um_nome_nao_ambiguo() -> None:
    assert dominio.capability_da_linha_da_receita(None) is None
    assert dominio.capability_da_linha_da_receita({"nomes": ["A"], "ambigua": False, "fonte": "origem"}) == "A"
    assert dominio.capability_da_linha_da_receita({"nomes": ["A", "B"], "ambigua": True, "fonte": "x"}) is None
    assert dominio.capability_da_linha_da_receita({"nomes": ["*"], "ambigua": False, "fonte": "x"}) is None
    assert dominio.capability_da_linha_da_receita({"nomes": [], "ambigua": False, "fonte": "x"}) is None


# ------------------------------------------------------------------ o mundo semeado (SQL)
def _receita(db: Database, passo: str, *, step_hash: str, origem: str | None = None, pacote: str = PACOTE,
             versao: int = 1) -> str:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"kind": "rid", "rid": "b"}], "commit": False}]
    rid = db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, replay_ok, replay_fail, consecutive_fail, shadow_agree, shadow_total,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pacote, *CHAVE[1:], step_hash, passo, versao, "active", json.dumps(acoes), origem, 0, 0, 0, 0, 0, TS))
    return str(rid)


def _etapa(db: Database, run_id: str, chave: str, *, capability: str | None, template_hash: str | None = None,
           app_ids: list[str] | None = None) -> str:
    if db.one("SELECT id FROM runs WHERE id=?", (run_id,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, app_ids, created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)", (run_id, f"k-{run_id}", "cmd", "execute", "completed",
                                                  json.dumps(["android-01"]), json.dumps(app_ids) if app_ids else None,
                                                  TS))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                   (f"{run_id}:o1", run_id, "android-01", "succeeded", 1))
    sid = f"{run_id}:android-01:v1:{chave}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, capability, template_hash)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, run_id, f"{run_id}:o1", "android-01", 1, 1, chave, chave, chave, "{}", 180, 3, "succeeded",
                capability, template_hash))
    return sid


def _fluxo(db: Database, fid: str) -> None:
    plano = {"summary": fid, "parameters": {}, "steps": [
        {"key": "a", "title": "a", "goal": "a", "side_effect": False,
         "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
               " VALUES (?,?,?,?,?,?,?,?,?)",
               (fid, fid, f"cmd {fid}", f"cmd {fid}", json.dumps(plano), "inst", "active", TS, "run"))


def _item(db: Database, id_: str, kind: str, *, capacidade: str, state: str = "published",
          detalhe: str | None = None) -> None:
    db.execute("INSERT INTO learning_items(id, kind, state, state_detail, scope_app, scope_capability, content,"
               " content_hash, summary, source_kind, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (id_, kind, state, detalhe, PACOTE, capacidade, "{}", f"h-{id_}", f"resumo {id_}", "recovery",
                "system", TS))


class TitulosFalsos:
    """O catálogo do app de exemplo: três capabilities com título; `COMMENT` e o outro app não têm."""

    TITULOS = {"LIKE_POST": "Curtir a publicação", "FOLLOW": "Seguir {username}",
               "SEND_MESSAGE": "Enviar a mensagem para {username}"}

    def titulo(self, app: str, capability: str) -> str | None:
        return self.TITULOS.get(capability) if app == PACOTE else None

    def apps_que_declaram(self, capability: str) -> tuple[str, ...]:
        return (PACOTE,) if capability in self.TITULOS else ()


class RegistroVazio:
    def declarados(self) -> list[Declarado]:
        return []


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('inst','Exemplo',?,0)", (PACOTE,))
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('outro','Outro',?,0)", (OUTRO,))
        # a) origem direta: a etapa de origem diz a capability
        origem = _etapa(db, "r1", "curtir", capability="LIKE_POST", app_ids=["inst"])
        self.com_origem = _receita(db, "curtir", step_hash="h-origem", origem=origem)
        # b) sem origem, mas UMA capability entre as etapas do mesmo step_hash no mesmo app (a de outro app não conta)
        _etapa(db, "r2", "seguir", capability="FOLLOW", template_hash="h-so-hash", app_ids=["inst"])
        _etapa(db, "r3", "seguir", capability="FOLLOW", template_hash="h-so-hash", app_ids=["inst"])
        _etapa(db, "r4", "seguir", capability="OUTRO_APP", template_hash="h-so-hash", app_ids=["outro"])
        self.so_por_hash = _receita(db, "seguir", step_hash="h-so-hash")
        # c) ambígua: duas capabilities distintas no mesmo app e no mesmo step_hash
        _etapa(db, "r5", "dm", capability="DM_SEND", template_hash="h-ambigua", app_ids=["inst"])
        _etapa(db, "r6", "dm", capability="DM_REPLY", template_hash="h-ambigua", app_ids=["inst"])
        self.ambigua = _receita(db, "dm", step_hash="h-ambigua")
        # d) sem nenhuma fonte (nem origem nem etapa) e) treino (sem etapa de execução)
        self.sem_fonte = _receita(db, "nada", step_hash="h-nada")
        self.de_treino = _receita(db, "treino", step_hash="h-treino", origem="training:abc")
        # f) a etapa de origem existe mas é livre (`*`) e nenhuma outra diz: null (a etapa livre não é capability)
        livre = _etapa(db, "r7", "livre", capability="*", app_ids=["inst"])
        self.livre = _receita(db, "livre", step_hash="h-livre", origem=livre)
        _fluxo(db, "f-1")
        _item(db, "li-licao", "licao", capacidade="SEND_MESSAGE")
        _item(db, "li-licao-livre", "licao", capacidade="*")
        _item(db, "li-licao-vazia", "licao", capacidade="")
        _item(db, "li-tela", "tela", capacidade="COMMENT")
        _item(db, "li-tela-livre", "tela", capacidade="*")
        _item(db, "li-tela-abs", "tela", capacidade="ABSORVIDA", state="deprecated", detalhe="absorvida:abc1234")
        repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14,
                                       titulos=TitulosFalsos())
        self.servico.anexar(VisaoPorApp(self.servico, RegistroVazio(), LojaSql(db)))

    def esperado(self) -> dict[tuple[str, str], str | None]:
        return {
            ("receita", self.com_origem): "LIKE_POST",
            ("receita", self.so_por_hash): "FOLLOW",
            ("receita", self.ambigua): None,
            ("receita", self.sem_fonte): None,
            ("receita", self.de_treino): None,
            ("receita", self.livre): None,
            ("fluxo", "f-1"): None,
            ("licao", "li-licao"): "SEND_MESSAGE",
            ("licao", "li-licao-livre"): None,
            ("licao", "li-licao-vazia"): None,
            ("tela", "li-tela"): "COMMENT",
            ("tela", "li-tela-livre"): None,
            ("tela", "li-tela-abs"): "ABSORVIDA",
        }


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "capability_na_linha.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico, cfg=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _por_tipo_e_ref(itens: list[dict[str, object]]) -> dict[tuple[str, str], object]:
    return {(str(i["kind"]), str(i["ref"])): i["capability"] for i in itens}


async def test_lista_do_livro_traz_a_capability_de_cada_linha(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/api/aprendizado")
    assert r.status_code == 200, r.text
    itens = r.json()["itens"]
    assert all("capability" in i for i in itens)
    lidas = _por_tipo_e_ref(itens)
    for chave, esperada in mundo.esperado().items():
        assert lidas[chave] == esperada, chave


async def test_filas_e_detalhe_levam_a_mesma_capability(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    for rota in ("/api/aprendizado/pendentes", "/api/aprendizado/revisar"):
        r = await cliente.get(rota)
        assert r.status_code == 200 and all("capability" in i for i in r.json()["itens"]), rota
    d = await cliente.get(f"/api/aprendizado/receita/{mundo.com_origem}")
    assert d.json()["item"]["capability"] == "LIKE_POST" and d.json()["conteudo"]["capability"]["nomes"] == ["LIKE_POST"]
    d = await cliente.get(f"/api/aprendizado/receita/{mundo.ambigua}")
    assert d.json()["item"]["capability"] is None and d.json()["conteudo"]["capability"]["ambigua"] is True


async def test_linhas_de_apps_pacote_trazem_a_capability(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.get(f"/api/aprendizado/apps/{PACOTE}")
    assert r.status_code == 200, r.text
    corpo = r.json()
    linhas = corpo["aprendido"] + corpo["absorvido"]
    assert all("capability" in x for x in linhas)
    lidas = _por_tipo_e_ref(linhas)
    assert {x["ref"] for x in corpo["absorvido"]} == {"li-tela-abs"}
    # as receitas e os itens do app saem com a capability certa; o fluxo (app resolvido pela loja) não tem
    for chave, esperada in mundo.esperado().items():
        if chave in lidas:
            assert lidas[chave] == esperada, chave
    assert ("receita", mundo.com_origem) in lidas and ("licao", "li-licao") in lidas and ("tela", "li-tela-abs") in lidas


def test_a_leitura_e_em_lote_e_nao_uma_consulta_por_linha(mundo: Mundo, monkeypatch: pytest.MonkeyPatch) -> None:
    entradas = list(mundo.servico.livro().itens)
    chamadas: list[str] = []
    original = mundo.db.query

    def contar(sql: str, params: tuple | dict = ()) -> list:
        chamadas.append(sql)
        return original(sql, params)

    monkeypatch.setattr(mundo.db, "query", contar)
    mundo.servico.capabilities(entradas)
    primeiras = len(chamadas)
    # dobrar as receitas (mesmo `step_hash`, outras linhas) NÃO aumenta o número de consultas
    for i in range(12):
        _receita(mundo.db, "seguir", step_hash="h-so-hash", versao=i + 2)
    entradas = list(mundo.servico.livro().itens)
    chamadas.clear()
    resultado = mundo.servico.capabilities(entradas)
    assert len(chamadas) == primeiras and primeiras <= 6
    assert sum(1 for e in entradas if e.kind is LivroKind.RECEITA and resultado[e.trail_ref] == "FOLLOW") == 13


# ------------------------------------------------------------------ o nome em português (deploy 2)
@pytest.mark.parametrize(("titulo", "nome"), [
    ("Abrir o perfil de {username}", "Abrir o perfil"),
    ("Abrir a conversa com {username}", "Abrir a conversa"),
    ("Enviar a mensagem para {username}", "Enviar a mensagem"),
    ('Buscar "{query}" no Outlook', "Buscar no Outlook"),
    ("Seguir {username}", "Seguir"),
    ("Abrir o feed", "Abrir o feed"),
    ("{username}", None), ("", None), (None, None),
])
def test_nome_da_capability_tira_as_lacunas_de_parametro(titulo: str | None, nome: str | None) -> None:
    assert dominio.nome_da_capability(titulo) == nome


def test_titulos_do_registro_le_o_catalogo_real_com_as_internas() -> None:
    from app.modules.learning.infrastructure.eventos import TitulosDoRegistro
    t = TitulosDoRegistro()
    assert t.titulo("com.instagram.android", "OPEN_PROFILE") == "Abrir o perfil de {username}"
    assert t.titulo("com.microsoft.office.outlook", "OPEN_MAIL_INBOX") == "Abrir a caixa de entrada do Outlook"
    assert t.titulo("com.instagram.android", "NAO_EXISTE") is None
    assert t.titulo("com.exemplo.sem.catalogo", "OPEN_PROFILE") is None and t.titulo("", "X") is None
    # O sinal da decisão de aprovação vem sem pacote: quem declara o código (polimento do Chrome do deploy 12).
    assert t.apps_que_declaram("REPLY_COMMENT") == ("com.instagram.android",)
    assert t.apps_que_declaram("NAO_EXISTE") == () and t.apps_que_declaram("") == ()


def test_linha_sem_app_e_nomeada_pelo_unico_catalogo_que_declara_a_capability(mundo: Mundo) -> None:
    """Polimento do Chrome do deploy 12: `aprovacao_decidida` chega aos Sinais sem `app_package` e o painel mostrava
    REPLY_COMMENT cru. Sem app, o nome vem do catálogo que declara o código, só se for um; dois apps, nulo."""
    from app.modules.learning.presentation.nomes import nomear
    linhas: list[object] = [{"app_package": None, "capability": "LIKE_POST"},
                            {"app_package": None, "capability": "COMMENT"},
                            {"app_package": "*", "capability": "LIKE_POST"}]
    nomear(linhas, mundo.servico, app="app_package")
    assert [x["capability_nome"] for x in linhas if isinstance(x, dict)] == ["Curtir a publicação", None, None]

    class DoisApps(TitulosFalsos):
        def apps_que_declaram(self, capability: str) -> tuple[str, ...]:
            return (PACOTE, OUTRO)

    mundo.servico._titulos = DoisApps()
    ambigua: list[object] = [{"app_package": None, "capability": "LIKE_POST"}]
    nomear(ambigua, mundo.servico, app="app_package")
    assert isinstance(ambigua[0], dict) and ambigua[0]["capability_nome"] is None


async def test_linhas_e_grupos_trazem_o_nome_da_capability(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    esperado = {("receita", mundo.com_origem): "Curtir a publicação", ("receita", mundo.so_por_hash): "Seguir",
                ("licao", "li-licao"): "Enviar a mensagem", ("tela", "li-tela"): None, ("fluxo", "f-1"): None,
                ("receita", mundo.ambigua): None}
    itens = (await cliente.get("/api/aprendizado")).json()["itens"]
    nomes = {(str(i["kind"]), str(i["ref"])): i["capability_nome"] for i in itens}
    for chave, nome in esperado.items():
        assert nomes[chave] == nome, chave
    d = (await cliente.get(f"/api/aprendizado/receita/{mundo.com_origem}")).json()
    assert d["item"]["capability_nome"] == "Curtir a publicação"
    corpo = (await cliente.get(f"/api/aprendizado/apps/{PACOTE}")).json()
    linhas = {(str(x["kind"]), str(x["ref"])): x["capability_nome"] for x in corpo["aprendido"] + corpo["absorvido"]}
    assert linhas[("licao", "li-licao")] == "Enviar a mensagem" and linhas[("tela", "li-tela-abs")] is None


async def test_linhas_trazem_o_nome_do_app_e_a_etapa_de_origem_da_receita(mundo: Mundo,
                                                                         cliente: httpx.AsyncClient) -> None:
    """Validação do deploy 4: as listas e a Identidade diziam o pacote onde o resto do painel já dizia o nome, e a
    receita de app sem catálogo ficava com a chave crua. `app_nome` em toda linha; `etapa` (o título da etapa de
    origem) só na receita que tem origem de execução."""
    itens = (await cliente.get("/api/aprendizado")).json()["itens"]
    por = {(str(i["kind"]), str(i["ref"])): i for i in itens}
    assert por[("receita", mundo.com_origem)]["app_nome"] == "Exemplo"
    assert por[("receita", mundo.com_origem)]["etapa"] == "curtir"                 # o `steps.title` da origem
    assert por[("receita", mundo.sem_fonte)]["etapa"] is None
    assert por[("receita", mundo.de_treino)]["etapa"] is None                      # treino não casa com `steps`
    assert por[("licao", "li-licao")]["etapa"] is None
    d = (await cliente.get(f"/api/aprendizado/receita/{mundo.com_origem}")).json()["item"]
    assert (d["app_nome"], d["etapa"], d["capability_nome"]) == ("Exemplo", "curtir", "Curtir a publicação")
    # As filas ("Para aprovar"): a lição de pessoa espera o dono e vem com o nome do app.
    _item(mundo.db, "li-da-pessoa", "licao", capacidade="SEND_MESSAGE", state="validated")
    mundo.db.execute("UPDATE learning_items SET human_origin=1 WHERE id='li-da-pessoa'")
    fila = (await cliente.get("/api/aprendizado/pendentes")).json()["itens"]
    assert [(i["ref"], i["app_nome"]) for i in fila] == [("li-da-pessoa", "Exemplo")]


def test_nomes_sao_lidos_uma_vez_por_par_app_e_capability(mundo: Mundo) -> None:
    contagem: dict[tuple[str, str], int] = {}

    class Contando(TitulosFalsos):
        def titulo(self, app: str, capability: str) -> str | None:
            contagem[(app, capability)] = contagem.get((app, capability), 0) + 1
            return super().titulo(app, capability)

    mundo.servico._titulos = Contando()
    entradas = mundo.servico.livro().itens
    mundo.servico.nomes_das_capabilities(entradas, mundo.servico.capabilities(entradas))
    assert contagem and max(contagem.values()) == 1
