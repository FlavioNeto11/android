"""As costuras de gesto do aprendizado (ADR-054) que moram FORA da fila: o contrato, o no-op e o `avisar`.

Quem avisa daqui não conhece a fila nem o livro: o gerenciador de aparelhos (a tomada de controle) é do central, mas
`devices` não importa `taskqueue` — era a dívida aceita na integração do A2, quando o contrato morava em
`taskqueue/costuras.py`. O kernel é a raiz do grafo: qualquer pacote o enxerga, e ele não enxerga ninguém.

`taskqueue/costuras.py` reexporta tudo o que está aqui e estende o no-op (`SemCosturas`) com as costuras da fila;
quem cumpre as duas é o livro (`modules/learning/infrastructure/ligar_costuras.py`).

O que NUNCA passa por aqui: texto de tela, coordenada, credencial. A tomada de controle leva só os ids da etapa.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

log = logging.getLogger("poc.aprendizado")


@dataclass(frozen=True, slots=True)
class TomadaDeControle:
    """Uma pessoa pediu o aparelho enquanto a IA conduzia uma etapa. Sem árvore, texto nem coordenada."""

    instance_id: str
    run_id: str
    objective_id: str | None
    step_id: str


class CosturaDeControle(Protocol):
    """O que o gerenciador de aparelhos chama (a porta é dele: o gerenciador não conhece a fila nem o livro)."""

    def tomou_controle(self, tomada: TomadaDeControle) -> None: ...


class SemCosturasDeGesto:
    """O padrão das costuras de gesto: nenhum aviso — o comportamento de antes do ADR-054."""

    def tomou_controle(self, tomada: TomadaDeControle) -> None:
        return None


SEM_COSTURAS_DE_GESTO = SemCosturasDeGesto()

_T = TypeVar("_T")


def avisar(aviso: Callable[[_T], None], dado: _T) -> None:
    """Chama uma costura de aviso. A falha vira log: o aprendizado nunca derruba quem o avisou."""
    try:
        aviso(dado)
    except Exception:  # noqa: BLE001 - aprendizado é registro: nunca derruba a etapa, o gesto nem a execução
        log.exception("aprendizado: a costura %s falhou (a operação seguiu)", type(dado).__name__)


__all__ = ["SEM_COSTURAS_DE_GESTO", "CosturaDeControle", "SemCosturasDeGesto", "TomadaDeControle", "avisar"]
