"""Custo de IA em DINHEIRO: preço por modelo e quanto já se gastou (achado #95).

Por que existe um módulo só para isto: o teto de gasto precisa da MESMA conta que o painel de uso mostra, e ela
estava embutida em `api.py`. Com dois chamadores (o relatório e o roteador, que barra antes de gastar), uma conta
copiada viraria duas verdades — o painel dizendo US$ 8 e o teto contando outra coisa.

Regra que não é detalhe: modelo **sem preço cadastrado** é cobrado pelo preço MAIS CARO da tabela, nunca como
zero. Zero só vale quando alguém escreveu `[0, 0, 0, 0]` em `ai.prices` — que é como se declara um modelo local
de graça. Sem isso, o destino de um fallback não cadastrado (o caso do achado #92) sairia de graça no teto.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..util import now, to_iso

#: Colunas de `ai_calls` na ordem dos preços: [entrada nova, cache lido, cache gravado, saída].
COLUNAS = ("input_tokens", "cache_read", "cache_write", "output_tokens")


def price_for(prices: dict[str, list[float]], model: str) -> list[float] | None:
    """Preço CADASTRADO deste modelo, ou None. Casamento por prefixo (o sufixo de data não muda a tarifa)."""
    return next((v for k, v in prices.items() if (model or "").startswith(k)), None)


def worst_price(prices: dict[str, list[float]]) -> list[float]:
    """O mais caro de cada coluna. É por este preço que um modelo não cadastrado é contado."""
    if not prices:
        return [0.0, 0.0, 0.0, 0.0]
    return [max(float(p[i]) if i < len(p) else 0.0 for p in prices.values()) for i in range(4)]


def effective_price(prices: dict[str, list[float]], model: str) -> tuple[list[float], bool]:
    """(preço usado, foi estimado?). Estimado = o modelo não está em `ai.prices` e pagou a tarifa mais cara."""
    p = price_for(prices, model)
    return (p, False) if p is not None else (worst_price(prices), True)


def usd(prices: dict[str, list[float]], model: str, tokens: Iterable[float]) -> float:
    p, _ = effective_price(prices, model)
    return sum(float(t) * p[i] for i, t in enumerate(tokens)) / 1_000_000


def _tokens(row: Any) -> list[float]:
    return [float(row[c] or 0) for c in COLUNAS]


def row_usd(prices: dict[str, list[float]], row: Any) -> float:
    return usd(prices, row["model"], _tokens(row))


def day_start_iso() -> str:
    """Começo do dia **UTC**. As colunas guardam ISO-8601 em UTC, que ordena lexicograficamente nos dois dialetos —
    então o corte calculado aqui em Python compara igual no SQLite e no PostgreSQL, sem função de data nenhuma."""
    return to_iso(now()).split("T")[0] + "T00:00:00.000Z"


def spent_usd(db: Any, prices: dict[str, list[float]], *, run_id: str | None = None,
              since: str | None = None) -> float:
    """Quanto já se gastou, em US$, nesta execução (`run_id`) ou desde um instante (`since`).

    Agrupa por modelo e soma com o preço de cada um — inclusive as linhas do fallback, que ficam no modelo que
    REALMENTE respondeu (`ai_calls.model`), como a API cobra.
    """
    if run_id is not None:
        where, params = "run_id=?", (run_id,)
    elif since is not None:
        where, params = "ts >= ?", (since,)
    else:
        return 0.0
    # Modo simulado não custa nada, e um modelo chamado "simulado" não está em `ai.prices` — sem esta cláusula
    # ele seria contado pelo preço MAIS CARO da tabela, e uma bateria de desenvolvimento apareceria como dólares.
    where += " AND COALESCE(provider,'') <> 'simulated'"
    linhas = db.query(
        f"SELECT model, SUM(input_tokens) input_tokens, SUM(cache_read) cache_read, SUM(cache_write) cache_write,"
        f" SUM(output_tokens) output_tokens FROM ai_calls WHERE {where} GROUP BY model", params)
    return round(sum(row_usd(prices, linha) for linha in linhas), 6)


def spent_today_usd(db: Any, prices: dict[str, list[float]]) -> float:
    return spent_usd(db, prices, since=day_start_iso())
