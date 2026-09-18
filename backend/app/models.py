"""Contratos validados (Pydantic). Espelham docs/api-contract.md."""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


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
    capability: str | None = None             # nome da ação no catálogo do app: chave de política e de limite
    commit_selector: str | None = None        # QUEM dispara o efeito externo (ex.: desc=Send); nulo = heurística antiga
    band_guard: list[str] = []                # guardas que precisam estar na MESMA LINHA do alvo, não em qualquer lugar
    bindings: dict[str, str] = {}             # argumentos da capability (alvo, conteúdo): histórico e aprovação usam
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


# ---------------------------------------------------------------- perfis do Instagram
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")


class SessionStatus(StrEnum):
    """Sessão é CACHE do que se observou no aparelho, nunca a verdade."""

    unknown = "unknown"
    auth_required = "auth_required"
    auth_challenge = "auth_challenge"
    wrong_account = "wrong_account"
    session_ready = "session_ready"


class CredentialInfo(BaseModel):
    """O que a API conta sobre a credencial. A senha NUNCA aparece aqui — o campo simplesmente não existe."""

    configured: bool = False
    login_identifier: str | None = None
    status: str | None = None              # active | invalid
    failed_attempts: int = 0
    blocked_until: str | None = None
    updated_at: str | None = None
    last_used_at: str | None = None


class SessionInfo(BaseModel):
    status: SessionStatus = SessionStatus.unknown
    instance_id: str | None = None
    observed_username: str | None = None
    verified_at: str | None = None
    detail: str | None = None


class InstagramProfileDTO(BaseModel):
    id: str
    username: str
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    birth_date: str | None = None
    email: str | None = None
    persona_id: str | None = None
    persona_name: str | None = None
    status: str = "active"
    instance_id: str | None = None          # aparelho vinculado agora
    credential: CredentialInfo = CredentialInfo()
    session: SessionInfo = SessionInfo()
    last_verified_at: str | None = None
    last_activity_at: str | None = None
    created_at: str
    updated_at: str


class ProfileCreate(BaseModel):
    """Cadastro pelo portal. `password` é SecretStr: não aparece em repr, log nem em erro de validação."""

    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=30)
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    birth_date: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=200)
    persona_id: str | None = Field(default=None, max_length=120)
    instance_id: str | None = Field(default=None, max_length=60)
    login_identifier: str | None = Field(default=None, max_length=200)
    password: SecretStr | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not _USERNAME.match(v):
            raise ValueError("username inválido: use letras, números, ponto ou sublinhado")
        return v


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=120)
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    birth_date: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=200)
    persona_id: str | None = Field(default=None, max_length=120)
    instance_id: str | None = Field(default=None, max_length=60)
    status: Literal["active", "disabled"] | None = None


class CredentialUpdate(BaseModel):
    """Só escrita. Não existe rota que devolva a senha — nem esta."""

    model_config = ConfigDict(extra="forbid")
    login_identifier: str | None = Field(default=None, max_length=200)
    password: SecretStr = Field(min_length=1)


class PersonaTraits(BaseModel):
    """Os traços que descrevem a persona. Tudo é opcional: o que estiver vazio simplesmente não vai ao modelo."""

    model_config = ConfigDict(extra="forbid")
    personality: str | None = Field(default=None, max_length=600)
    tone: str | None = Field(default=None, max_length=300)
    formality: Literal["informal", "neutro", "formal"] | None = None
    typical_length: Literal["curta", "media", "longa"] | None = None
    emojis: Literal["nunca", "raro", "moderado", "muito"] | None = None
    slang: str | None = Field(default=None, max_length=300)
    humor: str | None = Field(default=None, max_length=300)
    interests: list[str] = Field(default_factory=list, max_length=30)
    dm_style: str | None = Field(default=None, max_length=400)
    comment_style: str | None = Field(default=None, max_length=400)
    with_known: str | None = Field(default=None, max_length=400)
    with_strangers: str | None = Field(default=None, max_length=400)
    examples: list[str] = Field(default_factory=list, max_length=20)
    common_phrases: list[str] = Field(default_factory=list, max_length=30)
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=30)


class PersonaDTO(BaseModel):
    id: str
    name: str
    summary: str | None = None
    persona_prompt: str = ""
    traits: PersonaTraits = PersonaTraits()
    profile_id: str | None = None           # persona pertence a UM perfil (restrição no esquema)
    profile_username: str | None = None
    created_at: str
    updated_at: str


class PersonaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    persona_prompt: str = Field(default="", max_length=4000)
    traits: PersonaTraits = PersonaTraits()


class PersonaPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    persona_prompt: str | None = Field(default=None, max_length=4000)
    traits: PersonaTraits | None = None


class PersonaPreviewBody(BaseModel):
    """Testar a persona SEM publicar nada: nenhuma tela é tocada, nenhuma interação é gravada."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["dm_reply", "comment_reply"] = "dm_reply"
    profile_id: str | None = Field(default=None, max_length=120)   # usa memória/relacionamento deste perfil
    counterparty: str | None = Field(default=None, max_length=60)
    incoming: str = Field(min_length=1, max_length=2000)


class InteractionType(StrEnum):
    """Vocabulário do sistema. A coluna é TEXT de propósito: tipos novos não exigem migração."""

    dm_received = "dm_received"
    dm_sent = "dm_sent"
    comment_received = "comment_received"
    comment_replied = "comment_replied"
    post_liked = "post_liked"
    post_unliked = "post_unliked"
    comment_liked = "comment_liked"
    followed = "followed"
    unfollowed = "unfollowed"
    follow_request_accepted = "follow_request_accepted"
    follow_request_declined = "follow_request_declined"
    profile_opened = "profile_opened"
    other = "other"


class InteractionStatus(StrEnum):
    """Só `confirmed` vira fato. Falha, incerteza e cancelamento nunca alimentam a memória."""

    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"
    uncertain = "uncertain"
    cancelled = "cancelled"


class InteractionDTO(BaseModel):
    id: str
    profile_id: str
    instance_id: str | None = None
    run_id: str | None = None
    objective_id: str | None = None
    step_id: str | None = None
    occurred_at: str
    type: str
    direction: str                          # inbound | outbound | none
    counterparty: str | None = None
    thread_key: str | None = None
    incoming_content: str | None = None     # veio do app: é DADO, nunca instrução
    outgoing_content: str | None = None
    target: str | None = None
    status: InteractionStatus = InteractionStatus.pending
    evidence: str | None = None
    created_at: str


class MemoryItemDTO(BaseModel):
    id: str
    profile_id: str
    subject: str
    content: str
    source: str                             # interaction | operator | system
    interaction_id: str | None = None
    importance: float = 0.5
    confidence: float = 0.5
    occurrences: int = 1
    expires_at: str | None = None
    created_at: str
    updated_at: str
    last_used_at: str | None = None


class MemoryCreate(BaseModel):
    """O operador pode ensinar um fato à mão. O que parece segredo é recusado pelo serviço."""

    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=1000)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    expires_at: str | None = Field(default=None, max_length=40)


class RelationshipDTO(BaseModel):
    counterparty: str
    summary: str = ""
    tone: str | None = None
    interactions: int = 0
    first_interaction_at: str | None = None
    last_interaction_at: str | None = None


class ThreadSummaryDTO(BaseModel):
    thread_key: str
    counterparty: str | None = None
    summary: str = ""
    messages: int = 0
    last_message_at: str | None = None


class SocialContextDTO(BaseModel):
    """O que o modelo vê sobre o perfil. Não existe campo de credencial — e o construtor não conhece o cofre."""

    profile_id: str
    username: str
    persona: PersonaDTO | None = None
    relationship: RelationshipDTO | None = None
    thread: ThreadSummaryDTO | None = None
    memories: list[MemoryItemDTO] = Field(default_factory=list)
    recent_interactions: list[InteractionDTO] = Field(default_factory=list)
    rendered: str = ""                      # exatamente o texto que iria ao modelo
    estimated_tokens: int = 0
    dropped_memories: int = 0               # quantas ficaram de fora pelo teto de tokens


class MemoryCandidateDTO(BaseModel):
    subject: str = Field(max_length=120)
    content: str = Field(max_length=1000)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SocialDraftDTO(BaseModel):
    """Conteúdo gerado ANTES de qualquer envio — registrado, revisável e ainda não publicado."""

    content: str = ""
    rationale: str = ""
    refused: bool = False
    refusal_reason: str | None = None
    memory_candidates: list[MemoryCandidateDTO] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    """Os três verbos do §17. `content` só faz sentido em `edit` — é o texto que realmente será enviado."""

    model_config = ConfigDict(extra="forbid")
    verb: Literal["approve", "edit", "reject"]
    content: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=400)


class ReleaseImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_reference: str | None = Field(default=None, max_length=300)   # de onde veio, informado por quem importou
    expected_package: str | None = Field(default=None, max_length=120)


class SignatureApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=300)


class AppInstallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_id: str = Field(min_length=3, max_length=200)


class AppVerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str = Field(min_length=3, max_length=120)


class ReleaseState(StrEnum):
    """Ciclo de vida de uma release nesta rodada. Canário, promoção e rollback entram numa fase posterior."""

    imported = "imported"
    inspected = "inspected"
    validated = "validated"
    installable = "installable"
    invalid = "invalid"
    incompatible = "incompatible"


class InstalledAppState(StrEnum):
    """Estado do aplicativo NUM aparelho. Só `ready` libera tarefa."""

    missing = "missing"
    installing = "installing"
    installed = "installed"
    verifying = "verifying"
    ready = "ready"
    install_failed = "install_failed"
    verify_failed = "verify_failed"
    incompatible = "incompatible"
    version_drift = "version_drift"


class ReleaseFileDTO(BaseModel):
    role: Literal["base", "split"]
    split_name: str | None = None
    file_name: str
    sha256: str
    size_bytes: int


class ReleaseDTO(BaseModel):
    id: str
    package_name: str
    version_name: str
    version_code: int
    artifact_type: Literal["single", "split_set", "unverified_split_set"]
    signature_sha256: str
    min_sdk: int | None = None
    target_sdk: int | None = None
    supported_abis: list[str] = []
    source_type: Literal["inbox", "upload"]
    source_reference: str | None = None
    imported_at: str
    status: ReleaseState
    detail: str | None = None
    files: list[ReleaseFileDTO] = []
    devices: list[str] = []            # aparelhos com esta release instalada


class DeviceAppStateDTO(BaseModel):
    instance_id: str
    package_name: str
    desired_release_id: str | None = None
    installed_release_id: str | None = None
    observed_version_name: str | None = None
    observed_version_code: int | None = None
    observed_splits: list[str] = []
    first_install_time: str | None = None
    last_update_time: str | None = None
    state: InstalledAppState
    pending_op: str | None = None
    verified_at: str | None = None
    drift_kind: str | None = None
    detail: str | None = None


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
    """Execução por APARELHO (como sempre) ou por PERFIL: `profile_ids` resolve para o aparelho vinculado a cada
    perfil. Quem pensa em "responda as mensagens da Mariana" não deveria precisar saber em qual emulador ela está."""

    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=3, max_length=4000)
    instance_ids: list[str] = Field(default_factory=list, max_length=10)
    profile_ids: list[str] = Field(default_factory=list, max_length=10)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    mode: Literal["plan", "execute"] = "execute"

    @field_validator("instance_ids", "profile_ids")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _um_dos_dois(self) -> "RunCreate":
        # Validador de MODELO, não de campo: campo com valor padrão não passa pelo field_validator, e a execução
        # sem alvo nenhum seria aceita.
        if not self.instance_ids and not self.profile_ids:
            raise ValueError("informe instance_ids ou profile_ids")
        return self


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
    capability: str | None = None
    commit_selector: str | None = None
    band_guard: list[str] = []
    bindings: dict[str, str] = {}
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
