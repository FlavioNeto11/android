"""Tokenização e extração de termos, compartilhadas pelas baselines e pelo provedor falso. Só stdlib."""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset("""
a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre entre ate que qual quais quem
onde quando como ser foi sao esta estao tem ter faz fazer ha se ou e mas nao sim ao aos pelo pela pelos pelas
isso isto esse essa este desta deste dessa desse aqui ali la mais menos muito cada todo toda todos todas outro outra
the an of to in on at by for with from is are was be been do does did how where who what which when and or not it its
this that these those as into than then there their they them can will would should could has have had
""".split())

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def deaccent(text: str) -> str:
    """Remove acentos preservando maiúsculas/minúsculas (necessário para separar camelCase)."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def fold(text: str) -> str:
    """Minúsculas sem acento: o código do projeto mistura 'manutenção' e 'manutencao'."""
    return deaccent(text).lower()


def tokenize(text: str, *, keep_compound: bool = True) -> list[str]:
    """Identificadores quebrados em snake_case e camelCase; o composto inteiro também é mantido (peso de exatidão)."""
    out: list[str] = []
    for ident in _IDENT.findall(deaccent(text)):
        folded = fold(ident)
        parts = [p for p in re.split(r"_+", ident) if p]
        subs: list[str] = []
        for p in parts:
            subs.extend(fold(x) for x in _CAMEL.findall(p))
        subs = [s for s in subs if len(s) > 1 and s not in STOPWORDS]
        out.extend(subs)
        if keep_compound and len(subs) > 1 and len(folded) > 3:
            out.append(folded.strip("_"))
        elif keep_compound and folded.startswith("_") and len(folded.strip("_")) > 3:
            out.append(folded.strip("_"))
    return out


_BACKTICK = re.compile(r"`([^`]+)`")
_QUOTED = re.compile(r"['\"‘“]([^'\"’”]{3,}?)['\"’”]")


def query_terms(question: str) -> tuple[list[str], str]:
    """Termos para uma busca textual feita do jeito que uma pessoa faria, de forma MECÂNICA (sem ajuste por item).

    Prioridade: (1) trechos entre crases, (2) identificadores com `_` ou `.`, (3) trechos entre aspas, quebrados em
    reticências, (4) palavras significativas (>= 5 letras, fora de stopwords), com e sem acento.
    Devolve (termos, regra_usada).
    """
    ticks = [t.strip() for t in _BACKTICK.findall(question)]
    if ticks:
        return _dedup(ticks), "backtick"
    assigns = re.findall(r"\b[A-Za-z_]\w*\s*=\s*'[^']+'", question)
    if assigns:
        return _dedup([a.replace(" ", "") for a in assigns]), "assignment"
    idents = [w for w in re.findall(r"[A-Za-z_][\w.]*", question) if "_" in w or "." in w.strip(".")]
    idents = [w.strip(".") for w in idents if len(w.strip(".")) > 3]
    if idents:
        return _dedup(idents), "identifier"
    quoted: list[str] = []
    for q in _QUOTED.findall(question):
        quoted.extend(s.strip() for s in re.split(r"\.\.\.|…", q) if len(s.strip()) > 3)
    if quoted:
        return _dedup(quoted), "quoted"
    words: list[str] = []
    for w in re.findall(r"[A-Za-zÀ-ÿ_]+", question):
        if len(w) >= 5 and fold(w) not in STOPWORDS:
            words.extend([w, fold(w)])
    return _dedup(words), "words"


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        if i.lower() not in seen:
            seen.add(i.lower())
            out.append(i)
    return out


_APP_ONLY = re.compile(r"fora d[oa]s? testes?|codigo de producao")
_TESTS_ONLY = re.compile(r"\bquais testes\b|\btestes? (?:cobre|cobrem)\b")


def scope_for(question: str) -> str:
    """'app' | 'tests' | 'all' — escopo de caminho que qualquer pessoa aplicaria. Igual para TODAS as estratégias."""
    q = fold(question)
    if _APP_ONLY.search(q):
        return "app"
    if _TESTS_ONLY.search(q):
        return "tests"
    return "all"


def scoped_roots(question: str, roots: list[str]) -> list[str]:
    sc = scope_for(question)
    if sc == "all":
        return list(roots)
    pick = [r for r in roots if ("tests" in r) == (sc == "tests")]
    return pick or list(roots)
