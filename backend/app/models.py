"""Contratos validados (Pydantic). Espelham docs/api-contract.md."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------- enums
class InstanceState(StrEnum):
    absent = "absent"
    stopped = "stopped"
    hibernated = "hibernated"    # desligado com snapshot: acorda em segundos, sem ocupar RAM
    booting = "booting"
    online = "online"
    stopping = "stopping"
    error = "error"


class ControlOwner(StrEnum):
    none = "none"
    ai = "ai"
    user = "user"


class RunStatus(StrEnum):
    planning = "planning"
    needs_input = "needs_input"
    planned = "planned"
    running = "running"
    paused = "paused"
    cancelling = "cancelling"
    completed = "completed"
    completed_with_issues = "completed_with_issues"
    cancelled = "cancelled"
    failed = "failed"


RUN_TERMINAL = {RunStatus.completed, RunStatus.completed_with_issues, RunStatus.cancelled, RunStatus.failed}


class ObjectiveStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting_user = "waiting_user"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    uncertain = "uncertain"


OBJECTIVE_TERMINAL = {ObjectiveStatus.succeeded, ObjectiveStatus.failed, ObjectiveStatus.cancelled}
# waiting_user e uncertain encerram o trabalho automático, mas aguardam decisão do usuário
OBJECTIVE_SETTLED = OBJECTIVE_TERMINAL | {ObjectiveStatus.waiting_user, ObjectiveStatus.uncertain}


class StepStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    running = "running"
    verifying = "verifying"
    succeeded = "succeeded"
    retry_wait = "retry_wait"
    waiting_user = "waiting_user"
    failed = "failed"
    cancelled = "cancelled"
    uncertain = "uncertain"
    skipped = "skipped"


class AttemptStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    interrupted = "interrupted"
    uncertain = "uncertain"
    cancelled = "cancelled"


class ActionStatus(StrEnum):
    intended = "intended"
    done = "done"
    failed = "failed"
    unknown = "unknown"
    rejected = "rejected"


class DeliveryLevel(StrEnum):
    none = "none"
    appeared = "appeared"
    sent = "sent"
    delivered = "delivered"
    read = "read"


DELIVERY_ORDER = {DeliveryLevel.none: 0, DeliveryLevel.appeared: 1, DeliveryLevel.sent: 2,
                  DeliveryLevel.delivered: 3, DeliveryLevel.read: 4}


# ---------------------------------------------------------------- plano
class Postcondition(BaseModel):
    # items_collected: etapa de COLETA — comprovada pelo executor quando `collect_list` leu a lista até o fim
    kind: Literal["text_visible", "app_foreground", "element_present", "model_judged", "items_collected"]
    value: str
    description: str
    required_delivery_level: DeliveryLevel | None = None


class PlanStep(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    title: str
    goal: str
    depends_on: list[str] = []
    side_effect: bool = False
    commit_guard: list[str] = []
    precondition: str | None = None
    postcondition: Postcondition
    timeout_s: int = 180
    max_attempts: int = 3
    for_each: str | None = None               # etapa-MODELO: repetida para cada item da etapa de coleta com esta chave
    template_key: str | None = None           # interno: chave da etapa-modelo de onde esta cópia saiu (identidade da receita)
    variables: dict[str, str] = {}            # interno: variáveis próprias da cópia (item, item_index)


class MissingInfo(BaseModel):
    field: str
    question: str


class PlannerInfo(BaseModel):
    provider: str
    model: str
    simulated: bool


class Plan(BaseModel):
    summary: str
    app_id: str | None = None
    app_package: str | None = None
    parameters: dict[str, str] = {}
    success_criteria: list[str] = []
    steps: list[PlanStep] = []
    missing: list[MissingInfo] = []
    planner: PlannerInfo

    @field_validator("steps")
    @classmethod
    def _unique_keys_and_deps(cls, steps: list[PlanStep]) -> list[PlanStep]:
        keys = [s.key for s in steps]
        if len(set(keys)) != len(keys):
            raise ValueError("chaves de etapa duplicadas no plano")
        seen: set[str] = set()
        for s in steps:
            for d in s.depends_on:
                if d not in seen:
                    raise ValueError(f"etapa '{s.key}' depende de '{d}', que não a precede")
            seen.add(s.key)
        by_key = {s.key: s for s in steps}
        closed: set[str] = set()                  # blocos for_each já encerrados (têm de ser contíguos)
        prev: str | None = None
        for s in steps:
            collects = s.postcondition.kind == "items_collected"
            if collects and (s.side_effect or s.for_each):
                raise ValueError(f"etapa de coleta '{s.key}' não pode ter efeito externo nem for_each")
            if s.for_each != prev and prev:
                closed.add(prev)
            prev = s.for_each
            if not s.for_each:
                continue
            src = by_key.get(s.for_each)
            if src is None or src.postcondition.kind != "items_collected" or s.for_each not in keys[:keys.index(s.key)]:
                raise ValueError(f"etapa '{s.key}': for_each precisa apontar para uma etapa de coleta anterior")
            if s.for_each in closed:
                raise ValueError(f"etapas com for_each='{s.for_each}' precisam ser consecutivas")
        for src_key in {s.for_each for s in steps if s.for_each}:
            block = [s for s in steps if s.for_each == src_key]
            text = " ".join([b.goal + b.title + b.postcondition.value + " ".join(b.commit_guard) for b in block])
            if "{item}" not in text:
                raise ValueError(f"o bloco for_each='{src_key}' não usa {{item}} em nenhuma etapa")
        return steps


# ---------------------------------------------------------------- DTOs da API
class FrameInfo(BaseModel):
    id: str
    ts: str
    width: int
    height: int
    orientation: Literal["portrait", "landscape"]
    stale: bool = False


class InstanceCurrent(BaseModel):
    run_id: str | None = None
    objective_id: str | None = None
    objective_status: ObjectiveStatus | None = None
    step_id: str | None = None
    step_title: str | None = None
    step_status: StepStatus | None = None
    steps_done: int = 0
    steps_total: int = 0


class InstancePorts(BaseModel):
    system: int
    mjpeg: int
    chromedriver: int


class AutomationInfo(BaseModel):
    state: Literal["none", "starting", "ready", "error"] = "none"
    detail: str | None = None


class InstanceResources(BaseModel):
    rss_mb: float | None = None
    cpu_percent: float | None = None


class InstanceDTO(BaseModel):
    id: str
    index: int
    avd_name: str
    serial: str
    console_port: int
    ports: InstancePorts
    state: InstanceState
    state_detail: str | None = None
    pid: int | None = None
    boot_seconds: float | None = None
    app_id: str | None = None
    account_label: str | None = None
    account_evidence: str | None = None
    account_evidence_ts: str | None = None
    control: ControlOwner = ControlOwner.none
    control_since: str | None = None
    control_pending: bool = False
    automation: AutomationInfo = AutomationInfo()
    frame: FrameInfo | None = None
    current: InstanceCurrent | None = None
    attention: str | None = None
    resources: InstanceResources | None = None
    kind: str = "emulator"                    # emulator | external (aparelho ADB que o projeto não liga/desliga)


class AppDTO(BaseModel):
    id: str
    name: str
    package: str
    activity: str | None = None
    apk_path: str | None = None
    nav_hints: str | None = None
    known_selectors: dict[str, str] | None = None
    builtin: bool = False


_PKG = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$"


class AppInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    package: str = Field(pattern=_PKG, max_length=200)
    activity: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.$]*$")
    apk_path: str | None = Field(default=None, max_length=400)
    nav_hints: str | None = Field(default=None, max_length=4000)
    known_selectors: dict[str, str] | None = None


class AppPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    package: str | None = Field(default=None, pattern=_PKG, max_length=200)
    activity: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.$]*$")
    apk_path: str | None = Field(default=None, max_length=400)
    nav_hints: str | None = Field(default=None, max_length=4000)
    known_selectors: dict[str, str] | None = None


class InstancePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str | None = None
    account_label: str | None = Field(default=None, max_length=80)


class InstanceActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    app_id: str | None = None


class BulkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=10)
    action: str
    params: InstanceActionBody | None = None


class ManualInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: str
    frame_id: str
    type: Literal["tap", "long_press", "swipe", "text", "key"]
    x: float | None = None
    y: float | None = None
    x2: float | None = None
    y2: float | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=10000)
    text: str | None = Field(default=None, max_length=2000)
    key: Literal["back", "home", "recents", "enter", "delete"] | None = None


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_id: str


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=3, max_length=4000)
    instance_ids: list[str] = Field(min_length=1, max_length=10)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    mode: Literal["plan", "execute"] = "execute"

    @field_validator("instance_ids")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: Literal["confirm_done", "retry", "abandon"]
    note: str | None = Field(default=None, max_length=500)


class RunCounts(BaseModel):
    succeeded: int = 0
    failed: int = 0
    waiting_user: int = 0
    uncertain: int = 0
    cancelled: int = 0
    running: int = 0
    pending: int = 0


class RunSummary(BaseModel):
    id: str
    short_id: str
    command: str
    status: RunStatus
    simulated: bool
    instance_ids: list[str]
    instances_requested: int
    instances_used: int
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    counts: RunCounts
    progress: float
    status_detail: str | None = None
    deduplicated: bool | None = None


class StepResult(BaseModel):
    verified: bool
    evidence_text: str | None = None
    delivery_level: DeliveryLevel | None = None
    driven_by: str | None = None              # ai | recipe | recipe+ai
    items: list[str] | None = None            # etapa de coleta: itens lidos da tela


class StepDTO(BaseModel):
    id: str
    run_id: str
    objective_id: str
    instance_id: str
    plan_version: int
    seq: int
    key: str
    title: str
    goal: str
    depends_on: list[str]
    side_effect: bool
    commit_guard: list[str] = []
    precondition: str | None = None
    postcondition: Postcondition
    timeout_s: int
    max_attempts: int
    attempts: int
    status: StepStatus
    status_detail: str | None = None
    next_retry_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result: StepResult | None = None
    driven_by: str | None = None              # ai | recipe | recipe+ai (quem decidiu as ações desta etapa)
    for_each: str | None = None               # etapa-modelo ainda não expandida
    variables: dict[str, str] = {}            # variáveis próprias da etapa (item, item_index)


class ActionDTO(BaseModel):
    id: int
    attempt_id: str
    seq: int
    tool: str
    args: dict[str, Any]
    rationale: str | None = None
    status: ActionStatus
    side_effect: bool
    intent_at: str
    done_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    source: str = "ai"                        # ai | recipe (ação reproduzida de uma receita, sem chamada de modelo)


class AttemptDTO(BaseModel):
    id: str
    step_id: str
    number: int
    status: AttemptStatus
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    recovery: str | None = None
    observed_result: str | None = None
    actions: list[ActionDTO] = []


class EvidenceDTO(BaseModel):
    id: int
    run_id: str
    instance_id: str
    step_id: str | None = None
    attempt_id: str | None = None
    ts: str
    kind: Literal["screenshot", "hierarchy", "text", "verifier"]
    note: str | None = None
    url: str | None = None
    redacted: bool = False


class ObjectiveDTO(BaseModel):
    id: str
    run_id: str
    instance_id: str
    status: ObjectiveStatus
    status_detail: str | None = None
    blocked_reason: str | None = None
    needs: str | None = None
    plan_version: int
    parameters: dict[str, str]
    steps_done: int
    steps_total: int
    delivery_level: DeliveryLevel | None = None
    effects: list[str]
    started_at: str | None = None
    finished_at: str | None = None
    ai_calls: int
    ai_input_tokens: int
    ai_output_tokens: int


class PlanVersionDTO(BaseModel):
    objective_id: str
    version: int
    reason: str
    created_at: str
    steps: list[PlanStep]


class DecisionDTO(BaseModel):
    ts: str
    instance_id: str | None
    text: str


class RunDetail(RunSummary):
    plan: Plan | None = None
    objectives: list[ObjectiveDTO] = []
    steps: list[StepDTO] = []
    attempts: list[AttemptDTO] = []
    evidence: list[EvidenceDTO] = []
    plan_versions: list[PlanVersionDTO] = []
    decisions: list[DecisionDTO] = []


class EventRecord(BaseModel):
    id: int | None
    ts: str
    kind: str
    level: Literal["info", "warn", "error"] = "info"
    run_id: str | None = None
    instance_id: str | None = None
    objective_id: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    message: str
    data: dict[str, Any] | None = None


class AiStatus(BaseModel):
    provider: str
    model: str | None
    configured: bool
    simulated: bool
    sends_data_externally: bool
    notice: str
    effort: str | None = None
    models: dict[str, str] | None = None      # plan | decide | verify | escalation → modelo
    recipes: str | None = None                # off | shadow | replay
    flows: bool | None = None
    image_policy: str | None = None


class Problem(BaseModel):
    code: str
    message: str
    hint: str


class AppiumStatus(BaseModel):
    running: bool
    port: int
    detail: str | None = None


class SdkStatus(BaseModel):
    found: bool
    root: str | None = None
    emulator_version: str | None = None
    accel: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded", "error"]
    version: str
    ai: AiStatus
    appium: AppiumStatus
    sdk: SdkStatus
    problems: list[Problem] = []
    features: dict[str, Any] = {}             # hibernation, recipes, flows, image_policy, system_image


class EmulatorMetric(BaseModel):
    instance_id: str
    pid: int
    rss_mb: float
    cpu_percent: float


class Metrics(BaseModel):
    ts: str
    cpu_percent: float
    mem_total_gb: float
    mem_available_gb: float
    mem_used_percent: float
    emulators: list[EmulatorMetric] = []
