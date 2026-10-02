"""Retriever híbrido: salvaguarda lexical + ranking semântico, com o local como rede de segurança (regra v1).

Reescrita limpa da regra validada no piloto (`claude/jev-pilot:experiments/jev/hybrid.py`; o piloto segue como
evidência, não como dependência). Não conhece provedor nenhum: recebe um `ContextRetriever` semântico.

- Há identificador explícito na pergunta E o léxico achou arquivo ("salvaguarda ativa"): o melhor arquivo lexical
  (`lexical_preserve`, 1 por padrão — o que o piloto validou) fica no topo, o ranking semântico completa sem
  duplicar, e as janelas lexicais desse arquivo (até 2) entram antes das regiões semânticas.
- Senão: o ranking semântico é o principal.
- O semântico falhou ou veio vazio: o resultado é o do local, com `fallback_used` e a razão registrada.

Não se otimiza para top-1: o contrato é top-3/top-5 de contexto para o agente.
"""
from __future__ import annotations

import time
from dataclasses import replace

from ..domain.model import (ContextSelection, FallbackReason, FileHit, Region, RetrievalRequest)
from ..domain.ports import ContextRetriever
from .local import MAX_REGIONS, LocalParts, LocalRetriever

MAX_LEXICAL_WINDOWS = 2


class HybridRetriever:
    name = "hybrid"

    def __init__(self, *, local: LocalRetriever, semantic: ContextRetriever, lexical_preserve: int = 1) -> None:
        self._local = local
        self._semantic = semantic
        self._preserve = lexical_preserve

    def retrieve(self, request: RetrievalRequest) -> ContextSelection:
        return self.retrieve_with(request, self._local.parts(request))

    def retrieve_with(self, request: RetrievalRequest, parts: LocalParts) -> ContextSelection:
        """O mesmo, com a busca local já feita (o serviço a reaproveita no modo sombra)."""
        t0 = time.monotonic()
        try:
            sem = self._semantic.retrieve(request)
        except Exception as exc:  # noqa: BLE001 - o semântico nunca pode bloquear o trabalho (fail-open)
            sem = ContextSelection.empty("semantic", fallback_used=True, fallback_reason=FallbackReason.PROVIDER_ERROR,
                                         warnings=(f"semantic_raised:{type(exc).__name__}",))
        gasto = {"cost_usd": sem.cost_usd, "input_tokens": sem.input_tokens}
        if sem.fallback_used or not sem.selected_files:
            razao = sem.fallback_reason or FallbackReason.EMPTY_SEMANTIC
            return replace(parts.merged, source=f"{self.name}:local_fallback", fallback_used=True,
                           fallback_reason=razao, latency_ms=parts.merged.latency_ms + sem.latency_ms,
                           warnings=tuple(dict.fromkeys(parts.merged.warnings + sem.warnings)),
                           metadata={**parts.merged.metadata, "semantic": _meta(sem)}, **gasto)

        ativa = parts.safeguard
        lex_topo = list(parts.lexical.selected_files[:self._preserve]) if ativa else []
        arquivos: list[FileHit] = []
        vistos: set[str] = set()
        for hit in [*lex_topo, *sem.selected_files]:
            if hit.path not in vistos and len(arquivos) < request.top_k:
                vistos.add(hit.path)
                arquivos.append(hit)

        regioes: list[Region] = []
        chaves: set[tuple[str, int, int]] = set()

        def adiciona(rs: list[Region]) -> None:
            for r in rs:
                if r.path in vistos and r.key() not in chaves and len(regioes) < MAX_REGIONS:
                    chaves.add(r.key())
                    regioes.append(r)

        if ativa and lex_topo:
            adiciona([r for r in parts.lexical.selected_regions if r.path == lex_topo[0].path][:MAX_LEXICAL_WINDOWS])
        adiciona(list(sem.selected_regions))
        # Arquivo escolhido sem nenhuma região (etapa B falhou, ou foi bloqueada): a janela local, se houver, ocupa o lugar.
        cobertos = {r.path for r in regioes}
        faltando = [h.path for h in arquivos if h.path not in cobertos]
        if faltando:
            adiciona([r for r in (*parts.lexical.selected_regions, *parts.bm25.selected_regions)
                      if r.path in faltando][:MAX_REGIONS])

        local_topo = {h.path for h in parts.merged.selected_files}
        meta = {**parts.merged.metadata, "safeguard": ativa, "semantic": _meta(sem),
                "agreement": round(len(local_topo & vistos) / max(1, len(vistos)), 3)}
        return ContextSelection(
            selected_files=tuple(arquivos), selected_regions=tuple(regioes), source=self.name,
            latency_ms=parts.merged.latency_ms + (time.monotonic() - t0) * 1000,
            warnings=tuple(dict.fromkeys(parts.merged.warnings + sem.warnings)), metadata=meta, **gasto)


def _meta(sem: ContextSelection) -> dict[str, object]:
    """Só o que ajuda a medir; nada de conteúdo."""
    return {"source": sem.source, "files": len(sem.selected_files), "regions": len(sem.selected_regions),
            "fallback_reason": sem.fallback_reason.value if sem.fallback_reason else None,
            "latency_ms": round(sem.latency_ms, 1), **{k: v for k, v in sem.metadata.items()
                                                       if isinstance(v, (int, float, str, bool))}}
