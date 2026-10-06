"""Geometria pura dos gestos gravados (31.114): de onde um arraste saiu. Sem I/O, sem driver, sem estado."""
from __future__ import annotations

#: Até quanto da largura ou da altura, a partir de uma borda, o começo do arraste conta como "saiu da borda" (a gaveta de
#: notificações e os gestos de borda começam colados nela).
FRACAO_DA_BORDA = 0.03


def borda_de_saida(x: int, y: int, largura: int, altura: int) -> str | None:
    """A borda da tela de onde o dedo saiu (`superior`, `inferior`, `esquerda`, `direita`), ou `None` se saiu do meio."""
    for nome, valor, tamanho, inicio in (("superior", y, altura, True), ("inferior", y, altura, False),
                                         ("esquerda", x, largura, True), ("direita", x, largura, False)):
        if (inicio and valor <= FRACAO_DA_BORDA * tamanho) or (not inicio and valor >= (1 - FRACAO_DA_BORDA) * tamanho):
            return nome
    return None
