"""Piloto B (offline, replay): extrai atributos SEGUROS do histórico e avalia roteadores SEM executar nada.

- Lê o SQLite do projeto em modo SOMENTE LEITURA (`mode=ro`) e só as colunas da LISTA PERMITIDA abaixo. Nunca lê
  `args`, `rationale`, `error`, `title`, `goal`, `result`, `observed_result`, `status_detail`, `failure_screen`,
  `variables`, `bindings`, `saidas` nem texto de tela: são texto livre e podem conter conteúdo de conta real.
- O ids viram hash (sem reversão). Saída em `data/jev-pilot/replay/` (ignorado pelo Git).
- NENHUM roteador aqui chama o Jev real. `JevRouter` aceita qualquer `DecisionProvider` (nos testes: o falso).

Unidade de replay = uma ETAPA com desfecho final. Domínio do Jev = o que a regra determinística NÃO resolveu
(rótulo != DETERMINISTIC): o desenho é "regra primeiro, Jev só no resto".

LIMITAÇÕES (também em docs/research/jev-pilot.md): os rótulos são derivados do desfecho, não de um oráculo; o
modelo realmente usado NÃO é o rótulo; `driven_by` vaza o rótulo DETERMINISTIC; as chamadas de IA só se ligam à
etapa (não à tentativa) em linhas anteriores à migração 045; `STRONG_AI_UNPROVEN` (escalado por política na 1ª
tentativa) não tem contrafactual e fica fora da acurácia.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import repo_root                                  # noqa: E402
from netguard import no_network                               # noqa: E402
from provider import DecisionProvider, ProviderError, choice_q  # noqa: E402

#: LISTA PERMITIDA de colunas. Qualquer outra é recusada por `safe_select`.
ALLOWED = {
    "steps": ["id", "side_effect", "capability", "driven_by", "status", "attempts", "strategy", "failure_kind",
              "app_id", "started_at", "finished_at"],
    "ai_calls": ["step_id", "role", "tier", "model", "provider", "ok", "ms", "input_tokens", "output_tokens",
                 "with_image", "usd", "error_kind", "fallback"],
}
FINAL = ("succeeded", "failed", "uncertain", "waiting_user")
ROUTES = ("DETERMINISTIC", "CHEAP_AI", "STRONG_AI", "HUMAN")


def safe_select(table: str, cols: list[str], where: str = "") -> str:
    bad = [c for c in cols if c not in ALLOWED[table]]
    if bad:
        raise ValueError(f"colunas fora da lista permitida em {table}: {bad}")
    return f"SELECT {', '.join(cols)} FROM {table}" + (f" WHERE {where}" if where else "")


def pseudonym(value: str) -> str:
    return hashlib.sha256(("jev-pilot|" + value).encode()).hexdigest()[:12]


def load_risk_map(root: Path) -> dict[str, str]:
    """capability.key → risk, lido dos catálogos YAML do repositório (`- key: X` … `risk: Y`)."""
    out: dict[str, str] = {}
    for cat in (root / "backend" / "app" / "conhecimento" / "apps").glob("*/catalogo.yaml"):
        key = None
        for line in cat.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*-\s*key:\s*([A-Z0-9_]+)\s*$", line)
            if m:
                key = m.group(1)
                continue
            m = re.match(r"\s*risk:\s*(low|medium|high)\s*$", line)
            if m and key:
                out.setdefault(key, m.group(1))
    return out


def extract(db_path: Path, risk: dict[str, str]) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        steps = con.execute(safe_select("steps", ALLOWED["steps"], f"status IN ({','.join('?' * len(FINAL))})"), FINAL).fetchall()
        calls: dict[str, list[sqlite3.Row]] = defaultdict(list)
        has_fallback = "fallback" in {r[1] for r in con.execute("PRAGMA table_info(ai_calls)")}
        cols = [c for c in ALLOWED["ai_calls"] if has_fallback or c != "fallback"]
        for r in con.execute(safe_select("ai_calls", cols, "step_id IS NOT NULL")):
            calls[r["step_id"]].append(r)
    finally:
        con.close()
    cases = []
    for s in steps:
        cs = calls.get(s["id"], [])
        dv = [c for c in cs if c["role"] in ("decide", "verify")]
        cap = s["capability"]
        cases.append({
            "case": pseudonym(s["id"]), "side_effect": bool(s["side_effect"]), "capability": cap,
            "risk": risk.get(cap, "unknown") if cap else "no_capability", "driven_by": s["driven_by"],
            "final_status": s["status"], "attempts": s["attempts"], "strategy": s["strategy"],
            "failure_kind": s["failure_kind"], "app_id": s["app_id"],
            "n_decide_t0": sum(1 for c in dv if c["role"] == "decide" and c["tier"] == 0),
            "n_decide_t1": sum(1 for c in dv if c["role"] == "decide" and c["tier"] == 1),
            "n_verify": sum(1 for c in dv if c["role"] == "verify"),
            "models": sorted({c["model"] for c in cs if c["model"]}),
            "providers": sorted({c["provider"] for c in cs if c["provider"]}),
            "with_image": any(c["with_image"] for c in cs), "calls_ok": all(c["ok"] for c in cs) if cs else None,
            "usd": sum((c["usd"] or 0.0) for c in cs), "ai_ms": sum(c["ms"] or 0 for c in cs),
        })
    return cases


def derive_label(c: dict) -> str:
    """Rótulo DERIVADO DO DESFECHO (não é verdade absoluta)."""
    if c["final_status"] == "waiting_user":
        return "HUMAN"
    if c["final_status"] != "succeeded":
        return "UNLABELED"
    n_model = c["n_decide_t0"] + c["n_decide_t1"] + c["n_verify"]
    if n_model == 0:
        return "DETERMINISTIC"
    if c["n_decide_t1"] > 0:
        # Retentativa escalada que resolveu = contrafactual (a barata falhou antes). Sem retentativa = política.
        return "STRONG_AI" if c["attempts"] and c["attempts"] > 1 else "STRONG_AI_UNPROVEN"
    return "CHEAP_AI"


# --------------------------------------------------------------------------- roteadores (só features pré-execução)
Router = Callable[[dict], tuple[str, float]]


def features_for_routing(c: dict) -> dict:
    """O que existiria ANTES da decisão: efeito, risco, capability, app e se há receita (proxy vazado)."""
    return {"side_effect": c["side_effect"], "risk": c["risk"], "capability": c["capability"] or "none",
            "recipe_exists": bool(c["driven_by"] and "recipe" in c["driven_by"]), "app": c["app_id"] or "none"}


def always_cheap(_: dict) -> tuple[str, float]:
    return "CHEAP_AI", 1.0


def rule_router(c: dict) -> tuple[str, float]:
    """Espelho simplificado da política de hoje (`side_effect_tier`, modo by_risk): efeito de risco alto ou
    desconhecido → forte; o resto → barato."""
    f = features_for_routing(c)
    if f["recipe_exists"]:
        return "DETERMINISTIC", 1.0
    if f["side_effect"] and f["risk"] in ("high", "unknown", "no_capability"):
        return "STRONG_AI", 1.0
    return "CHEAP_AI", 1.0


class JevRouter:
    """Roteador por Choice fechado. Baixa confiança ⇒ sobe um degrau (nunca desce). NÃO é usado em produção."""

    def __init__(self, provider: DecisionProvider, floor: float = 0.5) -> None:
        self.provider, self.floor = provider, floor
        self.failures: Counter[str] = Counter()

    def __call__(self, c: dict) -> tuple[str, float]:
        f = features_for_routing(c)
        state = {"step_features": f}
        crit = {r: d for r, d in (
            ("CHEAP_AI", "A small fast model can do it"), ("STRONG_AI", "Needs the strongest model"),
            ("HUMAN", "A person must decide"))}
        try:
            res = self.provider.choose(state, "Which handler should take this automation step? Use `step_features`.", crit)
        except ProviderError as exc:
            self.failures[exc.kind] += 1
            return "STRONG_AI", 0.0                      # falha ⇒ degrau mais seguro
        if res.confidence < self.floor:
            return {"CHEAP_AI": "STRONG_AI"}.get(res.choice, res.choice), res.confidence
        return res.choice, res.confidence


def evaluate(cases: list[dict], router: Router) -> dict:
    """Só etapas fora do DETERMINISTIC e com rótulo ∈ {CHEAP_AI, STRONG_AI, HUMAN}; UNPROVEN/UNLABELED ficam de fora."""
    pool = [c for c in cases if derive_label(c) in ("CHEAP_AI", "STRONG_AI", "HUMAN")]
    n = len(pool)
    agree = unsafe = 0
    savings_ok = savings_total = 0
    hi = []
    for c in pool:
        label = derive_label(c)
        route, conf = router(c)
        agree += route == label
        if label in ("STRONG_AI", "HUMAN") and route in ("DETERMINISTIC", "CHEAP_AI"):
            unsafe += 1
        if label == "CHEAP_AI":
            savings_total += 1
            savings_ok += route == "CHEAP_AI"
        if conf >= 0.8:
            hi.append(route == label)
    return {"n_pool": n, "agreement": agree / n if n else float("nan"),
            "unsafe_under_escalation_rate": unsafe / n if n else float("nan"),
            "cheap_kept_cheap_rate": savings_ok / savings_total if savings_total else float("nan"),
            "high_conf_n": len(hi), "high_conf_accuracy": (sum(hi) / len(hi)) if hi else float("nan")}


def summarize(cases: list[dict]) -> dict:
    labels = Counter(derive_label(c) for c in cases)
    return {"cases_total": len(cases), "labels": dict(labels),
            "by_status": dict(Counter(c["final_status"] for c in cases)),
            "side_effect": dict(Counter(c["side_effect"] for c in cases)),
            "risk": dict(Counter(c["risk"] for c in cases)),
            "providers": dict(Counter(p for c in cases for p in c["providers"])),
            "eval_always_cheap": evaluate(cases, always_cheap), "eval_rule_router": evaluate(cases, rule_router)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="caminho do SQLite (aberto SOMENTE LEITURA)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    root = repo_root()
    with no_network():
        cases = extract(Path(args.db), load_risk_map(root))
        summary = summarize(cases)
    out = Path(args.out) if args.out else root / "data" / "jev-pilot" / "replay"
    out.mkdir(parents=True, exist_ok=True)
    (out / "cases.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
