# Contrato da API (backend ⇄ frontend)

Base: `http://127.0.0.1:8000`. Todas as rotas REST ficam sob `/api`. JSON em `snake_case`.
Datas: ISO-8601 UTC (`2026-09-17T12:00:00.123Z`). Erros: `{"detail": {"code": string, "message": string, ...}}`
com status HTTP adequado (400 validação, 404, 409 conflito/estado inválido, 503 dependência indisponível).

O frontend **solicita e acompanha**. Fila, scheduler, regras e persistência são do backend.

**Como ler este documento.** O corpo abaixo é a base; cada "Adendo" que segue é CUMULATIVO e, quando um adendo
posterior contradiz um anterior (ou o corpo base), **o mais recente vale**. A fonte da verdade nunca é este
arquivo — é `backend/app/api.py` (rotas) e `backend/app/models.py` (tipos); divergências encontradas entre o
código e este documento estão registradas no início do Adendo v0.11.

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
  ai_max_calls_per_item: number; ai_max_calls_absolute: number;   // 17.12: teto = base + por_item × (itens − 1), até o absoluto
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
| `GET /api/snapshot` | – | `Snapshot`; `runs` traz as 20 execuções mais recentes **e todas as não terminais, exceto `planned`** (planejando, em execução, pausada, cancelando e `needs_input`), por mais antigas que sejam: o painel as conta (contador do topo e chip "Em andamento"; caixa de Pendências, que conta todas as `needs_input`), e uma execução fora das 20 fazia o contador abrir num número de janela (RF-05 e decisão D1 da revisão final de UX). `planned` é um plano pronto para inspeção, ainda não executado: não é "em andamento" e só vem se estiver entre as 20 recentes (decisão D2) |
| `POST /api/admin/shutdown?stop_emulators=0|1` | – | `202` — encerramento gracioso usado por `scripts/stop.ps1`. DUAS trancas: par de rede em `127.0.0.1`/`::1` **e** o segredo local de `data/shutdown.token` no cabeçalho (o par de loopback sozinho deixou de valer quando o túnel SSH reverso passou a chegar como loopback de verdade). Recusa vira evento `log` — e o segredo recebido nunca entra nele |
| `GET /api/diagnostics?refresh=0|1` | – | objeto livre `{collected_at, host:{...}, tools:{...}, acceleration:{...}, capacity:{...}, measurements:[...]}` |
| `GET /api/metrics` | – | `Metrics` |
| `GET /api/settings` / `PUT /api/settings` | `Partial<Settings>` | `Settings` |
| `GET /api/ai` | – | `AiStatus` |
| `GET /api/usage` (v0.28) | – | `UsageReport.by_account`: US$ por conta de IA (`anthropic`, `openai`, `gemini`) na janela ou na execução (ADR-051) |
| `GET /api/ai/balances` | – | `{accounts: AiBalance[], blocked, estimated: true, note}` (ADR-051) |
| `POST /api/ai/balances/{conta}` | `{balance, source?: manual ou console, observed_at?, currency?, units_per_usd?, note?}` | 201, o mesmo relatório; 404 `unknown_account`, 400 `invalid_observed_at` |
| `POST /api/ai/balances/{conta}/recharge` | `{amount > 0, currency?, note?}` | 201, o mesmo relatório (âncora nova = saldo de agora + valor); 409 `no_initial_balance`, 400 `invalid_recharge` |
| `PUT /api/ai/balances/{conta}` | `{warn_below?, block_below?, units_per_usd?, currency?}` (`null` desliga) | o mesmo relatório |
| `GET /api/apps` | – | `AppConfig[]` |
| `POST /api/apps` | `Omit<AppConfig,'id'|'builtin'>` | `AppConfig` |
| `PUT /api/apps/{id}` | idem parcial | `AppConfig` |
| `DELETE /api/apps/{id}` | – | `204` |
| `GET /api/instances` | – | `Instance[]` |
| `PUT /api/instances/{id}` | `{app_id?, account_label?}` | `Instance` |
| `GET /api/instances/{id}/packages` | – | `{packages: string[]}` (exige online) |
| `POST /api/instances/{id}/actions/{action}` | ver abaixo | `202 CommandAccepted` `{command_id, state, deduplicated}` — **substituído pelo adendo v0.7**: aceito NÃO é sucesso; acompanhe por `GET /api/commands/{id}` ou `command.updated` |
| `POST /api/instances/bulk` | `{ids:string[], action, params?}` | `202 {accepted:string[], rejected:{id,reason}[]}` |
| `GET /api/instances/{id}/frame?mode=thumb|full` | – | `image/jpeg` + cabeçalhos `X-Frame-Id, X-Frame-Ts, X-Frame-Width, X-Frame-Height, X-Frame-Orientation`; `404` se não há frame |
| `GET /api/instances/{id}/hierarchy` | – | `{ts, elements: {id,text,desc,resource_id,class_name,bounds:[x1,y1,x2,y2],clickable,enabled,focused}[]}` |
| `POST /api/instances/{id}/control/take` | – | `{status:'granted'|'pending', lease_id}` |
| `POST /api/instances/{id}/control/release` | `{lease_id}` | `{status:'released'}` |
| `PUT /api/instances/{id}/repair-pause` | `{ttl_s: 60..10800, reason: string(3..200)}` | `RepairPauseInfo` `{until, since, reason, by, remaining_s}`; pausa o reparo AUTOMÁTICO (escada e reinício por saúde) só deste aparelho; `ttl_s` obrigatório, expira sozinha, repetir renova; `422` sem prazo ou fora dos limites, `404` aparelho desconhecido |
| `DELETE /api/instances/{id}/repair-pause` | – | `{status:'resumed'}`; `404 {code:'no_repair_pause'}` sem pausa em vigor |
| `POST /api/instances/{id}/input` | `ManualInput` | `{ok:true}`; `409 {code:'stale_frame'|'frame_mismatch'|'not_controller'}` |
| `POST /api/runs` | `{command, instance_ids, idempotency_key, mode:'plan'|'execute', ai_profile?}` | `RunSummary` (`deduplicated:true` se a chave já existia; `ai_profile`/`ai_profile_source:'explicit'|'canary'|null`, item 17.7); `422 {code:'ai_profile_desconhecido'}` se `ai_profile` não está em `ai.profiles` |
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
| `GET /api/instagram/profiles/{id}/avatar` | – | `image/jpeg` da foto do perfil; **404** `sem_foto` quando não há (o portal cai nas iniciais sozinho, sem campo no DTO) |
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
| `GET /api/approvals?status=&profile_id=&run_id=&limit=` | – | `Approval[]` — `run_id` junta os textos de UMA execução (um por perfil) para serem lidos e decididos de uma vez |
| `POST /api/approvals/decide` | `{decisions: ApprovalDecisionItem[]}` (1–50) | decide várias de uma vez; cada uma é independente — uma recusada não impede as demais, e a resposta diz quais |
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
catalog_version_code, update_available, fleet_target_release_id, fleet_target_version_code}`. `package` não tem
padrão (400 `package_required`; o antigo `instagram.package` do `config.yaml` saiu no ADR-052). Sem loja configurada: 409 `no_store` (e `configured:false` no GET). Loja desligada: 409
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
| `POST /api/commands/{command_id}/resolve` | `CommandResolveBody {outcome, note?, requested_by?, origin?: 'panel'}` | `Command` · `409 {code:'not_unsettled'}` se não está `uncertain` |
| `POST /api/commands/{command_id}/cancel` | `CommandCancelBody {note?, requested_by?, origin?: 'panel'}` | `{command, delivered, detail}` · `409 {code:'not_open'}` se o comando já fechou |

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
3. Worker envia `heartbeat` a cada `heartbeat_s` (padrão 10 s), com recursos e inventário. Campos de relógio, ambos
   opcionais: `sent_at` (ISO UTC, relógio local do agente na saída da batida; o central re-mede o desvio a cada batida
   como `relógio do banco na chegada − sent_at`, positivo = worker atrasado) e `clock_offset_s` (desvio medido uma vez
   no `welcome`, mantido para central antigo). `sent_at` vale mais quando presente e legível; sem ele o central usa
   `clock_offset_s`. Acima de 5 s o worker fica `degraded` ("relógio desalinhado"), e o estado some sozinho quando o
   desvio medido volta ao limite. Sem versão nem feature nova (A9).
4. Central envia `dispatch {command_id, fence, verb, instance_id, serial, params, timeout_s}`.
5. Worker responde `ack` (recebi) e, depois, `result {command_id, outcome, reason, data}` com o `fence` de volta.

**Cerca (`fence`):** monotônica por aparelho. Resultado com cerca velha é **recusado** — worker que voltou do
limbo não sobrescreve o presente. O `Hello` traz `fences: {instance_id: int}` (opcional, padrão `{}`): a maior
cerca que o agente já executou em cada aparelho. O central despacha sempre acima dela, o que cobre o banco
restaurado de um backup mais antigo (K-004).

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

## Adendo v0.11 (24/09/2026) — o que o código tem e o documento não

### Divergências encontradas nesta revisão

1. **Dois adendos com o mesmo nome "v0.9"** — um em `## Adendo v0.9 — autenticação: a API deixa de depender só
   do loopback` (por volta da linha 881) e outro em `## Adendo v0.9 — o central é um worker, capacidades
   declaradas e o log do emulador` (por volta da linha 965). Não foram renumerados nesta revisão para não
   quebrar âncoras/links existentes; o pedido é para o próximo editor corrigir a numeração (um dos dois deveria
   ser v0.85 ou os adendos de v0.9 em diante precisam deslizar).
2. **`InstanceState` do topo do arquivo não lista `hibernated`** — o bloco de tipos no início (seção "Tipos
   (TypeScript)") define `InstanceState` sem `hibernated`; só o Adendo v0.2 corrige isso
   (`'absent' | 'stopped' | 'hibernated' | 'booting' | 'online' | 'stopping' | 'error'`). O código
   (`backend/app/models.py`, `class InstanceState`) tem `hibernated` desde sempre — é só o texto do topo deste
   documento que ficou desatualizado. Ao ler o tipo, use a versão do Adendo v0.2, não a do topo.

### Rotas que existem no código e não estão em nenhum adendo anterior

Resumidas a partir de `backend/app/api.py` e `backend/app/models.py`; ver o código para validação completa de
campo.

**Modo treinamento (itens 13.1–13.3)** — `backend/app/api.py:337-410`:

| Rota | Corpo | Resposta |
| --- | --- | --- |
| `POST /api/instances/{id}/training` (201) | `TrainingStartBody {intent, lease_id, app_id?}` | `TrainingSession` — exige controle manual do aparelho (`lease_id`) |
| `GET /api/training?instance_id=&limit=` | – | `TrainingSession[]` |
| `GET /api/training/{session_id}` | – | `TrainingSession` |
| `POST /api/training/{session_id}/stop` | – | `TrainingSession` |
| `POST /api/training/{session_id}/propose` | – | proposta gerada pela IA (uma chamada de modelo; `502 ai_error` se falhar) |
| `POST /api/training/{session_id}/save` | `TrainingSaveBody {proposal?, profile_ids[], group_ids[]}` | fluxo salvo (`FlowStore.learn_from_plan` + escopo) |
| `POST /api/training/{session_id}/discard` | – | `TrainingSession` (mesmo que `stop`, com `discard=true`) |

**Limites por servidor (item 10.5)** — `backend/app/api.py:2696-2736`, ver também
[`../worker.md`](worker.md#limites-por-servidor-item-105) e [`../dominios/parque.md`](dominios/parque.md):

| Rota | Corpo | Resposta |
| --- | --- | --- |
| `GET /api/servers/limits` | – | `ServerLimitsDTO[]` — por máquina: `declared` (o que ela declara), `decided` (o que o dono escolheu), `effective`, `locked` (campos travados e por quê), `connected`, `maintenance`, `devices`, `online`, `working`, `cpu_percent`, `cpu_count`, `ram_free_mb`, `ram_total_mb` |
| `PUT /api/servers/{worker_id}/limits` | `ServerLimitsPatch {max_slots?, boot_parallelism?, max_working?, min_free_ram_mb?}` — campo AUSENTE não mexe; campo `null` volta ao valor da máquina | `ServerLimitsDTO` atualizado |

Nota de validação: `ServerLimitsPatch.boot_parallelism`, `limits.boot_parallelism` (`config.py`) e a mensagem de
protocolo `Limits` aceitam até **10**. Até o item 10.6 (24/09) a mensagem prometia 16; hoje os três tetos são o
mesmo, e `tests/test_limites_por_servidor.py` falha se voltarem a divergir. O `hello` segue aceitando até 16, que é
só o que a máquina declara (o `worker.yaml` aceita até 8).

**Grupos de acesso (item 11.10)** — `backend/app/api.py:949-991`:

| Rota | Corpo | Resposta |
| --- | --- | --- |
| `GET /api/instagram/policy-groups` | – | `PolicyGroupDTO[]` |
| `GET /api/instagram/policy-defaults` | – | os limites-padrão (`DEFAULT_LIMITS`) que o editor de grupo usa como ponto de partida |
| `POST /api/instagram/policy-groups` (201) | `{name, description?, capabilities?, limits?, from_profile_id?}` | `PolicyGroupDTO` |
| `GET/PUT/DELETE /api/instagram/policy-groups/{group_id}` | `PUT`: campos parciais do grupo | `PolicyGroupDTO` / 204 |

**Contas por app (item 12.1)** — `backend/app/api.py:908-946`:

| Rota | Corpo | Resposta |
| --- | --- | --- |
| `GET /api/instagram/profiles/{id}/accounts` | – | `ProfileAccountDTO[]` |
| `POST /api/instagram/profiles/{id}/accounts` (201) | `ProfileAccountCreate {app_id, handle?, login_identifier?, password?, notes?}` | `ProfileAccountDTO` — 409 `duplicate_account` se já existe conta daquele app no perfil |
| `PATCH /api/instagram/profiles/{id}/accounts/{account_id}` | `ProfileAccountPatch {handle?, status?, session_status?, notes?}` | `ProfileAccountDTO` — 409 `session_managed` se o app tem login automático e o corpo tenta mudar `session_status` |
| `DELETE /api/instagram/profiles/{id}/accounts/{account_id}` (204) | – | 409 `anchor_account` para a conta Instagram (não pode ser removida) |
| `PUT /api/instagram/profiles/{id}/accounts/{account_id}/credential` | `CredentialUpdate` | grava no cofre (`account_credentials` ou, para Instagram, o cofre do perfil) |

**Visão por app (item 12.2)** — `backend/app/api.py:321-334`:

| Rota | Corpo | Resposta |
| --- | --- | --- |
| `GET /api/apps-overview?days=7` | – | resumo por app: contas, aparelhos, execuções, custo de IA, receitas, fluxos, versões |
| `GET /api/apps/{app_id}/overview?days=30` | – | o mesmo, para um app; 404 se o app não existe |

**Cobertura de fluxos** — `GET /api/flows/cobertura` (`api.py:475-481`) — cada fluxo com quantas etapas já têm
receita ativa para a versão promovida do app (os "caminhos mapeados" do parque) e o custo de IA esperado ao
repetir (zero/parcial/total); só leitura, sem efeito.

**Prévia de distribuição entre servidores (item 10.5)** — `GET /api/runs/distribution?count=&app_id=`
(`api.py:2429-2434`) — quais aparelhos uma execução distribuída pegaria AGORA, por servidor, sem criar nada;
usa o mesmo `taskqueue/balanceamento.py::distribuir` que `POST /api/runs` usaria de verdade.

**Capacidades de um perfil** — `GET /api/instagram/profiles/{id}/capacidades` (`api.py:829-840`) — o que a
persona já fez e quanto roda sem IA: fluxos concluídos com cobertura de receitas, etapas por origem
(receita/IA), interações confirmadas por tipo. Leitura pura, sem custo de modelo.

### Eventos ausentes da tabela de `EventRecord.kind`

A tabela de eventos deste documento (seção "Eventos") não lista os seguintes, todos em uso no código
(`backend/app/events.py`, `EPHEMERAL_KINDS` marca os que NÃO são gravados no banco):

| Evento | Persistido | Onde é emitido |
| --- | --- | --- |
| `command.updated` | sim | `commands/store.py`, `api.py`, `state.py` — toda mudança de estado de um comando |
| `worker.updated` | sim, só quando algo OBSERVÁVEL muda (estado, detalhe, inventário) | `state.py`, `workers/registry.py::on_heartbeat` |
| `worker.removed` | sim | `api.py` (worker removido pelo painel) |
| `worker.refused` | sim | `api.py` (conexão de worker recusada — host fora da lista, versão de protocolo incompatível) |
| `worker.metrics` | **não** (`EPHEMERAL_KINDS`) | `state.py` — CPU/RAM/disco a cada batida (10 s); persistir enchia o log (57% dos eventos) |
| `approval.pending` | sim | `state.py` — uma aprovação social passou a aguardar decisão |
| `app_state.updated` | sim | `state.py` |
| `session.needs_person` | sim | `modules/identity/application/session_rules.py::emit_needs_person_change`, chamada por `integrations/app_declarado/sessao.py::SessaoDeclarada._save` e `state.py::AppState._sessao_desmentida` — a sessão da conta entrou em `auth_challenge`/`wrong_account` |
| `training.input` | sim | `training/recorder.py` — cada entrada gravada numa sessão de treinamento |
| `instance.remediation` | sim | `commands/despacho.py::remediar` — cada degrau do reparo automático (ver [`dominios/parque.md`](dominios/parque.md#reparo-automático)) |

### Mensagens do canal do worker ausentes do adendo v0.8

Ver `backend/app/workers/protocol.py` (contrato completo; os dois lados importam o mesmo arquivo).

- **`limits`** (central → worker) — os limites que o dono decidiu para aquela máquina (`max_slots`,
  `boot_parallelism`, `min_free_ram_mb`; nunca `max_working`, que fica só no central). Campo `None` = "use o
  `worker.yaml`". **Quando é enviada:** na PRIMEIRA batida de coração de cada conexão
  (`workers/registry.py::on_heartbeat`) — **não** "logo depois do `welcome`", como o comentário de
  `workers/protocol.py` dizia até o item 10.6 (24/09); o motivo (no próprio código) é que o agente lê o `welcome`
  como resposta de um único `recv()` do `hello`, e qualquer mensagem antes dele seria lida fora de ordem.
- **`result_ack`** (central → worker) — confirma que o central RECEBEU e tratou o desfecho de um comando; só
  então o agente pode apagar o resultado do diário local dele. Sem isto, um resultado produzido com o canal
  caído se perderia para sempre.
- **`PROTOCOL_MIN = 1`** (`workers/protocol.py`) — versão mínima de protocolo que o central ainda atende (hoje
  igual a `PROTOCOL_VERSION`, também 1: nada é recusado por versão baixa ainda). Central recusa worker de versão
  MAIOR que a dele (mensagens que não entende); worker de versão MENOR que `PROTOCOL_MIN` recebe `refused` dizendo
  para atualizar o agente, em vez de conectar e falhar mais adiante.

## Adendo v0.12 (25/09/2026) — aparelho × app × perfil × sessão

- `InstanceDTO.stream` (`StreamInfo`): `status` ∈ `live | stale | capture_error | no_frame | device_offline |
  device_hibernated | worker_offline`, `detail`, `last_frame_at`, `frame_age_s`, `last_capture_error`,
  `last_capture_error_at`, `consecutive_capture_failures`. `stale` = aparelho online sem frame novo; nunca offline.
- `InstagramProfileDTO.app_on_device` (`AppOnDevice`; `state=null` = nunca inspecionado) e `session_actions`
  (`SessionActions`: `phase`, `detail`, e `connect`/`verify`/`logout`/`inspect_app` como `{allowed, reason}`).
- `POST /instagram/profiles/{id}/connect|verify|logout` recusam com `409 app_not_installed | app_not_verified |
  app_busy | session_busy` pela mesma regra. `verify` não exige mais senha.
- `AppDTO.promoted_release_id | promoted_version_name | promoted_version_code`; `apps.updated` também sai em
  `promote`/`quarantine`.
- `POST /instances/{id}/actions/install_apk`: `409 sem_versao_promovida` antes do 202; o 202 traz `install_target`
  (`app_id`, `app_name`, `package`, `release_id`, `version_name`, `version_code`, `mechanism`).
- `GET /instances/{id}/operational-context` e `GET /instagram/profiles/{id}/operational-context` (só leitura):
  `server`, `device`, `stream`, `apps[]` (presença, versão instalada × promovida), `profiles[]` (sessão e fase).
  Nunca carregam senha nem identificador de login.
- `authentication_attempts.stage = login_error_dialog` quando o app mostra o erro genérico de login.
- `InstanceDTO.connectivity` (`ConnectivityInfo`): `state` ∈ `unknown | healthy | degraded | unavailable`, `route`,
  `dns`, `tcp_443`, `validated`, `checked_at`, `detail`. Volta a `unknown` a cada entrada no ar e fora do ar; não
  muda `state` do aparelho. Também em `operational-context.connectivity`.
- `POST /instagram/profiles/{id}/connect` recusa com `409 device_no_internet` quando a internet do aparelho não está
  confirmada `healthy` (sonda na hora se o resultado tiver mais de 120 s).
- Config por máquina: `android.dns_servers` → `-dns-server` no boot do emulador.

## Adendo v0.13 (26/09/2026) — identidade do backend em `/api/health`

- `Health.service = "android-farm-central"`: identidade estável ("este HTTP é a Farm?"), separada de `commit`
  (versão), `migration` (banco) e `status` (ok/degradado). Nunca muda com o commit.
- Supervisor e scripts (`scripts/lib/farm-health.ps1`) só tratam como "Farm no ar" o corpo que a identifica —
  `service` correto, ou o esquema antigo completo (`status` + `version` + `ai` + `appium{port,running}` +
  `sdk{found}`), reconhecimento legado para não subir um segundo backend diante de uma Farm anterior ao campo. 503 da
  Farm é Farm viva; 404/HTML/JSON de outro serviço não é.

## Adendo v0.14 (25/09/2026) — prontidão real e sondas com trilha própria

- `InstanceDTO.readiness` (`ReadinessInfo`): `phase` ∈ `not_running | process_running | adb_device | boot_completed |
  android_responsive | ready`, `detail`, `since`. Também em `operational-context.readiness`. `online` só com `ready`:
  o framework precisa responder à sonda (`service check`), não só o adb.
- Entrada no ar (boot local, readoção, adoção externa) e `start`/`wake` do worker só fecham com o framework
  respondendo. Framework mudo dentro do prazo de boot = `booting` com o motivo; além do prazo = degradado (externo)
  ou `error` (boot local a frio); wake local mudo cai no boot a frio. O worker devolve `uncertain` com o motivo.
- Sondas de saúde, pressão e internet rodam numa trilha própria por aparelho; a fila da captura/automação não as
  cala mais.

## Adendo v0.15 (26/09/2026) — prontidão por subsistema

- `readiness.phase` não muda de vocabulário; `android_responsive`/`ready` agora exigem os TRÊS subsistemas de
  `devices/prontidao.py`: servicemanager (`service check`), system_server (`settings get`, só leitura) e display
  (`screencap > /dev/null`). `readiness.detail` diz qual ainda falta ("servicemanager respondeu; aguardando
  system_server").
- `start`/`wake` do worker: preparo que estoura o prazo (`AdbTimeout`) não é mais só aviso; pronto só com os três
  subsistemas respondendo dentro do prazo do verbo, senão `uncertain` com o degrau. O central usa a mesma função.
- Contrato temporal: pronto = NESTA tentativa, os três responderam DEPOIS do último sinal de não-resposta; nenhuma
  prontidão positiva sobrevive a um timeout nem a uma operação local ainda em execução. Estouro de prazo no preparo ou
  no acerto do relógio pós-boot/wake (`sync_clock`), no worker e no
  central, deixa ESTA tentativa não pronta mesmo que as sondas respondam logo depois: `AdbTimeout` encerra só o
  cliente adb local (efeito incerto no aparelho), e o `drain` de um `DriverTimeout` prova só o fim da thread local (o
  zumbi é drenado, com teto, para a próxima tentativa não concorrer com ele). Worker: `uncertain`. Central: `booting`
  até a próxima passagem (readoção/adoção externa), wake → boot a frio, a frio → `error` com a escada de reparo. Erro
  rápido (`AdbError`, que pode ser `device offline`) depois de uma prontidão positiva a invalida e exige rodada nova e
  completa; erro benigno segue sem bloquear, porque a rodada nova passa.
- Limitação conhecida: um `AdbTimeout` pode deixar efeito remoto tardio no mesmo guest (transação binder entregue a
  um `system_server` congelado executa quando ele destrava). Não há isolamento entre tentativas nem quarentena por
  geração de processo; a próxima tentativa no mesmo guest é recuperação funcional. Efeitos tardios não idempotentes
  identificados: o `input tap` de `dismiss_system_dialog` e o `cmd alarm set-time` do `sync_clock`.


## Adendo v0.16 (26/09/2026) — credencial fornecida para a execução (ADR-025)

- `POST /api/runs` aceita `credentials` (objeto nome → valor; nome em minúsculas, dígitos e `_`, até 8) e
  `consent_credentials` (bool). O valor vai ao cofre, ligado à execução, e é apagado quando ela termina; nenhuma
  resposta da API o devolve.
- Recusas novas, antes de gravar qualquer coisa:
  - `409 credencial_no_comando`: o texto do comando tem formato de segredo (ex.: `Senha: …`). Limitação conhecida,
    por escolha: a detecção é por formato, a mesma da redação dos logs, e também recusa texto descritivo como
    "credencial: escolha CPF" ou "token: aguarde o SMS" — recusar e pedir outra redação custa menos que deixar
    passar uma senha. A senha vai no campo `credentials`, nunca no comando.
  - `409 consentimento_de_credencial`: há credencial e falta `consent_credentials: true`. `details.credentials` (os
    nomes) e `details.instance_ids`; a mensagem diz o que acontece com cada dado. O painel pergunta e reenvia.
  - `503 cofre_indisponivel`: sem chave mestra pronta, a credencial não tem onde ficar.
- Ferramentas novas do ator: `type_secret(name, element_id?)` preenche só campo de senha, pelo canal sensível (o
  resultado traz o nome e o campo, nunca o valor), só no app da etapa e, no navegador, só no host de uma URL escrita
  pela pessoa (ou subdomínio); o envio do formulário é um `tap` à parte. `open_url(url)` abre só endereço http/https
  escrito no comando (nem parâmetro do plano, nem texto da tela), sem `usuário:senha@`; os mesmos endereços definem
  os sites onde `type_secret` digita. Nenhuma das duas vira receita.
- A credencial sai do cofre em `completed`, `cancelled` e `failed`, ou depois de 24 h parada; `completed_with_issues`
  a mantém (item aguardando a pessoa ainda será retomado).
- 422 de qualquer rota: erro cujo caminho passa por um nome sensível (credencial, senha, token…) sai sem `input` e sem
  `ctx`.
- Com credencial, a tela de senha deixa de pôr a etapa em `waiting_user`; desafio (código não fornecido, CAPTCHA)
  continua pedindo a pessoa.
- Comando que pede site/navegador ou nomeia outro app registrado não fica preso ao catálogo do app da conta do
  aparelho: o plano é livre.


## Adendo v0.17 (26/09/2026) — loja de aplicativos e proxy do aparelho

Pedido do dono de 26/09: uma loja no painel para cadastrar apps (Outlook, TikTok, VPN…), distribuir uma versão para
N aparelhos, para os escolhidos ou para todos, com prévia, e atualizar quem ficou na versão antiga. Na mesma
conversa ele decidiu incluir o proxy do aparelho. Domínio: [`dominios/apps-e-loja.md`](dominios/apps-e-loja.md).

- `POST /api/releases/{id}/lifecycle` com `verb: distribute` ganhou três campos opcionais:
  - `instance_ids` (os aparelhos escolhidos);
  - `count` (1–200, N aparelhos escolhidos pelo backend entre os que podem receber e ainda não estão na versão:
    ligados primeiro, e quem já tem o app antes de quem nunca teve);
  - `dry_run` (prévia).

  Sem `instance_ids` nem `count`, vale o parque inteiro, como antes. Os dois juntos dão `409 lifecycle_refused`. Um
  aparelho escolhido que é a loja, ou que não existe, também é recusado, com o nome. A resposta ganhou `dry_run`, e
  `accepted` é `false` na prévia. Na prévia, cada aparelho vem com `outcome` ∈ `would_start | pending | already |
  incompatible`, e nada é gravado nem instalado.
- `GET /api/app-store`: é a vitrine, com um item por app cadastrado. Traz `app_id`, `name`, `package`, `category`,
  `builtin`, `has_catalog`, `label`, `icon_release_id`, `promoted`/`latest` (`{id, version_name, version_code,
  status, channel}`), `releases`, `devices_with_app`, `by_version[]`, `other_version` (versão fora do catálogo),
  `outdated` (versão menor que a promovida), `pending`, `installing`, `failed` e `attention[]`. A loja fica fora
  das contagens.
- `POST /api/apps` e `PUT /api/apps/{id}` aceitam `category` ∈ `social | mensagens | email | rede | utilitario | qa`
  (a lista fixa do dono), e `AppDTO.category` a devolve. `POST /api/apps` com um pacote já cadastrado dá
  `409 package_exists`.
- `POST /api/releases/upload` aceita `.apks`, `.xapk` e `.apkm`, além de `.apk`. O contêiner vem sozinho, é
  extraído e passa pela mesma inspeção. O extraído tem teto de 2 GiB (`MAX_CONTAINER_EXTRACTED_BYTES`): acima dele,
  ou com cabeçalho que não confere, a importação é recusada com o motivo e nada fica no disco temporário.
- A importação de uma versão de pacote não cadastrado **cadastra o app** (com o rótulo lido do APK e sem categoria)
  e emite `apps.updated`.
- Aparelho que entra no ar recebe, no mesmo trabalho de reobservação, as versões distribuídas para ele de apps que
  **não** são o principal dele, e o proxy pedido. Falha não se repete sozinha.
- Proxy do aparelho:
  - `GET /api/proxies` devolve `{profiles[], devices[]}`;
  - `POST /api/proxies` recebe `{name, host, port}`, com `host` só nome ou IPv4, e responde `201`;
  - `DELETE /api/proxies/{id}` responde `204`, ou `409 proxy_in_use` se o proxy está pedido para algum aparelho;
  - `POST /api/proxies/apply` recebe `{proxy_id | null, instance_ids? | all: true, dry_run?}` e devolve
    `{accepted, dry_run, devices[]}`. É exatamente um dos dois alvos; sem nenhum, ou com os dois, dá
    `400 target_required`. O parque inteiro nunca é inferido.

  O ligado recebe um comando `device.proxy`, e o desligado fica `pending` até ligar. Estados por aparelho:
  `pending | applying | applied | failed`. `applied` só quando `settings get global http_proxy` responde o que foi
  pedido (prova a configuração, não o tráfego). Evento novo: `proxy.updated`, EFÊMERO (fica fora do log; a
  verdade está em `GET /api/proxies`).
  Pedido trocado enquanto o anterior era aplicado: o desfecho do anterior não é gravado por cima; a linha volta a
  `pending` e a varredura aplica o pedido novo.
- `PUT /api/apps/{id}` com o pacote de outro app cadastrado dá `409 package_exists`.
- A entrega pendente (app secundário ou proxy) de aparelho ligado e livre é feita por uma varredura de 60 s no
  hospedeiro. Não liga aparelho nem passa na frente de tarefa.

## Adendo v0.18 (26/09/2026) — prontidão sem efeito tardio não idempotente; relógio como condição própria

- O caminho de prontidão (preparo + escada, worker e central) só tem efeitos idempotentes (K-031). O `input tap` do
  diálogo de sistema e o `cmd alarm set-time` do relógio saíram dele; a limitação do v0.15 fica restrita ao que é
  idempotente.
- `start`/`wake` do worker não acertam mais o relógio: fecham na escada. O resultado continua `{"started", "pid",
  "from_snapshot"}`. Um estouro do relógio não deixa mais o verbo `uncertain`, nem o wake local cai no boot a frio
  (e descarta o snapshot) por causa dele.
- Relógio do convidado = condição própria do central, medida na entrada no ar e a cada 5 min (1 min depois de um
  acerto que estourou ou não convergiu). Não converge → `attention` começando por "Relógio do aparelho"; volta ao
  certo → o aviso some. Nunca muda `state` nem `readiness`. Cada acerto vira `measurements.kind = "clock"` com
  `clock_skew_before_after_s`; a medição `boot` deixou de ter esse campo.
- Diálogo de sistema que já estava na tela é dispensado depois de `ready`, antes da sessão de automação, com a
  confirmação do mesmo diálogo na mesma chamada do toque; estouro ali não muda `state` nem `readiness`.
- `readiness.detail` de `ready` é o da escada que acabou de passar ("servicemanager, system_server e display
  responderam"), não mais o texto do v0.14.
- Sonda do display: prazo de 12 s para 20 s (o do `screencap_png` do stream); o piso do orçamento de prontidão do
  central é uma rodada inteira (36 s). No worker, o tempo do preparo é devolvido à escada, até uma rodada.
- Readoção: depois de uma tentativa incerta (preparo estourado), a próxima no mesmo guest espera 30 s; adb `device` no
  serial do aparelho nunca vira `stopped`/`hibernated` (é `booting`, e o PID velho sai).
- Falha de código numa sonda (`AttributeError`/`NameError`/`TypeError`) não é "mudo": o worker devolve `uncertain` na
  hora, e o central, `error` com "a prontidão não pôde ser avaliada"; a pilha vai para o log. Cada rodada da escada
  deixa uma linha INFO com o tempo de cada degrau.

## Adendo v0.19 (26/09/2026) — todos os aparelhos na versão promovida (ADR-026)

Decisão do dono de 26/09: "todos devem ficar atualizados sempre". Domínio:
[`dominios/apps-e-loja.md`](dominios/apps-e-loja.md#todos-na-versão-promovida-adr-026-2609).

- `POST /api/releases/{id}/lifecycle` com `verb: promote` continua respondendo `200 {"accepted": true, "release"}` e
  ganhou dois campos:
  - `target_release_id`: a versão que o parque persegue, ou `null` se não há promovida instalável. Promover uma
    versão MENOR que a promovida não muda o alvo;
  - `devices[]`: um item por aparelho de tarefa que tem o app (a loja fica de fora), na forma de `distribute`,
    `{id, outcome, reason, worker_id}`. O `outcome` vale:
    - `started`: ligado e livre, instalando agora;
    - `pending`: ocupado (instala na varredura de 60 s), com tarefa esperando, ou desligado (instala quando ligar);
    - `already`: já está no alvo, ou numa promovida de mesmo `version_code`;
    - `incompatible`;
    - `kept` (valor novo): fica onde está, e `reason` diz por quê — versão mais nova que ninguém voltou (canário),
      entrega que falhou esperando a nova tentativa diária, recusa de voltar sem apagar dados, operação em andamento.

  Promover não liga aparelho nenhum e não abre comando: a entrega acontece pelo trabalho do aparelho
  (`run_device_job`) e o desfecho chega por `app_state.updated`, como na entrega ao ligar. Se a convergência
  imediata falhar, a promoção continua valendo (`200`), `devices` vem vazio e `convergence_error` diz o motivo; a
  varredura de 60 s e a entrada no ar entregam do mesmo jeito.
- Quem "tem o app": linha em `device_app_state` com `installed_release_id` ou `observed_version_code`, ou com versão
  desejada gravada; o app principal do aparelho conta mesmo sem linha. Nada é instalado em quem não tem o app.
- Aparelho que entra no ar, e cada passada da varredura, adota a promovida de TODOS os apps que tem, não só o
  principal. O app principal também é entregue sem tarefa, exceto com um objetivo rodando ou esperando uma pessoa.
- `verb: rollback`: no fim do trabalho da volta, os outros aparelhos que estão na versão voltada (`rolled_back`)
  convergem para a promovida anterior, com `-d` (preservando os dados). Recusa do Android: `install_failed` com
  `drift_kind: "downgrade_refused"`, sem nova tentativa automática. `verb: quarantine` não rebaixa ninguém.
- `promoted_release` (e com ele `promoted_release_id` de `GET /api/apps`, `fleet_target_release_id` de
  `GET /api/store` e `promoted` de `GET /api/app-store`): no empate de `version_code`, a promovida por último e
  depois o maior id. Antes, a ordem do empate era a do banco.
- Entrega que falhou: nova tentativa automática no máximo uma vez por dia, contada desde a última tentativa (comando
  de app ou prova de instalação em `app_release_validations`).
- Prova de abertura (`launch` em `app_release_validations`) de um app que NÃO é o principal do aparelho: depois dela,
  o aparelho volta à tela inicial e o pacote conferido é parado (`am force-stop`). A prova e o estado `ready`
  continuam iguais; o app principal segue aberto depois da prova, como antes. Motivo: o app de QA distribuído ao
  android-01 (26/09) ficou na frente e o "Abrir app" do Instagram terminou `uncertain` duas vezes.
- `POST /api/instances/{id}/actions/open_app`: com outro pacote em foco, o aparelho volta à tela inicial antes do
  `am start`. O contrato da resposta não muda.

## Adendo v0.20 (26/09/2026) — evolução de desempenho: prévia sob demanda, observação com imagem opcional, métricas

Contratos da primeira onda da evolução de desempenho (relatório em
[`relatorio-desempenho.md`](relatorio-desempenho.md)). Todos são **aditivos**: campo novo é opcional com padrão, e
cliente ou agente antigo mantém o comportamento de antes.

**C1. Observação** (`devices/manager.py::Observation`). Os campos de hoje ficam (`frame_id`, `ts`, `width`,
`height`, `jpeg`, `tree`, `package`, `sensitive`). Entram, opcionais:
- `tree_at` e `image_at`: horário ISO da hierarquia e do screencap (`image_at: null` = sem imagem);
- `image_omitted`: `sensitive` ou `policy` quando `jpeg` é `null`. Omitir a imagem **não** é falha de captura;
- `source` (`central_adb`, ou `worker_local` quando a imagem veio da captura na origem, `observe_local`) e
  `runtime_gen` (geração do runtime do aparelho).

Regras:
- coordenadas só saem de uma observação cuja árvore e imagem foram lidas em sequência, no executor do aparelho;
- imagem adquirida depois (evidência de falha) é só evidência, com o próprio horário;
- sem imagem, `width`/`height` vêm do último frame da mesma geração e orientação, ou de `wm size`; nunca de outro
  aparelho.

`observe(rt, timeout=…, imagem=True | False | f(tree) -> bool)` lê a árvore primeiro e só captura a imagem se
pedida. O padrão (`True`) preserva o comportamento anterior.

**C2. Interesse em prévia** (WebSocket `/ws`, cliente → servidor):
`{"type": "watch", "grid": ["android-01", …], "focus": "android-03" | null, "ttl_s": 20}`.
- Substitui o interesse DESTA conexão; não acumula. `ttl_s` fica entre 5 e 60; o cliente renova antes de vencer.
- Por aparelho, o interesse é de foco se alguma conexão o tem em `focus`, e de grade se alguma o tem em `grid`.
  Desconectar apaga o interesse da conexão.
- `{"type": "focus", "instance_id": …}` continua valendo: vira foco com TTL de 15 s.
- **Cliente antigo:** conexão que nunca mandou `watch` conta como interesse de grade em todos os aparelhos enquanto
  estiver conectada, que é o comportamento anterior. Consequência aceita: uma aba antiga custa o de antes até
  recarregar.
- Interesse de visualização não concede controle manual. Controle manual (`control: user`) conta como foco.
- Rodízio e hibernação continuam considerando só o foco e o controle manual; interesse de grade não mantém aparelho
  ligado.
- `Settings.preview_mode`: `on_demand` (padrão) ou `always` (o laço antigo, como volta atrás sem reinício). Com
  `on_demand` e sem interesse, não há captura periódica de prévia.

**C3. Estado da tela.** `StreamInfo.status` ganha `paused`: aparelho online, prévia suspensa por falta de
interesse. Não é `stale` nem erro. Na ordem das perguntas de `devices/stream.py`, vem depois de
offline/hibernado/worker fora e antes de `no_frame`/`live`. Com o interesse de volta, a captura é imediata.

**C4. Tela sensível na prévia.** A prévia nunca mostra tela classificada como sensível:
- a classificação vem antes de publicar o frame;
- `GET /api/instances/{id}/frame` responde 404 `sensitive_screen` quando o último frame é sensível;
- a captura de prévia pausa durante `type_secret`;
- a VM-loja segue a mesma regra.

**C5. Métricas agregadas** (`backend/app/metricas.py`). Contadores e distribuições em memória, com teto de séries.
Rótulos são valores curtos de conjunto pequeno e nunca id de execução, texto de tela ou credencial. Grava-se uma
linha agregada por janela de 15 min em `measurements` (`kind='metricas'`), apagada pela retenção de
`log_retention_days`; o Diagnóstico não lista esse `kind`.

`GET /api/desempenho?janelas=24` devolve `{"processo": {desde, ate, uptime_s, contadores[], distribuicoes[], owner},
"janelas": [...]}`. Cada distribuição traz `n`, `soma`, `min`, `max`, `media`, `p50`, `p95` e `amostra_n`.
Percentil sem amostra é `null`. Não confundir com `GET /api/metrics`, o retrato de CPU e RAM do host.

`?dias=N` (0 a 90, padrão 0) acrescenta `historico`: o `desempenho.resumo` dos últimos N dias, calculado sobre as
tabelas que já existiam (objetivos, etapas, ações, `ai_calls`, comandos e boot). Traz p50/p95/n por entidade,
com taxas separadas de sucesso, falha, incerto, espera humana e cancelamento. Intervalos que se sobrepõem não se
somam.

Nomes reservados:
- `captura.total{origem,resultado}`, `captura.evitada{motivo}`, `captura.ms{origem}`, `captura.bytes{origem}`;
- `codificacao.ms{tipo}`, `observacao.ms{parte}`;
- `receita.consulta{resultado}`, `receita.reproducao{resultado}`, `receita.retorno_ia{motivo}`;
- `pathfinder.espera_s`, `pathfinder.desfecho{resultado}`;
- `capacidade.reserva{resultado,motivo}`.

**C6. Recursos do worker** (`WorkerResources`, aditivo). Campos opcionais novos, em que `null` significa
desconhecido e nunca ilimitado:
- `mem_limit_mb`: limite efetivo de cgroup ou job, quando menor que o host;
- `mem_available_mb`;
- `cpu_effective`: quota ou cpuset;
- `swap_used_pct`;
- `mem_pressure`: PSI avg10 no Linux;
- `reserved_mb`: boots em andamento;
- `measured_at`.

**C7. Capacidades do worker** (aditivo).
- `Hello.features: list[str]`: o que o agente implementa e confere.
- `Welcome.accepted_features: list[str]`: o que o central vai usar.

O central só usa o que foi anunciado e aceito. Agente sem `features` segue o caminho anterior. Mensagem de tipo novo
só vai para o agente que aceitou a feature correspondente. A aceitação é a interseção de `Hello.features` com o
que o central sabe usar (`registry.FEATURES_DO_CENTRAL`, hoje `boot_reservations`), negociada POR CONEXÃO
(`WorkerLink.features_aceitas`); a porta de toda mensagem nova é `WorkerRegistry.aceitou(worker_id, feature)`.

`observe_local` (feature do agente com Pillow no venv): o central pede a imagem por `observe_image`
(`request_id`, `instance_id`, `serial`, `previa`, `cheia`, `lado_max`, `so_dimensoes`, `upload_token`,
`timeout_s`, `max_bytes`); o agente faz o screencap e a codificação na máquina dele e manda o corpo pelo canal de
mídia `/api/worker/midia` (WebSocket próprio, token de uso único, teto `MIDIA_MAX_BYTES` = 8 MiB, prazo do
pedido). Falha, ou as dimensões de `so_dimensoes`, voltam por `observe_result` no canal de comando. O
`DeviceManager` usa a captura na origem (`_capturar_na_origem`, dentro do executor do aparelho) na observação, na
prévia e na evidência de aparelho cujo canal vivo negociou a feature, e a imagem passa pelas mesmas guardas da local
(trecho sensível, geração, classificação no instante de publicar); `Observation.source` vira `worker_local`. Falha
na origem é falha de captura (`DriverError`), sem volta ao ADB no meio do pedido. Métricas: `captura.ms`,
`captura.bytes` e `codificacao.ms` ganham `via=worker` nesse caminho.

### v0.20: o que a implementação fixou

Detalhes decididos na implementação e na revisão F8, que o texto dos contratos C1 a C7 não dizia.

**Tipos no painel** (`frontend/src/api/types.ts`, marcados `// v0.20`):

```ts
type StreamStatus = 'live' | 'stale' | 'capture_error' | 'no_frame' | 'device_offline' | 'device_hibernated'
                  | 'worker_offline' | 'paused';
interface FrameInfo { /* …campos de antes… */ sensitive?: boolean }   // ausente = false
interface Settings  { /* …campos de antes… */ preview_mode?: 'on_demand' | 'always' }
type ClientMessage = { type: 'ping' } | { type: 'focus'; instance_id: string | null }
                   | { type: 'watch'; grid: string[]; focus: string | null; ttl_s: number };
```

**Prévia e tela:**

- **Precedência:** `paused` cede lugar a `capture_error` quando há falha de captura registrada. Uma prévia suspensa
  nunca esconde uma captura quebrada.
- **Frame marcador** (C4): o evento `frame` traz `FrameInfo.sensitive: true`, com o tamanho da tela e sem imagem. O
  `frame_id` dele vale para tecla e texto do controle manual. O painel mostra "Tela sensível — prévia oculta" e não
  busca `/frame`.
- **Transição `paused` ↔ ao vivo:** publica `instance.updated`, porque é o evento que leva o `StreamInfo` ao painel.
- **Conexão nova:** não acorda a captura do parque antes do primeiro `watch`. Conexão que já mandou `watch` e cujo
  TTL venceu fica sem interesse e nunca volta a ser tratada como painel antigo.
- **Aba oculta:** o painel manda `watch` vazio. A exceção é o aparelho cujo controle manual ele detém: esse continua
  em `focus`, e o foco do `watch` renova o lease manual.

**Observação** (C1):

- `observe(rt, *, timeout, imagem=True | False | f(tree), lado_max=None)`: com `lado_max`, a imagem já vem no
  tamanho do modelo, sem recodificar.
- `completar_imagem(rt, obs)` captura logo depois da árvore, no mesmo executor e sem ação no meio.
- `imagem_tardia(rt)` serve só para evidência e traz o horário na nota.

**Rótulos de métrica** (C5), vocabulário fechado:

| Métrica | Rótulo | Valores |
|---|---|---|
| `captura.*` | `origem` | `previa`, `observacao`, `evidencia` |
| `captura.total` | `resultado` | `ok`, `falha`, `sensivel`, `descartada` |
| `captura.evitada` | `motivo` | `sem_interesse`, `frame_recente`, `sensivel`, `politica` |
| `codificacao.ms` | `tipo` | `previa`, `cheia`, `modelo`, `evidencia` |
| `captura.ms`, `captura.bytes`, `codificacao.ms` | `via` | `worker` (captura na origem, `observe_local`); ausente = no central |
| `receita.retorno_ia` | `motivo` | vocabulário de `recipes.motivo_do_retorno` |
| `capacidade.reserva` | `resultado` | `concedida`, `recusada`, com `motivo` curto |

**Recursos e admissão** (C6):

- `reserved_mb` é a RAM que o agente reservou para boots em andamento, pelo custo da imagem no host.
- `null` quando o agente não anuncia `boot_reservations`; o central desconta 0.
- A admissão usa `min(mem_available_mb, ram_free_mb) − reserved_mb`, limitado por `mem_limit_mb` quando conhecido.
- Batida com mais de 30 s ou sem RAM medida recusa o boot com motivo escrito.

**Capacidades** (C7):

- O central preenche `Welcome.accepted_features` com o que o agente anuncia e o central sabe usar: hoje
  `boot_reservations` e, com a fase B da F4, `observe_local`.
- A porta de mensagem nova é `WorkerRegistry.aceitou(worker, feature)`.

**Cerca e reentrega:**

- A cerca é calculada dentro da transação, serializada por aparelho: `pg_advisory_xact_lock` no PostgreSQL,
  `BEGIN IMMEDIATE` no SQLite. Não existe restrição `UNIQUE`.
- O agente recusa despacho com cerca ≤ à maior já executada no aparelho, sem executar. Se o `command_id` está entre
  os últimos 64 confirmados do diário, devolve o mesmo corpo de antes. Senão, devolve
  `outcome: failed, data.refused: "fence_not_newer"`, que não pode ser lido como efeito novo.
- Um `result` tardio de comando antigo registra o desfecho do comando, mas não mexe no estado atual do aparelho.

**NATS**, atrás da bandeira:

- O comando vai para `comandos.<hosted_by>`, a réplica que segura o link do worker, e não para o `worker_id`.
- `ack_wait` é de 660 s, com `in_progress` enquanto o verbo roda.
- `Nats-Msg-Id` é o `command_id`.
- Não há prova contra um broker real.

**Desbravador e receitas:**

- `objectives.wait_reason` ganha o valor `pathfinder`, que o painel mostra como "aguardando outro aparelho
  aprender o caminho".
- `GET /api/flows/cobertura` ganha, por fluxo, o campo `aproveitamento` (`taskqueue/aproveitamento.py`).

## Adendo v0.21 (27/09/2026) — habilidades no caminho dos fluxos

Fase G da evolução arquitetural: a execução resolve o comando por skill publicada antes do fluxo
([execution](dominios/execution.md), [skills](dominios/skills.md)). Nada implantado; prova `simulated`.

**`GET /api/flows/match?command=`** (`api.py::flows_match`):

- Resolve pela mesma porta da execução (`AppState.skill_planner.for_command(command, None)`): skill publicada atrás de
  `skills.enabled`, depois fluxo ativo atrás de `ai.flows`.
- **Mudança visível: passa a respeitar `ai.flows`.** Com os fluxos desligados e sem skill que case, responde `null`,
  mesmo que um fluxo ativo case o comando. Antes, a rota chamava `FlowStore.match` sem conferir o interruptor e
  estimava um plano que a execução nunca usaria.
- Fluxo legado: a resposta é a de antes, `social/capacidades.py::cobertura_do_fluxo` da linha de `flows`:
  `{flow_id, package, target_version, steps_total, steps_with_recipe, ai_cost, estimated_usd}`.
- Skill: o mesmo formato, calculado sobre o plano compilado com os valores do comando, mais `skill_ref`.
  - `flow_id` traz a referência com versão (`"ig.abrir_conversa@1"`). **Não é** um `flows.id`.
  - `skill_ref` traz o mesmo valor e diz que a resposta é de skill. Não aparece na resposta de fluxo.
- Skill que casa e não compila: `null`.
- A prévia não tem aparelhos (`profile_ids=None`): o escopo da skill ou do fluxo não filtra a estimativa. Era assim
  também com `FlowStore.match(command)`.
- O painel (`frontend/src/api/client.ts`) tipa a resposta como `FlowCoverage | null` e ignora `skill_ref`.

**`PUT /api/flows/{id}`** (`api.py::update_flow`):

- `{status: "active"}` num fluxo adotado por uma skill que tem versão publicada responde **409**
  `{"detail": {"code": "flow_adopted", "message": …}}`, e o fluxo continua `disabled`.
  - Motivo: o mesmo comando ficaria vivo nos dois backends.
  - Voltar ao fluxo é desfazer a adoção (`SqlSkillRepository.release_flow`), que desabilita a versão na mesma
    transação.
  - A conferência é `SqlSkillRepository.published_adopter(flow_id)`.
- `{status: "disabled"}` continua aceito (200).
- Ordem das recusas: 404 `not_found`, 400 `invalid`, 409 `flow_adopted`.
- **`DELETE /api/flows/{id}`** (`api.py::delete_flow`): fluxo adotado por uma skill, em qualquer estado dela, responde
  **409** `flow_adopted` e não é apagado, porque ele é o caminho de volta da adoção (`release_flow` o religa). A
  conferência é `SqlSkillRepository.adopter_id(flow_id)`. Fluxo não adotado: 204, como antes.

**Execução resolvida por skill** (`POST /api/runs`, sem mudança no corpo nem na resposta):

- Evento `decision`: "Plano da habilidade `<skill>@<n>` “<nome>” (sem chamada ao planejador)", com os códigos dos
  avisos de compilação entre colchetes. O fluxo legado mantém o texto de antes.
- Skill que casa e não compila: a execução vai a `needs_input`, com `status_detail` "A habilidade `<ref>` não compilou
  para este comando: …", e um evento `log` de nível `warn` com `data: {skill, issues}`. Cada item de `issues` é
  `{code, message, path, severity}` (`CompileIssue.as_dict()`).

**Provas (`simulated`):** `backend/tests/test_fatia_abrir_conversa.py::test_rotas_de_fluxo_respeitam_a_skill`,
`::test_filha_desabilitada_poe_a_composta_em_needs_input_sem_plano`;
`backend/tests/test_perfil_bloqueado_e_capacidades.py::test_estimativa_de_custo_por_fluxo`.

## Adendo v0.22 (27/09/2026) — resolução de intenção: `POST /api/skills/resolve` e a pergunta em `needs_input`

Fase I da evolução arquitetural: a RESOLVE é uma cadeia com parâmetros tipados, e o que ela não decide vira pergunta
([skills](dominios/skills.md#resolução-de-intenção), [execution](dominios/execution.md#a-pergunta-da-resolve-needs_input)).
Nada implantado; prova `simulated`.

**`POST /api/skills/resolve`** (`api.py::skills_resolve`), rota nova. É a prévia da RESOLVE:

- **Sem efeito:** não cria execução, não grava nada, não chama o planejador nem IA. É a mesma cadeia do `_plan`
  (`AppState.skill_planner.resolve_intent`), com as etapas por IA no provedor nulo: em `stages`, `not_run` quando teriam o que fazer, `skipped` quando não.
- **Corpo** (`contracts/skills/resolve.py::SkillResolveRequest`, `extra="forbid"`):

  ```json
  {"command": "abra a conversa com @Ana no instagram", "instance_ids": ["android-01"], "profile_ids": []}
  ```

  - `command`: 1 a 4000 caracteres (os limites de `RunCreate`);
  - `instance_ids` e `profile_ids`: até 64 cada, opcionais. Com algum deles, o escopo da skill é conferido como no
    planejamento: o perfil vinculado a cada aparelho (`social.profile_of`) mais os perfis dados. Aparelho sem perfil
    conta como "sem perfil", e uma skill com escopo não casa. Sem nenhum dos dois, é a prévia sem escopo: qualquer
    skill serve.
- **200.** `IntentResolution.as_dict()` mais `gated_by_config`:

  ```json
  {"status": "resolved",
   "intent": {"skill_ref": "ig.abrir_conversa@1", "skill_id": "ig.abrir_conversa", "version": 1,
              "name": "Abrir conversa no Instagram", "backend": "skill", "method": "template",
              "parameters": [{"name": "username", "type": "handle", "value": "@ana", "origin": "command",
                              "raw": "@Ana"}]},
   "questions": [], "subject": null, "candidates": [],
   "stages": [{"stage": "template", "outcome": "matched", "detail": "ig.abrir_conversa@1"},
              {"stage": "typed", "outcome": "validated", "detail": "ig.abrir_conversa@1"},
              {"stage": "semantic", "outcome": "skipped", "detail": ""},
              {"stage": "llm", "outcome": "skipped", "detail": ""}],
   "gated_by_config": {"skills.enabled": true, "ai.flows": true}}
  ```

  - `status`: `resolved`, `needs_input` ou `no_match`.
  - `intent` (só em `resolved`): `backend` é `skill` ou `legacy_flow`; `method` é `template`, `typed`, `semantic` ou
    `llm` (hoje, só os dois primeiros acontecem). Em `parameters`, `type` é `null` no conteúdo legado; `origin` é
    `command` ou `default`; `raw` é `null` quando o valor é o padrão.
  - `questions` (em `needs_input`): cada uma é `{field, question, reason, expected, options, received, skill}`.
    `reason` é `missing_parameter`, `invalid_parameter` ou `ambiguous_intent`. No empate, `field` é `"skill"` e
    `options` traz as referências. Exemplo, com "abra a conversa com a Ana no instagram":

    ```json
    {"field": "username",
     "question": "“a Ana” não serve para 'username' de “Abrir conversa no Instagram”: nome de usuário tem só letras, números, ponto e sublinhado, sem espaço. Informe um nome de usuário (@nome) ou o link do perfil (ex.: @ana.teste).",
     "reason": "invalid_parameter", "expected": "um nome de usuário (@nome) ou o link do perfil", "options": [],
     "received": "a Ana", "skill": "ig.abrir_conversa@1"}
    ```

  - `subject`: a referência da única skill que precisa de resposta, ou `null`.
  - `candidates`: no empate, `[{skill_ref, name, backend}]`; senão, `[]`.
  - `stages`: uma linha por etapa (`template`, `typed`, `semantic`, `llm`), com `outcome` em `matched`, `partial`,
    `ambiguous`, `no_match`, `validated`, `invalid`, `tie_broken`, `skipped` ou `not_run`.
  - `gated_by_config`: `skills.enabled` e `ai.flows`, lidos **nesta** chamada. Vem sempre, porque a mesma frase
    resolve diferente com eles mudados. `ai.flows` não fecha a rota; só decide se um fluxo legado pode aparecer.
- **Erros:**
  - **404** `skills_disabled` com `skills.enabled` desligado, o mesmo status das rotas do ensino v2. O corpo traz os
    interruptores dentro de `detail`:
    `{"detail": {"code": "skills_disabled", "message": …, "gated_by_config": {"skills.enabled": false, "ai.flows": …}}}`;
  - **404** `not_found`: um `instance_ids` que não existe;
  - **422**: campo fora do contrato ou `command` vazio (validação do FastAPI);
  - **409** `content_tampered`: uma skill publicada adulterada entre as candidatas (`ContentTampered`), com os
    interruptores em `detail`.
- O painel ainda não chama a rota (fase F).

**Mudança na execução** (`POST /api/runs`, sem mudança no corpo nem na resposta; só com `skills.enabled` ligado):

- Quando a RESOLVE devolve pergunta (parâmetro vazio, valor que não serve ao tipo, empate entre skills), a execução
  vai a `needs_input`:
  - `status_detail`: o texto das perguntas unido por `" | "`, o formato das perguntas do planejador;
  - evento `log` de nível `warn` ("… precisa de resposta antes de planejar") com
    `data: {skill, questions, candidates}`. `skill` é a referência ou `null` no empate; `questions` tem a forma acima;
    `candidates` são as referências empatadas, ou `[]`;
  - `runs.plan` nulo, nenhum objetivo, planejador não chamado. No empate, `runs.skill_id`, `skill_version` e
    `skill_hash` também ficam nulos.
- Antes (v0.21), o empate era decidido pelo menor `skill_id`, o valor ia cru ao compilador, e um comando com `{nome}`
  vazio não casava e ia ao fluxo ou ao planejador.
- `handle` chega ao plano como `@nome` em minúsculas; `integer`, `boolean` e `enum` chegam normalizados
  ([DSL](skill-dsl.md#tipos-extração-e-normalização-fase-i)). `@ana` sai igual ao de antes.
- `GET /api/flows/match` responde `null` quando a resolução é pergunta, como para skill que não compila.
- Com `skills.enabled` desligado, nada muda: o fluxo legado responde como em v0.21.

**Provas (`simulated`):** `backend/tests/test_intencao_chamadores.py::test_os_tres_chamadores_coerentes_para_a_mesma_frase`,
`::test_empate_na_execucao_pergunta_com_as_opcoes_e_nao_grava_skill`,
`::test_rota_resolve_atras_de_skills_enabled_e_sempre_com_os_interruptores`,
`::test_rota_resolve_confere_o_escopo_como_o_planejamento`; `backend/tests/test_intencao_resolucao.py::test_paridade_com_o_flowstore_match`.

## Adendo v0.23 (27/09/2026) — ensino v2 e habilidades no HTTP

Fase F da evolução arquitetural: as rotas de habilidades versionadas e do ensino v2 ([ensino](teaching.md),
[skills](dominios/skills.md)). Todas são aditivas. Nada implantado; prova `simulated`.

**Onde e atrás de quê** (`backend/app/modules/skills/presentation/router.py`):

- **Interruptor.** Com `skills.enabled` desligado (o padrão), **toda** rota deste adendo responde **404**
  `{"detail": {"code": "skills_disabled", "message": …}}`.
  - O valor é lido a cada requisição (`router.py::_exige_habilidades`).
  - É um 404 explícito: quem chama sabe que a rota existe e está desligada.
  - Desde `578fe36`, `POST /api/skills/resolve` (v0.22) usa o mesmo status.
- **503** `not_ready`: o serviço do ensino ou o repositório de habilidades ainda não foi composto.
- **Nada muda no legado.** `/api/training*` e `/api/flows` não passam por aqui.
- **Ordem de registro.** O roteador entra em `main.py::create_app` depois do router principal, para que `POST
  /api/skills/resolve` (em `api.py`) case antes de `/api/skills/{skill_id}`.
- **Corpos** com `extra="forbid"`: campo fora do contrato, ou fora dos limites, é **422** (validação do FastAPI).
- **Quem decide** (`operator`, `reviewed_by`, `state_by`, `decided_by`): o operador da sessão do painel, ou `panel`
  (`router.py::_quem`). Nunca o ator de sistema.

**`Health`** (tipos, na forma do adendo v0.2):

```ts
interface Health   { /* + */ features: { hibernation: boolean; recipes: string; flows: boolean; image_policy: string;
                                          system_image: string;
                                          skills?: boolean } }   // v0.23: skills.enabled; ausente = desligado
```

- `features.skills` é `skills.enabled`, lido a cada `GET /api/health` (`state.py`, `Health.features`).
- Opcional no painel: backend anterior à fase F não manda o campo, e o painel o trata como desligado.
- O painel só chama as rotas abaixo com ele `true` ([produto](produto.md#3-fluxos-do-usuário)).

**`/api/skills`** (sobre `SqlSkillRepository`):

| Rota | Corpo | Resposta |
|---|---|---|
| `GET /api/skills?app_id=&state=` | — | `SkillSummary[]`: `{ref, skill_id, version, name, app_id, state, command_template, schema_version, content_hash, intact, state_at}` |
| `GET /api/skills/{skill_id}` | — | `{id, name, description, app_id, legacy_flow_id, created_by, created_at, updated_at, scope: {profile_ids, group_ids}, versions: SkillSummary[]}` |
| `GET /api/skills/{skill_id}/versions/{version}` | — | a versão: `ref`, `state`, `schema_version`, `content` (o documento), `content_hash`, `command_template`, `app_ids`, `parent_version`, `source_kind`, `source_ref`, `provenance`, `created_by`, `created_at`, `state_at`, `state_by`, `state_detail` e `history: [{from, to, reason, by, at}]` |
| `POST /api/skills/{skill_id}/versions/{version}/status` | `{to: SkillState, reason?: string (≤500), manual?: boolean}` | a versão, como acima. A transição segue a tabela do ciclo de vida ([skills](dominios/skills.md#estados-e-transições)) |
| `PUT /api/skills/{skill_id}/scope` | `{profile_ids?: string[] (≤500), group_ids?: string[] (≤100)}` | `{skill_id, profile_ids, group_ids}`. Escopo muda sem versão nova |
| `POST /api/skills/{skill_id}/rollback` | `{version: int ≥ 1, reason?: string}` | a versão. É a transição de uma versão `deprecated` para `published`; a publicada atual é depreciada na mesma transação |

- `GET /api/skills` lista **só o SQL**. As versões `flow:<id>@1` do adaptador legado não aparecem.
- `SkillState`: `draft`, `candidate`, `validated`, `published`, `deprecated` e `disabled`.

**`/api/teaching-sessions`** (sobre `TeachingService`). Toda resposta de escrita e o `GET` por id devolvem a **visão
do ensino**:

- os campos da sessão: `id`, `instruction`, `skill_id`, `base_version`, `app_id`, `profile_id`, `status`,
  `validation_status`, `result_version_id`, `operator`, `created_at`, `updated_at` e `closed_at`;
- `source` (derivada): `instruction`, `demonstration`, `hybrid`, `correction` ou `successful_execution`;
- `demonstrations: [{id, seq, kind, training_session_id, run_id, instance_id, app_snapshot, note, created_at}]`;
- `turns: [{id, kind, author, reply_to, target, body, payload, candidate_id, created_by, created_at}]`;
- `candidates` e `current_candidate`: `{id, teaching_id, seq, status, validation_status, generated_by, content_hash,
  version_id, document, annotations, created_at, updated_at}`;
- `open_questions: [{id, kind, key, origin, text, target, candidate_id}]`;
- `errors: string[]`: os erros de compilação da candidata atual, lidos agora.

| Rota | Corpo | Resposta |
|---|---|---|
| `POST /api/teaching-sessions` | `{instruction?: string (≤2000), skill_id?, base_version?: int ≥ 1, app_id?, profile_id?}` | **201**, a visão (`status: "open"`) |
| `GET /api/teaching-sessions?status=&limit=&training_session_id=` | — | resumos: os campos da sessão, mais `source`, `demonstration_count`, `candidate_count`, `open_question_count` e `current_candidate_id`. `limit` de 1 a 200 (padrão 50). Com `training_session_id`, o ensino que usa aquela gravação: zero ou um |
| `GET /api/teaching-sessions/{id}` | — | a visão |
| `POST /api/teaching-sessions/{id}/demonstrations` | exatamente um de `{training_session_id}` (gravação v1) ou `{run_id}` (execução `completed`), mais `note?` (≤500) | a visão. Os dois, ou nenhum: 400 `invalid_input` |
| `POST /api/teaching-sessions/{id}/corrections` | `{body (1–2000), run_id, step_id, payload?: {string: string}}` | a visão |
| `POST /api/teaching-sessions/{id}/candidates` | `{idempotency_key?: string (8–120)}` ou sem corpo | a visão, com a candidata nova. **Com IA real, uma chamada paga do planejador**; no simulado, nenhuma. A mesma chave não chama de novo |
| `POST /api/teaching-sessions/{id}/answers` | `{question_id: int ≥ 1, body (1–2000)}` | a visão |
| `POST /api/teaching-sessions/{id}/discard` | — | a visão (`status: "discarded"`) |

**`/api/skill-candidates/{id}`:**

| Rota | Corpo | Resposta |
|---|---|---|
| `GET /api/skill-candidates/{id}` | — | a candidata, mais `teaching_status`, `compile` e `open_questions` |
| `POST /api/skill-candidates/{id}/compile` | — | `{skill_id, name, app_id, command_template, app_ids, errors, ok}`. Compila agora e não muda nada |
| `POST /api/skill-candidates/{id}/validate` | `{mode?: "static", idempotency_key?}` ou sem corpo | a visão: `ready` se compilou, `open` com a candidata `rejected` se não. `mode` diferente de `static` é 422 |
| `POST /api/skill-candidates/{id}/publish` | `{idempotency_key?}` ou sem corpo | a visão (`status: "published"`, `result_version_id: "<skill>@<n>"`). A versão nasce **`draft`**: publicar a habilidade é `…/versions/{n}/status` |

- Não há `PATCH` da candidata, e `validate` não tem o modo `device`.
- `idempotency_key` é aceita em `validate` e `publish`, mas não é usada: as duas são idempotentes pelo estado.
  Publicar uma candidata já aceita devolve a mesma versão.

**Recusas.** O domínio levanta `SkillError` com `code` estável; `router.py::_http` escolhe o status:

| Status | `code` |
|---|---|
| 404 | `not_found` (`SkillNotFound`, `TeachingNotFound`) |
| 400 | `invalid_ref` (`InvalidSkillRef`); `credential_in_text`; `secret_in_parameters`; `invalid_input` e os subcódigos `unknown_app`, `recording_discarded`, `run_not_completed`, `correction_needs_skill`, `step_not_correctable`, `nothing_to_teach`, `app_required`; `validation_mode` só pelo serviço (pela HTTP, `mode` fora de `static` é 422) |
| 422 | `invalid_document`, com `errors` em `detail` |
| 502 | `generalizer_error`: a IA não devolveu proposta utilizável; a sessão volta a `open` |
| 409 | todo o resto. Do ensino: `teaching_state`, `still_recording`, `questions_pending`, `recording_taken`. Das versões: `transition_forbidden`, `frozen_version`, `content_tampered`, `E_DUPLICATE_COMMAND`, `state_conflict`, `validation_pending` (com `pending` em `detail`) |

- `credential_in_text` nunca ecoa o texto recusado.
- A candidata com valor em formato de credencial **não** é erro HTTP: o pedido responde 200, a candidata fica
  `rejected`, gravada com `**REDACTED**`, e a visão traz o turno `note` `secret_in_candidate`.

**Evento novo** (na forma da tabela do adendo v0.11):

| Evento | Persistido | Onde é emitido |
| --- | --- | --- |
| `teaching.updated` | sim | `state.py`, pelo `on_updated` do `TeachingService` — toda mudança de um ensino, com `data: {teaching_id}` |

- Não há `skill.published`.
- O painel ainda não escuta `teaching.updated`, e continua sem chamar `POST /api/skills/resolve`.

**Provas (`simulated`):** `backend/tests/test_ensino_v2.py::test_rotas_desligadas_respondem_404_explicito_e_o_treino_segue`,
`::test_rotas_ligadas_do_ensino_ao_rascunho_e_ciclo_da_versao`; no painel,
`frontend/src/features/training/TrainingReview.test.tsx` e `frontend/src/features/settings/FlowsRecipesSection.test.tsx`
(desligado e ligado). IA real, PostgreSQL e conferência visual: `not_run`.

## Adendo v0.24 (27/09/2026) — `plan_report` em `mode=plan` e corpos fora de `models.py`

Fases H (parte 2) e K2 da evolução arquitetural
([execution](dominios/execution.md#modeplan-o-planreport-servido-fase-h-parte-2)). Tudo aditivo. Nada implantado;
prova `simulated`.

**`POST /api/runs` com `mode: "plan"`** (`api.py::create_run`):

- Corpo: o mesmo `RunCreate`.
- Resposta: o `RunSummary` de sempre, campo a campo, **mais** `plan_report`, o relatório dos recursos que a skill
  declara (`spec.resources`), lido sem aplicar nada. Com `mode: "execute"`, a resposta não muda e não traz
  `plan_report`.
- A execução nasce `planning`, e o relatório é montado na hora (`RunService.relatorio_de_recursos`), resolvendo a skill
  de novo: `_plan` ainda roda em segundo plano. Nenhum comando, entrega ou chamada de IA é feito para montá-lo.
- Com a mesma `idempotency_key`, o resumo é o da execução que já existe (`deduplicated: true`), e o relatório é lido
  de novo, sobre o mundo de agora.
- A rota não tem `response_model`: o OpenAPI não declara `plan_report`.

Tipos (na forma do adendo v0.2):

```ts
// resposta de POST /api/runs com mode='plan'
type RunCreatedWithPlan = RunSummary & { plan_report: PlanReport | PlanReportError };

interface PlanReport {
  source: 'skill' | 'legacy_flow' | 'needs_input' | 'planner';
  skill: { id: string; version: number; content_hash: string | null } | null;   // null quando nada casou
  targets: { instance_id: string; profile_id: string | null }[];               // o perfil vinculado agora
  resources: ResourceLine[];                                                    // vazio fora de 'skill'
  risks: string[];
  summary: { in_sync: number; diverged: number; pending: number; held: number; unknown: number; blocked: number;
             unsupported: number; actions: number; human_interventions: number; blockers: number;
             ready_to_run: boolean };
  content_hash: string;                         // sha256 (64 hex) do JSON canônico do relatório, sem 'source'
}
interface PlanReportError { source: 'error'; detail: string }

interface ResourceLine {
  kind: 'device.state' | 'app.installation' | 'account.binding' | 'app.session';
  target: string | null;                        // o app em app.*; null em device.state
  instance_id: string;
  profile_id: string | null;
  desired: string;                              // "online", "release=promoted", "account=bound_profile, session=ready"
  on_missing: 'wait' | 'apply' | 'ask';
  status: 'in_sync' | 'diverged' | 'pending' | 'held' | 'unknown' | 'blocked' | 'unsupported';
  code: string;                                 // ex.: 'ready', 'no_profile', 'not_read'
  detail: string;
  observed: [string, string][] | null;          // null = não lido; nunca segredo
  actions: { purpose: 'observe' | 'converge' | 'ask'; verb: string | null; reason: string }[];  // verb null só em 'ask'
}
```

- `source`:
  - `skill`: os recursos declarados pela skill publicada, com os das filhas compostas;
  - `legacy_flow`: fluxo legado, que não declara recurso;
  - `needs_input`: a skill casou e precisa de resposta, ou não compilou;
  - `planner`: nada casou, e o planejador escreve as etapas;
  - `error`: montar o relatório falhou. A execução **já foi criada** e volta com status 200; `detail` traz o motivo, e
    o erro vai para o log. Nunca um 500.
- **Atenção:** fora de `skill`, `resources` vem vazio e `summary.ready_to_run` vem `true`, porque não há linha a
  comparar. Quer dizer "nada foi declarado", não "está tudo pronto".
- Ordem das linhas: por aparelho e, dentro dele, aparelho → app → vínculo → sessão. A mesma entrada dá o mesmo
  `content_hash`.
- Par (recurso, aparelho) que não foi lido: `status: "unknown"`, `code: "not_read"`, `observed: null`, sem ação, e
  listado em `risks`.
- `actions` diz o que **seria** feito; nada é aplicado. `ready_to_run` é `true` só com todas as linhas em `in_sync` ou
  `held`.
- O painel (`frontend/src/api/client.ts`) ainda tipa a resposta como `RunSummary` e ignora `plan_report`.

**Efeito no banco, nos dois modos:** `objectives.resource_plan` (045) passa a ser gravado no `materialize` quando a skill
declara recursos: `{skill: {id, version, content_hash}, instance_id, profile_id, resources, resolved_at}`. Nulo no
fluxo legado e no planejador. Nenhuma rota expõe a coluna ainda.

**Corpos de requisição fora de `models.py` (K2), sem mudança de contrato.** 29 dos 41 corpos de requisição moram agora
em `backend/app/modules/{fleet,identity,execution,applications}/presentation/schemas.py`. `app.models` reexporta os
**mesmos** objetos, e `api.py` não mudou. O OpenAPI de `create_app()` e o esquema JSON de cada classe ficaram
idênticos (conferência por script relatada na mensagem de `e7af6f0`; o script não está no Git). A frase do topo deste
documento ("os tipos em `backend/app/models.py`") continua valendo como ponto de import.

**Provas (`simulated`):** `backend/tests/test_plan_report_na_execucao.py::test_mode_plan_serve_o_plan_report_sem_criar_comando`,
`::test_mode_plan_pelo_planejador_diz_que_nada_foi_declarado`, `::test_mode_execute_nao_traz_relatorio_mas_grava_a_foto`,
`::test_relatorio_que_falha_nao_derruba_a_execucao_criada`; `backend/tests/test_models_fatiado.py` (67 testes). PostgreSQL
e produção: `not_run`.

## Adendo v0.25 (27/09/2026) — conversão de fluxo em habilidade e provedor de sessão por app

Fases J e K1 da evolução arquitetural ([skills](dominios/skills.md#conversão-de-fluxo-fase-j),
[perfis](dominios/perfis-e-instagram.md#sessionprovider-e-o-registro-por-pacote-fase-k1)). Rotas novas aditivas e dois
códigos 409 novos. Nada implantado; prova `simulated`.

**Conversão de fluxo** (fase J; `backend/app/modules/skills/presentation/router.py`). As três rotas estão no roteador
de habilidades e seguem o adendo v0.23: 404 `skills_disabled` com `skills.enabled` desligado, 503 `not_ready`, corpos
com `extra="forbid"`, quem decide = operador da sessão ou `panel`, recusas do domínio por `router.py::_http`.

| Rota | Corpo | Resposta |
|---|---|---|
| `POST /api/flows/{flow_id}/adopt` | `{skill_id?: string (≤64), reason?: string (≤500)}` ou sem corpo | **201** `{flow_id, skill_id, published, draft, warnings}`. `published` é a v1 (o plano do fluxo, `schema_version` 0) e `draft` a v2 (o documento descompilado), as duas na forma da versão do adendo v0.23 (com `history`); o fluxo fica `disabled`. Uma transação |
| `POST /api/flows/{flow_id}/release` | `{reason?: string (≤500)}` ou sem corpo | `{flow_id, skill_id, flow_status: "active", discarded_drafts: string[], versions: SkillSummary[]}`: a publicada desabilitada, o fluxo religado como era e os rascunhos da conversão apagados. Uma transação |
| `POST /api/skills/{skill_id}/versions/{version}/decompile` | — | **201** `{draft, warnings}`: o rascunho v2 a partir de uma versão de conteúdo legado (quem adotou um fluxo antes da fase J) |

```ts
interface DecompileIssue {                       // warnings[] de adopt e decompile
  code: string;                                  // 'W_ROUNDTRIP' ou um aviso do compilador
  message: string;
  path: string;                                  // '/steps/1/title' no plano (origin 'plan') ou no documento
  severity: 'error' | 'warning';
  origin: 'plan' | 'document';
}
interface SkillSummary { /* v0.23 + */ legacy_flow_id: string | null }   // GET /api/skills e versions[] do release
```

- Sem `skill_id`, a conversão usa a habilidade que já adotou o fluxo (readoção) ou o id sugerido `<app>.<fluxo>`.
- **422** `invalid_document` com `errors: string[]` (`"E_CODIGO /caminho: mensagem"`): a ida e volta pelo compilador
  não reproduz o plano do fluxo (`E_ROUNDTRIP`, `E_RUNTIME_VARIABLE`, `E_UNREPRESENTABLE` ou erro do compilador).
  Nada é alterado. Em `adopt` os erros vêm como texto; os avisos, como objeto.
- Outras recusas, com os códigos do adendo v0.23: 404 `not_found` (fluxo inexistente; `release` de fluxo que não foi
  convertido); 400 `invalid_ref` (`skill_id` fora do formato); 409 `state_conflict` (fluxo desligado ao adotar) e
  `E_DUPLICATE_COMMAND` (comando já publicado noutra habilidade, ao adotar ou ao religar pelo `release`).
- `GET /api/skills` e `GET /api/skills/{id}` já traziam `legacy_flow_id` no detalhe; agora a lista também traz.

**Um comando, um dono** (fase J), mudança em rotas antigas:

| Rota | Mudança |
|---|---|
| `PUT /api/flows/{flow_id}` com `status: "active"` | **409** `command_published` (novo) quando **qualquer** habilidade publicada tem o mesmo comando, além do 409 `flow_adopted` da fase G. Conferência e escrita numa transação |
| `POST /api/training/{session_id}/save` | o 409 `duplicate_command` de sempre passa a valer também quando uma habilidade publicada tem o comando (`FlowStore.learn_from_plan`) |
| `DELETE /api/flows/{flow_id}` | sem mudança: 409 `flow_adopted` enquanto houver adotante, **inclusive depois de desfazer** (a definição fica com `legacy_flow_id`) |

**Trilha da v1 adotada** (fase J): a execução da v1 que adotou um fluxo grava `runs.flow_id` além de
`runs.skill_id`/`skill_version`/`skill_hash`, e conta em `flows.used` (`uses` de `GET /api/flows`). `RunSummary` não
expõe essas colunas; elas aparecem pela contagem do fluxo e pelas capacidades do perfil (abaixo).

**`GET /api/instagram/profiles/{id}/capacidades`** ganha `skills` (aditivo): o que o perfil concluiu por habilidade.

```ts
interface ProfileSkillCapacity {
  skill_id: string; version: number | null; name: string; legacy_flow_id: string | null;
  package: string | null; target_version: string | null;       // a cobertura de receitas, como a dos fluxos
  steps_total: number; steps_with_recipe: number; ai_cost: 'zero' | 'parcial' | 'total'; estimated_usd: number | null;
  times: number; last_at: string;
}
```

- A v1 adotada aparece nas duas listas (`flows` e `skills`); `legacy_flow_id` diz que é a mesma coisa.
- O painel ainda não mostra `skills` na tela do perfil.

**Provedor de sessão por app** (fase K1; `api.py::_start_session_job`):

- `POST /api/instagram/profiles/{id}/connect` e `/verify` pedem o provedor ao registro
  (`AppState.provedor_do_perfil`), em vez de chamar o autenticador do Instagram pelo nome.
- **409** `no_session_provider` (novo): nenhum app registrado provê a sessão do perfil. Vem depois das recusas de
  sempre (credencial, portão, aparelho, internet). **Inalcançável hoje**: o Instagram é embutido e sempre tem
  provedor.
- Nenhuma outra resposta mudou: `automated_login` de `/api/apps-overview` e das contas do perfil passa a vir de
  "o app tem provedor de sessão no registro" (antes, `session_provider == "instagram"`), com o mesmo valor em
  produção.

**Provas (`simulated`):** `backend/tests/test_conversao_de_fluxo.py::test_rotas_de_conversao_atras_do_interruptor_e_com_o_tratamento_de_erro`,
`::test_religar_o_fluxo_pela_rota_com_outra_publicada_e_recusado`, `::test_aprender_fluxo_de_comando_publicado_nao_cria_fluxo`;
`backend/tests/test_equivalencia_fluxo_skill.py::test_a_v1_adotada_grava_a_skill_e_o_fluxo_e_as_capacidades_enxergam`;
`backend/tests/test_app_novo_pelo_manifesto.py::test_app_novo_entra_so_pelo_registro_com_provedor_e_catalogo`; no painel,
`frontend/src/features/settings/FlowsRecipesSection.test.tsx`. O 409 `no_session_provider` não tem teste de rota
(inalcançável). PostgreSQL, conta real e conferência visual: `not_run`.

## Adendo v0.26 (27/09/2026) — provisionar e aposentar instância pela plataforma

**`POST /api/instances`** (`api.py`, `DeviceManager.provisionar`; corpo `InstanceProvisionBody` em
`modules/fleet/presentation/schemas.py`, `extra="forbid"`):

- Corpo: `worker_id?` (nulo = o hospedeiro), `app_id?`, `system_image?` (formato do SDK), `ram_mb?` (1024–32768),
  `create: true`, `start: false`, `idempotency_key?`.
- **202** `{instance, instance_id, command_id, command_state, deduplicated, start: "not_requested" | "after_create"}`
  quando abre o comando `create`; **201** com `create: false` (só a linha).
- **409**: `provisionamento_remoto_indisponivel`, `teto_de_aparelhos` (+`devices`, `max_devices`),
  `disco_insuficiente` (+`disk_free_gb`, `min_free_disk_gb`), `disco_desconhecido`, `chave_ja_usada`,
  `servidor_nao_hospeda`, `conflito_de_provisionamento`, e as recusas do pré-voo do `create` (`device_busy`,
  `rejected`, com `command_id`).
- **400**: `unknown_app`, `idempotency_key_sem_comando`, `start_sem_create`, `sobreposicao_invalida`. **404**: worker
  inexistente.

**`DELETE /api/instances/{id}`** → **200** `{instance_id, retired_at, avd_removed}`; **409**: `aparelho_de_worker`,
`instancia_da_configuracao`, `objetivo_aberto`, `vinculo_ativo`, `comando_em_voo`, `trabalho_em_curso`,
`aparelho_ligado`, `avd_nao_apagado`.

**`GET/PUT /api/servers/limits`**: `max_devices` entra nos valores por servidor (`ServerLimitValues`); nulo = sem
teto. Não vai na mensagem `Limits` ao agente.


## Adendo v0.27 (27/09/2026) — a persona é a pessoa, geração por IA e imagens

Evolução 2, onda A ([persona](dominios/persona.md); ADR-041 e ADR-042). Código em `api.py`, `models.py`,
`social/service.py`, `modules/identity/presentation/schemas.py`; integrado em `8c19d5a`.

**`PersonaDTO` = a pessoa inteira** (`models.py`). `InstagramProfileDTO` é o **mesmo objeto** pelo nome antigo. Campos
novos: `visual` (`PersonaVisual`), `biography` (`PersonaBiography`, com `schema_version`), `generation`
(`PersonaGeneration`: `source` `manual|ai|legacy_persona`, `provider`, `model`, `at`, `enriched_at`), `gender`,
`locale`, `age` (calculado de `birth_date`; senão `biography.approx_age`; nunca gravado), `voice_gaps` (computado),
`images` (`PersonaImageDTO[]`), `primary_image_id`, `accounts_count`. `username` é `null` quando a pessoa ainda não
tem conta (a coluna guarda `''`). `persona_id` e `profile_id` são o próprio `id`; `persona_name` é alias de `name`.
`traits` é só a voz: as chaves `appearance`/`visual_style`/`photo_scenario` agora vêm em `visual`; um cliente que
ainda as manda dentro de `traits` (`PersonaTraitsEdit`) tem o valor movido para `visual` no servidor, sem 422.

**`/api/personas` é a rota canônica.**

- `GET /personas` → todas as pessoas (com ou sem conta), ordenadas por nome.
- `POST /personas` (`PersonaCreate`, `extra="forbid"`: `name`, `summary?`, `persona_prompt?`, `traits?`,
  `first_name?`, `last_name?`, `birth_date?`, `gender?`, `locale?`, `biography?`, `visual?`, `generation?`) → **201**
  `PersonaDTO`. Com `ai.image.on_create` e gerador configurado, agenda `ai.image.per_persona` imagens em segundo
  plano; elas chegam pelo evento `persona.image.updated`.
- `GET /personas/{id}` → `PersonaDTO`; **404** `not_found`. O id legado da tabela `personas` ainda resolve.
- `PATCH /personas/{id}` (`PersonaPatch`) → `PersonaDTO`. **Por seção**: `traits`, `visual` e `biography` são
  mesclados no servidor (`mesclar_secao`: campo ausente fica, `null` explícito apaga, lista substitui inteira).
  `null` em `name`/`persona_prompt` não mexe.
- `DELETE /personas/{id}` → **204**. Apaga a **pessoa** (contas, credencial e ciphertext, sessão, vínculo, memória,
  histórico, imagens). **409** `persona_in_use` com vínculo a aparelho ou execução em curso.
- `POST /personas/generate` (`PersonaGenerateBody`: `prompt` 3–2000, `locale?`, `constraints?` até 20 pares) →
  **200** `PersonaCreate` (rascunho **não gravado**, com `generation.source = "ai"`), para revisar e mandar em
  `POST /personas`. Chamada paga pelo papel `social` (teto do dia). **422** `persona_draft_invalid` (menor de 18,
  nome que não é nome, voz ou biografia incompletas, texto com formato de segredo); **503** `ai_budget` |
  `ai_refusal` | `ai_error` | `ai_unavailable`.
- `POST /personas/{id}/enrich` → **200** `PersonaDTO`. Completa **só o que está vazio**; sem lacuna, devolve a
  persona sem chamar o modelo. Mesmos erros do `generate`.
- `POST /personas/{id}/preview` inalterado.

**Imagens da persona** (`PersonaImageDTO`: `id`, `persona_id`, `status` `pending|ready|failed|refused`, `source`
`generated|upload|imported_legacy`, `is_primary`, `width`, `height`, `provider`, `model`, `seed`, `aspect`,
`cost_usd`, `error`, `created_at`, `url`):

- `GET /personas/{id}/images` → `PersonaImageDTO[]`.
- `POST /personas/{id}/images`, dois comportamentos pelo `Content-Type`:
  - JSON `{count}` (1–3, `PersonaImagesBody`) → **202** `{accepted, persona_id, count, provider, simulated}`: gera em
    segundo plano; cada imagem chega por `persona.image.updated`. **409** `image_not_configured` (provedor pago sem
    `OPENAI_API_KEY`), `persona_minor`, `ai_budget` (teto do dia, conferido **antes** de aceitar); **422**
    `invalid_body`;
  - corpo cru `image/jpeg` ou `image/png` → **201** `PersonaImageDTO` (upload; principal se for a primeira). **413**
    `image_too_large` (> 10 MB); **400** `invalid_image`.
- `GET /personas/{id}/images/{img}` → os bytes, pelo storage. **404** `not_found`; **409** `image_not_ready` (com o
  `status` e o erro na mensagem).
- `PUT /personas/{id}/images/{img}/primary` → `PersonaDTO`. **404**; **409** `image_not_ready`.
- `DELETE /personas/{id}/images/{img}` → **204**; **404**.
- Evento `persona.image.updated` (`data`: `profile_id`, `image_id`, `status` `ready|refused|failed|primary|deleted`).

**`/api/instagram/profiles*`** continua para a conta, a credencial e a sessão; não são apelidos puros:

- `GET /instagram/profiles` → só quem **tem conta** de cadastro (`username <> ''`).
- `POST /instagram/profiles` (`ProfileCreate`, exige `username`): com `persona_id` de uma pessoa **sem conta**, é
  ela que ganha a conta (mesma linha, mesmo `id`). **409** `persona_in_use` se o id for de outra pessoa com conta;
  **400** `unknown_persona`.
- `PATCH /instagram/profiles/{id}` com `persona_id` de uma pessoa sem conta **absorve** a voz, a biografia e o
  visual dela neste perfil e apaga a linha sem conta; **409** `persona_in_use` / **400** `unknown_persona` como acima.
- `GET /instagram/profiles/{id}/avatar` → a imagem **principal** da pessoa; senão o jpg legado; senão **404**
  `sem_foto`.

**`GET /api/ai`**: `AiStatus.image` (`AiImageStatus`: `provider` `simulated|openai`, `model`, `quality`, `configured`,
`simulated`, `sends_data_externally`, `per_persona`, `on_create`, `price_per_image_usd`). O gerador de imagem não é
um papel de `roles`.

Provas: `simulated` (`backend/tests/test_persona_unificada.py::test_rotas_canonicas_e_apelidos_da_persona`,
`test_persona_geracao.py::test_rotas_de_geracao_e_enriquecimento`,
`test_persona_imagens.py::test_rotas_de_imagem_e_o_avatar_da_pessoa`). IA real e OpenAI real: `not_run`.

## Adendo v0.28 (27/09/2026) — conta única, credencial com consentimento e sessão por conta e aparelho (ADR-040)

Evolução 2, onda B ([persona § Contas e acesso](dominios/persona.md#contas-e-acesso);
[ADR-040](decisoes.md#adr-040--a-credencial-pertence-à-conta-da-persona-e-a-execução-não-carrega-credencial),
que substitui em parte o ADR-025 e o [adendo v0.16](#adendo-v016-26092026--credencial-fornecida-para-a-execução-adr-025)).
Código em `api.py`, `models.py`, `social/service.py`, `modules/identity/presentation/schemas.py`; integrado em
`4b95592`.

**A execução não carrega credencial.**

- `POST /api/runs` **não aceita mais** `credentials` nem `consent_credentials`: `RunCreate` tem `extra="forbid"`, e
  um cliente que ainda os mande recebe **422**. O painel de hoje ainda mostra o campo "Senha para a automação"
  (`features/command/CommandPanel.tsx`) até a onda E: digitar nele dá 422.
- Saem o 409 `consentimento_de_credencial` **por execução** e o 503 `cofre_indisponivel` da criação. Fica o 409
  `credencial_no_comando`, cuja mensagem aponta a conta da persona.
- `type_secret(name)` continua a mesma ferramenta do ator, mas `name` é o nome lógico da senha de uma conta da persona
  (`conta_<app>_senha`, `conta_<app>_<host>_senha`): resolvido pelo perfil do objetivo, exige consentimento na conta,
  só campo de senha, só no pacote da conta e, no navegador, só no `host` da conta (ou subdomínio). `open_url` abre os
  endereços do comando **e** qualquer caminho dos hosts das contas de portal da persona.
- Pré-voo: `requires.secrets` da habilidade casada é conferido contra as contas da persona de cada aparelho; aparelho
  sem a senha utilizável é recusado com `missing_credential` (mesmo formato dos demais impedimentos do pré-voo:
  `code`, `motivo`, `acao`).

**Rotas por conta** (`/api/instagram/profiles/{id}/accounts/{aid}/…`):

| Rota | Resposta | Recusas |
|---|---|---|
| `PUT …/credential` (`CredentialUpdate`: `password`, `login_identifier?`, `consent: bool`) | `ProfileAccountDTO` | **409** `consentimento_de_credencial` (conta que nunca consentiu, sem `consent: true`); **503** `secret_store_unavailable` |
| `DELETE …/credential` | `ProfileAccountDTO` | **404** |
| `POST …/credential/consent` | `ProfileAccountDTO` (marca `consent_at`/`consent_by` sem redigitar) | **409** `no_credential` |
| `POST …/session/connect` | **202** `{command_id, …, profile_id, account_id}` | **409** `no_credential`, `consentimento_de_credencial`, `no_session_provider` (app sem login gerenciado), `device_unavailable`, `device_no_internet`, e as do portão de sessão (`social/sessao_gate.py`) |
| `POST …/session/verify` | **202** (só observa) | as do portão |
| `POST …/session/logout` | **202** (`session.logout`: apaga os dados do app da conta naquele aparelho) | as do portão |
| `GET …/auth-attempts?limit=` | tentativas **desta** conta (`authentication_attempts.account_id`) | **404** |

- **Apelidos por perfil**: `PUT/DELETE /instagram/profiles/{id}/credential`, `POST …/{id}/connect`, `…/verify`,
  `…/logout`, `GET …/{id}/auth-attempts` resolvem a **conta âncora** (a do app que provê a conta do perfil) e
  continuam devolvendo o que devolviam. Perfil sem conta no app âncora → **409** `no_account`. `PUT …/credential`
  pelo apelido também exige `consent` (mesmo 409).
- `POST …/accounts` (`ProfileAccountCreate`) ganha `host?` (conta de portal; normalizado: minúsculo, sem esquema,
  caminho nem porta) e `consent`; com `password` sem `consent`, **409** `consentimento_de_credencial` antes de criar a
  conta. `duplicate_account` passa a ser por (app, host). `PATCH …/accounts/{aid}` ganha `host` (mesmo
  `duplicate_account`); `session_status` aceita `unknown | session_ready | auth_required | needs_person`
  (**`logged_out` → 422**), vale para o aparelho vinculado (**409** `no_binding` sem vínculo; **409**
  `session_managed` em app com provedor).

**Campos novos.**

- `ProfileAccountDTO`: `host`, `login_identifier`, `credential` (`CredentialInfo`: `configured`, `login_identifier`,
  `status`, `failed_attempts`, `blocked_until`, `updated_at`, `last_used_at`, **`consent_at`**, **`consent_by`**),
  `consent_at`, `session` (`SessionInfo`, com `instance_id` e `stale`), `session_actions`. Os escalares
  `session_status`/`session_detail`/`session_verified_at`/`credential_configured` ficam por compatibilidade e leem a
  mesma fonte (`account_sessions` no aparelho vinculado).
- `CredentialInfo` do perfil (`PersonaDTO.credential`) ganha `consent_at`/`consent_by` (é a credencial da conta
  âncora).
- `SessionStatus` ganha `needs_person`; `auth_required` é o antigo `logged_out` das contas sem provedor.
- `GET /api/instances/{id}/operational-context` (`contexto.py::contexto_do_aparelho`): `profile.accounts` lista as contas do perfil vinculado,
  cada uma com credencial (metadados), consentimento e a sessão neste aparelho.

Provas: `simulated` (`backend/tests/test_contas_unificadas_api.py::test_rotas_por_conta_e_apelidos_por_perfil`,
`::test_dto_da_conta_traz_credencial_consentimento_e_sessao`, `::test_marcar_sessao_sem_aparelho_e_409`;
`backend/tests/test_credenciais_da_conta.py::test_a_execucao_nao_aceita_mais_credencial`,
`::test_put_credential_sem_consentimento_e_409_tambem_pelo_apelido_por_perfil`,
`::test_pre_voo_recusa_aparelho_sem_a_credencial_que_a_skill_exige`). Conta real, PostgreSQL e produção: `not_run`.

## Adendo v0.29 (28/09/2026) — persona N:N aparelho e roteamento por persona

Evolução 2, onda C ([ADR-043](decisoes.md#adr-043--persona-nn-aparelho-vínculo-por-app-aparelho-principal-e-uma-conta-por-app-em-cada-aparelho),
[ADR-044](decisoes.md#adr-044--roteamento-das-execuções-por-persona-alvos-resolvidos-destinos-no-texto-e-prévia-obrigatória);
[persona § Aparelhos e roteamento](dominios/persona.md#aparelhos-e-roteamento)).

**Vínculos.**

- `POST /api/personas/{id}/devices {instance_id, app_id?, primary?}` → 201 `PersonaDTO`. 409
  `conta_do_app_ja_no_aparelho` (outra persona já serve aquele app ali; vale também para vínculo sem app de persona
  com conta no app); 400 `unknown_instance`/`store_instance`/`unknown_app`; 404 persona.
- `DELETE /api/personas/{id}/devices/{instance_id}[?app_id=]` → 200 `PersonaDTO` (o principal que sai é substituído
  pelo mais antigo); 404 `not_bound`; 409 `persona_in_use` (objetivo aberto dela ali).
- `PUT /api/personas/{id}/devices/{instance_id}/primary` → 200 `PersonaDTO`; 404 `not_bound`.
- `GET /api/instances/{id}/personas` → `[{profile_id, username, display_name, name, status, app_id, is_primary,
  bound_at, session}]`.
- `PersonaDTO.instance_id` passa a ser o aparelho **principal**; novo `devices: [{instance_id, app_id, is_primary,
  state, worker_id, bound_at, session}]`.
- `connect`/`verify`/`logout` (por perfil e por conta) e `GET …/operational-context` do perfil aceitam
  `?instance_id=` (aparelho vinculado; senão 409 `sem_vinculo`); sem ele, o principal.
- `POST /api/instagram/profiles` com `instance_id` de aparelho que já tem outra conta do Instagram → 409
  `conta_do_app_ja_no_aparelho` sem criar nada (antes tomava o aparelho).
- `POST /api/instances/{id}/training/start` (`profile_id`): 409 `persona_nao_vinculada`, 409 `persona_ambigua`.

**Execução.**

- `POST /api/runs`: +`targets: [{profile_id, instance_ids[], app_id?}]`, +`device_policy: one|primary|all`
  (padrão `one`); `profile_ids` + `instance_ids` = **interseção** (antes substituía); `targets` × `distribute` → 422.
- Novos códigos: 409 `alvos_nao_confirmados` (details `targets`, `command_sem_destinos`), 409
  `aparelho_repetido_na_execucao`, 409 `sem_intersecao`, 409 `sem_vinculo`, 409 `no_binding`, 400 `sem_alvo` (só
  na prévia). Ambiguidade ou contradição → a execução nasce `needs_input`, com evento `data.questions`.
- `POST /api/runs/targets/resolve {command, instance_ids?, profile_ids?, targets?, device_policy?}` →
  `{targets: [{instance_id, profile_id, app_id, origem: ui|texto|vinculo|balanceamento}], questions: [{code,
  question, field, options, instance_id, profile_id}], command_sem_destinos, warnings[]}`; não grava, não planeja.

Provas: `simulated` (`backend/tests/test_vinculos_n_n.py`, `test_personas_aparelhos_api.py`,
`test_roteamento_por_persona.py`, `test_alvos_no_texto.py`, `test_roteamento_execucao.py`). PostgreSQL e produção:
`not_run` até a implantação.

## Adendo v0.30 (28/09/2026) — assistente do comando: refinar e responder

[ADR-047](decisoes.md#adr-047--assistente-do-comando-refinar-com-a-ia-e-responder-à-execução-sem-reescrever-o-texto).
Corpos em `backend/app/taskqueue/assistente.py` (fora de `models.py`, como os do adendo v0.24).

- `POST /api/commands/refine {command, answers?: [{field, question, answer}], instance_ids?, profile_ids?, run_id?}`
  → 200 `{command, summary, questions: [{field, question, options, why}], ready, notes}`. Uma chamada de IA pelo
  papel `plan` (`ai_calls.role = "plan"`); não cria execução. Com `run_id`, as perguntas abertas daquela execução
  (plano `missing` ou evento com `data.questions`) vão ao refinador e os alvos saem da foto dela.
  - 409 `credencial_no_comando` (no comando ou numa resposta; nada vai à IA);
  - 409 `pergunta_de_destino` (resposta a `profile_id`/`instance_id`: escolha de alvo, não texto);
  - 404 `not_found` (`run_id` desconhecido); 503 `ai_not_configured`; 503 `ai_error` (`details.kind`,
    `details.retryable`).
- `POST /api/runs/{id}/successor {command, mode: "plan"|"execute" = "plan"}` → 200 `RunSummary` da nova execução.
  Mesmo pedido de alvos da foto (`instance_ids`, `profile_ids`, `targets`, `device_policy`; execução distribuída ou
  sem foto: os aparelhos dela). A antiga vai para `cancelled` com `status_detail` "Respondida: continua na execução
  <curto>" e um evento `log` com `data.successor_run_id`. Repetir a mesma resposta devolve a mesma sucessora
  (`deduplicated: true`). 409 `invalid_state` se a execução não está em `needs_input` (e não há sucessora daquele
  texto); os erros de `POST /api/runs` valem para a criação.

## Adendo v0.31 (28/09/2026) — crenças ricas da persona (ADR-048)

[ADR-048](decisoes.md#adr-048--crenças-ricas-da-persona-vão-ao-modelo-com-regra-de-conduta-biografia-v2);
[persona § O que vai ao modelo](dominios/persona.md#o-que-vai-ao-modelo-e-o-que-fica-guardado).

- `biography.schema_version = 2`. `biography.beliefs.religion: BioReligion | null` e `politics: BioPolitics | null`
  (campos no ADR-048). Tipos em `frontend/src/api/types.ts`: `BioReligion`, `BioIssue {topic, stance?}`,
  `BioPolitics`, `PraticaReligiosa`, `OrientacaoPolitica`, `EngajamentoPolitico`.
- Respostas sempre em v2. Linha v1 é lida assim: texto → `{summary, affiliation?}` ou `{summary, orientation?}`; texto
  em branco → `null`.
- `PATCH /api/personas/{id}`: mescla chave a chave dentro de `beliefs.*`; `null` apaga a crença; listas substituem
  inteiras; texto v1 ainda é aceito e convertido; chave desconhecida ou enum inválido → 422. A escrita grava v2.
- `POST /api/personas/generate` devolve crenças ricas (podem vir nulas). `POST /api/personas/{id}/enrich` completa
  crença sem `affiliation`/`orientation`, sem sobrescrever o que existe.

Provas: `simulated` (`backend/tests/test_persona_crencas.py`); `real` no relatório de validação §17.

## Adendo v0.32 (28/09/2026) — completar a persona com instruções do dono

Pedido do dono de 28/09: o "gerar por prompt" também completa o que falta numa persona existente, sem formulário novo
([persona § Geração por IA](dominios/persona.md#geração-por-ia-post-apipersonasgenerate); extensão do ADR-041/048).

- `POST /api/personas/{id}/enrich` aceita corpo **opcional** `PersonaEnrichBody {instructions?: string (≤ 500)}`
  (`extra="forbid"`). Sem corpo, como antes. Com `instructions`, o texto vai ao modelo como pedido do dono ("para o que
  falta, siga estas instruções… sem reescrever o que já está preenchido"), passando por `sem_marcacao`.
- A regra não muda: completa **só o vazio** (`preencher_vazios`); sem lacuna, devolve a persona sem chamar o modelo.
- Instrução com formato de credencial → **422 `instructions_with_secret`**, antes de qualquer chamada (o texto iria
  ao provedor e à proveniência). A regra de conduta do ADR-048 vale para o que a instrução pedir.
- Painel: cartão "Completar com IA" no topo da guia Persona, com uma linha de instrução opcional e o aviso de chamada
  paga; `api.enrichPersona(id, instructions?)`.

Provas: `simulated` (`backend/tests/test_persona_geracao.py::test_enriquecer_com_instrucoes_leva_o_pedido_do_dono_e_recusa_segredo_antes_de_chamar`,
`::test_rotas_de_geracao_e_enriquecimento`; vitest `ProfileDetail.test.tsx` "Completar com IA …").

## Adendo v0.33 (28/09/2026) — modo Automático: quem faz e onde (ADR-050)

[ADR-050](decisoes.md#adr-050--modo-automático-a-ia-escolhe-quem-faz-o-código-escolhe-onde-crença-é-coerência-não-alvo-de-persuasão).
Corpos em `backend/app/taskqueue/orquestrador.py`.

- `POST /api/runs/targets/suggest {command, max_personas?: 1..10 = 3}` → 200 `RunTargetsSuggestion`:
  `{modo: "ia"|"texto"|"distribuir"|"nenhuma", app_id, targets: [ResolvedTargetDTO], escolhidas: [{profile_id, nome,
  motivo, aderencia: "alta"|"media"|"baixa", instance_id, servidor}], descartadas: [{profile_id, nome, motivo}],
  nao_avaliaveis: [{profile_id, nome, falta}], alerta_conduta: str|null, perguntas: [str], questions (do
  resolvedor), command_sem_destinos, resumo, warnings}`. Não cria execução.
  - `modo=texto`: o comando cita destinos; é a prévia de `/runs/targets/resolve`, sem IA.
  - `modo=distribuir`: app sem conta; aparelhos pela carga (`N aparelhos` no texto, senão 1), sem IA.
  - `modo=ia`: uma chamada do papel `plan` (`ai_calls.role = "plan"`, sem `run_id`); com `alerta_conduta`,
    `targets` e `escolhidas` vêm vazios.
  - `modo=nenhuma`: sem app identificado e sem persona disponível, ou app com conta sem persona vinculada livre.
  - 409 `credencial_no_comando` (nada vai à IA); 503 `ai_not_configured`; 503 `ai_error`; 422 corpo inválido.
  - Declarada antes de `/runs/{run_id}/{op}`.
- Confirmar é `POST /api/runs` com os `targets` ecoados (agrupados por persona), como na prévia por persona.

## Adendo v0.34 (28/09/2026) — personas em lote

Pedido do dono de 28/09: "Nova persona a partir de um prompt" em lote e operações em lote na lista
([persona § Geração por IA](dominios/persona.md#geração-por-ia-post-apipersonasgenerate)).

- `POST /api/personas/generate/batch {prompt, count: 1..10, locale?, constraints?, create: bool = false}` → **202**
  `{batch_id, count}`; sem provedor de IA, 503 `ai_unavailable` antes do 202; `count` fora de 1..10 ou campo a mais →
  422. Em segundo plano, concorrência 2, cada item pelo mesmo caminho de `generate_persona_draft` (mesmas regras e a
  mesma recusa de segredo). Com `create: true`, cada rascunho válido vira persona pela porta única
  (`AppState.criar_persona`, a mesma do `POST /api/personas`), com a imagem de `ai.image.on_create`.
- **Variedade:** cada item leva no pedido `avoid` (as pessoas que já existem e as irmãs do lote) e `variation` (o
  índice); depois de gerar, nome repetido vira `failed` sem nova chamada paga. O simulado usa o índice na semente.
- `GET /api/personas/generate/batch/{id}` → `{batch_id, prompt, count, create, items: [{index, status:
  pending|generating|ready|created|failed, name, persona_id, draft, error}], done, created_at}`. Estado em **memória**
  (os últimos 20 lotes terminados): perdido num reinício → 404. Rascunhos `ready` ficam no estado para o painel criar
  os escolhidos por `POST /api/personas`.
- Evento `persona.batch.updated` `{batch_id, index, status, name, persona_id, error, finished, count, done}`.
- Teto de gasto do dia: a primeira recusa por orçamento encerra o lote; os itens restantes viram `failed` com o motivo.
- **Operações em lote no painel** (sem rota nova; as rotas por persona, três de cada vez, com resumo por pessoa):
  gerar mais fotos (`POST /personas/{id}/images {count}`), completar com IA (`POST /personas/{id}/enrich`), grupo de
  acesso, bloquear/reativar e apagar (confirmação digitada "apagar N"; as travas do `DELETE` voltam por pessoa).

Provas: `simulated` (`backend/tests/test_persona_lote.py`; vitest `NovaPersona`/`ProfilesPage`); `real` no relatório
de validação §17.

## Adendo v0.35 (28/09/2026) — projeção do plano por ação (item 18.3, ADR-052)

- `GET /api/runs/{run_id}/projection` → o normal medido de cada etapa do plano e a soma. Não chama IA.
  - Corpo:
    - `janela_dias` e `minimo_de_amostras`;
    - `chamadas`, `segundos` e `usd`, cada um `{p50, p90}` (somas das etapas);
    - `sem_base`: as chaves das etapas sem amostras próprias;
    - `etapas[]`: `{key, title, action, samples, calls, seconds, usd, no_baseline}`.
  - Etapa sem base própria usa o `*` do app (etapas sem ação), marcada; sem nem isso, entra com zero, marcada.
  - 404 `not_found`; 409 `no_plan` enquanto a execução não tem plano.
- A mesma soma sai no evento `decision` logo depois do plano: "Projeção pelo histórico (normal medido por ação): …".
- Na execução, uma etapa que passa do p90 da ação dela ganha um `decision` "acima do normal". Acima de
  `max(p90 × ai.step_budget.p90_factor, p90 + ai.step_budget.slack)` chamadas ela para com erro `budget`: `failed`,
  ou `uncertain` se o efeito já saiu. A recusa é gravada em `ai_calls` como as outras de orçamento.
- `type_text` devolve o que de fato entrou (item 18.1): `typed_chars` (real), `verified` (`true`, `false` ou `null`
  quando não há leitura ou campo), `completed_after_cut`, e, se incompleto, `missing` e `field_now`. Com texto
  incompleto, `enter` sai `false`.

## Adendo v0.36 (28/09/2026) — prévia com a IA no controle e `type_text` sem `null` (itens 19.3 e 19.5, ADR-053)

Sem mudança de forma na prévia: `StreamInfo` e o `frame` do `InstanceDTO` são os mesmos, sem status novo. Mudam a
origem do frame, o prazo e os textos.

- **Origem do frame.** Com a IA no controle (`control == "ai"`, de `ai_begin` a `ai_end`), a prévia não põe screencap
  próprio na fila do aparelho, nos dois `preview_mode`. O frame do painel (evento `frame`) vem da observação da IA:
  a observação só de árvore, com alguém olhando e o frame vencido no ritmo do nível, captura logo depois da árvore só
  para o painel (`DeviceManager._previa_pela_observacao`). O modelo continua sem imagem; falha dessa captura é falha
  da prévia, nunca da observação.
- **Prazo do frame.** Com a IA no controle e a prévia não pausada, `frame.stale` e `stream.status` usam no mínimo
  `FRAME_MAX_AGE_IA_S` = 30 s (`devices/manager.py::dto`); um `frame_max_age_ms` maior continua valendo. O painel usa
  o mesmo número (`AI_FRAME_MAX_AGE_MS` em `DeviceCard.tsx`, conferido por teste). Motivo: entre duas ações da IA no
  android-06 o menor intervalo foi 5,8 s e a mediana 21 s, e o limite do foco (6 s) acusava "Desatualizado" a cada
  ciclo.
- **Textos de `stream.detail`** (`devices/stream.py`): `live` diz "Tela ao vivo no ritmo da IA: um frame a cada
  observação dela"; `stale` diz que a IA não olha a tela há tempo demais (decisão, ação ou leitura demorada, ou
  aparelho sobrecarregado), e não que a captura atrasou. `capture_error`, `worker_offline` e `paused` não mudam.
- **Painel** (`features/devices/streamState.ts`): com `instance.control === 'ai'`, o selo do frame velho é "IA sem
  olhar a tela" (aviso), decidido pelo `control` e não pelo `stream.status` guardado, que pode ser o `live` do último
  `instance.updated`.
- **Avisos do aparelho** (`attention`): o de pressão diz o recurso que disparou ("Convidado sob pressão de CPU", "de
  RAM" ou "de CPU e RAM") com o remédio de cada um. Novo: "Convidado com interrupções acumuladas: …" quando o
  reinício a frio por interrupção (item 19.9) de menos de 6 h não resolveu; o reinício em si é um `restart` com
  `requested_by='system'`, visível no histórico de comandos.
- **`type_text`** (revê o v0.35): o texto é definido de uma vez no campo (`mobile: replaceElementValue`), com
  `mobile: type` só como alternativa em pedaços. `typed_chars` é o que está no campo; `verified` não sai mais `null`:
  sem leitura da tela ou sem o campo identificado, sai `false` com `reason`. `AutoCompleteTextView` e
  `MultiAutoCompleteTextView` contam como campo. `type_secret` não mudou.

Provas: `simulated` (`backend/tests/test_previa_nao_disputa_com_a_ia.py`, `test_saude_do_convidado.py`,
`test_digitacao_atomica.py`; vitest `DeviceCard.preview.test.tsx` e `FocusPanel.test.tsx`). `real` da digitação:
`r-20260928235215-6eb84c`, comentário inteiro em 234 ms ([relatório §21](relatorio-validacao.md)).

## Adendo v0.37 (29/09/2026) — aprendizado (fundação), proteção de contas e a leva aberta (ADR-054, ADR-055; Fases 20 e 21)

[ADR-054](decisoes.md#adr-054--aprendizado-contínuo-livro-de-aprendizado-com-ciclo-de-vida-publicação-sozinha-só-sem-efeito-externo-d1-feedback-implícito-com-botão-opcional-d2-lições-medidas-e-backlog-do-que-mais-falha)
e
[ADR-055](decisoes.md#adr-055--proteção-de-contas-a-conta-travada-para-sem-ser-tocada-o-aparelho-entra-em-quarentena-uma-conta-por-alvo-e-nenhum-reset-com-conta).
A leva aberta (itens 21.10–21.14) está implantada desde `7a02491`; o aprendizado e a proteção de contas entram com a
integração `c359f65` (migrações 054 e 055), e o painel vai no mesmo deploy.

**Livro de aprendizado (item 20.2).** Corpos em `backend/app/modules/learning/presentation/livro.py`. Nenhuma rota chama
IA.

- `GET /api/aprendizado?kind=&state=&app=&origem=` → `{itens: [Entrada], total, contagem: {<kind>: {<estado>: n}}}`.
  - `Entrada`: `{kind, ref, state, native_status, title, app, origin, side_effect, human_origin, requires_owner,
    created_at, state_at, last_used_at, uses, evidence: {for, against}, count, detail}`.
  - `kind`: `receita`, `fluxo`, `habilidade`, `memoria` (casa nativa; memória só como contagem) e `tela`, `licao`,
    `voz`, `preferencia` (em `learning_items`). `state`: o `SkillState` (`draft`, `candidate`, `validated`, `published`,
    `deprecated`, `disabled`). `origem`: `execucao`, `treino`, `pessoa`, `ensino`, `sistema`.
- `GET /api/aprendizado/pendentes` → `{itens, total}`: a fila do D1 ("Para aprovar") e a contagem da barra do topo.
- `GET /api/aprendizado/revisar` → `{itens, total}`: receitas e fluxos ATIVOS com efeito externo que nenhuma pessoa
  decidiu pelo livro (o legado anterior ao D1).
- `GET /api/aprendizado/{kind}/{ref}` → `{item: Entrada, evidencias: [{stance, origin_ref, run_id, instance_id,
  app_version, simulated, detail, observed_at}], trilha: [{id, from, to, reason, decided_by, decided_at, run_id}],
  exposicoes}`.
- `POST /api/aprendizado/{kind}/{ref}/status {to, reason}` (`reason` com 1 a 500 caracteres, `extra=forbid`) → o mesmo
  corpo do detalhe. Em receita e fluxo: CAS no status nativo e uma linha na trilha, com as guardas de antes (nunca duas
  receitas ativas na mesma chave, fluxo adotado não religa, substituída não volta). Quem decide é o operador da sessão
  (`panel` sem sessão), nunca `sistema`.
- Erros `{code, message}`: 404 `not_found`; 422 `invalid`; 409 `transition_forbidden`, `owner_required` (D1: item com
  efeito ou texto de pessoa só o dono publica), `state_conflict` (CAS), `vetoed` (conteúdo desligado por uma pessoa) e
  `use_skills_route` (habilidade: o corpo traz `href` da rota das habilidades); 503 `not_ready` antes da composição.
- Ainda não existem as rotas dos pacotes seguintes (`/api/aprendizado/falhas`, `/backlog/{id}`, `/sinais`,
  `/licoes/previa`, `/export`, `/voz/previa` e `/api/runs/{id}/feedback`); `router.py` já as registra antes da rota
  genérica `{kind}/{ref}`.
- Configuração: bloco `aprendizado` do `config.example.yaml` (modos `off`/`shadow`/`on` entre aspas;
  `ia_resumos_por_dia` só aceita 0).

**Projeção (revê o v0.35).** Em `GET /api/runs/{run_id}/projection`, `janela_dias` passa a ser a janela EFETIVA,
`min(ai.step_budget.window_days, log_retention_days)` — 14 no central, não 30 —, porque `ai_calls` é purgado na
retenção; a etapa só entra se começou dentro dela. Campos novos: `janela_configurada` e `amostras_sem_custo`. A mensagem
de orçamento do executor cita a janela efetiva.

**Confirmação manual presa ao print (item 21.5).** `POST /api/runs/{run_id}/objectives/{objective_id}/resolve` aceita
`{resolution, note?, evidence_id?}` (`ResolveBody`, `extra=forbid`).

- Em `confirm_done` de etapa com efeito externo, `evidence_id` é obrigatório: sem ele, 422 `evidence_required`. A
  evidência precisa existir e ser `screenshot` com imagem, desta execução e deste aparelho; senão, 422
  `invalid_evidence`. Sem efeito externo, o print é opcional.
- O id vai para `StepResult.evidence_id`, e "com base na evidência #N (print de …)" vai para o texto da evidência e para
  o histórico do perfil.
- O painel manda o último print com imagem da etapa parada e avisa quando não há. Painel e backend se implantam juntos:
  com `extra=forbid`, cada lado antigo recusa o outro.
- SEND_MESSAGE ganha `pending_marks` no catálogo ("Sending…", "Enviando…"): com a marca na tela, a verificação não chama
  o modelo e a etapa não se comprova (`VerifyOutcome.pending`); a prova local nova `sent_text:<seletor>` exige a bolha
  com o texto inteiro e o compositor vazio.

**Conta travada e quarentena (itens 21.1–21.4).**

- `/api/health`, `problems[]`: `locked_account_on_device` enquanto houver conta travada logada em aparelho `online`,
  `booting` ou `error`.
- `InstanceDTO.locked_account: string | null`, o @ do marcador aberto (os scripts `scale-test` e `rotation-test` pulam o
  aparelho). `account_label` passa a ser derivado do marcador ou, sem ele, do vínculo do app do aparelho, quando há o
  que derivar.
- `POST /api/instances/{instance_id}/actions/{action}`: `InstanceActionBody` ganha `confirm_locked_account: bool =
  false`, separado de `confirm`. Com marcador, só `stop` e `hibernate` passam sem ele; o resto → 409 `locked_account`, e
  o comando fica `rejected` no histórico. O pedido automático (remediação, rodízio, saúde, reconciliação) nunca o manda;
  `POST /api/instances/bulk` não o repassa, então em lote a quarentena sempre recusa. Os trabalhos de aparelho
  `app.install`, `app.canary`, `app.rollback`, `app.distribute`, `device.proxy` e `session.*` também são recusados;
  `app.verify` segue.
- Vínculo (`POST /api/personas/{id}/devices`), troca de aparelho (`PATCH /api/instagram/profiles/{id}` com
  `instance_id`) e cadastro (`POST /api/instagram/profiles`) num aparelho em quarentena → 409 `aparelho_em_quarentena`,
  antes de criar qualquer linha.
- O `PATCH` do status de um perfil grava a origem `declarado` com o operador como autor.
- Eventos novos:
  - `profile.status` em toda mudança de status: `{profile_id, anterior, status, origem: observado|declarado|regra,
    autor, evidencia}`;
  - `device.locked_account`: `{instance_id, handle, profile_id, app_id, origem, acao: marcado|resolvido, autor,
    evidencia}`;
  - `decision` do executor na trava: `data {kind: "auth_challenge", subtipo: conta_travada|codigo|verificacao, trecho,
    tela, package}`; o motivo em `objectives.blocked_reason` e `attempts.error` é `auth_challenge (<subtipo>): …
    "<trecho>"`;
  - `log` com `data.reason = "disjuntor_de_conta"` (`{profile_id, related_profile_ids, run_ids}`) e com `data.reason =
    "login_parado"`.
- `StepBlocked.kind` ganha `challenge` (vira `auth_challenge`, subtipo `verificacao`, que pede pessoa sem bloquear).
- Sessão: a credencial da conta ganha o estado `review` depois de um envio de senha sem sucesso; o login automático é
  recusado sem tocar, e só o Conectar (a pessoa) tenta. Ajuste novo `max_logins_per_day` no `sessao.yaml` (3 no
  Instagram), sobrescrevível em `contas.sessao.<pacote>.max_logins_per_day`.
- Frota: a política recusa, com motivo e sem `retry_at`, a ação sobre um alvo em que outra conta já agiu dentro de
  `limits.fleet_target_window_days` (30): uma conta por alvo em seguir, DM e comentário;
  `limits.fleet_max_accounts_per_target` em curtida. OPEN_POST aceita `post_author` opcional, herdado por LIKE_POST,
  UNLIKE_POST e CREATE_COMMENT; cada ação com balde declara `counterparty` no catálogo. SEND_MESSAGE para quem nunca
  escreveu a esta conta é sempre `approval_required`.

**Leva aberta (itens 21.10–21.14, implantada em `7a02491`).**

- `GET /api/desempenho?irq_horas=0..336&irq_aparelho=`: com `irq_horas` 0 (padrão), a resposta é a de antes. Com mais
  que 0, bloco `interrupcoes`: `{desde, aparelho, n, truncada, por_aparelho: {<id>: {ultimo, irq_frac: {n, p50, p95,
  max, media}, serie: [{ts, irq_frac, load1, ocioso, controle, interesse}]}}}`, com teto de 20.000 linhas (ficam as mais
  recentes). A fonte é `measurements(kind='irq')`, uma linha por sonda de saúde (30 s) por aparelho, com `instance_id`,
  `irq_frac`, `load1`, `ncpu`, `ocioso`, `controle`, `interesse`, `cpu_total_ticks` e `cpu_irq_ticks`; o Diagnóstico
  (`medicoes_recentes`) não as mostra.
- `Recipe.status` ganha `candidate`: a receita aprendida pela IA nasce candidata (`ai.recipes_promote_after`, padrão 2;
  0 é o modo antigo), a IA decide e a receita só é comparada; as concordâncias seguidas a promovem a `active`.
  `shadow_agree` e `shadow_total` passam a contar por execução da etapa e zeram na divergência da candidata;
  `superseded` passa a ser escrito. `PUT /api/recipes/{id}` segue aceitando `active` e `quarantined` (pendência: ainda
  não recusa `active` para candidata ou substituída).
- Métricas: `automacao.leitura_falhou{resultado=relida|persistiu}` e `receita.consulta{resultado=candidata}`.

Provas: `simulated` (`backend/tests/test_learning_livro.py`, `test_projecao.py`, `test_dm_verificador.py`,
`test_quarentena_de_conta.py`, `test_detector_conta_travada.py`, `test_protecao_de_frota.py`,
`test_disjuntor_de_conta.py`, `test_conduta_de_login.py`, `test_interrupcoes_persistidas.py`,
`test_receita_candidata.py`; vitest `execution.test.tsx`); `real` só de leitura no [relatório
§22](relatorio-validacao.md); o resto `not_run` até o deploy.

## Adendo v0.38 (29/09/2026) — aprendizado A2–A9, apps de segundo plano e a espera do reparo (ADR-054, ADR-055; itens 20.3–20.10, 21.15 e 21.16)

[ADR-054](decisoes.md#adr-054--aprendizado-contínuo-livro-de-aprendizado-com-ciclo-de-vida-publicação-sozinha-só-sem-efeito-externo-d1-feedback-implícito-com-botão-opcional-d2-lições-medidas-e-backlog-do-que-mais-falha)
e
[ADR-055](decisoes.md#adr-055--proteção-de-contas-a-conta-travada-para-sem-ser-tocada-o-aparelho-entra-em-quarentena-uma-conta-por-alvo-e-nenhum-reset-com-conta).
Implantado com `f497075` em 29/09 ~07:38Z, sem migração nova: tudo cabe nas tabelas da 055 (a espera do reparo, 21.16,
estava no ar desde `9348e9c`, ~04:17Z). Nenhuma rota abaixo chama IA. As rotas que o v0.37 anunciava como "ainda não
existem" existem agora; `/api/aprendizado/backlog` tem só `GET` e `PATCH` por id. Domínio:
[dominios/aprendizado.md](dominios/aprendizado.md).

**Ordem de montagem.** As rotas específicas entram antes da genérica `GET /api/aprendizado/{kind}/{ref}`
(`modules/learning/presentation/router.py`), que casaria com elas e recusaria o `kind` com 422. O roteador do
aprendizado entra antes do `api.py` (`main.py`), cujo `POST /api/runs/{run_id}/{op}` casaria com `/feedback`.

**O que mais falha e backlog (A3, item 20.4).** Corpos em `presentation/falhas.py`.

- `GET /api/aprendizado/falhas?dias=14&app=&camada=&limite=20&simulados=false&retroativo=true&formato=json|md`
  (`dias` 1–90, `limite` 1–200).
  - JSON: `{gerado_em, commit, janela: {dias, desde, ate, corte_de_custo, custo_parcial}, filtros, regras, itens,
    total_de_grupos, abaixo_do_minimo, verificacao, telas, propostas, outro: {ocorrencias, total, pct, alerta},
    saude, em_andamento}`.
  - Cada item: `{id: "fk-…", cluster_key, app, capability, tipo, tela, camada, titulo, ocorrencias, retroativas,
    elegiveis, taxa, etapas, execucoes, aparelhos, usd_perdido, min_perdidos, intervencoes, custo_total,
    custo_parcial, tendencia: {ultimos_7d, anteriores_7d, direcao}, exemplos, erros_de_ia, primeira, ultima,
    onde_alterar: {arquivos, doc, prova}, estado, registrado, plan_item, licoes_ativas}`.
  - `retroativo=true` (padrão) inclui o legado classificado NA LEITURA, nunca gravado; `retroativas` diz quantas
    ocorrências vêm dele.
  - `formato=md`: `text/markdown`, com a chave `fk-*` na primeira coluna do topo. É o que
    `scripts/aprendizado-backlog.py` grava em `data/aprendizado/`.
- `GET /api/aprendizado/backlog/{id}` → `{linha, registrado, grupo | null, proposta | null}`. A linha pode estar gravada
  ou existir ainda só no relatório. `linha`: `{id, category: falha|proposta, cluster_key, title, state, app,
  capability, tipo, tela, plan_item, fixed_in_commit, fixed_at, baseline, verification, reopened_count, first_seen,
  last_seen, notes, updated_by, updated_at}`.
  - `verification.reincidencia.janela_de_tentativas` diz quantas tentativas elegíveis a reincidência mede depois de
    `fixed`: as últimas 2 × `prova_minimo`, nunca desde a prova.
- `PATCH /api/aprendizado/backlog/{id} {state, plan_item?, fixed_in_commit?, notes?}` (`extra=forbid`; `plan_item` até
  60, `fixed_in_commit` até 40, `notes` até 500) → o corpo do `GET`. `state` é um de `open`, `triaged`, `planned`,
  `fixed_pending_proof`, `fixed`, `reopened` e `wontfix`. Erros:
  - 409 `transition_forbidden` para `fixed` e `reopened`, que são medida, não declaração;
  - 422 `invalid` para `fixed_pending_proof` sem o sha do commit (7 a 40 hexadecimais) ou numa proposta;
  - 409 `note_looks_secret`: nada é gravado.
  
  A prova começa no `PATCH`, e o commit não é conferido contra o que está no ar (pendência): marque depois do deploy.

**Feedback D2 (A4, item 20.5).** Corpos em `presentation/feedback.py`.

- `POST /api/runs/{run_id}/feedback {objective_id?, verdict, reason?, note?}` (`extra=forbid`) → 201
  `{signal, efeitos, resumo}`.
  - `verdict`: `certo` | `errado`; `errado` exige `reason`, `certo` não leva.
  - `reason`: `fez_outra_coisa`, `alvo_errado`, `nao_terminou` (os três de navegação rebaixam o que o item usou e o que
    aprendeu), `texto_ruim`, `demorou_ou_gastou`, `pediu_ajuda_a_toa` e `outro`.
  - `note` até 500 caracteres.
  - Sem `objective_id`, o voto vale para a execução (`source_ref` `run:<id>`); com ele, `objective:<id>`. Há um voto
    por pessoa e por item: votar de novo troca o voto, sem reativar nada.
  - Cada efeito: `{acao, kind, ref, uso, de, para, aplicado, erro, desfazer}`. `desfazer` é
    `{method: "POST", href: "/api/aprendizado/{kind}/{ref}/status", body: {to: "published", reason}}` só quando o voto
    desligou algo que ESTAVA publicado. Do `candidate`/`validated`, vem `null`: aquela volta publicaria o que nunca
    passou pelo D1.
  - Erros: 404 execução inexistente ou objetivo de outra execução; 422 fora do vocabulário; 409 `note_looks_secret`
    (nem o voto nem a nota são gravados); 409 `state_conflict`.
- `GET /api/runs/{run_id}/feedback` → `{run_id, votos: [Sinal], sinais: [Sinal]}`. O bloco `aprendizado` (o que a
  execução ensinou ou usou do livro) é emitido desde o adendo v0.39.
- `GET /api/aprendizado/sinais?dias=14&kind=&app=` (`dias` 1–400) → `{sinais: [Sinal], total, contagem: {<kind>: n},
  dias}`. `total` e `contagem` contam as linhas devolvidas, que param em 500 (as mais recentes), não a janela inteira.
- `Sinal`: `{id, kind, polarity, verdict, reason, note, note_refused, source_ref, created_by, run_id, objective_id,
  step_id, attempt_id, instance_id, profile_id, app_package, capability, step_hash, failure_kind, step_verified, data,
  simulated, created_at, updated_at}`.
  - `kind`: `feedback`, `confirmou_a_mao`, `repetiu_item`, `abandonou_item`, `repetiu_execucao`, `tomou_controle`,
    `respondeu_pergunta`, `escolheu_habilidade`, `aprovacao_decidida`, `tela_vista` e
    `tela_desconhecida_chamou_pessoa`. `cancelou_execucao`, `comando_incerto_resolvido` e `correcao_de_ensino` têm
    escritor desde o adendo v0.39.
  - `source_ref` dos gestos (A2): `resolve:<objective_id>:<plan_version>.<seq>`, `repeticao:<run_id>:<sha12>`,
    `resposta:<run_id>:<campo>` e `takeover:<attempt_id>`. O gesto sai com `created_by` `panel`: o nome do operador não
    chega aos arquivos quentes.
  - A resposta a uma pergunta guarda o campo e o sha256, nunca o valor; a tomada de controle, só ids (`data {}`).

**Status de item.** `POST /api/aprendizado/{kind}/{ref}/status` (v0.37) é também o `desfazer` do voto e o caminho das
lições, telas, vozes e preferências. A habilidade continua 409 `use_skills_route`; o painel a decide pela rota das
habilidades (`POST /api/skills/{id}/versions/{n}/status`).

**Lições (A7, item 20.8).** `GET /api/aprendizado/licoes/previa?app=&papel=actor|planner&acao=&etapa=` (`app` com 3 a
200 caracteres) → `{app, papel, acao, etapa, modo, vai_ao_prompt, bloco, tokens: {bloco, licoes}, teto: {tokens,
itens, caracteres_por_item}, licoes: [{id, texto, tokens, nivel, detalhe, escopo: {app, acao, etapa}, evidencia:
{for, against, execucoes}, faltam}], cortadas}`.

- `bloco` é o texto EXATO que iria ao prompt, com todas as publicadas que cabem no teto, como se estivessem no braço
  `with`. Nada é gravado.
- `vai_ao_prompt` só é `true` com o modo `on`. No `shadow` de fábrica, nada vai.
- `faltam` é quantas unidades faltam no braço mais curto até o veredito.
- O verificador não tem papel aqui. As lições entram só em `DecisionRequest.lessons` e `PlanRequest.lessons`, que são
  contrato interno do provedor, não HTTP ([ia.md §15](ia.md)).

**Telas aprendidas (A8, item 20.9).** `GET /api/aprendizado/export?app=<pacote>&kind=tela` → `text/yaml`: o fragmento
das telas validadas e publicadas do app, com a proveniência em comentário e já conferido pelo carregador do motor. Outro
`kind` → 422 `invalid`; telas não ligadas → 503 `not_ready`. Só leitura: a absorção pelo YAML é da curadoria, depois do
commit implantado.

**Voz e preferências (A9, item 20.10).**

- `GET /api/aprendizado/voz/previa?profile_id=` → `{profile_id, modo, vai_ao_prompt, aprovacoes_editadas, candidatas,
  publicadas, blocos: [{capability, tokens, texto, pares: [{gerado, editado}]}], mensagem}`. `texto` é o bloco
  `<exemplos_de_voz origem="pessoa">` que iria ao contexto social daquele perfil e daquela ação. `tokens` conta os
  pares, não o bloco inteiro (pendência). 404 perfil inexistente.
- `GET /api/aprendizado/preferencias/sugestoes?run_id=` → `{run_id, modo, sugestoes: [{campo, valor, item_id}]}`: o
  que PRÉ-PREENCHER nas perguntas de uma execução em `needs_input`. Nunca responde, nunca cria a sucessora, nunca grava.
  404 execução inexistente. O painel ainda não a consome.

**Estados novos nos conhecimentos nativos (A5, item 20.6).**

- `GET /api/flows`: `status` ganha `candidate` (o fluxo aprendido de execução nasce assim, inerte) e `validated` (com
  etapa de efeito, espera o dono).
- `Recipe.status` ganha `validated`: a receita com ação `commit` para ali na promoção em sombra.
- `PUT /api/flows/{id}` e `PUT /api/recipes/{id}` seguem aceitando só `active`/`disabled` e `active`/`quarantined`.
  Desde o adendo v0.39 elas passam pela trilha e pelo veto do livro.

**Aparelhos (itens 21.15 e 21.16).**

- `instance.updated` com a mensagem `<id>: apps de fundo — N desativado(s): …` quando o preparo desativou ou reativou
  algum pacote. O resumo cita também os já desativados, os ausentes na imagem, as falhas e o `incerto`. Passagem sem
  mudança não publica, e o `incerto` sozinho (prazo de 12 s estourado) só vai ao log.
- `InstanceDTO.attention` ganha "Reparo adiado: esta máquina está com N% de CPU; …": o reparo automático esperou a
  máquina, e nenhum comando nem `instance.remediation` foi emitido.

**Configuração nova** (`config.example.yaml`; os padrões do código são os mesmos):

| Chave | Padrão | O que faz |
|---|---|---|
| `aprendizado.licoes.modo` | `"shadow"` | `off` não grava; `shadow` grava e mede sem ir ao prompt; `on` publica sem efeito e leva ao prompt, em prova |
| `aprendizado.licoes.ator` / `.planejador` | `{tokens: 120, max: 3}` / `{tokens: 150, max: 3}` | teto por papel, sobre todas as elegíveis antes do braço |
| `aprendizado.licoes.minimo_por_braco` / `maximo_por_braco` / `holdout_publicada` | 8 / 20 / 0.1 | veredito de efeito; controle depois de "ajuda" |
| `aprendizado.telas` | `{modo: "observe", observacoes: 3, execucoes: 2}` | `observe` grava, minera e valida, e a sessão não consome |
| `aprendizado.fluxo` | `{concordancias: 1, com_prova: true}` | `com_prova: false` volta ao modo sem D1 (só para a suíte) |
| `aprendizado.voz.modo` / `aprendizado.preferencias.modo` | `"off"` | a voz é sempre publicada pelo dono; a preferência só sugere |
| `aprendizado.backlog` | `{minimo_ocorrencias: 3, pessoa_usd: 0.25, aparelho_usd_min: 0, prova_minimo: 10, prova_fator: 0.5}` | ordem e prova da correção |
| `aprendizado.takeover_gravar` | `false` | reservado: gravar as entradas manuais da tomada (não implementado) |
| `aprendizado.retencao` | sinais 180 d, feedback 365 d, exposições 120 d, 200 evidências por item, diário 400 d, candidata sem evidência 90 d | purga do livro; publicados, desligados, a trilha e o backlog nunca são purgados |
| `android.desativar_apps` | 13 apps do Google | lista desativada no preparo; `[]` devolve tudo; por aparelho em `instances.overrides.<id>.desativar_apps` |
| `instances.remediation_host_cpu_max` | 90 (10–101) | CPU do central a partir da qual o reparo espera 10 min; 101 desliga |

Provas: `simulated` (`backend/tests/test_learning_backlog.py`, `test_learning_rotas_falhas.py`,
`test_learning_feedback.py`, `test_learning_licoes.py`, `test_prompts_licoes.py`, `test_learning_telas.py`,
`test_learning_voz.py`, `test_learning_preferencias.py`, `test_d1_fluxos.py`, `test_d1_receitas.py`,
`test_apps_de_fundo.py`, `test_saude_do_convidado.py`, `test_cobertura_de_rotas.py`); `real` só de leitura
(`/api/aprendizado`, `/revisar`, `/pendentes`, `/falhas`) e o evento dos apps de fundo, no [relatório
§23](relatorio-validacao.md); o resto `not_run`.

## Adendo v0.39 (29/09/2026) — o código em aberto do aprendizado: interruptor antigo pelo livro, bloco da execução, três sinais e nota triada (ADR-054)

**`PUT /api/flows/{id}` e `PUT /api/recipes/{id}` passam pelo livro** (`api.py::update_flow`/`update_recipe` →
`livro.py::mudar_status_legado` → `LearningService.mudar_status_nativo`, o mesmo serviço de
`POST /api/aprendizado/{kind}/{ref}/status`).

- **Contrato:** corpo e resposta iguais. `active` vira `published`; `disabled` e `quarantined` viram `disabled`. A
  rota de receita continua zerando `consecutive_fail`.
- **Trilha:** `learning_transitions` grava `decided_by` = operador da sessão (sem sessão, `panel`), com motivo fixo
  ("ligado/desligado na lista de fluxos do painel", "reativada/posta em quarentena na lista de receitas do painel"). O
  que a pessoa desliga por ali passa a vetar: o sistema não reaprende nem repromove.
- **Sem mudança:** pedir o status em que o item já está devolve 200 e não gera transição.
- **Fluxo em prova com `active`:** dois passos na trilha (`candidate → validated → published`), atômicos na
  transação da rota. Se o segundo for recusado, nada fica: nem status, nem linha da trilha, nem o `consecutive_fail`
  da receita.
- **Recusas novas:**
  - receita inexistente: 404 `not_found` (antes, 200 sem mexer em nada);
  - receita `superseded`: 409 `transition_forbidden`;
  - segunda receita ativa na mesma chave: 409 `state_conflict`;
  - a guarda do livro: 409 `state_conflict`.
- **Recusas que continuam:** 409 `flow_adopted` e `command_published`, conferidas na mesma transação da mudança.

**`GET /api/runs/{run_id}/feedback` traz `aprendizado`**:
`{receitas, fluxos, falhas, candidatas, licoes}`, cada um uma lista de
`{kind, ref, titulo, estado, papel, braco, failure_kind, n}`.

- `estado` é o vocabulário do livro (`candidate|validated|published|deprecated|disabled`), o ATUAL do item.
- `braco` é `with|holdout|null`.
- `papel` é texto fechado em português. A autoria vem no final: "nesta execução" (sistema), "pelo voto de uma pessoa"
  ou "por uma pessoa". `decided_by` e motivo nunca saem.
- A linha de falha vem com `kind`, `ref` e `titulo` nulos, mais `failure_kind` e `n`.
- Item apagado depois da execução: a linha fica, com o `ref` e sem título nem estado.
- O título passa pela triagem de credencial (recusado sai `null`) e é cortado em 160 caracteres.
- As listas vêm sempre, vazias quando nada mudou. `aprendizado: null` só quando a leitura falhou; votos e sinais saem
  mesmo assim.
- Regras dos grupos em [dominios/aprendizado.md](dominios/aprendizado.md).

`GET /api/runs/{run_id}/projection` não mudou; o painel passou a consumi-la no cartão de custo da execução (409
`no_plan` é "ainda sem plano", 404 esconde a seção).

**Três escritores de sinal.**

| `kind` | Rota do gesto | `source_ref` | `data` |
|---|---|---|---|
| `cancelou_execucao` | `POST /api/runs/{id}/cancel` | `cancelamento:<run_id>:<instante da transição>` (um por episódio) | `{status_anterior}` |
| `comando_incerto_resolvido` | `POST /api/commands/{id}/resolve` | `comando:<command_id>` | `{verbo, resolucao, resolved_by}` |
| `correcao_de_ensino` | `POST /api/teaching-sessions/{id}/corrections` | `correcao:<teaching_id>:<turn_id>` | `{teaching_id, skill_id}` |

`created_by` é o operador da sessão (sem sessão, `panel`); o `requested_by` do corpo não entra. As rotas não mudaram
de contrato; só gravam o sinal, e a falha do escritor não derruba o gesto. Polaridades em
[dominios/aprendizado.md](dominios/aprendizado.md#sinais-o-que-a-pessoa-já-faz-e-o-botão-opcional-d2).

**Nota triada no comando.** `POST /api/commands/{id}/resolve` e `POST /api/commands/{id}/cancel` recusam a nota com
formato ou assunto de credencial (o critério do voto do D2): 409 `note_looks_secret` antes de qualquer escrita. Nada
é gravado, nem a resolução nem o pedido, e o comando segue como estava.

## Adendo v0.40 (29/09/2026) — Fase 22: pendências da rodada (ADR-054)

**Nota e autor do comando (22.2).** `CommandResolveBody` e `CommandCancelBody` ganham `origin?: 'panel'`.

- **Nota:** é só o texto da pessoa. Com `origin: 'panel'`, o backend compõe o contexto depois de "por <autor>": ", no
  painel a partir de <instance_id>", ou só ", a partir de <instance_id>" quando o autor é `panel`.
- **Resultado:** `result.note` guarda só o texto da pessoa, e `result.origin='panel'`.
- **Autor livre:** o `requested_by` passa pela mesma triagem de credencial da nota sempre que vier, com ou sem sessão
  (409 `note_looks_secret`, nada gravado, nem a decisão nem o pedido).
- **Cliente antigo:** o prefixo "decidido/cancelado no painel a partir de <id>" continua aceito só com o
  `instance_id` do próprio comando, e vira `origin=panel`.

**Operador nos gestos (22.1).** `POST /runs/{id}/objectives/{oid}/resolve`, `POST /runs/{id}/retry_failed`, a sucessora
e o pedido de controle do aparelho levam o operador da sessão ao sinal do aprendizado (sem sessão, `panel`). Sinal de
gesto é um por `(kind, source_ref)`: o primeiro autor fica. Nenhum corpo ou resposta mudou.

**Tela da falha (22.3).** `attempts.failure_screen` passa a ser gravado:
- é o nome de uma regra declarada no `telas.yaml` do app da etapa, ou o tipo do motor nas telas protegidas
  (`desafio|dois_fatores|login`);
- é NULL quando a tela é desconhecida, outro app está na frente ou não houve observação;
- só é gravado com `failure_kind`;
- nunca é texto da tela.

Em `GET /api/aprendizado/falhas` e no backlog, a linha sem tela mede o trio em qualquer tela (`alcance: "trio"`), e a
linha com tela conta na prova o excesso da tela desconhecida (`alcance: "tela"`, `sem_tela: {ocorrencias, excesso,
taxa_base}`).

**Adoção de fluxo com trilha (22.4).** Adotar, desfazer a adoção e publicar a versão de quem adotou gravam a trilha do
fluxo (`GET /api/aprendizado/fluxo/{id}` mostra "adotado pela habilidade X" / "devolvido pela habilidade X").
- Adoção ou devolução pelo sistema: `transition_forbidden` (pelas rotas não acontece).
- O 409 `state_conflict` da adoção tem texto novo: o banco recusou a adoção, a versão, o escopo ou a trilha; nada foi
  gravado.
- A falha não-integridade da trilha na adoção sobe como 500 e desfaz o gesto (política atual).

**Transação abortada (22.5).** No PostgreSQL, uma escrita dentro de uma transação já abortada deixa de virar ROLLBACK
calado: o `tx()` confere a transação antes do COMMIT e responde 500 (`TransacaoAbortada`), sem gravar nada pela metade.

**Correção de ensino pela execução (22.7).** Nenhuma rota nova.
- `GET /api/runs/{id}` já devolve `origin {skill_id, skill_version, node_id, strategies}` em `plan.steps` e em
  `plan_versions[].steps` quando a etapa veio de habilidade; o tipo `PlanStep` do painel passa a declará-lo.
- O painel usa `GET /api/teaching-sessions?status=open` (o resumo traz `base_version`), `POST /api/teaching-sessions`
  com `{instruction, skill_id, base_version}` (versão inexistente: 404 `not_found`) e
  `POST /api/teaching-sessions/{id}/corrections` com `{body, run_id, step_id}`, em que `step_id` é o `steps.id`.
- Erros mostrados em português: 400 `credential_in_text`, `correction_needs_skill`, `step_not_correctable`, 409
  `teaching_state`, 404 e 422.

**Preferência no bloco da execução (22.6).** Novo `papel` no grupo `candidatas` para `kind=preferencia`: "preferência
que nasceu com a evidência desta execução, entre N execuções", ou "… e de outras" quando o número não vem. O formato
não muda.

## Adendo v0.41 (29/09/2026) — Onda 0 da terceira evolução: contratos C1–C5 (ADR-056, ADR-057, ADR-058)

Só forma, compatível para trás: com os valores padrão, nada muda no comportamento. Nenhuma rota nova; as rotas de rede
(`/api/network/*`) são do 25.2 e do 25.8. Prova: `simulated` (`backend/tests/test_contratos_terceira_evolucao.py`).

**Sessão por conta (C1, ADR-057).** `SessionProvider.ensure_session(rt, profile_id, *, account_id=None, force_login,
automatic, observe_only)`: o parâmetro novo vem antes dos outros nomeados, na porta, no motor
`integrations/app_declarado/sessao.py` e nos dublês. `account_id=None` é a conta do app âncora, como hoje; o motor
aceita um id e ainda o ignora. A resolução por conta é o 23.4. Nenhum corpo HTTP mudou.

**Saídas de etapa (C2, ADR-058).** Migração 056.
- `PlanStep.saidas: string[]`: os nomes que a etapa produz, cada um em `^[a-z][a-z0-9_]{0,39}$` e sem repetir; fora disso,
  a validação do `PlanStep` recusa. Fica fora do JSON quando vazia: `runs.plan` e `plan_versions[].steps` dos planos de hoje não mudam. No
  painel, `saidas?: string[]`.
- `steps.saidas` guarda a mesma lista na linha da etapa. `StepDTO` não mudou.
- `Repository.save_step_output(step_id, name, value, *, value_kind='text', app_id=None)` recusa nome inválido, valor
  acima de 2000 caracteres e `value_kind` fora de `text|number|url|list`. O nome é único no objetivo e a última escrita
  vence. `Repository.step_outputs(objective_id) -> {nome: valor}`.
- `StepOutcome.outputs: dict[str, str] | None` existe no executor e ainda não é preenchido.
- A referência `{{saida:<nome>}}` ainda não é resolvida: é o 24.3.

**Rede por aparelho (C3, ADR-056).** Migração 057: `network_profiles`, `device_network` e `network_measurements`.
- Tipos em `models.py` e em `frontend/src/api/types.ts`:
  - `NetworkProfileKind` (`vpn|proxy`) e `NetworkProtocol` (`wireguard|singbox|http|socks5`);
  - `NetworkPolicy` (`livre|exigida|exigida_com_bloqueio`, padrão `livre`);
  - `NetworkState` (`pendente|configurado|conectado|trafego_verificado|parcial`, padrão `pendente`).
- `NetworkProfileDTO` não tem campo de segredo nem o `secret_ref`, só `has_secret: bool`. `params` recusa chave com cara
  de segredo (senha, token, chave privada, pre-shared, credencial).
- `DeviceNetworkDTO` e `NetworkMeasurementDTO` espelham as colunas. Nos booleanos da medição, `null` quer dizer "não
  medido".
- Os três DTOs recusam campo desconhecido (`extra="forbid"`).
- O verbo `device.network` entra em `APP_COMMAND_VERBS`, sem executor: ninguém o despacha ainda.
  - Por estar nesse conjunto, ele ocupa o aparelho enquanto estiver em voo, como os outros verbos de app.
  - Na quarentena do ADR-055 é recusado, porque não está em `VERBOS_DE_APP_NA_QUARENTENA` (ADR-056 §7).
- As tabelas da 041 (`proxy_profiles`, `device_proxy_state`) e as rotas `/api/proxies` ficam como estão.

**Portão de rede (C4).**
- `Scheduler.rede_gate: Callable[[instance_id], motivo | None] | None`, padrão `None`, sem efeito.
- Com o portão ligado, o motivo devolvido faz o objetivo esperar em `pending`, sem virar `waiting_user`, com
  `status_detail` = o motivo e `wait_reason = "rede"`, um valor novo no vocabulário de `wait_reason`.
- O portão é consultado com o aparelho online e antes das portas do app e da sessão, para um login não sair por uma
  rede ainda não verificada.
- Quem liga o portão ao estado da rede, e o tratamento de `rede` no painel, é o 25.6.

**Conjunto de apps (C5).**
- `RunTargetsSuggestion.app_ids: string[]` (`POST /runs/targets/suggest`) e `ResolvedTargetDTO.app_ids: string[]` (em
  `targets` da prévia e da sugestão) passam a vir sempre.
- `app_id` segue sendo o primeiro da lista. Com só `app_id`, a lista é `[app_id]`; sem app, `[]`.
- No painel, `ResolvedTarget.app_ids` é opcional: o alvo que vem no `detail` de uma recusa (409 da criação) sai do
  resolvedor, sem o campo.
- Tirar a lista de `Plan.required_apps` é das frentes da Fase 24.

## Adendo v0.42 (29/09/2026) — Onda 1 da terceira evolução: sessão por conta, contas por app, comando entre apps e rede por aparelho (ADR-056, ADR-057, ADR-058)

Compatível para trás: quem não manda os campos novos tem o comportamento de antes. Prova: `simulated` (os testes da
evolução em `backend/tests/`); Outlook e Instagram num aparelho real, a aplicação e a medição da rede no aparelho
seguem `not_run` (24.9, 25.4, 25.5).

**Rede por aparelho (25.2, ADR-056).** Rotas novas; nenhuma resposta ou evento traz segredo nem `secret_ref`.

| Rota | Sucesso | Erros |
|---|---|---|
| `GET /api/network/profiles` | 200 `{profiles: [NetworkProfileDTO + in_use: string[]]}` | — |
| `POST /api/network/profiles` | 201 `NetworkProfileDTO` (só `has_secret`) | 422 sem `input` nem `ctx` (`invalid_body`, `invalid_secret` ou a lista de erros); 409 `name_taken`; 503 `secret_store_unavailable` |
| `DELETE /api/network/profiles/{id}` | 204 | 404 `not_found`; 409 `network_profile_in_use` com `instance_ids` |
| `GET /api/network/devices` | 200 `{devices: [...]}` | — |
| `POST /api/network/assign` | 200 `{accepted, dry_run, devices: [...]}` | ver abaixo |
| `POST /api/network/devices/{iid}/verify` | 202 | 404 `not_found`; 409 `store_instance`, `aparelho_em_quarentena`, `nothing_requested` |
| `POST /api/network/devices/{iid}/reapply` | 202 | os mesmos de `verify` |

- **Cadastro:** `{name, kind: vpn|proxy, protocol, endpoint_host, endpoint_port, params?, secret?}`, campo desconhecido
  recusado. O `secret` sai do corpo antes da validação e vai ao cofre. `params` com chave ou valor com cara de segredo
  é recusado.
- **Aparelhos:** cada item traz `instance_id`, `worker_id`, `external`, `device_state`, `network`
  (`DeviceNetworkDTO` ou `null`), `effective_state`, `legacy_proxy` (o proxy da 041, que vale `configurado` no
  máximo), `restriction` (a quarentena), `real_account`, `required_apps` (pacotes que a medição tem de cobrir),
  `pending` (`aplicar`, `verificar` ou `null`) e `last_measurement`. A loja fica de fora.
- **Atribuição:** `{instance_ids (1–200), vpn_profile_id?, proxy_profile_id?, policy?, confirm_real_account: string[],
  dry_run}`. Campo omitido fica como está; `null` num perfil o tira. O alvo é sempre a lista explícita.
  - Cada item de `devices`: `{id, outcome: would_assign|assigned|unchanged|refused, code?, reason, from, to, reapply,
    warnings}`.
  - `dry_run` devolve só a prévia. Sem ele é tudo ou nada: com alguma recusa, 409 com o `code` da recusa (ou
    `assign_refused` quando há mais de um) e `devices` com a prévia inteira, sem gravar nada.
  - Recusas por item: `store_instance`, `aparelho_em_quarentena`, `policy_without_profile`, `policy_without_vpn` e
    `real_account_confirm_required`. Esta última vale quando a saída de um aparelho com conta vinculada muda sem o id
    dele em `confirm_real_account` (um a um, ADR-056 §7).
  - Erros do lote: 400 `nothing_to_change`, `unknown_instance` e `wrong_profile_kind`; 404
    `network_profile_not_found`.
- **Verificar e reaplicar:** 202 `{accepted, instance_id, action: verify|reapply, pending, desired_rev, applied_rev,
  state, executed: false, reason}`. O 202 é "pedido registrado", nunca "aplicado". `reapply` cria revisão nova e volta
  o estado a `pendente`; `verify` não muda o estado.
- **Evento `network.updated`:** `data` com `instance_id`, `acao` (`assign|verify|reapply|observado|medicao|
  perfil_criado|perfil_apagado`) e, conforme a ação, `desired_rev`, `applied_rev`, `state`, `measurement_id`,
  `profile_id` e a política e os perfis pedidos.
- **Portão (C4):** `wait_reason = "rede"` também é gravado na troca de app entre etapas, antes da conta e da sessão do
  app seguinte. A composição ainda não liga o portão ao estado da rede (25.6).

**Conta e sessão por conta (23.4, 23.5, ADR-057).**
- `POST …/accounts/{aid}/session/connect` e `…/session/verify` operam a CONTA do caminho (`ensure_session(…,
  account_id=aid)`). Conta de site (com `host`) é recusada com 409 `conta_de_site`: o login gerenciado é o da conta do
  app, sem site.
- Desafio numa conta de outro app para só aquela conta (credencial em `review`), sem bloquear a persona; a conta
  travada põe o aparelho em quarentena. O marcador da quarentena usa o @ da conta, ou o identificador de login quando
  ela não tem @.
- Trocar o servidor de um aparelho pede confirmação quando QUALQUER conta de uma persona vinculada tem sessão pronta
  nele, não só a âncora.

**Senha de outra conta da persona (ADR-057, D1).** O campo é `clonar_de` (um id de conta), não `credencial_de`: nome
com "credencial" é tratado como segredo pela redação.
- `POST /api/instagram/profiles/{pid}/accounts` aceita `clonar_de`. O cofre copia a senha para uma entrada própria da
  conta nova, que nasce sem consentimento. Erros: 422 `clonar_de_com_senha` (com `password`) e
  `consentimento_nao_clonado` (com `consent`).
- `POST /api/instagram/profiles/{pid}/accounts/{aid}/credential/clone`, corpo `{clonar_de, login_identifier?}`:
  200 `ProfileAccountDTO`. Troca a senha da conta existente; o consentimento dela, se já dado, fica.
- Erros das duas rotas: 404 `not_found` (origem inexistente); 409 `credencial_de_outra_persona`,
  `clonar_de_si_mesma` e `no_credential`; 503 `secret_store_unavailable`.
- A trilha registra só ids. O identificador de login não é copiado da origem.

**Política de ações por app (23.10).**
- `GET|PUT /api/instagram/profiles/{pid}/policy` e `GET|POST|PUT /api/instagram/policy-groups[/{id}]` aceitam
  `?package=`; sem ele, o app âncora.
- `ProfilePolicyDTO.package` e `PolicyGroupDTO.package` dizem de que app é o recorte: `capabilities`, `loosened`,
  `own`, `group` e `origin` são só desse app; `limits` valem para o perfil inteiro.
- Gravado: a política dos apps que não são o âncora fica em `capabilities.por_app.<pacote>`, sem migração.
- `GET /api/app-catalog` traz `profile_anchor` por app.

**Comando entre apps (24.1–24.6, ADR-058).**
- **Etapas e apps do plano:** `PlanStep.app_id` diz o app de cada etapa. Todo plano do planejador preenche
  `Plan.required_apps`: os apps em que as etapas rodam, e o app dos aparelhos só quando todos estão no mesmo. No início
  da execução, o pré-voo confere esses apps em cada aparelho.
- **Distribuição:** `GET /api/runs/distribution` aceita `command` no lugar de `app_id` (422 `distribution_sem_alvo`
  sem os dois); a criação distribuída sem app recusa com 409 `distribution_sem_app`.
- **Saídas de etapa (C2):**
  - A etapa que lê declara `saidas` e as seguintes citam `{{saida:<nome>}}`. O planejador declara e cita; citar um
    nome que nenhuma etapa anterior lê vira `missing` com `field: "saida"`, e o plano sai sem etapas.
  - O despacho troca a referência pelo valor na linha da etapa antes da porta de política. A ferramenta `read_value`
    lê o valor do texto do elemento, e `step_done` sem a leitura é recusado.
  - Código de verificação, senha e token nunca são saída.
  - Ação de catálogo também entrega valor (ADR-065): a capability declara `saidas` (nomes que pode entregar), a etapa
    de catálogo do plano entre apps leva `saidas` com um subconjunto delas, e o `PlanStep.saidas` gravado é o mesmo
    da etapa livre. Nome fora do declarado vira `missing` (`field` = a ação em minúsculas). Sem mudança de rota,
    de DTO ou de migração.
- **Relatório:** `per_instance[].values_read: [{name, value, value_kind, step_title, app, read_at}]` em
  `GET /api/runs/{id}/report`, e a seção "Valores lidos entre etapas" no markdown.

## Adendo v0.43 (30/09/2026) — Fase 29: prova de vazamento na linha do aparelho e leitura do firewall por interface (ADR-056, ADR-061)

Compatível para trás: só campos novos e um estado novo na leitura do firewall. Prova: `simulated`
(`backend/tests/test_rede_sonda.py`, `test_rede_worker.py`, `test_db.py`); a leitura do cliente VPN em aparelho real e
a migração numa cópia do banco do central são `real` (30/09); o reinício do backend sem reinício de aparelho por 6 h é
`not_run` (item 29.4).

**`DeviceNetworkDTO` (em `GET /api/network/devices` → `devices[].network`, e nas respostas de `verify`, `reapply` e
`assign`).** Campos novos, da migração 063:

| Campo | Tipo | Significado |
|---|---|---|
| `leak_rev` | inteiro ou nulo | a revisão em que o teste de vazamento foi feito; nulo = nenhum teste |
| `leak_client` | texto ou nulo | a instalação do cliente VPN testada: `<versão> (<código>) <pasta de instalação>` |
| `leak_result` | booleano ou nulo | `true` = bloqueio provado; `false` = vazou; nulo = não concluiu |
| `leak_at`, `leak_detail` | texto ou nulo | quando, e o que a sonda mostrou (ou por que a prova foi apagada) |
| `leak_pending` | booleano | ensaio marcado e sem desfecho |

A prova vale quando `leak_rev == desired_rev` e `leak_result == true` (o backend ainda confere o cliente lido no
aparelho). Com `policy: exigida_com_bloqueio`, é ela que decide `state: trafego_verificado` e `pending`; o
`leak_blocked` de `last_measurement` é o registro do que a sonda levou.

**`POST /api/network/devices/{iid}/verify`.** Com `exigida_com_bloqueio`, o pedido apaga a prova da linha (`leak_rev`
volta a nulo, o motivo fica em `leak_detail`). O `state` não muda na resposta, mas `pending` passa a `verificar` e a
tarefa com rede exigida espera até o teste refeito; o `reason` diz isso e avisa do reinício. O pedido sobrevive a um
reinício do backend.

**Evento `network.updated`.** `data.acao: "vazamento"` com `leak_result` (e `adotada: true` quando a prova veio do
histórico, na primeira subida com a 063).

**`GET /api/network/server` → `remote_access.firewall` e `POST /api/network/server/firewall-check`.** Estado novo
`regra_obsoleta` (fechado: a regra aponta, por `-Program`, para um executável que não é o binário atual do servidor).
Campos novos na leitura: `lan_subnet`, `warnings`, `stale_rules`, `ignored_rules`, `missing`, `inspect_command` e
`revert_command`. `commands` passa a trazer a regra sem `-Program`, restrita à sub-rede IPv4 e à interface lidas do
sistema, idempotente; o que o sistema não informou vem como marcador (`<SUB-REDE-IPV4-DA-LAN>`), e `missing` diz o que
faltou. A plataforma continua só lendo o firewall.

**Medição por perna de UDP (29.5).** `GET /api/network/devices` → `devices[].last_measurement` ganha `udp_dns_ok` e
`udp_ntp_ok` (booleano, ou nulo quando o `detail` não diz), derivados do `detail` da medição, que passa a trazer
`UDP DNS <bytes> B (<n>ª de 3, <s> s), NTP <bytes> B (0 de 3, <s> s)`. `udp_ok` continua o E das duas pernas e **não**
entra na regra de `trafego_verificado`.

**Saída esperada (29.6).** `POST /api/network/profiles` aceita `params.egress_esperado` (IPv4 público) e
`params.egress_esperado_ipv6`; endereço não público responde 422 com o campo no texto. `GET /api/network/devices`
ganha, por aparelho, `egress_expected: {ipv4, ipv6, profile_id, profile_name}` ou nulo (a do perfil que dá a saída
final: o proxy, se atribuído; senão a VPN) e `egress_matches` (nulo até a revisão pedida ser medida). Medida diferente
da esperada leva a `parcial`, com o motivo no `detail`, e emite `network.updated` de nível `warn` com
`data.acao: "saida_divergente"`. `POST /api/network/assign` devolve, por item, `egress_warnings: [{code, message,
profile_id, shared_with?}]` com `saida_dedicada_compartilhada` e `saida_dedicada_trocada_por_compartilhada`; são avisos
e não recusam (a confirmação por aparelho com conta real não muda).

**Saída pela casa (29.20).** `GET /api/network/devices` devolve, além de `devices`, `central_egress: {ipv4, ipv6,
measured_at, reason}` (a saída pública medida do próprio central, em cache de memória; família sem medida válida = `null`,
com o motivo em `reason`, `"ok"` quando as duas valem) e, por aparelho, `egress_home: {ipv4, ipv6, ipv6_outside_profile,
leaves_by_home, basis, measured, reason}`: `ipv4`/`ipv6` = a última saída medida do aparelho é a do central (IPv6: mesmo
/64); `ipv6_outside_profile` = IPv6 medido com o perfil de VPN sem IPv6; `leaves_by_home` = resumo. Qualquer campo `null`
= sem medida de um dos lados (nunca "limpo"). `basis`: `"measured"` (saída medida contra a do central), `"presumed"`
(aparelho SEM rede pedida e sem medida que o contradiga: `leaves_by_home` é `true`, estado próprio) ou `null` (sem
veredito). `measured`: `{ipv4, ipv6, measured_at, source: "device_network" | "probe_no_network"}` com os IPs em que o
veredito se apoia, ou `null`. Aparelho sem rede pedida é medido só nos IPs (sonda de IP do uid 2000) pela varredura, sem
criar linha em `device_network`. Campos novos, aditivos; sem migração. Configuração, todos com padrão:
`rede.sonda.medir_central` (true), `central_ttl_s` (600), `central_prazo_s` (6) e `medir_sem_rede` (true).

**Configuração.** `rede.cliente_atividade` (atividade principal do cliente VPN; o Start dela religa o túnel sem reinício, W8:
só o Start da interface recalcula o `serviceMode` do SFA; vazio desliga), `rede.cliente_tile` (LEGADO: tile do cliente, não é
mais usado pela convergência) e `rede.espera_tun_s` com padrão 180 (era 60), contados do boot. Compatível para trás: campo
novo com padrão; `config.yaml` antigo, sem `cliente_atividade`, passa a usar o Start da interface.

**Renderizador do emulador (29.11).** Compatível para trás: campos novos com padrão e um código de recusa novo. Prova:
`simulated` (`backend/tests/test_renderizador.py`, `test_contratos_do_worker.py`); a configuração por aparelho e por
worker é `real` (android-07 e android-09, 30/09); a leitura pela API no ambiente central é `not_run` até a segunda
implantação.

- `InstanceDTO.renderer`: `{configured, gles, vulkan, fallback}` ou nulo. `configured` é o `gpu_mode` pedido (o
  `instances.overrides.<id>.gpu_mode` ou o padrão do `config.yaml`; num aparelho de worker, o `android.gpu_mode` do
  `worker.yaml`). `gles` e `vulkan` são o que o emulador **selecionou**, lidos da última linha `emuglConfig_init` do
  log a cada entrada no ar; nulos fora do ar ou sem a linha. `fallback: true` quando o selecionado não é o pedido — e
  aí o aparelho ganha um aviso em `attention` com o pedido, o selecionado e o caminho do log. Nulo no DTO = não é
  emulador conhecido (aparelho físico, ou worker com agente anterior a esta versão).
- Recusa `409 app_incompativel`: o app declarou, no `app.yaml` (`renderizador_recusado: [swiftshader]`), o
  renderizador do emulador em que não roda, e o deste aparelho é esse, ou não se sabe qual é (o que não se sabe
  recusa: abrir o app derrubaria o emulador). Vale para os verbos `install_apk` e `open_app` do aparelho, para a
  instalação por destino (`POST /api/instances/{id}/app/install`), para o canário e a volta de uma release, para a
  distribuição (o aparelho fica fora do lote, com o motivo) e para o pré-voo do comando. Aparelho físico não é
  recusado.
- Protocolo do worker (`DeclaredDevice`): `gpu_mode`, `gpu_gles`, `gpu_vulkan`, todos com padrão nulo. Agente antigo
  não os manda, e o central trata o renderizador daquele aparelho como desconhecido.

## Adendo v0.44 (01/10/2026) — Retrieval de contexto de código: leitura de estado (ADR-063)

Compatível para trás: uma rota nova, só leitura, sem efeito colateral e sem mudar nenhuma existente. Prova: `simulated`
(`backend/tests/test_context_retrieval_integration.py`).

**`GET /api/context-retrieval/status`** — não consulta código, não chama provedor e não gasta nada.

| Campo | Tipo | Significado |
|---|---|---|
| `enabled` | booleano | o interruptor `context_retrieval.enabled` |
| `mode` | texto | modo efetivo: `disabled`, `local_only`, `shadow` ou `hybrid` (`disabled` sempre que `enabled` é falso) |
| `top_k` | inteiro | arquivos no contexto entregue |
| `provider` | objeto | `name`, `model`, `available` (booleano) e `unavailable_reason` (`key_missing`, `no_provider`…); nunca a chave |
| `external_send` | objeto | o que a política de envio decide, SEM rede: `allowed`, `reason`, `repository_class`, `configured_for_remote` (a configuração pede envio a provedor remoto), `visibility_verified` (há prova vigente de que o repositório real é público; igual a `remote_visibility_verified`), `visibility` (`public`, `private`, `unverified`, `not_applicable`), `remote_visibility_verified`, `head_public_verified` (o HEAD local existe no repositório público) e `worktree_clean` (`true`, `false` ou `null` quando não se aplica). O envio remoto exige as três provas: `public` no YAML sem prova vigente é `allowed: false`, `reason: repository_visibility_unverified`; HEAD sem prova, `repository_head_not_public`; worktree sujo, `repository_worktree_dirty` (imediato, sem depender de TTL) |
| `budget` | objeto | `timeout_ms`, `max_calls` (por pedido) e `max_cost_usd` |
| `summary` | objeto | dos eventos recentes: `requests`, `by_mode`, `cache` (`hit`/`miss`), `latency_ms` (`p50`, `p95`, `n`), `cost_usd`, `input_tokens`, `fallbacks` (por razão) e `privacy_blocks` |

Sem a configuração composta responde 503 `not_ready`. Nunca devolve código, a pergunta ou caminhos de arquivo.

## Adendo v0.45 (02/10/2026) — Pedidos persistentes (Fase 28, item 28.9) — adendo, PROPOSTO, não implementado

**Estado deste adendo.** É a PROPOSTA de contrato do item 28.9 do [plano-100](plano-100.md). Nenhuma rota, evento ou campo
abaixo existe no código: nada aqui é `real` nem `simulated`; a prova é `not_run` até a implementação (aceite proposto no
fim). Quando esta proposta contradiz o corpo base ou um adendo anterior, vale o que está em vigor no código, e a mudança
só vale depois de implementada e de a prova trocar de nível. Compatível para trás: só rotas novas e campos opcionais novos.

Fontes: [desenho dos pedidos persistentes](design/pedidos-persistentes.md), §6 (modelo), §6.2 (estados), §6.3
(identidade), §7.9 (cancelar, pausar, editar, retomar) e §11 (experiência, incluída a lista de rotas); ADR-044 (prévia
obrigatória dos alvos), ADR-047 (assistente e sucessora), ADR-059, ADR-062 (a caixa de Pendências). O modelo e os estados
estão escritos, e ainda não na `main`, no commit `a46485a4` do branch de integração da Fase 28 (`jev/integ-28`):
`backend/migrations/067_pedidos.sql` (tabelas e CHECKs, linhas 47–123),
`backend/app/modules/pedidos/domain/estados.py` (vocabulários nas linhas 36–52, arestas do pedido em 68–98, da
ocorrência em 105–137, regras em 143–168), `domain/chave.py` (formato do instante na linha 46, chave em 63–81, tentativa
em 83–96) e `domain/recorrencia.py` (`interpretar` na 163, `Instante` na 106, `proximas` na 375, `LIMITE_PREVIA` na 59).

### O que o contrato fixa, em seis linhas

1. O pedido é o objetivo que dura; a ocorrência é cada vez que ele pede uma execução; a execução é a `RunDetail` de
   sempre. A API **solicita e acompanha**: quem materializa, despacha e fecha é o laço de pedidos (28.4), nunca a rota.
2. A criação passa por uma **prévia sem efeito e sem custo** (ADR-044): só a confirmação, com o selo `confirmacao` que a
   prévia devolveu, cria um pedido `ativo`.
3. Estado muda só por ação nomeada (`ativar`, `pausar`, `retomar`, `cancelar`); `PATCH` nunca muda `estado`.
4. Toda ação inválida para o estado atual é `409 invalid_state` com as ações que valem; a tabela de transições é a de
   `estados.py`, e o painel lê `acoes_permitidas` em vez de reescrevê-la.
5. Repetir é seguro: criação por `idempotency_key`; ação de estado repetida no estado que ela já produziu é `200` com
   `sem_mudanca: true`; `executar` e `backfill` repetem pela chave determinística da ocorrência (`chave.py`).
6. A caixa de **avisos** é um canal informativo; o que depende de uma pessoa continua na caixa de **Pendências**
   (ADR-062). Os dois não se misturam (seção "Avisos e Pendências").

### Tipos (TypeScript)

Datas: o formato-base do documento, com UMA exceção — `OcorrenciaDTO.previsto_para` e `chave` usam o instante canônico
de `chave.py` (UTC, segundo cheio, sufixo `Z`: `2026-10-03T11:00:00Z`), porque a identidade da ocorrência depende do
texto. Todo nome de campo abaixo é o da coluna da migração 067; o que é calculado pela API está marcado.

```ts
type Autonomia     = 'observar' | 'preparar' | 'agir';                      // estados.py:36
type Sobreposicao  = 'pular' | 'guardar_uma' | 'permitir_todas';            // estados.py:38
type TipoDeGatilho = 'agora' | 'horario' | 'recorrencia' | 'evento' | 'condicao' | 'persona';   // estados.py:40
type OrigemOcorrencia = 'agenda' | 'recuperacao' | 'evento' | 'condicao' | 'persona' | 'manual' | 'backfill'; // :42
type MotivoDeEncerramento = 'prazo' | 'contagem' | 'orcamento' | 'abandonado';   // estados.py:52
type EstadoPedido = 'rascunho' | 'ativo' | 'pausado' | 'aguardando_pessoa' | 'concluido' | 'encerrado' | 'cancelado';
type EstadoOcorrencia = 'prevista' | 'devida' | 'despachada' | 'rodando' | 'concluida' | 'falhou' | 'incerta'
                      | 'cancelada' | 'pulada' | 'perdida';
type AcaoDePedido = 'editar' | 'ativar' | 'pausar' | 'retomar' | 'cancelar' | 'executar' | 'backfill';

interface PedidoDTO {                       // uma linha de `pedidos`
  id: string;                               // 1–28 caracteres de [A-Za-z0-9_-] (chave.py:41)
  titulo: string;
  objetivo: string;                         // texto-base do comando, SEM destino e SEM segredo
  contexto: string | null;
  criterios_sucesso: string[] | null;       // lista verificável; null = só prazo, contagem ou orçamento encerram
  alvos: PedidoAlvos | null;                // foto dos alvos no formato de `runs.targets` (migração 051)
  autonomia: Autonomia;                     // padrão 'observar': pedido sem escolha nunca age (`067_pedidos.sql:54`)
  fuso: string;                             // nome IANA; padrão 'America/Sao_Paulo'
  inicio_em: string | null; fim_em: string | null;                 // UTC; fim_em null = sem prazo
  max_ocorrencias: number | null;           // > 0
  orcamento_total_usd: number | null; orcamento_ocorrencia_usd: number | null;      // >= 0; limite conhecido: ver abaixo
  sobreposicao: Sobreposicao;               // padrão 'pular'
  janela_recuperacao_s: number | null;      // null = o padrão por tipo de gatilho (§7.5)
  coalescer: boolean;                       // a coluna é 0/1; padrão true
  max_tentativas: number;                   // execuções por ocorrência (total); >= 1; padrão 2; só repete sem efeito possível (28.5)
  pausa_por_falha: number;                  // N falhas seguidas pausam; >= 1; padrão 3
  estado: EstadoPedido;
  versao: number;                           // sobe a cada edição
  proxima_em: string | null;                // UTC; CACHE da próxima materialização
  criado_por: string | null;                // operador da sessão (nunca o nome do corpo)
  pausado_motivo: string | null;            // sempre preenchido quando estado = 'pausado'
  encerrado_motivo: MotivoDeEncerramento | null;
  pai_id: string | null;                    // SÓ LEITURA até o 28.10 (sub-pedidos)
  criado_em: string; atualizado_em: string;
}

interface PedidoAlvos {                     // o mesmo `targets` de POST /api/runs/targets/resolve
  targets: { instance_id: string | null; profile_id: string; app_id: string | null;
             origem: 'ui' | 'texto' | 'vinculo' | 'balanceamento' }[];
  device_policy: 'one' | 'primary' | 'all';
}

interface GatilhoDTO {                      // uma linha de `pedido_gatilhos`
  id: string; tipo: TipoDeGatilho; ativo: boolean; criado_em: string;
  spec: GatilhoSpec;                        // JSON; ver abaixo
  cursor: string | null;                    // só evento e condição (28.8); nunca editável
}
type GatilhoSpec =
  | {}                                                        // agora
  | { dtstart: string }                                       // horario: hora LOCAL ingênua 'YYYY-MM-DDTHH:MM:SS', no fuso do pedido
  | { dtstart: string; rrule: string }                        // recorrencia: subconjunto da RFC 5545 (recorrencia.py)
  | Record<string, unknown>;                                  // evento, condicao, persona: forma do 28.8

interface OcorrenciaDTO {                   // uma linha de `pedido_ocorrencias`
  id: string; pedido_id: string; pedido_versao: number; gatilho_id: string | null;
  previsto_para: string;                    // instante canônico (segundo cheio, 'Z')
  chave: string;                            // 'ped:<pedido>:<gatilho>:<instante>' (+ ':manual'|':backfill'); sem dado da pessoa
  origem: OrigemOcorrencia; estado: EstadoOcorrencia;
  tentativa: number;                        // execuções já pedidas; a n-ésima usa a chave 'chave:t<n>'
  run_id: string | null;
  run: { id: string; short_id: string; status: RunStatus; status_detail: string | null } | null;   // CALCULADO
  run_disponivel: boolean;                  // CALCULADO: false com run_id preenchido = a execução foi purgada
  motivo: string | null;                    // obrigatório em pulada, perdida, cancelada, falhou e incerta (estados.py:137)
  custo_usd: number;                        // acumulado de todas as tentativas, gravado antes da purga
  resumo: string | null;
  criada_em: string; iniciada_em: string | null; terminada_em: string | null;
}
// NÃO expostos (encanamento do laço): materializada_token, dono, prazo_posse.

interface PedidoView extends PedidoDTO {    // o que a lista e o detalhe devolvem; tudo abaixo é CALCULADO
  gatilhos_resumo: { tipo: TipoDeGatilho; descricao: string }[];   // 'Todo dia às 08:00 (America/Sao_Paulo)'
  personas: { profile_id: string; nome: string }[];                // lidas de `alvos`
  proxima_local: string | null;             // `proxima_em` no fuso do pedido, com o deslocamento
  ultima_ocorrencia: { id: string; estado: EstadoOcorrencia; terminada_em: string | null; motivo: string | null;
                       run_id: string | null } | null;
  ocorrencias_por_estado: Partial<Record<EstadoOcorrencia, number>>;
  gasto_usd: number;                        // soma de `custo_usd` das ocorrências
  orcamento_usado: number | null;           // gasto_usd / orcamento_total_usd; null sem orçamento
  avisos_nao_lidos: number;
  acoes_permitidas: AcaoDePedido[];         // pela tabela de estados.py; o painel não a reescreve
}

interface PedidoDetalhe extends PedidoView {
  gatilhos: GatilhoDTO[];
  proximas: ProximaData[];                  // as próximas 5, calculadas com `recorrencia.proximas`
  ocorrencias_recentes: OcorrenciaDTO[];    // as últimas 20; a lista completa é a rota de ocorrências
  execucoes_em_curso: { run_id: string; ocorrencia_id: string; status: RunStatus }[];
  pendencias: PendenciaDoPedido[];          // só com estado = 'aguardando_pessoa'; lido do estado vivo
  memoria: unknown | null; relatorios_recentes: unknown[] | null; observacoes_recentes: unknown[] | null;
                                            // null = ainda não existe (28.7); [] = existe e está vazio
}
interface ProximaData { gatilho: number; nominal: string; local: string; utc: string;
                        desviado: boolean; repetido: boolean }     // `Instante.para_dict` + índice do gatilho
interface PendenciaDoPedido { tipo: 'aprovacao' | 'pergunta' | 'ocorrencia_incerta';
                              ref: string; run_id: string | null; ocorrencia_id: string | null; desde: string }
```

**Orçamento: limite conhecido.** `gasto_usd` soma o custo das ocorrências FECHADAS: o custo de uma execução em curso só entra
quando ela fecha. Por isso o excesso máximo do orçamento é o custo de UMA ocorrência aberta, limitado pelo teto da execução
(`min(orcamento_ocorrencia_usd − gasto da ocorrência, orcamento_total_usd − gasto_usd)`); com a ocorrência aberta nenhuma outra é
despachada, e o pedido encerra com `encerrado_motivo = 'orcamento'` quando ela fecha. `orcamento_usado` pode, portanto, passar de
1. Uma ocorrência adiada por saldo da conta de IA (ADR-051) segue `devida` dentro da janela de recuperação e, passada ela, vira
`perdida` com `motivo` "adiada por saldo além da janela: ...". (Implementado e testado no 28.6/28.5, prova `simulated`.)

`ProximaData.desviado` e `repetido` existem **para esta API mostrá-los** (`recorrencia.py:18` e `:111`): `desviado` = a hora local
não existia (salto do horário de verão) e o pedido roda no primeiro instante válido depois do salto; `repetido` = a hora
local aconteceu duas vezes e o pedido roda só na primeira. O painel diz isso ao lado da data.

`RunSummary` ganha `pedido_id: string | null` e `ocorrencia_id: string | null` (a coluna `runs.pedido_id` e
`runs.ocorrencia_id` da 067; `null` em toda execução anterior, que é o que ela de fato é). Aditivo.

### Rotas (todas sob `/api/pedidos`)

`/previa` e `/avisos` são declaradas ANTES de `/{pedido_id}` (o mesmo cuidado de `POST /api/runs/targets/suggest`).

| Método e rota | Corpo | Resposta |
|---|---|---|
| `POST /api/pedidos/previa` | `PedidoCorpo` + `proximas?` | `200 PedidoPrevia`; sem efeito, sem gravação, **sem chamada de IA** |
| `POST /api/pedidos` | `PedidoCorpo` + `idempotency_key` + `titulo?` + `confirmacao?` | `201 PedidoView` (`ativo` se veio `confirmacao`, senão `rascunho`); `200 {…, deduplicated: true}` se a chave já existia |
| `GET /api/pedidos` | filtros em query | `200 {items: PedidoView[], proximo_cursor, total_por_estado}` |
| `GET /api/pedidos/{id}` | – | `PedidoDetalhe`; `404 not_found` |
| `PATCH /api/pedidos/{id}` | `PedidoEdicao` | `200 PedidoEdicaoResultado` (`dry_run: true` = prévia da edição, nada grava) |
| `POST /api/pedidos/{id}/ativar` | `{confirmacao}` | `200 PedidoView` (`rascunho` → `ativo`) |
| `POST /api/pedidos/{id}/pausar` | `{motivo?}` | `200 {pedido, sem_mudanca}` |
| `POST /api/pedidos/{id}/retomar` | `{modo?: 'daqui' \| 'recuperar'}` | `200 {pedido, sem_mudanca, puladas, recuperadas}` |
| `POST /api/pedidos/{id}/cancelar` | `{confirmar: true, motivo?}` | `200 {pedido, sem_mudanca, execucoes_em_curso, ocorrencias_canceladas}` |
| `POST /api/pedidos/{id}/executar` | `{solicitado_em}` | `202 {ocorrencia, deduplicated}` |
| `POST /api/pedidos/{id}/backfill` | `{de, ate, dry_run?, confirmar_ocorrencias?}` | `200` na prévia; `202 {ocorrencias, ja_existentes}` ao gravar |
| `GET /api/pedidos/{id}/ocorrencias` | `?estado=&origem=&de=&ate=&limit=&antes_de=` | `200 {items: OcorrenciaDTO[], proximo: string \| null}` |
| `GET /api/pedidos/{id}/execucoes?limit=` | – | `RunSummary[]` pelo `runs.pedido_id`, as mais novas primeiro |
| `GET /api/pedidos/avisos` | `?lido=&requer_pessoa=&pedido_id=&limit=&cursor=` | `200 {items: AvisoDTO[], nao_lidos, proximo_cursor}` |
| `POST /api/pedidos/avisos/ler` | `{ids?: string[] (≤ 200), todos?: true}` | `200 {lidos, nao_lidos}`; repetir é seguro |
| `GET /api/pedidos/{id}/relatorios` · `/observacoes` | `?limit=&cursor=` | **dependem do 28.7** (tabelas que a 067 não tem); até lá a rota não existe |

Os nomes de rota são os do §11 do desenho, com **uma divergência proposta**: a prévia é `POST`, não `GET /api/pedidos/previa`.
Ela leva o texto do comando e a especificação dos gatilhos; texto de comando em query string vai para log de proxy e
histórico do navegador, e a regra do projeto é nunca pôr dado da pessoa na URL.

### Criação pelo Comando, com prévia

**Corpo comum (`PedidoCorpo`).** `extra="forbid"` como `RunCreate`: campo desconhecido é 422, e `pai_id` e `estado` não se
enviam (o primeiro é do 28.10; o segundo muda só por ação).

```ts
interface PedidoCorpo {
  objetivo: string;                         // 3–4000, como `RunCreate.command`; destinos do texto (ADR-044) saem dele
  contexto?: string; criterios_sucesso?: string[];
  alvos: { instance_ids?: string[]; profile_ids?: string[]; targets?: RunTarget[];
           device_policy?: 'one' | 'primary' | 'all'; distribute?: DistributeSpec };   // as mesmas regras de POST /api/runs
  autonomia?: Autonomia;                    // padrão 'observar'
  fuso?: string;                            // padrão 'America/Sao_Paulo'
  gatilhos: { tipo: TipoDeGatilho; spec: GatilhoSpec }[];     // 1 a 8 (limite proposto)
  inicio_em?: string; fim_em?: string; max_ocorrencias?: number;
  orcamento_total_usd?: number; orcamento_ocorrencia_usd?: number;
  sobreposicao?: Sobreposicao; janela_recuperacao_s?: number; coalescer?: boolean;
  max_tentativas?: number; pausa_por_falha?: number;
}
```

**`POST /api/pedidos/previa`** devolve sempre `200` quando o corpo é bem formado, com o que a criação decidiria:

```ts
interface PedidoPrevia {
  valido: boolean;                          // false se há ao menos um bloqueio
  objetivo_sem_destinos: string;            // `command_sem_destinos` do resolvedor de alvos
  alvos: { targets: ResolvedTargetDTO[]; questions: Question[]; command_sem_destinos: string | null;
           warnings: string[] };            // o mesmo resolvedor de POST /api/runs/targets/resolve
  proximas: ProximaData[];                  // padrão 5 (`proximas`: 1..50), no fuso escolhido; ordenadas por `utc`
  intervalo_minimo_s: number | null;        // o menor intervalo entre as próximas 20 datas; base do piso
  autonomia: { teto: Autonomia; exige_aprovacao: string[]; recusado: string[] };   // capacidades, do §6.4 e de POLICIES
  custo: { base: 'mediana_das_ultimas_5' | 'teto_por_ocorrencia' | 'sem_base';
           por_ocorrencia_usd: number | null; ocorrencias_por_mes: number | null; por_mes_usd: number | null };
  bloqueios: { codigo: string; campo?: string; mensagem: string }[];   // os MESMOS códigos que a criação devolveria
  alertas: { codigo: string; mensagem: string }[];                      // não impedem (ex.: persona sem sessão pronta)
  confirmacao: string | null;               // selo opaco 'sha256:…'; null quando `valido` é false
}
```

- **Prévia nunca recusa por regra de negócio**: devolve `bloqueios[]`. Só corpo malformado (422) e `credencial_no_comando`
  (409, nada é processado nem ecoado) viram erro HTTP.
- **Sem IA e sem custo.** A escolha automática de personas (ADR-050) continua sendo `POST /api/runs/targets/suggest`; o
  painel chama essa rota antes e passa os `targets` que a pessoa aceitou. Refinar o texto é `POST /api/commands/refine`
  (ADR-047). O custo estimado **não inventa número**: sem histórico nem teto por ocorrência, `base: 'sem_base'` e os
  valores vêm `null` (para um pedido que já tem ocorrências, a base é a mediana das últimas 5).
- **`confirmacao`** é o selo do que a pessoa viu: o resumo SHA-256 da forma canônica de `objetivo_sem_destinos`, `targets`
  resolvidos, `device_policy`, `autonomia`, `fuso`, gatilhos (a `rrule` na forma de `Regra.para_texto`), `inicio_em`,
  `fim_em`, `max_ocorrencias`, os dois orçamentos, `sobreposicao`, `janela_recuperacao_s`, `coalescer`, `max_tentativas` e
  `pausa_por_falha`. Não entram `titulo`, `contexto` nem `criterios_sucesso`. O contrato fixa só que ele é opaco, estável
  para o mesmo conteúdo e diferente se qualquer campo acima mudar; as datas e o custo, que dependem do relógio, não entram.
  A prévia não grava nada, então o selo não tem validade nem estado no servidor.
- **Piso de frequência** (§10): `observar` e `preparar` ≥ 15 min; `agir` (efeito externo) ≥ 1 h. Abaixo, o bloqueio
  `frequencia_abaixo_do_piso` (`piso_s`, `observado_s` em `mensagem`); os dois valores são configuração, com estes padrões.

**`POST /api/pedidos`** — `PedidoCorpo` mais:

| Campo | Regra |
|---|---|
| `idempotency_key` | 8–100 caracteres de `[A-Za-z0-9_.:-]`, como `RunCreate`; obrigatório |
| `titulo` | 1–120; sem ele, os primeiros 80 caracteres de `objetivo_sem_destinos` |
| `confirmacao` | o selo da prévia. **Com ele**, o pedido nasce `rascunho` e passa a `ativo` na MESMA transação (`rascunho → ativo` pela pessoa, `estados.py:70`); **sem ele**, fica `rascunho` e a pessoa ativa depois por `POST /{id}/ativar` |

- A criação recalcula tudo o que a prévia calculou. Selo diferente → `409 previa_desatualizada`; alvos que o servidor
  resolve hoje diferentes dos `targets` ecoados → `409 alvos_nao_confirmados` (o código que já existe, adendo v0.29).
- **Idempotência.** A 067 não tem coluna de chave de idempotência; a proposta é que o `id` do pedido seja determinístico:
  `"ped_" + 24 primeiros caracteres hexadecimais do SHA-256 da idempotency_key` (28 caracteres, o máximo de `chave.py:41`).
  Repetir a chamada com o mesmo conteúdo devolve o MESMO pedido, `200` com `deduplicated: true`; a mesma chave com conteúdo
  diferente é `409 idempotency_conflict`. Alternativa descartada por ora: coluna própria (exige migração nova).
- `criado_por` é o operador da sessão; um `criado_por` no corpo é 422. `proxima_em` é calculada e gravada na ativação, e a
  criação acorda o laço de pedidos (`wake()`, §7.2).
- Pedido com `alvos` que não resolvem a nenhum aparelho: `400 sem_alvo` (o código que a prévia por persona já usa).

### Leitura

**`GET /api/pedidos`** — os nomes dos filtros são os do link da tela (`#/pedidos?estado=ativo,pausado&q=preço`), para o
link e a chamada serem a mesma coisa (ADR-062, item 4).

| Query | Significado |
|---|---|
| `estado` | um ou mais dos sete, separados por vírgula |
| `autonomia`, `tipo` | `Autonomia`; `TipoDeGatilho` (qualquer gatilho do pedido) |
| `profile_id` | pedidos que têm essa persona em `alvos` |
| `q` | texto em `titulo` e `objetivo` (≤ 80 caracteres) |
| `pede_atencao=1` | `pausado`, `aguardando_pessoa`, ou com aviso não lido |
| `ordem` | `atualizado` (padrão), `proxima` (a de `proxima_em` mais cedo primeiro, sem data por último) ou `criado` |
| `limit`, `cursor` | 1–200 (padrão 50); cursor opaco devolvido em `proximo_cursor` |

`total_por_estado` conta TODOS os pedidos por estado, ignorando `estado` e `cursor` (os chips da tela), no mesmo espírito
do snapshot completo da ADR-062. Estado fora dos sete → `422`.

**`GET /api/pedidos/{id}/ocorrencias`** pagina pelo tempo, do mais novo ao mais antigo: `antes_de` é um `previsto_para`
canônico, e `proximo` é o valor para a página seguinte (`null` no fim). `limit` 1–500 (padrão 50). Uma `pulada` ou `perdida`
traz sempre o `motivo` (nada some em silêncio, `estados.py:137`). A ocorrência sobrevive à purga da execução: com `run_id`
preenchido e `run_disponivel: false`, a tela mostra o resumo e o custo gravados e não oferece o link.

### Edição (`PATCH /api/pedidos/{id}`)

```ts
interface PedidoEdicao extends Partial<PedidoCorpo> {
  versao: number;                           // a versão que a pessoa viu: compare-and-set
  titulo?: string;
  dry_run?: boolean;                        // true: calcula e devolve, não grava
  confirmacao?: string;                     // exigida quando a edição muda um campo que entra no selo
}
interface PedidoEdicaoResultado {
  aplicado: boolean;                        // false no dry_run
  pedido: PedidoView;                       // com a versão nova se aplicado; senão como ficaria
  mudancas: { campo: string; de: unknown; para: unknown }[];
  proximas_antes: ProximaData[]; proximas_depois: ProximaData[];
  ocorrencias_refeitas: number;             // `prevista`/`devida` da versão anterior, canceladas e refeitas (§7.9)
  custo: PedidoPrevia['custo'];
  confirmacao: string | null;               // o selo do estado resultante
}
```

- `versao` diferente da atual → `409 versao_desatualizada` (`versao_atual` em `details`). Aplicar sobe a versão em 1.
- As ocorrências `prevista` e `devida` da versão anterior viram `cancelada` (motivo "pedido editado") e são refeitas na
  versão nova; as `despachada` em diante **terminam na versão em que nasceram** (§7.9). Nenhuma execução é cancelada por
  edição.
- Editável em `rascunho`, `ativo`, `pausado` e `aguardando_pessoa`; nos três terminais, `409 invalid_state`. `estado`, `id`,
  `pai_id`, `proxima_em`, `criado_*` e `versao` não se editam por aqui. O fuso muda só em `rascunho` ou `pausado`
  (mudar fuso com o pedido correndo desloca todas as datas).
- Mudar alvo, autonomia, fuso, gatilho, limite ou orçamento muda o selo: a edição real exige a `confirmacao` que o
  `dry_run` devolveu (senão `409 previa_nao_confirmada`). Editar só `titulo`, `contexto` ou `criterios_sucesso` não exige.
- Recusas da criação valem aqui (recorrência inválida, piso, sobreposição, credencial no texto).

### Ações e estados

Quem age é sempre a **pessoa** (`ator = pessoa`, de `estados.py`); as arestas do sistema (pausa automática, `concluido`,
`encerrado`, `aguardando_pessoa`) não têm rota. A API confere a aresta com `transicionar_pedido` e escreve com
`UPDATE … WHERE estado = <de>` (o CAS que fecha a corrida com o laço).

| Ação | De → para | O que mais faz |
|---|---|---|
| `ativar {confirmacao}` | `rascunho` → `ativo` | calcula `proxima_em`; acorda o laço |
| `pausar {motivo?}` | `ativo` → `pausado` | `motivo` 1–200 caracteres, padrão "Pausado pela pessoa", sempre gravado em `pausado_motivo`; para de materializar; **não cancela** execução em curso |
| `retomar {modo}` | `pausado` → `ativo` | `daqui` (padrão): o que venceu durante a pausa vira `pulada` com motivo, e o pedido segue da próxima data; `recuperar`: o que está dentro da janela vira `devida` (origem `recuperacao`, mesma chave) e o resto vira `pulada`; devolve as duas contagens |
| `retomar` | `aguardando_pessoa` → `ativo` | sem `modo` (com ele, 422). Exige que as pendências estejam resolvidas: senão `409 pendencia_aberta`, com `pendencias` em `details` |
| `cancelar {confirmar: true}` | `rascunho`, `ativo`, `pausado` ou `aguardando_pessoa` → `cancelado` | ver abaixo |
| `executar {solicitado_em}` | só `ativo` | cria uma ocorrência `manual` já `devida`; segue a sobreposição do pedido |
| `backfill {de, ate}` | só `ativo`, só `observar` | cria ocorrências `backfill` dos instantes passados da agenda |

- **Repetir é seguro, por estado.** `pausar` num pedido já `pausado`, `retomar` num `ativo`, `ativar` num `ativo` e `cancelar`
  num `cancelado` respondem `200` com `sem_mudanca: true` e não gravam nem emitem evento (a tabela não tem aresta de um
  estado para ele mesmo, `estados.py:17`; a camada da API se adianta). Uma ação cuja aresta não existe — `pausar` num
  `aguardando_pessoa`, `retomar` num `concluido`, qualquer ação num terminal — é `409 invalid_state`, com `estado` e
  `acoes_permitidas` em `details`. Não há "reabrir": nenhum terminal tem saída.
- **Cancelar é pedir, não desfazer** (o princípio do adendo v0.7). Sem `confirmar: true`, a chamada responde
  `409 confirmacao_necessaria` com `execucoes_em_curso` e `ocorrencias_futuras` em `details` e não muda nada: é o que a tela
  mostra antes de confirmar. Confirmado, o pedido vira `cancelado` no ato (nenhuma ocorrência nasce depois do CAS); as
  ocorrências `prevista`/`devida` viram `cancelada`; para cada execução em curso sai `RunService.cancel`. O desfecho de
  cada uma é o REAL: ocorrência `cancelada` se a execução fechou `cancelled`; `incerta` se `uncertain`; um desfecho que
  chega no meio ganha do pedido. `execucoes_em_curso[].entregue: false` não é erro (o pedido ficou registrado).
- **`executar`.** Aceito não é feito: `202` com a ocorrência `devida`; o laço a despacha em até um ciclo (`pedidos.tick_s`) e
  a execução aparece depois. `solicitado_em` é o instante do gesto, no formato canônico: a identidade é a chave
  `ped:<id>:-:<instante>:manual` (§6.3), então repetir a MESMA chamada devolve a mesma ocorrência (`deduplicated: true`)
  e um segundo clique, em outro segundo, é outra ocorrência. Um `solicitado_em` a mais de 120 s do relógio do servidor é
  `422 solicitado_em_invalido`. A ocorrência pode nascer `pulada` por sobreposição, com o motivo à vista.
- **`backfill`.** Só `observar`: com `preparar` ou `agir`, `409 backfill_so_observar`. Com `dry_run: true` devolve
  `{ocorrencias, primeira, ultima, custo, ja_existentes}`; para gravar, `confirmar_ocorrencias` repete o número da
  prévia (senão `409 previa_desatualizada`). Teto por chamada de ocorrências: configuração (padrão proposto 100); acima,
  `422 backfill_grande_demais`. A chave leva o instante do slot e a origem, então repetir o gesto não duplica
  (`ja_existentes` conta os que já estavam).
- **Responder a uma pendência** não ganha rota nova: aprovação segue as rotas de aprovação, pergunta segue
  `POST /api/runs/{id}/successor` (ADR-047) e ocorrência `incerta` segue a resolução de objetivo; depois, `retomar` devolve
  o pedido a `ativo` (a aresta `aguardando_pessoa → ativo` é da pessoa e não é automática em `estados.py:79`).

### Avisos e Pendências

[ADR-062](decisoes.md#adr-062--pendência-tem-dona-a-caixa-e-a-url-é-a-fonte-da-verdade-da-tela) reserva a palavra
"pendência" ao que depende de uma pessoa e dá a ela uma dona, a caixa de Pendências. A proposta separa:

| | Caixa de **Pendências** (existe) | Caixa de **avisos** (nova) |
|---|---|---|
| O que é | decisão que só uma pessoa toma | notificação informativa do que o pedido fez |
| Itens de pedido | pedido `aguardando_pessoa` (aprovação, pergunta, ocorrência `incerta`) | `pausa_automatica`, `orcamento_80`, `orcamento_esgotado`, `ocorrencia_perdida`, `relatorio_pronto`, `encerramento` |
| Contador | o total da caixa (menu, topo, semáforo) | "avisos não lidos", com nome próprio; **nunca se chama pendência** |
| Sai quando | a pessoa decide | a pessoa marca como lido |

- O **snapshot** ganha `pedidos: {por_estado: Record<EstadoPedido, number>, avisos_nao_lidos: number, aguardando_pessoa:
  PedidoView[]}`, completo desde a primeira carga e sem janela (a regra 2 da ADR-062). Os `aguardando_pessoa` alimentam a
  caixa de Pendências como uma origem nova. **Para não contar duas vezes** a decisão que já tem item próprio (a aprovação
  e a execução `needs_input`), o item do pedido é o que conta, e os itens daquele pedido aparecem agrupados sob ele
  (`pedido_id`) sem contar de novo. Isso muda a definição de Pendência e **pede emenda à ADR-062**, confirmada pelo dono em 02/10 (decisão 1).
```ts
type AvisoTipo = 'pausa_automatica' | 'orcamento_80' | 'orcamento_esgotado' | 'ocorrencia_perdida'
               | 'relatorio_pronto' | 'encerramento'                         // informativos: vão para a caixa de avisos
               | 'aprovacao_pendente' | 'pergunta' | 'ocorrencia_incerta';  // requer_pessoa: vão para as Pendências
interface AvisoDTO {
  id: string; pedido_id: string; pedido_titulo: string; ocorrencia_id: string | null;
  tipo: AvisoTipo; nivel: 'info' | 'warn' | 'error'; mensagem: string; dados: Record<string, unknown>;
  requer_pessoa: boolean; criado_em: string; lido_em: string | null;
}
```

- **Avisos** são linhas da tabela `pedido_avisos` (a 067 não a tem: **migração própria na implementação**, número a conferir
  em todos os branches): `id`, `pedido_id`, `ocorrencia_id?`, `tipo`, `nivel` (`info` | `warn` | `error`), `mensagem` em
  português, `dados` (JSON), `requer_pessoa` (booleano), `chave_dedupe` UNIQUE (`orcamento_80` sai uma vez por pedido até o
  orçamento subir), `criado_em`, `lido_em`. Nunca guardam segredo nem texto de terceiros.
- `GET /api/pedidos/avisos?lido=0` alimenta a caixa; `requer_pessoa=0` é o filtro do painel (o aviso de aprovação,
  pergunta ou incerteza existe para o canal de fora e não repete na caixa de avisos). `POST /api/pedidos/avisos/ler` marca
  lido e é idempotente.
- **Ponto de extensão do 28.11** (aviso fora do painel; o dono escolheu o **Telegram** em 02/10, com token e chat_id só
  pelo cofre/.env): o canal externo assina o evento `pedido.aviso`, que sai para TODOS os tipos, `requer_pessoa` ou não. O
  28.9 não envia nada para fora do painel.

### Eventos (WebSocket `/api/ws`, `EventRecord.kind`)

Persistidos, fora de `EPHEMERAL_KINDS`; o cliente refaz o snapshot no `resync`, como em todos os eventos.

| kind | data | persistido |
|---|---|---|
| `pedido.updated` | `{pedido: PedidoView}` (sem `proximas` nem `gatilhos`) | sim |
| `pedido.ocorrencia.updated` | `{ocorrencia: OcorrenciaDTO}` | sim |
| `pedido.aviso` | `{aviso: AvisoDTO}` | sim |

Nível: `pedido.updated` é `warn` quando o estado vira `pausado` pelo sistema ou `aguardando_pessoa`, `info` no resto;
`pedido.ocorrencia.updated` é `error` em `falhou`, `warn` em `incerta`, `perdida` e `pulada`, `info` no resto;
`pedido.aviso` herda `nivel`. `message` traz sempre texto legível em português.

### Erros

Corpo `{detail: {code, message, ...}}`. Os códigos marcados com ● já existem e mantêm o significado.

| Status | `code` | Quando |
|---|---|---|
| 404 | `not_found` ● | pedido, ocorrência ou aviso inexistente (`details.kind`) |
| 409 | `credencial_no_comando` ● | o `objetivo` tem formato de segredo; nada é gravado nem ecoado |
| 409 | `alvos_nao_confirmados` ● | os alvos de hoje diferem dos ecoados |
| 409 | `invalid_state` ● | ação sem aresta no estado atual, edição em terminal, `executar`/`backfill` fora de `ativo` (`estado`, `acoes_permitidas`) |
| 409 | `versao_desatualizada` | `PATCH` com `versao` velha (`versao_atual`) |
| 409 | `previa_desatualizada` · `previa_nao_confirmada` | selo diferente do recalculado; edição que muda o selo sem `confirmacao` |
| 409 | `idempotency_conflict` | mesma `idempotency_key`, conteúdo diferente |
| 409 | `pendencia_aberta` | `retomar` de `aguardando_pessoa` com pendência (`pendencias`) |
| 409 | `confirmacao_necessaria` | `cancelar` sem `confirmar: true` (`execucoes_em_curso`, `ocorrencias_futuras`) |
| 409 | `backfill_so_observar` | `backfill` em `preparar` ou `agir` |
| 400 | `sem_alvo` ● | os alvos não resolvem a nenhum aparelho |
| 422 | `recorrencia_invalida` | `ErroRecorrencia`: regra fora do subconjunto, ou início inválido (a mensagem é para a pessoa) |
| 422 | `fuso_desconhecido` | nome fora da base IANA |
| 422 | `gatilho_nao_suportado` | `evento`, `condicao` ou `persona` antes do 28.8 |
| 422 | `frequencia_abaixo_do_piso` | intervalo menor que o piso da autonomia |
| 422 | `sobreposicao_incompativel` | `agir` só com `pular`; `permitir_todas` só com `observar` (`estados.py:37`–`:38`, §7.4) |
| 422 | `limite_invalido` | `fim_em` ≤ `inicio_em`; orçamento por ocorrência maior que o total; `max_ocorrencias` ≤ 0; os CHECKs da 067 |
| 422 | `solicitado_em_invalido` · `backfill_grande_demais` | ver `executar` e `backfill` |

Em `previa`, todos os 422 acima de regra (não de forma) viram `bloqueios[]`. O resto dos 422 é o de corpo inválido
(`extra="forbid"`, tipos), igual ao das outras rotas.

### Permissões

Nada novo. As rotas que alteram estado passam pelo mesmo portão do adendo v0.9: `Origin` fora de `allowed_origins` é
`403 forbidden_origin`; fora do loopback, `Authorization: Bearer` ou o cookie de sessão; `public_hosts` como sempre.
A identidade na trilha é o operador da sessão (`criado_por`, e o autor de cada ação nos eventos); o nome que vier no corpo
nunca vence. A prévia não grava, mas passa pelo mesmo portão (ela resolve alvos e lê o parque).

### Lacunas do modelo 067 e dependências

| Lacuna | Proposta neste adendo | Quem fecha |
|---|---|---|
| sem coluna de chave de idempotência | `id` determinístico a partir da chave | 28.9 (ou migração, se o dono preferir) |
| sem tabela de avisos | `pedido_avisos`, migração própria | 28.9 |
| `aguardando_pessoa` não guarda a causa | `pendencias[]` lido do estado vivo (aprovações, execuções `needs_input`, ocorrências `incerta`) | 28.9 |
| `pedido_memoria`, `pedido_observacoes`, `pedido_relatorios` | rotas declaradas; campos `null` até existirem | 28.7 |
| `spec` de `evento`, `condicao` e `persona` | `422 gatilho_nao_suportado` | 28.8 |
| `pai_id` e sub-pedidos | só leitura; o corpo o recusa | 28.10 |
| re-resolução dos alvos a cada ocorrência (a persona pode ter mudado de aparelho) | fora deste contrato | 28.4 |
| `estados.py`: `aguardando_pessoa` não vai a `pausado`, e `fim_em` que passa nessa espera não encerra (linhas 20–24) | a API não inventa a aresta; `pausar` ali é `409 invalid_state` | 28.4 e 28.5 |

### Decisões tomadas (02/10/2026)

O coordenador decidiu todas com a recomendação desta proposta; o dono confirmou em chat as marcadas com **(dono)**.

1. **(dono) Emenda à ADR-062:** o pedido `aguardando_pessoa` é uma origem da caixa de Pendências, agrupando a aprovação e a
   execução `needs_input` dele para não contar em dobro (texto da emenda na própria ADR-062).
2. **Prévia como `POST /api/pedidos/previa`** (o texto do comando não vai em query string); o `GET` do §11 fica superado.
3. **Repetir ação de estado:** `200 sem_mudanca`.
4. **Retomar de `aguardando_pessoa`:** pela mesma rota `retomar`, sem rota `responder` e sem retorno automático.
5. **Idempotência de criação:** `id` determinístico a partir da `idempotency_key`, sem migração, com o hash INTEIRO
   (`uuid5` ou `sha256`), nunca truncado a ponto de colidir.
6. **`backfill`:** a chave leva o instante de cada slot (repetir não duplica); o §6.3 do desenho é ajustado.
7. **Avisos:** os tipos do §11 mais `ocorrencia_perdida` e `encerramento`; `pausar` sem motivo usa "Pausado pela pessoa".
8. **(dono) Piso de frequência:** `observar` e `preparar` ≥ 15 min; `agir` ≥ 1 h.

### Aceite proposto e prova

Aceite do 28.9 (desenho §13): `typecheck`, testes e navegador contra o backend simulado a 1366 e a 375 px. Prova possível:
`simulated` (testes de contrato da API com pedido, gatilho e execução falsos; nomes dos arquivos definidos na implementação).
`real`: só no 28.12, com ocorrências ligadas a `runs` reais. **Hoje: `not_run` em todos os níveis** (este adendo é texto).

## Adendo v0.46 (02/10/2026) — Aprendizado vivo, item 30.5: ações permitidas calculadas no backend

Mudança aditiva na `Entrada` do livro (`GET /api/aprendizado`, `/pendentes`, `/revisar` e o `item` de
`GET /api/aprendizado/{kind}/{ref}`). Corpos em `backend/app/modules/learning/presentation/livro.py`; a regra mora em
`domain/livro.py` (`acoes_da_pessoa`, `por_que_o_sistema_nao_publica`), sobre `ciclo.TRANSICOES` e `permitido`.
O painel deixou de espelhar o `ciclo.py`: só traduz as chaves para texto.

- `acoes: [{to, rotulo, exige_motivo}]`: os passos que uma PESSOA pode dar agora, na ordem da tabela do ciclo.
  `to` é o estado de destino (o corpo de `POST …/status`); `rotulo` é a chave estável `validar | aprovar | rejeitar |
  aposentar | desligar | reativar`; `exige_motivo` é `true` (decidir deixa o motivo na trilha). Vazio em `habilidade` (rota
  própria das habilidades), em `memoria`, em item sem estado, em receita substituída (`deprecated` não volta) e sem o
  destino `deprecated` em fluxo (sai de circulação como `disabled`). O veto e o modo do tipo não travam a pessoa.
- `por_que_nao_publica: {codigo, espera_o_dono, detalhe} | null`: por que o sistema não publica sozinho. `codigo`:
  `habilidade`, `efeito_externo`, `texto_de_pessoa` (esperam o dono: `espera_o_dono=true`), `vetado` (`detalhe` = razão do
  veto) ou `modo_desligado`; `null` quando o sistema publica sozinho. Nesta entrega a rota só preenche os três
  primeiros; veto e modo precisam de leitura do repositório e do ajuste, e entram quando o serviço os expuser.

Prova `simulated`: `backend/tests/test_learning_acoes.py` percorre `TRANSICOES` × D1 × tipo × estado e confere que `acoes`
só tem transições válidas para `by=pessoa` e que nenhuma válida falta (salvo as exceções acima). `real`: `not_run`.

## Adendo v0.47 (02/10/2026) — Aprendizado vivo: visão por app e chave de app canônica (Fase 30, itens 30.1 e 30.2)

Compatível para trás: duas rotas novas, só leitura, sem tabela nova e sem cópia de conteúdo (composição de leitura sobre o
registro de apps, a tabela `apps` e o Livro); no Livro, `app` passa a ser o PACOTE também em fluxo e habilidade, e entra o
campo `app_ref`. Prova: `simulated` (`backend/tests/test_learning_apps.py`, registro de apps falso). `real` (leitura no
central depois do deploy): `not_run`.

**Chave canônica (30.2).** `GET /api/aprendizado` (e o item) mostram `app` = pacote. O `app_id` de fluxo e habilidade vira
pacote pela tabela `apps` e pelo registro de apps; sem principal, os apps exigidos valem só se derem um pacote único. O que
não resolve vai ao balde `app = "nao_resolvido"` (filtrável: `?app=nao_resolvido`), com o id cru em `app_ref` (`null` nas
demais linhas). A memória é da persona: `app = null`, nunca no balde.

**`GET /api/aprendizado/apps`** — a lista: registro de apps ∪ apps da loja ∪ pacotes com linha no Livro. Um app sem linha
aparece com zeros.

| Campo | Significado |
|---|---|
| `apps[]` | por app: `pacote`, `nome`, `existencia` (`declarado`, `loja` ou `so_aprendido`), `declarado` (`arquivos{app,catalogo,telas,sessao}`, `acoes`, `telas`, `login_gerenciado`; `null` fora do registro), `loja` (`nome`, `nav_hints`, `known_selectors`; `null` fora da tabela `apps`), `aprendido{total, contagem{tipo:{estado:n}}}`, `absorvido` (n; o que o repositório absorveu não entra no aprendido) e `uso{tipo:{camada:n}}` |
| `nao_resolvido` | o balde, no mesmo formato de um app (`existencia: null`) |
| `fora_do_eixo` | contagem por tipo e estado do que não tem eixo de app (memória, voz, preferência, lição sem app) |
| `modos` | os modos usados na camada: `receitas`, `fluxos`, `habilidades`, `licoes`, `telas` (`null` = não lido) |

**`GET /api/aprendizado/apps/{pacote}`** — o detalhe (também de `nao_resolvido`): `app` (o resumo acima), `declarado[]`
(`tipo` `app`/`catalogo`/`telas`/`sessao`/`loja`, `arquivo`, `presente`, `quantidade` de ações ou telas, `uso`), `aprendido[]`
e `absorvido[]` (cada linha é a do Livro mais `origem_na_visao`, `uso` e `absorvida_em`, o commit) e `modos`. `404 not_found`
se o pacote não está em nenhuma das três fontes. As duas rotas entram antes de `/{kind}/{ref}`.

**Camada de uso** (`uso.camada`, com `uso.porque`): `decide_sem_ia`, `vai_ao_prompt`, `classifica_tela`, `login_fora_da_ia`,
`pre_preenche`, `contexto_da_persona`, `medido_nao_usado`, `nao_medido`, `inerte` e `desconhecida` (o modo de que depende não
foi lido). Vale o modo GLOBAL (`ai.recipes`, `ai.flows`, `skills.enabled`, `aprendizado.licoes.modo`, `aprendizado.telas.modo`);
o modo por app é o item 30.20.

## Adendo v0.48 (02/10/2026) — Papel de IA `persona` (item 17.8)

- `GET /api/ai`: `roles` ganha uma linha `role: "persona"` (a ordem é a de `AI_ROLES`: plan, decide, verify, escalation, social, persona) e `models.persona`. Sem `ai.roles.persona` a linha é idêntica à do `social`.
- `GET /api/usage`: as chamadas de geração e enriquecimento de persona passam a vir com `role="persona"` (antes `social`); consumidores que filtravam por `social` para somar custo de persona devem somar os dois.
- Sem mudança de rota, corpo ou código de erro; `POST /api/personas/generate` e `.../enrich` seguem pagos e sob o teto do dia.
