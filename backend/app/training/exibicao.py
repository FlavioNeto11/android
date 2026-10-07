"""31.183: a proposta do ensino para EXIBIR, com o dado da persona mascarado. Puro.

A revisão de segredos do K-107 deixou um médio: o título, o objetivo e o resumo da proposta só trocam o dado que a
pessoa DIGITOU inteiro (31.87), e um @ ou um nome visto na tela e citado pela IA ("Abrir o perfil de ana_lopes") sai em claro
no `GET /api/training/{id}`. A proposta guardada não pode mudar: o painel a devolve na prévia e no salvar, e o marcador
no título faria a etapa mirar a persona de cada aparelho. Então a leitura ganha uma CÓPIA só para exibir,
`proposal_exibicao`, com todo dado da persona trocado pelo marcador; a `proposal` segue igual.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

#: Abaixo disto o valor casaria com pedaço de outra palavra: o mesmo piso de `dado_da_persona.MINIMO`.
MINIMO = 3
#: Campos que são identificadores, não texto mostrado: ficam como estão. A `key` da etapa NÃO está aqui: a IA a escolhe
#: a partir da tela e ela pode trazer o nome ("abrir_perfil_ana_lopes"); na cópia de exibir ela é mascarada também.
_IDENTIFICADORES = frozenset({"capability", "app_id", "kind", "name", "inputs", "seq", "independente",
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


def _na_chave(chave: str, persona: Mapping[str, str]) -> str:
    """A `key` da etapa é snake_case ("abrir_perfil_ana_lopes"): o valor vira o slug dele, e o `_` conta como
    separador (no texto, `_` é parte da palavra)."""
    for nome, valor in sorted(persona.items(), key=lambda kv: -len(kv[1].strip())):
        v = re.sub(r"[^0-9a-z]+", "_", valor.strip().lstrip("@").casefold()).strip("_")
        if len(v) >= MINIMO:
            chave = re.sub(r"(?<![0-9a-z])" + re.escape(v) + r"(?![0-9a-z])", "{" + nome + "}", chave,
                           flags=re.IGNORECASE)
    return chave


def _em(valor: object, persona: Mapping[str, str], campo: str = "") -> object:
    if campo == "key" and isinstance(valor, str):
        return _na_chave(valor, persona)
    if isinstance(valor, str):
        return _mascarar(valor, persona)
    if isinstance(valor, list):
        return [_em(v, persona) for v in valor]
    if isinstance(valor, dict):
        return {k: v if k in _IDENTIFICADORES else _em(v, persona, k) for k, v in valor.items()}
    return valor


def proposta(p: object, persona: Mapping[str, str]) -> object:
    """A cópia da proposta para exibir; sem proposta ou sem persona, a mesma (cópia rasa não é preciso: nada muda)."""
    validos = {n: v for n, v in persona.items() if isinstance(v, str) and len(v.strip().lstrip("@")) >= MINIMO}
    if not isinstance(p, dict) or not validos:
        return p
    return _em(p, validos)


def relatorio(etapas: object, persona: Mapping[str, str]) -> object:
    """O relatório por etapa da prévia, do salvar e do refazer receitas (`steps[]`) com o título e o motivo mascarados
    (achado da Portal no 31.189: o título vinha em claro). É só exibição: o painel não o devolve. A `key` e o resto
    ficam."""
    validos = {n: v for n, v in persona.items() if isinstance(v, str) and len(v.strip().lstrip("@")) >= MINIMO}
    if not isinstance(etapas, list) or not validos:
        return etapas
    return [{**e, **{k: _mascarar(e[k], validos) for k in ("title", "reason") if isinstance(e.get(k), str)}}
            if isinstance(e, dict) else e for e in etapas]


def avisos(lista: object, persona: Mapping[str, str]) -> object:
    """Os `warnings` da prévia, do salvar e do refazer receitas, mascarados (achado da Portal no 31.182): vários citam a
    etapa pelo título ou, sem título, pela `key` ("A etapa “abrir_perfil_ana_lopes” …"), e a IA escolhe os dois a partir
    da tela. O texto e a forma de chave são mascarados; o que não é texto fica."""
    validos = {n: v for n, v in persona.items() if isinstance(v, str) and len(v.strip().lstrip("@")) >= MINIMO}
    if not isinstance(lista, list) or not validos:
        return lista
    return [_na_chave(_mascarar(a, validos), validos) if isinstance(a, str) else a for a in lista]


__all__ = ["avisos", "proposta", "relatorio"]
