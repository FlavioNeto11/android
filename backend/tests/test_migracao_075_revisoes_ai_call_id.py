"""Migração 075 — `learning_reviews.ai_call_id`, a chamada do hub que sustenta o custo medido da revisão.

Prova no SQLite sempre e no PostgreSQL quando `TEST_DATABASE_URL` existe (abre pela fábrica `_banco`, nunca
`Database(path)` direto): a coluna existe, é nulável, as linhas sem chamada ficam NULL e a impressão da migração fica em
`schema_migrations`.
"""
from __future__ import annotations

from pathlib import Path

from .test_db import _banco

NOME = "075_revisoes_ai_call_id"
TS = "2026-10-02T12:00:00Z"


def _insere(db, id_: str, ai_call_id: int | None) -> None:  # noqa: ANN001
    db.execute(
        "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, scope_app, gatilho, dossie_hash,"
        " template_id, template_versao, simulated, usd, validade, ai_call_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (id_, TS, f"li-{id_}", "licao", "com.x", "a_revisar", f"h-{id_}", "curador", "1", 0,
         0.0, "ok", ai_call_id))


def test_coluna_nulavel_e_impressao_registrada(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        assert "ai_call_id" in set(db.columns("learning_reviews"))
        _insere(db, "medida", 42)
        _insere(db, "sem", None)
        linhas = {str(r["id"]): r["ai_call_id"] for r in db.query("SELECT id, ai_call_id FROM learning_reviews")}
        assert linhas == {"medida": 42, "sem": None}
        assert db.one("SELECT 1 AS x FROM schema_migrations WHERE version=?", (NOME,)) is not None
    finally:
        db.close()
