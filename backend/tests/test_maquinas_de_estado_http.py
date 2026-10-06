"""15.15 F7: a tabela de estados imposta no que a pessoa vê pela API.

Nível de prova: `simulated` (harness, aparelho falso, porta 5640 do harness de testes).
- cancelar uma execução que já terminou devolve 409 `invalid_state` (já era assim; fica provado);
- o gesto que chega depois de o estado mudar (a tabela recusa a escrita) vira 409 `invalid_transition`, nunca 500.
"""
from __future__ import annotations

import json

import pytest

from app.main import create_app
from app.models import RunStatus
from app.taskqueue.states import InvalidTransition

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente


async def test_cancelar_execucao_ja_terminada_devolve_409_com_motivo(harness: Harness) -> None:
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    harness.state.repo.db.execute("UPDATE runs SET status='completed' WHERE id=?", (run.id,))
    async with _cliente(harness) as c:
        r = await c.post(f"/api/runs/{run.id}/cancel")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "invalid_state" and "já terminou" in r.json()["detail"]["message"]
    assert harness.state.repo.run_row(run.id)["status"] == RunStatus.completed.value


async def test_transicao_recusada_na_corrida_vira_409_e_nao_500(harness: Harness,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    repo = harness.state.repo

    def recusa(*_a: object, **_k: object) -> bool:
        raise InvalidTransition("transição inválida de run: completed → cancelling")

    monkeypatch.setattr(repo, "set_run_status", recusa)
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    import httpx

    transporte = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with httpx.AsyncClient(transport=transporte, base_url="http://127.0.0.1") as c:
        r = await c.post(f"/api/runs/{run.id}/start")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "invalid_transition"
    # O 409 deixa um evento `warn` com o MODELO da rota (nunca o id), para a recusa aparecer sem log de acesso.
    ev = harness.state.db.one("SELECT level, message, data FROM events WHERE kind='log' AND message LIKE ? ORDER BY id DESC LIMIT 1",
                              ("%409 invalid_transition%",))
    assert ev is not None and ev["level"] == "warn"
    dados = json.loads(ev["data"])
    assert dados == {"code": "invalid_transition", "method": "POST", "route": "/api/runs/{run_id}/{op}",
                     "detail": "transição inválida de run: completed → cancelling"}
    assert run.id not in ev["message"] and run.id not in ev["data"]
