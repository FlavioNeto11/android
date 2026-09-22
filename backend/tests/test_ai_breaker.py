"""Disjuntor de conta de IA (achado #90): billing/not_configured não gastam tentativa, disparam uma vez por
execução, pausam a execução e aparecem em health()/`/api/ai` — em vez de cada aparelho descobrir sozinho."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.planning.provider import AIError
from app.planning.simulated_provider import SimulatedProvider

from .conftest import Harness


class BillingFailProvider:
    """`decide()` nega por falta de crédito enquanto `fail` estiver ligado — simula o 400 do achado #90."""

    def __init__(self, inner: SimulatedProvider):
        self.inner = inner
        self.fail = True
        self.calls = 0
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> object:
        return self.inner.status()

    async def decide(self, req: object) -> object:
        self.calls += 1
        if self.fail:
            raise AIError("Sem crédito no provedor de IA.", kind="billing")
        return await self.inner.decide(req)

    async def verify(self, req: object) -> object:
        return await self.inner.verify(req)

    async def plan(self, req: object) -> object:
        return await self.inner.plan(req)

    async def generate_social_response(self, req: object) -> object:
        raise NotImplementedError


async def test_disjuntor_represa_sem_gastar_tentativa_pausa_e_acusa_na_saude(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    fail_provider = BillingFailProvider(SimulatedProvider())
    h.ai = fail_provider  # type: ignore[assignment]
    await h.boot()
    assert h.state is not None
    try:
        run = h.run(["android-01"])
        detail = await h.wait_run(run.id)

        # zero tentativas consumidas: uma única chamada ao provedor, e o passo volta a 0 tentativas (refund_attempt)
        assert fail_provider.calls == 1
        obj = detail.objectives[0]
        assert obj.status == "waiting_user"
        step = next(s for s in detail.steps if s.id.startswith(obj.id))
        assert step.attempts == 0

        # mensagem limpa — nunca o dicionário cru do provedor
        assert "Sem crédito" in (obj.blocked_reason or "")
        assert "{" not in (obj.blocked_reason or "") and "invalid_request_error" not in (obj.blocked_reason or "")

        # execução pausada de verdade (o campo sobrevive mesmo depois de recompute_run rotular o status final)
        run_row = h.state.repo.run_row(run.id)
        assert run_row["pause_requested"] == 1

        # disjuntor visível: scheduler.executor, health() e o equivalente de /api/ai
        breaker = h.state.scheduler.executor.ai_breaker
        assert breaker is not None and breaker.kind == "billing" and breaker.run_id == run.id
        problems = {p.code: p for p in h.state.health().problems}
        assert "ai_billing" in problems
        ai_status = h.state.ai_status()
        assert ai_status.account_blocked and ai_status.account_blocked_reason

        # disparado: uma segunda chamada à MESMA execução nem toca o provedor (represa na hora, sem gastar tentativa)
        called = False

        async def would_call() -> None:
            nonlocal called
            called = True

        with pytest.raises(AIError) as exc:
            await h.state.scheduler.executor._ai(run.id, obj.id, would_call)  # noqa: SLF001
        assert not called and exc.value.kind == "billing"

        # retomada: corrige a causa (recarrega o crédito) e usa "Tentar novamente" — o disjuntor solta
        fail_provider.fail = False
        h.state.runs.retry_failed(run.id)
        detail2 = await h.wait_run(run.id, statuses=("completed",))
        assert detail2.counts.succeeded == 1
        assert h.state.scheduler.executor.ai_breaker is None
        assert h.state.repo.run_row(run.id)["pause_requested"] == 0
        assert fail_provider.calls > 1   # voltou a chamar o provedor normalmente depois de retomar
    finally:
        await h.state.stop()
