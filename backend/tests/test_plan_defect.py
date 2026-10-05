"""Execução c4da09: pós-condição de PROCESSO ("a lista foi percorrida") julgada por uma tela só.
O verificador marca `unprovable` → falha na hora (sem nova tentativa nem reexecução do mesmo plano), os aparelhos
que ainda não começaram são retidos, e o verificador passa a receber os fatos registrados pelo executor."""
from __future__ import annotations

from typing import Any

from app.automation.hierarchy import parse_hierarchy
from app.automation.tools import Scroll, ToolContext, execute_tool
from app.metricas import metricas
from app.planning.provider import Decision, Usage, Verdict
from app.planning.simulated_provider import _post

from .conftest import Harness


def _list(names: list[str]) -> Any:
    rows = "".join(f'<node class="android.widget.TextView" text="{n}" resource-id="app:id/name" '
                   f'bounds="[0,{200 + i * 100}][720,{300 + i * 100}]"/>' for i, n in enumerate(names))
    return parse_hierarchy('<hierarchy><node class="android.widget.ListView" scrollable="true" resource-id="app:id/list" '
                           f'bounds="[0,200][720,1200]"/>{rows}'
                           '<node class="android.widget.TextView" text="12:01" resource-id="clock" bounds="[0,0][100,40]"/>'
                           "</hierarchy>")


async def test_rolagem_informa_se_o_conteudo_mudou_e_quando_chegou_ao_fim() -> None:
    class Io:
        def swipe(self, *a: Any) -> None: ...

    async def call(fn: Any, *a: Any) -> Any:
        return fn(*a)

    after = [_list(["C", "D", "E"]), _list(["C", "D", "E"])]

    async def observe() -> Any:
        return after.pop(0)

    def ctx(tree: Any) -> ToolContext:
        return ToolContext(io=Io(), call=call, tree=tree, width=720, height=1280, image_scale=1.0,  # type: ignore[arg-type]
                           app_package=None, app_activity=None, observe=observe)

    first = _list(["A", "B", "C"])
    lst = first.find_selector("id=list")[0].id
    out = await execute_tool(ctx(first), "scroll", Scroll(rationale="r", direction="down", element_id=lst))
    assert out.result["changed"] is True and out.result["at_end"] is False
    end = _list(["C", "D", "E"])                                   # o relógio fora da área não conta como mudança
    out = await execute_tool(ctx(end), "scroll", Scroll(rationale="r", direction="down", element_id=lst))
    assert out.result["changed"] is False and out.result["at_end"] is True


async def test_pos_condicao_nao_comprovavel_falha_na_hora_e_retem_os_demais(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.pathfinder_wait_s = 60                     # android-02 espera o desbravador sem começar
    metricas.limpar()
    inner = harness.ai.inner
    plan0, decide0, verify0 = inner.plan, inner.decide, inner.verify
    seen: dict[str, Any] = {"scrolls": 0, "facts": None}

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        i = next(n for n, s in enumerate(p.steps) if s.key == "confirm_account") + 1
        bad = p.steps[i - 1].model_copy(update={
            "key": "list_contacts", "title": "Levantar os contatos", "goal": "Rolar a lista e identificar todos.",
            "depends_on": ["confirm_account"], "max_attempts": 2,
            "postcondition": _post("model_judged", "A lista completa foi percorrida.", "Inventário concluído.")})
        rest = [s.model_copy(update={"depends_on": ["list_contacts"]}) if n == 0 else s for n, s in enumerate(p.steps[i:])]
        return p.model_copy(update={"steps": [*p.steps[:i], bad, *rest]}), u

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "list_contacts":
            return await decide0(req)
        seen["scrolls"] += 1
        if seen["scrolls"] == 1:
            return Decision(tool="scroll", args={"rationale": "ver o resto", "direction": "down", "element_id": None}), Usage()
        return Decision(tool="step_done", args={"rationale": "fim", "evidence": "lista estável", "delivery_level": None}), Usage()

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "list_contacts":
            return await verify0(req)
        seen["facts"] = list(req.facts)
        return Verdict(satisfied="unprovable", evidence="fala de processo, não de estado da tela"), Usage()

    inner.plan, inner.decide, inner.verify = plan, decide, verify
    run = harness.run(["android-01", "android-02"])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "awaiting_person", "waiting_user"))
    db = harness.state.db                                          # type: ignore[union-attr]
    objs = {o["instance_id"]: o for o in db.query("SELECT * FROM objectives WHERE run_id=?", (run.id,))}

    assert objs["android-01"]["status"] == "failed" and "Defeito do plano" in objs["android-01"]["status_detail"]
    assert objs["android-01"]["plan_version"] == 1                 # o MESMO plano não é reexecutado
    assert harness.ai.count("verify", step="list_contacts") == 1   # sem 2º julgamento, sem nova tentativa
    assert db.scalar("SELECT attempts FROM steps WHERE objective_id=? AND key='list_contacts'", (objs["android-01"]["id"],)) == 1
    assert any("scroll(" in f and "at_end=True" in f for f in seen["facts"])     # fato do executor chega ao verificador
    assert not any("lista estável" in f for f in seen["facts"])                  # alegação do ator NÃO é fato

    assert objs["android-02"]["status"] == "waiting_user" and "defeito do plano" in objs["android-02"]["blocked_reason"]
    assert harness.ai.count("decide", instance="android-02") == 0  # não gastou IA para falhar igual
    # a espera pelo desbravador foi medida e fechada pela falha do líder (não ficou aberta até o teto)
    await harness.ticks(2)
    assert metricas.valor("pathfinder.desfecho", resultado="falhou") == 1
    assert metricas.total("pathfinder.desfecho") == 1
    assert not harness.fakes["android-01"].messages and not harness.fakes["android-02"].messages
