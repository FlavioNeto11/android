"""Frente GitHub (29.188): publica o relatório semanal de custo (`github_custo.py --resumos`) numa issue única de custo.

Quem chama: `.github/workflows/custo-semanal.yml` (segunda 06:00Z, runner HOSPEDADO). Na mão, `--ensaio` só imprime o que faria.

O que faz: lê o arquivo do relatório, recusa publicar se o texto tiver FORMATO de dado que não pode sair (e-mail, IPv4, serial de
aparelho, arroba de conta, credencial, chave conhecida: o relatório já nasce sem isso, a checagem é a rede), garante o rótulo `custo`
(nunca outro) e procura a issue ABERTA com esse rótulo e o título fixo: se há, comenta nela com a semana nova; se não há, abre. Nunca
atribui ninguém nem usa rótulo de agente. Erro do `gh` derruba o script com código 1: falha nunca vira "publicado".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROTULO = "custo"
ROTULO_COR = "0e8a16"
ROTULO_DESCRICAO = "Relatório semanal de custo do GitHub (minutos e créditos)"
TITULO = "Custo semanal do GitHub (minutos hospedados e créditos)"
LIMITE_CORPO = 60_000  # o GitHub aceita 65.536
Gh = Callable[..., str]

_PROIBIDO = {
    "e-mail": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "IPv4": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    "serial de aparelho": re.compile(r"\b(?:emulator|worker)-\d+\b", re.IGNORECASE),
    "arroba de conta": re.compile(r"(?<![\w.])@[A-Za-z][\w.]{1,}"),
    "credencial": re.compile(r"(?i)\bbearer\s+\S{6,}|\b(?:senha|password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S{3,}"),
    "chave conhecida": re.compile(r"\b(?:sk-|ghp_|gho_|AKIA)[A-Za-z0-9_\-]{8,}"),
}


def proibidos(texto: str) -> list[str]:
    return [nome for nome, rx in _PROIBIDO.items() if rx.search(texto)]


def gh_real(*args: str, entrada: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=entrada, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"o gh falhou (código {r.returncode})")  # a mensagem do gh costuma trazer dono/repo: não vai para o log
    return r.stdout


def cortar(texto: str) -> str:
    if len(texto) <= LIMITE_CORPO:
        return texto
    return texto[: LIMITE_CORPO - 80].rstrip() + "\n\n(relatório cortado no limite; o arquivo inteiro está no artifact do run)"


def publicar(repo: str, texto: str, *, ensaio: bool, gh: Gh | None = None) -> str:
    """Devolve 'ensaio', 'aberta N' ou 'comentada N'. ValueError se o texto tiver formato proibido ou vier vazio."""
    gh = gh or gh_real
    if not texto.strip():
        raise ValueError("relatório vazio: nada publicado")
    achados = proibidos(texto)
    if achados:
        raise ValueError("o relatório tem formato que não pode sair (" + ", ".join(achados) + "): nada publicado")
    corpo = cortar(texto)
    if ensaio:
        return "ensaio"
    gh("label", "create", ROTULO, "--repo", repo, "--color", ROTULO_COR, "--description", ROTULO_DESCRICAO, "--force")
    abertas = json.loads(
        gh("issue", "list", "--repo", repo, "--label", ROTULO, "--state", "open", "--limit", "100", "--json", "number,title")
    )
    da_medida = sorted(int(i["number"]) for i in abertas if i.get("title") == TITULO)
    if da_medida:
        gh("issue", "comment", str(da_medida[0]), "--repo", repo, "--body-file", "-", entrada=corpo)
        return f"comentada {da_medida[0]}"
    url = gh("issue", "create", "--repo", repo, "--title", TITULO, "--label", ROTULO, "--body-file", "-", entrada=corpo).strip()
    return f"aberta {url.rsplit('/', 1)[-1]}"


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publica o relatório semanal de custo numa issue única.")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--arquivo", required=True, type=Path)
    ap.add_argument("--ensaio", action="store_true")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("erro: --repo ausente ou fora do formato dono/nome", file=sys.stderr)
        return 1
    try:
        texto = a.arquivo.read_text(encoding="utf-8")
        print(publicar(a.repo, texto, ensaio=a.ensaio, gh=gh))
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
