"""As métricas do aprendizado e a lista de revisões do curador (30.8; §10 e §11.3 do desenho).

Nível de prova: `simulated` (banco de teste migrado, linhas sintéticas; o aproveitamento é injetado falso). O `real` é
a leitura de `GET /api/aprendizado/metricas` no central depois de implantado.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.metricas import (ConducaoDaEtapa, MetricasDoAprendizado, RevisaoLida,
                                                       ServicoDeMetricas, TransicaoLida, orcamento, proxy_de_falhas,
                                                       resumo_do_curador, tempos)
from app.modules.learning.application.ports import Ajustes, AjustesDoCurador, LeituraDaJanela
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.saude import Rotulo
from app.modules.learning.domain.vocabulario import Modo
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.metricas_sql import FontesDeMetricasSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.util import to_iso

from .fake_skills import banco as banco_migrado

AGORA = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
# Pacotes SINTÉTICOS: o código de produção não tem pacote nenhum (ADR-052).
APP = "com.exemplo.loja"
OUTRO = "com.exemplo.outro"
PRECOS = {"modelo-caro": [5.0, 0.5, 6.25, 25.0]}     # (entrada, cache, escrita, saída) por milhão
RUN_X = "r-20261001010000-abcdef"                    # o formato de `new_run_id`: o motivo casa exato


def _em(texto: str) -> str:
    return to_iso(datetime.fromisoformat(texto).replace(tzinfo=UTC))


# ------------------------------------------------------------------ contas puras
def _t(i: int, ref: str, para: str, quando: str, de: str | None = None, por: str = "sistema") -> TransicaoLida:
    return TransicaoLida(id=i, item_ref=ref, from_state=de, to_state=para, decided_by=por, decided_at=_em(quando))


def test_tempos_pareiam_a_ultima_chegada_e_so_contam_a_chegada_na_janela() -> None:
    desde, ate = _em("2026-09-19T12:00"), _em("2026-10-03T12:00")
    trilhas = {
        "li-a": [_t(1, "li-a", "candidate", "2026-09-25T00:00"), _t(2, "li-a", "validated", "2026-09-26T00:00"),
                 _t(3, "li-a", "published", "2026-09-28T00:00")],
        # Voltou a candidato: o par é da ÚLTIMA chegada a candidate.
        "li-b": [_t(4, "li-b", "candidate", "2026-09-01T00:00"), _t(5, "li-b", "candidate", "2026-09-30T00:00"),
                 _t(6, "li-b", "validated", "2026-09-30T06:00")],
        # Sem a chegada a candidate na trilha (a receita antiga): fora, e o `n` mostra.
        "li-c": [_t(7, "li-c", "validated", "2026-10-01T00:00")],
        # A chegada fora da janela não conta.
        "li-d": [_t(8, "li-d", "candidate", "2026-09-01T00:00"), _t(9, "li-d", "validated", "2026-09-02T00:00")],
    }
    t = tempos(trilhas, desde, ate)
    # 24 h (li-a) e 6 h (li-b); o percentil é o de `app/metricas.py`, posto ceil(p·n/100) (K-085): a mediana de dois é
    # o MENOR (o `round` antigo dava o maior) e o p90, o maior.
    assert (t["candidate_validated"].n, t["candidate_validated"].mediana_h, t["candidate_validated"].p90_h) == (
        2, 6.0, 24.0)
    assert t["validated_published"].n == 1 and t["validated_published"].mediana_h == 48.0
    vazio = tempos({}, desde, ate)["validated_published"]
    assert (vazio.mediana_h, vazio.p90_h, vazio.n) == (None, None, 0)      # ausente é None, nunca zero


def test_proxy_compara_so_as_etapas_com_as_duas_conducoes() -> None:
    linhas = [ConducaoDaEtapa(APP, "h1", "recipe", False, 2), ConducaoDaEtapa(APP, "h1", "ai", True, 1),
              ConducaoDaEtapa(APP, "h1", "ai", False, 1), ConducaoDaEtapa(APP, "h1", "recipe+ai", True, 1),
              ConducaoDaEtapa(APP, "h2", "recipe", True, 5),          # só receita: não compara
              ConducaoDaEtapa(APP, "h3", "sem_ator", True, 9),        # LT-6: fora
              ConducaoDaEtapa(OUTRO, "h1", "ai", True, 4)]            # o mesmo hash noutro app é outra etapa
    p = proxy_de_falhas(linhas, APP)
    assert p["rotulo"] == "proxy" and p["etapas_comparadas"] == 1
    assert (p["com_receita"].etapas, p["com_receita"].falhas, p["com_receita"].taxa_de_falha) == (3, 1, 0.333)
    assert (p["so_ia"].etapas, p["so_ia"].falhas, p["so_ia"].taxa_de_falha) == (2, 1, 0.5)
    nada = proxy_de_falhas([ConducaoDaEtapa(APP, "h2", "recipe", True, 5)], None)
    assert nada["etapas_comparadas"] == 0 and nada["so_ia"].taxa_de_falha is None


def _leitura(g: float, medidos: tuple[float, ...], sem_medida: tuple[int, ...] = ()) -> LeituraDaJanela:
    return LeituraDaJanela(gasto_da_operacao=g, custos_medidos=medidos, tamanhos_sem_medida=sem_medida,
                           tamanhos_sem_medida_na_hora=(), gasto_medido=sum(medidos), gasto_medido_na_hora=0.0,
                           revisoes_antes_de_hoje=len(medidos) + len(sem_medida), revisoes_de_hoje=0)


def test_orcamento_usa_as_revisoes_gravadas_e_avisa_a_80_porcento() -> None:
    aj = AjustesDoCurador(modo=Modo.SHADOW, alfa=0.10, k=1.5, janela_dias=7)
    o = orcamento(_leitura(10.0, (0.01, 0.01)), aj, PRECOS)
    # B = min(0,1 × 10, 1,5 × 2 × 0,01) = 0,03; C = 0,02.
    assert (o["orcamento"], o["gasto_da_curadoria"], o["teto_alfa"], o["uso"], o["aviso"]) == (0.03, 0.02, 1.0, 0.667,
                                                                                              False)
    # A revisão sem medida entra pela estimativa do dossiê gravado (a mesma conta da volta do curador).
    com_estimada = orcamento(_leitura(10.0, (0.01, 0.01), (40_000,)), aj, PRECOS)
    assert com_estimada["gasto_da_curadoria"] > 0.02 and com_estimada["aviso"] is True
    # Sem nenhuma medida (o `usd` ainda não gravado), o c̄ é a média das estimativas: o B_W não cai a zero por isso.
    so_estimada = orcamento(_leitura(10.0, (), (3000, 3000)), aj, PRECOS)
    # Cada uma: (1000 × 5 + 400 × 25) / 1e6 = 0,015; B = min(1, 1,5 × 2 × 0,015) = 0,045; C = 0,03.
    assert (so_estimada["orcamento"], so_estimada["gasto_da_curadoria"], so_estimada["uso"]) == (0.045, 0.03, 0.667)
    sem_operacao = orcamento(_leitura(0.0, (0.01,)), aj, PRECOS)
    assert (sem_operacao["orcamento"], sem_operacao["uso"], sem_operacao["aviso"]) == (0.0, None, False)


def test_resumo_do_curador_separa_o_simulado() -> None:
    r = resumo_do_curador([
        RevisaoLida(APP, "ok", False, 0.003, "manter", "aceita", False),
        RevisaoLida(APP, "ok", True, 0.0, "desativar", None, False),
        RevisaoLida(APP, "recusada:custo", False, 0.0, None, None, False),
        RevisaoLida(APP, "invalida:json", False, 0.001, None, None, False),
        RevisaoLida(APP, "ok", False, 0.002, "desativar", "recusada", True)])
    assert r == {"revisoes": 4, "simuladas": 1, "validade": {"ok": 2, "invalida": 1, "recusada": 1},
                 "decisoes": {"desativar": 1, "manter": 1}, "aplicadas": 2, "overrides": 1, "usd": 0.006}


# ------------------------------------------------------------------ do banco à rota
def _app(db: Database, id_: str, pacote: str) -> None:
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)", (id_, f"App {id_}", pacote))


def _item(db: Database, id_: str, app: str, state: str, criado: str, *, state_at: str | None = None) -> None:
    db.execute("INSERT INTO learning_items(id, kind, state, scope_app, scope_capability, content, content_hash,"
               " summary, source_kind, created_by, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (id_, "tela", state, app, f"cap-{id_}", "{}", f"h-{id_}", f"resumo {id_}", "recovery", "system",
                _em(criado), _em(state_at) if state_at else None))


def _transicao(db: Database, ref: str, de: str | None, para: str, quando: str, *, por: str = "sistema",
               motivo: str = "teste") -> None:
    db.execute("INSERT INTO learning_transitions(item_ref, item_kind, from_state, to_state, reason, decided_by,"
               " decided_at) VALUES (?,?,?,?,?,?,?)", (ref, "tela", de, para, motivo, por, _em(quando)))


def _evidencia(db: Database, ref: str, stance: str, quando: str, run: str, *, simulado: bool = False) -> None:
    db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, observed_at)"
               " VALUES (?,?,?,?,?,?)", (ref, stance, f"run:{run}", run, int(simulado), _em(quando)))


def _revisao(db: Database, id_: str, app: str, quando: str, *, validade: str = "ok", decisao: str | None = None,
             simulado: bool = False, usd: float = 0.0, template: str = "curador", final: str | None = None,
             override: bool = False) -> None:
    saida = json.dumps({"decisao": decisao, "evidencias_citadas": [], "confianca": "alta"}) if decisao else None
    db.execute("INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, scope_app, gatilho, dossie_hash,"
               " dossie, template_id, template_versao, provedor, modelo, simulated, usd, saida, validade,"
               " decisao_final, override) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (id_, _em(quando), "li-pub", "tela", app, "manual", f"dh-{id_}", "{}", template, "1",
                "" if validade.startswith("recusada") else "hub", "modelo-x", int(simulado), usd, saida, validade,
                final, int(override)))


def _etapa(db: Database, run: str, n: int, hash_: str, driven_by: str, status: str, *, simulado: bool = False) -> None:
    if db.one("SELECT id FROM runs WHERE id=?", (run,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
                   " app_ids) VALUES (?,?,?,?,?,?,?,?,?)",
                   (run, run, "abrir", "execute", "completed", int(simulado), '["android-01"]', _em("2026-10-02T10:00"),
                    '["loja"]'))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES (?,?,?,?)",
                   (f"{run}:o", run, "android-01", "succeeded"))
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, driven_by, started_at, finished_at, template_hash)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{run}:s{n}", run, f"{run}:o", "android-01", 1, n, f"k{n}", "t", "g", "{}", 60, 3, status, driven_by,
                _em("2026-10-02T10:00"), _em("2026-10-02T10:01"), hash_))


def _aproveitamento_falso(dias: int, agora: datetime) -> dict[str, object]:
    assert (dias, agora) == (14, AGORA)
    return {"totais": {"por_receita": 4, "chamadas_evitadas_estimadas": 3.0, "divergencias": {"x": 1}},
            "fluxos": [{"package": APP, "por_receita": 3, "chamadas_evitadas_estimadas": 2.5},
                       {"package": OUTRO, "por_receita": 1, "chamadas_evitadas_estimadas": 0.5}]}


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        _app(db, "loja", APP)
        _app(db, "outro", OUTRO)
        _item(db, "li-pub", APP, "published", "2026-09-25T00:00", state_at="2026-09-28T00:00")
        _item(db, "li-cand", APP, "candidate", "2026-10-01T00:00")
        _item(db, "li-desl", APP, "disabled", "2026-09-10T00:00")
        _item(db, "li-outro", OUTRO, "published", "2026-09-30T00:00", state_at="2026-10-01T00:00")
        _transicao(db, "li-pub", None, "candidate", "2026-09-25T00:00")
        _transicao(db, "li-pub", "candidate", "validated", "2026-09-26T00:00")
        _transicao(db, "li-pub", "validated", "published", "2026-09-28T00:00", por="painel")
        _transicao(db, "li-desl", "published", "disabled", "2026-10-02T00:00", motivo="contradição")
        _transicao(db, "li-outro", "candidate", "validated", "2026-09-30T00:00")
        _transicao(db, "li-outro", "validated", "published", "2026-10-01T00:00")
        _transicao(db, "li-sumiu", "published", "disabled", "2026-10-02T00:00")          # fora do livro
        # 30.23: a execução run-x foi marcada evidência inválida no li-pub (fora da janela; a regra vale assim mesmo).
        _transicao(db, "li-pub", "published", "published", "2026-09-10T00:00", por="painel",
                   motivo=f"evidencia_invalida:{RUN_X}")
        _evidencia(db, "li-pub", "for", "2026-09-29T00:00", "run-a")                       # conta
        _evidencia(db, "li-pub", "for", "2026-09-27T00:00", "run-b")                       # antes de promovido
        _evidencia(db, "li-pub", "against", "2026-10-01T00:00", "run-c")                   # conta, contesta
        _evidencia(db, "li-pub", "for", "2026-10-01T01:00", RUN_X)                         # inválida: fora
        _evidencia(db, "li-pub", "for", "2026-10-01T02:00", "run-s", simulado=True)        # simulada: fora
        _evidencia(db, "li-cand", "for", "2026-10-01T03:00", "run-d")                      # não publicado
        _evidencia(db, "li-sumiu", "for", "2026-10-01T04:00", "run-e")                     # fora do livro
        _revisao(db, "lr-1", APP, "2026-10-02T10:00", decisao="manter", usd=0.003)
        _revisao(db, "lr-2", APP, "2026-10-02T11:00", decisao="desativar", simulado=True)
        _revisao(db, "lr-3", APP, "2026-10-02T09:00", validade="recusada:custo")
        _revisao(db, "lr-4", APP, "2026-10-02T09:30", decisao="manter", template="intencao")   # 30.25: não é parecer
        _revisao(db, "lr-5", OUTRO, "2026-10-02T12:00", decisao="desativar", usd=0.002, final="aceita", override=True)
        for n, (h, quem, status) in enumerate([("h1", "recipe", "succeeded"), ("h1", "recipe", "succeeded"),
                                               ("h1", "ai", "failed"), ("h1", "ai", "succeeded"),
                                               ("h2", "recipe", "failed"), ("h3", "sem_ator", "failed")]):
            _etapa(db, "run-p", n, h, quem, status)
        _etapa(db, "run-sim", 0, "h1", "ai", "failed", simulado=True)
        repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(repo, FontesSql(db, pacotes_do_registro=list), TriagemDeCredencial(),
                                       ajustes=Ajustes, relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
        self.metricas = ServicoDeMetricas(self.servico, FontesDeMetricasSql(db), aproveitamento=_aproveitamento_falso,
                                          curador=lambda: AjustesDoCurador(modo=Modo.SHADOW), precos=lambda: PRECOS,
                                          relogio=lambda: AGORA)
        self.servico.anexar(self.metricas)


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "metricas.sqlite3")
    yield Mundo(db)
    db.close()


def test_metricas_do_app_seguem_a_tabela_do_desenho(mundo: Mundo) -> None:
    m = mundo.metricas.metricas(app=APP, dias=14)
    assert isinstance(m, MetricasDoAprendizado)
    assert m.itens == {"tela": {"published": 1, "candidate": 1, "disabled": 1}}
    assert m.aprovacoes == {"sistema": {"candidate": 1, "disabled": 1, "validated": 1}, "pessoa": {"published": 1}}
    assert m.curador == {"revisoes": 2, "simuladas": 1, "validade": {"ok": 1, "invalida": 0, "recusada": 1},
                         "decisoes": {"manter": 1}, "aplicadas": 0, "overrides": 0, "usd": 0.003}
    assert m.refutados_depois_de_promovidos == {"desligados_pelo_sistema": 1, "desligados_por_pessoa": 0,
                                                "publicados_com_evidencia_contra": 1}
    # Só a evidência real, depois de promovido, fora a da execução inválida.
    assert (m.sucesso_depois_de_promovido.a_favor, m.sucesso_depois_de_promovido.contra,
            m.sucesso_depois_de_promovido.taxa) == (1, 1, 0.5)
    assert m.churn == {"transicoes": 4, "itens_com_transicao": 2, "criados": 2, "desligados": 1}
    assert (m.tempos["candidate_validated"].n, m.tempos["candidate_validated"].mediana_h) == (1, 24.0)
    assert (m.tempos["validated_published"].n, m.tempos["validated_published"].mediana_h) == (1, 48.0)
    assert set(m.saude) == {r.value for r in Rotulo} and sum(m.saude.values()) == 1
    assert m.economia is not None and (m.economia["por_receita"], m.economia["chamadas_evitadas_estimadas"]) == (3, 2.5)
    p = m.falhas_evitadas_proxy
    assert p["etapas_comparadas"] == 1 and p["so_ia"].falhas == 1      # a etapa simulada ficou fora
    assert m.orcamento_do_curador is not None and m.orcamento_do_curador["uso"] is None   # sem operação, sem B_W
    assert m.sem_item == {"transicoes": 1, "evidencias": 1}


def test_sem_app_soma_tudo_e_a_economia_vem_dos_totais(mundo: Mundo) -> None:
    m = mundo.metricas.metricas(app=None, dias=14)
    assert m.tempos["validated_published"].n == 2
    assert m.curador["revisoes"] == 3 and m.curador["overrides"] == 1 and m.curador["aplicadas"] == 1
    assert m.economia is not None and m.economia["por_receita"] == 4
    sem_economia = ServicoDeMetricas(mundo.servico, FontesDeMetricasSql(mundo.db), relogio=lambda: AGORA)
    vazio = sem_economia.metricas(app=None, dias=14)
    assert vazio.economia is None and vazio.orcamento_do_curador is None


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_rota_das_metricas(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/api/aprendizado/metricas", params={"app": APP, "dias": 14})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["app"] == APP and corpo["janela_dias"] == 14
    assert corpo["falhas_evitadas_proxy"]["rotulo"] == "proxy"
    assert corpo["tempos"]["candidate_validated"] == {"mediana_h": 24.0, "p90_h": 24.0, "n": 1}
    assert corpo["sucesso_depois_de_promovido"] == {"a_favor": 1, "contra": 1, "taxa": 0.5}
    assert (await cliente.get("/api/aprendizado/metricas", params={"dias": 0})).status_code == 422


async def test_rota_das_revisoes_filtra_e_pagina(cliente: httpx.AsyncClient) -> None:
    todas = (await cliente.get("/api/aprendizado/revisoes")).json()
    assert [x["id"] for x in todas["revisoes"]] == ["lr-5", "lr-2", "lr-1", "lr-3"]      # sem o rótulo de intenção
    assert todas["proximo"] is None
    assert "dossie" not in todas["revisoes"][0] and todas["revisoes"][0]["override"] is True
    manter = (await cliente.get("/api/aprendizado/revisoes", params={"decisao": "manter"})).json()
    assert [x["id"] for x in manter["revisoes"]] == ["lr-1"] and manter["revisoes"][0]["confianca"] == "alta"
    do_app = (await cliente.get("/api/aprendizado/revisoes", params={"app": APP, "desde": "2026-10-02T09:30"})).json()
    assert [x["id"] for x in do_app["revisoes"]] == ["lr-2", "lr-1"]
    vistos: list[str] = []
    cursor = None
    while True:
        params = {"limite": 1} | ({"cursor": cursor} if cursor else {})
        pagina = (await cliente.get("/api/aprendizado/revisoes", params=params)).json()
        vistos += [x["id"] for x in pagina["revisoes"]]
        cursor = pagina["proximo"]
        if cursor is None:
            break
    assert vistos == ["lr-5", "lr-2", "lr-1", "lr-3"]
    assert (await cliente.get("/api/aprendizado/revisoes", params={"cursor": "so-data"})).status_code == 422


async def test_sem_composicao_responde_503(mundo: Mundo) -> None:
    servico = LearningService(SqlLearningRepository(mundo.db, precos=dict), FontesSql(mundo.db), TriagemDeCredencial(),
                              ajustes=Ajustes, relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/aprendizado/metricas")).status_code == 503
        assert (await c.get("/api/aprendizado/revisoes")).status_code == 503


def test_a_montagem_compoe_as_metricas(tmp_path: Path) -> None:
    from app.config import LearningCfg
    from app.modules.learning.infrastructure.montagem import montar_aprendizado
    db = banco_migrado(tmp_path, "montagem.sqlite3")
    try:
        servico = montar_aprendizado(db, config=LearningCfg, retencao_de_logs_dias=lambda: 14, precos=lambda: PRECOS,
                                     relogio=lambda: AGORA)
        metricas = servico.extensao(ServicoDeMetricas)
        assert metricas is not None
        m = metricas.metricas(app=None, dias=7)
        assert m.economia is not None and m.economia["etapas"] == 0         # o aproveitamento real, banco vazio
        assert m.orcamento_do_curador is not None and m.orcamento_do_curador["modo"] == "off"
    finally:
        db.close()


def test_janela_vazia_sai_com_none_e_nao_com_zero(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "vazio.sqlite3")
    try:
        servico = LearningService(SqlLearningRepository(db, precos=dict), FontesSql(db), TriagemDeCredencial(),
                                  ajustes=Ajustes, relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
        m = ServicoDeMetricas(servico, FontesDeMetricasSql(db), relogio=lambda: AGORA).metricas(app=None, dias=14)
        assert m.sucesso_depois_de_promovido.taxa is None
        assert all(t.mediana_h is None and t.n == 0 for t in m.tempos.values())
        assert m.falhas_evitadas_proxy["com_receita"].taxa_de_falha is None
        assert m.desde == to_iso(AGORA - timedelta(days=14))
    finally:
        db.close()


def test_o_percentil_das_metricas_e_o_do_processo() -> None:
    """K-085 e 30.33-C: a camada de aplicação não importa `app.metricas`, então `_percentil` repete a fórmula; este
    teste as prende uma à outra, de n = 1 a 200, nos percentis que o painel e o relatório usam."""
    from app.metricas import percentil
    from app.modules.learning.application.metricas import _percentil

    for n in range(1, 201):
        ordenada = [float(i) * 1.5 for i in range(n)]
        for p in (1, 5, 10, 25, 50, 75, 90, 95, 99, 100):
            assert _percentil(ordenada, p) == percentil(ordenada, p), (n, p)
    assert _percentil([], 50) is None and percentil([], 50) is None
