"""Smoke test SINTÉTICO do Jev: valida protocolo, autenticação, tipos, confiança, latência, timeout e erro.

NÃO envia nenhum código do repositório: o `state` vem só de `synthetic_corpus/` (fictício) e de textos escritos aqui.
Por padrão só ESTIMA (sem rede). A rodada real (no máximo 6 requisições de rede, retries contam) exige, ao mesmo tempo:
`--run`, `--confirm-synthetic-only`, `TYPESAFE_API_KEY` e `SMOKE_RUN_AUTHORIZED = True` (trava abaixo, que só muda num commit
próprio citando a autorização explícita do dono).

    python experiments/jev/smoke.py                 # estima ESTIMATED_CALLS/TOKENS/COST, sem rede
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import chunk_file, repo_root                                   # noqa: E402
from netguard import no_network                                            # noqa: E402
from provider import (KEY_ENV, PINNED_MODEL, PRICE_USD_PER_MTOK_INPUT, ProviderError,   # noqa: E402
                      RealJevProvider, choice_q, noul_q, parse_answers, parse_choice, parse_noul, payload_bytes,
                      request_body, retrieval_request, score_q)

HERE = Path(__file__).resolve().parent
SYNTHETIC = HERE / "synthetic_corpus"
#: TRAVA DO DONO (2026-10-01): a rodada real do smoke só roda depois de autorização explícita. Continua False.
SMOKE_RUN_AUTHORIZED = False
MAX_CALLS = 6
TIMEOUT_PROBE_S = 0.05


@dataclass
class Planned:
    name: str
    purpose: str
    state: Any
    questions: dict[str, Any]
    expect: str = "ok"                     # ok | http_422 | timeout_or_ok
    timeout_s: float | None = None
    check: Callable[[dict], list[str]] | None = field(default=None, repr=False)


# --------------------------------------------------------------------------- validações do que volta
def _check_choice(allowed: set[str], qid: str) -> Callable[[dict], list[str]]:
    def run(raw: dict) -> list[str]:
        answers, _, _ = parse_answers(raw)
        choice, probs, conf = parse_choice(answers[qid], allowed, qid)
        out = ["choice∈conjunto fechado", "confidence∈[0,1]"]
        if abs(sum(probs.values()) - 1.0) < 1e-3:
            out.append("probabilidades somam 1")
        else:
            raise ValueError(f"probabilidades somam {sum(probs.values()):.4f}")
        return out
    return run


def _check_score(levels: int) -> Callable[[dict], list[str]]:
    def run(raw: dict) -> list[str]:
        answers, _, _ = parse_answers(raw)
        a = answers["risk"]
        if a.get("type") != "score" or not 0 <= float(a["score"]) <= levels - 1 + 1e-6:
            raise ValueError("score fora dos níveis")
        if set(a["legend"]) != {str(i) for i in range(levels)}:
            raise ValueError("legend diferente dos níveis")
        parse_choice({"type": "choice", "choice": "0", "probabilities": a["probabilities"], "confidence": a["confidence"]},
                     {str(i) for i in range(levels)}, "risk")
        return ["score dentro dos níveis", "legend completa", "probabilities/confidence válidos"]
    return run


def _check_rerank(ids: set[str]) -> Callable[[dict], list[str]]:
    def run(raw: dict) -> list[str]:
        answers, _, _ = parse_answers(raw)
        parse_choice(answers["best"], ids, "best")
        parse_noul(answers["exists"], "exists")
        return ["choice∈ids do shortlist", "noul válido", "2 perguntas em 1 requisição"]
    return run


def synthetic_chunks(limit: int = 8):
    chunks = []
    for p in sorted((SYNTHETIC / "harbor").glob("*.py")):
        rel = f"harbor/{p.name}"
        chunks += [c for c in chunk_file(rel, p.read_text(encoding="utf-8")) if c.name != "<module>"]
    return chunks[:limit]


def build_plan() -> list[Planned]:
    log_line = "worker-3 timed out while uploading batch 41 after 30s; retrying with a smaller batch"
    chunks = synthetic_chunks()
    cands = {c.id: c.text for c in chunks}
    q_rerank = "Which entry decides whether a crew that is on hold may be asked to relaunch a job?"
    state_r, questions_r = retrieval_request(q_rerank, cands)
    actions = {"A01": "Dispatch the job immediately", "A02": "Queue the job until the crew leaves hold",
               "A03": "Ask a person to sign off", "A04": "Cancel the job", "A05": "Escalate to a stronger assistant"}
    return [
        Planned("noul_auth_latency", "autenticação, formato noul, latência de referência", log_line,
                {"q": noul_q("Does this message report a failure?")}, check=_check_noul),
        Planned("choice_closed_set", "choice com conjunto fechado A01..A05, probabilidades e confiança",
                "Crew 7 is on hold for servicing. A relaunch request arrived for job 114, flagged as high impact; "
                "no sign-off has been given yet.",
                {"q": choice_q("Which action should the dispatcher take? Use only the listed options.", actions)},
                check=_check_choice(set(actions), "q")),
        Planned("score_three_levels", "score com 3 níveis, legend e confiança",
                "Change request: rebuild the index of finished jobs during business hours without a backup.",
                {"risk": score_q("How risky is applying this change now?", ["low", "medium", "high"])},
                check=_check_score(3)),
        Planned("rerank_shape", "o mesmo formato do Piloto A: choice sobre ids do shortlist + noul na mesma requisição",
                state_r, questions_r, check=_check_rerank(set(cands))),
        Planned("error_422", "erro de validação (score com 1 nível): formato do corpo de erro e ausência de retry",
                "any text", {"bad": {"type": "score", "instructions": "How bad?", "criteria": ["only-one-level"]}},
                expect="http_422"),
        Planned("timeout_probe", f"timeout do cliente ({TIMEOUT_PROBE_S}s) sobre a requisição mais simples",
                log_line, {"q": noul_q("Does this message report a failure?")}, expect="timeout_or_ok",
                timeout_s=TIMEOUT_PROBE_S),
    ]


def _check_noul(raw: dict) -> list[str]:
    answers, _, _ = parse_answers(raw)
    parse_noul(answers["q"], "q")
    return ["noul∈[0,1]"]


# --------------------------------------------------------------------------- estimativa (sem rede)
def estimate(plan: list[Planned]) -> dict:
    sizes = [payload_bytes(request_body(p.state, p.questions, PINNED_MODEL)) for p in plan]
    total = sum(sizes)
    lo, hi = total / 4.0, total / 3.0
    return {
        "ESTIMATED_CALLS": len(plan),
        "ESTIMATED_INPUT_BYTES": total,
        "ESTIMATED_TOKENS": f"UNKNOWN (tokenizador do Jev não documentado); PROXY bytes/4..bytes/3 = {int(lo)}..{int(hi)}",
        "ESTIMATED_COST": f"US$ {lo * PRICE_USD_PER_MTOK_INPUT / 1e6:.6f}..{hi * PRICE_USD_PER_MTOK_INPUT / 1e6:.6f} "
                          f"(preço OFICIAL US$ {PRICE_USD_PER_MTOK_INPUT}/Mtok de entrada × tokens PROXY; saída grátis; "
                          "free tier UNKNOWN — se não houver, é o saldo mínimo da conta)",
        "per_call": [{"name": p.name, "payload_bytes": s, "expect": p.expect} for p, s in zip(plan, sizes)],
        "max_calls_cap": MAX_CALLS,
        "retries": "max_retries=0 na rodada real: um 429/529 NÃO é repetido (cada retry gastaria uma das 6 chamadas)",
        "not_validated_by_real_calls": ["retry/backoff em 429/529 (só simulado, com transporte falso)",
                                        "rate limits", "comportamento sob carga", "português"],
    }


# --------------------------------------------------------------------------- rodada real (travada)
def run_real(plan: list[Planned], provider: RealJevProvider) -> dict:
    results = []
    for p in plan:
        rec: dict[str, Any] = {"name": p.name, "purpose": p.purpose, "expect": p.expect}
        old_timeout = provider.timeout_s
        if p.timeout_s:
            provider.timeout_s = p.timeout_s
        t0 = time.perf_counter()
        try:
            raw = provider.evaluate(p.state, p.questions)
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            _, model, usage = parse_answers(raw)
            rec.update(outcome="ok", model_reported=model, input_tokens=usage.input_tokens,
                       output_tokens=usage.output_tokens,
                       real_cost_usd=usage.input_tokens * PRICE_USD_PER_MTOK_INPUT / 1e6)
            if p.check:
                rec["validated"] = p.check(raw)
            rec["answers_shape"] = {k: v.get("type") for k, v in raw.get("answers", {}).items()}
            if "q" in raw.get("answers", {}) and raw["answers"]["q"].get("type") == "choice":
                rec["confidence"] = raw["answers"]["q"].get("confidence")
        except ProviderError as exc:
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            rec.update(outcome=exc.kind, error=str(exc), error_body=provider.last_error_body)
        except Exception as exc:                                    # noqa: BLE001 - validação falhou: registrar, não esconder
            rec.update(outcome="validation_failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            provider.timeout_s = old_timeout
        rec["network_calls_so_far"] = provider.calls
        results.append(rec)
    return {"calls_used": provider.calls, "results": results,
            "total_real_cost_usd": sum(r.get("real_cost_usd", 0.0) for r in results),
            "redactions": provider.redactions}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--confirm-synthetic-only", action="store_true")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    plan = build_plan()
    out_dir = Path(args.out) if args.out else repo_root() / "data" / "jev-pilot" / "smoke"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not args.run:
        with no_network():
            est = estimate(plan)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{stamp}-estimate.json").write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(est, ensure_ascii=False, indent=1))
        return 0
    if not args.confirm_synthetic_only:
        raise SystemExit("--run exige --confirm-synthetic-only. Nada foi enviado.")
    if not SMOKE_RUN_AUTHORIZED:
        raise SystemExit("BLOCKED_AUTHORIZATION: a rodada real do smoke aguarda autorização EXPLÍCITA do dono. Nada foi enviado.")
    if not os.environ.get(KEY_ENV):
        raise SystemExit(f"variável {KEY_ENV} ausente. Nada foi enviado.")
    cap = min(args.max_calls, MAX_CALLS)
    provider = RealJevProvider(enabled=True, max_calls=cap, max_retries=0)
    report = run_real(plan, provider)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stamp}-real.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
