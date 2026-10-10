"""31.307 (ressalva do 31.293): o laço de eventos não espera o banco no boot, e a espera que sobrar aparece no log.

O laço parou 70 s em 10/10/2026 (16:33Z) dentro de UMA consulta de `DeviceManager._ultimo_dto_persistido`, na thread do laço, com o
disco estrangulado. O que se prova:
- a migração 135 cria o índice `events(instance_id, kind, id)`, é aditiva e idempotente, e o plano da consulta o usa (SQLite);
- o último DTO de todos os aparelhos é lido numa thread ANTES da adoção, e `publish` depois não consulta o banco na thread do laço;
- a consulta síncrona que passa do limite NA THREAD DO LAÇO vira aviso (uma vez por intervalo, com o chamador), e a mesma espera
  numa thread comum não conta.

Nível de prova: `simulated` (SQLite; o PostgreSQL quando `TEST_DATABASE_URL` existe, pela fábrica de `test_db`). `real`: `not_run`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from pathlib import Path

import pytest

from app import db as db_mod

from .conftest import Harness
from .test_db import _banco, _copia_das_migracoes

NOVA = "135_indice_dos_eventos_por_instancia"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
INDICE = "idx_events_instance_kind_id"
INDICE_DOS_SINAIS = "ix_learning_signals_kind_criado"
CONSULTA = ("SELECT data FROM events WHERE kind='instance.updated' AND instance_id=? ORDER BY id DESC LIMIT 20")


def _indices(db: db_mod.Database, tabela: str = "events") -> set[str]:
    if db.dialect == "postgres":
        return {str(r["indexname"]) for r in db.query("SELECT indexname FROM pg_indexes WHERE tablename=?", (tabela,))}
    return {str(r["name"]) for r in db.query(f"PRAGMA index_list({tabela})")}


def test_a_migracao_135_cria_o_indice_sem_tocar_nas_linhas_e_e_idempotente(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == ANTERIOR
        db.execute("INSERT INTO events(ts, kind, level, instance_id, message, data) VALUES (?,?,?,?,?,?)",
                   ("2026-10-10T16:00:00.000Z", "instance.updated", "info", "android-01", "m", "{}"))
        assert INDICE not in _indices(db)
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert INDICE in _indices(db) and INDICE_DOS_SINAIS in _indices(db, "learning_signals")
        assert db.scalar("SELECT COUNT(*) FROM events") == 1                  # nenhuma linha tocada
        assert db.migrate() == []                                             # idempotente
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_o_mesmo_indice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        for db in (novo, atualizado):
            assert db.divergencias() == [] and INDICE in _indices(db)
            assert INDICE_DOS_SINAIS in _indices(db, "learning_signals")
    finally:
        novo.close()
        atualizado.close()


def test_o_plano_da_consulta_do_boot_usa_o_indice(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        if db.dialect != "sqlite":
            pytest.skip("EXPLAIN QUERY PLAN é do SQLite; no PostgreSQL a medida é manual (EXPLAIN no harness)")
        plano = " ".join(str(r["detail"]) for r in db.query("EXPLAIN QUERY PLAN " + CONSULTA, ("android-01",)))
        assert INDICE in plano, plano
    finally:
        db.close()


def test_o_plano_da_leitura_das_intervencoes_usa_o_indice_dos_sinais(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        if db.dialect != "sqlite":
            pytest.skip("EXPLAIN QUERY PLAN é do SQLite")
        plano = " ".join(str(r["detail"]) for r in db.query(
            "EXPLAIN QUERY PLAN SELECT attempt_id, step_id FROM learning_signals WHERE kind IN (?,?) AND created_at >= ?"
            " AND created_at < ? AND simulated = 0", ("a", "b", "2026-10-01", "2026-10-10")))
        assert INDICE_DOS_SINAIS in plano, plano
    finally:
        db.close()


# ===================================================================== o boot sem consulta no laço
def _evento(db: db_mod.Database, instance_id: str, marca: str) -> None:
    dados = {"instance": {"id": instance_id, "marca": marca, "renderer": {"configured": True}}}
    db.execute("INSERT INTO events(ts, kind, level, instance_id, message, data) VALUES (?,?,?,?,?,?)",
               ("2026-10-10T16:00:00.000Z", "instance.updated", "info", instance_id, "m", json.dumps(dados)))


@pytest.mark.asyncio
async def test_o_ultimo_dto_e_lido_fora_do_laco_antes_da_adocao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    devices, db = st.devices, st.db
    ids = list(devices.devices)
    assert len(ids) >= 2
    for i in ids[:2]:
        _evento(db, i, f"antigo-{i}")
        _evento(db, i, f"novo-{i}")
    for rt in devices.devices.values():
        rt.dto_persistido, rt.dto_persistido_lido = None, False
    laco = threading.get_ident()
    onde: list[int] = []
    original = db.query

    def espia(sql, params=()):
        if "instance.updated" in sql:
            onde.append(threading.get_ident())
        return original(sql, params)
    db.query = espia                                                           # type: ignore[method-assign]
    try:
        await devices._pre_carregar_dtos_persistidos()
        assert len(onde) == len(ids), "uma leitura por aparelho"
        assert all(t != laco for t in onde), "a leitura rodou na thread do laço"
        for i in ids[:2]:
            assert devices.devices[i].dto_persistido["marca"] == f"novo-{i}"      # o mais recente
        assert all(rt.dto_persistido_lido for rt in devices.devices.values())
        depois = len(onde)
        for rt in devices.devices.values():
            devices._ultimo_dto_persistido(rt)                                 # o que `publish` chama
        assert len(onde) == depois, "publish consultou o banco depois da pré-leitura"
        await devices._pre_carregar_dtos_persistidos()                         # nada pendente: nenhuma leitura
        assert len(onde) == depois
    finally:
        db.query = original                                                    # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_falha_na_pre_leitura_deixa_a_leitura_sob_demanda(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    st = harness.state
    assert st is not None
    devices, db = st.devices, st.db
    rt = next(iter(devices.devices.values()))
    _evento(db, rt.id, "x")
    rt.dto_persistido, rt.dto_persistido_lido = None, False
    original = db.query

    def quebra(sql, params=()):
        raise RuntimeError("banco indisponível")
    db.query = quebra                                                          # type: ignore[method-assign]
    try:
        with caplog.at_level(logging.ERROR, logger="poc.devices"):
            await devices._pre_carregar_dtos_persistidos()
        assert not rt.dto_persistido_lido and rt.dto_persistido is None
    finally:
        db.query = original                                                    # type: ignore[method-assign]
    assert devices._ultimo_dto_persistido(rt)["marca"] == "x"                  # e a leitura de sempre ainda funciona


# ===================================================================== a medida na thread do laço
@pytest.mark.asyncio
async def test_consulta_lenta_na_thread_do_laco_vira_aviso_com_o_chamador(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                         caplog: pytest.LogCaptureFixture) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        monkeypatch.setattr(db_mod, "LIMITE_DA_CONSULTA_NO_LACO_S", 0.0)
        with caplog.at_level(logging.WARNING, logger="poc.db"):
            db.query("SELECT 1")
            db.query("SELECT 2")                                               # dentro do intervalo: conta, não repete o aviso
        avisos = [r.getMessage() for r in caplog.records if "consulta síncrona no laço" in r.getMessage()]
        assert len(avisos) == 1 and "test_laco_sem_sql_sincrono.py" in avisos[0]
        assert db.consultas_lentas_no_laco == 2
    finally:
        db.close()


@pytest.mark.asyncio
async def test_a_mesma_espera_numa_thread_comum_nao_conta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        monkeypatch.setattr(db_mod, "LIMITE_DA_CONSULTA_NO_LACO_S", 0.0)
        with caplog.at_level(logging.WARNING, logger="poc.db"):
            await asyncio.to_thread(db.query, "SELECT 1")
        assert db.consultas_lentas_no_laco == 0
        assert not [r for r in caplog.records if "consulta síncrona no laço" in r.getMessage()]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_a_consulta_rapida_no_laco_nao_avisa(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        with caplog.at_level(logging.WARNING, logger="poc.db"):
            db.query("SELECT 1")
        assert db.consultas_lentas_no_laco == 0
    finally:
        db.close()


# ===================================================================== a saúde fora do laço (4º ponto, 16:56Z)
@pytest.mark.asyncio
async def test_a_saude_roda_fora_da_thread_do_laco(harness: Harness) -> None:
    """O despejo das 16:56Z: `/health` esperava a trava do banco NA thread do laço enquanto a curadoria (`curar`, que o agendador
    despacha numa thread do pool) segurava a trava numa consulta longa. A rota agora chama `saude.health` numa thread."""
    import httpx

    from app.main import create_app

    st = harness.state
    assert st is not None
    laco = threading.get_ident()
    vistas: list[int] = []
    original = st.saude.health

    def espia():
        vistas.append(threading.get_ident())
        return original()
    st.saude.health = espia                                                    # type: ignore[method-assign]
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                                     base_url="http://127.0.0.1") as c:
            r = await c.get("/api/health")
        assert r.status_code == 200
        assert vistas and all(t != laco for t in vistas), "a saúde rodou na thread do laço"
    finally:
        st.saude.health = original                                             # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_a_curadoria_nao_consulta_o_banco_na_thread_do_laco(harness: Harness) -> None:
    """A leitura do relatório de falhas (`executar` → `_ler` → `relatorio_sql._intervencoes_ligadas`) vem de `_curadoria_uma_vez`,
    que a despacha com `asyncio.to_thread(learning.curar)`: nenhuma consulta dela roda na thread do laço."""
    st = harness.state
    assert st is not None
    laco = threading.get_ident()
    vistas: list[tuple[int, str]] = []
    original = st.db.query

    def espia(sql, params=()):
        if "learning_signals" in sql:
            vistas.append((threading.get_ident(), sql[:60]))
        return original(sql, params)
    st.db.query = espia                                                        # type: ignore[method-assign]
    try:
        if not await st._curadoria_uma_vez():
            pytest.skip("esta instância não é a líder da curadoria")
    finally:
        st.db.query = original                                                 # type: ignore[method-assign]
    assert vistas, "a curadoria não leu os sinais: o teste não mede nada"
    assert all(t != laco for t, _ in vistas), f"consulta de sinais na thread do laço: {vistas}"
