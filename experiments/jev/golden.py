"""Carrega e VALIDA o golden set contra o working tree. Validar não é gerar: o gabarito é manual (ver golden.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from corpus import read_text

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


def validate(items: list[Item], root: Path) -> list[str]:
    """Problemas encontrados (lista vazia = golden consistente com o working tree)."""
    problems: list[str] = []
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        problems.append("ids duplicados")
    if not 10 <= len(items) <= 20:
        problems.append(f"esperado entre 10 e 20 itens, há {len(items)}")
    cats = {i.category for i in items}
    if REQUIRED_CATEGORIES - cats:
        problems.append(f"categorias sem item: {sorted(REQUIRED_CATEGORIES - cats)}")
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
