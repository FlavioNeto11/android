"""31.50 (depois do corte): a lista de execuções conta os objetivos de todas numa consulta só.

Antes, `run_summary` fazia um `SELECT … GROUP BY status` por execução da lista (N+1). `Repository.run_summaries`
lê as contagens em lote e dá o mesmo resultado do caminho de uma execução. Prova `simulated` (harness).
"""
from __future__ import annotations

import pytest

from .conftest import Harness

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


async def test_a_lista_conta_os_objetivos_numa_consulta_so(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    execucoes = [harness.run(["android-01"]), harness.run(["android-01"])]
    for r in execucoes:
        await harness.wait_run(r.id, statuses=TERMINAIS)
    linhas = [st.repo.run_row(r.id) for r in execucoes]
    um_a_um = [st.repo.run_summary(r).counts for r in linhas]
    consultas: list[str] = []
    for nome in ("query", "scalar", "one"):
        original = getattr(st.db, nome)
        monkeypatch.setattr(st.db, nome, lambda sql, *a, _o=original, **k: (consultas.append(sql), _o(sql, *a, **k))[1])
    em_lote = [x.counts for x in st.repo.run_summaries(linhas)]
    monkeypatch.undo()
    assert em_lote == um_a_um
    assert [sum(c.model_dump().values()) for c in em_lote] == [1, 1]
    assert sum("FROM objectives" in q and "GROUP BY" in q for q in consultas) == 1


async def test_lista_vazia_nao_consulta(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    assert st.repo.run_summaries([]) == []
