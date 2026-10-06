"""Migração 126 (31.155, ADR-080): o índice único `ux_binding_conta_do_app_no_aparelho` (051) sai; a regra D2-a fica
no repositório e relaxa só para o app que declara a troca de conta.

- atualização a partir da migração anterior: só a 126 é aplicada, o índice sai, os outros dois da 051 ficam, nenhuma
  linha de vínculo muda; o segundo `migrate()` não aplica nada;
- banco novo = banco atualizado;
- sem o índice, a recusa do mesmo app no mesmo aparelho segue pelo repositório (`BindingConflict`) para o app que não
  declara a troca.

Nível de prova: `simulated` (SQLite; o PostgreSQL quando `TEST_DATABASE_URL` existe, pela fábrica de `test_db`).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import db as db_mod
from app.social.repository import BindingConflict, SocialRepository

from .test_db import _banco, _copia_das_migracoes, _tem_indice

NOVA = "126_troca_de_conta"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
ANTERIOR = max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)
TS = "2026-10-06T12:00:00.000Z"
FICAM = ("ux_binding_par_ativo", "ux_binding_principal")


def _semear(db: db_mod.Database) -> None:
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',1)")
    for pid, iid in (("p-a", "android-01"), ("p-b", "android-02")):
        db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
                   (pid, pid, TS, TS))
        db.execute("INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, app_id, is_primary)"
                   " VALUES (?,?,1,?,?,1)", (pid, iid, TS, "instagram"))


def test_atualizacao_para_a_126_tira_so_o_indice_e_nao_muda_linha(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == ANTERIOR
        assert _tem_indice(db, "ux_binding_conta_do_app_no_aparelho")
        _semear(db)
        antes = db.query("SELECT * FROM device_profile_bindings ORDER BY id")
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert not _tem_indice(db, "ux_binding_conta_do_app_no_aparelho")
        assert [_tem_indice(db, n) for n in FICAM] == [True, True]
        assert db.query("SELECT * FROM device_profile_bindings ORDER BY id") == antes
        assert db.migrate() == []                                    # idempotente
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_os_mesmos_indices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=ANTERIOR)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        indices = ("ux_binding_conta_do_app_no_aparelho", *FICAM)
        assert [_tem_indice(novo, n) for n in indices] == [_tem_indice(atualizado, n) for n in indices] \
            == [False, True, True]
        assert novo.divergencias() == [] and atualizado.divergencias() == []
    finally:
        novo.close()
        atualizado.close()


def test_sem_o_indice_o_repositorio_segue_recusando_o_app_que_nao_declara_a_troca(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        _semear(db)
        repo = SocialRepository(db)
        with pytest.raises(BindingConflict):
            repo.bind("p-b", "android-01", app_id="instagram")
        assert db.scalar("SELECT COUNT(*) FROM device_profile_bindings WHERE instance_id='android-01' AND active=1") == 1
    finally:
        db.close()
