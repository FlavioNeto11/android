#!/usr/bin/env python3
"""Confere um banco RESTAURADO por `restore.ps1` (ensaio): integridade, última migração, tabelas e, se pedido, a migração
do código ATUAL aplicada NA CÓPIA (29.167). Só abre o arquivo que recebe; nunca o banco do ambiente central.

    python scripts/restore-ensaio-confere.py <poc.sqlite3 restaurado> [--backend <pasta backend>] [--migrar]

Imprime uma linha de JSON: `integridade`, `migracao_da_copia`, `tabelas`, `migracao_do_codigo` (o último arquivo de
`backend/migrations`), `migracoes_aplicadas_na_copia` (só com `--migrar`) e `erro` quando a conferência não terminou.
A saída é 0 mesmo com `erro` preenchido: quem decide o veredito é o `restore-ensaio.ps1`. Nenhum valor de tabela é lido.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def conferir(banco: Path, backend: Path | None, migrar: bool) -> dict:
    out: dict = {"integridade": None, "migracao_da_copia": None, "tabelas": None, "migracao_do_codigo": None,
                 "migracoes_aplicadas_na_copia": [], "erro": None}
    if backend is not None and (backend / "migrations").is_dir():
        sql = sorted(p.stem for p in (backend / "migrations").glob("*.sql"))
        out["migracao_do_codigo"] = sql[-1] if sql else None
    try:
        con = sqlite3.connect(f"file:{banco.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            out["integridade"] = con.execute("PRAGMA integrity_check").fetchone()[0]
            out["tabelas"] = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            linha = con.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            out["migracao_da_copia"] = linha[0] if linha else None
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        out["erro"] = f"{type(e).__name__} ao abrir a cópia"
        return out
    if migrar and out["integridade"] == "ok":
        if backend is None:
            out["erro"] = "sem --backend: não há como aplicar a migração do código na cópia"
            return out
        sys.path.insert(0, str(backend))
        try:
            from app.db import Database  # noqa: PLC0415 - só aqui, com o backend desta árvore

            aplicadas = Database(str(banco)).migrate()
            out["migracoes_aplicadas_na_copia"] = [str(x) for x in (aplicadas or [])]
        except Exception as e:  # noqa: BLE001 - o veredito é do chamador; aqui só o nome do erro, nunca o texto
            out["erro"] = f"{type(e).__name__} ao migrar a cópia"
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print("uso: restore-ensaio-confere.py <poc.sqlite3> [--backend <pasta>] [--migrar]", file=sys.stderr)
        return 2
    banco = Path(argv[0])
    backend = Path(argv[argv.index("--backend") + 1]) if "--backend" in argv else None
    print(json.dumps(conferir(banco, backend, "--migrar" in argv), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
