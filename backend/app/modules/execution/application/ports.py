"""Portas que o contexto de execução CONSOME (design §7). Quem implementa não importa estes `Protocol`s: a tipagem
estrutural basta, e é o que mantém os contextos num DAG (§9, D5) — capabilities implementa sem enxergar execução.

Por isso os tipos das assinaturas moram em `modules/capabilities/domain` e no kernel (`app.shared`), e não aqui.

`CommandBus` é a exceção de endereço: os providers de recurso de fleet, applications e identity também o consomem
(o `apply` deles só pede comando por ele), e importar execução os poria em ciclo com ela — execução já depende dos três
pelo `PlanReport`. Então o `Protocol` mora no kernel (`shared/commands.py`) e é reexportado aqui, onde a §7 o lista.

As portas que já existem no legado como atributos `Callable` (as do `Scheduler`, `scheduler.py:105-142`, e as do
`StepExecutor`, `executor.py:154-168`) ainda não viram `Protocol`: nenhum consumidor tipado as usaria, e um tipo
sem quem o confira é só texto. Entram quando o código que as chama sair do legado.
"""
from __future__ import annotations

from typing import Protocol

from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.domain.strategy import StrategyContext, StrategyKind, StrategyResult
from app.modules.capabilities.domain.verification import Observation, StepView, VerifyResult
from app.shared.commands import CommandBus as CommandBus
from app.shared.commands import CommandRef, RunRef
from app.shared.convergence import ReconcileOutcome, ResourceVerification
from app.shared.resources import (Drift, ObservedState, ResourceAction, ResourceKind, ResourceRef, ResourceSpec,
                                  Target)


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


class ResourceProvider(Protocol):
    """Um tipo de recurso declarativo (§11): `read_current_state → diff → plan → apply → verify → reconcile`.

    Cumprem por estrutura: `DeviceStateProvider` (fleet), `AppInstallationProvider` (applications),
    `AccountBindingProvider` e `AppSessionProvider` (identity).

    Três diferenças da assinatura proposta na §7, todas pelo mesmo motivo — o que o código de hoje exige:

    * `apply`, `verify` e `reconcile` recebem o `ResourceSpec`, não só o `ResourceRef`: comparar precisa do DESEJADO
      (`app.session` tem `account` opcional), e aplicar precisa do `on_missing` (quem dispara) e de replanejar;
    * `apply` e `reconcile` são síncronos: o canal de comandos é síncrono (o despacho grava o comando e agenda o
      trabalho), e quem fecha o comando é o desfecho dele, nunca quem pediu;
    * `reconcile` devolve o que fechou e o que continua incerto, além da verificação.
    """

    @property
    def kind(self) -> ResourceKind: ...

    def read_current_state(self, ref: ResourceRef, target: Target) -> ObservedState:
        """Pura, sem efeito: `SELECT` ou memória."""
        ...

    def diff(self, desired: ResourceSpec, observed: ObservedState) -> Drift: ...

    def plan(self, drift: Drift) -> list[ResourceAction]: ...

    def apply(self, action: ResourceAction, *, spec: ResourceSpec, run_ref: RunRef | None = None) -> CommandRef:
        """Só por `commands` (R10); duas vezes não abre dois comandos; `uncertain` não se repete."""
        ...

    def verify(self, spec: ResourceSpec, target: Target) -> ResourceVerification:
        """Relê; `proved` só com leitura positiva."""
        ...

    def reconcile(self, spec: ResourceSpec, target: Target) -> ReconcileOutcome:
        """Só no hospedeiro (R11); fecha `uncertain` como sucesso só com prova posterior ao comando."""
        ...
