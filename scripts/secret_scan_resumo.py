"""Frente GitHub (29.155 / 29.158): resumo REDIGIDO do relatório do gitleaks, e a issue do achado.

O gitleaks grava um relatório JSON com, por achado, o valor (`Secret`, `Match`), o autor e o e-mail do commit. Este script lê
esse relatório e só deixa passar o que não é dado sensível: a regra, o caminho do arquivo, a linha e o hash curto do commit.
Valor, linha de contexto, autor, e-mail e impressão digital NUNCA saem daqui, nem no log nem na issue. Se o relatório vier
SEM a redação pedida (`--redact`: `Secret` diferente de "REDACTED"), o script não imprime nenhum achado e sai com 2: uma
varredura que vazaria o valor não vira log.

Uso (no workflow `secret-scan.yml`): `python3 scripts/secret_scan_resumo.py --relatorio r.json --repo dono/nome --run-id N`.
Com `--ensaio` só imprime. A issue leva o rótulo `achado`, título "Secret scan AAAA-MM-DD: N achado(s)"; se já há uma aberta com
o prefixo "Secret scan", comenta nela. Sem achados, não abre nada.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone

MAX_LISTADOS = 50
ROTULO = "achado"
PREFIXO_TITULO = "Secret scan"
_REGRA = re.compile(r"[\w.-]{1,60}")
_CAMINHO = re.compile(r"[\w./ +@=,()-]{1,160}")  # sem `<`, `>`, crase, `#`, `|` nem quebra de linha: não vira marcação nem menção
_COMMIT = re.compile(r"[0-9a-f]{7,40}")

Gh = Callable[..., str]


class RelatorioSemRedacao(Exception):
    """O relatório trouxe o valor do segredo: nada dele pode ser impresso."""


def _regra(v: object) -> str:
    t = str(v or "")
    return t if _REGRA.fullmatch(t) else "regra-desconhecida"


def _caminho(v: object) -> str:
    t = str(v or "")
    return t if _CAMINHO.fullmatch(t) and ".." not in t and "@" not in t else "(caminho omitido)"


def _linha(v: object) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 10**7 else 0


def _commit(v: object) -> str:
    t = str(v or "")
    return t[:7] if _COMMIT.fullmatch(t) else "?"


def resumir(achados: list[dict[str, object]]) -> dict[str, object]:
    """Só campos seguros. Levanta RelatorioSemRedacao se algum achado trouxer valor."""
    for a in achados:
        # Chave ausente também é recusa: sem prova de que o relatório foi redigido, nada é impresso (falha fechada).
        if "Secret" not in a or str(a["Secret"]) not in ("REDACTED", ""):
            raise RelatorioSemRedacao("o relatório do gitleaks veio sem a redação")
    por_regra = Counter(_regra(a.get("RuleID")) for a in achados)
    itens = [{"regra": _regra(a.get("RuleID")), "arquivo": _caminho(a.get("File")),
              "linha": _linha(a.get("StartLine")), "commit": _commit(a.get("Commit"))} for a in achados]
    itens.sort(key=lambda i: (i["regra"], i["arquivo"], i["linha"], i["commit"]))
    return {"total": len(achados), "por_regra": dict(sorted(por_regra.items())), "itens": itens}


def texto(r: dict[str, object], run_id: str) -> str:
    linhas = [f"Varredura do histórico inteiro com o gitleaks (run {run_id}). **{r['total']} achado(s).** "
              "Nenhum valor, linha de contexto, autor ou e-mail está neste texto: abra o commit citado e confira à mão.", "",
              "| Regra | Achados |", "|---|---|"]
    linhas += [f"| {k} | {v} |" for k, v in r["por_regra"].items()]  # type: ignore[union-attr]
    itens = r["itens"]
    linhas += ["", "| Regra | Arquivo | Linha | Commit |", "|---|---|---|---|"]
    linhas += [f"| {i['regra']} | `{i['arquivo']}` | {i['linha']} | {i['commit']} |" for i in itens[:MAX_LISTADOS]]  # type: ignore[index]
    if len(itens) > MAX_LISTADOS:  # type: ignore[arg-type]
        linhas.append(f"\n(mostrados {MAX_LISTADOS} de {len(itens)})")  # type: ignore[arg-type]
    linhas += ["", "Falso positivo conhecido entra em `.gitleaks.toml` (allowlist por caminho ou regra), nunca por valor. "
               "Achado de verdade: o segredo é considerado vazado e precisa ser trocado, não só apagado do histórico."]
    return "\n".join(linhas)


def gh_real(*args: str, entrada: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=entrada, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def abrir_ou_comentar(repo: str, r: dict[str, object], run_id: str, hoje: str, gh: Gh) -> str:
    corpo = texto(r, run_id)
    abertas = json.loads(gh("issue", "list", "--repo", repo, "--label", ROTULO, "--state", "open", "--limit", "100",
                            "--json", "number,title"))
    ja = next((i for i in abertas if str(i.get("title", "")).startswith(PREFIXO_TITULO)), None)
    if ja:
        gh("issue", "comment", str(ja["number"]), "--repo", repo, "--body-file", "-", entrada=f"Nova varredura.\n\n{corpo}")
        return f"comentada {ja['number']}"
    # O rótulo `achado` é do scripts/github_rotulos.py (cor e descrição dele): criar aqui com outros valores brigaria com ele.
    url = gh("issue", "create", "--repo", repo, "--label", ROTULO, "--title", f"{PREFIXO_TITULO} {hoje}: {r['total']} achado(s)",
             "--body-file", "-", entrada=corpo)
    return f"aberta {url.strip().rsplit('/', 1)[-1]}"


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resumo redigido do relatório do gitleaks.")
    ap.add_argument("--relatorio", required=True)
    ap.add_argument("--repo", default="")
    ap.add_argument("--run-id", default="0")
    ap.add_argument("--ensaio", action="store_true")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"\d{1,20}", a.run_id):
        print("erro: --run-id precisa ser numérico", file=sys.stderr)
        return 1
    try:
        with open(a.relatorio, encoding="utf-8") as fh:
            achados = json.load(fh) or []
        r = resumir(achados)
    except RelatorioSemRedacao as e:
        print(f"erro: {e}; nada foi impresso", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, AttributeError) as e:
        print(f"erro: relatório ilegível ({type(e).__name__})", file=sys.stderr)
        return 1
    print(f"gitleaks: {r['total']} achado(s)")
    for k, v in r["por_regra"].items():  # type: ignore[union-attr]
        print(f"  regra {k}: {v}")
    if r["total"] == 0 or a.ensaio:
        return 0
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("erro: informe --repo dono/nome", file=sys.stderr)
        return 1
    try:
        feito = abrir_ou_comentar(a.repo, r, a.run_id, datetime.now(timezone.utc).strftime("%Y-%m-%d"), gh or gh_real)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    print(f"issue: {feito}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
