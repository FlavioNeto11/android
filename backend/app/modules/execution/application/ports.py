"""Portas que o contexto de execução CONSOME (design §7). Quem implementa não importa estes `Protocol`s: a tipagem
estrutural basta, e é o que mantém os contextos num DAG (§9, D5) — capabilities implementa sem enxergar execução.

Por isso os tipos das assinaturas moram em `modules/capabilities/domain`, e não aqui.

As portas que já existem no legado como atributos `Callable` (as do `Scheduler`, `scheduler.py:105-142`, e as do
`StepExecutor`, `executor.py:154-168`) ainda não viram `Protocol`: nenhum consumidor tipado as usaria, e um tipo
sem quem o confira é só texto. Entram quando o código que as chama sair do legado.
"""
from __future__ import annotations

from typing import Protocol

from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.domain.strategy import StrategyContext, StrategyKind, StrategyResult
from app.modules.capabilities.domain.verification import Observation, StepView, VerifyResult


class CapabilityProvider(Protocol):
    """Quem sabe observar, executar, verificar e reconciliar uma capability de um app.

    A implementação de hoje (`CatalogCapabilityProvider`) só verifica; as outras três operações entram na fase G,
    delegando ao executor e ao scheduler, que continuam donos do aparelho e do desfecho (§14.1).
    """

    def supports(self, cap: CapabilityRef) -> bool: ...

    async def observe(self, ctx: StrategyContext) -> Observation: ...

    async def execute(self, node: StepView, ctx: StrategyContext) -> StrategyResult: ...

    async def verify(self, node: StepView, obs: Observation) -> VerifyResult: ...

    async def reconcile(self, node: StepView, ctx: StrategyContext) -> VerifyResult: ...


class ExecutionStrategy(Protocol):
    """Um jeito de levar a etapa adiante (receita, ator de IA, pessoa). Percorridas na ordem do nó, pulando as que
    não se aplicam; nenhuma declara o próprio sucesso (§14.3). Sem implementação até a fase G."""

    @property
    def kind(self) -> StrategyKind: ...

    def applicable(self, node: StepView, ctx: StrategyContext) -> bool: ...

    async def run(self, node: StepView, ctx: StrategyContext) -> StrategyResult: ...
