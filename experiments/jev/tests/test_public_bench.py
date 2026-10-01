"""Benchmark PÚBLICO (Scrapy fixado): golden, regras EXACT/SEMANTIC, travas, harness com provedor falso, mapa do repositório.

Nenhum teste abre rede nem chama o Jev. Os que dependem do checkout (`data/jev-pilot/public/scrapy`, preparado por
`public_bench/prepare.py`) são pulados quando ele não existe."""
from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

import benchmark as bm
import golden as gd
import provider as pv
import repomap
from corpus import repo_root

PUB = Path(bm.__file__).resolve().parent / "public_bench"
CHECKOUT = repo_root() / "data" / "jev-pilot" / "public" / "scrapy"
needs_checkout = pytest.mark.skipif(not (CHECKOUT / ".git").exists(), reason="checkout público ausente (rode public_bench/prepare.py)")


def _meta_items():
    return gd.load(PUB / "golden.json")


def test_public_golden_shape_and_pinning():
    meta, items = _meta_items()
    assert meta["visibility"] == "public" and meta["license"] == "BSD-3-Clause"
    assert meta["source_sha_full"].startswith(meta["source_sha"]) and len(meta["source_sha_full"]) == 40
    assert meta["source_url"] == "https://github.com/scrapy/scrapy.git" and meta["source_tag"] == "2.19.0"
    assert 15 <= len(items) <= 20 and len({i.id for i in items}) == len(items)
    groups = [gd.group_of(i) for i in items]
    assert groups.count("EXACT") == 8 and groups.count("SEMANTIC") == 12
    assert set(meta["required_categories"]) <= {i.category for i in items}


@needs_checkout
def test_public_golden_matches_pinned_checkout_and_group_rules():
    meta, items = _meta_items()
    assert bm.verify_public_checkout(CHECKOUT, meta) == []
    assert gd.validate(items, CHECKOUT, meta) == []
    assert gd.validate_groups(items, meta, CHECKOUT) == []


def test_group_rules_catch_a_leaky_semantic_question_and_a_missing_literal(tmp_path):
    f = tmp_path / "pkg" / "m.py"
    f.parent.mkdir()
    f.write_text("def download_delay():\n    return 1\n", encoding="utf-8")
    meta = {"ubiquitous_words": ["request"]}
    leaky = gd.Item("X1", "Where is the download delay applied?", "SEMANTIC_BEHAVIOR", ("pkg/m.py",), ("download_delay",),
                    (("pkg/m.py", 1, 2),), False, True, "")
    assert any("compartilha tokens" in p for p in gd.validate_groups([leaky], meta, tmp_path))
    nolit = gd.Item("X2", "Where is the delay defined?", "EXACT_SYMBOL", ("pkg/m.py",), ("download_delay",),
                    (("pkg/m.py", 1, 2),), True, False, "")
    assert any("sem literal" in p for p in gd.validate_groups([nolit], meta, tmp_path))
    wrong = gd.Item("X3", "Where is `nope_missing` defined?", "EXACT_SYMBOL", ("pkg/m.py",), ("download_delay",),
                    (("pkg/m.py", 1, 2),), True, False, "")
    assert any("não aparece verbatim" in p for p in gd.validate_groups([wrong], meta, tmp_path))


def test_thresholds_are_preregistered_and_headline_is_fixed_before_results():
    t = json.loads((PUB / "thresholds.json").read_text(encoding="utf-8"))
    assert t["registered_before_any_real_jev_result"] is True and t["THRESHOLD_CHANGED_AFTER_RESULTS"] == "NO"
    th = t["pilot_a_claude_code_retrieval"]
    assert {"T1_exact_no_regression", "T2_semantic_gain", "T3_context_reduction", "T4_latency", "T5_reliability",
            "T6_cost", "T7_privacy_gate", "T8_critical_misses"} <= set(th)
    assert th["pipeline_spec"]["headline_strategy"] == "jev_map"
    assert th["pipeline_spec"]["measured_before_any_jev_call"]["bm25_shortlist30_file_coverage_SEMANTIC"] == 0.0
    assert _meta_items()[0]["headline_strategy"] == "jev_map"
    # os números centrais não podem divergir do piloto privado sem registro
    priv = json.loads((PUB.parent / "thresholds.json").read_text(encoding="utf-8"))["pilot_a_claude_code_retrieval"]
    for k in ("T1_exact_no_regression", "T2_semantic_gain", "T3_context_reduction", "T4_latency", "T6_cost"):
        strip = lambda d: {a: b for a, b in d.items() if a != "why"}             # noqa: E731
        assert strip(th[k]) == strip(priv[k]), k


# --------------------------------------------------------------------------- travas
def test_public_lock_is_off_and_private_lock_is_untouched():
    assert bm.PUBLIC_BENCHMARK_AUTHORIZED is False
    assert bm.PRIVATE_CODE_SEND_APPROVED is False
    import smoke
    assert smoke.SMOKE_RUN_AUTHORIZED is True               # o smoke sintético segue autorizado


@needs_checkout
def test_public_jev_run_is_blocked_even_with_key_and_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv(pv.KEY_ENV, "k" * 24)

    def deny(*a, **k):
        raise AssertionError("rede aberta")
    monkeypatch.setattr(socket.socket, "connect", deny)
    with pytest.raises(SystemExit) as e:
        bm.main(["--golden", str(PUB / "golden.json"), "--corpus-root", str(CHECKOUT), "--provider", "jev",
                 "--confirm-external-send", "--out", str(tmp_path)])
    assert "BLOCKED_AUTHORIZATION" in str(e.value)


def test_public_golden_without_corpus_root_is_refused_and_private_path_still_blocked(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as e:
        bm.main(["--golden", str(PUB / "golden.json"), "--out", str(tmp_path)])
    assert "--corpus-root" in str(e.value)
    monkeypatch.setenv(pv.KEY_ENV, "k" * 24)
    with pytest.raises(SystemExit) as e2:                    # o golden PRIVADO continua BLOCKED_PRIVACY
        bm.main(["--provider", "jev", "--confirm-external-send", "--out", str(tmp_path)])
    assert "BLOCKED_PRIVACY" in str(e2.value)


def test_verify_public_checkout_rejects_the_private_repo_and_unpinned_or_dirty_checkouts(tmp_path):
    meta, _ = _meta_items()
    assert "privado" in bm.verify_public_checkout(repo_root(), meta)[0]
    d = tmp_path / "fake"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    probs = bm.verify_public_checkout(d, meta)
    assert any("HEAD diferente" in p for p in probs) and any("origem diferente" in p for p in probs)


# --------------------------------------------------------------------------- harness (provedor falso)
@needs_checkout
def test_public_harness_runs_with_fake_provider_and_both_variants_without_network(monkeypatch, tmp_path):
    def deny(*a, **k):
        raise AssertionError("rede aberta")
    monkeypatch.setattr(socket.socket, "connect", deny)
    assert bm.main(["--golden", str(PUB / "golden.json"), "--corpus-root", str(CHECKOUT), "--provider", "fake",
                    "--out", str(tmp_path)]) == 0
    res = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert {"ripgrep", "code_search", "jev_rerank", "jev_map"} <= set(res["summary"])
    assert res["meta"]["corpus_visibility"] == "public" and res["meta"]["checkout_verified"] is True
    jm = [r for r in res["rows"] if r["strategy"] == "jev_map"]
    assert len(jm) == 20 and all(r["jev_requests"] == 2 for r in jm)
    assert json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "NOT_EVALUATED"


@needs_checkout
@pytest.mark.parametrize("mode,stage", [("ERROR", "stage_a"), ("TIMEOUT", "stage_a"), ("INVALID_RESPONSE", "stage_a")])
def test_jev_map_falls_back_to_bm25_and_counts_the_failure(mode, stage, tmp_path):
    from fake_provider import FakeJevProvider
    meta, items = _meta_items()
    files, chunks, index = bm._corpus_index(str(CHECKOUT), tuple(meta["corpus"]["roots"]), tuple(meta["corpus"]["extensions"]))
    fm = repomap.build_map(CHECKOUT, files)
    bm.index_root = CHECKOUT
    r, m = bm.jev_map(index, fm, FakeJevProvider(mode), items[0].question, meta["corpus"]["roots"])
    assert m["fallback"] is True and m["failure"].startswith(stage) and r.spans


@needs_checkout
def test_jev_map_stage_b_failure_keeps_stage_a_ranking(monkeypatch):
    from fake_provider import FakeJevProvider
    meta, items = _meta_items()
    files, chunks, index = bm._corpus_index(str(CHECKOUT), tuple(meta["corpus"]["roots"]), tuple(meta["corpus"]["extensions"]))
    fm = repomap.build_map(CHECKOUT, files)
    bm.index_root = CHECKOUT
    prov = FakeJevProvider("HIGH_CONFIDENCE")
    real = prov.retrieve
    calls = {"n": 0}

    def flaky(question, candidates, kind="code"):
        calls["n"] += 1
        if calls["n"] == 2:
            raise pv.ProviderTimeout("etapa B")
        return real(question, candidates, kind)
    monkeypatch.setattr(prov, "retrieve", flaky)
    r, m = bm.jev_map(index, fm, prov, items[8].question, meta["corpus"]["roots"])
    assert m["failure"] == "stage_b:timeout" and m["jev_requests"] == 2 and 0 < len(r.spans) <= bm.MAP_REPRESENT_FILES


@needs_checkout
def test_estimate_covers_both_variants_and_stays_under_the_documented_budget(tmp_path):
    assert bm.main(["--golden", str(PUB / "golden.json"), "--corpus-root", str(CHECKOUT), "--estimate-only",
                    "--out", str(tmp_path)]) == 0
    est = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["estimate"]
    assert est["TOTAL"]["ESTIMATED_JEV_CALLS"] == 60 and est["jev_map"]["map_files"] <= repomap.MAX_OPTIONS
    assert est["max_payload_bytes"] < 100_000 and est["jev_map"]["stage_a_max_payload"] < 100_000


# --------------------------------------------------------------------------- mapa do repositório
def test_repomap_is_deterministic_capped_and_skips_empty_files(tmp_path):
    (tmp_path / "a.py").write_text('"""Does things. More text."""\nclass A:\n    def go(self): ...\n    def __repr__(self): ...\n\ndef run(): ...\n',
                                   encoding="utf-8")
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    (tmp_path / "big.py").write_text("\n".join(f"def function_number_{i}(): ..." for i in range(60)), encoding="utf-8")
    (tmp_path / "bad.py").write_text("def (:\n", encoding="utf-8")
    m1 = repomap.build_map(tmp_path, ["a.py", "empty.py", "big.py", "bad.py"])
    assert m1 == repomap.build_map(tmp_path, ["a.py", "empty.py", "big.py", "bad.py"])
    assert "empty.py" not in m1 and "bad.py" not in m1
    assert "defs: A, A.go, run" in m1["a.py"] and "__repr__" not in m1["a.py"] and m1["a.py"].startswith("Does things.")
    assert all(len(v.encode("utf-8")) <= repomap.LINE_CAP_BYTES for v in m1.values())


def test_repomap_refuses_more_than_255_options(tmp_path):
    files = []
    for i in range(repomap.MAX_OPTIONS + 1):
        (tmp_path / f"m{i}.py").write_text("def f(): ...\n", encoding="utf-8")
        files.append(f"m{i}.py")
    with pytest.raises(ValueError):
        repomap.build_map(tmp_path, files)


def test_retrieval_request_map_kind_is_distinct_and_default_unchanged():
    s1, q1 = pv.retrieval_request("q?", {"a": "x"})
    s2, q2 = pv.retrieval_request("q?", {"a": "x"}, "map")
    assert s1 == s2 and q1["best"]["instructions"] != q2["best"]["instructions"] and "summarizes one file" in q2["best"]["instructions"]
    assert "directly answer" in q1["exists"]["instructions"]
