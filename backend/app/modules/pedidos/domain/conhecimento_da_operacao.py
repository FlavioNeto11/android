"""Conhecimento da OPERAÇÃO: o que os N agentes de uma operação sabem em comum (prova30 A1; migração 125).

Mora onde a tarefa já guarda o que sabe, sem segundo sistema: a leitura do alvo é uma linha de `pedido_observacoes`
(com fonte, sha256 e instante), e o fato consolidado é uma entrada de `pedido_memoria` (com origem, confiança, evidência e
frescor). Este módulo é puro: decide o que gravar e como o bloco vai ao texto de cada persona; o banco e o relógio chegam
de quem chama.

Separação que o dono pediu (pedido de 06/10):
    * o que a OPERAÇÃO sabe (o post, o assunto, os fatos e as fontes) vai a todas as personas no bloco
      `<fatos_da_operacao>`, como DADO citado;
    * o que a PERSONA é e viveu (identidade, conta, memória, histórico, voz) segue no `context_text` dela;
    * nada do bloco da operação vira memória de persona: a persona lembra do que FEZ (o efeito confirmado), não do que
      leu em comum.

A leitura do alvo é gravada UMA vez por operação: o primeiro agente que chega ao post a grava; os outros conferem o
sha256 e, se a tela deles diz outra coisa (legenda editada, outro post), a leitura deles entra como observação `incerto`
do agente, e a execução segue com a tela própria. Conflito não vota: fica registrado.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .memoria import Entrada
from .observacao import curto, sha256_do_valor

#: O nome da observação da leitura do alvo (o mesmo `NOME_RE` de `step_outputs`).
NOME_DA_LEITURA = "leitura_do_alvo"
#: A chave do fato que a leitura vira na memória da operação.
CHAVE_DO_CONTEUDO = "alvo.conteudo"
#: O que se guarda da tela lida: o bastante para o texto, com o teto da memória (`memoria.VALOR_MAX`).
LEITURA_MAX = 2000
#: Teto do bloco `<fatos_da_operacao>` no texto da persona (caracteres). O `<tela>` dela continua indo inteiro.
TETO_DO_BLOCO = 2500
FONTE_MAX = 120

PRIMEIRA = "primeira"
IGUAL = "igual"
DIFERENTE = "diferente"


@dataclass(frozen=True)
class Leitura:
    """A tela lida por UM agente, pronta para gravar: o texto cortado no teto e o sha256 do texto INTEIRO."""
    texto: str
    sha256: str
    cortado: bool


def preparar_leitura(texto: str | None) -> Leitura | None:
    """`None` quando não há o que gravar (tela vazia). O sha256 é do texto normalizado em espaços: a mesma legenda lida
    com quebras de linha diferentes é a mesma leitura."""
    normal = " ".join((texto or "").split())
    if not normal:
        return None
    return Leitura(normal[:LEITURA_MAX], sha256_do_valor(normal), len(normal) > LEITURA_MAX)


def conferir(da_operacao: str | None, do_agente: str) -> str:
    """`primeira` (não havia leitura), `igual` ou `diferente`, pelo sha256."""
    if not da_operacao:
        return PRIMEIRA
    return IGUAL if da_operacao == do_agente else DIFERENTE


def fonte_curta(fonte: str) -> str:
    return curto(fonte, FONTE_MAX) or ""


def bloco(entradas: Iterable[Entrada], *, agora: str, teto: int = TETO_DO_BLOCO) -> str:
    """O texto de `<fatos_da_operacao>`: só o que vale agora (dentro do frescor), sem pendência resolvida.

    Ordem: fatos confirmados, depois hipóteses (marcadas como tais), depois as fontes. Dentro de cada grupo, o mais
    recente primeiro, com desempate pela chave. Corta no teto sem partir uma linha; vazio quando nada vale.
    """
    vivas = [e for e in entradas if e.vale(agora) and not (e.tipo == "pendencia" and e.resolvida)
             and e.tipo in ("descoberta", "decisao", "fonte")]

    def grupo(filtro: Iterable[Entrada]) -> list[Entrada]:
        return sorted(sorted(filtro, key=lambda e: e.chave), key=lambda e: e.atualizada_em, reverse=True)

    fatos = grupo(e for e in vivas if e.tipo != "fonte" and e.confianca == "confirmado")
    hipoteses = grupo(e for e in vivas if e.tipo != "fonte" and e.confianca == "hipotese")
    fontes = grupo(e for e in vivas if e.tipo == "fonte")
    linhas: list[str] = []
    usado = 0
    for rotulo, lista in (("fato", fatos), ("hipótese, não confirmada", hipoteses), ("fonte", fontes)):
        for e in lista:
            linha = f"- [{rotulo}] {e.chave}: {' '.join(e.valor.split())}"
            if usado + len(linha) > teto:
                continue
            linhas.append(linha)
            usado += len(linha)
    return "\n".join(linhas)


def quantos(entradas: Iterable[Entrada], *, agora: str) -> int:
    """Quantas entradas o bloco considera (para o estágio e o evento, que levam contagem, nunca texto)."""
    return sum(1 for e in entradas if e.vale(agora) and e.tipo in ("descoberta", "decisao", "fonte"))
