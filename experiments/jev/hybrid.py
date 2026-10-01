"""Regra HÍBRIDA (experimento NOVO, pós-hoc e exploratório): salvaguarda lexical + `jev_map`.

NÃO faz parte do experimento Scrapy original (veredito NO_GO preservado). A regra nasceu DEPOIS de ver os resultados do Scrapy,
por isso o replay offline dela não pode produzir GO; só `PROMISING | NOT_PROMISING | INCONCLUSIVE`. O teste de verdade é um
holdout com corpus novo, definido e congelado antes de qualquer chamada Jev.

A regra é UMA só e está congelada (versão e hash em `hybrid_rule.lock.json`; um teste falha se o bloco RULE mudar). Ela só olha a
pergunta e os resultados dos retrievers; nunca o gabarito (expected_*) nem a categoria EXACT/SEMANTIC.

Só stdlib. Nenhuma rede.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
LOCK = HERE / "hybrid_rule.lock.json"

# --- RULE BEGIN (o hash cobre exatamente este bloco)
RULE_VERSION = "1"
RULE_SPEC = (
    "1. Identificador explícito = trecho entre crases (>= 3 caracteres) OU snake_case OU CamelCase (>= 2 partes) OU dotted.path "
    "(cada parte >= 2 caracteres); os três últimos com >= 4 caracteres no total.\\n"
    "2. Se a pergunta tem identificador explícito E a busca lexical (ripgrep com ESSES termos) acha ao menos um arquivo: "
    "a salvaguarda está ATIVA; senão o híbrido é exatamente o jev_map.\\n"
    "3. Ativa: ranking de arquivos = [melhor arquivo lexical] + ranking do jev_map sem duplicatas. O melhor arquivo lexical é o "
    "primeiro do ranking do ripgrep (termos distintos casados, depois ocorrências, depois caminho).\\n"
    "4. Ativa: trechos entregues = até 2 janelas do melhor arquivo lexical + trechos que o jev_map entregou, sem duplicatas, "
    "cortados pelo mesmo teto de entrega de todos (8 trechos, 16 KiB).\\n"
    "5. A regra não lê expected_files, expected_regions nem a categoria do golden."
)
MAX_LEXICAL_WINDOWS = 2
DELIVER_MAX_SPANS = 8
DELIVER_MAX_BYTES = 16 * 1024
MIN_IDENT_LEN = 4

_TICK = re.compile(r"`([^`]{3,})`")
_SNAKE = re.compile(r"(?<![\w.])_*[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\w])")
_LEAD_UNDERSCORE = re.compile(r"(?<![\w])_[A-Za-z][A-Za-z0-9_]{2,}")
_CAMEL = re.compile(r"(?<![\w])(?:[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+|[a-z]+(?:[A-Z][a-z0-9]+)+)(?![\w])")
_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w+(?:\.[A-Za-z_]\w+)+(?![\w])")


def explicit_identifiers(question: str) -> list[str]:
    """Identificadores explícitos da pergunta (ordem de aparição, sem repetir ignorando maiúsculas)."""
    found = [m.group(1).strip() for m in _TICK.finditer(question)]
    text = _TICK.sub(" ", question)
    for rx in (_SNAKE, _LEAD_UNDERSCORE, _CAMEL, _DOTTED):
        found += [m.group(0) for m in rx.finditer(text) if len(m.group(0)) >= MIN_IDENT_LEN]
    seen: set[str] = set()
    out: list[str] = []
    for f in found:
        if f and f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    return out


def safeguard_active(question: str, lexical_files: Sequence[str]) -> bool:
    return bool(explicit_identifiers(question)) and bool(lexical_files)


def merge_files(active: bool, lexical_files: Sequence[str], jev_files: Sequence[str]) -> list[str]:
    if not active:
        return list(jev_files)
    top = lexical_files[0]
    return [top] + [f for f in jev_files if f != top]


def merge_regions(active: bool, lexical_top_regions: Sequence[Sequence], jev_regions: Sequence[Sequence]) -> list[list]:
    """`lexical_top_regions`: janelas [arquivo, ini, fim] do MELHOR arquivo lexical, em ordem."""
    if not active:
        return [list(r) for r in jev_regions]
    out: list[list] = []
    seen: set[tuple] = set()
    for r in list(lexical_top_regions)[:MAX_LEXICAL_WINDOWS] + list(jev_regions):
        t = tuple(r)
        if t not in seen:
            seen.add(t)
            out.append(list(t))
    return out
# --- RULE END


def _rule_block() -> str:
    text = Path(__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    a = text.index("# --- RULE BEGIN")
    b = text.index("# --- RULE END")
    return text[a:b]


def rule_hash() -> str:
    return hashlib.sha256(_rule_block().encode("utf-8")).hexdigest()


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def cap_regions(regions: Sequence[Sequence], size_of) -> list[list]:
    """Mesmo corte de entrega dos outros retrievers (`baselines.deliver`): até 8 trechos e 16 KiB, parando no primeiro que estoura."""
    out: list[list] = []
    total = 0
    for r in regions:
        n = size_of(r)
        if len(out) >= DELIVER_MAX_SPANS or total + n > DELIVER_MAX_BYTES:
            break
        out.append(list(r))
        total += n
    return out


def hybrid_spans(question: str, rg, jm, jm_delivered):
    """Para o harness: (Retrieval híbrido, trechos entregues). `rg` = Retrieval do ripgrep com os termos identificadores,
    `jm` = Retrieval do jev_map, `jm_delivered` = trechos que o jev_map entregou. Reaproveita a MESMA requisição do jev_map."""
    import baselines as bl
    lex_files = rg.ranked_files()
    active = safeguard_active(question, lex_files)
    files = merge_files(active, lex_files, jm.ranked_files())
    by_file: dict[str, list] = {}
    for s in list(rg.spans) + list(jm.spans):
        by_file.setdefault(s.file, []).append(s)
    lex_top = [s for s in rg.spans if lex_files and s.file == lex_files[0]] if active else []
    regions = merge_regions(active, [[s.file, s.start, s.end] for s in lex_top], [[s.file, s.start, s.end] for s in jm_delivered])
    index: dict[tuple, bl.Span] = {}
    for s in list(rg.spans) + list(jm.spans) + list(jm_delivered):
        index.setdefault((s.file, s.start, s.end), s)
    delivered = bl.deliver([index[tuple(r)] for r in regions])
    ranked_spans = []
    for f in files:
        ranked_spans.extend(by_file.get(f, [])[:1])
    out = bl.Retrieval("hybrid", ranked_spans, files_examined=len(files), bytes_read=0,
                       notes={"safeguard": active, "identifiers": explicit_identifiers(question)})
    return out, delivered
