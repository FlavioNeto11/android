"""Regras puras da exclusão a pedido do titular (29.83, ADR-075): como o telefone se compara e o que se aceita do pedido.

A comparação é por TODOS os dígitos que o operador informou, com DDD: "os últimos 8" casava números de outros DDDs
(revisão do procedimento manual, X2). O `55` do país é opcional dos dois lados: o visitante pode ter escrito
`+55 (11) …` e o operador `11 …`, ou o contrário.
"""
from __future__ import annotations

#: Por onde o pedido chegou. Sem texto livre: texto livre vira lugar de dado pessoal (decisão da orquestradora, 04/10).
PEDIDO_POR = frozenset({"formulario", "telefone", "outro"})

#: Quantos contatos uma exclusão aceita de uma vez: o pedido de uma pessoa, não uma faxina.
IDS_MAX = 50

#: Quantos dígitos o final mostrado na lista tem: desempata homônimos sem expor o número.
DIGITOS_DO_FINAL = 4

_PAIS = "55"


def digitos(texto: str) -> str:
    return "".join(c for c in texto if c.isascii() and c.isdigit())


def nacional(texto: str) -> str | None:
    """Os dígitos do número sem o `55` do país (DDD + número, 10 ou 11 dígitos), ou `None` se não é um telefone
    brasileiro completo. Aceita de 10 a 13 dígitos na entrada."""
    d = digitos(texto)
    if len(d) in (12, 13) and d.startswith(_PAIS):
        d = d[len(_PAIS):]
    return d if len(d) in (10, 11) else None


def mesmo_telefone(informado: str, guardado: str) -> bool:
    """Os dois números são o mesmo, dígito a dígito, com DDD. Telefone guardado vazio (o descarte apaga) nunca casa."""
    a, b = nacional(informado), nacional(guardado)
    return a is not None and a == b


def final(telefone: str) -> str:
    return digitos(telefone)[-DIGITOS_DO_FINAL:]
