"""31.114 F2: o arraste que termina uma etapa e que a PESSOA confirmou como o objetivo dela vira receita.

Hoje o arraste só vale como dica "rolar até o alvo aparecer" da PRÓXIMA ação com alvo; no fim da etapa a receita nascia
recusada ("rolagem sem ação-alvo depois dela"). A receita repete a rolagem (item `scroll`, relativo à área rolável, nunca
pixel absoluto) só quando as três coisas valem: o arraste termina a etapa, a pessoa respondeu "sim" à pergunta fixa abaixo e
a tela do aparelho era conhecida e o dedo NÃO saiu da borda (gesto de borda, como abrir as notificações, é do sistema: o
`scroll` do conteúdo não o reproduz, e a tool de borda, o F3, está fora até haver demanda).

Puro: sem banco, sem aparelho. A leitura da tela e o guardar da resposta são do `propose` e do `salvar`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..automation.gestos import borda_de_saida
from .respostas import Resposta, chave_da_pergunta

_SIM = {"sim", "s", "yes", "y", "claro", "isso", "isso mesmo"}


def _n(e: Mapping[str, object], campo: str) -> int | None:
    valor = e.get(campo)
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def arrastes_finais(entradas: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Os arrastes com coordenada que vêm DEPOIS da última entrada que não é arraste (a cauda da etapa), na ordem gravada."""
    cauda: list[Mapping[str, object]] = []
    for e in entradas:
        if e.get("type") == "swipe":
            cauda.append(e)
        else:
            cauda = []
    return [e for e in cauda if _n(e, "y") is not None and _n(e, "y2") is not None]


def pode_ser_receita(arrastes: list[Mapping[str, object]], tela: tuple[int, int] | None) -> bool:
    """Há arraste na cauda, a tela do aparelho se sabia na proposta e nenhum deles saiu da borda."""
    if not arrastes or tela is None:
        return False
    return all(_n(e, "x") is not None and _n(e, "y") is not None
               and borda_de_saida(_n(e, "x") or 0, _n(e, "y") or 0, tela[0], tela[1]) is None for e in arrastes)


def pergunta(chave_da_etapa: str) -> str:
    """O texto é FIXO por etapa (só a chave da etapa entra): a resposta guardada tem de casar com ele em outra proposta."""
    return (f"A etapa «{chave_da_etapa}» termina num arraste. O arraste é o objetivo dela, para a receita repetir a rolagem? "
            "Responda sim ou não.")


def confirmou(respostas: list[Resposta], chave_da_etapa: str) -> bool:
    alvo = chave_da_pergunta(pergunta(chave_da_etapa))
    return any(chave_da_pergunta(r["question"]) == alvo and r["answer"].strip().casefold().rstrip(".!") in _SIM
               for r in respostas)
