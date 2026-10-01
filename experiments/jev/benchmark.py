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
import repomap                                           # noqa: E402
from netguard import no_network                          # noqa: E402
from provider import (KEY_ENV, MAX_STATE_BYTES_PROXY, PINNED_MODEL, PRICE_USD_PER_MTOK_INPUT,  # noqa: E402
                      DecisionProvider, ProviderError, RealJevProvider, payload_bytes, request_body,
                      retrieval_request)
from fake_provider import FakeJevProvider                # noqa: E402
from textutil import scoped_roots                        # noqa: E402
from redact import redact                                # noqa: E402

HERE = Path(__file__).resolve().parent
#: TRAVA DO DONO (2026-10-01): o repositório é PRIVADO e a retenção padrão da API é UNKNOWN. Enquanto isto for False,
#: `--provider jev` sobre o código real aborta com BLOCKED_PRIVACY, mesmo com chave e `--confirm-external-send`.
#: Só vira True num commit próprio, citando a autorização EXPLÍCITA do dono (docs/research/jev-pilot.md §35).
PRIVATE_CODE_SEND_APPROVED = False
#: TRAVA do benchmark PÚBLICO (Scrapy fixado, licença BSD-3): preparado em 2026-10-01 e NÃO autorizado a rodar com o Jev.
#: Só vira True num commit próprio, citando a autorização EXPLÍCITA do dono para este benchmark e a chave local disponível.
#: Não interfere em `PRIVATE_CODE_SEND_APPROVED`: o caminho do código privado continua exigindo o True acima.
PUBLIC_BENCHMARK_AUTHORIZED = False
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


# Variante `jev_map` (benchmark público; pré-registrada em public_bench/thresholds.json antes de qualquer chamada):
STAGE_B_FILES = 2             # arquivos que o Jev escolheu na etapa A e cujos trechos vão para a etapa B
STAGE_B_MAX_CHUNKS = 60
STAGE_B_CAP_BYTES = 80_000    # 32k tokens valem para state + maior pergunta; em código Python ≈ 3 B/token ⇒ ~27k tokens
MAP_REPRESENT_FILES = 8       # arquivos (por probabilidade da etapa A) que aparecem no ranking final


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


def _chunk_scores(index: bl.Bm25Index, question: str) -> dict[int, float]:
    return dict(index.search(question, len(index.chunks)))


def jev_map(index: bl.Bm25Index, file_map: dict[str, str], provider: DecisionProvider, question: str,
            roots: list[str] | None = None) -> tuple[bl.Retrieval, dict]:
    """Variante B do benchmark público. Etapa A: UMA requisição com Choice sobre os ARQUIVOS (mapa do repositório).
    Etapa B: UMA requisição com Choice sobre os trechos dos STAGE_B_FILES primeiros arquivos. Qualquer falha cai no BM25
    (etapa A falhou) ou no ranking da etapa A com o melhor trecho local por arquivo (etapa B falhou); a falha é contada."""
    base = bl.code_search(index, question, top=N_SHORTLIST, roots=roots)
    meta: dict = {"jev_requests": 0, "jev_cost_usd": 0.0, "failure": None, "fallback": False}
    scores = _chunk_scores(index, question)
    by_file: dict[str, list[int]] = {}
    for i, c in enumerate(index.chunks):
        by_file.setdefault(c.file, []).append(i)

    def best_local(f: str) -> bl.Span:
        i = max(by_file[f], key=lambda j: (scores.get(j, 0.0), -j))
        c = index.chunks[i]
        return bl.Span(c.file, c.start, c.end, c.text, 0.0)

    try:
        a = provider.retrieve(question, file_map, kind="map")
    except ProviderError as exc:
        meta.update(failure=f"stage_a:{exc.kind}", fallback=True, jev_requests=1)
        return bl.Retrieval("jev_map", base.spans, base.files_examined, base.bytes_read, {"stage": "A_failed"}), meta
    meta["jev_requests"] = 1
    meta["jev_cost_usd"] = a.usage.input_tokens * PRICE_USD_PER_MTOK_INPUT / 1e6
    meta["confidence_a"] = a.confidence
    files_a = [f for f, _ in a.ranked if f in by_file]
    top_files = files_a[:STAGE_B_FILES]
    order = {f: n for n, f in enumerate(top_files)}
    pool = sorted((i for f in top_files for i in by_file[f]), key=lambda j: (-scores.get(j, 0.0), j))
    cands: list[Chunk] = []
    total = 0
    for i in pool:
        c = index.chunks[i]
        if len(cands) >= STAGE_B_MAX_CHUNKS or total + c.nbytes > STAGE_B_CAP_BYTES or redact(c.text).blocked:
            continue
        cands.append(c)
        total += c.nbytes
    cands.sort(key=lambda c: (order[c.file], c.start))
    rest = [best_local(f) for f in files_a[STAGE_B_FILES:MAP_REPRESENT_FILES]]
    if not cands:
        meta.update(failure="stage_b:no_candidates", fallback=True)
        spans = [best_local(f) for f in files_a[:MAP_REPRESENT_FILES]]
        return bl.Retrieval("jev_map", spans, len({s.file for s in spans}), 0, {"stage": "A_only"}), meta
    by_id = {c.id: c for c in cands}
    try:
        res = provider.retrieve(question, {c.id: c.text for c in cands})
    except ProviderError as exc:
        meta.update(failure=f"stage_b:{exc.kind}", fallback=True, jev_requests=2)
        spans = [best_local(f) for f in files_a[:MAP_REPRESENT_FILES]]
        return bl.Retrieval("jev_map", spans, len({s.file for s in spans}), 0, {"stage": "B_failed"}), meta
    meta["jev_requests"] = 2
    meta["jev_cost_usd"] += res.usage.input_tokens * PRICE_USD_PER_MTOK_INPUT / 1e6
    meta["confidence"] = res.confidence
    meta["exists"] = res.exists
    meta["deliver_k"] = adaptive_k([p for _, p in res.ranked])
    spans = [bl.Span(by_id[i].file, by_id[i].start, by_id[i].end, by_id[i].text, p) for i, p in res.ranked] + rest
    files = {s.file for s in spans}
    return bl.Retrieval("jev_map", spans, len(files), sum(file_size(index_root, f) for f in files),
                        {"stage": "AB", "stage_b_chunks": len(cands)}), meta


index_root: Path = repo_root()


def estimate_map(index: bl.Bm25Index, items: list[gd.Item], file_map: dict[str, str]) -> dict:
    """ESTIMATED_* da variante `jev_map` (2 requisições por pergunta), SEM rede. Etapa A é exata (mapa + pergunta). Etapa B
    depende do que o Jev escolher: limite INFERIOR = trechos dos arquivos esperados (acerto perfeito), SUPERIOR = o teto."""
    stage_a = [payload_bytes(request_body(*retrieval_request(it.question, file_map, "map"), PINNED_MODEL)) for it in items]
    lo: list[int] = []
    hi: list[int] = []
    for it in items:
        cs = [c for c in index.chunks if c.file in set(it.expected_files)]
        cs = cs[:STAGE_B_MAX_CHUNKS]
        while cs and sum(c.nbytes for c in cs) > STAGE_B_CAP_BYTES:
            cs.pop()
        lo.append(payload_bytes(request_body(*retrieval_request(it.question, {c.id: c.text for c in cs}), PINNED_MODEL)))
        hi.append(STAGE_B_CAP_BYTES + 2_000)
    return {"calls": 2 * len(items), "stage_a_bytes": sum(stage_a), "stage_a_max_payload": max(stage_a),
            "stage_b_bytes_lower": sum(lo), "stage_b_bytes_upper": sum(hi), "map_files": len(file_map),
            "map_bytes": sum(len(v.encode("utf-8")) + len(k.encode("utf-8")) for k, v in file_map.items())}


def estimate(index: bl.Bm25Index, items: list[gd.Item], roots: list[str] | None = None,
             file_map: dict[str, str] | None = None) -> dict:
    """ESTIMATED_* de uma rodada Jev sobre o golden, SEM rede. Tokens são PROXY (bytes/4); o preço é o oficial."""
    per_item = []
    for it in items:
        cands = shortlist_candidates(index, it.question, roots=roots)
        state, questions = retrieval_request(it.question, {c.id: c.text for c in cands})
        per_item.append({"id": it.id, "candidates": len(cands),
                         "payload_bytes": payload_bytes(request_body(state, questions, PINNED_MODEL))})
    total = sum(p["payload_bytes"] for p in per_item)
    tok_lo, tok_hi = total / 4.0, total / 3.0       # faixa PROXY: 3–4 bytes por token
    _est = {
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
    return _with_map(_est, index, items, file_map)


def _with_map(est: dict, index: bl.Bm25Index, items: list[gd.Item], file_map: dict[str, str] | None) -> dict:
    if not file_map:
        return est
    m = estimate_map(index, items, file_map)
    v1 = est["ESTIMATED_INPUT_BYTES"]
    lo_b, hi_b = v1 + m["stage_a_bytes"] + m["stage_b_bytes_lower"], v1 + m["stage_a_bytes"] + m["stage_b_bytes_upper"]
    cost = lambda by, d: by / d * PRICE_USD_PER_MTOK_INPUT / 1e6                        # noqa: E731
    est["jev_map"] = m
    est["TOTAL"] = {
        "ESTIMATED_JEV_CALLS": est["ESTIMATED_JEV_CALLS"] + m["calls"],
        "ESTIMATED_INPUT_BYTES": f"{lo_b}..{hi_b}",
        "ESTIMATED_INPUT_TOKENS": f"UNKNOWN; PROXY bytes/4..bytes/3 = {int(lo_b / 4)}..{int(hi_b / 3)}",
        "ESTIMATED_COST": f"US$ {cost(lo_b, 4.0):.4f}..{cost(hi_b, 3.0):.4f} (preço OFICIAL × tokens PROXY; saída grátis)",
        "retries": "max_retries=0: um 429/529 não é repetido (cada retry gastaria uma requisição do teto)",
    }
    return est


def _git(root: Path, *a: str) -> str:
    import subprocess
    p = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def verify_public_checkout(root: Path, meta: dict) -> list[str]:
    """Garante que `root` é EXATAMENTE o repositório público fixado: repositório Git próprio (não o privado), origem e SHA
    esperados, nenhum arquivo rastreado modificado. Sem isto nada do corpus público roda. Não abre rede."""
    problems: list[str] = []
    if meta.get("visibility") != "public":
        return ["golden sem visibility=public"]
    if root.resolve() == repo_root().resolve():
        return ["corpus-root é o repositório privado"]
    top = _git(root, "rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != root.resolve():
        problems.append("corpus-root não é a raiz de um repositório Git próprio")
    if _git(root, "rev-parse", "HEAD") != meta.get("source_sha_full"):
        problems.append(f"HEAD diferente do SHA fixado {meta.get('source_sha_full')}")
    if _git(root, "remote", "get-url", "origin") != meta.get("source_url"):
        problems.append("origem diferente de " + str(meta.get("source_url")))
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        problems.append("há arquivos rastreados modificados no checkout")
    return problems


def make_provider(args: argparse.Namespace, meta: dict | None = None, public: bool = False,
                  n_items: int = 0) -> DecisionProvider | None:
    if args.provider == "none":
        return None
    if args.provider == "fake":
        return FakeJevProvider(args.fake_mode)
    if args.provider == "jev":
        if not args.confirm_external_send:
            raise SystemExit("--provider jev exige --confirm-external-send (código-fonte SAI da máquina). Nada foi enviado.")
        if not os.environ.get(KEY_ENV):
            raise SystemExit(f"variável {KEY_ENV} ausente. Nada foi enviado.")
        if public:
            if not PUBLIC_BENCHMARK_AUTHORIZED:
                raise SystemExit("BLOCKED_AUTHORIZATION: o benchmark público aguarda autorização EXPLÍCITA do dono. "
                                 "Nada foi enviado.")
            budget = 3 * n_items                        # jev_rerank (1) + jev_map (2) por pergunta
            cap = args.max_calls if args.max_calls is not None else budget
            return RealJevProvider(enabled=True, max_calls=min(cap, budget), max_retries=args.max_retries)
        if not PRIVATE_CODE_SEND_APPROVED:
            raise SystemExit("BLOCKED_PRIVACY: o envio de código privado à TypeSafe não está autorizado "
                             "(retenção padrão UNKNOWN; ver docs/research/jev-pilot.md §35). Nada foi enviado. "
                             "Para só validar o protocolo use experiments/jev/smoke.py (corpus sintético).")
        return RealJevProvider(enabled=True, max_calls=args.max_calls if args.max_calls is not None else 60,
                               max_retries=args.max_retries)
    raise SystemExit(f"provedor desconhecido: {args.provider}")


@functools.lru_cache(maxsize=2)
def _corpus_index(root: str, roots: tuple[str, ...], exts: tuple[str, ...]):
    """Corpus + índice BM25 (caro: ~10 MB). Cache por processo; o working tree não muda durante a rodada."""
    files = list_files(Path(root), list(roots), list(exts))
    chunks = build_chunks(Path(root), files)
    return files, chunks, bl.Bm25Index(chunks)


def run(args: argparse.Namespace) -> dict:
    global index_root
    meta, items = gd.load(Path(args.golden))
    public = bool(args.corpus_root)
    root = Path(args.corpus_root).resolve() if public else repo_root()
    index_root = root
    if public:                                  # corpus público: só com o checkout fixado e íntegro
        problems = verify_public_checkout(root, meta)
        if problems:
            raise SystemExit("checkout público inválido (nada foi enviado):\n  " + "\n  ".join(problems))
    elif meta.get("visibility") == "public":
        raise SystemExit("golden público exige --corpus-root (o corpus não é o repositório privado).")
    problems = gd.validate(items, root, meta)
    if public:
        problems += gd.validate_groups(items, meta, root)
    if problems:
        raise SystemExit("golden inconsistente com o working tree:\n  " + "\n  ".join(problems))
    corpus = meta["corpus"]
    files, chunks, index = _corpus_index(str(root), tuple(corpus["roots"]), tuple(corpus["extensions"]))
    file_map = repomap.build_map(root, files) if public else None
    out: dict = {"meta": {"golden_version": meta["version"], "source_sha": meta["source_sha"],
                          "corpus_files": len(files), "corpus_chunks": len(chunks),
                          "corpus_bytes": sum(file_size(root, f) for f in files),
                          "corpus_visibility": "public" if public else "private", "checkout_verified": public or None,
                          "headline_strategy": meta.get("headline_strategy", "jev_rerank"),
                          "thresholds_path": (str(Path(args.golden).resolve().parent / meta["thresholds_file"])
                                              if meta.get("thresholds_file") else None),
                          "provider": args.provider, "fake_mode": args.fake_mode if args.provider == "fake" else None,
                          "deliver_max_spans": bl.DELIVER_MAX_SPANS, "deliver_max_bytes": bl.DELIVER_MAX_BYTES,
                          "rg_context_lines": bl.RG_CONTEXT, "metric_kind": "PROXY_METRIC (bytes)"}}
    if args.estimate_only:
        out["estimate"] = estimate(index, items, corpus["roots"], file_map)
        return out
    provider = make_provider(args, meta, public, len(items))
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
            if file_map:                                         # variante B: só no benchmark público
                t0 = time.perf_counter()
                mr, mm = jev_map(index, file_map, provider, it.question, corpus["roots"])
                rows.append(_row(it, mr, bl.deliver(mr.spans, max_spans=mm.get("deliver_k", bl.DELIVER_MAX_SPANS)), root,
                                 (time.perf_counter() - t0) * 1000, **mm))
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
    ap.add_argument("--max-calls", type=int, default=None,
                    help="teto de requisições de rede (privado: 60; público: 3 por pergunta)")
    ap.add_argument("--max-retries", type=int, default=0, help="retries em 429/529; cada um gasta uma requisição do teto")
    ap.add_argument("--corpus-root", default=None,
                    help="raiz do corpus PÚBLICO fixado (ex.: data/jev-pilot/public/scrapy); sem isto o corpus é o repositório privado")
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
