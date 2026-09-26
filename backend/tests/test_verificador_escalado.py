"""Item 7.10 — o que a bateria de 25/09 mediu no verificador barato (Haiku 4.5), e as duas proteções baratas.

1. Recusa por nível de entrega que JÁ atende ao exigido ("Entregue" onde bastava "Enviada"): a execução
   `r-20260925014321-86058e` parou numa pessoa com a mensagem entregue de verdade. Agora quem decide é o modelo de
   escalonamento, uma vez, sobre a mesma tela.
2. "Sim" sobre uma tela sem NENHUM elemento na hierarquia: 4 dos 6 falsos positivos do rejulgamento. Tela vazia não
   prova estado — o executor espera a tela mudar e julga de novo.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from app.automation.hierarchy import UiTree
from app.models import DeliveryLevel
from app.planning.provider import Usage, Verdict

from .conftest import Harness


async def test_recusa_com_nivel_suficiente_e_rejulgada_pelo_modelo_de_escalonamento(harness: Harness) -> None:
    inner = harness.ai.inner
    verify0 = inner.verify
    vistos: list[bool] = []

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "verify_sent":
            return await verify0(req)
        vistos.append(req.escalate)
        if not req.escalate:       # o erro medido: viu "Entregue" e recusou por não ser exatamente "Enviada"
            return Verdict(satisfied="no", evidence="exige exatamente Enviada; a tela mostra Entregue",
                           delivery_level=DeliveryLevel.delivered), Usage()
        return Verdict(satisfied="yes", evidence="Entregue ✓✓ é posterior a Enviada",
                       delivery_level=DeliveryLevel.delivered), Usage()

    inner.verify = verify
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
    db = harness.state.db                                          # type: ignore[union-attr]
    obj = db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] == "succeeded"                            # não parou numa pessoa
    assert vistos[:2] == [False, True]                             # barato, depois UM escalonado
    assert harness.ai.count("verify", step="verify_sent", escalate=True) == 1
    assert len(harness.fakes["android-01"].messages) == 1          # e nada foi reenviado


async def test_recusa_por_nivel_insuficiente_nao_escala(harness: Harness) -> None:
    """O escalonamento é só para o erro medido; recusa com nível abaixo do exigido segue o caminho de sempre."""
    inner = harness.ai.inner
    verify0 = inner.verify

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "verify_sent":
            return await verify0(req)
        v, u = await verify0(req)
        return v, u

    inner.verify = verify
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
    assert harness.ai.count("verify", escalate=True) == 0


async def test_sim_sobre_tela_sem_elementos_nao_conta_como_prova(harness: Harness) -> None:
    inner = harness.ai.inner
    decide0, verify0 = inner.decide, inner.verify
    estado = {"vazia": False, "julgou_vazia": 0}
    devices = harness.state.devices                                # type: ignore[union-attr]
    observe0 = devices.observe

    async def observe(rt: Any, *, timeout: float, **kw: Any) -> Any:   # `imagem`/`lado_max` (adendo v0.20, C1)
        o = await observe0(rt, timeout=timeout, **kw)
        if estado["vazia"]:
            return dataclasses.replace(o, tree=UiTree(elements=[], packages=o.tree.packages, sensitive=False))
        return o

    async def decide(req: Any) -> Any:
        decisao, uso = await decide0(req)
        if req.ctx.step_key == "verify_sent" and decisao.tool == "step_done":
            estado["vazia"] = True                                 # a tela que o VERIFICADOR vai ver vem sem hierarquia
        return decisao, uso

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "verify_sent":
            return await verify0(req)
        if not req.screen.elements:
            estado["julgou_vazia"] += 1
            estado["vazia"] = False                                # a tela "carrega" depois do primeiro julgamento
        return Verdict(satisfied="yes", evidence="parece enviada", delivery_level=DeliveryLevel.sent), Usage()

    devices.observe = observe                                      # type: ignore[method-assign]
    inner.decide, inner.verify = decide, verify
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
    db = harness.state.db                                          # type: ignore[union-attr]
    assert estado["julgou_vazia"] >= 1                             # o "sim" sobre a tela vazia aconteceu...
    assert harness.ai.count("verify", step="verify_sent") >= 2     # ...e não bastou: julgou de novo a tela carregada
    assert db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))["status"] == "succeeded"
