"""30.44: o ritmo por hora do despachante conta pela hora em que a execução nasceu, não pela do fechamento.

Medido no P4 de 03/10: o limite de 4 por hora dava 4 execuções a cada 70 min, porque o fechamento move `updated_at` e
a execução fechada 5 min depois de começar segurava a vaga além da hora cheia. Nível de prova: `simulated`.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.validacao import FOLGA_DO_RITMO_S
from app.modules.learning.infrastructure.validacoes_sql import RegistroDeValidacoesSql
from app.util import to_iso

from .fake_skills import banco as banco_migrado

AGORA = datetime(2026, 10, 3, 23, 7, 21, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "ritmo.sqlite3")
    yield d
    d.close()


def _validacao(db: Database, n: int, *, nasceu: datetime, fechou: datetime, estado: str = "feita") -> None:
    run_id = f"r-{n}"
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (run_id, f"k-{n}", "x", "execute", "completed", 0, "[]", to_iso(nasceu)))
    db.execute(
        "INSERT INTO learning_validations(id, created_at, updated_at, review_id, item_ref, item_kind, scope_app, grupo,"
        " falta, run_origem, comando, aparelho_excluido, estado, run_id, aparelho, usd, expira_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"lv-{n}", to_iso(nasceu), to_iso(fechou), f"rv-{n}", f"receita:{n}", "receita", "p", "qa", "[]", "r-o", "x",
         None, estado, run_id, "android-10", 0.07, to_iso(AGORA + timedelta(days=1))))


def test_a_execucao_de_ha_uma_hora_fechada_depois_nao_segura_a_vaga(db: Database) -> None:
    """A das 22:07:21 fechou às 22:12: na volta das 23:07:21 ela já saiu da janela (antes contava até 23:12)."""
    _validacao(db, 1, nasceu=AGORA - timedelta(hours=1), fechou=AGORA - timedelta(minutes=55))
    _validacao(db, 2, nasceu=AGORA - timedelta(minutes=50), fechou=AGORA - timedelta(minutes=45))
    registro = RegistroDeValidacoesSql(db)
    janela = AGORA - timedelta(hours=1) + timedelta(seconds=FOLGA_DO_RITMO_S)
    assert registro.comecados_desde(janela) == 1


def test_a_execucao_que_nasceu_na_janela_conta_mesmo_rodando(db: Database) -> None:
    _validacao(db, 1, nasceu=AGORA - timedelta(minutes=5), fechou=AGORA - timedelta(minutes=5), estado="rodando")
    _validacao(db, 2, nasceu=AGORA - timedelta(minutes=20), fechou=AGORA - timedelta(minutes=2), estado="recusada")
    assert RegistroDeValidacoesSql(db).comecados_desde(AGORA - timedelta(hours=1)) == 2
