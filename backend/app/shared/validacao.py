"""O lugar de cada erro de uma `ValidationError` do pydantic SEM o que o modelo escreveu (31.63, 31.70).

Duas funções faziam isto em separado: `planning/provider.erro_de_validacao_sem_entrada` (31.63) e
`modules/execution/domain/command_refinement.motivo_sem_valor` (31.70). O domínio não pode importar `app.planning`
(regra de CAMADAS do `test_arquitetura`), e as duas divergiam no `loc`: a do 31.63 deixava passar a chave de um `dict` e a
do `extra_forbidden`, que são texto do modelo. Aqui fica a regra única, no kernel, visível às duas.

De cada erro: o `type` (código fechado do pydantic, nunca o `msg`, que já vem formatado e carrega o valor no
`value_error` de um validador e na tag do `union_tag_invalid`) e, do `loc`, só índice e nome de campo do esquema
(com `alias` e `validation_alias`). O resto vira `?`: é seguro e perde informação de propósito.
"""
from __future__ import annotations

import typing

from pydantic import AliasChoices, BaseModel, ValidationError


def nomes_de_campo(modelo: type[BaseModel]) -> frozenset[str]:
    """Os nomes de campo do `modelo` e dos modelos aninhados nele (`list[X]`, `X | None`, `dict[str, X]`), por
    recursão, com o `alias` e o `validation_alias` de texto de cada campo."""
    nomes: set[str] = set()
    vistos: set[type[BaseModel]] = set()
    pendentes: list[type[BaseModel]] = [modelo]
    while pendentes:
        atual = pendentes.pop()
        if atual in vistos:
            continue
        vistos.add(atual)
        for nome, campo in atual.model_fields.items():
            nomes.add(nome)
            if isinstance(campo.alias, str):
                nomes.add(campo.alias)
            apelido = campo.validation_alias
            if isinstance(apelido, str):
                nomes.add(apelido)
            elif isinstance(apelido, AliasChoices):
                nomes.update(c for c in apelido.choices if isinstance(c, str))
            tipos = [campo.annotation]
            while tipos:
                t = tipos.pop()
                if isinstance(t, type) and issubclass(t, BaseModel):
                    pendentes.append(t)
                tipos.extend(typing.get_args(t))
    return frozenset(nomes)


def lugar_sem_valor(loc: tuple[int | str, ...], campos: frozenset[str]) -> str:
    return ".".join(str(p) if isinstance(p, int) or p in campos else "?" for p in loc) or "(raiz)"


def erros_sem_valor(exc: ValidationError, modelo: type[BaseModel]) -> list[str]:
    """`lugar: type` de cada erro, na ordem do pydantic."""
    campos = nomes_de_campo(modelo)
    return [f"{lugar_sem_valor(tuple(e['loc']), campos)}: {e['type']}"
            for e in exc.errors(include_input=False, include_url=False, include_context=False)]


__all__ = ["erros_sem_valor", "lugar_sem_valor", "nomes_de_campo"]
