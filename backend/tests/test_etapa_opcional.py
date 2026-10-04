"""Item 31.36: a etapa que só limpa a tela é opcional; falhar nela não derruba o objetivo.

Achado real do 28.12-02 (f8722d, android-09): uma etapa `dismiss_cookies` num banner que não fecha gastou 3 tentativas,
um replano e a verba (US$ 0,309, 23 decisões do ator). Agora o planejador marca `opcional`, o parsing só aceita a marca
em etapa sem efeito, sem saída, sem for_each e sem commit_guard, e a etapa opcional que não se comprova vira `skipped`
como AVISO: a seguinte roda e o objetivo fecha `succeeded`.

Nível de prova: `simulated` (texto do modelo escrito à mão e harness com provedor por roteiro; nenhuma IA paga).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import Plan, PlanStep, Postcondition
from app.planning.parsing import plan_from_json
from app.planning.provider import PlanRequest, Usage
from app.planning.simulated_provider import SimulatedProvider

from .conftest import CountingProvider, Harness

TERMINAIS = ("completed", "completed_with_issues", "failed", "needs_input", "waiting_user")


def _livre(key: str, **over: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"key": key, "title": key, "goal": key, "depends_on": [], "side_effect": False,
                         "commit_guard": [], "precondition": None,
                         "postcondition": {"kind": "model_judged", "value": "x", "description": "x",
                                           "required_delivery_level": None},
                         "timeout_s": 60, "max_attempts": 3, "for_each": None, "app_id": None, "saidas": [],
                         "opcional": True}
    e.update(over)
    return e


def test_o_parsing_so_aceita_opcional_em_etapa_que_nao_deixa_marca() -> None:
    passos = [_livre("fechar_aviso"), _livre("enviar", side_effect=True), _livre("ler", saidas=["assunto"]),
              _livre("guardar", commit_guard=["Ana"]), _livre("normal", opcional=False)]
    bruto = json.dumps({"summary": "s", "app_id": None, "parameters": [], "success_criteria": [], "steps": passos,
                        "missing": []})
    plano = plan_from_json(bruto, PlanRequest(command="c", run_id="r", instances=[], apps=[]), provider="p",
                           model="m", max_steps=10)
    assert {s.key: s.opcional for s in plano.steps} == {"fechar_aviso": True, "enviar": False, "ler": False,
                                                        "guardar": False, "normal": False}


class _ComLimpeza(SimulatedProvider):
    """O plano simulado de sempre, com uma etapa opcional ANTES dele que nunca se comprova (o seletor não existe)."""

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        plano, uso = await super().plan(req)
        limpar = PlanStep(key="fechar_aviso", title="Fechar o aviso", goal="fechar o aviso que cobre a tela",
                          postcondition=Postcondition(kind="element_present", value="id=aviso_que_nao_existe",
                                                      description="o aviso some"),
                          max_attempts=1, opcional=True)
        plano.steps = [limpar, *[s.model_copy(update={"depends_on": [*s.depends_on, "fechar_aviso"]})
                                 for s in plano.steps]]
        return plano, uso


async def test_etapa_opcional_que_falha_vira_aviso_e_o_objetivo_fecha(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(_ComLimpeza())
    state = await h.boot()
    try:
        run = h.run(["android-01"])
        await h.wait_run(run.id, statuses=TERMINAIS)
        linha = state.repo.run_row(run.id)
        assert linha["status"] == "completed", linha["status_detail"]          # aviso, não problema
        etapas = {r["key"]: r for r in state.db.query(
            "SELECT key, status, opcional, plan_version, status_detail FROM steps WHERE run_id=?", (run.id,))}
        limpar = etapas["fechar_aviso"]
        assert limpar["status"] == "skipped" and limpar["opcional"] == 1 and limpar["plan_version"] == 1
        assert "limpeza opcional" in limpar["status_detail"]
        assert all(r["plan_version"] == 1 for r in etapas.values())             # sem replano
        assert all(r["status"] == "succeeded" for k, r in etapas.items() if k != "fechar_aviso")
    finally:
        await state.stop()


async def test_desligada_a_etapa_opcional_e_a_de_sempre(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(_ComLimpeza())
    h.cfg.file.ai.limpeza_opcional = False
    state = await h.boot()
    try:
        run = h.run(["android-01"])
        await h.wait_run(run.id, statuses=TERMINAIS)
        status = state.db.scalar("SELECT status FROM steps WHERE run_id=? AND key='fechar_aviso' AND plan_version=1",
                                 (run.id,))
        assert status in ("failed", "uncertain", "waiting_user"), status        # a etapa de sempre: falha de verdade
        assert state.repo.run_row(run.id)["status"] != "completed"
    finally:
        await state.stop()
