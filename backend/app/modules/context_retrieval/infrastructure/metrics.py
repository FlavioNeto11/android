"""Observabilidade local do retrieval: um evento por pedido, sem código e sem segredo.

O evento só tem campos de uma LISTA FECHADA (`CAMPOS`), com tipo e tamanho conferidos na gravação — o que não está
na lista, ou tem o tipo errado, é descartado. É essa lista, e não a boa vontade de quem monta o evento, que
garante que trecho de código, pergunta crua ou segredo não chegam ao disco. A pergunta vira uma impressão digital
(hash curto) para contar repetição sem guardá-la.

Dois destinos: o arquivo JSONL em `data_dir/context_retrieval/events.jsonl` (cruza processos: a CLI e o backend
escrevem no mesmo lugar, e a API lê dele) e os contadores em memória de `app.metricas` (o painel de desempenho que já
existe). Os rótulos dos contadores têm conjunto fechado e pequeno.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ....metricas import metricas as _metricas

MAX_BYTES_DO_ARQUIVO = 2_000_000   # passado disto, o arquivo vira `.1` (o anterior é descartado)

#: campo → (tipos aceitos, tamanho máximo de texto)
CAMPOS: dict[str, tuple[tuple[type, ...], int]] = {
    "ts": ((str,), 32), "retriever": ((str,), 40), "mode": ((str,), 16), "query_fp": ((str,), 16),
    "revision": ((str,), 24), "provider": ((str,), 32), "model": ((str,), 48),
    "files_considered": ((int,), 0), "files_selected": ((int,), 0), "regions_selected": ((int,), 0),
    "latency_ms": ((int, float), 0), "fallback": ((bool,), 0), "fallback_reason": ((str,), 32),
    "privacy_block_reason": ((str,), 32), "input_tokens": ((int,), 0), "cost_usd": ((int, float), 0),
    "cache": ((str,), 16), "safeguard": ((bool,), 0), "calls": ((int,), 0), "agreement": ((int, float), 0),
}


def sanear(evento: dict[str, Any]) -> dict[str, Any]:
    limpo: dict[str, Any] = {}
    for campo, valor in evento.items():
        regra = CAMPOS.get(campo)
        if regra is None or valor is None:
            continue
        tipos, limite = regra
        # `bool` é `int` em Python: um contador não pode aceitar True por engano, e uma flag não pode virar número
        if isinstance(valor, bool) != (bool in tipos):
            continue
        if not isinstance(valor, tipos):
            continue
        if isinstance(valor, str):
            valor = valor[:limite]
        limpo[campo] = valor
    return limpo


class RetrievalMetrics:
    """Implementa `MetricsSink`. `record` nunca levanta: medir não pode derrubar quem mede."""

    def __init__(self, directory: Path | None) -> None:
        self._dir = directory
        self._lock = threading.Lock()

    @property
    def arquivo(self) -> Path | None:
        return None if self._dir is None else self._dir / "events.jsonl"

    def record(self, event: dict[str, Any]) -> None:
        try:
            limpo = sanear({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event})
            self._contar(limpo)
            if self._dir is not None:
                self._gravar(limpo)
        except Exception:  # noqa: BLE001
            return

    def _contar(self, e: dict[str, Any]) -> None:
        motivo = e.get("fallback_reason", "none")
        _metricas.contar("context_retrieval.pedidos", mode=e.get("mode", "?"),
                         resultado="fallback" if e.get("fallback") else "ok", motivo=motivo)
        if "latency_ms" in e:
            _metricas.observar("context_retrieval.latencia_ms", float(e["latency_ms"]), mode=e.get("mode", "?"))
        if e.get("cost_usd"):
            _metricas.contar("context_retrieval.custo_usd", float(e["cost_usd"]), provider=e.get("provider", "?"))

    def _gravar(self, e: dict[str, Any]) -> None:
        assert self._dir is not None
        linha = json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            arq = self._dir / "events.jsonl"
            if arq.exists() and arq.stat().st_size > MAX_BYTES_DO_ARQUIVO:
                os.replace(arq, self._dir / "events.jsonl.1")
            with arq.open("a", encoding="utf-8") as f:
                f.write(linha)

    def recentes(self, limite: int = 500) -> list[dict[str, Any]]:
        arq = self.arquivo
        if arq is None or not arq.is_file():
            return []
        eventos: list[dict[str, Any]] = []
        try:
            with arq.open("r", encoding="utf-8") as f:
                for linha in f:
                    try:
                        valor = json.loads(linha)
                    except ValueError:
                        continue
                    if isinstance(valor, dict):
                        eventos.append(sanear(valor))
        except OSError:
            return []
        return eventos[-limite:]


def resumir(eventos: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """O que o painel mostra: pedidos, cache, latência, custo, fallbacks e bloqueios de privacidade."""
    lista = list(eventos)
    n = len(lista)
    fallbacks: dict[str, int] = {}
    bloqueios: dict[str, int] = {}
    por_modo: dict[str, int] = {}
    cache = {"hit": 0, "miss": 0}
    for e in lista:
        por_modo[e.get("mode", "?")] = por_modo.get(e.get("mode", "?"), 0) + 1
        if e.get("fallback"):
            r = e.get("fallback_reason", "unknown")
            fallbacks[r] = fallbacks.get(r, 0) + 1
        if e.get("privacy_block_reason"):
            r = e["privacy_block_reason"]
            bloqueios[r] = bloqueios.get(r, 0) + 1
        if e.get("cache") in cache:
            cache[e["cache"]] += 1
    lat = sorted(float(e["latency_ms"]) for e in lista if "latency_ms" in e)

    def pct(p: float) -> float | None:
        return round(lat[min(len(lat) - 1, int(p * len(lat)))], 1) if lat else None

    return {"requests": n, "by_mode": por_modo, "cache": cache,
            "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "n": len(lat)},
            "cost_usd": round(sum(float(e.get("cost_usd", 0)) for e in lista), 6),
            "input_tokens": sum(int(e.get("input_tokens", 0)) for e in lista),
            "fallbacks": fallbacks, "privacy_blocks": bloqueios}

