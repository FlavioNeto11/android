"""Achado #98/#99, item 3: rejulga com o modelo CARO (Opus 5), por imagem, as capturas de verificação que o
verificador barato (Haiku 4.5) já julgou desde a troca — mede a CONCORDÂNCIA dos veredictos, sem aparelho e sem
nenhum efeito externo (só lê `evidence` do banco e reenvia a mesma imagem para julgar de novo).

Não é a bateria de avaliação (scripts/eval_run.py): não roda comando nenhum, não toca o parque, não fala com o
backend em HTTP. Só lê o banco (read-only) e chama a API da Anthropic — GASTA tokens de verdade (uma imagem por
capítulo julgado; poucos centavos por dezena, ~US$0,03-0,05/imagem em Opus 5).

Uso:  backend\\.venv\\Scripts\\python.exe scripts\\eval_rejudge.py --since 2026-09-19T21:42:00Z --yes
Saída: data/eval-rejudge.jsonl (uma linha por captura) + tabela de concordância no stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_config  # noqa: E402
from app.db import Database, loads  # noqa: E402
from app.planning.anthropic_provider import AnthropicProvider  # noqa: E402
from app.planning.provider import AIError, AppContext, ScreenInput, StepContext, VerifyRequest  # noqa: E402
from app.storage import build_storage  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MODELO_CARO = "claude-opus-5"

QUERY = """
SELECT e.id evidence_id, e.run_id, e.instance_id, e.step_id, e.ts, e.note, e.path,
       s.title, s.goal, s.postcondition,
       i.account_label,
       a.id app_id, a.name app_name, a.package app_package, a.activity app_activity,
       a.nav_hints app_nav_hints, a.known_selectors app_selectors
FROM evidence e
JOIN steps s ON s.id = e.step_id
LEFT JOIN instances i ON i.id = e.instance_id
LEFT JOIN apps a ON a.id = i.app_id
WHERE e.note LIKE 'Pós-condição%' AND e.path IS NOT NULL AND e.redacted = 0 AND e.ts >= ?
ORDER BY e.ts
"""


def _haiku_ok(note: str) -> bool:
    """A mesma regra que gravou a nota (executor.py): 'Pós-condição comprovada: …' × 'NÃO comprovada: …'."""
    return "NÃO comprovada" not in note


def _ctx_de(row: dict[str, Any]) -> StepContext:
    post = loads(row["postcondition"], {}) or {}
    selectors = loads(row["app_selectors"], {}) or {} if row["app_selectors"] else {}
    app = AppContext(row["app_id"], row["app_name"], row["app_package"], row["app_activity"],
                     row["app_nav_hints"], selectors)
    return StepContext(run_id=row["run_id"], instance_id=row["instance_id"],
                       objective_summary=row["goal"] or row["title"], parameters={},
                       step_key=row["step_id"].rsplit(":", 1)[-1], step_title=row["title"], step_goal=row["goal"],
                       side_effect=True, commit_done=True, commit_guard=[], precondition=None,
                       postcondition_description=post.get("description") or post.get("value") or row["note"],
                       remaining_steps=[], app=app, account_label=row["account_label"],
                       required_delivery_level=post.get("required_delivery_level"))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-19T21:42:00Z",
                    help="ISO-8601: início da produção em Haiku 4.5 (padrão: a troca real, achado #98)")
    ap.add_argument("--limit", type=int, default=0, help="0 = todas as capturas encontradas")
    ap.add_argument("--modelo", default=MODELO_CARO)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "eval-rejudge.jsonl"))
    a = ap.parse_args()

    cfg = get_config()
    db = Database(cfg.db_dsn)
    storage = build_storage(cfg.env.evidence_storage, evidence_dir=cfg.evidence_dir, bucket=cfg.env.s3_bucket,
                            endpoint_url=cfg.env.s3_endpoint_url, region=cfg.env.s3_region,
                            access_key=cfg.env.s3_access_key_id.get_secret_value() if cfg.env.s3_access_key_id else None,
                            secret_key=cfg.env.s3_secret_access_key.get_secret_value() if cfg.env.s3_secret_access_key else None)
    linhas = db.query(QUERY, (a.since,))
    if a.limit:
        linhas = linhas[:a.limit]
    if not linhas:
        print(f"Nenhuma captura de verificação encontrada desde {a.since} (evidence.note LIKE 'Pós-condição%').")
        return 0

    opus = AnthropicProvider(cfg)
    if not opus.configured:
        print("ANTHROPIC_API_KEY ausente no .env — nada para rejulgar.")
        return 2
    opus.models = {k: a.modelo for k in opus.models}

    if not a.yes:
        print(f"{len(linhas)} captura(s) desde {a.since} seriam rejulgadas em {a.modelo} (uma chamada de imagem "
              f"cada, ~US$ 0,03-0,05/captura em Opus 5 — referência medida, não garantida). "
              "Rode de novo com --yes para confirmar o gasto.")
        return 2

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    resultados: list[dict[str, Any]] = []
    with out_path.open("a", encoding="utf-8") as fh:
        for row in linhas:
            jpeg = storage.get(row["path"])
            if jpeg is None:
                print(f"- {row['evidence_id']}: arquivo ausente no storage ({row['path']}) — pulado")
                continue
            haiku_ok = _haiku_ok(row["note"])
            ctx = _ctx_de(dict(row))
            screen = ScreenInput(width=1080, height=1920, jpeg=jpeg, elements=[], package=row["app_package"],
                                 sensitive=False)
            try:
                veredito, usage = await opus.verify(VerifyRequest(ctx=ctx, screen=screen))
                opus_ok = veredito.satisfied == "yes"
                concorda = opus_ok == haiku_ok
                rec = {"evidence_id": row["evidence_id"], "run_id": row["run_id"], "step_id": row["step_id"],
                      "ts": row["ts"], "haiku_nota": row["note"], "haiku_ok": haiku_ok,
                      "opus_satisfied": veredito.satisfied, "opus_evidence": veredito.evidence,
                      "opus_delivery_level": veredito.delivery_level, "concorda": concorda,
                      "tokens_in": usage.input_tokens, "tokens_out": usage.output_tokens}
                print(f"- {row['evidence_id']} ({row['step_id']}): haiku={'ok' if haiku_ok else 'NAO'} "
                      f"opus={veredito.satisfied} {'CONCORDA' if concorda else '*** DIVERGE ***'}")
            except AIError as exc:
                rec = {"evidence_id": row["evidence_id"], "run_id": row["run_id"], "step_id": row["step_id"],
                      "ts": row["ts"], "haiku_nota": row["note"], "haiku_ok": haiku_ok, "erro": str(exc),
                      "erro_kind": exc.kind}
                print(f"- {row['evidence_id']}: ERRO ao rejulgar ({exc.kind}): {exc}")
            resultados.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    julgados = [r for r in resultados if "concorda" in r]
    divergentes = [r for r in julgados if not r["concorda"]]
    print(f"\n## Concordância Opus × Haiku — {len(julgados)} captura(s) julgadas, "
          f"{len(julgados) - len(divergentes)}/{len(julgados)} concordam "
          f"({100 * (len(julgados) - len(divergentes)) / len(julgados):.0f}%)" if julgados else "\nNenhuma captura julgada com sucesso.")
    if divergentes:
        print("\nDivergências (Haiku × Opus):")
        for r in divergentes:
            print(f"  {r['evidence_id']} ({r['step_id']}): haiku={'ok' if r['haiku_ok'] else 'NAO'} "
                  f"opus={r['opus_satisfied']} — {r['opus_evidence']}")
    custo_tokens = sum(r.get("tokens_in", 0) for r in julgados)
    print(f"\nResultado completo em {out_path}. Tokens de entrada somados: {custo_tokens}.")
    return 1 if divergentes else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
