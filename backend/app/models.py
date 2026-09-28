"""Contratos validados (Pydantic). Espelham docs/api-contract.md.

Este arquivo está sendo fatiado por contexto (design §16, fase K), com reexport, nesta ordem: primeiro os corpos de
requisição, depois os DTOs de infraestrutura, por último os tipos de domínio. Os corpos que só a API usa já moram em
`app/modules/<contexto>/presentation/schemas.py`; os nomes abaixo são os MESMOS objetos (o `is` vale, conferido em
`tests/test_models_fatiado.py`), então `from app.models import ProfileCreate` continua valendo até cada importador
migrar. Não acrescente corpo novo aqui: ele nasce na apresentação do contexto.

Os corpos que ficaram aqui ficaram por um motivo: dependem de tipo de domínio que ainda mora neste arquivo
(`ProfilePatch` → `OfflinePolicy`; `PersonaCreate`/`PersonaPatch` → `PersonaTraits`; `BulkBody` →
`InstanceActionBody`) — o módulo de apresentação importá-lo daqui fecharia um ciclo de import de topo —, o domínio
também os importa (`InstanceActionBody` no despacho, `ManualInput` no gerenciador de aparelhos, `RunCreate`,
`DistributeSpec` e `ResolveBody` na fila), ou o contexto de destino ainda não tem casa na apresentação
(`TrainingStartBody`/`TrainingSaveBody`, de habilidades, e `LoginBody`, da plataforma).
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import (BaseModel, ConfigDict, Field, SecretStr, computed_field,
                      field_validator, model_validator)

# Reexport dos corpos movidos (ver o docstring). Importados, não usados aqui: é o que mantém `app.models` como
# ponto de import dos importadores antigos.
from .modules.applications.presentation.schemas import (  # noqa: F401
    APP_CATEGORIES, AppCategory, AppInput, AppInstallBody, AppPatch, AppVerifyBody, ReleaseImportBody,
    ReleaseLifecycleBody, SignatureApprovalBody, StoreBody)
from .modules.execution.presentation.schemas import (  # noqa: F401
    ApprovalBatchBody, ApprovalDecision, ApprovalDecisionItem)
from .modules.fleet.presentation.schemas import (  # noqa: F401
    AdoptDeviceBody, CommandCancelBody, CommandResolveBody, InstancePatch, InstanceProvisionBody, ReleaseBody,
    ServerLimitsPatch, WorkerEnrollBody, WorkerMaintenanceBody, WorkerRemoveBody)
from .modules.identity.domain.persona import BIOGRAPHY_SCHEMA_VERSION
from .modules.identity.presentation.schemas import (  # noqa: F401
    CredentialUpdate, MemoryCreate, PersonaPreviewBody, PolicyGroupCreate, PolicyGroupPatch, PolicyName,
    ProfileAccountCreate, ProfileAccountPatch, ProfileCreate, ProfilePolicyPatch)
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


class StepOrigin(BaseModel):
    """De que skill, versão e nó esta etapa saiu, quando o plano foi compilado de uma skill (design §12.1).

    Mora no próprio `Plan` porque a recuperação, a expansão do `for_each` e a revisão releem `runs.plan` e reinserem
    etapas: sem a origem aqui, a trilha se perderia no primeiro replanejamento. `node_id == template_key or key`.
    """

    skill_id: str
    skill_version: int
    node_id: str
    strategies: list[str] = []


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
    # Item 12.1: o app em que ESTA etapa roda — um comando pode atravessar apps (pegar um código no Outlook e
    # usá-lo no Instagram). `None` = o app do plano. Receita, catálogo, sessão e memória da etapa seguem este app.
    app_id: str | None = None
    # Só em plano compilado de skill. Fora da serialização quando vazio: `runs.plan` e `plan_versions.steps` de
    # todo plano que não veio de skill continuam byte a byte iguais (e o painel ignora campo que não conhece).
    origin: StepOrigin | None = Field(default=None, exclude_if=lambda v: v is None)


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
    #: A tela deste frame foi classificada como sensível (contrato C4 do adendo v0.20): não há imagem, e
    #: `GET /frame` responde 404 `sensitive_screen`. O frame existe mesmo assim — é ele que tira do ar a imagem
    #: anterior e mantém o tamanho da tela para o controle manual.
    sensitive: bool = False


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


class StreamInfo(BaseModel):
    """Saúde da TELA ao vivo, separada da saúde do APARELHO (ver `devices/stream.py`).

    Existe porque o painel tinha só `frame.stale`: "Desatualizado" dizia a mesma coisa para aparelho desligado,
    worker fora do ar, captura falhando e aparelho vivo com captura atrasada. Um frame antigo não diz que o
    aparelho está online — e aparelho que responde a comando sem frames novos é `stale`, nunca `device_offline`.
    """

    #: `paused` (adendo v0.20, C3): online, prévia suspensa por falta de interesse — não é `stale` nem erro.
    status: Literal["live", "stale", "capture_error", "no_frame", "device_offline", "device_hibernated",
                    "worker_offline", "paused"]
    detail: str
    last_frame_at: str | None = None
    #: Idade do último frame, em segundos, medida no backend (relógio monotônico). `None` = nenhum frame.
    frame_age_s: float | None = None
    last_capture_error: str | None = None
    last_capture_error_at: str | None = None
    consecutive_capture_failures: int = 0


class ConnectivityInfo(BaseModel):
    """Internet DENTRO do aparelho, separada do estado do aparelho (ver `devices/conectividade.py`).

    `online` só diz que o adb responde. Em 25/09/2026 o android-06 ficou `online`, "pronto em 114 s", sem resolver
    nome nenhum: o Instagram tentou o login sem DNS e mostrou "An unexpected error occurred". Aparelho sem internet
    continua `online` — as ações locais (instalar por adb, abrir app, tela) funcionam; as que precisam de rede não.
    """

    state: Literal["unknown", "healthy", "degraded", "unavailable"] = "unknown"
    #: Há rota default em alguma tabela do convidado.
    route: bool | None = None
    #: O resolvedor do Android devolveu endereço para o host de teste.
    dns: bool | None = None
    #: Conexão TCP na 443 do host de teste (por NOME: depende do DNS).
    tcp_443: bool | None = None
    #: O Android marcou alguma rede como VALIDATED — é a sonda HTTPS do próprio sistema (NetworkMonitor).
    validated: bool | None = None
    checked_at: str | None = None
    detail: str = "Conectividade ainda não verificada desde que o aparelho entrou no ar."


class ReadinessInfo(BaseModel):
    """Até onde o aparelho chegou na escada de prontidão — separado de `state`, como `stream` e `connectivity`.

    Existe pelo wake remoto de 25/09/2026: processo no ar, adb `device`, `boot_completed=1` restaurado do snapshot
    — e o framework congelado (`service check`, `dumpsys`, `screencap` travando). O comando fechou `succeeded` e o
    aparelho ficou `online`. `ready` agora exige o framework respondendo; o degrau em que parou fica visível.
    """

    phase: Literal["not_running", "process_running", "adb_device", "boot_completed", "android_responsive",
                   "ready"] = "not_running"
    detail: str = ""
    since: str | None = None


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
    stream: StreamInfo | None = None
    connectivity: ConnectivityInfo = ConnectivityInfo()
    readiness: ReadinessInfo = ReadinessInfo()
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


# ---------------------------------------------------------------- perfis do Instagram
class SessionStatus(StrEnum):
    """Sessão é CACHE do que se observou no aparelho, nunca a verdade. É a sessão de UMA conta NUM aparelho
    (`account_sessions`, 049), com o mesmo vocabulário para app com provedor de sessão e sem: `auth_required` é o
    antigo `logged_out` da 037; `needs_person` é o que só uma pessoa resolve sem ser desafio nem conta errada."""

    unknown = "unknown"
    auth_required = "auth_required"
    auth_challenge = "auth_challenge"
    wrong_account = "wrong_account"
    session_ready = "session_ready"
    needs_person = "needs_person"


class CredentialInfo(BaseModel):
    """O que a API conta sobre a credencial. A senha NUNCA aparece aqui — o campo simplesmente não existe."""

    configured: bool = False
    login_identifier: str | None = None
    status: str | None = None              # active | invalid
    failed_attempts: int = 0
    blocked_until: str | None = None
    updated_at: str | None = None
    last_used_at: str | None = None
    #: Consentimento POR CONTA (ADR-040): quando e por quem a pessoa autorizou a automação a digitar esta senha.
    #: Nulo = guardada, mas ninguém a digita (nem `type_secret`, nem o provedor de sessão).
    consent_at: str | None = None
    consent_by: str | None = None


class SessionInfo(BaseModel):
    status: SessionStatus = SessionStatus.unknown
    instance_id: str | None = None
    observed_username: str | None = None
    verified_at: str | None = None
    detail: str | None = None
    # "Conectado" verificado há tempo demais: continua sendo o que se observou, mas deixa de valer como verdade
    # de agora — a porta relê o aparelho antes da tarefa, e o cartão diz que o dado é velho.
    stale: bool = False


class ActionGate(BaseModel):
    """Uma ação oferecida (ou não) AGORA, com o motivo. A tela só mostra; quem decide é o backend."""

    allowed: bool
    reason: str | None = None


class AppOnDevice(BaseModel):
    """O app da conta no aparelho vinculado, como a camada de releases o OBSERVOU (`device_app_state`).

    `state=None` = nunca inspecionado: não se sabe se está instalado — o que é diferente de "não está".
    """

    package: str
    state: str | None = None
    version_name: str | None = None
    version_code: int | None = None
    verified_at: str | None = None
    pending_op: str | None = None
    detail: str | None = None


class SessionActions(BaseModel):
    """Fonte única do que a tela oferece para a sessão de um perfil (Conectar / Verificar conta / Sair).

    Antes, cada botão decidia por uma flag solta (senha guardada? aparelho vinculado?) e "Conectar" aparecia num
    aparelho sem o Instagram. As camadas são independentes e cada uma só libera a próxima: vínculo → app
    presente (observado) → credencial → sessão. `phase` resume onde a cadeia parou.
    """

    phase: Literal["no_device", "app_unknown", "app_missing", "app_installing", "no_credential",
                   "authenticating", "logged_out", "authenticated", "challenge", "wrong_account", "unknown"]
    detail: str
    connect: ActionGate
    verify: ActionGate
    logout: ActionGate
    #: Reler do aparelho se o pacote está instalado (e em que versão). É a saída de `app_unknown`.
    inspect_app: ActionGate


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


class PersonaTraits(BaseModel):
    """Os traços que descrevem a VOZ da persona. Tudo é opcional: o que estiver vazio simplesmente não vai ao modelo.

    Desde a migração 047 as três chaves visuais (`appearance`, `visual_style`, `photo_scenario`) moram em
    `PersonaVisual`; aqui só fica como a pessoa escreve. `extra="forbid"` continua: uma chave desconhecida em
    `traits` é dado errado, não dado a mais.
    """

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


class PersonaTraitsEdit(PersonaTraits):
    """`traits` como um CLIENTE pode mandar: a voz mais as três chaves visuais que o painel de hoje ainda escreve
    dentro de `traits` (`ProfileDetail.tsx`). O servidor as move para `visual` em vez de responder 422 — a
    compatibilidade fica na borda, e o dado gravado é o novo."""

    appearance: str | None = Field(default=None, max_length=600)
    visual_style: str | None = Field(default=None, max_length=600)
    photo_scenario: str | None = Field(default=None, max_length=600)


class PersonaVisual(BaseModel):
    """Identidade VISUAL: descreve a pessoa, não como ela escreve. Não entra no prompt de texto; alimenta a receita
    de imagem (`modules/identity/domain/persona_image.py`) e aparece no painel."""

    model_config = ConfigDict(extra="forbid")
    appearance: str | None = Field(default=None, max_length=600)
    visual_style: str | None = Field(default=None, max_length=600)
    photo_scenario: str | None = Field(default=None, max_length=600)
    palette: str | None = Field(default=None, max_length=200)
    age_presentation: str | None = Field(default=None, max_length=80)
    gender_presentation: str | None = Field(default=None, max_length=80)


class BioOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    birthplace: str | None = Field(default=None, max_length=120)
    hometown: str | None = Field(default=None, max_length=120)
    nationality: str | None = Field(default=None, max_length=60)


class BioHome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=80)
    residence: str | None = Field(default=None, max_length=200)      # "apartamento com a irmã", "casa dos pais"


class BioWork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profession: str | None = Field(default=None, max_length=120)
    employer: str | None = Field(default=None, max_length=120)
    education: list[str] = Field(default_factory=list, max_length=10)


class BioLife(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marital_status: str | None = Field(default=None, max_length=60)
    children: int | None = Field(default=None, ge=0, le=20)
    history: list[str] = Field(default_factory=list, max_length=20)  # fatos marcantes, um por item


class BioBeliefs(BaseModel):
    """Guardado, NÃO enviado ao modelo nem exigido para a biografia contar como completa: mandar religião e
    posicionamento político para o prompt é decisão do dono, ainda pendente (fica fora de `PERSONA_BIO_FIELDS`)."""

    model_config = ConfigDict(extra="forbid")
    religion: str | None = Field(default=None, max_length=80)
    politics: str | None = Field(default=None, max_length=120)


class BioTastes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Espelho de `traits.interests` gravado pela 047; a VOZ continua lendo `traits.interests` (fonte única do prompt).
    interests: list[str] = Field(default_factory=list, max_length=30)
    hobbies: list[str] = Field(default_factory=list, max_length=30)
    preferences: list[str] = Field(default_factory=list, max_length=30)
    dislikes: list[str] = Field(default_factory=list, max_length=30)


class PersonaBiography(BaseModel):
    """Quem a pessoa é fora da tela, por seção. Cada seção é mesclada no servidor num PATCH (nunca substituída
    inteira); `schema_version` diz que forma este JSON tem."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = BIOGRAPHY_SCHEMA_VERSION
    #: Idade aproximada, quando não há `birth_date` (a 047 a extrai do resumo). Nunca inventa nascimento.
    approx_age: int | None = Field(default=None, ge=0, le=120)
    origin: BioOrigin = BioOrigin()
    home: BioHome = BioHome()
    work: BioWork = BioWork()
    life: BioLife = BioLife()
    beliefs: BioBeliefs = BioBeliefs()
    tastes: BioTastes = BioTastes()


class PersonaGeneration(BaseModel):
    """Proveniência da persona: `manual`, `ai` (gerada por modelo) ou `legacy_persona` (dobrada pela 047)."""

    model_config = ConfigDict(extra="ignore")
    source: str | None = Field(default=None, max_length=40)
    persona_id: str | None = Field(default=None, max_length=120)     # linha de `personas` de origem (047)
    prompt: str | None = Field(default=None, max_length=2000)
    provider: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=120)
    usd: float | None = None
    at: str | None = None
    enriched_at: str | None = None


#: Os traços que DESCREVEM A VOZ, na ordem em que fazem sentido lidos de cima para baixo, com o rótulo que o
#: portal mostra. É a mesma lista que o construtor de contexto renderiza no bloco `<persona>` — fonte única, para
#: um campo novo não passar a ir ao modelo sem aparecer na conferência, nem o contrário.
PERSONA_VOICE_TRAITS: tuple[tuple[str, str], ...] = (
    ("personality", "personalidade"), ("tone", "tom"), ("formality", "formalidade"),
    ("typical_length", "tamanho típico da mensagem"), ("emojis", "uso de emojis"), ("slang", "gírias"),
    ("humor", "humor"), ("interests", "interesses"), ("dm_style", "estilo em mensagem direta"),
    ("comment_style", "estilo em comentário"), ("with_known", "com quem já conhece"),
    ("with_strangers", "com desconhecidos"), ("common_phrases", "expressões comuns"),
    ("forbidden_phrases", "expressões proibidas"), ("examples", "exemplos"),
)

#: O que da BIOGRAFIA vai ao modelo, como linhas curtas no bloco `<persona>`, além do nome e da idade calculada
#: (que vêm da identidade, não deste JSON). Caminho dentro de `biography` → rótulo. Fonte única, como a lista de
#: voz. `beliefs.religion` e `beliefs.politics` ficam de FORA de propósito: guardar é uma coisa, mandar ao modelo é
#: decisão do dono, ainda pendente. `tastes.interests` também fica de fora: os interesses já vão pela voz.
PERSONA_BIO_FIELDS: tuple[tuple[str, str], ...] = (
    ("home.city", "cidade onde mora"), ("work.profession", "profissão"), ("work.education", "formação"),
    ("tastes.hobbies", "hobbies"),
)


def voice_gaps(traits: PersonaTraits) -> list[str]:
    """Quais traços de voz esta persona não tem — pelo nome do campo, na ordem do prompt.

    Existe porque oito personas com só os traços básicos preenchidos dão ao modelo tom/formalidade/tamanho/emoji
    para distinguir oito vozes, e é pouco: sem exemplo, expressão comum e estilo em DM, as contas convergem para
    o mesmo jeito de escrever (achado #107). O portal mostra esta lista; quem decide preencher é o dono.
    """
    dados = traits.model_dump()
    return [campo for campo, _ in PERSONA_VOICE_TRAITS if not dados.get(campo)]


class PersonaVoiceDTO(BaseModel):
    """A persona como o CONSTRUTOR DE CONTEXTO a vê: voz, biografia e identidade — sem credencial, sem sessão, sem
    aparelho. É o que entra em `SocialContextDTO.persona` e no bloco `<persona>`; a `PersonaDTO` completa é para a
    API e herda daqui."""

    id: str
    name: str
    summary: str | None = None
    persona_prompt: str = ""
    traits: PersonaTraits = PersonaTraits()
    biography: PersonaBiography = PersonaBiography()
    #: Idade calculada de `birth_date` hoje; senão `biography.approx_age`. Nunca gravada.
    age: int | None = None
    gender: str | None = None
    locale: str | None = None
    #: Compatibilidade com a `PersonaDTO` antiga: a persona É o perfil, então `profile_id == id`.
    profile_id: str | None = None
    profile_username: str | None = None
    created_at: str
    updated_at: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def voice_gaps(self) -> list[str]:
        """Os campos de voz vazios, calculados — não há coluna para isso, e não haveria como mantê-la em dia."""
        return voice_gaps(self.traits)


class PersonaImageDTO(BaseModel):
    """Uma imagem da persona (migração 048). `url` é a rota que a serve pelo storage; nunca um caminho de disco."""

    id: str
    persona_id: str
    status: str                               # pending | ready | failed | refused
    source: str                               # generated | upload | imported_legacy
    is_primary: bool = False
    width: int | None = None
    height: int | None = None
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    aspect: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    created_at: str
    url: str


class PersonaDTO(PersonaVoiceDTO):
    """A pessoa inteira (evolução 2, onda A): voz + biografia + visual + proveniência + o que o perfil sempre expôs
    (conta do Instagram de cadastro, credencial, sessão, aparelho, política). `InstagramProfileDTO` é este MESMO
    objeto, pelo nome antigo, para o painel atual não quebrar.

    `username` é `None` quando a pessoa ainda não tem conta (a coluna guarda `''`; a tradução é aqui, na borda).
    `persona_id` e `profile_id` são o próprio `id`: o painel de hoje acha a persona do perfil por eles.
    """

    visual: PersonaVisual = PersonaVisual()
    generation: PersonaGeneration = PersonaGeneration()
    username: str | None = None
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    birth_date: str | None = None
    email: str | None = None
    persona_id: str | None = None
    #: Alias de `name`, mantido só porque `contexto.py` e o painel ainda o leem. Some com eles.
    persona_name: str | None = None
    #: Grupo de acesso (migração 036): políticas e limites herdados; o que o perfil mudou deliberadamente sobrepõe.
    policy_group_id: str | None = None
    policy_group_name: str | None = None
    status: str = "active"
    instance_id: str | None = None          # aparelho vinculado agora
    #: Onde os dados deste perfil vivem. `None` = sem vínculo, então não há localidade a afirmar.
    locality: ProfileLocality | None = None
    offline_policy: OfflinePolicy = OFFLINE_POLICY_PADRAO
    credential: CredentialInfo = CredentialInfo()
    session: SessionInfo = SessionInfo()
    #: O app da conta no aparelho vinculado. `None` = sem vínculo.
    app_on_device: AppOnDevice | None = None
    session_actions: SessionActions | None = None
    accounts_count: int = 0
    images: list[PersonaImageDTO] = Field(default_factory=list)
    primary_image_id: str | None = None
    last_verified_at: str | None = None
    last_activity_at: str | None = None


#: O nome antigo do MESMO objeto (`is`): quem importa `InstagramProfileDTO` continua funcionando.
InstagramProfileDTO = PersonaDTO


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=120)
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    birth_date: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=200)
    #: Desde a 047 a persona é a própria pessoa. Mandar aqui o id de uma persona SEM conta absorve a voz dela neste
    #: perfil (e a linha sem conta some); o id de outra pessoa com conta é recusado (`persona_in_use`).
    persona_id: str | None = Field(default=None, max_length=120)
    #: `null` desvincula do grupo: o perfil volta a herdar só do padrão do catálogo (as escolhas próprias ficam).
    policy_group_id: str | None = Field(default=None, max_length=120)
    instance_id: str | None = Field(default=None, max_length=60)
    #: `blocked` = a plataforma bloqueou a conta: o sistema respeita o bloqueio e não despacha tarefa nenhuma para
    #: este perfil até uma pessoa reativá-lo. `disabled` = o dono pausou. O banco já aceitava os três (migração 008).
    status: Literal["active", "blocked", "disabled"] | None = None
    #: O que fazer quando o servidor onde os dados vivem não está disponível. Ver `OfflinePolicy`.
    offline_policy: OfflinePolicy | None = None
    #: Mudar de SERVIDOR um perfil com sessão pronta é decisão de pessoa: a sessão de lá não existe. Sem esta
    #: confirmação explícita a troca é recusada com 409, e o painel explica o que vai acontecer.
    confirm_locality_change: bool = False


class PersonaDraft(BaseModel):
    """O que o MODELO devolve em `generate_persona` (e o que `enrich` recebe dele): a pessoa fictícia, sem
    proveniência nem conta. É também o esquema estrito da saída estruturada — todo campo aparece, vazio ou não."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    gender: str | None = Field(default=None, max_length=40)
    locale: str | None = Field(default=None, max_length=20)
    birth_date: str | None = Field(default=None, max_length=10)
    persona_prompt: str = Field(default="", max_length=4000)
    traits: PersonaTraits = PersonaTraits()
    visual: PersonaVisual = PersonaVisual()
    biography: PersonaBiography = PersonaBiography()


class PersonaCreate(BaseModel):
    """Cria uma PESSOA (sem conta em app nenhum). `first_name`/`last_name` vazios saem de `name` no primeiro espaço.
    `traits` aceita as chaves visuais antigas e as move para `visual`."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    persona_prompt: str = Field(default="", max_length=4000)
    traits: PersonaTraitsEdit = PersonaTraitsEdit()
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    birth_date: str | None = Field(default=None, max_length=10)
    gender: str | None = Field(default=None, max_length=40)
    locale: str | None = Field(default=None, max_length=20)
    biography: PersonaBiography = PersonaBiography()
    visual: PersonaVisual = PersonaVisual()
    #: Preenchido pelo servidor em `POST /personas/generate`; quem cria à mão pode deixar vazio (`source=manual`).
    generation: PersonaGeneration | None = None

    @field_validator("traits", mode="before")
    @classmethod
    def _traits_como_edicao(cls, v: object) -> object:
        return _traits_para_edicao(v)


class PersonaPatch(BaseModel):
    """PATCH parcial POR SEÇÃO: só o que vier muda. `traits`, `visual` e `biography` são MESCLADOS no servidor
    (`modules/identity/domain/persona.py::mesclar_secao`) — um campo explicitamente `null` apaga, um campo ausente
    fica como está. Nunca se substitui o JSON inteiro."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    persona_prompt: str | None = Field(default=None, max_length=4000)
    traits: PersonaTraitsEdit | None = None
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    birth_date: str | None = Field(default=None, max_length=10)
    gender: str | None = Field(default=None, max_length=40)
    locale: str | None = Field(default=None, max_length=20)
    biography: PersonaBiography | None = None
    visual: PersonaVisual | None = None

    @field_validator("traits", mode="before")
    @classmethod
    def _traits_como_edicao(cls, v: object) -> object:
        return _traits_para_edicao(v)


def _traits_para_edicao(v: object) -> object:
    """Quem monta o corpo em Python passa `PersonaTraits(...)` (a voz); o campo é `PersonaTraitsEdit`, subclasse com
    as chaves visuais antigas. Pydantic não aceita a instância da classe-mãe onde espera a filha, então ela vira o
    dicionário do que foi de fato informado."""
    if isinstance(v, PersonaTraits) and not isinstance(v, PersonaTraitsEdit):
        return v.model_dump(exclude_unset=True)
    return v


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
    app_id: str | None = None               # item 12.1: em que app a interação aconteceu
    created_at: str


class MemoryItemDTO(BaseModel):
    id: str
    profile_id: str
    subject: str
    content: str
    source: str                             # interaction | operator | system | observation (tela vista)
    app_id: str | None = None               # item 12.1: app de onde veio; None = fato geral da identidade
    interaction_id: str | None = None
    importance: float = 0.5
    confidence: float = 0.5
    occurrences: int = 1
    expires_at: str | None = None
    created_at: str
    updated_at: str
    last_used_at: str | None = None


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
    persona: PersonaVoiceDTO | None = None      # só voz e biografia: credencial não tem caminho até aqui
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
    # Achado #114: chaves de `capabilities` cuja política efetiva é mais FROUXA que `defaults` — para o portal
    # marcar visualmente em vez de deixar o afrouxamento silencioso (o perfil sempre pôde afrouxar; só não
    # aparecia em lugar nenhum).
    loosened: list[str] = Field(default_factory=list)
    # Grupo de acesso (migração 036). Ordem: o que o perfil mudou (`own`) → o grupo (`group`) → o padrão.
    group_id: str | None = None
    group_name: str | None = None
    own: dict[str, str] = Field(default_factory=dict)               # escolhas deliberadas do perfil (sobrepõem)
    group: dict[str, str] = Field(default_factory=dict)             # o que o grupo diz, para comparação
    origin: dict[str, Literal["own", "group", "default"]] = Field(default_factory=dict)
    own_limits: dict[str, int] = Field(default_factory=dict)
    group_limits: dict[str, int] = Field(default_factory=dict)
    limits_origin: dict[str, Literal["own", "group", "default"]] = Field(default_factory=dict)


class ProfileAccountDTO(BaseModel):
    """Uma conta do perfil NUM app (item 12.1; ADR-040: a Conta é a entidade única). O perfil é a identidade; cada
    app — e cada site, no navegador — tem a sua conta, com credencial, consentimento e sessão por aparelho."""

    id: str
    profile_id: str
    app_id: str
    app_name: str | None = None
    package: str | None = None
    handle: str = ""
    #: Conta de PORTAL ou site (app de navegador): o host onde a credencial pode ser digitada (ADR-040).
    host: str | None = None
    #: Com que identificador a conta entra (e-mail no Instagram; usuário no portal). Não é segredo.
    login_identifier: str | None = None
    status: str = "active"
    #: Sessão neste app NO APARELHO VINCULADO (`account_sessions`, 049): no Instagram gravada pelo provedor
    #: determinístico; nos demais, pelo operador (ou pela IA). Os escalares ficam por compatibilidade; `session`
    #: é a forma completa (com `stale`).
    session_status: str = "unknown"
    session_detail: str | None = None
    session_verified_at: str | None = None
    session: SessionInfo = Field(default_factory=SessionInfo)
    #: Conectar / Verificar / Sair para ESTA conta, pela mesma regra que a rota recusa (`social/sessao_gate.py`).
    session_actions: SessionActions | None = None
    #: Login automático existe para este app? (hoje só o Instagram). Sem ele, quem entra é a pessoa pelo Foco.
    automated_login: bool = False
    credential_configured: bool = False
    #: A credencial desta conta (só metadados; a senha não tem campo) e o consentimento dela.
    credential: CredentialInfo = Field(default_factory=CredentialInfo)
    consent_at: str | None = None
    notes: str = ""
    created_at: str
    updated_at: str


class TrainingStartBody(BaseModel):
    """Começa a gravar um treinamento (item 13.1). Exige o controle manual do aparelho (`lease_id`)."""

    model_config = ConfigDict(extra="forbid")
    intent: str = Field(min_length=1, max_length=400)
    lease_id: str = Field(min_length=1, max_length=120)
    app_id: str | None = Field(default=None, max_length=120)
    #: De quem é a demonstração (vínculo N:N): a persona escolhida entre as vinculadas ao aparelho. Sem ela, a única
    #: do aparelho; com duas e nenhuma escolhida, o treino fica sem perfil.
    profile_id: str | None = Field(default=None, max_length=120)


class TrainingSaveBody(BaseModel):
    """Salvar um treinamento como habilidade (item 13.2). `proposal` é a proposta REVISADA pela pessoa (omitida = a
    da IA como veio); `profile_ids`/`group_ids` = quem recebe (vazio = todos os perfis)."""

    model_config = ConfigDict(extra="forbid")
    proposal: dict[str, Any] | None = None
    profile_ids: list[str] = Field(default_factory=list, max_length=500)
    group_ids: list[str] = Field(default_factory=list, max_length=100)


class PolicyGroupMember(BaseModel):
    id: str
    username: str | None = None               # pessoa sem conta também pode estar num grupo


class PolicyGroupDTO(BaseModel):
    id: str
    name: str
    description: str = ""
    capabilities: dict[str, str] = Field(default_factory=dict)      # só o que o grupo muda em relação ao padrão
    limits: dict[str, int] = Field(default_factory=dict)
    loosened: list[str] = Field(default_factory=list)               # ações de risco alto que o grupo afrouxa
    members: list[PolicyGroupMember] = Field(default_factory=list)
    created_at: str
    updated_at: str


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
    # `store` = copiado do aparelho-loja, onde o usuário instalou pela Play Store. `builtin` = app embutido
    # (`apps[].builtin: true`) importado sozinho na subida do backend — hoje só o QA Messenger (item 6.3, #83).
    # A coluna é TEXT sem CHECK: um valor fora desta lista gravado no banco quebraria a listagem INTEIRA, não só
    # aquela release — mude aqui e no TS juntos.
    source_type: Literal["inbox", "upload", "store", "builtin"]
    source_reference: str | None = None
    #: O nome que o app mostra ao usuário, lido do base.apk na importação. `None` = release catalogada antes do
    #: catálogo visual, ou APK sem rótulo declarado — a interface cai no rótulo do registro e daí no pacote.
    label: str | None = None
    #: Há ícone extraído para esta release? O arquivo é servido por `GET /api/releases/{id}/icon`; o DTO só diz
    #: se vale a pena pedir, para a tela não tentar carregar uma imagem que não existe.
    has_icon: bool = False
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
    #: A versão que ESTE servidor roda — o que o agente daquela máquina deveria estar rodando. Sai junto com a
    #: dele para o painel poder mostrar as duas lado a lado: "agente defasado" sem dizer *defasado em relação a
    #: quê* manda o operador procurar o número em outro lugar.
    expected_agent_version: str | None = None
    #: O agente daquela máquina roda código diferente do deste servidor. Calculado, nunca gravado: a resposta
    #: muda quando o central é atualizado, e uma coluna guardaria a comparação de ontem.
    agent_outdated: bool = False
    #: Aceleração de virtualização declarada pelo agente (`kvm`, `kvm-inacessivel`, `kvm-ausente`). `None` = não
    #: se sabe (Windows, ou worker de protocolo antigo).
    accel: str | None = None
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
    #: A versão que "Instalar versão promovida" instalaria AGORA (a maior promovida do pacote). Vai no DTO para o
    #: botão dizer "Instalar Instagram 412.0 (promovida)" ANTES do clique. `None` = nada promovido: o verbo recusa.
    promoted_release_id: str | None = None
    promoted_version_name: str | None = None
    promoted_version_code: int | None = None
    #: Categoria da vitrine (loja de apps, migração 041). `None` = sem categoria.
    category: str | None = None


class InstanceActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    app_id: str | None = None
    # Reenviar a MESMA chave devolve o comando original em vez de agir de novo. Quem não manda chave aceita que
    # um reenvio por rede instável possa virar dois comandos — por isso o frontend manda.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


class LoginBody(BaseModel):
    """O que a tela de login manda. `operator` é o NOME que vai aparecer na auditoria — não é um usuário do
    sistema, e o backend não o inventa a partir de nada: sem alguém dizer quem é, a trilha continuaria dizendo
    `panel`.

    `token` só é exigido de quem ainda não estaria autorizado (chamada de fora do loopback). No loopback o
    token é opcional de propósito: exigi-lo ali quebraria o painel local, que sempre funcionou sem segredo.
    """

    model_config = ConfigDict(extra="forbid")
    operator: str = Field(min_length=2, max_length=60)
    token: SecretStr | None = None


class PanelSessionInfo(BaseModel):
    """A resposta de `GET /api/session`: quem sou eu, e o que esta origem precisa apresentar.

    `PanelSessionInfo` e nao `SessionInfo`: aquele nome ja e da sessao do Instagram dentro de um perfil, e duas
    classes com o mesmo nome no mesmo modulo nao colidem em tempo de import — a segunda simplesmente apaga a
    primeira, e o erro aparece longe daqui."""

    model_config = ConfigDict(extra="forbid")
    #: Nome do operador desta sessão, ou `None` quando ninguém fez login neste navegador.
    operator: str | None = None
    #: Esta origem precisa do `API_TOKEN` para logar? Falso no loopback, verdadeiro num host declarado.
    token_required: bool = False
    #: Quando esta sessão expira (ISO), ou `None` sem sessão.
    expires_at: str | None = None


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


class DistributeSpec(BaseModel):
    """"Distribuir entre servidores": quantos aparelhos de um app, escolhidos pela carga de cada máquina
    (`taskqueue/balanceamento.py`) em vez de marcados um por um. Para comando que não depende de conta."""

    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1, le=64)
    app_id: str = Field(min_length=1, max_length=80)


class RunCreate(BaseModel):
    """Execução por APARELHO (como sempre), por PERFIL ou DISTRIBUÍDA: `profile_ids` resolve para o aparelho
    vinculado a cada perfil; `distribute` deixa o balanceamento escolher N aparelhos do app entre os servidores.
    Quem pensa em "responda as mensagens da Mariana" não deveria precisar saber em qual emulador ela está."""

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
    distribute: DistributeSpec | None = None
    # ADR-040: a execução NÃO carrega credencial. `credentials`/`consent_credentials` (ADR-025) saíram: a senha é da
    # conta da persona (cofre, consentimento por conta) e a automação a digita de lá. `extra="forbid"` faz um
    # cliente antigo que ainda mande o campo receber 422 em vez de ser aceito em silêncio.

    @field_validator("instance_ids", "profile_ids")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _um_dos_dois(self) -> "RunCreate":
        # Validador de MODELO, não de campo: campo com valor padrão não passa pelo field_validator, e a execução
        # sem alvo nenhum seria aceita.
        if self.distribute is not None:
            if self.instance_ids or self.profile_ids:
                raise ValueError("distribute escolhe os aparelhos: não combine com instance_ids nem profile_ids")
            return self
        if not self.instance_ids and not self.profile_ids:
            raise ValueError("informe instance_ids, profile_ids ou distribute")
        return self


class ServerLimitValues(BaseModel):
    max_slots: int | None = None
    boot_parallelism: int | None = None
    max_working: int | None = None
    min_free_ram_mb: int | None = None
    #: Teto de aparelhos EXISTENTES na máquina (migração 050), conferido ao provisionar. Só existe como decisão do
    #: dono: nenhuma máquina o declara, e `None` em `effective` é "sem teto".
    max_devices: int | None = None


class ServerLimitsDTO(BaseModel):
    """Uma máquina na tela Limites: o que ela declara, o que o dono decidiu, o que vale e como ela está agora."""

    worker_id: str
    name: str
    is_host: bool
    connected: bool
    maintenance: bool
    #: O que a máquina declara (worker.yaml / config.yaml). `None` = não declarado (agente antigo).
    declared: ServerLimitValues
    #: O que o dono decidiu no painel. `None` = segue o declarado.
    decided: ServerLimitValues
    #: O que o agendador e o agente estão usando.
    effective: ServerLimitValues
    #: Campos que não se editam por aqui para esta máquina, com o motivo.
    locked: dict[str, str] = {}
    online: int = 0
    working: int = 0
    devices: int = 0
    cpu_percent: float | None = None
    cpu_count: int | None = None
    ram_free_mb: int | None = None
    ram_total_mb: int | None = None


class DistributionPick(BaseModel):
    instance_id: str
    server_id: str
    server_name: str
    needs_start: bool


class DistributionPreview(BaseModel):
    requested: int
    picks: list[DistributionPick]
    per_server: dict[str, int]
    missing: int
    reasons: list[str]


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
    #: Apps que a execução toca (o do plano e o de cada etapa) — é o que a visão por app filtra.
    app_ids: list[str] = []


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
    app_id: str | None = None                 # item 12.1: app desta etapa (None = o do plano)
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
    # Item 7.3: motivo ESTRUTURADO do bloqueio (limit | policy | approval | ai) e da espera (device_slot |
    # profile_limit | ai_capacity | model_response | human) — sem eles a interface só tinha o texto livre de
    # `status_detail`, que qualquer ajuste de redação no backend quebrava em silêncio (achados #93, #68).
    blocked_kind: str | None = None
    wait_reason: str | None = None
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


class AiRoleStatus(BaseModel):
    """Uma função de IA, como a aba IA precisa mostrá-la (item 7.1).

    O campo que não existia e o pedido exige: `sends_data_externally` POR FUNÇÃO. Com o hub, o ator pode rodar
    num modelo local (nada sai da máquina) enquanto o planejador continua num provedor externo — e uma única
    frase no topo da tela deixaria de ser verdade.
    """

    role: str
    provider: str
    kind: str                                 # anthropic | openai | simulated
    model: str
    endpoint: str                             # só o host; nunca a URL com credencial
    sends_data_externally: bool
    configured: bool
    priced: bool                              # o modelo tem preço em `ai.prices`? (sem preço → "Total parcial")
    vision: bool                              # capacidade DECLARADA em `ai.models`
    tools: bool
    refusal_fallback: bool                    # fallback de recusa do lado do servidor, para ESTA função
    fallback_provider: str | None = None      # para onde cai quando o provedor desta função falha (vazio = não cai)
    timeout_s: float = 0
    concurrency: int = 0
    effort: str | None = None


class AiImageStatus(BaseModel):
    """O gerador de IMAGEM da persona, para a aba IA (evolução 2, onda A). Não é papel de IA: porta própria, chave
    própria (`OPENAI_API_KEY`), preço por imagem declarado; só o teto do dia em US$ é compartilhado."""

    provider: str                             # simulated | openai
    model: str
    quality: str
    configured: bool
    simulated: bool
    sends_data_externally: bool
    per_persona: int
    on_create: bool
    price_per_image_usd: float | None = None


class AiStatus(BaseModel):
    provider: str
    model: str | None
    configured: bool
    simulated: bool
    sends_data_externally: bool
    notice: str
    #: Gerador de imagem da persona (048). `None` só enquanto a composição não o montou.
    image: AiImageStatus | None = None
    effort: str | None = None
    models: dict[str, str] | None = None      # plan | decide | verify | escalation → modelo
    roles: list[AiRoleStatus] = []            # item 7.1: provedor + endpoint + "os dados saem?" POR função
    # Item 7.2: o fallback pago de recusa deixa de ser invisível na tela. `target` é descritivo porque quem
    # escolhe o destino é o servidor do provedor (`fallbacks: "default"` roteia por categoria da recusa).
    refusal_fallback: bool = False
    refusal_fallback_target: str | None = None
    spend_today_usd: float | None = None      # gasto de hoje (UTC), em US$ — item 7.2
    spend_limit_day_usd: float | None = None
    spend_limit_run_usd: float | None = None
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


class DatabaseStatus(BaseModel):
    """O banco, na saúde. Existe porque `/health` não olhava para ele (achado #33): com o PostgreSQL fora do ar,
    todo o resto continuava respondendo `ok` e nada na resposta sequer dizia QUAL banco o processo estava usando —
    a pergunta "este backend está no SQLite ou no PostgreSQL?" só se respondia lendo o `.env` da máquina."""

    dialect: Literal["sqlite", "postgres"]
    #: O banco respondeu a uma consulta AGORA. Não é o estado da última vez: a pergunta é feita a cada chamada.
    reachable: bool
    #: `sqlite` | `postgres://host:porta/base` — nunca o DSN inteiro, que carrega usuário e senha.
    target: str | None = None


class Health(BaseModel):
    #: Identidade estável: "este HTTP é a Farm?" (`app/identidade.py`). Vem antes de tudo porque é o que o
    #: supervisor e o deploy perguntam primeiro — um 404 de outro serviço na mesma porta não é backend vivo.
    service: Literal["android-farm-central"] = "android-farm-central"
    status: Literal["ok", "degraded", "error"]
    version: str
    #: Commit em execução e última migração aplicada. Existem porque `version` é uma constante no código ("0.1.0")
    #: e não respondia a única pergunta que importa depois de um deploy: **este processo é o código novo?** Sem
    #: isso, o parque rodou por um dia inteiro um backend anterior às migrações 016/017 sem ninguém notar. `None`
    #: quando o diretório `.git` não veio junto (instalação por cópia) — dizer "não sei" é melhor que mentir.
    commit: str | None = None
    migration: str | None = None
    database: DatabaseStatus | None = None
    ai: AiStatus
    appium: AppiumStatus
    sdk: SdkStatus
    problems: list[Problem] = []
    features: dict[str, Any] = {}             # hibernation, recipes, flows, image_policy, system_image, skills (fase F)


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
