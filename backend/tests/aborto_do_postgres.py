"""A regra de transação do PostgreSQL que o SQLite não tem, embrulhada na conexão SQLite de verdade (22.5).

No PostgreSQL, uma instrução que falha DENTRO de uma transação a aborta: toda instrução seguinte é recusada
("current transaction is aborted, commands ignored until end of transaction block") até `ROLLBACK` ou
`ROLLBACK TO SAVEPOINT`, e o `COMMIT` da transação abortada vira `ROLLBACK` no servidor, sem erro. No SQLite a mesma
falha não contamina nada — por isso a suíte, que roda nele, não via a trilha engolida derrubar a escrita da loja.

O que se embrulha é a conexão (como `_ConexaoFragil` em `test_db.py`): o SQLite não deixa trocar o `execute` dela,
e provar com um PostgreSQL exigiria um de pé. Isto prova a REGRA do código (savepoint dentro do `try`), não o
servidor: a conferência no PostgreSQL real é a suíte com `TEST_DATABASE_URL`.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.db import Database

ABORTADA = "current transaction is aborted, commands ignored until end of transaction block"


class ConexaoQueAbortaComoOPostgres:
    """A conexão SQLite real, com o aborto do PostgreSQL e a lista de cada instrução enviada (`instrucoes`).

    `falhar_em`: a instrução que contém este trecho falha como falharia no banco (sem chegar ao SQLite)."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.instrucoes: list[str] = []
        self.abortada = False
        self.falhar_em: str | None = None
        #: Quantos `COMMIT` viraram `ROLLBACK` por encontrar a transação abortada (o que o PostgreSQL faz calado).
        self.commits_perdidos = 0

    def execute(self, sql: str, params: Any = ()) -> Any:
        texto = " ".join(sql.split())
        self.instrucoes.append(texto)
        cabeca = texto.upper()
        if self.abortada:
            if cabeca.startswith("ROLLBACK"):            # ROLLBACK e ROLLBACK TO SAVEPOINT tiram do estado abortado
                self.abortada = False
                return self._real.execute(sql, params)
            if cabeca == "COMMIT":
                self.abortada = False
                self.commits_perdidos += 1
                return self._real.execute("ROLLBACK")
            raise sqlite3.OperationalError(ABORTADA)
        try:
            if self.falhar_em is not None and self.falhar_em in texto:
                raise sqlite3.OperationalError(f"falha de teste em: {self.falhar_em}")
            return self._real.execute(sql, params)
        except Exception:
            if self._real.in_transaction:
                self.abortada = True
            raise

    def close(self) -> None:
        self._real.close()


def embrulhar(db: Database) -> ConexaoQueAbortaComoOPostgres:
    """Troca a conexão do banco pela que aborta como o PostgreSQL. Só faz sentido no SQLite (no PostgreSQL real o
    próprio servidor já aborta)."""
    if db.dialect != "sqlite":
        raise RuntimeError("o embrulho imita o PostgreSQL sobre o SQLite; no PostgreSQL, rode sem ele")
    conexao = ConexaoQueAbortaComoOPostgres(db._conn)
    db._conn = conexao
    return conexao


def savepoints(conexao: ConexaoQueAbortaComoOPostgres) -> list[str]:
    """As instruções de savepoint enviadas, na ordem (o SQL gerado que o teste confere)."""
    return [i for i in conexao.instrucoes if i.upper().startswith(("SAVEPOINT ", "RELEASE ", "ROLLBACK TO "))]
