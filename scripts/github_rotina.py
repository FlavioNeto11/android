"""Frente GitHub (29.155, C10): a leitura diária do GitHub numa linha só, só LEITURA.

`python scripts/github_rotina.py --repo dono/nome` lê, pela API do `gh`, as últimas 26 horas do repositório e imprime:
uma linha de resumo (a que a frente GitHub manda à orquestradora) e, embaixo, os detalhes. Não escreve nada: não cria issue,
não comenta, não dispara workflow, não toca o runner. O que ele NÃO consegue ler é o saldo de créditos do Copilot (a página
de uso da conta pede o escopo `user`, que o `gh` do dono não tem): a linha traz uma ESTIMATIVA por contagem de runs, marcada
como tal, e a leitura real da página de uso continua sendo feita à mão no Chrome.

A estimativa usa o que a frente mediu em 06/10/2026 (página de uso da conta, 01 a 06/10): uma revisão de PR do Copilot custou
cerca de 146 créditos e uma tarefa do agente de nuvem cerca de 31. Dois números de uma semana: servem de ordem de grandeza,
não de saldo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

JANELA_HORAS = 26  # o cron é das 05:17Z; 26 h cobre "ontem à noite até agora" com folga
CREDITOS_POR_REVISAO = 146
CREDITOS_POR_TAREFA_DO_AGENTE = 31
ORCAMENTO_MENSAL = 7000
RUINS = ("failure", "cancelled", "timed_out", "startup_failure")

Gh = Callable[..., str]


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _hora(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None


def minutos(run: dict[str, object]) -> int | None:
    a, b = _hora(str(run.get("run_started_at") or "")), _hora(str(run.get("updated_at") or ""))
    return None if a is None or b is None else max(int((b - a).total_seconds() // 60), 0)


def cron_da_noite(runs: list[dict[str, object]]) -> dict[str, object] | None:
    """O run do CI de evento `schedule` mais recente da janela, ou None."""
    cron = [r for r in runs if r.get("name") == "CI" and r.get("event") == "schedule"]
    return max(cron, key=lambda r: str(r.get("created_at")), default=None)


def resumir(runs: list[dict[str, object]], issues_ci: list[dict[str, object]], issues_agente: list[dict[str, object]],
            prs_agente: list[dict[str, object]], runners: list[dict[str, object]], agora: datetime,
            total_runs: int | None = None) -> dict[str, object]:
    """Tudo o que a linha diz, em dados. Função pura: o teste a chama sem rede."""
    desde = agora - timedelta(hours=JANELA_HORAS)
    # run sem hora legível NÃO entra na janela: contá-lo como de agora inflaria o número sem aviso
    na_janela = [r for r in runs if (_hora(str(r.get("created_at"))) or desde - timedelta(seconds=1)) >= desde]
    cron = cron_da_noite(na_janela)
    revisoes = [r for r in na_janela if str(r.get("name", "")).startswith("Running Copilot Code Review")]
    tarefas = [r for r in na_janela if str(r.get("name", "")).startswith("Running Copilot cloud agent")]
    ruins = [r for r in na_janela if str(r.get("conclusion")) in RUINS and r.get("name") in ("CI", "Contêiner")]
    central = next((x for x in runners if x.get("name") == "central"), None)
    abertos_do_agente = [p for p in prs_agente if str(p.get("state")) == "OPEN"]
    return {
        "cron": None if cron is None else {
            "id": cron.get("id"), "conclusao": cron.get("conclusion") or cron.get("status"), "minutos": minutos(cron),
            "data": str(cron.get("created_at"))[:10]},
        "runs": len(na_janela),
        # a API entrega no máximo 100 runs por página: se houver mais, as contagens abaixo são PISO, não valor
        "truncado": total_runs is not None and total_runs > len(runs),
        "runs_lidos": len(runs),
        "runs_total": total_runs,
        "runs_ruins": [{"id": r.get("id"), "nome": r.get("name"), "evento": r.get("event"), "conclusao": r.get("conclusion")}
                       for r in ruins],
        "revisoes_copilot": len(revisoes),
        "tarefas_do_agente": len(tarefas),
        "creditos_estimados": len(revisoes) * CREDITOS_POR_REVISAO + len(tarefas) * CREDITOS_POR_TAREFA_DO_AGENTE,
        "runner_central": None if central is None else {"status": central.get("status"), "ocupado": bool(central.get("busy"))},
        "issues_ci_abertas": [i.get("number") for i in issues_ci],
        "issues_agente_abertas": [i.get("number") for i in issues_agente],
        "prs_do_agente_abertos": [{"numero": p.get("number"), "rascunho": bool(p.get("isDraft"))} for p in abertos_do_agente],
    }


def linha(r: dict[str, object], agora: datetime) -> str:
    cron = r["cron"]
    c = "cron: sem corrida na janela" if cron is None else f"cron {cron['data']} {cron['conclusao']} ({cron['minutos']} min, run {cron['id']})"
    ru = r["runner_central"]
    runner = "runner central: NÃO listado" if ru is None else (
        f"runner central {ru['status']}{' ocupado' if ru['ocupado'] else ' livre'}")
    ruins = r["runs_ruins"]
    partes = [
        f"GitHub {agora.strftime('%d/%m %H:%MZ')}",
        c,
        runner,
        f"runs ruins na janela: {len(ruins)}" + (" (" + ", ".join(f"{x['nome']} {x['id']}" for x in ruins[:3]) + ")" if ruins else ""),
        f"issues ci abertas: {len(r['issues_ci_abertas'])}, issues agente abertas: {len(r['issues_agente_abertas'])}",
        f"PRs do agente abertos: {len(r['prs_do_agente_abertos'])}",
        f"Copilot na janela: {r['revisoes_copilot']} revisão(ões) e {r['tarefas_do_agente']} tarefa(s) do agente "
        f"(~{r['creditos_estimados']} créditos ESTIMADOS de {ORCAMENTO_MENSAL}/mês; saldo real só na página de uso)",
    ]
    if r.get("truncado"):
        partes.append(f"ATENÇÃO: TRUNCADO ({r['runs_lidos']} de {r['runs_total']} runs lidos; contagens são piso)")
    return "; ".join(partes)


def coletar(repo: str, gh: Gh, agora: datetime) -> dict[str, object]:
    desde = (agora - timedelta(hours=JANELA_HORAS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pagina = json.loads(gh("api", f"repos/{repo}/actions/runs?per_page=100&created=%3E%3D{desde}"))
    runs = pagina["workflow_runs"]
    issues_ci = json.loads(gh("issue", "list", "--repo", repo, "--label", "ci", "--state", "open", "--json", "number,title"))
    issues_ag = json.loads(gh("issue", "list", "--repo", repo, "--label", "agente", "--state", "open", "--json", "number,title"))
    prs = json.loads(gh("pr", "list", "--repo", repo, "--state", "open", "--author", "app/copilot-swe-agent", "--limit", "20",
                        "--json", "number,state,isDraft,title"))
    runners = json.loads(gh("api", f"repos/{repo}/actions/runners"))["runners"]
    return resumir(runs, issues_ci, issues_ag, prs, runners, agora, total_runs=pagina.get("total_count"))


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Leitura diária do GitHub numa linha (só leitura).")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="dono/nome")
    ap.add_argument("--json", action="store_true", help="imprime os dados em JSON em vez da linha")
    args = ap.parse_args(argv)
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo or ""):
        print("erro: informe --repo dono/nome (ou GITHUB_REPOSITORY)", file=sys.stderr)
        return 1
    agora = agora or datetime.now(timezone.utc)
    try:
        r = coletar(args.repo, gh or gh_real, agora)
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else linha(r, agora))
    return 0


if __name__ == "__main__":
    sys.exit(main())
