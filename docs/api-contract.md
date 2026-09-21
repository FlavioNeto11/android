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

## Adendo v0.2 — rodízio, hibernação, custo de IA, fluxos e receitas

Mudanças de tipos (todas aditivas):

```ts
type InstanceState = 'absent' | 'stopped' | 'hibernated' | 'booting' | 'online' | 'stopping' | 'error';
//  hibernated = desligado com snapshot salvo: acorda em segundos e não ocupa RAM
type InstanceAction = /* anteriores */ | 'hibernate' | 'wake';   // hibernate → 409 se android.hibernation=false

interface Settings {            // + campos do rodízio (editáveis em tempo de execução)
  auto_start_devices: boolean;  // o scheduler liga o aparelho quando há tarefa para ele
  max_online_devices: number;   // vagas de RAM (1–10): ligados + ligando + desligando
  min_online_dwell_s: number;   // anti-vaivém
  idle_stop_s: number;          // 0 = só desliga para ceder vaga
}
interface AiStatus { /* + */ models?: { plan: string; decide: string; verify: string; escalation: string } | null;
                     recipes?: 'off' | 'shadow' | 'replay' | null; flows?: boolean | null;
                     image_policy?: 'always' | 'auto' | 'never' | null; }
interface Health   { /* + */ features: { hibernation: boolean; recipes: string; flows: boolean; image_policy: string;
                                          system_image: string } }
interface Step     { /* + */ driven_by: 'ai' | 'recipe' | 'recipe+ai' | null }   // quem decidiu as ações da etapa
interface Action   { /* + */ source: 'ai' | 'recipe' }                           // recipe = sem chamada de modelo

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
```

| Rota | Corpo | Resposta |
|---|---|---|
| `GET /api/usage?run_id=` ou `?days=7` | – | `UsageReport` |
| `GET /api/flows` | – | `Flow[]` |
| `PUT /api/flows/{id}` | `{status:'active'|'disabled'}` | `Flow` |
| `DELETE /api/flows/{id}` | – | 204 |
| `GET /api/recipes` | – | `Recipe[]` |
| `PUT /api/recipes/{id}` | `{status:'active'|'quarantined'}` | `{id,status}` |
| `DELETE /api/recipes/{id}` | – | 204 |

Comportamento: com `auto_start_devices`, objetivo de aparelho `stopped`/`hibernated`/`absent` fica `pending` com
`status_detail` "aguardando vaga (k/K ligados)" em vez de `waiting_user`. Eventos `decision` anunciam "receita vN
reproduzida (0 decisões de IA)", "receita divergiu — …; a IA assume esta etapa", "receita aprendida", "Plano
reaproveitado do fluxo …" e "Fluxo … salvo".

## Adendo v0.3 — coleta e repetição (`for_each`)
- `Postcondition.kind` ganha `items_collected` (etapa de coleta, comprovada pelo executor via `collect_list`).
- `PlanStep`/`StepDTO`: `for_each: string | null` (etapa-modelo ainda não expandida; nunca executa) e
  `variables: {item, item_index}` nas cópias (`<key>_i<n>`). `StepResult.items: string[] | null` na etapa de coleta.
- `Settings.for_each_max_items` (1–200, padrão 25).
- Ação `collect_list` em `ActionDTO.tool`; resultado `{items, count, pages, at_end}`. `scroll` devolve `changed`/`at_end`.
- Objetivo com itens que falharam: `status=failed`, `status_detail="N de M itens concluídos…; falharam: …"`.

## Adendo v0.4 — Instagram: perfis, releases, persona/memória, capabilities e aprovação

Tudo aqui é **aditivo**: nenhuma rota anterior mudou de forma. O que mudou de comportamento está no fim.

### Releases de aplicativo (`apks/inbox` → catálogo → aparelho)

| Método | Corpo | Resposta |
|---|---|---|
| `GET /api/releases?package=` | – | `AppRelease[]` |
| `POST /api/releases/import` | `{source_reference?, expected_package?}` | 202 `{imported: [...]}` |
| `POST /api/releases/{id}/approve-signature` | `{note?}` | `AppRelease` |
| `GET /api/app-state?package=` | – | `DeviceAppState[]` |

`AppRelease`: `{id, package_name, version_name, version_code, artifact_type:'single'|'split_set'|
'unverified_split_set', signature_sha256, min_sdk, target_sdk, supported_abis, source_type, source_reference,
imported_at, status, detail, files:[{role:'base'|'split', split_name, file_name, sha256, size_bytes}], devices}`.
`DeviceAppState`: `{instance_id, package_name, desired_release_id, installed_release_id, observed_version_name,
observed_version_code, observed_splits, first_install_time, last_update_time, state, pending_op, verified_at,
drift_kind, detail}`. Nenhum APK é baixado pelo sistema: os arquivos entram pela pasta.

### Perfis e credenciais

| Método | Corpo | Resposta |
|---|---|---|
| `GET /api/instagram/profiles` | – | `InstagramProfile[]` |
| `POST /api/instagram/profiles` | `ProfileCreate` (com `password`, write-only) | 201 `InstagramProfile` |
| `GET/PATCH/DELETE /api/instagram/profiles/{id}` | `ProfilePatch` | `InstagramProfile` / 204 |
| `PUT /api/instagram/profiles/{id}/credential` | `{login_identifier?, password}` | `InstagramProfile` |
| `DELETE /api/instagram/profiles/{id}/credential` | – | `InstagramProfile` |
| `POST /api/instagram/profiles/{id}/connect\|verify\|logout` | – | 202 `{accepted, profile_id, instance_id}` |

`InstagramProfile` **não tem campo de senha** — nem em resposta, nem em erro de validação. `credential` traz só
metadados (`configured`, `status`, `failed_attempts`, `blocked_until`). `session`:
`{status:'unknown'|'auth_required'|'auth_challenge'|'wrong_account'|'session_ready', instance_id,
observed_username, verified_at, detail}`.

### Persona, memória, histórico e contexto

| Método | Corpo | Resposta |
|---|---|---|
| `GET/POST /api/personas` | `PersonaInput` | `Persona[]` / 201 `Persona` |
| `GET/PATCH/DELETE /api/personas/{id}` | `PersonaPatch` | `Persona` / 204 |
| `POST /api/personas/{id}/preview` | `{kind?, profile_id?, counterparty?, incoming}` | `SocialDraft` |
| `GET/POST /api/instagram/profiles/{id}/memory` | `MemoryInput` | `MemoryItem[]` / 201 `MemoryItem` |
| `DELETE /api/instagram/profiles/{id}/memory/{memoryId}` | – | 204 |
| `GET /api/instagram/profiles/{id}/interactions?counterparty=&thread_key=&limit=` | – | `SocialInteraction[]` |
| `GET /api/instagram/profiles/{id}/context?counterparty=&thread_key=&content=` | – | `SocialContext` |
| `GET /api/instagram/profiles/{id}/auth-attempts?limit=` | – | `AuthAttempt[]` |
| `GET /api/instagram/profiles/{id}/runs?limit=` | – | `RunSummary[]` |

Persona pertence a **um** perfil (409 `persona_in_use` ao tentar compartilhar ou apagar em uso). A prévia não
publica nada e não toca no aparelho. Memória com formato de credencial é recusada com 400 `memory_refused`.

### Capabilities, política e aprovação

| Método | Corpo | Resposta |
|---|---|---|
| `GET /api/capabilities?package=` | – | `Capability[]` |
| `GET/PUT /api/instagram/profiles/{id}/policy` | `{limits?, capabilities?}` | `ProfilePolicy` |
| `GET /api/approvals?status=&profile_id=&limit=` | – | `Approval[]` |
| `POST /api/approvals/{id}/decide` | `{verb:'approve'|'edit'|'reject', content?, note?}` | `Approval` |

`ProfilePolicy`: `{limits, capabilities, defaults}` — `capabilities` é a política efetiva por ação
(`autonomous | approval_required | manual_only | disabled`) e `defaults` é o que o catálogo propõe.
Nome de ação ou limite desconhecido: 400 (`unknown_capability`, `unknown_limit`).

### Mudanças de comportamento

- `POST /api/runs` aceita `profile_ids` no lugar de `instance_ids` (resolve o aparelho vinculado a cada perfil);
  exatamente um dos dois é obrigatório. Perfil sem vínculo: 409 `no_binding`.
- `POST /api/runs/{run}/objectives/{objetivo}/resolve` com `confirm_done` devolve 409 `needs_approval` quando o
  item está parado esperando aprovação: nesse caso a decisão certa é aprovar, editar ou rejeitar.
- `AiStatus.models` ganha a função `social`; `UsageReport` pode trazer `role='social'`.
- `StepDTO` ganha `capability`, `commit_selector`, `band_guard` e `bindings` (nulos no planejamento livre).

---

## Adendo v0.5 — ciclo de vida de release

Aditivo. Nada foi removido nem renomeado; as rotas da v0.4 continuam valendo.

### Um verbo por chamada

| Método | Corpo | Resposta |
|---|---|---|
| `POST /api/releases/{id}/lifecycle` | `{verb, instance_id?, note?, confirm_reinstall?}` | ver abaixo |

`verb` é `canary | promote | quarantine | rollback`.

- `promote` e `quarantine` decidem no banco e respondem na hora: `200 {accepted:true, release}`.
- `canary` e `rollback` mexem no aparelho: `200 {accepted:true, instance_id, release_id, verb}` e o resultado
  aparece em `GET /api/app-state`. Ambos exigem `instance_id` (400 `instance_required`) e aparelho online
  (409 `not_online`).
- `rollback` exige que o aparelho tenha uma versão anterior registrada (409 `no_previous_release`).
- Promover sem prova registrada: 409 `lifecycle_refused`, com o motivo em texto. `canary` numa versão já
  promovida também é 409: provar de novo exige passar pela quarentena antes, para a promoção não ser desfeita
  em silêncio.
- Release inexistente: 404. Verbo fora da lista: 422 (validação do corpo).

### Dois eixos independentes numa release

`ReleaseDTO` ganha `channel`, `channel_at`, `channel_detail`, `canary_instance_id` e `validations`.

`status` responde **"dá para instalar este arquivo?"** — integridade, assinatura e compatibilidade.
`channel` responde **"esta versão já provou que funciona?"**:

| channel | significado |
|---|---|
| `candidate` | importada, nunca provada em aparelho nenhum |
| `canary` | em prova num aparelho só |
| `promoted` | instalou e abriu no canário |
| `quarantined` | falhou a prova; instalação bloqueada até alguém decidir o contrário |
| `rolled_back` | substituída de propósito por uma versão anterior |

`validations[]`: `{instance_id, stage:'install'|'launch', ok, detail, observed_at}`. É daí — e só daí — que sai a
promoção: a última prova de cada etapa, no aparelho do canário, tem de ter dado certo.

`POST /api/instances/{id}/app/install` recusa uma release em quarentena **antes de aceitar**, com
409 `release_quarantined` e o motivo em texto — junto da recusa por `status`, e pelo mesmo motivo: o trabalho roda
em segundo plano, então validar só lá dentro devolveria 202 e esconderia o problema. A saída da quarentena é pedir
`canary` de novo, de propósito.

`DeviceAppStateDTO` ganha `previous_release_id` (para onde o rollback volta), `last_operation`
(`install | reinstall | upgrade | downgrade | rollback | uninstall`) e `expected_splits` — o que **este** aparelho
deveria ter quando o conjunto foi filtrado por densidade, ABI e idioma (vazio quer dizer "o conjunto inteiro").
Sem registrar a escolha, `POST /api/instances/{id}/app/verify` cobraria os splits de outra configuração e acusaria
divergência para sempre. O `previous_release_id` só é gravado **depois** de a instalação mexer no disco: uma
tentativa que falha não pode virar o próprio alvo de rollback.

### Rollback e a recusa do Android

`confirm_reinstall` default é `false` — preservar os dados é sempre a primeira tentativa (`adb install -r -d`).
O Android pode recusar; nesse caso **nada é apagado**, o aparelho continua como estava e o estado do app fica com
`drift_kind='downgrade_refused'`. A única saída é `confirm_reinstall: true`, que desinstala antes e **apaga os dados
do aplicativo, inclusive a sessão**. A API nunca escolhe esse caminho sozinha.

### Matriz de invalidação, agora ligada

Qualquer instalação bem-sucedida — primeira instalação, reinstalação, atualização, downgrade ou rollback — leva a
sessão do perfil vinculado àquele aparelho para `unknown`, com o motivo em `session.detail`. `unknown` quer dizer
**observar antes de pedir a senha**, nunca "pedir a senha": se o próprio Instagram preservou o login, a verificação
termina em `session_ready` sem digitar nada. Memória, histórico, persona e vínculo pertencem ao perfil e não são
tocados por nada disso.

---

## Adendo v0.6 — a loja (Play Store) como fonte e a distribuição ao parque

Aditivo. Nada foi removido nem renomeado.

### O aparelho-loja

`instances.store` (config) nomeia UMA instância como loja. `Instance.kind` passa a valer `'emulator' | 'external' |
'store'`. A loja é o inverso do externo: o projeto gere o ciclo de vida dela e **nunca lhe despacha tarefa**.

| Onde | Comportamento com a loja |
|---|---|
| `POST /api/runs` | 400 `store_instance` se ela estiver entre os alvos |
| `POST/PATCH /api/instagram/profiles` | 400 `store_instance` ao tentar vincular um perfil a ela |
| `POST /api/instances/bulk` | rejeitada em qualquer ação, com o motivo em `rejected[]` |
| `POST /api/releases/{id}/lifecycle` (`canary`, `rollback`) e `POST /api/instances/{id}/app/install` | 409 `store_instance` — ela é a fonte, nunca o destino |
| `POST /api/instances/{id}/input` com `type:'text'` | 409 `store_text_blocked` — a conta Google é digitada na janela do emulador |
| `POST /api/instances/{id}/app/verify` | permitido (só lê) |
| rodízio | não é acordada sob demanda, nunca é despejada, **conta como vaga** |

### Rotas da loja

| Método | Corpo | Resposta |
|---|---|---|
| `GET /api/store?package=` | – | `StoreStatus` |
| `POST /api/store/open-listing` | `{package?}` | `200 {ok, instance_id, package}` — abre a página do app na Play Store da loja |
| `POST /api/store/sync` | `{package?}` | `202 {accepted, instance_id, package}` — o resultado aparece em `GET /api/releases` |

`StoreStatus`: `{configured, instance_id, package, state, store_version_code, store_version_name,
catalog_version_code, update_available, fleet_target_release_id, fleet_target_version_code}`. `package` default é
`instagram.package`. Sem loja configurada: 409 `no_store` (e `configured:false` no GET). Loja desligada: 409
`not_online`. Loja sob controle manual: 409 `device_busy`. Instalar ou atualizar NA Play Store é sempre um toque do
usuário — nenhuma rota faz isso.

`sync` não baixa nada da rede: copia, por adb, os arquivos que o Android tem em `/data/app` para uma subpasta da
inbox e importa **só ela**. Mesma versão com os mesmos splits já catalogada → nada é copiado.
`AppRelease.source_type` ganha `'store'`; `source_reference` é gerado (`"Play Store via android-NN em AAAA-MM-DD"`).

### Distribuir

`ReleaseLifecycleBody.verb` ganha `'distribute'` (sem `instance_id`) e o corpo ganha `eager?: boolean`.
Resposta: `200 {accepted, eager, devices: [{id, outcome:'started'|'pending'|'already', reason}]}`.
Exige `status='installable'` **e** `channel='promoted'`; senão 409 `lifecycle_refused`.

Grava `desired_release_id` em cada aparelho de tarefa. Ligado e livre → instala já (`started`). Desligado ou ocupado
→ `pending`: a **porta do app** do despacho instala antes da próxima tarefa daquele pacote. `eager:true` ("instalar
em todos agora") faz o rodízio ligar os pendentes dentro das vagas, sem esperar tarefa; a pressa vive em memória —
reinício do backend volta ao modo padrão sem perder a versão desejada.

Uma entrega que falha fica em `install_failed | verify_failed | incompatible | version_drift` e **não é repetida
sozinha**; o objetivo que dependia dela bloqueia com o motivo. Pedir `distribute` de novo é a nova tentativa.
Objetivo já em andamento nunca tem o app trocado no meio.

### Saúde

`Health.problems[]` ganha `system_image_missing` (imagem de sistema de override ausente, com o comando
`sdkmanager`); `Diagnostics.sdk.override_images[]`: `{instance_id, image, installed}`.

## Adendo v0.7 — comando como entidade: a interface para de chamar de sucesso o que só foi aceito

Aditivo. Nada foi removido nem renomeado.

### O defeito

`POST /api/instances/{id}/actions/{action}` respondia `202 {"accepted": true}` e disparava a ação sem registro
nenhum: sem id, sem ACK, sem estado terminal. Qualquer exceção era engolida e virava um evento `log` que o
frontend não tratava — ele só aparecia num painel fechado do Diagnóstico. A assimetria era o ponto: a IA já tinha
`ActionStatus` (`intended → done | failed | unknown | rejected`, gravado **antes** de tocar no aparelho) e o
comando do painel tinha só "aceitei a requisição". Em aparelho de outra máquina isso ficava grosseiro: "Resetar
dados" era recusado no fundo e ficava indistinguível de sucesso.

### Tipos

```ts
type CommandState =
  | 'created' | 'dispatched' | 'acked' | 'running'
  | 'succeeded' | 'failed' | 'uncertain' | 'rejected'
  | 'cancel_requested' | 'cancelled';

interface Command {
  id: string; instance_id: string; worker_id: string | null; verb: string;
  state: CommandState; fence: number; requested_by: string;
  reason: string | null;                 // motivo da recusa, ou o que deu errado
  attempt: number;
  created_at: string; dispatched_at: string | null; acked_at: string | null;
  started_at: string | null; finished_at: string | null;
}
interface CommandAccepted { command_id: string; state: CommandState; deduplicated: boolean }
```

`rejected` garante que o aparelho **não foi tocado** (recusado no pré-voo, `dispatched_at` nulo). `uncertain` diz
que **não se sabe** o efeito — nada é repetido sozinho. `cancel_requested` **não** é `cancelled`: pedir não é
conseguir. As marcas de tempo contam a história: `dispatched_at` sem `acked_at` é "entreguei e não sei se chegou".

### Rotas

| Método e rota | Corpo | Resposta |
|---|---|---|
| `POST /api/instances/{id}/actions/{action}` | `InstanceActionBody` (ganhou `idempotency_key?`) | `202 CommandAccepted`; `409 {code:'rejected', command_id}`; `400` para verbo inexistente (sem registro) |
| `GET /api/commands/{command_id}` | – | `Command` |
| `GET /api/commands?instance_id=&limit=50` | – | `Command[]` (mais recente primeiro) |

`POST /api/instances/bulk` mantém `accepted: string[]` e `rejected: {id, reason, command_id?}[]`, e **ganha**
`commands: {id, command_id, deduplicated}[]` — um comando por aparelho, porque o desfecho de um não fala pelo do
outro.

**Idempotência:** mesma `idempotency_key` devolve o comando **original** com `deduplicated: true`, sem agir de
novo. Verbo desconhecido responde 400 e **não** cria registro — chamada malformada não é tentativa de operar o
aparelho. No lote, a chave recebe o sufixo `:{instance_id}`.

### Evento

| kind | data | persistido |
|---|---|---|
| `command.updated` | `{command: Command}` | sim |

Nível do evento: `error` para `failed`/`rejected`, `warn` para `uncertain`, `info` para o resto.

### Reconciliação no reinício

Comando em voo recebe desfecho honesto na partida: `created` (nunca despachado) vira `failed` — nada aconteceu;
`dispatched`/`acked`/`running`/`cancel_requested` viram `uncertain`. Mesmo princípio que já valia para as ações da
IA (`intended` → `unknown`).

### Mudanças de comportamento no painel

- O toast do `202` passa a ser `info` ("solicitado"), nunca `success`. O `success` só aparece com `succeeded`
  confirmado; `uncertain` vira `warning` com o aviso de que nada será repetido; `rejected` vira `danger` dizendo
  que o aparelho ficou intacto.
- Todo clique manda `idempotency_key` própria.
- Evento `log` de nível `error` passa a virar toast — antes existia só num `Disclosure` fechado.

### Capacidades do aparelho (v0.7)

`Instance` ganha `supported_verbs: string[]` — os verbos que **aquele** aparelho aceita. O painel usa para não
oferecer botão que não faria nada; o pré-voo usa para recusar com a explicação. Os dois lados leem a mesma lista
(`backend/app/devices/verbs.py`), então contam a mesma história.

| Tipo | Aceita | Recusa |
|---|---|---|
| `emulator` | tudo | – |
| `store` | tudo menos `install_apk` e `open_app` | é a FONTE do app, nunca o destino |
| `external` (sem worker) | `start` (= reconectar o ADB), `install_apk`, `open_app`, `home`, `back`, `recents` | `create`, `stop`, `hibernate`, `wake`, `restart`, `reset` — o ciclo de vida do emulador vive na outra máquina |

A recusa vem como `409 {code:'rejected', message, command_id}` com a frase que explica a limitação, e o comando
fica em `rejected` — `dispatched_at` nulo prova que o aparelho não foi tocado. Em lote, cada aparelho recusado
aparece em `rejected[]` com `command_id`, e os demais seguem.

Quando um worker existir e declarar que consegue operar o aparelho (E4), é a declaração dele que vale — a
dedução por tipo é só o padrão de quem não tem worker.

**Configuração:** `instances.external` passa a ser validada contra os ids existentes, como `overrides` e `store`
já eram. Antes, `android-9` (sem o zero) era ignorado em silêncio e o aparelho subia como emulador local vazio.
