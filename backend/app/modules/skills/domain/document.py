"""O conteúdo de uma versão de habilidade como DADO: JSON tipado, forma canônica e hash.

Por que um serializador próprio: `db.dumps` não ordena as chaves (`db.py:545`), então o mesmo documento escrito em
outra ordem daria outro hash, e a conferência de integridade da versão congelada (ADR-034) acusaria adulteração
onde não houve. A forma canônica aqui é uma só: chaves ordenadas, separadores fixos, sem escape de não-ASCII e sem
NaN/Infinito (que não são JSON). O hash é o sha256 desse texto em UTF-8.

O documento chega sem schema: a validação contra `automation/v1alpha1` é de outra peça (porta `DocumentValidator`).
Aqui só se garante que é JSON de verdade — e cada leitura devolve uma CÓPIA, para que a versão congelada não possa
ser alterada por quem recebeu o dicionário.
"""
from __future__ import annotations

import hashlib
import json
import math

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class NotJson(ValueError):
    """O valor não é JSON representável (tipo estranho, chave que não é texto, NaN ou Infinito)."""


def as_json_value(value: object, *, where: str = "$") -> JsonValue:
    """Confere, recursivamente, que `value` é JSON, e devolve uma cópia profunda com listas e dicionários novos."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NotJson(f"{where}: número não finito não é JSON")
        return value
    if isinstance(value, (list, tuple)):
        return [as_json_value(item, where=f"{where}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        copia: JsonObject = {}
        for chave, item in value.items():
            if not isinstance(chave, str):
                raise NotJson(f"{where}: chave {chave!r} não é texto")
            copia[chave] = as_json_value(item, where=f"{where}.{chave}")
        return copia
    raise NotJson(f"{where}: {type(value).__name__} não é JSON")


def as_json_object(value: object, *, where: str = "$") -> JsonObject:
    copia = as_json_value(value, where=where)
    if not isinstance(copia, dict):
        raise NotJson(f"{where}: esperado um objeto JSON")
    return copia


def canonical_json(value: JsonValue) -> str:
    """A forma canônica: a MESMA para o mesmo conteúdo, qualquer que seja a ordem em que as chaves chegaram."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise NotJson(str(exc)) from exc


def parse_json_object(text: str) -> JsonObject:
    falha: str | None = None
    try:
        bruto = json.loads(text)
    except json.JSONDecodeError as exc:
        falha = exc.msg
    # 31.70 (G3): FORA do `except` e sem `from exc`; o `.doc` do `JSONDecodeError` é o texto inteiro.
    if falha is not None:
        raise NotJson(f"texto não é JSON: {falha}")
    return as_json_object(bruto)


def content_hash(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
