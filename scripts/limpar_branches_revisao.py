"""Frente GitHub (29.155, C18): apaga no GitHub as branches de revisão e de teste que JÁ ESTÃO na main.

As frentes abrem PRs só de revisão a partir de branches `revisao/*` e provas descartáveis em `teste/*`: cada uma aponta para um
commit que depois entra na main por avanço rápido. Sem limpeza elas se acumulam. Este script apaga uma branch só se TODAS estas
condições valem: o nome começa por um prefixo permitido (`revisao/`, `teste/`), a ponta já está contida na main (a comparação do
GitHub diz `identical` ou `behind`), não há PR ABERTO com ela como origem e o último commit tem mais de `--horas-minimas` horas.
Qualquer dúvida (erro de API, estado desconhecido) deixa a branch como está. Nada fora desses dois prefixos é tocado, nem a main.

Por padrão é ensaio (só imprime); `--aplicar` apaga. Roda no workflow `limpa-branches-revisao.yml` (runner hospedado).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

PREFIXOS = ("revisao/", "teste/")
_NOME = re.compile(r"[\w./-]{1,200}")
Gh = Callable[..., str]


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _itens(saida: str) -> list[dict[str, object]]:
    """`gh api --paginate` pode devolver vários arrays colados; lê todos."""
    texto = saida.strip()
    if not texto:
        return []
    achados: list[dict[str, object]] = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(texto):
        valor, i = decoder.raw_decode(texto, i)
        achados.extend(valor if isinstance(valor, list) else [valor])
        while i < len(texto) and texto[i].isspace():
            i += 1
    return achados


def listar(repo: str, prefixos: tuple[str, ...], gh: Gh) -> list[tuple[str, str]]:
    """(nome, sha da ponta) de cada branch dos prefixos permitidos."""
    achadas: list[tuple[str, str]] = []
    for p in prefixos:
        for it in _itens(gh("api", "--paginate", f"repos/{repo}/git/matching-refs/heads/{p}")):
            nome = str(it.get("ref", "")).removeprefix("refs/heads/")
            obj = it.get("object")
            sha = str(obj.get("sha", "")) if isinstance(obj, dict) else ""
            if nome.startswith(prefixos) and _NOME.fullmatch(nome) and re.fullmatch(r"[0-9a-f]{40}", sha):
                achadas.append((nome, sha))
    return sorted(set(achadas))


def decidir(repo: str, nome: str, sha: str, agora: datetime, horas_minimas: int, gh: Gh) -> tuple[bool, str]:
    """(apagar?, motivo). Só True com todas as condições provadas."""
    comp = json.loads(gh("api", f"repos/{repo}/compare/main...{sha}"))
    if comp.get("status") not in ("identical", "behind"):
        return False, f"não está na main ({comp.get('status')})"
    abertos = json.loads(gh("pr", "list", "--repo", repo, "--head", nome, "--state", "open", "--json", "number"))
    if abertos:
        return False, "tem PR aberto"
    data = json.loads(gh("api", f"repos/{repo}/commits/{sha}"))["commit"]["committer"]["date"]
    quando = datetime.fromisoformat(data.replace("Z", "+00:00"))
    if agora - quando < timedelta(hours=horas_minimas):
        return False, "ponta recente demais"
    return True, "já está na main, sem PR aberto"


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apaga branches revisao/* e teste/* já mescladas na main.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--aplicar", action="store_true", help="apaga de verdade (padrão: ensaio)")
    ap.add_argument("--horas-minimas", type=int, default=6)
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("erro: --repo precisa ser dono/nome", file=sys.stderr)
        return 1
    gh = gh or gh_real
    agora = agora or datetime.now(timezone.utc)
    apagadas = mantidas = erros = 0
    try:
        branches = listar(a.repo, PREFIXOS, gh)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    for nome, sha in branches:
        try:
            apagar, motivo = decidir(a.repo, nome, sha, agora, a.horas_minimas, gh)
            if apagar and a.aplicar:
                gh("api", "-X", "DELETE", f"repos/{a.repo}/git/refs/heads/{nome}")
        except (RuntimeError, ValueError, KeyError) as e:
            erros += 1
            print(f"mantida {nome}: erro ({type(e).__name__}), nada feito")
            continue
        if apagar:
            apagadas += 1
            print(f"{'apagada' if a.aplicar else 'apagaria'} {nome}: {motivo}")
        else:
            mantidas += 1
            print(f"mantida {nome}: {motivo}")
    print(f"resumo: {len(branches)} branches dos prefixos {', '.join(PREFIXOS)}; "
          f"{apagadas} {'apagadas' if a.aplicar else 'a apagar (ensaio)'}, {mantidas} mantidas, {erros} com erro")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
