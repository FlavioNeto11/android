"""Visão POR APLICATIVO (item 12.2): o que cada app tem de contas, aparelhos, execuções, custo de IA e conhecimento.

Antes, o menu Aplicativos era a página de versões de APK: nada dizia quantas execuções um app teve, quanto de IA
elas custaram, quais perfis têm conta nele ou quanto do trabalho já roda por receita. Tudo isso já existia no banco,
espalhado por tabelas com a chave do app em formatos diferentes (`apps.id`, `package`, `runs.plan` em JSON); este
módulo junta num lugar só, sem tabela nova.

Atribuição de uma execução a um app: `runs.app_ids` (migração 037 — o app do plano e o de cada etapa). Execuções
anteriores a ela têm só o plano em JSON; `preencher_apps_das_execucoes` completa a coluna uma vez, a partir dele.
Custo de IA: a chamada vai para o app da ETAPA (`steps.app_id`), senão para o app principal da execução.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .db import dumps, loads
from .planning import costs
from .planning.catalog import capabilities_of, session_provider_of
from .util import iso_in


def preencher_apps_das_execucoes(db: Any, limite: int = 5000) -> int:
    """Completa `runs.app_ids` das execuções antigas a partir do plano. Idempotente; devolve quantas mudou."""
    feitos = 0
    for r in db.query("SELECT id, plan FROM runs WHERE app_ids IS NULL AND plan IS NOT NULL LIMIT ?", (limite,)):
        try:
            plano = json.loads(r["plan"])
        except (TypeError, ValueError):
            continue
        apps = [a for a in dict.fromkeys([plano.get("app_id"), *(s.get("app_id") for s in plano.get("steps") or [])]) if a]
        db.execute("UPDATE runs SET app_ids=? WHERE id=?", (dumps(apps), r["id"]))
        feitos += 1
    return feitos


def _custo_por_app(db: Any, prices: dict[str, list[float]], desde: str) -> dict[str, float]:
    apps_da_execucao = {r["id"]: (loads(r["app_ids"], []) or [None])[0]
                        for r in db.query("SELECT id, app_ids FROM runs WHERE created_at >= ?", (desde,))}
    app_da_etapa = {r["id"]: r["app_id"] for r in db.query(
        "SELECT s.id, s.app_id FROM steps s JOIN runs r ON r.id = s.run_id WHERE r.created_at >= ? AND s.app_id IS NOT NULL",
        (desde,))}
    total: dict[str, float] = defaultdict(float)
    for c in db.query("SELECT run_id, step_id, model, input_tokens, cache_read, cache_write, output_tokens"
                      " FROM ai_calls WHERE ts >= ?", (desde,)):
        app = app_da_etapa.get(c["step_id"]) or apps_da_execucao.get(c["run_id"])
        if app:
            total[app] += costs.row_usd(prices, c)
    return total


def _execucoes_do_app(db: Any, app_id: str, desde: str | None = None, limite: int | None = None) -> list[Any]:
    sql = "SELECT * FROM runs WHERE app_ids LIKE ?" + (" AND created_at >= ?" if desde else "") + " ORDER BY created_at DESC"
    args: list[Any] = [f'%"{app_id}"%'] + ([desde] if desde else [])
    if limite:
        sql += " LIMIT ?"
        args.append(limite)
    return db.query(sql, tuple(args))


def apps_overview(state: Any, days: int = 7) -> list[dict[str, Any]]:
    db = state.db
    preencher_apps_das_execucoes(db)
    desde = iso_in(-days * 86400)
    custo = _custo_por_app(db, state.cfg.file.ai.prices, desde)
    saida = []
    for app in db.query("SELECT id, name, package FROM apps ORDER BY name"):
        pkg = app["package"]
        caps = capabilities_of(pkg)
        execucoes = _execucoes_do_app(db, app["id"], desde)
        ok = sum(1 for r in execucoes if r["status"] == "completed")
        aparelhos = db.query("SELECT state, COUNT(*) AS n FROM device_app_state WHERE package_name=? GROUP BY state", (pkg,))
        receitas = db.query("SELECT status, COUNT(*) AS n FROM recipes WHERE app_package=? GROUP BY status", (pkg,))
        saida.append({
            "app_id": app["id"], "name": app["name"], "package": pkg,
            # Login automático = o app tem provedor de sessão no registro (conta gerenciada), não "é o Instagram".
            "has_catalog": bool(caps.has_catalog), "automated_login": session_provider_of(pkg) is not None,
            "accounts": db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE app_id=?", (app["id"],)) or 0,
            # Pronta = a sessão da conta NO APARELHO VINCULADO ao perfil está `session_ready` (`account_sessions`,
            # 049). `profile_accounts.session_status` era cópia congelada da 037 e não é mais lida.
            "accounts_ready": db.scalar(
                "SELECT COUNT(DISTINCT s.account_id) FROM account_sessions s"
                " JOIN profile_accounts a ON a.id = s.account_id"
                " JOIN device_profile_bindings b ON b.profile_id = a.profile_id AND b.instance_id = s.instance_id"
                " AND b.active = 1 WHERE a.app_id=? AND s.status='session_ready'", (app["id"],)) or 0,
            "devices": {r["state"]: int(r["n"]) for r in aparelhos},
            "default_on_devices": db.scalar("SELECT COUNT(*) FROM instances WHERE app_id=?", (app["id"],)) or 0,
            "runs": len(execucoes), "runs_completed": ok,
            "last_run_at": execucoes[0]["created_at"] if execucoes else None,
            "ai_usd": round(custo.get(app["id"], 0.0), 4),
            "recipes": {r["status"]: int(r["n"]) for r in receitas},
            "flows": db.scalar("SELECT COUNT(*) FROM flows WHERE app_id=?", (app["id"],)) or 0,
            "releases": db.scalar("SELECT COUNT(*) FROM app_releases WHERE package_name=?", (pkg,)) or 0,
            "days": days,
        })
    return saida


def app_detail(state: Any, app_id: str, days: int = 30) -> dict[str, Any] | None:
    db = state.db
    app = db.one("SELECT id, name, package, activity FROM apps WHERE id=?", (app_id,))
    if app is None:
        return None
    preencher_apps_das_execucoes(db)
    pkg = app["package"]
    desde = iso_in(-days * 86400)
    # A sessão de cada conta é a do aparelho vinculado ao perfil (`account_sessions`, 049), nunca a cópia da 037.
    contas = db.query("SELECT a.id, a.profile_id, p.username, a.handle, a.host, a.status,"
                      " COALESCE(s.status, 'unknown') AS session_status, s.verified_at AS session_verified_at"
                      " FROM profile_accounts a JOIN instagram_profiles p ON p.id = a.profile_id"
                      # O aparelho PRINCIPAL da persona (051): com N aparelhos, uma linha por conta, não por vínculo.
                      " LEFT JOIN device_profile_bindings b ON b.profile_id = a.profile_id AND b.active = 1"
                      " AND b.is_primary = 1"
                      " LEFT JOIN account_sessions s ON s.account_id = a.id AND s.instance_id = b.instance_id"
                      " WHERE a.app_id=? ORDER BY p.username", (app_id,))
    aparelhos = db.query("SELECT instance_id, state, observed_version_name, verified_at, drift_kind FROM device_app_state"
                         " WHERE package_name=? ORDER BY instance_id", (pkg,))
    execucoes = _execucoes_do_app(db, app_id, limite=40)
    # custo por dia (US$), para o gráfico da página do app
    por_dia: dict[str, float] = defaultdict(float)
    ids = {r["id"] for r in _execucoes_do_app(db, app_id, desde)}
    app_da_etapa = {r["id"]: r["app_id"] for r in db.query(
        "SELECT id, app_id FROM steps WHERE app_id IS NOT NULL AND run_id IN (SELECT id FROM runs WHERE created_at >= ?)",
        (desde,))}
    for c in db.query("SELECT ts, run_id, step_id, model, input_tokens, cache_read, cache_write, output_tokens"
                      " FROM ai_calls WHERE ts >= ?", (desde,)):
        dono = app_da_etapa.get(c["step_id"])
        if dono == app_id or (dono is None and c["run_id"] in ids):
            por_dia[c["ts"][:10]] += costs.row_usd(state.cfg.file.ai.prices, c)
    etapas = db.query("SELECT COALESCE(s.driven_by,'ai') AS origem, s.status, COUNT(*) AS n FROM steps s"
                      " JOIN runs r ON r.id = s.run_id WHERE r.app_ids LIKE ? AND r.created_at >= ?"
                      " AND (s.app_id IS NULL OR s.app_id = ?) GROUP BY 1, 2", (f'%"{app_id}"%', desde, app_id))
    falhas = db.query("SELECT s.title, s.status_detail, s.finished_at, s.run_id FROM steps s JOIN runs r ON r.id=s.run_id"
                      " WHERE r.app_ids LIKE ? AND s.status IN ('failed','uncertain') AND (s.app_id IS NULL OR s.app_id=?)"
                      " ORDER BY s.finished_at DESC LIMIT 12", (f'%"{app_id}"%', app_id))
    return {
        "app_id": app["id"], "name": app["name"], "package": pkg, "activity": app["activity"],
        "has_catalog": bool(capabilities_of(pkg).has_catalog),
        "automated_login": session_provider_of(pkg) is not None,
        "accounts": [dict(r) for r in contas],
        "devices": [dict(r) for r in aparelhos],
        "runs": [state.repo.run_summary(r).model_dump() for r in execucoes],
        "ai_usd_by_day": [{"day": d, "usd": round(v, 4)} for d, v in sorted(por_dia.items())],
        "steps": [dict(r) for r in etapas],
        "recent_failures": [dict(r) for r in falhas],
        "recipes": [dict(r) for r in db.query(
            "SELECT id, step_key, app_version, status, replay_ok, replay_fail, created_at, last_used_at FROM recipes"
            " WHERE app_package=? ORDER BY COALESCE(last_used_at, created_at) DESC LIMIT 30", (pkg,))],
        "flows": [dict(r) for r in db.query("SELECT id, name, command_template, uses, status FROM flows WHERE app_id=?"
                                            " ORDER BY uses DESC LIMIT 30", (app_id,))],
        "days": days,
    }
