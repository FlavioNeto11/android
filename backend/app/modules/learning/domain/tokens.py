"""Teto de contexto das lições (ADR-054, decisão 5): quanto texto cabe no prompt de cada papel.

A estimativa é a mesma da memória da persona (`social/memory.py::CARACTERES_POR_TOKEN`), copiada e não importada: o
domínio não vê `app.social` (catraca de camadas). Erra para o lado seguro (superestima) em português. O que não cabe
fica de fora INTEIRO — lição cortada no meio muda o sentido — e é contado.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.modules.learning.domain.vocabulario import Papel

CARACTERES_POR_TOKEN = 3.6


@dataclass(frozen=True, slots=True)
class Teto:
    tokens: int
    itens: int
    caracteres_por_item: int | None = None


#: Os tetos de fábrica (o config `aprendizado.licoes.*` sobrepõe). Só ator e planejador recebem lição.
TETOS_DE_FABRICA: dict[Papel, Teto] = {
    Papel.ACTOR: Teto(tokens=120, itens=3, caracteres_por_item=240),
    Papel.PLANNER: Teto(tokens=150, itens=3),
}


def estimar_tokens(texto: str) -> int:
    return int(len(texto) / CARACTERES_POR_TOKEN) + 1 if texto else 0


@dataclass(frozen=True, slots=True)
class Selecao:
    escolhidos: tuple[str, ...]
    tokens: int
    cortados: int                 # quantos ficaram de fora pelo teto (métrica `licao.cortada`)


def caber(textos: Sequence[str], teto: Teto) -> Selecao:
    """Na ordem dada (a ordem é da seleção, não daqui), fica o que cabe em itens, tokens e caracteres por item."""
    escolhidos: list[str] = []
    usados = 0
    for texto in textos:
        if teto.caracteres_por_item is not None and len(texto) > teto.caracteres_por_item:
            continue
        custo = estimar_tokens(texto)
        if len(escolhidos) >= teto.itens or usados + custo > teto.tokens:
            continue
        escolhidos.append(texto)
        usados += custo
    return Selecao(tuple(escolhidos), usados, len(textos) - len(escolhidos))
