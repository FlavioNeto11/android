"""Regressão da regra híbrida v1 contra o que o piloto mediu (holdout público Poetry 2.5.1, 30 perguntas).

`simulated`: não chama nada. A fixture (`fixtures/context_retrieval/pilot_regression.json`, gerada por
`scripts/gen-pilot-regression-fixture.py` a partir dos resultados públicos do piloto) guarda, por pergunta, o que o ripgrep
e o `jev_map` devolveram e o que a regra do piloto produziu a partir deles. Aqui a implementação NOVA recebe as mesmas
entradas por meio de retrievers de mentira e tem de chegar às mesmas saídas. Nada do código do piloto é importado.

Divergências INTENCIONAIS (documentadas em `docs/dominios/context-retrieval.md`):
1. O piloto entrega até 8 arquivos; aqui o contrato é `top_k` (5 por padrão, e 3 também é testado): comparamos o PREFIXO.
2. O piloto corta as regiões por 8 trechos e 16 KiB; aqui as regiões não carregam texto (o corte em bytes não existe) e só
   entram regiões de arquivos que ficaram no `top_k`: comparamos a saída do piloto filtrada por esse conjunto e cortada em 8.
3. Piloto sem resultado semântico não tinha fallback definido; aqui cai no local (testado à parte, abaixo).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.context_retrieval.application.hybrid import MAX_LEXICAL_WINDOWS, HybridRetriever
from app.modules.context_retrieval.application.local import MAX_REGIONS, LocalRetriever
from app.modules.context_retrieval.domain.identifiers import explicit_identifiers
from app.modules.context_retrieval.domain.model import (ContextSelection, FallbackReason, FileHit, Region,
                                                        RetrievalRequest)

FIXTURE = Path(__file__).parent / "fixtures" / "context_retrieval" / "pilot_regression.json"
CASOS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


class _Fixo:
    """Retriever que devolve o que o piloto mediu."""

    def __init__(self, name: str, files: list[str], regions: list[list[Any]], *, fallback: FallbackReason | None = None):
        self.name = name
        self._files = files
        self._regions = regions
        self._fallback = fallback

    def retrieve(self, request: RetrievalRequest) -> ContextSelection:
        if self._fallback:
            return ContextSelection.empty(self.name, fallback_used=True, fallback_reason=self._fallback)
        return ContextSelection(
            selected_files=tuple(FileHit(f, 1.0 / (i + 1), self.name) for i, f in enumerate(self._files)),
            selected_regions=tuple(Region(r[0], r[1], r[2]) for r in self._regions), source=self.name)


def _hibrido(caso: dict[str, Any], *, semantico_cai: FallbackReason | None = None) -> HybridRetriever:
    lexical = _Fixo("lexical", caso["rg_files"], caso["rg_regions"])
    bm25 = _Fixo("bm25", [], [])
    semantico = _Fixo("semantic", caso["jev_files"], caso["jev_regions"], fallback=semantico_cai)
    return HybridRetriever(local=LocalRetriever(lexical, bm25), semantic=semantico)


def _req(caso: dict[str, Any], k: int) -> RetrievalRequest:
    return RetrievalRequest(query=caso["question"], root=Path("."), revision="poetry-94b6e35", top_k=k)


def test_a_fixture_e_representativa_e_consistente_com_o_piloto() -> None:
    assert len(CASOS) == 30
    grupos = {c["group"] for c in CASOS}
    assert grupos == {"EXACT", "SEMANTIC", "MIXED"}
    assert any(c["safeguard"] for c in CASOS) and any(not c["safeguard"] for c in CASOS)
    for c in CASOS:                                    # a regra pura do piloto reproduz o que o piloto de fato entregou
        assert c["rule_files"][:8] == c["pilot_files"][:8], c["id"]
        assert c["pilot_fallback"] is False


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: f"{c['id']}-{c['group']}")
def test_identificadores_explicitos_iguais_aos_do_piloto(caso: dict[str, Any]) -> None:
    assert explicit_identifiers(caso["question"]) == caso["identifiers"]


@pytest.mark.parametrize("k", [3, 5])
@pytest.mark.parametrize("caso", CASOS, ids=lambda c: f"{c['id']}-{c['group']}")
def test_arquivos_e_regioes_da_regra_nova_batem_com_a_regra_do_piloto(caso: dict[str, Any], k: int) -> None:
    sel = _hibrido(caso).retrieve(_req(caso, k))
    assert sel.metadata["safeguard"] is caso["safeguard"]
    esperados = caso["rule_files"][:k]
    assert [h.path for h in sel.selected_files] == esperados
    # regiões: a saída do piloto, só dos arquivos que ficaram no top-k, sem repetir, até 8 (divergência 2)
    dentro = set(esperados)
    vistas: set[tuple[Any, ...]] = set()
    esperadas: list[tuple[Any, ...]] = []
    for r in caso["rule_regions"]:
        t = tuple(r)
        if r[0] in dentro and t not in vistas and len(esperadas) < MAX_REGIONS:
            vistas.add(t)
            esperadas.append(t)
    obtidas = [(r.path, r.start_line, r.end_line) for r in sel.selected_regions]
    # Estrito: as regiões do piloto vêm na mesma ordem, no começo. A nova pode ACRESCENTAR janelas locais no fim (a
    # arquivo semântico sem região), nunca reordena nem remove as do piloto. Medido: 60 de 60 (30 casos x top-3/top-5).
    assert obtidas[:len(esperadas)] == esperadas, caso["id"]
    assert not sel.fallback_used


@pytest.mark.parametrize("caso", [c for c in CASOS if c["safeguard"]], ids=lambda c: f"{c['id']}-{c['group']}")
def test_exact_o_melhor_arquivo_lexical_fica_no_topo_e_as_janelas_dele_vem_primeiro(caso: dict[str, Any]) -> None:
    sel = _hibrido(caso).retrieve(_req(caso, 5))
    assert sel.selected_files[0].path == caso["rg_files"][0]
    janelas = [r for r in caso["rg_regions"] if r[0] == caso["rg_files"][0]][:MAX_LEXICAL_WINDOWS]
    assert [(r.path, r.start_line, r.end_line) for r in sel.selected_regions[:len(janelas)]] == [tuple(j) for j in janelas]


@pytest.mark.parametrize("caso", [c for c in CASOS if not c["safeguard"]], ids=lambda c: f"{c['id']}-{c['group']}")
def test_sem_salvaguarda_o_ranking_e_o_do_semantico(caso: dict[str, Any]) -> None:
    sel = _hibrido(caso).retrieve(_req(caso, 5))
    assert [h.path for h in sel.selected_files] == caso["jev_files"][:5]


@pytest.mark.parametrize("razao", [FallbackReason.TIMEOUT, FallbackReason.PRIVACY_BLOCK, FallbackReason.BUDGET_EXCEEDED])
def test_fallback_e_bloqueio_de_privacidade_entregam_o_local_e_registram_a_razao(razao: FallbackReason) -> None:
    for caso in CASOS[:8] + CASOS[-4:]:
        sel = _hibrido(caso, semantico_cai=razao).retrieve(_req(caso, 5))
        assert sel.fallback_used and sel.fallback_reason is razao
        # o que entrega é o local: com o BM25 vazio, é o resultado lexical, exatamente como o ripgrep devolveu
        assert [h.path for h in sel.selected_files] == caso["rg_files"][:5]
