"""Item 29.35 (RA-9): o `missing_info` do ator passa pela revisão determinística antes de chegar à pessoa.

23 % dos objetivos paravam em `waiting_user`, e "o campo X não existe" chegava à pessoa sem passar pela recuperação.
Agora a falta que não é credencial ganha UMA revisão do plano (a mesma da recuperação automática, com a marca
"defeito de plano"); senha, código e 2FA continuam com a pessoa (ADR-009). `simulated`: provedor falso do harness.
"""
from __future__ import annotations

import json
from typing import Any

from app.planning.provider import Decision, Usage
from app.taskqueue.scheduler import MOTIVO_DEFEITO_DE_PLANO

from .conftest import Harness

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


def _bloqueia_uma_vez(harness: Harness, razao: str) -> list[str]:
    """A etapa de envio relata `missing_info` na primeira decisão e segue normal depois."""
    harness.cfg.file.ai.cascade_blocked_to_tier1 = False     # sem a subida do 17.10: o que se exercita é a revisão
    inner = harness.ai.inner
    decide0 = inner.decide
    vistos: list[str] = []

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "send_message" and not vistos:
            vistos.append(req.ctx.step_key)
            return Decision(tool="step_blocked", args={"kind": "missing_info", "reason": razao, "needs_user": True,
                                                       "rationale": "o ator não achou"}), Usage()
        return await decide0(req)

    inner.decide = decide
    return vistos


def _revisoes(harness: Harness, run_id: str) -> list[str]:
    return [r["reason"] for r in harness.state.db.query(
        "SELECT v.reason FROM plan_versions v JOIN objectives o ON o.id = v.objective_id"
        " WHERE o.run_id=? AND v.version > 1 ORDER BY v.version", (run_id,))]


async def test_falta_que_nao_e_credencial_revisa_o_plano_e_o_objetivo_conclui(harness: Harness) -> None:
    _bloqueia_uma_vez(harness, "o campo de texto não existe nesta tela")
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))  # type: ignore[union-attr]
    assert obj["status"] == "succeeded"                      # sem pedir pessoa
    revisoes = _revisoes(harness, run.id)
    assert len(revisoes) == 1 and MOTIVO_DEFEITO_DE_PLANO in revisoes[0]
    assert revisoes[0].startswith("Recuperação automática")  # entra no MESMO teto da recuperação: sem laço
    # O contrato para Aprendizado e Jev: `plan.revised` com a marca em `data.reason`.
    eventos = [json.loads(r["data"]) for r in harness.state.db.query(
        "SELECT data FROM events WHERE kind='plan.revised' AND run_id=? ORDER BY id", (run.id,))]
    assert eventos and MOTIVO_DEFEITO_DE_PLANO in eventos[-1]["reason"]
    assert len(harness.fakes["android-01"].messages) == 1    # a mensagem saiu uma vez


async def test_falta_de_credencial_fica_com_a_pessoa_sem_revisao(harness: Harness) -> None:
    """ADR-009: senha, código e 2FA são da pessoa; revisar o plano não faria a senha aparecer."""
    _bloqueia_uma_vez(harness, "falta a senha da conta para continuar")
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    obj = harness.state.db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))  # type: ignore[union-attr]
    assert obj["status"] == "waiting_user"
    assert _revisoes(harness, run.id) == []
