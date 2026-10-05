"""Item 30.57 (b): o leque de um `for_each` com efeito em app real é limitado pela POLÍTICA, item a item, antes do pedido.

Decisão da orquestradora (04/10/2026): nenhum teto novo de itens (o `for_each_max_items` "nunca trunca, acima disso
bloqueia" e o fluxo de 8 contatos do QA seguem como estão). Cada cópia do bloco é uma etapa e passa pela porta de
política (`AppState._policy_gate`) antes de virar pedido de aprovação: teto por hora e por dia, uma conta por alvo
(ADR-055), a resposta já dada (30.56) e o teto por execução. A expansão só tira o item que é o PRÓPRIO perfil.

Nível de prova: `simulated` (harness, banco de teste). Nada real.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from app.models import (InteractionStatus, InteractionType, Plan, PlannerInfo, PlanStep, Postcondition,
                        ProfileCreate)
from app.util import now, to_iso

from .test_capabilities import IG, SENHA

LUCAS = "tadeu.quintela4821"


def _execucao(state: Any, alvos: list[str], *, limites: str = '"comments_per_hour": 3') -> str:
    """Uma execução no android-01 (conta do lucas) com uma etapa REPLY_COMMENT por alvo, como a expansão as deixa."""
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id='android-01'")
    pid = state.social.create_profile(ProfileCreate(username=LUCAS, password=SENHA, instance_id="android-01")).id
    state.social_repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                                '"cooldown_between_external_actions_s": 0, '
                                                                f'{limites}}}}}'})
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-l','kl','x','execute','running',1,'[\"android-01\"]','2026-10-04T18:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES ('run-l:android-01','run-l','android-01','running',1,'{}',?)", (pid,))
    for n, alvo in enumerate(alvos, start=1):
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
            " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
            " bindings, variables) VALUES (?,'run-l','run-l:android-01','android-01',1,?,?,'Responder','responder','[]',"
            "1,'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready','REPLY_COMMENT',"
            "'text=Post',?,?)",
            (f"run-l:android-01:v1:reply_i{n}", n, f"reply_i{n}",
             json.dumps({"username": alvo, "content": "Valeu!", "content_verbatim": "true"}),
             json.dumps({"item": alvo, "item_index": str(n)})))
    return pid


async def _portas(state: Any, n: int) -> list[Any]:
    db = state.db
    obj = db.one("SELECT * FROM objectives WHERE id='run-l:android-01'")
    run = db.one("SELECT * FROM runs WHERE id='run-l'")
    return [await state._policy_gate(obj, db.one("SELECT * FROM steps WHERE id=?",
                                                  (f"run-l:android-01:v1:reply_i{i}",)), run)
            for i in range(1, n + 1)]


def _plano_de_responder_a_lista() -> Plan:
    def pos(kind: str) -> Postcondition:
        return Postcondition(kind=kind, value="x", description="x")  # type: ignore[arg-type]
    return Plan(summary="responder a cada comentário", planner=PlannerInfo(provider="t", model="t", simulated=True),
                steps=[PlanStep(key="collect", title="Levantar os comentários", goal="c", postcondition=pos("items_collected")),
                       PlanStep(key="reply", title="Responder {item}", goal="g", depends_on=["collect"], for_each="collect",
                                side_effect=True, capability="REPLY_COMMENT", commit_guard=["{item}"],
                                bindings={"username": "{item}", "content": "Valeu!", "content_verbatim": "true"},
                                postcondition=pos("model_judged"))])


async def test_lista_mista_proprio_perfil_conta_nossa_na_janela_e_terceiro(harness: Any) -> None:
    """O próprio perfil sai na expansão (sem etapa); a conta nossa que outra conta nossa já tocou na janela é recusada
    na porta (ADR-055, sem pedido); só o terceiro vira pedido ao dono."""
    state = harness.state
    lucas = _execucao(state, [])
    andre = state.social.create_profile(ProfileCreate(username="rene.sampaio381524", password=SENHA,
                                                      instance_id="android-02")).id
    state.social.create_profile(ProfileCreate(username="valdir.teixeira6352", password=SENHA, instance_id="android-03"))
    state.social_repo.record_interaction(andre, type=InteractionType.comment_replied.value, direction="outbound",
                                         status=InteractionStatus.confirmed.value, counterparty="@valdir.teixeira6352",
                                         app_id="ig", occurred_at=to_iso(now() - timedelta(days=2)))
    db = state.db
    db.execute("UPDATE runs SET plan=? WHERE id='run-l'", (_plano_de_responder_a_lista().model_dump_json(),))
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
               " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status) VALUES"
               " ('run-l:android-01:v1:collect','run-l','run-l:android-01','android-01',1,0,'collect',"
               "'Levantar os comentários','c','[]',0,'[]','{\"kind\":\"items_collected\",\"value\":\"x\","
               "\"description\":\"x\"}',60,1,'succeeded')")
    obj = db.one("SELECT * FROM objectives WHERE id='run-l:android-01'")
    coleta = state.scheduler.repo.step_dto(db.one("SELECT * FROM steps WHERE id='run-l:android-01:v1:collect'"))
    state.scheduler._expand_for_each(obj, coleta,
                                     [LUCAS, "valdir.teixeira6352", "terceiro.real"])
    obj = db.one("SELECT * FROM objectives WHERE id='run-l:android-01'")
    etapas = db.query("SELECT * FROM steps WHERE objective_id=? AND plan_version=? AND capability='REPLY_COMMENT'"
                      " ORDER BY seq", (obj["id"], obj["plan_version"]))
    alvos = [json.loads(e["bindings"])["username"] for e in etapas]
    assert alvos == ["valdir.teixeira6352", "terceiro.real"]                 # o próprio perfil não virou etapa
    assert any("próprio perfil" in d["message"] for d in db.query("SELECT message FROM events WHERE kind='decision' AND run_id='run-l'"))
    run = db.one("SELECT * FROM runs WHERE id='run-l'")
    nossa, terceiro = [await state._policy_gate(obj, e, run) for e in etapas]
    assert nossa is not None and not nossa.allowed and nossa.retry_at is None and "uma conta por alvo" in nossa.reason
    assert terceiro is not None and terceiro.policy == "approval_required"
    [pedido] = state.approval_service.list(run_id="run-l")
    assert pedido["target"] in ("terceiro.real", "@terceiro.real") and pedido["profile_id"] == lucas


async def test_cinco_terceiros_com_tres_por_hora_viram_tres_pedidos(harness: Any) -> None:
    """O limite que já existe: com `comments_per_hour` 3, só 3 itens viram pedido ao dono; os outros 2 param na
    política (adiados para a próxima janela), sem pedido."""
    state = harness.state
    _execucao(state, [f"@terceiro.{i}" for i in range(1, 6)])
    vereditos = await _portas(state, 5)
    pedidos = state.approval_service.list(run_id="run-l")
    assert len(pedidos) == 3, [v.reason for v in vereditos]
    assert sorted(p["target"] for p in pedidos) == ["@terceiro.1", "@terceiro.2", "@terceiro.3"]
    for v in vereditos[3:]:                     # parados na política: adiados, sem pedido ao dono
        assert v is not None and not v.allowed and v.retry_at is not None
        assert "por hora" in v.reason and "fila de aprovação" in v.reason
    # na retomada do item 1, o pedido DELE não conta contra ele mesmo: a porta não o barra pelo teto
    [retomada] = await _portas(state, 1)
    assert retomada is None or "por hora" not in retomada.reason
