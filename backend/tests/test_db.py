"""A camada de banco, por ela mesma: divisor de instruções, tradutor de dialeto, reconexão, savepoint e controle de
migração.

Por que este arquivo existe (achado #169): o divisor de SQL e o tradutor de marcas são um mini-parser escrito à
mão, e até aqui só eram exercitados INDIRETAMENTE — pelas 27 migrações existentes, aplicadas num SQLite. Quer
dizer: a prova de que eles funcionam era "as migrações de hoje passam", e a próxima construção nova (um `?`
dentro de literal, um corpo de função entre cifrões) quebraria só no PostgreSQL, que ninguém roda a cada commit.
O erro apareceria num deploy, longe da causa.

Quase tudo aqui é de texto puro (`_instrucoes`, `render`, `_sql`) e não precisa de banco nenhum: um `Database`
por dialeto, sem conexão de verdade, responde. O que precisa de banco usa o DSN da suíte — `TEST_DATABASE_URL`
quando ela existe, arquivo temporário quando não.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from app import db as db_mod
from app.db import OPERATIONAL_ERRORS, Database

from .aborto_do_postgres import ABORTADA, embrulhar, savepoints
from .conftest import _dsn_de_teste


class _Falso(Database):
    """Um `Database` do dialeto pedido, SEM abrir conexão. As funções de texto não precisam de banco, e abrir um
    PostgreSQL só para perguntar como ele escreveria um `?` seria pagar caro por uma resposta de string."""

    def __init__(self, dialect: str):
        self.dsn = "postgresql://x/y" if dialect == "postgres" else ":memory:"
        self.dialect = dialect


@pytest.fixture
def pg() -> _Falso:
    return _Falso("postgres")


@pytest.fixture
def lite() -> _Falso:
    return _Falso("sqlite")


def _banco(tmp_path: Path, nome: str = "t.sqlite3") -> Database:
    return Database(_dsn_de_teste() or tmp_path / nome)


# --------------------------------------------------------------------- divisor de instruções
def test_ponto_e_virgula_dentro_de_comentario_nao_divide() -> None:
    """O caso que quebrou de verdade: `-- 0 = modelo da função; 1 = escalonado`. A primeira versão dividia em todo
    `;` e o SQLite reclamava de "incomplete input" — um erro que não aponta para a linha que o causou."""
    instrucoes = Database._instrucoes("CREATE TABLE t (\n  a INT  -- 0 = padrão; 1 = escalonado\n);")
    assert len(instrucoes) == 1
    assert instrucoes[0].startswith("CREATE TABLE t")


def test_ponto_e_virgula_dentro_de_literal_nao_divide() -> None:
    instrucoes = Database._instrucoes("INSERT INTO t(a) VALUES ('um; dois'); SELECT 1;")
    assert len(instrucoes) == 2
    assert "'um; dois'" in instrucoes[0]


def test_aspas_escapada_dentro_de_literal_nao_fecha_o_literal() -> None:
    """`''` é uma aspas dentro do texto, não o fim dele. Sem isto o divisor sairia do literal no meio e passaria a
    tratar o resto do texto como SQL — e o `;` seguinte dividiria a instrução em duas metades inválidas."""
    instrucoes = Database._instrucoes("INSERT INTO t(a) VALUES ('d''água; e sal'); SELECT 2;")
    assert len(instrucoes) == 2
    assert instrucoes[0].endswith("('d''água; e sal')")


def test_corpo_de_trigger_fica_inteiro() -> None:
    """`CREATE TRIGGER ... BEGIN ...; ...; END;` tem `;` DENTRO: só o `END` fecha a instrução."""
    script = ("CREATE TRIGGER t_ai AFTER INSERT ON t BEGIN\n"
              "  INSERT INTO fts(rowid, c) VALUES (new.id, new.c);\n"
              "  UPDATE t SET n = n + 1;\n"
              "END;\n"
              "SELECT 3;")
    instrucoes = Database._instrucoes(script)
    assert len(instrucoes) == 2
    assert instrucoes[0].count(";") == 2 and instrucoes[0].rstrip().endswith("END")
    assert instrucoes[1] == "SELECT 3"


def test_funcao_do_postgres_na_forma_da_017_fica_inteira() -> None:
    """A forma que a 017 usa de verdade: `RETURN <expressão>;`, sem corpo citado."""
    script = ("CREATE OR REPLACE FUNCTION sem_acento(txt text) RETURNS text\n"
              "    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE\n"
              "    RETURN translate(txt, 'áç', 'ac');\n"
              "SELECT 1;")
    instrucoes = Database._instrucoes(script)
    assert len(instrucoes) == 2
    assert instrucoes[0].startswith("CREATE OR REPLACE FUNCTION") and "translate" in instrucoes[0]


def test_corpo_de_funcao_entre_cifroes_fica_inteiro() -> None:
    """Nenhuma migração usa `$$` hoje — e é por isso que o teste existe: a primeira que usar quebraria só no
    PostgreSQL, num deploy, e o `;` de dentro do corpo partiria a função em pedaços inválidos."""
    script = ("CREATE FUNCTION f() RETURNS int AS $$\n"
              "BEGIN\n  RAISE NOTICE 'oi; tudo bem';\n  RETURN 1;\nEND;\n"
              "$$ LANGUAGE plpgsql;\n"
              "SELECT 4;")
    instrucoes = Database._instrucoes(script)
    assert len(instrucoes) == 2
    assert instrucoes[0].count("RETURN 1") == 1 and instrucoes[0].rstrip().endswith("LANGUAGE plpgsql")
    assert instrucoes[1] == "SELECT 4"


def test_cifrao_com_marcador_nomeado_tambem_fecha() -> None:
    instrucoes = Database._instrucoes("CREATE FUNCTION f() RETURNS int AS $corpo$ SELECT 1; $corpo$ LANGUAGE sql;")
    assert len(instrucoes) == 1


# --------------------------------------------------------------------- blocos por dialeto
_COM_BLOCOS = ("CREATE TABLE t (a INT);\n"
               "-- @dialect:sqlite\n"
               "CREATE VIRTUAL TABLE fts USING fts5(c);\n"
               "-- @dialect:end\n"
               "-- @dialect:postgres\n"
               "ALTER TABLE t ADD COLUMN busca tsvector;\n"
               "-- @dialect:end\n"
               "CREATE INDEX ix ON t(a);")


def test_bloco_do_outro_dialeto_some(pg: _Falso, lite: _Falso) -> None:
    so_pg = pg._so_do_dialeto(_COM_BLOCOS)
    assert "tsvector" in so_pg and "fts5" not in so_pg
    so_lite = lite._so_do_dialeto(_COM_BLOCOS)
    assert "fts5" in so_lite and "tsvector" not in so_lite
    # O que está FORA de bloco vale nos dois — é a maior parte de toda migração.
    for saida in (so_pg, so_lite):
        assert "CREATE TABLE t" in saida and "CREATE INDEX ix" in saida


def test_dialeto_desconhecido_levanta(lite: _Falso) -> None:
    """Erro, e não "ignora o bloco": um `-- @dialect:mysql` esquecido faria a migração aplicar MENOS do que o
    autor escreveu, e o esquema nasceria incompleto em silêncio."""
    with pytest.raises(KeyError, match="mysq"):
        lite._so_do_dialeto("-- @dialect:mysq\nSELECT 1;\n-- @dialect:end\n")


# --------------------------------------------------------------------- marcas
def test_marcas_trocam_por_dialeto(pg: _Falso, lite: _Falso) -> None:
    script = "CREATE TABLE t (id {{PK_AUTO}}, dado {{BLOB}}, quando TEXT DEFAULT {{NOW}});"
    assert "BIGSERIAL PRIMARY KEY" in pg.render(script) and "BYTEA" in pg.render(script)
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in lite.render(script) and " BLOB" in lite.render(script)
    assert "{{" not in pg.render(script) and "{{" not in lite.render(script)


def test_marca_desconhecida_levanta(lite: _Falso) -> None:
    """Sem isto, `{{PK_AUTOO}}` chegaria ao banco como texto solto no meio do DDL."""
    with pytest.raises(KeyError, match="PK_AUTOO"):
        lite.render("CREATE TABLE t (id {{PK_AUTOO}});")


# --------------------------------------------------------------------- tradutor de marcador
def test_no_sqlite_o_sql_passa_intacto(lite: _Falso) -> None:
    sql = "SELECT * FROM t WHERE a=? AND b LIKE '%x%'"
    assert lite._sql(sql) == sql


def test_marcador_vira_por_cento_s(pg: _Falso) -> None:
    assert pg._sql("SELECT * FROM t WHERE a=? AND b=?") == "SELECT * FROM t WHERE a=%s AND b=%s"


def test_por_cento_de_like_e_dobrado(pg: _Falso) -> None:
    """O `%` do `LIKE` é literal e precisa chegar dobrado: o psycopg desfaz a dobra ao receber os parâmetros. Sem
    isto o `LIKE` não casa com nada — sem erro nenhum, só resultado errado."""
    assert pg._sql("SELECT 1 FROM t WHERE c LIKE '%\"content\"%'") == "SELECT 1 FROM t WHERE c LIKE '%%\"content\"%%'"


def test_interrogacao_dentro_de_literal_nao_e_marcador(pg: _Falso) -> None:
    """O ponto do achado #169: `?` só é marcador FORA de literal. Trocando dentro, `'quem?'` viraria `'quem%s'` —
    e o psycopg ou reclamaria de parâmetro a mais, ou gravaria a pergunta errada."""
    assert pg._sql("UPDATE t SET titulo='e aí?' WHERE id=?") == "UPDATE t SET titulo='e aí?' WHERE id=%s"
    assert pg._sql("SELECT 'a''b?c' AS x WHERE id=?") == "SELECT 'a''b?c' AS x WHERE id=%s"


def test_interrogacao_dentro_de_identificador_entre_aspas_nao_e_marcador(pg: _Falso) -> None:
    assert pg._sql('SELECT "col?" FROM t WHERE id=?') == 'SELECT "col?" FROM t WHERE id=%s'


# --------------------------------------------------------------------- reconexão
class _ConexaoMorreu(Exception):
    """Faz o papel de `psycopg.OperationalError` sem exigir um PostgreSQL de pé."""


class _ConexaoFragil:
    """A conexão de verdade, com um interruptor de queda. O SQLite não deixa trocar o `execute` da conexão dele,
    e derrubar um PostgreSQL no meio do teste exigiria um PostgreSQL — então o que se embrulha é a conexão."""

    def __init__(self, real: Any):
        self._real = real
        self.morre = 0                     # próximas N chamadas levantam "a conexão morreu"
        self.rollback_falha = False        # o ROLLBACK da faxina também não completa

    def execute(self, sql: str, params: Any = ()) -> Any:
        if self.rollback_falha and sql.strip().upper().startswith("ROLLBACK"):
            raise RuntimeError("a conexão caiu: nem o ROLLBACK completou")
        if self.morre > 0:
            self.morre -= 1
            raise _ConexaoMorreu("server closed the connection unexpectedly")
        return self._real.execute(sql, params)

    def close(self) -> None:
        self._real.close()


def test_conexao_morta_e_reaberta_e_a_consulta_responde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado #33: a conexão era aberta UMA vez, no construtor. Um reinício do PostgreSQL derrubava toda consulta
    até alguém reiniciar o backend na mão.

    O teste não derruba um PostgreSQL de verdade — ele declara qual exceção significa "a conexão morreu" e faz a
    primeira chamada levantá-la. O que se prova é a REGRA: reabrir e repetir uma vez.
    """
    db = _banco(tmp_path)
    db.migrate()
    monkeypatch.setattr(db_mod, "ERROS_DE_CONEXAO", (_ConexaoMorreu,))
    db._conn = fragil = _ConexaoFragil(db._conn)
    fragil.morre = 1
    assert db.scalar("SELECT 1") == 1                 # respondeu, apesar da queda
    assert db.reconexoes == 1                         # reabriu UMA vez, não um laço
    assert db.scalar("SELECT 1") == 1                 # e a conexão nova continua servindo
    assert db.reconexoes == 1
    db.close()


def test_banco_fora_por_completo_levanta_em_vez_de_repetir_para_sempre(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Uma tentativa a mais, não um laço: se o banco está fora MESMO, quem chamou precisa saber agora — é o que
    vira `database_down` no `/health`."""
    db = _banco(tmp_path)
    db.migrate()
    monkeypatch.setattr(db_mod, "ERROS_DE_CONEXAO", (_ConexaoMorreu,))
    db._conn = fragil = _ConexaoFragil(db._conn)
    fragil.morre = 1
    monkeypatch.setattr(db, "_abrir", lambda: fragil)       # "reabrir" devolve uma conexão igualmente morta
    fragil.morre = 2
    with pytest.raises(_ConexaoMorreu):
        db.scalar("SELECT 1")
    assert db.reconexoes == 1
    db.close()


def test_dentro_de_transacao_o_erro_sobe_em_vez_de_repetir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Repetir só a última instrução de uma transação morta gravaria METADE do trabalho. O erro tem de subir — e,
    como nem o ROLLBACK da faxina completa, a conexão fica marcada para a próxima chamada de fora reabrir.

    O que este teste também protege: a falha do ROLLBACK **não** substitui a exceção original. Sem o `except` que
    a engole, quem chamasse veria "nem o ROLLBACK completou" em vez do erro que de fato derrubou a transação.
    """
    db = _banco(tmp_path)
    db.migrate()
    monkeypatch.setattr(db_mod, "ERROS_DE_CONEXAO", (_ConexaoMorreu,))
    db._conn = fragil = _ConexaoFragil(db._conn)
    with pytest.raises(_ConexaoMorreu):                 # e NÃO o RuntimeError do ROLLBACK
        with db.tx():
            fragil.morre, fragil.rollback_falha = 1, True
            db.execute("SELECT 1")
    assert db.reconexoes == 0                           # nada foi repetido dentro da transação
    assert db._suspeita is True
    fragil.rollback_falha = False
    assert db.scalar("SELECT 1") == 1                   # a chamada seguinte reabre sozinha, antes de tentar
    assert db.reconexoes == 1
    db.close()


# --------------------------------------------------------------------- savepoint (22.5)
def _tabela(db: Database) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS t_savepoint (x TEXT)")
    db.execute("DELETE FROM t_savepoint")


def _linhas(db: Database) -> list[str]:
    return sorted(str(r["x"]) for r in db.query("SELECT x FROM t_savepoint"))


def test_savepoint_desfaz_so_o_sub_bloco_e_a_transacao_segue(tmp_path: Path) -> None:
    """Nos dois bancos. Com `TEST_DATABASE_URL` é a prova no PostgreSQL de verdade: sem o `ROLLBACK TO SAVEPOINT`,
    o erro de dentro abortaria a transação, e o INSERT de depois seria recusado."""
    db = _banco(tmp_path)
    _tabela(db)
    with db.tx():
        db.execute("INSERT INTO t_savepoint(x) VALUES ('antes')")
        with pytest.raises(OPERATIONAL_ERRORS):
            with db.savepoint():
                db.execute("INSERT INTO t_savepoint(x) VALUES ('dentro')")
                db.execute("INSERT INTO tabela_que_nao_existe(x) VALUES ('x')")
        db.execute("INSERT INTO t_savepoint(x) VALUES ('depois')")
    assert _linhas(db) == ["antes", "depois"]            # o que o sub-bloco gravou antes de falhar também saiu
    # Sem falha, o sub-bloco entra junto; o aninhado tem nome próprio; e fora de transação é um `tx()` (atômico).
    with db.tx():
        with db.savepoint():
            db.execute("INSERT INTO t_savepoint(x) VALUES ('fora')")
            with db.savepoint():
                db.execute("INSERT INTO t_savepoint(x) VALUES ('aninhado')")
    with pytest.raises(OPERATIONAL_ERRORS):
        with db.savepoint():
            db.execute("INSERT INTO t_savepoint(x) VALUES ('solto')")
            db.execute("INSERT INTO tabela_que_nao_existe(x) VALUES ('x')")
    assert _linhas(db) == ["aninhado", "antes", "depois", "fora"]
    db.execute("DROP TABLE t_savepoint")
    db.close()


def test_com_o_aborto_do_postgres_o_erro_engolido_so_nao_perde_a_escrita_com_o_savepoint_dentro_do_try(
        tmp_path: Path) -> None:
    """A regra do 22.5, sobre o SQLite com o aborto do PostgreSQL imitado (`aborto_do_postgres`).

    1. Sem savepoint (o defeito do A5): a falha engolida aborta a transação, e o `COMMIT` vira `ROLLBACK` sem erro —
       a escrita principal some, e quem chamou nem fica sabendo.
    2. Savepoint FORA do `try` que engole: ele não vê a falha, tenta `RELEASE` numa transação abortada, e tudo cai.
    3. Savepoint DENTRO do `try`: `ROLLBACK TO SAVEPOINT` tira a transação do aborto, e a principal fica.
    """
    db = _banco(tmp_path)
    if db.dialect != "sqlite":
        db.close()
        pytest.skip("o embrulho imita o PostgreSQL sobre o SQLite; no PostgreSQL real vale o teste acima")
    _tabela(db)
    pg = embrulhar(db)
    pg.falhar_em = "'acessoria'"
    with db.tx():
        db.execute("INSERT INTO t_savepoint(x) VALUES ('principal')")
        try:
            db.execute("INSERT INTO t_savepoint(x) VALUES ('acessoria')")
        except OPERATIONAL_ERRORS:
            pass
    assert pg.commits_perdidos == 1 and _linhas(db) == []
    with pytest.raises(OPERATIONAL_ERRORS, match=ABORTADA):
        with db.tx():
            db.execute("INSERT INTO t_savepoint(x) VALUES ('principal')")
            with db.savepoint():
                try:
                    db.execute("INSERT INTO t_savepoint(x) VALUES ('acessoria')")
                except OPERATIONAL_ERRORS:
                    pass
    assert _linhas(db) == [] and not pg.abortada
    pg.instrucoes.clear()
    with db.tx():
        db.execute("INSERT INTO t_savepoint(x) VALUES ('principal')")
        try:
            with db.savepoint():
                db.execute("INSERT INTO t_savepoint(x) VALUES ('acessoria')")
        except OPERATIONAL_ERRORS:
            pass
    assert pg.commits_perdidos == 1 and _linhas(db) == ["principal"]
    assert savepoints(pg) == ["SAVEPOINT sp_1", "ROLLBACK TO SAVEPOINT sp_1", "RELEASE SAVEPOINT sp_1"]
    db.close()


def test_alcancavel_responde_falso_quando_o_banco_nao_responde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _banco(tmp_path)
    db.migrate()
    assert db.alcancavel() is True
    monkeypatch.setattr(db, "scalar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("banco fora")))
    assert db.alcancavel() is False
    db.close()


# --------------------------------------------------------------------- controle de migração
def _copia_das_migracoes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, ate: str | None = None) -> Path:
    """Uma cópia dos arquivos de migração, para o teste poder editá-los sem tocar nos de verdade."""
    destino = tmp_path / "migracoes"
    destino.mkdir()
    for f in sorted((Path(db_mod.__file__).resolve().parents[1] / "migrations").glob("*.sql")):
        if ate is not None and f.stem > ate:
            continue
        shutil.copy2(f, destino / f.name)
    monkeypatch.setattr(db_mod, "MIGRATIONS_DIR", destino)
    return destino


def test_migracao_grava_a_impressao_do_arquivo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _copia_das_migracoes(tmp_path, monkeypatch)
    db = _banco(tmp_path)
    aplicadas = db.migrate()
    assert "028_convergencia_da_008" in aplicadas
    linha = db.one("SELECT checksum FROM schema_migrations WHERE version=?", ("001_init",))
    assert linha is not None and linha["checksum"] and len(linha["checksum"]) == 64
    assert db.divergencias() == []
    db.close()


def test_arquivo_de_migracao_ja_aplicada_que_muda_vira_divergencia(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """O dano que já aconteceu (achado #169): a 008 foi reescrita depois de aplicada e o banco de produção ficou
    com um esquema que o mesmo arquivo não gera mais. O controle guardava só o número, e ninguém viu."""
    destino = _copia_das_migracoes(tmp_path, monkeypatch)
    db = _banco(tmp_path)
    db.migrate()
    assert db.divergencias() == []
    alvo = destino / "008_instagram_domain.sql"
    alvo.write_text(alvo.read_text(encoding="utf-8") + "\nCREATE INDEX ix_tarde_demais ON personas(handle);\n",
                    encoding="utf-8")
    assert db.divergencias() == ["008_instagram_domain"]
    assert db.migrate() == []                          # e a divergência NÃO é aplicada por baixo do pano
    db.close()


def test_linha_sem_impressao_nao_vira_alarme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Todo banco que existe hoje foi migrado antes desta conferência: `checksum` nulo quer dizer "não sei", e
    inventar alarme para isso seria ruído no primeiro `/health` de cada banco antigo."""
    _copia_das_migracoes(tmp_path, monkeypatch)
    db = _banco(tmp_path)
    db.migrate()
    db.execute("UPDATE schema_migrations SET checksum=NULL")
    assert db.divergencias() == []
    db.close()


def test_028_cria_o_indice_que_falta_num_banco_com_a_008_antiga(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Teste de upgrade: aplicar até N-1, inserir dados, aplicar N e conferir.

    O banco de produção foi migrado com a 008 ANTIGA: `username` com `UNIQUE COLLATE NOCASE` e sem o índice sobre
    `lower(username)`. Aqui o estado antigo é reproduzido apagando o índice; o que se prova é que a 028 o recria e
    que os dados já existentes atravessam.
    """
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate="027_hospedeiro_e_vagas_de_ia")
    db = _banco(tmp_path)
    db.migrate()
    db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
               ("p1", "Fulano", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    db.execute("DROP INDEX ux_instagram_profiles_username")          # o esquema que produção tem hoje
    assert not _tem_indice(db, "ux_instagram_profiles_username")

    shutil.copy2(Path(db_mod.__file__).resolve().parents[1] / "migrations" / "028_convergencia_da_008.sql",
                 destino / "028_convergencia_da_008.sql")
    assert db.migrate() == ["028_convergencia_da_008"]
    assert _tem_indice(db, "ux_instagram_profiles_username")
    assert db.scalar("SELECT username FROM instagram_profiles WHERE id=?", ("p1",)) == "Fulano"
    # E o índice faz o que promete: unicidade sem diferenciar maiúsculas.
    from app.db import INTEGRITY_ERRORS

    with pytest.raises(INTEGRITY_ERRORS):
        db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
                   ("p2", "FULANO", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    db.close()


def _tem_indice(db: Database, nome: str) -> bool:
    if db.dialect == "sqlite":
        return db.one("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (nome,)) is not None
    return db.one("SELECT indexname FROM pg_indexes WHERE indexname=?"
                  " AND schemaname = ANY (current_schemas(false))", (nome,)) is not None


def test_tabelas_e_colunas_respondem_nos_dois_bancos(tmp_path: Path) -> None:
    """`tables()` existe para a varredura de senha em claro deixar de usar `sqlite_master` (achado #162)."""
    db = _banco(tmp_path)
    db.migrate()
    tabelas = db.tables()
    assert {"instagram_profiles", "secrets", "schema_migrations"} <= tabelas
    assert not any(t.startswith("sqlite_") for t in tabelas)
    assert {"ref", "key_id", "nonce", "ciphertext"} <= db.columns("secrets")
    db.close()


def test_a_conferencia_de_divergencia_nao_relê_os_arquivos_a_cada_chamada(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """`health()` roda num laço e chamava isto a cada volta: 2,7 ms medidos para reler, renderizar e hashear as 28
    migrações. Os arquivos não mudam durante a execução — mas um arquivo editado COM o processo no ar muda a
    assinatura, e o alarme continua aparecendo sem precisar reiniciar."""
    destino = _copia_das_migracoes(tmp_path, monkeypatch)
    db = _banco(tmp_path)
    db.migrate()
    lidos = {"n": 0}
    original = Path.read_text

    def contando(self: Path, *a: Any, **k: Any) -> str:
        if self.suffix == ".sql":
            lidos["n"] += 1
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", contando)
    assert db.divergencias() == [] and lidos["n"] > 0
    antes = lidos["n"]
    assert db.divergencias() == [] and lidos["n"] == antes        # segunda volta: nenhum arquivo relido
    alvo = destino / "008_instagram_domain.sql"
    alvo.write_text(alvo.read_text(encoding="utf-8") + "\n-- editada com o processo no ar\n", encoding="utf-8")
    assert db.divergencias() == ["008_instagram_domain"]          # e a edição é vista mesmo assim
    db.close()


def test_a_impressao_nao_muda_com_a_quebra_de_linha(lite: _Falso) -> None:
    """Com `core.autocrlf`, o mesmo commit chega como CRLF numa máquina e LF noutra. Sem normalizar, o backend A
    gravaria as impressões de um jeito e o backend B calcularia as do outro: TODAS as migrações apareceriam como
    alteradas, e o `/health` ficaria degradado num parque saudável — no cenário de dois backends, justamente."""
    assert lite._impressao("CREATE TABLE t (a INT);\r\nCREATE INDEX i ON t(a);\r\n") == \
           lite._impressao("CREATE TABLE t (a INT);\nCREATE INDEX i ON t(a);\n")
    assert lite._impressao("SELECT 1;") != lite._impressao("SELECT 2;")     # e o conteúdo ainda conta
