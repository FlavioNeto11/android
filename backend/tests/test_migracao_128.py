"""Migração 128 (31.200, adendo v1.113): `learning_items.scope_subject` e o índice único `ux_learning_items_vivo` com o
assunto.

- atualização a partir da migração anterior: só a 128 é aplicada, os itens anteriores ficam com assunto '' e nenhuma
  outra coluna muda; o índice segue único (o mesmo item vivo no mesmo escopo não duplica) e passa a separar assuntos;
  o segundo `migrate()` não aplica nada;
- banco novo = banco atualizado.

Nível de prova: `simulated` (SQLite; o PostgreSQL quando `TEST_DATABASE_URL` existe, pela fábrica de `test_db`).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import db as db_mod

from .test_db import _banco, _copia_das_migracoes, _tem_indice

NOVA = "128_livro_escopo_de_assunto"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
TS = "2026-10-07T00:00:00.000Z"
COLUNAS = ("id, kind, state, scope_app, scope_capability, scope_step_hash, scope_role, scope_profile_id, content,"
           " content_hash, summary, source_kind, created_by, created_at")


def _item(db: db_mod.Database, item_id: str, *, subject: str | None = None) -> None:
    colunas, valores = COLUNAS, [item_id, "licao", "candidate", "com.x", "", "", "writer", "", "{}", "h", "t",
                                 "operation_fact", "sistema", TS]
    if subject is not None:
        colunas, valores = colunas + ", scope_subject", [*valores, subject]
    db.execute(f"INSERT INTO learning_items({colunas}) VALUES ({','.join('?' * len(valores))})", tuple(valores))


def test_atualizacao_para_a_128_mantem_os_itens_e_o_indice_separa_assuntos(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == ANTERIOR
        _item(db, "li-antigo")
        antes = db.query(f"SELECT {COLUNAS} FROM learning_items ORDER BY id")
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert _tem_indice(db, "ux_learning_items_vivo")
        assert db.query(f"SELECT {COLUNAS} FROM learning_items ORDER BY id") == antes
        assert db.scalar("SELECT scope_subject FROM learning_items WHERE id='li-antigo'") == ""
        with pytest.raises(Exception):                                   # mesmo escopo, mesmo conteúdo: único
            with db.tx():
                _item(db, "li-duplicado", subject="")
        _item(db, "li-outro-assunto", subject="festival de inverno")     # outro assunto: outro item
        assert db.scalar("SELECT COUNT(*) FROM learning_items") == 2
        assert db.migrate() == []                                        # idempotente
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_a_mesma_coluna_e_indice(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        for db in (novo, atualizado):
            assert _tem_indice(db, "ux_learning_items_vivo") and db.divergencias() == []
            assert "scope_subject" in db.columns("learning_items")
    finally:
        novo.close()
        atualizado.close()
