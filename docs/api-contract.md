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
| `GET /api/health` | – | `Health` (inclui `commit` e `migration`: qual código e qual esquema estão no ar) |
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

`install_apk` instala a versão **promovida** do pacote daquele app, pela camada de releases — com hash
conferido, assinatura aprovada e estado observado —, e não mais o arquivo de `apps.apk_path`. Sem nenhuma versão
promovida o comando fecha em `failed` com `sem_versao_promovida`, dizendo para importar o APK e promovê-lo: há
UM caminho de instalação, e um segundo (invisível para `device_app_state`) deixava o painel descrevendo o
aparelho errado.

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
  max_online_devices: number;   // vagas de RAM DESTE servidor (1–64): ligados + ligando + desligando;
                                // cada worker traz as vagas dele em `max_slots` (GET /api/workers)
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
| `POST /api/releases/upload?filename=&set_id=&final=&source_reference=` | o arquivo CRU (`application/octet-stream`) | 201 `{stored, size_bytes, set_id, imported}` |
| `POST /api/releases/{id}/approve-signature` | `{note?}` | `AppRelease` |
| `GET /api/releases/{id}/icon` | – | a imagem (`image/png`/`image/webp`), ou 404 `sem_icone` |
| `GET /api/releases/{id}/targets` | – | `{release_id, package, targets: ReleaseTarget[]}` |
| `GET /api/app-state?package=` | – | `DeviceAppState[]` |

`POST /releases/upload` é a entrada de APK para quem não tem acesso ao disco do servidor. Não é multipart: o
corpo é o arquivo, e um conjunto de splits vai arquivo a arquivo com o MESMO `set_id` — só o que leva
`final=true` manda importar a pasta (importar a cada arquivo reprovaria por "falta o base.apk"). O arquivo passa
pela mesma inspeção da pasta de entrada; enviar não instala nada e não aprova assinatura nenhuma. Recusas:
400 `nome_invalido` (nome com caminho ou que não termina em `.apk`), 400 `set_id_invalido`, 400 `arquivo_vazio`,
413 `arquivo_grande` (512 MB por arquivo), 400 `import_failed`.

`ReleaseTarget`: `{id, worker_id, state, compatible, reason, app_state, installed_release_id,
installed_version_name, already}`. É o que a tela usa para escolher destinos. A compatibilidade é julgada pelo
BACKEND, com a mesma função que recusa `POST /instances/{id}/app/install` — nunca por uma segunda regra no
cliente, que ficaria velha na primeira mudança. O aparelho-loja não aparece na lista: ele é a FONTE do
aplicativo, nunca o destino.

`AppRelease`: `{id, package_name, version_name, version_code, artifact_type:'single'|'split_set'|
'unverified_split_set', signature_sha256, min_sdk, target_sdk, supported_abis, source_type, source_reference,
label, has_icon, imported_at, status, detail,
files:[{role:'base'|'split', split_name, file_name, sha256, size_bytes}], devices}`. `label` é o nome que o app
mostra ao usuário e `has_icon` diz se vale pedir `GET /releases/{id}/icon`: os dois saem do próprio APK
(`aapt2 dump badging`), e são nulos/`false` para release catalogada antes do catálogo visual ou cujo ícone é
adaptativo (XML, que nenhum navegador abre). O ícone NÃO entra em `files`: ele não se instala.
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
| `POST /api/instagram/profiles/{id}/connect\|verify\|logout` | – | 202 `{accepted, command_id, state, profile_id, instance_id}` — verbos `session.connect/verify/logout` na tabela `commands`; acompanhe por `GET /api/commands/{id}` |

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
| `GET /api/capabilities?package=` | – | `Capability[]` — **`package` obrigatório**: não há app por omissão |
| `GET /api/app-catalog` | – | apps que o registro conhece: `{package, name, label, has_catalog, session_provider, needs_profile}[]` |
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
| `POST /api/releases/{id}/lifecycle` | `{verb, instance_id?, note?, confirm_reinstall?, eager?, idempotency_key?}` | ver abaixo — `canary`/`rollback` devolvem `command_id`; `distribute` devolve um `command_id` por aparelho em `devices[]` |

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
| `GET /api/store?package=` | – | `StoreStatus` — **`package` obrigatório** (400 `package_required`) |
| `POST /api/store/open-listing` | `{package}` | `200 {ok, instance_id, package}` — abre a página do app na Play Store da loja |
| `POST /api/store/sync` | `{package, idempotency_key?}` | `202 {accepted, command_id, state, instance_id, package}` — acompanhe por `GET /api/commands/{id}`; `uncertain` NÃO é falha |

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
  emulator_log: string | null;           // v0.9 — cauda do log do emulador quando o boot terminou mal
  attempt: number;
  created_at: string; dispatched_at: string | null; acked_at: string | null;
  started_at: string | null; finished_at: string | null;
}
interface CommandAccepted { command_id: string; state: CommandState; deduplicated: boolean }
```

`rejected` garante que o aparelho **não foi tocado** (recusado no pré-voo, `dispatched_at` nulo). `uncertain` diz
que **não se sabe** o efeito — nada é repetido sozinho. `cancel_requested` **não** é `cancelled`: pedir não é
conseguir. As marcas de tempo contam a história: `dispatched_at` sem `acked_at` é "entreguei e não sei se chegou".

**O mesmo verbo produz os mesmos EFEITOS nas duas máquinas.** O desfecho que vem do agente é aplicado no
central como o caminho local aplicaria: `desired_state` gravado antes do despacho (a decisão é daqui e sobrevive
a reinício), `stop`/`hibernate` bem-sucedidos fecham sessão e captura e vão direto para `stopped`/`hibernated`
(sem passar por `error: sumiu do ADB`, que era alarme falso), `hibernate` grava o snapshot como válido, `reset`
invalida a sessão, zera `account_evidence` e marca o app como `missing`, e `start`/`wake`/`restart`/`reset`
readotam o aparelho na hora. `hibernate` remoto só é oferecido quando **o worker** declara `hibernation` no
`Hello`, e `wake` exige snapshot — sem ele a recusa é explicada, em vez de virar um boot a frio disfarçado.

**O mesmo verbo significa a mesma coisa nas duas máquinas.** `start`, `wake`, `restart` e `reset` só chegam a
`succeeded` com o aparelho **online**: recusa da guarda de RAM ou emulador que não subiu viram `failed` com o
motivo, e o prazo estourado vira `uncertain` (o boot continua; nada é morto por causa da espera). Os prazos são
os mesmos dos dois lados (`api.PRAZO_POR_VERBO`: `start` 540 s, `wake` 180 s, `restart`/`reset` 600 s,
`create` 240 s, `hibernate` 400 s). `create` que falhou vira `failed` — AVD inexistente nunca é sucesso. E
`hibernate` que **não** salvou snapshot vira `failed` ("desligado sem snapshot…; o próximo boot será a frio"):
o aparelho desligou, então não é recusa, mas hibernar era justamente evitar o boot a frio.

### Rotas

| Método e rota | Corpo | Resposta |
|---|---|---|
| `POST /api/instances/{id}/actions/{action}` | `InstanceActionBody` (ganhou `idempotency_key?`) | `202 CommandAccepted`; `409 {code:'rejected', command_id}`; `400` para verbo inexistente (sem registro) |
| `GET /api/commands/{command_id}` | – | `Command` |
| `GET /api/commands?instance_id=&unsettled=&limit=50` | – | `Command[]` (mais recente primeiro); `unsettled=true` devolve só os `uncertain` |
| `POST /api/commands/{command_id}/verify` | – | `{command, changed, verifiable}` · 404 se não existe |
| `POST /api/commands/{command_id}/resolve` | `CommandResolveBody {outcome, note?, requested_by?}` | `Command` · `409 {code:'not_unsettled'}` se não está `uncertain` |
| `POST /api/commands/{command_id}/cancel` | `CommandCancelBody {note?, requested_by?}` | `{command, delivered, detail}` · `409 {code:'not_open'}` se o comando já fechou |

### Cancelar é pedir, não desfazer

`POST /commands/{id}/cancel` leva o comando a `cancel_requested` e **entrega o pedido a quem está executando**:
`{'type':'cancel'}` pelo socket do worker, ou o cancelamento da tarefa de boot no caminho local. Só comando
**aberto** aceita o pedido (senão `409 not_open`), e repetir é seguro — o estado não muda de novo e o sinal sai
outra vez, que é o que se faz quando o worker acabou de reconectar.

O desfecho continua sendo de quem executa. `delivered:false` não é erro: significa que o pedido ficou registrado
(worker desconectado, ou verbo sem ponto seguro de interrupção) e o comando ainda espera o desfecho real —
`cancel_requested` **não** é `cancelled`.

`cancelled` só é gravado quando se pode afirmar que **o efeito não aconteceu**: cancelado antes do envio ao
worker, antes de a execução local começar, ou antes de o emulador subir. Cancelar depois disso vale `uncertain`
com o motivo (o agente responde `uncertain` quando já criou AVD, iniciou emulador ou começou a salvar snapshot;
no caminho local, quando o processo do emulador já existe). E um desfecho real que chegue no meio **ganha** do
pedido: a tabela de transições permite `cancel_requested → succeeded/failed/uncertain`. Cancelar um
`start`/`wake`/`restart`/`reset` também devolve `desired_state` a "parado", senão o monitor religaria em ≤30 s o
aparelho que alguém acabou de mandar não subir.

### `uncertain` tem saída

`uncertain` não se repete sozinho — e também não fica para sempre. Ele sai por duas portas, e só por elas:

1. **Verificação pelo estado real** (`POST /commands/{id}/verify`, a batida de cada worker e um laço periódico).
   Vale para `start`, `wake`, `restart`, `stop` e `hibernate`, cujo desfecho o estado do aparelho comprova
   (tabela `devices.manager.ESTADO_ALVO`). A sonda é **assimétrica**: ver o aparelho no estado prometido prova o
   sucesso; **não** ver não prova o fracasso (podem tê-lo desligado depois), então ela nunca conclui `failed`.
   `changed:false` com `verifiable:true` significa "ainda não dá para afirmar"; é resposta 200, não erro.
2. **Decisão de uma pessoa** (`POST /commands/{id}/resolve`). É a única saída possível para `reset`,
   `install_apk` e `open_app`, cujo efeito nenhum estado de aparelho revela. Quem decidiu, quando, o que observou
   e o motivo anterior ficam gravados em `result` (`resolved_by`, `resolved_at`, `note`, `previous_reason`).


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

## Adendo v0.8 — workers: a máquina que hospeda aparelhos

Aditivo. Nada foi removido nem renomeado. Ver `docs/worker.md` para o passo a passo de cadastro.

### Por que existe

O túnel carrega **só ADB**. Tudo que o executor local faz além disso — criar AVD, ligar o processo do emulador,
`emu kill`, salvar snapshot, guardar RAM, ler o log do emulador — não tinha contraparte na outra máquina. Era a
causa de fundo de "os comandos não são obedecidos nos remotos": não havia nada lá para obedecer.

### Tipos

```ts
interface WorkerResources { cpu_percent, cpu_count, ram_total_mb, ram_free_mb, disk_free_gb, disk_total_gb }
interface WorkerDevice {
  serial, avd_name, state, detail, adb_port, instance_id;
  // v0.9 — capacidades declaradas pelo agente, lidas do `config.ini` do AVD. Nulo = não se sabe.
  kind: string | null; system_image: string | null; api_level: number | null;
  abis: string[]; play_store: boolean | null;
}
interface Worker {
  id; name; os; os_version; agent_version;
  appium_mode: 'local' | 'central'; appium_url: string | null;
  max_slots: number; verbs: string[]; hibernation: boolean;   // declarado pelo worker no Hello
  state: 'online' | 'offline' | 'degraded' | 'maintenance';   // manutenção ganha na EXIBIÇÃO
  observed_state: string;      // o que se observa da conexão, sem a manutenção por cima
  maintenance: boolean; state_detail: string | null; connected: boolean;
  local: boolean;              // v0.9 — este worker É o servidor central (`LocalWorker`); ele tem cartão próprio
  resources: WorkerResources; devices: WorkerDevice[];
  enrolled_at: string; last_seen_at: string | null;           // idade do dado: sem ela não se sabe que a tela envelheceu
}
```

`state` e `observed_state` coexistem de propósito: um worker em manutenção **continua online**, e esconder isso
atrapalharia quem está diagnosticando.

### Rotas

| Método e rota | Corpo | Resposta |
|---|---|---|
| `GET /api/workers` | – | `Worker[]` |
| `GET /api/workers/{id}` | – | `Worker` |
| `POST /api/workers/enroll` | `{label?}` | `201 {enrollment_token, expires_in_s}` — token de **uso único**, 1 h, aparece uma vez |
| `POST /api/workers/{id}/maintenance` | `{on: boolean}` | `Worker` |

`Instance` ganha `worker_id: string | null` (nulo = esta máquina). Evento novo: **`worker.updated`**
(`{worker: Worker}`, persistido; nível `warn` quando offline ou degradado).

### Canal do worker — `WS /api/worker/ws`

**É o worker que liga para o central.** Atravessa NAT sem abrir porta na casa de ninguém.

1. Worker envia `{hello: Hello, token?: string, enrollment_token?: string}` — credencial **ou** inscrição, nunca
   as duas. Segredo não vai em query string, que acabaria em log de proxy.
2. Central responde `Welcome` (com `credential` **só** na inscrição, uma única vez) ou `Refused {code, message}`
   e fecha — recusa explicada, não socket calado. Toda recusa vira o evento persistido **`worker.refused`**
   (`{reason, ip, worker_id}`, nível `warn`), com o id **declarado**: antes a tentativa não deixava rastro nenhum
   e o operador não ficava sabendo que alguém tentava se passar por um worker.

Antes do `accept()`, nesta ordem: IP bloqueado ou com handshakes pendentes demais → fecha com **4429**; `Host`
fora de loopback ∪ `public_hosts` → **4403**. Depois do `accept()`: `hello` maior que 32 KiB ou que demore mais de
10 s → **4400**; credencial recusada → `Refused` + **4401**, e 5 recusas de um mesmo IP em 5 min bloqueiam aquele
IP por 60 s. Loopback continua valendo como `Host` aqui: pelo túnel o agente chega com `Host: 127.0.0.1:18000`.
3. Worker envia `heartbeat` a cada `heartbeat_s` (padrão 10 s), com recursos e inventário.
4. Central envia `dispatch {command_id, fence, verb, instance_id, serial, params, timeout_s}`.
5. Worker responde `ack` (recebi) e, depois, `result {command_id, outcome, reason, data}` com o `fence` de volta.

**Cerca (`fence`):** monotônica por aparelho. Resultado com cerca velha é **recusado** — worker que voltou do
limbo não sobrescreve o presente.

**O que vira `uncertain`:** prazo estourado, socket caído no meio, conexão nova do mesmo worker substituindo a
anterior, erro inesperado no agente. Falha ao **enviar** é o único caso que vira `failed`, porque é o único em
que se pode afirmar que nada aconteceu no aparelho.

**Indisponibilidade é a ausência de batida, não o socket fechado.** Socket cai por rede piscando; tratar as duas
coisas como a mesma é o defeito que a implementação de referência arrastou por anos.

### Capacidades com worker

`Instance.supported_verbs` passa a ser a **união** do que o worker declara (ciclo de vida) com os verbos de ADB,
que continuam saindo do central pelo túnel. Worker desconectado devolve o aparelho ao conjunto só-ADB, e o painel
para de oferecer botão que não faria nada.

### O que o worker NÃO recebe

Credencial de conta. O canal de entrada sensível continua central, e nenhuma senha de perfil atravessa o canal.

### Infraestrutura no painel (v0.8)

`Instance` ganha `worker_id: string | null` (nulo = o servidor central) e `PUT /api/instances/{id}` passa a
aceitá-lo — antes, amarrar um aparelho a um worker exigia `UPDATE` direto no banco e reinício, e **o que não tem
rota não tem como ser operado**. A mudança vale na hora: o runtime relê as capacidades do worker sem reiniciar.
Worker não inscrito é recusado com `400 unknown_worker`.

`GET /api/snapshot` passa a incluir `workers: Worker[]`, para a tela de infraestrutura hidratar sem uma chamada
extra. Campo opcional no cliente: backend antigo não o manda, e aí o que já havia é preservado em vez de apagado.

`GET /api/snapshot` também inclui `commands: Command[]` — **um por aparelho**, o mais recente entre os que ainda
estão em voo e os que acabaram `uncertain`. Os em voo, para recarregar a página não fazer o painel esquecer que
o aparelho está ocupado; os incertos, porque antes eles só existiam enquanto o toast durava e sumiam da tela no
primeiro F5, embora continuassem abertos no banco.

A view `infraestrutura` mostra, por servidor: estado, SO, versão do agente, **idade do último contato** (dado
velho não pode parecer atual), CPU/RAM/disco, vagas ocupadas, e a lista de aparelhos com **as duas visões** — o
estado do Android, que o central conhece, e o estado do processo, que só o worker conhece. Daí se navega para o
aparelho e para a tarefa que ele executa.

## Adendo v0.9 — autenticação: a API deixa de depender só do loopback

Até aqui **nenhuma rota era autenticada**, e estava certo: o backend só atendia `127.0.0.1`, e o middleware
recusava qualquer outro `Host`. Era também o motivo de um worker de outra máquina só alcançar o central por túnel
SSH reverso.

### O que mudou

A API pode atender num endereço de rede, e aí passa a exigir credencial:

| Situação | Resultado |
|---|---|
| `Host` de loopback (`127.0.0.1`, `localhost`, `[::1]`) | passa **sem** token |
| `Host` em `server.public_hosts`, com `Authorization: Bearer <API_TOKEN>` | passa |
| `Host` em `server.public_hosts`, sem credencial ou com credencial errada | `401 unauthorized` + `WWW-Authenticate: Bearer` |
| Qualquer outro `Host` | `403 forbidden_host` — **mesmo com a credencial certa** |
| Método que altera estado, com `Origin` fora de `allowed_origins` | `403 forbidden_origin` (como antes) |

Subir com `server.host` fora do loopback **sem** `API_TOKEN` ou **sem** `server.public_hosts` não é aceito: o
processo recusa arrancar, com a explicação de qual dos dois falta.

### Decisões, e por que elas são assim

- **Loopback sem token** é deliberado. Quem já está na máquina tem o banco e o adb na mão; exigir segredo ali não
  protegeria nada e quebraria o frontend servido localmente.
- **"Loopback" é o PAR, não o cabeçalho `Host`.** Isto era um defeito: a isenção olhava só `Host`, que quem chama
  escreve, e um `curl -H 'Host: localhost'` de qualquer máquina da rede atravessava o portão sem token (os nomes
  `test`/`testserver` também valiam em produção; saíram). Agora a isenção pede as duas coisas — par de loopback
  **e** nome de loopback. Nome de loopback vindo de outro IP responde **`401`**, não `403`: o nome não é hostil, o
  que falta é o segredo. O uvicorn sobe com `proxy_headers=False`, senão `X-Forwarded-For` reescreveria o par.
- **Par de loopback deixou de significar "esta máquina"** no dia em que o túnel SSH reverso existiu: toda conexão
  que chega pelo `-R` tem par `127.0.0.1` de verdade. Por isso `POST /api/admin/shutdown` exige, além do par
  local, o cabeçalho `X-Shutdown-Token` com o segredo de `data/shutdown.token` (regravado a cada subida do
  backend, ACL restrita, lido por `scripts/stop.ps1`); sem ele, `403`.
- **`public_hosts` não é redundante com o token.** O token responde *quem é você*; a lista responde *por qual nome
  você me chamou*. A segunda pergunta é a defesa contra **DNS rebinding** — um nome controlado pelo atacante que
  resolve para `127.0.0.1`, fazendo o navegador da vítima falar com este backend. Por isso `403` vence o token.
- **Comparação em tempo constante** (`hmac.compare_digest`). `==` sai no primeiro byte diferente, e o tempo dela
  conta quantos bytes o atacante acertou.
- **O `401` não é oráculo:** a resposta não repete o que foi enviado nem confirma o formato do que era esperado.
- **O canal do worker não passa por aqui.** `WS /api/worker/ws` autentica na **primeira mensagem** (token de
  inscrição de uso único, ou a credencial permanente), nunca em query string — query string acaba em log de proxy.
- **O canal do worker tem um listener só dele.** `server.worker_port` (127.0.0.1:8010) serve `/api/worker/ws` e
  **nada mais**: qualquer rota REST que chegue por ali recebe `404`. É para essa porta que o `-R` do túnel aponta.
  Antes o `-R` apontava para a 8000 e, como o par do túnel é loopback de verdade, qualquer processo da máquina do
  worker alcançava a API inteira sem credencial.

### Sessão do painel (item 9.1)

Uma segunda credencial, ao lado do `Authorization: Bearer`, para o cliente que **não consegue mandar
cabeçalho**: o `WebSocket` do navegador e a `<img>` do frame, da evidência e do avatar. Era por isso que o
painel só funcionava aberto na própria máquina central.

| Rota | Corpo | Resposta |
|---|---|---|
| `GET /api/session` | — | `{operator, token_required, expires_at}` |
| `POST /api/login` | `{operator, token?}` | `{operator, token_required, expires_at}` + `Set-Cookie` |
| `POST /api/logout` | — | `{ended}` |

- **As três respondem antes da credencial** (só elas, mais o frontend estático em `/`): sem isso, a tela de
  login receberia `401` no próprio pedido que a faria aparecer. `403 forbidden_host` **não** tem exceção.
- **O cookie** é `HttpOnly` (XSS não o lê), `SameSite=Strict` (outro site não o usa nem num GET, o que substitui
  o token de CSRF), `Path=/api` e `Secure` sempre que houver TLS declarado — nunca em HTTP simples, onde o
  navegador o descartaria calado. Vale 30 dias, com janela deslizante. No banco fica só o SHA-256 dele.
- **`token` só é exigido de quem ainda não passaria pelo portão.** Do loopback, o login é só o nome: quem está
  na máquina já tem o banco e o adb na mão, e cobrar segredo ali tiraria o login de quem só quer aparecer na
  trilha. É o que `token_required` responde, e é por isso que o painel pergunta `GET /api/session` antes de
  qualquer outra coisa em vez de esperar um `401` que no loopback nunca vem.
- **`WS /api/ws` aceita o cookie** e recusa com `4401` (sem sessão/credencial) ou `4403` (host não permitido).
  O painel para de tentar nesses dois códigos e mostra o login — antes ficaria em backoff para sempre.
- **Força bruta:** `POST /api/login` trava por 60 s depois de 8 tentativas inválidas em 1 min (`429
  too_many_attempts`), e a recusa é sempre `401 invalid_credentials`, sem dizer o que estava errado.

**Identidade na auditoria.** `commands.requested_by` e `pending_approvals.decided_by` passam a gravar o operador
da sessão, e **o nome do corpo da requisição não vence o da sessão** — `requested_by` no corpo era, até aqui, o
único "quem" que o banco guardava, e qualquer chamador escrevia ali o que quisesse. `panel` continua aparecendo
quando ninguém se identificou.

### O que ainda não existe

**Não há conta por pessoa.** Quem tem o `API_TOKEN` entra com o nome que quiser: a sessão dá **trilha**, não
controle de acesso por pessoa. Senha por usuário (hash, papéis, quem pode o quê) é decisão de quem cuida do
parque e continua fora do escopo.

## Adendo v0.9 — o central é um worker, capacidades declaradas e o log do emulador

**`LocalWorker`.** O servidor central se registra na tabela `workers` com o `OWNER_ID` e aparece em
`GET /api/workers` como qualquer outra máquina, marcado com `local: true`. Os aparelhos desta máquina passam a
ter `worker_id` preenchido (antes era nulo), e o **ciclo de vida sai por um despacho só** — o mesmo
`Dispatch`/`Ack`/`Progress`/`Result`, com a mesma cerca e o mesmo prazo, para o agente do notebook e para este
servidor. Consequência para quem consome a API: um comando de aparelho local agora passa por `acked` e traz
`worker_id`; `worker_id` não-nulo **deixou de significar "remoto"** (compare com o worker que tem `local: true`).
`DELETE /api/workers/{id}` e a rotação de credencial recusam o worker local com `409 local_worker`.

**Capacidades declaradas.** `Instance` ganha `device_kind`, `system_image`, `api_level`, `abis` e `play_store`;
`WorkerDevice` ganha os mesmos campos. Nulo/vazio quer dizer **não se sabe**, nunca "não tem". Eles alimentam a
recusa explicada ANTES de agendar: `POST /instances/{id}/app/install` responde `409 app_incompativel`,
`POST /runs` responde `409 app_incompativel` e `POST /releases/{id}/distribute` devolve
`outcome: "incompatible"` com a frase, sem gravar a versão desejada naquele aparelho.

**Log do emulador.** `Command` ganha `emulator_log`: a cauda do log do emulador (com redação de segredo) quando
`start`/`wake`/`restart`/`reset` terminam `failed` ou `uncertain`, nas duas máquinas. No protocolo do worker há o
verbo `emulator_log`, que lê o log e não toca no aparelho.

## Adendo v0.10 — onde o perfil vive, e aparelho novo sem editar YAML

**Localidade de perfil (E9).** `InstagramProfile` ganha `locality` e `offline_policy`.

```ts
type OfflinePolicy = 'wait' | 'reauth_elsewhere';

interface ProfileLocality {
  worker_id: string | null;      // máquina onde os dados vivem; null = este servidor
  worker_name: string | null;
  worker_state: string | null;   // online | degraded | offline | maintenance
  known: boolean;                // a localidade foi registrada neste vínculo
  available: boolean;            // aquela máquina responde agora
  moved: boolean;                // o id lógico aponta hoje para outro servidor/aparelho
  physical_id: string | null;    // impressão digital do aparelho no momento do vínculo
  detail: string | null;
}
```

`locality` é `null` quando o perfil não tem aparelho vinculado. O vínculo **fotografa** a máquina e a impressão
digital do aparelho no instante em que os dados passam a viver ali; `moved: true` quer dizer que o id lógico
mudou de lugar depois disso — e então o despacho é recusado, porque a sessão gravada no disco anterior não está
no aparelho atual. `known: false` é vínculo anterior a esta versão: o que não se sabe nunca invalida nada.

Duas recusas novas, ambas `409 locality_change_requires_confirmation`, e ambas dispensadas pela confirmação
explícita de uma pessoa:

| Rota | Quando recusa | Como confirmar |
| --- | --- | --- |
| `PATCH /api/instagram/profiles/{id}` | trocar o aparelho para outro **servidor** com a sessão `session_ready` | `confirm_locality_change: true` no corpo |
| `PUT /api/instances/{id}` | mudar `worker_id` de um aparelho que hospeda perfil com sessão pronta | `confirm_locality_change: true` no corpo |

Trocar de aparelho **dentro da mesma máquina** não é este caso e segue sem confirmação. Quem confirma recebe a
sessão invalidada no mesmo movimento. `offline_policy` (`PATCH` do perfil) decide o que fazer quando o servidor
do perfil não está disponível: `wait` (padrão) espera, e `reauth_elsewhere` autoriza entrar na conta de novo em
outro servidor.

**Inventário e instância dinâmica.** `Instance` ganha `inventory_state` (`"divergent"` ou nulo),
`inventory_detail`, `origin` (`"config"` | `"dynamic"`), `tunnel_port` e `remote_adb_port`. A cada `hello` e a
cada batida, o central confronta `instances.worker_id`, o mapa de portas e o inventário que o agente declara; a
divergência é exposta nesses campos e faz `POST /instances/{id}/actions/{verbo}` recusar com `409 rejected` os
verbos destrutivos (`reset`, `stop`, `restart`, `hibernate`, `install_apk`, `create`) daquele aparelho.

| Rota | Corpo | Resposta |
| --- | --- | --- |
| `GET /api/workers/devices/unbound` | – | `WorkerDeviceProposal[]` — aparelhos anunciados que ainda não são instância |
| `POST /api/workers/{id}/devices/adopt` | `{serial, instance_id?}` | 201 `{instance, tunnel_map, tunnel_map_file}` |

A adoção cria a instância com a porta do túnel **alocada pelo central**, grava o mapa em
`data/tunnel/<worker_id>.map` e põe o aparelho no painel sem reiniciar o backend. Códigos de recusa:
`unknown_device` (o worker não anunciou aquele serial), `no_adb_port` (anunciado sem porta de ADB),
`already_bound` e `rejected` (id de instância já em uso).
