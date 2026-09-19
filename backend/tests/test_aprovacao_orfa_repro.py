"""REPRO (investigação): retomar um item bloqueado por aprovação deixa a aprovação antiga pendente.

Não é para ficar no repositório como está: é a prova do caminho descrito na revisão.
"""
from __future__ import annotations

import json
from typing import Any

from app.models import ProfileCreate, ResolveBody

SENHA = "$a=B7ee1#<b-C?S-{"
IG = "com.instagram.android"

PLANO = {
    "summary": "mandar mensagem",
    "app_id": "ig",
    "app_package": IG,
    "parameters": {},
    "success_criteria": [],
    "planner": {"provider": "simulado", "model": "simulado", "simulated": True},
    "steps": [{
        "key": "send_1",
        "title": "Enviar a mensagem para @ana",
        "goal": "enviar",
        "depends_on": [],
        "side_effect": True,
        "commit_guard": ["@ana"],
        "postcondition": {"kind": "model_judged", "value": "x", "description": "y"},
        "timeout_s": 180,
        "max_attempts": 1,
        "capability": "SEND_MESSAGE",
        "commit_selector": "desc=Send",
        "bindings": {"username": "@ana", "content": "bom dia", "content_verbatim": "true"},
    }],
}


async def test_retomar_item_bloqueado_por_aprovacao_deixa_aprovacao_orfa(harness: Any) -> None:
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username="mariana.costa91182", password=SENHA,
                                                    instance_id="android-01")).id
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan, pause_requested) VALUES ('run-x','kx','responda','execute','running',1,'[\"android-01\"]',"
               "'2026-09-17T10:00:00Z',?,1)", (json.dumps(PLANO),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES ('run-x:android-01','run-x','android-01','running',1,'{}',?)", (pid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-x:android-01:v1:send_1','run-x','run-x:android-01','android-01',1,1,'send_1',"
        "'Enviar a mensagem para @ana','enviar','[]',1,'[\"@ana\"]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','SEND_MESSAGE','desc=Send',"
        "'{\"username\": \"@ana\", \"content\": \"bom dia\", \"content_verbatim\": \"true\"}')")

    obj = db.one("SELECT * FROM objectives WHERE id='run-x:android-01'")
    srow = db.one("SELECT * FROM steps WHERE id='run-x:android-01:v1:send_1'")
    run = db.one("SELECT * FROM runs WHERE id='run-x'")

    # 1) a porta abre A1 e bloqueia o item
    veredito = await state._policy_gate(obj, srow, run)
    assert veredito is not None and not veredito.allowed
    a1 = state.approval_service.list()
    assert len(a1) == 1

    # 2) é assim que o item fica na tela: `_hold` → `_block` (waiting_user) e blocked_kind sobrescrito por 'policy'
    db.execute("UPDATE objectives SET status='waiting_user' WHERE id='run-x:android-01'")
    assert db.one("SELECT blocked_kind FROM objectives WHERE id='run-x:android-01'")["blocked_kind"] == "approval"

    # 3) o operador clica "repetir" — caminho de produção, sem atalho
    state.runs.resolve("run-x", "run-x:android-01", ResolveBody(resolution="retry"))
    v1 = db.one("SELECT status FROM steps WHERE id='run-x:android-01:v1:send_1'")
    assert v1["status"] == "skipped"
    v2 = db.one("SELECT * FROM steps WHERE id='run-x:android-01:v2:send_1'")
    assert v2 is not None

    # 4) a porta é atravessada de novo pela etapa v2 — e abre uma SEGUNDA aprovação
    obj = db.one("SELECT * FROM objectives WHERE id='run-x:android-01'")
    await state._policy_gate(obj, v2, run)
    pendentes = state.approval_service.list()
    assert len(pendentes) == 2, f"esperava dois cartões pendentes, veio {len(pendentes)}"
    assert {(p["capability"], p["target"]) for p in pendentes} == {("SEND_MESSAGE", "@ana")}

    # 5) editar o cartão velho escreve numa etapa morta
    antiga = next(p for p in pendentes if p["id"] == a1[0]["id"])
    state.approval_service.decide(antiga["id"], "edit", content="T3 — o texto que a pessoa combinou")
    morta = db.one("SELECT status, bindings FROM steps WHERE id='run-x:android-01:v1:send_1'")
    viva = db.one("SELECT status, bindings FROM steps WHERE id='run-x:android-01:v2:send_1'")
    assert morta["status"] == "skipped"
    assert json.loads(morta["bindings"])["content"] == "T3 — o texto que a pessoa combinou"
    assert json.loads(viva["bindings"])["content"] == "bom dia"      # o que vai ser digitado não mudou
    assert len(state.approval_service.list()) == 1                   # A2 segue pendente
