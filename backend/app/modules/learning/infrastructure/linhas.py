"""Linha do banco → valor tipado. A `Row` (um `dict` sem tipo) não sai da infraestrutura do aprendizado.

Mesma disciplina de `skills/infrastructure/rows.py`: cada coluna é conferida no tipo ao sair, e um valor estranho vira
erro aqui, na borda — nunca um `None` que o domínio confundiria com "não informado".
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Sequence

from app.db import Row
from app.modules.skills.domain.document import JsonObject, JsonValue, NotJson, as_json_value, parse_json_object

#: Tamanho do lote de `IN (...)` (abaixo do limite de variáveis do SQLite).
LOTE = 400


def contra_efetivo(alias: str) -> str:
    """A condição SQL de `promocao.contrarias` sobre `learning_evidence AS <alias>` (30.36): `against` ou `conflict`,
    menos o `against` que tem uma `forma` da mesma origem no mesmo item (a linha que a reclassificação corrigiu). Os
    leitores em SQL usam esta, para não divergir do domínio."""
    a = alias
    return (f"({a}.stance = 'conflict' OR ({a}.stance = 'against' AND NOT EXISTS (SELECT 1 FROM learning_evidence f"
            f" WHERE f.item_ref = {a}.item_ref AND f.origin_ref = {a}.origin_ref AND f.stance = 'forma')))")


def lotes[T](itens: Sequence[T]) -> Iterator[Sequence[T]]:
    for i in range(0, len(itens), LOTE):
        yield itens[i:i + LOTE]


def marcas(n: int) -> str:
    """`?,?,?` para um `IN (...)` de `n` valores."""
    return ",".join("?" for _ in range(n))


def texto(row: Row, coluna: str) -> str:
    valor = row[coluna]
    if not isinstance(valor, str):
        raise TypeError(f"coluna {coluna}: esperado texto, veio {type(valor).__name__}")
    return valor


def texto_ou_nulo(row: Row, coluna: str) -> str | None:
    valor = row[coluna]
    if valor is None or isinstance(valor, str):
        return valor
    raise TypeError(f"coluna {coluna}: esperado texto ou nulo, veio {type(valor).__name__}")


def inteiro(row: Row, coluna: str) -> int:
    valor = row[coluna]
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise TypeError(f"coluna {coluna}: esperado inteiro, veio {type(valor).__name__}")
    return valor


def inteiro_ou_nulo(row: Row, coluna: str) -> int | None:
    return None if row[coluna] is None else inteiro(row, coluna)


def real(row: Row, coluna: str) -> float:
    """REAL/SUM: o SQLite devolve `int` quando a soma é inteira; o PostgreSQL pode devolver `Decimal` num agregado."""
    valor = row[coluna]
    if valor is None:
        return 0.0
    if isinstance(valor, bool):
        raise TypeError(f"coluna {coluna}: esperado número, veio bool")
    try:
        return float(valor)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"coluna {coluna}: esperado número, veio {type(valor).__name__}") from exc


def json_objeto(row: Row, coluna: str) -> JsonObject:
    bruto = texto_ou_nulo(row, coluna)
    if not bruto:
        return {}
    try:
        return parse_json_object(bruto)
    except NotJson:
        return {}


def json_legado(texto: str | None) -> JsonValue:
    """JSON de coluna legada (`recipes.actions`, `flows.plan`), gravado por `db.dumps`; ilegível é `None` (e nada é
    presumido dele: sem efeito externo, sem hash)."""
    if not texto:
        return None
    try:
        return as_json_value(json.loads(texto))
    except (ValueError, NotJson):
        return None
