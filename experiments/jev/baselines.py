"""Baselines do piloto A: BASELINE_RIPGREP e BASELINE_CODE_SEARCH (BM25 local). Só stdlib + o binário `rg`.

Um "retriever" devolve `Retrieval`: uma lista ORDENADA de trechos (`Span`) candidatos. O harness decide depois o que
é entregue ao Claude (`deliver`), com as MESMAS travas para todos os retrievers.

PROXY_METRIC: tudo em bytes. Nada aqui é contagem de tokens.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from corpus import Chunk, file_size, read_text
from textutil import fold, query_terms, scoped_roots, tokenize

#: Janela de contexto do ripgrep (±linhas) e travas de entrega, fixas para todos (decididas antes de medir).
RG_CONTEXT = 5
DELIVER_MAX_SPANS = 8
DELIVER_MAX_BYTES = 16 * 1024


@dataclass(frozen=True)
class Span:
    file: str
    start: int
    end: int
    text: str
    score: float = 0.0

    @property
    def nbytes(self) -> int:
        return len(self.text.encode("utf-8"))


@dataclass
class Retrieval:
    strategy: str
    spans: list[Span]                       # ordenados do mais para o menos relevante
    files_examined: int = 0
    bytes_read: int = 0
    notes: dict = field(default_factory=dict)

    def ranked_files(self) -> list[str]:
        seen: list[str] = []
        for s in self.spans:
            if s.file not in seen:
                seen.append(s.file)
        return seen

    @property
    def bytes_returned(self) -> int:
        return sum(s.nbytes for s in self.spans)


def deliver(spans: list[Span], max_spans: int = DELIVER_MAX_SPANS, max_bytes: int = DELIVER_MAX_BYTES) -> list[Span]:
    """O que o Claude de fato recebe: os melhores trechos até um teto de quantidade e de bytes."""
    out: list[Span] = []
    total = 0
    for s in spans:
        if len(out) >= max_spans or total + s.nbytes > max_bytes:
            break
        out.append(s)
        total += s.nbytes
    return out


# --------------------------------------------------------------------------- ripgrep
def rg_command() -> tuple[list[str], dict[str, str]] | None:
    """Como chamar o ripgrep. Ordem: JEV_RG_BIN; `rg` no PATH; o binário do Claude Code embutido (`ARGV0=rg`, que é
    o que a função `rg` do shell faz aqui). Sem nenhum, `ripgrep()` usa o equivalente em Python (`engine=python`)."""
    env = dict(os.environ)
    if os.environ.get("JEV_RG_BIN"):
        return [os.environ["JEV_RG_BIN"]], env
    if shutil.which("rg"):
        return ["rg"], env
    cc = os.environ.get("CLAUDE_CODE_EXECPATH") or str(Path.home() / ".local" / "bin" / "claude.exe")
    if Path(cc).is_file():
        env["ARGV0"] = "rg"
        return [cc], env
    return None


def rg_available() -> bool:
    return rg_command() is not None


def _python_matches(root: Path, roots: list[str], terms: list[str]) -> dict[str, dict[int, set[str]]]:
    """Mesma semântica de `rg -i -F -g '*.py'` (sem diferenciar acento: o termo e a linha são dobrados)."""
    per_file: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    folded = [(t, fold(t)) for t in terms]
    for r in roots:
        for p in sorted((root / r).rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            for ln, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                low = fold(line)
                for t, ft in folded:
                    if ft in low:
                        per_file[rel][ln].add(t.lower())
    return per_file


def ripgrep(root: Path, roots: list[str], question: str, terms: list[str] | None = None) -> Retrieval:
    """Busca textual fixa, sem diferenciar maiúsculas, com janela ±RG_CONTEXT. Termos derivados mecanicamente da
    pergunta (`query_terms`). Ranking de arquivos = termos distintos casados (desc), depois nº de ocorrências (desc),
    depois caminho. O `rg` em si não ranqueia; esta ordenação é a que uma pessoa aplicaria ao ler o resultado."""
    if terms is None:
        terms, rule = query_terms(question)
    else:                                   # termos dados por quem chama (híbrido: identificadores explícitos)
        terms, rule = list(terms), "explicit_identifiers"
    roots = scoped_roots(question, roots)
    if not terms:
        return Retrieval("ripgrep", [], notes={"terms": [], "rule": rule})
    per_file: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    cmdenv = rg_command()
    engine = "ripgrep" if cmdenv else "python"
    if cmdenv:
        prefix, env = cmdenv
        cmd = prefix + ["--json", "-i", "-F", "-g", "*.py"]
        for t in terms:
            cmd += ["-e", t]
        cmd += roots
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        for line in proc.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") != "match":
                continue
            d = ev["data"]
            path = d["path"]["text"].replace("\\", "/")
            ln = d["line_number"]
            low = fold(d["lines"].get("text", ""))
            for t in terms:
                if fold(t) in low:
                    per_file[path][ln].add(t.lower())
    else:
        per_file = _python_matches(root, roots, terms)
    ranked = sorted(per_file, key=lambda f: (-len({t for s in per_file[f].values() for t in s}),
                                             -len(per_file[f]), f))
    spans: list[Span] = []
    bytes_read = 0
    for f in ranked:
        bytes_read += file_size(root, f)
        lines = read_text(root, f).splitlines()
        windows: list[list[int]] = []
        for ln in sorted(per_file[f]):
            lo, hi = max(1, ln - RG_CONTEXT), min(len(lines), ln + RG_CONTEXT)
            if windows and lo <= windows[-1][1] + 1:
                windows[-1][1] = max(windows[-1][1], hi)
            else:
                windows.append([lo, hi])
        for lo, hi in windows:
            spans.append(Span(f, lo, hi, "\n".join(lines[lo - 1:hi]), 0.0))
    return Retrieval("ripgrep", spans, files_examined=len(ranked), bytes_read=bytes_read,
                     notes={"terms": terms, "rule": rule, "files_with_matches": len(ranked), "engine": engine})


# --------------------------------------------------------------------------- BM25 sobre chunks
class Bm25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tf: list[Counter[str]] = []
        self.len: list[int] = []
        df: Counter[str] = Counter()
        for c in chunks:
            toks = tokenize(f"{c.file} {c.name} {c.text}")
            tf = Counter(toks)
            self.tf.append(tf)
            self.len.append(len(toks))
            df.update(tf.keys())
        n = len(chunks)
        self.avg = (sum(self.len) / n) if n else 1.0
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, question: str, top: int = 50, prefixes: list[str] | None = None) -> list[tuple[int, float]]:
        q = [t for t in tokenize(question) if t in self.idf]
        if not q:
            return []
        scores: dict[int, float] = {}
        pref = tuple(p.rstrip("/") + "/" for p in prefixes) if prefixes else None
        for i, tf in enumerate(self.tf):
            if pref and not self.chunks[i].file.startswith(pref):
                continue
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                scores[i] = s
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top]


def code_search(index: Bm25Index, question: str, top: int = 30, roots: list[str] | None = None) -> Retrieval:
    hits = index.search(question, top, scoped_roots(question, roots) if roots else None)
    spans = [Span(index.chunks[i].file, index.chunks[i].start, index.chunks[i].end, index.chunks[i].text, s)
             for i, s in hits]
    files = {s.file for s in spans}
    return Retrieval("code_search", spans, files_examined=len(files), bytes_read=sum(sizes_of(index, files)),
                     notes={"candidates": len(spans)})


def sizes_of(index: Bm25Index, files: set[str]) -> list[int]:
    seen: dict[str, int] = {}
    for c in index.chunks:
        if c.file in files:
            seen[c.file] = seen.get(c.file, 0) + len(c.text.encode("utf-8"))
    return list(seen.values())
