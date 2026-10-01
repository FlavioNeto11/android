"""Regra híbrida (exploratória): identificadores explícitos, fusão, congelamento por hash e coerência com a baseline ripgrep.
Sem rede, sem Jev."""
from __future__ import annotations

from pathlib import Path

import pytest

import baselines as bl
import golden as gd
import hybrid as hy
from textutil import query_terms

PUB = Path(hy.__file__).resolve().parent / "public_bench"


def test_rule_is_frozen_by_hash_and_version():
    lock = hy.load_lock()
    assert lock["HYBRID_RULE_VERSION"] == hy.RULE_VERSION == "1"
    assert lock["HYBRID_RULE_HASH"] == hy.rule_hash(), "o bloco RULE mudou: nova versão exige novo lock e registro"
    assert lock["frozen_before_any_hybrid_scoring"] is True
    assert set(lock["classification_criteria_for_the_exploratory_replay"]) == {"note", "PROMISING", "NOT_PROMISING", "INCONCLUSIVE"}


@pytest.mark.parametrize("q,expected", [
    ("Which code sends the `request_dropped` signal?", ["request_dropped"]),
    ("Where is the `depth` key of `request.meta` assigned a value?", ["depth", "request.meta"]),
    ("Where is DontCloseSpider caught?", ["DontCloseSpider"]),
    ("Who calls get_retry_request in scrapy.core.engine.", ["get_retry_request", "scrapy.core.engine"]),
    ("Where does _ScrapyAgent get built?", ["_ScrapyAgent"]),
    ("What does fetchPage do, e.g. on errors?", ["fetchPage"]),
    ("What decides that a crawl is finished and starts the shutdown when no work is left?", []),
    ("How are a username and password attached to outgoing requests?", []),
    ("What happens when user-written parsing code blows up unexpectedly?", []),
])
def test_explicit_identifiers(q, expected):
    assert hy.explicit_identifiers(q) == expected


def test_no_identifier_means_hybrid_is_exactly_jev_map():
    assert hy.safeguard_active("How does the system retry?", ["a.py"]) is False
    assert hy.merge_files(False, ["a.py"], ["b.py", "c.py"]) == ["b.py", "c.py"]
    assert hy.merge_regions(False, [["a.py", 1, 5]], [["b.py", 1, 5]]) == [["b.py", 1, 5]]


def test_identifier_without_lexical_hit_does_not_activate():
    assert hy.safeguard_active("Where is `nope_missing` set?", []) is False


def test_active_safeguard_puts_best_lexical_file_first_and_dedups():
    assert hy.safeguard_active("Where is `x_y` set?", ["a.py", "z.py"]) is True
    assert hy.merge_files(True, ["a.py", "z.py"], ["b.py", "a.py", "c.py"]) == ["a.py", "b.py", "c.py"]
    assert hy.merge_files(True, ["a.py"], ["a.py"]) == ["a.py"]


def test_active_regions_take_at_most_two_lexical_windows_and_dedup():
    lex = [["a.py", 1, 5], ["a.py", 20, 30], ["a.py", 40, 50]]
    jev = [["b.py", 3, 9], ["a.py", 1, 5]]
    assert hy.merge_regions(True, lex, jev) == [["a.py", 1, 5], ["a.py", 20, 30], ["b.py", 3, 9]]


def test_cap_regions_matches_the_delivery_cap():
    regs = [["f.py", i, i] for i in range(1, 20)]
    assert len(hy.cap_regions(regs, lambda r: 10)) == hy.DELIVER_MAX_SPANS == 8
    assert len(hy.cap_regions(regs, lambda r: 6000)) == 2                     # 16 KiB: parou antes de passar do teto


def test_rule_never_reads_the_golden_expectations():
    import inspect
    src = inspect.getsource(hy)
    block = src[src.index("# --- RULE BEGIN"):src.index("# --- RULE END")]
    for forbidden in ("expected_files", "expected_regions", "category", "grep_friendly", "semantic_only"):
        assert forbidden not in block.replace("5. A regra não lê expected_files, expected_regions nem a categoria do golden.", "")


def test_identifier_rule_agrees_with_the_ripgrep_baseline_terms_on_the_scrapy_golden():
    """Premissa do replay offline: para toda pergunta do golden, `explicit_identifiers` coincide com os termos que o ripgrep
    baseline usou (regra backtick) ou não há identificador (regra words). Só olha o TEXTO da pergunta."""
    _, items = gd.load(PUB / "golden.json")
    for it in items:
        ids = hy.explicit_identifiers(it.question)
        terms, rule = query_terms(it.question)
        if ids:
            assert rule in ("backtick", "identifier"), it.id
            assert [t.lower() for t in terms] == [i.lower() for i in ids], it.id
        else:
            assert rule == "words", it.id


def test_ripgrep_accepts_explicit_terms(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\nrequest_dropped = 2\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("y = 1\n", encoding="utf-8")
    r = bl.ripgrep(tmp_path, ["pkg"], "irrelevant words here", terms=["request_dropped"])
    assert r.ranked_files() == ["pkg/a.py"] and r.notes["rule"] == "explicit_identifiers"
