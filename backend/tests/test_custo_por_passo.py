"""31.229 (adendo v1.124): o custo e o modelo por passo no GET da execução e no do alvo da operação.

`simulated`: harness na porta 5640, provedor simulado (as chamadas dele contam em `chamadas` e custam US$ 0). Depois de a
execução assentar, o teste grava numa etapa dela as chamadas que o provedor real gravaria: um `decide` tier 0 cuja ação
de efeito foi rejeitada (a decisão descartada) e o `decide` tier 1 com `escalate='efeito'` que refez o commit (a trilha
do 31.223), mais um `plan` sem etapa. O que se prova:
- o passo traz o modelo do último decide, o commit com o modelo, o tier e o escalate da decisão que valeu, e o custo
  por modelo pela regra de `spent_usd`; o plano vai em `sem_passo`;
- a soma dos passos com `sem_passo` é o `spent_usd` da execução, e as chamadas são todas as de `ai_calls`;
- o GET do alvo da operação traz o mesmo objeto, e a operação soma por modelo e por estágio.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.main import create_app
from app.modules.operacoes.infrastructure.servico import AlvoPedido
from app.planning import costs, custo_por_passo
from app.util import now_iso

from .conftest import Harness
from .test_operacoes import _alvo, _conta, _pedido, _persona, _servico

pytestmark = pytest.mark.asyncio
ASSENTADOS = ("awaiting_person", "completed", "completed_with_issues", "failed", "cancelled", "needs_input")


async def _assentar(h: Harness, run_id: str) -> None:
    st = h.state
    assert st is not None
    for _ in range(300):
        if st.db.scalar("SELECT status FROM runs WHERE id=?", (run_id,)) in ASSENTADOS:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"a execução não assentou: {st.db.scalar('SELECT status FROM runs WHERE id=?', (run_id,))}")


def _chamada(h: Harness, run_id: str, step_id: str | None, role: str, modelo: str, tier: int = 0,
             escalate: str | None = None) -> int:
    st = h.state
    assert st is not None
    st.db.execute("INSERT INTO ai_calls(ts, run_id, step_id, role, model, tier, escalate, input_tokens, output_tokens,"
                  " ok, provider) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (now_iso(), run_id, step_id, role, modelo, tier, escalate, 10_000, 1_000, 1, "anthropic"))
    return int(st.db.scalar("SELECT MAX(id) FROM ai_calls"))


async def test_o_custo_e_o_modelo_por_passo_na_execucao_e_no_alvo(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Passo", "android-01")
    _conta(harness, pid, "qa-user-61", sessao_em="android-01")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-custo-passo", max_usd=1000))
    run_id = str(_alvo(op, pid)["run_id"])
    await _assentar(harness, run_id)
    passo = st.db.one("SELECT id, capability FROM steps WHERE run_id=? ORDER BY seq LIMIT 1", (run_id,))
    assert passo is not None, "a execução simulada não planejou etapa"
    sid = str(passo["id"])
    navega, forte = list(st.cfg.file.ai.prices)[:2]
    descartada = _chamada(harness, run_id, sid, "decide", navega)
    refeita = _chamada(harness, run_id, sid, "decide", forte, tier=1, escalate="efeito")
    _chamada(harness, run_id, None, "plan", navega)
    st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                  (f"{sid}:a99", sid, 99, "succeeded", now_iso()))
    # a rejeitada vai por último de propósito: é o filtro de status, não a ordem, que a tira do commit
    for seq, status, chamada in ((900, "done", refeita), (901, "rejected", descartada)):
        st.db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, side_effect, intent_at, ai_call_id)"
                      " VALUES (?,?,?,?,?,?,?,?)", (f"{sid}:a99", seq, "tap", "{}", status, 1, now_iso(), chamada))

    lido = custo_por_passo.por_execucao(st.db, st.cfg.file.ai.prices, [run_id], {})[run_id]
    p = next(x for x in lido["passos"] if x["step_id"] == sid)  # type: ignore[attr-defined]
    assert p["modelo"] == forte
    assert p["commit"] == {"fonte": "ai", "modelo": forte, "tier": 1, "escalate": "efeito"}
    por_modelo = {m["modelo"]: m for m in p["por_modelo"]}
    um = {"model": navega, "input_tokens": 10_000, "cache_read": 0, "cache_write": 0, "output_tokens": 1_000}
    assert por_modelo[navega]["custo_usd"] == pytest.approx(costs.row_usd(st.cfg.file.ai.prices, um), abs=1e-6)
    assert por_modelo[forte]["chamadas"] >= 1 and por_modelo[forte]["custo_usd"] > 0
    assert lido["sem_passo"]["chamadas"] >= 1 and lido["sem_passo"]["custo_usd"] > 0  # type: ignore[index]
    # a soma fecha com o total da execução: custo pela regra de spent_usd, chamadas = todas as linhas
    soma_usd = sum(float(x["custo_usd"]) for x in lido["passos"]) + float(lido["sem_passo"]["custo_usd"])  # type: ignore[attr-defined,index]
    soma_n = sum(int(x["chamadas"]) for x in lido["passos"]) + int(lido["sem_passo"]["chamadas"])  # type: ignore[attr-defined,index]
    assert soma_usd == pytest.approx(costs.spent_usd(st.db, st.cfg.file.ai.prices, run_id=run_id), abs=1e-5)
    assert soma_n == st.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id=?", (run_id,))
    assert sum(int(m["chamadas"]) for m in lido["por_modelo"]) == soma_n  # type: ignore[attr-defined]
    assert "sem_passo" in lido["por_estagio"]  # type: ignore[operator]

    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/runs/{run_id}")
        assert r.status_code == 200, r.text
        do_run = r.json()["custo_por_passo"]
        r = await c.get(f"/api/operacoes/{op['id']}")
        assert r.status_code == 200, r.text
        da_op = r.json()
    assert [x["step_id"] for x in do_run["passos"]] == [x["step_id"] for x in lido["passos"]]  # type: ignore[attr-defined]
    alvo = next(a for a in da_op["alvos"] if a["profile_id"] == pid)
    assert alvo["custo_por_passo"]["por_modelo"] == do_run["por_modelo"]
    assert {m["modelo"]: m["chamadas"] for m in da_op["custo_por_modelo"]} == {
        m["modelo"]: m["chamadas"] for m in do_run["por_modelo"]}
    assert sum(e["chamadas"] for e in da_op["custo_por_estagio"].values()) == soma_n


async def test_alvo_sem_execucao_traz_custo_nulo_e_a_operacao_soma_zero(harness: Harness) -> None:
    pid = _persona(harness, "SemRun")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-custo-nulo"))
    assert _alvo(op, pid)["custo_por_passo"] is None
    assert op["custo_por_modelo"] == [] and op["custo_por_estagio"] == {}
    assert custo_por_passo.por_execucao(harness.state.db, {}, [], {}) == {}  # type: ignore[union-attr]
