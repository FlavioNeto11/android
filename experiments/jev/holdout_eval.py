"""Avaliação MECÂNICA do holdout confirmatório (limiares em `holdout_bench/thresholds.json`, escritos antes de qualquer chamada).

Não decide nada sozinha sobre o código privado: um PASS aqui só diz que o híbrido merece o PRÓXIMO portão (retenção/ZDR da API).
Só stdlib. Nenhuma rede.
"""
from __future__ import annotations

import json
from pathlib import Path

import metrics as mt
from redact import HARD

HARD_KINDS = frozenset(HARD)          # private_key, bearer, api_key, jwt; o resto (secret_assign, email, win_user_path) é SOFT


def _items(summary_part: dict, n_key: str = "RECALL_AT_3") -> int:
    """R@3 médio × n = itens (fracionários quando há 2 arquivos esperados) com o arquivo esperado no top-3."""
    return round(summary_part[n_key] * summary_part["n"], 6)


def _h9(result: dict, th: dict, hybrid_rows: list[dict]) -> dict:
    """H9 EXATAMENTE como congelado em `thresholds.json` (checkout fixado e limpo; achados DUROS <= hard_redaction_hits_max; tentativas
    <= network_attempts_max; hash da regra híbrida igual ao congelado; nenhum código privado enviado). Redações SOFT (e-mail,
    secret_assign, win_user_path) são registradas e redigidas antes do envio, mas NÃO reprovam o critério.

    Versão 2 do avaliador: a v1 comparava o total de redações de QUALQUER tipo com 0 (defeito de implementação, reconhecido pelo dono
    em 2026-10-01; o limiar e os dados não mudaram). `private_code_sent` e o hash por linha são verificados com o que a rodada persistiu:
    quando o bruto não traz `private_code_sent`, ele é DERIVADO dos artefatos existentes (`derived_from_existing_artifacts`)."""
    spec = th["criteria"]["H9_provenance_and_privacy"]
    meta = result["meta"]
    red = meta.get("redactions") or {}
    red = red if isinstance(red, dict) else {}
    hard = sum(n for k, n in red.items() if k in HARD_KINDS)
    soft = {k: n for k, n in red.items() if k not in HARD_KINDS}
    attempts = meta.get("network_attempts") or 0
    frozen_hash = th["hybrid_rule"]["hash"]
    hashes = {r.get("hybrid_rule_hash") for r in hybrid_rows}
    rule_ok = bool(hybrid_rows) and hashes == {frozen_hash}
    if "private_code_sent" in meta:
        private_sent, private_src = bool(meta["private_code_sent"]), "persisted"
    else:                          # corpus público + checkout verificado (pinned, limpo, origem do golden) + allowed_files ⇒ nada privado saiu
        private_sent = not (meta.get("corpus_visibility") == "public" and meta.get("checkout_verified") is True)
        private_src = "derived_from_existing_artifacts (corpus_visibility=public, checkout_verified=true)"
    checks = {
        "checkout_pinned_and_clean": bool(meta.get("checkout_verified")) or not spec["checkout_must_match_pinned_sha_and_be_clean"],
        "hard_redaction_hits": hard <= spec["hard_redaction_hits_max"],
        "network_attempts": attempts <= spec["network_attempts_max"],
        "hybrid_rule_hash_matches": rule_ok or not spec["hybrid_rule_hash_must_match"],
        "private_code_not_sent": private_sent is False,
    }
    return {"ok": all(checks.values()), "checks": checks,
            "observed": {"checkout_verified": meta.get("checkout_verified"), "hard_redaction_hits": hard, "soft_redactions": soft,
                         "network_attempts": attempts, "hybrid_rule_hash_matches": rule_ok, "private_code_sent": private_sent,
                         "private_code_sent_source": private_src},
            "evaluator_version": 2}


def evaluate(result: dict, thresholds: dict | None = None) -> dict:
    meta = result["meta"]
    path = Path(meta["thresholds_path"]) if meta.get("thresholds_path") else None
    th = thresholds or json.loads(path.read_text(encoding="utf-8"))
    crit = th["criteria"]
    strat = th["strategy_under_test"]
    if meta.get("provider") != "jev":
        return {"verdict": "NOT_EVALUATED", "reason": f"provider={meta.get('provider')}: só a rodada real avalia"}
    s = result.get("summary", {})
    if strat not in s or "jev_map" not in s:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": f"rodada real sem linhas de `{strat}` (interrompida: {meta.get('aborted') or 'não'})"}
    rows = {name: [r for r in result["rows"] if r["strategy"] == name] for name in (strat, "jev_map", "ripgrep", "code_search")}
    n_items = len(rows["ripgrep"])
    if meta.get("aborted") or len(rows[strat]) < n_items:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": f"{len(rows[strat])}/{n_items} linhas do híbrido (interrompida: {meta.get('aborted') or 'não'})"}
    h, jm, rg, bm = s[strat], s["jev_map"], s["ripgrep"], s["code_search"]
    out: dict = {}

    ex_h, ex_best = _items(h["grep_friendly"]), max(_items(rg["grep_friendly"]), _items(bm["grep_friendly"]))
    out["H1"] = {"ok": ex_h >= ex_best - crit["H1_exact_no_material_regression"]["tolerance_items"],
                 "observed": {"hybrid_items": ex_h, "best_baseline_items": ex_best, "n": h["grep_friendly"]["n"]}}

    mx_h, mx_jm = _items(h["mixed"]), _items(jm["mixed"])
    mx_best = max(_items(rg["mixed"]), _items(bm["mixed"]))
    out["H2"] = {"ok": mx_h >= mx_best and mx_h >= mx_jm - 1,
                 "observed": {"hybrid_items": mx_h, "best_baseline_items": mx_best, "jev_map_items": mx_jm, "n": h["mixed"]["n"]}}

    se_h, se_best = _items(h["semantic_only"]), max(_items(rg["semantic_only"]), _items(bm["semantic_only"]))
    n_sem = h["semantic_only"]["n"]
    need = max(crit["H3_semantic_gain"]["absolute_items_min"], se_best + (n_sem - se_best) * crit["H3_semantic_gain"]["gap_closed_fraction_min"])
    out["H3"] = {"ok": se_h >= need - 1e-9,
                 "observed": {"hybrid_items": se_h, "best_baseline_items": se_best, "required_items": need, "n": n_sem}}
    if se_best > n_sem - 3:                             # sem folga: o ganho não é avaliável
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": f"melhor baseline SEMANTIC {se_best}/{n_sem}: sem folga para medir ganho", "criteria": out}

    ctx, naive = h["all"]["CONTEXT_BYTES_RETURNED_MEDIAN"], h["all"]["NAIVE_READ_BYTES_MEDIAN"]
    out["H4"] = {"ok": ctx <= bm["all"]["CONTEXT_BYTES_RETURNED_MEDIAN"] and ctx <= 0.5 * naive,
                 "observed": {"hybrid_median": ctx, "bm25_median": bm["all"]["CONTEXT_BYTES_RETURNED_MEDIAN"], "naive_median": naive}}

    lat = crit["H5_latency"]
    out["H5"] = {"ok": h["all"]["LATENCY_MS_P50"] <= lat["p50_ms_max"] and h["all"]["LATENCY_MS_P95"] <= lat["p95_ms_max"],
                 "observed": {"p50_ms": h["all"]["LATENCY_MS_P50"], "p95_ms": h["all"]["LATENCY_MS_P95"]}}

    failed = sum(1 for r in rows["jev_map"] if r.get("failure"))
    out["H6"] = {"ok": failed <= crit["H6_reliability"]["failed_questions_max"], "observed": {"failed_questions": failed, "of": n_items}}

    cost = jm["all"]["JEV_COST_USD_PROXY"]
    out["H7"] = {"ok": cost <= crit["H7_cost"]["max_usd_for_run"] and cost / n_items <= crit["H7_cost"]["max_usd_per_question"],
                 "observed": {"usd": round(cost, 6), "usd_per_question": round(cost / n_items, 6)}}

    bm_r3 = {r["id"]: r["r3"] for r in rows["code_search"]}
    misses = sum(1 for r in rows[strat] if bm_r3[r["id"]] > 0 and r["r3"] < bm_r3[r["id"]])
    out["H8"] = {"ok": misses <= crit["H8_critical_misses"]["max_items"], "observed": {"critical_misses": misses, "of": n_items}}

    out["H9"] = _h9(result, th, rows[strat])

    core = all(out[k]["ok"] for k in ("H1", "H3", "H6", "H8", "H9"))
    secondary = all(out[k]["ok"] for k in ("H2", "H4", "H5", "H7"))
    verdict = "FAIL" if not core else "PASS" if secondary else "PASS_WITH_RESERVATIONS"
    return {"verdict": verdict, "criteria": out, "strategy_under_test": strat, "private_code_authorized": False,
            "note": "Nenhum veredito altera o NO_GO do Scrapy nem autoriza código privado (PRIVATE_CODE_BENCHMARK_STATUS = BLOCKED_PRIVACY)."}


def markdown(result: dict, verdict: dict) -> str:
    meta = result["meta"]
    lines = [f"# Holdout confirmatório — rodada `{meta.get('provider')}` · base `{meta['source_sha']}`", "",
             f"- {meta['corpus_files']} arquivos, {meta['corpus_chunks']} chunks · métricas em bytes = **PROXY_METRIC**",
             f"- tentativas de rede: {meta.get('network_attempts')} (teto {meta.get('max_calls')}, retries {meta.get('max_retries')})", ""]
    if "estimate" in result:
        e = result["estimate"]
        lines += ["## Estimativa (sem rede)", ""]
        for k in ("ESTIMATED_JEV_CALLS", "ESTIMATED_INPUT_BYTES", "ESTIMATED_INPUT_TOKENS", "ESTIMATED_COST"):
            if k in e:
                lines.append(f"- **{k}**: {e[k]}")
        for k, v in e.get("TOTAL_CALIBRATED_WITH_SCRAPY_RATIOS", {}).items():
            lines.append(f"- **{k} (razões do Scrapy)**: {v}")
        return "\n".join(lines) + "\n"
    cols = ["n", "RECALL_AT_1", "RECALL_AT_3", "RECALL_AT_5", "REGION_RECALL", "CONTEXT_BYTES_RETURNED_MEDIAN", "LATENCY_MS_P50", "FAILURES"]
    names = {"all": "ALL", "grep_friendly": "EXACT", "semantic_only": "SEMANTIC", "mixed": "MIXED"}
    for group, label in names.items():
        lines += [f"## {label}", "", "| estratégia | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for strat, groups in result["summary"].items():
            if group in groups:
                g = groups[group]
                lines.append(f"| {strat} | " + " | ".join(f"{g.get(c, ''):.3f}" if isinstance(g.get(c), float) else str(g.get(c, "")) for c in cols) + " |")
        lines.append("")
    lines += ["## Veredito mecânico", "", f"`{verdict['verdict']}`" + (f" — {verdict['reason']}" if "reason" in verdict else "")]
    for k, v in verdict.get("criteria", {}).items():
        lines.append(f"- {k}: {'ok' if v['ok'] else 'FALHOU'} {json.dumps(v['observed'], ensure_ascii=False)}")
    if "note" in verdict:
        lines += ["", verdict["note"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Replay OFFLINE do avaliador sobre um `results.json` já gravado: não chama API, não regera respostas, não toca o bruto.
    Escreve `verdict.corrected-replay.json` e `report.corrected-replay.md` ao lado do bruto."""
    import argparse
    import hashlib
    from netguard import no_network
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--results", required=True)
    args = ap.parse_args(argv)
    path = Path(args.results)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = json.loads(path.read_text(encoding="utf-8"))
    with no_network():
        verdict = evaluate(result)
    verdict = {"kind": "PROTOCOL_CORRECTED_REPLAY", "evaluator_version": 2, "NEW_NETWORK_ATTEMPTS": 0, "API_CALLS_REPEATED": "NO",
               "THRESHOLD_CHANGED_AFTER_RESULTS": "NO", "RULE_CHANGED_AFTER_RESULTS": "NO",
               "results_json_sha256": before, **verdict}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before          # o bruto da rodada não muda
    (path.parent / "verdict.corrected-replay.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    (path.parent / "report.corrected-replay.md").write_text(markdown(result, verdict), encoding="utf-8")
    print(json.dumps({k: verdict[k] for k in ("verdict", "results_json_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
