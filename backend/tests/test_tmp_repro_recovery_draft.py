from __future__ import annotations

import json
from typing import Any

from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.social.approvals import definir_texto, guardar_rascunho

IG = "com.instagram.android"


async def test_repro_recuperacao_descarta_rascunho_e_aprovacao(harness: Any) -> None:
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")

    plano = Plan(summary="comentar", app_id="ig", app_package=IG,
                 planner=PlannerInfo(provider="simulated", model="x", simulated=True),
                 steps=[PlanStep(key="c1", title="Comentar", goal="comentar", side_effect=True,
                                 capability="CREATE_COMMENT", commit_selector="id=post",
                                 postcondition=Postcondition(kind="model_judged", value="x", description="y"),
                                 bindings={"content_brief": "elogiar o post"})])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at, plan)"
               " VALUES ('run-r','kr','comentar','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z',?)",
               (plano.model_dump_json(),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-r:android-01','run-r','android-01','running',1,'{}')")
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-r:android-01:v1:c1','run-r','run-r:android-01','android-01',1,1,'c1','Comentar',"
        "'comentar','[]',1,'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'failed',"
        "'CREATE_COMMENT','id=post','{\"content_brief\": \"elogiar o post\"}')")

    v1 = "run-r:android-01:v1:c1"
    definir_texto(db, v1, "Que trabalho bonito, parabens.")
    guardar_rascunho(db, v1, {"memory_candidates": [], "rationale": "porque sim", "screen_seen": "", "incoming": ""})
    pedido = state.approvals.open(profile_id=None, capability="CREATE_COMMENT", summary="Comentar",
                                  target="@x", content="Que trabalho bonito, parabens.",
                                  run_id="run-r", objective_id="run-r:android-01", step_id=v1)
    state.approvals.decide(pedido.id, status="approved")

    obj = db.one("SELECT * FROM objectives WHERE id='run-r:android-01'")
    step = state.repo.step_dto(db.one("SELECT * FROM steps WHERE id=?", (v1,)))
    assert state.scheduler._try_recover(obj, step, "elemento nao encontrado") is True

    v2 = db.one("SELECT * FROM steps WHERE id='run-r:android-01:v2:c1'")
    assert v2 is not None, "a v2 nem foi criada"
    print("v2 bindings  :", v2["bindings"])
    print("v2 draft_meta:", v2["draft_meta"])
    print("v2 guard     :", v2["commit_guard"])
    print("for_step(v2) :", state.approvals.for_step("run-r:android-01:v2:c1"))
    print("aprovacao v1 :", state.approvals.for_step(v1).status, state.approvals.for_step(v1).interaction_id)

    assert (json.loads(v2["bindings"]) or {}).get("content") is None
    assert v2["draft_meta"] is None
    assert state.approvals.for_step("run-r:android-01:v2:c1") is None
