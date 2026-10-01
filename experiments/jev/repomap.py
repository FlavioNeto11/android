"""Mapa do repositório (repo map) para o benchmark público: UMA linha por arquivo, gerada de forma determinística.

Cada linha tem a primeira frase da docstring do módulo e os nomes de classes/funções/métodos definidos (sem corpo). Serve
de `entries` para o Jev escolher ARQUIVOS (variante `jev_map`). Só stdlib; nada de rede.

Limite do Jev: Choice aceita no máximo 255 opções (docs.typesafe.ai); `build_map` falha alto se o corpus passar disso.
"""
from __future__ import annotations

import ast
from pathlib import Path

from corpus import read_text

LINE_CAP_BYTES = 260
DOC_CAP = 110
NAMES_CAP = 14
MAX_OPTIONS = 255


def _doc_line(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree) or ""
    first = doc.strip().splitlines()[0].strip() if doc.strip() else ""
    return first[:DOC_CAP]


def _names(tree: ast.Module) -> list[str]:
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(node.name)
        elif isinstance(node, ast.ClassDef):
            out.append(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                        sub.name.startswith("__") and sub.name != "__init__"):
                    out.append(f"{node.name}.{sub.name}")
    seen: set[str] = set()
    uniq = [n for n in out if not (n in seen or seen.add(n))]
    return uniq


def describe(text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    names = _names(tree)
    if not names and not tree.body:
        return ""
    doc = _doc_line(tree)
    line = (doc + " | " if doc else "") + "defs: " + ", ".join(names[:NAMES_CAP])
    while len(line.encode("utf-8")) > LINE_CAP_BYTES and ", " in line:
        line = line.rsplit(", ", 1)[0]
    return line


def build_map(root: Path, files: list[str]) -> dict[str, str]:
    """{caminho: linha}. Arquivos vazios ficam de fora (nada a escolher neles)."""
    out: dict[str, str] = {}
    for rel in files:
        line = describe(read_text(root, rel))
        if line:
            out[rel] = line
    if len(out) > MAX_OPTIONS:
        raise ValueError(f"{len(out)} arquivos no mapa: o Choice do Jev aceita no máximo {MAX_OPTIONS} opções")
    return out
