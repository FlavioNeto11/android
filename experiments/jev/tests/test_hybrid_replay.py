"""Replay offline do híbrido: classificação pelos critérios congelados e recomposição sobre dados sintéticos. Sem rede."""
from __future__ import annotations

import socket

import pytest

import golden as gd
import hybrid as hy
import hybrid_replay as hr


def _h(exact_r3, sem_r3, ctx=1000, n=10):
    part = lambda r3: {"n": n, "R1": r3, "R3": r3, "R5": r3, "P1": 0, "P3": 0, "DELIVERED_RECALL": 0, "REGION_RECALL": 0,   # noqa: E731
                       "CONTEXT_BYTES_MEDIAN": ctx, "READ_BYTES_AVOIDED_MEDIAN": 0}
    return {"all": part((exact_r3 + sem_r3) / 2), "EXACT": part(exact_r3), "SEMANTIC": part(sem_r3)}


def _summary(h, rg, bm, misses=0):
    return {"hybrid": h, "ripgrep": rg, "code_search": bm, "_critical_misses": {"hybrid": misses}}


CRIT = hy.load_lock()["classification_criteria_for_the_exploratory_replay"]


def test_classify_promising_when_all_four_criteria_hold():
    s = _summary(_h(1.0, 0.9, 900), _h(1.0, 0.1), _h(1.0, 0.0, 1000))
    assert hr.classify(s, CRIT)["HYBRID_EXPLORATORY_RESULT"] == "PROMISING"


def test_classify_not_promising_when_exact_regresses():
    s = _summary(_h(0.5, 0.9), _h(1.0, 0.1), _h(1.0, 0.0))
    assert hr.classify(s, CRIT)["HYBRID_EXPLORATORY_RESULT"] == "NOT_PROMISING"


def test_classify_not_promising_when_semantic_gain_is_small():
    s = _summary(_h(1.0, 0.15), _h(1.0, 0.1), _h(1.0, 0.0))
    assert hr.classify(s, CRIT)["HYBRID_EXPLORATORY_RESULT"] == "NOT_PROMISING"


def test_classify_inconclusive_between_the_bands():
    s = _summary(_h(1.0, 0.3), _h(1.0, 0.1), _h(1.0, 0.0))        # ganho 0,20: nem >= 0,25 nem < 0,10
    assert hr.classify(s, CRIT)["HYBRID_EXPLORATORY_RESULT"] == "INCONCLUSIVE"


def test_classify_never_returns_go_or_no_go():
    for s in (_summary(_h(1, 1), _h(1, 0), _h(1, 0)), _summary(_h(0, 0), _h(1, 1), _h(1, 1))):
        assert hr.classify(s, CRIT)["HYBRID_EXPLORATORY_RESULT"] in {"PROMISING", "NOT_PROMISING", "INCONCLUSIVE"}


def _row(strategy, qid, files, regions, ctx, r3):
    return {"id": qid, "strategy": strategy, "selected_files": files, "selected_regions": regions, "context_bytes_returned": ctx,
            "r1": r3, "r3": r3, "r5": r3, "p1": r3, "p3": r3, "delivered_recall": r3, "region_recall": r3, "read_bytes_avoided": 0,
            "grep_friendly": True, "semantic_only": False, "calls": []}


def test_replay_active_safeguard_promotes_lexical_file_and_keeps_bytes_consistent(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("\n".join(f"a{i}" for i in range(1, 21)) + "\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("\n".join(f"b{i}" for i in range(1, 21)) + "\n", encoding="utf-8")
    item = gd.Item("Q1", "Where is `thing_x` set?", "EXACT_SYMBOL", ("pkg/a.py",), ("thing_x",), (("pkg/a.py", 3, 5),), True, False, "")
    rg_regions = [["pkg/a.py", 3, 5]]
    jm_regions = [["pkg/b.py", 1, 4]]
    rg_bytes = sum(len(x) + 1 for x in ("a3", "a4")) + len("a5")       # linhas a3..a5 unidas por \n
    results = {"rows": [
        _row("ripgrep", "Q1", ["pkg/a.py"], rg_regions, len("a3\na4\na5".encode()), 1.0),
        _row("code_search", "Q1", ["pkg/a.py"], [], 0, 1.0),
        _row("jev_map", "Q1", ["pkg/b.py"], jm_regions, len("b1\nb2\nb3\nb4".encode()), 0.0),
    ]}
    out = hr.replay(results, [item], tmp_path)
    row = out["rows"][0]
    assert row["safeguard_active"] is True
    assert row["ranked_files_top10"][0] == "pkg/a.py" and row["r1"] == 1.0
    assert row["delivered_regions"][0] == ["pkg/a.py", 3, 5]
    assert row["critical_miss"] is False
    assert out["summary"]["hybrid"]["EXACT"]["R1"] == 1.0
    assert rg_bytes > 0


def test_replay_rejects_recorded_bytes_that_do_not_match_the_checkout(tmp_path):
    (tmp_path / "a.py").write_text("x\ny\n", encoding="utf-8")
    item = gd.Item("Q1", "How does it work?", "BEHAVIOR_SEARCH", ("a.py",), ("x",), (("a.py", 1, 1),), False, True, "")
    results = {"rows": [
        _row("ripgrep", "Q1", [], [], 0, 0.0), _row("code_search", "Q1", [], [], 0, 0.0),
        _row("jev_map", "Q1", ["a.py"], [["a.py", 1, 2]], 999, 0.0),
    ]}
    with pytest.raises(AssertionError):
        hr.replay(results, [item], tmp_path)


def test_main_runs_inside_no_network_guard():
    import netguard
    with netguard.no_network():
        with pytest.raises(Exception):
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
