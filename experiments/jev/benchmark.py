"""Benchmark do piloto A (recuperação de contexto para o Claude Code). Só stdlib.

Padrão: SEM Jev e SEM rede (baselines ripgrep + BM25). Com `--provider fake` o pipeline de rerank roda contra o
provedor falso (só valida o harness). `--provider jev` exige `--confirm-external-send` E a chave em
TYPESAFE_API_KEY, e é o único caminho que abre rede.

    python experiments/jev/benchmark.py                           # baselines
    python experiments/jev/benchmark.py --provider fake           # harness + provedor falso
    python experiments/jev/benchmark.py --estimate-only           # custo previsto de uma rodada Jev, sem rede
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baselines as bl                                   # noqa: E402
import golden as gd                                      # noqa: E402
import metrics as mt                                     # noqa: E402
from corpus import Chunk, build_chunks, file_size, list_files, repo_root     # noqa: E402
from netguard import no_network                          # noqa: E402
from provider import (KEY_ENV, MAX_STATE_BYTES_PROXY, PINNED_MODEL, PRICE_USD_PER_MTOK_INPUT,  # noqa: E402
                      DecisionProvider, ProviderError, RealJevProvider, payload_bytes, request_body,
                      retrieval_request)
from fake_provider import FakeJevProvider                # noqa: E402
from textutil import scoped_roots                        # noqa: E402
from redact import redact                                # noqa: E402

HERE = Path(__file__).resolve().parent
N_SHORTLIST = 30
BYTES_PER_TOKEN_PROXY = 4.0       # PROXY: o tokenizador do Jev é desconhecido; usado só para estimar custo


def _row(item: gd.Item, r: bl.Retrieval, delivered: list[bl.Span], root: Path, latency_ms: float, **extra) -> dict:
    expected = set(item.expected_files)
    ranked = r.ranked_files()
    naive = sum(file_size(root, f) for f in expected)
    ctx = sum(s.nbytes for s in delivered)
    row = {
        "id": item.id, "category": item.category, "strategy": r.strategy,
        "grep_friendly": item.grep_friendly, "semantic_only": item.semantic_only,
        "ranked_files_top10": ranked[:10],
        "p1": mt.precision_at_k(ranked, expected, 1), "p3": mt.precision_at_k(ranked, expected, 3),
        "r3": mt.recall_at_k(ranked, expected, 3), "r5": mt.recall_at_k(ranked, expected, 5),
        "delivered_recall": mt.delivered_recall(delivered, expected),
        "region_recall": mt.region_recall(delivered, item.expected_regions),
        "latency_ms": latency_ms, "files_examined": r.files_examined, "bytes_read": r.bytes_read,
        "bytes_returned": r.bytes_returned, "context_bytes_returned": ctx,
        "naive_read_bytes": naive, "read_bytes_avoided": max(0, naive - ctx),
        "failure": None, "fallback": False, "jev_requests": 0, "jev_cost_usd": 0.0,
    }
    row.update(extra)
    return row


def shortlist_candidates(index: bl.Bm25Index, question: str, n: int = N_SHORTLIST,
                         cap_bytes: int = MAX_STATE_BYTES_PROXY, roots: list[str] | None = None) -> list[Chunk]:
    out: list[Chunk] = []
    total = 0
    for i, _ in index.search(question, n, scoped_roots(question, roots) if roots else None):
        c = index.chunks[i]
        if total + c.nbytes > cap_bytes:
            continue
        if redact(c.text).blocked:       # padrão de segredo (mesmo em fixture de teste): o trecho NUNCA entra no payload
            continue
        out.append(c)
        total += c.nbytes
    return out


ADAPTIVE_MASS = 0.8       # entrega os trechos até somar 80 % da probabilidade do Jev
ADAPTIVE_KMIN = 2
ADAPTIVE_KMAX = bl.DELIVER_MAX_SPANS


def adaptive_k(probs: list[float], mass: float = ADAPTIVE_MASS, kmin: int = ADAPTIVE_KMIN,
               kmax: int = ADAPTIVE_KMAX) -> int:
    """Quantos trechos entregar: o menor k cuja probabilidade acumulada atinge `mass` (entre kmin e kmax).
    Parte do pipeline PRÉ-REGISTRADA (thresholds.json): é aí que o Jev poderia entregar menos que o BM25."""
    total = 0.0
    for i, p in enumerate(probs, 1):
        total += p
        if total >= mass:
            return max(kmin, min(kmax, i))
    return kmax


def jev_rerank(index: bl.Bm25Index, provider: DecisionProvider, question: str,
               roots: list[str] | None = None) -> tuple[bl.Retrieval, dict]:
    """Etapa 1: BM25 local (shortlist). Etapa 2: o provedor reordena por Choice. Qualquer ProviderError cai no
    ranking BM25 (fail-open para o baseline gratuito) e fica registrado — nunca derruba a rodada."""
    cands = shortlist_candidates(index, question, roots=roots)
    base = bl.code_search(index, question, top=N_SHORTLIST, roots=roots)
    meta: dict = {"jev_requests": 0, "jev_cost_usd": 0.0, "failure": None, "fallback": False}
    if not cands:
        meta["failure"] = "no_candidates"
        meta["fallback"] = True
        return bl.Retrieval("jev_rerank", base.spans, base.files_examined, base.bytes_read, {"shortlist": 0}), meta
    by_id = {c.id: c for c in cands}
    try:
        res = provider.retrieve(question, {c.id: c.text for c in cands})
    except ProviderError as exc:
        meta.update(failure=exc.kind, fallback=True, jev_requests=1)
        return bl.Retrieval("jev_rerank", base.spans, base.files_examined, base.bytes_read,
                            {"shortlist": len(cands)}), meta
    spans = [bl.Span(by_id[i].file, by_id[i].start, by_id[i].end, by_id[i].text, p) for i, p in res.ranked]
    meta["jev_requests"] = res.requests
    meta["jev_cost_usd"] = res.usage.input_tokens * PRICE_USD_PER_MTOK_INPUT / 1e6
    meta["confidence"] = res.confidence
    meta["deliver_k"] = adaptive_k([p for _, p in res.ranked])
    meta["exists"] = res.exists
    files = {s.file for s in spans}
    return bl.Retrieval("jev_rerank", spans, len(files), sum(file_size(index_root, f) for f in files),
                        {"shortlist": len(cands), "model": res.model}), meta


index_root: Path = repo_root()


def estimate(index: bl.Bm25Index, items: list[gd.Item], roots: list[str] | None = None) -> dict:
    """ESTIMATED_* de uma rodada Jev sobre o golden, SEM rede. Tokens são PROXY (bytes/4); o preço é o oficial."""
    per_item = []
    for it in items:
        cands = shortlist_candidates(index, it.question, roots=roots)
        state, questions = retrieval_request(it.question, {c.id: c.text for c in cands})
        per_item.append({"id": it.id, "candidates": len(cands),
                         "payload_bytes": payload_bytes(request_body(state, questions, PINNED_MODEL))})
    total = sum(p["payload_bytes"] for p in per_item)
    tok_lo, tok_hi = total / 4.0, total / 3.0       # faixa PROXY: 3–4 bytes por token
    return {
        "ESTIMATED_JEV_CALLS": len(items),
        "ESTIMATED_INPUT_BYTES": total,
        "ESTIMATED_INPUT_TOKENS": "UNKNOWN (tokenizador do Jev não documentado); PROXY bytes/4..bytes/3 = "
                                  f"{int(tok_lo)}..{int(tok_hi)}",
        "ESTIMATED_COST": f"US$ {tok_lo * PRICE_USD_PER_MTOK_INPUT / 1e6:.4f}..{tok_hi * PRICE_USD_PER_MTOK_INPUT / 1e6:.4f}"
                          f" (preço OFICIAL US$ {PRICE_USD_PER_MTOK_INPUT}/Mtok de entrada × tokens PROXY; "
                          "free tier UNKNOWN; acesso por waitlist)",
        "max_payload_bytes": max(p["payload_bytes"] for p in per_item),
        "per_item": per_item,
    }


def make_provider(args: argparse.Namespace) -> DecisionProvider | None:
    if args.provider == "none":
        return None
    if args.provider == "fake":
        return FakeJevProvider(args.fake_mode)
    if args.provider == "jev":
        if not args.confirm_external_send:
            raise SystemExit("--provider jev exige --confirm-external-send (código-fonte SAI da máquina). Nada foi enviado.")
        if not os.environ.get(KEY_ENV):
            raise SystemExit(f"variável {KEY_ENV} ausente. Nada foi enviado.")
        return RealJevProvider(enabled=True, max_calls=args.max_calls)
    raise SystemExit(f"provedor desconhecido: {args.provider}")


@functools.lru_cache(maxsize=2)
def _corpus_index(root: str, roots: tuple[str, ...], exts: tuple[str, ...]):
    """Corpus + índice BM25 (caro: ~10 MB). Cache por processo; o working tree não muda durante a rodada."""
    files = list_files(Path(root), list(roots), list(exts))
    chunks = build_chunks(Path(root), files)
    return files, chunks, bl.Bm25Index(chunks)


def run(args: argparse.Namespace) -> dict:
    global index_root
    root = repo_root()
    index_root = root
    meta, items = gd.load(Path(args.golden))
    problems = gd.validate(items, root)
    if problems:
        raise SystemExit("golden inconsistente com o working tree:\n  " + "\n  ".join(problems))
    corpus = meta["corpus"]
    files, chunks, index = _corpus_index(str(root), tuple(corpus["roots"]), tuple(corpus["extensions"]))
    out: dict = {"meta": {"golden_version": meta["version"], "source_sha": meta["source_sha"],
                          "corpus_files": len(files), "corpus_chunks": len(chunks),
                          "corpus_bytes": sum(file_size(root, f) for f in files),
                          "provider": args.provider, "fake_mode": args.fake_mode if args.provider == "fake" else None,
                          "deliver_max_spans": bl.DELIVER_MAX_SPANS, "deliver_max_bytes": bl.DELIVER_MAX_BYTES,
                          "rg_context_lines": bl.RG_CONTEXT, "metric_kind": "PROXY_METRIC (bytes)"}}
    if args.estimate_only:
        out["estimate"] = estimate(index, items, corpus["roots"])
        return out
    provider = make_provider(args)
    rows: list[dict] = []
    for it in items:
        t0 = time.perf_counter()
        rg = bl.ripgrep(root, corpus["roots"], it.question)
        rows.append(_row(it, rg, bl.deliver(rg.spans), root, (time.perf_counter() - t0) * 1000,
                         rg_terms=rg.notes.get("terms"), rg_rule=rg.notes.get("rule")))
        t0 = time.perf_counter()
        cs = bl.code_search(index, it.question, roots=corpus["roots"])
        rows.append(_row(it, cs, bl.deliver(cs.spans), root, (time.perf_counter() - t0) * 1000))
        if provider is not None:
            t0 = time.perf_counter()
            jr, jm = jev_rerank(index, provider, it.question, corpus["roots"])
            rows.append(_row(it, jr, bl.deliver(jr.spans, max_spans=jm.get("deliver_k", bl.DELIVER_MAX_SPANS)), root, (time.perf_counter() - t0) * 1000, **jm))
    out["rows"] = rows
    out["summary"] = {}
    for strat in sorted({r["strategy"] for r in rows}):
        sub = [r for r in rows if r["strategy"] == strat]
        out["summary"][strat] = {"all": mt.aggregate(sub), "grep_friendly": mt.aggregate(sub, "grep_friendly"),
                                 "semantic_only": mt.aggregate(sub, "semantic_only")}
    if isinstance(provider, RealJevProvider):
        out["meta"]["redactions"] = provider.redactions
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["none", "fake", "jev"], default="none")
    ap.add_argument("--fake-mode", default="HIGH_CONFIDENCE")
    ap.add_argument("--golden", default=str(HERE / "golden.json"))
    ap.add_argument("--out", default=None, help="diretório de saída (padrão: data/jev-pilot/<timestamp>-<provedor>)")
    ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--confirm-external-send", action="store_true")
    ap.add_argument("--max-calls", type=int, default=60)
    args = ap.parse_args(argv)
    from report import write_outputs
    if args.provider == "jev":
        result = run(args)                                  # único caminho com rede
    else:
        with no_network():
            result = run(args)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else repo_root() / "data" / "jev-pilot" / f"{stamp}-{args.provider}"
    write_outputs(out_dir, result)
    print(f"resultados em {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
