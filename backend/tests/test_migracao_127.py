"""Migração 127 (31.154, adendo v1.95): `operacoes.parametros` nasce nula; nenhuma linha muda.

Nível de prova: `simulated` (SQLite; o PostgreSQL quando `TEST_DATABASE_URL` existe, pela fábrica de `test_db`).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import db as db_mod

from .test_db import _banco, _copia_das_migracoes

NOVA = "127_operacoes_parametros"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
TS = "2026-10-06T12:00:00.000Z"


def test_atualizacao_para_a_127_soma_a_coluna_nula(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == ANTERIOR
        assert "parametros" not in db.columns("operacoes")
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',1)")
        db.execute("INSERT INTO operacoes(id, command, app_id, acao_final, max_usd, status, idempotency_key,"
                   " corpo_sha256, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("op-antiga", "comente", "instagram", "preparar", 1.0, "em_curso", "k-antiga", "x", TS, TS))
        antes = db.query("SELECT * FROM operacoes")
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert db.scalar("SELECT parametros FROM operacoes WHERE id='op-antiga'") is None
        assert [{k: v for k, v in r.items() if k != "parametros"} for r in db.query("SELECT * FROM operacoes")] == antes
        assert db.migrate() == []                                    # idempotente
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_as_mesmas_colunas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        assert sorted(novo.columns("operacoes")) == sorted(atualizado.columns("operacoes"))
        assert novo.divergencias() == [] and atualizado.divergencias() == []
    finally:
        novo.close()
        atualizado.close()
