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


def _irma(alias: str, posicao: str) -> str:
    """`EXISTS` de uma linha IRMÃ (mesmo item, mesma origem) na `posicao`: a marca que a reclassificação deixa ao lado."""
    return (f"EXISTS (SELECT 1 FROM learning_evidence f WHERE f.item_ref = {alias}.item_ref"
            f" AND f.origin_ref = {alias}.origin_ref AND f.stance = '{posicao}')")


def contra_efetivo(alias: str) -> str:
    """A condição SQL de `promocao.contrarias` sobre `learning_evidence AS <alias>` (30.36, 30.42): `against` ou
    `conflict`, menos o `against` que tem uma `forma` ou uma `invalida` da mesma origem no mesmo item (a linha que a
    reclassificação corrigiu). Os leitores em SQL usam esta, para não divergir do domínio."""
    a = alias
    return (f"({fora_da_reproducao(a)} AND ({a}.stance = 'conflict' OR ({a}.stance = 'against'"
            f" AND NOT {_irma(a, 'forma')} AND NOT {_irma(a, 'invalida')})))")


def favor_efetivo(alias: str) -> str:
    """A condição SQL do `for` que vale (30.42): fora da reprodução da receita e sem uma `invalida` da mesma origem no
    mesmo item (a prova que a reclassificação tirou do a favor). É o par de `contra_efetivo` e o de `promocao.efetivas`."""
    a = alias
    return f"({fora_da_reproducao(a)} AND {a}.stance = 'for' AND NOT {_irma(a, 'invalida')})"


def fora_da_reproducao(alias: str) -> str:
    """A condição SQL de `promocao.efetivas` para a reprodução da receita (30.39): a linha `reproducao:<run_id>` é o
    registro datado dos contadores e não conta de novo. Os leitores em SQL que contam a favor usam esta também."""
    return f"{alias}.origin_ref NOT LIKE 'reproducao:%'"


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
