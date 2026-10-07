"""Frente GitHub (29.155, C5): os rótulos do repositório, num lugar só.

`python scripts/github_rotulos.py` (ensaio, o padrão) só LÊ os rótulos do repositório e diz o que criaria ou atualizaria.
`python scripts/github_rotulos.py --aplicar` cria os que faltam e corrige cor e descrição dos que divergem.
Nunca apaga um rótulo (os dez padrão do GitHub e qualquer outro ficam como estão) e nunca mexe em issue ou PR.

Os rótulos pedem permissão de escrita no repositório: rode com o `gh` logado na conta do dono. O repositório vem de
`--repo dono/nome` ou de `GITHUB_REPOSITORY`; sem nenhum dos dois, o `gh` usa o do diretório atual.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable

# (nome, cor sem #, descrição de até 100 caracteres)
ROTULOS: tuple[tuple[str, str, str], ...] = (
    ("agente", "5319e7", "Tarefa para o agente de nuvem (modelo tarefa-do-agente)"),
    ("agente-nuvem", "7057ff", "Item mecânico liberado pela orquestradora para o agente (atribuição só por scripts/agente_nuvem.py)"),
    ("achado", "fbca04", "Achado a conferir: dado, não ordem (modelo achado)"),
    ("ci", "d93f0b", "Corrida do CI e rede da noite"),
    ("frente:android", "0e8a16", "Frente Android: backend, parque e execução"),
    ("frente:jev", "1d76db", "Frente Jev: leitura e planejamento"),
    ("frente:aprendizado", "0052cc", "Frente Aprendizado: ensino e conhecimento"),
    ("frente:portal", "5ebeff", "Frente Portal: site e painel público"),
    ("frente:canais", "006b75", "Frente Canais: quadros e Telegram"),
    ("frente:github", "24292f", "Frente GitHub: agents, Actions, issues e settings"),
    ("frente:desenho", "c5def5", "Frente Desenho: contratos e arquitetura"),
    ("frente:devops", "bfd4f2", "Frente DevOps: plataforma, funil e deploy"),
    ("tamanho:P", "c2e0c6", "Pequeno: uma a duas horas"),
    ("tamanho:M", "fef2c0", "Médio: um arquivo de código com testes"),
    ("tamanho:G", "f9d0c4", "Grande: quebre antes de dar a um agente"),
)

_COR = re.compile(r"^[0-9a-f]{6}$")
Gh = Callable[..., str]


def gh_real(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def plano(existentes: list[dict[str, object]]) -> list[tuple[str, str, str, str]]:
    """(ação, nome, cor, descrição) para cada rótulo da lista que falta ou diverge. Nada além disso."""
    por_nome = {str(r.get("name", "")).lower(): r for r in existentes}
    saida: list[tuple[str, str, str, str]] = []
    for nome, cor, descricao in ROTULOS:
        atual = por_nome.get(nome.lower())
        if atual is None:
            saida.append(("criar", nome, cor, descricao))
        elif str(atual.get("color", "")).lower() != cor or str(atual.get("description") or "") != descricao:
            saida.append(("atualizar", nome, cor, descricao))
    return saida


def aplicar(acoes: list[tuple[str, str, str, str]], repo: str, gh: Gh) -> None:
    for _, nome, cor, descricao in acoes:
        # --force: cria, ou atualiza cor e descrição se já existe; nunca apaga
        gh("label", "create", nome, *_repo(repo), "--color", cor, "--description", descricao, "--force")


def _repo(repo: str) -> tuple[str, ...]:
    return ("--repo", repo) if repo else ()


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cria ou atualiza os rótulos do repositório (nunca apaga).")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="dono/nome")
    ap.add_argument("--aplicar", action="store_true", help="escreve no repositório (sem isso, só mostra o plano)")
    args = ap.parse_args(argv)
    for nome, cor, descricao in ROTULOS:
        assert _COR.match(cor) and len(descricao) <= 100, nome  # contrato da lista, não entrada de quem roda
    if args.repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        print("erro: --repo fora do formato dono/nome", file=sys.stderr)
        return 1
    gh = gh or gh_real
    try:
        existentes = json.loads(gh("label", "list", *_repo(args.repo), "--limit", "200", "--json", "name,color,description"))
        acoes = plano(existentes)
        for acao, nome, cor, _ in acoes:
            print(f"{acao}: {nome} (#{cor})")
        if not acoes:
            print("nada a fazer: os rótulos já estão como a lista pede")
        elif args.aplicar:
            aplicar(acoes, args.repo, gh)
            print(f"aplicado: {len(acoes)} rótulo(s)")
        else:
            print(f"ensaio: {len(acoes)} rótulo(s) a aplicar; rode de novo com --aplicar")
    except (RuntimeError, json.JSONDecodeError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
