"""Prepara (ou confere) o checkout PÚBLICO fixado do benchmark: Scrapy, tag 2.19.0, SHA 8026dee…

Único passo deste benchmark que usa rede, e só para `git clone` de um repositório público (BSD-3). NÃO chama o Jev, NÃO usa
chave nenhuma e NÃO executa código do Scrapy: o corpus só é lido como texto. O destino fica em `data/jev-pilot/public/`
(ignorado pelo Git). Depois de clonar confere o SHA; se divergir, apaga nada e aborta.

    python experiments/jev/public_bench/prepare.py            # clona se faltar e confere
    python experiments/jev/public_bench/prepare.py --verify   # só confere (sem rede)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import benchmark as bm                                     # noqa: E402
from corpus import repo_root                               # noqa: E402

DEFAULT_DEST = repo_root() / "data" / "jev-pilot" / "public" / "scrapy"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--verify", action="store_true", help="só conferir o checkout existente (sem rede)")
    args = ap.parse_args(argv)
    meta = json.loads((HERE / "golden.json").read_text(encoding="utf-8"))["meta"]
    dest = Path(args.dest)
    if not (dest / ".git").exists():
        if args.verify:
            print(f"checkout ausente em {dest}")
            return 2
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", meta["source_tag"], meta["source_url"], str(dest)],
                       check=True)
    problems = bm.verify_public_checkout(dest, meta)
    if problems:
        print("checkout NÃO confere:\n  " + "\n  ".join(problems))
        return 1
    print(f"OK: {meta['source_url']} @ {meta['source_sha_full']} ({meta['source_tag']}), licença {meta['license']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
