"""Frente GitHub (29.201): fecha SEM MERGE os PRs de revisão (`[revisão] …`) que já cumpriram o papel.

    python scripts/fecha_pr_revisao.py --repo dono/nome [--lidos lidos.json] [--horas-minimas 2] [--aplicar]

O PR de revisão existe só para o Codex ler a branch; o merge é pela integração do corte. Sem fechar, eles se acumulam (dezenas
abertos). Este script lê cada PR aberto cujo título começa por `[revisão]` e classifica o que o Codex fez nele:

  - `com_achados`: há revisão do Codex (comentários de achado). Só fecha se o número está em `--lidos` (JSON com os números dos PRs
    cujos achados a frente dona já leu e respondeu; a medida do 29.194 guarda isso);
  - `sem_achado`: o Codex concluiu e reagiu com 👍 (ou o resumo diz Completed) sem revisão: fecha;
  - `limite`: o Codex disse que o limite de uso do plano acabou: fecha (repetir é decisão da orquestradora);
  - `falhou`: o resumo diz Failed: fecha;
  - `andamento`: 👀 ou nenhuma resposta ainda: NÃO fecha.

Só fecha PR com mais de `--horas-minimas` de idade, no máximo `MAXIMO_POR_EXECUCAO` por execução, com um comentário padrão (sem
nome de pessoa, repositório nem valor). Não apaga branch (a limpeza é do `limpar_branches_revisao.py`, 29.155 C18), não mexe em PR
de outro título, não mescla nunca. Reabrir é um clique. Por padrão é ensaio (só imprime a tabela); `--aplicar` fecha. Só `gh`; roda
no terminal da sessão, nunca em workflow.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

PREFIXO = "[revisão] "
MAXIMO_POR_EXECUCAO = 30
COMENTARIO = ("Fechado sem merge: este PR existia só para a revisão automática da branch. Os achados, se houve, já foram entregues "
              "à frente dona do item e a mudança entra pela integração do corte.")
CODEX = "chatgpt-codex-connector[bot]"  # login exato: um nome parecido não pode decidir o fechamento
Gh = Callable[..., str]
_REPO = re.compile(r"[\w][\w.-]*/[\w][\w.-]*")


def gh_real(*args: str) -> str:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        raise RuntimeError(f"gh {args[0]} não rodou: o gh está instalado?") from None
    if r.returncode != 0:
        raise RuntimeError(f"gh {args[0]} saiu com {r.returncode}")  # sem a saída nem a rota: citam repositório e conta
    return r.stdout


def _itens(saida: str) -> list[dict]:
    texto = saida.strip()
    if not texto:
        return []
    achados: list[dict] = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(texto):
        valor, i = decoder.raw_decode(texto, i)
        achados.extend(valor if isinstance(valor, list) else [valor])
        while i < len(texto) and texto[i].isspace():
            i += 1
    return achados


def _do_codex(item: dict) -> bool:
    user = item.get("user")
    return isinstance(user, dict) and str(user.get("login", "")).lower() == CODEX


def estado_do_codex(repo: str, numero: int, gh: Gh) -> str:
    """com_achados | sem_achado | limite | falhou | andamento."""
    revisoes = [r for r in _itens(gh("api", "--paginate", f"repos/{repo}/pulls/{numero}/reviews")) if _do_codex(r)]
    if revisoes:
        return "com_achados"
    comentarios = [c for c in _itens(gh("api", "--paginate", f"repos/{repo}/issues/{numero}/comments")) if _do_codex(c)]
    texto = " ".join(str(c.get("body") or "") for c in comentarios)
    if "usage limits" in texto:
        return "limite"
    if "Failed" in texto and "Completed" not in texto:
        return "falhou"
    reacoes = [x for x in _itens(gh("api", "--paginate", f"repos/{repo}/issues/{numero}/reactions")) if _do_codex(x)]
    if any(x.get("content") == "+1" for x in reacoes) or "Completed" in texto:
        return "sem_achado"
    return "andamento"


def decidir(estado: str, numero: int, idade_h: float, lidos: set[int], horas_minimas: float) -> tuple[bool, str]:
    if idade_h < horas_minimas:
        return False, f"aberto há {idade_h:.1f} h (mínimo {horas_minimas:g} h)"
    if estado == "andamento":
        return False, "o Codex ainda não respondeu"
    if estado == "com_achados" and numero not in lidos:
        return False, "achados ainda não confirmados como lidos pela frente (--lidos)"
    return True, estado


def planejar(repo: str, agora: datetime, lidos: set[int], horas_minimas: float, gh: Gh) -> list[dict]:
    prs = json.loads(gh("pr", "list", "--repo", repo, "--state", "open", "--limit", "100", "--json", "number,title,createdAt"))
    linhas: list[dict] = []
    for p in sorted((x for x in prs if str(x.get("title", "")).startswith(PREFIXO)), key=lambda x: int(x["number"])):
        n = int(p["number"])
        criado = datetime.fromisoformat(str(p["createdAt"]).replace("Z", "+00:00"))
        estado = estado_do_codex(repo, n, gh)
        fecha, motivo = decidir(estado, n, (agora - criado).total_seconds() / 3600, lidos, horas_minimas)
        linhas.append({"pr": n, "estado": estado, "fecha": fecha, "motivo": motivo})
    return linhas


def ler_lidos(caminho: Path | None) -> set[int]:
    if caminho is None:
        return set()
    dado = json.loads(caminho.read_text(encoding="utf-8"))
    if isinstance(dado, dict):  # o registro da medida: só conta como lido o PR com confirmados ou falsos registrados pela frente
        chaves = [k for k, v in dado.items() if isinstance(v, dict) and (int(v.get("confirmados", 0) or 0) + int(v.get("falsos", 0) or 0)) > 0]
    else:
        chaves = dado
    return {int(x) for x in chaves if re.fullmatch(r"\d{1,6}", str(x))}


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fecha sem merge os PRs de revisão já cumpridos.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--lidos", type=Path, help="JSON: lista de números, ou objeto {PR: {confirmados, falsos}} (só vale o PR com algum registrado)")
    ap.add_argument("--horas-minimas", type=float, default=2.0)
    ap.add_argument("--aplicar", action="store_true", help="fecha de verdade (padrão: ensaio)")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not _REPO.fullmatch(a.repo) or not math.isfinite(a.horas_minimas) or a.horas_minimas < 0:
        print("erro: --repo precisa ser dono/nome e --horas-minimas não pode ser negativo", file=sys.stderr)
        return 1
    gh = gh or gh_real
    try:
        lidos = ler_lidos(a.lidos)
        linhas = planejar(a.repo, agora or datetime.now(timezone.utc), lidos, a.horas_minimas, gh)
        print("| PR | Codex | decisão |\n|---|---|---|")
        for l in linhas:
            print(f"| {l['pr']} | {l['estado']} | {'fecha' if l['fecha'] else 'fica'}: {l['motivo']} |")
        a_fechar = [l for l in linhas if l["fecha"]][:MAXIMO_POR_EXECUCAO]
        if not a.aplicar:
            print(f"ensaio: fecharia {len(a_fechar)} de {len(linhas)} PR(s) de revisão abertos")
            return 0
        for l in a_fechar:
            gh("pr", "close", str(l["pr"]), "--repo", a.repo, "--comment", COMENTARIO)
        print(f"fechados sem merge: {len(a_fechar)} de {len(linhas)}")
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
