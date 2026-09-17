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
  models?: { plan: string; decide: string; verify: string; escalation: string } | null;
  recipes?: 'off' | 'shadow' | 'replay' | null; flows?: boolean | null;
  image_policy?: 'always' | 'auto' | 'never' | null;
}

interface Health {
  status: 'ok' | 'degraded' | 'error';
  version: string;
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

interface UsageGroup { role: 'plan' | 'decide' | 'verify'; model: string; tier: 0 | 1; calls: number;
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
}

export interface ActionAccepted { accepted: true }

export interface BulkRequest { ids: string[]; action: InstanceAction; params?: InstanceActionParams }
export interface BulkResult { accepted: string[]; rejected: { id: string; reason: string }[] }

export interface InstanceUpdate { app_id?: string | null; account_label?: string | null }

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
