"""Redação, métricas, golden, textutil e chunker."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import golden as gd
import metrics as mt
from baselines import Span
from corpus import chunk_file, repo_root
from redact import is_sensitive_path, redact
from textutil import query_terms, scope_for, scoped_roots, tokenize


# --------------------------------------------------------------------------- redact
def test_redact_hard_and_soft_counts_without_values():
    # Montado em tempo de execução: o arquivo não contém nenhum literal com formato de segredo.
    txt = ("Authorization: " + "Bear" + "er " + "ab" * 12 + "\n" + "-----BEGIN " + "RSA PRIVATE KEY" + "-----\n"
           "user a@b.co password='segredo123'")
    r = redact(txt)
    assert r.blocked and set(r.hard) == {"bearer", "private_key"}
    assert "ab" * 12 not in r.text and "a@b.co" not in r.text and "segredo123" not in r.text
    assert r.soft == {"secret_assign": 1, "email": 1}


def test_redact_clean_text_untouched():
    r = redact("def f(x):\n    return x + 1\n")
    assert not r.blocked and r.soft == {} and r.text.startswith("def f")


@pytest.mark.parametrize("path,expected", [
    (".env", True), ("config/config.yaml", True), ("data/poc.sqlite3", True), ("x/credentials.key", True),
    ("backend/app/config.py", False), ("backend/tests/test_x.py", False), ("backend/app/data_model.py", False),
])
def test_sensitive_paths(path, expected):
    assert is_sensitive_path(path) is expected


def test_corpus_hard_hits_are_only_test_fixtures_and_never_reach_a_payload():
    """O corpus que o piloto enviaria tem 6 fixtures de teste com padrão de segredo (valores falsos, conferidos à mão
    em 2026-10-01). Nenhum está em `backend/app`, e o shortlist do benchmark exclui qualquer trecho com achado duro."""
    root = repo_root()
    meta, _ = gd.load(Path(__file__).resolve().parents[1] / "golden.json")
    from corpus import list_files, read_text
    hits = {}
    for f in list_files(root, meta["corpus"]["roots"], meta["corpus"]["extensions"]):
        r = redact(read_text(root, f))
        if r.hard:
            hits[f] = r.hard
    assert hits, "o scanner deveria achar as fixtures conhecidas; se não achou, o scanner regrediu"
    assert all(f.startswith("backend/tests/") for f in hits), f"achado duro fora dos testes: {hits}"
    import benchmark as bm
    from baselines import Bm25Index
    from corpus import Chunk
    bad = Chunk("backend/tests/x.py", 1, 2, "f", "sk" + "-" + "ab" * 20 + " reinicio worker")
    ok = Chunk("backend/app/y.py", 1, 2, "g", "reinicio worker")
    idx = Bm25Index([bad, ok])
    assert [c.file for c in bm.shortlist_candidates(idx, "reinicio worker")] == ["backend/app/y.py"]


# --------------------------------------------------------------------------- métricas
def _sp(f, a, b):
    return Span(f, a, b, "x" * (b - a + 1))


def test_precision_recall_at_k():
    ranked = ["a", "b", "c", "d"]
    exp = {"a", "c"}
    assert mt.precision_at_k(ranked, exp, 1) == 1.0
    assert mt.precision_at_k(ranked, exp, 3) == pytest.approx(2 / 3)
    assert mt.recall_at_k(ranked, exp, 1) == 0.5 and mt.recall_at_k(ranked, exp, 5) == 1.0
    assert mt.recall_at_k([], exp, 3) == 0.0


def test_region_and_delivered_recall():
    delivered = [_sp("a.py", 10, 20), _sp("b.py", 1, 5)]
    regions = [("a.py", 18, 30), ("a.py", 50, 60), ("c.py", 1, 2)]
    assert mt.region_recall(delivered, regions) == pytest.approx(1 / 3)
    assert mt.delivered_recall(delivered, {"a.py", "c.py"}) == 0.5
    assert mt.intersects(_sp("a.py", 1, 5), ("a.py", 5, 9)) and not mt.intersects(_sp("a.py", 1, 4), ("a.py", 5, 9))


def test_percentile_and_aggregate():
    assert mt.percentile([1, 2, 3, 4], 50) == 2.5 and math.isnan(mt.percentile([], 50))
    rows = [dict(p1=1, p3=1 / 3, r3=1, r5=1, delivered_recall=1, region_recall=1, latency_ms=10, files_examined=2,
                 bytes_read=100, bytes_returned=50, context_bytes_returned=40, naive_read_bytes=400,
                 read_bytes_avoided=360, failure=None, grep_friendly=True, jev_requests=1, jev_cost_usd=0.001),
            dict(p1=0, p3=0, r3=0, r5=0, delivered_recall=0, region_recall=0, latency_ms=30, files_examined=1,
                 bytes_read=10, bytes_returned=5, context_bytes_returned=5, naive_read_bytes=100,
                 read_bytes_avoided=95, failure="timeout", grep_friendly=False, jev_requests=1, jev_cost_usd=0.0)]
    a = mt.aggregate(rows)
    assert a["n"] == 2 and a["RECALL_AT_3"] == 0.5 and a["FAILURES"] == 1 and a["TIMEOUTS"] == 1
    assert a["JEV_REQUESTS"] == 2 and a["JEV_COST_USD_PROXY"] == pytest.approx(0.001)
    assert "PROXY_METRIC" in a["METRIC_KIND"]
    assert mt.aggregate(rows, "grep_friendly")["n"] == 1


# --------------------------------------------------------------------------- textutil / chunker
def test_query_terms_rules_are_mechanical():
    assert query_terms("Onde `_pedir_reinicio` é chamado?") == (["_pedir_reinicio"], "backtick")
    assert query_terms("Onde o objetivo passa a ter blocked_kind='approval'?")[1] == "assignment"
    assert query_terms("Onde está 'Teto de gasto de IA ... atingido'?")[1] == "quoted"
    assert query_terms("Quem decide o roteamento?")[1] == "words"


def test_scope_rules():
    roots = ["backend/app", "backend/tests"]
    assert scope_for("Qual código de produção cria X?") == "app" and scoped_roots("fora dos testes", roots) == ["backend/app"]
    assert scoped_roots("Quais testes cobrem X?", roots) == ["backend/tests"]
    assert scoped_roots("Onde X é chamado?", roots) == roots


def test_tokenize_splits_identifiers_and_folds_accents():
    t = tokenize("_pedir_reinicio manutenção CamelCaseName")
    assert "pedir" in t and "reinicio" in t and "pedir_reinicio" in t and "manutencao" in t and "camel" in t


def test_chunker_splits_long_functions_and_covers_lines():
    body = "\n".join(f"    x{i} = {i}" for i in range(200))
    src = f"import os\n\ndef big():\n{body}\n\ndef small():\n    return 1\n"
    chunks = chunk_file("m.py", src)
    big = [c for c in chunks if c.name == "big"]
    assert len(big) >= 3 and all(c.end - c.start + 1 <= 60 for c in big)
    assert any(c.name == "small" for c in chunks)
    assert all(c.start <= c.end for c in chunks)


# --------------------------------------------------------------------------- golden
GOLDEN = Path(__file__).resolve().parents[1] / "golden.json"


def test_golden_loads_and_is_consistent_with_working_tree():
    meta, items = gd.load(GOLDEN)
    assert gd.validate(items, repo_root()) == []
    assert 10 <= len(items) <= 20
    assert {i.category for i in items} >= gd.REQUIRED_CATEGORIES
    assert meta["source_sha"] == "3eba639"


def test_golden_has_both_groups_and_no_overlap():
    _, items = gd.load(GOLDEN)
    assert sum(i.grep_friendly for i in items) >= 4 and sum(i.semantic_only for i in items) >= 4
    assert not any(i.grep_friendly and i.semantic_only for i in items)


def test_golden_validation_detects_problems(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    base = dict(id="X1", question="q", category="EXACT_SYMBOL", expected_files=("a.py",), expected_symbols=("f",),
                expected_regions=(("a.py", 1, 2),), grep_friendly=True, semantic_only=False, notes="")
    ok = gd.Item(**base)
    assert [p for p in gd.validate([ok] * 1, tmp_path) if "fora do arquivo" in p or "inexistente" in p] == []
    bad_region = gd.Item(**{**base, "id": "X2", "expected_regions": (("a.py", 1, 99),)})
    bad_file = gd.Item(**{**base, "id": "X3", "expected_files": ("nope.py",), "expected_regions": (("nope.py", 1, 2),)})
    bad_sym = gd.Item(**{**base, "id": "X4", "expected_symbols": ("zzz_inexistente",)})
    probs = gd.validate([ok, bad_region, bad_file, bad_sym], tmp_path)
    assert any("X2" in p and "fora do arquivo" in p for p in probs)
    assert any("X3" in p and "inexistente" in p for p in probs)
    assert any("X4" in p and "símbolo" in p for p in probs)


def test_golden_expected_regions_still_contain_their_symbols():
    """Âncora contra deriva: para itens com símbolo, o símbolo aparece numa região esperada OU no arquivo."""
    root = repo_root()
    _, items = gd.load(GOLDEN)
    for it in items:
        for sym in it.expected_symbols:
            needle = sym.split(".")[-1]
            texts = [(root / f).read_text(encoding="utf-8", errors="replace") for f in it.expected_files]
            assert any(needle in t for t in texts), (it.id, sym)
