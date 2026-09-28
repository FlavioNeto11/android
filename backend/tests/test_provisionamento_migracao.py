"""Migração 050 (provisionamento pela plataforma): `worker_limits.max_devices`, `instances.android_overrides` e
`instances.retired_at`.

O que se prova aqui, nos dois bancos (o PostgreSQL quando `TEST_DATABASE_URL` existe):
- ATUALIZAÇÃO de um banco na última migração anterior à 050, com dados: aplica só a 050, não toca o que havia e as
  colunas novas nascem NULAS nas linhas antigas (aparelho do YAML não ganha sobreposição nem aposentadoria);
- idempotência: o segundo `migrate()` não aplica nada;
- o esquema das tabelas tocadas é o MESMO num banco criado do zero e num atualizado (a lição da 008);
- a 050 renderiza sem marca de dialeto sobrando e é só `ALTER TABLE`.

"Última anterior" é calculada, não escrita: outras ondas numeram 047–049 em paralelo, e o teste tem de continuar
verdadeiro quando elas chegarem à árvore (K-029: os INSERTs de semente usam só os tipos declarados na migração).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import Database

from .test_db import _banco, _copia_das_migracoes, _Falso

NOVA = "050_provisionamento"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
TS = "2026-09-27T12:00:00Z"


def _anterior() -> str:
    """A última migração antes da 050 que existe NESTA árvore (046 hoje; 049 quando as outras ondas chegarem)."""
    return max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)


def _assinatura(db: Database, tabela: str) -> str:
    linhas = db.query(f"SELECT * FROM {tabela} ORDER BY 1")          # noqa: S608 - nome fixo do teste
    return hashlib.sha256(json.dumps(linhas, sort_keys=True, default=str).encode()).hexdigest()


def _semear(db: Database) -> None:
    """Um retrato de produção antes da 050: aparelho do YAML, aparelho adotado de um worker e um limite decidido."""
    db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
               " hosted_by) VALUES (?,?,?,?,?,?,?,?)", ("android-01", 1, "android-01", 5554, 8200, 9200, 9515, "central"))
    db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
               " worker_id, external_serial, tunnel_port, remote_adb_port, origin, hosted_by)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("android-16", 16, "worker-01", 5584, 8215, 9215, 9530, "worker-lan-01", "127.0.0.1:15555", 15555,
                5555, "dynamic", "central"))
    db.execute("INSERT INTO worker_limits(worker_id, max_slots, boot_parallelism, max_working, min_free_ram_mb,"
               " updated_at, updated_by) VALUES (?,?,?,?,?,?,?)", ("worker-lan-01", 6, 1, 3, 4096, TS, "painel"))


def _esquema(db: Database) -> dict[str, list[str]]:
    return {t: sorted(db.columns(t)) for t in ("instances", "worker_limits")}


def test_atualizacao_para_050_nao_toca_o_que_havia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anterior = _anterior()
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=anterior)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == anterior
        assert not {"max_devices"} & db.columns("worker_limits")
        assert not {"android_overrides", "retired_at"} & db.columns("instances")
        _semear(db)
        antes = {t: _assinatura(db, t) for t in ("instances", "worker_limits")}
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert "max_devices" in db.columns("worker_limits")
        assert {"android_overrides", "retired_at"} <= db.columns("instances")
        # As colunas novas nascem NULAS: aparelho antigo não ganha sobreposição nem aposentadoria inventadas, e o
        # limite decidido continua sem teto de aparelhos (NULL = sem teto).
        for linha in db.query("SELECT id, android_overrides, retired_at FROM instances"):
            assert linha["android_overrides"] is None and linha["retired_at"] is None, linha["id"]
        assert db.scalar("SELECT max_devices FROM worker_limits WHERE worker_id='worker-lan-01'") is None
        # O que havia atravessa sem mudar (a assinatura inclui as colunas novas, todas nulas).
        depois = {t: _assinatura(db, t) for t in ("instances", "worker_limits")}
        assert antes != depois                                       # as colunas novas entraram na assinatura…
        assert db.query("SELECT id, idx, console_port, origin, hosted_by FROM instances ORDER BY idx") == [
            {"id": "android-01", "idx": 1, "console_port": 5554, "origin": None, "hosted_by": "central"},
            {"id": "android-16", "idx": 16, "console_port": 5584, "origin": "dynamic", "hosted_by": "central"}]
        assert db.one("SELECT max_slots, max_working FROM worker_limits WHERE worker_id='worker-lan-01'") == {
            "max_slots": 6, "max_working": 3}                        # …e só elas
        assert db.migrate() == []                                    # nada se repete
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_o_mesmo_esquema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anterior = _anterior()
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=anterior)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    _semear(atualizado)
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        assert _esquema(novo) == _esquema(atualizado)
        assert novo.divergencias() == [] and atualizado.divergencias() == []
        # As colunas novas aceitam o que a plataforma grava: JSON em texto, ISO em texto, inteiro no teto.
        novo.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port,"
                     " chromedriver_port, origin, hosted_by, android_overrides, retired_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("android-17", 17, "android-17", 5586, 8216, 9216, 9531, "dynamic", "central",
                      json.dumps({"system_image": "system-images;android-34;google_apis;x86_64", "ram_mb": 2048}), TS))
        novo.execute("INSERT INTO worker_limits(worker_id, max_devices, updated_at) VALUES (?,?,?)",
                     ("central", 12, TS))
        assert json.loads(novo.scalar("SELECT android_overrides FROM instances WHERE id='android-17'"))["ram_mb"] == 2048
        assert novo.scalar("SELECT max_devices FROM worker_limits WHERE worker_id='central'") == 12
    finally:
        novo.close()
        atualizado.close()


@pytest.mark.parametrize("dialeto", ["sqlite", "postgres"])
def test_a_050_renderiza_sem_marca_sobrando_e_so_altera_tabelas(dialeto: str) -> None:
    texto = _Falso(dialeto).render((ORIGEM / f"{NOVA}.sql").read_text("utf-8"))
    assert "{{" not in texto and "@dialect" not in texto
    instrucoes = Database._instrucoes(texto)
    assert len(instrucoes) == 3
    assert all(i.startswith("ALTER TABLE") and "ADD COLUMN" in i for i in instrucoes)
