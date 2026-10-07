"""Frente GitHub (29.202): custo por PR do que o GitHub cobra do dono (Copilot) e do que ele limita (Codex), só LEITURA.

    python scripts/custo_por_pr.py --repo dono/nome [--dias 7] [--sem-codex] [--anexar arquivo.md]

Para cada PR criado na janela mostra o tipo (`revisão`, `agente` ou `sessão`), as execuções de Actions ligadas a ele (contagem e
minutos aproximados por duração da execução), as revisões do Copilot (créditos ESTIMADOS: 146 por revisão) e as tarefas do agente
de nuvem (31 por tarefa), e quantas revisões do Codex o PR recebeu. O fim do quadro traz o pico de PRs de revisão em 5 horas
(o limite do plano do Codex, que é do dono, estourou em 07/10 com 22 PRs em 1 h 35 min) contra a regra de 20 por janela.

É estimativa, não saldo: o saldo real de créditos só existe na página de uso da conta, e o Codex não tem API de uso (só se conta
revisão). Nunca imprime título de PR, nome de branch, conta nem repositório: só número, tipo e contagens. Não escreve nada no
GitHub; `--anexar` acrescenta ao arquivo local (nunca sobrescreve).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

CREDITOS_POR_REVISAO = 146
CREDITOS_POR_TAREFA_DO_AGENTE = 31
POR_JANELA_CODEX = 20
JANELA_CODEX_H = 5
MAX_PRS = 150
CODEX = "chatgpt-codex-connector[bot]"
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
    achados: list[dict] = []
    decoder = json.JSONDecoder()
    i = 0
    while i < len(texto):
        valor, i = decoder.raw_decode(texto, i)
        achados.extend(valor if isinstance(valor, list) else [valor])
        while i < len(texto) and texto[i].isspace():
            i += 1
    return achados


def _hora(valor: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


def tipo_do_pr(titulo: str, branch: str) -> str:
    if titulo.startswith("[revisão] "):
        return "revisão"
    return "agente" if branch.startswith("copilot/") else "sessão"


def tipo_da_execucao(nome: str) -> str:
    n = nome.lower()
    if "copilot code review" in n:
        return "revisao_copilot"
    if "copilot cloud agent" in n or "copilot coding agent" in n:
        return "tarefa_agente"
    return "outra"


def minutos(run: dict) -> int:
    ini, fim = _hora(run.get("run_started_at")), _hora(run.get("updated_at"))
    if ini is None or fim is None or fim <= ini:
        return 0
    return max(1, round((fim - ini).total_seconds() / 60))


def pico_por_janela(horas: list[datetime], janela_h: int = JANELA_CODEX_H) -> int:
    """Maior número de eventos em qualquer janela deslizante de `janela_h` horas."""
    horas = sorted(horas)
    melhor, ini = 0, 0
    for fim, h in enumerate(horas):
        while h - horas[ini] >= timedelta(hours=janela_h):
            ini += 1
        melhor = max(melhor, fim - ini + 1)
    return melhor


def _prs_da_execucao(run: dict, por_branch: dict[str, list[tuple[datetime, int]]]) -> list[int]:
    """PRs a que a execução pertence. A revisão do Copilot e o agente de nuvem rodam como `dynamic` e vêm com `pull_requests` vazio:
    nesse caso o elo é a branch (head_branch) e vale o PR mais recente criado antes da execução; se nenhum PR da janela é anterior a ela,
    a execução é de um PR antigo (ou de push sem PR) e NÃO é atribuída a ninguém."""
    diretos = [int(n) for n in run.get("pull_requests") or []]
    if diretos:
        return diretos
    candidatos = sorted(por_branch.get(str(run.get("head_branch") or ""), []), key=lambda x: x[0])
    if not candidatos:
        return []
    inicio = _hora(run.get("run_started_at"))
    antes = [n for h, n in candidatos if inicio is not None and h <= inicio]
    return [antes[-1]] if antes else []


def coletar(repo: str, desde: datetime, com_codex: bool, gh: Gh) -> dict:
    prs = json.loads(gh("pr", "list", "--repo", repo, "--state", "all", "--limit", str(MAX_PRS), "--json", "number,title,headRefName,createdAt"))
    truncado = len(prs) >= MAX_PRS
    prs = [p for p in prs if (c := _hora(p.get("createdAt"))) is not None and c >= desde]
    linhas: dict[int, dict] = {}
    por_branch: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for p in prs:
        n = int(p["number"])
        por_branch[str(p.get("headRefName", ""))].append((_hora(p["createdAt"]), n))
        linhas[n] = {"pr": n, "tipo": tipo_do_pr(str(p.get("title", "")), str(p.get("headRefName", ""))), "criado": _hora(p["createdAt"]),
                     "execucoes": 0, "min": 0, "revisoes_copilot": 0, "tarefas_agente": 0, "codex": None}
    runs = _itens(gh("api", "--paginate", f"repos/{repo}/actions/runs?per_page=100&created=%3E%3D{desde.strftime('%Y-%m-%d')}",
                     "--jq", ".workflow_runs[] | {name, head_branch, run_started_at, updated_at, pull_requests: [.pull_requests[].number]}"))
    for r in runs:
        for n in _prs_da_execucao(r, por_branch):
            linha = linhas.get(int(n))
            if linha is None:
                continue
            linha["execucoes"] += 1
            linha["min"] += minutos(r)
            t = tipo_da_execucao(str(r.get("name", "")))
            linha["revisoes_copilot"] += t == "revisao_copilot"
            linha["tarefas_agente"] += t == "tarefa_agente"
    if com_codex:
        for n, linha in linhas.items():
            revs = _itens(gh("api", "--paginate", f"repos/{repo}/pulls/{n}/reviews", "--jq", ".[] | {login: .user.login}"))
            linha["codex"] = sum(1 for x in revs if str(x.get("login", "")).lower() == CODEX)
    if truncado:
        print(f"aviso: {MAX_PRS} PRs lidos; a janela pode estar truncada (use --dias menor)", file=sys.stderr)
    return linhas


def relatorio(linhas: dict[int, dict], agora: datetime, dias: int, com_codex: bool) -> str:
    rows = sorted(linhas.values(), key=lambda x: x["pr"])
    tab = ["| PR | tipo | execuções | min aprox. | revisões Copilot (créditos est.) | tarefas do agente (créditos est.) | revisões Codex |", "|---|---|---|---|---|---|---|"]
    for x in rows:
        cred_r, cred_t = x["revisoes_copilot"] * CREDITOS_POR_REVISAO, x["tarefas_agente"] * CREDITOS_POR_TAREFA_DO_AGENTE
        tab.append(f"| {x['pr']} | {x['tipo']} | {x['execucoes']} | {x['min']} | {x['revisoes_copilot']} ({cred_r}) | {x['tarefas_agente']} ({cred_t}) | "
                   f"{x['codex'] if x['codex'] is not None else '-'} |")
    por_tipo: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for x in rows:
        t = por_tipo[x["tipo"]]
        t["prs"] += 1
        t["min"] += x["min"]
        t["creditos"] += x["revisoes_copilot"] * CREDITOS_POR_REVISAO + x["tarefas_agente"] * CREDITOS_POR_TAREFA_DO_AGENTE
        t["codex"] += x["codex"] or 0
    resumo = [f"- {tipo}: {t['prs']} PR(s), ~{t['min']} min de Actions, ~{t['creditos']} créditos estimados do Copilot, {t['codex']} revisão(ões) do Codex"
              for tipo, t in sorted(por_tipo.items())]
    revisao = [x["criado"] for x in rows if x["tipo"] == "revisão" and x["criado"]]
    pico = pico_por_janela(revisao)
    aviso = "ACIMA da regra" if pico > POR_JANELA_CODEX else "dentro da regra"
    total = sum(t["creditos"] for t in por_tipo.values())
    return "\n".join([
        f"## Custo por PR, últimos {dias} dia(s) até {agora.strftime('%Y-%m-%d %H:%MZ')} (29.202)", "",
        f"- PRs criados na janela: {len(rows)}; créditos do Copilot ESTIMADOS: ~{total} (146 por revisão, 31 por tarefa do agente); saldo real só na página de uso da conta.",
        f"- Pico de PRs de revisão em {JANELA_CODEX_H} h: {pico} (regra: até {POR_JANELA_CODEX} por janela, 4 por hora): {aviso}.",
        "- Codex: não há API de uso; conta-se revisão recebida por PR (" + ("lida" if com_codex else "não lida, --sem-codex") + "). O limite do plano é do dono.",
        *resumo, "", *tab, "",
        "Estimativa e contagem; nunca ordem. Execução ligada a vários PRs conta em cada um (os totais por tipo podem somar a mais); execução sem PR "
        "da janela não entra. Nada foi escrito no GitHub.", ""])


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Custo por PR (Copilot estimado e Codex contado), só leitura.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--sem-codex", action="store_true", help="não lê as revisões do Codex (poupa uma chamada por PR)")
    ap.add_argument("--anexar", type=Path, help="acrescenta o relatório a este arquivo (nunca sobrescreve)")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not _REPO.fullmatch(a.repo) or not 1 <= a.dias <= 60:
        print("erro: --repo precisa ser dono/nome e --dias de 1 a 60", file=sys.stderr)
        return 1
    agora = agora or datetime.now(timezone.utc)
    try:
        texto = relatorio(coletar(a.repo, agora - timedelta(days=a.dias), not a.sem_codex, gh or gh_real), agora, a.dias, not a.sem_codex)
        print(texto)
        if a.anexar:
            with a.anexar.open("a", encoding="utf-8", newline="\n") as f:
                f.write("\n" + texto)
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
