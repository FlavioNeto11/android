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
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.config import AiCfg, AppConfigFile, Config, SkillsCfg
from app.db import Database
from app.modules.applications.infrastructure import registry
from app.modules.learning.application.apps import Declarado, ResumoDoApp, VisaoDeApps, VisaoPorApp
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.camada import (ArquivoDeclarado, Camada, Existencia, ModosDeRuntime,
                                                ModosDeUso, uso_do_declarado, uso_do_item)
from app.modules.learning.domain.ciclo import NaoEncontrado, SkillState
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.vocabulario import APP_NAO_RESOLVIDO, LivroKind, Modo, ModoDeTelas
from app.modules.learning.infrastructure.declarados import DeclaradosDoRegistro, LojaSql
from app.modules.learning.infrastructure.fontes import FontesSql, ResolvedorDeApp
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router

from .fake_skills import TS, perfil
from .fake_skills import banco as banco_migrado

AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
# Pacotes SINTÉTICOS do teste: o código de produção não tem pacote nenhum (ADR-052), e o teste não depende dos reais.
LOJA = "com.exemplo.loja"            # app da loja (tabela `apps`) com id curto "loja"
SOMENTE_DECLARADO = "com.exemplo.declarado"      # declarado e já citado por um fluxo (sem linha em `apps`)
DECLARADO_SEM_LINHA = "com.exemplo.semlinha"     # declarado e SEM nenhuma linha no livro: aparece com zeros
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


# ------------------------------------------------------------------ 30.1: a visão por app
SEM_DADO = "com.exemplo.vazia"            # app da loja SEM nenhuma linha no livro: aparece com zeros


class RegistroFalso:
    """O registro de apps e a pasta do repositório, falsos: um app declarado com tudo, sem nenhuma linha no livro."""

    def declarados(self) -> list[Declarado]:
        return [Declarado(package=DECLARADO_SEM_LINHA, name="App Declarado", tem_app=True, tem_catalogo=True,
                          tem_telas=True, tem_sessao=True, acoes=4, telas=3, login_gerenciado=True),
                Declarado(package=SOMENTE_DECLARADO, name="Citado", tem_app=True),
                Declarado(package=LOJA, name="App da Loja Declarado", tem_app=True, tem_catalogo=False,
                          tem_telas=True, telas=2)]


def _item(db: Database, id_: str, kind: str, app: str, *, state: str = "published", detalhe: str | None = None,
          capacidade: str = "*") -> None:
    db.execute("INSERT INTO learning_items(id, kind, state, state_detail, scope_app, scope_capability, content,"
               " content_hash, summary, source_kind, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (id_, kind, state, detalhe, app, capacidade, "{}", f"h-{id_}", f"resumo {id_}", "recovery", "system", TS))


@pytest.fixture
def visao(mundo: Mundo) -> VisaoPorApp:
    db = mundo.db
    _loja(db, "vazia", SEM_DADO)
    _item(db, "li-tela-pub", "tela", LOJA)
    _item(db, "li-tela-obs", "tela", LOJA, state="candidate", capacidade="a")
    _item(db, "li-tela-abs", "tela", LOJA, state="deprecated", detalhe="absorvida:abc1234", capacidade="b")
    _item(db, "li-licao", "licao", LOJA)
    _item(db, "li-so-aprendido", "tela", SOMENTE_APRENDIDO)
    _item(db, "li-voz", "voz", "")                                  # persona: sem eixo de app
    v = VisaoPorApp(mundo.servico, RegistroFalso(), LojaSql(db))
    mundo.servico.anexar(v)
    return v


def _app(v: VisaoDeApps, pacote: str) -> ResumoDoApp:
    return next(a for a in v.apps if a.pacote == pacote)


def test_lista_de_apps_e_a_uniao_com_a_origem_de_existencia(visao: VisaoPorApp) -> None:
    v = visao.apps()
    assert {a.pacote for a in v.apps} == {DECLARADO_SEM_LINHA, SOMENTE_DECLARADO, LOJA, SOMENTE_LOJA, SEM_DADO, SOMENTE_APRENDIDO}
    assert _app(v, DECLARADO_SEM_LINHA).existencia is Existencia.DECLARADO
    assert _app(v, LOJA).existencia is Existencia.DECLARADO                  # a pasta vale mais que a loja
    assert _app(v, SOMENTE_LOJA).existencia is Existencia.LOJA
    assert _app(v, SOMENTE_APRENDIDO).existencia is Existencia.SO_APRENDIDO and _app(v, SOMENTE_APRENDIDO).loja is None
    # O app declarado sem linha aparece com zeros (não some), com o que o repositório declara.
    d = _app(v, DECLARADO_SEM_LINHA)
    assert d.total_aprendido == 0 and d.absorvido == 0 and d.contagem == {} and d.declarado is not None
    assert d.declarado.acoes == 4 and d.declarado.telas == 3 and d.nome == "App Declarado"
    vazio = _app(v, SEM_DADO)
    assert vazio.existencia is Existencia.LOJA and vazio.total_aprendido == 0 and vazio.nome == "App vazia"


def test_aprendido_declarado_e_absorvido_nao_se_misturam(visao: VisaoPorApp) -> None:
    v = visao.apps()
    loja = _app(v, LOJA)
    # aprendido: 2 telas (publicada e candidata), 1 lição, 1 fluxo, 1 habilidade; a tela absorvida sai da conta.
    assert loja.contagem["tela"] == {"published": 1, "candidate": 1} and loja.contagem["licao"] == {"published": 1}
    assert loja.absorvido == 1 and loja.total_aprendido == 5
    detalhe = visao.detalhe(LOJA)
    assert [x.entrada.ref for x in detalhe.absorvido] == ["li-tela-abs"]
    assert detalhe.absorvido[0].absorvida_em == "abc1234"                      # aparece, com o commit
    assert "li-tela-abs" not in {x.entrada.ref for x in detalhe.aprendido}
    assert {i.arquivo.value: i.presente for i in detalhe.declarado if i.arquivo.value != "loja"} == {
        "app": True, "catalogo": False, "telas": True, "sessao": False}
    assert next(i for i in detalhe.declarado if i.arquivo.value == "telas").quantidade == 2
    assert next(i for i in detalhe.declarado if i.arquivo.value == "catalogo").uso is None     # ausente não faz nada
    uso_da_loja = next(i for i in detalhe.declarado if i.arquivo.value == "loja").uso
    assert uso_da_loja is not None and uso_da_loja.camada is Camada.INERTE                      # sem nav_hints


def test_balde_e_fora_do_eixo_sao_contados_a_parte(visao: VisaoPorApp) -> None:
    v = visao.apps()
    assert v.nao_resolvido.total_aprendido == 4 and v.nao_resolvido.pacote == APP_NAO_RESOLVIDO
    assert v.nao_resolvido.contagem["fluxo"] == {"published": 3}
    assert v.nao_resolvido.contagem["habilidade"] == {"published": 1}
    assert APP_NAO_RESOLVIDO not in {a.pacote for a in v.apps}
    assert v.fora_do_eixo["memoria"] == {"-": 2} and v.fora_do_eixo["voz"] == {"published": 1}
    assert visao.detalhe(APP_NAO_RESOLVIDO).resumo.total_aprendido == 4


def test_detalhe_de_app_desconhecido_e_404(visao: VisaoPorApp) -> None:
    with pytest.raises(NaoEncontrado):
        visao.detalhe("com.exemplo.nao.existe")


def test_camada_de_uso_segue_os_modos_vigentes(visao: VisaoPorApp) -> None:
    def camadas(runtime: ModosDeRuntime | None) -> dict[str, str]:
        return {x.entrada.ref: x.uso.camada.value for x in visao.detalhe(LOJA, runtime).aprendido}

    c = camadas(ModosDeRuntime(receitas="replay", fluxos=True, habilidades=True))
    assert c["f-id-da-loja"] == "decide_sem_ia" and c["h.da.loja@1"] == "decide_sem_ia"
    assert c["li-tela-obs"] == "medido_nao_usado"            # candidata, modo observe (padrão): grava e valida
    assert c["li-tela-pub"] == "medido_nao_usado"            # observe: a sessão não consome
    assert c["li-licao"] == "nao_medido"                     # lição em shadow (padrão): nem a medida existe
    desligado = camadas(ModosDeRuntime(receitas="off", fluxos=False, habilidades=False))
    assert desligado["f-id-da-loja"] == "inerte" and desligado["h.da.loja@1"] == "inerte"
    # Sem conseguir ler o config, o que depende dele sai `desconhecida`, nunca um palpite.
    sem = camadas(None)
    assert sem["f-id-da-loja"] == "desconhecida" and sem["li-licao"] == "nao_medido"


def test_camada_pura_cobre_a_tabela_do_desenho() -> None:
    t = ModosDeUso(receitas="replay", fluxos=True, habilidades=True, licoes=Modo.ON, telas=ModoDeTelas.ON)
    s = SkillState
    assert uso_do_item(LivroKind.RECEITA, s.PUBLISHED, t).camada is Camada.DECIDE_SEM_IA
    assert uso_do_item(LivroKind.RECEITA, s.CANDIDATE, t).camada is Camada.MEDIDO_NAO_USADO
    assert uso_do_item(LivroKind.RECEITA, s.VALIDATED, t).camada is Camada.INERTE
    assert uso_do_item(LivroKind.RECEITA, s.PUBLISHED, replace(t, receitas="shadow")).camada is Camada.MEDIDO_NAO_USADO
    assert uso_do_item(LivroKind.FLUXO, s.CANDIDATE, t).camada is Camada.MEDIDO_NAO_USADO
    assert uso_do_item(LivroKind.LICAO, s.PUBLISHED, t).camada is Camada.VAI_AO_PROMPT
    assert uso_do_item(LivroKind.LICAO, s.PUBLISHED, replace(t, licoes=Modo.SHADOW)).camada is Camada.NAO_MEDIDO
    assert uso_do_item(LivroKind.TELA, s.PUBLISHED, t).camada is Camada.CLASSIFICA_TELA
    observe = replace(t, telas=ModoDeTelas.OBSERVE)
    assert uso_do_item(LivroKind.TELA, s.PUBLISHED, observe).camada is Camada.MEDIDO_NAO_USADO
    assert uso_do_item(LivroKind.TELA, s.DEPRECATED, t, detalhe="absorvida:x").camada is Camada.INERTE
    assert uso_do_item(LivroKind.PREFERENCIA, s.PUBLISHED, t).camada is Camada.PRE_PREENCHE
    assert uso_do_item(LivroKind.MEMORIA, None, t).camada is Camada.CONTEXTO_DA_PERSONA
    assert uso_do_item(LivroKind.LICAO, s.PUBLISHED, replace(t, licoes=None)).camada is Camada.DESCONHECIDA
    assert uso_do_declarado(ArquivoDeclarado.CATALOGO).camada is Camada.VAI_AO_PROMPT
    assert uso_do_declarado(ArquivoDeclarado.TELAS).camada is Camada.CLASSIFICA_TELA
    assert uso_do_declarado(ArquivoDeclarado.SESSAO).camada is Camada.LOGIN_FORA_DA_IA
    assert uso_do_declarado(ArquivoDeclarado.LOJA).camada is Camada.VAI_AO_PROMPT


def _cfg(*, recipes: str, flows: bool, skills: bool) -> Config:
    """Um `Config` sem tocar no ambiente (`EnvSettings` leria o `.env`): só o arquivo importa à camada de uso."""
    cfg = object.__new__(Config)
    cfg.file = AppConfigFile(ai=AiCfg(recipes=recipes, flows=flows), skills=SkillsCfg(enabled=skills))
    return cfg


async def test_rotas_da_visao_por_app(visao: VisaoPorApp, mundo: Mundo) -> None:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico, cfg=_cfg(recipes="replay", flows=True, skills=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/aprendizado/apps")
        assert r.status_code == 200, r.text
        corpo = r.json()
        por_pacote = {a["pacote"]: a for a in corpo["apps"]}
        assert corpo["total"] == 6 and por_pacote[DECLARADO_SEM_LINHA]["aprendido"]["total"] == 0
        assert por_pacote[DECLARADO_SEM_LINHA]["existencia"] == "declarado"
        assert por_pacote[SOMENTE_LOJA]["existencia"] == "loja"
        assert por_pacote[DECLARADO_SEM_LINHA]["declarado"]["acoes"] == 4 and por_pacote[LOJA]["absorvido"] == 1
        assert por_pacote[LOJA]["uso"]["fluxo"] == {"decide_sem_ia": 1}
        assert corpo["nao_resolvido"]["aprendido"]["total"] == 4 and corpo["fora_do_eixo"]["memoria"] == {"-": 2}
        assert corpo["modos"] == {"receitas": "replay", "fluxos": True, "habilidades": True, "licoes": "shadow",
                                  "telas": "observe", "licoes_por_app": {}, "telas_por_app": {}}
        # sem override no config, cada app segue o global (30.20)
        assert por_pacote[LOJA]["modos_do_app"] == {"licoes": {"modo": "shadow", "origem": "global"},
                                                    "telas": {"modo": "observe", "origem": "global"}}
        # `/apps/{pacote}` NÃO cai na rota genérica `{kind}/{ref}` (que recusaria o `kind` com 422).
        d = await c.get(f"/api/aprendizado/apps/{LOJA}")
        assert d.status_code == 200, d.text
        assert {x["ref"] for x in d.json()["absorvido"]} == {"li-tela-abs"}
        assert d.json()["absorvido"][0]["absorvida_em"] == "abc1234"
        # A linha da visão por app traz a MESMA saúde da lista do Livro (30.4), sem o painel completar por outra rota.
        assert all("saude" in x for x in d.json()["absorvido"] + d.json()["aprendido"])
        assert d.json()["absorvido"][0]["saude"] is not None
        assert {x["tipo"] for x in d.json()["declarado"]} == {"app", "catalogo", "telas", "sessao", "loja"}
        assert (await c.get(f"/api/aprendizado/apps/{APP_NAO_RESOLVIDO}")).status_code == 200
        assert (await c.get("/api/aprendizado/apps/com.exemplo.nao.existe")).status_code == 404
        # O livro continua respondendo pela rota genérica.
        assert (await c.get("/api/aprendizado/receita/999999")).status_code == 404


async def test_rota_sem_config_devolve_modos_desconhecidos(visao: VisaoPorApp, cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/api/aprendizado/apps")
    assert r.status_code == 200 and r.json()["modos"]["receitas"] is None and r.json()["modos"]["fluxos"] is None


def test_o_registro_real_alimenta_a_visao_sem_pacote_literal() -> None:
    """O adaptador real lê o registro de apps do processo; o teste não nomeia pacote nenhum."""
    reais = DeclaradosDoRegistro().declarados()
    assert {d.package for d in reais} == {d.package for d in registry.registered()}
    assert all(d.acoes is None or d.tem_catalogo for d in reais)       # ação contada => o app tem catalogo.yaml
