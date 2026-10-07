"""31.231: a pesquisa da operação reaproveita os fatos do Livro do mesmo assunto. Puro: sem banco.

A pesquisa paga (31.158) roda uma vez por operação com assunto: US$ 0,043 na onda 1. Os fatos confirmados que ela deixa
viram itens do Livro com o assunto no escopo (31.190, 31.200) e a proveniência v1.117 (frescor, domínios das fontes).
A 2ª operação do MESMO assunto pagava de novo pelo que o Livro já sabia.

"Cobrir o pedido" é um critério explícito, e a decisão sai com ele por extenso (nunca um pulo silencioso):
    1. pelo menos `min_fatos` fatos do Livro do assunto (o canônico do 31.200), vivos (`candidate`, `validated` ou
       `published`; o rejeitado e o desligado ficam fora);
    2. todos com confiança "confirmado" (só o fato confirmado nasce no Livro, `fatos_da_operacao.elegivel`) e dentro do
       frescor (`frescor_ate` > agora; sem frescor, não conta);
    3. se a operação indicou fontes, cada domínio indicado está entre os domínios das fontes desses fatos.
Não cobre: a pesquisa paga roda como hoje, com o motivo no log.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

#: Os estados de item do Livro que contam como fato vivo.
ESTADOS_VIVOS = ("candidate", "validated", "published")


@dataclass(frozen=True, slots=True)
class FatoDoLivro:
    ref: str                          # o id do item do Livro
    texto: str
    estado: str
    frescor_ate: str | None
    dominios: tuple[str, ...]
    operacao_de_origem: str


@dataclass(frozen=True, slots=True)
class Cobertura:
    cobre: bool
    usados: tuple[FatoDoLivro, ...]
    motivo: str                       # o critério por extenso: vai ao registro (cobre) ou ao log (não cobre)

    @property
    def frescor_ate(self) -> str | None:
        """O menor frescor dos fatos usados: o reaproveitamento vale até ele."""
        return min((f.frescor_ate for f in self.usados if f.frescor_ate), default=None)


def dominio(url: str) -> str:
    """A mesma régua do 31.190 (`fatos_da_operacao_sql._dominios`)."""
    return urlparse(url).netloc.lower().removeprefix("www.")


def cobertura(fatos: Sequence[FatoDoLivro], *, fontes_indicadas: Sequence[str], agora: str,
              min_fatos: int) -> Cobertura:
    if min_fatos <= 0:
        return Cobertura(False, (), "reaproveitamento desligado (ai.pesquisa.reaproveitar_min_fatos = 0)")
    validos = tuple(f for f in fatos if f.estado in ESTADOS_VIVOS and f.frescor_ate and f.frescor_ate > agora)
    criterio = f"≥{min_fatos} fato(s) confirmado(s) do assunto, vivos e dentro do frescor"
    if len(validos) < min_fatos:
        return Cobertura(False, (), f"{len(validos)} de {min_fatos} fato(s) válido(s) no Livro ({criterio})")
    indicados = {d for d in (dominio(u) for u in fontes_indicadas) if d}
    if indicados:
        cobertos = {d for f in validos for d in f.dominios}
        faltam = sorted(indicados - cobertos)
        if faltam:
            return Cobertura(False, (), f"fonte(s) indicada(s) sem fato no Livro: {', '.join(faltam)}")
        criterio += ", fontes indicadas cobertas"
    return Cobertura(True, validos, criterio)


__all__ = ["ESTADOS_VIVOS", "Cobertura", "FatoDoLivro", "cobertura", "dominio"]
