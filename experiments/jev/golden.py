"""Carrega e VALIDA o golden set contra o working tree. Validar não é gerar: o gabarito é manual (ver golden.json)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from corpus import read_text
from textutil import tokenize

REQUIRED_CATEGORIES = {
    "EXACT_SYMBOL", "EXACT_TEXT", "BEHAVIOR_SEARCH", "CROSS_MODULE_FLOW", "CONFIGURATION", "GUARDRAIL",
    "EVENT_ORIGIN", "TEST_LOCATION", "STATE_MUTATION", "ARCHITECTURE",
}
FIELDS = ("id", "question", "category", "expected_files", "expected_symbols", "expected_regions",
          "grep_friendly", "semantic_only", "notes")


@dataclass(frozen=True)
class Item:
    id: str
    question: str
    category: str
    expected_files: tuple[str, ...]
    expected_symbols: tuple[str, ...]
    expected_regions: tuple[tuple[str, int, int], ...]
    grep_friendly: bool
    semantic_only: bool
    notes: str


def load(path: Path) -> tuple[dict, list[Item]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for raw in data["items"]:
        missing = [f for f in FIELDS if f not in raw]
        if missing:
            raise ValueError(f"{raw.get('id', '?')}: campos ausentes {missing}")
        items.append(Item(
            raw["id"], raw["question"], raw["category"], tuple(raw["expected_files"]),
            tuple(raw["expected_symbols"]), tuple((f, int(a), int(b)) for f, a, b in raw["expected_regions"]),
            bool(raw["grep_friendly"]), bool(raw["semantic_only"]), raw["notes"]))
    return data["meta"], items


def validate(items: list[Item], root: Path, meta: dict | None = None) -> list[str]:
    """Problemas encontrados (lista vazia = golden consistente com o working tree).
    `meta["required_categories"]` (golden público) substitui as categorias do golden privado."""
    problems: list[str] = []
    required = set((meta or {}).get("required_categories") or REQUIRED_CATEGORIES)
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        problems.append("ids duplicados")
    if not 10 <= len(items) <= 20:
        problems.append(f"esperado entre 10 e 20 itens, há {len(items)}")
    cats = {i.category for i in items}
    if required - cats:
        problems.append(f"categorias sem item: {sorted(required - cats)}")
    for it in items:
        if it.grep_friendly and it.semantic_only:
            problems.append(f"{it.id}: grep_friendly e semantic_only ao mesmo tempo")
        if not it.expected_files or not it.expected_regions:
            problems.append(f"{it.id}: sem expected_files/expected_regions")
        for f in it.expected_files:
            if not (root / f).is_file():
                problems.append(f"{it.id}: arquivo inexistente {f}")
        regions_files = {r[0] for r in it.expected_regions}
        if not regions_files <= set(it.expected_files):
            problems.append(f"{it.id}: região fora de expected_files")
        for f, a, b in it.expected_regions:
            p = root / f
            if not p.is_file():
                continue
            n = len(read_text(root, f).splitlines())
            if not 1 <= a <= b <= n:
                problems.append(f"{it.id}: região {f}:{a}-{b} fora do arquivo ({n} linhas)")
        text_by_file = {f: read_text(root, f) for f in it.expected_files if (root / f).is_file()}
        for sym in it.expected_symbols:
            needle = sym.split(".")[-1]
            if not any(needle in t for t in text_by_file.values()):
                problems.append(f"{it.id}: símbolo {sym} não aparece em expected_files")
    return problems


_TICKS = re.compile(r"`([^`]+)`")


def group_of(item: Item) -> str | None:
    """'EXACT' | 'SEMANTIC' pelo prefixo da categoria (só o golden público usa este esquema)."""
    for grp in ("EXACT", "SEMANTIC"):
        if item.category.startswith(grp + "_"):
            return grp
    return None


def validate_groups(items: list[Item], meta: dict, root: Path) -> list[str]:
    """Regras MECÂNICAS que separam EXACT de SEMANTIC no golden público (meta.group_rule). Não usa Jev."""
    problems: list[str] = []
    ubiquitous = {w.lower() for w in meta.get("ubiquitous_words", [])}
    for it in items:
        grp = group_of(it)
        if grp is None:
            problems.append(f"{it.id}: categoria {it.category} sem prefixo EXACT_/SEMANTIC_")
            continue
        region_text = "\n".join(
            "\n".join(read_text(root, f).splitlines()[a - 1:b]) for f, a, b in it.expected_regions if (root / f).is_file())
        if grp == "EXACT":
            if not it.grep_friendly or it.semantic_only:
                problems.append(f"{it.id}: EXACT exige grep_friendly=true e semantic_only=false")
            ticks = _TICKS.findall(it.question)
            if not ticks:
                problems.append(f"{it.id}: EXACT sem literal entre crases na pergunta")
            for t in ticks:
                if t not in region_text:
                    problems.append(f"{it.id}: literal `{t}` não aparece verbatim nas regiões esperadas")
        else:
            if not it.semantic_only or it.grep_friendly:
                problems.append(f"{it.id}: SEMANTIC exige semantic_only=true e grep_friendly=false")
            if _TICKS.findall(it.question):
                problems.append(f"{it.id}: SEMANTIC não pode citar literais entre crases")
            sym_tokens = {t for s in it.expected_symbols for part in s.split(".")
                          for t in tokenize(part, keep_compound=False)}
            q_tokens = {t for t in tokenize(it.question, keep_compound=False) if t not in ubiquitous}
            shared = sorted(sym_tokens & q_tokens)
            if shared:
                problems.append(f"{it.id}: a pergunta compartilha tokens com os símbolos esperados: {shared}")
    return problems
