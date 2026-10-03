"""30.33-C: o fluxo que atravessa apps (ler no Correio, abrir o perfil no Social) na leitura por app do aprendizado.

Validação do deploy 10: o fluxo que lê o Outlook aparecia arquivado sob o Instagram e sumia do Outlook. Não era dado
errado: o `app_id` do fluxo é o app PRINCIPAL (onde rodam as etapas sem app próprio), e trocá-lo quebraria a etapa
que roda nele. O que estava errado era a leitura, que só olhava o principal:

- o livro dá ao fluxo multi-app os pacotes dele, na ordem do plano (`EntradaDoLivro.apps`); o de um app só fica vazio;
- a visão por app, o filtro `?app=` do livro e as métricas (o recorte, a saúde, as revisões, a economia) põem o item
  em cada app dele; o rótulo QA/PRODUTO e o `scope_app` da revisão seguem o principal;
- o dossiê do curador ganha `item.apps` (id, pacote, principal) só no multi-app, e o de um app só fica com as mesmas
  chaves (o mesmo `dossie_hash`: nenhuma revisão nova fora dos multi-app);
- a lista das revisões traz o título do item e os nomes dos apps.

Pacotes sintéticos (ADR-052). Nível de prova: `simulated`.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.apps import Declarado, VisaoPorApp
from app.modules.learning.application.metricas import ServicoDeMetricas, orcamento
from app.modules.learning.application.ports import Ajustes, AjustesDoCurador, LeituraDaJanela
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.curador import PRINCIPAL_DO_ITEM
from app.modules.learning.domain.livro import EntradaDoLivro, apps_do_item
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Origem
from app.modules.learning.infrastructure.declarados import LojaSql
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.metricas_sql import FontesDeMetricasSql
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.livro import _revisao as _revisao_do_livro
from app.modules.learning.presentation.router import router as learning_router
from app.util import to_iso

from .fake_skills import TS
from .fake_skills import banco as banco_migrado

AGORA = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
CORREIO, SOCIAL, QA = "com.exemplo.correio", "com.exemplo.social", "com.exemplo.qa"
#: As chaves de `item` do dossiê antes da 30.33-C: o item de um app só sai exatamente com elas.
CHAVES_DO_ITEM = {"id", "kind", "ref", "app", "capability", "app_version", "estado", "origem", "side_effect",
                  "human_origin", "criado_em"}


def _passo(key: str, app_id: str | None) -> dict[str, object]:
    return {"key": key, "title": key, "goal": key, "side_effect": False, "app_id": app_id,
            "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}


def _fluxo(db: Database, fid: str, principal: str, etapas: list[str | None], exigidos: list[str]) -> None:
    plano = {"summary": fid, "parameters": {}, "app_id": principal, "required_apps": exigidos,
             "steps": [_passo(f"e{i}", a) for i, a in enumerate(etapas)]}
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
               " VALUES (?,?,?,?,?,?,?,?,?)", (fid, f"Nome {fid}", f"cmd {fid}", f"Comando {fid}", json.dumps(plano),
                                               principal, "candidate", TS, "run"))
    for a in exigidos:
        db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES (?,?)", (fid, a))


def _revisao(db: Database, id_: str, item_ref: str, scope_app: str, quando: datetime) -> None:
    saida = json.dumps({"decisao": "observar", "evidencias_citadas": [], "confianca": "media"})
    db.execute("INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, scope_app, gatilho, dossie_hash,"
               " dossie, template_id, template_versao, provedor, modelo, simulated, usd, saida, validade)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (id_, to_iso(quando), item_ref, "fluxo", scope_app, "pedido_da_pessoa", f"dh-{id_}", "{}", "curador",
                "dossie-v1", "hub", "modelo-x", 0, 0.01, saida, "ok"))


class SemDeclarados:
    def declarados(self) -> list[Declarado]:
        return []


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        for id_, nome, pacote, categoria in (("correio", "Correio", CORREIO, None), ("social", "Social", SOCIAL, None),
                                             ("qa", "QA", QA, "qa")):
            db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES (?,?,?,0,?)",
                       (id_, nome, pacote, categoria))
        # Lê no Correio (duas etapas com app próprio) e abre o perfil no Social (a etapa sem app roda no principal).
        _fluxo(db, "f-multi", "social", ["correio", "correio", None], ["correio", "social"])
        _fluxo(db, "f-mono", "social", [None], ["social"])
        # O exigido que nenhuma etapa cita vai ao fim; o principal fora da tabela de exigidos entra pela leitura.
        _fluxo(db, "f-extra", "qa", [None], ["qa", "correio"])
        _fluxo(db, "f-tabela-sem-principal", "social", ["correio", None], ["correio"])
        self.repo = SqlLearningRepository(db, precos=dict)
        self.fontes = FontesSql(db)
        self.servico = LearningService(self.repo, self.fontes, TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
        self.visao = VisaoPorApp(self.servico, SemDeclarados(), LojaSql(db))
        self.servico.anexar(self.visao)
        self.metricas = ServicoDeMetricas(self.servico, FontesDeMetricasSql(db), relogio=lambda: AGORA)
        self.servico.anexar(self.metricas)


@pytest.fixture
def m(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "multi_app.sqlite3")
    yield Mundo(db)
    db.close()


def _fluxos(m: Mundo) -> dict[str, EntradaDoLivro]:
    return {e.ref: e for e in m.fontes.fluxos()}


# ------------------------------------------------------------------ o livro
def test_o_fluxo_multi_app_tem_os_apps_na_ordem_do_plano(m: Mundo) -> None:
    f = _fluxos(m)
    assert f["f-multi"].app == SOCIAL and f["f-multi"].apps == (CORREIO, SOCIAL)
    assert apps_do_item(f["f-multi"]) == (CORREIO, SOCIAL)
    assert f["f-mono"].apps == () and apps_do_item(f["f-mono"]) == (SOCIAL,)
    assert f["f-extra"].apps == (QA, CORREIO)
    assert f["f-tabela-sem-principal"].apps == (CORREIO, SOCIAL)
    assert m.fontes.fluxo("f-multi") == f["f-multi"]                     # a leitura de um só dá o mesmo


def test_apps_do_item_sem_app_e_com_um_app() -> None:
    base = EntradaDoLivro(kind=LivroKind.RECEITA, ref="1", state=None, native_status=None, title="t", app=None,
                          origin=Origem.EXECUCAO)
    assert apps_do_item(base) == ()
    assert apps_do_item(replace(base, app=QA)) == (QA,)


def test_o_filtro_do_livro_e_a_visao_por_app_poem_o_multi_app_em_cada_app(m: Mundo) -> None:
    no_correio = {e.ref for e in m.servico.livro(app=CORREIO).itens}
    no_social = {e.ref for e in m.servico.livro(app=SOCIAL).itens}
    assert no_correio == {"f-multi", "f-extra", "f-tabela-sem-principal"}
    assert no_social == {"f-multi", "f-mono", "f-tabela-sem-principal"}
    detalhe = m.visao.detalhe(CORREIO)
    assert {x.entrada.ref for x in detalhe.aprendido} == no_correio
    resumo = {r.pacote: r for r in m.visao.apps().apps}
    assert (resumo[CORREIO].total_aprendido, resumo[SOCIAL].total_aprendido, resumo[QA].total_aprendido) == (3, 3, 1)


# ------------------------------------------------------------------ as métricas
def test_as_metricas_e_as_revisoes_do_app_incluem_o_multi_app(m: Mundo) -> None:
    _revisao(m.db, "lr-multi", "fluxo:f-multi", SOCIAL, AGORA.replace(hour=10))
    _revisao(m.db, "lr-mono", "fluxo:f-mono", SOCIAL, AGORA.replace(hour=11))
    no_correio = m.metricas.metricas(app=CORREIO, dias=7)
    assert no_correio.curador["revisoes"] == 1                           # a do f-multi, gravada com o principal
    assert sum(no_correio.itens.get("fluxo", {}).values()) == 3
    no_social = m.metricas.metricas(app=SOCIAL, dias=7)
    assert no_social.curador["revisoes"] == 2
    pagina = m.metricas.revisoes(app=CORREIO, decisao=None, desde=None, limite=10, cursor=None)
    assert [x.revisao.id for x in pagina.revisoes] == ["lr-multi"]
    assert [x.revisao.id for x in m.metricas.revisoes(app=QA, decisao=None, desde=None, limite=10,
                                                       cursor=None).revisoes] == []


def test_a_economia_do_app_conta_o_fluxo_multi_app() -> None:
    db_resumo = {"totais": {}, "fluxos": [
        {"flow_id": "f-multi", "package": SOCIAL, "etapas": 2},
        {"flow_id": "f-mono", "package": SOCIAL, "etapas": 5}]}
    servico = SimpleNamespace(livro=lambda: SimpleNamespace(itens=()))
    metricas = ServicoDeMetricas(servico, None, aproveitamento=lambda dias, agora: db_resumo,  # type: ignore[arg-type]
                                 relogio=lambda: AGORA)
    multi = {"f-multi": (CORREIO, SOCIAL)}
    assert metricas._economia(CORREIO, 7, AGORA, multi)["etapas"] == 2
    assert metricas._economia(SOCIAL, 7, AGORA, multi)["etapas"] == 7
    assert metricas._economia(CORREIO, 7, AGORA)["etapas"] == 0


def test_o_orcamento_diz_qual_ramo_manda() -> None:
    aj = AjustesDoCurador(modo=Modo.SHADOW, alfa=0.10, k=1.5, janela_dias=7)
    leitura = LeituraDaJanela(gasto_da_operacao=10.0, custos_medidos=(0.01, 0.01), tamanhos_sem_medida=(),
                              tamanhos_sem_medida_na_hora=(), gasto_medido=0.02, gasto_medido_na_hora=0.0,
                              revisoes_antes_de_hoje=2, revisoes_de_hoje=0)
    o = orcamento(leitura, aj, {})
    # B = min(α·G = 1,0; k·N·c̄ = 1,5 × 2 × 0,01 = 0,03): manda o ramo das revisões, e o uso fica em 1/k.
    assert (o["orcamento"], o["teto_alfa"], o["pelas_revisoes"], o["ramo"], o["k"], o["uso"]) == (
        0.03, 1.0, 0.03, "revisoes", 1.5, 0.667)
    pouca_operacao = orcamento(replace(leitura, gasto_da_operacao=0.2), aj, {})
    assert (pouca_operacao["orcamento"], pouca_operacao["ramo"]) == (0.02, "operacao")


# ------------------------------------------------------------------ o dossiê do curador
def test_o_dossie_do_multi_app_diz_os_apps_e_o_principal(m: Mundo) -> None:
    dossies = DossiesSql(m.db, m.servico, m.repo, None)
    f = _fluxos(m)
    multi = dossies.dossie(f["f-multi"])
    assert multi is not None
    item = multi.como_dados()["item"]
    assert item["app"] == SOCIAL and item["principal_e"] == PRINCIPAL_DO_ITEM
    assert item["apps"] == [{"id": "correio", "pacote": CORREIO, "principal": False},
                            {"id": "social", "pacote": SOCIAL, "principal": True}]
    sem_principal = dossies.dossie(f["f-tabela-sem-principal"])
    assert sem_principal is not None
    assert [a["id"] for a in sem_principal.como_dados()["item"]["apps"]] == ["correio", "social"]


def test_o_dossie_de_um_app_so_fica_com_as_mesmas_chaves(m: Mundo) -> None:
    """Nenhuma revisão nova fora dos multi-app: o item de um app só sai com as chaves de antes, e o hash é o do
    mesmo dossiê sem o campo novo (a `VERSAO_DO_DOSSIE` não mudou)."""
    dossies = DossiesSql(m.db, m.servico, m.repo, None)
    mono = dossies.dossie(_fluxos(m)["f-mono"])
    assert mono is not None
    dados = mono.como_dados()
    assert set(dados["item"]) == CHAVES_DO_ITEM and dados["versao_do_dossie"] == 1
    assert mono.item.apps == ()
    # Mesmo item, entrada sem `apps` (a leitura de antes da 30.33-C): o mesmo hash.
    antes = dossies.dossie(replace(_fluxos(m)["f-mono"], apps=()))
    assert antes is not None and antes.dossie_hash == mono.dossie_hash


# ------------------------------------------------------------------ as rotas
@pytest.fixture
async def cliente(m: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=m.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_rotas_mostram_os_apps_pelo_nome_e_o_titulo_da_revisao(m: Mundo, cliente: httpx.AsyncClient) -> None:
    _revisao(m.db, "lr-multi", "fluxo:f-multi", SOCIAL, AGORA.replace(hour=10))
    r = await cliente.get(f"/api/aprendizado/revisoes?app={CORREIO}")
    assert r.status_code == 200, r.text
    [linha] = r.json()["revisoes"]
    assert (linha["app"], linha["app_nome"], linha["titulo"]) == (SOCIAL, "Social", "Comando f-multi")
    assert (linha["apps"], linha["apps_nomes"]) == ([CORREIO, SOCIAL], ["Correio", "Social"])
    livro = {i["ref"]: i for i in (await cliente.get(f"/api/aprendizado?app={CORREIO}")).json()["itens"]}
    assert livro["f-multi"]["apps_nomes"] == ["Correio", "Social"] and livro["f-extra"]["apps"] == [QA, CORREIO]
    detalhe = (await cliente.get(f"/api/aprendizado/apps/{CORREIO}")).json()
    linhas = {x["ref"]: x for x in detalhe["aprendido"]}
    assert linhas["f-multi"]["apps_nomes"] == ["Correio", "Social"] and linhas["f-multi"]["app_nome"] == "Social"
    mono = {i["ref"]: i for i in (await cliente.get(f"/api/aprendizado?app={SOCIAL}")).json()["itens"]}["f-mono"]
    assert mono["apps"] == [] and "apps_nomes" not in mono


async def test_o_desfecho_em_14_dias_sai_nas_rotas(m: Mundo, cliente: httpx.AsyncClient) -> None:
    """30.35 no painel (30.33-C): o `resultado_posterior` só existia no banco; sai na lista das revisões e nos
    pareceres do item, e é nulo enquanto a janela não fecha."""
    _revisao(m.db, "lr-velha", "fluxo:f-mono", SOCIAL, AGORA.replace(day=1))
    _revisao(m.db, "lr-nova", "fluxo:f-mono", SOCIAL, AGORA.replace(hour=9))
    m.db.execute("UPDATE learning_reviews SET resultado_posterior='manter', resultado_em=? WHERE id='lr-velha'",
                 (to_iso(AGORA),))
    linhas = {x["id"]: x for x in (await cliente.get("/api/aprendizado/revisoes")).json()["revisoes"]}
    assert (linhas["lr-velha"]["resultado_posterior"], linhas["lr-velha"]["resultado_em"]) == ("manter", to_iso(AGORA))
    assert linhas["lr-nova"]["resultado_posterior"] is None
    # Os pareceres do item (a seção do detalhe) leem pela mesma revisão gravada e pelo mesmo `_revisao` da rota.
    [velha] = [r for r in RegistroDeRevisoesSql(m.db).do_item("fluxo:f-mono", 10) if r.id == "lr-velha"]
    assert _revisao_do_livro(velha)["resultado_posterior"] == "manter" and velha.resultado_em == to_iso(AGORA)


async def test_revisao_de_item_que_saiu_do_livro_fica_sem_titulo(m: Mundo, cliente: httpx.AsyncClient) -> None:
    _revisao(m.db, "lr-velha", "fluxo:f-que-sumiu", SOCIAL, AGORA.replace(hour=9))
    [linha] = (await cliente.get("/api/aprendizado/revisoes")).json()["revisoes"]
    assert (linha["titulo"], linha["apps"], linha["app_nome"]) == (None, [], "Social")
