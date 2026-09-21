"""Acesso ao banco, neutro de dialeto: SQLite por padrão, PostgreSQL por configuração.

Por que neutro. Enquanto havia um processo só, SQLite era a escolha certa e continua sendo para quem roda tudo
numa máquina. Distribuir componentes exige um banco que várias máquinas alcancem — e a diferença entre os dois
não pode vazar para 20 arquivos de regra de negócio.

Três coisas o resto do código não precisa saber:

1. **A linha é um `dict`.** `sqlite3.Row` só era acessada por nome (conferido: índice numérico existia apenas
   aqui dentro), e `dict` tem `.keys()`, então a troca é transparente e vale nos dois dialetos.
2. **O marcador continua `?`.** Quem escreve SQL segue usando `?`; a tradução para `%s` do PostgreSQL acontece
   aqui. Uma convenção só, em vez de duas.
3. **As migrações são as mesmas.** O que difere entre os dialetos vira um punhado de marcas (`{{PK_AUTO}}`),
   trocadas na hora de aplicar — um arquivo por migração, não dois, para os dois não divergirem com o tempo.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import now_iso

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

#: Uma linha do banco. `dict` de propósito: é o que os dois drivers entregam e o que o resto do código já usava.
Row = dict[str, Any]

#: Diferenças de DDL entre os dialetos. Ficam aqui, e não espalhadas pelas migrações.
_DIALETO: dict[str, dict[str, str]] = {
    "sqlite": {"PK_AUTO": "INTEGER PRIMARY KEY AUTOINCREMENT", "BLOB": "BLOB", "NOW": "CURRENT_TIMESTAMP"},
    "postgres": {"PK_AUTO": "BIGSERIAL PRIMARY KEY", "BLOB": "BYTEA", "NOW": "CURRENT_TIMESTAMP"},
}
_MARCA = re.compile(r"\{\{(\w+)\}\}")
#: Instrução com `BEGIN` aberto e sem `END` correspondente — corpo de trigger, onde `;` não termina nada.
_CORPO_ABERTO = re.compile(r"\bBEGIN\b(?!.*\bEND\b)", re.IGNORECASE | re.DOTALL)
#: Blocos que só valem num dialeto: `-- @dialect:sqlite` … `-- @dialect:end`.
_BLOCO = re.compile(r"^[ \t]*--[ \t]*@dialect:(\w+)[ \t]*$", re.MULTILINE)

#: Chave do lock de aplicação do PostgreSQL. Dois backends subindo juntos migrariam em paralelo sem isto.
_LOCK_MIGRACAO = 728_193_004


#: Violação de unicidade, seja qual for o driver. Capturar `sqlite3.IntegrityError` direto amarraria a regra de
#: negócio ao SQLite — e a idempotência do projeto (chave UNIQUE + captura) depende de acertar isto.
INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)
#: Erro de sintaxe/objeto ausente. Usado onde o código já tolerava a ausência de um recurso do banco.
OPERATIONAL_ERRORS: tuple[type[BaseException], ...] = (sqlite3.OperationalError,)
try:                                   # o driver do PostgreSQL é opcional: quem roda em SQLite não o instala
    import psycopg as _psycopg
    INTEGRITY_ERRORS = INTEGRITY_ERRORS + (_psycopg.errors.IntegrityError,)
    OPERATIONAL_ERRORS = OPERATIONAL_ERRORS + (_psycopg.errors.OperationalError, _psycopg.errors.ProgrammingError)
except ImportError:                    # pragma: no cover
    pass


def _e_postgres(dsn: str) -> bool:
    return dsn.startswith(("postgres://", "postgresql://"))


class Database:
    """Conexão única serializada por lock. As operações são de milissegundos, então são chamadas direto."""

    def __init__(self, dsn: Path | str):
        self.dsn = str(dsn)
        self.dialect = "postgres" if _e_postgres(self.dsn) else "sqlite"
        self._lock = threading.RLock()
        self._tx_depth = 0
        if self.dialect == "postgres":
            self._conn = self._abrir_postgres()
        else:
            self._conn = self._abrir_sqlite()

    # -- abertura ---------------------------------------------------------------
    def _abrir_sqlite(self) -> Any:
        conn = sqlite3.connect(self.dsn, check_same_thread=False, isolation_level=None)
        # Linha como `dict`: o resto do código acessa por nome, e assim os dois dialetos entregam a mesma coisa.
        conn.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _abrir_postgres(self) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - só falha em instalação sem o driver
            raise RuntimeError("DATABASE_URL aponta para PostgreSQL, mas `psycopg` não está instalado. "
                               "Rode: pip install -r requirements.txt") from exc
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    @property
    def path(self) -> str:
        """Caminho do arquivo, quando existe arquivo. Só faz sentido no SQLite — e há quem precise dele: o teste
        que confere, byte a byte, que nenhuma senha ficou em claro no banco."""
        if self.dialect != "sqlite":
            raise AttributeError("banco PostgreSQL não tem arquivo local")
        return self.dsn

    def columns(self, table: str) -> set[str]:
        """Nomes das colunas de uma tabela. Existe porque a pergunta não tem forma comum: no SQLite é
        `PRAGMA table_info`, no PostgreSQL é `information_schema.columns` — e `PRAGMA` nem aceita parâmetro.

        Quem pergunta é quem confere a FORMA do esquema (que uma coluna não existe, por exemplo). Ter isto aqui é o
        que impede um `PRAGMA` de aparecer solto num teste e travá-lo num banco só.
        """
        if self.dialect == "sqlite":
            # `PRAGMA` não aceita marcador; o nome vem do próprio esquema, nunca de entrada externa.
            return {r["name"] for r in self.query(f"PRAGMA table_info({table})")}   # noqa: S608
        return {r["column_name"] for r in self.query(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?"
            " AND table_schema = ANY (current_schemas(false))", (table,))}

    # -- infraestrutura -------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _sql(self, sql: str) -> str:
        """`?` é a convenção do projeto. O PostgreSQL quer `%s`; traduzir aqui evita duas convenções no código.

        O `%` literal (há dois `LIKE '%"content"%'` no projeto) vira `%%` porque no PostgreSQL `%` é o marcador.
        Quem desfaz a dobra é o psycopg — **mas só quando recebe uma sequência de parâmetros**. Por isso o padrão
        dos métodos abaixo é `()` e não `None`: com `None` o psycopg manda o texto cru, o `%%` chega ao banco e o
        `LIKE` passa a não casar com nada — sem erro nenhum, só resultado errado. Conferido contra PostgreSQL 17.
        """
        if self.dialect != "postgres":
            return sql
        return sql.replace("%", "%%").replace("?", "%s")

    @contextmanager
    def tx(self) -> Iterator[Any]:
        """Transação curta e atômica. Reentrante: o bloco de dentro entra na transação do de fora."""
        with self._lock:
            outer = self._tx_depth == 0
            if outer:
                # BEGIN IMMEDIATE no SQLite pega a trava de escrita já na abertura, evitando o erro tardio.
                self._conn.execute("BEGIN IMMEDIATE" if self.dialect == "sqlite" else "BEGIN")
            self._tx_depth += 1
            try:
                yield self._conn
                if outer:
                    self._conn.execute("COMMIT")
            except BaseException:
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            finally:
                self._tx_depth -= 1

    def execute(self, sql: str, params: tuple | dict = ()) -> Any:
        with self._lock:
            return self._conn.execute(self._sql(sql), params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[Row]:
        with self._lock:
            return list(self._conn.execute(self._sql(sql), params).fetchall())

    def one(self, sql: str, params: tuple | dict = ()) -> Row | None:
        with self._lock:
            return self._conn.execute(self._sql(sql), params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def inserted_id(self, sql: str, params: tuple | dict = (), *, column: str = "id") -> Any:
        """Insere e devolve a chave gerada.

        `cursor.lastrowid` é do SQLite e não existe no PostgreSQL; `RETURNING` funciona nos dois (SQLite desde
        3.35). Um caminho só, em vez de um `if` de dialeto em cada ponto de inserção.
        """
        row = self.one(f"{sql.rstrip().rstrip(';')} RETURNING {column}", params)
        return row[column] if row else None

    # -- migrações -------------------------------------------------------------
    def _so_do_dialeto(self, script: str) -> str:
        """Remove os blocos marcados para OUTRO dialeto.

        Existe porque há divergência real — busca textual é FTS5 no SQLite e `tsvector` no PostgreSQL, e não há
        como fingir que é a mesma coisa. Marcar onde a diferença é real, num arquivo só, é melhor que manter dois
        conjuntos de migração que divergem com o tempo.

            -- @dialect:sqlite
            CREATE VIRTUAL TABLE ... USING fts5(...);
            -- @dialect:end
        """
        saida: list[str] = []
        bloco: str | None = None
        for linha in script.splitlines():
            m = _BLOCO.match(linha)
            if m:
                alvo = m.group(1).lower()
                if alvo == "end":
                    bloco = None
                elif alvo in _DIALETO:
                    bloco = alvo
                else:
                    raise KeyError(f"dialeto desconhecido em migração: @dialect:{alvo}")
                continue
            if bloco is None or bloco == self.dialect:
                saida.append(linha)
        return "\n".join(saida)

    def render(self, script: str) -> str:
        """Aplica os blocos por dialeto e troca as marcas. Marca desconhecida é erro, não texto solto no SQL."""
        script = self._so_do_dialeto(script)
        tabela = _DIALETO[self.dialect]

        def troca(m: re.Match[str]) -> str:
            nome = m.group(1)
            if nome not in tabela:
                raise KeyError(f"marca de dialeto desconhecida na migração: {{{{{nome}}}}}")
            return tabela[nome]

        return _MARCA.sub(troca, script)

    @staticmethod
    def _instrucoes(script: str) -> list[str]:
        """Divide o script em instruções. Existe porque `executescript` é do SQLite.

        Precisa entender literal de texto e comentário, e não é preciosismo: a primeira versão dividia em todo
        `;` e quebrou em `-- 0 = modelo da função; 1 = escalonado`, um comentário de fim de linha. Um `;` dentro
        de aspas quebraria igual, e o erro aparece longe da causa ("incomplete input"), então vale fazer certo.
        """
        instrucoes: list[str] = []
        atual: list[str] = []
        i, n, em_texto = 0, len(script), False
        while i < n:
            c = script[i]
            # Corpo de trigger (`... BEGIN ... END;`) tem `;` DENTRO: só o `END` fecha a instrução. Sem isto o
            # `CREATE TRIGGER` saía pela metade e o SQLite reclamava de "incomplete input".
            if not em_texto and c == ";" and _CORPO_ABERTO.search("".join(atual)):
                atual.append(c)
                i += 1
                continue
            if em_texto:
                atual.append(c)
                if c == "'":
                    # `''` é aspas escapada dentro do literal, não o fim dele.
                    if i + 1 < n and script[i + 1] == "'":
                        atual.append(script[i + 1])
                        i += 2
                        continue
                    em_texto = False
                i += 1
                continue
            if c == "'":
                em_texto = True
                atual.append(c)
            elif c == "-" and i + 1 < n and script[i + 1] == "-":
                while i < n and script[i] != "\n":
                    i += 1
                atual.append("\n")
                continue
            elif c == ";":
                if (s := "".join(atual).strip()):
                    instrucoes.append(s)
                atual = []
            else:
                atual.append(c)
            i += 1
        if (s := "".join(atual).strip()):
            instrucoes.append(s)
        return instrucoes

    @contextmanager
    def _trava_de_migracao(self) -> Iterator[None]:
        """No PostgreSQL, dois backends subindo juntos aplicariam a mesma migração em paralelo."""
        if self.dialect != "postgres":
            yield
            return
        self._conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_MIGRACAO,))
        try:
            yield
        finally:
            self._conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_MIGRACAO,))

    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self._lock, self._trava_de_migracao():
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            done = {r["version"] for r in self._conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.stem in done:
                    continue
                instrucoes = self._instrucoes(self.render(f.read_text(encoding="utf-8")))
                with self.tx():
                    for instrucao in instrucoes:
                        self._conn.execute(instrucao)
                    self._conn.execute(
                        self._sql("INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)"),
                        (f.stem, now_iso()))
                applied.append(f.stem)
        return applied


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
