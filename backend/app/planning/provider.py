"""Interface do provedor de IA. Para trocar de provedor, implemente `AIProvider` e registre em `build_provider`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from ..config import Config
from ..models import AiStatus, DeliveryLevel, Plan


class AIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, kind: str = "error"):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind          # error | not_configured | refusal | budget | invalid_output


@dataclass(slots=True)
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class AppContext:
    id: str | None
    name: str | None
    package: str | None
    activity: str | None
    nav_hints: str | None
    known_selectors: dict[str, str] | None


@dataclass(slots=True)
class PlanRequest:
    command: str
    run_id: str
    instances: list[dict[str, Any]]           # [{instance_id, account_label, app_id}]
    apps: list[AppContext]


@dataclass(slots=True)
class StepContext:
    run_id: str
    instance_id: str
    objective_summary: str
    parameters: dict[str, str]
    step_key: str
    step_title: str
    step_goal: str
    side_effect: bool
    commit_done: bool                         # a ação com efeito desta etapa já foi disparada
    commit_guard: list[str]
    precondition: str | None
    postcondition_description: str
    remaining_steps: list[str]
    app: AppContext
    account_label: str | None
    required_delivery_level: str | None = None
    resumed_after_manual_control: bool = False


@dataclass(slots=True)
class ScreenInput:
    width: int                                # dimensões da imagem enviada (coordenadas do modelo)
    height: int
    jpeg: bytes | None
    elements: list[str]                       # linhas compactas da hierarquia
    package: str | None
    sensitive: bool
    tree: Any = None                          # UiTree completo (usado só pelo provedor simulado)


@dataclass(slots=True)
class DecisionRequest:
    ctx: StepContext
    screen: ScreenInput
    history: list[str] = field(default_factory=list)   # ações anteriores desta tentativa, em texto


@dataclass(slots=True)
class Decision:
    tool: str
    args: dict[str, Any]
    raw_text: str | None = None


class Verdict(BaseModel):
    satisfied: Literal["yes", "no", "uncertain"]
    evidence: str
    delivery_level: DeliveryLevel | None = None


@dataclass(slots=True)
class VerifyRequest:
    ctx: StepContext
    screen: ScreenInput


class AIProvider(Protocol):
    name: str
    model: str
    simulated: bool

    def status(self) -> AiStatus: ...
    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]: ...
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]: ...
    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]: ...


def build_provider(cfg: Config) -> AIProvider:
    kind = (cfg.env.ai_provider or "anthropic").strip().lower()
    if kind == "simulated":
        from .simulated_provider import SimulatedProvider

        return SimulatedProvider()
    if kind == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    raise ValueError(f"AI_PROVIDER desconhecido: {kind!r} (use 'anthropic' ou 'simulated')")
