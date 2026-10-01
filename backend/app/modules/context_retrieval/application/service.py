"""Serviço de retrieval de contexto: despacha por modo, nunca bloqueia o trabalho e devolve um `ContextPack`.

É a porta que o resto da plataforma usa. `gather` devolve `None` com o retrieval desligado (`DISABLED`) SEM tocar
em disco, em git, em rede ou em métrica: quem chama segue o pipeline antigo, e é isso que torna a integração
inofensiva de mergear.

Modos: `LOCAL_ONLY` entrega léxico + BM25; `SHADOW` roda o caminho híbrido só para medir e entrega o local;
`HYBRID` entrega o híbrido. Todo erro do caminho semântico cai no local (fail-open) com a razão registrada; um erro
do próprio local vira pacote vazio com aviso — o retrieval é auxílio, nunca um ponto de falha.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ..domain.model import (Budget, ContextPack, ContextSelection, FallbackReason, Region, RetrievalMode,
                            RetrievalRequest)
from ..domain.identifiers import query_fingerprint
from ..domain.ports import MetricsSink
from ..domain.sensitive import hard_secret_kind, has_soft_secret
from .hybrid import HybridRetriever
from .local import LocalParts, LocalRetriever

#: Teto do texto materializado no pacote (quando pedido). A região é só um endereço; o texto é conveniência.
MAX_TEXT_BYTES = 16 * 1024


class DisabledContextRetrieval:
    """O serviço com o retrieval desligado: existe para quem chama não precisar de `if`, e não faz NADA."""

    mode = RetrievalMode.DISABLED
    enabled = False

    def gather(self, query: str, *, scope: tuple[str, ...] = (), top_k: int | None = None,
               with_text: bool = False) -> ContextPack | None:
        return None


class ContextRetrievalService:
    def __init__(self, *, mode: RetrievalMode, root: Path, top_k: int, local: LocalRetriever,
                 hybrid: HybridRetriever | None, revision: Callable[[], str],
                 read_text: Callable[[str], str | None], sink: MetricsSink, budget: Budget,
                 provider: str = "none", model: str = "") -> None:
        self.mode = mode
        self._root = root
        self._top_k = top_k
        self._local = local
        self._hybrid = hybrid
        self._revision = revision
        self._read_text = read_text
        self._sink = sink
        self._budget = budget
        self._provider = provider
        self._model = model

    @property
    def enabled(self) -> bool:
        return self.mode is not RetrievalMode.DISABLED

    def gather(self, query: str, *, scope: tuple[str, ...] = (), top_k: int | None = None,
               with_text: bool = False) -> ContextPack | None:
        if not self.enabled:
            return None
        k = top_k or self._top_k
        revision = self._revision()
        request = RetrievalRequest(query=query, root=self._root, revision=revision, scope=scope, top_k=k)
        t0 = time.monotonic()
        delivered, observed = self._select(request)
        warnings = list(delivered.warnings)
        regioes = list(delivered.selected_regions)
        if with_text:
            regioes, avisos = self._materializar(regioes)
            warnings += avisos
        pack = ContextPack(
            query=query, revision=revision, mode=self.mode, origin=delivered.source,
            files=delivered.selected_files, regions=tuple(regioes), confidence=delivered.confidence,
            budget={"limits": asdict(self._budget), "spent": {"cost_usd": round(observed.cost_usd, 6),
                                                              "input_tokens": observed.input_tokens}},
            metadata={**delivered.metadata, "fallback_used": observed.fallback_used,
                      "fallback_reason": observed.fallback_reason.value if observed.fallback_reason else None,
                      "latency_ms": round((time.monotonic() - t0) * 1000, 1)},
            warnings=tuple(dict.fromkeys(warnings)))
        self._registrar(request, delivered, observed)
        return pack

    # ------------------------------------------------------------------ despacho
    def _select(self, request: RetrievalRequest) -> tuple[ContextSelection, ContextSelection]:
        """`(o que se entrega, o que se observou)`. Só diferem no modo sombra."""
        try:
            parts = self._local.parts(request)
        except Exception as exc:  # noqa: BLE001 - o retrieval é auxílio: falhar vira pacote vazio com aviso
            vazio = ContextSelection.empty("local", fallback_used=True, fallback_reason=FallbackReason.PROVIDER_ERROR,
                                           warnings=(f"local_failed:{type(exc).__name__}",))
            return vazio, vazio
        if self.mode is RetrievalMode.LOCAL_ONLY:
            return parts.merged, parts.merged
        hibrido = self._hibrido(request, parts)
        if self.mode is RetrievalMode.SHADOW:
            local_set = {h.path for h in parts.merged.selected_files}
            sombra = {h.path for h in hibrido.selected_files}
            meta = {**parts.merged.metadata, "shadow": True,
                    "agreement": round(len(local_set & sombra) / max(1, len(local_set | sombra)), 3)}
            return replace(parts.merged, metadata=meta), hibrido
        return hibrido, hibrido

    def _hibrido(self, request: RetrievalRequest, parts: LocalParts) -> ContextSelection:
        if self._hybrid is None:
            return _com_fallback(parts.merged, FallbackReason.NO_PROVIDER)
        try:
            return self._hybrid.retrieve_with(request, parts)
        except Exception as exc:  # noqa: BLE001
            return _com_fallback(parts.merged, FallbackReason.PROVIDER_ERROR, f"hybrid_raised:{type(exc).__name__}")

    # ------------------------------------------------------------------ texto das regiões (opcional)
    def _materializar(self, regioes: list[Region]) -> tuple[list[Region], list[str]]:
        saida: list[Region] = []
        avisos: list[str] = []
        restante = MAX_TEXT_BYTES
        for r in regioes:
            texto = self._read_text(r.path)
            if texto is None:
                saida.append(r)
                continue
            trecho = "\n".join(texto.splitlines()[r.start_line - 1:r.end_line])
            if hard_secret_kind(trecho) or has_soft_secret(trecho):
                avisos.append("text_withheld:secret")
                saida.append(r)
                continue
            if len(trecho.encode("utf-8")) > restante:
                avisos.append("text_truncated:budget")
                saida.append(r)
                continue
            restante -= len(trecho.encode("utf-8"))
            saida.append(Region(r.path, r.start_line, r.end_line, trecho))
        return saida, avisos

    # ------------------------------------------------------------------ observabilidade
    def _registrar(self, request: RetrievalRequest, delivered: ContextSelection, observed: ContextSelection) -> None:
        cache = observed.metadata.get("semantic", {})
        cache_estado = None
        if isinstance(cache, dict):
            estado = str(cache.get("stage_a_cache", ""))
            cache_estado = estado if estado in ("hit", "miss") else None
        evento: dict[str, Any] = {
            "retriever": delivered.source, "mode": self.mode.value, "query_fp": query_fingerprint(request.query),
            "revision": request.revision[:24], "provider": self._provider, "model": self._model,
            "files_considered": int(delivered.metadata.get("files_considered", 0) or 0),
            "files_selected": len(delivered.selected_files), "regions_selected": len(delivered.selected_regions),
            "latency_ms": round(delivered.latency_ms + (observed.latency_ms if observed is not delivered else 0), 1),
            "fallback": observed.fallback_used,
            "fallback_reason": observed.fallback_reason.value if observed.fallback_reason else None,
            "privacy_block_reason": _razao_de_privacidade(observed.fallback_reason),
            "input_tokens": observed.input_tokens, "cost_usd": round(observed.cost_usd, 6), "cache": cache_estado,
            "safeguard": bool(delivered.metadata.get("safeguard", False)),
            "agreement": observed.metadata.get("agreement", delivered.metadata.get("agreement")),
        }
        self._sink.record(evento)


def _com_fallback(base: ContextSelection, razao: FallbackReason, aviso: str | None = None) -> ContextSelection:
    return replace(base, fallback_used=True, fallback_reason=razao,
                   warnings=base.warnings + ((aviso,) if aviso else ()))


def _razao_de_privacidade(razao: FallbackReason | None) -> str | None:
    if razao in (FallbackReason.PRIVACY_BLOCK, FallbackReason.SECRET_BLOCK):
        return razao.value
    return None
