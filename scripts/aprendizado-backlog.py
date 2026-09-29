"""O backlog "o que mais falha" para a sessão de desenvolvimento (ADR-054, decisão 7; pacote A3).

O que ele faz: pede ao central `GET /api/aprendizado/falhas?formato=md`, grava o md em
`data/aprendizado/backlog-AAAA-MM-DD.md` (fora do Git) e imprime o topo (a skill `retomar` mostra o top 5). Nada
mais: só GET, nenhuma IA, nada gravado no banco. O relatório é MEDIDA — custo × frequência, a camada e onde alterar,
e a prova sugerida; a chave `fk-*` de cada linha é a mesma do `PATCH /api/aprendizado/backlog/{id}`.

`--retroativo` inclui o legado: as tentativas antigas sem `failure_kind` gravado, classificadas NA LEITURA pelo mesmo
classificador e marcadas como retroativas (nunca gravadas). Sem ele, só o que a execução classificou ao gravar.

Uso:  python scripts/aprendizado-backlog.py [--retroativo] [--dias 14] [--app PACOTE] [--camada CAMADA]
                                           [--limite 20] [--top 5] [--simulados] [--base URL] [--saida PASTA]

O token da API (quando houver) vem de `API_TOKEN` no ambiente e NUNCA é impresso — nem na saída, nem no erro.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_PADRAO = "http://127.0.0.1:8000"
ROTA = "/api/aprendizado/falhas"
#: O único método que este script usa: ler. Um teste confere.
METODO = "GET"
SAIDA_PADRAO = ROOT / "data" / "aprendizado"

#: Separador de coluna da tabela em Markdown: o `|` que o relatório escapou (`\|`) é texto, não coluna.
_COLUNA = re.compile(r"(?<!\\)\|")
#: (endereço, cabeçalhos) → corpo. Injetável: os testes não abrem rede.
Buscador = Callable[[str, dict[str, str]], str]


def url(base: str, *, dias: int, retroativo: bool, limite: int, app: str | None = None,
        camada: str | None = None, simulados: bool = False) -> str:
    params: dict[str, str | int] = {"dias": dias, "formato": "md", "retroativo": int(retroativo), "limite": limite}
    if app:
        params["app"] = app
    if camada:
        params["camada"] = camada
    if simulados:
        params["simulados"] = 1
    return base.rstrip("/") + ROTA + "?" + urllib.parse.urlencode(params)


def cabecalhos(token: str | None) -> dict[str, str]:
    """Sem token, nenhum `Authorization` — o loopback não exige credencial."""
    cab = {"Accept": "text/markdown"}
    if token:
        cab["Authorization"] = f"Bearer {token}"
    return cab


def buscar(endereco: str, cab: dict[str, str], timeout: float = 60.0) -> str:
    req = urllib.request.Request(endereco, headers=cab, method=METODO)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL é do próprio central
        return resp.read().decode("utf-8")


def arquivo(saida: Path, hoje: date) -> Path:
    return saida / f"backlog-{hoje:%Y-%m-%d}.md"


def topo(md: str, n: int) -> list[str]:
    """As linhas da tabela da seção 1 (o topo), na ordem do relatório; nunca as das outras seções."""
    linhas: list[str] = []
    na_secao = False
    for linha in md.splitlines():
        if linha.startswith("## "):
            na_secao = linha.startswith("## 1.")
            continue
        if na_secao and linha.startswith("| fk-"):
            linhas.append(linha)
    return linhas[:max(0, n)]


def resumo(linha: str) -> str:
    """`fk-… · camada · o quê · ocorrências · US$ · estado`, a partir das colunas da tabela do topo."""
    c = [x.strip().replace("\\|", "|") for x in _COLUNA.split(linha.strip().strip("|"))]
    if len(c) < 10:
        return linha
    return f"{c[0]} · {c[1]} · {c[2]} · {c[3]} ocorrências · US$ {c[5]} · {c[9]}"


def _sem_token(texto: str, token: str | None) -> str:
    return texto.replace(token, "***") if token else texto


def main(argv: Sequence[str] | None = None, *, buscador: Buscador = buscar, hoje: date | None = None,
         token: str | None = None) -> int:
    ap = argparse.ArgumentParser(description="O que mais falha: grava o backlog do dia (só GET, sem IA).")
    ap.add_argument("--base", default=BASE_PADRAO, help="endereço do central (padrão: %(default)s)")
    ap.add_argument("--dias", type=int, default=14, help="janela em dias (padrão: %(default)s)")
    ap.add_argument("--retroativo", action="store_true",
                    help="inclui o legado classificado na leitura (tentativas sem failure_kind gravado)")
    ap.add_argument("--app", help="só este pacote")
    ap.add_argument("--camada", help="só esta camada (ia_ator, plano, verificacao, aparelho...)")
    ap.add_argument("--limite", type=int, default=20, help="linhas no topo do relatório (padrão: %(default)s)")
    ap.add_argument("--top", type=int, default=5, help="linhas impressas aqui (padrão: %(default)s)")
    ap.add_argument("--simulados", action="store_true", help="inclui as execuções simuladas")
    ap.add_argument("--saida", default=str(SAIDA_PADRAO), help="pasta do md (padrão: data/aprendizado)")
    a = ap.parse_args(argv)
    chave = token if token is not None else (os.environ.get("API_TOKEN") or None)
    endereco = url(a.base, dias=a.dias, retroativo=a.retroativo, limite=a.limite, app=a.app, camada=a.camada,
                   simulados=a.simulados)
    try:
        md = buscador(endereco, cabecalhos(chave))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(_sem_token(f"erro ao ler {endereco}: {type(exc).__name__}: {exc}", chave), file=sys.stderr)
        return 2
    destino = arquivo(Path(a.saida), hoje or date.today())
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(md, encoding="utf-8")
    print(f"gravado: {destino}")
    linhas = topo(md, a.top)
    if not linhas:
        print("nenhum grupo acima do mínimo na janela" + ("" if a.retroativo else " (sem o legado: --retroativo)"))
    for linha in linhas:
        print(_sem_token(resumo(linha), chave))
    return 0


if __name__ == "__main__":
    sys.exit(main())
