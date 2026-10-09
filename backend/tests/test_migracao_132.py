"""Migracao 132 (ponte android <-> igfarm): `persona_reservas`, `caixas_email` e `contas_igfarm`.

- atualizacao a partir da migracao anterior: so a 132 e aplicada, nenhuma tabela existente muda, o segundo `migrate()` nao
  aplica nada; banco novo = banco atualizado;
- as unicidades (e-mail e @ sugeridos, endereco da caixa, chave de idempotencia) e a cascata pela pessoa valem.

Nivel de prova: `simulated` (SQLite; o PostgreSQL quando `TEST_DATABASE_URL` existe, pela fabrica de `test_db`).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import db as db_mod

from .test_db import _banco, _copia_das_migracoes

NOVA = "132_ponte_igfarm"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
TABELAS = ("persona_reservas", "caixas_email", "contas_igfarm")
AGORA = "2026-10-09T12:00:00.000Z"


def _pessoa_e_conta(db: db_mod.Database, pid: str = "ig-1") -> str:
    db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
               (pid, "", AGORA, AGORA))
    conta = f"acc-{pid}"
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at) VALUES (?,?,?,?,?,?)",
               (conta, pid, "app", "h", AGORA, AGORA))
    return conta


def test_atualizacao_para_a_132_cria_so_as_tabelas_novas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == ANTERIOR
        assert not set(TABELAS) & set(db.tables())
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert set(TABELAS) <= set(db.tables())
        assert db.migrate() == []
    finally:
        db.close()


def test_unicidades_e_cascata(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        conta = _pessoa_e_conta(db)
        db.execute("INSERT INTO persona_reservas(profile_id, email_sugerido, username_sugerido, criada_em)"
                   " VALUES ('ig-1','Ana@x.com','ana.a',?)", (AGORA,))
        db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES ('ig-2','',?,?)",
                   (AGORA, AGORA))
        for email, user in (("ana@x.com", "outro.a"), ("outro@x.com", "ANA.A")):
            with pytest.raises(db_mod.INTEGRITY_ERRORS):
                with db.tx():
                    db.execute("INSERT INTO persona_reservas(profile_id, email_sugerido, username_sugerido, criada_em)"
                               " VALUES ('ig-2',?,?,?)", (email, user, AGORA))
        db.execute("INSERT INTO caixas_email(account_id, profile_id, endereco, dominio, secret_ref, key_id, criada_em)"
                   " VALUES (?,?,?,?,?,?,?)", (conta, "ig-1", "a@x.com", "x.com", "ref", "k", AGORA))
        db.execute("INSERT INTO contas_igfarm(account_id, profile_id, igfarm_account_id, username_registrado,"
                   " criada_em_igfarm, registrada_em) VALUES (?,?,?,?,?,?)", (conta, "ig-1", "g1", "ana.a", AGORA, AGORA))
        with pytest.raises(db_mod.INTEGRITY_ERRORS):
            with db.tx():
                db.execute("INSERT INTO caixas_email(account_id, profile_id, endereco, dominio, secret_ref, key_id,"
                           " criada_em) VALUES ('acc-2','ig-2','A@X.com','x.com','r','k',?)", (AGORA,))
        db.execute("DELETE FROM instagram_profiles WHERE id='ig-1'")
        for tabela in TABELAS:
            assert db.scalar(f"SELECT COUNT(*) FROM {tabela}") == 0  # noqa: S608
    finally:
        db.close()
