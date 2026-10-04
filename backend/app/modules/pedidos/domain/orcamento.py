"""Orçamento do pedido em US$ (item 28.6; `docs/design/pedidos-persistentes.md` §6.5 e §10, `pedidos-laco.md` §11).

Dois tetos, ambos opcionais (NULL = sem teto, e o pedido se comporta exatamente como antes da 28.6):

- `orcamento_total_usd`: soma do `custo_usd` de todas as ocorrências do pedido. Quando o que resta não cobre mais uma
  ocorrência, o pedido ENCERRA (`encerrado_motivo='orcamento'`) e nada mais é despachado.
- `orcamento_ocorrencia_usd`: teto de UMA ocorrência (todas as tentativas dela). Vale na própria execução, no
  `AIRouter._budget`, e é a estimativa de custo da PRIMEIRA ocorrência, quando ainda não há histórico.

O gasto de uma ocorrência só entra em `custo_usd` quando ela FECHA; a execução em andamento não conta no total até lá. O
excesso possível é, no pior caso, o de uma ocorrência aberta por vez (a sobreposição `pular` e a `guardar_uma` mantêm uma
só) e fica limitado pelo teto que `teto_da_execucao` dá à própria execução.

Puro: stdlib. Recebe números, devolve números e textos.
"""
from __future__ import annotations

from collections.abc import Sequence
from statistics import median

#: Quantas ocorrências já fechadas entram na estimativa (§10 do desenho: "mediana das últimas 5").
ULTIMAS_PARA_ESTIMAR = 5


def custo_estimado(ultimos: Sequence[float], teto_ocorrencia: float | None) -> float:
    """O que a próxima ocorrência deve custar: a mediana das últimas `ULTIMAS_PARA_ESTIMAR` com custo conhecido; sem
    histórico, o teto da ocorrência; sem os dois, 0 (não há com o que comparar, e a primeira ocorrência sai).

    Ocorrência de custo 0 não entra na mediana: é a que não gastou (cancelada, falhou antes de chamar a IA) e puxaria a
    estimativa para baixo, deixando o pedido estourar o orçamento na ocorrência seguinte."""
    com_custo = [c for c in list(ultimos)[:ULTIMAS_PARA_ESTIMAR] if c > 0]
    if com_custo:
        return float(median(com_custo))
    return float(teto_ocorrencia or 0.0)


def restante(total: float | None, gasto: float, reservado: float = 0.0) -> float | None:
    """O que sobra do orçamento total (`None` = sem orçamento total).

    `reservado` (28.10 F2) é o que os filhos VIVOS do pedido seguram do saldo dele (`colaboracao.reservado_aos_filhos`):
    o filho terminado já devolveu o que não gastou, então o desconto é só o dos que ainda podem gastar. Sem filhos, 0, e
    a conta é a de sempre."""
    return None if total is None else total - gasto - reservado


def motivo_sem_orcamento(total: float | None, gasto: float, necessario: float, reservado: float = 0.0) -> str | None:
    """Texto do motivo quando o pedido NÃO pode despachar outra ocorrência; `None` = pode (ou não tem orçamento).

    `gasto >= total` encerra, e `restante < necessario` também: despachar uma ocorrência que não cobre só gastaria o que
    não há. Sem estimativa (`necessario == 0`) basta haver algum saldo. Com `reservado` (filhos vivos, 28.10 F2) o saldo
    livre é o que sobra DEPOIS da reserva, e o motivo diz que é a reserva que segura o pedido."""
    sobra = restante(total, gasto, reservado)
    if total is None or sobra is None:
        return None
    if sobra <= 0:
        if reservado > 0 and gasto < total:
            return (f"o que resta do orçamento (US$ {total - gasto:.4f}) está reservado aos pedidos filhos "
                    f"(US$ {reservado:.4f})")
        return f"orçamento total esgotado (gastou US$ {gasto:.4f} de US$ {total:.4f})"
    if necessario > 0 and sobra < necessario:
        extra = f"; US$ {reservado:.4f} estão reservados aos pedidos filhos" if reservado > 0 else ""
        return f"o que resta do orçamento (US$ {sobra:.4f}) não cobre uma ocorrência (~US$ {necessario:.4f}){extra}"
    return None


def quantas_cabem(total: float | None, gasto: float, necessario: float, reservado: float = 0.0) -> int | None:
    """Quantas ocorrências a estimativa deixa despachar numa mesma volta; `None` = sem limite (sem orçamento total, ou
    sem estimativa). Uma volta que despacha várias ocorrências do mesmo pedido (`permitir_todas`) não pode gastar mais
    de uma vez o que sobrou. `reservado`: o que os filhos vivos seguram (ver `restante`)."""
    sobra = restante(total, gasto, reservado)
    if sobra is None:
        return None
    if sobra <= 0:
        return 0
    return None if necessario <= 0 else int(sobra // necessario)


def teto_da_execucao(total: float | None, gasto_do_pedido: float, teto_ocorrencia: float | None,
                     custo_da_ocorrencia: float) -> float | None:
    """O teto, em US$, que a EXECUÇÃO de uma ocorrência pode gastar; `None` = nenhum dos dois orçamentos existe.

    O menor entre o que resta do teto da ocorrência (descontadas as tentativas anteriores, já em `custo_da_ocorrencia`)
    e o que resta do orçamento total do pedido (`gasto_do_pedido` já inclui as tentativas anteriores desta
    ocorrência). Nunca negativo."""
    tetos: list[float] = []
    if teto_ocorrencia is not None:
        tetos.append(teto_ocorrencia - custo_da_ocorrencia)
    if total is not None:
        tetos.append(total - gasto_do_pedido)
    return max(0.0, min(tetos)) if tetos else None
