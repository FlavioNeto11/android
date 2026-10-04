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


#: 30.43: as origens que são EXECUÇÃO DE VALIDAÇÃO (a prova de fluxo e a re-execução da validação do QA): partem de
#: estado conhecido (`Scheduler._partir_da_prova`) e o efeito repetido delas tira a evidência (`domain/prova.py`).
ORIGENS_DE_VALIDACAO: tuple[OrigemDaExecucao, ...] = ("prova_fluxo", "validacao_qa")


def eh_execucao_de_validacao(prova_fluxo_id: str | None, idempotency_key: str | None) -> bool:
    """A execução é de validação, pela MESMA regra de `origem_da_execucao` (30.43)."""
    return origem_da_execucao(prova_fluxo_id, idempotency_key)[0] in ORIGENS_DE_VALIDACAO


#: A chave de idempotência das execuções de LOTE de uma frente (28.19): rodada de medida, de teste ou de validação que
#: uma sessão dispara pela API (`lote:<frente>:<id>`, um id por execução). Fica FORA de `PREFIXOS_DE_ORIGEM` de
#: propósito: não é uma origem que o painel mostre (o selo do 30.38 e o `types.ts` não mudam); só o avisador a lê, para
#: não mandar ao dono um aviso por execução de um lote que não é dele.
PREFIXO_LOTE = "lote:"


#: 30.31 (fatia 2): a chave das execuções de ENSAIO SÓ DE LEITURA (`ensaio:<pedido>`). O ensaio percorre o fluxo até
#: a etapa com efeito fora do aparelho e PARA antes dela (`Scheduler._parar_no_ensaio`): é como a validação lê um fluxo
#: do Instagram em perfil de terceiro sem nada sair da máquina. Fica FORA de `PREFIXOS_DE_ORIGEM`, como o lote: o ensaio
#: de um fluxo é uma prova (`prova_fluxo_id`), e a origem que o painel mostra é a dela.
PREFIXO_ENSAIO = "ensaio:"


def eh_ensaio_de_leitura(idempotency_key: str | None) -> bool:
    """A execução é um ensaio só de leitura (30.31, fatia 2): para antes da primeira etapa com efeito externo."""
    return (idempotency_key or "").startswith(PREFIXO_ENSAIO)


def e_execucao_do_sistema(prova_fluxo_id: str | None, idempotency_key: str | None) -> bool:
    """A execução é do sistema, e não de uma pessoa (28.19): prova de fluxo, validação do QA, ensaio só de leitura ou
    lote de frente. Nada dela vira aviso individual ao dono. Telegram e Trello são pedidos de pessoa: seguem avisando."""
    origem, _ = origem_da_execucao(prova_fluxo_id, idempotency_key)
    return (origem in ("prova_fluxo", "validacao_qa") or (idempotency_key or "").startswith(PREFIXO_LOTE)
            or eh_ensaio_de_leitura(idempotency_key))


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
