"""O gravador do `resultado_posterior` (item 30.35), um passo da curadoria periódica: a regra está em
`domain/resultado_posterior.py`.

A cada passo, as revisões REAIS e válidas do curador sobre receita e lição cuja janela de 14 dias já fechou, e que
ainda não têm o campo, ganham o desfecho medido. É uma gravação só por revisão: o `UPDATE` é condicionado a
`resultado_posterior IS NULL`. Sem IA, sem aparelho. Quem lê o campo é o relatório do 31.10 (o rótulo 2 do golden set).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.modules.learning.domain.resultado_posterior import (JANELA_DIAS, FatosDaJanela, MudancaNaJanela,
                                                             fim_da_janela, resultado)
from app.modules.learning.domain.saude import LimiaresDeSaude
from app.util import parse_iso, to_iso

#: Quantas revisões um passo grava, no máximo: o resto fica para o passo seguinte.
POR_PASSO = 500


@dataclass(frozen=True, slots=True)
class RevisaoSemDesfecho:
    id: str
    item_ref: str
    item_kind: str
    criada_em: str


class FontesDoResultadoPosterior(Protocol):
    def abertas(self, criadas_ate: str, limite: int) -> list[RevisaoSemDesfecho]: ...
    def mudancas(self, item_ref: str, desde: str, ate: str) -> list[MudancaNaJanela]: ...
    def usos(self, item_ref: str, item_kind: str, desde: str, ate: str) -> list[bool]: ...
    def gravar(self, review_id: str, valor: str, em: str) -> bool: ...    # CAS: só a linha ainda sem o campo


class GravadorDoResultadoPosterior:
    nome = "resultado_posterior"

    def __init__(self, fontes: FontesDoResultadoPosterior, limiares: Callable[[], LimiaresDeSaude]) -> None:
        self._fontes = fontes
        self._limiares = limiares

    def executar(self, agora: datetime) -> int:
        """Grava o desfecho das revisões com a janela fechada. Devolve quantas gravou."""
        limiares = self._limiares()
        gravadas = 0
        # A janela de quem foi criada até `agora - 14 d` já fechou.
        for r in self._fontes.abertas(to_iso(agora - timedelta(days=JANELA_DIAS)), POR_PASSO):
            inicio = parse_iso(r.criada_em)
            if inicio is None:
                continue
            desde, ate = to_iso(inicio), to_iso(fim_da_janela(inicio))
            fatos = FatosDaJanela(mudancas=tuple(self._fontes.mudancas(r.item_ref, desde, ate)),
                                  usos=tuple(self._fontes.usos(r.item_ref, r.item_kind, desde, ate)))
            gravadas += int(self._fontes.gravar(r.id, resultado(fatos, limiares).value, to_iso(agora)))
        return gravadas


__all__ = ["POR_PASSO", "FontesDoResultadoPosterior", "GravadorDoResultadoPosterior", "RevisaoSemDesfecho"]
