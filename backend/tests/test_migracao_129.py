"""Migração 129 (31.271): `recipes.ultima_consulta_em` e `recipes.ultima_consulta_resultado`.

- atualização a partir da migração anterior: só a 129 é aplicada, as receitas anteriores ficam com as duas colunas
  NULL (nunca consultadas desde a migração) e nenhuma outra coluna muda; o segundo `migrate()` não aplica nada;
- banco novo = banco atualizado.

Nível de prova: `simulated` (SQLite; o PostgreSQL quando `TEST_DATABASE_URL` existe, pela fábrica de `test_db`).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import db as db_mod

from .test_db import _banco, _copia_das_migracoes

NOVA = "129_prova_da_candidata"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
COLUNAS = ("id, app_package, app_version, app_signature, variant, step_hash, step_key, version, status, actions,"
           " shadow_agree, shadow_total, created_at")
NOVAS = ("ultima_consulta_em", "ultima_consulta_resultado")


def _receita(db: db_mod.Database) -> None:
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, created_at) VALUES ('com.x','1','','','h','abrir',1,'candidate','[]',"
               "'2026-10-07T00:00:00.000Z')")


def test_atualizacao_para_a_129_mantem_as_receitas_e_nasce_sem_consulta(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == ANTERIOR
        _receita(db)
        antes = db.query(f"SELECT {COLUNAS} FROM recipes ORDER BY id")
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert db.query(f"SELECT {COLUNAS} FROM recipes ORDER BY id") == antes
        linha = db.one(f"SELECT {', '.join(NOVAS)} FROM recipes")
        assert linha is not None and all(linha[c] is None for c in NOVAS)
        assert db.migrate() == []                                        # idempotente
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
        for db in (novo, atualizado):
            assert db.divergencias() == []
            assert set(NOVAS) <= set(db.columns("recipes"))
    finally:
        novo.close()
        atualizado.close()
