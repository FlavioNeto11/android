"""31.183: a proposta do ensino para EXIBIR, com o dado da persona mascarado. Puro.

A revisão de segredos do K-107 deixou um médio: o título, o objetivo e o resumo da proposta só trocam o dado que a pessoa
DIGITOU inteiro (31.87), e um @ ou um nome visto na tela e citado pela IA ("Abrir o perfil de ana_lopes") sai em claro
no `GET /api/training/{id}`. A proposta guardada não pode mudar: o painel a devolve na prévia e no salvar, e o marcador
no título faria a etapa mirar a persona de cada aparelho. Então a leitura ganha uma CÓPIA só para exibir,
`proposal_exibicao`, com todo dado da persona trocado pelo marcador; a `proposal` segue igual.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

#: Abaixo disto o valor casaria com pedaço de outra palavra: o mesmo piso de `dado_da_persona.MINIMO`.
MINIMO = 3
#: Campos que são identificadores, não texto mostrado: ficam como estão.
_IDENTIFICADORES = frozenset({"key", "capability", "app_id", "kind", "name", "inputs", "seq", "independente",
                              "side_effect", "commit_guard", "depends_on"})


def _mascarar(texto: str, persona: Mapping[str, str]) -> str:
    """Por palavra, sem diferença de caixa, também logo depois de um @, com qualquer espaço entre as partes do valor; o
    mais longo primeiro. Limite: casa o valor inteiro (o primeiro nome sozinho de "Ana Lopes" fica)."""
    for nome, valor in sorted(persona.items(), key=lambda kv: -len(kv[1].strip())):
        v = valor.strip().lstrip("@")
        if len(v) >= MINIMO:
            padrao = r"\s+".join(re.escape(parte) for parte in v.split())
            texto = re.sub(r"(?<![\w.])" + padrao + r"(?![\w@])", "{" + nome + "}", texto, flags=re.IGNORECASE)
    return texto


def _em(valor: object, persona: Mapping[str, str]) -> object:
    if isinstance(valor, str):
        return _mascarar(valor, persona)
    if isinstance(valor, list):
        return [_em(v, persona) for v in valor]
    if isinstance(valor, dict):
        return {k: v if k in _IDENTIFICADORES else _em(v, persona) for k, v in valor.items()}
    return valor


def proposta(p: object, persona: Mapping[str, str]) -> object:
    """A cópia da proposta para exibir; sem proposta ou sem persona, a mesma (cópia rasa não é preciso: nada muda)."""
    validos = {n: v for n, v in persona.items() if isinstance(v, str) and len(v.strip().lstrip("@")) >= MINIMO}
    if not isinstance(p, dict) or not validos:
        return p
    return _em(p, validos)


__all__ = ["proposta"]
