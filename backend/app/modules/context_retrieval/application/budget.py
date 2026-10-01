"""Orçamento técnico do provedor semântico: chamadas por pedido e por sessão, tokens de entrada, custo e prazo.

Existe para que um agente em laço não vire uma conta: o teto é conferido ANTES de cada chamada, e estourar um teto
não falha o retrieval, manda o pedido para o local (`FallbackReason.BUDGET_EXCEEDED`). O que se gasta é o que o
provedor reporta (`ProviderUsage`); nada aqui estima custo por conta própria além dos tokens de entrada, que
vêm da conta de bytes do payload (a conferência é prévia, o provedor ainda não respondeu).

Thread-safe: o retrieval roda em `asyncio.to_thread` e o ledger é da sessão inteira.
"""
from __future__ import annotations

import threading

from ..domain.model import Budget, FallbackReason, ProviderUsage


class BudgetLedger:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.cost_usd = 0.0

    def begin_request(self) -> "RequestBudget":
        return RequestBudget(self)

    def _pode(self, calls_do_pedido: int, est_input_tokens: int, tokens_do_pedido: int, custo_do_pedido: float
              ) -> FallbackReason | None:
        b = self.budget
        with self._lock:
            if calls_do_pedido >= b.max_calls_per_request or self.calls >= b.max_calls_per_session:
                return FallbackReason.BUDGET_EXCEEDED
            if tokens_do_pedido + est_input_tokens > b.max_input_tokens:
                return FallbackReason.BUDGET_EXCEEDED
            if self.cost_usd >= b.max_cost_usd or custo_do_pedido >= b.max_cost_usd:
                return FallbackReason.BUDGET_EXCEEDED
        return None

    def _gasta(self, usage: ProviderUsage) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += usage.input_tokens
            self.cost_usd += usage.cost_usd

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {"calls": self.calls, "input_tokens": self.input_tokens, "cost_usd": round(self.cost_usd, 6)}


class RequestBudget:
    """A parte de UM pedido; o ledger da sessão soma por baixo."""

    def __init__(self, ledger: BudgetLedger) -> None:
        self._ledger = ledger
        self.timeout_s = ledger.budget.timeout_ms / 1000
        self.calls = 0
        self.input_tokens = 0
        self.cost_usd = 0.0

    def check_call(self, *, est_input_tokens: int) -> FallbackReason | None:
        return self._ledger._pode(self.calls, est_input_tokens, self.input_tokens, self.cost_usd)

    def record(self, usage: ProviderUsage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.cost_usd += usage.cost_usd
        self._ledger._gasta(usage)
