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
  /**
   * Máquina que hospeda o aparelho (v0.8). Desde o `LocalWorker`, o servidor central também é um worker: o
   * aparelho desta máquina traz aqui o `OWNER_ID` dele, e não mais nulo. Para saber se é local, compare com o
   * worker que tem `local: true`.
   */
  worker_id?: string | null;
  /**
   * Capacidades DECLARADAS (v0.9): o que o aparelho é, e não só que verbo aceita. Nulo/ausente = não se sabe,
   * que é diferente de "não tem" — o painel mostra o que sabe e cala sobre o resto.
   */
  device_kind?: string | null;           // emulator | physical | container
  system_image?: string | null;
  api_level?: number | null;
  abis?: string[];
  play_store?: boolean | null;
  /**
   * Inventário conferido contra o que o worker DECLARA hospedar (v0.10). `divergent` quer dizer que as fontes
   * discordam sobre qual aparelho está por trás deste id — e aí verbo destrutivo é recusado pelo backend, porque
   * um `reset` agiria num aparelho com a tela em outro. Nulo/ausente = conferido, ou worker desconectado.
   */
  inventory_state?: string | null;
  inventory_detail?: string | null;
  /** `dynamic` = instância adotada de um aparelho anunciado por um worker, sem editar `config.yaml`. */
  origin?: string;
  tunnel_port?: number | null;           // porta local do central que o túnel encaminha
  remote_adb_port?: number | null;       // porta de ADB do lado do worker
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
  claimed_by?: string | null;                        // backend que assumiu a etapa; null = nunca despachada
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
  // ONDE isto rodou, fotografado no plano e no despacho. `null` = execução anterior a esta fotografia (ou que
  // nunca chegou a ser despachada): a tela diz "não registrado" em vez de chutar a máquina local.
  worker_id?: string | null;       // máquina que hospeda o aparelho
  hosted_by?: string | null;       // backend que despachou
  device_serial?: string | null;   // endereço de ADB no momento
  physical_id?: string | null;     // impressão digital do aparelho por trás do id lógico
  status: ObjectiveStatus; status_detail: string | null;
  blocked_reason: string | null; needs: string | null;   // o que o usuário precisa resolver
  // Item 7.3: motivo ESTRUTURADO do bloqueio (limit | policy | approval | ai) e da espera (device_slot |
  // profile_limit | ai_capacity | model_response) — `status_detail` continua existindo como texto livre, mas a
  // tela para de adivinhar por regex em cima dele (achados #93, #68).
  blocked_kind?: string | null;
  wait_reason?: string | null;
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
  session_unknown_retry_cap: number;
  // v0.4 — coordenação de frota sobre o mesmo alvo (item 8.3/achado #114)
  fleet_max_accounts_per_target: number; fleet_target_window_s: number;
  fleet_min_spacing_between_accounts_s: number; fleet_spacing_jitter_s: number;
  ai_max_calls_per_objective: number; ai_max_tokens_per_run: number;
  // v0.3 — teto em DINHEIRO (item 7.2). 0 = desligado. Os dois de cima estão em unidades que não
  // se traduzem em US$; estes somam `ai_calls × ai.prices`, a mesma conta do painel de custo.
  ai_max_usd_per_run: number; ai_max_usd_per_day: number;
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
  // v0.3 — hub de IA (itens 7.1/7.2). Uma linha por função: quem atende, em que endpoint, e se os dados saem
  // desta máquina. Com provedor por função, uma frase só no topo da tela deixaria de ser verdade.
  roles?: AiRoleStatus[] | null;
  refusal_fallback?: boolean;            // fallback pago de recusa ligado em alguma função
  refusal_fallback_target?: string | null;
  spend_today_usd?: number | null;       // gasto de hoje (UTC) em US$
  spend_limit_day_usd?: number | null;   // 0 = sem teto
  spend_limit_run_usd?: number | null;
}

interface AiRoleStatus {
  role: string;
  provider: string;
  kind: 'anthropic' | 'openai' | 'simulated' | string;
  model: string;
  endpoint: string;                      // só o host
  sends_data_externally: boolean;
  configured: boolean;
  priced: boolean;                       // sem preço cadastrado, o painel de uso mostra "Total parcial"
  vision: boolean;                       // capacidade DECLARADA em ai.models
  tools: boolean;
  refusal_fallback: boolean;
  fallback_provider?: string | null;     // vazio = a falha desta função NÃO cai em provedor pago
  timeout_s?: number;
  concurrency?: number;
  effort?: string | null;
}

interface Health {
  status: 'ok' | 'degraded' | 'error';
  version: string;
  // Qual código está NO AR. `version` é uma constante do backend e responde igual antes e depois de um deploy;
  // estes dois respondem a pergunta que importa. `null` quando a instalação veio por cópia, sem `.git`.
  commit: string | null;
  migration: string | null;
  // Qual banco este backend está usando e se ele respondeu AGORA. Opcional porque um backend anterior a esta
  // entrega não manda o campo — e o painel prefere não mostrar nada a mostrar "sqlite" por chute.
  database?: { dialect: 'sqlite' | 'postgres'; reachable: boolean; target: string | null } | null;
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
  commands?: Command[];        // comandos ainda em voo — opcional: backend antigo não manda
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
  steps_driven_by: Record<string, number>; unpriced_models: string[];
  // v0.3 — item 7.2. `fallbacks`: quantas chamadas foram servidas por outro modelo, e por quê ('refusal' = recusa
  // reexecutada pelo provedor; nome de provedor = o endpoint da função falhou e ela declarou para onde cair).
  // `spend_today_usd` é o gasto do dia UTC, independente da janela deste relatório.
  fallbacks?: { fallback: string; requested_model: string | null; model: string; calls: number }[];
  spend_today_usd?: number | null
  // Item 7.3 (achado #101): chamadas com erro, por TIPO (refusal | budget | billing | not_configured |
  // invalid_output | error) — sem isto, saber por que uma chamada falhou exigia casar horário de log.
  errors_by_kind?: Record<string, number> }
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
  Step, Action, Attempt, Evidence, Objective, PlanVersion, RunDetail, EventRecord, Settings, AiStatus, AiRoleStatus,
  Health, Metrics, Snapshot, ManualInput, UsageGroup, UsageReport, Flow, Recipe,
};

// =====================================================================================
// PARTE 2 — Formas que só aparecem na tabela REST / seção WebSocket do contrato.
/** Aparelho que um worker anuncia e que ainda não é instância deste parque. */
export interface WorkerDeviceProposal {
  worker_id: string;
  worker_name: string | null;
  serial: string;
  avd_name: string | null;
  state: string;
  adb_port: number | null;
}

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
  /** "Conectado" verificado há tempo demais: o aparelho é relido antes da próxima tarefa. */
  stale: boolean;
}

/** Política de localidade: o que fazer quando o servidor onde os dados do perfil vivem não responde. */
export type OfflinePolicy = 'wait' | 'reauth_elsewhere';

/**
 * Onde os dados deste perfil VIVEM (E9). A sessão do Instagram mora na partição de dados de um aparelho, no
 * disco de uma máquina: `worker_id` é essa máquina, fotografada quando o vínculo foi feito. `moved` é o fato
 * que antes não existia — o id lógico aponta hoje para outro servidor ou outro aparelho físico.
 */
export interface ProfileLocality {
  worker_id: string | null;
  worker_name: string | null;
  worker_state: string | null;
  known: boolean;
  available: boolean;
  moved: boolean;
  physical_id: string | null;
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
  /** `null` quando não há vínculo: sem aparelho não há localidade a afirmar. */
  locality: ProfileLocality | null;
  offline_policy: OfflinePolicy;
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
  offline_policy?: OfflinePolicy;
  /** Mudar de SERVIDOR um perfil com sessão pronta é decisão de pessoa: sem isto a API recusa com 409. */
  confirm_locality_change?: boolean;
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
  /** Campos de voz vazios, calculados no backend a partir da MESMA lista que vai ao modelo. */
  voice_gaps?: string[];
}

export interface PersonaInput {
  name: string;
  summary?: string | null;
  persona_prompt?: string;
  traits?: PersonaTraits;
}

/** Prévia da persona: mostra como ela responderia, sem publicar nada. */
export interface PersonaPreviewRequest {
  kind?: 'dm_reply' | 'comment_reply' | 'dm_initiate' | 'post_comment';
  profile_id?: string | null;
  counterparty?: string | null;
  /** Responder pede `incoming`; puxar conversa/comentar pede `brief`. Pelo menos um dos dois. */
  incoming?: string;
  brief?: string;
  screen?: string;
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
  /** `store` = copiado do aparelho-loja, onde o app foi instalado pela Play Store. `builtin` = app embutido
   *  importado sozinho na subida do backend (hoje só o QA Messenger). Espelha o `Literal` do backend. */
  source_type: 'inbox' | 'upload' | 'store' | 'builtin';
  source_reference: string | null;
  /** O nome que o app mostra ao usuário, lido do base.apk na importação. `null` = release catalogada antes do
   *  catálogo visual, ou APK sem rótulo — a tela cai no rótulo do registro de apps e daí no pacote. */
  label: string | null;
  /** Há ícone extraído? Só então vale pedir `releaseIconUrl(id)`; sem isto a tela tentaria carregar uma imagem
   *  que responde 404 em toda release de ícone adaptativo. */
  has_icon: boolean;
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
  /** A QUEM este conjunto serve, lido dos próprios arquivos (ex.: `['x86_64', 'xhdpi']`). O conjunto copiado da
   *  loja é o da VM-loja: um aparelho de outra ABI é recusado com motivo, e um de outra densidade recebe o split
   *  que existe, com os recursos reescalados. Sem isto, nada na tela dizia que o conjunto é de uma configuração. */
  serves: string[];
}

/** Um destino possível para uma versão (`GET /api/releases/{id}/targets`). Quem decide a compatibilidade é o
 *  BACKEND, com a mesma função que recusa a instalação — a tela só mostra o motivo que veio de lá. */
export interface ReleaseTarget {
  id: string;
  /** Onde o aparelho está. `null` = nesta máquina. É por isto que o diálogo agrupa por servidor. */
  worker_id: string | null;
  state: string;
  compatible: boolean;
  /** Por que NÃO cabe aqui. Vem pronto do backend; a tela não reinventa a regra. */
  reason: string | null;
  app_state: string | null;
  installed_release_id: string | null;
  installed_version_name: string | null;
  /** Já está nesta versão: instalar de novo não mudaria nada. */
  already: boolean;
}

export interface ReleaseTargets {
  release_id: string;
  package: string;
  targets: ReleaseTarget[];
}

/** Um aplicativo que o REGISTRO conhece (`GET /api/app-catalog`). É o que permite a tela perguntar "qual app?"
 *  em vez de assumir o Instagram por omissão. */
export interface AppCatalogEntry {
  package: string;
  name: string;
  label: string;
  has_catalog: boolean;
  /** Quem provê a conta deste app (hoje só `instagram`). `null` = app sem conta gerenciada. */
  session_provider: string | null;
  needs_profile: boolean;
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
  /** `incompatible` = o aparelho não roda esta versão (API, ABI ou GMS); a versão desejada NEM foi gravada. */
  outcome: 'started' | 'pending' | 'already' | 'incompatible';
  reason: string;
  /** A entrega abre UM comando por aparelho: é por ele que a tela acompanha o desfecho, em vez de mostrar para
   *  sempre o selo "instalando" da resposta do POST. Ausente em `already`/`incompatible`, que decidem na hora. */
  command_id?: string;
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
  /** Chaves de `capabilities` mais frouxas que `defaults` — achado #114: afrouxar sempre foi aceito, isto marca. */
  loosened: string[];
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
  /** Quem decidiu (operador da sessão do painel). Nulo nas decididas antes de existir sessão — e nulo continua
   *  querendo dizer "não dá para saber", que é mais honesto do que carimbar `panel` em todas elas. */
  decided_by: string | null;
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
  /** Fim do log do emulador quando o desfecho foi negativo num verbo que sobe o aparelho (já sem segredo). */
  emulator_log?: string | null;
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
  /** Total do disco do worker (v0.9). Sem ele a barra de disco fica vazia — livre sozinho não diz se há folga. */
  disk_total_gb?: number | null;
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
  /** A versão que o servidor central roda — o que o agente daquela máquina deveria estar rodando. */
  expected_agent_version?: string | null;
  /** O agente daquela máquina roda código diferente do do central. Calculado a cada leitura, nunca gravado. */
  agent_outdated?: boolean;
  /** Aceleração declarada pelo agente: `kvm`, `kvm-inacessivel`, `kvm-ausente`. Ausente = não se sabe. */
  accel?: string | null;
  appium_mode: 'local' | 'central';
  appium_url?: string | null;
  max_slots: number;
  verbs: string[];
  state: 'online' | 'offline' | 'degraded' | 'maintenance';
  observed_state: string;
  maintenance: boolean;
  state_detail?: string | null;
  connected: boolean;
  /** Este worker é o próprio servidor central. Ele tem cartão próprio na Infraestrutura. */
  local: boolean;
  resources: WorkerResources;
  devices: WorkerDevice[];
  enrolled_at: string;
  last_seen_at?: string | null;
  /** Estado do túnel SSH que carrega o ADB remoto e o canal do agente (achado #179), à parte de `state`: o
   * túnel pode cair sem que o worker "pareça" offline por muito tempo, e vice-versa. `null` = nunca sondado
   * (sem aparelho externo vinculado, ou worker local, que não tem túnel). */
  transport_state?: 'up' | 'down' | null;
  transport_detail?: string | null;
  transport_since?: string | null;
}

export interface WorkerEnrollment { enrollment_token: string; expires_in_s: number }

/** O que `POST /instances/{id}/actions/{action}` devolve agora: algo para ACOMPANHAR, não uma promessa. */
export interface CommandAccepted { command_id: string; state: CommandState; deduplicated: boolean }

/** Resposta de `POST /commands/{id}/verify`. `changed=false` com `verifiable=true` significa "o estado do
 *  aparelho ainda não comprova nada" — o comando segue incerto, esperando uma pessoa. */
export interface CommandVerified { command: Command; changed: boolean; verifiable: boolean }

/** A decisão humana sobre um comando incerto. `note` é o que a pessoa observou. */
export interface CommandResolution { outcome: 'succeeded' | 'failed' | 'cancelled'; note?: string; requested_by?: string }

/** O pedido de cancelamento de um comando aberto: não há `outcome` a escolher — quem dá o desfecho é quem executa. */
export interface CommandCancelRequest { note?: string; requested_by?: string }

/**
 * Resposta de `POST /commands/{id}/cancel`. `delivered=false` NÃO é erro: significa que o pedido ficou
 * registrado (worker desconectado, ou verbo sem ponto seguro de interrupção) e o comando ainda espera o desfecho
 * de verdade — `cancel_requested` não é `cancelled`.
 */
export interface CommandCancelled { command: Command; delivered: boolean; detail: string }

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
  /** Resposta à recusa do pré-voo: criar a execução só com os aparelhos aptos. */
  only_ready?: boolean;
}

/** Um aparelho recusado pelo pré-voo de `POST /api/runs` (409 `preflight`). */
export interface PreflightDevice { instance_id: string; code: string; motivo: string; acao: string }

/** O `detail` do 409 `preflight`: por que cada aparelho não pode, e quais seguem aptos. */
export interface PreflightRefusal { devices: PreflightDevice[]; ready: string[] }

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
  per_instance?: ReportPerInstance[];
  untested?: unknown;
  markdown?: unknown;
}

/** Uma linha do relatório por aparelho. `worker_id`/`device_serial` são ONDE aquilo rodou, como ficou gravado. */
export interface ReportPerInstance {
  instance_id: string;
  status: string;
  detail: string | null;
  worker_id: string | null;
  device_serial: string | null;
  proven: boolean;
  delivery_level: DeliveryLevel | null;
  blocked_reason: string | null;
  needs: string | null;
  effects: string[];
  proven_steps: string[];
  manually_confirmed_steps: string[];
  open_steps: string[];
  plan_versions: number;
  ai_calls: number;
  ai_tokens: number;
}

/** Resposta de `GET /api/runs`: página do histórico + total, para o "carregar mais" saber se acabou. */
export interface RunPage { runs: RunSummary[]; total: number; limit: number; offset: number }

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

/** `GET /api/session`, `POST /api/login`: quem está operando o painel e o que esta origem exige.
 *  `PanelSession` e não `SessionInfo`: aquele nome já é da sessão do Instagram dentro de um perfil. */
export interface PanelSession {
  /** Nome que vai aparecer na auditoria, ou `null` quando ninguém entrou neste navegador. */
  operator: string | null;
  /** Esta origem precisa da chave de acesso (`API_TOKEN`) para logar? Falso no loopback. */
  token_required: boolean;
  expires_at: string | null;
}
