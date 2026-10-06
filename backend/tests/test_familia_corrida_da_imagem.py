"""31.53 (F2 da revisão): a corrida de duas personas do mesmo pedido publicando a MESMA imagem com legenda GERADA.

Na porta, `check` → `await _draft_gate` (a chamada do modelo, segundos) → `_approval_gate`. As duas personas passavam
pelo `check` antes de qualquer pedido existir, e a regra do objeto não via a irmã. Agora a porta roda a regra de novo
depois do rascunho, sem `await` até gravar o pedido: aqui a persona B passa a porta INTEIRA enquanto o rascunho de A está
em curso; ao voltar, A vê o pedido de B e é recusada. Uma segue, a outra é recusada; nunca as duas.

Nível de prova: `simulated` (harness, catálogo do Instagram, banco de teste; rascunho e contexto do pedido falsos).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app import gates as gates_mod
from app.social.policy import ContextoDoPedido

from .test_porta_do_plano import _plano, _sem_iniciar

POST = {"key": "post", "cap": "CREATE_POST", "bindings": {"image_id": "img-1", "content_brief": "um fim de tarde"}}


async def _porta(state: Any, run_id: str, aparelho: str) -> Any:
    db = state.db
    etapa = db.one("SELECT * FROM steps WHERE id=?", (f"{run_id}:{aparelho}:v1:post",))
    return await state._policy_gate(db.one("SELECT * FROM objectives WHERE id=?", (etapa["objective_id"],)),  # noqa: SLF001
                                    etapa, db.one("SELECT * FROM runs WHERE id=?", (run_id,)))


async def test_duas_personas_do_pedido_com_a_mesma_imagem_uma_segue_e_a_outra_e_recusada(harness: Any,
                                                                                         monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [POST], aparelho="android-01", run_id="run-a")
    _plano(state, [POST], aparelho="android-02", run_id="run-b")
    perfis = frozenset(r["profile_id"] for r in state.db.query("SELECT profile_id FROM objectives"))
    assert len(perfis) == 2
    monkeypatch.setattr(gates_mod, "contexto_do_pedido",
                        lambda _db, _run: ContextoDoPedido(raiz="ped-1", familia=perfis))
    vereditos: dict[str, Any] = {}

    async def draft_response(*_a: Any, **_k: Any) -> Any:
        if "b" not in vereditos:                      # 1ª chamada é a de A: B passa a porta inteira no meio dela
            vereditos["b"] = None
            vereditos["b"] = await _porta(state, "run-b", "android-02")
        return SimpleNamespace(content="Fim de tarde na praia", refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.social, "draft_response", draft_response)
    vereditos["a"] = await _porta(state, "run-a", "android-01")
    a, b = vereditos["a"], vereditos["b"]
    pedidos = state.db.query("SELECT run_id, status FROM pending_approvals WHERE capability='CREATE_POST'")
    assert [p["run_id"] for p in pedidos] == ["run-b"]                 # só B chegou ao pedido (a publicação pede sim)
    assert b is not None and "31.53" not in (b.reason or "")
    assert a is not None and not a.allowed and a.retry_at is None and "31.53" in a.reason
    assert "recusado, não adiado" in a.reason
