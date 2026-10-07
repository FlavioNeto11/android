"""Frente GitHub (29.171): põe no PR o rótulo que o PREFIXO da branch indica, e só ele.

`ci/…` é da frente GitHub (`frente:github`); `copilot/…` é do agente de nuvem (`agente`); `devops/…`, `jev/…`, `aprendizado/…`,
`portal/…` e `canais/…` são da frente de mesmo nome (29.203; as integrações das frentes e a branch da DevOps usam esses prefixos). Outro
prefixo não ganha rótulo: as frentes usam `feat/…` e `fix/…` para o trabalho do item, então o prefixo não diz a frente e adivinhar seria dado errado. O rótulo só é
posto se já existir no repositório (`scripts/github_rotulos.py --aplicar` os cria); nunca cria rótulo, nunca tira rótulo, nunca
comenta. Qualquer dúvida deixa o PR como está e sai com 0, para não pintar de vermelho um PR por causa de etiqueta.

Por padrão é ensaio (só imprime); `--aplicar` rotula. Roda no workflow `rotula-pr.yml` (runner hospedado, sem checkout do PR).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable

ROTULO_POR_PREFIXO = {"ci/": "frente:github", "copilot/": "agente", "devops/": "frente:devops", "jev/": "frente:jev",
                      "aprendizado/": "frente:aprendizado", "portal/": "frente:portal", "canais/": "frente:canais"}
_BRANCH = re.compile(r"[\w./-]{1,200}")
Gh = Callable[..., str]


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def rotulo_da_branch(branch: str) -> str | None:
    if not _BRANCH.fullmatch(branch) or ".." in branch:
        return None
    return next((r for p, r in ROTULO_POR_PREFIXO.items() if branch.startswith(p)), None)


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rotula o PR pelo prefixo da branch.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--aplicar", action="store_true", help="rotula de verdade (padrão: ensaio)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("erro: --repo precisa ser dono/nome", file=sys.stderr)
        return 1
    rotulo = rotulo_da_branch(a.branch)
    if rotulo is None:
        print("sem rótulo: o prefixo da branch não indica uma frente")
        return 0
    gh = gh or gh_real
    try:
        existentes = {str(x.get("name")) for x in json.loads(gh("label", "list", "--repo", a.repo, "--limit", "200", "--json", "name"))}
        if rotulo not in existentes:
            print(f"sem rótulo: '{rotulo}' não existe no repositório (rode scripts/github_rotulos.py --aplicar)")
            return 0
        if a.aplicar:
            gh("pr", "edit", str(a.pr), "--repo", a.repo, "--add-label", rotulo)
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"erro: {e}; PR deixado como está", file=sys.stderr)
        return 0
    print(f"{'rotulado' if a.aplicar else 'rotularia'} PR {a.pr} com '{rotulo}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
