"""29.63: o esquema do worker volta ao estado de logo depois da migração entre um teste e outro.

Só roda com `TEST_DATABASE_URL` (em SQLite o esquema do worker não existe). Cada teste usa uma instância PRÓPRIA de
`EsquemaDoWorker` (não a do processo) e apaga os esquemas dela no fim. Nível de prova: `simulated` (banco de teste).
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.db import Database

from .esquema_do_worker import EsquemaDoWorker

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="só no PostgreSQL")


@pytest.fixture
def worker() -> Iterator[tuple[EsquemaDoWorker, list[str], str]]:
    import psycopg

    base = os.environ["TEST_DATABASE_URL"]
    w, abandonados = EsquemaDoWorker(), []
    yield w, abandonados, base
    with psycopg.connect(base, autocommit=True) as c:
        for s in {*w.criados, *abandonados}:
            c.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')


def _abre(dsn: str | None) -> Database:
    assert dsn is not None
    return Database(dsn)


def test_o_segundo_uso_recebe_o_mesmo_esquema_vazio_com_as_linhas_semeadas(worker) -> None:  # noqa: ANN001
    w, abandonados, base = worker
    db = _abre(w.dsn(base, abandonados))
    semeadas = {t: db.scalar(f'SELECT count(*) FROM "{t}"') for t in w._semeadas}            # noqa: SLF001
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('r-1','k-1','x','execute','completed',0,'[]','2026-10-04T00:00:00Z')")
    for t in w._semeadas:                                                                       # noqa: SLF001
        db.execute(f'DELETE FROM "{t}"')                                                        # o teste mexeu na semente
    db.close()
    nome = w.nome
    db = _abre(w.dsn(base, abandonados))
    try:
        assert w.nome == nome and abandonados == []                    # reuso, não troca
        assert db.scalar("SELECT count(*) FROM runs") == 0
        assert {t: db.scalar(f'SELECT count(*) FROM "{t}"') for t in w._semeadas} == semeadas    # noqa: SLF001
        assert db.migrate() == []                                      # já migrado: nada a aplicar
    finally:
        db.close()
    assert w.contagem["migrado"] == 1 and w.contagem["reuso"] == 1


def test_o_teste_que_mexeu_na_estrutura_troca_o_esquema(worker) -> None:  # noqa: ANN001
    w, abandonados, base = worker
    db = _abre(w.dsn(base, abandonados))
    db.execute("CREATE TABLE sobra_do_teste (a INT)")
    db.close()
    antigo = w.nome
    db = _abre(w.dsn(base, abandonados))
    try:
        assert w.nome != antigo and antigo in abandonados
        assert w.contagem["troca:estrutura"] == 1
        assert "sobra_do_teste" not in db.tables()
    finally:
        db.close()


def test_a_conexao_que_sobrou_do_teste_anterior_e_encerrada(worker) -> None:  # noqa: ANN001
    w, abandonados, base = worker
    vazada = _abre(w.dsn(base, abandonados))                           # o teste anterior "esqueceu" de fechar
    db = _abre(w.dsn(base, abandonados))
    try:
        assert w.contagem["conexoes_encerradas"] >= 1 and w.contagem["reuso"] == 1
    finally:
        db.close()
        try:
            vazada.close()
        except Exception:  # noqa: BLE001 — a conexão foi encerrada do lado do servidor
            pass
