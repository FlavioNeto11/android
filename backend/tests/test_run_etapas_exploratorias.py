"""31.305 (adendo v1.135): `etapas_exploratorias` no RunSummary de `GET /api/runs` e `GET /api/runs/{id}`.

O Portal filtra e conta "descoberta pela IA" nas Execuções sem ler cada execução: o número sai de `steps.exploratoria`
(31.273), numa consulta só para a lista inteira. Execução sem etapa exploratória diz 0, nunca falta o campo.

Prova `simulated`: banco do harness com execuções semeadas, rota HTTP de verdade (ASGI).
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.main import create_app
from app.models import PlanStep, Postcondition, RunSummary

from .conftest import Harness
from .test_etapas_descobertas import _semear as _semear_bruto

pytestmark = pytest.mark.asyncio


def _semear(st: Any, rid: str, passo: PlanStep, **kw: Any) -> int:
    """`_semear` grava `succeeded` na execução (a coluna é texto); o resumo exige um `RunStatus` válido."""
    recibo = _semear_bruto(st, rid, passo, **kw)
    st.db.execute("UPDATE runs SET status='completed' WHERE id=?", (rid,))
    st.db.execute("UPDATE objectives SET status='succeeded' WHERE run_id=?", (rid,))
    return recibo


def _passo(chave: str) -> PlanStep:
    return PlanStep(key=chave, title="t", goal="g",
                    postcondition=Postcondition(kind="model_judged", value="v", description="d"))


async def test_lista_e_detalhe_trazem_o_numero_de_etapas_exploratorias(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _semear(st, "r-20261010150000-aaaaaa", _passo("explorar_ver_caixa_de_lixo"))
    _semear(st, "r-20261010150001-bbbbbb", _passo("ver_ajuda"), exploratoria=False)
    # uma segunda etapa exploratória na primeira execução
    st.db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                  " postcondition, timeout_s, max_attempts, status, app_id, exploratoria)"
                  " VALUES (?,?,?,?,1,2,?,?,?,?,60,3,'succeeded','qa-messenger',1)",
                  ("r-20261010150000-aaaaaa:android-01:v1:explorar_ver_ajuda", "r-20261010150000-aaaaaa",
                   "r-20261010150000-aaaaaa:o", "android-01", "explorar_ver_ajuda", "t", "g",
                   _passo("xx").postcondition.model_dump_json()))

    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        lista = (await c.get("/api/runs")).json()["runs"]
        por_id = {r["id"]: r for r in lista}
        assert por_id["r-20261010150000-aaaaaa"]["etapas_exploratorias"] == 2
        assert por_id["r-20261010150001-bbbbbb"]["etapas_exploratorias"] == 0
        detalhe = (await c.get("/api/runs/r-20261010150000-aaaaaa")).json()
        corpo = detalhe.get("run", detalhe)
        assert corpo["etapas_exploratorias"] == 2


async def test_o_resumo_avulso_conta_com_uma_consulta_so_da_execucao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _semear(st, "r-20261010150002-cccccc", _passo("explorar_ver_caixa_de_lixo"))
    resumo = st.repo.run_summary(st.repo.run_row("r-20261010150002-cccccc"))
    assert isinstance(resumo, RunSummary) and resumo.etapas_exploratorias == 1
    assert RunSummary.model_fields["etapas_exploratorias"].default == 0
