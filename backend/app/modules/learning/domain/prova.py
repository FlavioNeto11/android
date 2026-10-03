"""A execução de prova que NÃO vale como evidência do fluxo (item 30.42, desenho aprovado pela orquestradora em 03/10).

A prova de fluxo (30.37) grava UMA linha de evidência do fluxo provado. Três desfechos não dizem nada sobre o fluxo e
viram a posição `invalida` (`Posicao.INVALIDA`): nem a favor nem contra, à vista na trilha, fora das contagens:

- `efeito_repetido`: o efeito saiu mais de uma vez dentro da prova (o caso da `5f2de5`, a mensagem enviada duas
  vezes, que tinha virado evidência a favor);
- `ponto_de_partida`: a etapa de abertura não chegou ao ponto de partida do fluxo (o caso da `e1b7d0`, o app dentro
  de uma conversa deixada pela execução anterior, que tinha virado evidência contra);
- `ator_sem_acao`: a etapa reprovou sem ação do ator além de `step_done` (o ator declarou pronto sem agir).

O motivo vai no começo do `detail` da linha, num formato fechado: o desfecho da validação o lê para fechar o pedido
com o motivo de mesmo nome (`domain/validacao.Motivo`), e o painel o mostra. Puro: sem banco, sem relógio.
"""
from __future__ import annotations

import re
from enum import StrEnum


class MotivoDaInvalida(StrEnum):
    """Por que a prova não vale. Vocabulário fechado: o mesmo valor é o `Motivo` do pedido de validação."""

    EFEITO_REPETIDO = "efeito_repetido"
    PONTO_DE_PARTIDA = "ponto_de_partida"
    ATOR_SEM_ACAO = "ator_sem_acao"


#: O começo do `detail` de uma linha `invalida`, depois da marca do conteúdo (`[xxxxxxxxxxxx] `) quando há.
PREFIXO = "invalida:"
_MOTIVO = re.compile(r"^(?:\[[0-9a-f]{6,}\]\s*)?invalida:([a-z_]+)")


def detalhe_da_invalida(motivo: MotivoDaInvalida, texto: str, *, marca: str | None = None) -> str:
    """O `detail` da linha: `[marca] invalida:<motivo> — <texto>`. O texto é técnico e curto (etapa, contagem), nunca
    texto de tela nem de pessoa; a coluna corta em 200 caracteres."""
    base = f"{PREFIXO}{motivo.value} — {texto}" if texto else f"{PREFIXO}{motivo.value}"
    return f"[{marca}] {base}" if marca else base


def motivo_da_invalida(detalhe: str | None) -> MotivoDaInvalida | None:
    """O motivo de um `detail` de linha `invalida`, ou `None` (outro texto, ou motivo fora do vocabulário)."""
    m = _MOTIVO.match(detalhe or "")
    if m is None:
        return None
    try:
        return MotivoDaInvalida(m.group(1))
    except ValueError:
        return None


__all__ = ["PREFIXO", "MotivoDaInvalida", "detalhe_da_invalida", "motivo_da_invalida"]
