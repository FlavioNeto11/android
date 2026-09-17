# Contrato da API (backend ⇄ frontend)

Base: `http://127.0.0.1:8000`. Todas as rotas REST ficam sob `/api`. JSON em `snake_case`.
Datas: ISO-8601 UTC (`2026-09-17T12:00:00.123Z`). Erros: `{"detail": {"code": string, "message": string, ...}}`
com status HTTP adequado (400 validação, 404, 409 conflito/estado inválido, 503 dependência indisponível).

O frontend **solicita e acompanha**. Fila, scheduler, regras e persistência são do backend.

## Tipos (TypeScript)

```ts
type InstanceState = 'absent' | 'stopped' | 'booting' | 'online' | 'stopping' | 'error';
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
  postcondition: { kind: 'text_visible' | 'app_foreground' | 'element_present' | 'model_judged'; value: string; description: string };
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
}

interface Action {
  id: number; attempt_id: string; seq: number;
  tool: string; args: Record<string, unknown>;
  rationale: string | null;        // resumo legível da decisão
  status: ActionStatus;
  side_effect: boolean;
  intent_at: string; done_at: string | null;
  result: Record<string, unknown> | null; error: string | null;
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
  step_timeout_s: number; objective_timeout_s: number; driver_call_timeout_s: number;
  retry_backoff_s: number; no_progress_limit: number;
  ai_max_calls_per_objective: number; ai_max_tokens_per_run: number;
  capture_grid_interval_s: number; capture_focus_interval_s: number; frame_max_age_ms: number;
  log_retention_days: number; evidence_retention_days: number;
}

interface AiStatus {
  provider: 'anthropic' | 'simulated' | string;
  model: string | null;
  configured: boolean;            // chave presente
  simulated: boolean;             // modo simulado de desenvolvimento
  sends_data_externally: boolean; // screenshots/textos saem da máquina
  notice: string;                 // texto para exibir
  effort: string | null;
}

interface Health {
  status: 'ok' | 'degraded' | 'error';
  version: string;
  ai: AiStatus;
  appium: { running: boolean; port: number; detail: string | null };
  sdk: { found: boolean; root: string | null; emulator_version: string | null; accel: string | null };
  problems: { code: string; message: string; hint: string }[];
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
```

## REST

| Método e rota | Corpo | Resposta |
|---|---|---|
| `GET /api/health` | – | `Health` |
| `GET /api/snapshot` | – | `Snapshot` |
| `GET /api/diagnostics?refresh=0|1` | – | objeto livre `{collected_at, host:{...}, tools:{...}, acceleration:{...}, capacity:{...}, measurements:[...]}` |
| `GET /api/metrics` | – | `Metrics` |
| `GET /api/settings` / `PUT /api/settings` | `Partial<Settings>` | `Settings` |
| `GET /api/ai` | – | `AiStatus` |
| `GET /api/apps` | – | `AppConfig[]` |
| `POST /api/apps` | `Omit<AppConfig,'id'|'builtin'>` | `AppConfig` |
| `PUT /api/apps/{id}` | idem parcial | `AppConfig` |
| `DELETE /api/apps/{id}` | – | `204` |
| `GET /api/instances` | – | `Instance[]` |
| `PUT /api/instances/{id}` | `{app_id?, account_label?}` | `Instance` |
| `GET /api/instances/{id}/packages` | – | `{packages: string[]}` (exige online) |
| `POST /api/instances/{id}/actions/{action}` | ver abaixo | `202 {accepted:true}` |
| `POST /api/instances/bulk` | `{ids:string[], action, params?}` | `202 {accepted:string[], rejected:{id,reason}[]}` |
| `GET /api/instances/{id}/frame?mode=thumb|full` | – | `image/jpeg` + cabeçalhos `X-Frame-Id, X-Frame-Ts, X-Frame-Width, X-Frame-Height, X-Frame-Orientation`; `404` se não há frame |
| `GET /api/instances/{id}/hierarchy` | – | `{ts, elements: {id,text,desc,resource_id,class_name,bounds:[x1,y1,x2,y2],clickable,enabled,focused}[]}` |
| `POST /api/instances/{id}/control/take` | – | `{status:'granted'|'pending', lease_id}` |
| `POST /api/instances/{id}/control/release` | `{lease_id}` | `{status:'released'}` |
| `POST /api/instances/{id}/input` | `ManualInput` | `{ok:true}`; `409 {code:'stale_frame'|'frame_mismatch'|'not_controller'}` |
| `POST /api/runs` | `{command, instance_ids, idempotency_key, mode:'plan'|'execute'}` | `RunSummary` (`deduplicated:true` se a chave já existia) |
| `GET /api/runs?limit=20` | – | `RunSummary[]` |
| `GET /api/runs/{id}` | – | `RunDetail` |
| `GET /api/runs/{id}/events?after=0&limit=500` | – | `EventRecord[]` |
| `GET /api/runs/{id}/report` | – | relatório final `{run, totals, per_instance:[...], untested:[...], markdown}` |
| `POST /api/runs/{id}/start` | – | `RunSummary` (apenas de `planned`) |
| `POST /api/runs/{id}/pause` · `/resume` · `/cancel` | – | `RunSummary` |
| `POST /api/runs/{id}/retry_failed` | – | `{retried:string[], skipped:{objective_id,reason}[]}` |
| `POST /api/runs/{id}/objectives/{oid}/resolve` | `{resolution:'confirm_done'|'retry'|'abandon', note?}` | `Objective` |
| `GET /api/evidence/{id}` | – | arquivo (image/jpeg, text/plain) |

Ações de instância (`action`): `create` (cria AVD), `start`, `stop`, `restart`, `reset` (apaga dados; exige
`{confirm:true}`), `install_apk` (`{app_id}` ou usa o app associado), `open_app`, `home`, `back`, `recents`.
As ações `open_app/home/back/recents` exigem controle do usuário **ou** instância sem execução ativa.

```ts
interface ManualInput {
  lease_id: string;
  frame_id: string;                 // frame que o usuário estava vendo
  type: 'tap' | 'long_press' | 'swipe' | 'text' | 'key';
  x?: number; y?: number;           // pixels do aparelho (FrameInfo.width/height), já convertidos pelo frontend
  x2?: number; y2?: number; duration_ms?: number;
  text?: string;
  key?: 'back' | 'home' | 'recents' | 'enter' | 'delete';
}
```
`text` e `key` não exigem coordenadas, mas exigem `lease_id` e um `frame_id` recente.

## WebSocket `/api/ws?last_event_id=N`

1. O cliente faz `GET /api/snapshot`, guarda `last_event_id` e abre o WS com esse valor.
2. O servidor envia `{type:'hello', server_time, last_event_id}`; em seguida reenvia os eventos persistidos com
   `id > N` e passa a transmitir ao vivo. Se `N` for antigo demais (>5000 eventos), envia `{type:'resync'}` e o
   cliente refaz o passo 1. Reconectar **nunca** reenfileira execuções.
3. Mensagens do servidor: `{type:'event', event: EventRecord}`.
4. Mensagens do cliente: `{type:'focus', instance_id: string|null}` (aumenta a taxa de captura do aparelho em foco;
   expira em 15 s sem renovação) e `{type:'ping'}` → `{type:'pong'}`.

### Eventos (`EventRecord.kind`)

| kind | data | persistido |
|---|---|---|
| `instance.updated` | `{instance: Instance}` | sim |
| `frame` | `{instance_id, frame: FrameInfo}` | não |
| `metrics` | `{metrics: Metrics}` | não |
| `health.updated` | `{health: Health}` | não |
| `run.updated` | `{run: RunSummary}` | sim |
| `objective.updated` | `{objective: Objective}` | sim |
| `step.updated` | `{step: Step}` | sim |
| `attempt.updated` | `{attempt: Attempt}` (sem `actions`) | sim |
| `action.logged` | `{action: Action, instance_id, step_id}` | sim |
| `evidence.added` | `{evidence: Evidence}` | sim |
| `plan.revised` | `{objective_id, version, reason}` | sim |
| `control.changed` | `{instance_id, control, pending}` | sim |
| `decision` | `{text}` | sim |
| `log` | livre | sim |
| `apps.updated` | `{apps: AppConfig[]}` | não |
| `settings.updated` | `{settings: Settings}` | não |

`message` sempre traz um texto legível em português para a linha do tempo.
