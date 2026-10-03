"""P12 da reavaliação de 03/10 (decisão da orquestradora: (a) + (c); não é defeito). A prévia (`mode=plan`) só executa
pelo início explícito; o `run.updated` do início diz quem iniciou (`iniciada_por`): a pessoa da sessão, ou `panel`, pela
rota; `sistema` no início automático do `mode=execute`. A métrica de prévia é `mode='plan' AND started_at IS NULL`.

Medido no central em 03/10 (só leitura): as 11 execuções em `mode='plan'` que gastaram decisões tinham sido iniciadas
12 a 44 s depois de criadas; sem o autor no evento, isso não se separava de uma prévia que executasse sozinha.

Nível de prova: `simulated` (harness na porta 5640, provedor simulado).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.main import create_app

from .conftest import Harness

#: A métrica de prévia (P12 (a)): criada em `mode=plan` e nunca iniciada.
PREVIAS = "SELECT COUNT(*) FROM runs WHERE mode='plan' AND started_at IS NULL"


def _iniciada_por(state: Any, run_id: str) -> list[Any]:
    """O `iniciada_por` de cada `run.updated` da execução que a mostra em `running`."""
    saida = []
    for r in state.db.query("SELECT data FROM events WHERE run_id=? AND kind='run.updated' ORDER BY id", (run_id,)):
        data = json.loads(r["data"] or "{}")
        if data.get("run", {}).get("status") == "running":
            saida.append(data.get("iniciada_por"))
    return saida


async def test_previa_nao_inicia_sozinha_e_o_inicio_pela_rota_leva_quem(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], mode="plan")
    await harness.wait(lambda: st.runs.repo.run_row(run.id)["status"] == "planned", what="prévia pronta")
    await harness.ticks(3)
    assert st.runs.repo.run_row(run.id)["started_at"] is None and _iniciada_por(st, run.id) == []
    assert st.db.scalar(PREVIAS) == 1

    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/api/runs/{run.id}/start")
    assert r.status_code == 200, r.text

    assert st.runs.repo.run_row(run.id)["started_at"] is not None
    # o evento da transição leva o autor (sem sessão: o painel, nunca o sistema); os seguintes, do recompute, não
    inicios = _iniciada_por(st, run.id)
    assert inicios[0] == "panel" and set(inicios[1:]) <= {None}
    assert st.db.scalar(PREVIAS) == 0                       # iniciada, deixa de ser prévia


async def test_execute_inicia_sozinho_com_sistema(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], mode="execute")
    await harness.wait(lambda: st.runs.repo.run_row(run.id)["started_at"] is not None, what="início automático")
    inicios = _iniciada_por(st, run.id)
    assert inicios[0] == "sistema" and set(inicios[1:]) <= {None}
    assert st.db.scalar(PREVIAS) == 0
