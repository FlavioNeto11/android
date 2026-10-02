"""Ponto de extensão do resumo por IA do relatório (docs/design/pedidos-persistentes.md §6.6). Só a forma.

O relatório DETERMINÍSTICO (`relatorio.py`) é a fonte da verdade e é gravado sempre. Um resumo em texto pelo papel `plan`
é opcional: DESLIGADO de fábrica (`pedidos.resumo_ia: false`), com teto por relatório (`resumo_ia_teto_usd`) e contado no
orçamento do pedido. Nesta entrega NENHUMA implementação paga existe: quem a escrever (28.6, orçamento) injeta um
`ResumidorDeRelatorio` no serviço; o que vem dele é guardado em `resumo_texto` ao lado do conteúdo, nunca no lugar dele, e
nunca entra em "conclusão".

Puro: stdlib.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResumoDeIA:
    texto: str
    papel: str               # o papel de IA que escreveu (`plan`): vai para `pedido_relatorios.resumo_por`
    custo_usd: float = 0.0


class ResumidorDeRelatorio(Protocol):
    def resumir(self, relatorio: Mapping[str, object], *, teto_usd: float) -> ResumoDeIA | None:
        """Resume o relatório (a estrutura de `relatorio.montar`) respeitando `teto_usd`. `None` = sem resumo."""
        ...


class SemResumo:
    """O padrão: nenhum resumo, nenhuma chamada. É o que o serviço usa quando `resumo_ia` está desligado."""

    def resumir(self, relatorio: Mapping[str, object], *, teto_usd: float) -> ResumoDeIA | None:
        return None
