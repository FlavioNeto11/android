"""31.181: o alcance por persona, quem pode usar o quê hoje num app. Puro: sem banco.

Medido em 06/10 (leitura da onda 2): das 19 receitas ativas do Instagram, 18 nasceram de execução e valem para as três
personas com conta; a do ensino (30.81) vale só para quem ensinou, e o fluxo ensinado tem escopo de uma persona. O Livro
e a Portal não diziam isso: adivinhavam pelo rótulo dos alvos. Agora a resposta é por item e por persona, com o motivo do
"não" num vocabulário fechado. A regra de cada "não" é a do código que decide na execução (`RecipeStore` e
`FlowStore`), recebida por quem monta: aqui só se junta.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


class MotivoDoNao(StrEnum):
    PRESA_A_QUEM_ENSINOU = "presa_a_quem_ensinou"   # 30.81: sem "Confirmar que fica" nem prova real do fluxo
    FORA_DO_ESCOPO = "fora_do_escopo"               # o escopo do fluxo (personas ou grupos) não a inclui
    FLUXO_NAO_LIGADO = "fluxo_nao_ligado"           # candidato ou desligado: nenhuma persona o usa


@dataclass(frozen=True, slots=True)
class Veredito:
    pode: bool
    motivo: MotivoDoNao | None = None

    def como_dict(self) -> dict[str, object]:
        return {"pode": self.pode, "motivo": self.motivo.value if self.motivo else None}


@dataclass(frozen=True, slots=True)
class ItemDoAlcance:
    tipo: str                                       # "receita" ou "fluxo"
    id: str
    chave: str                                      # a etapa da receita; o `ref_publico` (ou id) do fluxo
    origem: str                                     # "ensino" ou "execucao"
    estado: str
    detalhe: Mapping[str, object]                   # reproduções da receita; usos e marca de prova do fluxo
    por_persona: Mapping[str, Veredito]

    def como_dict(self) -> dict[str, object]:
        return {"tipo": self.tipo, "id": self.id, "chave": self.chave, "origem": self.origem, "estado": self.estado,
                **self.detalhe, "por_persona": {p: v.como_dict() for p, v in sorted(self.por_persona.items())}}


def veredito_da_receita(presa: bool) -> Veredito:
    return Veredito(False, MotivoDoNao.PRESA_A_QUEM_ENSINOU) if presa else Veredito(True)


def veredito_do_fluxo(estado: str, no_escopo: bool) -> Veredito:
    if estado != "active":
        return Veredito(False, MotivoDoNao.FLUXO_NAO_LIGADO)
    return Veredito(True) if no_escopo else Veredito(False, MotivoDoNao.FORA_DO_ESCOPO)


def resumo(itens: Sequence[ItemDoAlcance], personas: Sequence[str]) -> dict[str, dict[str, int]]:
    """Por persona: quantas receitas e fluxos ela pode usar."""
    return {p: {t: sum(1 for i in itens if i.tipo == t and i.por_persona[p].pode) for t in ("receita", "fluxo")}
            for p in personas}


__all__ = ["ItemDoAlcance", "MotivoDoNao", "Veredito", "resumo", "veredito_da_receita", "veredito_do_fluxo"]
