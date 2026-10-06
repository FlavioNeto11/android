"""Frente GitHub (29.170): junta num quadro só os achados das revisões automáticas de PR (Codex e Copilot), só LEITURA.

`python scripts/coletar_achados_revisao.py --repo dono/nome` lê, pela API do `gh`, os PRs (abertos e fechados) atualizados nas
últimas `--horas` horas e, de cada um, os comentários de revisão em linha e os resumos de revisão escritos por um revisor
automático (login com `codex` ou `copilot`). Imprime uma tabela em Markdown: PR, revisor, gravidade (P0 a P3, quando o texto traz
a marca), arquivo:linha e a primeira frase do achado. Todo achado é "a conferir": nunca ordem, e quem decide é a orquestradora.

Não escreve nada: não comenta, não cria issue, não dispara revisão (cada revisão do Copilot gasta créditos do dono). O texto sai
encurtado e mascarado por FORMATO (e-mail, IPv4, sequência longa de letras e dígitos), porque um revisor pode citar um trecho do
diff; ainda assim o quadro vai só para o terminal ou para `.claude/handoffs`, fora do Git.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

REVISORES = frozenset({"chatgpt-codex-connector[bot]", "copilot-pull-request-reviewer[bot]", "copilot"})
LIMITE_PRS = 100
RESUMO_MAX = 180
Gh = Callable[..., str]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_CREDENCIAL = re.compile(r"(?i)\b(authorization|bearer|senha|password|passwd|token|secret|api[_-]?key)\b\s*[:=]?\s*(?:bearer\s+)?\S+")
_LONGO = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_GRAVIDADE = re.compile(r"\bP([0-3])\b")
_IMAGEM = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_COMENTARIO_HTML = re.compile(r"<!--.*?(?:-->|$)")


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {mascarar((r.stderr or r.stdout).strip()[:300])}")
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


def eh_revisor(login: object) -> bool:
    return isinstance(login, str) and login.lower() in REVISORES


def mascarar(texto: str) -> str:
    for rx, troca in ((_CREDENCIAL, "[credencial omitida]"), (_EMAIL, "[e-mail omitido]"), (_IPV4, "[ip omitido]"), (_LONGO, "[sequencia omitida]")):
        texto = rx.sub(troca, texto)
    return texto


def resumo(corpo: str) -> str:
    """Primeira linha com texto, sem imagem/link/marcação, mascarada e encurtada."""
    for linha in corpo.splitlines():
        limpa = _LINK.sub(r"\1", _IMAGEM.sub("", _COMENTARIO_HTML.sub("", linha))).replace("*", "").replace("`", "'").replace("|", "/").strip(" #>-\t")
        if limpa:
            limpa = mascarar(limpa)
            return limpa if len(limpa) <= RESUMO_MAX else limpa[: RESUMO_MAX - 1] + "…"
    return "(sem texto)"


def gravidade(corpo: str) -> str:
    m = _GRAVIDADE.search(corpo)
    return f"P{m.group(1)}" if m else "-"


def achados_do_pr(repo: str, numero: int, gh: Gh) -> list[dict[str, str]]:
    achados: list[dict[str, str]] = []
    for c in _itens(gh("api", "--paginate", f"repos/{repo}/pulls/{numero}/comments")):
        user = c.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        if not eh_revisor(login):
            continue
        corpo = str(c.get("body") or "")
        linha = c.get("line") or c.get("original_line")
        achados.append({"pr": str(numero), "revisor": mascarar(str(login)), "gravidade": gravidade(corpo),
                        "onde": mascarar(f"{c.get('path', '?')}:{linha}" if linha else str(c.get("path", "?"))), "resumo": resumo(corpo)})
    for r in _itens(gh("api", "--paginate", f"repos/{repo}/pulls/{numero}/reviews")):
        user = r.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        corpo = str(r.get("body") or "").strip()
        if eh_revisor(login) and corpo:
            achados.append({"pr": str(numero), "revisor": mascarar(str(login)), "gravidade": gravidade(corpo),
                            "onde": "(resumo da revisão)", "resumo": resumo(corpo)})
    return achados


def coletar(repo: str, horas: int, agora: datetime, gh: Gh, prs: list[int] | None = None) -> list[dict[str, str]]:
    if prs is None:
        corte = agora - timedelta(hours=horas)
        lista = json.loads(gh("pr", "list", "--repo", repo, "--state", "all", "--limit", str(LIMITE_PRS), "--json", "number,updatedAt"))
        if len(lista) >= LIMITE_PRS:
            print(f"aviso: {LIMITE_PRS} PRs lidos; a janela pode estar truncada (use --prs)", file=sys.stderr)
        prs = sorted(p["number"] for p in lista if datetime.fromisoformat(str(p["updatedAt"]).replace("Z", "+00:00")) >= corte)
    achados: list[dict[str, str]] = []
    for n in prs:
        achados.extend(achados_do_pr(repo, n, gh))
    return achados


def tabela(achados: list[dict[str, str]]) -> str:
    if not achados:
        return "Nenhum achado de revisão automática na janela."
    linhas = ["Achados de revisão automática: A CONFERIR, nunca ordem.", "",
              "| PR | revisor | grav. | onde | achado |", "|---|---|---|---|---|"]
    ordem = sorted(achados, key=lambda a: (a["gravidade"] == "-", a["gravidade"], int(a["pr"])))
    linhas += [f"| {a['pr']} | {a['revisor']} | {a['gravidade']} | {a['onde']} | {a['resumo']} |" for a in ordem]
    return "\n".join(linhas)


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Junta os achados das revisões automáticas de PR (só leitura).")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--horas", type=int, default=48, help="janela de PRs atualizados (padrão 48)")
    ap.add_argument("--prs", help="números separados por vírgula, em vez da janela")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("erro: --repo precisa ser dono/nome", file=sys.stderr)
        return 1
    if a.prs is not None and not re.fullmatch(r"\d+(,\d+)*", a.prs):
        print("erro: --prs precisa ser números separados por vírgula", file=sys.stderr)
        return 1
    try:
        achados = coletar(a.repo, a.horas, agora or datetime.now(timezone.utc), gh or gh_real,
                          [int(x) for x in a.prs.split(",")] if a.prs else None)
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    print(json.dumps(achados, ensure_ascii=False, indent=2) if a.json else tabela(achados))
    return 0


if __name__ == "__main__":
    sys.exit(main())
