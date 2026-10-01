"""Replay OFFLINE da regra híbrida sobre os resultados BRUTOS já gravados da rodada real do Scrapy.

ZERO requisições: roda dentro de `no_network()` e só lê (1) `results.json` da rodada real (ripgrep, BM25 e jev_map já calculados),
(2) o golden SOMENTE para pontuar e (3) o checkout público fixado, para recompor o tamanho dos trechos. A regra (hybrid.py) nunca
olha o gabarito. O replay é EXPLORATÓRIO: classifica com PROMISING/NOT_PROMISING/INCONCLUSIVE, nunca GO/NO_GO, e não altera o
veredito do experimento original (NO_GO).

    python experiments/jev/hybrid_replay.py --results data/jev-pilot/public-run-real/results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baselines as bl                                   # noqa: E402
import golden as gd                                      # noqa: E402
import hybrid as hy                                      # noqa: E402
import metrics as mt                                     # noqa: E402
from corpus import file_size, read_text, repo_root       # noqa: E402
from netguard import no_network                          # noqa: E402

HERE = Path(__file__).resolve().parent


def region_text(root: Path, region) -> str:
    f, a, b = region
    return "\n".join(read_text(root, f).splitlines()[a - 1:b])


def _span(root: Path, r) -> bl.Span:
    return bl.Span(r[0], r[1], r[2], region_text(root, r), 0.0)


def replay(results: dict, items: list[gd.Item], root: Path) -> dict:
    rows = results["rows"]
    by = {s: {r["id"]: r for r in rows if r["strategy"] == s} for s in ("ripgrep", "code_search", "jev_map")}
    out_rows = []
    for it in items:
        rg, cs, jm = by["ripgrep"][it.id], by["code_search"][it.id], by["jev_map"][it.id]
        # Fidelidade: o tamanho recomposto dos trechos entregues tem de bater com o gravado na rodada real.
        for name, row in (("ripgrep", rg), ("jev_map", jm)):
            rebuilt = sum(_span(root, r).nbytes for r in row["selected_regions"])
            assert rebuilt == row["context_bytes_returned"], f"{it.id}/{name}: bytes recompostos {rebuilt} != gravados {row['context_bytes_returned']}"
        active = hy.safeguard_active(it.question, rg["selected_files"])
        files = hy.merge_files(active, rg["selected_files"], jm["selected_files"])
        lex_top = [r for r in rg["selected_regions"] if rg["selected_files"] and r[0] == rg["selected_files"][0]] if active else []
        merged = hy.merge_regions(active, lex_top, jm["selected_regions"])
        regions = hy.cap_regions(merged, lambda r: _span(root, r).nbytes)
        delivered = [_span(root, r) for r in regions]
        expected = set(it.expected_files)
        ctx = sum(s.nbytes for s in delivered)
        naive = sum(file_size(root, f) for f in expected)
        out_rows.append({
            "id": it.id, "category": it.category, "grep_friendly": it.grep_friendly, "semantic_only": it.semantic_only,
            "safeguard_active": active, "identifiers": hy.explicit_identifiers(it.question),
            "expected_files": sorted(expected), "expected_regions": [list(r) for r in it.expected_regions],
            "ranked_files_top10": files[:10], "delivered_regions": regions,
            "r1": mt.recall_at_k(files, expected, 1), "r3": mt.recall_at_k(files, expected, 3), "r5": mt.recall_at_k(files, expected, 5),
            "p1": mt.precision_at_k(files, expected, 1), "p3": mt.precision_at_k(files, expected, 3),
            "delivered_recall": mt.delivered_recall(delivered, expected), "region_recall": mt.region_recall(delivered, it.expected_regions),
            "context_bytes_returned": ctx, "naive_read_bytes": naive, "read_bytes_avoided": max(0, naive - ctx),
            "bm25_r3": cs["r3"], "critical_miss": cs["r3"] > 0 and mt.recall_at_k(files, expected, 3) < cs["r3"],
        })
    summary = {"hybrid": _agg(out_rows)}
    for s in ("ripgrep", "code_search", "jev_map"):
        summary[s] = _agg(list(by[s].values()))
    summary["_critical_misses"] = {"hybrid": sum(r["critical_miss"] for r in out_rows)}
    return {"rows": out_rows, "summary": summary}


def _agg(rows: list[dict]) -> dict:
    def part(sel):
        n = len(sel)
        m = mt.mean
        return {"n": n, "R1": m([r["r1"] for r in sel]), "R3": m([r["r3"] for r in sel]), "R5": m([r["r5"] for r in sel]),
                "P1": m([r["p1"] for r in sel]), "P3": m([r["p3"] for r in sel]),
                "DELIVERED_RECALL": m([r["delivered_recall"] for r in sel]), "REGION_RECALL": m([r["region_recall"] for r in sel]),
                "CONTEXT_BYTES_MEDIAN": mt.percentile([r["context_bytes_returned"] for r in sel], 50),
                "READ_BYTES_AVOIDED_MEDIAN": mt.percentile([r["read_bytes_avoided"] for r in sel], 50)}
    return {"all": part(rows), "EXACT": part([r for r in rows if r["grep_friendly"]]),
            "SEMANTIC": part([r for r in rows if r["semantic_only"]])}


def classify(summary: dict, criteria: dict) -> dict:
    """Aplica os critérios congelados em hybrid_rule.lock.json (escritos antes do scoring)."""
    h, rg, bm = summary["hybrid"], summary["ripgrep"], summary["code_search"]
    best_exact = max(rg["EXACT"]["R3"], bm["EXACT"]["R3"])
    best_sem = max(rg["SEMANTIC"]["R3"], bm["SEMANTIC"]["R3"])
    a = h["EXACT"]["R3"] >= best_exact - 0.05
    b_gain = h["SEMANTIC"]["R3"] - best_sem
    b = b_gain >= 0.25
    c = h["all"]["CONTEXT_BYTES_MEDIAN"] <= bm["all"]["CONTEXT_BYTES_MEDIAN"]
    d = summary["_critical_misses"]["hybrid"] <= 2
    if a and b and c and d:
        verdict = "PROMISING"
    elif not a or b_gain < 0.10:
        verdict = "NOT_PROMISING"
    else:
        verdict = "INCONCLUSIVE"
    return {"HYBRID_EXPLORATORY_RESULT": verdict,
            "a_exact_no_regression": {"ok": a, "hybrid": h["EXACT"]["R3"], "best_baseline": best_exact, "limit": ">= best - 0.05"},
            "b_semantic_gain": {"ok": b, "gain": round(b_gain, 4), "hybrid": h["SEMANTIC"]["R3"], "best_baseline": best_sem, "limit": ">= +0.25"},
            "c_context": {"ok": c, "hybrid_median": h["all"]["CONTEXT_BYTES_MEDIAN"], "bm25_median": bm["all"]["CONTEXT_BYTES_MEDIAN"]},
            "d_critical_misses": {"ok": d, "value": summary["_critical_misses"]["hybrid"], "limit": "<= 2"},
            "by_construction_note": criteria["note"]}


def p01_report(results: dict, replayed: dict) -> dict:
    rows = results["rows"]
    pick = lambda s: next(r for r in rows if r["strategy"] == s and r["id"] == "P01")        # noqa: E731
    rg, jm = pick("ripgrep"), pick("jev_map")
    hy_row = next(r for r in replayed["rows"] if r["id"] == "P01")
    calls = {c["stage"]: c for c in jm["calls"]}
    return {"question": "Which code sends the `request_dropped` signal?", "expected": rg["expected_files"],
            "ripgrep_top": rg["selected_files"][:5], "ripgrep_windows": [r for r in rg["selected_regions"] if r[0] == rg["selected_files"][0]],
            "jev_map_top": jm["selected_files"][:5], "jev_map_stage_a_top5": calls["A"]["top"] if "A" in calls else None,
            "jev_map_stage_b_top5": calls["B"]["top"] if "B" in calls else None,
            "hybrid_top": hy_row["ranked_files_top10"][:5], "hybrid_delivered": hy_row["delivered_regions"],
            "safeguard_active": hy_row["safeguard_active"], "identifiers": hy_row["identifiers"],
            "hybrid_r1_r3": [hy_row["r1"], hy_row["r3"]], "hybrid_region_recall": hy_row["region_recall"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--golden", default=str(HERE / "public_bench" / "golden.json"))
    ap.add_argument("--corpus-root", default=str(repo_root() / "data" / "jev-pilot" / "public" / "scrapy"))
    ap.add_argument("--out", default=str(repo_root() / "data" / "jev-pilot" / "hybrid-replay"))
    args = ap.parse_args(argv)
    lock = hy.load_lock()
    if lock["HYBRID_RULE_HASH"] != hy.rule_hash():
        raise SystemExit("a regra mudou depois do congelamento (hash diferente): replay recusado")
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    meta, items = gd.load(Path(args.golden))
    with no_network():                                   # ZERO requisições: qualquer socket levanta erro
        rep = replay(results, items, Path(args.corpus_root))
        cls = classify(rep["summary"], lock["classification_criteria_for_the_exploratory_replay"])
        p01 = p01_report(results, rep)
    out = {"ORIGINAL_EXPERIMENT_VERDICT": "NO_GO", "ORIGINAL_VERDICT_CHANGED": "NO", "kind": "POST_HOC_EXPLORATORY_HYBRID",
           "HYBRID_RULE_VERSION": lock["HYBRID_RULE_VERSION"], "HYBRID_RULE_HASH": lock["HYBRID_RULE_HASH"],
           "NEW_NETWORK_ATTEMPTS": 0, "classification": cls, "summary": rep["summary"], "P01": p01, "rows": rep["rows"]}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("ORIGINAL_EXPERIMENT_VERDICT", "ORIGINAL_VERDICT_CHANGED", "HYBRID_RULE_VERSION",
                                          "HYBRID_RULE_HASH", "NEW_NETWORK_ATTEMPTS", "classification", "summary", "P01")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
