"""Frente GitHub (29.193): a corrida noturna do PostgreSQL só roda se algo que ela testa mudou desde a última vez que ela passou.

Quem chama: o job `pg-necessario` do `.github/workflows/ci.yml` (hospedado), antes do `backend-postgres`. A saída `rodar=true|false`
vai para `$GITHUB_OUTPUT` e para a tela, com o motivo. O job do PostgreSQL é o maior consumidor de minutos hospedados (~42 min por
noite); numa noite em que nada que ele testa mudou desde a última corrida VERDE dele, rodar de novo repetiria o mesmo resultado.

Regra (qualquer dúvida roda; nunca pula por incerteza):
  - disparo manual (`workflow_dispatch`): roda sempre (quem dispara quer rodar);
  - procura, nas últimas corridas do `ci.yml` (cron e manual), a mais recente em que o job `backend · pytest (PostgreSQL)` terminou
    em sucesso, e compara o commit dela com o de hoje pela API de comparação (`compare`);
  - sem corrida verde recente, comparação com erro, resposta truncada (300 arquivos) ou qualquer erro do `gh`: roda;
  - com arquivo mudado sob `backend/` (código, testes, migrações, dependências, configuração e conhecimento de apps), `.github/actions/`
    ou no próprio `ci.yml`: roda; só se TODOS os arquivos mudados ficarem de fora: não roda.

Só LEITURA (`gh api`). Não imprime nome de conta nem de repositório; erro do `gh` vira só o código de saída.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable

JOB = "backend · pytest (PostgreSQL)"
PREFIXOS = ("backend/", ".github/actions/", ".github/workflows/ci.yml")
RUNS_LIDOS = 14
LIMITE_COMPARE = 300
Gh = Callable[..., str]


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"o gh falhou (código {r.returncode})")
    return r.stdout


def relevantes(arquivos: list[str]) -> list[str]:
    return [a for a in arquivos if a.startswith(PREFIXOS)]


def ultima_verde(repo: str, run_atual: str, gh: Gh) -> str | None:
    """Commit da corrida mais recente (cron ou manual) em que o job do PostgreSQL passou; None se não achar nas últimas."""
    runs = json.loads(gh("api", f"repos/{repo}/actions/workflows/ci.yml/runs?per_page=30&status=completed"))["workflow_runs"]
    vistos = 0
    for r in runs:
        if str(r.get("id")) == run_atual or r.get("event") not in ("schedule", "workflow_dispatch"):
            continue
        vistos += 1
        if vistos > RUNS_LIDOS:
            break
        jobs = json.loads(gh("api", f"repos/{repo}/actions/runs/{r['id']}/jobs?per_page=100&filter=latest"))["jobs"]
        if any(j.get("name") == JOB and j.get("conclusion") == "success" for j in jobs):
            return str(r["head_sha"])
    return None


def decidir(repo: str, evento: str, head: str, run_atual: str, gh: Gh | None = None) -> tuple[bool, str]:
    """(rodar, motivo). Erro de qualquer tipo vira rodar=True."""
    gh = gh or gh_real
    if evento == "workflow_dispatch":
        return True, "disparo manual: roda sempre"
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return True, "commit de hoje ilegível: roda"
    try:
        base = ultima_verde(repo, run_atual, gh)
        if base is None:
            return True, f"nenhuma corrida verde do PostgreSQL nas últimas {RUNS_LIDOS}: roda"
        if base == head:
            return False, "o commit é o mesmo da última corrida verde do PostgreSQL: nada mudou"
        cmp = json.loads(gh("api", f"repos/{repo}/compare/{base}...{head}"))
        arquivos = [f["filename"] for f in cmp.get("files") or []]
        if cmp.get("status") not in ("ahead", "identical", "behind", "diverged"):
            return True, "comparação com estado inesperado: roda"
        if len(arquivos) >= LIMITE_COMPARE or int(cmp.get("total_commits", 0)) > 250:
            return True, "comparação grande demais (resposta truncada): roda"
        if cmp.get("status") == "behind":
            return True, "o commit de hoje está atrás do último verde: roda"
        achados = relevantes(arquivos)
        if achados:
            return True, f"{len(achados)} arquivo(s) relevante(s) mudaram desde o último verde (ex.: {achados[0]}): roda"
        return False, f"{len(arquivos)} arquivo(s) mudaram desde o último verde, nenhum que o PostgreSQL teste: pula"
    except (RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as erro:
        return True, f"não consegui decidir ({type(erro).__name__}): roda"


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    ap = argparse.ArgumentParser(description="Decide se a corrida noturna do PostgreSQL precisa rodar.")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--evento", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    ap.add_argument("--head", default=os.environ.get("GITHUB_SHA", ""))
    ap.add_argument("--run", default=os.environ.get("GITHUB_RUN_ID", "0"))
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo) or not re.fullmatch(r"\d{1,20}", a.run):
        # argumento torto não pode virar "pula": a saída é rodar
        rodar, motivo = True, "argumentos fora do formato: roda"
    else:
        rodar, motivo = decidir(a.repo, a.evento, a.head, a.run, gh)
    print(f"rodar={'true' if rodar else 'false'}\n{motivo}")
    saida = os.environ.get("GITHUB_OUTPUT")
    if saida:
        with open(saida, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"rodar={'true' if rodar else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
