"""Frente GitHub (29.192): esteira do agente de nuvem do Copilot, por etiqueta, com teto e medida.

    python scripts/agente_nuvem.py atribuir N --repo dono/nome [--aplicar]
    python scripts/agente_nuvem.py medir --repo dono/nome [--custos custos.json]

`atribuir` entrega a issue N (gerada do pacote pelo `scripts/issue_do_pacote.py --agente-nuvem`) ao agente de nuvem do Copilot. É a
ÚNICA porta de atribuição do repositório e só roda quando a orquestradora escolhe o item: um comando, na sessão (token do próprio `gh`
da máquina; o token de workflow não atribui o Copilot). Recusa, com o motivo e código 1, se qualquer condição falhar:

  - a issue está ABERTA, tem a etiqueta `agente-nuvem` e a marca de pacote `<!-- pacote:ID -->` no corpo;
  - ninguém está atribuído (nada de tirar tarefa de gente) e o tamanho não é G;
  - o corpo e o título não têm formato de dado que não pode sair (e-mail, IPv4, serial, arroba, credencial, chave);
  - não há PR do agente aberto (uma tarefa por vez) e hoje (UTC) ainda não houve atribuição (teto de 1 por dia: orçamento do Copilot é
    do dono, ~31 créditos medidos por tarefa).

Sem `--aplicar` só mostra as conferências (ensaio). Com `--aplicar`: `gh issue edit --add-assignee @copilot`, confere pela leitura da
issue que o agente aparece como atribuído e deixa um comentário-marca com a data (é o que o teto diário conta).

`medir` é só leitura: por issue com a etiqueta, o PR do agente que a cita, se foi aceito (mesclado), recusado (fechado sem mesclar) ou
segue aberto, o tempo da atribuição ao PR, o tamanho do diff e os créditos (os reais de `--custos`, lidos na página de uso, ou a
estimativa por tarefa, sempre dita como estimativa). Não muda nenhuma configuração de conta, e não imprime nome de conta nem de repositório.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

ROTULO = "agente-nuvem"
ASSIGNEE = "@copilot"
TETO_POR_DIA = 1
TETO_SIMULTANEO = 1
CREDITOS_ESTIMADOS_POR_TAREFA = 31  # medido em 06/10 (github-medida-de-creditos.md): o piso, sem a revisão de código do agente
MARCA_ATRIBUICAO = "<!-- agente-nuvem:atribuida {data} -->"
_MARCA_RX = re.compile(r"<!-- agente-nuvem:atribuida (\d{4}-\d{2}-\d{2}) -->")
_PACOTE_RX = re.compile(r"<!-- pacote:((?:\d+|T)\.\d+) -->")
# login EXATO do agente (o `gh` mostra "Copilot"; a API, "copilot-swe-agent[bot]"): gente com "copilot" no nome não conta
_AGENTE_RX = re.compile(r"(?:app/)?copilot(?:-swe-agent)?(?:\[bot\])?", re.IGNORECASE)
Gh = Callable[..., str]


def _proibidos():
    """`proibidos()` do gerador de issues: uma só lista de formatos que não podem sair (e-mail, IPv4, serial, arroba, credencial)."""
    spec = importlib.util.spec_from_file_location("issue_do_pacote", Path(__file__).resolve().parent / "issue_do_pacote.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.proibidos


def gh_real(*args: str, entrada: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=entrada, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"o gh falhou (código {r.returncode})")  # a mensagem do gh traz dono/repo: não sai
    return r.stdout


def _agora(agora: datetime | None) -> datetime:
    return agora or datetime.now(timezone.utc)


def _hora(texto: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None


def _do_agente(autor: object) -> bool:
    login = autor.get("login") if isinstance(autor, dict) else autor
    return bool(_AGENTE_RX.fullmatch(str(login or "")))


def atribuicoes_de_hoje(issues: list[dict[str, object]], hoje: str) -> list[int]:
    """Números das issues da esteira que já têm o comentário-marca de HOJE (UTC)."""
    achadas = []
    for it in issues:
        for c in it.get("comments") or []:  # type: ignore[union-attr]
            m = _MARCA_RX.search(str(c.get("body") or "")) if isinstance(c, dict) else None
            if m and m.group(1) == hoje:
                achadas.append(int(it["number"]))
                break
    return achadas


def conferir(issue: dict[str, object], esteira: list[dict[str, object]], prs_abertos_do_agente: int, hoje: str) -> list[str]:
    """Motivos de recusa (lista vazia = pode atribuir)."""
    motivos: list[str] = []
    rotulos = {str(r.get("name")) for r in issue.get("labels") or []}  # type: ignore[union-attr]
    corpo = str(issue.get("body") or "")
    if str(issue.get("state", "")).upper() != "OPEN":
        motivos.append("a issue não está aberta")
    if ROTULO not in rotulos:
        motivos.append(f"falta a etiqueta {ROTULO} (quem libera o item é a orquestradora)")
    if not _PACOTE_RX.search(corpo):
        motivos.append("o corpo não tem a marca do pacote (<!-- pacote:ID -->): só issue gerada por scripts/issue_do_pacote.py")
    if issue.get("assignees"):
        motivos.append("a issue já tem alguém atribuído")
    if "tamanho:G" in rotulos:
        motivos.append("tamanho G: quebre o item antes de dar a um agente")
    achados = _proibidos()(str(issue.get("title") or "") + "\n" + corpo)
    if achados:
        motivos.append("o texto da issue tem formato proibido (" + ", ".join(achados) + ")")
    if prs_abertos_do_agente >= TETO_SIMULTANEO:
        motivos.append(f"já há {prs_abertos_do_agente} PR(s) do agente abertos: uma tarefa por vez")
    hoje_feitas = atribuicoes_de_hoje(esteira, hoje)
    if len(hoje_feitas) >= TETO_POR_DIA:
        motivos.append(f"teto diário de {TETO_POR_DIA} atribuição(ões) já usado hoje (issue {hoje_feitas[0]})")
    return motivos


def _issue(gh: Gh, repo: str, n: int) -> dict[str, object]:
    return json.loads(gh("issue", "view", str(n), "--repo", repo, "--json", "number,title,body,state,labels,assignees,comments"))


def _esteira(gh: Gh, repo: str) -> list[dict[str, object]]:
    return json.loads(gh("issue", "list", "--repo", repo, "--label", ROTULO, "--state", "all", "--limit", "200",
                         "--json", "number,title,body,state,createdAt,closedAt,assignees,comments"))


def _prs(gh: Gh, repo: str, estado: str = "all") -> list[dict[str, object]]:
    return json.loads(gh("pr", "list", "--repo", repo, "--state", estado, "--limit", "200",
                         "--json", "number,body,state,isDraft,mergedAt,closedAt,createdAt,additions,deletions,changedFiles,author"))


def atribuir(repo: str, n: int, *, aplicar: bool, gh: Gh | None = None, agora: datetime | None = None) -> tuple[str, list[str]]:
    """Devolve ('ensaio'|'atribuida'|'recusada', motivos). Erro do gh propaga como RuntimeError."""
    gh = gh or gh_real
    hoje = _agora(agora).strftime("%Y-%m-%d")
    issue = _issue(gh, repo, n)
    esteira = _esteira(gh, repo)
    prs_abertos = sum(1 for p in _prs(gh, repo, "open") if str(p.get("state")).upper() == "OPEN" and _do_agente(p.get("author")))
    # issue já entregue ao agente e ainda aberta conta como tarefa em andamento mesmo sem PR (o agente demora a abrir o PR)
    em_andamento = sum(1 for it in esteira if str(it.get("state")).upper() == "OPEN" and any(_do_agente(a) for a in it.get("assignees") or []))  # type: ignore[union-attr]
    motivos = conferir(issue, esteira, max(prs_abertos, em_andamento), hoje)
    if motivos:
        return "recusada", motivos
    if not aplicar:
        return "ensaio", []
    # a marca (o que o teto diário conta) sai ANTES da atribuição: se a atribuição ou a conferência falhar, o teto já está gasto
    gh("issue", "comment", str(n), "--repo", repo, "--body-file", "-",
       entrada=MARCA_ATRIBUICAO.format(data=hoje) + "\nAtribuição ao agente de nuvem pedida por `scripts/agente_nuvem.py` (29.192).")
    gh("issue", "edit", str(n), "--repo", repo, "--add-assignee", ASSIGNEE)
    depois = _issue(gh, repo, n)
    if not any(_do_agente(a) for a in depois.get("assignees") or []):  # type: ignore[union-attr]
        raise RuntimeError("não consegui confirmar o agente como atribuído na leitura da issue (a marca do dia já foi gravada)")
    return "atribuida", []


def pr_da_issue(n: int, prs: list[dict[str, object]]) -> dict[str, object] | None:
    """O PR do agente que cita `#n` no corpo (o mais novo)."""
    rx = re.compile(rf"(?<![\w/])#{n}\b")
    achados = [p for p in prs if _do_agente(p.get("author")) and rx.search(str(p.get("body") or ""))]
    return max(achados, key=lambda p: str(p.get("createdAt"))) if achados else None


def _estado(pr: dict[str, object] | None) -> str:
    if pr is None:
        return "sem PR"
    if pr.get("mergedAt"):
        return "aceito"
    if str(pr.get("state")).upper() == "CLOSED":
        return "recusado"
    return "em andamento" + (" (rascunho)" if pr.get("isDraft") else "")


def medir(repo: str, *, custos: dict[str, float] | None = None, gh: Gh | None = None) -> str:
    gh = gh or gh_real
    esteira = sorted(_esteira(gh, repo), key=lambda i: int(i["number"]))
    prs = _prs(gh, repo)
    linhas = ["## Esteira do agente de nuvem (29.192)", "",
              "| issue | item | estado | PR | atribuição até o PR (min) | diff | créditos |", "|---|---|---|---|---|---|---|"]
    aceitos = recusados = abertos = 0
    creditos = 0.0
    reais = True
    for it in esteira:
        n = int(it["number"])
        pr = pr_da_issue(n, prs)
        estado = _estado(pr)
        aceitos += estado == "aceito"
        recusados += estado == "recusado"
        abertos += estado.startswith("em andamento")
        achou = _PACOTE_RX.search(str(it.get("body") or ""))
        item = achou.group(1) if achou else "-"
        marca_de = next((c for c in it.get("comments") or [] if isinstance(c, dict) and _MARCA_RX.search(str(c.get("body") or ""))), None)  # type: ignore[union-attr]
        t0, t1 = _hora(marca_de.get("createdAt")) if marca_de else None, _hora(pr.get("createdAt")) if pr else None
        minutos = str(max(0, round((t1 - t0).total_seconds() / 60))) if t0 and t1 else "-"
        diff = f"{pr.get('changedFiles')} arq. +{pr.get('additions')}/-{pr.get('deletions')}" if pr else "-"
        if custos and str(n) in custos:
            c = float(custos[str(n)])
        else:
            c, reais = float(CREDITOS_ESTIMADOS_POR_TAREFA), False
        creditos += c
        linhas.append(f"| #{n} | {item} | {estado} | {('#' + str(pr['number'])) if pr else '-'} | {minutos} | {diff} | {c:g}{'' if custos and str(n) in custos else ' (est.)'} |")
    total = len(esteira)
    fechados = aceitos + recusados
    linhas += ["", f"- Issues da esteira: {total}; aceitas: {aceitos}; recusadas: {recusados}; em andamento: {abertos}; sem PR: {total - fechados - abertos}.",
               f"- Taxa de aceitação (entre as decididas): {aceitos}/{fechados}" + (f" = {100 * aceitos // fechados} %" if fechados else " (nenhuma decidida)"),
               f"- Créditos {'lidos na página de uso' if reais else 'ESTIMADOS (31 por tarefa; o real está na página de uso da conta)'}: {creditos:g}"
               + (f"; por item aceito: {creditos / aceitos:.0f}" if aceitos else "; por item aceito: n/a (nenhum aceito)"),
               "- Sem nome de conta nem de repositório neste texto; nada aqui muda configuração de conta."]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Esteira do agente de nuvem: atribuir (com teto) e medir.")
    sub = ap.add_subparsers(dest="acao", required=True)
    pa = sub.add_parser("atribuir")
    pa.add_argument("issue", type=int)
    pa.add_argument("--repo", required=True)
    pa.add_argument("--aplicar", action="store_true", help="atribui de verdade (padrão: ensaio com as conferências)")
    pm = sub.add_parser("medir")
    pm.add_argument("--repo", required=True)
    pm.add_argument("--custos", type=Path, help='JSON {"<n da issue>": créditos lidos na página de uso}')
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("erro: --repo fora do formato dono/nome", file=sys.stderr)
        return 1
    try:
        if a.acao == "atribuir":
            resultado, motivos = atribuir(a.repo, a.issue, aplicar=a.aplicar, gh=gh, agora=agora)
            if resultado == "recusada":
                print("RECUSADA:", *motivos, sep="\n  - ")
                return 1
            print("ensaio: pode atribuir (rode de novo com --aplicar)" if resultado == "ensaio" else f"atribuída a issue {a.issue}")
            return 0
        custos = json.loads(a.custos.read_text(encoding="utf-8")) if a.custos else None
        print(medir(a.repo, custos=custos, gh=gh))
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
