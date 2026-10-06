"""Memória do pedido: o estado da TAREFA que vale entre uma ocorrência e outra (docs/design/pedidos-persistentes.md §8.1).

Chave/valor por pedido, com versão. Puro: nada de banco nem de relógio (o instante chega de quem chama) e nada de
`app.security` (o domínio não pode importá-lo): a recusa de segredo por FORMATO chega como `parece_segredo`, injetada pela
infraestrutura (`looks_secret` ou `mentions_credential`).

Regras (as mesmas de `social/memory.py`, no que cabe à tarefa):
    * só fato confirmado entra; o que parece credencial ou código de verificação é RECUSADO, nunca mascarado;
    * escrever o mesmo valor NÃO sobe a versão (a versão conta mudanças, não gestos);
    * texto vindo de terceiros é DADO, não instrução: a memória volta ao plano como bloco estruturado, e quem o monta
      trata cada `valor` como texto citado;
    * tamanho limitado: chave até 64, valor até `VALOR_MAX`, e o bloco do plano tem teto (`compactar`).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

#: O vocabulário de `pedido_memoria.tipo` (CHECK da 070; `tests/test_pedidos_memoria.py` confere os dois).
TIPOS = ("progresso", "descoberta", "decisao", "pendencia", "fonte")
#: Quem afirmou o fato (125): o operador, a ocorrência (o fechamento de uma execução), a leitura do alvo de uma operação
#: ou a pesquisa externa. A linha anterior à 125 é `ocorrencia`.
ORIGENS = ("operador", "ocorrencia", "leitura", "pesquisa")
#: `hipotese` é o que ainda não se confirmou (uma fonte só, por exemplo): entra no contexto marcado como tal, e quem
#: escreve não o afirma como fato. Antes da 125 só existia `confirmado`.
CONFIANCAS = ("confirmado", "hipotese")
EVIDENCIA_MAX = 20
CHAVE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
VALOR_MAX = 2000
#: Teto padrão do bloco que entra no plano da ocorrência (caracteres: o desenho fala em tokens, que aqui se medem em
#: ~4 caracteres; o excesso é compactado SEM IA).
TETO_DO_BLOCO = 6000
MAX_DESCOBERTAS = 8


class MemoriaInvalida(ValueError):
    """A entrada não pode ser guardada; a mensagem diz por quê, sem repetir o valor (pode ser um segredo)."""


@dataclass(frozen=True)
class Entrada:
    chave: str
    tipo: str
    valor: str
    versao: int = 1
    atualizada_em: str = ""
    ocorrencia_id: str | None = None
    resolvida: bool = False
    origem: str = "ocorrencia"
    confianca: str = "confirmado"
    #: Ids das observações que sustentam o fato (no máximo `EVIDENCIA_MAX`); vazio = afirmado sem observação.
    evidencia: tuple[str, ...] = ()
    #: Até quando o fato vale (UTC ISO); `None` = não vence. Vencido, sai do contexto e vira lacuna de novo.
    frescor_ate: str | None = None

    def vale(self, agora: str) -> bool:
        return self.frescor_ate is None or self.frescor_ate > agora


@dataclass(frozen=True)
class Escrita:
    """O que `escrever` decide: `entrada` é a linha que deve ficar; `mudou` diz se há o que gravar."""
    entrada: Entrada
    mudou: bool


def validar(chave: str, tipo: str, valor: str, *, parece_segredo: Callable[[str], bool]) -> None:
    if not CHAVE_RE.fullmatch(chave or ""):
        raise MemoriaInvalida(f"chave de memória inválida: {chave!r}")
    if tipo not in TIPOS:
        raise MemoriaInvalida(f"tipo de memória inválido: {tipo!r}")
    if not isinstance(valor, str) or not valor.strip():
        raise MemoriaInvalida(f"valor da memória '{chave}' precisa ser texto não vazio")
    if len(valor) > VALOR_MAX:
        raise MemoriaInvalida(f"valor da memória '{chave}' passa de {VALOR_MAX} caracteres")
    if parece_segredo(chave) or parece_segredo(valor):
        raise MemoriaInvalida(f"memória '{chave}' recusada: tem formato de credencial ou código")


def escrever(atual: Entrada | None, *, chave: str, tipo: str, valor: str, agora: str,
             ocorrencia_id: str | None = None, parece_segredo: Callable[[str], bool], origem: str = "ocorrencia",
             confianca: str = "confirmado", evidencia: Iterable[str] = (), frescor_ate: str | None = None) -> Escrita:
    """A entrada que fica depois de gravar `valor` em `chave`. Valor igual ao atual: nada muda (versão e instante
    ficam). Valor novo: versão + 1. Mudar o TIPO de uma chave existente é recusado (a chave `x` não vira outra coisa
    em silêncio).

    Mesmo valor com a procedência mudada (confiança, evidência ou frescor) também sobe a versão: uma hipótese que se
    confirma, ou um fato relido que ganha prazo novo, é mudança do que se SABE, mesmo com o texto igual."""
    validar(chave, tipo, valor, parece_segredo=parece_segredo)
    if origem not in ORIGENS:
        raise MemoriaInvalida(f"origem de memória inválida: {origem!r}")
    if confianca not in CONFIANCAS:
        raise MemoriaInvalida(f"confiança de memória inválida: {confianca!r}")
    provas = tuple(dict.fromkeys(str(e) for e in evidencia if e))[:EVIDENCIA_MAX]
    if atual is None:
        return Escrita(Entrada(chave, tipo, valor, 1, agora, ocorrencia_id, False, origem, confianca, provas,
                               frescor_ate), True)
    if atual.tipo != tipo:
        raise MemoriaInvalida(f"a chave '{chave}' já é do tipo '{atual.tipo}', não '{tipo}'")
    if (atual.valor, atual.confianca, atual.evidencia, atual.frescor_ate) == (valor, confianca, provas, frescor_ate):
        return Escrita(atual, False)
    return Escrita(Entrada(chave, tipo, valor, atual.versao + 1, agora, ocorrencia_id or atual.ocorrencia_id,
                           atual.resolvida, origem, confianca, provas, frescor_ate), True)


def resolver(atual: Entrada, *, agora: str) -> Escrita:
    """Marca uma `pendencia` como resolvida (ela deixa de pesar no plano; o registro fica). Idempotente."""
    if atual.tipo != "pendencia":
        raise MemoriaInvalida(f"só pendência se resolve; '{atual.chave}' é '{atual.tipo}'")
    if atual.resolvida:
        return Escrita(atual, False)
    return Escrita(replace(atual, versao=atual.versao + 1, atualizada_em=agora, resolvida=True), True)


def compactar(entradas: Iterable[Entrada], *, teto: int = TETO_DO_BLOCO,
              max_descobertas: int = MAX_DESCOBERTAS) -> tuple[Entrada, ...]:
    """O bloco que cabe no plano da ocorrência (§8.1), SEM IA e determinístico.

    Ordem de prioridade: pendências abertas, progresso (último estado por chave), decisões, fontes, e as
    `max_descobertas` descobertas mais recentes. Dentro de cada grupo, a mais recente primeiro, desempate pela chave.
    Corta no teto de caracteres (chave + valor), sem partir uma entrada ao meio. `resolvida` sai.
    """
    ultimo: dict[str, Entrada] = {}
    for e in entradas:
        anterior = ultimo.get(e.chave)
        if anterior is None or (e.versao, e.atualizada_em) > (anterior.versao, anterior.atualizada_em):
            ultimo[e.chave] = e
    vivas = [e for e in ultimo.values() if not (e.tipo == "pendencia" and e.resolvida)]

    def grupo(tipo: str) -> list[Entrada]:
        return sorted((e for e in vivas if e.tipo == tipo), key=lambda e: (_neg(e.atualizada_em), e.chave))

    ordem = (grupo("pendencia") + grupo("progresso") + grupo("decisao") + grupo("fonte")
             + grupo("descoberta")[:max(0, max_descobertas)])
    saida: list[Entrada] = []
    usado = 0
    for e in ordem:
        custo = len(e.chave) + len(e.valor)
        if usado + custo > teto:
            continue                    # uma entrada grande não impede as menores que vêm depois
        saida.append(e)
        usado += custo
    return tuple(saida)


def _neg(texto: str) -> tuple[int, ...]:
    """Chave de ordenação decrescente para TEXTO (mais recente primeiro) sem depender de sort estável com reverse."""
    return tuple(-ord(c) for c in texto)
