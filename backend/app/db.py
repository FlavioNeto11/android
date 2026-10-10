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

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .util import now, now_iso, to_iso

log = logging.getLogger("poc.db")

#: 31.307: a consulta SÍNCRONA que a thread do laço de eventos espera por mais que isto vira aviso no log, com o primeiro quadro de
#: fora deste arquivo. O laço parado é o sintoma (ver `vigia_do_laco.py`); isto aponta QUAL chamada o segura, sem esperar outro
#: travamento de 70 s. No máximo um aviso por `INTERVALO_DO_AVISO_NO_LACO_S`.
LIMITE_DA_CONSULTA_NO_LACO_S = 1.0
INTERVALO_DO_AVISO_NO_LACO_S = 5.0

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

#: Segundos que `psycopg.connect` pode esperar antes de desistir. Sem isto, um PostgreSQL fora do ar trava a
#: reconexão no tempo do TCP (~20 s no Windows) DENTRO do laço de eventos — e `health()`, que é a chamada que
#: deveria dizer "o banco caiu", é justamente a que congela o processo inteiro.
CONNECT_TIMEOUT_S = 5

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
#: Migração que RECONSTRÓI uma tabela-pai no SQLite (`-- @foreign_keys:off` no arquivo). Medido antes da 047: com
#: `PRAGMA foreign_keys=ON`, o `DROP TABLE` da tabela antiga faz um `DELETE` implícito que dispara o `ON DELETE
#: CASCADE` dos filhos — credenciais, sessões, vínculos e memória sumiriam junto —, e `PRAGMA foreign_keys=OFF`
#: dentro de uma transação é operação sem efeito. É o procedimento dos 12 passos da documentação do SQLite:
#: desligar antes do `BEGIN`, reconstruir, `PRAGMA foreign_key_check` antes do `COMMIT`, religar depois.
#: No PostgreSQL a marca é ignorada: lá `ALTER COLUMN` resolve sem reconstruir nada.
_SEM_CHAVES = re.compile(r"^[ \t]*--[ \t]*@foreign_keys:off[ \t]*$", re.MULTILINE)
#: Abertura de texto entre cifrões do PostgreSQL: `$$` ou `$tag$`.
_DOLAR = re.compile(r"\$[A-Za-z_]\w*\$|\$\$")

#: Chave do lock de aplicação do PostgreSQL. Dois backends subindo juntos migrariam em paralelo sem isto. É a 1ª das
#: DUAS chaves de `pg_advisory_lock(int, int)`; a 2ª é o esquema corrente (29.62, `_trava_de_migracao`). Cabe em int4.
_LOCK_MIGRACAO = 728_193_004
#: A 2ª chave: o esquema em que a migração vai cair. `COALESCE`: com o `search_path` apontando para um esquema que
#: não existe, `current_schema()` é NULL, e `pg_advisory_lock(k, NULL)` devolve NULL SEM travar (é estrita).
_ESQUEMA_DA_TRAVA = "hashtext(COALESCE(current_schema(), ''))"


#: Violação de unicidade, seja qual for o driver. Capturar `sqlite3.IntegrityError` direto amarraria a regra de
#: negócio ao SQLite — e a idempotência do projeto (chave UNIQUE + captura) depende de acertar isto.
INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)
#: Erro de sintaxe/objeto ausente. Usado onde o código já tolerava a ausência de um recurso do banco.
OPERATIONAL_ERRORS: tuple[type[BaseException], ...] = (sqlite3.OperationalError,)
def coluna_ausente(exc: BaseException) -> bool:
    """O erro é de COLUNA ausente (banco sem a migração que a cria), e só isso. `OPERATIONAL_ERRORS` também pega banco
    travado e SQL inválido (`ProgrammingError`); quem tolera só o banco antigo confere aqui antes de engolir."""
    return "no such column" in str(exc).lower() or getattr(exc, "sqlstate", None) == "42703"   # 42703 = undefined_column


#: A conexão MORREU (banco reiniciado, rede piscou, sessão derrubada pelo administrador) — e só isso.
#:
#: Por que uma tupla separada de `OPERATIONAL_ERRORS`: aquela existe para o código tolerar um RECURSO ausente e
#: inclui `ProgrammingError`, que é erro de SQL. Reabrir a conexão e repetir a consulta por causa de um erro de
#: sintaxe esconderia o defeito e dobraria o trabalho. Aqui entram apenas os erros em que a instrução
#: comprovadamente NÃO chegou ao banco — repetir é seguro porque nada aconteceu.
#:
#: Vazia quando o driver do PostgreSQL não está instalado: no SQLite o banco é um arquivo desta máquina, não há
#: conexão para cair, e `sqlite3.OperationalError` quer dizer "banco travado", que reabrir não resolve.
ERROS_DE_CONEXAO: tuple[type[BaseException], ...] = ()
#: O estado "transação abortada" que o libpq informa (`psycopg.pq.TransactionStatus.INERROR`); `None` sem o driver.
_TRANSACAO_EM_ERRO: object | None = None
try:                                   # o driver do PostgreSQL é opcional: quem roda em SQLite não o instala
    import psycopg as _psycopg
    INTEGRITY_ERRORS = INTEGRITY_ERRORS + (_psycopg.errors.IntegrityError,)
    OPERATIONAL_ERRORS = OPERATIONAL_ERRORS + (_psycopg.errors.OperationalError, _psycopg.errors.ProgrammingError)
    ERROS_DE_CONEXAO = (_psycopg.OperationalError, _psycopg.InterfaceError)
    _TRANSACAO_EM_ERRO = _psycopg.pq.TransactionStatus.INERROR
except ImportError:                    # pragma: no cover
    pass


class TransacaoAbortada(RuntimeError):
    """O `tx()` de fora chegou ao fim com a transação ABORTADA no PostgreSQL: uma instrução falhou lá dentro e alguém
    engoliu o erro sem `savepoint()`. O `COMMIT` viraria `ROLLBACK` sem aviso, e quem chamou seguiria achando que
    gravou (22.5). Fora de `OPERATIONAL_ERRORS` e `INTEGRITY_ERRORS` de propósito: um `except` de recurso ausente ou de
    chave repetida não pode engolir também esta."""


def _e_postgres(dsn: str) -> bool:
    return dsn.startswith(("postgres://", "postgresql://"))


class Database:
    """Conexão única serializada por lock. As operações são de milissegundos, então são chamadas direto."""

    def __init__(self, dsn: Path | str):
        self.dsn = str(dsn)
        self.dialect = "postgres" if _e_postgres(self.dsn) else "sqlite"
        self._lock = threading.RLock()
        self._tx_depth = 0
        #: 31.307: quantas consultas passaram de `LIMITE_DA_CONSULTA_NO_LACO_S` NA THREAD DO LAÇO (o aviso é limitado no tempo).
        self.consultas_lentas_no_laco = 0
        self._ultimo_aviso_no_laco = float("-inf")
        #: O que espera o `COMMIT` da transação de fora para rodar (`depois_do_commit`). Some no `ROLLBACK`.
        self._depois_do_commit: list[Callable[[], None]] = []
        #: A conexão pode estar podre sem ter falhado ainda (um ROLLBACK que não completou). A próxima chamada
        #: fora de transação reabre antes de tentar, em vez de gastar uma falha para descobrir.
        self._suspeita = False
        #: Quantas vezes esta instância reabriu a conexão. É o número que prova que a reconexão aconteceu.
        self.reconexoes = 0
        self._conn = self._abrir()

    # -- abertura ---------------------------------------------------------------
    def _abrir(self) -> Any:
        return self._abrir_postgres() if self.dialect == "postgres" else self._abrir_sqlite()

    def _reabrir(self) -> None:
        """Descarta a conexão morta e abre outra.

        Por que existe (achado #33): a conexão era aberta UMA vez, no construtor. Um `pg_ctl restart`, um
        `pg_terminate_backend` ou uma rede que piscou derrubavam o backend até alguém reiniciá-lo na mão — toda
        consulta passava a falhar, e o `/health` continuava dizendo `ok` porque não olhava o banco.
        """
        with self._lock:
            try:
                self._conn.close()
            except Exception:           # noqa: BLE001 - fechar uma conexão já morta não pode impedir a nova
                pass
            self._conn = self._abrir()
            self._suspeita = False
            self.reconexoes += 1
            log.warning("conexão com o banco reaberta (%s) — reconexão nº %d", self.dialect, self.reconexoes)

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
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT_S)

    @property
    def path(self) -> str:
        """Caminho do arquivo, quando existe arquivo. Só faz sentido no SQLite — e há quem precise dele: o teste
        que confere, byte a byte, que nenhuma senha ficou em claro no banco."""
        if self.dialect != "sqlite":
            raise AttributeError("banco PostgreSQL não tem arquivo local")
        return self.dsn

    # -- relógio ----------------------------------------------------------------
    def agora(self) -> datetime:
        """O relógio do BANCO, não o desta máquina.

        Por que existe (achado #32): o vencimento de um lease é escrito com o relógio de QUEM assumiu e comparado
        com o relógio de QUEM pergunta. Com dois backends, um relógio adiantado alguns minutos enxerga todo lease
        vivo como vencido e adota etapas em plena execução — exatamente o que o lease existe para impedir. Lendo o
        tempo do banco, os dois backends passam a comparar contra a MESMA fonte, e o NTP deixa de ser pré-requisito.

        `clock_timestamp()` e não `now()`: `now()` é o instante em que a TRANSAÇÃO começou e fica congelado dentro
        de um `tx()`, o que encurtaria silenciosamente a validade de um lease renovado lá dentro.

        No SQLite o banco é um arquivo desta máquina: o relógio dele É o relógio local, e a pergunta é grátis.
        """
        if self.dialect != "postgres":
            return now()
        valor = self.scalar("SELECT clock_timestamp()")
        if isinstance(valor, datetime):
            return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
        return now()                       # driver que não devolveu datetime: o local é melhor que nada

    def agora_iso(self) -> str:
        return to_iso(self.agora())

    def prazo_iso(self, seconds: float) -> str:
        """Vencimento daqui a `seconds`, medido pelo relógio do banco."""
        return to_iso(self.agora() + timedelta(seconds=seconds))

    def desvio_do_relogio(self) -> float:
        """Quantos segundos o relógio DESTA máquina está longe do relógio do banco (valor absoluto).

        Sempre 0 no SQLite, onde os dois relógios são o mesmo. É o número que a partida confere e que `health()`
        publica: sem medi-lo, "relógio sincronizado" é prosa em documento, não garantia do sistema.
        """
        if self.dialect != "postgres":
            return 0.0
        antes = now()
        do_banco = self.agora()
        depois = now()
        # O ponto médio da ida-e-volta é o instante local que melhor corresponde à leitura do banco; sem isto, a
        # latência da consulta entraria no desvio e uma rede lenta viraria "relógio errado".
        meio = antes + (depois - antes) / 2
        return abs((do_banco - meio).total_seconds())

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

    def tables(self) -> set[str]:
        """Nomes das tabelas deste banco. Mesmo motivo de `columns()`: a pergunta não tem forma comum.

        Existe porque a prova de que nenhuma senha ficou em claro varre TODAS as tabelas, e ela fazia isso com
        `SELECT name FROM sqlite_master` — o que prendia ao SQLite justamente o teste que guarda credencial
        (achado #162). Com isto a mesma varredura roda nos dois bancos.
        """
        if self.dialect == "sqlite":
            return {r["name"] for r in self.query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        return {r["table_name"] for r in self.query(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = ANY (current_schemas(false)) AND table_type='BASE TABLE'")}

    def alcancavel(self) -> bool:
        """O banco responde? Uma consulta de verdade, porque só ela prova.

        `/health` chamava `ultima_migracao()` engolindo toda exceção, então um PostgreSQL fora do ar aparecia como
        `migration: null` num health `ok` (achado #33). Aqui a pergunta é explícita e passa pelo caminho de
        reconexão: um banco que voltou é reencontrado por esta mesma chamada.
        """
        try:
            return self.scalar("SELECT 1") == 1
        except Exception:               # noqa: BLE001 - qualquer falha aqui É a resposta: o banco não responde
            return False

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

        O `?` só é marcador FORA de literal e de identificador entre aspas (achado #169). A primeira versão trocava
        todo `?` do texto, então `WHERE nome = 'quem?'` viraria `'quem%s'` e o psycopg reclamaria de parâmetro a
        mais — ou, pior, casaria e gravaria a pergunta errada. O `%`, ao contrário, é dobrado em toda parte:
        a desdobra do psycopg também acontece em toda parte.
        """
        if self.dialect != "postgres":
            return sql
        saida: list[str] = []
        i, n, em_texto, em_ident = 0, len(sql), False, False
        while i < n:
            c = sql[i]
            if c == "%":
                saida.append("%%")
            elif em_texto:
                saida.append(c)
                if c == "'":
                    if i + 1 < n and sql[i + 1] == "'":      # `''` é aspas escapada, não o fim do literal
                        saida.append("'")
                        i += 2
                        continue
                    em_texto = False
            elif em_ident:
                saida.append(c)
                if c == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        saida.append('"')
                        i += 2
                        continue
                    em_ident = False
            elif c == "'":
                em_texto = True
                saida.append(c)
            elif c == '"':
                em_ident = True
                saida.append(c)
            elif c == "?":
                saida.append("%s")
            else:
                saida.append(c)
            i += 1
        return "".join(saida)

    def _avisar_se_no_laco(self, gasto: float) -> None:
        """31.307: chamada na thread que roda um laço de eventos e que levou `gasto` s (esperar a trava incluso)? Avisa, com o
        primeiro quadro da pilha fora deste arquivo, e conta em `consultas_lentas_no_laco`. Nunca levanta."""
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return                                  # thread comum (pool, vigia, scripts): esperar ali não para o laço
            self.consultas_lentas_no_laco += 1
            agora = time.monotonic()
            if agora - self._ultimo_aviso_no_laco < INTERVALO_DO_AVISO_NO_LACO_S:
                return
            self._ultimo_aviso_no_laco = agora
            de_fora = next((q for q in reversed(traceback.extract_stack()) if not q.filename.endswith("db.py")), None)
            onde = f"{Path(de_fora.filename).name}:{de_fora.lineno} em {de_fora.name}" if de_fora else "?"
            log.warning("consulta síncrona no laço de eventos levou %.1f s (%s): o laço ficou parado esse tempo; mova a "
                        "chamada para fora dele (asyncio.to_thread)", gasto, onde)
        except Exception:  # noqa: BLE001 - a medida nunca derruba a consulta
            pass

    def _com_reconexao(self, operacao: Callable[[Any], Any]) -> Any:
        """Executa e, se a CONEXÃO tiver morrido, reabre e tenta uma vez mais.

        Uma tentativa a mais, não um laço: se a segunda também falhar, o banco está fora e quem chamou precisa
        saber disso agora — é o que vira `database_down` no `/health`. Repetir em laço só adiaria a verdade.

        **Nunca dentro de uma transação.** Lá a conexão morta levou junto tudo o que a transação já tinha feito;
        repetir só a última instrução gravaria metade do trabalho. O erro sobe, o `tx()` desfaz o que der e a
        próxima chamada de fora reabre.
        """
        inicio = time.monotonic()                       # 31.307: a espera da trava entra na medida
        try:
            with self._lock:
                if self._suspeita and self._tx_depth == 0:
                    self._reabrir()
                try:
                    return operacao(self._conn)
                except ERROS_DE_CONEXAO:
                    if self._tx_depth > 0:
                        self._suspeita = True
                        raise
                    self._reabrir()
                    return operacao(self._conn)
        finally:
            gasto = time.monotonic() - inicio
            if gasto >= LIMITE_DA_CONSULTA_NO_LACO_S:
                self._avisar_se_no_laco(gasto)

    def depois_do_commit(self, fn: Callable[[], None]) -> None:
        """Roda `fn` depois do `COMMIT` da transação aberta NESTA thread, fora da trava; sem transação, roda já. No
        `ROLLBACK`, não roda; num `savepoint()` desfeito, o que foi pedido dentro dele também não (29.108: a escrita
        que o pediu saiu com o `ROLLBACK TO SAVEPOINT`). Existe para o efeito que outra thread vai ler (o assentamento
        de uma execução, 29.93): disparado dentro da transação, ele podia esperar a trava no laço de eventos, ou ler o
        estado de antes."""
        with self._lock:
            if self._tx_depth > 0:
                self._depois_do_commit.append(fn)
                return
        fn()

    @contextmanager
    def tx(self) -> Iterator[Any]:
        """Transação curta e atômica. Reentrante: o bloco de dentro entra na transação do de fora."""
        pendentes: list[Callable[[], None]] = []
        with self._lock:
            outer = self._tx_depth == 0
            if outer:
                # BEGIN IMMEDIATE no SQLite pega a trava de escrita já na abertura, evitando o erro tardio.
                self._conn.execute("BEGIN IMMEDIATE" if self.dialect == "sqlite" else "BEGIN")
            self._tx_depth += 1
            try:
                yield self._conn
                if outer:
                    if self._transacao_abortada():
                        # Defesa em profundidade (22.5): os pontos conhecidos que engolem erro dentro de uma transação
                        # alheia já usam `savepoint()`; o próximo que esquecer derruba a escrita aqui, alto, em vez de
                        # o `COMMIT` virar `ROLLBACK` calado. O `except` abaixo faz o `ROLLBACK`.
                        raise TransacaoAbortada("transação abortada por um erro engolido lá dentro (sem savepoint): "
                                                "nada foi gravado")
                    self._conn.execute("COMMIT")
                    pendentes, self._depois_do_commit = self._depois_do_commit, []
            except BaseException:
                if outer:
                    self._depois_do_commit = []
                    try:
                        self._conn.execute("ROLLBACK")
                    except Exception:   # noqa: BLE001 - ver abaixo
                        # Quando a CONEXÃO é o que caiu, o ROLLBACK também falha — e, sem este `except`, a falha
                        # da faxina substituía a exceção original: quem chamasse via "não foi possível desfazer"
                        # em vez do erro que de fato derrubou a transação. O banco já desfez tudo sozinho ao
                        # perder a sessão; o que falta é não usar mais esta conexão.
                        self._suspeita = True
                raise
            finally:
                self._tx_depth -= 1
        for fn in pendentes:            # já gravado e fora da trava: o efeito lê o que foi gravado
            try:
                fn()
            except Exception:  # noqa: BLE001 - o efeito posterior nunca desfaz nem derruba o que já foi gravado
                log.exception("efeito depois do commit")

    def _transacao_abortada(self) -> bool:
        """A conexão diz que a transação aberta está abortada. Só o psycopg sabe dizer (`info.transaction_status`, lido
        do libpq, sem ida ao servidor); o `sqlite3` não tem `info` e nunca aborta — ali a resposta é sempre não.

        Pelo atributo, e não por `dialect == "postgres"`, de propósito: é o que deixa a imitação do aborto sobre o
        SQLite (`tests/aborto_do_postgres.py`) exercitar esta defesa sem um PostgreSQL de pé."""
        if _TRANSACAO_EM_ERRO is None:
            return False
        info = getattr(self._conn, "info", None)
        return getattr(info, "transaction_status", None) == _TRANSACAO_EM_ERRO

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Sub-bloco de uma transação que pode falhar SOZINHO: o que ele gravou é desfeito, e a de fora segue.

        Por que existe (22.5): no PostgreSQL, uma instrução que falha dentro de `tx()` ABORTA a transação inteira —
        toda instrução seguinte é recusada ("current transaction is aborted") e o `COMMIT` do fim vira `ROLLBACK`
        no servidor, sem erro para quem chamou. Quem ENGOLE a exceção de uma escrita acessória dentro da transação
        alheia (a trilha do livro dentro de `FlowStore`/`RecipeStore`) tem de pôr o savepoint DENTRO do `try`, em
        volta de tudo o que tocou: `ROLLBACK TO SAVEPOINT` tira a transação do estado abortado. O SQLite não aborta
        a transação, então só lá o erro engolido não fazia estrago — e a suíte, que roda nele, não via.

        Reentrante como `tx()`: dentro dele, `tx()` entra na transação e outro `savepoint()` ganha nome próprio.
        Fora de transação é um `tx()` comum (cada instrução já seria a sua própria transação; assim o bloco ao menos
        é atômico).
        """
        with self._lock:
            if self._tx_depth == 0:
                with self.tx():
                    yield
                return
            nome = f"sp_{self._tx_depth}"
            self._conn.execute(f"SAVEPOINT {nome}")
            self._tx_depth += 1
            suspeita_antes = self._suspeita
            pendentes_antes = len(self._depois_do_commit)
            try:
                yield
            except BaseException:
                # 29.108: o efeito pedido dentro do sub-bloco desfeito sai com ele (a escrita que o pediu foi desfeita).
                del self._depois_do_commit[pendentes_antes:]
                try:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {nome}")
                    self._conn.execute(f"RELEASE SAVEPOINT {nome}")
                except Exception:       # noqa: BLE001 - a conexão caiu: a exceção original é a que conta (ver tx())
                    self._suspeita = True
                else:
                    # A conexão respondeu ao ROLLBACK TO e ao RELEASE: está viva. Um deadlock ou um statement timeout
                    # lá dentro é `psycopg.OperationalError` (em ERROS_DE_CONEXAO), e `_com_reconexao` já a tinha
                    # marcado suspeita — sem isto, a primeira instrução de fora reabria uma conexão boa à toa.
                    self._suspeita = suspeita_antes
                raise
            else:
                self._conn.execute(f"RELEASE SAVEPOINT {nome}")
            finally:
                self._tx_depth -= 1

    def execute(self, sql: str, params: tuple | dict = ()) -> Any:
        return self._com_reconexao(lambda c: c.execute(self._sql(sql), params))

    def query(self, sql: str, params: tuple | dict = ()) -> list[Row]:
        return self._com_reconexao(lambda c: list(c.execute(self._sql(sql), params).fetchall()))

    def one(self, sql: str, params: tuple | dict = ()) -> Row | None:
        return self._com_reconexao(lambda c: c.execute(self._sql(sql), params).fetchone())

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
            # Corpo de função do PostgreSQL entre `$$` (ou `$tag$`): tudo lá dentro é texto, inclusive `;` e `'`.
            # Nenhuma migração usa isto HOJE — a 017 escreve a função na forma `RETURN <expressão>;`, sem corpo
            # citado. Está aqui porque a primeira que usar quebraria só no PostgreSQL, que ninguém roda a cada
            # commit: o erro apareceria num deploy, longe da causa.
            if not em_texto and c == "$" and (m := _DOLAR.match(script, i)):
                fim = script.find(m.group(0), m.end())
                fim = n if fim == -1 else fim + len(m.group(0))
                atual.append(script[i:fim])
                i = fim
                continue
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
        """No PostgreSQL, dois backends subindo juntos aplicariam a mesma migração em paralelo.

        29.62: a trava é POR ESQUEMA (a forma de duas chaves, com o esquema corrente na segunda). Em produção há um
        esquema só, e dois backends no mesmo banco seguem em fila. Na suíte em PostgreSQL cada teste migra o PRÓPRIO
        esquema (`tests/conftest.py::_dsn_de_teste`), e a chave única punha os workers do xdist em fila: 1006 testes
        em 22 min com `-n 4`, mais devagar que em série.

        A forma de uma chave (a de antes) e a de duas não se enxergam: um backend antigo e um novo subindo JUNTOS no
        mesmo banco não se esperariam. O deploy para o antigo antes de subir o novo (`scripts/deploy.ps1`)."""
        if self.dialect != "postgres":
            yield
            return
        # A 2ª chave é lida UMA vez e o mesmo inteiro vai ao lock e ao unlock: recalculada no unlock, um `search_path`
        # trocado no meio daria outra chave, e a trava ficaria presa até a sessão fechar.
        esquema = int(self._conn.execute(f"SELECT {_ESQUEMA_DA_TRAVA} AS k").fetchone()["k"])
        self._conn.execute("SELECT pg_advisory_lock(%s::int, %s::int)", (_LOCK_MIGRACAO, esquema))
        try:
            yield
        finally:
            self._conn.execute("SELECT pg_advisory_unlock(%s::int, %s::int)", (_LOCK_MIGRACAO, esquema))

    def _impressao(self, script: str) -> str:
        """sha256 do script RENDERIZADO. Renderizado, e não o arquivo cru, porque é o texto renderizado que o banco
        executou: o mesmo arquivo gera esquemas diferentes em SQLite e PostgreSQL, e é a divergência de ESQUEMA
        que se quer detectar.

        A quebra de linha é normalizada antes de entrar no hash, e isso não é preciosismo: com `core.autocrlf` o
        mesmo commit chega como CRLF numa máquina e LF noutra. Sem normalizar, o backend A gravaria as impressões
        de uma forma, o backend B calcularia as da outra, e TODAS as migrações apareceriam como alteradas — um
        `/health` degradado num parque saudável, exatamente no cenário de dois backends que isto veio servir.
        """
        return hashlib.sha256(script.replace("\r\n", "\n").encode("utf-8")).hexdigest()

    def divergencias(self) -> list[str]:
        """Versões já aplicadas cujo arquivo MUDOU desde então (achado #169).

        A regra escrita é "migração aplicada não se edita, cria-se outra" — mas nada a fazia valer, e o dano já
        aconteceu: o banco de produção foi migrado com a 008 antiga e tem hoje um esquema diferente do que o mesmo
        arquivo gera. Ninguém notou porque o controle guardava só o número.

        Linhas com `checksum` nulo são as aplicadas ANTES desta conferência existir: delas não se sabe nada, e
        inventar um alarme para elas seria ruído no primeiro `/health` de todo banco antigo.
        """
        # `health()` é chamado num laço, e reler+renderizar+hashear as 28 migrações custou 2,7 ms medidos por
        # chamada — barato, mas pago à toa: os arquivos não mudam durante a execução. A assinatura (nome, tamanho,
        # mtime) responde isso em microssegundos, e um arquivo editado com o processo no ar muda a assinatura, de
        # modo que o alarme continua aparecendo sem precisar reiniciar.
        assinatura = tuple(sorted((f.name, st.st_size, st.st_mtime_ns)
                                  for f in MIGRATIONS_DIR.glob("*.sql") if (st := f.stat())))
        if getattr(self, "_assinatura_das_migracoes", None) == assinatura:
            return self._divergencias_em_cache
        try:
            linhas = self.query("SELECT version, checksum FROM schema_migrations WHERE checksum IS NOT NULL")
        except OPERATIONAL_ERRORS:      # banco sem a coluna (nunca migrou por este código) — nada a comparar
            return []
        mudaram: list[str] = []
        for linha in linhas:
            arquivo = MIGRATIONS_DIR / f"{linha['version']}.sql"
            if not arquivo.exists():
                mudaram.append(linha["version"])
                continue
            if self._impressao(self.render(arquivo.read_text(encoding="utf-8"))) != linha["checksum"]:
                mudaram.append(linha["version"])
        self._assinatura_das_migracoes, self._divergencias_em_cache = assinatura, mudaram
        return mudaram

    def migrate(self, ao_aplicar: Callable[[str], None] | None = None) -> list[str]:
        """Aplica as migrações pendentes. `ao_aplicar(versao)` roda depois de cada uma (29.131: a marca da partida
        anda a cada migração); um erro dele não desfaz nem interrompe nada."""
        applied: list[str] = []
        with self._lock, self._trava_de_migracao():
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            # `schema_migrations` é a tabela que o próprio migrador cria; ela não nasce de um arquivo de migração,
            # então é aqui — e não numa 028 — que a coluna nova entra. `ADD COLUMN IF NOT EXISTS` não existe no
            # SQLite, daí a pergunta antes.
            if "checksum" not in self.columns("schema_migrations"):
                self._conn.execute("ALTER TABLE schema_migrations ADD COLUMN checksum TEXT")
            done = {r["version"] for r in self._conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.stem in done:
                    continue
                texto = f.read_text(encoding="utf-8")
                renderizado = self.render(texto)
                instrucoes = self._instrucoes(renderizado)
                reconstroi = self.dialect == "sqlite" and _SEM_CHAVES.search(texto) is not None
                with self._sem_chaves_estrangeiras(reconstroi), self.tx():
                    for instrucao in instrucoes:
                        self._conn.execute(instrucao)
                    if reconstroi:
                        # Dentro da transação, de propósito: uma linha órfã que a reconstrução tenha deixado
                        # desfaz a migração inteira em vez de virar dado quebrado com o esquema novo.
                        violacoes = self._conn.execute("PRAGMA foreign_key_check").fetchall()
                        if violacoes:
                            raise sqlite3.IntegrityError(
                                f"migração {f.stem}: {len(violacoes)} linha(s) com chave estrangeira quebrada "
                                f"após a reconstrução (primeira: {violacoes[0]})")
                    self._conn.execute(
                        self._sql("INSERT INTO schema_migrations(version, applied_at, checksum) VALUES (?,?,?)"),
                        (f.stem, now_iso(), self._impressao(renderizado)))
                applied.append(f.stem)
                if ao_aplicar is not None:
                    try:
                        ao_aplicar(f.stem)
                    except Exception:  # noqa: BLE001 - quem acompanha não pode derrubar a migração
                        pass
        return applied

    @contextmanager
    def _sem_chaves_estrangeiras(self, ligado: bool) -> Iterator[None]:
        """`PRAGMA foreign_keys=OFF` ao redor de UMA migração marcada com `-- @foreign_keys:off` (ver `_SEM_CHAVES`).

        Fora da transação, porque dentro dela o pragma não faz nada; religado no `finally`, porque uma migração que
        falha não pode deixar a conexão sem chave estrangeira para o resto da vida do processo.
        """
        if not ligado:
            yield
            return
        self._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            yield
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
