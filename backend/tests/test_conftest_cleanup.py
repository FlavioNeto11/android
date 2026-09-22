"""Achado #163: a corrida em PostgreSQL nunca apagava os schemas de teste (1608 schemas / 1,9 GB medidos no
farm-pg-teste). `conftest.pytest_sessionfinish` apaga, no fim da sessão, os schemas que ELA criou.

Prova de ponta a ponta (com o container `farm-pg-teste` de verdade, `TEST_DATABASE_URL` setado):
    docker exec farm-pg-teste psql -U postgres -d farm -tAc \
        "select count(*) from pg_namespace where nspname ~ '^t[0-9a-f]{12}\\$'"   # -> 1608 antes
    TEST_DATABASE_URL=postgresql://postgres:teste@127.0.0.1:55433/farm \
        .venv/Scripts/python.exe -m pytest -q "tests/test_execution.py::test_cada_aparelho_recebe_apenas_a_sua_mensagem"
    # a mesma consulta -> 1608 depois: o schema criado por esta corrida nasceu e morreu na mesma sessão.

Aqui, sem depender de container nenhum, os testes provam a FUNÇÃO: dropa cada schema empilhado, um erro num
schema não impede os demais, e sem `TEST_DATABASE_URL`/sem schemas criados ela não faz nada (nem importa
`psycopg`, que pode nem estar instalado num ambiente sem PostgreSQL)."""
from __future__ import annotations

from typing import Any

from . import conftest as cft


class _CursorFalso:
    def __init__(self, chamadas: list[str], falha_em: set[str]):
        self.chamadas, self.falha_em = chamadas, falha_em

    def execute(self, sql: str) -> None:
        self.chamadas.append(sql)
        if any(f in sql for f in self.falha_em):
            raise RuntimeError("lock_timeout: outra sessão é dona deste schema")


class _ConexaoFalsa:
    def __init__(self, chamadas: list[str], falha_em: set[str]):
        self._cursor = _CursorFalso(chamadas, falha_em)

    def execute(self, sql: str) -> None:
        self._cursor.execute(sql)

    def __enter__(self) -> "_ConexaoFalsa":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def test_sessionfinish_apaga_cada_schema_que_esta_sessao_criou(monkeypatch: Any) -> None:
    chamadas: list[str] = []
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://postgres:teste@127.0.0.1:55433/farm")
    monkeypatch.setattr(cft, "_SCHEMAS_DE_TESTE", ["t111111111111", "t222222222222"])
    fake_psycopg = type("M", (), {"connect": staticmethod(lambda *a, **k: _ConexaoFalsa(chamadas, set()))})
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_psycopg)

    cft.pytest_sessionfinish(session=None, exitstatus=0)

    assert any('DROP SCHEMA IF EXISTS "t111111111111" CASCADE' in c for c in chamadas)
    assert any('DROP SCHEMA IF EXISTS "t222222222222" CASCADE' in c for c in chamadas)


def test_sessionfinish_no_esta_nao_bloqueia_suite_ainda_quando_um_schema_reage(monkeypatch: Any) -> None:
    """Um schema com lock de outra sessão não pode derrubar a limpeza dos demais nem a corrida."""
    chamadas: list[str] = []
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://postgres:teste@127.0.0.1:55433/farm")
    monkeypatch.setattr(cft, "_SCHEMAS_DE_TESTE", ["t_preso", "t_livre"])
    fake_psycopg = type("M", (), {"connect": staticmethod(lambda *a, **k: _ConexaoFalsa(chamadas, {"t_preso"}))})
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_psycopg)

    cft.pytest_sessionfinish(session=None, exitstatus=0)                     # não levanta

    assert any('"t_preso"' in c for c in chamadas) and any('"t_livre"' in c for c in chamadas)


def test_sessionfinish_sem_test_database_url_nao_faz_nada(monkeypatch: Any) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(cft, "_SCHEMAS_DE_TESTE", ["t111111111111"])
    cft.pytest_sessionfinish(session=None, exitstatus=0)                     # não importa psycopg, não faz nada


def test_sessionfinish_sem_schemas_criados_nao_faz_nada(monkeypatch: Any) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://postgres:teste@127.0.0.1:55433/farm")
    monkeypatch.setattr(cft, "_SCHEMAS_DE_TESTE", [])
    cft.pytest_sessionfinish(session=None, exitstatus=0)
