"""Congelamento do holdout: hashes de tudo que não pode mudar depois da primeira chamada Jev real.

    python experiments/jev/holdout_bench/freeze.py --check    # confere contra freeze.json (sai 1 se algo mudou)
    python experiments/jev/holdout_bench/freeze.py --write    # SÓ antes da primeira chamada real; depois disso é violação

Congela: golden (perguntas, gabarito, regras de grupo), limiares, regra híbrida, constantes e código do pipeline `jev_map` (corpus,
chunking, mapa do repositório, formato das perguntas Choice/Noul), árvore Git do corpus. Sem rede, sem Jev.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import benchmark as bm                                     # noqa: E402
import corpus                                              # noqa: E402
import hybrid as hy                                        # noqa: E402
import provider as pv                                      # noqa: E402
import repomap                                             # noqa: E402
from corpus import repo_root                               # noqa: E402

FREEZE = HERE / "freeze.json"
DEFAULT_CHECKOUT = repo_root() / "data" / "jev-pilot" / "public" / "poetry"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes().replace(b"\r\n", b"\n"))


def _src(*objs) -> str:
    return _sha("\n".join(inspect.getsource(o) for o in objs).replace("\r\n", "\n").encode("utf-8"))


def manifest(checkout: Path | None = DEFAULT_CHECKOUT) -> dict:
    out = {
        "golden_sha256": _file_sha(HERE / "golden.json"),
        "thresholds_sha256": _file_sha(HERE / "thresholds.json"),
        "hybrid_rule_hash": hy.rule_hash(),
        "pipeline_constants": {
            "N_SHORTLIST": bm.N_SHORTLIST, "STAGE_B_FILES": bm.STAGE_B_FILES, "STAGE_B_MAX_CHUNKS": bm.STAGE_B_MAX_CHUNKS,
            "STAGE_B_CAP_BYTES": bm.STAGE_B_CAP_BYTES, "MAP_REPRESENT_FILES": bm.MAP_REPRESENT_FILES,
            "ADAPTIVE_MASS": bm.ADAPTIVE_MASS, "ADAPTIVE_KMIN": bm.ADAPTIVE_KMIN, "ADAPTIVE_KMAX": bm.ADAPTIVE_KMAX,
            "DELIVER_MAX_SPANS": bm.bl.DELIVER_MAX_SPANS, "DELIVER_MAX_BYTES": bm.bl.DELIVER_MAX_BYTES, "RG_CONTEXT": bm.bl.RG_CONTEXT,
            "PINNED_MODEL": pv.PINNED_MODEL, "PRICE_USD_PER_MTOK_INPUT": pv.PRICE_USD_PER_MTOK_INPUT,
            "chunk_MAX_LINES": corpus.MAX_LINES, "chunk_WINDOW": corpus.WINDOW, "chunk_STEP": corpus.STEP,
        },
        "pipeline_code_sha256": {
            "jev_map_and_helpers": _src(bm.jev_map, bm.adaptive_k, bm._chunk_scores, bm._hybrid_row),
            "prompt_format_retrieval_request": _src(pv.retrieval_request, pv.request_body),
            "repomap": _src(repomap.describe, repomap.build_map, repomap._names, repomap._doc_line),
            "chunking": _src(corpus.build_chunks, corpus._units),
        },
    }
    if checkout is not None and (checkout / ".git").exists():
        tree = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD:src/poetry"], capture_output=True, text=True).stdout.strip()
        files = corpus.list_files(checkout, ["src/poetry"], [".py"])
        out["corpus"] = {"git_tree_src_poetry": tree, "files": len(files),
                         "repomap_sha256": _sha(json.dumps(repomap.build_map(checkout, files), sort_keys=True, ensure_ascii=False).encode("utf-8"))}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--checkout", default=str(DEFAULT_CHECKOUT))
    args = ap.parse_args(argv)
    cur = manifest(Path(args.checkout))
    if args.write:
        FREEZE.write_text(json.dumps(cur, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("freeze.json escrito")
        return 0
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    diffs = [k for k in frozen if frozen[k] != cur.get(k)]
    if diffs:
        print("MUDOU depois do congelamento:", ", ".join(diffs))
        return 1
    print("OK: nada mudou desde o congelamento")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
