"""Identidade e idempotência da ocorrência (docs/design/pedidos-persistentes.md §6.3).

A `chave` é determinística: o MESMO instante devido do MESMO gatilho do MESMO pedido tem sempre a mesma chave, em
qualquer backend, laço ou reinício. É ela (UNIQUE em `pedido_ocorrencias.chave`, inserção com
`ON CONFLICT DO NOTHING`) que faz dois laços produzirem UMA ocorrência; a trava de líder é só eficiência (§7.3).

    agenda, recuperacao, evento, condicao, persona:  ped:<pedido>:<gatilho>:<previsto_para>
    manual, backfill (gesto da pessoa):              ped:<pedido>:<gatilho ou ->:<instante do pedido>:<origem>

* `agenda` e `recuperacao` do mesmo instante têm a MESMA chave de propósito: recuperar uma ocorrência atrasada é a
  ocorrência do instante original, não uma segunda. Já o gesto da pessoa leva o instante em que ELA pediu (não o
  instante devido) e a origem, para nunca colidir com a da agenda.
* O instante é UTC, no segundo, com sufixo `Z` (`2026-10-02T12:00:00Z`): `formatar_instante` é a ÚNICA fonte desse
  texto e vale também para `previsto_para` na tabela, senão o `UNIQUE (pedido_id, gatilho_id, previsto_para)` e a
  chave poderiam discordar. Fração de segundo é TRUNCADA (piso), nunca arredondada: arredondar atravessaria a
  fronteira do segundo e duas leituras do mesmo gesto poderiam cair em chaves diferentes.
* A chave não carrega dado da pessoa nem do alvo (nada sensível em chave de idempotência): só identificadores
  gerados pelo sistema, no alfabeto de `_ID`, e o instante. Por isso `:` não cabe num id (é o separador).
* A execução da tentativa `n` usa `chave:t<n>` como `idempotency_key` (`RunService.create`): repetir a criação depois
  de uma queda entre criar a execução e marcar a ocorrência devolve a MESMA execução.

Orçamento de tamanho: `RunCreate.idempotency_key` aceita até 100 caracteres, no alfabeto `[A-Za-z0-9_.:-]`. Com ids
de até `TAMANHO_MAXIMO_DO_ID` (28) a chave mais longa — `ped:` + 28 + `:` + 28 + `:` + 20 + `:backfill` + `:t99` — tem
95: cabe. Id maior (um UUID de 36 caracteres, por exemplo) NÃO cabe e é recusado aqui, não truncado: o gerador de
ids de pedido e de gatilho (28.4/28.9) tem de produzir ids curtos.

Puro: stdlib e o vocabulário de `estados.py`. Instante sem fuso é recusado (a hora local sem fuso é ambígua).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.modules.pedidos.domain.estados import ORIGENS, ORIGENS_DE_GESTO

#: Limite e alfabeto da `idempotency_key` de `RunCreate` (`app/models.py`); `tests/test_pedidos_modelo.py` confere que
#: continuam os mesmos, para a mudança de lá não tornar a chave daqui inválida em silêncio.
TAMANHO_MAXIMO_DA_CHAVE_DE_EXECUCAO = 100
_ALFABETO_DA_CHAVE_DE_EXECUCAO = re.compile(r"^[A-Za-z0-9_.:-]+$")

TAMANHO_MAXIMO_DO_ID = 28
_ID = re.compile(rf"^[A-Za-z0-9_-]{{1,{TAMANHO_MAXIMO_DO_ID}}}$")

#: Marca do "sem gatilho" na chave do gesto da pessoa (o `-` não é um id válido: não colide com nenhum).
SEM_GATILHO = "-"
_FORMATO_DO_INSTANTE = "%Y-%m-%dT%H:%M:%SZ"


def formatar_instante(instante: datetime) -> str:
    """O instante em UTC, no segundo (fração truncada), com `Z`: o texto canônico de `previsto_para` e da chave."""
    if instante.tzinfo is None or instante.utcoffset() is None:
        raise ValueError("instante sem fuso: a ocorrência é identificada em UTC, e hora local sem fuso é ambígua")
    return instante.astimezone(timezone.utc).strftime(_FORMATO_DO_INSTANTE)


def _id(valor: str, nome: str) -> str:
    if not isinstance(valor, str) or not _ID.match(valor):
        raise ValueError(f"{nome} inválido para a chave (use 1 a {TAMANHO_MAXIMO_DO_ID} caracteres de [A-Za-z0-9_-]): "
                         f"{valor!r}")
    return valor


def chave_da_ocorrencia(pedido_id: str, gatilho_id: str | None, previsto_para: datetime, *,
                        origem: str = "agenda") -> str:
    """A chave da ocorrência: `ped:<pedido>:<gatilho>:<instante>` (e `:<origem>` no gesto da pessoa).

    `gatilho_id` é obrigatório nas origens com gatilho e opcional em `manual`/`backfill`. Duas chamadas com os mesmos
    argumentos devolvem o mesmo texto; qualquer diferença em pedido, gatilho, instante (no segundo) ou, no gesto da
    pessoa, origem, devolve outro.
    """
    if origem not in ORIGENS:
        raise ValueError(f"origem desconhecida: {origem!r} (esperada uma de {list(ORIGENS)})")
    pedido = _id(pedido_id, "pedido_id")
    instante = formatar_instante(previsto_para)
    if origem in ORIGENS_DE_GESTO:
        gatilho = SEM_GATILHO if gatilho_id is None else _id(gatilho_id, "gatilho_id")
        return f"ped:{pedido}:{gatilho}:{instante}:{origem}"
    if gatilho_id is None:
        raise ValueError(f"a origem {origem!r} nasce de um gatilho: gatilho_id é obrigatório")
    return f"ped:{pedido}:{_id(gatilho_id, 'gatilho_id')}:{instante}"


def chave_da_tentativa(chave: str, tentativa: int) -> str:
    """A `idempotency_key` da execução da tentativa `n` (a partir de 1): `chave:t<n>`.

    Recusa o que o `RunCreate` recusaria (mais de 100 caracteres, fora do alfabeto), para o erro aparecer aqui, com a
    causa, e não como uma validação genérica na hora de criar a execução.
    """
    if isinstance(tentativa, bool) or not isinstance(tentativa, int) or tentativa < 1:
        raise ValueError(f"a tentativa começa em 1: {tentativa!r}")
    texto = f"{chave}:t{tentativa}"
    if len(texto) > TAMANHO_MAXIMO_DA_CHAVE_DE_EXECUCAO:
        raise ValueError(f"a chave da execução passa de {TAMANHO_MAXIMO_DA_CHAVE_DE_EXECUCAO} caracteres: {len(texto)}")
    if not _ALFABETO_DA_CHAVE_DE_EXECUCAO.match(texto):
        raise ValueError("a chave da execução tem caractere fora de [A-Za-z0-9_.:-]")
    return texto
