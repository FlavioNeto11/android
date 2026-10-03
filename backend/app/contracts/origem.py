"""A origem de uma execução (30.38 a): de onde ela veio, DERIVADA da linha de `runs`, sem coluna nova. O vocabulário
é fechado e mora só aqui: o selo do painel (30.38) e o "pelo Telegram" / "pelo Trello" dos canais (32.3) leem daqui,
e nenhum dos dois nasce com um segundo campo.

A regra, na ordem:
- `prova_fluxo_id` preenchido → `prova_fluxo` (ref = o fluxo). Ganha da chave: a prova do 30.37 pode nascer de um
  pedido de validação;
- a chave de idempotência com um prefixo de `PREFIXOS_DE_ORIGEM` → a origem dele (ref = o resto da chave). A
  validação do QA cria com `validacao:<pedido>`; os canais, com `<canal>:<id_externo>` (28.15, 32.2);
- nada disso → `None`: pedido de pessoa pelo painel, ou linha anterior às marcas.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

OrigemDaExecucao = Literal["prova_fluxo", "validacao_qa", "telegram", "trello"]
ORIGENS_DA_EXECUCAO: tuple[OrigemDaExecucao, ...] = ("prova_fluxo", "validacao_qa", "telegram", "trello")

#: A chave de idempotência das execuções que a validação do QA cria (`modules/learning/application/validacao.py`).
#: Nenhum outro lugar escreve o literal: `tests/test_origem_da_execucao.py` reprova se escrever.
PREFIXO_VALIDACAO = "validacao:"

#: Prefixo da chave de idempotência → origem.
PREFIXOS_DE_ORIGEM: Mapping[str, OrigemDaExecucao] = {
    PREFIXO_VALIDACAO: "validacao_qa",
    "telegram:": "telegram",
    "trello:": "trello",
}


def origem_da_execucao(prova_fluxo_id: str | None,
                       idempotency_key: str | None) -> tuple[OrigemDaExecucao | None, str | None]:
    """(origem, ref) de uma execução, pela regra do módulo. Sem marca: (None, None)."""
    if prova_fluxo_id:
        return "prova_fluxo", str(prova_fluxo_id)
    chave = idempotency_key or ""
    for prefixo, origem in PREFIXOS_DE_ORIGEM.items():
        if chave.startswith(prefixo):
            return origem, chave[len(prefixo):] or None
    return None, None
