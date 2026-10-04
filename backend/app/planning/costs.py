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

from ..db import Database
from ..util import now, to_iso

#: Colunas de `ai_calls` na ordem dos preços: [entrada nova, cache lido, cache gravado, saída].
COLUNAS = ("input_tokens", "cache_read", "cache_write", "output_tokens")
#: Item 31.31 (migração 092): a gravação de cache de 1 h custa 2x a entrada base (a de 5 min, o 3º preço de
#: `ai.prices`, é 1,25x). `cache_write` é o total gravado; `cache_write_1h` é a parte de 1 h dele.
FATOR_DA_GRAVACAO_DE_1H = 2.0


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


def extra_1h(prices: dict[str, list[float]], model: str, tokens_1h: float | None) -> float:
    """31.31: o que a parte de 1 h de `cache_write` custa ALÉM do preço de gravação de 5 min com que `usd` já a
    contou: `tokens_1h × (2 × entrada − gravação)`. Modelo sem preço paga a tarifa mais cara, como em `usd`."""
    if not tokens_1h:
        return 0.0
    p, _ = effective_price(prices, model)
    return float(tokens_1h) * (FATOR_DA_GRAVACAO_DE_1H * p[0] - p[2]) / 1_000_000


def row_usd(prices: dict[str, list[float]], row: Any) -> float:
    """Custo por tokens de uma linha (ou soma) de `ai_calls`. A coluna `cache_write_1h` é opcional na linha: sem ela
    (a consulta não a pediu), a gravação inteira sai pelo preço de 5 min, como antes do 31.31."""
    um_h = row["cache_write_1h"] if "cache_write_1h" in row.keys() else 0
    return usd(prices, row["model"], _tokens(row)) + extra_1h(prices, row["model"], um_h)


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
        f" SUM(CASE WHEN usd IS NULL THEN COALESCE(cache_write_1h, 0) ELSE 0 END) cache_write_1h,"
        f" SUM(CASE WHEN usd IS NULL THEN output_tokens ELSE 0 END) output_tokens,"
        f" SUM(COALESCE(usd, 0)) usd_declarado FROM ai_calls WHERE {where} GROUP BY model", params)
    return round(sum(row_usd(prices, linha) + float(linha["usd_declarado"] or 0) for linha in linhas), 6)


def spent_today_usd(db: Any, prices: dict[str, list[float]], *, origem: str | None = None) -> float:
    return spent_usd(db, prices, since=day_start_iso(), origem=origem)


#: RA-10 (migração 080): as colunas de `ai_calls` por que `usd_por` agrupa. Lista fechada, porque o nome entra no SQL.
COLUNAS_DE_GRUPO: frozenset[str] = frozenset({"role", "origem", "motivo", "escalate", "image_reason", "verdict"})


def usd_por(db: Database, prices: dict[str, list[float]], coluna: str, where: str,
            params: tuple[object, ...]) -> dict[str | None, tuple[int, float]]:
    """(chamadas, US$) por valor de uma coluna de `ai_calls`, para os grupos de `/api/usage` (RA-10).

    A regra de preço é a de `spent_usd`, sem uma terceira: o custo declarado (`usd`) onde há, tokens × preço do modelo
    onde não, e o modo simulado a US$ 0. As chamadas simuladas CONTAM em `chamadas`, como nos demais grupos do
    relatório. `where` é sem alias, como em `/api/usage`."""
    if coluna not in COLUNAS_DE_GRUPO:
        raise ValueError(f"coluna de grupo desconhecida: {coluna!r}")
    linhas = db.query(
        f"SELECT {coluna} grupo, model, COALESCE(provider,'') = 'simulated' simulado, COUNT(*) n,"
        f" SUM(CASE WHEN usd IS NULL THEN input_tokens ELSE 0 END) input_tokens,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_read ELSE 0 END) cache_read,"
        f" SUM(CASE WHEN usd IS NULL THEN cache_write ELSE 0 END) cache_write,"
        f" SUM(CASE WHEN usd IS NULL THEN COALESCE(cache_write_1h, 0) ELSE 0 END) cache_write_1h,"
        f" SUM(CASE WHEN usd IS NULL THEN output_tokens ELSE 0 END) output_tokens,"
        f" SUM(COALESCE(usd, 0)) usd_declarado FROM ai_calls WHERE {where}"
        f" GROUP BY {coluna}, model, COALESCE(provider,'') = 'simulated'", params)
    soma: dict[str | None, tuple[int, float]] = {}
    for linha in linhas:
        n, valor = soma.get(linha["grupo"], (0, 0.0))
        custo = 0.0 if linha["simulado"] else row_usd(prices, linha) + float(linha["usd_declarado"] or 0)
        soma[linha["grupo"]] = (n + int(linha["n"]), valor + custo)
    return {grupo: (n, round(valor, 6)) for grupo, (n, valor) in soma.items()}
