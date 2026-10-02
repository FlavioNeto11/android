"""Identificadores explícitos de uma pergunta (regra híbrida v1, passo 1). Puro, só `re`.

Explícito = trecho entre crases (>= 3 caracteres) OU snake_case OU CamelCase (>= 2 partes) OU dotted.path, os três
últimos com >= 4 caracteres no total. É o sinal de que a pessoa nomeou um símbolo, e a busca lexical vale mais que a
semântica para ele. Ideia validada no piloto (`claude/jev-pilot:experiments/jev/hybrid.py`), reescrita aqui.
"""
from __future__ import annotations

import hashlib
import re

MIN_IDENT_LEN = 4

_TICK = re.compile(r"`([^`]{3,})`")
_SNAKE = re.compile(r"(?<![\w.])_*[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\w])")
_LEAD_UNDERSCORE = re.compile(r"(?<![\w])_[A-Za-z][A-Za-z0-9_]{2,}")
_CAMEL = re.compile(r"(?<![\w])(?:[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+|[a-z]+(?:[A-Z][a-z0-9]+)+)(?![\w])")
_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w+(?:\.[A-Za-z_]\w+)+(?![\w])")


def explicit_identifiers(query: str) -> list[str]:
    """Na ordem em que aparecem, sem repetir (ignorando maiúsculas)."""
    found = [m.group(1).strip() for m in _TICK.finditer(query)]
    resto = _TICK.sub(" ", query)
    for rx in (_SNAKE, _LEAD_UNDERSCORE, _CAMEL, _DOTTED):
        found += [m.group(0) for m in rx.finditer(resto) if len(m.group(0)) >= MIN_IDENT_LEN]
    vistos: set[str] = set()
    saida: list[str] = []
    for f in found:
        if f and f.lower() not in vistos:
            vistos.add(f.lower())
            saida.append(f)
    return saida


def query_fingerprint(query: str) -> str:
    """Impressão digital curta da pergunta: conta repetição nas métricas sem guardar o texto."""
    normal = re.sub(r"\s+", " ", query.strip().lower())
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:12]
