from __future__ import annotations

from typing import Any

import pytest

from app.models import ProfileCreate, ResolveBody
from app.taskqueue.service import RunError

pytestmark = pytest.mark.anyio

IG = "com.instagram.android"
SENHA = "Senha#Forte123"


async def test_repro_blocked_kind_aprovacao_sobrescrito(harness: Any) -> None:
    state = harness.state
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username="mariana.costa91182", password=SENHA,
                                                    instance_id="android-01")).id
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-x','kx','responda','execute','running',1,'[\"android-01\"]','2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES ('run-x:android-01','run-x','android-01','running',1,'{}',?)", (pid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES ('run-x:android-01:v1:send_1','run-x','run-x:android-01','android-01',1,1,'send_1',"
        "'Enviar a mensagem para @ana','enviar','[]',1,'[\"@ana\"]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','SEND_MESSAGE','desc=Send',"
        "'{\"username\": \"@ana\", \"content\": \"bom dia\", \"content_verbatim\": \"true\"}')")

    rt = state.devices.get("android-01")
    # caminho de produção: o worker do scheduler
    await state.scheduler._work("run-x:android-01", rt)

    row = db.one("SELECT status, blocked_kind FROM objectives WHERE id='run-x:android-01'")
    srow = db.one("SELECT status FROM steps WHERE id='run-x:android-01:v1:send_1'")
    print("APROVACOES:", state.approval_service.list())
    print("OBJETIVO:", dict(row))
    print("ETAPA:", dict(srow))

    with pytest.raises(RunError) as exc:
        state.runs.resolve("run-x", "run-x:android-01", ResolveBody(resolution="confirm_done"))
    print("RESOLVE:", exc.value.code, exc.value.message)
