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
from app.util import now_iso

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
                   "'android-01','succeeded',?)", (perfil.id,))
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


@pytest.mark.asyncio
async def test_estimativa_de_custo_por_fluxo(tmp_path: Path) -> None:
    """Item 7.7: etapas sem receita × custo mediano de `decide` + etapas totais × custo mediano de `verify`,
    dos últimos 7 dias, por `ai_calls`. Sem nenhuma chamada no período, a estimativa é `None` ("sem base"), não
    zero — um fluxo nunca rodado não é um fluxo grátis."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        db = s.db
        from app.models import Plan, PlannerInfo, PlanStep, Postcondition
        plano = Plan(summary="enviar oi", steps=[
            PlanStep(key="abrir", title="abrir", goal="abrir",
                     postcondition=Postcondition(kind="app_foreground", value=INSTAGRAM, description="x")),
            PlanStep(key="enviar", title="enviar", goal="enviar",
                     postcondition=Postcondition(kind="model_judged", value="oi enviado", description="x")),
        ], planner=PlannerInfo(provider="fake", model="fake", simulated=True)).model_dump()
        db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, status,"
                   " uses, created_at) VALUES ('f2','Enviar oi','enviar oi','enviar oi',?,'instagram',NULL,"
                   "'active',1,'2026-09-20T00:00:00Z')", (json.dumps(plano),))

        async with _cliente(h) as c:
            # Sem histórico de `ai_calls`: sem base para estimar.
            r = await c.get("/api/flows/cobertura")
            f2 = next(f for f in r.json() if f["flow_id"] == "f2")
            assert f2["estimated_usd"] is None
            assert (await c.post("/api/flows/match", json={"command": "não existe nenhum fluxo assim"})).json() is None

        # `claude-sonnet-5`: US$ 2/milhão de tokens de entrada (config.example.yaml) — tokens escolhidos para dar
        # custos redondos por chamada: decide 1,00 e 3,00 (mediana 2,00); verify 0,50 e 1,50 (mediana 1,00).
        # `tier` é INTEGER (0 = modelo da função; 1 = escalonado, migração 003): o SQLite aceitava o texto 'fast'
        # que estava aqui, o PostgreSQL recusa — o valor é o que `Repository.add_usage` grava de fato.
        agora = now_iso()
        for step_id, role, tokens in (("f2:s1", "decide", 500_000), ("f2:s2", "decide", 1_500_000),
                                       ("f2:s1", "verify", 250_000), ("f2:s2", "verify", 750_000)):
            db.execute("INSERT INTO ai_calls(ts, run_id, objective_id, step_id, role, model, tier, input_tokens,"
                       " cache_read, cache_write, output_tokens, with_image, ms, ok) VALUES (?,NULL,NULL,?,?,"
                       "'claude-sonnet-5',0,?,0,0,0,0,100,1)", (agora, step_id, role, tokens))

        async with _cliente(h) as c:
            r = await c.get("/api/flows/cobertura")
            f2 = next(f for f in r.json() if f["flow_id"] == "f2")
            # sem receita: 2 etapas × mediana decide (2,00) + 2 etapas × mediana verify (1,00) = 6,00
            assert f2["estimated_usd"] == pytest.approx(6.0, abs=0.01)

            # Fase G (decisão P2): `/flows/match` passa pelo MESMO registro que o planejamento, e por isso respeita
            # `ai.flows` — com os fluxos desligados (o padrão do harness), nenhuma execução usaria f2, e a rota não
            # estima um plano que não rodaria. Antes ela ignorava o interruptor.
            assert (await c.post("/api/flows/match", json={"command": "enviar oi"})).json() is None
            h.cfg.file.ai.flows = True
            r2 = await c.post("/api/flows/match", json={"command": "enviar oi"})
            assert r2.status_code == 200
            corpo = r2.json()
            assert corpo["flow_id"] == "f2"
            assert corpo["estimated_usd"] == pytest.approx(6.0, abs=0.01)
    finally:
        await h.state.stop()


def test_identidade_da_etapa_nao_depende_do_alvo_escrito_por_extenso() -> None:
    """15 receitas ativas do Instagram e cobertura zero: o planejador escreveu "@nasa" na pós-condição, o hash
    ficou preso ao alvo, e o mesmo caminho para outro perfil nunca casava. Com os valores devolvidos ao nome do
    parâmetro, "@nasa" e "@outro" são a MESMA etapa — e a receita aprendida numa serve na outra."""
    from app.models import PlanStep, Postcondition
    from app.taskqueue.recipes import para_hash, step_template_hash

    def etapa(alvo: str) -> PlanStep:
        return PlanStep(key="open_profile", title="abrir", goal="abrir o perfil",
                        postcondition=Postcondition(kind="model_judged", value=f"perfil de {alvo} aberto", description="x"),
                        commit_guard=[f"{alvo}"])

    a = step_template_hash(para_hash(etapa("@nasa"), {"perfil": "@nasa", "run_id": "r-1"}))
    b = step_template_hash(para_hash(etapa("@outro"), {"perfil": "@outro", "run_id": "r-2"}))
    assert a == b
    assert a != step_template_hash(etapa("@nasa")), "sem a troca, o hash carrega o alvo"
    # O fluxo-modelo já vem com `{perfil}`: a troca é idempotente e casa com o hash de cima.
    assert step_template_hash(para_hash(etapa("{perfil}"), {"perfil": "@nasa"})) == a
