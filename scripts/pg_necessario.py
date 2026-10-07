"""Frente GitHub (29.193): a corrida noturna do PostgreSQL só roda se algo que ela testa mudou desde a última vez que ela passou.

Quem chama: o job `pg-necessario` do `.github/workflows/ci.yml` (hospedado), antes do `backend-postgres`. A saída `rodar=true|false`
vai para `$GITHUB_OUTPUT` e para a tela, com o motivo. O job do PostgreSQL é o maior consumidor de minutos hospedados (~42 min por
noite); numa noite em que nada que ele testa mudou desde a última corrida VERDE dele, rodar de novo repetiria o mesmo resultado.

Regra (qualquer dúvida roda; nunca pula por incerteza):
  - disparo manual (`workflow_dispatch`): roda sempre (quem dispara quer rodar);
  - procura, nas últimas corridas do `ci.yml` na `main` (cron e manual), a mais recente em que o job `backend · pytest (PostgreSQL)` terminou
    em sucesso, e compara o commit dela com o de hoje pela API de comparação (`compare`);
  - sem corrida verde recente, comparação com erro, resposta truncada (300 arquivos), verde que não seja ancestral do commit de hoje
    (`diverged`/`behind`) ou qualquer erro do `gh`: roda;
  - só pula se TODO arquivo mudado (inclusive o nome antigo de um arquivo movido) for de `docs/`, `.claude/`, `frontend/` (menos
    `frontend/src/lib/rotas.ts`, que um teste do backend lê), dos outros workflows e de modelos de issue, ou for `.md`; o `ci.yml`
    e todo o resto (`backend/`, `scripts/`, `config/`, `.github/actions/`, arquivo novo e desconhecido) fazem rodar.

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
# O que NÃO exige o PostgreSQL: documentação, frontend, estado das sessões e os outros workflows. O resto é relevante (lista do que é
# irrelevante, não do que é relevante: os testes do backend leem scripts/, config/ e até frontend/src/lib/rotas.ts, e um arquivo
# novo e desconhecido tem de rodar). As exceções valem mesmo dentro de um prefixo irrelevante.
IRRELEVANTES = ("docs/", ".claude/", "frontend/", ".github/ISSUE_TEMPLATE/", ".github/workflows/", ".github/agents/")
EXCECOES = ("frontend/src/lib/rotas.ts", ".github/workflows/ci.yml")
RUNS_LIDOS = 14
LIMITE_COMPARE = 300
Gh = Callable[..., str]


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"o gh falhou (código {r.returncode})")
    return r.stdout


def irrelevante(arquivo: str) -> bool:
    if arquivo in EXCECOES:
        return False
    return arquivo.endswith(".md") or arquivo.startswith(IRRELEVANTES)


def relevantes(arquivos: list[str]) -> list[str]:
    return [a for a in arquivos if not irrelevante(a)]


def ultima_verde(repo: str, run_atual: str, gh: Gh) -> str | None:
    """Commit da corrida mais recente (cron ou manual) em que o job do PostgreSQL passou; None se não achar nas últimas."""
    runs = json.loads(gh("api", f"repos/{repo}/actions/workflows/ci.yml/runs?per_page=30&status=completed&branch=main"))["workflow_runs"]
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
        arquivos = []
        for f in cmp.get("files") or []:
            arquivos.append(f["filename"])
            if f.get("previous_filename"):  # arquivo movido para fora da pasta continua sendo mudança da pasta de origem
                arquivos.append(f["previous_filename"])
        if cmp.get("status") not in ("ahead", "identical"):
            # só vale como "já passou" um commit que seja ANCESTRAL do de hoje: "diverged" (outra linha de código) e "behind" rodam
            return True, f"o último verde não é ancestral do commit de hoje (comparação {str(cmp.get('status'))[:20]}): roda"
        if len(arquivos) >= LIMITE_COMPARE or int(cmp.get("total_commits", 0)) > 250:
            return True, "comparação grande demais (resposta truncada): roda"
        achados = relevantes(arquivos)
        if achados:
            return True, f"{len(achados)} arquivo(s) relevante(s) mudaram desde o último verde (ex.: {achados[0]}): roda"
        return False, f"{len(arquivos)} arquivo(s) mudaram desde o último verde, nenhum que o PostgreSQL teste: pula"
    except (RuntimeError, ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as erro:
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
