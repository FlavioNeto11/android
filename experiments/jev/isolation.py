"""Verificação de isolamento do corpus sintético: roda ANTES de qualquer byte sair (o smoke aborta se falhar).

Confere, sobre os arquivos de `synthetic_corpus/` e sobre os payloads do plano: nenhum segredo, nenhum caminho/nome do projeto
privado (aparelhos, contas, apps reais, caminhos), nenhum nome de função/classe igual ao do repositório, nenhuma sequência
informativa de 12 tokens idêntica à do código privado. Só stdlib.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from corpus import list_files, read_text, repo_root
from redact import redact

N = 12
FORBIDDEN_LITERALS = (
    "backend/", "backend\\", "c:\\", "/users/", "/home/", "flavio", "android-0", "android-1", "worker-lan", "instagram",
    "outlook", "persona", "poc.sqlite", "estado-atual", "farm-central", "appium", "emulator", "wireguard", ".env",
    "config.yaml", "typesafe_api_key", "bearer ",
)
GENERIC_NAMES = {"__init__", "add", "check", "record", "resolve", "main", "run", "get", "set", "delay_for"}
BOILERPLATE = {"self", "def", "return", "none", "str", "int", "float", "bool", "if", "else", "elif", "not", "in", "is",
               "for", "import", "from", "class", "the", "and", "or", "pass", "raise", "true", "false", "dict", "list"}
DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]", text.lower())


def informative(window: list[str]) -> bool:
    words = {w for w in window if re.fullmatch(r"[a-z_][a-z0-9_]*", w) and w not in BOILERPLATE}
    return len(words) >= 5


def check(corpus_dir: Path, payloads: list[Any] | None = None, root: Path | None = None) -> dict:
    """Devolve {"status": "PASS"|"FAIL", "problems": [...], counters}. Nunca devolve conteúdo, só contagens e nomes de arquivo."""
    root = root or repo_root()
    problems: list[str] = []
    files = sorted(corpus_dir.rglob("*.py")) + sorted(corpus_dir.rglob("*.md"))
    texts = {f: f.read_text(encoding="utf-8") for f in files}
    blobs = [("corpus:" + f.name, t) for f, t in texts.items()]
    blobs += [(f"payload:{i}", json.dumps(p, ensure_ascii=False)) for i, p in enumerate(payloads or [])]
    for label, text in blobs:
        r = redact(text)
        if r.hard or r.soft:
            problems.append(f"{label}: achado de redação {sorted({**r.hard, **r.soft})}")
        low = text.lower()
        for lit in FORBIDDEN_LITERALS:
            if lit in low:
                problems.append(f"{label}: literal proibido {lit!r}")
    repo_idents: set[str] = set()
    repo_ngrams: set[int] = set()
    for rel in list_files(root, ["backend/app", "backend/tests"], [".py"]):
        text = read_text(root, rel)
        try:
            repo_idents.update(n.name.lower() for n in ast.walk(ast.parse(text)) if isinstance(n, DEF_NODES))
        except SyntaxError:
            pass
        tk = tokens(text)
        for i in range(len(tk) - N + 1):
            w = tk[i:i + N]
            if informative(w):
                repo_ngrams.add(hash(tuple(w)))
    for f, text in texts.items():
        if f.suffix != ".py":
            continue
        own = {n.name.lower() for n in ast.walk(ast.parse(text)) if isinstance(n, DEF_NODES)}
        shared = (own & repo_idents) - GENERIC_NAMES
        if shared:
            problems.append(f"{f.name}: nomes iguais aos do repositório privado {sorted(shared)}")
        tk = tokens(text)
        dup = sum(1 for i in range(len(tk) - N + 1) if informative(tk[i:i + N]) and hash(tuple(tk[i:i + N])) in repo_ngrams)
        if dup:
            problems.append(f"{f.name}: {dup} sequências informativas de {N} tokens idênticas às do repositório")
    return {"status": "FAIL" if problems else "PASS", "problems": problems, "corpus_files": len(files),
            "payloads_checked": len(payloads or []), "repo_identifiers": len(repo_idents), "repo_ngrams": len(repo_ngrams)}
