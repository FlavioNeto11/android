"""Holdout confirmatório (Poetry 2.5.1): golden, regras de grupo (incl. MIXED), limiares, congelamento, travas, harness com provedor falso
e avaliador. Nenhum teste abre rede nem chama o Jev. Os que dependem do checkout (`data/jev-pilot/public/poetry`, preparado por
`holdout_bench/prepare.py`) são pulados quando ele não existe."""
from __future__ import annotations

import json
import socket
import sys
from math import comb
from pathlib import Path

import pytest

import baselines as bl
import benchmark as bm
import golden as gd
import holdout_eval as he
import hybrid as hy
import provider as pv
from corpus import repo_root

HB = Path(bm.__file__).resolve().parent / "holdout_bench"
CHECKOUT = repo_root() / "data" / "jev-pilot" / "public" / "poetry"
needs_checkout = pytest.mark.skipif(not (CHECKOUT / ".git").exists(), reason="checkout do holdout ausente (rode holdout_bench/prepare.py)")


def _load():
    return gd.load(HB / "golden.json")


def test_holdout_golden_shape_and_pinning():
    meta, items = _load()
    assert meta["visibility"] == "public" and meta["license"] == "MIT" and meta["kind"] == "CONFIRMATORY_HOLDOUT"
    assert meta["source_sha_full"].startswith(meta["source_sha"]) and len(meta["source_sha_full"]) == 40
    assert meta["source_url"] == "https://github.com/python-poetry/poetry.git" and meta["source_tag"] == "2.5.1"
    assert meta["variants"] == ["jev_map", "hybrid"] and meta["headline_strategy"] == "hybrid" and meta["evaluation"] == "holdout"
    assert 24 <= len(items) <= 30 and meta["item_count"] == [24, 30] and len({i.id for i in items}) == len(items)
    groups = [gd.group_of(i) for i in items]
    assert groups.count("EXACT") >= 12 and groups.count("SEMANTIC") >= 12 and groups.count("MIXED") == 6 and None not in groups
    assert set(meta["required_categories"]) <= {i.category for i in items}


def test_holdout_has_no_scrapy_questions_or_corpus():
    meta, items = _load()
    pub_meta, pub_items = gd.load(HB.parent / "public_bench" / "golden.json")
    assert {i.question for i in items}.isdisjoint({i.question for i in pub_items})
    assert all(f.startswith("src/poetry/") for i in items for f in i.expected_files)
    assert "scrapy" not in json.dumps([i.question for i in items]).lower()


def test_holdout_hybrid_rule_reference_matches_the_frozen_rule():
    meta, _ = _load()
    assert meta["hybrid_rule"]["hash"] == hy.rule_hash() == hy.load_lock()["HYBRID_RULE_HASH"]
    th = json.loads((HB / "thresholds.json").read_text(encoding="utf-8"))
    assert th["hybrid_rule"]["hash"] == hy.rule_hash() and th["strategy_under_test"] == "hybrid"


@needs_checkout
def test_holdout_golden_matches_pinned_checkout_and_group_rules():
    meta, items = _load()
    assert bm.verify_public_checkout(CHECKOUT, meta) == []
    assert gd.validate(items, CHECKOUT, meta) == []
    top = lambda q, ids: bl.ripgrep(CHECKOUT, meta["corpus"]["roots"], q, terms=ids).ranked_files()      # noqa: E731
    assert gd.validate_groups(items, meta, CHECKOUT, lexical_top=top) == []


@needs_checkout
def test_holdout_repo_map_fits_the_choice_limit_and_semantic_questions_do_not_trigger_the_safeguard():
    import repomap
    from corpus import list_files
    meta, items = _load()
    files = list_files(CHECKOUT, meta["corpus"]["roots"], meta["corpus"]["extensions"])
    assert len(repomap.build_map(CHECKOUT, files)) <= repomap.MAX_OPTIONS
    for it in items:
        ids = hy.explicit_identifiers(it.question)
        assert bool(ids) == (gd.group_of(it) in ("EXACT", "MIXED")), it.id


def test_group_rules_mixed_and_semantic_identifier_checks(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def alpha_one():\n    pass\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("x = 'alpha_one'\n", encoding="utf-8")
    meta = {"ubiquitous_words": []}
    mixed = gd.Item("M", "How does `alpha_one` behave?", "MIXED_X", ("pkg/a.py",), ("alpha_one",), (("pkg/a.py", 1, 2),), False, False, "")
    assert gd.validate_groups([mixed], meta, tmp_path, lexical_top=lambda q, ids: ["pkg/b.py", "pkg/a.py"]) == []
    assert any("NÃO esteja" in p for p in gd.validate_groups([mixed], meta, tmp_path, lexical_top=lambda q, ids: ["pkg/a.py"]))
    assert any("não verificável" in p for p in gd.validate_groups([mixed], meta, tmp_path))
    no_id = gd.Item("M", "How does it behave?", "MIXED_X", ("pkg/a.py",), ("alpha_one",), (("pkg/a.py", 1, 2),), False, False, "")
    assert any("identificador explícito" in p for p in gd.validate_groups([no_id], meta, tmp_path, lexical_top=lambda q, ids: []))
    sem = gd.Item("S", "Where is get_thing_done used?", "SEMANTIC_X", ("pkg/a.py",), ("alpha_one",), (("pkg/a.py", 1, 2),), False, True, "")
    assert any("identificador explícito" in p for p in gd.validate_groups([sem], meta, tmp_path))


def test_item_count_bound_comes_from_meta_and_defaults_to_the_old_range(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    it = gd.Item("A", "q", "C", ("a.py",), (), (("a.py", 1, 1),), False, False, "")
    assert any("entre 10 e 20" in p for p in gd.validate([it] * 3, tmp_path))
    assert not any("itens" in p for p in gd.validate([gd.Item(f"I{i}", "q", "C", ("a.py",), (), (("a.py", 1, 1),), False, False, "") for i in range(24)],
                                                     tmp_path, {"item_count": [24, 30]}))


# --------------------------------------------------------------------------- limiares
def test_thresholds_are_preregistered_with_item_counts_and_binomial_tables_are_correct():
    th = json.loads((HB / "thresholds.json").read_text(encoding="utf-8"))
    assert th["registered_before_any_holdout_jev_call"] is True and th["THRESHOLD_CHANGED_AFTER_RESULTS"] == "NO"
    c = th["criteria"]
    assert set(c) == {"H1_exact_no_material_regression", "H2_mixed_safeguard_cost", "H3_semantic_gain", "H4_context_reduction",
                      "H5_latency", "H6_reliability", "H7_cost", "H8_critical_misses", "H9_provenance_and_privacy"}
    assert c["H1_exact_no_material_regression"]["tolerance_items"] == 1 and c["H8_critical_misses"]["max_items"] == 2
    assert c["H6_reliability"]["failed_questions_max"] == 1 and c["H6_reliability"]["of"] == 30
    assert "BLOCKED_PRIVACY" in th["decision_purpose"] and "NÃO autoriza" in th["decision_purpose"]

    tail = lambda n, p, k: sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))      # noqa: E731
    h3 = c["H3_semantic_gain"]["pass_probability_of_a_retriever_with_true_recall_p"]
    for k_label, k in (("need_8_of_12", 8), ("need_10_of_12", 10)):
        for p, v in h3[k_label].items():
            assert round(tail(12, float(p), k), 3) == v, (k_label, p)
    for q, v in c["H1_exact_no_material_regression"]["false_fail_if_per_item_regression_probability"].items():
        assert round(1 - tail(12, 1 - float(q), 11), 3) == v, q                          # P(>= 2 regressões em 12)
    assert th["revision_history"][0]["changed"].startswith("H3")                         # a única revisão, antes de qualquer chamada
    # a 1ª redação do H3 (+6 itens) é inalcançável com a melhor baseline medida (6,5/12): é por isso que foi revista
    assert 6.5 + 6 > 12
    need = lambda b: max(8, b + (12 - b) / 2)                                           # noqa: E731
    assert {k: need(float(k)) for k in c["H3_semantic_gain"]["required_items_by_best_baseline"]} == \
        {k: float(v) for k, v in c["H3_semantic_gain"]["required_items_by_best_baseline"].items()}
    assert all(need(b) <= 12 for b in range(0, 10))                                     # sempre alcançável quando há folga


def test_thresholds_were_not_derived_from_the_scrapy_result():
    th = json.dumps(json.loads((HB / "thresholds.json").read_text(encoding="utf-8")), ensure_ascii=False)
    for scrapy_number in ("0.958", "0,958", "0.875", "0,875"):
        assert scrapy_number not in th


# --------------------------------------------------------------------------- avaliador
def _grp(n, r3, ctx=1000.0, naive=9000.0, lat50=800.0, lat95=1200.0, cost=0.0, fails=0):
    return {"n": n, "RECALL_AT_1": r3, "RECALL_AT_3": r3, "RECALL_AT_5": r3, "REGION_RECALL": r3, "CONTEXT_BYTES_RETURNED_MEDIAN": ctx,
            "NAIVE_READ_BYTES_MEDIAN": naive, "LATENCY_MS_P50": lat50, "LATENCY_MS_P95": lat95, "FAILURES": fails, "JEV_COST_USD_PROXY": cost}


def _result(hy_ex=1.0, hy_mx=1.0, hy_se=1.0, jm_mx=1.0, rg_se=0.5, bm_se=0.5, bm_ex=1.0, rg_ex=1.0, bm_mx=0.5, rg_mx=0.5, **kw):
    def s(ex, mx, se, **k):
        return {"all": _grp(30, (ex + mx + se) / 3, **k), "grep_friendly": _grp(12, ex, **k), "mixed": _grp(6, mx, **k), "semantic_only": _grp(12, se, **k)}
    summary = {"hybrid": s(hy_ex, hy_mx, hy_se, **kw), "jev_map": s(hy_ex, jm_mx, hy_se, cost=0.03),
               "ripgrep": s(rg_ex, rg_mx, rg_se, ctx=4000.0), "code_search": s(bm_ex, bm_mx, bm_se, ctx=8000.0)}
    rows = [{"id": f"H{i:02d}", "strategy": st, "r3": 1.0, "failure": None} for st in ("hybrid", "jev_map", "ripgrep", "code_search") for i in range(30)]
    return {"meta": {"provider": "jev", "thresholds_path": str(HB / "thresholds.json"), "aborted": None, "checkout_verified": True,
                     "redactions": 0, "network_attempts": 60}, "summary": summary, "rows": rows}


def test_evaluator_pass_fail_and_reservations():
    assert he.evaluate(_result())["verdict"] == "PASS"
    ok = he.evaluate(_result(hy_se=9 / 12, rg_se=0.5, bm_se=0.5))                            # melhor baseline 6 itens: precisa de max(8, 6+3) = 9
    assert ok["criteria"]["H3"]["observed"]["required_items"] == 9 and ok["criteria"]["H3"]["ok"] is True and ok["verdict"] == "PASS"
    short = he.evaluate(_result(hy_se=8 / 12, rg_se=0.5, bm_se=0.5))
    assert short["criteria"]["H3"]["ok"] is False and short["verdict"] == "FAIL"
    assert he.evaluate(_result(hy_se=0.5, rg_se=0.5))["verdict"] == "FAIL"                   # sem ganho
    assert he.evaluate(_result(hy_ex=10 / 12))["criteria"]["H1"]["ok"] is False              # 2 itens abaixo da melhor baseline
    assert he.evaluate(_result(hy_ex=11 / 12))["criteria"]["H1"]["ok"] is True               # 1 item de tolerância
    low_mixed = he.evaluate(_result(hy_mx=2 / 6, jm_mx=2 / 6, bm_mx=1.0))
    assert low_mixed["criteria"]["H2"]["ok"] is False and low_mixed["verdict"] == "PASS_WITH_RESERVATIONS"
    slow = he.evaluate(_result(lat50=2000.0))
    assert slow["criteria"]["H5"]["ok"] is False and slow["verdict"] == "PASS_WITH_RESERVATIONS"


def test_evaluator_insufficient_evidence_cases():
    assert he.evaluate({"meta": {"provider": "fake", "thresholds_path": str(HB / "thresholds.json")}})["verdict"] == "NOT_EVALUATED"
    r = _result()
    r["meta"]["aborted"] = "401"
    assert he.evaluate(r)["verdict"] == "INSUFFICIENT_EVIDENCE"
    r2 = _result(rg_se=11 / 12)                                                                # melhor baseline sem folga
    assert he.evaluate(r2)["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_evaluator_critical_misses_and_reliability_and_privacy():
    r = _result()
    for row in r["rows"]:
        if row["strategy"] == "hybrid" and row["id"] in {"H00", "H01", "H02"}:
            row["r3"] = 0.0
    assert he.evaluate(r)["criteria"]["H8"]["observed"]["critical_misses"] == 3
    assert he.evaluate(r)["verdict"] == "FAIL"
    r = _result()
    for row in r["rows"]:
        if row["strategy"] == "jev_map" and row["id"] in {"H00", "H01"}:
            row["failure"] = "timeout"
    assert he.evaluate(r)["criteria"]["H6"]["ok"] is False
    r = _result()
    r["meta"]["network_attempts"] = 61
    assert he.evaluate(r)["criteria"]["H9"]["ok"] is False


# --------------------------------------------------------------------------- travas e harness
def test_holdout_lock_is_off_and_private_code_stays_blocked():
    assert bm.PUBLIC_BENCHMARK_AUTHORIZED is False             # a chave do holdout é a MESMA trava: só muda em commit próprio, com autorização
    assert bm.PRIVATE_CODE_SEND_APPROVED is False


@needs_checkout
def test_holdout_jev_run_is_blocked_even_with_key_and_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv(pv.KEY_ENV, "k" * 24)

    def deny(*a, **k):
        raise AssertionError("rede aberta")
    monkeypatch.setattr(socket.socket, "connect", deny)
    with pytest.raises(SystemExit) as e:
        bm.main(["--golden", str(HB / "golden.json"), "--corpus-root", str(CHECKOUT), "--provider", "jev",
                 "--confirm-external-send", "--out", str(tmp_path)])
    assert "BLOCKED_AUTHORIZATION" in str(e.value)


@needs_checkout
def test_holdout_budget_is_two_requests_per_question_without_rerank(monkeypatch):
    monkeypatch.setenv(pv.KEY_ENV, "k" * 24)
    monkeypatch.setattr(bm, "PUBLIC_BENCHMARK_AUTHORIZED", True)           # só neste teste, para ler o teto; nada é enviado
    meta, items = _load()

    class A:
        provider, confirm_external_send, max_calls, max_retries = "jev", True, None, 0
    prov = bm.make_provider(A, meta, True, len(items))
    assert prov.max_calls == 60 and prov.calls == 0
    A.max_calls = 90
    assert bm.make_provider(A, meta, True, len(items)).max_calls == 60       # o teto do arquivo nunca é excedido pelo CLI


@needs_checkout
def test_holdout_harness_fake_run_has_hybrid_rows_group_buckets_and_exact_request_count(monkeypatch, tmp_path):
    def deny(*a, **k):
        raise AssertionError("rede aberta")
    monkeypatch.setattr(socket.socket, "connect", deny)
    assert bm.main(["--golden", str(HB / "golden.json"), "--corpus-root", str(CHECKOUT), "--provider", "fake", "--out", str(tmp_path)]) == 0
    res = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert set(res["summary"]) == {"ripgrep", "code_search", "jev_map", "hybrid"}            # sem jev_rerank
    assert res["meta"]["network_attempts"] == 60 and res["meta"]["evaluation"] == "holdout"
    hyb = [r for r in res["rows"] if r["strategy"] == "hybrid"]
    jm = [r for r in res["rows"] if r["strategy"] == "jev_map"]
    assert len(hyb) == len(jm) == 30 and all(r["jev_requests"] == 0 for r in hyb) and all(r["jev_requests"] == 2 for r in jm)
    assert all(r["hybrid_rule_hash"] == hy.rule_hash() for r in hyb)
    assert res["summary"]["hybrid"]["mixed"]["n"] == 6 and res["summary"]["hybrid"]["grep_friendly"]["n"] == 12
    # SEMANTIC não dispara a salvaguarda: o híbrido é exatamente o jev_map nesses itens
    by_jm = {r["id"]: r for r in jm}
    for r in hyb:
        if r["semantic_only"]:
            assert r["safeguard_active"] is False and r["selected_files"] == by_jm[r["id"]]["selected_files"]
    assert json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "NOT_EVALUATED"


@needs_checkout
def test_holdout_estimate_is_60_calls_and_uses_scrapy_token_ratios(monkeypatch, tmp_path):
    assert bm.main(["--golden", str(HB / "golden.json"), "--corpus-root", str(CHECKOUT), "--estimate-only", "--out", str(tmp_path)]) == 0
    est = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["estimate"]
    assert est["ESTIMATED_JEV_CALLS"] == 0 and est["TOTAL"]["ESTIMATED_JEV_CALLS"] == 60       # só jev_map: 2 por pergunta
    assert est["jev_map"]["map_files"] == 174 and est["jev_map"]["stage_a_max_payload"] < 100_000
    assert "TOTAL_CALIBRATED_WITH_SCRAPY_RATIOS" in est and "stage_b_expected_chunks_lost_lower_bound" in est["jev_map"]


# --------------------------------------------------------------------------- congelamento
def test_holdout_freeze_manifest_matches_freeze_json():
    sys.path.insert(0, str(HB))
    import freeze
    frozen = json.loads((HB / "freeze.json").read_text(encoding="utf-8"))
    cur = freeze.manifest(CHECKOUT if (CHECKOUT / ".git").exists() else None)
    for key, value in frozen.items():
        if key == "corpus" and "corpus" not in cur:
            continue                                          # sem checkout não dá para recomputar a árvore do corpus
        assert cur[key] == value, f"{key} mudou depois do congelamento: registre uma nova versão do holdout"
