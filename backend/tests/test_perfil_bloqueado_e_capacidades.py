"""Item 4: conta bloqueada pela plataforma não recebe tarefa. Item 6: o que a persona já fez e o que roda sem IA.

O status `blocked` existia no banco desde a migração 008 e nada o lia: em 23/09/2026 cinco dos oito perfis
estavam bloqueados pelo Instagram e continuavam elegíveis para despacho. O bloqueio é respeitado, não contornado.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.main import create_app
from app.models import ProfileCreate, ProfilePatch

from .conftest import Harness

INSTAGRAM = "com.instagram.android"


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)), base_url="http://127.0.0.1")


@pytest.mark.asyncio
async def test_perfil_bloqueado_nao_passa_pela_porta_de_sessao(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        rt = s.devices.get("android-01")
        perfil = s.social.create_profile(ProfileCreate(username="conta.teste", instance_id="android-01"))
        s.social.update_profile(perfil.id, ProfilePatch(status="blocked"))
        recusa = s._session_gate(rt, INSTAGRAM)
        assert recusa is not None and recusa[1] is None, "bloqueado = depende de pessoa, não de autenticação"
        assert "blocked" in recusa[0] and "conta.teste" in recusa[0]
        # Reativado por uma pessoa: a porta volta a decidir pela sessão, como antes (não é mais a recusa de status).
        s.social.update_profile(perfil.id, ProfilePatch(status="active"))
        de_novo = s._session_gate(rt, INSTAGRAM)
        assert de_novo is None or "blocked" not in de_novo[0]
        # Outro app no mesmo aparelho não é afetado: a porta é por app (QA Messenger segue igual).
        assert s._session_gate(rt, "com.pocqa.messenger") is None
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_patch_aceita_blocked_pelo_contrato_http(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        perfil = h.state.social.create_profile(ProfileCreate(username="conta.http"))
        async with _cliente(h) as c:
            r = await c.patch(f"/api/instagram/profiles/{perfil.id}", json={"status": "blocked"})
            assert r.status_code == 200 and r.json()["status"] == "blocked"
            r = await c.patch(f"/api/instagram/profiles/{perfil.id}", json={"status": "banido"})
            assert r.status_code == 422
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_capacidades_do_perfil_juntam_fluxos_etapas_e_interacoes(tmp_path: Path) -> None:
    """A visão por persona nasce das tabelas que já existiam (objectives.profile_id → runs.flow_id → flows,
    steps.driven_by, social_interactions). Nada novo é gravado; só se lê."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        perfil = s.social.create_profile(ProfileCreate(username="conta.cap", instance_id="android-01"))
        db = s.db
        # Plano-modelo válido de verdade (o leitor de cobertura usa `Plan.model_validate_json`; um JSON torto
        # renderia cobertura "desconhecida" e o teste passaria sem provar nada).
        from app.models import Plan, PlannerInfo, PlanStep, Postcondition
        plano = Plan(summary="abrir", steps=[PlanStep(
            key="abrir", title="abrir", goal="abrir o app",
            postcondition=Postcondition(kind="app_foreground", value=INSTAGRAM, description="app na frente"))],
            planner=PlannerInfo(provider="fake", model="fake", simulated=True)).model_dump()
        db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, status, uses,"
                   " created_at) VALUES ('f1','Abrir','abrir','abrir o app',?,'instagram',NULL,'active',2,'2026-09-20T00:00:00Z')",
                   (json.dumps(plano),))
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, flow_id)"
                   " VALUES ('r1','k-r1','abrir o app','execute','completed','[\"android-01\"]','2026-09-21T00:00:00Z','f1')")
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, profile_id) VALUES ('r1:android-01','r1',"
                   "'android-01','completed',?)", (perfil.id,))
        for i, origem in enumerate(("recipe", "recipe", "ai")):
            db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                       " postcondition, timeout_s, max_attempts, status, driven_by) VALUES (?,?,?,?,1,?,?,?,?,?,30,2,"
                       "'succeeded',?)", (f"r1:android-01:v1:k{i}", "r1", "r1:android-01", "android-01", i, f"k{i}",
                                         "t", "g", '{"kind":"text","value":"x"}', origem))
        db.execute("INSERT INTO social_interactions(id, profile_id, instance_id, run_id, occurred_at, type, direction,"
                   " counterparty, status, created_at, updated_at) VALUES ('i1',?,'android-01','r1',"
                   "'2026-09-21T00:00:00Z','dm_sent','outbound','@alguem','confirmed','2026-09-21T00:00:00Z',"
                   "'2026-09-21T00:00:00Z')", (perfil.id,))
        async with _cliente(h) as c:
            r = await c.get(f"/api/instagram/profiles/{perfil.id}/capacidades")
            assert r.status_code == 200, r.text
            corpo = r.json()
        assert [f["flow_id"] for f in corpo["flows"]] == ["f1"]
        assert corpo["flows"][0]["times"] == 1 and corpo["flows"][0]["steps_total"] == 1
        assert corpo["flows"][0]["ai_cost"] in ("total", "zero", "parcial", "desconhecido")
        assert corpo["steps_driven_by"] == {"recipe": 2, "ai": 1}
        assert corpo["recipe_share"] == pytest.approx(2 / 3, abs=0.01)
        assert corpo["interactions"] == {"dm_sent": 1}
        async with _cliente(h) as c:
            r = await c.get("/api/flows/cobertura")
            assert r.status_code == 200 and r.json()[0]["flow_id"] == "f1"
            assert (await c.get("/api/instagram/profiles/nao-existe/capacidades")).status_code == 404
    finally:
        await h.state.stop()
