/* eslint-disable */
// =====================================================================================
// PARTE 1 — Tipos copiados LITERALMENTE de docs/api-contract.md (seção "Tipos (TypeScript)"
// e o bloco `ManualInput`), com as mudanças ADITIVAS do "Adendo v0.2" já mescladas no lugar
// (marcadas com `// v0.2`). Não edite à mão: se o contrato mudar, recopie o bloco.
// Os `export` ficam todos na lista ao final da parte 1 para manter o bloco intocado.
// =====================================================================================

type InstanceState = 'absent' | 'stopped' | 'hibernated' | 'booting' | 'online' | 'stopping' | 'error';
//  v0.2 — hibernated = desligado com snapshot salvo: acorda em segundos e não ocupa RAM
type ControlOwner = 'none' | 'ai' | 'user';
type AutomationState = 'none' | 'starting' | 'ready' | 'error';

type RunStatus = 'planning' | 'needs_input' | 'planned' | 'running' | 'paused' | 'cancelling'
               | 'completed' | 'completed_with_issues' | 'cancelled' | 'failed';
type ObjectiveStatus = 'pending' | 'running' | 'waiting_user' | 'succeeded' | 'failed' | 'cancelled' | 'uncertain';
type StepStatus = 'pending' | 'ready' | 'running' | 'verifying' | 'succeeded' | 'retry_wait'
                | 'waiting_user' | 'failed' | 'cancelled' | 'uncertain' | 'skipped';
type AttemptStatus = 'running' | 'succeeded' | 'failed' | 'interrupted' | 'uncertain' | 'cancelled';
type ActionStatus = 'intended' | 'done' | 'failed' | 'unknown' | 'rejected';
type DeliveryLevel = 'none' | 'appeared' | 'sent' | 'delivered' | 'read';

interface FrameInfo {
  id: string;            // identificador opaco do frame
  ts: string;            // quando foi capturado
  width: number;         // pixels do aparelho (não da miniatura)
  height: number;
  orientation: 'portrait' | 'landscape';
  stale: boolean;        // true se mais antigo que o limite configurado
}

interface InstanceCurrent {
  run_id: string | null;
  objective_id: string | null;
  objective_status: ObjectiveStatus | null;
  step_id: string | null;
  step_title: string | null;
  step_status: StepStatus | null;
  steps_done: number;
  steps_total: number;
}

interface Instance {
  id: string;                 // 'android-01' … 'android-10'
  index: number;              // 1..10
  avd_name: string;
  serial: string;             // 'emulator-5554'
  console_port: number;
  ports: { system: number; mjpeg: number; chromedriver: number };
  state: InstanceState;
  state_detail: string | null;
  pid: number | null;
  boot_seconds: number | null;
  app_id: string | null;
  account_label: string | null;       // rótulo de configuração
  account_evidence: string | null;    // última evidência observada no app (ex.: "Conta: qa-user-03")
  account_evidence_ts: string | null;
  control: ControlOwner;
  control_since: string | null;
  control_pending: boolean;           // usuário pediu o controle e a IA ainda está terminando a ação atual
  automation: { state: AutomationState; detail: string | null };
  frame: FrameInfo | null;
  current: InstanceCurrent | null;
  attention: string | null;           // texto curto quando exige atenção do usuário
  resources: { rss_mb: number | null; cpu_percent: number | null } | null;
  // v0.6 — 'store' = aparelho-loja (Play Store): o projeto o liga e desliga, mas NUNCA lhe despacha tarefa.
  kind: 'emulator' | 'external' | 'store';
  /**
   * Verbos que ESTE aparelho aceita (v0.7). O painel usa para não oferecer botão que não faria nada. Opcional
   * porque um snapshot de backend antigo não traz o campo — e nesse caso vale "suporta tudo", já que o pré-voo do
   * backend recusa de todo modo, com a explicação.
   */
  supported_verbs?: string[];
  /** Máquina que hospeda o aparelho (v0.8); nulo/ausente = o servidor central. */
  worker_id?: string | null;
}

interface AppConfig {
  id: string;
  name: string;
  package: string;
  activity: string | null;
  apk_path: string | null;        // caminho local validado pelo backend
  nav_hints: string | null;       // instruções de navegação em texto livre
  known_selectors: Record<string, string> | null;  // nome → seletor (resource-id, texto, accessibility id)
  builtin: boolean;
}

interface PlanStep {
  key: string;                // estável dentro do plano: 'open_app', 'open_conversation', …
  title: string;
  goal: string;               // objetivo em linguagem natural (nunca coordenadas)
  depends_on: string[];       // keys
  side_effect: boolean;       // true = repetição NÃO é segura (ex.: enviar)
  precondition: string | null;
  postcondition: { kind: 'text_visible' | 'app_foreground' | 'element_present' | 'model_judged' | 'items_collected'; value: string; description: string };
  timeout_s: number;
  max_attempts: number;
}

interface Plan {
  summary: string;
  app_id: string | null;
  app_package: string | null;
  parameters: Record<string, string>;            // ex.: { recipient: 'QA-001', message_template: 'Teste POC {instance_id} {run_id}' }
  success_criteria: string[];
  steps: PlanStep[];
  missing: { field: string; question: string }[]; // vazio quando completo
  planner: { provider: string; model: string; simulated: boolean };
}

interface RunSummary {
  id: string;
  short_id: string;
  command: string;
  status: RunStatus;
  simulated: boolean;
  instance_ids: string[];
  instances_requested: number;
  instances_used: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  counts: { succeeded: number; failed: number; waiting_user: number; uncertain: number; cancelled: number; running: number; pending: number };
  progress: number;           // 0..1 por objetivos concluídos com sucesso
  status_detail: string | null;
  deduplicated?: boolean;     // presente em respostas de criação
}

interface Step {
  id: string;                 // estável: `${run_id}:${instance_id}:v${plan_version}:${key}`
  run_id: string; objective_id: string; instance_id: string;
  plan_version: number; seq: number; key: string;
  title: string; goal: string; depends_on: string[];
  side_effect: boolean;
  precondition: string | null;
  postcondition: PlanStep['postcondition'];
  timeout_s: number; max_attempts: number; attempts: number;
  status: StepStatus; status_detail: string | null;
  next_retry_at: string | null;
  started_at: string | null; finished_at: string | null;
  result: { verified: boolean; evidence_text: string | null; delivery_level?: DeliveryLevel } | null;
  driven_by: 'ai' | 'recipe' | 'recipe+ai' | null;   // v0.2 — quem decidiu as ações da etapa
}

interface Action {
  id: number; attempt_id: string; seq: number;
  tool: string; args: Record<string, unknown>;
  rationale: string | null;        // resumo legível da decisão
  status: ActionStatus;
  side_effect: boolean;
  intent_at: string; done_at: string | null;
  result: Record<string, unknown> | null; error: string | null;
  source: 'ai' | 'recipe';         // v0.2 — recipe = sem chamada de modelo
}

interface Attempt {
  id: string; step_id: string; number: number; status: AttemptStatus;
  started_at: string; finished_at: string | null;
  error: string | null; recovery: string | null; observed_result: string | null;
  actions: Action[];
}

interface Evidence {
  id: number; run_id: string; instance_id: string; step_id: string | null; attempt_id: string | null;
  ts: string; kind: 'screenshot' | 'hierarchy' | 'text' | 'verifier';
  note: string | null; url: string | null;   // url relativa para GET (imagens)
  redacted: boolean;
}

interface Objective {
  id: string; run_id: string; instance_id: string;
  status: ObjectiveStatus; status_detail: string | null;
  blocked_reason: string | null; needs: string | null;   // o que o usuário precisa resolver
  plan_version: number;
  parameters: Record<string, string>;       // parâmetros já resolvidos para a instância
  steps_done: number; steps_total: number;
  delivery_level: DeliveryLevel | null;
  effects: string[];                        // ações com efeito externo já realizadas (texto legível)
  started_at: string | null; finished_at: string | null;
  ai_calls: number; ai_input_tokens: number; ai_output_tokens: number;
}

interface PlanVersion { objective_id: string; version: number; reason: string; created_at: string; steps: PlanStep[] }

interface RunDetail extends RunSummary {
  plan: Plan | null;
  objectives: Objective[];
  steps: Step[];               // de todas as versões; filtre por objective.plan_version para a atual
  attempts: Attempt[];
  evidence: Evidence[];
  plan_versions: PlanVersion[];
  decisions: { ts: string; instance_id: string | null; text: string }[];
}

interface EventRecord {
  id: number | null;           // null = efêmero (não persistido: frame, metrics)
  ts: string;
  kind: string;                // ver "Eventos"
  level: 'info' | 'warn' | 'error';
  run_id: string | null; instance_id: string | null; objective_id: string | null;
  step_id: string | null; attempt_id: string | null;
  message: string;
  data: Record<string, unknown> | null;
}

interface Settings {
  max_active_devices: number; max_ai_concurrency: number; boot_parallelism: number;
  max_steps_per_objective: number; max_actions_per_step: number; max_attempts_per_step: number;
  for_each_max_items: number;
  step_timeout_s: number; objective_timeout_s: number; driver_call_timeout_s: number;
  retry_backoff_s: number; no_progress_limit: number;
  ai_max_calls_per_objective: number; ai_max_tokens_per_run: number;
  capture_grid_interval_s: number; capture_focus_interval_s: number; frame_max_age_ms: number;
  log_retention_days: number; evidence_retention_days: number;
  // v0.2 — campos do rodízio (editáveis em tempo de execução)
  auto_start_devices: boolean;  // o scheduler liga o aparelho quando há tarefa para ele
  max_online_devices: number;   // vagas de RAM (1–10): ligados + ligando + desligando
  min_online_dwell_s: number;   // anti-vaivém
  idle_stop_s: number;          // 0 = só desliga para ceder vaga
}

interface AiStatus {
  provider: 'anthropic' | 'simulated' | string;
  model: string | null;
  configured: boolean;            // chave presente
  simulated: boolean;             // modo simulado de desenvolvimento
  sends_data_externally: boolean; // screenshots/textos saem da máquina
  notice: string;                 // texto para exibir
  effort: string | null;
  // v0.2
  models?: { plan: string; decide: string; verify: string; escalation: string; social?: string } | null;
  recipes?: 'off' | 'shadow' | 'replay' | null; flows?: boolean | null;
  image_policy?: 'always' | 'auto' | 'never' | null;
  // disjuntor de conta de IA: chave válida, mas o provedor recusa por cobrança/credencial em tempo de execução
  account_blocked?: boolean;
  account_blocked_reason?: string | null;
}

interface Health {
  status: 'ok' | 'degraded' | 'error';
  version: string;
  // Qual código está NO AR. `version` é uma constante do backend e responde igual antes e depois de um deploy;
  // estes dois respondem a pergunta que importa. `null` quando a instalação veio por cópia, sem `.git`.
  commit: string | null;
  migration: string | null;
  ai: AiStatus;
  appium: { running: boolean; port: number; detail: string | null };
  sdk: { found: boolean; root: string | null; emulator_version: string | null; accel: string | null };
  problems: { code: string; message: string; hint: string }[];
  // v0.2
  features: { hibernation: boolean; recipes: string; flows: boolean; image_policy: string;
              system_image: string };
}

interface Metrics {
  ts: string; cpu_percent: number; mem_total_gb: number; mem_available_gb: number; mem_used_percent: number;
  emulators: { instance_id: string; pid: number; rss_mb: number; cpu_percent: number }[];
}

interface Snapshot {
  last_event_id: number;
  server_time: string;
  health: Health;
  metrics: Metrics | null;
  instances: Instance[];
  apps: AppConfig[];
  runs: RunSummary[];          // ativas + recentes (até 20)
  settings: Settings;
  workers?: Worker[];          // v0.8 — opcional: backend antigo não manda
}

interface ManualInput {
  lease_id: string;
  frame_id: string;                 // frame que o usuário estava vendo
  type: 'tap' | 'long_press' | 'swipe' | 'text' | 'key';
  x?: number; y?: number;           // pixels do aparelho (FrameInfo.width/height), já convertidos pelo frontend
  x2?: number; y2?: number; duration_ms?: number;
  text?: string;
  key?: 'back' | 'home' | 'recents' | 'enter' | 'delete';
}

// ---- Adendo v0.2 — custo de IA, fluxos e receitas (copiado do contrato) ----

interface UsageGroup { role: 'plan' | 'decide' | 'verify' | 'social'; model: string; tier: 0 | 1; calls: number;
  fresh: number; cache_read: number; cache_write: number; output: number;        // tokens
  with_image: number; errors: number; avg_ms: number; usd: number | null }       // usd null = modelo sem preço
interface UsageReport { scope: { run_id: string | null; days: number | null }; groups: UsageGroup[]; total_usd: number;
  objectives_with_ai: number; calls_per_objective: number; usd_per_objective: number;
  steps_driven_by: Record<string, number>; unpriced_models: string[] }
interface Flow { id: string; name: string; command_template: string; app_id: string | null; source_run_id: string | null;
  status: 'active' | 'disabled'; uses: number; created_at: string; last_used_at: string | null }
interface Recipe { id: number; app_package: string; app_version: string; step_key: string; step_hash: string;
  version: number; status: 'active' | 'quarantined' | 'superseded';
  actions: { tool: string; args: Record<string, unknown>; commit: boolean; why: string;
             selectors?: { kind: string; rid?: string; text?: string; desc?: string }[];
             scroll?: { direction: string; max: number } }[];
  replay_ok: number; replay_fail: number; consecutive_fail: number; shadow_agree: number; shadow_total: number;
  learned_from_step: string | null; created_at: string; last_used_at: string | null }

export type {
  InstanceState, ControlOwner, AutomationState, RunStatus, ObjectiveStatus, StepStatus, AttemptStatus,
  ActionStatus, DeliveryLevel, FrameInfo, InstanceCurrent, Instance, AppConfig, PlanStep, Plan, RunSummary,
  Step, Action, Attempt, Evidence, Objective, PlanVersion, RunDetail, EventRecord, Settings, AiStatus,
  Health, Metrics, Snapshot, ManualInput, UsageGroup, UsageReport, Flow, Recipe,
};

// =====================================================================================
// PARTE 2 — Formas que só aparecem na tabela REST / seção WebSocket do contrato.
// Tudo aqui é derivado do contrato; nada de campos inventados.
// =====================================================================================

/** Perfis do Instagram. A senha é write-only: entra em `ProfileCreate`/`CredentialUpdate` e nunca volta. */
export type SessionStatus = 'unknown' | 'auth_required' | 'auth_challenge' | 'wrong_account' | 'session_ready';

export interface CredentialInfo {
  configured: boolean;
  login_identifier: string | null;
  status: string | null;
  failed_attempts: number;
  blocked_until: string | null;
  updated_at: string | null;
  last_used_at: string | null;
}

export interface SessionInfo {
  status: SessionStatus;
  instance_id: string | null;
  observed_username: string | null;
  verified_at: string | null;
  detail: string | null;
}

export interface InstagramProfile {
  id: string;
  username: string;
  display_name: string | null;
  first_name: string | null;
  last_name: string | null;
  birth_date: string | null;
  email: string | null;
  persona_id: string | null;
  persona_name: string | null;
  status: string;
  instance_id: string | null;
  credential: CredentialInfo;
  session: SessionInfo;
  last_verified_at: string | null;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileCreateRequest {
  username: string;
  first_name?: string | null;
  last_name?: string | null;
  display_name?: string | null;
  birth_date?: string | null;
  email?: string | null;
  persona_id?: string | null;
  instance_id?: string | null;
  login_identifier?: string | null;
  password?: string | null;
}

export type ProfilePatchRequest = Partial<Omit<ProfileCreateRequest, 'username' | 'password' | 'login_identifier'>> & {
  status?: 'active' | 'disabled';
};

export interface CredentialUpdateRequest {
  login_identifier?: string | null;
  password: string;
}

/** Resposta 202 de connect/verify/logout: o trabalho roda no aparelho e o resultado aparece no perfil. */
export interface SessionJobAccepted {
  accepted: boolean;
  profile_id: string;
  instance_id: string;
}

export interface PersonaTraits {
  personality?: string | null;
  tone?: string | null;
  formality?: 'informal' | 'neutro' | 'formal' | null;
  typical_length?: 'curta' | 'media' | 'longa' | null;
  emojis?: 'nunca' | 'raro' | 'moderado' | 'muito' | null;
  slang?: string | null;
  humor?: string | null;
  interests?: string[];
  dm_style?: string | null;
  comment_style?: string | null;
  with_known?: string | null;
  with_strangers?: string | null;
  examples?: string[];
  common_phrases?: string[];
  forbidden_phrases?: string[];
  // Identidade visual: descreve a pessoa, não como ela escreve. Fica guardada e aparece aqui, mas NÃO vai ao modelo.
  appearance?: string | null;
  visual_style?: string | null;
  photo_scenario?: string | null;
}

export interface Persona {
  id: string;
  name: string;
  summary: string | null;
  persona_prompt?: string;
  traits?: PersonaTraits;
  profile_id?: string | null;
  profile_username?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface PersonaInput {
  name: string;
  summary?: string | null;
  persona_prompt?: string;
  traits?: PersonaTraits;
}

/** Prévia da persona: mostra como ela responderia, sem publicar nada. */
export interface PersonaPreviewRequest {
  kind?: 'dm_reply' | 'comment_reply';
  profile_id?: string | null;
  counterparty?: string | null;
  incoming: string;
}

export interface SocialDraft {
  content: string;
  rationale: string;
  refused: boolean;
  refusal_reason: string | null;
  memory_candidates: { subject: string; content: string; importance: number; confidence: number }[];
}

export interface MemoryItem {
  id: string;
  profile_id: string;
  subject: string;
  content: string;
  source: string;
  interaction_id: string | null;
  importance: number;
  confidence: number;
  occurrences: number;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface MemoryInput {
  subject: string;
  content: string;
  importance?: number;
  confidence?: number;
  expires_at?: string | null;
}

export type InteractionStatus = 'pending' | 'confirmed' | 'failed' | 'uncertain' | 'cancelled';

export interface SocialInteraction {
  id: string;
  profile_id: string;
  instance_id: string | null;
  run_id: string | null;
  objective_id: string | null;
  step_id: string | null;
  occurred_at: string;
  type: string;
  direction: string;
  counterparty: string | null;
  thread_key: string | null;
  incoming_content: string | null;
  outgoing_content: string | null;
  target: string | null;
  status: InteractionStatus;
  evidence: string | null;
  created_at: string;
}

export interface DeviceAppState {
  instance_id: string;
  package_name: string;
  desired_release_id: string | null;
  installed_release_id: string | null;
  observed_version_name: string | null;
  observed_version_code: number | null;
  observed_splits: string[];
  /** O que ESTE aparelho deveria ter, quando o conjunto foi filtrado por densidade/ABI/idioma. Vazio = tudo. */
  expected_splits: string[];
  first_install_time: string | null;
  last_update_time: string | null;
  state: string;
  pending_op: string | null;
  verified_at: string | null;
  drift_kind: string | null;
  detail: string | null;
  previous_release_id: string | null;
  last_operation: string | null;
}

export interface ReleaseFile {
  role: 'base' | 'split';
  split_name: string | null;
  file_name: string;
  sha256: string;
  size_bytes: number;
}

export interface AppRelease {
  id: string;
  package_name: string;
  version_name: string;
  version_code: number;
  artifact_type: 'single' | 'split_set' | 'unverified_split_set';
  signature_sha256: string;
  min_sdk: number | null;
  target_sdk: number | null;
  supported_abis: string[];
  /** `store` = copiado do aparelho-loja, onde o app foi instalado pela Play Store. Espelha o `Literal` do backend. */
  source_type: 'inbox' | 'upload' | 'store';
  source_reference: string | null;
  imported_at: string;
  status: string;
  detail: string | null;
  channel: ReleaseChannel;
  channel_at: string | null;
  channel_detail: string | null;
  canary_instance_id: string | null;
  validations: ReleaseValidation[];
  files: ReleaseFile[];
  devices: string[];
}

/** `status` responde "dá para instalar este arquivo?"; `channel`, "esta versão já provou que funciona?". */
export type ReleaseChannel = 'candidate' | 'canary' | 'promoted' | 'quarantined' | 'rolled_back';

export interface ReleaseValidation {
  instance_id: string;
  stage: 'install' | 'launch';
  ok: boolean;
  detail: string | null;
  observed_at: string;
}

export interface ReleaseLifecycleBody {
  verb: 'canary' | 'promote' | 'quarantine' | 'rollback' | 'distribute';
  instance_id?: string;
  note?: string;
  confirm_reinstall?: boolean;
  /** Só para `distribute`: "instalar em todos agora" — o rodízio liga os pendentes em vez de esperar tarefa. */
  eager?: boolean;
}

/** O que aconteceu com cada aparelho do parque ao distribuir uma versão. */
export interface DistributeDevice {
  id: string;
  outcome: 'started' | 'pending' | 'already';
  reason: string;
}

/** Loja × catálogo: o que a Play Store tem instalado no aparelho-loja e o que já foi catalogado. */
export interface StoreStatus {
  configured: boolean;
  instance_id: string | null;
  package: string;
  state: string | null;
  store_version_code: number | null;
  store_version_name: string | null;
  catalog_version_code: number | null;
  update_available: boolean;
  fleet_target_release_id: string | null;
  fleet_target_version_code: number | null;
}

export interface Capability {
  key: string;
  title: string;
  side_effect: boolean;
  risk: string;
  default_policy: PolicyName;
  limit_bucket: string | null;
  needs_draft: boolean;
  bindings: string[];
  /** Nas ações que escrevem, o texto é opcional: o normal é vir `content_brief` e cada perfil escrever o seu. */
  optional_bindings: string[];
}

export type PolicyName = 'autonomous' | 'approval_required' | 'manual_only' | 'disabled';

export interface ProfilePolicy {
  limits: Record<string, number>;
  capabilities: Record<string, PolicyName>;
  defaults: Record<string, PolicyName>;
}

export interface ProfilePolicyPatch {
  limits?: Record<string, number>;
  capabilities?: Record<string, PolicyName>;
}

export interface Approval {
  id: string;
  profile_id: string | null;
  run_id: string | null;
  objective_id: string | null;
  step_id: string | null;
  capability: string;
  target: string | null;
  summary: string;
  generated_content: string | null;
  approved_content: string | null;
  content: string | null;
  status: 'pending' | 'approved' | 'edited' | 'rejected' | 'expired';
  created_at: string;
  decided_at: string | null;
  decided_note: string | null;
  /** O efeito que esta decisão liberou — preenchido só no commit, quando a interação nasce. */
  interaction_id: string | null;
}

export interface ApprovalDecisionItem {
  id: string;
  verb: 'approve' | 'edit' | 'reject';
  content?: string;
  note?: string;
}

/** Cada decisão é independente: as que falharam vêm em `refused`, com o motivo, sem derrubar as demais. */
export interface ApprovalBatchResult {
  decided: Approval[];
  refused: { id: string; reason: string }[];
}

export interface AuthAttempt {
  id: number;
  profile_id: string;
  instance_id: string | null;
  started_at: string;
  finished_at: string | null;
  stage: string | null;
  outcome: string | null;
  detail: string | null;
}

/** Corpo de `POST /api/apps` — `Omit<AppConfig,'id'|'builtin'>`. */
export type AppConfigInput = Omit<AppConfig, 'id' | 'builtin'>;

/** Ações de instância aceitas em `POST /api/instances/{id}/actions/{action}` e no `bulk`. */
export type InstanceAction =
  | 'create' | 'start' | 'stop' | 'restart' | 'reset'
  | 'install_apk' | 'open_app' | 'home' | 'back' | 'recents'
  // v0.2 — `hibernate` devolve 409 quando `android.hibernation=false` (ver `health.features.hibernation`)
  | 'hibernate' | 'wake';

/** Parâmetros opcionais das ações: `reset` exige `{confirm:true}`; `install_apk` aceita `{app_id}`. */
export interface InstanceActionParams {
  confirm?: boolean;
  app_id?: string;
  /** Reenviar a MESMA chave devolve o comando original em vez de agir de novo. */
  idempotency_key?: string;
}

/**
 * Estado de um comando do painel. `rejected` garante que o aparelho não foi tocado; `uncertain` diz que não se
 * sabe o efeito, e por isso nada é repetido sozinho; `cancel_requested` não é `cancelled` — pedir não é conseguir.
 */
export type CommandState =
  | 'created' | 'dispatched' | 'acked' | 'running'
  | 'succeeded' | 'failed' | 'uncertain' | 'rejected'
  | 'cancel_requested' | 'cancelled';

export interface Command {
  id: string;
  instance_id: string;
  worker_id: string | null;
  verb: string;
  state: CommandState;
  fence: number;
  requested_by: string;
  reason: string | null;            // motivo da recusa, ou o que deu errado
  attempt: number;
  created_at: string;
  dispatched_at: string | null;
  acked_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface WorkerResources {
  cpu_percent?: number | null;
  cpu_count?: number | null;
  ram_total_mb?: number | null;
  ram_free_mb?: number | null;
  disk_free_gb?: number | null;
}

export interface WorkerDevice {
  serial: string;
  avd_name?: string | null;
  /** Estado do PROCESSO na máquina do worker: `running` não quer dizer "pronto para automação". */
  state: string;
  detail?: string | null;
  adb_port?: number | null;
  instance_id?: string | null;
}

/**
 * Uma máquina que hospeda aparelhos. `state` é o que se mostra (manutenção ganha) e `observed_state` é o que se
 * observa da conexão — os dois coexistem porque um worker em manutenção continua online, e esconder isso
 * atrapalharia quem diagnostica. `last_seen_at` é a IDADE do dado: sem ela não se sabe que a tela envelheceu.
 */
export interface Worker {
  id: string;
  name: string;
  os?: string | null;
  os_version?: string | null;
  agent_version?: string | null;
  appium_mode: 'local' | 'central';
  appium_url?: string | null;
  max_slots: number;
  verbs: string[];
  state: 'online' | 'offline' | 'degraded' | 'maintenance';
  observed_state: string;
  maintenance: boolean;
  state_detail?: string | null;
  connected: boolean;
  resources: WorkerResources;
  devices: WorkerDevice[];
  enrolled_at: string;
  last_seen_at?: string | null;
}

export interface WorkerEnrollment { enrollment_token: string; expires_in_s: number }

/** O que `POST /instances/{id}/actions/{action}` devolve agora: algo para ACOMPANHAR, não uma promessa. */
export interface CommandAccepted { command_id: string; state: CommandState; deduplicated: boolean }

export interface BulkRequest { ids: string[]; action: InstanceAction; params?: InstanceActionParams }
export interface BulkResult {
  accepted: string[];
  rejected: { id: string; reason: string; command_id?: string }[];
  /** Acréscimo rastreável: um comando por aparelho. `accepted` segue sendo lista de ids. */
  commands?: { id: string; command_id: string; deduplicated: boolean }[];
}

export interface InstanceUpdate {
  app_id?: string | null;
  account_label?: string | null;
  /** Máquina que hospeda o aparelho; `null` o devolve ao servidor central (v0.8). */
  worker_id?: string | null;
}

export interface PackagesResponse { packages: string[] }

export interface HierarchyElement {
  id: string;
  text: string | null;
  desc: string | null;
  resource_id: string | null;
  class_name: string | null;
  bounds: [number, number, number, number];
  clickable: boolean;
  enabled: boolean;
  focused: boolean;
}
export interface HierarchyResponse { ts: string; elements: HierarchyElement[] }

export interface ControlTakeResponse { status: 'granted' | 'pending'; lease_id: string }
export interface ControlReleaseResponse { status: 'released' }
export interface InputOk { ok: true }

export type RunMode = 'plan' | 'execute';
export interface CreateRunRequest {
  command: string;
  instance_ids: string[];
  idempotency_key: string;
  mode: RunMode;
}

export interface RetryFailedResponse {
  retried: string[];
  skipped: { objective_id: string; reason: string }[];
}

export type Resolution = 'confirm_done' | 'retry' | 'abandon';
export interface ResolveRequest { resolution: Resolution; note?: string }

/**
 * `GET /api/runs/{id}/report` — o contrato só fixa as chaves de topo; o conteúdo interno é livre,
 * por isso fica como `unknown` e a UI renderiza de forma defensiva.
 */
export interface RunReport {
  run?: unknown;
  totals?: unknown;
  per_instance?: unknown;
  untested?: unknown;
  markdown?: unknown;
}

/** `GET /api/usage?run_id=` ou `?days=7` (v0.2): um dos dois. */
export type UsageQuery = { run_id: string } | { days: number };

/** Corpo de `PUT /api/flows/{id}` (v0.2). */
export interface FlowStatusUpdate { status: 'active' | 'disabled' }

/** Corpo de `PUT /api/recipes/{id}` (v0.2) — `superseded` só o backend atribui. */
export interface RecipeStatusUpdate { status: 'active' | 'quarantined' }
/** Resposta de `PUT /api/recipes/{id}`: só `{id,status}`, não a receita inteira. */
export interface RecipeStatusResult { id: number; status: Recipe['status'] }

/** `GET /api/diagnostics` — "objeto livre". */
export type Diagnostics = Record<string, unknown>;

/** Mensagens servidor → cliente no WebSocket. */
export type ServerMessage =
  | { type: 'hello'; server_time: string; last_event_id: number }
  | { type: 'event'; event: EventRecord }
  | { type: 'resync' }
  | { type: 'pong' };

/** Mensagens cliente → servidor no WebSocket. */
export type ClientMessage =
  | { type: 'focus'; instance_id: string | null }
  | { type: 'ping' };

/** Metadados lidos dos cabeçalhos `X-Frame-*` de `GET /frame`. */
export interface FrameHeaders {
  id: string | null;
  ts: string | null;
  width: number | null;
  height: number | null;
  orientation: 'portrait' | 'landscape' | null;
}
