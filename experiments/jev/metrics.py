"""Métricas do piloto A. Tudo em bytes é PROXY_METRIC — nunca "tokens".

Definições (fixadas antes de qualquer resultado Jev real):
- PRECISION_AT_K : |top-k arquivos distintos ∩ esperados| / k   (denominador fixo k)
- RECALL_AT_K    : |top-k arquivos distintos ∩ esperados| / |esperados|
- DELIVERED_RECALL: fração dos arquivos esperados presentes nos trechos de fato ENTREGUES ao Claude
- REGION_RECALL  : fração das regiões esperadas que algum trecho entregue intersecta
- BYTES_READ     : soma dos tamanhos dos arquivos que o retriever abriu para produzir candidatos
- BYTES_RETURNED : bytes de todos os trechos candidatos que a ferramenta imprimiria (antes do corte de entrega)
- CONTEXT_BYTES_RETURNED: bytes dos trechos entregues ao Claude (após as travas de entrega)
- NAIVE_READ_BYTES: soma dos tamanhos dos arquivos esperados lidos por inteiro (o cenário "Read do arquivo todo")
- READ_BYTES_AVOIDED: max(0, NAIVE_READ_BYTES − CONTEXT_BYTES_RETURNED) — só tem sentido junto com o recall
"""
from __future__ import annotations

import math
from typing import Sequence

from baselines import Span


def precision_at_k(ranked: Sequence[str], expected: set[str], k: int) -> float:
    return len(set(ranked[:k]) & expected) / k


def recall_at_k(ranked: Sequence[str], expected: set[str], k: int) -> float:
    return len(set(ranked[:k]) & expected) / len(expected) if expected else 0.0


def intersects(span: Span, region: tuple[str, int, int]) -> bool:
    f, a, b = region
    return span.file == f and span.start <= b and a <= span.end


def region_recall(delivered: Sequence[Span], regions: Sequence[tuple[str, int, int]]) -> float:
    if not regions:
        return 0.0
    return sum(any(intersects(s, r) for s in delivered) for r in regions) / len(regions)


def delivered_recall(delivered: Sequence[Span], expected: set[str]) -> float:
    return len({s.file for s in delivered} & expected) / len(expected) if expected else 0.0


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def aggregate(rows: list[dict], key: str | None = None) -> dict:
    """Agrega linhas de um mesmo `strategy`. `key` filtra por 'grep_friendly' / 'semantic_only' verdadeiros."""
    sel = [r for r in rows if key is None or r.get(key)]
    n = len(sel)
    if n == 0:
        return {"n": 0}
    lat = [r["latency_ms"] for r in sel]
    fails = [r for r in sel if r.get("failure")]
    return {
        "n": n,
        "PRECISION_AT_1": mean([r["p1"] for r in sel]),
        "PRECISION_AT_3": mean([r["p3"] for r in sel]),
        "RECALL_AT_3": mean([r["r3"] for r in sel]),
        "RECALL_AT_5": mean([r["r5"] for r in sel]),
        "DELIVERED_RECALL": mean([r["delivered_recall"] for r in sel]),
        "REGION_RECALL": mean([r["region_recall"] for r in sel]),
        "LATENCY_MS_P50": percentile(lat, 50),
        "LATENCY_MS_P95": percentile(lat, 95),
        "FILES_EXAMINED_MEAN": mean([r["files_examined"] for r in sel]),
        "BYTES_READ_MEDIAN": percentile([r["bytes_read"] for r in sel], 50),
        "BYTES_RETURNED_MEDIAN": percentile([r["bytes_returned"] for r in sel], 50),
        "CONTEXT_BYTES_RETURNED_MEDIAN": percentile([r["context_bytes_returned"] for r in sel], 50),
        "NAIVE_READ_BYTES_MEDIAN": percentile([r["naive_read_bytes"] for r in sel], 50),
        "READ_BYTES_AVOIDED_MEDIAN": percentile([r["read_bytes_avoided"] for r in sel], 50),
        "FAILURES": len(fails),
        "TIMEOUTS": sum(1 for r in fails if r["failure"] == "timeout"),
        "INVALID_RESPONSES": sum(1 for r in fails if r["failure"] in ("invalid_response", "unknown_choice")),
        "JEV_REQUESTS": sum(r.get("jev_requests", 0) for r in sel),
        "JEV_COST_USD_PROXY": sum(r.get("jev_cost_usd", 0.0) for r in sel),
        "METRIC_KIND": "PROXY_METRIC (bytes; não são tokens)",
    }
