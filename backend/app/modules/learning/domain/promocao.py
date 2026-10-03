"""Validação por repetição (ADR-054, fluxo §4): quando a evidência de um item basta para `validated`.

Só evidência REAL promove (`simulated=0`): execução simulada serve a teste, nunca ao parque. As contagens que decidem
são por EXECUÇÃO e por APARELHO distintos — dez observações da mesma execução são uma repetição, não dez. Os limiares
são por tipo e vêm do config (`aprendizado.*`); aqui só a regra.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.vocabulario import Posicao


@dataclass(frozen=True, slots=True)
class Evidencia:
    """Uma linha de `learning_evidence`, já tipada."""

    item_ref: str
    stance: Posicao
    origin_ref: str
    run_id: str | None
    instance_id: str | None
    app_version: str | None
    simulated: bool
    detail: str | None
    observed_at: str


class Decisao(StrEnum):
    PROMOVE = "promove"            # candidate → validated
    ESPERA = "espera"              # ainda falta repetição (ver `faltam`)
    CONTRADITA = "contradita"      # contra ou conflito demais: candidate → disabled


@dataclass(frozen=True, slots=True)
class VereditoDeRepeticao:
    decisao: Decisao
    a_favor: int                   # observações reais a favor
    execucoes: int                 # execuções reais distintas a favor
    aparelhos: int                 # aparelhos reais distintos a favor
    contra: int                    # observações reais contra ou em conflito
    faltam: int                    # quanto falta no critério mais distante (0 quando promove)


@dataclass(frozen=True, slots=True)
class Limiares:
    n_min: int = 3
    execucoes_min: int = 2
    aparelhos_min: int = 1
    contra_max: int = 0


def efetivas(evidencias: Iterable[Evidencia]) -> list[Evidencia]:
    """As evidências que valem, na ordem recebida (30.36): o `against` que tem uma `forma` da MESMA origem no mesmo item
    sai — é a linha que a reclassificação corrigiu sem apagar (o log só cresce). A `forma` fica: não conta a favor nem
    contra, mas a trilha a mostra. Todo leitor que conta contra passa por aqui, para nenhum deles divergir."""
    lista = list(evidencias)
    formas = {(e.item_ref, e.origin_ref) for e in lista if e.stance is Posicao.FORMA}
    if not formas:
        return lista
    return [e for e in lista if not (e.stance is Posicao.AGAINST and (e.item_ref, e.origin_ref) in formas)]


def contrarias(evidencias: Iterable[Evidencia]) -> list[Evidencia]:
    """As que contam contra: `against` ou `conflict` efetivos (a `forma` nunca)."""
    return [e for e in efetivas(evidencias) if e.stance in (Posicao.AGAINST, Posicao.CONFLICT)]


def veredito_de_repeticao(evidencias: Iterable[Evidencia], limiares: Limiares = Limiares()) -> VereditoDeRepeticao:
    reais = [e for e in efetivas(evidencias) if not e.simulated]
    favor = [e for e in reais if e.stance is Posicao.FOR]
    contra = sum(1 for e in reais if e.stance in (Posicao.AGAINST, Posicao.CONFLICT))
    execucoes = len({e.run_id for e in favor if e.run_id})
    aparelhos = len({e.instance_id for e in favor if e.instance_id})
    if contra > limiares.contra_max:
        return VereditoDeRepeticao(Decisao.CONTRADITA, len(favor), execucoes, aparelhos, contra, 0)
    faltam = max(limiares.n_min - len(favor), limiares.execucoes_min - execucoes, limiares.aparelhos_min - aparelhos, 0)
    decisao = Decisao.PROMOVE if faltam == 0 else Decisao.ESPERA
    return VereditoDeRepeticao(decisao, len(favor), execucoes, aparelhos, contra, faltam)


def evidencia_decisiva(cronologicas: Sequence[Evidencia], decisao: Decisao,
                       limiares: Limiares = Limiares()) -> Evidencia | None:
    """A evidência que levou a repetição a `decisao`: a primeira, na ordem em que foram observadas, cujo prefixo já dá
    esse veredito — é a execução dela que a trilha leva (`run_id`). `None` quando nem todas juntas dão. A mesma regra de
    `veredito_de_repeticao`, prefixo a prefixo, para não divergir dela; a simulada nunca é a decisiva (não muda o
    veredito)."""
    for i in range(len(cronologicas)):
        if veredito_de_repeticao(cronologicas[:i + 1], limiares).decisao is decisao:
            return cronologicas[i]
    return None


@dataclass(frozen=True, slots=True)
class Contadores:
    """O cache de `learning_items` (evidence_for/against, distinct_runs/devices). Só evidência real."""

    a_favor: int
    contra: int
    execucoes: int
    aparelhos: int


def contadores(evidencias: Iterable[Evidencia]) -> Contadores:
    reais = [e for e in efetivas(evidencias) if not e.simulated]
    favor = [e for e in reais if e.stance is Posicao.FOR]
    return Contadores(a_favor=len(favor), contra=sum(1 for e in reais if e.stance in (Posicao.AGAINST, Posicao.CONFLICT)),
                      execucoes=len({e.run_id for e in favor if e.run_id}),
                      aparelhos=len({e.instance_id for e in favor if e.instance_id}))
