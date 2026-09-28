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

import yaml  # noqa: E402

from app.config import AppConfigFile, Config, EnvSettings, config_file_path, get_config  # noqa: E402
from app.db import Database, loads  # noqa: E402
from app.planning import costs  # noqa: E402
from app.planning.anthropic_provider import AnthropicProvider  # noqa: E402
from app.planning.provider import AIError, AppContext, ScreenInput, StepContext, VerifyRequest, build_one  # noqa: E402
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


# ------------------------------------------------------------------ modo candidato (Fase 17, item 17.2)
#: Blocos de `ai` que o arquivo sobreposto pode trazer. O resto da configuração (banco, storage) é o da instalação.
_BLOCOS_SOBREPONIVEIS = ("providers", "models", "prices", "roles")


def _mesclar_ai(raw: dict[str, Any], sobrepor: dict[str, Any]) -> dict[str, Any]:
    """A configuração da instalação com `ai.providers/models/prices/roles` do arquivo do candidato por cima.

    É o que deixa julgar com um modelo candidato SEM escrever no `config.yaml` guardado: o candidato mora num arquivo
    à parte, e só esses quatro blocos entram (chave a chave; a do candidato vence).
    """
    novo = dict(raw)
    ai = dict(novo.get("ai") or {})
    extra = (sobrepor or {}).get("ai") or {}
    for bloco in _BLOCOS_SOBREPONIVEIS:
        if bloco in extra:
            ai[bloco] = {**(ai.get(bloco) or {}), **(extra[bloco] or {})}
    novo["ai"] = ai
    return novo


def _referencia(linhas: list[str]) -> dict[Any, bool]:
    """evidence_id → o Opus disse "yes"? Lido do jsonl da rodada de referência; linha com erro não conta, e a
    última leitura de uma captura vence (o arquivo é só de acréscimo)."""
    ref: dict[Any, bool] = {}
    for linha in linhas:
        linha = linha.strip()
        if not linha:
            continue
        try:
            rec = json.loads(linha)
        except ValueError:
            continue
        if "opus_satisfied" in rec and rec.get("evidence_id") is not None:
            ref[rec["evidence_id"]] = rec["opus_satisfied"] == "yes"
    return ref


def _placar(resultados: list[dict[str, Any]]) -> dict[str, int]:
    """Candidato × referência (Opus): concordância, falso positivo (candidato "sim" onde o Opus disse não — o erro
    que conta como sucesso o que não foi) e falso negativo."""
    julgados = [r for r in resultados if "candidato_ok" in r and "referencia_ok" in r]
    fp = sum(1 for r in julgados if r["candidato_ok"] and not r["referencia_ok"])
    fn = sum(1 for r in julgados if not r["candidato_ok"] and r["referencia_ok"])
    return {"julgados": len(julgados), "concordam": len(julgados) - fp - fn, "falso_positivo": fp,
            "falso_negativo": fn, "erros": sum(1 for r in resultados if "erro" in r)}


async def _candidato(a: argparse.Namespace) -> int:
    """Julga as MESMAS capturas da rodada de referência com o provedor do papel `verify` do arquivo sobreposto."""
    base = config_file_path()
    raw = (yaml.safe_load(base.read_text(encoding="utf-8")) or {}) if base else {}
    sobre = yaml.safe_load(Path(a.sobrepor).read_text(encoding="utf-8")) or {}
    cfg = Config(AppConfigFile.model_validate(_mesclar_ai(raw, sobre)), EnvSettings())
    papel = cfg.ai_role("verify")
    provedor = build_one(cfg, papel)
    ref_path = Path(a.referencia)
    ref = _referencia(ref_path.read_text(encoding="utf-8").splitlines()) if ref_path.exists() else {}
    if not ref:
        print(f"Sem vereditos de referência em {ref_path} — rode antes o modo padrão (Opus).")
        return 2
    db = Database(cfg.db_dsn)
    storage = build_storage(cfg.env.evidence_storage, evidence_dir=cfg.evidence_dir, bucket=cfg.env.s3_bucket,
                            endpoint_url=cfg.env.s3_endpoint_url, region=cfg.env.s3_region,
                            access_key=cfg.env.s3_access_key_id.get_secret_value() if cfg.env.s3_access_key_id else None,
                            secret_key=cfg.env.s3_secret_access_key.get_secret_value() if cfg.env.s3_secret_access_key else None)
    linhas = [r for r in db.query(QUERY, (a.since,)) if r["evidence_id"] in ref]
    if a.limit:
        linhas = linhas[:a.limit]
    print(f"Candidato: papel verify → {papel.provider}/{papel.model} ({papel.endpoint}); "
          f"{len(linhas)} captura(s) com veredito de referência.")
    if not a.yes:
        print("Rode de novo com --yes para confirmar o gasto (uma chamada de imagem por captura).")
        return 2
    out_path = Path(a.out if a.out != a.referencia else str(ROOT / "data" / "eval-rejudge-candidato.jsonl"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    resultados: list[dict[str, Any]] = []
    tokens = [0.0, 0.0, 0.0, 0.0]
    with out_path.open("a", encoding="utf-8") as fh:
        for row in linhas:
            jpeg = storage.get(row["path"])
            if jpeg is None:
                continue
            screen = ScreenInput(width=1080, height=1920, jpeg=jpeg, elements=[], package=row["app_package"],
                                 sensitive=False)
            base_rec = {"evidence_id": row["evidence_id"], "step_id": row["step_id"], "modelo": papel.model,
                        "provedor": papel.provider, "referencia_ok": ref[row["evidence_id"]],
                        "haiku_ok": _haiku_ok(row["note"])}
            try:
                veredito, usage = await provedor.verify(VerifyRequest(ctx=_ctx_de(dict(row)), screen=screen))
                fresco = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
                for i, v in enumerate((fresco, usage.cache_read_tokens, usage.cache_write_tokens,
                                       usage.output_tokens)):
                    tokens[i] += v
                rec = {**base_rec, "candidato_satisfied": veredito.satisfied,
                       "candidato_ok": veredito.satisfied == "yes", "evidencia": veredito.evidence,
                       "tokens_in": usage.input_tokens, "cache_read": usage.cache_read_tokens,
                       "tokens_out": usage.output_tokens, "ms": usage.ms}
                marca = "CONCORDA" if rec["candidato_ok"] == rec["referencia_ok"] else "*** DIVERGE ***"
                print(f"- {row['evidence_id']} ({row['step_id']}): opus={'ok' if rec['referencia_ok'] else 'NAO'} "
                      f"candidato={veredito.satisfied} {marca} {usage.ms}ms")
            except AIError as exc:
                rec = {**base_rec, "erro": str(exc), "erro_kind": exc.kind}
                print(f"- {row['evidence_id']}: ERRO ({exc.kind}): {exc}")
            resultados.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    p = _placar(resultados)
    haiku = [r for r in resultados if "candidato_ok" in r]
    haiku_concorda = sum(1 for r in haiku if r["haiku_ok"] == r["referencia_ok"])
    usd = costs.usd(cfg.file.ai.prices, papel.model, tokens)
    ms = sorted(r["ms"] for r in resultados if "ms" in r)
    p50 = ms[len(ms) // 2] if ms else 0
    print(f"\n## {papel.model} × Opus — {p['concordam']}/{p['julgados']} concordam; falso positivo {p['falso_positivo']}, "
          f"falso negativo {p['falso_negativo']}, erros {p['erros']}")
    print(f"Haiku × Opus nas mesmas capturas: {haiku_concorda}/{len(haiku)}")
    print(f"Custo pelo preço declarado em ai.prices: US$ {usd:.4f} ({usd / max(p['julgados'], 1):.5f} por captura); "
          f"p50 {p50} ms. Resultado em {out_path}.")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-19T21:42:00Z",
                    help="ISO-8601: início da produção em Haiku 4.5 (padrão: a troca real, achado #98)")
    ap.add_argument("--limit", type=int, default=0, help="0 = todas as capturas encontradas")
    ap.add_argument("--modelo", default=MODELO_CARO)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "eval-rejudge.jsonl"))
    ap.add_argument("--sobrepor", default="",
                    help="YAML com ai.providers/models/prices/roles do CANDIDATO (papel verify); liga o modo candidato")
    ap.add_argument("--referencia", default=str(ROOT / "data" / "eval-rejudge.jsonl"),
                    help="jsonl com os vereditos do Opus usados como referência no modo candidato")
    a = ap.parse_args()
    if a.sobrepor:
        return await _candidato(a)

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
