"""31.182: o ensino avisa quando a etapa mira a conta da PRÓPRIA persona. Puro.

Medido em 06/10 (31.160): a pessoa ensinou a abrir o perfil da persona que ensinava, o 31.87 trocou o @ pelo marcador
`{conta_instagram_usuario}`, e a receita 221 virou "abrir o PRÓPRIO perfil": ela abre a conta de quem roda, em cada
aparelho, e não serve ao alvo de uma operação. Só avisa: abrir o próprio perfil pode ser o que a pessoa quis.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

#: O marcador da conta da persona num app (`conta_<app>[_<host>]_usuario[_N]`, `available_data.account_name`).
_DA_PROPRIA_CONTA = re.compile(r"\{(conta_\w+?_usuario(?:_\d+)?)\}")
#: O que a etapa diz, digita ou confere.
_CAMPOS = ("title", "goal", "bindings", "postcondition", "precondition")


def aviso(p: Mapping[str, object]) -> list[str]:
    """Uma linha por etapa da proposta que cita o marcador da conta da persona; sem valor, só a posição da etapa
    e o marcador."""
    passos = p.get("steps")
    saida: list[str] = []
    for n, st in enumerate(passos if isinstance(passos, list) else [], start=1):
        if not isinstance(st, dict):
            continue
        achados = sorted(set(_DA_PROPRIA_CONTA.findall(repr({k: st.get(k) for k in _CAMPOS}))))
        if achados:
            marcadores = ", ".join("{" + m + "}" for m in achados)
            # Pela posição, não pela `key`: a IA escolhe a chave a partir da tela, e ela pode trazer o nome.
            saida.append(f"A etapa {n} mira a conta da própria persona ({marcadores}): a receita dela "
                         "abre a conta de quem roda, em cada aparelho. Para um alvo da operação, ensine com um perfil "
                         "que não seja o da persona.")
    return saida


__all__ = ["aviso"]
