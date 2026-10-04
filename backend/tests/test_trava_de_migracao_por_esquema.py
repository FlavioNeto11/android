"""29.62: a trava de migração do PostgreSQL é POR ESQUEMA.

Na suíte em PostgreSQL cada teste migra o próprio esquema (`conftest._dsn_de_teste`), e a chave única de
`pg_advisory_lock` punha os workers do xdist em fila (1006 testes em 22 min com `-n 4`). Com a forma de duas chaves e o
esquema corrente na segunda: esquemas diferentes não se bloqueiam; o MESMO esquema (produção: um só) segue em fila.

Só roda com `TEST_DATABASE_URL` (no SQLite não há trava: `_trava_de_migracao` não faz nada). Nível de prova:
`simulated` (banco de teste, sem o ambiente central).
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from app.db import _ESQUEMA_DA_TRAVA, _LOCK_MIGRACAO, Database

from .conftest import _dsn_de_teste

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="só no PostgreSQL")

_TENTA = f"SELECT pg_try_advisory_lock(%s, {_ESQUEMA_DA_TRAVA})"
_SOLTA = f"SELECT pg_advisory_unlock(%s, {_ESQUEMA_DA_TRAVA})"


def _tenta(dsn: str) -> bool:
    """Outra conexão (outra sessão do PostgreSQL) tenta a trava de migração do esquema do `dsn`, sem esperar."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as c:
        linha = c.execute(_TENTA, (_LOCK_MIGRACAO,)).fetchone()
        pegou = bool(linha and linha[0])
        if pegou:
            c.execute(_SOLTA, (_LOCK_MIGRACAO,))
        return pegou


def test_esquemas_diferentes_nao_se_bloqueiam_e_o_mesmo_esquema_bloqueia() -> None:
    dsn_a, dsn_b = _dsn_de_teste(), _dsn_de_teste()
    assert dsn_a and dsn_b
    db = Database(dsn_a)
    try:
        with db._trava_de_migracao():                                   # noqa: SLF001
            assert _tenta(dsn_b) is True                                # outro esquema: livre
            assert _tenta(dsn_a) is False                               # o mesmo esquema: em fila
        assert _tenta(dsn_a) is True                                    # soltou ao sair
    finally:
        db.close()


def test_duas_migracoes_em_esquemas_diferentes_correm_juntas() -> None:
    """O efeito que importa à suíte: com o esquema A preso no meio da migração, o esquema B migra até o fim."""
    dsn_a, dsn_b = _dsn_de_teste(), _dsn_de_teste()
    assert dsn_a and dsn_b
    preso = Database(dsn_a)
    terminou = threading.Event()

    def migrar_b() -> None:
        b = Database(dsn_b)
        try:
            b.migrate()
        finally:
            b.close()
        terminou.set()

    try:
        with preso._trava_de_migracao():                                # noqa: SLF001
            t = threading.Thread(target=migrar_b, daemon=True)
            inicio = time.monotonic()
            t.start()
            assert terminou.wait(120), "a migração do esquema B esperou a trava do esquema A"
            assert time.monotonic() - inicio < 120
    finally:
        preso.close()


def test_o_unlock_solta_a_mesma_chave_com_o_search_path_trocado_no_meio() -> None:
    """A 2ª chave é lida uma vez: recalculada no unlock, o `search_path` trocado no meio soltaria outra chave e a do
    esquema A ficaria presa até a sessão fechar (revisão da Android no #184)."""
    dsn_a = _dsn_de_teste()
    assert dsn_a
    db = Database(dsn_a)
    try:
        with db._trava_de_migracao():                                   # noqa: SLF001
            db._conn.execute("SET search_path TO public")               # noqa: SLF001
        assert _tenta(dsn_a) is True                                    # a trava do esquema A foi solta
    finally:
        db.close()
