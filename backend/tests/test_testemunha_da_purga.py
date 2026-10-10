"""31.288: a testemunha da purga acha o evento sem execução mais antigo SEM consulta que varre as linhas sem execução.

O `SELECT MIN(ts) ... WHERE run_id IS NULL` antigo, no SQLite, escolhia o índice de `run_id` e lia todas as linhas sem
execução (~30 mil no central) com o lock do banco na mão: 41 s com o disco frio (09/10 16:51Z). Aqui o contrato é o
valor (o mesmo de antes) e o caminho (blocos na ordem de `ts`, o lock solto entre eles).

Prova `simulated`: SQLite do harness; o disco frio do central é `not_run`.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.infrastructure import relatorio_sql
from app.modules.learning.infrastructure.relatorio_sql import FontesDeFalhaSql
from app.util import parse_iso
from tests.test_learning_backlog import banco_migrado


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "testemunha.sqlite3")
    yield d
    d.close()


def _evento(db: Database, ts: str, run_id: str | None) -> None:
    db.execute("INSERT INTO events(ts, kind, level, message, run_id) VALUES (?,?,?,?,?)",
               (ts, "instance.updated", "info", "x", run_id))


def _fontes(db: Database) -> FontesDeFalhaSql:
    return FontesDeFalhaSql(db, precos=lambda: {})


def test_sem_evento_nenhum_nao_ha_testemunha(db: Database) -> None:
    assert _fontes(db).testemunha_da_purga() is None


def test_so_eventos_de_execucao_nao_ha_testemunha(db: Database) -> None:
    for i in range(5):
        _evento(db, f"2026-10-01T00:00:0{i}.000Z", f"r-{i}")
    assert _fontes(db).testemunha_da_purga() is None


def test_acha_o_mais_antigo_sem_execucao_e_ignora_os_de_execucao_mais_velhos(db: Database) -> None:
    _evento(db, "2026-10-01T00:00:00.000Z", "r-velho")
    _evento(db, "2026-10-03T00:00:00.000Z", None)
    _evento(db, "2026-10-02T00:00:00.000Z", "r-meio")
    _evento(db, "2026-10-04T00:00:00.000Z", None)
    assert _fontes(db).testemunha_da_purga() == parse_iso("2026-10-03T00:00:00.000Z")


def test_acha_depois_de_varios_blocos_de_eventos_de_execucao(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relatorio_sql, "BLOCO_DA_TESTEMUNHA", 4)
    for i in range(11):
        _evento(db, f"2026-10-01T00:00:{i:02d}.000Z", f"r-{i}")
    _evento(db, "2026-10-01T00:01:00.000Z", None)
    _evento(db, "2026-10-01T00:02:00.000Z", None)
    assert _fontes(db).testemunha_da_purga() == parse_iso("2026-10-01T00:01:00.000Z")


def test_ts_repetido_na_fronteira_do_bloco_nao_perde_o_evento(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quatro eventos com o MESMO `ts` e blocos de 2: o sem execução está no segundo bloco, no meio do empate."""
    monkeypatch.setattr(relatorio_sql, "BLOCO_DA_TESTEMUNHA", 2)
    for run in ("r-1", "r-2", "r-3"):
        _evento(db, "2026-10-01T00:00:00.000Z", run)
    _evento(db, "2026-10-01T00:00:00.000Z", None)
    _evento(db, "2026-10-02T00:00:00.000Z", None)
    assert _fontes(db).testemunha_da_purga() == parse_iso("2026-10-01T00:00:00.000Z")


def test_bloco_exato_sem_testemunha_termina(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """O número de eventos é múltiplo do bloco e nenhum é sem execução: o último bloco cheio é seguido de um vazio."""
    monkeypatch.setattr(relatorio_sql, "BLOCO_DA_TESTEMUNHA", 3)
    for i in range(6):
        _evento(db, f"2026-10-01T00:00:0{i}.000Z", f"r-{i}")
    assert _fontes(db).testemunha_da_purga() is None


def test_cada_consulta_le_no_maximo_um_bloco(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """O lock do banco é pedido por consulta de um bloco, nunca por uma varredura da tabela inteira."""
    monkeypatch.setattr(relatorio_sql, "BLOCO_DA_TESTEMUNHA", 5)
    for i in range(23):
        _evento(db, f"2026-10-01T00:{i:02d}:00.000Z", f"r-{i}")
    _evento(db, "2026-10-02T00:00:00.000Z", None)
    tamanhos: list[int] = []
    original = db.query

    def espiona(sql: str, params: tuple | dict = ()) -> list:
        linhas = original(sql, params)
        if "FROM events" in sql:
            tamanhos.append(len(linhas))
        return linhas

    monkeypatch.setattr(db, "query", espiona)
    assert _fontes(db).testemunha_da_purga() == parse_iso("2026-10-02T00:00:00.000Z")
    assert max(tamanhos) <= 5 and len(tamanhos) == 5
