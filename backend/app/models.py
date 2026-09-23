"""Contratos validados (Pydantic). Espelham docs/api-contract.md."""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

# `workers.protocol` não importa nada do app: é o contrato puro entre central e agente. Reaproveitar `WorkerDevice`
# e `WorkerResources` aqui evita duas definições da mesma coisa — o que o worker declara é o que a API mostra.
from .workers.protocol import WorkerDevice, WorkerResources


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


class CommandState(StrEnum):
    """Estados de um comando do painel. Mesmo vocabulário que `ActionStatus` já dava às ações da IA.

    `rejected` e `uncertain` são os dois que a interface mais precisa distinguir: o primeiro garante que NADA
    aconteceu no aparelho (recusado antes de despachar); o segundo diz que não sabemos, e por isso nada é
    repetido sozinho. E `cancel_requested` não é `cancelled`: pedir não é ter conseguido.
    """

    created = "created"
    dispatched = "dispatched"
    acked = "acked"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    uncertain = "uncertain"
    rejected = "rejected"
    cancel_requested = "cancel_requested"
    cancelled = "cancelled"


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
    #: Os aplicativos de que este plano PRECISA, por id de app. Um fluxo que usa dois apps (copiar algo das
    #: Configurações e colar no Instagram) não tinha como dizer isso, e a pendência só aparecia depois — como
    #: etapa que falha ou item bloqueado, sem ação clara. Vazio = só `app_id`, como sempre foi.
    required_apps: list[str] = []
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
    # Máquina que hospeda este aparelho; nulo = esta. É o que permite navegar servidor → dispositivo → tarefa.
    worker_id: str | None = None
    # Verbos que ESTE aparelho aceita. O painel usa para não oferecer botão que não faria nada — e a mesma lista
    # alimenta a recusa explicada no pré-voo, para os dois lados contarem a mesma história.
    supported_verbs: list[str] = []
    # Capacidades DECLARADAS: o que o aparelho é, e não só que verbo ele aceita (migração 019). Nulo/vazio = não
    # se sabe, que é diferente de "não tem" — e o que não se sabe nunca vira recusa. São o que permite ao pré-voo
    # dizer "este pacote é só ARM e este aparelho não traduz" ANTES de agendar.
    device_kind: str | None = None            # emulator | physical | container
    system_image: str | None = None
    api_level: int | None = None
    abis: list[str] = []
    play_store: bool | None = None            # tem Google Play Services? nulo = não se sabe
    # Inventário conferido contra o que o worker DECLARA hospedar (item 4.5; achado #47). `divergent` quer dizer
    # que as fontes discordam — e aí verbo destrutivo é recusado, porque `reset` agiria num aparelho e a tela em
    # outro. Nulo = conferido, ou worker desconectado (declaração de quem não está lá não confirma nada).
    inventory_state: str | None = None        # divergent | nulo
    inventory_detail: str | None = None
    # Instância criada em tempo de execução a partir de um aparelho anunciado por um worker, sem editar YAML.
    origin: str = "config"                    # config | dynamic
    tunnel_port: int | None = None            # porta local daqui que o túnel encaminha
    remote_adb_port: int | None = None        # porta de ADB do lado do worker


class WorkerDeviceProposal(BaseModel):
    """Aparelho que um worker anuncia e que ainda não é instância deste parque (item 4.5).

    É o que permite "conectar servidores novos e executar os mesmos comandos" sem editar `config.yaml`: o painel
    mostra o que a máquina nova oferece, e um clique transforma em instância com porta de túnel alocada aqui.
    """

    worker_id: str
    worker_name: str | None = None
    serial: str
    avd_name: str | None = None
    state: str = "unknown"
    adb_port: int | None = None


class AdoptDeviceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Serial do aparelho DENTRO do worker, como o agente o anunciou (ex.: `emulator-5554`).
    serial: str = Field(min_length=1, max_length=80)
    #: Id da instância a criar. Vazio = o próximo livre do prefixo configurado.
    instance_id: str | None = Field(default=None, max_length=60)


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
    # "Conectado" verificado há tempo demais: continua sendo o que se observou, mas deixa de valer como verdade
    # de agora — a porta relê o aparelho antes da tarefa, e o cartão diz que o dado é velho.
    stale: bool = False


#: Política de localidade do perfil: o que fazer quando o servidor onde os dados vivem não está disponível.
#: `wait` (padrão) espera aquele servidor voltar — ninguém reautentica a conta noutro aparelho sozinho.
#: `reauth_elsewhere` é DECISÃO DE PESSOA: aceita que usar o perfil em outro servidor exige login de novo.
OfflinePolicy = Literal["wait", "reauth_elsewhere"]
OFFLINE_POLICY_PADRAO = "wait"


class ProfileLocality(BaseModel):
    """Onde os dados deste perfil VIVEM (item 4.4 / E9).

    A sessão do Instagram mora na partição de dados de um aparelho, no disco de uma máquina. `worker_id` é essa
    máquina (`None` = este servidor), fotografada quando o vínculo foi feito. `moved` é o fato que antes não
    existia: o id lógico aponta hoje para outro servidor ou outro aparelho físico, então o que o central afirma
    sobre a sessão deixou de valer. `known=False` é vínculo anterior à migração 023 — e o que não se sabe nunca
    invalida nada.
    """

    worker_id: str | None = None
    worker_name: str | None = None
    worker_state: str | None = None            # online | degraded | offline | maintenance
    known: bool = False                        # a localidade foi registrada neste vínculo
    available: bool = True                     # o servidor onde os dados vivem está respondendo
    moved: bool = False                        # o id lógico mudou de servidor/aparelho desde o vínculo
    physical_id: str | None = None             # impressão digital do aparelho no momento do vínculo
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
    #: Onde os dados deste perfil vivem. `None` = sem vínculo, então não há localidade a afirmar.
    locality: ProfileLocality | None = None
    offline_policy: OfflinePolicy = OFFLINE_POLICY_PADRAO
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
    #: O que fazer quando o servidor onde os dados vivem não está disponível. Ver `OfflinePolicy`.
    offline_policy: OfflinePolicy | None = None
    #: Mudar de SERVIDOR um perfil com sessão pronta é decisão de pessoa: a sessão de lá não existe. Sem esta
    #: confirmação explícita a troca é recusada com 409, e o painel explica o que vai acontecer.
    confirm_locality_change: bool = False


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
    # Identidade VISUAL: descreve a pessoa, não como ela escreve. Fica guardada e aparece no portal, mas NÃO entra
    # no prompt (não está em `_TRACOS`): mandar aparência e cenário de foto em toda geração de texto é custo sem
    # retorno. Quem usa isto é quem escolhe/produz a foto, não o modelo que redige.
    appearance: str | None = Field(default=None, max_length=600)
    visual_style: str | None = Field(default=None, max_length=600)
    photo_scenario: str | None = Field(default=None, max_length=600)


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


class CapabilityDTO(BaseModel):
    """O catálogo como o portal precisa vê-lo: o que a ação faz, se tem efeito, e qual a política padrão."""

    key: str
    title: str
    side_effect: bool = False
    risk: str = "low"
    default_policy: str = "autonomous"
    limit_bucket: str | None = None
    needs_draft: bool = False
    bindings: list[str] = Field(default_factory=list)
    # Nas ações que escrevem, o texto é opcional de propósito: o normal é vir `content_brief` (a intenção) e cada
    # perfil escrever a sua versão. Quem consome o catálogo precisa enxergar esses argumentos.
    optional_bindings: list[str] = Field(default_factory=list)


class ProfilePolicyDTO(BaseModel):
    limits: dict[str, int] = Field(default_factory=dict)
    capabilities: dict[str, str] = Field(default_factory=dict)      # política EFETIVA por ação
    defaults: dict[str, str] = Field(default_factory=dict)          # o que o catálogo propõe, para comparação


class ProfilePolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: dict[str, int] | None = None
    capabilities: dict[str, Literal["autonomous", "approval_required", "manual_only", "disabled"]] | None = None


class ApprovalDecision(BaseModel):
    """Os três verbos do §17. `content` só faz sentido em `edit` — é o texto que realmente será enviado."""

    model_config = ConfigDict(extra="forbid")
    verb: Literal["approve", "edit", "reject"]
    content: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=400)


class ApprovalDecisionItem(ApprovalDecision):
    """Uma decisão dentro de um lote: o mesmo contrato, mais o id de quem está sendo decidido."""

    id: str = Field(min_length=1, max_length=120)


class ApprovalBatchBody(BaseModel):
    """Decidir os N textos de uma execução de uma vez, cada um com o seu verbo — aprovar uns, editar outros."""

    model_config = ConfigDict(extra="forbid")
    decisions: list[ApprovalDecisionItem] = Field(min_length=1, max_length=50)


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
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class AppVerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str = Field(min_length=3, max_length=120)
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class StoreBody(BaseModel):
    """Ações sobre o aparelho-loja. O pacote é obrigatório: a loja não assume um aplicativo por omissão."""

    model_config = ConfigDict(extra="forbid")
    package: str | None = Field(default=None, min_length=3, max_length=120)
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class ReleaseLifecycleBody(BaseModel):
    """Um verbo por chamada, no mesmo formato das aprovações — quatro rotas diriam a mesma coisa em quatro lugares."""

    model_config = ConfigDict(extra="forbid")
    verb: Literal["canary", "promote", "quarantine", "rollback", "distribute"]
    instance_id: str | None = Field(default=None, max_length=120)   # obrigatório em canary e rollback
    note: str | None = Field(default=None, max_length=300)
    # Rollback preservando dados pode ser recusado pelo Android. Reinstalar resolve, mas APAGA a sessão — então
    # quem chama tem de dizer isso de propósito. A API nunca escolhe esse caminho sozinha.
    confirm_reinstall: bool = False
    # Só para `distribute`: "instalar em todos agora". O rodízio liga os aparelhos pendentes dentro das vagas, em vez de
    # esperar que cada um pegue uma tarefa.
    eager: bool = False
    #: Chave de idempotência do COMANDO: reenviar a mesma requisição devolve o comando original em vez de
    #: abrir um efeito novo. Opcional — sem ela, cada chamada é um pedido novo.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class ReleaseState(StrEnum):
    """Este ARQUIVO pode ser instalado? Integridade, assinatura e compatibilidade — nada sobre funcionar."""

    imported = "imported"
    inspected = "inspected"
    validated = "validated"
    installable = "installable"
    invalid = "invalid"
    incompatible = "incompatible"


class ReleaseChannel(StrEnum):
    """Esta VERSÃO já provou que funciona? Segundo eixo, independente de `ReleaseState`.

    Separar os dois evita a confusão de um estado só: um arquivo íntegro e assinado (`installable`) pode nunca ter
    aberto em aparelho nenhum (`candidate`), e uma versão promovida continua precisando do hash conferido antes de
    cada instalação.
    """

    candidate = "candidate"        # importada, nunca provada num aparelho
    canary = "canary"              # em prova num aparelho só
    promoted = "promoted"          # abriu e sobreviveu no canário
    quarantined = "quarantined"    # falhou a prova; instalação bloqueada até alguém decidir o contrário
    rolled_back = "rolled_back"    # substituída de propósito por uma versão anterior


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


class ReleaseValidationDTO(BaseModel):
    """Uma prova observada num aparelho. É o que promove uma release — não a lembrança de quem clicou."""

    instance_id: str
    stage: Literal["install", "launch"]
    ok: bool
    detail: str | None = None
    observed_at: str


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
    # `store` = copiado do aparelho-loja, onde o usuário instalou pela Play Store. A coluna é TEXT sem CHECK: um valor
    # fora desta lista gravado no banco quebraria a listagem INTEIRA, não só aquela release — mude aqui e no TS juntos.
    source_type: Literal["inbox", "upload", "store"]
    source_reference: str | None = None
    imported_at: str
    status: ReleaseState
    detail: str | None = None
    channel: ReleaseChannel = ReleaseChannel.candidate
    channel_at: str | None = None
    channel_detail: str | None = None
    canary_instance_id: str | None = None
    validations: list[ReleaseValidationDTO] = []
    files: list[ReleaseFileDTO] = []
    devices: list[str] = []            # aparelhos com esta release instalada
    #: A QUEM este conjunto serve, lido dos próprios arquivos: ABIs do pacote e faixas de densidade dos splits
    #: (ex.: `["x86_64", "xhdpi"]`). Um conjunto copiado da loja é o conjunto da VM-loja: específico dela. Sem
    #: isto, quem olha a tela não tem como saber que aquele conjunto é de uma configuração só — e o aparelho de
    #: outra densidade recebia `config.xhdpi` calado, pela regra conservadora.
    serves: list[str] = []


class WorkerDTO(BaseModel):
    """Uma máquina que hospeda aparelhos.

    `state` é o que a interface mostra (manutenção ganha), e `observed_state` é o que se observa da conexão. Os
    dois ficam aqui de propósito: um worker em manutenção continua online, e esconder isso atrapalharia quem está
    diagnosticando. `connected` é o socket agora; `last_seen_at` é a idade do dado — sem ela não há como saber que
    a tela está velha.
    """

    id: str
    name: str
    os: str | None = None
    os_version: str | None = None
    agent_version: str | None = None
    appium_mode: str = "central"
    appium_url: str | None = None
    max_slots: int = 1
    verbs: list[str] = []
    state: str                                  # online | offline | degraded | maintenance
    observed_state: str
    maintenance: bool = False
    state_detail: str | None = None
    connected: bool = False
    #: Este worker É o servidor central (`workers/local.py`). A interface precisa saber: o central tem cartão
    #: próprio na Infraestrutura, e sem esta marca ele apareceria duas vezes — uma como "este servidor" e outra
    #: como um worker qualquer, com o mesmo nome e os mesmos aparelhos.
    local: bool = False
    resources: WorkerResources = WorkerResources()
    devices: list[WorkerDevice] = []
    enrolled_at: str
    last_seen_at: str | None = None
    #: Estado do TRANSPORTE (o túnel SSH: `scripts/worker-tunnel.ps1`), à parte do `state` observado pela batida
    #: do agente — achado #179. `up` | `down` | `unknown` (nunca sondado, ou worker local, que não tem túnel).
    #: A queda do túnel derrubava seis aparelhos "sem ADB" e um worker "sem batida" sem que nada dissesse a causa;
    #: aqui ela vira um campo próprio, sondado por conexão TCP nas portas locais que o túnel encaminha.
    transport_state: str | None = None
    transport_detail: str | None = None
    transport_since: str | None = None


class WorkerEnrollBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=120)


class WorkerMaintenanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool


class WorkerRemoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Desconecta o canal e ignora comando em voo — para a máquina que nunca mais volta.
    force: bool = False


class CommandDTO(BaseModel):
    """O que a interface acompanha depois de pedir uma ação. As marcas de tempo contam a história por si:
    `dispatched_at` sem `acked_at` é "entreguei e não sei se chegou"; `finished_at` com `state=uncertain` é
    "acabou e continuo sem saber o efeito"."""

    id: str
    instance_id: str
    worker_id: str | None = None
    #: Onde o aparelho morava quando o comando foi aberto. Diferente de `worker_id`, que só é carimbado quando o
    #: comando SAI para o agente: verbo de ADB em aparelho remoto sai daqui pelo túnel e por isso nunca tem
    #: `worker_id` — mas rodou na outra máquina, e o histórico precisa dizer isso.
    host_worker_id: str | None = None
    verb: str
    state: CommandState
    fence: int
    requested_by: str
    reason: str | None = None           # motivo da recusa, ou o que deu errado — texto para humano
    #: A cauda do log do emulador, quando o desfecho foi negativo num verbo que sobe o aparelho. Sai do
    #: `result.data` do executor (`devices/emulator.log_do_emulador`), com redação de segredo, e vale para as
    #: duas máquinas: o log do emulador REMOTO não tinha contraparte nenhuma, e o operador recebia uma frase.
    emulator_log: str | None = None
    attempt: int = 0
    created_at: str
    dispatched_at: str | None = None
    acked_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class DeviceAppStateDTO(BaseModel):
    instance_id: str
    package_name: str
    desired_release_id: str | None = None
    installed_release_id: str | None = None
    observed_version_name: str | None = None
    observed_version_code: int | None = None
    observed_splits: list[str] = []
    expected_splits: list[str] = []     # o que ESTE aparelho deveria ter; vazio = o conjunto inteiro
    first_install_time: str | None = None
    last_update_time: str | None = None
    state: InstalledAppState
    pending_op: str | None = None
    verified_at: str | None = None
    drift_kind: str | None = None
    detail: str | None = None
    previous_release_id: str | None = None      # para onde o rollback volta
    last_operation: str | None = None           # install | reinstall | upgrade | downgrade | rollback | uninstall


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
    # Máquina que hospeda este aparelho. `null` devolve o aparelho a esta máquina. Existe porque amarrar
    # instância a worker exigia `UPDATE` direto no banco — e o que não tem rota não tem como ser operado.
    worker_id: str | None = Field(default=None, max_length=64)
    #: Mudar de máquina um aparelho que hospeda perfil com sessão pronta apaga o acesso àquela sessão: os dados
    #: ficam no disco da máquina antiga. Sem esta confirmação a troca é recusada com 409 (item 4.4 / E9).
    confirm_locality_change: bool = False


class InstanceActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    app_id: str | None = None
    # Reenviar a MESMA chave devolve o comando original em vez de agir de novo. Quem não manda chave aceita que
    # um reenvio por rede instável possa virar dois comandos — por isso o frontend manda.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class CommandResolveBody(BaseModel):
    """A decisão de uma pessoa sobre um comando `uncertain`. `note` é o que ela observou — o que separa
    "marquei como sucesso" de "abri o aparelho, os dados estavam apagados, então o reset aconteceu"."""

    model_config = ConfigDict(extra="forbid")
    outcome: Literal["succeeded", "failed", "cancelled"]
    note: str | None = Field(default=None, max_length=400)
    requested_by: str | None = Field(default=None, max_length=60)


class CommandCancelBody(BaseModel):
    """O pedido de cancelamento de um comando ainda aberto. Pedir não é ter cancelado: o desfecho continua vindo
    de quem executa, e por isso aqui não há `outcome` nenhum para escolher."""

    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=400)
    requested_by: str | None = Field(default=None, max_length=60)


class BulkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Teto do PARQUE, não do código: com 14 aparelhos de tarefa, 'Selecionar todas' + uma ação em lote era
    # recusada com 422 antes de tocar em coisa alguma.
    ids: list[str] = Field(min_length=1, max_length=64)
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
    # Mesmo motivo do lote: o limite acompanha o tamanho do parque (e a soma das vagas dos workers), não um
    # número herdado de quando o projeto tinha 10 emuladores.
    instance_ids: list[str] = Field(default_factory=list, max_length=64)
    profile_ids: list[str] = Field(default_factory=list, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    mode: Literal["plan", "execute"] = "execute"
    # Resposta à recusa do pré-voo: "seguir só com os aptos". Por omissão é `False` porque criar metade da
    # execução sem que ninguém tenha pedido seria decidir pelo operador qual parte do trabalho não acontece.
    only_ready: bool = False

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
    claimed_by: str | None = None             # backend que assumiu esta etapa (migração 016); nulo = nunca despachada
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
    # ONDE isto rodou, fotografado no plano e re-fotografado no despacho (migração 022). Nulo = execução anterior
    # à fotografia, ou aparelho que nunca foi despachado — e o que não se sabe não afirma nada.
    worker_id: str | None = None        # máquina que hospeda o aparelho
    hosted_by: str | None = None        # backend que despachou (OWNER_ID)
    device_serial: str | None = None    # endereço de ADB no momento
    physical_id: str | None = None      # impressão digital do aparelho por trás do id lógico
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
    # Disjuntor de conta de IA (achado #90): dispara em cobrança/credencial, independente de `configured`
    # (a chave existe e é válida — o provedor está recusando por outro motivo, e não some sozinho).
    account_blocked: bool = False
    account_blocked_reason: str | None = None


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
    #: Commit em execução e última migração aplicada. Existem porque `version` é uma constante no código ("0.1.0")
    #: e não respondia a única pergunta que importa depois de um deploy: **este processo é o código novo?** Sem
    #: isso, o parque rodou por um dia inteiro um backend anterior às migrações 016/017 sem ninguém notar. `None`
    #: quando o diretório `.git` não veio junto (instalação por cópia) — dizer "não sei" é melhor que mentir.
    commit: str | None = None
    migration: str | None = None
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
