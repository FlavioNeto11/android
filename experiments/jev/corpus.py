"""Corpus e divisão em trechos (chunks) do piloto A. Só stdlib; lê o working tree, nunca o Git remoto.

Um chunk é uma função/método (ou o resto do módulo/classe entre elas). Trechos acima de MAX_LINES são fatiados em
janelas com sobreposição, para que um retriever nunca devolva uma função de 900 linhas como "um trecho".
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from redact import is_sensitive_path

MAX_LINES = 80
WINDOW = 60
STEP = 50


@dataclass(frozen=True)
class Chunk:
    file: str          # relativo à raiz do repositório, com '/'
    start: int         # 1-indexado, inclusivo
    end: int
    name: str
    text: str

    @property
    def id(self) -> str:
        return f"{self.file}:{self.start}-{self.end}"

    @property
    def nbytes(self) -> int:
        return len(self.text.encode("utf-8"))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def list_files(root: Path, roots: list[str], extensions: list[str]) -> list[str]:
    out: list[str] = []
    for r in roots:
        base = root / r
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in extensions and "__pycache__" not in p.parts:
                rel = p.relative_to(root).as_posix()
                if not is_sensitive_path(rel):
                    out.append(rel)
    return out


def read_text(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8", errors="replace")


def file_size(root: Path, rel: str) -> int:
    return (root / rel).stat().st_size


def _units(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(início, fim, nome) de funções de módulo e métodos de classe. Funções aninhadas ficam dentro da unidade."""
    out: list[tuple[int, int, str]] = []
    body = getattr(tree, "body", [])
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((_start(node), node.end_lineno or node.lineno, node.name))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((_start(sub), sub.end_lineno or sub.lineno, f"{node.name}.{sub.name}"))
    return sorted(out)


def _start(node: ast.AST) -> int:
    decos = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decos])  # type: ignore[attr-defined]


def _windows(lo: int, hi: int) -> list[tuple[int, int]]:
    if hi - lo + 1 <= MAX_LINES:
        return [(lo, hi)]
    out = []
    s = lo
    while s <= hi:
        e = min(hi, s + WINDOW - 1)
        out.append((s, e))
        if e == hi:
            break
        s += STEP
    return out


def chunk_file(rel: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return []
    try:
        units = _units(ast.parse(text))
    except SyntaxError:
        units = []
    spans: list[tuple[int, int, str]] = []
    cursor = 1
    for s, e, name in units:
        if s < cursor:           # método dentro de unidade já coberta (não deve ocorrer) → ignora
            continue
        if s > cursor:
            spans.append((cursor, s - 1, "<module>"))
        spans.append((s, e, name))
        cursor = e + 1
    if cursor <= n:
        spans.append((cursor, n, "<module>"))
    out: list[Chunk] = []
    for lo, hi, name in spans:
        for a, b in _windows(lo, hi):
            body = "\n".join(lines[a - 1:b])
            if body.strip():
                out.append(Chunk(rel, a, b, name, body))
    return out


def build_chunks(root: Path, files: list[str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for rel in files:
        chunks.extend(chunk_file(rel, read_text(root, rel)))
    return chunks
