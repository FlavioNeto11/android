#!/usr/bin/env python3
"""Conta, por aparelho, os avisos de pressão de CPU do convidado desde um instante (29.156, fatia 1).

    python scripts/amostrador-host-pressao.py <poc.sqlite3> <desde AAAA-MM-DDTHH:MM:SS (UTC)>

Imprime `android-05:3;android-01:1` (aparelho:contagem, do maior para o menor), `0` se mediu e não há aviso, ou linha VAZIA
se não conseguiu medir. Chamado a cada minuto pelo
`amostrador-host.ps1`. Abre o banco SÓ para leitura (`mode=ro`), lê apenas `instance_id` e a contagem dos eventos
`instance.updated` cuja mensagem é o aviso de pressão; nenhum texto de mensagem, nenhuma linha de outra tabela. Qualquer erro
vira linha vazia e código 0: o amostrador nunca pode parar por causa do banco, e a coluna vazia significa "não medido"
(diferente de `0`, que é "medido: nenhum aviso").
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

AVISO = "%press%o de CPU%"  # "pressão": o `%` cobre o til/acentuação sem depender da codificação do console


def contar(banco: Path, desde: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", desde):
        return ""
    con = sqlite3.connect(f"file:{banco.resolve().as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        linhas = con.execute(
            "SELECT instance_id, COUNT(*) AS n FROM events WHERE kind = 'instance.updated' AND ts >= ? "
            "AND message LIKE ? AND instance_id IS NOT NULL GROUP BY instance_id ORDER BY n DESC, instance_id",
            (desde, AVISO),
        ).fetchall()
    finally:
        con.close()
    return ";".join(f"{i}:{n}" for i, n in linhas) or "0"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("")
        return 0
    try:
        print(contar(Path(argv[0]), argv[1]))
    except Exception:  # noqa: BLE001 - coluna vazia = não medido; o amostrador segue
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
