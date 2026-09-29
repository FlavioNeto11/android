"""Repetição de etapas sobre uma lista lida da tela.

Uma etapa de coleta (pós-condição `items_collected`) devolve os itens; as etapas marcadas com `for_each=<chave da
coleta>` são um MODELO: aqui viram uma cópia por item. `{item}` só é resolvido na materialização — as cópias guardam
`variables` e `template_key`, para que todas compartilhem a mesma receita. Função pura: serve tanto para a expansão
logo após a coleta quanto para a recuperação/retomada, que precisam enxergar o mesmo plano.
"""
from __future__ import annotations

import re

from ..models import PlanStep
from .saidas import renomear_para_o_item

ITEM_MAX_CHARS = 80


def sanitize_item(text: str) -> str:
    """Item lido da tela é DADO: uma linha, sem chaves de template nem caracteres de controle, tamanho limitado."""
    clean = re.sub(r"[\x00-\x1f\x7f{}]", " ", text or "")
    return re.sub(r"\s+", " ", clean).strip()[:ITEM_MAX_CHARS]


def _copy_key(key: str, n: int) -> str:
    suffix = f"_i{n}"
    return key[:41 - len(suffix)] + suffix


def expand(steps: list[PlanStep], collected: dict[str, list[str]]) -> list[PlanStep]:
    """Plano-modelo → plano efetivo. Coletas já feitas somem (não se repetem) e seus blocos `for_each` são copiados
    item a item, em ordem de item. Blocos de coletas ainda não feitas ficam como estão. Não há dependência ENTRE
    itens: a ordem vem de `seq`, e um item que falha não trava os seguintes."""
    by_key = {s.key: s for s in steps}
    out: list[PlanStep] = []
    copies: dict[str, list[str]] = {}                 # chave-modelo → chaves das cópias (para quem depende do bloco)

    def outer(deps: list[str]) -> list[str]:
        res: list[str] = []
        for d in deps:
            if d in collected and d in by_key:        # a coleta some do plano: herda-se o que ELA exigia
                res += outer(by_key[d].depends_on)
            elif d in copies:                         # depende de etapa-modelo já expandida: de todas as cópias
                res += copies[d]
            else:
                res.append(d)
        return list(dict.fromkeys(res))

    i = 0
    while i < len(steps):
        s = steps[i]
        if s.key in collected:                        # coleta já feita nesta execução
            i += 1
            continue
        if not s.for_each or s.for_each not in collected:
            out.append(s.model_copy(update={"depends_on": outer(s.depends_on)}))
            i += 1
            continue
        source, block = s.for_each, []
        while i < len(steps) and steps[i].for_each == source:
            block.append(steps[i])
            i += 1
        inside = {b.key for b in block}
        # Item 24.3: o valor que uma etapa DO BLOCO lê é de cada item (`nome_i2`), senão as cópias se sobrescreveriam
        # (o nome é único no objetivo) e o relatório só guardaria o do último item.
        nomes_do_bloco = {nome for b in block for nome in b.saidas}
        items = collected[source]
        for n, item in enumerate(items, start=1):
            for b in block:
                deps = ([_copy_key(d, n) for d in b.depends_on if d in inside]
                        + outer([d for d in b.depends_on if d not in inside]))
                copia = renomear_para_o_item(b, nomes_do_bloco, n)
                out.append(copia.model_copy(update={
                    "key": _copy_key(b.key, n), "for_each": None, "template_key": b.template_key or b.key,
                    "variables": {**copia.variables, "item": item, "item_index": str(n)}, "depends_on": deps}))
        for b in block:
            copies[b.key] = [_copy_key(b.key, n) for n in range(1, len(items) + 1)]
    return out
