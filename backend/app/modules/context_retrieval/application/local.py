"""Retrieval local: léxico (ripgrep) + BM25, fundidos por uma regra só. Sem rede, sem provedor.

É o piso do sistema: `LOCAL_ONLY` entrega isto, e todo caminho semântico que falha (timeout, 429, chave ausente,
orçamento, bloqueio de privacidade) volta para isto. Por isso nada aqui pode depender de um provedor.

Regra de fusão: identificador explícito na pergunta E achado lexical = o léxico manda (a pessoa nomeou o símbolo);
senão o BM25 manda e o léxico completa. Os arquivos saem sem repetição, até `top_k`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..domain.identifiers import explicit_identifiers
from ..domain.model import ContextSelection, FileHit, Region, RetrievalRequest
from ..domain.ports import ContextRetriever

#: Teto de janelas entregues (o mesmo corte do piloto: 8 trechos).
MAX_REGIONS = 8


@dataclass(frozen=True)
class LocalParts:
    """As três saídas, para o híbrido reaproveitar a mesma busca em vez de refazê-la."""

    lexical: ContextSelection
    bm25: ContextSelection
    merged: ContextSelection
    safeguard: bool


class LocalRetriever:
    name = "local"

    def __init__(self, lexical: ContextRetriever, bm25: ContextRetriever) -> None:
        self._lexical = lexical
        self._bm25 = bm25

    def retrieve(self, request: RetrievalRequest) -> ContextSelection:
        return self.parts(request).merged

    def parts(self, request: RetrievalRequest) -> LocalParts:
        t0 = time.monotonic()
        lex = self._lexical.retrieve(request)
        bm = self._bm25.retrieve(request)
        safeguard = bool(explicit_identifiers(request.query)) and bool(lex.selected_files)
        primeiro, segundo = (lex, bm) if safeguard else (bm, lex)

        arquivos: list[FileHit] = []
        vistos: set[str] = set()
        for fonte in (primeiro, segundo):
            for hit in fonte.selected_files:
                if hit.path not in vistos and len(arquivos) < request.top_k:
                    vistos.add(hit.path)
                    arquivos.append(hit)

        regioes: list[Region] = []
        chaves: set[tuple[str, int, int]] = set()
        for hit in arquivos:
            do_primeiro = [r for r in primeiro.selected_regions if r.path == hit.path]
            do_segundo = [r for r in segundo.selected_regions if r.path == hit.path]
            for r in (do_primeiro or do_segundo):
                if r.key() not in chaves and len(regioes) < MAX_REGIONS:
                    chaves.add(r.key())
                    regioes.append(r)

        merged = ContextSelection(
            selected_files=tuple(arquivos), selected_regions=tuple(regioes), source=self.name,
            latency_ms=(time.monotonic() - t0) * 1000,
            warnings=tuple(dict.fromkeys(lex.warnings + bm.warnings)),
            metadata={"safeguard": safeguard, "lexical_files": len(lex.selected_files),
                      "bm25_files": len(bm.selected_files),
                      "files_considered": int(bm.metadata.get("files_considered", 0)
                                              or lex.metadata.get("files_considered", 0))})
        return LocalParts(lexical=lex, bm25=bm, merged=merged, safeguard=safeguard)
