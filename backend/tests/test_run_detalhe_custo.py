"""29.153: custo no detalhe HTTP, sem mudar a lista. Prova simulada com banco e aparelhos falsos."""
from __future__ import annotations

import pytest

from app.models import RunSummary
from app.util import now_iso

from .conftest import Harness
from .test_contrato_http import _cliente


@pytest.fixture
def execucao(harness: Harness) -> RunSummary:
    st = harness.state
    assert st is not None
    st.db.execute(
        "INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("execucao-custo", "chave-custo", "Abra o app de teste", "execute", "cancelled", 1, '["android-01"]', now_iso()),
    )
    row = st.repo.run_row("execucao-custo")
    assert row is not None
    return st.repo.run_summary(row)


async def test_detalhe_soma_custo_e_conta_so_as_chamadas_da_execucao(
    harness: Harness, execucao: RunSummary,
) -> None:
    st = harness.state
    assert st is not None
    st.cfg.file.ai.prices = {"modelo-a": [2.0, 0.2, 2.5, 10.0], "modelo-b": [4.0, 0.4, 5.0, 20.0]}
    for run_id, modelo, entrada, leitura, gravacao, saida in (
        (execucao.id, "modelo-a", 1000, 1000, 200, 1000),
        (execucao.id, "modelo-b", 2000, 0, 0, 1000),
        ("outra-execucao", "modelo-a", 1_000_000, 0, 0, 0),
        (None, "modelo-a", 1_000_000, 0, 0, 0),
    ):
        st.db.execute(
            "INSERT INTO ai_calls(ts, run_id, role, model, provider, input_tokens, cache_read, cache_write, output_tokens)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("2026-01-01T00:00:00Z", run_id, "plan", modelo, "anthropic", entrada, leitura, gravacao, saida),
        )
    async with _cliente(harness) as c:
        resposta = await c.get(f"/api/runs/{execucao.id}")
    assert resposta.status_code == 200
    assert resposta.json()["id"] == execucao.id
    assert resposta.json()["costs"] == {"spent_usd": pytest.approx(0.0407), "calls": 2}


async def test_detalhe_sem_chamadas_traz_zeros(harness: Harness, execucao: RunSummary) -> None:
    async with _cliente(harness) as c:
        resposta = await c.get(f"/api/runs/{execucao.id}")
    assert resposta.status_code == 200
    assert resposta.json()["costs"] == {"spent_usd": 0.0, "calls": 0}


async def test_detalhe_conta_simuladas_sem_cobra_las_e_inclui_custo_declarado(
    harness: Harness, execucao: RunSummary,
) -> None:
    st = harness.state
    assert st is not None
    st.cfg.file.ai.prices = {"modelo-a": [2.0, 0.2, 2.5, 10.0]}
    for provedor, declarado in (("simulated", None), ("openai", 0.07)):
        st.db.execute(
            "INSERT INTO ai_calls(ts, run_id, role, model, provider, input_tokens, output_tokens, usd)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (now_iso(), execucao.id, "plan", "modelo-a", provedor, 1_000_000, 0, declarado),
        )
    async with _cliente(harness) as c:
        resposta = await c.get(f"/api/runs/{execucao.id}")
    assert resposta.status_code == 200
    assert resposta.json()["costs"] == {"spent_usd": 0.07, "calls": 2}


async def test_lista_continua_sem_custos(harness: Harness, execucao: RunSummary) -> None:
    async with _cliente(harness) as c:
        resposta = await c.get("/api/runs")
    assert resposta.status_code == 200
    runs = resposta.json()["runs"]
    assert any(run["id"] == execucao.id for run in runs)
    assert all("costs" not in run for run in runs)


async def test_detalhe_inexistente_continua_404(harness: Harness) -> None:
    async with _cliente(harness) as c:
        resposta = await c.get("/api/runs/execucao-inexistente")
    assert resposta.status_code == 404
    assert resposta.json()["detail"]["code"] == "not_found"
