"""Frente GitHub (29.178): relatório de custo do GitHub numa janela (padrão 7 dias), só LEITURA, sem Chrome.

`python scripts/github_custo.py --repo dono/nome [--dias 7] [--anexar ARQUIVO]` lê, pela API do `gh`, os runs do Actions da janela
e, de cada um, os jobs; imprime (e com `--anexar` acrescenta ao arquivo, com a data) uma tabela por workflow: runs, falhas, jobs
HOSPEDADOS e minutos faturáveis ESTIMADOS (cada job arredondado para cima, em minutos, como o GitHub fatura), jobs no runner
`central` e os minutos que ele ficou ocupado (não faturam, mas ocupam a máquina do dono).

Limites que o relatório diz em voz alta, em vez de esconder:
- A API de billing (`/users/<dono>/settings/billing/...`) pede o escopo `user`, que o `gh` desta máquina não tem (conta é do dono):
  o saldo de minutos e o gasto extra NÃO são lidos aqui; ficam com a leitura da página de uso no Chrome.
- O endpoint de timing do run devolve faturável 0 para runs hospedados em repositório privado (medido em 06/10): por isso os minutos
  são estimados pela duração dos jobs, e são marcados como estimativa.
- Créditos do Copilot: contagem de runs "Running Copilot Code Review" x 146 e "Running Copilot cloud agent" x 31 (medidos em 06/10);
  ESTIMATIVA, não saldo. O uso do Codex não tem API: o limite é do dono.

O texto não leva nome de conta, de repositório nem de runner. Não escreve em lugar nenhum além do `--anexar`.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

CREDITOS_POR_REVISAO = 146
CREDITOS_POR_TAREFA_DO_AGENTE = 31
MAX_RUNS = 600
Gh = Callable[..., str]


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"o gh falhou (código {r.returncode}); o texto do erro não é impresso por conter o nome do repositório")
    return r.stdout


def _itens(saida: str) -> list[dict[str, object]]:
    """`gh api --paginate --jq '.x[]'` devolve objetos colados; lê todos."""
    texto = saida.strip()
    achados: list[dict[str, object]] = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(texto):
        valor, i = decoder.raw_decode(texto, i)
        achados.extend(valor if isinstance(valor, list) else [valor])
        while i < len(texto) and texto[i].isspace():
            i += 1
    return achados


def _hora(texto: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None


def minutos_do_job(job: dict[str, object]) -> int:
    """Minutos faturáveis estimados: duração do job arredondada para cima, 0 se não rodou."""
    ini, fim = _hora(job.get("started_at")), _hora(job.get("completed_at"))
    if ini is None or fim is None or fim <= ini or job.get("conclusion") == "skipped":
        return 0
    return max(1, math.ceil((fim - ini).total_seconds() / 60))


def coletar(repo: str, desde: datetime, gh: Gh) -> tuple[dict[str, dict[str, int]], int]:
    """({workflow: contadores}, runs lidos). Falha de API sobe como RuntimeError."""
    runs = _itens(gh("api", "--paginate", f"repos/{repo}/actions/runs?per_page=100&created=%3E%3D{desde.strftime('%Y-%m-%d')}",
                     "--jq", ".workflow_runs[] | {id, name, event, conclusion, created_at}"))
    runs = [r for r in runs if (c := _hora(r.get("created_at"))) is not None and c >= desde][:MAX_RUNS]
    por: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in runs:
        w = por[str(r.get("name") or "?")]
        w["runs"] += 1
        w["falhas"] += r.get("conclusion") in ("failure", "timed_out", "startup_failure")
        for j in _itens(gh("api", "--paginate", f"repos/{repo}/actions/runs/{int(str(r['id']))}/jobs?per_page=100&filter=all",
                           "--jq", ".jobs[] | {labels, started_at, completed_at, conclusion}")):
            m = minutos_do_job(j)
            if m == 0:
                continue  # job que não rodou (skipped, sem hora): não conta como job nem como minuto
            if "self-hosted" in [str(x) for x in (j.get("labels") or [])]:  # type: ignore[union-attr]
                w["jobs_central"] += 1
                w["min_central"] += m
            else:
                w["jobs_hospedados"] += 1
                w["min_hospedados"] += m
    return {k: dict(v) for k, v in por.items()}, len(runs)


_RESUMO = re.compile(r"\*\*(?P<job>[^*]{1,60})\*\*: .*soma das etapas (?P<soma>\d+) s(?P<resto>.*)")
_COBRADOS = re.compile(r"~?(\d+) min cobrados")
_TESTES = re.compile(r"(pytest|vitest) (\d+) passed")
WORKFLOWS_COM_RESUMO = ("CI leve do PR", "CI")
MAX_LOGS = 40


def resumos_da_semana(repo: str, desde: datetime, gh: Gh) -> tuple[dict[str, dict[str, int]], int, int]:
    """Lê as linhas `Resumo do job` (29.184) dos logs dos runs da janela. ({job: contadores}, runs com resumo, runs lidos)."""
    runs = _itens(gh("api", "--paginate", f"repos/{repo}/actions/runs?per_page=100&created=%3E%3D{desde.strftime('%Y-%m-%d')}",
                     "--jq", ".workflow_runs[] | select(.status==\"completed\") | {id, name, created_at}"))
    runs = [r for r in runs if r.get("name") in WORKFLOWS_COM_RESUMO and (c := _hora(r.get("created_at"))) is not None and c >= desde][:MAX_LOGS]
    por: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    com_resumo = 0
    for r in runs:
        try:
            log = gh("run", "view", str(int(str(r["id"]))), "--repo", repo, "--log")
        except RuntimeError:
            continue
        achou = False
        for linha in log.splitlines():
            m = _RESUMO.search(linha)
            if not m:
                continue
            achou = True
            d = por[m["job"].strip()]
            d["corridas"] += 1
            d["soma_s"] += int(m["soma"])
            c = _COBRADOS.search(m["resto"])
            d["cobrados_min"] += int(c[1]) if c else 0
            for ferramenta, n in _TESTES.findall(m["resto"]):
                d[f"testes_{ferramenta}"] = max(d.get(f"testes_{ferramenta}", 0), int(n))
        com_resumo += achou
    return {k: dict(v) for k, v in por.items()}, com_resumo, len(runs)


def relatorio_resumos(por: dict[str, dict[str, int]], com_resumo: int, lidos: int) -> str:
    linhas = ["", "### Resumos por corrida (linha `Resumo do job`, 29.184)", "",
              f"- Runs lidos: {lidos}; com resumo: {com_resumo} (os anteriores ao 29.184 não têm a linha)."]
    if not por:
        return "\n".join(linhas) + "\n"
    linhas += ["", "| job | corridas | soma média das etapas (s) | min cobrados (soma, estimado) | testes (maior contagem) |", "|---|---|---|---|---|"]
    for job in sorted(por):
        d = por[job]
        testes = " · ".join(f"{k.split('_')[1]} {v}" for k, v in sorted(d.items()) if k.startswith("testes_")) or "-"
        linhas.append(f"| {job.replace('|', '/')} | {d['corridas']} | {d['soma_s'] // d['corridas']} | {d['cobrados_min']} | {testes} |")
    return "\n".join(linhas) + "\n"


def billing_legivel(repo: str, gh: Gh) -> str:
    try:
        gh("api", f"users/{repo.split('/')[0]}/settings/billing/actions")
    except RuntimeError:
        return ("NÃO lido: a API de billing não respondeu (este `gh` não tem o escopo `user`, ou a conta é uma organização); "
                "leia a página de uso no Chrome")
    return "a API respondeu, mas este script NÃO imprime os números do billing: leia a página de uso"


def relatorio(por: dict[str, dict[str, int]], lidos: int, agora: datetime, dias: int, billing: str) -> str:
    g = lambda w, k: por.get(w, {}).get(k, 0)  # noqa: E731
    total_h = sum(g(w, "min_hospedados") for w in por)
    total_c = sum(g(w, "min_central") for w in por)
    revisoes = sum(g(w, "runs") for w in por if w.startswith("Running Copilot Code Review"))
    tarefas = sum(g(w, "runs") for w in por if w.startswith("Running Copilot cloud agent"))
    linhas = [
        f"## Custo do GitHub: janela de {dias} dia(s) até {agora.strftime('%Y-%m-%d %H:%MZ')}", "",
        f"- Runs lidos: {lidos}" + (f" (TRUNCADO em {MAX_RUNS}: ficaram de fora os runs MAIS ANTIGOS da janela; a API também limita a 1000 por consulta)" if lidos >= MAX_RUNS else ""),
        f"- Minutos hospedados FATURÁVEIS (estimativa pela duração dos jobs, arredondada por job): {total_h}",
        f"- Minutos ocupados no runner próprio, self-hosted (não faturam; ocupam a máquina do dono): {total_c}",
        f"- Créditos do Copilot ESTIMADOS: {revisoes} revisão(ões) x {CREDITOS_POR_REVISAO} + {tarefas} tarefa(s) do agente x "
        f"{CREDITOS_POR_TAREFA_DO_AGENTE} = {revisoes * CREDITOS_POR_REVISAO + tarefas * CREDITOS_POR_TAREFA_DO_AGENTE} (saldo real: só a página de uso)",
        "- Codex: sem API de uso; o limite é do dono.",
        "- Limites da conta de minutos: vale só para runners Linux (Windows custa 2x e macOS 10x e não são separados aqui); "
        "inclui todas as tentativas de cada run (`filter=all`).",
        f"- Billing (saldo de minutos e gasto extra): {billing}.", "",
        "| workflow | runs | falhas | jobs hospedados | min faturáveis (est.) | jobs no runner próprio | min no runner próprio |",
        "|---|---|---|---|---|---|---|",
    ]
    for w in sorted(por, key=lambda x: (-g(x, "min_hospedados") - g(x, "min_central"), x)):
        linhas.append(f"| {w.replace('|', '/')} | {g(w, 'runs')} | {g(w, 'falhas')} | {g(w, 'jobs_hospedados')} | "
                      f"{g(w, 'min_hospedados')} | {g(w, 'jobs_central')} | {g(w, 'min_central')} |")
    leve = "CI leve do PR"
    if g(leve, "runs"):
        linhas += ["", f"CI leve do PR: {g(leve, 'min_hospedados') / g(leve, 'runs'):.1f} min faturáveis estimados por run "
                       f"({g(leve, 'runs')} run(s), {g(leve, 'falhas')} com falha)."]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Relatório de custo do GitHub (só leitura).")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--anexar", type=Path, help="acrescenta o relatório a este arquivo (nunca sobrescreve)")
    ap.add_argument("--resumos", action="store_true", help="lê também as linhas `Resumo do job` dos logs do CI leve e do CI (29.184; baixa até 40 logs)")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo) or not 1 <= a.dias <= 31:
        print("erro: --repo precisa ser dono/nome e --dias ficar entre 1 e 31", file=sys.stderr)
        return 1
    gh = gh or gh_real
    agora = agora or datetime.now(timezone.utc)
    try:
        por, lidos = coletar(a.repo, agora - timedelta(days=a.dias), gh)
        texto = relatorio(por, lidos, agora, a.dias, billing_legivel(a.repo, gh))
        if a.resumos:
            texto += relatorio_resumos(*resumos_da_semana(a.repo, agora - timedelta(days=a.dias), gh))
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    print(texto)
    if a.anexar:
        try:
            with a.anexar.open("a", encoding="utf-8", newline="\n") as f:
                f.write("\n" + texto)
        except OSError as e:
            print(f"erro: não foi possível anexar ao arquivo ({type(e).__name__})", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
