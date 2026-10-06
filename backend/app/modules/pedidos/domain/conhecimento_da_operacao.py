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


def preparar_leitura(texto: str | None, *, identidade: str | None = None) -> Leitura | None:
    """`None` quando não há o que gravar (tela vazia). O sha256 é da `identidade` do alvo quando ela existe (o autor e o
    trecho da legenda que a etapa procura), senão do texto normalizado em espaços.

    Por que a identidade: a porta de escrita do comentário roda com a lista de comentários ABERTA, e o texto visível
    inclui os comentários. Depois do 1º agente comentar, a tela do 2º já é outra, mas o post é o mesmo: comparar o
    texto inteiro acusaria 29 "posts diferentes". "Diferente" tem de querer dizer outro alvo."""
    normal = " ".join((texto or "").split())
    if not normal:
        return None
    chave = " ".join((identidade or "").casefold().split()) or normal
    return Leitura(normal[:LEITURA_MAX], sha256_do_valor(chave), len(normal) > LEITURA_MAX)


def identidade_do_alvo(autor: str | None, legenda: str | None) -> str | None:
    """`autor|legenda` dos argumentos da etapa (`post_author`, `caption_contains`, herdados do OPEN_POST), ou `None`."""
    partes = [" ".join(str(x or "").casefold().lstrip("@").split()) for x in (autor, legenda)]
    return "|".join(partes) if any(partes) else None


def recorte_do_alvo(tela: str, *, autor: str | None, legenda: str | None) -> str:
    """O que se guarda do alvo: as linhas da tela que contêm o trecho da legenda (a publicação, nossa), nunca a lista de
    comentários de terceiros. Sem a legenda na tela, só o que a etapa já sabia (autor e trecho); sem nada disso, a tela."""
    if not legenda:
        return tela
    alvo = legenda.casefold()
    linhas = [ln for ln in (tela or "").splitlines() if alvo in ln.casefold()]
    if linhas:
        return "\n".join(linhas)
    return f"publicação de @{(autor or '').lstrip('@')} cuja legenda contém: {legenda}" if autor else f"legenda contém: {legenda}"


def conferir(da_operacao: str | None, do_agente: str) -> str:
    """`primeira` (não havia leitura), `igual` ou `diferente`, pelo sha256."""
    if not da_operacao:
        return PRIMEIRA
    return IGUAL if da_operacao == do_agente else DIFERENTE


def fonte_curta(fonte: str) -> str:
    return curto(fonte, FONTE_MAX) or ""


def escolhidas(entradas: Iterable[Entrada], *, agora: str, teto: int = TETO_DO_BLOCO) -> list[tuple[str, Entrada]]:
    """O que vai em `<fatos_da_operacao>`, com o rótulo de cada linha: só o que vale agora (dentro do frescor), sem
    pendência resolvida.

    Ordem: fatos confirmados, depois hipóteses (marcadas como tais), depois as fontes. Dentro de cada grupo, o mais
    recente primeiro, com desempate pela chave. Corta no teto sem partir uma linha.
    """
    vivas = [e for e in entradas if e.vale(agora) and not (e.tipo == "pendencia" and e.resolvida)
             and e.tipo in ("descoberta", "decisao", "fonte")]

    def grupo(filtro: Iterable[Entrada]) -> list[Entrada]:
        return sorted(sorted(filtro, key=lambda e: e.chave), key=lambda e: e.atualizada_em, reverse=True)

    fatos = grupo(e for e in vivas if e.tipo != "fonte" and e.confianca == "confirmado")
    hipoteses = grupo(e for e in vivas if e.tipo != "fonte" and e.confianca == "hipotese")
    fontes = grupo(e for e in vivas if e.tipo == "fonte")
    saida: list[tuple[str, Entrada]] = []
    usado = 0
    for rotulo, lista in (("fato", fatos), ("hipótese, não confirmada", hipoteses), ("fonte", fontes)):
        for e in lista:
            tamanho = len(_linha(rotulo, e))
            if usado + tamanho > teto:
                continue
            saida.append((rotulo, e))
            usado += tamanho
    return saida


def _linha(rotulo: str, e: Entrada) -> str:
    return f"- [{rotulo}] {e.chave}: {' '.join(e.valor.split())}"


def bloco(entradas: Iterable[Entrada], *, agora: str, teto: int = TETO_DO_BLOCO) -> str:
    """O texto de `<fatos_da_operacao>` (as linhas de `escolhidas`); vazio quando nada vale."""
    return "\n".join(_linha(r, e) for r, e in escolhidas(entradas, agora=agora, teto=teto))


def ref(e: Entrada) -> str:
    """A referência da entrada, a mesma do relatório do aprendizado da operação (31.163): `fato:`, `fonte:` ou
    `registro:` mais a chave. É o que vai em `resultado.conhecimento_ids` do alvo."""
    return f"{'fonte' if e.tipo == 'fonte' else 'fato' if e.tipo == 'descoberta' else 'registro'}:{e.chave}"


def quantos(entradas: Iterable[Entrada], *, agora: str) -> int:
    """Quantas entradas o bloco considera (para o estágio e o evento, que levam contagem, nunca texto)."""
    return sum(1 for e in entradas if e.vale(agora) and e.tipo in ("descoberta", "decisao", "fonte"))
