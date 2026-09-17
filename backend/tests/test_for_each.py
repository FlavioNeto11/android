"""P2 — repetição sobre lista lida da tela: coleta determinística (collect_list) + bloco for_each.
Comando-modelo: a execução c4da09 ("mensagem para todos os contatos")."""
from __future__ import annotations

from typing import Any

import pytest

from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.taskqueue.foreach import expand, sanitize_item
from app.taskqueue.recipes import step_template_hash

from .conftest import Harness
from .fake_device import CONTACTS

ALL = 'Abra o QA Messenger e envie "Bom dia {instance_id} {run_id}" para todos os contatos da lista.'


def _post(kind: str, value: str = "x") -> Postcondition:
    return Postcondition(kind=kind, value=value, description=value)  # type: ignore[arg-type]


def _steps() -> list[PlanStep]:
    return [
        PlanStep(key="open_app", title="a", goal="a", postcondition=_post("app_foreground")),
        PlanStep(key="collect", title="c", goal="c", depends_on=["open_app"], postcondition=_post("items_collected")),
        PlanStep(key="open_chat", title="Abrir {item}", goal="g", depends_on=["collect"], for_each="collect",
                 postcondition=_post("element_present", "id=t|text={item}")),
        PlanStep(key="send", title="Enviar", goal="g", depends_on=["open_chat"], for_each="collect", side_effect=True,
                 commit_guard=["{item}"], postcondition=_post("model_judged")),
        PlanStep(key="report", title="r", goal="r", depends_on=["send"], postcondition=_post("text_visible")),
    ]


def test_expand_copia_o_bloco_por_item_sem_dependencia_entre_itens() -> None:
    steps = _steps()
    assert [s.key for s in expand(steps, {})] == [s.key for s in steps]          # sem coleta feita: plano-modelo intacto
    out = expand(steps, {"collect": ["Ana", "Bia"]})
    assert [s.key for s in out] == ["open_app", "open_chat_i1", "send_i1", "open_chat_i2", "send_i2", "report"]
    by = {s.key: s for s in out}
    assert by["open_chat_i2"].depends_on == ["open_app"]                        # herda o que a COLETA exigia; nada de _i1
    assert by["send_i2"].depends_on == ["open_chat_i2"]
    assert by["report"].depends_on == ["send_i1", "send_i2"]
    assert by["send_i2"].variables == {"item": "Bia", "item_index": "2"} and by["send_i2"].for_each is None
    assert "{item}" in by["open_chat_i1"].title                                 # só resolve na materialização
    assert step_template_hash(by["send_i1"]) == step_template_hash(by["send_i2"]) == step_template_hash(steps[3])
    assert sanitize_item("  QA {run_id}\n\x00 ok ") == "QA run_id ok"           # item é dado: sem chaves nem controle


def test_plano_invalido_com_for_each_mal_formado() -> None:
    info = PlannerInfo(provider="t", model="t", simulated=True)
    s = _steps()
    with pytest.raises(ValueError, match="coleta anterior"):
        Plan(summary="x", planner=info, steps=[s[0], s[2].model_copy(update={"depends_on": ["open_app"], "for_each": "open_app"})])
    with pytest.raises(ValueError, match="consecutivas"):
        Plan(summary="x", planner=info, steps=[s[0], s[1], s[2], s[4].model_copy(update={"depends_on": ["open_chat"]}),
                                               s[3].model_copy(update={"depends_on": ["open_chat"]})])
    with pytest.raises(ValueError, match="item"):
        Plan(summary="x", planner=info, steps=[s[0], s[1], s[3].model_copy(update={"depends_on": ["collect"], "commit_guard": []})])


async def test_mensagem_para_todos_os_contatos_uma_por_contato_e_receita_compartilhada(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    run = harness.run(["android-01"], command=ALL)
    assert (await harness.wait_run(run.id, timeout=90)).status == "completed"
    fake = harness.fakes["android-01"]
    assert sorted(m.contact for m in fake.messages) == sorted(CONTACTS)         # uma por contato, nenhuma repetida
    db = harness.state.db                                                       # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["plan_version"] == 2 and len(CONTACTS) == 5
    assert harness.ai.count("verify", step="collect_contacts") == 0             # coleta comprovada pelo executor
    # a receita aprendida no 1º contato serve aos demais: o 2º em diante abre/preenche/envia sem decisão de IA
    for n in range(2, len(CONTACTS) + 1):
        for key in ("open_conversation", "compose_message", "send_message", "back_to_list"):
            assert harness.ai.count("decide", step=f"{key}_i{n}") == 0, f"{key}_i{n}"
    assert harness.ai.count("decide", step="open_conversation_i1") >= 1

    # outro aparelho, mesmo comando: a coleta e TODO o bloco rodam por receita
    run2 = harness.run(["android-02"], command=ALL)
    assert (await harness.wait_run(run2.id, timeout=90)).status == "completed"
    assert sorted(m.contact for m in harness.fakes["android-02"].messages) == sorted(CONTACTS)
    mine = [c for c in harness.ai.calls if c["role"] == "decide" and c.get("instance") == "android-02"]
    assert not [c["step"] for c in mine if c["step"] == "collect_contacts" or "_i" in c["step"]]


async def test_item_que_falha_nao_trava_os_demais_e_nunca_vira_sucesso(harness: Harness) -> None:
    inner = harness.ai.inner
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key.startswith("open_conversation") and req.ctx.parameters.get("item") == "QA-002":
            from app.planning.provider import Decision, Usage
            return Decision(tool="step_blocked", args={"rationale": "x", "kind": "other", "needs_user": False,
                                                       "reason": "contato indisponível (teste)"}), Usage()
        return await decide0(req)

    inner.decide = decide
    run = harness.run(["android-01"], command=ALL)
    await harness.wait_run(run.id, timeout=120)
    fake = harness.fakes["android-01"]
    assert sorted(m.contact for m in fake.messages) == sorted(c for c in CONTACTS if c != "QA-002")
    obj = harness.state.db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))  # type: ignore[union-attr]
    assert obj["status"] == "failed" and "4 de 5" in obj["status_detail"] and "QA-002" in obj["status_detail"]

    # "tentar novamente" refaz só o item que falhou — nada é reenviado aos outros
    inner.decide = decide0
    harness.state.runs.retry_failed(run.id)                                     # type: ignore[union-attr]
    await harness.wait(lambda: len(fake.messages) == len(CONTACTS), timeout=90, what="item refeito")
    assert sorted(m.contact for m in fake.messages) == sorted(CONTACTS)
