"""Cópia CONSISTENTE de um SQLite que pode estar sendo escrito agora.

Chamado por `scripts/backup.ps1`. Existe como arquivo próprio, e não como um `-c "..."` dentro do PowerShell,
porque caminho do Windows dentro de aspas dentro de aspas é como um backup vira um arquivo de zero byte.

**Por que não `Copy-Item`.** O banco roda em WAL (`db.py`: `PRAGMA journal_mode=WAL`), então o estado atual é o
`.sqlite3` **mais** o `-wal` **mais** o `-shm`. Copiar só o `.sqlite3` com o backend no ar produz um arquivo sem as
últimas transações — ou pior, um arquivo que abre, parece íntegro e está velho. Copiar os três à mão também não
serve: eles são lidos em momentos diferentes e a cópia sai de um instante que nunca existiu.

`sqlite3.Connection.backup()` é a API de backup online do próprio SQLite: ela lê páginas sob a trava certa e
reinicia a cópia se alguém escrever no meio. O resultado é UM arquivo, já com o WAL incorporado, consistente num
instante real — com o backend no ar, sem parar nada e sem travar quem escreve.

Uso:
    python scripts/sqlite-copia.py <origem.sqlite3> <destino.sqlite3>

Imprime uma linha JSON com o que dá para conferir sem abrir o banco: páginas copiadas, `integrity_check` e a
última migração aplicada. Nada de conteúdo — este script nunca lê linha de tabela.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def copiar(origem: Path, destino: Path) -> dict:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()
    # `mode=ro` na origem: um backup nunca deve ser capaz de escrever no banco de produção, nem por engano de
    # digitação nos argumentos. E `immutable` NÃO serve aqui — o arquivo está sendo escrito.
    uri = "file:" + origem.resolve().as_posix() + "?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        dst = sqlite3.connect(str(destino))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # Conferência em leitura E escrita, de propósito: a cópia herda `journal_mode=WAL` da origem, e uma conexão
    # SOMENTE LEITURA não pode fazer checkpoint nem apagar o `-wal` ao fechar. O backup sairia com um `-wal` e um
    # `-shm` pendurados ao lado — e a pessoa que restaurasse teria de adivinhar se eles importam. Com escrita, o
    # `wal_checkpoint(TRUNCATE)` e o fechamento limpo deixam UM arquivo, que é o contrato desta função.
    conf = sqlite3.connect(str(destino))
    try:
        integridade = conf.execute("PRAGMA integrity_check").fetchone()[0]
        try:
            migracao = conf.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            migracao = migracao[0] if migracao else None
        except sqlite3.DatabaseError:
            migracao = None
        tabelas = conf.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        conf.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conf.close()
    for sufixo in ("-wal", "-shm"):
        vizinho = destino.with_name(destino.name + sufixo)
        if vizinho.exists() and vizinho.stat().st_size == 0:
            vizinho.unlink()
    return {"origem": str(origem), "destino": str(destino), "bytes": destino.stat().st_size,
            "integrity_check": integridade, "migration": migracao, "tables": tabelas}


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: sqlite-copia.py <origem> <destino>", file=sys.stderr)
        return 2
    origem, destino = Path(sys.argv[1]), Path(sys.argv[2])
    if not origem.exists():
        print(json.dumps({"erro": f"origem não existe: {origem}"}), file=sys.stderr)
        return 1
    resultado = copiar(origem, destino)
    print(json.dumps(resultado, ensure_ascii=False))
    return 0 if resultado["integrity_check"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
