"""Copia os DADOS de um banco para outro — SQLite -> PostgreSQL — com conferência.

    python -m app.tools.migrate_data --de data/poc.sqlite3 --para postgresql://usuario:senha@host:5432/farm
    python -m app.tools.migrate_data --de data/copia.sqlite3 --para postgresql://... --conferir

Por que existe (achado #34). O ESQUEMA já nasce igual nos dois bancos: a mesma migração roda nos dois. Os DADOS
não atravessavam. Sem esta ferramenta, adotar o PostgreSQL significava perder meses de histórico, receitas,
fluxos, perfis, memória social e estado de release — ou copiar na mão, tabela a tabela, sem conferir nada. Era o
que impedia, na prática, a seção 3 de sair do papel.

**Como usar, na ordem certa:**

1. Pare o backend (`scripts/stop.ps1`). Copiar um banco que está sendo escrito copia um instante que nunca
   existiu — e o SQLite roda em WAL, então nem o arquivo sozinho basta.
2. Tire uma cópia consistente: `python scripts/sqlite-copia.py data/poc.sqlite3 data/copia.sqlite3`. Migre a
   partir da CÓPIA: assim o banco de produção nunca é aberto para escrita e uma migração interrompida não custa
   nada além do tempo.
3. `--conferir` primeiro: ele percorre tudo e relata o que faria, sem gravar.
4. `--de <cópia> --para <dsn>`; depois aponte `DATABASE_URL` para o destino e suba o backend.

**O que a ferramenta confere, e é por isso que ela existe em vez de um `INSERT … SELECT`:**

- a origem está na ÚLTIMA migração (um banco atrasado copiaria colunas que o destino não tem — ou pior, deixaria
  de copiar as que ele tem);
- o destino está migrado e VAZIO (copiar por cima de dados existentes duplicaria histórico em silêncio);
- ordem de chave estrangeira, tirada do próprio esquema da origem (não de uma lista escrita à mão que envelhece);
- contagem **e impressão digital** por tabela, comparadas no fim. Contagem sozinha não pega valor truncado,
  `NULL` virado string nem byte perdido na travessia.

**O que NÃO atravessa: as senhas do cofre.** O ciphertext é copiado como qualquer outro dado, mas a chave mestra
fica FORA do banco e, no Windows, embrulhada por DPAPI — presa a este usuário e a esta máquina. Se o backend que
vai usar o PostgreSQL for outro, ou rodar como outro usuário, há duas saídas: adotar `CREDENTIALS_MASTER_KEY`
(mesma chave nos dois, ver `.env.example`) e rodar `python -m app.security.rekey --aplicar` antes de migrar, ou
recadastrar as credenciais pelo portal depois. A ferramenta avisa qual é o caso ao terminar.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from ..db import MIGRATIONS_DIR, Database

#: Tabelas que NÃO se copiam. `schema_migrations` é do destino (ele já se migrou sozinho, com as impressões
#: digitais dele). As virtuais e suas tabelas-sombra são derivadas: o FTS5 do SQLite é reconstruído pelos
#: gatilhos quando `memory_items` chega, e no PostgreSQL o equivalente é uma coluna gerada.
NAO_COPIAR = {"schema_migrations"}


class Recusa(RuntimeError):
    """Uma pré-condição não foi satisfeita. Nada foi gravado."""


# ------------------------------------------------------------------ o que copiar
def _virtuais_e_sombras(db: Database) -> set[str]:
    if db.dialect != "sqlite":
        return set()
    virtuais = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE ?", ("CREATE VIRTUAL%",))}
    todas = db.tables()
    # As sombras do FTS5 (`_data`, `_idx`, `_content`, `_docsize`, `_config`) são tabelas comuns no catálogo:
    # copiá-las escreveria por cima do índice que os gatilhos acabaram de montar, com o formato interno de OUTRA
    # versão do SQLite. O índice é derivado — reconstruí-lo é o certo, copiá-lo é frágil.
    return virtuais | {t for t in todas for v in virtuais if t.startswith(f"{v}_")}


def tabelas_a_copiar(origem: Database, destino: Database) -> list[str]:
    comuns = (origem.tables() & destino.tables()) - NAO_COPIAR
    return sorted(comuns - _virtuais_e_sombras(origem) - _virtuais_e_sombras(destino))


def _dependencias(origem: Database, tabelas: set[str]) -> dict[str, set[str]]:
    """Quem cada tabela precisa que exista ANTES dela. Lido do esquema, nunca de uma lista escrita à mão."""
    dep: dict[str, set[str]] = defaultdict(set)
    if origem.dialect == "sqlite":
        for t in tabelas:
            # `PRAGMA` não aceita marcador; o nome vem do catálogo do próprio banco, nunca de entrada externa.
            for r in origem.query(f"PRAGMA foreign_key_list({t})"):     # noqa: S608
                if r["table"] in tabelas and r["table"] != t:
                    dep[t].add(r["table"])
        return dep
    for r in origem.query(
            "SELECT tc.table_name AS filho, ccu.table_name AS pai"
            " FROM information_schema.table_constraints tc"
            " JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name"
            " AND ccu.table_schema = tc.table_schema"
            " WHERE tc.constraint_type = 'FOREIGN KEY'"
            " AND tc.table_schema = ANY (current_schemas(false))"):
        if r["filho"] in tabelas and r["pai"] in tabelas and r["filho"] != r["pai"]:
            dep[r["filho"]].add(r["pai"])
    return dep


def ordem_por_fk(origem: Database, tabelas: Iterable[str]) -> list[str]:
    """Pais antes dos filhos. No PostgreSQL a chave estrangeira é conferida a cada instrução (as deste esquema não
    são `DEFERRABLE`), então inserir um filho antes do pai é erro — não um aviso."""
    restantes, dep = set(tabelas), _dependencias(origem, set(tabelas))
    ordem: list[str] = []
    while restantes:
        prontas = sorted(t for t in restantes if not (dep[t] & restantes))
        if not prontas:
            # Ciclo de chave estrangeira: não existe hoje neste esquema, e se passar a existir a ordem alfabética
            # das que sobraram vai falhar ALTO, na inserção, em vez de copiar pela metade em silêncio.
            ordem.extend(sorted(restantes))
            break
        ordem.extend(prontas)
        restantes -= set(prontas)
    return ordem


def colunas_gravaveis(db: Database, tabela: str) -> set[str]:
    """Colunas em que dá para INSERIR. Exclui as geradas pelo banco — `memory_items.busca` é um `tsvector`
    calculado pelo PostgreSQL a partir de `subject`/`content`, e tentar escrevê-lo é erro."""
    if db.dialect == "sqlite":
        return db.columns(tabela)       # `PRAGMA table_info` já omite coluna gerada
    return {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name=?"
        " AND table_schema = ANY (current_schemas(false)) AND is_generated <> 'ALWAYS'", (tabela,))}


# ------------------------------------------------------------------ conferência
def _normaliza(valor: Any) -> str:
    """Um valor, um texto — igual nos dois bancos.

    A impressão digital tem de comparar CONTEÚDO, não representação do driver: o mesmo `1` volta como `int` do
    SQLite e como `bool` do psycopg quando a coluna é booleana, e o mesmo carimbo de tempo volta como `str` de um
    e como `datetime` do outro. Sem normalizar, toda tabela "divergiria" e a conferência não valeria nada.
    """
    if valor is None:
        return "\x00"
    if isinstance(valor, bool):
        return "b1" if valor else "b0"
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return "x" + bytes(valor).hex()
    if isinstance(valor, (int,)):
        return f"i{valor}"
    if isinstance(valor, float):
        # `REAL` no PostgreSQL é precisão SIMPLES (float4); no SQLite, dupla. As colunas `importance` e
        # `confidence` de `memory_items` são `REAL` nos dois — então 0.75 atravessa igual, mas 0.7 volta do
        # PostgreSQL como 0.699999988079071. Comparar o `repr` faria a ferramenta gritar DIVERGE por uma perda de
        # precisão que é do ESQUEMA, não da cópia. Os dois lados passam pelo mesmo estreitamento antes de comparar.
        return f"f{struct.unpack('<f', struct.pack('<f', valor))[0]!r}"
    if isinstance(valor, Decimal):
        return f"i{int(valor)}" if valor == valor.to_integral_value() else f"f{float(valor)!r}"
    if isinstance(valor, (datetime, date)):
        return "t" + valor.isoformat()
    return "s" + str(valor)


def impressao_da_tabela(db: Database, tabela: str, colunas: list[str]) -> tuple[int, str]:
    """Quantas linhas e qual a impressão digital do conteúdo, independente de ORDEM.

    Por que independente de ordem: sem `ORDER BY` nenhum dos dois bancos promete ordem de leitura, e ordenar por
    uma chave que nem toda tabela tem exigiria um caso especial por tabela. Cada linha vira um hash; os hashes são
    ordenados; o hash dos hashes é a impressão. Duas tabelas com o mesmo conteúdo em qualquer ordem coincidem.
    """
    lista = ", ".join(f'"{c}"' for c in colunas)
    hashes = []
    for row in db.query(f'SELECT {lista} FROM "{tabela}"'):             # noqa: S608 - nomes vêm do esquema
        texto = "\x1f".join(_normaliza(row[c]) for c in colunas)
        hashes.append(hashlib.sha256(texto.encode("utf-8")).digest())
    hashes.sort()
    h = hashlib.sha256()
    for item in hashes:
        h.update(item)
    return len(hashes), h.hexdigest()


# ------------------------------------------------------------------ pré-condições
def _ultima_migracao_no_disco() -> str:
    return sorted(f.stem for f in MIGRATIONS_DIR.glob("*.sql"))[-1]


def conferir_precondicoes(origem: Database, destino: Database, tabelas: list[str]) -> None:
    esperada = _ultima_migracao_no_disco()
    na_origem = origem.scalar("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
    if na_origem != esperada:
        raise Recusa(
            f"A origem está em {na_origem or 'nenhuma migração'} e o código está em {esperada}. Migre a origem "
            "primeiro (suba o backend uma vez apontado para ela, ou rode a migração) e repita. Copiar um banco "
            "atrasado deixaria de fora as colunas que as migrações novas criaram.")
    cheias = [t for t in tabelas if (destino.scalar(f'SELECT COUNT(*) FROM "{t}"') or 0) > 0]   # noqa: S608
    if cheias:
        raise Recusa(
            "O destino já tem dados em: " + ", ".join(cheias) + ". Esta ferramenta copia para um banco VAZIO — "
            "copiar por cima duplicaria histórico em silêncio. Use um banco novo (ou limpe este de propósito).")


# ------------------------------------------------------------------ a cópia
def copiar(origem: Database, destino: Database, *, aplicar: bool) -> dict[str, Any]:
    tabelas = ordem_por_fk(origem, tabelas_a_copiar(origem, destino))
    conferir_precondicoes(origem, destino, tabelas)
    relatorio: dict[str, Any] = {"ordem": tabelas, "tabelas": [], "divergencias": [], "puladas": []}

    for t in tabelas:
        colunas = sorted(colunas_gravaveis(origem, t) & colunas_gravaveis(destino, t))
        so_na_origem = sorted(colunas_gravaveis(origem, t) - set(colunas))
        if not colunas:
            relatorio["puladas"].append(t)
            continue
        lista = ", ".join(f'"{c}"' for c in colunas)
        marcadores = ", ".join("?" for _ in colunas)
        linhas = origem.query(f'SELECT {lista} FROM "{t}"')             # noqa: S608 - nomes vêm do esquema
        if aplicar:
            # Uma transação por TABELA, e não uma para tudo: 62 mil eventos numa transação só é um `undo` gigante
            # e um bloqueio longo do lado do PostgreSQL, sem ganho — se a cópia falhar no meio, a recuperação é
            # recomeçar num destino vazio de qualquer jeito, e a conferência do fim é quem diz se deu certo.
            with destino.tx():
                for linha in linhas:
                    destino.execute(f'INSERT INTO "{t}" ({lista}) VALUES ({marcadores})',   # noqa: S608
                                    tuple(linha[c] for c in colunas))
        relatorio["tabelas"].append({"tabela": t, "linhas": len(linhas), "colunas": len(colunas),
                                     "colunas_so_na_origem": so_na_origem})

    if aplicar:
        _ajustar_sequences(destino)
        for item in relatorio["tabelas"]:
            t = item["tabela"]
            colunas = sorted(colunas_gravaveis(origem, t) & colunas_gravaveis(destino, t))
            n_o, h_o = impressao_da_tabela(origem, t, colunas)
            n_d, h_d = impressao_da_tabela(destino, t, colunas)
            item.update({"conferido": n_o == n_d and h_o == h_d, "linhas_destino": n_d})
            if not item["conferido"]:
                relatorio["divergencias"].append(
                    f"{t}: origem {n_o} linhas / {h_o[:12]}, destino {n_d} linhas / {h_d[:12]}")
    return relatorio


def _ajustar_sequences(destino: Database) -> None:
    """Reposiciona as sequências de identidade do PostgreSQL.

    As chaves foram copiadas com o valor que tinham; a sequência, porém, continua no 1. Sem isto, a primeira
    inserção depois da migração pediria o id 1 — que já existe — e o sistema quebraria com violação de unicidade
    na primeira execução, longe daqui. As sequências são descobertas pelo `column_default`, não por uma lista.
    """
    if destino.dialect != "postgres":
        return
    for r in destino.query(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = ANY (current_schemas(false)) AND column_default LIKE ?", ("nextval%",)):
        t, c = r["table_name"], r["column_name"]
        maximo = destino.scalar(f'SELECT MAX("{c}") FROM "{t}"')        # noqa: S608 - nomes vêm do esquema
        if maximo is None:
            continue
        destino.execute("SELECT setval(pg_get_serial_sequence(?, ?), ?)", (t, c, int(maximo)))


# ------------------------------------------------------------------ linha de comando
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m app.tools.migrate_data",
        description="Copia os dados de um banco para outro, com conferência por tabela.")
    p.add_argument("--de", required=True, help="origem: caminho de um .sqlite3 (de preferência uma CÓPIA) ou DSN")
    p.add_argument("--para", required=True, help="destino: postgresql://usuario:senha@host:porta/base")
    p.add_argument("--conferir", action="store_true",
                   help="não grava: confere as pré-condições e relata o que copiaria")
    args = p.parse_args(argv)

    origem, destino = Database(args.de), Database(args.para)
    try:
        aplicadas = destino.migrate()                 # o destino monta o próprio esquema antes de receber dado
        if aplicadas:
            print(f"destino migrado: {len(aplicadas)} migrações aplicadas (até {aplicadas[-1]}).")
        try:
            relatorio = copiar(origem, destino, aplicar=not args.conferir)
        except Recusa as r:
            print(f"RECUSADO: {r}", file=sys.stderr)
            return 2
        largura = max((len(i["tabela"]) for i in relatorio["tabelas"]), default=10)
        for item in relatorio["tabelas"]:
            marca = "" if args.conferir else ("  ok" if item["conferido"] else "  DIVERGE")
            print(f"  {item['tabela']:<{largura}}  {item['linhas']:>7} linhas{marca}")
            if item["colunas_so_na_origem"]:
                print(f"      colunas sem par no destino (NÃO copiadas): {', '.join(item['colunas_so_na_origem'])}")
        total = sum(i["linhas"] for i in relatorio["tabelas"])
        if args.conferir:
            print(f"\nCONFERÊNCIA: {total} linhas em {len(relatorio['tabelas'])} tabelas seriam copiadas. "
                  "Nada foi gravado.")
            return 0
        if relatorio["divergencias"]:
            print("\nDIVERGÊNCIA — o destino NÃO confere com a origem:", file=sys.stderr)
            for d in relatorio["divergencias"]:
                print(f"  {d}", file=sys.stderr)
            print("Não aponte DATABASE_URL para este destino. Recomece num banco vazio.", file=sys.stderr)
            return 1
        print(f"\n{total} linhas copiadas e conferidas (contagem e impressão digital) em "
              f"{len(relatorio['tabelas'])} tabelas.")
        print("As SENHAS do cofre NÃO atravessam sozinhas: a chave mestra fica fora do banco e, no Windows, presa "
              "a este usuário e a esta máquina. Se o backend do PostgreSQL for outro, adote CREDENTIALS_MASTER_KEY "
              "nos dois e rode `python -m app.security.rekey --aplicar`, ou recadastre as credenciais pelo portal.")
        return 0
    finally:
        origem.close()
        destino.close()


if __name__ == "__main__":                  # pragma: no cover - ponto de entrada
    sys.exit(main())
