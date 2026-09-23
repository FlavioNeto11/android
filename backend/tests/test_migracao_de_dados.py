"""Item 5.4 / achado #34 — a ferramenta que leva os DADOS de um banco ao outro, com conferência.

O esquema já nascia igual nos dois bancos; os dados não atravessavam. Sem isto, adotar o PostgreSQL significava
perder meses de histórico, receitas, fluxos, perfis e memória social — ou copiar na mão, sem conferir nada.

Por que o teste roda SQLite -> SQLite quando `TEST_DATABASE_URL` não está definida: o que a ferramenta tem de
acertar é a REGRA — ordem de chave estrangeira, quais tabelas ficam de fora, quais colunas não se escrevem,
contagem e impressão digital por tabela, e as duas recusas. Tudo isso é dialeto-neutro e vale nos dois. Com a
suíte apontada para o PostgreSQL, este mesmo arquivo migra de verdade para lá, com sequências e coluna gerada.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.tools.migrate_data import (Recusa, copiar, impressao_da_tabela, main, ordem_por_fk, tabelas_a_copiar)

from .conftest import _dsn_de_teste

AGORA = "2026-09-23T10:00:00Z"


def semear(db: Database) -> None:
    """Dados com as três formas que importam: cadeia de chave estrangeira, chave gerada pelo banco (`seq`) e a
    memória social, cujo índice de busca é DERIVADO (FTS5 no SQLite, coluna gerada no PostgreSQL)."""
    for i in (1, 2):
        # Uma persona por perfil: `instagram_profiles.persona_id` é UNIQUE (o vínculo é 1 para 1).
        db.execute("INSERT INTO personas(id, name, summary, created_at, updated_at) VALUES (?,?,?,?,?)",
                   (f"per-{i}", f"Mariana {i}", "gosta de São Paulo", AGORA, AGORA))
        db.execute("INSERT INTO instagram_profiles(id, username, persona_id, created_at, updated_at)"
                   " VALUES (?,?,?,?,?)", (f"prof-{i}", f"perfil.teste{i}", f"per-{i}", AGORA, AGORA))
    db.execute("INSERT INTO secrets(ref, key_id, nonce, ciphertext, created_at, updated_at) VALUES (?,?,?,?,?,?)",
               ("sec-1", "memoria-v1:aabbccdd", b"\x00\x01\x02nonce12", b"\xff\xfe ciphertext", AGORA, AGORA))
    db.execute("INSERT INTO instagram_credentials(profile_id, login_identifier, secret_ref, key_id, created_at,"
               " updated_at) VALUES (?,?,?,?,?,?)",
               ("prof-1", "perfil.teste1", "sec-1", "memoria-v1:aabbccdd", AGORA, AGORA))
    for i in range(1, 4):
        db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, importance, occurrences,"
                   " fingerprint, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (f"mem-{i}", "prof-1", "@amigo", f"mora em São Paulo, fato {i}", "operator", 0.75, i,
                    f"fp-{i}", AGORA, AGORA))


@pytest.fixture
def origem(tmp_path: Path) -> Database:
    db = Database(tmp_path / "origem.sqlite3")
    db.migrate()
    semear(db)
    return db


@pytest.fixture
def destino(tmp_path: Path) -> Database:
    return Database(_dsn_de_teste() or tmp_path / "destino.sqlite3")


def test_o_que_se_copia_deixa_de_fora_o_derivado(origem: Database, destino: Database) -> None:
    """`schema_migrations` é do destino (ele se migrou sozinho, com as impressões digitais dele) e o índice de
    busca é derivado: copiar a tabela-sombra do FTS5 escreveria por cima do que os gatilhos acabaram de montar,
    com o formato interno de OUTRA versão do SQLite."""
    destino.migrate()
    tabelas = tabelas_a_copiar(origem, destino)
    assert "instagram_profiles" in tabelas and "memory_items" in tabelas
    assert "schema_migrations" not in tabelas
    assert not any(t.startswith("memory_fts") for t in tabelas)
    origem.close()
    destino.close()


def test_a_ordem_poe_o_pai_antes_do_filho(origem: Database, destino: Database) -> None:
    """No PostgreSQL a chave estrangeira é conferida a cada instrução: inserir um filho antes do pai é ERRO."""
    destino.migrate()
    ordem = ordem_por_fk(origem, tabelas_a_copiar(origem, destino))
    assert ordem.index("personas") < ordem.index("instagram_profiles")
    assert ordem.index("instagram_profiles") < ordem.index("instagram_credentials")
    assert ordem.index("instagram_profiles") < ordem.index("memory_items")
    assert ordem.index("secrets") < ordem.index("instagram_credentials")
    origem.close()
    destino.close()


def test_a_copia_confere_contagem_e_impressao_digital_por_tabela(origem: Database, destino: Database) -> None:
    """Contagem sozinha não pega valor truncado, `NULL` virado string nem byte perdido na travessia — e é
    justamente o ciphertext do cofre (BLOB no SQLite, BYTEA no PostgreSQL) que corre esse risco."""
    destino.migrate()
    relatorio = copiar(origem, destino, aplicar=True)
    assert relatorio["divergencias"] == []
    assert all(item["conferido"] for item in relatorio["tabelas"])
    copiadas = {i["tabela"]: i["linhas"] for i in relatorio["tabelas"] if i["linhas"]}
    assert copiadas == {"personas": 2, "instagram_profiles": 2, "secrets": 1, "instagram_credentials": 1,
                        "memory_items": 3}

    # E o conteúdo chegou mesmo: os bytes do cofre, o acento e o número quebrado.
    assert destino.scalar("SELECT ciphertext FROM secrets WHERE ref=?", ("sec-1",)) is not None
    assert bytes(destino.scalar("SELECT nonce FROM secrets WHERE ref=?", ("sec-1",))) == b"\x00\x01\x02nonce12"
    assert "São Paulo" in destino.scalar("SELECT content FROM memory_items WHERE id=?", ("mem-1",))
    assert destino.scalar("SELECT importance FROM memory_items WHERE id=?", ("mem-1",)) == 0.75
    origem.close()
    destino.close()


def test_o_indice_de_busca_e_reconstruido_no_destino(origem: Database, destino: Database) -> None:
    """A prova de que NÃO copiar o derivado foi a escolha certa: o destino monta o próprio índice a partir dos
    dados que chegaram, e a busca sem acento responde — que é o que a 017 existe para garantir."""
    destino.migrate()
    copiar(origem, destino, aplicar=True)
    if destino.dialect == "sqlite":
        achados = destino.query("SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", ("sao",))
    else:
        achados = destino.query(
            "SELECT seq FROM memory_items WHERE busca @@ plainto_tsquery('simple', sem_acento(?))", ("sao",))
    assert len(achados) == 3
    origem.close()
    destino.close()


def test_conferir_nao_grava_nada(origem: Database, destino: Database) -> None:
    destino.migrate()
    relatorio = copiar(origem, destino, aplicar=False)
    assert sum(i["linhas"] for i in relatorio["tabelas"]) == 9
    assert destino.scalar("SELECT COUNT(*) FROM instagram_profiles") == 0
    origem.close()
    destino.close()


def test_recusa_origem_atrasada(origem: Database, destino: Database) -> None:
    """Um banco atrasado deixaria de fora as colunas que as migrações novas criaram — e a cópia pareceria ter
    dado certo. É o estado real da produção de hoje, que parou na 015 enquanto o código seguiu."""
    destino.migrate()
    ultima = origem.scalar("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
    origem.execute("DELETE FROM schema_migrations WHERE version=?", (ultima,))
    with pytest.raises(Recusa, match="Migre a origem primeiro"):
        copiar(origem, destino, aplicar=True)
    assert destino.scalar("SELECT COUNT(*) FROM personas") == 0
    origem.close()
    destino.close()


def test_recusa_destino_com_dados(origem: Database, destino: Database) -> None:
    """Copiar por cima duplicaria histórico em silêncio: as chaves de `events` e `ai_calls` são geradas pelo
    banco, então nada colidiria — só apareceria o dobro de tudo."""
    destino.migrate()
    destino.execute("INSERT INTO personas(id, name, created_at, updated_at) VALUES (?,?,?,?)",
                    ("ja-existia", "Alguém", AGORA, AGORA))
    with pytest.raises(Recusa, match="já tem dados"):
        copiar(origem, destino, aplicar=True)
    origem.close()
    destino.close()


def test_impressao_digital_nao_depende_da_ordem_de_leitura(origem: Database, destino: Database) -> None:
    """Nenhum dos dois bancos promete ordem de leitura sem `ORDER BY`. Se a impressão dependesse dela, toda
    migração "divergiria" e a conferência não valeria nada."""
    destino.migrate()
    copiar(origem, destino, aplicar=True)
    colunas = ["id", "subject", "content", "importance", "occurrences"]
    n1, h1 = impressao_da_tabela(origem, "memory_items", colunas)
    n2, h2 = impressao_da_tabela(destino, "memory_items", colunas)
    assert (n1, h1) == (n2, h2)
    # Um único valor diferente muda a impressão — senão ela não estaria conferindo nada.
    destino.execute("UPDATE memory_items SET occurrences = occurrences + 1 WHERE id=?", ("mem-1",))
    assert impressao_da_tabela(destino, "memory_items", colunas)[1] != h1
    origem.close()
    destino.close()


def test_linha_de_comando_de_ponta_a_ponta(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """O caminho que o dono vai rodar de verdade, com os argumentos que a documentação promete."""
    fonte = Database(tmp_path / "fonte.sqlite3")
    fonte.migrate()
    semear(fonte)
    fonte.close()
    alvo = _dsn_de_teste() or str(tmp_path / "alvo.sqlite3")

    assert main(["--de", str(tmp_path / "fonte.sqlite3"), "--para", alvo, "--conferir"]) == 0
    assert "Nada foi gravado" in capsys.readouterr().out

    assert main(["--de", str(tmp_path / "fonte.sqlite3"), "--para", alvo]) == 0
    saida = capsys.readouterr().out
    assert "9 linhas copiadas e conferidas" in saida
    assert "CREDENTIALS_MASTER_KEY" in saida                  # o aviso sobre o cofre não é opcional

    # Rodar de novo é recusado, não duplicado.
    assert main(["--de", str(tmp_path / "fonte.sqlite3"), "--para", alvo]) == 2
    assert "RECUSADO" in capsys.readouterr().err


def test_precisao_simples_do_postgres_nao_e_confundida_com_perda_de_dado() -> None:
    """`REAL` no PostgreSQL e precisao SIMPLES; no SQLite, dupla. `memory_items.importance` e `REAL` nos dois, e
    0.7 gravado no SQLite volta do PostgreSQL como 0.699999988079071. Sem estreitar os dois lados antes de
    comparar, a ferramenta gritaria DIVERGE por uma perda de precisao que e do ESQUEMA, nao da copia — e mandaria
    o dono nao usar um destino perfeitamente bom."""
    from app.tools.migrate_data import _normaliza

    assert _normaliza(0.7) == _normaliza(0.699999988079071)
    assert _normaliza(0.75) == _normaliza(0.75)
    assert _normaliza(0.7) != _normaliza(0.8)                  # e valor de verdade diferente continua diferente
    assert _normaliza(None) != _normaliza("") != _normaliza(0)
