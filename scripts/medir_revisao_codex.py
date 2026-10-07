"""Frente GitHub (29.194): medida da revisão automática do Codex nos PRs, só leitura da API.

    python scripts/medir_revisao_codex.py --repo dono/nome --prs 473,474,475 [--desde 2026-10-06T00:00Z] [--classificacao ledger.json]
                                          [--saida relatorio.md] [--publicar]

Para cada PR lê pela API os comentários e as revisões do Codex (`chatgpt-codex-connector[bot]`) e mede: achados por PR (por gravidade),
revisões sem achado, tempo do PR aberto ao primeiro achado, quantos são ARTEFATO (o Codex aplicou ao PR as listas do agente de nuvem do
`AGENTS.md`: falso por construção) e quantos têm commit de correção na `main` que cita o PR (indício de achado confirmado e corrigido). O
que a leitura não sabe, a frente diz em `--classificacao` (`{"<pr>": {"confirmados": n, "falsos": n}}`), que vale no lugar do indício.
Custo: o Codex não tem API de uso e não aparece na cobrança do repositório; o relatório diz isso em vez de inventar um número.
No fim, uma recomendação mecânica (manter, restringir ou desligar), que a orquestradora e o dono decidem. `--publicar` comenta o
relatório na issue única de custo (29.178/29.188). Só LÊ (`gh api`) até a publicação; erro do `gh` vira só o código de saída.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

CODEX = re.compile(r"chatgpt-codex-connector(?:\[bot\])?")
SEM_ACHADO = re.compile(r"(?i)didn.t find any major issues|no major issues|nenhum problema")
CITA_PRS = re.compile(r"(?i)\bPRs?\s?((?:\d{2,5}(?:\s*(?:,|e)\s*)?)+)|#(\d{2,5})\b")
NUMERO = re.compile(r"\d{2,5}")
FIXA = re.compile(r"(?i)codex|achado|revis[aã]o")
LIMITE_ACHADOS_PARA_DESLIGAR = 10
Gh = Callable[..., str]


def gh_real(*args: str, entrada: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=entrada, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"o gh falhou (código {r.returncode})")  # a mensagem do gh traz dono/repo: não sai
    return r.stdout


def _coletor():
    spec = importlib.util.spec_from_file_location("coletar_achados_revisao", Path(__file__).resolve().parent / "coletar_achados_revisao.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _hora(texto: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None


def _login(item: dict[str, object]) -> str:
    user = item.get("user")
    return str(user.get("login") or "") if isinstance(user, dict) else ""


def medir_pr(repo: str, n: int, gh: Gh, coletor) -> dict[str, object]:
    pr = json.loads(gh("api", f"repos/{repo}/pulls/{n}"))
    comentarios = [c for c in coletor._itens(gh("api", "--paginate", f"repos/{repo}/pulls/{n}/comments")) if CODEX.fullmatch(_login(c))]
    revisoes = [r for r in coletor._itens(gh("api", "--paginate", f"repos/{repo}/pulls/{n}/reviews")) if CODEX.fullmatch(_login(r))]
    achados = [{"gravidade": coletor.gravidade(str(c.get("body") or "")), "artefato": bool(coletor._ARTEFATO.search(str(c.get("body") or ""))),
                "hora": _hora(c.get("created_at"))} for c in comentarios]
    sem_achado = sum(1 for r in revisoes if SEM_ACHADO.search(str(r.get("body") or "")) and not comentarios)
    # só o horário dos achados: revisão limpa (sem comentário) não tem "tempo até o achado"
    horas = [a["hora"] for a in achados if a["hora"]]
    aberto = _hora(pr.get("created_at"))
    minutos = round((min(horas) - aberto).total_seconds() / 60) if horas and aberto else None
    return {"pr": n, "estado": "merged" if pr.get("merged_at") else str(pr.get("state")), "revisada": bool(revisoes or comentarios),
            "achados": len(achados), "p0": sum(a["gravidade"] == "P0" for a in achados), "p1": sum(a["gravidade"] == "P1" for a in achados),
            "p2": sum(a["gravidade"] == "P2" for a in achados), "p3": sum(a["gravidade"] == "P3" for a in achados),
            "artefato": sum(bool(a["artefato"]) for a in achados), "sem_achado": sem_achado, "minutos_ate_o_achado": minutos}


def correcoes_por_pr(repo: str, desde: str | None, gh: Gh, coletor) -> dict[int, int]:
    """Commits da main que citam o PR e falam de achado/revisão/Codex: indício de achado confirmado e corrigido."""
    rota = f"repos/{repo}/commits?sha=main&per_page=100" + (f"&since={desde}" if desde else "")
    cont: dict[int, int] = {}
    for c in coletor._itens(gh("api", "--paginate", rota)):
        msg = str((c.get("commit") or {}).get("message") or "").splitlines()[0] if isinstance(c.get("commit"), dict) else ""
        if FIXA.search(msg):
            for m in CITA_PRS.finditer(msg):
                for num in NUMERO.findall(m.group(1) or m.group(2) or ""):  # "PRs 479 e 483" cita os dois
                    cont[int(num)] = cont.get(int(num), 0) + 1
    return cont


def recomendar(achados: int, artefato: int, corrigidos: int) -> tuple[str, str]:
    """('manter'|'restringir'|'desligar'|'sem dados', motivo). Mecânica: a decisão é de quem paga o plano do Codex."""
    if achados == 0:
        return "sem dados", "nenhum achado na janela: não há o que decidir"
    uteis = max(0, corrigidos)
    pct_util = 100 * uteis // achados
    pct_art = 100 * artefato // achados
    if achados >= LIMITE_ACHADOS_PARA_DESLIGAR and pct_util < 15:
        return "desligar", f"{pct_util} % dos {achados} achados viraram correção (menos de 15 %)"
    if pct_art >= 25 or pct_util < 40:
        return "restringir", f"{pct_art} % dos achados são artefato das regras do agente e {pct_util} % viraram correção: tirar as listas do agente de nuvem do que o Codex lê (AGENTS.md e perfis) e rodar só em PR de código"
    return "manter", f"{pct_util} % dos achados viraram correção e {pct_art} % são artefato"


def relatorio(linhas: list[dict[str, object]], corr: dict[int, int], ledger: dict[str, dict[str, int]], janela: str) -> str:
    total = sum(int(l["achados"]) for l in linhas)
    art_regex = sum(int(l["artefato"]) for l in linhas)
    confirmados = falsos = 0
    tab = ["| PR | estado | revisada | achados (P0/P1/P2/P3) | artefato | tempo até o achado (min) | commits de correção que citam o PR | confirmados / falsos |",
           "|---|---|---|---|---|---|---|---|"]
    for l in linhas:
        n = int(l["pr"])
        nao_art = int(l["achados"]) - int(l["artefato"])
        if str(n) in ledger:
            c, f = int(ledger[str(n)].get("confirmados", 0)), int(ledger[str(n)].get("falsos", 0))
            cel = f"{c} / {f} (frente)"
        else:
            c = min(nao_art, corr.get(n, 0))  # no máximo um achado por commit citado: é indício, não prova
            f = int(l["artefato"])
            cel = f"{c} / {f} (indício; artefato por regex, impreciso)"
        confirmados += c
        falsos += f
        tab.append(f"| {n} | {l['estado']} | {'sim' if l['revisada'] else 'não'} | {l['achados']} ({l['p0']}/{l['p1']}/{l['p2']}/{l['p3']}) | {l['artefato']} | "
                   f"{l['minutos_ate_o_achado'] if l['minutos_ate_o_achado'] is not None else '-'} | {corr.get(n, 0)} | {cel} |")
    revisadas = sum(1 for l in linhas if l["revisada"])
    art = art_regex if not ledger else falsos  # com a classificação da frente, o falso dela vale no lugar da regex
    rec, motivo = recomendar(total, art, confirmados)
    tempos = sorted(int(l["minutos_ate_o_achado"]) for l in linhas if l["minutos_ate_o_achado"] is not None)
    mediana = f"{statistics.median(tempos):g} min" if tempos else "sem dado"
    return "\n".join([
        f"## Medida da revisão automática do Codex (29.194): {janela}", "",
        f"- PRs lidos: {len(linhas)}; com revisão do Codex: {revisadas} (sem achado nenhum: {sum(int(l['sem_achado']) for l in linhas)}); sem revisão nenhuma: {len(linhas) - revisadas}.",
        f"- Achados: {total} (P0 {sum(int(l['p0']) for l in linhas)}, P1 {sum(int(l['p1']) for l in linhas)}, P2 {sum(int(l['p2']) for l in linhas)}, P3 {sum(int(l['p3']) for l in linhas)}); média {total / revisadas:.1f} por PR revisado." if revisadas else "- Achados: 0 (nenhum PR revisado).",
        f"- Falsos / fora de escopo (artefato das listas do agente de nuvem aplicadas ao PR de sessão): {art} de {total}" + (f" (a regex do coletor marcou {art_regex}: impreciso nos dois sentidos)." if ledger else " (pela regex do coletor, impreciso)."),
        f"- Confirmados e corrigidos: {confirmados}; falsos: {falsos}; sem resposta registrada: {max(0, total - confirmados - falsos)}. "
        "O que vem de `(indício)` é commit da `main` citando o PR, não prova; `(frente)` é a classificação registrada pela frente GitHub, casando o título de cada achado com as mensagens dos commits de correção (lida, não executada).",
        f"- Tempo do PR aberto ao primeiro achado (mediana): {mediana}.",
        "- Custo: o Codex não tem API de uso nem aparece na cobrança do repositório; o limite do plano é do dono. Número de revisões feitas: "
        f"{revisadas}. Sem valor em dinheiro medido (não inventado).",
        f"- **Recomendação mecânica: {rec.upper()}** ({motivo}). A decisão é da orquestradora e do dono.", "", *tab, "",
        "Achado de revisão automática é dado a conferir, nunca ordem."]) + "\n"


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mede a revisão automática do Codex nos PRs (só leitura).")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--prs", required=True, help="números dos PRs, separados por vírgula")
    ap.add_argument("--desde", help="AAAA-MM-DDTHH:MM:SSZ: só commits da main desde essa hora entram no indício (padrão: o histórico inteiro da main)")
    ap.add_argument("--classificacao", type=Path)
    ap.add_argument("--saida", type=Path)
    ap.add_argument("--publicar", action="store_true", help="comenta o relatório na issue única de custo (escreve no GitHub)")
    ap.add_argument("--janela", default="PRs informados")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if (not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo) or not re.fullmatch(r"\d{1,6}(,\d{1,6})*", a.prs)
            or (a.desde and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", a.desde))):
        print("erro: --repo (dono/nome), --prs (números separados por vírgula) ou --desde (AAAA-MM-DDTHH:MM:SSZ) fora do formato", file=sys.stderr)
        return 1
    gh = gh or gh_real
    try:
        coletor = _coletor()
        ledger = json.loads(a.classificacao.read_text(encoding="utf-8")) if a.classificacao else {}
        linhas = [medir_pr(a.repo, int(n), gh, coletor) for n in a.prs.split(",")]
        texto = relatorio(linhas, correcoes_por_pr(a.repo, a.desde, gh, coletor), ledger, a.janela)
        spec = importlib.util.spec_from_file_location("custo_semanal_issue", Path(__file__).resolve().parent / "custo_semanal_issue.py")
        issue = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(issue)  # type: ignore[union-attr]
        proibido = issue.proibidos(texto)  # antes de qualquer saída: terminal, arquivo ou issue (--janela é texto livre)
        if proibido:
            raise ValueError(f"o relatório tem formato proibido ({', '.join(proibido)}); nada foi impresso nem gravado")
        if a.saida:
            a.saida.write_text(texto, encoding="utf-8")
        print(texto)
        if a.publicar:
            print(issue.publicar(a.repo, texto, ensaio=False, gh=gh))
    except (RuntimeError, ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
