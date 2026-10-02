"""Modo por app (§8.10 do desenho do aprendizado vivo): a regra única de qual modo vale para um pacote.

`aprendizado.licoes.modo` e `aprendizado.telas.modo` são o padrão da instalação; `por_app` sobrescreve o de UM pacote.
O pacote é dado de instalação (config), nunca regra de código (ADR-052): nada aqui sabe o nome de app nenhum.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

M = TypeVar("M")


def modo_efetivo(global_: M, por_app: Mapping[str, M], pacote: str | None) -> M:
    """O modo que vale para `pacote`: o override dele, se houver; senão o global. Sem pacote (vazio ou `None`), o
    global. Quem chama decide o `enabled` (desligado vence tudo) ANTES de olhar aqui."""
    if not pacote:
        return global_
    return por_app.get(pacote, global_)


__all__ = ["modo_efetivo"]
