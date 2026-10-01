"""Serialização do relatório do piloto A e avaliação (mecânica) dos limiares pré-registrados.

A avaliação só emite veredito quando a rodada foi com o provedor REAL. Rodada com provedor falso ou sem provedor
termina em `NOT_EVALUATED`: o falso não diz nada sobre o Jev.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

THRESHOLDS = Path(__file__).resolve().parent / "thresholds.json"


def _fmt(v) -> str:
    if isinstance(v, float):
        return "nan" if math.isnan(v) else f"{v:.3f}" if abs(v) < 100 else f"{v:,.0f}"
    return str(v)


def evaluate(result: dict, thresholds: dict | None = None) -> dict:
    th = (thresholds or json.loads(THRESHOLDS.read_text(encoding="utf-8")))["pilot_a_claude_code_retrieval"]
    meta = result["meta"]
    if meta.get("provider") != "jev" or "summary" not in result or "jev_rerank" not in result["summary"]:
        return {"verdict": "NOT_EVALUATED", "reason": f"provider={meta.get('provider')}: só a rodada real avalia"}
    s = result["summary"]
    jev, rg, cs = s["jev_rerank"], s["ripgrep"], s["code_search"]
    rows = [r for r in result["rows"] if r["strategy"] == "jev_rerank"]
    base = {r["id"]: r for r in result["rows"] if r["strategy"] == "code_search"}
    out: dict = {}
    t1 = th["T1_exact_no_regression"]
    out["T1"] = (jev["grep_friendly"]["RECALL_AT_3"] - rg["grep_friendly"]["RECALL_AT_3"]) >= t1["jev_minus_ripgrep_min"]
    t2 = th["T2_semantic_gain"]
    out["T2"] = ((jev["semantic_only"]["RECALL_AT_3"] - cs["semantic_only"]["RECALL_AT_3"]) >= t2["jev_minus_code_search_min"]
                 and (jev["semantic_only"]["RECALL_AT_3"] - rg["semantic_only"]["RECALL_AT_3"]) >= t2["jev_minus_ripgrep_min"])
    t3 = th["T3_context_reduction"]
    out["T3"] = (jev["all"]["CONTEXT_BYTES_RETURNED_MEDIAN"] <= t3["max_ratio_of_naive_read"] * jev["all"]["NAIVE_READ_BYTES_MEDIAN"]
                 and jev["all"]["CONTEXT_BYTES_RETURNED_MEDIAN"] <= t3["max_ratio_of_code_search"] * cs["all"]["CONTEXT_BYTES_RETURNED_MEDIAN"])
    t4 = th["T4_latency"]
    out["T4"] = jev["all"]["LATENCY_MS_P50"] <= t4["p50_ms_max"] and jev["all"]["LATENCY_MS_P95"] <= t4["p95_ms_max"]
    t5 = th["T5_reliability"]
    n_req = jev["all"]["JEV_REQUESTS"]
    out["T5"] = n_req >= t5["min_requests"] and (jev["all"]["FAILURES"] / max(1, jev["all"]["n"])) <= t5["failure_rate_max"]
    t6 = th["T6_cost"]
    cost = jev["all"]["JEV_COST_USD_PROXY"]
    out["T6"] = cost <= t6["max_usd_for_golden_run"] and cost / max(1, jev["all"]["n"]) <= t6["max_usd_per_query"]
    hard = sum(v for k, v in (meta.get("redactions") or {}).items() if k in ("private_key", "bearer", "api_key", "jwt"))
    out["T7"] = hard <= th["T7_privacy_gate"]["hard_redaction_hits_max"]       # a aprovação do dono é externa ao benchmark
    crit = 0
    for r in rows:
        b = base.get(r["id"])
        if b and b["r3"] > 0 and r["r3"] < b["r3"]:
            crit += 1
    out["T8"] = crit <= th["T8_critical_misses"]["max_items"]
    out["critical_misses"] = crit
    if n_req < t5["min_requests"]:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif not (out["T1"] and out["T5"] and out["T7"] and out["T8"]) or (not out["T2"] and not out["T3"]):
        verdict = "NO_GO"
    elif all(out[k] for k in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")):
        verdict = "GO"
    else:
        verdict = "PARTIAL_GO"
    out["verdict"] = verdict
    out["note"] = "T7 também exige aprovação do dono e DPA lido (fora do benchmark)."
    return out


def markdown(result: dict, verdict: dict) -> str:
    meta = result["meta"]
    lines = [f"# Piloto A — rodada `{meta.get('provider')}`", "",
             f"- golden v{meta['golden_version']} · base `{meta['source_sha']}` · corpus {meta['corpus_files']} arquivos, "
             f"{meta['corpus_chunks']} chunks, {meta['corpus_bytes']:,} bytes",
             f"- entrega ao Claude: até {meta['deliver_max_spans']} trechos / {meta['deliver_max_bytes']:,} bytes · "
             f"janela rg ±{meta['rg_context_lines']} linhas",
             f"- métricas em bytes = **PROXY_METRIC** (não são tokens)", ""]
    if "estimate" in result:
        e = result["estimate"]
        lines += ["## Estimativa de custo (sem rede)", ""] + [f"- **{k}**: {e[k]}" for k in
                 ("ESTIMATED_JEV_CALLS", "ESTIMATED_INPUT_BYTES", "ESTIMATED_INPUT_TOKENS", "ESTIMATED_COST", "max_payload_bytes")]
        return "\n".join(lines) + "\n"
    cols = ["n", "PRECISION_AT_1", "PRECISION_AT_3", "RECALL_AT_3", "RECALL_AT_5", "DELIVERED_RECALL", "REGION_RECALL",
            "LATENCY_MS_P50", "CONTEXT_BYTES_RETURNED_MEDIAN", "NAIVE_READ_BYTES_MEDIAN", "FAILURES"]
    for group in ("all", "grep_friendly", "semantic_only"):
        lines += [f"## Grupo `{group}`", "", "| estratégia | " + " | ".join(cols) + " |",
                  "|---|" + "---|" * len(cols)]
        for strat, groups in result["summary"].items():
            g = groups[group]
            lines.append(f"| {strat} | " + " | ".join(_fmt(g.get(c, "")) for c in cols) + " |")
        lines.append("")
    lines += ["## Veredito mecânico", "", f"`{verdict['verdict']}`" + (f" — {verdict['reason']}" if "reason" in verdict else "")]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, result: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    verdict = evaluate(result)
    (out_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out_dir / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "report.md").write_text(markdown(result, verdict), encoding="utf-8")
    return verdict
