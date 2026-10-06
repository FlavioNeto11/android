"""Migração 069 — `learning_reviews`, a trilha auditável do curador por IA (item 30.9).

Prova no SQLite sempre e no PostgreSQL quando `TEST_DATABASE_URL` existe (abre pela fábrica `_banco`, nunca
`Database(path)` direto): a migração aplica num banco limpo, a tabela e os índices existem, o índice único barra o
mesmo (item, dossiê), a tabela é independente (sem FK) e a impressão da migração fica em `schema_migrations`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS

from .test_db import _banco, _tem_indice

NOME = "069_revisoes_do_aprendizado"
ARQUIVO = Path(db_mod.__file__).resolve().parents[1] / "migrations" / f"{NOME}.sql"
TS = "2026-10-02T12:00:00Z"
COLUNAS = {"id", "created_at", "item_ref", "item_kind", "scope_app", "gatilho", "dossie_hash", "dossie",
           "template_id", "template_versao", "provedor", "modelo", "simulated", "input_tokens", "output_tokens",
           "usd", "ms", "saida", "validade", "classe_de_risco", "politica", "decisao_final", "decidido_por",
           "transicao_id", "override", "override_motivo", "resultado_posterior", "resultado_em",
           "ai_call_id",                                     # a 075 acrescenta a chamada medida do hub
           "instrucao_versao"}                               # a 117 (30.76): a versão do texto que a IA leu


def _insere(db, id_: str, item_ref: str = "li-aaa", dossie_hash: str = "h1", app: str = "com.x") -> None:
    # K-029: cada valor no tipo da coluna (inteiro em INTEGER, número em REAL).
    db.execute(
        "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, scope_app, gatilho, dossie_hash,"
        " template_id, template_versao, simulated, input_tokens, output_tokens, usd, ms, validade)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (id_, TS, item_ref, "licao", app, "a_revisar", dossie_hash, "curador", "1", 1, 100, 20, 0.0042, 350, "ok"))


def _n(db) -> int:
    return int(db.one("SELECT COUNT(*) AS n FROM learning_reviews")["n"])


def test_migracao_aplica_e_cria_tabela_e_indices(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    db.migrate()
    assert COLUNAS == set(db.columns("learning_reviews"))
    for indice in ("ux_learning_reviews_item_dossie", "ix_learning_reviews_criada", "ix_learning_reviews_app"):
        assert _tem_indice(db, indice), indice


def test_indice_unico_barra_o_mesmo_item_com_o_mesmo_dossie(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    db.migrate()
    _insere(db, "lr-1")
    with pytest.raises(INTEGRITY_ERRORS):
        _insere(db, "lr-2")  # mesmo (item_ref, dossie_hash)
    assert _n(db) == 1
    _insere(db, "lr-3", dossie_hash="h2")  # dossiê novo passa
    _insere(db, "lr-4", item_ref="li-bbb")  # item novo passa
    assert _n(db) == 3


def test_defaults_e_nulos_da_revisao_sem_decisao(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    db.migrate()
    _insere(db, "lr-1")
    r = db.one("SELECT * FROM learning_reviews WHERE id=?", ("lr-1",))
    assert r["dossie"] == "{}" and r["override"] == 0 and r["provedor"] == "" and r["modelo"] == ""
    assert r["saida"] is None and r["decisao_final"] is None and r["transicao_id"] is None
    assert r["resultado_posterior"] is None and r["simulated"] == 1 and abs(r["usd"] - 0.0042) < 1e-9


def test_sem_fk_o_item_pode_ser_de_qualquer_lugar(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    db.migrate()
    for i, ref in enumerate(("receita:r-1", "fluxo:f-1", "fk-grupo")):
        _insere(db, f"lr-{i}", item_ref=ref)  # nenhuma dessas linhas existe em outra tabela
    assert _n(db) == 3


def test_impressao_da_migracao_fica_em_schema_migrations(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    db.migrate()
    linha = db.one("SELECT * FROM schema_migrations WHERE version=?", (NOME,))
    assert linha is not None
    assert linha["checksum"] == db._impressao(db.render(ARQUIVO.read_text(encoding="utf-8")))  # o script renderizado
