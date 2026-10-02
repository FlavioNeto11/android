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
    """Preço CADASTRADO deste modelo, ou None. Casamento exato primeiro; depois o prefixo MAIS LONGO (o sufixo de
    data não muda a tarifa). Era o primeiro prefixo na ordem de inserção: com `claude-opus-5` cadastrado antes,
    `claude-opus-5-5` seria cobrado a $5/$25 e nunca apareceria em `unpriced_models`."""
    nome = model or ""
    if nome in prices:
        return prices[nome]
    candidatos = [k for k in prices if nome.startswith(k)]
    return prices[max(candidatos, key=len)] if candidatos else None


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
              since: str | None = None, origem: str | None = None) -> float:
    """Quanto já se gastou, em US$, nesta execução (`run_id`) ou desde um instante (`since`).

    `origem` (item 31.2, migração 073) restringe a quem pediu a chamada (`ai_calls.origem`); combina com `run_id` ou
    com `since`, e sozinho não define janela nenhuma (devolve 0, como a chamada sem `run_id` nem `since`). Linhas
    antigas têm `origem` NULL e nunca entram num filtro por origem.

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
    if origem is not None:
        where += " AND origem=?"
        params = (*params, origem)
    # `usd` (migração 048) é o custo DECLARADO de uma chamada cobrada por unidade — imagem — e vale no lugar dos
    # tokens daquela linha; onde é nulo, a conta continua sendo tokens × preço do modelo.
    linhas = db.query(
        f"SELECT model, SUM(CASE WHEN usd IS NULL THEN input_tokens ELSE 0 END) input_tokens,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_read ELSE 0 END) cache_read,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_write ELSE 0 END) cache_write,"
        f" SUM(CASE WHEN usd IS NULL THEN output_tokens ELSE 0 END) output_tokens,"
        f" SUM(COALESCE(usd, 0)) usd_declarado FROM ai_calls WHERE {where} GROUP BY model", params)
    return round(sum(row_usd(prices, linha) + float(linha["usd_declarado"] or 0) for linha in linhas), 6)


def spent_today_usd(db: Any, prices: dict[str, list[float]], *, origem: str | None = None) -> float:
    return spent_usd(db, prices, since=day_start_iso(), origem=origem)
