"""29.63: o esquema-modelo do harness de PostgreSQL — um esquema migrado UMA vez por processo (worker do xdist) e
esvaziado entre os testes, em vez de criar, migrar e apagar um esquema por teste.

Medido em 04/10 (29.62): cada teste que abre banco em PG custava ~3,2 s (criar o esquema 0,02 s, 82 migrações 2,25 s,
DROP 0,9 s), e é esse o grosso dos 7 min 58 s de `-n 8` nos 1016 testes do aprendizado. Em SQLite nada muda: sem
`TEST_DATABASE_URL` ninguém chega aqui.

Quem usa: só os ajudantes compartilhados (`conftest.make_config` e `fake_skills.banco`), e só na PRIMEIRA abertura de
banco de cada teste. A segunda abertura no mesmo teste e toda chamada direta de `_dsn_de_teste()` seguem com esquema
novo — é o caso dos testes da própria migração, que apontam `MIGRATIONS_DIR` para uma cópia e esperam aplicar num
esquema vazio. Com `MIGRATIONS_DIR` trocado, também não há reuso.

O esvaziamento devolve o estado de logo depois das migrações: `TRUNCATE` de todas as tabelas (menos
`schema_migrations`), as linhas que as migrações semeiam de volta (copiadas para um esquema-gêmeo `<nome>m`) e as
sequências no valor de então. Antes disso:
- as conexões que sobraram do teste anterior neste esquema (marcadas por `application_name`) são encerradas — uma
  execução que vazou não escreve no banco do teste seguinte;
- a impressão da ESTRUTURA (colunas, índices, restrições, gatilhos, funções, visões e `schema_migrations`) tem de ser
  a de logo depois da migração; um teste que mexeu em DDL faz o esquema ser trocado por um novo.
Qualquer falha no esvaziamento (trava, impressão, exceção) troca o esquema por um novo, migrado do zero: no pior caso
o custo é o de antes, nunca um teste vermelho por causa do harness. As contagens (reusos, trocas e o motivo de cada
uma) vão para `ESQUEMA_MODELO_RELATORIO/<worker>.json` quando a variável aponta para uma pasta.
"""
from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

#: Impressão só da estrutura: nada de estatística (`reltuples`) nem valor de sequência, ou todo teste trocaria o esquema.
_IMPRESSAO = """
SELECT md5(coalesce(string_agg(x, '|' ORDER BY x), '')) AS h FROM (
  SELECT 'c:' || table_name || '.' || column_name || ':' || data_type || ':' || coalesce(column_default, '') || ':'
         || is_nullable AS x
    FROM information_schema.columns WHERE table_schema = %(s)s
  UNION ALL SELECT 'i:' || indexname || ':' || indexdef FROM pg_indexes WHERE schemaname = %(s)s
  UNION ALL SELECT 'k:' || conname || ':' || pg_get_constraintdef(k.oid)
    FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace WHERE n.nspname = %(s)s
  UNION ALL SELECT 't:' || c.relname || ':' || t.tgname
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = %(s)s AND NOT t.tgisinternal
  UNION ALL SELECT 'f:' || p.proname || ':' || md5(p.prosrc)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = %(s)s
  UNION ALL SELECT 'v:' || viewname || ':' || md5(definition) FROM pg_views WHERE schemaname = %(s)s
) q
"""


class EsquemaDoWorker:
    """Um por processo. `dsn()` devolve o esquema pronto (migrado e vazio) ou `None` se não deu — e aí quem chamou segue
    pelo caminho de sempre (esquema novo)."""

    def __init__(self) -> None:
        self.nome: str | None = None
        self._impressao = ""
        self._migracoes: list[tuple[str, str]] = []
        self._tabelas: list[str] = []
        self._semeadas: dict[str, list[str]] = {}          # tabela → colunas copiáveis (sem as GENERATED ALWAYS)
        self._sequencias: dict[str, int | None] = {}
        self.criados: list[str] = []                        # todos os esquemas (e gêmeos) desta sessão, para a faxina
        self.contagem: Counter[str] = Counter()

    # ------------------------------------------------------------------------------------------------- uso
    def dsn(self, base: str, abandonar: list[str]) -> str | None:
        """O DSN do esquema do worker, esvaziado. `abandonar` recebe o esquema trocado (a faxina do teste o apaga)."""
        try:
            if self.nome is not None:
                motivo = self._esvaziar(base)
                if motivo is None:
                    self.contagem["reuso"] += 1
                    return self._dsn(base, self.nome)
                self.contagem[f"troca:{motivo}"] += 1
                abandonar.extend((self.nome, self.nome + "m"))
                self.nome = None
            self._criar(base)
            self.contagem["migrado"] += 1
            return self._dsn(base, self.nome)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — o harness nunca reprova um teste: cai no esquema novo de sempre
            self.contagem[f"falha:{type(exc).__name__}"] += 1
            if self.nome is not None:
                abandonar.extend((self.nome, self.nome + "m"))
            self.nome = None
            return None

    @staticmethod
    def _dsn(base: str, nome: str) -> str:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}options=-csearch_path%3D{nome}&application_name={nome}"

    # ------------------------------------------------------------------------------------------- criação
    def _criar(self, base: str) -> None:
        import psycopg
        from psycopg import sql

        from app.db import Database

        nome = f"w{uuid.uuid4().hex[:12]}"
        gemeo = nome + "m"
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(nome)))
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(gemeo)))
        self.criados.extend((nome, gemeo))
        db = Database(self._dsn(base, nome))
        try:
            db.migrate()
        finally:
            db.close()
        with psycopg.connect(base, autocommit=True) as c:
            tabelas = [r[0] for r in c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (nome,))]
            semeadas: dict[str, list[str]] = {}
            for t in tabelas:
                if t == "schema_migrations":
                    continue
                if c.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{})").format(
                        sql.Identifier(nome), sql.Identifier(t))).fetchone()[0]:      # type: ignore[index]
                    semeadas[t] = [r[0] for r in c.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s"
                        " AND is_generated <> 'ALWAYS' ORDER BY ordinal_position", (nome, t))]
                    c.execute(sql.SQL("CREATE TABLE {}.{} AS SELECT * FROM {}.{}").format(
                        sql.Identifier(gemeo), sql.Identifier(t), sql.Identifier(nome), sql.Identifier(t)))
            self._sequencias = {r[0]: r[1] for r in c.execute(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = %s ORDER BY sequencename",
                (nome,))}
            self._impressao = self._ler_impressao(c, nome)
            self._migracoes = self._ler_migracoes(c, nome)
        self._tabelas = [t for t in tabelas if t != "schema_migrations"]
        self._semeadas = semeadas
        self.nome = nome

    @staticmethod
    def _ler_impressao(c: Any, nome: str) -> str:
        return str(c.execute(_IMPRESSAO, {"s": nome}).fetchone()[0])

    @staticmethod
    def _ler_migracoes(c: Any, nome: str) -> list[tuple[str, str]]:
        from psycopg import sql

        return [(r[0], r[1] or "") for r in c.execute(sql.SQL(
            "SELECT version, checksum FROM {}.schema_migrations ORDER BY version").format(sql.Identifier(nome)))]

    # ------------------------------------------------------------------------------------------ esvaziar
    def _esvaziar(self, base: str) -> str | None:
        """Devolve o motivo da troca, ou `None` quando o esquema voltou ao estado de logo depois da migração."""
        import psycopg
        from psycopg import sql

        nome = self.nome
        assert nome is not None
        gemeo = sql.Identifier(nome + "m")
        esquema = sql.Identifier(nome)
        with psycopg.connect(base, autocommit=True, connect_timeout=5) as c:
            # As conexões que o teste anterior deixou abertas neste esquema (execução que vazou) não escrevem no
            # banco do próximo. Conta à parte: é sinal de vazamento a investigar, não erro do harness.
            mortas = c.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity"
                               " WHERE application_name = %s AND pid <> pg_backend_pid()", (nome,)).fetchone()[0]
            if mortas:
                self.contagem["conexoes_encerradas"] += int(mortas)
            if self._ler_impressao(c, nome) != self._impressao:
                return "estrutura"
            if self._ler_migracoes(c, nome) != self._migracoes:
                return "schema_migrations"
            c.execute("SET lock_timeout = '2s'")
            try:
                with c.transaction():
                    if self._tabelas:
                        c.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(sql.SQL(", ").join(
                            sql.SQL("{}.{}").format(esquema, sql.Identifier(t)) for t in self._tabelas)))
                    # As linhas semeadas voltam sem disparar gatilho nem conferir chave estrangeira (são as mesmas
                    # que a migração gravou, já coerentes); `OVERRIDING SYSTEM VALUE` para as colunas de identidade.
                    c.execute("SET LOCAL session_replication_role = replica")
                    for t, colunas in self._semeadas.items():
                        lista = sql.SQL(", ").join(sql.Identifier(x) for x in colunas)
                        c.execute(sql.SQL("INSERT INTO {}.{} ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM {}.{}").format(
                            esquema, sql.Identifier(t), lista, lista, gemeo, sql.Identifier(t)))
                    for s, valor in self._sequencias.items():
                        if valor is None:
                            c.execute(sql.SQL("ALTER SEQUENCE {}.{} RESTART").format(esquema, sql.Identifier(s)))
                        else:
                            c.execute("SELECT setval(%s::regclass, %s, true)", (f'"{nome}"."{s}"', valor))
            except psycopg.errors.LockNotAvailable:
                return "trava"
        return None

    # ------------------------------------------------------------------------------------------- relatório
    def relatar(self) -> None:
        pasta = os.environ.get("ESQUEMA_MODELO_RELATORIO")
        if not pasta or not self.contagem:
            return
        quem = os.environ.get("PYTEST_XDIST_WORKER", "unico")
        try:
            Path(pasta).mkdir(parents=True, exist_ok=True)
            (Path(pasta) / f"{quem}.json").write_text(json.dumps(dict(self.contagem), sort_keys=True), encoding="utf-8")
        except OSError:
            pass                                            # relatório é conveniência de medida; nunca reprova


ESQUEMA_DO_WORKER = EsquemaDoWorker()
