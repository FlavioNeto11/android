"""Riscos centrais da fila: transições, deduplicação e exclusividade ao assumir etapas."""
from __future__ import annotations

import asyncio

import pytest

from app.models import StepStatus
from app.taskqueue.states import STEP_TRANSITIONS, InvalidTransition, check_transition

from .conftest import Harness


def test_transicoes_validas_e_invalidas() -> None:
    check_transition("pending", "ready")
    check_transition("ready", "running")
    check_transition("running", "verifying")
    check_transition("verifying", "succeeded")
    check_transition("running", "uncertain")
    check_transition("uncertain", "ready")          # só por decisão explícita do usuário
    for bad in (("pending", "running"), ("running", "succeeded"), ("succeeded", "ready"), ("cancelled", "ready"),
                ("skipped", "running"), ("ready", "succeeded")):
        with pytest.raises(InvalidTransition):
            check_transition(*bad)
    # estados terminais não têm saída; sucesso nunca é reaberto
    for terminal in (StepStatus.succeeded, StepStatus.cancelled, StepStatus.skipped):
        assert STEP_TRANSITIONS[terminal] == set()


async def test_chave_de_idempotencia_nao_duplica_execucao(harness: Harness) -> None:
    a = harness.run(["android-01"], key="clique-duplo-0001")
    b = harness.run(["android-01"], key="clique-duplo-0001")      # clique duplo / repetição HTTP
    assert a.id == b.id and a.deduplicated is False and b.deduplicated is True
    assert harness.state.db.scalar("SELECT COUNT(*) FROM runs") == 1
    detail = await harness.wait_run(a.id)
    assert detail.status == "completed"
    # uma única mensagem no "aparelho", apesar das duas requisições
    assert len(harness.fakes["android-01"].messages) == 1
    c = harness.run(["android-01"], key="clique-duplo-0001")      # repetir depois de concluída também não recria
    assert c.id == a.id and c.deduplicated is True


async def test_requisicoes_simultaneas_com_a_mesma_chave(harness: Harness) -> None:
    results = await asyncio.gather(*(asyncio.to_thread(harness.state.repo.create_run, _req("mesma-chave-0002"),
                                                       simulated=True) for _ in range(8)))
    ids = {row["id"] for row, _ in results}
    assert len(ids) == 1 and sum(1 for _, created in results if created) == 1


def _req(key: str):
    from app.models import RunCreate

    return RunCreate(command="abrir o app", instance_ids=["android-01"], idempotency_key=key, mode="plan")


async def test_assumir_etapa_e_exclusivo_por_aparelho_e_por_tentativa(harness: Harness) -> None:
    st = harness.state
    run = harness.run(["android-01"], mode="plan")
    await harness.wait_run(run.id, statuses=("planned",))
    repo = st.repo
    await asyncio.sleep(1.2)                                 # dá tempo a um tick do scheduler
    steps = st.db.query("SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run.id,))
    first, second = steps[0], steps[1]
    assert first["status"] == "pending"                      # execução apenas planejada: nada é promovido/despachado
    st.db.execute("UPDATE steps SET status='ready' WHERE id IN (?,?)", (first["id"], second["id"]))

    attempts = await asyncio.gather(*(asyncio.to_thread(repo.claim_step, first["id"]) for _ in range(6)))
    won = [a for a in attempts if a is not None]
    assert len(won) == 1 and won[0]["id"] == f"{first['id']}:a1"   # id estável da tentativa
    # enquanto há etapa ativa neste aparelho, nenhuma outra pode ser assumida (dono único do executor)
    assert repo.claim_step(second["id"]) is None
    assert st.db.scalar("SELECT COUNT(*) FROM attempts WHERE step_id=?", (first["id"],)) == 1
    assert st.db.scalar("SELECT attempts FROM steps WHERE id=?", (first["id"],)) == 1


async def test_plano_e_tarefas_sao_persistidos_antes_de_executar(harness: Harness) -> None:
    run = harness.run(["android-01", "android-02"], mode="plan")
    detail = await harness.wait_run(run.id, statuses=("planned",))
    assert detail.plan is not None and len(detail.objectives) == 2 and len(detail.steps) == 12
    assert all(s.status == "pending" for s in detail.steps)
    send = next(s for s in detail.steps if s.key == "send_message" and s.instance_id == "android-02")
    assert send.side_effect and send.max_attempts == 1
    assert send.id == f"{run.id}:android-02:v1:send_message"
    # parâmetros resolvidos POR APARELHO
    obj = next(o for o in detail.objectives if o.instance_id == "android-02")
    assert obj.parameters["message"] == f"Teste POC android-02 {run.id}"
    assert not harness.fakes["android-02"].messages
    started = harness.state.runs.start(run.id)
    assert started.status == "running"
    assert (await harness.wait_run(run.id)).status == "completed"
