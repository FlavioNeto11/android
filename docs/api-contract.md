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
type InstanceState = 'absent' | 'stopped' | 'hibernated' | 'booting' | 'online' | 'stopping' | 'error';   // `hibernated` desde o Adendo v0.2
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
  capture_yield_to_tree_s: number;   // 31.34: segundos em que a grade cede a vez à leitura da árvore (0 desliga)
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
| `GET /api/usage` (v0.75) | – | `UsageReport` ganha `by_origin`, `escalations`, `rejudges`, `cascades`, `image_reasons` e `steps_driven_by_null` (RA-10, migração 080; adendo v0.75) |
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
| `POST /api/instances/{id}/input` | `ManualInput` | `{ok:true}`; `409 {code:'stale_frame'|'frame_mismatch'|'capture_failing'|'not_controller'}` (a tecla não confere o quadro: v1.52) |
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
| `instance.progress` | `{instance: Instance}`, o mesmo DTO; sai quando só mudou telemetria ou o controle já anunciado por `control.changed` (14.13) | não |
| `frame` | `{instance_id, frame: FrameInfo}` | não |
| `metrics` | `{metrics: Metrics}` | não |
| `health.updated` | `{health: Health}` | não |
| `run.updated` | `{run: RunSummary}` | sim |
| `objective.updated` | `{objective: Objective, failure_kind?: string \| null}` (`failure_kind` na entrada em `waiting_user` pela etapa, desde a v1.47) | sim |
| `step.updated` | `{step: Step, failure_kind?: string \| null}` (`failure_kind` desde a v1.47) | sim |
| `attempt.updated` | `{attempt: Attempt}` (sem `actions`) | sim |
| `action.logged` | `{action: Action, instance_id, step_id}` | sim |
| `evidence.added` | `{evidence: Evidence}` | sim |
| `plan.revised` | `{objective_id, version, reason}` | sim |
| `control.changed` | `{instance_id, control, pending}` | sim |
| `decision` | `{text}` | sim |
| `pendencia.vence_em` | 31.50, uma vez por item, 2 h antes de vencer: `{o_que: aprovacao\|objetivo\|execucao, run_id, objective_id?, aparelho? \| aparelhos?, acao?, etapa?, vence_em, acontece_se_vencer, chave: "vencimento:lembrete:<id>", regra: "31.50"}`; nunca o comando nem o título. `vence_em` (ISO, nulo fora da espera) também vem em `RunSummary` (`needs_input`), `Objective` (parado) e na lista de aprovações pendentes | sim |
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
  status: 'active' | 'disabled'; uses: number; created_at: string; last_used_at: string | null;
  required_apps: string[] /* adendo (29.42, 03/10): ids dos apps, na ordem em que o plano os usa */ }
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
| `GET /api/flows` | `?nascido_de_prova=true|false` (31.130, v1.87) | `Flow[]` |
| `PUT /api/flows/{id}` | `{status:'active'|'disabled', motivo?}` (`motivo` 31.130, v1.87) | `Flow` |
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
metadados (`configured`, `status`, `failed_attempts`, `blocked_until`).

### Persona, memória, histórico e contexto

| Método | Corpo | Resposta |
|---|---|---|
| `GET/POST /api/personas` | `PersonaInput` | `Persona[]` / 201 `Persona` |
| `GET/PATCH/DELETE /api/personas/{id}` | `PersonaPatch` | `Persona` / 204 |
| `POST /api/personas/{id}/preview` | `{kind?, profile_id?, counterparty?, incoming}` | `SocialDraft` |
| `GET/POST /api/instagram/profiles/{id}/memory` | `MemoryInput` | `MemoryItem[]` / 201 `MemoryItem` |
| `DELETE /api/instagram/profiles/{id}/memory/{memoryId}` | – | 204 |
| `GET /api/instagram/profiles/{id}/interactions?counterparty=&thread_key=&limit=` | – | `SocialInteraction[]` |
| `POST /api/instagram/profiles/{id}/context` | `{counterparty?, thread_key?, content?}` (era `GET` com query até o 29.26) | `SocialContext` |
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
   (tabela `devices.manager.ESTADO_ALVO`) e, desde 10/10/2026, para `device.network` (`aplicar`, `conectar` e `verificar`),
   que a linha `device_network` comprova: fecha como `succeeded` só quando a rede está em `trafego_verificado` na MESMA
   revisão (`params.rev`) do comando, verificada depois de ele começar (`desfazer` e revisão trocada esperam a pessoa).
   A sonda é **assimétrica**: ver o aparelho no estado prometido prova o
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
  - **Por cliente (29.56).** A trava é de quem errou, não do processo. Pelo túnel o par é sempre `127.0.0.1`, e o
    cliente é o `CF-Connecting-IP` só quando valem juntos: par loopback, `server.tls_behind_proxy: true` e `Host`
    em `public_hosts` (`security.access.cliente_de`). Par da rede vale pelo próprio endereço; o acesso local (par e
    nome de loopback) nunca entra na trava.
  - **O Bearer conta (29.56).** Um `Authorization` errado em `/api/*` que termina em `401` é um chute do mesmo
    cliente; bloqueado, qualquer `/api/*` com `Authorization` responde `429 too_many_attempts`, mesmo com o token
    certo. A sessão por cookie segue valendo. `401` sem `Authorization` (painel antes do login) não conta.
  - O `429` leva `Retry-After` (segundos) e `detail.retry_after_s`. O `429` em HTML do limite de taxa da borda
    (Cloudflare) vira, no painel, `rate_limited` com "espere 10 segundos" quando falta o `Retry-After`.
- **Cabeçalhos de segurança (29.56):** toda resposta leva `X-Frame-Options: DENY`, `X-Content-Type-Options:
  nosniff` e `Referrer-Policy: same-origin`.

**Identidade na auditoria.** `commands.requested_by` e `pending_approvals.decided_by` passam a gravar o operador
da sessão, e **o nome do corpo da requisição não vence o da sessão** — `requested_by` no corpo era, até aqui, o
único "quem" que o banco guardava, e qualquer chamador escrevia ali o que quisesse. `panel` continua aparecendo
quando ninguém se identificou.

### O que ainda não existe

**Não há conta por pessoa.** Quem tem o `API_TOKEN` entra com o nome que quiser: a sessão dá **trilha**, não
controle de acesso por pessoa. Senha por usuário (hash, papéis, quem pode o quê) é decisão de quem cuida do
parque e continua fora do escopo.

## Adendo v0.9b — o central é um worker, capacidades declaradas e o log do emulador

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

1. **[Resolvido na higiene 12.3/B10, 10/10/2026: o segundo virou `v0.9b`; o de autenticação segue `v0.9`.]** **Dois adendos com o mesmo nome "v0.9"** — um em `## Adendo v0.9 — autenticação: a API deixa de depender só
   do loopback` (por volta da linha 881) e outro em `## Adendo v0.9b — o central é um worker, capacidades
   declaradas e o log do emulador` (por volta da linha 965). Não foram renumerados nesta revisão para não
   quebrar âncoras/links existentes; o pedido é para o próximo editor corrigir a numeração (um dos dois deveria
   ser v0.85 ou os adendos de v0.9 em diante precisam deslizar).
2. **[Resolvido na higiene 12.3/B10, 10/10/2026: o tipo do topo lista `hibernated`.]** **`InstanceState` do topo do arquivo não lista `hibernated`** — o bloco de tipos no início (seção "Tipos
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
| `POST /api/training/{session_id}/save` | `TrainingSaveBody {proposal?, profile_ids[], group_ids[], scope_on_proof?}` | fluxo salvo (`FlowStore.learn_from_plan` + escopo); 400 `pos_condicao_ja_vale` quando a `text_visible` ou a `element_present` de uma etapa já vale na tela em que ela começa (31.122, adendos v1.83 e v1.86, com `pos_condicoes_ja_valem`); etapa da proposta aceita `independente: bool` (31.127, adendo v1.85) |
| `POST /api/training/{session_id}/preview` | `TrainingSaveBody` | `{steps: [{key, title, recipe, reason, pacotes_aceitos}], warnings, pos_condicoes_ja_valem, code, message}`, sem gravar nada; o comando repetido vem em `code: duplicate_command` num 200 (v1.58, v1.86, v1.89, v1.91) |
| `POST /api/training/{session_id}/recipes` | – | `{session, flow_id, steps, created}`: refaz as receitas de uma sessão salva (v1.58) |
| `POST /api/training/{session_id}/discard` | – | `TrainingSession` (mesmo que `stop`, com `discard=true`) |
| `POST /api/training/from-run` | `TrainingDeFalhaBody {run_id, step_id, lease_id, intent?, app_id?, profile_id?}` | `TrainingSession` (201) com `origin {run_id, step_id, step_key, attempt_id, motivo}`: abre o ensino a partir de uma etapa que falhou (31.111 F1 e F2, adendo v1.75); 404 `step_not_found`, 409 `step_not_failed` e as recusas de `POST /instances/{id}/training` |
| `GET /api/runs/{run_id}/steps/{step_id}/ensino-sugerido` | — | `{intent, pergunta, rotulo, causa}` ou `null` (sem tentativa): o que "Ensinar a corrigir" pré-preenche antes da sessão, pelo diagnóstico do 31.111 F4; só leitura, sem IA (31.116, adendos v1.80 e v1.82: `causa` e a pergunta pelo estado da etapa); 404 `step_not_found`, 409 `step_not_failed` |
| `POST /api/training/{session_id}/undo` | `TrainingUndoBody {lease_id, seq?}` | `TrainingSession` com `undone: {seq, type}`: tira a última entrada da gravação viva (31.90-D, adendo v1.70) |

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

**Prévia de distribuição entre servidores (item 10.5)** — `POST /api/runs/distribution` com corpo `{count, app_id?, command?}` (era `GET …?count=&app_id=` até o 29.26)
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
| `politica.excecao_criada` | sim | `social/excecoes.py` — exceção de uso único à regra de uma conta por alvo criada pela rota (30.65) |
| `politica.excecao_usada` | sim | `social/excecoes.py` — o efeito da etapa presa saiu e a exceção foi gasta (30.65) |
| `politica.excecao_vencida` | sim | `social/excecoes.py` — a exceção venceu sem uso (30.65) |
| `politica.excecao_recusada` | sim | `social/excecoes.py` — o dono rejeitou o cartão da etapa presa e a exceção acabou (30.65) |
| `politica.excecao_revogada` | sim | `social/excecoes.py` — a exceção em aberto foi revogada pela rota (30.65) |
| `politica.excecao_sem_efeito` | sim | `social/excecoes.py` — reservada, o gesto terminou sem efeito; ela fecha e não volta a aberta (30.65) |
| `politica.excecao_incerta` | sim | `social/excecoes.py` — reservada, e a etapa terminou sem liquidação; o efeito pode ter saído e conta como usada (30.65) |
| `app_state.updated` | sim | `state.py` |
| `session.needs_person` | sim | — |
| `learning.needs_person` | sim | `modules/learning/application/espera.py::AvisadorDeEspera`, chamado por `LearningService` (`mudar_estado`, `propor`, `avisar_item`, `avisar_mudanca_nativa`) e pelos ouvintes das lojas de receita e fluxo (`infrastructure/ligar_nativos.py`) — um item do Livro de aprendizado entrou na espera do dono (faixa B ou C da política de risco) ou saiu dela; ver o adendo v0.49 |
| `learning.ensinado_rebaixado` | sim | `modules/learning/application/ensinado.py::AvisadorDoEnsinado`, chamado por `LearningService` (`avisar_mudanca_nativa` e `_mover_nativo`) — a receita ou o fluxo ensinado no modo treinamento saiu de uso por decisão do SISTEMA (quarentena, substituição, obsolescência) e outro ativo segura o lugar; 30.80 B |
| `learning.ensinado_sem_receita` | sim | o mesmo, quando nada ativo ficou no lugar (a etapa voltou para a IA); `warn`; 30.80 B; ver o adendo v1.61 |
| `learning.ensinado_espera_decisao` | sim | `ServicoDeValidacao` (a volta da validação), via `LearningService.avisar_espera_do_ensinado`. O fluxo ensinado que a prova automática não cobre espera a decisão de uma pessoa; `warn`; 30.81; ver o adendo v1.65 |
| `learning.ensinado_decidido` | sim | `LearningService` (`confirmar_que_fica`, `_mover_nativo`): uma pessoa decidiu o ensinado que esperava; `info`; 30.81; ver o adendo v1.65 |
| `aprendizado.curadoria_da_operacao` | sim | `state.py::_curadoria_da_operacao`, ao ouvir `operacao.encerrada` (só no líder da trava `curadoria`): `data` `{operacao_id, fatos_da_operacao: {operacao, app, nascidas: [ids], ja_no_livro, recusadas: {motivo: n}, vetadas, vencidas_no_livro: [ids]}}`; só ids e contagens; `info`; 31.217 |
| `training.input` | sim | `training/recorder.py` — cada entrada gravada numa sessão de treinamento |
| `training.input.undone` | sim | `training/recorder.py` (`desfazer_a_ultima`): a última entrada saiu da gravação viva; `data: {training_session_id, seq, type}`; 31.90-D |
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
- `image_omitted`: `sensitive`, `policy` ou `capture_failed` (adendo v1.55) quando `jpeg` é `null`. Omitir a imagem **não** é falha de captura (`capture_failed` é, e quem chamou a aceitou);
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

**C4. (revogado em 10/10/2026, ADR-089)** A plataforma não esconde tela de ninguém. Antes, a prévia nunca mostrava a
tela classificada como sensível (campo de senha, desafio, a VM-loja, a digitação de uma credencial), com `GET /frame`
em 404 `sensitive_screen`, marcador sem imagem e observação da IA sem `jpeg`. Agora toda tela tem imagem na prévia, na
rota, na observação da IA e na evidência. `FrameInfo.sensitive` deixou de existir; a classificação `UiTree.sensitive`
segue só para as DECISÕES da automação (não agir por receita numa tela de senha ou desafio, detectar conta travada).

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
interface FrameInfo { /* …campos de antes… */ }   // `sensitive` foi removido (ADR-089)
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

**`POST /api/flows/match`** com corpo `{"command"}` (era `GET …?command=` até o v0.58; ver o adendo v0.65) (`modules/learning/presentation/fluxos.py::flows_match`):

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

**`PUT /api/flows/{id}`** (`modules/learning/presentation/fluxos.py::update_flow`):

- `{status: "active"}` num fluxo adotado por uma skill que tem versão publicada responde **409**
  `{"detail": {"code": "flow_adopted", "message": …}}`, e o fluxo continua `disabled`.
  - Motivo: o mesmo comando ficaria vivo nos dois backends.
  - Voltar ao fluxo é desfazer a adoção (`SqlSkillRepository.release_flow`), que desabilita a versão na mesma
    transação.
  - A conferência é `SqlSkillRepository.published_adopter(flow_id)`.
- `{status: "disabled"}` continua aceito (200).
- Ordem das recusas: 404 `not_found`, 400 `invalid`, 409 `flow_adopted`.
- **`DELETE /api/flows/{id}`** (`modules/learning/presentation/fluxos.py::delete_flow`): fluxo adotado por uma skill, em qualquer estado dela, responde
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
- `POST /api/flows/match` responde `null` quando a resolução é pergunta, como para skill que não compila.
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

- `features.skills` é `skills.enabled`, lido a cada `GET /api/health` (`state.py`, `Health.features`). `features.ensino_v2` é
  `skills.ensino_v2_na_tela` (31.91 F1, padrão `false`): a TELA do ensino v2 (a revisão só para leitura e o "Corrigir etapa")
  só aparece com `skills` E `ensino_v2` ligados; as rotas não dependem dele.
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

**`POST /api/runs` com `mode: "plan"`** (`modules/execution/presentation/router.py::create_run`):

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
  ao provedor e à proveniência).
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
    `targets` e `escolhidas` vêm vazios. Desde 06/10 o orquestrador não preenche `alerta_conduta` (a regra de
    conteúdo do pedido vai para o serviço externo de autorização); o campo fica como ponto de recusa.
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
  - `log` com `data.reason = "disjuntor_de_conta"` (`{profile_id, related_profile_ids, run_ids}`) e com `data.reason =
    "login_parado"`.
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

**`PUT /api/flows/{id}` e `PUT /api/recipes/{id}` passam pelo livro** (`modules/learning/presentation/fluxos.py::update_flow`/`update_recipe` →
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
- O marcador da quarentena usa o @ da conta, ou o identificador de login quando ela não tem @.
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
- **Distribuição:** `POST /api/runs/distribution` (corpo; era `GET`) aceita `command` no lugar de `app_id` (422 `distribution_sem_alvo`
  sem os dois); a criação distribuída sem app recusa com 409 `distribution_sem_app`.
- **Saídas de etapa (C2):**
  - A etapa que lê declara `saidas` e as seguintes citam `{{saida:<nome>}}`. O planejador declara e cita; citar um
    nome que nenhuma etapa anterior lê vira `missing` com `field: "saida"`, e o plano sai sem etapas.
  - O despacho troca a referência pelo valor na linha da etapa antes da porta de política. A ferramenta `read_value`
    lê o valor do texto do elemento, e `step_done` sem a leitura é recusado.
  - senha e token nunca são saída.
  - Ação de catálogo também entrega valor (ADR-065): a capability declara `saidas` (nomes que pode entregar), a etapa
    de catálogo do plano entre apps leva `saidas` com um subconjunto delas, e o `PlanStep.saidas` gravado é o mesmo
    da etapa livre. Nome fora do declarado vira `missing` (`field` = a ação em minúsculas). Sem mudança de rota,
    de DTO ou de migração.
- **Relatório:** `per_instance[].values_read: [{name, value, value_kind, step_title, app, read_at}]` em
  `GET /api/runs/{id}/report`, e a seção "Valores lidos entre etapas" no markdown.
- **Origem do valor (item 12.5, ADR-070; adendo provisório):** cada item de `values_read` ganha `origem`
  (`arvore`|`visual`), `leitor` (`provedor/modelo`), `frame_sha256` e `evidence_id` (os três nulos quando
  `origem=arvore`). O markdown traz, para cada valor visual, a linha "lido da imagem; conferido às cegas por <leitor> no
  recorte da captura <sha8>". A ferramenta `read_value` ganha `source` (`tree` padrão | `visual`; `visual` exige `value`
  e só vale com `value_kind=text`). A ação `read_value` visual registra `{name, value_kind, chars, origem, frame_id,
  evidence_id, leitor}` e `args.value` fica `**OMITIDO**`; a recusa é uma ação `rejected` cujo `error` é só um código do
  vocabulário fechado (`desligado`, `elemento_com_texto`, `regiao_nao_declarada`, `arvore_truncada`, `tela_sensivel`,
  `fora_do_app`, `sem_ancora`, `captura_mudou`, `repetida`, `sem_leitor`, `leitor_falhou`, `ilegivel`, `truncado`,
  `nao_confere`, `triagem:<motivo>`; o vocabulário é fechado e inclui `tela_sensivel` e `leitor_falhou`, que o
  orquestrador também aceita). A triagem (senha, token) NÃO é erro de chamada: como no caminho da árvore, a ação fica
  `rejected` com `valor recusado pela triagem: <motivo>` e a etapa vai para `waiting_user`, sem nova tentativa do ator.
  O valor gravado é o do leitor (limpo); as saídas `origem=visual` não entram nas variáveis de receita; orçamento, prazo
  e crédito do leitor seguem o desfecho do ator e não viram `leitor_falhou`. `GET /api/ai` lista a função `leitura` em
  `roles` e `models` quando `ai.roles.leitura` está escrito, e o `notice` nomeia provedor, modelo e os apps que declaram
  a região. `GET /api/usage` agrupa as chamadas pelo `role` `leitura`, e `ai_calls.origem` é `leitura`. Sem rota nova;
  migração 078.

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

## Adendo v0.45 (02/10/2026) — Pedidos persistentes (Fase 28, item 28.9) — adendo, IMPLEMENTADO no backend (simulated)

**Estado deste adendo.** Implementado no backend do item 28.9 (branch `feat/28-9-rotas`): prova `simulated` em
`backend/tests/test_pedidos_api.py` (14 testes, TestClient, SQLite, laço de pedidos de verdade com relógio falso e o
planejamento desligado); `real`: `not_run`. O texto abaixo é o contrato original; **o que diverge ou ficou de fora está
na seção "Divergências da implementação" no fim deste adendo** e vale sobre o texto. A tela é de outro agente e segue
este contrato. Compatível para trás: só rotas novas e campos opcionais novos.

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
  proxima_prevista: ProximaData | null;     // (28.12) sem `proxima_em` e com agenda (ativo, pausado, aguardando_pessoa,
                                            // rascunho): a 1ª data CALCULADA pelos gatilhos; com `proxima_em`, null
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
  laco: { ligado: boolean };                // (28.12) o laço de pedidos roda nesta instalação (`pedidos.enabled`)
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
| `GET /api/pedidos` | filtros em query | `200 {items: PedidoView[], proximo_cursor, total_por_estado, laco: {ligado}}` |
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
| `q` | **não vai mais na query (29.26)**: com `q` na URL a rota responde 422 `busca_no_corpo`; o termo vai em `POST /api/pedidos/busca` (corpo `{q, …}`, ver o adendo do 29.26) |
| `pede_atencao=1` | `pausado`, `aguardando_pessoa`, ou com aviso não lido |
| `ordem` | `atualizado` (padrão), `proxima` (a de `proxima_em` mais cedo primeiro, sem data por último) ou `criado` |
| `limit`, `cursor` | 1–200 (padrão 50); cursor opaco devolvido em `proximo_cursor` |

`total_por_estado` conta TODOS os pedidos por estado, ignorando `estado` e `cursor` (os chips da tela), no mesmo espírito
do snapshot completo da ADR-062. Estado fora dos sete → `422`. `laco.ligado` (28.12) é `pedidos.enabled` desta instalação:
a tarefa do laço só sobe no boot com ele, e desligado nenhum gatilho dispara (a tela diz isso em vez de prometer a próxima
data).

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
  `POST /api/runs/{id}/successor` (ADR-047) e ocorrência `incerta` segue `POST /api/pedidos/{id}/ocorrencias/{oid}/resolver`
  (v1.15, item 28.21); depois, `retomar` devolve o pedido a `ativo` (a aresta `aguardando_pessoa → ativo` é da pessoa e não é automática em `estados.py:79`).

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
  criado_pelo_dono: boolean; de_lote: boolean;   // Adendo v1.31 (28.31 F2a)
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
| sem tabela de avisos | `pedido_avisos`, migração 072 | 28.9 (feito) |
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

### Divergências da implementação (28.9, vale sobre o texto acima)

Rotas e campos que **existem**: tudo da tabela de rotas, exceto o que está em "Fora do 28.9". Códigos e corpos como no
contrato, salvo o abaixo.

**Fora do 28.9 (não implementado; a rota não existe e responde 404/405):**
- `POST /{id}/executar` e `POST /{id}/backfill` (e os erros `solicitado_em_invalido`, `backfill_grande_demais`,
  `backfill_so_observar`); `acoes_permitidas` não as oferece.
- **Avisos (migração 072, `pedido_avisos`): implementados.** O texto anterior desta seção (sem tabela, `lido=` e `ler`
  fora do 28.9) está superado:
  - Todo `pedido.aviso` é GRAVADO em `pedido_avisos` com `chave_dedupe` (`INSERT ... ON CONFLICT DO NOTHING`) e só então
    emitido, e só se a linha é nova. É um caminho único (`CaixaDeAvisos.registrar`): o laço (`avisar` do `AppState`) e a API
    usam o mesmo, então o mesmo fato visto pelos dois é uma linha e um evento. O `id` do `AvisoDTO` é o da linha
    (`avs_` + 26 hex do sha256 da chave).
  - `GET /api/pedidos/avisos` lê da tabela: filtros `lido` (`0`/`1`/`true`/`false`), `requer_pessoa`, `pedido_id`, `limit`,
    `cursor` (deslocamento opaco; a ordem é `criado_em` decrescente). `nao_lidos` é real.
  - `POST /api/pedidos/avisos/ler` com `{ids?, todos?, pedido_id?}` (`pedido_id` é extensão deste adendo: restringe o
    `todos` a um pedido). Sem `ids` nem `todos`: 422; mais de 200 ids: 422; id inexistente: `404 not_found` com
    `details.kind="aviso"` e nada é gravado; `pedido_id` inexistente: `404` com `kind="pedido"`. Resposta
    `{lidos, nao_lidos}`; repetir devolve `lidos: 0` e não muda `lido_em`.
  - **Definição de `avisos_nao_lidos` e `nao_lidos`:** só os informativos (`requer_pessoa=0`). Os que pedem pessoa moram
    nas Pendências (ADR-062: não contar duas vezes). Por isso `todos` também marca só os informativos. Vale na
    lista e no detalhe (por pedido), no snapshot (`pedidos.avisos_nao_lidos`) e na resposta de `/avisos` (do pedido
    filtrado, ou de todos). O selo do menu do painel vem de `nao_lidos`.
  - `GET /api/pedidos?pede_atencao=1` cobre `pausado`, `aguardando_pessoa` e o pedido com aviso informativo não lido.
  - Chaves de deduplicação: `pausa_automatica:<pedido>:<atualizado_em da pausa>`, `encerramento:<pedido>`,
    `orcamento_esgotado:<pedido>`, `orcamento_80:<pedido>:<teto>` (o teto na chave: sobe o orçamento e a pessoa é avisada de
    novo), `ocorrencia_perdida:<ocorrência>`, `relatorio_pronto:<relatório>`, `<ocorrência>:ocorrencia_incerta`.
  - Emitidos hoje: `pausa_automatica`, `encerramento`, `ocorrencia_perdida`, `ocorrencia_incerta` (28.5),
    `orcamento_80` (laço, em `_conferir_orcamento`, com o gasto em [80%, 100%) do teto), `orcamento_esgotado` (quando o
    pedido encerra por orçamento ele SUBSTITUI o `encerramento`: uma notícia, um aviso) e `relatorio_pronto` (todo
    relatório gravado que não é `sob_demanda`; o evento sai depois do commit, pelas marcas do repositório). **Ainda não
    emitidos:** `aprovacao_pendente` e `pergunta` (o contrato os prevê; nenhum ponto do laço os decide hoje, as pendências
    seguem lidas do estado vivo).
  - Divergência da tela: o painel não envia `alvos.distribute` (o backend o recusa); "distribuir por contas" fica barrado
    no pedido persistente.
- Gatilhos do `PedidoCorpo`: `evento`, `condicao` e `persona` → `gatilho_nao_suportado` (28.8). `alvos.distribute` não é
  aceito (422 por `extra="forbid"`): a seleção é `instance_ids`, `profile_ids` e `targets`.
- `PATCH` troca a recorrência por UMA só (`gatilhos` com um item; mais de um → `limite_invalido`), porque o laço troca o
  gatilho ativo por um novo (D5).

**Diferenças de comportamento:**
- `horario`: o laço lê `spec.local`; a API aceita `local` e `dtstart` e grava sempre `local`. A `rrule` é gravada na forma
  canônica.
- `alvos` no banco usam o formato de `runs.targets` que o laço lê (`{"alvos": [...], "device_policy"}`); o DTO devolve a
  mesma foto com a chave `targets`, como o contrato.
- Id do pedido: `"ped_" + base64url(uuid5(chave))` sem preenchimento (26 caracteres, o `uuid5` inteiro): um SHA-256 de 64 hex
  não cabe nos 28 caracteres de `chave.py`. `idempotency_conflict` só é conferido enquanto `versao == 1`; depois de editado,
  repetir devolve o pedido atual.
- Bloqueios novos (além do contrato): `alvos_com_pergunta` (409), `unknown_instance` e `store_instance` (400), os mesmos de
  `RunService.create`.
- `inicio_em` é gravado e limita as datas da prévia, mas **o laço (28.4) ainda não o aplica** ao materializar: a prévia traz
  o alerta `inicio_em_nao_aplicado`.
- `ativar`: o gatilho passa a valer da ativação (`pedido_gatilhos.criado_em` é reposto), para um rascunho antigo não gerar um
  rastro de ocorrências atrasadas.
- `editar` segue o laço (D5/A6), não o texto do contrato: as `prevista`/`devida` passam à versão nova NA MESMA LINHA, e só a
  troca de gatilho as cancela e refaz; `ocorrencias_refeitas` conta as `prevista`/`devida` quando o gatilho muda e é 0 senão.
  O fuso só muda em `rascunho` ou `pausado` (senão `409 invalid_state`).
- `retomar` de `aguardando_pessoa` usa o modo `recuperar` por dentro (nada é pulado); de `pausado`, `puladas` é a contagem
  real e `recuperadas` vale sempre 0 (o laço decide ao rodar, aplicando janela e coalescência).
- `cancelar`: `execucoes_em_curso` traz só `run_id` (`entregue` não é conhecido: `RunService.cancel` roda dentro de
  `acoes.cancelar`).
- `pendencias` (aguardando_pessoa): `pergunta` = execução do pedido em `needs_input`; `aprovacao` = `pending_approvals`
  pendente de execução do pedido; `ocorrencia_incerta` = `incerta` ainda não resolvida pela pessoa (`resolvida_em` nulo, v1.15) e
  sem ocorrência posterior `concluida` (a regra antiga, que fica).
- A prévia devolve `autonomia.exige_aprovacao`/`recusado` pela TABELA do §6.4 do desenho (por grau), não pelas capacidades do
  plano, que só se conhecem depois de planejar (sem IA na prévia). `custo` fica `sem_base` sem histórico nem
  `orcamento_ocorrencia_usd`. Limite conhecido do orçamento: o excesso máximo é o custo de UMA ocorrência aberta, limitado
  pelo teto da execução.
- Eventos: o repositório anota as mudanças e quem escreveu as publica depois do commit; numa volta do laço sai UM
  `pedido.ocorrencia.updated` por ocorrência, com o estado em que ela ficou (a que nasce `devida` e é despachada na mesma
  volta só aparece `despachada`).
- Emenda à ADR-062 (já escrita): o snapshot traz `pedidos.aguardando_pessoa[]` com as `pendencias` de cada pedido; as `runs`
  do snapshot carregam `pedido_id`/`ocorrencia_id` para a caixa tirar do total os filhos agrupados. O agrupamento no painel
  é da tela.

### Aceite proposto e prova

Aceite do 28.9 (desenho §13): `typecheck`, testes e navegador contra o backend simulado a 1366 e a 375 px. Prova hoje: `simulated` no backend (`backend/tests/test_pedidos_api.py`, 14 testes); a tela e o navegador a 1366 e 375 px são do outro agente (`not_run` aqui); `real`: só no 28.12, com ocorrências ligadas a `runs` reais (`not_run`).

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

## Adendo v0.49 (02/10/2026) — Evento `learning.needs_person`: conhecimento aguardando a pessoa (item 30.21)

Evento persistido no barramento (`events`, fora de `EPHEMERAL_KINDS`), no padrão de `approval.pending` e `session.needs_person`.
Sem `instance_id`. `level`: `warn` ao entrar na faixa C, `info` nos demais. A `message` só tem identificadores e o vocabulário
fechado (também é persistida e transmitida).

**`data`** (lista fechada, `CAMPOS_DO_PAYLOAD`; um teste falha se aparecer outra chave):

| Campo | Conteúdo |
|---|---|
| `kind`, `ref` | o item (`receita` + `100`; `licao` + `li-…`) |
| `app` | pacote (`""` se o item não tem app) |
| `faixa` | `B` ou `C` (§8.4 de `design/aprendizado-vivo.md`) |
| `aguardando` | `true` ao entrar na espera; `false` ao sair |
| `motivo` | entrada: `efeito_externo`, `texto_de_pessoa`, `commit_sem_catalogo`, `alto_risco`, `sessao_ou_autenticacao`, `parecer_da_ia`; saída: `decidido_por_pessoa`, `rebaixado_pelo_sistema`, `substituido` |
| `href` | `#/aprendizado?aba=aprendido&item=<kind>:<ref>` (o detalhe exige a autenticação do painel) |
| `desde` | ISO UTC de quando entrou na espera (também no evento de saída) |

**Nunca** vai no evento: conteúdo de receita ou fluxo, seletor, texto digitado, parâmetro, texto de persona, nota nem conclusão de IA.

**Quando publica.** Na transição ou no nascimento que deixa o item na fila "Para aprovar" (`validated` com `requires_owner`;
candidata de origem humana) e em qualquer transição que o tira dela, incluindo a mudança que a própria loja de receitas ou de
fluxos faz. Idempotente por (`kind:ref`, `aguardando`): mover entre dois estados que não mudam a espera, ou repetir a mesma
mudança, não publica. Habilidade fica de fora (ciclo próprio, não passa pelo serviço do Livro). `parecer_da_ia` é publicado
pelo curador do 30.11 (adendo v0.55).

**Faixa (mínima, `domain/espera.py::classificar_espera`; o 30.10 a estende).** C: `risk=high`, `default_policy=manual_only`,
ação que envia texto escrito (`needs_draft`) ou item nascido de sessão desconhecida. B: efeito externo médio ou commit sem
catálogo no app, e origem humana sem efeito (D-2). O mapa de `interaction_type` para envio, publicação e exclusão e a
autenticação por conteúdo de tela ainda não são derivados.

**Ligação.** `montar_aprendizado(..., eventos=<EventBus>)`. Sem `eventos`, nada é publicado; `state.py` passa
`eventos=self.bus`. Prova `simulated` (`tests/test_learning_espera.py`).

## Adendo v0.50 (02/10/2026) — `GET /api/aprendizado/{kind}/{ref}`: campo `conteudo` (item 30.3)

O detalhe do Livro ganha `conteudo` (objeto, ou `null`), ao lado de `item`, `evidencias`, `trilha` e `exposicoes` (que não mudam). Só
leitura, montado do que já existe no banco (nenhuma migração). Sempre traz `tipo` (`receita`, `fluxo`, `habilidade`, `licao`, `tela`).
`null` em `memoria` (só a contagem sai), `voz` e `preferencia` (texto de pessoa).

**Regra do segredo.** Nunca sai valor de parâmetro nem texto digitado: o texto de uma receita é 100 % parâmetro e só os NOMES saem
(`{nome}`). A ação `type_secret` e o parâmetro sigiloso (`senha`, `token`, `código`...) saem só como `segredo: true`, sem nome; dentro
de um seletor o parâmetro sigiloso vira `{segredo}`.

**`receita`**

| Campo | Conteúdo |
|---|---|
| `identidade` | `app`, `app_version`, `assinatura`, `variante` (`null` se vazios), `step_key`, `step_hash`, `versao`, `estado` (o status nativo da receita) |
| `acoes[]` | `indice` (base 0), `ferramenta`, `commit` (bool), `alvo[]` (um por seletor, em ordem de confiança: `tipo` = `rid+text`, `rid+desc`, `rid`, `desc`, `text`, mais `rid`, `texto`, `desc` do que existir), `parametros[]` (nomes não sigilosos), `segredo` (bool); quando se aplicam: `digita` (`limpa_antes`, `enter`, `so_parametro`), `pacote`, `duracao_ms`, `coleta` (`seletor_do_item`, `exclusoes`), `rolagem` (`direcao`, `max`) |
| `efeito` | `externo` (o selo de `side_effect`) e `acoes_commit[]` (índices de `acoes[]` que fazem o commit) |
| `capability` | `null` se nenhuma fonte diz; senão `nomes[]`, `ambigua` (bool) e `fonte`: `origem` (a etapa em `learned_from_step`) ou `mesmo_step_hash` (etapas com o mesmo `template_hash` no app; mais de um nome: todos listados e `ambigua: true`). A receita não grava capability: é derivada |
| `origem` | `{tipo: "execucao", step_id, run_id}` (`run_id` `null` se a etapa não existe mais), `{tipo: "treino", ref}` (prefixo `training:`) ou `{tipo: "desconhecida"}` |
| `uso` | `replay_ok`, `replay_fail`, `consecutive_fail`, `last_used_at` |
| `sombra` | `shadow_agree`, `shadow_total` |
| `substitui`, `substituida_por` | `{id, versao, estado}` da versão vizinha da mesma chave (app, versão do app, assinatura, variante, `step_hash`), ou `null` |

**`fluxo`**: `nome`, `comando_modelo`, `origem` (`{tipo: "execucao"|"treino", fonte, source_run_id, session_id, run_id, step_id, attempt_id}`; os quatro ids, adendo v1.81, são a origem da correção ensinada e ficam `null` no fluxo que não veio de uma falha), `etapas[]` (`indice`, `chave`,
`capability`, `alvo` = `commit_selector`, `efeito`, `pos_condicao` = `{tipo, descricao}` ou `null`, `parametros[]` = nomes dos `bindings`,
`segredo`) e `efeito` (`externo`, `etapas_com_efeito[]`).

**`habilidade`**: `skill_id`, `versao`, `schema_version`, `estado`, `source_kind`, `source_ref`, `parent_version`, `command_template`,
`content_hash`, `parametros[]`, `nos[]` (`id`, `tipo`), `total_de_nos`, `rota` (`/api/skills/{id}/versions/{versao}`; o ciclo e a
edição continuam nas rotas das habilidades).

**`licao`**: `texto` (o `summary`, o que o ator lê e que `item.title` já mostra), `modelo`, `acao`, `alvo` (`{tipo, valor}`; `valor` de
`parametro` é o NOME), `escopo` (`app`, `capability`, `step_hash`, `role`), `tokens`. A nota crua da pessoa não sai além do `texto`.

**`tela`**: `tela`, `casa`, `autenticada`, `ids_todos[]`, `razao`.

Nenhum código de erro novo. Prova `simulated` (`tests/test_learning_conteudo.py`); `not_run` no central.

## Adendo v0.51 (02/10/2026) — `GET /api/aprendizado/{kind}/{ref}`: campo `versao` (item 30.6)

O detalhe do Livro ganha `versao` (objeto, sempre presente), ao lado de `conteudo` (v0.50). Só leitura, montado de `device_app_state` (as
versões do app vivas no parque) e das chaves de `recipes` (a receita não cruza versão do app). Nenhuma migração. Desenho: `design/aprendizado-vivo.md` §7.

Forma: `{estado, app, app_version, vivas[], nao_testada_em[], por_versao[]}`.

| Campo | Conteúdo |
|---|---|
| `estado` | o estado de versão DO ITEM, na versão dele: `independente`, `comprovado`, `nao_testado`, `em_prova`, `falhando`, `incompativel`, `superseded`, `versao_aposentada` (os do §7) ou `desconhecido` (o "não sei" explícito: sem pacote, sem versão ou sem nenhum aparelho observado; nunca um estado inventado) |
| `app`, `app_version` | o pacote e a versão do app do item; `null` onde não se aplica (`independente`) |
| `vivas[]` | `{versao, aparelhos}`: as versões do app observadas hoje em aparelho ativo (aparelho aposentado, app `missing` e versão vazia não contam), ordenadas pelo texto da versão |
| `nao_testada_em[]` | as versões vivas em que a chave da receita não tem receita nenhuma (`nao_testado`) |
| `por_versao[]` | uma linha por versão em que a chave tem receita OU que está viva: `{versao, viva, aparelhos, estado, receita_ref}`; `receita_ref` é a de maior versão da chave naquela versão do app, `null` em `nao_testado` |

**Por tipo.** `receita`: o quadro completo; a chave é (pacote, assinatura, variante, `step_hash`) em todas as versões do app. Regras: `superseded` se a
linha foi trocada; `versao_aposentada` se a versão dela não está viva (não é falha); quarentena = `incompativel` se uma versão anterior da chave
estava comprovada, senão `falhando`; `consecutive_fail > 0` = `falhando`; `active` com `replay_ok > 0` = `comprovado` (precisa de versão viva;
sem nenhuma observada vira `desconhecido`); `active` sem prova = `em_prova`. `tela`: `incompativel` (dias sem casar E versão nova no parque, a regra `sem_casar` das
telas), `versao_aposentada` ou `desconhecido`; `vivas` preenchido e `por_versao` vazio. `fluxo`, `habilidade`, `licao`, `voz`, `preferencia`,
`memoria` (e o declarado): `{estado: "independente", app: null, app_version: null, vivas: [], nao_testada_em: [], por_versao: []}`.

Comparação de versão por TEXTO exato (`recipes.app_version` × `device_app_state.observed_version_name`, ambas o `versionName` do aparelho; a
equivalência de formato segue "a conferir" no §7). Nenhum código de erro novo. Prova `simulated` (`tests/test_learning_versao.py`); `not_run` no central.

## Adendo v0.52 (02/10/2026) — `saude` na lista e no detalhe do Livro (item 30.4)

`GET /api/aprendizado` (cada elemento de `itens[]`), `GET /api/aprendizado/pendentes`, `GET /api/aprendizado/revisar` e
`GET /api/aprendizado/{kind}/{ref}` (em `item`) ganham o campo `saude`: objeto, ou `null` na memória (só a contagem sai). É UMA função
no backend (`domain/saude.py`, pura), a mesma na lista e no detalhe, então o rótulo nunca diverge. Só leitura: o rótulo não é estado,
não move nada e não dispara nada. Nenhuma migração, nenhum código de erro novo.

```json
{"rotulo": "degradando",
 "motivos": [{"codigo": "eficacia_abaixo_do_minimo", "dimensao": "eficacia", "valor": 0.6, "limite": 0.8, "detalhe": "10 usos"}],
 "dimensoes": [{"nome": "eficacia", "estado": "medida", "valor": 0.6, "amostra": 10, "fonte": "recipes.replay_ok+replay_fail"}]}
```

**`rotulo`** (vocabulário fechado; a ordem é a de avaliação, o primeiro que casa vence; regras e limiares da proposta D-5, medidos no banco
real, que valem sobre o §5.3 do desenho):

| `rotulo` | Regra |
|---|---|
| `inativo` | `deprecated`, `disabled` (receita `superseded`/`quarantined` já chegam assim); o detalhe leva o motivo da trilha em `motivos[0].detalhe` |
| `em_prova` | `candidate` ou `validated` (`aguarda_repeticao`, `aguarda_o_dono`, `validado_aguarda_publicacao`) |
| `degradando` | publicado e qualquer de: `consecutive_fail ≥ falhas_seguidas`; eficácia `< taxa_minima` com `usos ≥ amostra_minima`; evidência contra ou conflito nos últimos `contestacao_dias` |
| `sem_evidencia` | publicado há `≥ sem_uso_dias` e nunca usado |
| `parado` | já usado, sem uso há mais de `sem_uso_dias` |
| `pouca_amostra` | usado dentro de `sem_uso_dias` com menos de `amostra_minima` usos (inclui o publicado há pouco e ainda sem uso) |
| `saudavel` | usado dentro de `sem_uso_dias`, `usos ≥ amostra_minima`, eficácia `≥ taxa_minima`, sem contestação recente |
| `indeterminado` | publicado, mas falta a medida (contador de uso, data do último uso ou da criação, eficácia ou contestação desconhecidas); nunca vira `saudavel` por falta de dado |

**`motivos[]`**: um por fato que produziu o rótulo: `codigo` (vocabulário fechado de `CodigoDoMotivo`), `dimensao` (ou `null`), `valor`
medido, `limite` cruzado e `detalhe` (texto curto: janela, amostra, motivo da trilha). O texto em português é do painel.

**`dimensoes[]`** (sempre as sete, nesta ordem: `uso`, `eficacia`, `base_de_evidencia`, `frescor`, `versao`, `contestacao`,
`intervencao_humana`): `nome`, `estado` (`medida` | `desconhecida`), `valor` (`null` quando desconhecida, nunca 0), `amostra` e `fonte`
(de onde veio). A eficácia é a ACUMULADA `replay_ok/(ok+fail)` (receita) ou `a favor/(a favor+contra)` das evidências (fluxo, item);
não há janela por item. `versao` e `intervencao_humana` saem `desconhecida` por ora (versão: 30.6/30.13).

**Config** `aprendizado.saude` (defaults = limiares medidos; nada é gravado, mudar vale na próxima leitura): `sem_uso_dias: 14`,
`amostra_minima: 5`, `taxa_minima: 0.8`, `falhas_seguidas: 2`, `contestacao_dias: 7`.

Fora desta fatia: `obsoleto_provavel` (30.14, adendo v0.54); `acoes[]` já existe em `item` (§5.4) e não ganhou `motivo_de_bloqueio`. `falhas_seguidas` vem de
`recipes.consecutive_fail`. Limiares aprovados pelo dono em 02/10 (D-5). Prova
`simulated` (`tests/test_learning_saude.py`); `not_run` no central.

## Adendo v0.53 (02/10/2026) — `GET /api/aprendizado/{kind}/{ref}`: campo `relacoes` (item 30.7)

O detalhe do Livro ganha `relacoes` (lista, sempre presente, `[]` quando nada se deriva), ao lado de `conteudo` (v0.50) e `versao` (v0.51).
Só leitura, montada na leitura do que já existe; **sem tabela de arestas e sem migração**. Desenho: `design/aprendizado-vivo.md` §6.
(A v0.52 está reservada ao 30.4, a saúde.)

Forma de cada relação: `{tipo, kind, ref, rotulo, fonte}`. `kind` e `ref` apontam para o detalhe do alvo (`GET /api/aprendizado/{kind}/{ref}`),
salvo `absorvida`; `rotulo` é texto curto para exibir (pode ser `null`); `fonte` diz de onde a relação foi derivada. A lista vem ordenada por
`tipo` (na ordem da tabela) e depois por `kind` e `ref`.

| `tipo` | Onde aparece | De onde sai (`fonte`) | Alvo |
|---|---|---|---|
| `substitui` / `substituida_por` | receita | `recipes`: mesma chave (pacote, versão do app, assinatura, variante, `step_hash`), versão anterior e seguinte (a leitura do `conteudo.substitui`/`substituida_por`, v0.50) | `receita/<id>`; `rotulo` `v<n> (<status>)` |
| `substitui` / `substituida_por` | habilidade | `skill_versions.parent_version` (o pai) e as versões que a têm por pai | `habilidade/<skill>@<n>` |
| `substitui` / `substituida_por` | item (`tela`, `licao`, `voz`, `preferencia`) | `learning_items.parent_id` (o pai; os itens que o têm por pai). Pai que sumiu do banco não gera relação | `<kind>/<li-...>` |
| `derivado_de` | habilidade `flow:<id>@1` | o `skill_id` do fluxo legado | `fluxo/<id>` |
| `absorvida` | item | `state_detail = absorvida:<commit>`. Tela: o alvo é a regra declarada pelo nome (`kind` `regra_declarada`, `ref` o nome da tela); sem nome, `kind` `commit` e `ref` o commit | ver ao lado |
| `contradiz` | receita, tela | mesmo `scope_key`, `content_hash` diferente e os DOIS vivos (`candidate`, `validated`, `published`). Receita: duas vivas na mesma chave com caminho diferente (anomalia: a versão nova aposenta as vivas da chave). Tela: o `scope_key` é só o app, então exige o mesmo nome de regra (`conteudo.tela`) | `receita/<id>` ou `tela/<li-...>` |

**O que NÃO sai (e por quê).** `contradiz` não é derivado em `fluxo` (`flows.match_key` é único), `habilidade` (as versões da mesma habilidade
dividem o comando) nem `licao`, `voz` e `preferencia` (o `scope_key` não nomeia a proposição: acusaria toda lição do mesmo passo). A evidência
`conflict` da tela aponta para um aparelho, não para outro item: não vira relação. `nasceu de` (já exposto em `conteudo.origem`), `complementa / depende de`
e `revisado por` (IA, §8.5) ficam fora: o primeiro já existe, os outros dois não têm fonte hoje. `memoria` devolve `relacoes: []`.

Nenhum código de erro novo. Prova `simulated` (`tests/test_learning_relacoes.py`); `not_run` no central.

## Adendo v0.54 (02/10/2026) — rótulo `obsoleto_provavel` e rebaixamento `catalogo_sem_efeito` (item 30.14)

**`saude.rotulo`** (v0.52) ganha `obsoleto_provavel`, avaliado depois de `degradando` e antes de `sem_evidencia`: só para o **publicado**
(em prova, o rótulo continua `em_prova`). Só leitura (D-5): nada é desligado pela saúde. Os sinais vêm do §9.2 de
`design/aprendizado-vivo.md`, só os que têm fonte hoje; cada um é um `motivos[]` com o fato em `valor` e a **fonte em `detalhe`**
(o `Motivo` não tem campo `fonte`):

| `codigo` | Tipos | Fato (`valor`) e fonte (`detalhe`) |
|---|---|---|
| `substituta_viva` | receita, item com `parent_id` | `ref` da versão seguinte da mesma chave (receita) ou do item filho, em estado vivo; `recipes` mesma chave / `learning_items.parent_id` |
| `versao_fora_do_parque` | receita, tela | a versão do item, que nenhum aparelho ativo tem (`versao.estado = versao_aposentada`); `device_app_state` |
| `versao_viva_sem_reproducao` | receita, tela | as versões vivas em que a chave não tem receita e que não são provadamente mais antigas (tela: `incompativel`) |
| `efeito_sem_respaldo_no_catalogo` | receita, fluxo | a capability (ou `*`); `catalogo.yaml (<sem_respaldo\|duvidoso>): <fato>` |
| ~~`fluxo_nunca_casado`~~ | fluxo | **removido na v0.63**: o fluxo nunca usado é `sem_evidencia`/`nunca_usado`, como a receita |
| `absorvida` | item | o commit de `state_detail = absorvida:<commit>` |

Fora (sem fonte hoje): "sem uso enquanto a etapa roda por outro caminho", "duplicado" em chaves vizinhas e "habilidade publicada com a
mesma `match_key`" do fluxo. Quedas de eficácia e contestação já são `degradando`.

**Rebaixamento `catalogo_sem_efeito`** (o único efeito do item; passo `catalogo_sem_efeito` da curadoria periódica,
`aprendizado.curadoria_s`; determinístico, sem IA, idempotente): receita ou fluxo **vivo** com `commit` num app cujo catálogo ATUAL (o do
registro de apps, que a porta de política aplica) não respalda o efeito. Rebaixa só quando (a) o catálogo não tem nenhuma ação com efeito
externo → motivo `catalogo_sem_efeito:*`, ou (b) a capability é conhecida sem ambiguidade e a ação dela no catálogo não tem efeito →
`catalogo_sem_efeito:<CAPABILITY>`. App sem catálogo: nunca. Capability desconhecida ou ambígua num catálogo com efeito: só o motivo
`efeito_sem_respaldo_no_catalogo` (`duvidoso`). Transição pelo sistema (`decided_by = sistema`, `reason` = o motivo) pelo mesmo caminho do
Livro (`LearningService.mudar_estado`): `candidate`/`validated`/`published` → `disabled` (receita `quarantined`); nunca `deprecated`, porque a receita
`superseded` não volta pelo Livro e o §9.2 diz que reativar é de pessoa. O item rebaixado aparece `inativo` com o motivo da trilha em
`motivos[0].detalhe` — é o dado que o curador (30.11) vai ler. O `validated` que esperava o dono sai da espera com
`learning.needs_person` `motivo: rebaixado_pelo_sistema` (adendo v0.49). Reativar é de pessoa (`disabled` → `published`).

Nenhum campo novo além do vocabulário, nenhuma migração, nenhum código de erro novo. Prova `simulated`
(`tests/test_learning_obsolescencia.py`); `not_run` no central.

## Adendo v0.55 (02/10/2026) — `POST /api/instagram/profiles/{profile_id}/accounts/{account_id}/retire`: conta bloqueada sai (item 29.23, ADR-068)

Bloqueio confirmado: a conta sai da plataforma na hora e a persona fica. Corpo opcional `{"evidencia": "texto até 500"}` (o @ e o
id da conta são cortados do evento). Vale também para a conta âncora, que `DELETE …/accounts/{id}` recusa (409 `anchor_account`).

Resposta 200: `{profile_id, account_id, retirada, ancora, limpezas, status_da_persona, detail}`. `limpezas` são contagens
(`memory_items` e as dos módulos que registraram limpeza), sem texto da conta. A conta que já não existe devolve `retirada: false`
(idempotente). Persona inexistente: 404 `not_found`. Evento `profile.account_retired` (`data`: `profile_id`, `account_id`,
`app_id`, `ancora`, `origem`, `autor`, `evidencia`, `limpezas`, `status_da_persona`). O aparelho não é tocado.

## Adendo v0.61 (02/10/2026) — `POST /api/instances/{instance_id}/locked-account/resolve`: a pessoa resolve a quarentena (item 29.24, ADR-068)

Depois de limpar o app do aparelho (o que é decisão e ato da pessoa), a quarentena (marcador de conta travada, ADR-055) só sai por
este gesto explícito. Corpo `{"nota": "1 a 500 caracteres"}`, **obrigatória** (vazia, só espaços ou longa demais: 422). A nota
vira `resolution` do marcador; `resolved_by` é o operador da sessão (ou o rótulo de quem chama a API sem sessão).

Resposta 200: `{instance_id, resolvidos}` (nº de marcadores abertos que viraram história). Sem marcador aberto: 404
`no_locked_account` (como o `DELETE …/repair-pause` sem pausa); aparelho inexistente: 404. Só banco: não manda comando, não toca disco
nem app e não reativa o perfil (decisão de pessoa, na tela do perfil). Sincroniza o `account_label` e emite `device.locked_account`
com `acao: "resolvido"`.

Também (29.24): a conta já retirada não aparece mais com o @ em `GET /api/instances` (`account_label`, `locked_account` viram
`[conta removida]`), em `GET /api/health` (`locked_account_on_device`: "conta retirada, bloqueada"), nem nas frases da quarentena
(`restriction`, 409 `locked_account`/`aparelho_em_quarentena`, motivo do comando recusado). Conta viva continua com o @.

## Adendo v0.56 (02/10/2026) — `GET /api/aprendizado/falhas` e `/backlog/{id}`: `diagnostico`; propostas do diagnóstico (item 30.13)

Aditivo: nenhum campo some nem muda de tipo, nenhum código de erro novo. (A v0.54 e a v0.55 são de outras frentes; a v0.55 é do 30.11.)
Desenho: `design/aprendizado-vivo.md` §9.1. Só o determinístico: **nenhuma chamada de IA, prompt ou porta nova**; a causa `indeterminada` é dado.

**`diagnostico`** em cada item de `itens` do relatório (os grupos mostrados no topo) e no `GET /api/aprendizado/backlog/{id}` de um grupo `fk-*`
(chave `diagnostico` ao lado de `grupo`); `null` quando o grupo é só de verificação (seção 2) ou nada foi lido.

```json
{"causa": "receita_divergiu", "indeterminada": false, "amostra": 12,
 "fatos": [{"codigo": "conduzidas_por_receita", "valor": "12 de 12 tentativas lidas"}],
 "conhecimento_envolvido": [{"ref": "receita:12", "kind": "receita", "papel": "conduziu", "etapas": 4,
                             "aproximado": true, "estado": "falhando"}],
 "proposta": {"tipo": "rebaixar_receita", "alvo": "receita:12", "causa": "receita_divergiu"}}
```

- `causa` (vocabulário fechado, `CausaProvavel`): `teto_de_ia`, `provedor_de_ia`, `sessao_ou_autenticacao`, `aparelho`, `plano`, `informacao_da_pessoa`,
  `catalogo_recusou`, `verificador`, `receita_divergiu`, `versao_nova`, `licao_atrapalha`, `tela_desconhecida`, `falta_conhecimento`, `indeterminada`.
  O tipo da falha decide as causas que não dependem de contexto; só os tipos de navegação e conhecimento olham as últimas 30 tentativas do grupo.
- `fatos`: o que sustenta a causa (`codigo` curto, `valor` texto com números e refs; nunca texto de tela, comando ou segredo).
- `conhecimento_envolvido` (do mais presente ao menos): `kind` `receita | licao | tela | fluxo | habilidade`; `ref` citável (`receita:<id>`, `li-…`,
  `tela:<pacote>/<nome>`, `fluxo:<id>`, `habilidade:<id>@<v>`); `papel` `conduziu | exposta | tela_da_falha | fluxo_da_execucao |
  habilidade_da_execucao`; `etapas` = etapas distintas do grupo; **`aproximado: true`** quando a junção não é exata (a receita sem
  `attempts.recipe_id` é achada por pacote + `template_hash`); `estado` = estado de versão da receita (`domain/versao.py`), `status` do item da tela ou `null`.
- `proposta`: `{tipo, alvo, causa}` ou `null` (causa de ambiente, de IA ou de conta não propõe nada). É recomendação: **nada executa, rebaixa ou altera**.

**Propostas** (`propostas[]` do relatório e `proposta` do detalhe) ganham `alvo`, `causa` e `parent_id` (`null` nas três propostas de antes) e `tipo` ganha cinco
valores: `rebaixar_receita`, `revisar_licao`, `reaprender_tela`, `ajustar_catalogo`, `investigar`. A do diagnóstico tem `ref` = `<fk-do-grupo>|<alvo>`,
`fragmento` vazio e `parent_id` = o grupo; a curadoria a grava como linha `proposta` do backlog com `parent_id` (idempotente pela `cluster_key`).
Quem consome o `tipo` (painel) deve tolerar valor desconhecido.

**Teto de IA pelo tipo.** O tipo de falha `ia_orcamento` passa a vir também de `AIError.kind` (`ai_calls.error_kind`: `budget`; e `billing`/`balance`,
`refusal`, `not_configured` para `ia_saldo`, `ia_recusa`, `ia_indisponivel`) quando a tentativa tem as chamadas em `ai_calls`; o casamento por texto segue como
legado para a que não tem. Na leitura retroativa (`retroativo=true`) o tipo vence o `failure_kind` gravado, e a ocorrência conta em `retroativas`.

Prova `simulated` (`tests/test_learning_diagnostico.py`); `not_run` no central.

## Adendo v0.65 (02/10/2026) — `flows/match` passa a `POST` com corpo JSON; `members[].name` nos grupos de acesso (item 29.25)

**Quebra de contrato só para o painel do mesmo commit.** `GET /api/flows/match?command=<rascunho>` deixa de existir (responde 405) e vira
`POST /api/flows/match` com corpo `{"command": "1 a 4000 caracteres"}` (`extra="forbid"`; vazio, longo demais ou campo a mais: 422). Motivo: o
rascunho do comando, às vezes com e-mail, ia na query string e ficava na linha do log de acesso. A resposta não muda (cobertura do fluxo ou da
habilidade, ou `null` sem casamento) e o painel (`api.flowsMatch`) já chama o POST; quem usava o GET por fora precisa migrar.

Conferido (nada mudado fora do escopo): `POST /api/skills/resolve` e `POST /api/runs/targets/suggest` já recebem o comando no corpo. **Pendente, fora
deste item:** `GET /api/runs/distribution?command=` (prévia da distribuição, `modules/execution/presentation/router.py::preview_distribution`) ainda leva o texto do comando na query
e portanto no log de acesso; é o mesmo vazamento e pede o mesmo tratamento (POST com corpo).

Adição (compatível): `members[]` de `PolicyGroup` (`GET/POST/PATCH /api/instagram/policy-groups`) ganha `name` (nome da pessoa: exibição, nome e
sobrenome ou o @; `null` se nada existir). `username` continua `""`/nulo para quem teve a conta retirada (29.23); o painel mostra "nome · sem conta".
Prova `simulated` (`test_intencao_chamadores.py`, `test_grupos_de_acesso.py`); `not_run` no central.

## Adendo v0.66 (02/10/2026) — texto livre fora da query string; `has_avatar` (item 29.26)

**Quebra de contrato só para o painel do mesmo commit.** Continuação do v0.59: nenhuma rota `GET` carrega mais comando, mensagem ou busca de conteúdo na URL (query string vira linha de log de acesso e o texto pode ter e-mail ou nome).

| Antes | Agora | Corpo (`extra="forbid"`; fora do contrato: 422) |
|---|---|---|
| `GET /api/runs/distribution?count=&app_id=&command=` | `POST /api/runs/distribution` (o `GET` responde 405 `metodo_removido`, `Allow: POST`) | `{count: 1–64, app_id?: ≤80, command?: 1–4000}`; sem `app_id` nem `command`: 422 `distribution_sem_alvo`; senha no texto: 409 `credencial_no_comando`, como antes |
| `GET /api/instagram/profiles/{id}/context?counterparty=&thread_key=&content=` | `POST /api/instagram/profiles/{id}/context` (sem efeito; o `GET` responde 405) | `{counterparty?, thread_key?: ≤200, content?: ≤4000}`; corpo ausente vale `{}` |
| `GET /api/pedidos?q=` | `POST /api/pedidos/busca` (a resposta é a da listagem). `GET /api/pedidos` com `q` na URL responde 422 `busca_no_corpo` (ignorar `q` em silêncio devolveria a lista inteira como se fosse a busca) | `{q: 1–80, estado?, autonomia?, tipo?, profile_id?, pede_atencao?: bool, ordem?, limit?: 1–200, cursor?}` |

O resto da classe ficou na URL de propósito (varredura em `CHANGELOG.md`): ids, status, enums, paginação e nomes de app, capability ou handle curtos, que não são texto livre. O link do painel (`#/pedidos?q=…`) segue igual: o fragmento nunca vai ao servidor.

Adição (compatível): `has_avatar: bool` em `PersonaDTO`/`InstagramProfileDTO` (`/api/personas`, `/api/instagram/profiles`), em `PersonaOnDeviceDTO` (`GET /api/instances/{id}/personas`) e em `profiles[]` do contexto operacional: verdadeiro quando há imagem principal pronta, a que `GET /api/instagram/profiles/{id}/avatar` serve. O painel só pede a foto com ele e, sem foto, mostra as iniciais sem requisição (antes: um 404 por persona sem foto). A rota não mudou (404 sem foto); o jpg legado ainda não importado para a 048 (o import roda na partida) conta como sem foto no campo.

Prova `simulated` (`test_distribuicao_pelo_comando.py`, `test_limites_por_servidor.py`, `test_social_memory.py`, `test_pedidos_api.py`, `test_persona_imagens.py`, `Avatar.test.tsx`, `ProfilesPage.test.tsx`, `CommandPanel.test.tsx`, `PedidosPage.test.tsx`); `not_run` no central e no navegador.

## Adendo v0.58 (02/10/2026) — `capability` em cada linha do Livro e de `/apps/{pacote}` (hierarquia App → Capability → Item)

Aditivo: nenhum campo some nem muda de tipo, nenhum código de erro novo, sem migração. (A v0.57 é de outra frente: 30.11.)

Cada linha de `GET /api/aprendizado` (e de `/pendentes` e `/revisar`), de `GET /api/aprendizado/apps/{pacote}` (`aprendido[]` e `absorvido[]`)
e o `item` do detalhe `GET /api/aprendizado/{kind}/{ref}` ganham **`capability`**: `"<nome>"` ou `null`. O que não se sabe é `null`, nunca palpite:

- **receita**: a mesma derivação do `conteudo.capability` do detalhe (a capability da etapa de origem `learned_from`; sem ela, as etapas com o mesmo
  `step_hash` no mesmo app); ambígua (mais de uma capability distinta), sem fonte, treino e etapa livre `*` saem `null`;
- **lição e tela** (itens do livro): `escopo.capability`, exceto vazio e `*`;
- **fluxo, habilidade, memória** e o resto: `null` (o fluxo é um comando inteiro, não pertence a uma capability).

Lida em lote (uma consulta por tipo, como a `saude`); o painel agrupa por ela em `agruparPorCapability`.

## Adendo v0.59 (02/10/2026) — o `href` do item do Livro abre a aba certa

Muda o VALOR de um campo, não o tipo. O `href` de `learning.needs_person` (30.21) e do aviso externo (28.14, Telegram) passa de
`#/aprendizado?item=<kind>:<ref>` para **`#/aprendizado?aba=aprendido&item=<kind>:<ref>`**. Sem a aba, o painel caía na visão
Aplicativos e não abria o item (validação no Chrome de 02/10, I1). O painel aceita os dois formatos: `item` sem `aba` abre o
catálogo Aprendido com o item aberto, e os avisos já enviados continuam funcionando. "Revisar" das Pendências usa o mesmo link.

## Adendo v0.63 (02/10/2026) — mesmo rótulo para o mesmo fato e o nome da capability

- **`saude`: sai o motivo `fluxo_nunca_casado`.** O fluxo publicado e nunca usado há `sem_uso_dias` passa a sair como
  `sem_evidencia`, com o motivo `nunca_usado`, igual à receita. Antes, o mesmo fato era `obsoleto_provavel` no fluxo e
  `sem_evidencia` na receita, e o dono via o rótulo mudar sem saber por quê (validação no Chrome do deploy 2). A obsolescência
  do fluxo continua sendo a `substituta_viva`. Um cliente que ainda recebe `fluxo_nunca_casado` de um backend anterior mostra o
  mesmo texto do `nunca_usado`.
- **`capability_nome`** (texto ou `null`), campo novo ao lado de `capability` em cada linha de `GET /api/aprendizado`,
  `/pendentes`, `/revisar`, no `item` do detalhe, nas linhas de `/apps/{pacote}` e em cada grupo de `GET /api/aprendizado/falhas`
  (`itens[]` e `verificacao[]`, só no JSON; o Markdown segue com o código). É o `title` da capability no catálogo do app, com as
  internas, sem as lacunas de parâmetro: `OPEN_PROFILE` → "Abrir o perfil", `SEARCH_MAIL` → "Buscar no Outlook". Vem `null` se o
  app não tem catálogo, se a capability é desconhecida ou se a linha não tem capability. Nesses casos o painel mostra o código.

## Adendo v0.64 (02/10/2026) — o modo por app de lições e telas na visão por app

Só campos novos; nada muda de tipo.

- `GET /api/aprendizado/apps` e `/apps/{pacote}`: o resumo de cada app (`apps[]`, `nao_resolvido`, `app`) ganha
  **`modos_do_app`**: `{licoes: {modo, origem}, telas: {modo, origem}}`. `modo` é o efetivo no pacote (`off|shadow|on` para
  lições, `off|observe|on` para telas; `null` se o global não pôde ser lido). `origem` é `app` quando o config tem
  `aprendizado.<tipo>.por_app.<pacote>`, senão `global`.
- `modos` (visão e detalhe) ganha **`licoes_por_app`** e **`telas_por_app`**: as exceções do config, pacote → modo,
  ordenadas pelo pacote; `{}` quando todo app segue o global.

Só leitura: o config é da instalação e é lido ao iniciar o central. Mudar um modo é editar o `config.yaml` e reiniciar.
O `modos` do detalhe (`/apps/{pacote}`) continua sendo o GLOBAL, como na visão; o do app é o `modos_do_app`.

## Adendo v0.57 (02/10/2026) — `learning.needs_person` publica `motivo: parecer_da_ia` (item 30.11)

O curador por IA (`aprendizado.curador.modo` ≠ `off`; de fábrica `off`) passa a publicar o motivo `parecer_da_ia`, que já existia no
vocabulário (v0.49): quando um parecer B ou C **novo e válido** é gravado em `learning_reviews` para um item que JÁ está na fila "Para
aprovar". Mesmo payload (`aguardando: true`, `faixa`, `desde` = quando entrou na espera); a conclusão da IA nunca vai no evento. Não é
idempotente por (`kind:ref`, `aguardando`): cada parecer novo avisa uma vez (a chave única (item, dossiê) da 069 garante); a saída da
espera continua avisada normalmente. Parecer da faixa A, inválido ou recusado não publica. Em `shadow` também publica (o parecer não
decide nada; o aceite é da pessoa). Nenhuma rota, nenhuma migração, nenhum código de erro novo. Prova `simulated`
(`tests/test_learning_curador.py`); `not_run` no central.

## Adendo v0.67 (02/10/2026) — gatilhos `evento`, `condicao` e `persona` no `PedidoCorpo` (item 28.8)

Aditivo para quem lê; muda a resposta para quem já mandava esses tipos. Antes, `evento`, `condicao` e `persona` davam sempre
`gatilho_nao_suportado` (adendo v0.45). Agora são aceitos, com a `spec` validada (desenho em `docs/design/pedidos-laco.md` §14):

| tipo | `spec` | o que faz |
|---|---|---|
| `evento` | `{"kinds": ["run.failed", ...], "niveis": ["warn", "error"]?}`: de 1 a 10 tipos de evento; nunca `pedido.*` nem um tipo efêmero (`frame`, `metrics`...) | cada volta que acha eventos novos que casam cria UMA ocorrência (origem `evento`), respeitando o piso da autonomia; os eventos das execuções do próprio pedido não contam |
| `persona` | `{"intervalo_min_s": N, "intervalo_max_s": M}`, `300 ≤ N ≤ M ≤ 30 dias` | a primeira visita na ativação; a seguinte quando a anterior fecha, depois da saída `proxima_visita_s` da visita (presa a [N, M]) ou de M |
| `condicao` | `{"observacao": "<saída>", "op": "<", "valor": 3500}` (`op` em `<`, `<=`, `>`, `>=`, `==`, `!=`, `mudou`; `mudou` sem `valor`) | avalia a observação mais nova; só a passagem de falso para verdadeiro avisa; não cria ocorrência |

Códigos novos (em `previa`, viram `bloqueios[]`):

| HTTP | `codigo` | Quando | `campo` |
|---|---|---|---|
| 422 | `gatilho_invalido` | a `spec` de um dos três não serve (a mensagem diz o quê) | `gatilhos[i].spec.<campo>` |
| 422 | `condicao_sem_observacao` | o pedido só tem gatilhos `condicao`: nada observaria | `gatilhos` |
| 422 | `frequencia_abaixo_do_piso` | (já existia) também `persona` com `intervalo_min_s` abaixo do piso da autonomia | `gatilhos[i].spec.intervalo_min_s` |

- `gatilhos_resumo[].descricao` ganha os textos "Quando acontecer: …", "A persona volta entre … e …" e "Avisa quando …".
- `proximas` (prévia) mostra a primeira visita da persona e nenhuma data para evento e condição: elas dependem do que acontecer.
- A edição (`PATCH`) continua trocando só `agora`, `horario` e `recorrencia`. Os gatilhos dos três tipos novos ficam como
  foram criados.
- Dois tipos novos de aviso (`pedido.aviso` e `GET /api/pedidos/avisos`), migração 076: `eventos_perdidos` (warn;
  `dados`: `de_id`, `ate_id`) e `condicao_atendida` (warn; `dados`: `gatilho_id`, `observacao`, `op`). Os dois com
  `requer_pessoa=false`. O fato também fica na memória do pedido (`evento.buraco.<gatilho>`, `pendencia`;
  `condicao.<gatilho>`, `descoberta`).

## Adendo v0.68 (02/10/2026) — retirada de conta limpa o app nos aparelhos; evento `device.account_cleanup` (item 29.27, emenda do ADR-068)

`POST /api/instagram/profiles/{id}/accounts/{conta}/retire` (adendo v0.55) e o gatilho automático da retirada passam a **limpar os dados do app**
(`pm clear` só do pacote da conta) nos aparelhos onde a conta estava logada, quando o `app.yaml` do app declara `limpar_ao_retirar: true`
(hoje o Instagram). A resposta ganha o campo **`limpeza_dos_aparelhos`**: `{agendada: bool, aparelhos: n}` (com `motivo` quando `agendada` é
falso por falha ao agendar ou por não haver executor). Sem a chave no app, ou sem aparelho onde a conta estava logada: `{agendada: false,
aparelhos: 0}` e nada muda. A conta que já não existe (idempotente) não traz o campo.

A limpeza roda em tarefa de fundo, um aparelho por vez, como o comando `session.logout` (aparece em `GET /api/commands` com
`requested_by: sistema:limpeza-ao-retirar`): acorda o aparelho hibernado ou parado, captura a tela, `pm clear`, captura a tela, resolve a
quarentena (`resolved_by: sistema:limpeza-ao-retirar`, `resolution: "limpeza automática autorizada pelo dono em 02/10"`) e devolve a energia.
Evento novo **`device.account_cleanup`** por aparelho: `data` = `{profile_id, account_id, package, instance_id, resultado, passo, motivo, antes,
depois, energia, resolvidos}`; `resultado` é `concluida` (nível `warn`), `dispensada` (a quarentena já tinha sido resolvida por uma pessoa, ou a
retirada não valeu; `info`), `falhou` (`error`, o aviso de atenção: a quarentena segue aberta e nada é repetido) ou `nao_agendada` (`error`).
`antes`/`depois` são chaves do armazém de evidências (`limpeza-de-conta/<aparelho>/…png`). Os aparelhos do pedido vêm dos marcadores abertos da
conta e do vínculo (não da sessão); `passo: outra_conta` (em `falhou`) é a recusa de limpar um aparelho que também serve a outra conta do mesmo
app, ou que tem quarentena aberta de outra conta (sem acordá-lo). O evento e o log não carregam o @ da conta. Nada
roda retroativamente na subida. Nenhuma migração. Prova `simulated` (`tests/test_limpeza_ao_retirar.py`); `not_run` no central.

## Adendo v0.69 (02/10/2026) — D2-a também ao ganhar a conta (item 29.29, emenda do ADR da 051)

Sem rota nova nem campo novo: dois 409 `conta_do_app_ja_no_aparelho` em rotas que já existiam, sempre ANTES de criar qualquer linha.

- `POST /api/instagram/profiles` com `persona_id` de pessoa sem conta: 409 quando algum vínculo SEM app da pessoa (ou o `instance_id` do corpo)
  está num aparelho onde outra persona já serve o app da conta. Antes só o `instance_id` do corpo era conferido.
- `POST /api/instagram/profiles/{id}/accounts` (conta de outro app): 409 quando um vínculo sem app da persona passaria a servir esse app num aparelho
  onde outra persona já o serve. O vínculo COM app não é reconferido (já foi no vínculo) e quem já tem conta no app não muda de sentido.
- A mensagem do 409 nomeia o aparelho e a outra persona. Nada é criado: nem perfil, nem conta, nem senha no cofre.
- Ressalvas (revisão adversarial de 03/10): a conferência só roda com o app âncora registrado em `apps` (sem ele, nada é conferido, como antes);
  `conta_ancora(criar=True)` em dado legado cria a conta sem conferir (0 casos no central em 03/10); conferência e criação não estão numa transação
  (janela de corrida, no backlog).

## Adendo v0.70 (03/10/2026) — evidência inválida: ação própria, trilha com tipo e o item reaprendido (item 30.23)

Uma rota e campos novos; nada muda de tipo. A regra é a emenda de 03/10 ao ADR-054; o desenho, o §9.3 de
`design/aprendizado-vivo.md`.

- **`POST /api/aprendizado/{kind}/{ref}/evidencia-invalida`**, corpo `{run_id}` (só esse campo). Serve para quando a receita
  ou o fluxo foi aprendido de uma execução que terminou como sucesso sem comprovar o que fez. Quem decide é o operador da
  sessão do painel, como em `/status`. O motivo não vem do cliente: o backend grava `evidencia_invalida:<run_id>` na trilha.
  - O item vivo (`candidate`, `validated`, `published`) vai para `disabled`, com CAS no status nativo.
  - O já desligado ganha a linha `disabled → disabled`, que reclassifica o desligamento sem mudar o status nativo.
  - A mesma marca de novo não grava outra linha.
  - Devolve o detalhe, como `GET /api/aprendizado/{kind}/{ref}`.
  - Recusas:
    - 422 `invalid`: o tipo não é `receita` nem `fluxo`, ou o `run_id` está fora do formato `r-AAAAMMDDhhmmss-xxxxxx`;
    - 409 `transition_forbidden`: o item não nasceu de uma execução (treino ou origem ilegível), o `run_id` não é a execução
      de origem dele (`nasceu_de`), ou ele está aposentado (`deprecated`);
    - 409 `state_conflict`: o status nativo mudou entre a leitura e a escrita;
    - 404 `not_found`.
- **O motivo reservado é recusado em `POST …/status`** com 422 `invalid`. Vale para o `reason` que começa por
  `evidencia_invalida`, sem diferenciar maiúsculas e ignorando espaços nas pontas, e a mensagem aponta a ação própria.
  `PUT /api/flows/{id}` e `PUT /api/recipes/{id}` passam pelo mesmo serviço e recusam igual.
- **Linha da trilha** (`trilha[]` do detalhe): ganha `tipo` (`"evidencia_invalida"` ou `null`) e `run_invalidada` (o id da
  execução ou `null`), lidos do `reason` pelo backend. O cliente não interpreta o formato do motivo.
- **Evidência** (`evidencias[]`): ganha `invalidada` (booleano). É `true` quando a execução dela foi marcada como evidência
  inválida neste item. A evidência continua listada, mas não entra na saúde, na versão nem na sombra do fluxo.
- **Item** (linha de `GET /api/aprendizado`, `/pendentes` e `/revisar`, e `item` do detalhe):
  - `nasceu_de`: a execução de que a receita ou o fluxo foi aprendido; `null` no treino e nos outros tipos.
  - `reaprendido: {run_invalidada, item: {kind, ref}} | null`: o item (re)nasceu, por outra execução real, no escopo
    (`scope_key`) de uma evidência inválida, sem publicação de pessoa no escopo entre a marca e esse nascimento. `item` é o
    que foi desligado; no fluxo é a própria linha, que renasce nela. Com `reaprendido`, `requires_owner` é `true` e o item
    espera o dono em "Para aprovar". O item que o dono aprova continua com o campo, porque é a origem dele; o que nascer
    no escopo depois dessa aprovação já não é reaprendido.
- **`por_que_nao_publica.codigo`** ganha `reaprendido`, logo depois de `texto_de_pessoa`: `espera_o_dono` vem `true` e
  `detalhe` traz a execução invalidada. O código não aparece no item já publicado.
- **Detalhe** ganha `invalidar_evidencia: {run_id} | null`: a execução que a pessoa pode marcar agora (a de origem). Vem
  `null` quando a ação não cabe: outro tipo, item sem origem, aposentado ou já marcado.
- **`relacoes[].tipo`** ganha dois valores, ambos com `fonte` `learning_transitions: evidencia_invalida:<run>, mesmo scope_key`:
  - `reaprende`: da receita reaprendida para a desligada, com `rotulo` "<ref> (evidência inválida da execução <run>)";
  - `reaprendida_por`: o inverso.

  O fluxo renasce na mesma linha e não ganha relação consigo.
- **Evento `learning.needs_person`**: o `motivo` da entrada ganha `reaprendido`, na faixa B. Ele vem antes das outras
  razões B; as da faixa C continuam vencendo.

Não há migração: o tipo mora no `reason` de `learning_transitions`, com gramática fechada
(`evidencia_invalida:r-AAAAMMDDhhmmss-xxxxxx`).

## Adendo v0.71 (03/10/2026) — leitura visual de saída de etapa (item 12.5, ADR-070)

Sem rota nova. O contrato do 12.5 está no adendo "Origem do valor" do relatório de execução (acima): `values_read[].origem`, `read_value(source)`, o
vocabulário fechado de recusas (`desligado` … `leitor_falhou`, `tela_sensivel`, `triagem:<motivo>`), `GET /api/ai` com a função `leitura` e `GET
/api/usage` com o `role` `leitura`. Ajustes desta revisão: a triagem do valor visual leva a etapa a `waiting_user` (sem nova tentativa do ator);
o valor gravado é o do leitor; as saídas `origem=visual` não entram nas variáveis de receita; orçamento, prazo e crédito do leitor não viram
`leitor_falhou`; o aviso de `/api/ai` nomeia os apps pelo rótulo do dado. O número v0.71 é o final, confirmado pelo orquestrador na integração da suíte 6.

## Adendo v0.73 (03/10/2026; número final, confirmado na integração da suíte 6) — `retire`: `limpezas` ganha `memory_items_de_outras_personas` (item 29.32)

`limpezas.memory_items` passa a contar as lembranças reescritas em TODAS as personas (não só na que retira), e a chave nova
`memory_items_de_outras_personas` diz quantas dessas eram de outras personas. Só contagens, inteiros; o evento `profile.account_retired` leva as mesmas.

## Adendo v0.72 (03/10/2026, provisório: quem mergear depois renumera) — o parecer da IA diante da pessoa: rótulo, aceite e pedido de revisão (item 30.17)

Aditivo. O curador (30.11) grava pareceres em `learning_reviews`; agora a pessoa os vê, aceita ou recusa, e cada decisão
dela sobre o item vira o rótulo humano do parecer (`decisao_final`, `decidido_por`, `override`, `override_motivo`,
`transicao_id`, colunas da 069). Nenhuma migração. Campos e rotas só existem com o curador composto (`ligar_curador`, que o
`AppState` sempre chama); sem ele, o Livro de antes (`curador: null`, `pareceres: []`).

**Quando o parecer aparece** (`aprendizado.curador.modo`). Em `on`, sempre. Em `shadow` e `off`, só DEPOIS da decisão da
pessoa: a escolha dela mede se a IA acerta, sem a influência dela (D-3). O parecer pendente escondido só se conta
(`pendentes_ocultos`).

**Leitura.**
- `GET /api/aprendizado/pendentes` e `/revisar` → `{itens, total, curador}`; `curador: {modo} | null`. Cada item ganha
  `parecer: ParecerNaFila | null`, preenchido só em `on` e só com parecer válido, sem decisão e sobre o estado de agora:
  `{id, criado_em, decisao, confianca, classe, simulated, acao, recusa, recusa_no_lote}`. `acao: {to, rotulo} | null` é o
  passo que aceitar dá (`null` = aceitar é concordar, sem transição); `recusa` e `recusa_no_lote` dizem por que aceitar
  sozinho ou em lote não vale agora (`null` = vale; os códigos são os do gesto, abaixo).
- `GET /api/aprendizado/{kind}/{ref}` ganha `pareceres: [Revisao]` (das 5 revisões mais novas, as que o modo deixa ver) e
  `curador: {modo, pendentes_ocultos, pode_pedir_revisao} | null`. `Revisao`: `{id, criado_em, gatilho, validade, classe,
  simulated, modelo, estado_no_parecer, parecer, atual, acao, recusa, decisao_final, decidido_por, override,
  override_motivo, transicao_id}`. `parecer` é a saída validada (`decisao`, `alvo`, `faixa`, `causa`, `confianca`,
  `probabilidade`, `evidencias_citadas`, `riscos`, `inconsistencias`, `falta` e `conclusao`, o único texto livre; e
  `falta_descartada` só quando a validação tirou de `falta` um rótulo fora da classe, adendo v1.53), `null`
  quando a `validade` não é `ok` (`invalida:<motivo>`, `recusada:custo`, `recusada:triagem`). `atual`: é o parecer que uma
  decisão de agora responde (só ele traz `acao` e `recusa`). `classe`: a efetiva, a da política endurecida pela `faixa`
  que a IA declarou.

**O rótulo: toda decisão da pessoa sobre o item.**
- `POST /api/aprendizado/{kind}/{ref}/status {to, reason, review_id?}`. `review_id` (1 a 64 caracteres) é o parecer que a
  pessoa viu ao decidir; nunca trava a decisão. A transição é a de antes; depois dela, o parecer pendente do estado de antes
  recebe o rótulo. O rótulo é acessório: uma falha dele só vai ao log, a decisão fica.
- A pessoa VIU o parecer quando mandou `review_id` OU quando o curador está em `on` (o painel mostra o parecer na fila).
  Vista, `decisao_final` é `aceitou` quando a decisão vai para o mesmo lado da sugestão e `recusou` quando não vai (override;
  o `reason` vira `override_motivo`, salvo cara de credencial). Lados: sobe = `aprovar`; desce = `rebaixar`, `desativar`,
  `possivelmente_obsoleto`, `substituir`, `fundir`; espera = `observar`, `pedir_evidencia`, `manter`.
- Às cegas (`shadow` ou `off` sem `review_id`), `decisao_final` é o rótulo da própria ação (`validar`, `aprovar`, `rejeitar`,
  `desligar`, `aposentar`, `reativar`), com `override` pelo lado e sem motivo.
- `review_id` que não responde ao estado de agora (já decidido, outro estado): a decisão fica, sem rótulo.
- `PUT /api/flows/{id}` e `PUT /api/recipes/{id}` rotulam sempre às cegas (a página delas não mostra parecer), com o rótulo
  do primeiro passo. Decisão do ator `sistema` nunca rotula.
- `POST /api/aprendizado/{kind}/{ref}/evidencia-invalida` (30.23, adendo v0.70) rotula como o `/status`: vista em `on` (o
  detalhe mostra o parecer ao lado do botão), às cegas fora dele. Reclassificar o já desligado não muda o estado e não
  rotula.

**O gesto sobre o parecer.** `POST /api/aprendizado/{kind}/{ref}/parecer/{review_id} {resposta: "aceitar"|"recusar",
motivo (1 a 500), em_lote?: false}` → o corpo do detalhe.
- Aceitar dá o passo do lado sugerido que a pessoa pode dar agora. Sobe: o primeiro disponível de `aprovar`, `validar`,
  `reativar`. Desce (`rebaixar`, `desativar`, `possivelmente_obsoleto`): `desligar` ou `rejeitar`, nunca `aposentar`.
  `substituir`, `fundir`, `observar`, `pedir_evidencia` e `manter`: concordar, sem transição. O motivo vai à trilha do item
  quando há transição, e o parecer fica `aceitou`. Um passo por gesto: aceitar "aprovar" num candidato o valida.
- Recusar não mexe no item: `decisao_final = recusou`, `override = true`, `override_motivo` = o motivo. Motivo com cara de
  credencial: 409 `note_looks_secret`, nada gravado.
- A decisão do parecer é gravada ANTES da transição, na mesma transação, com CAS (`decisao_final IS NULL`): o segundo gesto
  perde (409 `parecer_ja_decidido`) e uma transição recusada desfaz o rótulo.
- A classe que vale é a mais restritiva entre a gravada e a do dossiê de agora (sem dossiê, C): um catálogo que mudou
  endurece, nunca afrouxa.
- O lote do painel é um gesto por item, em sequência, com `em_lote: true`; só a classe B entra.
- Erros `{code, message}`: 404 `review_not_found` (a revisão não existe ou é de outro item) e `not_found`; 422 `invalid`
  (motivo em branco); 409 `parecer_invalido` (sem parecer válido), `parecer_ja_decidido`, `parecer_oculto` (curador fora do
  `on`), `parecer_simulado` (provedor falso não move item real), `parecer_desatualizado` (o item mudou de estado depois do
  parecer), `so_registro_na_classe_a` (na A quem decide é a regra), `lote_na_classe_c` (a C só item a item),
  `note_looks_secret` e os da transição (`transition_forbidden`, `owner_required`, `state_conflict`, `vetoed`); 503
  `not_ready` sem curador composto.

**Pedido de revisão.** `POST /api/aprendizado/{kind}/{ref}/revisao`, sem corpo:
- 202 `{pedido: true, revisao: null}`: o pedido entrou (sinal `pediu_revisao`). O curador o atende numa volta seguinte,
  dentro do orçamento, pelo gatilho `pedido_da_pessoa`: pedidos dos últimos 7 dias sem revisão posterior. O pedido pula o
  cooldown e, desde o 30.30, vai na frente de todos os itens (fora a classe A, que segue só com sobra), sob o teto da hora.
- 200 `{pedido: false, revisao: Revisao}`: o dossiê de agora já foi revisado (a chave (item, dossiê) da 069). O dossiê muda
  com evidência nova, outro estado ou outra saúde.
- 409 `curador_fora_do_on` (em `shadow` o curador já revisa sozinho); 422 `invalid` para tipo que o curador não revisa
  (revisa `receita`, `fluxo`, `licao`, `tela`, `voz`, `preferencia`); 409 `state_conflict` quando não há como montar o dossiê.

**Sinais novos** (`learning_signals`, polaridade neutra, `created_by` = quem decidiu ou pediu):

| `kind` | `source_ref` | `data` |
|---|---|---|
| `parecer_decidido` | `parecer:<review_id>` | `{review_id, item_ref, decisao_final, override, viu}`; o `created_at` é QUANDO a pessoa decidiu (a 069 não tem coluna para isso) |
| `pediu_revisao` | `pedido_de_revisao:<item_ref>@<dossie_hash>` | `{kind, ref, item_ref, dossie_hash}` |

Prova `simulated`: `tests/test_learning_pareceres.py` e `frontend/src/features/aprendizado/ParecerDaIA.test.tsx`;
navegador na cópia do banco do central com um provedor de ensaio e o hub `simulated` (03/10). `not_run` no central: o
curador fica `off` até o deploy 4, que o liga em `shadow`.

## Adendo v0.74 (03/10/2026, provisório: quem mergear depois renumera) — os nomes do painel no "o que mais falha" e nos sinais (validação do deploy 3, P2 e P3)

Só campos novos, aditivos; o painel lê os dois com fallback para o código.

- `GET /api/aprendizado/falhas` (JSON): cada grupo de `itens` e `verificacao` ganha `app_nome`, ao lado do
  `capability_nome` que já existia. É o nome do agrupamento do Aprendido (`VisaoPorApp.nomes`: o declarado, depois o da
  loja, depois o próprio pacote); nulo no app `*`. O `titulo` (`pacote · CÓDIGO: motivo`) continua e segue sendo o de
  quem desenvolve; o Markdown (`formato=md`) não muda.
- `GET /api/aprendizado/sinais`: cada sinal ganha `app_nome` e `capability_nome` (o mesmo ajudante,
  `presentation/nomes.py`), nulos quando não se sabe. Os votos e sinais de `GET /api/runs/{id}/feedback` não mudam.

O painel passa a mostrar "Pós-condição não comprovada — Instagram · Abrir o feed" no lugar do título com o pacote e o
código, e "Instagram › Abrir o perfil" nos sinais. Também só na tela: a receita troca a chave da etapa pelo nome da
capability ("send_message_i1 (v1)" → "Enviar a mensagem (v1)"), dois itens iguais numa lista ganham quando foram
aprendidos (ou o número), e o texto da lição nomeia a capability ("Em Abrir o perfil (OPEN_PROFILE): …"). O texto
gravado da lição, que vai ao prompt, não muda. Sem resposta HTTP, o estado de erro diz "Sem resposta do servidor." no
lugar do texto do navegador (`lib/loadError.tsx`, todas as telas).

Prova `simulated`: `tests/test_learning_rotas_falhas.py`, `frontend/src/features/aprendizado/model.test.ts`,
`DetalheRico.test.tsx`, `SaudeDoApp.test.tsx`, `AprendizadoPage.test.tsx` e `frontend/src/lib/loadError.test.ts`.

## Adendo v0.75 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — os grupos de observabilidade de `/api/usage` (RA-10, migração 080)

Só chaves novas, aditivas; nenhuma existente muda. O preço de todo grupo é o de `spent_usd` (`usd` declarado onde
existe, senão tokens × preço; `provider='simulated'` conta a chamada a US$ 0). As linhas anteriores à 080 têm as colunas
nulas e não entram nos grupos que dependem delas: os números valem do deploy em diante.

```ts
interface UsageGrupo { calls: number; usd: number }
interface UsageReport {                                   // … os campos de sempre, mais:
  by_origin: Record<string, UsageGrupo>;                  // ai_calls.origem; nula vira "sem_origem"
  escalations: Record<MotivoDeEscalonamento, UsageGrupo>; // ai_calls.escalate não nulo
  rejudges: UsageGrupo & {                                // verify com motivo 'rejulgamento' (7.10 e 17.10)
    by_kind: Record<'nivel' | 'sim_com_efeito', UsageGrupo>;
    judged: number; disagreements: number; disagreement_rate: number | null;
    by_app: Record<string, { judged: number; disagreements: number; disagreement_rate: number }>;
  };
  cascades: UsageGrupo & { unblocked: number; by_verdict: Record<string, number> };  // decide com motivo 'cascata'
  image_reasons: Record<MotivoDaImagem, { calls: number; with_image: number }>;
  steps_driven_by_null: number;                           // etapas terminadas com decide e driven_by nulo
}
```

- **Discordância do rejulgamento**: o modelo forte desfez o veredito do barato. No `nivel` (7.10), o forte diz `yes`
  onde o barato recusou; no `sim_com_efeito` (17.10), o forte não diz `yes` onde o barato disse. Só linhas `ok=1`.
  `by_app` usa o app da etapa (`projecao.app_da_etapa`, o mesmo do histórico das ações).
- **`cascades.unblocked`**: a decisão do modelo forte, depois do bloqueio do barato, não foi `step_blocked`; erro do
  provedor aparece em `by_verdict` como `erro`.
- **`steps_driven_by_null`**: o aceite do RA-10 é zero nas etapas novas. O total de `driven_by` de sempre ainda soma
  nulo como `ai` (COALESCE), e este número mostra o que essa soma supõe.

Os vocabulários (`MotivoDeEscalonamento`, `MotivoDaChamada`, `MotivoDaImagem`) estão em `docs/ia.md` §9 e em
`backend/app/planning/provider.py`.

Prova `simulated`: `backend/tests/test_observabilidade_das_chamadas.py`. Prova real: `not_run` (a consulta de
conferência de `docs/ia.md` §9 roda um dia depois do deploy).

## Adendo v0.76 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — o rótulo de intenção (item 30.25)

Duas rotas novas, sem IA e sem custo. As duas entram antes da rota genérica do livro (`{kind}/{ref}`).

- `GET /api/aprendizado/intencao?limite=` (1 a 200, padrão 50): `{itens, total}`, as perguntas abertas da mais
  recente para a mais antiga. Cada item: `review_id`, `run_id`, `criado_em`, `terminou_em`, `app`, `app_nome`,
  `comando` (lido da execução na hora; nunca gravado no rótulo), `cadeia` (`sem_casamento|empate`), `candidatos`
  (`[{skill_id, nome}]`, o nome pelo catálogo de agora, o id quando a habilidade saiu dele), `empatados`,
  `decisao_final` e `decidido_por` (nulos). 503 antes da composição.
- `POST /api/aprendizado/execucao/{run_id}/intencao` com `{"escolha": "<skill_id>" | "nenhum"}`: grava a
  resposta da pessoa (o operador da sessão) por CAS e devolve `{review_id, run_id, criado_em, app, decisao_final,
  decidido_por}`. 404 `not_found` sem pergunta; 422 `invalid` fora do catálogo gravado; 409 `state_conflict` já
  respondida.
- `learning_reviews` ganha linhas com `template_id='intencao'` (sem migração: a 069 já tem as colunas). Os leitores
  do curador filtram `template_id='curador'`. O sinal `parecer_decidido` ganha `data.template_id`.

Prova `simulated`: `tests/test_learning_rotulo_de_intencao.py` e
`frontend/src/features/aprendizado/IntencaoSecao.test.tsx`. Ensaio no navegador (03/10, cópia do banco, provedor
simulado): responder, "Nenhuma destas", filtro, 409, 422, erro de carga e Sinais.

## Adendo v0.77 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — a causa do "ausente" e a herança da receita (RA-20, item 29.40)

Só nomes de métrica e uma chave de config, aditivos. Nenhuma rota muda.

- `receita.consulta{resultado}` ganha o valor `herdada`: a chave não tinha receita e herdou, como candidata, a receita
  provada da mesma etapa noutra chave. Como `candidata`, não termina em `receita.reproducao` (a IA decide a etapa).
- Nome reservado novo: `receita.ausente{causa}`, uma contagem por consulta sem receita na chave (herdada ou não), com
  `causa` ∈ `espera_o_dono`, `desligada`, `variante`, `legada`, `versao`, `assinatura`, `sem_receita`
  (`modules/learning/domain/causa_do_ausente.py`). Os dois saem em `GET /api/desempenho` como os demais contadores.
- `ai.recipes_heranca` (padrão `true`): `false` só mede a causa, sem herdar. Herda só com `ai.recipes_promote_after`
  maior que 0.
- Transição nova na trilha da receita (`active → superseded`, pelo sistema): a legada ativa da mesma etapa e versão sai
  quando uma receita da chave completa se prova e passa a agir.

Prova `simulated`: `tests/test_recipes.py::test_herda_da_versao_anterior`, `tests/test_receita_heranca.py` e
`tests/test_learning_causa_do_ausente.py`.

## Adendo v0.78 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — "Confirmar que fica" (item 30.24)

- `POST /api/aprendizado/{kind}/{ref}/confirmar` com `{"motivo"?: string (até 500), "review_id"?: string}`: a pessoa
  da sessão mantém o legado de "Revisar". Devolve o detalhe do item, como o `/status`. 404 sem o item; 422 para
  tipo que não seja `receita` ou `fluxo`; 409 `note_looks_secret` para motivo com cara de credencial; 409
  `state_conflict` fora de "Revisar" (inclusive a segunda confirmação). O estado e o status nativo não mudam.
- A trilha ganha a linha `published → published` com `reason` = `confirmado que fica` ou `confirmado que fica:
  <motivo>`. No JSON da trilha, `tipo: "confirmacao"` e `motivo_da_pessoa` (o motivo sem o prefixo, ou nulo).
- `GET /api/aprendizado/revisar`: o item confirmado sai, e volta quando chega evidência contrária real
  (`learning_evidence` `against`/`conflict`, `simulated=0`) depois da confirmação.
- Aceitar um parecer `manter` (`POST .../parecer/{review_id}`) num item de "Revisar" faz a mesma confirmação, ligada
  à revisão (`transicao_id`). Fora de "Revisar", aceitar "manter" segue só concordando.
- `learning_reviews.decisao_final` às cegas ganha o rótulo `confirmar` (a confirmação sem o parecer à vista).
- Receita e fluxo, nas listas do livro (`/aprendizado`, `/pendentes`, `/revisar`) e no detalhe, ganham `em_revisar`
  (bool), `confirmado` (a confirmação que vale) e `confirmacao_contestada` (a que a evidência contrária derrubou), os
  dois `{por, em, motivo}` ou nulos. Os outros tipos não têm as chaves.

Prova `simulated`: `tests/test_learning_confirmar_que_fica.py`, `AprendizadoPage.test.tsx` e `DetalheRico.test.tsx`.
Ensaio no navegador (03/10, cópia do banco do central, provedor simulado, 39 itens em "Revisar"): confirmar com e sem
motivo, em lote, o histórico, o aviso no Aprendido, a evidência contrária que devolve o item, o 409 de quem chegou
depois e 375 px.

## Adendo v0.79 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — evento `plan.refused`: a porta de política no planejamento (RA-7); `run.updated.iniciada_por` (P12)

Aditivo. Um kind novo de `EventRecord`, persistido, e um campo novo no `data` de um kind que já existe. Nenhuma rota
muda.

| kind | data | persistido |
|---|---|---|
| `plan.refused` | `{motivo: "efeito_fora_do_catalogo", etapas: [{key, title, app_id, app, capability, motivo}]}` | sim |

- Sai quando `RunService._plan` recusa o plano pela regra do item 13.2: etapa com efeito externo, num app com catálogo,
  sem uma ação daquele catálogo. Vale para todo plano (planejador, fluxo, skill), antes de materializar.
- `etapas[].motivo` vem do vocabulário fechado `sem_acao_do_catalogo` | `acao_de_outro_catalogo`
  (`planning/capabilities.py::MotivoForaDoCatalogo`). `app_id` é nulo quando o app só se sabe pelo pacote, e `app` é o
  nome (ou o pacote). `capability` é a chave que veio no plano (nula = nenhuma).
- A execução vai a `needs_input` com o plano zerado (`steps: []`) e uma pergunta por etapa recusada em `missing`
  (`field: "policy"`). O `message` do evento é a frase da linha do tempo, igual à da porta do despacho.
- O painel não precisa de mudança: `EventRecord.kind` é `string`, e a execução em `needs_input` já mostra as perguntas.

**`run.updated` do início** (P12): o evento da transição para `running` por `RunService.start` passa a levar
`{iniciada_por, run: RunSummary}`. `iniciada_por` é quem iniciou: o operador da sessão, ou `panel`, por
`POST /api/runs/{id}/start` (nunca `sistema`: `painel:sistema` se a sessão se chamar assim); `sistema` no início
automático do `mode=execute` depois do plano. Os outros `run.updated` não mudam (sem o campo). Uma execução em
`mode=plan` só passa a `running` por esse início explícito: a prévia é `mode='plan' AND started_at IS NULL`.

## Adendo v0.80 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — o nome do app e a etapa de origem nas entradas do livro (validação do deploy 4)

Só campos novos, aditivos; o painel lê os dois com fallback para o que já mostrava.

- `GET /api/aprendizado`, `/pendentes`, `/revisar` e o `item` de `GET /api/aprendizado/{kind}/{ref}`: cada entrada
  ganha `app_nome` (texto ou `null`), o nome do app pelo mesmo ajudante do adendo v0.74 (`VisaoPorApp.nomes`: o
  declarado, depois o da loja, depois o próprio pacote; nulo sem app).
- As mesmas entradas ganham `etapa` (texto ou `null`): na receita, o `steps.title` da etapa de que ela foi aprendida
  (`recipes.learned_from_step`); nulo no treino, na receita sem origem e nos outros tipos. O `title` não muda.

O painel mostra o nome do app onde mostrava o pacote, com o pacote no `title`. Na receita de app sem catálogo (sem
`capability_nome`), mostra o título da etapa com a chave: "Digitar a mensagem · etapa fill_message (v1)". O dossiê do
curador não leva `etapa`.

Prova `simulated`: `tests/test_learning_capability_na_linha.py`, `frontend/src/features/aprendizado/model.test.ts`,
`AprendizadoPage.test.tsx`, `SaudeDoApp.test.tsx` e `falhasTexto.test.ts`.

## Adendo v0.81 (03/10/2026) — `steps.driven_by` ganha `sem_ator` (caminho rápido 1)

Sem rota nova. O campo `driven_by` do `Step` (e a chave `steps_driven_by` de `GET /api/profiles/{id}/capacidades`, os
contadores por origem de `apps_overview` e do `aproveitamento` de fluxos) passa a poder ser `sem_ator`, além de `ai`,
`recipe` e `recipe+ai`: a etapa sem efeito cuja pós-condição já valia na tela lida fechou sem o ator decidir nenhuma ação
(a prova é a mesma, feita pelo `_verify`). `aproveitamento` ganha o contador `sem_ator` por fluxo e app e não a conta como
elegível a receita. Quem lê `driven_by` com união fechada precisa do valor novo; o painel mostra "Sem o ator".

## Adendo v0.82 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — `GET /api/apps/{pacote}/conhecimento`: a prova do conhecimento de app no ar (RA-24)

Rota nova, só leitura, sem efeito. O parâmetro é o **pacote** Android (`com.instagram.android`), e não o `app_id`.

```json
{"app": "com.instagram.android", "processo_iniciado_em": "2026-10-03T05:00:00.000Z",
 "arquivos": [{"nome": "telas.yaml", "caminho": "backend/app/conhecimento/apps/com.instagram.android/telas.yaml",
               "bytes": 21345, "fim_de_linha": "crlf", "sha256": "…", "git_blob": "…",
               "modificado_em": "2026-10-03T04:58:12.000Z", "mudou_depois_do_inicio": false}]}
```

- `arquivos`: cada `*.yaml` da pasta `app/conhecimento/apps/<pacote>/`, em ordem de nome.
- `sha256` e `git_blob` são do texto que o carregador lê (CRLF e CR solto viram LF, como no `read_text`), e não dos
  bytes do disco. O checkout do central tem `core.autocrlf=true` (CRLF no disco, LF no Git). Assim `git_blob` é o de
  `git hash-object <arquivo>` no checkout e o de `git rev-parse <commit>:<caminho>`, e confere com o `commit` de
  `GET /api/health` sem abrir a máquina.
- `bytes` e `fim_de_linha` (`crlf`, `lf` ou `misto`) são do disco, como está.
- `mudou_depois_do_inicio`: o arquivo foi gravado depois de o processo subir (`processo_iniciado_em`). Os carregadores
  leem cada arquivo uma vez por processo, então o disco pode não ser o que está em memória; só um reinício alinha.
- 404 `not_found` quando o pacote não tem a forma de pacote (o nome vira caminho) ou a pasta não tem YAML.

Junto, a versão do formato passa a ser conferida na carga, como a `contract_version` do catálogo. `telas.yaml` e
`sessao.yaml` só aceitam `versao: 1` (`VERSOES_DE_TELAS`, `VERSOES_DE_SESSAO`). Ausente, vale 1. Antes, `telas.yaml`
trocava qualquer não inteiro por 1 e aceitava qualquer inteiro, e `sessao.yaml` aceitava qualquer inteiro ≥ 1. Um
arquivo de versão nova lido por código velho seria entendido pela metade, sem aviso.

Prova `simulated`: `tests/test_prova_do_conhecimento.py`, com `git_blob` comparado a `git hash-object` de cada YAML
do repositório e a recusa de `versao` 2, 0, 1.0, `true` e `"1"`.

## Adendo v0.85 (03/10/2026; número da orquestradora, `.claude/reservas.md`; item 29.42) — `GET /api/flows` devolve `required_apps` na ordem do plano

- Cada fluxo de `GET /api/flows` (e a resposta de `PUT /api/flows/{id}`) ganha `required_apps: string[]`: os ids dos apps
  que o fluxo exige, **na ordem em que o plano gravado os usa** (primeira aparição em `steps[].app_id`; a etapa sem app
  roda no `app_id` do plano). Um comando entre apps ("mande no QA Messenger e abra no Chrome") volta como
  `["qa-messenger", "chrome"]`, não na ordem alfabética de `flow_required_apps`, que guarda só o conjunto.
- O conjunto é o da tabela `flow_required_apps`; sem linhas nela vale o `required_apps` congelado no plano (a mesma regra
  de `FlowStore.match`). Exigido que nenhuma etapa cita vai ao fim, em ordem alfabética; app citado pelo plano e fora do
  conjunto não entra. Fluxo sem apps: `[]`. Plano ilegível não derruba a lista (a ordem cai para a da tabela).
- Sem migração e sem N+1: a lista lê `flows` e `flow_required_apps` em duas consultas. `plan` segue `null` na lista.
- O dossiê do curador (`conteudo.apps` do `GET /api/aprendizado/{kind}/{ref}`) passa a usar a mesma ordem (antes, alfabética).
- O painel (Configurações, Fluxos) e a aba do curador mostram "QA Messenger → Chrome" com o nome de cada app.

Prova `simulated`: `backend/tests/test_flows_required_apps.py`; `real`: `not_run`.

## Adendo v0.83 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — `rotulo` no livro: o app de teste fora da lista padrão (RA-19, fatia A)

- `GET /api/aprendizado` aceita `rotulo` = `produto` | `qa` | `todos` (outro valor: 422). Os apps de teste são os de
  `apps.category='qa'` (o QA embutido).
  - Sem `rotulo` e sem `app`, vale `produto`: a lista esconde as entradas dos apps de teste.
  - Sem `rotulo` e com `app`, vale `todos`: o app escolhido mostra o que tem, mesmo sendo de teste.
- A resposta ganha `rotulo` (o que valeu) e `ocultos` (quantos itens os outros filtros deixavam e o `rotulo`
  escondeu). A `contagem` e o `total` são do que é mostrado.
- **Mudança visível:** a lista padrão encolhe. No central, em 03/10, eram 164 entradas, 94 do QA. Quem quer o livro
  inteiro passa `rotulo=todos`.
- `/pendentes`, `/revisar`, o detalhe e `/aprendizado/apps` não mudam: as filas de decisão e a visão por app leem tudo.

Prova `simulated`: `tests/test_learning_rotulo_do_livro.py`, `AprendizadoPage.test.tsx` e `model.test.ts`.

## Adendo v0.84 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — a sonda "o ator pensa?" no `GET /api/ai` (item 17.14)

Só campo novo, aditivo.

- `GET /api/ai` → cada item de `roles` ganha `thinking` (texto ou `null`): `adaptive` (o pedido leva `thinking` em toda
  chamada da função), `desligado_na_funcao` (`ai.roles.<f>.thinking: false`), `nao_declarado` (o modelo está declarado
  sem thinking em `ai.models`) ou `recusado_pelo_modelo` (um 400 desligou o thinking nesta instância, até reiniciar).
  `null` = provedor sem thinking (OpenAI, simulado).
- `effort` passa a ser o efetivo da função: o `ai.roles.<f>.effort` quando escrito, senão o do `.env`, como antes.

Vale para o padrão (`ai.roles`); o perfil de uma execução não aparece aqui (como o modelo, adendo do 17.7). Prova
`simulated`: `tests/test_perfil_esforco_e_dieta.py::test_sonda_do_thinking_na_aba_ia` e `::test_sonda_vazia_em_provedor_sem_thinking`.

## Adendo v0.86 (03/10/2026; número da orquestradora, `.claude/reservas.md`; item 29.45) — caminho rápido 2: `strategy` `deterministic` e o motivo `nova_tentativa` mais estreito

Sem rota nova e sem migração.

- `attempts.strategy` (trilha da 045) passa a poder começar por `deterministic`: `deterministic` sozinho quando o executor
  abriu o app da etapa `app_foreground` e ela se comprovou; `deterministic>ai_actor` quando o ator assumiu na mesma
  tentativa. Quem lê com união fechada precisa do valor (`recipe`, `ai_actor` e as cadeias já existiam). A etapa assim
  fechada segue `driven_by` = `sem_ator` (adendo v0.81). Na linha do tempo, um evento `decision` diz "aberto pelo
  executor, sem IA", com o tempo e o foco.
- `ai_calls.escalate` = `nova_tentativa` deixa de marcar toda decisão da 2ª tentativa em diante. Passa a marcar só as do
  tier 1 a partir da decisão que repetiu onde a anterior parou ou que dispararia o efeito. A série desse rótulo em
  `/api/usage` quebra no deploy que levar o 29.45.
- A verificação que termina "não comprovada" pode trazer na evidência "a tela não mudou em 3 sondagens depois do não":
  ela encerra sem esperar o fim do orçamento (LT-5).
- Série da Aprendizado: a partir do deploy 8, etapas `app_foreground` abertas sem IA não geram comparação de sombra. A
  candidata v4 de `open_app` do QA não promove por sombra.

## Adendo v0.87 (03/10/2026; número da orquestradora, `.claude/reservas.md`) — o `GET /api/ai` diz o esquema do plano, os perfis de IA e a leitura visual (I2 da validação do deploy 7)

Só campos novos, aditivos.

- `GET /api/ai` (e `health.ai`) ganha:
  - `esquema_do_plano`: `longo` | `curto` (`ai.esquema_do_plano`, LT-4b). Vem da configuração, também no modo
    simulado.
  - `profiles`: lista, vazia sem `ai.profiles`. Cada item tem:
    - `name` e `note`;
    - `roles`: só as funções que o perfil MUDA, já resolvidas (`role`, `provider`, `model`, `effort`,
      `sends_data_externally`). Sem bloco próprio, a `persona` é o `social`: o perfil que escreve o `social` muda as duas;
    - `canary_fraction`: a fatia das execuções sem perfil que vai para ele (`ai.canary`), ou `null` quando não é o
      canário;
    - `screenshot_max_side` e `rich_tree_min_elements`: os ajustes do 17.14, ou `null`.
  - `leitura_visual`: `ai.leitura_visual.enabled` (item 12.5). O leitor é a linha `leitura` de `roles`, quando
    configurado.
- Antes, o perfil só aparecia quando uma execução o usava, e o esquema só existia no YAML.
- Painel (Configuração › IA):
  - a Situação ganha "Esquema do plano", "Perfis de IA" e "Leitura visual";
  - o "Esforço de raciocínio" sai em português (baixo, médio, alto…);
  - a tabela Por função ganha as colunas "Esforço" e "Raciocínio" (a sonda do v0.84), e a linha `leitura` vira "Ler a
    tela (leitura visual)";
  - um cartão novo, "Perfis de IA", diz o que cada perfil muda, o canário e se os dados saem.
- Prova `simulated`:
  - `tests/test_aba_ia_esquema_e_perfis.py`;
  - `frontend/src/lib/aiLabels.test.ts` e `frontend/src/features/settings/AiSection.test.tsx`;
  - a tela percorrida no navegador contra um backend simulado do worktree (porta 8765, nada no central).
- **Correção junto, sem campo novo (I1 da mesma validação):**
  - `GET /api/usage`: o `total_usd` e o `usd` de cada grupo passam a contar o custo DECLARADO na linha (`ai_calls.usd`,
    a imagem da persona), como já faziam `by_account` e `by_origin`. Antes, as peças por conta somavam mais que o total:
    no central, 03/10, US$ 16,60 contra 16,34, e a diferença eram as 5 imagens da semana (US$ 0,2683; leitura só no
    banco).
  - O modelo sem preço por token cujas chamadas OK declararam todas o custo sai de `unpriced_models`.
  - Prova `simulated`: `tests/test_uso_total_com_custo_declarado.py`.
- **Campo novo no evento, sem campo novo na API (I4 da mesma validação, ajustado depois da suíte 8):**
  - a habilidade que casou e não compilou põe a execução em `needs_input` com texto para o dono no `status_detail` ("A
    habilidade “<nome>” não serve para este comando: <motivo>. Corrija o comando ou a habilidade."), sem id nem código;
  - o código de cada problema vai num campo próprio do `data` do `run.updated` dessa transição, `issue_codes`
    (`["E_SKILL_NOT_FOUND"]`, `["E_PLAN_INVALID"]`). O `log` da mesma transição segue com `skill` e `issues` completos;
  - prova `simulated`: `tests/test_needs_input_da_habilidade.py` e
    `tests/test_fatia_abrir_conversa.py::test_filha_desabilitada_poe_a_composta_em_needs_input_sem_plano`.

## Adendo v0.88 (03/10/2026; número da orquestradora; item 29.44) — `per_app` ganha `sem_trafego`

Sem rota nova e sem migração.

- `network_measurements.per_app` (e `last_measurement.per_app` em `GET /api/network/devices`) ganha o valor
  `sem_trafego`: o app instalado com 0 byte na janela, na VPN e na física.
  - `nao_medido` passa a querer dizer só "não instalado ou não lido"; antes juntava os dois casos.
  - Quem lê com união fechada precisa do valor novo. As medições gravadas antes seguem com `nao_medido` para o app
    parado.
- `trafego_verificado` passa a valer com app `sem_trafego` quando outro app (a sonda do shell conta) está `ok` e
  nenhum está `fora_da_rede`.
  - O `detail` da linha traz a ressalva: "`<app>` sem tráfego na janela: não provado, não segura o estado".
  - Nenhum app trafegou: segue `parcial`, com "nenhum app trafegou na janela" no `detail`.
  - A porta da tarefa (`apps_sem_prova`) aceita o `sem_trafego` como medido.
- A verificação que acharia o `parcial` sem prova do bloqueio não dispensa a medição quando o `parcial` veio só de
  app parado: o app pode ter trafegado desde então.

## Adendo v0.89 (03/10/2026; número da orquestradora, `.claude/reservas.md`; item 30.8) — `GET /api/aprendizado/metricas` e `GET /api/aprendizado/revisoes`

Duas rotas novas, só de leitura e sem efeito. O cálculo é feito na leitura, a partir das tabelas, sem contador novo em memória
(§10 do [desenho](design/aprendizado-vivo.md)). Sem o serviço composto, as duas respondem 503 `not_ready`.

`GET /api/aprendizado/metricas?app=&dias=14` (`dias` vai de 1 a 90). Cada bloco corresponde a uma linha da tabela do §10.
Ausente é `null`, nunca zero: uma taxa sem amostra ou um tempo sem par saem `null`, sempre com o `n` ao lado.

```json
{"app": "com.instagram.android", "janela_dias": 14, "desde": "…Z", "ate": "…Z",
 "itens": {"receita": {"published": 19, "candidate": 1}}, "por_origem": {"execucao": 44, "pessoa": 1}, "pendentes": 0,
 "aprovacoes": {"sistema": {"validated": 1}, "pessoa": {"published": 1}},
 "curador": {"revisoes": 7, "simuladas": 0, "validade": {"ok": 7, "invalida": 0, "recusada": 0},
             "decisoes": {"manter": 1, "pedir_evidencia": 6}, "aplicadas": 0, "overrides": 0, "usd": 0.0},
 "refutados_depois_de_promovidos": {"desligados_pelo_sistema": 0, "desligados_por_pessoa": 0,
                                    "publicados_com_evidencia_contra": 0},
 "sucesso_depois_de_promovido": {"a_favor": 0, "contra": 0, "taxa": null},
 "churn": {"transicoes": 12, "itens_com_transicao": 10, "criados": 36, "desligados": 1},
 "tempos": {"candidate_validated": {"mediana_h": 0.0, "p90_h": 0.0, "n": 1},
            "validated_published": {"mediana_h": null, "p90_h": null, "n": 0}},
 "saude": {"saudavel": 3, "pouca_amostra": 20, "sem_evidencia": 4, "obsoleto_provavel": 0, "…": 0},
 "economia": {"etapas": 186, "por_receita": 33, "so_ia": 123, "chamadas_evitadas_estimadas": 79.0, "…": 0},
 "falhas_evitadas_proxy": {"rotulo": "proxy", "etapas_comparadas": 9,
                           "com_receita": {"etapas": 37, "falhas": 6, "taxa_de_falha": 0.162},
                           "so_ia": {"etapas": 28, "falhas": 1, "taxa_de_falha": 0.036}},
 "orcamento_do_curador": {"modo": "on", "janela_dias": 7, "gasto_da_operacao": 11.48, "gasto_da_curadoria": 0.29,
                          "orcamento": 1.76, "teto_alfa": 8.04, "revisoes_na_janela": 21, "uso": 0.167, "aviso": false},
 "sem_item": {}}
```

- `itens`, `por_origem` e `pendentes` são a contagem do livro no recorte, a mesma composição da visão por app. A memória
  conta lembranças.
- `aprovacoes` conta as transições da janela por `to_state`. `sistema` é `decided_by = sistema`; qualquer outro autor é
  `pessoa`. A transição que não muda o estado (o "confirmar que fica" do 30.24) fica fora.
- `curador` conta as linhas do curador em `learning_reviews` (o rótulo de intenção do 30.25 fica fora). A revisão simulada
  vai só para `simuladas` e não entra em `decisoes` nem em `usd`. `decisoes` é a sugestão da IA nas revisões válidas, e
  `aplicadas` são as revisões com `decisao_final`.
- `refutados_depois_de_promovidos`: as transições `published → disabled` da janela, separadas pelo autor, mais os
  publicados com evidência contrária na janela. O `deprecated` fica fora, porque é absorção ou versão aposentada, não
  refutação.
- `sucesso_depois_de_promovido` usa só a evidência REAL datada depois do `state_at` do publicado. A evidência da execução
  marcada como inválida (30.23) também fica fora. A receita não tem evidência datada (a eficácia dela são contadores
  acumulados) e aparece pelo proxy e pela economia.
- `tempos`: para cada chegada a `validated` (ou `published`) dentro da janela, conta desde a última chegada a `candidate`
  (ou `validated`) no mesmo item. O item sem essa chegada na trilha, como a receita de antes da trilha, fica fora, e o
  `n` diz quantos pares entraram. O percentil é pelo posto mais próximo, `ceil(p·n/100)`, o do K-085 (ver o v0.94).
- `saude` traz todos os rótulos do §5.3, inclusive os zerados, e sai da mesma função da lista e do detalhe.
  `sem_evidencia` é o "nunca usado".
- `economia` é a de `taskqueue/aproveitamento.py`, com os totais ou a soma dos fluxos do pacote, reaproveitada e não
  recalculada.
- `falhas_evitadas_proxy` NÃO mede falha evitada, porque não há contrafactual. Compara a taxa de falha (`failed` ou
  `uncertain`) com receita (`recipe`, `recipe+ai`) e só com IA (`ai`), apenas nas etapas (app e `template_hash`) que
  tiveram as duas conduções na janela. Execução simulada e `sem_ator` ficam fora.
- `orcamento_do_curador` é global e usa a janela do curador (`janela_dias` do config), não a do pedido.
  - `orcamento` é o B_W com as revisões já gravadas. A volta soma os elegíveis dela, então este valor é um piso, o `uso`
    (C_W / B_W) é um teto e o `aviso` (a 80 %) sai cedo, nunca tarde.
  - Sem custo medido, o c̄ é a média das estimativas das revisões gravadas.
  - `teto_alfa` é α·G_W.
- `sem_item`: transições e evidências de itens que não estão mais no livro. Não têm app conhecido e ficam contadas à parte.
- Quebra de série: desde o deploy 8 (03/10 09:06:28Z), `app_foreground` aberta sem IA fecha `sem_ator`. Por isso
  `so_ia`, as elegíveis da economia e o proxy mudam de regime nessa data. Compare só janelas do mesmo lado.

`GET /api/aprendizado/revisoes?app=&decisao=&desde=&limite=50&cursor=` (`limite` vai de 1 a 200) lista as revisões do
curador, da mais nova para a mais velha, sem o dossiê nem a saída inteira (o detalhe do item entrega os dois).

```json
{"revisoes": [{"id": "lr-…", "criado_em": "…Z", "item_ref": "fluxo:12", "item_kind": "fluxo", "app": "com.pocqa.messenger",
               "gatilho": "pedido_da_pessoa", "validade": "ok", "simulado": false, "provedor": "…", "modelo": "…",
               "usd": 0.0, "classe": "B", "decisao": "manter", "confianca": "alta", "decisao_final": null,
               "decidido_por": null, "override": false}],
 "proximo": "2026-10-03T08:29:40.302Z|lr-…"}
```

- `decisao` filtra pela sugestão da IA, e `desde` aceita data ISO (sem fuso vale UTC).
- `proximo` é o cursor (`criada|id`) da página seguinte, ou `null`. Um cursor malformado dá 422 `cursor_invalido`.

Prova:
- `simulated`: `tests/test_learning_metricas.py` (11 testes).
- Ensaio só de leitura numa cópia do banco do central em 03/10 ~09:30Z (backup do SQLite, origem em `mode=ro`, código
  57d82a5a mais o branch): 142 ms no global e ~55 ms por app. Os números do exemplo acima saíram desse ensaio.
- `real` = a leitura no central depois de implantado.

## Adendo v0.90 (03/10/2026; número da orquestradora, `.claude/reservas.md`; item 31.16) — o bloco `decisao_fechada` de `GET /api/ai`, documentado a posteriori

Nenhum campo novo: este adendo documenta um bloco que já saía desde o 31.5 (ADR-069 item 8) e ganhou `decider` no 31.14
e `sending` no 31.17, sem adendo próprio.

- `GET /api/ai` (e `health.ai`) traz `decisao_fechada`:
  - `null` quando `ai.decisao_fechada.enabled` é falso ou nenhum consumidor está em `shadow` ou `on`. Nesse caso o
    `notice` também não fala do Jev;
  - senão, um objeto (`backend/app/planning/decisao_fechada/transparencia.py::status`):

    | Campo | Tipo | O que diz |
    |---|---|---|
    | `provider` | `"typesafe"` | o provedor externo da porta |
    | `name` | string | `"Jev (TypeSafe System One)"` |
    | `consumers` | `Record<origem, "shadow" \| "on">` | só os consumidores ligados, em ordem alfabética (`curador`, `intencao`…) |
    | `classes` | string[] | as classes de dado que PODERIAM sair: o teto do código (`JEV_ALLOWED_CLASSES`) ∩ `classes_permitidas` do YAML, em ordem (`C0`, `C1`…) |
    | `send_approved` | bool | o envio aprovado no código (`JEV_RUNTIME_SEND_APPROVED`) |
    | `key` | `"configurada"` \| `"não configurada"` | só a PRESENÇA da chave da TypeSafe; o valor nunca é lido para isto |
    | `decider` | `"nulo"` \| `"jev"` | o decisor montado na porta (31.14): `nulo` nunca chama a TypeSafe, `jev` é o real |
    | `sending` | bool | sai alguma coisa AGORA (31.17): só com as quatro condições juntas — `send_approved`, `decider: "jev"`, um consumidor ligado e a chave configurada |
    | `retention_days` | int | `ai.decisao_fechada.retencao_dias`, a retenção da sombra local |

- O `notice` de `/api/ai`, com o bloco presente, ganha uma frase que nomeia a TypeSafe, os consumidores, as classes e o que
  sai em palavras (sempre "ids e categorias"; o catálogo do dono quando a C2 vale e a `intencao` está ligada; o comando
  filtrado quando a C3 vale e a origem a manda), a chave (`configurada`/`não configurada`) e "Decisor na porta: nulo|jev".
  Termina com uma de duas:
  - "Envio ATIVO: cada pedido que passa pela privacidade sai para a TypeSafe." — exatamente quando `sending` é `true`;
  - "Nada sai agora: <motivo>." — o primeiro que falta, nesta ordem: nenhum consumidor ligado, envio fechado no código,
    decisor nulo, chave não configurada.
- Quem lê: o 31.10 confere `decider` e `sending` sem ler a configuração do central; o painel ainda não mostra o bloco
  (a Situação de Configuração › IA mostra o `notice`).
- **Telas do RA-10 (31.16), sem campo novo:** Diagnóstico › Custo de IA passa a ler as chaves do adendo v0.75 que
  só a API tinha:
  - a seção "Modelo forte e conferência": `escalations`, um motivo por linha (o maior custo primeiro, sem os zerados;
    motivo fora do vocabulário aparece cru), `rejudges` em uma linha ("N julgada(s), X % de discordância (D) · US$ em C chamada(s)";
    "nenhum veredito válido" quando só houve erro) e `cascades` em outra ("N subida(s), M desbloquearam a tela · US$");
  - dois recolhidos: "Discordância do rejulgamento, por app" (o nome do app do catálogo; `*` vira "etapa sem app";
    "27 % (3)") e "Imagem: por que foi junto (ou não)" (os motivos que mandam a imagem primeiro; "6 de 6" com imagem);
    os dois cabem no celular sem rolar de lado;
  - o aviso "Etapas sem registro de quem decidiu" só quando `steps_driven_by_null` passa de 0;
  - os dois motivos do rejulgamento (`nivel` e `sim_com_efeito`) aparecem também na lista de motivos, porque são
    subidas ao modelo forte (`escalations` agrupa `escalate` não nulo): a lista não se soma à linha do rejulgamento;
  - com servidor anterior ao v0.75 (sem as chaves) ou período sem nada disso, a seção e o aviso não aparecem.
  - Prova `simulated`: `frontend/src/features/usage/usage.test.ts` (o bloco "RA-10") e
    `frontend/src/features/diagnostics/DiagnosticsPage.test.tsx` (o bloco "Custo de IA: o RA-10").
## Adendo v0.91 (03/10/2026; número da orquestradora, `.claude/reservas.md`; item 30.31, item 0) — "devolver à prova"

`POST /api/aprendizado/{kind}/{ref}/status {to: "candidate", reason}` passa a valer para **fluxo `disabled`**, e só por
pessoa (a sessão do painel). Nenhuma rota nova e nenhum campo novo:
- **Entrada do livro:** `acoes` do fluxo desligado ganha `{"to": "candidate", "rotulo": "devolver", "exige_motivo": true}`,
  ao lado de `reativar`. Clientes antigos mostram a chave como veio.
- **Efeito:** o fluxo fica `candidate`, inerte (o casamento de comandos só usa o ativo), e sai do veto, porque a última
  decisão da pessoa já não é desligar. A sombra só conta a evidência observada a partir da volta, a favor e contra. Com
  efeito externo, o fluxo para em `validated`, como sempre.
- **Recusas** (409 `transition_forbidden`):
  - receita, lição e tela não têm "devolver";
  - o sistema nunca devolve;
  - de qualquer outro estado não existe `→ candidate`.
- **Parecer do curador:** aceitar não gera "devolver". Na concordância, a decisão da pessoa "devolver" vale como esperar
  (como `observar`/`pedir_evidencia`).
## Adendo v0.92 (03/10/2026; número da orquestradora, `.claude/reservas.md`; item I1 da suíte 10) — `GET /api/ai/balances`: a conta `typesafe` e `closed_decision`

A lista de `accounts` já trazia a conta `typesafe` (TypeSafe, o Jev; ADR-069) desde o 31.5. Este adendo a documenta e
acrescenta um campo a cada conta:

```json
{"account": "typesafe", "label": "TypeSafe (Jev, System One)", "currency": "USD", "roles": [], "image": false,
 "closed_decision": "shadow", "in_use": true, "anchor_balance": 5.0, "spent_since_usd": 0.003,
 "estimated_balance": 4.997, "state": "ok", "…": "…"}
```

- `account` vale `anthropic`, `openai`, `gemini` ou `typesafe`.
- `closed_decision` diz se a decisão fechada (a porta `DecisaoFechada`, Fase 31) usa a conta, e em que modo:
  - `"shadow"`: com `ai.decisao_fechada.enabled`, o `decisor: jev` e um consumidor em `shadow`, e nenhum em `on`;
  - `"on"`: algum consumidor em `on`;
  - `null`: em todos os outros casos. As outras contas são sempre `null`. Com o decisor `nulo`, que nunca chama, também.
  É a configuração, não a garantia de envio: se algo sai agora, quem diz é o `sending` do bloco `decisao_fechada` de
  `GET /api/ai`.
- `in_use` passa a ser `roles` não vazio, OU `image`, OU `closed_decision` não nulo.
- A decisão fechada NÃO entra em `roles`. O laço de pedidos adia o despacho pelo saldo das contas de `roles`
  (item 28.6), e o saldo do Jev não segura execução.
- `spent_since_usd` da `typesafe` já somava as linhas de `ai_calls` com o provedor `jev` (a sombra grava o `usd`
  declarado). O painel mostra esse consumo com até 4 casas abaixo de um centavo.

Prova:
- `simulated`: `tests/test_decisao_fechada_sombra.py::test_a_typesafe_diz_que_a_decisao_fechada_a_usa_e_mostra_o_consumo_do_jev`;
  `frontend/src/lib/aiBalance.test.ts`; navegador contra o backend simulado do worktree.
- `real`: `not_run`.

## Adendo v0.93 (03/10/2026; número da orquestradora; item 30.34-A) — `curador.autopublicacao` nas métricas e o config da autopublicação

Aditivo ao v0.89. Nenhuma rota nova e nenhuma migração. Com o serviço da autopublicação composto (o `AppState` sempre o
compõe), o bloco `curador` de `GET /api/aprendizado/metricas` ganha a chave `autopublicacao`, que é o balanço da sombra
(ADR-054, emenda de 03/10):

```json
"curador": {"revisoes": 7, "…": 0,
            "autopublicacao": {"modo": "shadow", "casos": 3, "abertos": 3, "limpos": 0, "regrediram": 0,
                               "taxa_sem_regressao": null, "libera": false,
                               "limiares": {"casos_fechados": 30, "taxa_sem_regressao": 0.9, "janela_dias": 7}}}
```

- O balanço é global: não depende de `app` nem de `dias`. Cada caso tem a janela própria de 7 dias, contada da marca.
- `casos` são os itens distintos que a regra publicaria, cada um marcado uma vez (o sinal `autopublicaria`).
  - `abertos` ainda estão na janela.
  - `limpos` fecharam sem regressão.
  - `regrediram` tiveram, na janela, evidência real contra ou em conflito, o item desligado, ou o parecer recusado por
    uma pessoa.
- `taxa_sem_regressao` = limpos / (limpos + regrediram), ou `null` sem caso fechado. O aberto não entra.
- `libera` = ≥ 30 casos fechados e taxa ≥ 0,9. É o que o relatório à orquestradora lê antes de virar `on`.
- O sinal `autopublicaria` fica fora de `GET /api/aprendizado/sinais` sem `kind`, porque não é gesto de pessoa.

Config (`aprendizado.autopublicacao`): `modo: "off" | "shadow"` (`off` de fábrica) e `intervalo_s` (3600). `on` é
recusado na carga até a 30.34-B. O central liga `shadow` no deploy 11 (decisão da orquestradora, 03/10).

Prova:
- `simulated`: `tests/test_learning_autopublicacao.py` e `tests/test_learning_autopublicacao_sombra.py`.
- `not_run`: a sombra no central (deploy 11).

## Adendo v0.94 (03/10/2026; número da orquestradora; item 30.33-C) — o item de mais de um app na leitura por app; título e nomes nas revisões; os ramos do orçamento; o desfecho em 14 dias

Aditivo aos v0.47, v0.89 e v0.93. Nenhuma rota nova e nenhuma migração. Origem: a validação do deploy 10 viu o fluxo
que lê o Outlook arquivado sob o Instagram. Não era dado errado: o `app_id` do fluxo é o app PRINCIPAL, onde rodam as
etapas sem app próprio. A leitura por app é que só olhava o principal.

**Livro** (`GET /api/aprendizado`, o detalhe e as linhas de `GET /api/aprendizado/apps/{pacote}`):
- cada entrada ganha `apps`, os pacotes do fluxo que atravessa apps na ordem em que o plano os usa. Só vem preenchido
  com mais de um app; no resto é `[]`. Com `apps` preenchido, a linha leva também `apps_nomes`, na mesma ordem;
- `app` continua o principal;
- o filtro `?app=` e a visão por app põem o item em cada app de `apps`. O rótulo QA/PRODUTO continua pelo principal;
- o fluxo sem principal resolvido continua só no balde `nao_resolvido` (30.2).

**Métricas** (`GET /api/aprendizado/metricas?app=`):
- com `app`, o recorte do livro, a saúde, as revisões do curador e a economia contam o fluxo multi-app em cada app
  dele;
- `orcamento_do_curador` ganha três chaves:
  - `pelas_revisoes`: o ramo k·N_W·c̄;
  - `ramo`: qual dos dois manda no mínimo, `"operacao"` ou `"revisoes"`;
  - `k`;
- `orcamento` continua o menor dos dois ramos, e `teto_alfa` continua o ramo da operação (α·G_W). Enquanto o ramo das
  revisões manda, o `uso` fica perto de 1/k por construção.

**Revisões** (`GET /api/aprendizado/revisoes?app=`):
- com `app`, entram também as revisões dos itens multi-app que usam esse app sem ser o principal. O `scope_app` é o
  gravado, o principal;
- cada linha ganha:
  - `app_nome`;
  - `titulo`, o título do item no livro, ou `null` se o item saiu dele;
  - `etapa`, `capability` e `capability_nome`, para nomear a receita como no catálogo;
  - `apps` e, só no multi-app, `apps_nomes`;
  - `resultado_posterior` e `resultado_em` (30.35), `null` enquanto a janela de 14 dias não fecha.

**Pareceres do item** (`pareceres[]` do detalhe): ganham `resultado_posterior` e `resultado_em`.

```json
{"id": "lr-6a9d1102ca65ceec", "item_ref": "fluxo:ler-no-outlook-o-assunto-do-e-mail-mais-", "app": "com.instagram.android",
 "app_nome": "Instagram", "titulo": "No Outlook, abra a caixa de entrada e leia o assunto…", "etapa": null,
 "capability": null, "capability_nome": null, "apps": ["com.microsoft.office.outlook", "com.instagram.android"],
 "apps_nomes": ["Microsoft Outlook", "Instagram"], "resultado_posterior": null, "resultado_em": null, "…": "…"}
```

**Dossiê do curador:**
- no fluxo de mais de um app, `item.apps` vira `[{"id", "pacote", "principal"}]`, na ordem do plano, e
  `item.principal_e` diz o que é o principal. O curador tinha lido `item.app` (pacote) contra `conteudo.apps` (ids)
  como divergência;
- no item de um app só nada muda: as mesmas chaves, o mesmo `dossie_hash` e `versao_do_dossie` 1;
- no central, só os três fluxos multi-app ganham revisão nova, até ~US$ 0,03 (autorizado pela orquestradora).

**Validação** (30.31):
- o pedido de um item multi-app só nasce `qa` se TODOS os apps forem de QA (a regra "mais restritivo");
- o despachante só oferece o aparelho que tem todos os apps do item prontos.

**Percentil de `tempos`** (v0.89): segue o `app/metricas.percentil` do K-085, posto `ceil(p·n/100)` em aritmética
exata. A mediana de dois valores passa a ser o MENOR (o `round` de antes levava o ,5 ao par e dava o maior). Um teste de
igualdade prende as duas fórmulas, porque a camada de aplicação não importa `app.metricas`.

Prova:
- `simulated`:
  - `tests/test_learning_multi_app.py`;
  - `tests/test_learning_validacao_sql.py` (dois casos novos);
  - `tests/test_learning_metricas.py` (o percentil igual ao do processo, n de 1 a 200);
  - `MetricasTab.test.tsx` e `AplicativosTab.test.tsx`;
- percurso no navegador sobre uma CÓPIA do banco do central de 03/10, com o backend do worktree em provedor simulado;
- `not_run`: o central.

## Adendo v0.95 (03/10/2026; número da orquestradora; item 29.50) — a pergunta sem resposta expira pelo sistema: `expirada` no `run.updated`

Nenhuma rota nova, nenhum status novo e nenhuma migração. Uma execução em `needs_input` há 24 h sem resposta passa a
`cancelled` pelo SISTEMA (`RunService.expirar_sem_resposta`, no laço de 10 min do líder da trava `retencao`). É a única
transição que `needs_input` já permitia.

- O prazo conta da entrada na pergunta (o `run.updated` daquela transição), não de `created_at`. Sem evento nenhum, vale
  `created_at`.
- `status_detail` é o motivo humano, sem código nem id: "Sem resposta em 24 h: a pergunta expirou e a execução foi
  encerrada pelo sistema. Para seguir, faça o pedido de novo."
- O `data` do `run.updated` dessa transição ganha um campo próprio, como o `issue_codes` do v0.87:

```json
{"run": {"status": "cancelled", "…": "…"},
 "expirada": {"motivo": "sem_resposta", "horas": 24, "desde": "2026-10-02T18:15:30.525Z"}}
```

- `desde` é o `ts` do evento de entrada. `cancel_requested` vira 1 e `finished_at` é preenchido, como em todo
  `cancelled`.
- Sem o sinal `cancelou_execucao` (ADR-054): ninguém fez o gesto. O cancelamento pela rota (`POST /api/runs/{id}/cancel`)
  segue igual.
- Se a pessoa responde no meio da varredura, a resposta ganha: o cancelamento é condicional ao `needs_input`, e o
  `status_detail` "Respondida: continua na execução …" fica.

Prova:
- `simulated`: `tests/test_needs_input_expira.py`.
- `not_run`: a varredura no central depois do deploy.

## Adendo v0.96 (03/10/2026; número da orquestradora; item 30.36) — a divergência de forma e a receita sem caminho

Aditivo aos v0.47, v0.70 e v0.89. Nenhuma rota nova e nenhuma migração. Origem: a 1ª validação do P4 (03/10,
r-20261003160725-213aae) comprovou 2/2 etapas, e a sombra do fluxo gravou `against` só porque o planejador reescreveu
as pós-condições.

**Evidência** (`evidencias[].stance` do detalhe `GET /api/aprendizado/{kind}/{ref}`):
- ganha o valor `forma`: a execução fez o caminho do fluxo e só reescreveu a forma, isto é, a pós-condição de uma etapa
  sem efeito ou um parâmetro que nenhuma etapa usa para agir;
- `forma` não conta contra nem a favor;
- o `detail` leva os tipos e os nomes: `"[marca] etapa 1: pós-condição reescrita (app_foreground × element_present)"`;
- a reclassificação de uma linha antiga leva `"[marca] reclassificada: …"`;
- um `against` com uma `forma` da MESMA `origin_ref` continua na lista, mas sai de toda contagem de contra: a saúde, as
  Métricas, a regressão da autopublicação (v0.93) e o dossiê do curador.

**O que a execução ensinou** (o bloco da execução): o papel do item diz "evidência de forma (não conta)", e o `against`
que a forma corrigiu some.

**Dossiê do curador** (só quando vale; nos outros, as mesmas chaves e o mesmo `dossie_hash`):
- `evidencias.forma_e`, a nota do que é a posição `forma`, quando alguma evidência incluída é `forma`;
- `item.sem_caminho`, a nota "variante sem caminho; sugerir aposentar", na receita cuja validação fechou `sem_caminho`.

**`learning_validations.motivo`** (sem CHECK, vocabulário do domínio) ganha:
- `evidencia_contra`: a execução deixou evidência contra. Dispara o curador com o gatilho `evidencia_chegou`;
- `divergencia_de_forma`: a execução só reescreveu a forma;
- `sem_caminho`: o plano do fluxo ativo do comando não alcança a etapa da receita. O pedido fecha sem execução, ao
  nascer ou ao despachar, e o curador volta ao item.

O pedido fechado `sem_evidencia` passa a um desses motivos quando a evidência chega depois, ou quando a receita não tem
caminho.

Prova:
- `simulated`: `tests/test_learning_forma.py` e `DetalheRico.test.tsx`;
- `not_run`: o central (a reclassificação das linhas do P4 de 03/10).

## Adendo v0.97 (03/10/2026; número da orquestradora; item 30.37) — a execução de prova de fluxo: `RunSummary.prova_fluxo_id`

Aditivo aos v0.47, v0.89 e v0.96. Nenhuma rota nova; migração 084 (`runs.prova_fluxo_id`,
`learning_validations.teto_usd`). Origem: o K-086 (a validação por re-execução não deixava evidência para o fluxo ativo
e media o planejador livre no candidato).

**`RunSummary.prova_fluxo_id`** (`string | null`, também no `RunDetail`):
- o id do fluxo que a execução PROVA, ou `null` numa execução comum;
- a execução de prova roda o plano do próprio fluxo com os parâmetros do comando de origem, sem planejador;
- aparece como "Prova de fluxo (validação)": nunca como comando de pessoa, nunca aviso, nunca o último comando do
  cartão;
- sem `flow_id` nem `skill_hash` nela, e a sombra da intenção não a vê;
- se a prova precisaria de uma pessoa (`needs_input`, `approval_required`, incerteza), o SISTEMA a encerra na hora
  (`cancelled`, sem `cancelou_execucao`, sem pergunta pendente).

**Config** `aprendizado.validacao.teto_por_pedido_usd` (padrão `0.10`, `0..10`):
- o teto de IA de UM pedido de validação, gravado em `learning_validations.teto_usd`;
- o roteador o aplica como o teto do pedido do 28.6 (`teto_usd_da_execucao`: o menor dos dois);
- vale também para a reabertura;
- o teto total do P4 não muda (US$ 3,09).

**`learning_validations.motivo`** (sem CHECK; os valores do v0.96 seguem):
- `sem_caminho` passa a valer também para o FLUXO cujo comando de origem não cabe mais no molde (ao nascer ou ao
  despachar); só a receita `sem_caminho` volta ao curador (`chegadas`);
- `sem_evidencia` (já existia) é também o fechamento da prova que não deixou evidência (infra: erro de IA, teto, aparelho, etapa que pediria pessoa);
- o pedido de FLUXO fechado `sem_evidencia` ou `divergencia_de_forma` numa execução comum ganha UM pedido novo (a
  reabertura), que roda como prova.

**Evidência** (`evidencias[]` do detalhe `GET /api/aprendizado/{kind}/{ref}`):
- a linha da prova tem `origin_ref = "run:<id da execução de prova>"`, e o `detail` abre com a marca do conteúdo do fluxo;
- `for`: execução `completed` com todas as etapas comprovadas; `against`: uma etapa `failed` na própria pós-condição;
- vale também para o fluxo ativo; o veredito do D1 só avalia o que ainda está em prova.

Prova:
- `simulated`: `tests/test_learning_prova.py`;
- `not_run`: o central (a 1ª validação de fluxo depois do deploy que levar o 30.37).
## Adendo v1.00 (03/10/2026; número da orquestradora; item 30.38) — validação e pareceres com a verdade no painel

**Parcial:** só a (c) está aqui; a (a), a origem nas execuções de validação, e a (b), a rota de leitura dos pedidos
(`learning_validations`), entram na suíte 14. Aditivo ao v0.47 (30.17). Nenhuma rota nova nem migração na (c).

**(c) A classe do parecer pendente é a de AGORA, a do gesto.** Vale no detalhe (`GET /api/aprendizado/{kind}/{ref}`,
`pareceres[]` com `atual: true`) e na fila (`GET /api/aprendizado/pendentes`, `itens[].parecer`):

- `classe` é a mais restritiva entre a gravada e a do dossiê de agora, a mesma que `conferir_gesto` usa no aceite.
  `recusa` e `recusa_no_lote` saem dela.
- `classe_no_parecer` (novo, `"A" | "B" | "C" | null`) é a classe gravada no parecer, só quando difere de `classe`.
  Nos outros casos é `null`.
- Os pareceres que não são o atual seguem com `classe` = a gravada e `classe_no_parecer: null`.

```json
{"id": "lr-…", "decisao": "aprovar", "classe": "C", "classe_no_parecer": "B", "recusa": null,
 "recusa_no_lote": "lote_na_classe_c"}
```

Origem: a validação do deploy 12 no navegador. Os 9 fluxos do Instagram com parecer de antes do 30.32 (deploy 10)
apareciam "Classe B · aceite em lote", e o clique voltava 409 `lote_na_classe_c`.

Prova:
- `simulated`: `tests/test_learning_pareceres.py` (`test_a_classe_de_agora_endurece_o_aceite`) e `ParecerDaIA.test.tsx`
  (o selo "era B no parecer" e a linha da fila fora do lote);
- `not_run`: o painel do central depois do deploy.
## Adendo v1.01 (03/10/2026; número da orquestradora; polimentos 3, 5 e 6 do Chrome do deploy 12) — texto para o dono em três respostas existentes

Nenhuma rota nova, nenhum campo novo e nenhuma migração. Três textos e um nome que já saíam mudam de valor. O texto
antigo do v0.87 fica como histórico.

- **`status_detail` do fluxo ou da habilidade que casou e não serviu** (o `needs_input` do v0.87, sem pergunta):
  - fluxo salvo (`flow:…`): "O fluxo salvo “<nome>” não serviu para este pedido: <motivo>. Responda à pergunta ou peça de
    novo pelo painel.";
  - skill publicada: começa com "A habilidade salva".
  - Sem nome, sai sem as aspas. Antes era "A habilidade “<nome>” não serve para este comando: … Corrija o comando ou a
    habilidade."
  - O código e o id continuam fora do texto, só no `issue_codes` do `run.updated` e no `log` da mesma transição
    (`skill`, `issues`).
- **`capability_nome` da linha sem app** (`GET /api/aprendizado/sinais`, e também as linhas de falhas e intenção que
  passam por `nomear()`):
  - o sinal `aprovacao_decidida` chega sem `app_package`. Agora o nome vem do catálogo que declara a capability, mas só
    se UM app a declara (`TitulosDoCatalogo.apps_que_declaram`). Com dois apps ou nenhum, `null`.
  - O app `*` segue `null`.
  - Na aba Sinais, a decisão de aprovação ainda sem nome mostra "decisão de aprovação", com o código só na dica (`title`).
- **`notice` de `GET /api/ai`**: a frase da leitura visual diz "é enviado ao provedor “…”", ou "a um endpoint que não sai
  desta máquina". Antes saía "é enviado a o provedor".

Prova `simulated`:
- `tests/test_needs_input_da_habilidade.py` e `tests/test_fatia_abrir_conversa.py`;
- `tests/test_learning_capability_na_linha.py`, com o registro real: REPLY_COMMENT só no Instagram;
- `tests/test_leitura_visual_papel.py`;
- `frontend/src/features/aprendizado/AprendizadoPage.test.tsx`.
## Adendo v0.98 (03/10/2026; número da orquestradora; item 28.15, ADR-071) — a conversa de volta pelo Telegram: config, saúde e o conteúdo do aviso

Nenhuma rota nova: o central PERGUNTA ao Telegram (long-poll do `getUpdates`) e nunca é chamado por ele. Muda o
seguinte, todo desligado de fábrica.

- **Config:** `avisos.entrada`, com `enabled` (false), `limite_por_min` (10), `max_chars` (1000), `long_poll_s` (50),
  `espera_conflito_s` (60), `ttl_previa_s` (900: o botão Executar de prévia mais velha não cria nada) e `idade_max_s`
  (900: a mensagem escrita há mais que isto, com a Central fora, fica `ignorada` sem texto). Ela só vale com
  `avisos.enabled`. O chat aceito é o `TELEGRAM_CHAT_ID` do `.env`, só em conversa privada e com o `from.id` igual a
  ele; nenhum chat_id mora na config.
- **`GET /api/health`:** três problemas novos, que somem quando a leitura volta a funcionar:
  - `telegram_entrada_conflito`: o 409 do `getUpdates` (outro processo lê o mesmo bot);
  - `telegram_entrada_recusada`: 401, 403 ou 404, com a causa de cada um no `hint`;
  - `telegram_entrada_pedido_invalido`: o 400 (não é o token).
- **O aviso** (`avisos_entregas.corpo` e a mensagem). Com `avisos.entrada.enabled`:
  - `approval.pending` leva o resumo, "Alvo: …" e "Texto: “…”";
  - `run.needs_input` leva o `status_detail` (a pergunta).
  Os dois passam pelo redator de credencial e são cortados em 500 caracteres, terminando em "…". Depois vem a instrução
  de resposta ("Responda a esta mensagem com sim ou não (ou /vetar <motivo>)." ou "Responda a esta mensagem com a
  resposta."). Os outros tipos não mudam. Com a entrada desligada, nada muda (28.11).
- **Gestos pela conversa:** a decisão grava `decided_by = telegram:dono`, e a auditoria e o sinal contam o gesto como de
  pessoa. A execução criada pela conversa usa a chave de idempotência `telegram:<update_id>`. A sucessora da resposta ao
  `needs_input` é a mesma do painel.
- **Banco:** a migração 085 cria `canal_entradas` e `canal_enviadas` (genéricas por canal; `docs/banco.md`).

Prova `simulated`: `tests/test_telegram_entrada.py`, `tests/test_canais_contrato.py`, `tests/test_avisos_servico.py`,
`tests/test_telegram_portas.py`, `tests/test_telegram_correcoes.py` e `tests/test_telegram_revisao_e.py` (as duas
revisões do PR #166). `not_run`: a conversa real.

## Adendo v1.03 (03/10/2026; número da orquestradora; item 29.52) — a resposta com credencial é recusada pelo contexto

Nenhuma rota nova e nenhuma migração. Um código de erro novo, um evento novo e duas leituras públicas no caminho comum
(painel e canais). O ADR-040 continua valendo: a senha mora na conta da persona e a automação a digita por
`type_secret`. A resposta a uma pergunta vira comando de
uma execução sucessora, que vai ao prompt do planejador e ao histórico; por isso a credencial não entra por ali. Na
dúvida, recusa.

- A mensagem aponta o caminho certo e nunca repete a resposta. Sai em:
  - `POST /api/runs/{id}/successor`, antes de qualquer gravação: nenhuma execução nova nasce e a antiga segue em
    `needs_input`. Recusa quando:
    - alguma pergunta aberta da execução pede credencial, seja qual for a resposta;
    - ou, sem pergunta assim, o que a resposta acrescentou ao comando (`original + "\n" + resposta`, ou as palavras novas
      do texto refinado) tem cara de credencial ou é um código solto de 4 a 8 dígitos.
  - `POST /api/commands/refine`, antes da chamada de IA. Em cada resposta, recusa quando a pergunta que vem no corpo
    (`question` e `field`) pede credencial, ou quando a própria resposta tem cara de credencial. É aqui que a resposta
    do painel iria ao modelo, antes da sucessora.
  - `POST /api/runs` (criação, sem o laço de pedidos): quando o comando é uma palavra só, sem espaço, e há uma pergunta
    de senha ou código aberta para os aparelhos do pedido. Sem aparelho explícito no pedido (por persona ou
    distribuição), qualquer pergunta aberta conta. É a senha mandada como pedido novo no lugar da resposta.
  - O `credencial_no_comando` segue como antes, para o formato (`senha: …`) no comando ou numa resposta, e é conferido
    primeiro.
- **Evento `pergunta_sensivel`** (`level: warn`): sai quando uma execução entra em `needs_input` com uma pergunta que
  pede credencial (pergunta do plano ou da habilidade). Leva só o `run_id` e `data: {"tipo": …}`, sem o texto da
  pergunta, e mede quantas vezes o planejador pede o que não devia (ADR-040). O painel o lê para trocar a caixa de
  resposta pela orientação, com o botão "Abrir Personas" (senha) ou "Abrir o aparelho" (código). Uma execução anterior
  ao 29.52 não tem o evento: a caixa aparece e a recusa vem pelo 409.
- **Leituras públicas** (`backend/app/taskqueue/perguntas.py`). As duas devolvem o tipo ou `None`, sobre a mesma regra
  (`TriagemDeCredencial.pergunta_sensivel` e `resposta_recusada`), e nenhuma devolve nem registra o texto. Os canais
  usam as mesmas, nunca um vocabulário próprio.
  - `pergunta_sensivel_da_execucao(db, run_id)`: a pergunta aberta desta execução pede credencial? Uma execução que não
    existe ou não está em `needs_input` dá `None`.
  - `pergunta_sensivel_aberta(db, instance_ids=())`: existe agora alguma pergunta assim? Com `instance_ids`, contam as
    execuções desses aparelhos mais as que ainda não têm aparelho.

Prova `simulated`:
- `backend/tests/test_resposta_com_credencial.py`: o vocabulário; o evento sem o texto; a sucessora e o refinamento
  pelas rotas HTTP do painel; as duas leituras; a palavra solta;
- `frontend/src/features/runs/RunView.test.tsx`: senha, código e a execução sem o evento;
- percurso no navegador contra o backend simulado do worktree (porta 8766), com as duas orientações, os dois botões e o
  409 de formato no assistente da resposta.

A cobertura do canal (Telegram e Trello pelas mesmas leituras) entra no commit de integração da suíte 14.

## Adendo v1.05 (03/10/2026; número da orquestradora; item 29.54, ADR-073) — o painel estático muda de `/` para `/central/`

Nenhuma rota de API nova ou alterada, nenhum campo novo e nenhuma migração. Muda onde o painel estático é servido e a
saúde ganha um problema.

- **Painel estático em `/central/`.** Antes era servido na raiz. Agora:
  - `GET /` e `GET /central` (e `HEAD`) respondem **307** com `Location: /central/` (relativo, de propósito: atrás do túnel
    TLS o esquema que o processo enxerga é `http`);
  - `GET /central/` serve o `index.html` e `/central/assets/*` os bundles, com os mesmos cabeçalhos de cache de antes
    (`no-cache, must-revalidate` no `index.html`; `public, max-age=31536000, immutable` no que tem hash no nome);
  - fora de `/api` e `/central` nenhum caminho serve arquivo (`/index.html`, `/assets/...` e `/favicon.svg` na raiz dão
    404). Sem `frontend/dist`, `/` e `/central/` seguem 404.
  - O frontend é construído com `base: '/central/'`; `API_BASE` continua `/api` e o WebSocket continua `/api/ws`.
- **Portão inalterado.** Com o `Host` declarado em `server.public_hosts` e sem credencial, passam só o que não começa com
  `/api/` (agora `/central/` e o redirecionamento da raiz) e `/api/login`, `/api/logout`, `/api/session`; o resto de
  `/api` responde 401 e `Host` não declarado responde 403 `forbidden_host`. O POST do login de uma origem fora de
  `server.allowed_origins` responde 403 `forbidden_origin`.
- **Docs da API sob `/api/`.** `/docs`, `/redoc`, `/openapi.json` e `/docs/oauth2-redirect` (que o portão tratava como
  estático e abriam sem credencial pelo Host público) agora são `/api/docs`, `/api/redoc`, `/api/openapi.json` e
  `/api/docs/oauth2-redirect`: 401 de fora sem credencial, livres no loopback; os caminhos antigos dão 404.
- **WebSocket do worker pela porta do painel.** Com `server.worker_port != 0`, `/api/worker/ws` e `/api/worker/midia`
  nessa porta recusam (4403) `Host` público e aceitam só loopback; com `worker_port: 0` nada muda. O listener dedicado
  não muda.
- **`Cache-Control` do ícone de release** (`GET /api/releases/{id}/icon`) passa de `public` para `private`.
- **`GET /api/health`: problema novo `exposicao_publica_incompleta`.** Aparece quando há nome em `server.public_hosts` e
  falta qualquer uma de: `API_TOKEN`, `server.tls_behind_proxy` (ou TLS direto) e a origem `https://<host>` em
  `server.allowed_origins`. O `message` lista só o que falta, pelo nome da chave de configuração; nunca valor de segredo.

Prova `simulated`:
- `tests/test_painel_estatico.py` (redirecionamentos, cache, nada estático na raiz);
- `tests/test_canal_do_worker.py` (listener dedicado, sem mudança);
- `tests/test_portal_publico_central.py` (portão com o painel em `/central`, login e origem, problema da saúde);
- `tests/test_autenticacao.py`, `tests/test_sessao_do_painel.py` e `tests/test_tls.py` (portão e sessão, sem mudança).

Prova `real`: `not_run` (o túnel no ar é da orquestradora; ver `operacao.md`, "Portal público pelo túnel da Cloudflare").

## Adendo v1.06 (03/10/2026; número da orquestradora; item 31.13) — a R5 travada não aparece em `decisao_fechada.consumers`

Nenhum campo novo e nenhuma forma muda. Muda quem aparece no bloco `decisao_fechada` de `GET /api/ai` (adendo v0.90) e
no `notice`.

- **A origem `apps` ganha consumidor** (os apps do comando, R5, 31.13), só em `shadow`.
  - Ela já estava no vocabulário (`contrato.Origem` e `ai.decisao_fechada.consumidores`), sem consumidor.
  - Agora entra também na lista das origens que podem mandar C3 (`privacidade.C3_ORIGENS`).
- **`transparencia.consumidores_ativos` omite `apps` enquanto a trava de código `privacidade.R5_LIBERADA` é falsa.**
  Ela foi falsa de fábrica até 05/10/2026 e virou `True` com o sim do dono ao P-013 (31.13, só sombra): agora `apps`
  aparece quando o YAML o põe em `shadow`. O texto abaixo descreve o comportamento com a trava fechada, que continua valendo
  se a chave voltar a `False`.
  - Por quê: travada, a R5 não lê o cadastro nem monta pedido, e nada dela sai.
  - Anunciar o consumidor prometeria uma exposição que não acontece. É a regra do 31.17: verdade antes de conforto.
- **Consequências, com a R5 travada:**
  - com só `apps` ligado no YAML, `decisao_fechada` é `null` e o `notice` não fala do Jev;
  - com outro consumidor ligado, `consumers` não traz `apps`;
  - `sending` e o "Nada sai agora" não contam a R5;
  - quem mais lê `consumidores_ativos` também não a vê: o `closed_decision` da conta `typesafe` em
    `GET /api/ai/balances` (adendo v0.92) e a retenção da sombra.
- **Destravada** (`R5_LIBERADA = True`: um commit, com suíte e deploy, só depois do GO do 31.10):
  - `consumers` traz `"apps": "shadow"`;
  - o `notice` diz "apps em sombra";
  - com C2 e C3 nas classes efetivas, entre o que sai aparecem os nomes do catálogo do dono (os apps cadastrados, nas
    perguntas) e o comando filtrado (o estado da R5 é SÓ o comando).
- Prova `simulated`: `backend/tests/test_decisao_fechada_apps.py::test_transparencia_nao_anuncia_a_r5_travada` e
  `backend/tests/test_decisao_fechada_sombra.py::test_aviso_nomeia_a_typesafe_e_as_classes_so_com_consumidor_em_shadow_ou_on`.

## Adendo v1.07 (03/10/2026; número da orquestradora; item 30.42) — a prova de fluxo parte de um estado conhecido e a posição `invalida`

Aditivo aos v0.96 e v0.47. Nenhuma rota nova e nenhuma migração. Origem: no P4 de 03/10, uma prova mandou a mensagem
duas vezes e virou evidência a favor (ev:48), e outra herdou a tela da execução anterior e virou contra.

**Evidência** (`evidencias[].stance` do detalhe `GET /api/aprendizado/{kind}/{ref}`):
- ganha o valor `invalida`: a execução de prova não vale como evidência do fluxo. Não conta contra nem a favor;
- o `detail` começa pelo motivo, de vocabulário fechado: `"[marca] invalida:<motivo> — texto"`, com `<motivo>` em
  `efeito_repetido`, `ponto_de_partida` ou `ator_sem_acao`;
- um `for` ou `against` com uma `invalida` da MESMA `origin_ref` continua na lista, mas sai de toda contagem: a saúde,
  as Métricas, a regressão da autopublicação (v0.93), o `a_favor` do fluxo e o dossiê do curador;
- a reclassificação de uma prova antiga leva `"… (reclassificada)"` no texto.

**O que a execução ensinou** (o bloco da execução): o papel do item diz "inválida (<motivo>; não conta)".

**Linha do tempo da execução de prova**: uma decisão "Prova de fluxo (validação): ponto de partida…" antes da 1ª etapa;
a etapa que falha fecha sem replanejar, com "a prova não replaneja, porque um plano novo não é mais o fluxo".

**Dossiê do curador** (só quando vale): `evidencias.invalida_e`, a nota do que é a posição `invalida`, quando alguma
evidência incluída é `invalida`.

**`learning_validations.motivo`** (sem CHECK, vocabulário do domínio) ganha, todos com `estado = recusada`:
- `efeito_repetido`, `ponto_de_partida`, `ator_sem_acao`: a prova deixou a linha `invalida` com esse motivo;
- `limite_de_provas`: o item já teve 2 provas da mesma versão do conteúdo em 7 dias; fecha ao despachar, sem execução;
- `sem_aparelho_novo`: a falta pede outro aparelho e nenhum aparelho que serve ficou fora dos já usados; fecha ao
  despachar, sem execução.

Nenhum deles devolve o item ao curador nem reabre o pedido. `sem_aparelho_novo` e `plano_acima_do_teto` (30.41) já
existiam no banco do central, gravados à mão em 03/10.

**Contrato com a Android (29.58)**: `steps.result.efeito_repetido = {"copias": int >= 2, "fonte": "verificador" |
"acoes"}` na etapa onde a repetição foi vista; chave AUSENTE sem repetição. Quando existe, vence a regra própria do
aprendizado.

**Rollback**: o código anterior lê `stance` como enum e quebra com a linha `invalida`.

Prova:
- `simulated`: `backend/tests/test_learning_prova_veredito.py`, `test_learning_reclassificacao_efeito.py`,
  `test_learning_prova_ponto_de_partida.py`, `test_learning_prova_limites.py`.
- `not_run`: a reclassificação da ev:48 e a 1ª prova do P4 depois do deploy.

## Adendo v1.02 (03/10/2026; número da orquestradora; item 30.38, partes (a) e (b)) — a origem da execução e a leitura dos pedidos de validação

Aditivo aos v0.97 e v1.00 (a parte (c) fica no v1.00, sem mudança de texto). Nenhuma migração e nenhuma config nova.

**(a) `RunSummary.origem` e `RunSummary.origem_ref`** (também no `RunDetail`), derivados da linha pela regra única de
`app/contracts/origem.py`, sem coluna nova:
- `origem`: `"prova_fluxo" | "validacao_qa" | "telegram" | "trello" | null`;
- `prova_fluxo_id` não nulo → `prova_fluxo`, com `origem_ref` = o fluxo (ganha das marcas seguintes);
- `idempotency_key` com o prefixo `validacao:` → `validacao_qa`, `origem_ref` = o pedido (`lv-…`); o prefixo é UMA
  constante (`PREFIXO_VALIDACAO`), a mesma que monta a chave, e um teste reprova o literal escrito fora do contrato;
- `telegram:<id>` e `trello:<id>` → o canal, `origem_ref` = o id externo;
- qualquer outro caso (o pedido pelo painel, a sucessora `sucessora-…`, a linha antiga): `null`.

O painel põe um selo neutro na lista e no resumo da execução ("Prova de fluxo (validação)", "Validação do QA", "Pelo
Telegram", "Pelo Trello"). Na prova e na validação o texto se chama "Comando de origem"; no canal continua "Pedido" (foi
uma pessoa). A validação do QA leva ao pedido em Aprendizado › Validação.

**(b) `GET /api/aprendizado/validacoes?estado=&limite=50&antes=`**: só leitura, sem IA, sem custo; 503 antes da
composição; `estado` fora do vocabulário é 422; `limite` de 1 a 200.

```json
{"itens": [{"id": "lv-…", "estado": "recusada", "motivo": "sem_evidencia",
            "motivo_humano": "A execução terminou sem deixar evidência no item.",
            "item_ref": "fluxo:12", "item_kind": "fluxo", "app": "com.pocqa.messenger", "app_nome": "QA Messenger",
            "grupo": "qa", "run_id": "r-…", "run_origem": "r-…", "aparelho": "android-02", "usd": 0.0512,
            "teto_usd": 0.1, "created_at": "…", "feito_em": "…", "expira_em": "…", "revisao_nova_id": null,
            "comando": "No QA Messenger, …"}],
 "contagem": {"pendente": 1, "rodando": 0, "feita": 1, "recusada": 1, "expirada": 0},
 "total": 3, "modo": "off"}
```

- `itens`: dos mais novos para os mais antigos; `antes` é o `created_at` do último da página anterior;
- `contagem`: por estado, de TODOS os pedidos (não só do filtro), com zero no estado sem nenhum; `total` é a soma;
- `motivo` em código e `motivo_humano` pela tabela do domínio (`MOTIVO_HUMANO`, um texto para cada valor de `Motivo`;
  um código desconhecido volta ele mesmo);
- `modo`: o do despachante agora; `off` = pausado (nada nasce, nada roda), e o painel explica a pausa;
- `comando`: o comando de origem cortado em 200 caracteres. **É só para o painel. Esta rota não é fonte para espelho
  externo: Trello e Telegram nunca recebem texto de comando.** A leitura de conhecimento do 32.2 é outra rota, da
  frente Canais.

O painel ganha a aba Aprendizado › Validação (`?aba=validacao&estado=<estado>`): fichas por estado com a contagem,
busca, um cartão por pedido (estado, item e app, motivo em texto, aparelho, custo e teto, "Abrir execução", "Abrir no
Livro") e o vazio que explica o que é a validação e por que ela pode estar pausada.

Prova:
- `simulated`: `tests/test_origem_da_execucao.py` (a regra, a constante única e a listagem de execuções),
  `tests/test_validacoes_listagem.py` (ordem, filtro, página, contagem, motivo humano, 422), `SeloDeProva.test.tsx` e
  `ValidacaoTab.test.tsx`;
- `not_run`: o painel do central depois do deploy que levar o 30.38 (a, b).

## Adendo v1.08 (03/10/2026; número da orquestradora; item 29.58) — efeito repetido em `steps.result` e a recusa gravada em `actions`

Sem migração e sem rota nova. Mudam dois registros que o painel e o aprendizado já leem.

- **`StepResult.efeito_repetido`** (o `result` das etapas em `GET /api/runs/{id}`, nos eventos de etapa e em
  `steps.result`): `{"copias": N, "fonte": "verificador" | "acoes"}`, com N >= 2.
  - Fica presente só quando o efeito externo da etapa saiu mais de uma vez, e só na etapa com efeito onde a repetição
    foi vista. Sem repetição, a chave fica ausente: nunca `null` nem `copias: 1`.
  - Com ela, a etapa e o objetivo fecham `uncertain`, com `status_detail` começando por "efeito repetido (N)", e o
    resultado leva `verified: false`. Nunca é "sucesso comprovado" e nada é reenviado.
  - `fonte`:
    - `verificador`: o veredito (`Verdict.copias`, interno ao provedor) contou as cópias desta execução na tela;
    - `acoes`: mais de uma etapa da mesma etapa-modelo e do mesmo `item` disparou o efeito no mesmo objetivo e na
      mesma versão do plano.
  - O leitor da prova de fluxo (30.42) é `learning/domain/prova.efeito_repetido`, pelo adendo v1.07. Quando a chave
    existe, ela vence a regra própria do aprendizado; uma forma fora desta é ignorada.
- **A recusa de efeito fora da etapa** (`actions`): numa etapa sem efeito declarado, a ação com cara de efeito externo
  é gravada com `side_effect: true`, `status: "rejected"` e `error` começando por "o efeito só sai na etapa que o
  declara". Antes, um toque assim saía gravado com `side_effect: false` e `done`.
- Prova `simulated`: `backend/tests/test_efeito_pela_acao.py`. Prova real: `not_run`.

## Adendo v0.99 (03/10/2026; número da orquestradora; item 32.2, ADR-072) — o Trello: a rota do webhook, a config e a saúde

Uma rota nova, **fora do login**, e só ela: `HEAD` e `POST /api/canais/trello/webhook`. O Trello chama; a Central não
guarda sessão nem credencial nela. `main.guarda` libera exatamente esses dois métodos nesse caminho (o caminho inteiro,
sem prefixo e sem curinga); todo o resto de `/api/` segue pedindo credencial, e o `forbidden_host` (403) vale. Fica fora do
OpenAPI. Desligada de fábrica.

- **`HEAD`** (o Trello confere a URL ao cadastrar o webhook): `200` com `trello.webhook.enabled` E `TRELLO_API_SECRET` E
  `trello.webhook.callback_url`; senão `404`. Sem assinatura.
- **`POST`:**
  - `404`: `trello.webhook.enabled` é false (a rota não existe);
  - `401`, sem corpo: ligado mas sem o segredo ou sem a URL (falha fechada), cabeçalho `X-Trello-Webhook` ausente ou
    diferente de base64(HMAC-SHA1(`TRELLO_API_SECRET`, corpo cru + `trello.webhook.callback_url`)). Nada é gravado;
  - `413`: corpo acima de `trello.webhook.max_bytes` (262144), sem ler o resto;
  - `200`, sem corpo: assinatura certa. Corpo ilegível, tipo que não interessa e id repetido também são `200` (o Trello
    repetiria à toa) e não gravam nada.
- **O corpo é só um aviso.** A Central guarda o id da action (`canal_entradas.estado = 'aviso'`, sem texto, autor nem
  cartão) e acorda o líder, que relê a action por `GET /1/actions/{id}` com o token do dono. Autor, cartão, texto e quadro
  do corpo são descartados. Nada do corpo vai a log, evento ou resposta.
- **Config (`trello:`):** `enabled`, `quadros`, `listas` (`central_automatico`, `aprovado`, `vetado`, `marcos`, `custos`),
  `membro_dono`, `espelho_s` (60), `reconciliar_s` (60), `comando_livre` (false), `idade_max_s` (900),
  `membros_autorizados` (vazia), `responder_convidados` (false) e `webhook` (`enabled` false, `callback_url`, `max_bytes`,
  `cadastro_automatico` false). `webhook.enabled` só faz a rota responder e nunca cadastra nada; o primeiro cadastro é
  `scripts/trello-webhook.py --aplicar`, e o recadastro de hora em hora no líder exige `webhook.cadastro_automatico`. Os
  segredos (`TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_API_SECRET`) só no `.env`.
- **`GET /api/health` — problemas novos**, que somem quando a causa some:
  - `trello_sem_segredo`: `trello.enabled` sem `TRELLO_API_KEY` ou `TRELLO_TOKEN`;
  - `trello_recusado` (401/403) e `trello_pedido_invalido` (outro 4xx): o Trello recusou a Central, no espelho, na leitura ou
    no cadastro (uma só entrada por código);
  - `trello_leitor_atrasado`: a última leitura das actions que deu certo tem mais de 3 × `reconciliar_s`;
  - `trello_webhook_sem_segredo`: `webhook.enabled` sem `TRELLO_API_SECRET` ou sem `callback_url`;
  - `trello_webhook_assinatura_invalida`: 5 ou mais assinaturas inválidas em 10 min;
  - `trello_webhook_inativo`: com `cadastro_automatico`, o cadastro falhou ou o Trello desativou o webhook.
- **Gestos pelo Trello:** o operador gravado é `trello:<idMember>` do autor da action. Mover o cartão de aprovação para ⛔,
  "não" ou `/vetar` decidem `reject` (`decided_by = trello:<id>`); mover para ✅, "sim" e `/aprovar` NÃO aprovam, e a Central
  responde que a aprovação se confirma no painel ou no Telegram. A execução criada pela resposta a uma pergunta usa a chave
  de idempotência do canal. Qualquer comentário que comece com 🤖 é de IA e nunca é pedido.
- **Banco:** a migração 087 cria `trello_cartoes` e `trello_cursor`; a 085 ganha o estado `aviso` em `canal_entradas`
  (`docs/banco.md`).

Prova `simulated`: `backend/tests/test_trello_webhook.py`, `test_trello_leitor.py`, `test_trello_espelho.py`,
`test_trello_cliente.py` e `test_trello_config.py`. `not_run`: o HEAD do Trello na URL pública, o primeiro cadastro e um
comentário real chegando pelo webhook.

## Adendo v1.09 (03/10/2026; número da orquestradora; item 30.41) — o teto da prova proporcional ao plano

Aditivo ao v1.07. Nenhuma rota nova e nenhuma migração.

- **`learning_validations.teto_usd`** da prova de fluxo passa a ser `min(0,40; 0,05 + 0,02 × etapas)`, gravado no
  despacho. Ele vence o teto que o pedido tinha ao nascer. A receita segue com o teto fixo (v0.96 e 30.40).
- **`learning_validations.motivo`** ganha `plano_acima_do_teto` (`estado = recusada`). O plano que a execução
  rodaria passa de 0,40 ou tem `for_each` de tamanho desconhecido, e o pedido fecha ao despachar, sem execução.
  - Vale para o fluxo e para a receita cujo comando resolve num fluxo ativo grande.
  - Não devolve o item ao curador nem reabre.
  - O valor já existia no banco do central, gravado à mão em 03/10 (lv-f544, a receita:135).
- **As etapas** contam o `for_each` pelo tamanho da lista que a execução de origem do fluxo coletou. É aproximado.

Prova:
- `simulated`: `backend/tests/test_learning_prova_teto.py`.
- `not_run`: o primeiro despacho do P4 depois do deploy.

## Adendo v1.10 (03/10/2026; número da orquestradora; item 30.43) — a validação com rosto no item

Aditivo aos v1.07, v1.09 e ao da lista de validações (30.38 b). Nenhuma rota nova e nenhuma migração.

- **`GET /api/aprendizado/validacoes`** ganha os filtros opcionais `item` (`<kind>:<ref>`) e `run` (o id da
  execução). A `contagem` e o `total` seguem sendo de TODOS os pedidos.
- **A entrada do livro** (`GET /api/aprendizado/{kind}/{ref}` e a lista) ganha `nasceu_em`, com o valor
  `prova_fluxo`, `validacao_qa` ou `null`: a receita foi aprendida dentro de uma execução de validação.
- **Dossiê do curador:** `item.nasceu_em_validacao`, a nota de origem, só na receita marcada.
- **Evidência:** a linha `reproducao:<run>` de receita pode ganhar a irmã `invalida` (`invalida:efeito_repetido — o
  efeito saiu N vezes nesta execução`). O pedido de validação de receita pode fechar `recusada/efeito_repetido`.
- **Linha do tempo:** a re-execução da validação do QA mostra a decisão "Validação do QA (re-execução): ponto de
  partida…".
- **30.44, no mesmo ciclo:** cada evidência do detalhe (`evidencias[]` de `GET /api/aprendizado/{kind}/{ref}`)
  ganha `etapa_titulo`: o título da etapa que o `detail` cita ("etapa N (chave)"). Ele é lido de `steps` pela execução
  e pela chave, na hora da leitura, sem gravar; o `detail` segue só com a chave, porque o título pode trazer texto de
  pessoa. É `null` sem etapa citada, sem a execução ou quando a triagem de credencial recusa o título. A lista sai
  pela data do acontecido (`observed_at`), não pela ordem de gravação.
- **30.45, aditivo:** cada pedido de `GET /api/aprendizado/validacoes` ganha `invalida_depois`, que é
  `{"motivo", "motivo_humano"}` ou `null`. É a linha `invalida` que a execução do pedido deixou no item, mesmo
  chegada depois do fechamento (a reclassificação do 30.42). O detalhe sem motivo legível lê `sem_evidencia`. O
  pedido segue no estado gravado (`feita`); quem lê não diz "a favor". O painel diz "inválida (…)" no veredito da
  execução e "Rodou; depois: inválida — …" no histórico do item. Um cliente antigo ignora o campo.

Prova:
- `simulated`: `backend/tests/test_learning_reproducao_repetida.py`, `test_learning_nasceu_em_validacao.py` e
  `test_validacoes_listagem.py::test_a_listagem_filtra_por_item_e_por_execucao`.
- `not_run`: o central depois do deploy.

## Adendo v1.11 (04/10/2026; número da orquestradora; item 30.31, fatia 2) — o ensaio só de leitura e a conferência no app de QA

Aditivo aos v1.07, v1.10 e ao contrato do 29.58. Nenhuma rota nova e nenhuma migração.

- **Motivo novo do pedido de validação: `ensaio_so_leitura`** (`GET /api/aprendizado/validacoes`, com `motivo_humano`).
  O ensaio só de leitura percorreu o fluxo e parou antes da etapa com efeito fora do aparelho, como devia. O pedido
  fecha `recusada` com esse motivo. Não é evidência do fluxo, nem a favor nem contra. O painel diz "ensaio só de
  leitura (parou antes do efeito; não conta)".
- **A execução de ensaio** tem a chave de idempotência `ensaio:<pedido>`, fora de `PREFIXOS_DE_ORIGEM`, como o lote:
  - a origem que o painel mostra é a da prova (`prova_fluxo`);
  - a etapa com efeito e as seguintes ficam `skipped`, o objetivo fecha `cancelled` pelo sistema e a execução,
    `cancelled`;
  - a decisão "Ensaio só de leitura: parou antes da etapa N (chave)…" fica na linha do tempo;
  - é execução do sistema: sem aviso individual (28.19).
- **`steps.result.efeito_repetido.fonte`** ganha o valor `provedor`, ao lado de `verificador` e `acoes`, que são da
  Android. Ao fim da execução de validação cujo comando traz `{run_id}`, o `ContentProvider` do QA conta as mensagens
  daquela execução. A partir de 2, a etapa de efeito ganha `{"copias": N, "fonte": "provedor"}`, e o veredito do 30.42
  e a reprodução do 30.43 a leem como as outras fontes. O painel diz "contado no próprio app de QA ao fim da
  validação". A decisão "Conferência no app de QA (etapa N, chave): …" fica na linha do tempo, com 0, 1 ou N
  mensagens.

Prova:
- `simulated`: `backend/tests/test_learning_ensaio_e_oraculo.py`.
- Real da conferência: a primeira validação do QA com `{run_id}` depois do deploy.
- Ensaio: `not_run`. Nada cria execução de ensaio no central ainda; a ligação do despachante ao Instagram em perfil
  de terceiro é da orquestradora.

## Adendo v1.12 (04/10/2026; número da orquestradora; item 30.34) — a última volta da sombra da autopublicação

Aditivo ao v0.93. `GET /api/aprendizado/metricas` → `curador.autopublicacao` ganha `ultima_volta`:

- `null` antes da primeira volta deste processo;
- depois, `{"em": "<ISO>", "modo": "shadow", "avaliados": N, "publicaria": P, "marcados": M}`:
  - `avaliados`: os fluxos em `validated` que a D1 segura;
  - `publicaria`: os que passam na regra agora;
  - `marcados`: os casos novos desta volta.

É em memória e zera no reinício. A primeira volta sai 60 s depois do início, e as seguintes de `intervalo_s` em
`intervalo_s`. Os outros campos do bloco não mudam. O painel ainda não lê a chave.

## Adendo v1.13 (04/10/2026; número da orquestradora; item 32.5) — `GET /api/canais/estado`, o estado dos canais

Uma rota nova, **só leitura**, atrás do login como toda rota `/api/` do painel (sessão ou credencial; sem elas, `401`
`unauthorized`; `POST`, `PUT`, `PATCH` e `DELETE` no caminho dão `405`). Sem migração e sem config nova. Alimenta a tela
**Canais** do painel (`#/canais`), que relê a cada 30 s.

A resposta é uma lista **fechada** de números, horas (ISO-8601) e códigos. Nunca título nem corpo de aviso, texto de
mensagem, `chat_id`, id de membro, nome, token, URL com segredo, nem id de quadro, lista ou cartão. O motivo da última falha
de envio é um código derivado do `ultimo_erro` por regra (o texto dele não sai: pode carregar a URL do bot). Dos
problemas da saúde sai só o `code`.

- `gerado_em`
- `aviso_telegram`: `ligado` (`avisos.enabled`), `segredo_presente` (booleano: token do bot E chat no `.env`), `fila`
  (`pendente`, `enviando`, `enviado`, `falhou`, `incerto`, `descartado`; todas presentes, com zero), `ultimo_envio_em`,
  `ultima_falha` (`null` ou `{em, motivo}`, `motivo` em `rede` | `401` | `429` | `tempo_esgotado` | `outro`; a hora é a do
  início da tentativa, e só conta a linha ainda `pendente` ou `falhou`), `problemas` (códigos, p. ex. `avisos_sem_segredo`).
- `conversa_telegram`: `ligada` (`avisos.enabled` E `avisos.entrada.enabled`), `ultima_leitura_em` (a mensagem mais recente
  recebida; o marco de 1ª subida não conta), `entradas` (por estado: `recebida`, `ignorada`, `recusada`, `limitada`,
  `orquestradora`, `pergunta`, `executando`, `feita`, `cancelada`, `falhou`, `aviso`; um estado desconhecido soma em
  `outro`), `problemas` (só `telegram_entrada_*`).
- `trello`: `ligado`, `webhook_ligado`, `cadastro_automatico`, `ultima_reconciliacao_em` (o `atualizado_em` mais recente
  do cursor), `cartoes` (`ativo`, `arquivado`, `criando`), `entradas` (como acima, `canal='trello'`),
  `comentarios_de_app_em_alvo_desconhecido` (28.55: quantos comentários escritos por app, num cartão fora das listas de
  perguntas e sem fato, foram tratados como `outro`; só o número), `problemas` (só `trello_*`, sem repetir).

Prova:
- `simulated`: `backend/tests/test_canais_estado.py` (conjunto de chaves travado; nenhum valor carrega o texto semeado nas
  tabelas nem segredo; `401` sem sessão; `405` nos métodos de escrita) e `frontend/src/features/canais/CanaisPage.test.tsx`.
- `not_run`: o central depois do deploy (a conferência no navegador vem depois).

## Adendo v1.15 (04/10/2026; número da orquestradora; item 28.21) — a pessoa resolve a ocorrência incerta

Aditivo ao v0.45 (pedidos). Migração **095**. Achado real do 28.12: o pedido com uma ocorrência `incerta` ia a
`aguardando_pessoa` e ficava preso, porque `retomar` respondia 409 `pendencia_aberta` e a incerta só saía das pendências
quando uma ocorrência POSTERIOR concluía, o que um pedido parado não gera. A única saída era cancelar.

- **`POST /api/pedidos/{id}/ocorrencias/{oid}/resolver`**, corpo `{nota}`: a pessoa conferiu no aparelho o que a ocorrência
  fez e a dá por resolvida. **Só marca**: a ocorrência CONTINUA `incerta`, nada é reexecutado e o estado do pedido não muda.
  Devolve a ocorrência (`OcorrenciaDTO`) com os campos novos.
  - `nota` é **obrigatória** e não vazia depois do `strip` (422 `nota_obrigatoria`, também para campo ausente), com o teto de
    500 caracteres da quarentena (29.24); acima disso, 422 do contrato.
  - 404 `not_found` se o pedido ou a ocorrência não existe, ou se a ocorrência é de outro pedido. 409 `invalid_state`
    (com `estado`) se a ocorrência não está `incerta`.
  - **Repetir é idempotente**: numa já resolvida, 200 com o que foi gravado na primeira vez; a nota nova é ignorada (a
    trilha não é reescrita) e o evento não é reemitido. O `UPDATE` tem CAS (`estado='incerta' AND resolvida_em IS NULL`),
    então duas chamadas concorrentes gravam uma só.
  - `resolvida_por` é o operador da sessão (`panel` sem sessão), `resolvida_em` o relógio do serviço de pedidos.
  - Evento: o `pedido.ocorrencia.updated` de sempre (nível `warn`, porque segue incerta), já com os campos novos.
- **`OcorrenciaDTO`** (detalhe, `ocorrencias_recentes`, lista de ocorrências, evento e snapshot) ganha `resolvida_em`,
  `resolvida_por` e `resolvida_nota`, todos `null` até alguém resolver. Um cliente antigo os ignora.
- **`pendencias`** deixa de listar a incerta com `resolvida_em` preenchido; a regra antiga (ocorrência posterior `concluida`)
  continua. Sem outra pendência, `POST /api/pedidos/{id}/retomar` passa a valer (`aguardando_pessoa → ativo`). Resolver uma
  incerta não libera `pergunta` nem `aprovacao` do mesmo pedido.
- **Painel:** no aviso "Este pedido espera você", a pendência `ocorrencia_incerta` ganha "Marcar como resolvida" (diálogo com
  a nota obrigatória); a guia Ocorrências mostra quem, quando e a nota.

Prova:
- `simulated`: `backend/tests/test_pedidos_resolver_incerta.py` (retomar 409 antes; nota; quem/quando/nota e estado
  incerto; pendências vazias e retomar 200; idempotência; 409; 404; evento; sessão; 401) e
  `frontend/src/features/pedidos/PedidosPage.test.tsx` (fluxo do botão, 409, exibição).
- `not_run`: o central depois do deploy (a migração 095 e o fluxo no painel).
**Com a 30.34-B (mesmo item, PR empilhado):**
- `ultima_volta` ganha `publicados`: os fluxos publicados pela emenda naquela volta, 0 fora de `on` com o balanço
  liberado.
- O bloco ganha `publicados_pela_emenda`: o total na trilha (`validated → published` pelo sistema, com o motivo
  `autopublicacao_b:`).
- `modo` passa a poder ser `"on"`.
## Adendo v1.14 (04/10/2026; número da orquestradora; item 30.47) — a pessoa pede a validação de um fluxo candidato

`POST /api/aprendizado/fluxo/{ref}/validacao`, sem corpo. Cria em `learning_validations` o pedido que um "pedir
evidência" do curador geraria, e o despachante P4 faz o resto com o teto de sempre.
- **O pedido:**
  - o comando de origem do fluxo e o aparelho de origem excluído;
  - a falta `reproducao_em_outro_aparelho` e o grupo `qa`;
  - quem pediu em `review_id = "pedido:<quem>"` (o operador da sessão do painel, ou `panel`).
- **201:** o pedido, no mesmo formato de um item de `GET /api/aprendizado/validacoes`.
- **404:** o fluxo não existe.
- **409** `pedido_vivo`: o fluxo já tem pedido pendente ou rodando.
- **422** com `detail.code`. Nenhuma recusa grava pedido.
  - Um motivo do vocabulário de recusa (`efeito_real` para o efeito fora do app de QA, `sem_origem`, `vetado`,
    `sessao`, `credencial`, `sem_caminho`), com o mesmo texto humano da lista.
  - Ou `recusado`, com a mensagem: o que não é fluxo `candidate`, a classe C (e o item sem dossiê de agora, que conta
    como C) e o modo `off`.
- **503:** a validação não foi composta.

## Adendo v1.16 (04/10/2026; número da orquestradora; item 28.10 F1) — colaboração entre pedidos, só a estrutura

Aditivo ao v0.45 (pedidos). Uma migração (096) e nenhuma rota nova. Desligado de fábrica: com
`pedidos.colaboracao.enabled: false` tudo se comporta como antes.

- **Corpo de `POST /api/pedidos/previa` e `POST /api/pedidos`** ganha três campos opcionais: `pai_id` (o pedido pai),
  `papel` (`pesquisador`, `checador`, `redator` ou `porta_voz`) e `dependencias: [{de, tipo}]` com `tipo` em
  `precisa_de_resultado` ou `depois_de`. O pedido novo é sempre o que depende (`para`) de cada `de`. A estrutura nasce com
  o pedido: o `PATCH` continua sem aceitar os três, e repetir a `idempotency_key` com outra estrutura é 409
  `idempotency_conflict`.
- **Desligada**, qualquer um dos três é 422 `colaboracao_desligada` na criação e um `bloqueio` na prévia.
- **Ligada**, a prévia e a criação conferem o mesmo (a prévia não grava) e recusam com 422 `{code, message, campo}`:
  `pai_inexistente`, `pai_terminal`, `profundidade_excedida` (padrão 2: o neto), `filhos_demais` (padrão 5),
  `porta_voz_duplicado` (um por família), `dependencia_fora_da_familia` (só o pai ou um irmão), `ciclo`, `linhagem`,
  `orcamento_do_pai`, `papel_invalido`, `tipo_de_dependencia_invalido` e `dependencia_duplicada`. O filho DECLARA o
  `orcamento_total_usd` dele, reservado do pai: gasto do pai + totais dos filhos não passam do total do pai. `ciclo` e
  `linhagem` quase só se alcançam no domínio na F1 (o `pai_id` é imutável e um pedido novo não tem quem aponte para ele).
- **`PedidoView`** ganha `papel` (`null` = pedido comum). **`GET /api/pedidos/{id}`** ganha `filhos: [{id, titulo, estado,
  papel}]` e `dependencias: [{de, para, tipo}]` (as do pedido e as dos filhos dele).
- **`POST /api/pedidos/{id}/cancelar`**: os descendentes ainda vivos são cancelados junto, na mesma transação; a confirmação
  (`execucoes_em_curso`, `ocorrencias_futuras`) já os conta e a resposta ganha `filhos_cancelados`.
- **Pai encerrado pelo sistema** (prazo, contagem, orçamento, abandono): os filhos vivos vão a `encerrado` com o novo
  `encerrado_motivo: "pai"`, depois do commit do pai, com os eventos `pedido.updated` de sempre. O laço não muda e nada
  disto segura ou limita uma ocorrência (dependência no laço é a F2; papel limitando a autonomia, a F3).

Prova:
- `simulated`: `backend/tests/test_pedidos_colaboracao_dominio.py` e `test_pedidos_colaboracao_api.py`.
- `not_run`: PostgreSQL e o central depois do deploy.

## Adendo v1.17 (04/10/2026; número da orquestradora; item 28.22) — relatório no encerramento por orçamento e motivo do cancelamento

Aditivo ao v1.16. Uma migração (099) e nenhuma rota nova. Os dois achados vêm da prova real do 28.12 (04/10).

- **Encerramento por orçamento:** o pedido que o laço encerra com `encerrado_motivo: "orcamento"` agora grava o
  relatório final de encerramento (`gatilho: "encerramento"`), como já faziam a contagem, o prazo e o cancelamento
  (§6.5 de `design/pedidos-persistentes.md`). Com ele vem o aviso `relatorio_pronto` de sempre. Se o relatório
  quebrar, o pedido encerra mesmo assim e o relatório sai depois, sob demanda.
- **`POST /api/pedidos/{id}/cancelar`:** o `motivo` do corpo (até 200, aparado; só espaços conta como sem motivo)
  passa a ser gravado em `cancelado_motivo`. Vale só para o pedido cancelado pela pessoa: os descendentes cancelados
  em cascata ficam sem motivo. Repetir o cancelamento (`sem_mudanca: true`) não troca o motivo gravado.
- **`GET /api/pedidos/{id}`** ganha `cancelado_motivo: string | null`. O campo **não** entra no `PedidoView`: a lista,
  a resposta das ações e o evento `pedido.updated` não o trazem. É texto livre de pessoa, então também não vai para
  aviso, Telegram ou Trello. O painel o mostra no detalhe, em Comportamento, como "Cancelado porque".

Prova:
- `simulated`: `backend/tests/test_pedidos_orcamento.py` (3 testes novos, com contraprova) e
  `test_pedidos_api.py::test_cancelar_grava_o_motivo_so_no_detalhe_e_nunca_no_evento_nem_no_aviso`; vitest
  `PedidosPage.test.tsx` (2 novos).
- `not_run`: PostgreSQL e o central depois do deploy.
## Adendo v1.18 (04/10/2026; número da orquestradora; item 28.10 F3) — o papel limita a autonomia do pedido

Aditivo ao v1.16 (o v1.17 é do 28.22). Sem migração e sem rota nova. Só vale com `pedidos.colaboracao.enabled`.

- **Teto por papel:** `pesquisador` e `checador` vão até `observar`, `redator` até `preparar`, e `porta_voz` até `agir`.
- **`POST /api/pedidos/previa` e `POST /api/pedidos`:** o pedido com `papel` e uma `autonomia` acima do teto é recusado com
  422 `autonomia_acima_do_papel` (`campo: "autonomia"`), e a prévia o lista como bloqueio, sem selo. A estrutura (os códigos
  do v1.16) é conferida antes. Vale também para o pedido sem pai.
- **`PATCH /api/pedidos/{id}`:** mudar a `autonomia` de um pedido com papel para acima do teto é 422
  `autonomia_acima_do_papel`, já no `dry_run`. O papel continua sem mudar depois de criado.
- **Ocorrência:** quando o laço decide abaixo da autonomia gravada (pedido gravado acima do teto antes desta fatia), a
  ocorrência terminada traz no `motivo` a nota `autonomia rebaixada ao teto do papel <papel>: <gravada> → <efetiva>`.
- **Limite:** a autonomia ainda não chega à execução. O teto vale no que o laço decide (sobreposição, janela, piso). A
  execução ganha o teto no 28.23 (adendo v1.19).

Prova:
- `simulated`: `backend/tests/test_pedidos_colaboracao_papeis.py` (17 testes, com contraprova: sem a fatia, 3 falham).
- `not_run`: PostgreSQL e o central (a colaboração e `pedidos` estão desligados lá).
## Adendo v1.19 (04/10/2026; número da orquestradora; item 28.23) — o teto de autonomia da execução

`POST /api/runs` aceita o campo opcional `teto_de_autonomia`: `"observar"`, `"preparar"`, `"agir"` ou `null`
(padrão). Ele fica gravado na execução (migração 100) e volta no resumo dela (`RunSummary.teto_de_autonomia`).
Só restringe: nunca afrouxa a política da persona.

- `null` e `"agir"`: como antes deste adendo.
- `"observar"`: um plano com etapa de efeito (ou `commit_guard`) é recusado. A execução termina `failed` (nunca
  `uncertain`: sem ação com efeito não há efeito possível), com o evento `plan.refused` e
  `data = {"motivo": "acima_da_autonomia", "teto": "observar", "etapas": [<chaves>]}`. Como defesa, se uma etapa com
  efeito chegar ao despacho (plano de fluxo, skill, revisão), a execução para antes dela e da etapa que a preenche;
  as etapas restantes ficam `skipped` e o objetivo, `failed` (métrica `execucao.parada_no_teto`).
- `"preparar"`: a etapa com efeito exige aprovação, qualquer que seja a política da persona. Sem ação do catálogo
  que peça a aprovação, a etapa é segurada (`approval_required`) e não roda sozinha.

Valor fora do vocabulário: `422`. Quem cria a execução a partir de um pedido persistente (o laço, F3 do 28.10)
passa a autonomia do pedido neste campo; isso é do PR da frente Canais.

Prova:
- `simulated`: `backend/tests/test_teto_de_autonomia.py` (os três níveis e o nulo).
- `not_run`: PostgreSQL e o central depois do deploy.

## Adendo v1.26 (04/10/2026; número da orquestradora; item 28.24, F4) — a lista de anexos e o conteúdo só de imagem e PDF

Sem migração. Alimenta a aba Anexos da tela Canais. Atrás do mesmo login das outras `/api/canais` (sem ele, **401**).
- `GET /api/canais/anexos`: página de anexos, do mais novo ao mais velho. Filtros (todos opcionais): `canal`, `direcao`
  (`entrada`|`saida`), `do_dono` (booleano; olha a mensagem de origem, e a saída nunca é "do dono"), `estado`
  (`guardado`|`recusado`|`apagado`; o `pendente`, que ainda espera o download, nunca entra), `desde` e `ate` (ISO; `ate` é
  exclusivo; data sozinha vale 00:00Z), `limit` (1 a 100, padrão 24) e `offset`. Resposta: `{items, total, limit, offset}`,
  com `total` batendo com os filtros. Cada item tem chaves fixas: `id`, `canal`, `direcao`, `mime`, `bytes`, `estado`,
  `motivo_recusa`, `criado_em`, `apagado_em`, `do_dono`, `tem_conteudo` (imagem ou PDF guardado que sai por `/conteudo`; nunca o
  de convidado) e `pode_ir_ao_cartao` (a regra de `POST .../trello`: mensagem do dono, guardada). Sem caminho de disco, sem
  `sha256`, sem referência do canal e sem nome de remetente. **422** para filtro inválido (`periodo_invalido` para data fora do ISO).
- `GET /api/canais/anexos/{id}/conteudo` (muda a v1.21): além de `Content-Disposition: attachment; filename="anexo-<id>.<ext>"` e
  `X-Content-Type-Options: nosniff`, agora responde `Cache-Control: no-store`; só sai imagem (JPEG, PNG, WEBP) e PDF. Novos erros:
  **415** `tipo_sem_previa` (o `text/plain` guardado não sai por aqui) e **404** `anexo_de_convidado` (a mensagem de origem não é
  do dono; o convidado nunca tem anexo baixado, então a linha só existiria por defeito). Os 404 `anexo_sem_arquivo` e o 410
  `anexo_apagado` seguem como na v1.21.

## Adendo v1.25 (04/10/2026; número da orquestradora; item 28.24, F3) — a IA lê a imagem que o dono mandou

Migração `103_canal_anexos_descricao` (só `ADD COLUMN` em `canal_anexos`: `descricao`, `lida_em`, `modelo_leitura`, `custo_usd`,
`tokens_entrada`, `tokens_saida`). Os metadados do `GET /api/canais/anexos/{id}` NÃO mudam (as chaves fixas da v1.21 seguem; a
descrição sai pela rota nova).

- `POST /api/canais/anexos/{id}/ler` com `{"confirmar": true}`: a IA descreve a imagem. Atrás do mesmo login das outras rotas de
  `/api/canais`. **200** `{anexo_id, descricao, custo_usd, do_cache, modelo}`; com a descrição já gravada, `do_cache: true`,
  `custo_usd: 0` e nenhuma chamada ao provedor. Erros (`detail.code`): **400** `confirmacao_necessaria` (sem `confirmar: true`);
  **404** `anexo_desconhecido`; **409** `anexo_nao_permitido` (convidado, saída ou mensagem que não é do dono), `anexo_sem_arquivo`,
  `leitura_acima_do_teto` (a estimativa passa de `teto_usd`; nada foi enviado), `gasto_barrado` (teto do dia ou saldo da conta) ou
  `gasto_nao_conferido`; **422** `anexo_nao_imagem` ou `imagem_grande_demais` (acima de 5 MB, o máximo do provedor); **502**
  `ia_falhou` (nada é gravado como lido); **503** `leitura_desligada`, `sem_modelo_de_visao` ou `not_ready`.
- Só imagem JPEG, PNG ou WEBP: o GIF não está na lista de tipos guardados (a animação é recusada no download), então não chega aqui.
- Telegram, sem rota HTTP: `/ler`, "leia" ou "o que tem nessa imagem" em reply a uma foto do dono (intenção `ler_anexo`, fato
  `anexo:<id>`); sem reply a um anexo, a resposta explica o formato. A resposta é a descrição mais uma linha com o modelo e o custo.
- Config: `avisos.entrada.anexos.leitura` (`enabled`, `teto_usd` 0,05, `modelo` vazio = o mais barato com visão de `ai.prices`,
  `max_tokens` 400). A descrição passa pelo redator de credencial dos textos do canal. O custo entra em `ai_calls` com
  `origem='canais'` (o vocabulário de origem ganhou `canais`), por tokens x `ai.prices`.

## Adendo v1.21 (04/10/2026; número da orquestradora; item 28.24, F1) — anexos nos canais

O Telegram passa a receber e a devolver arquivos (regra do dono em `docs/dominios/canais.md`, C-22). Migração `101_canal_anexos`.
Duas rotas só de leitura, atrás do mesmo login das outras `/api/canais` (`GET /api/canais/estado`):
- `GET /api/canais/anexos/{id}`: os metadados. Chaves fixas: `id`, `canal`, `entrada_id` (a `canal_entradas` da mensagem; nulo
  na saída), `direcao` (`entrada`|`saida`), `sha256`, `mime` (o detectado pelo conteúdo), `bytes`, `estado`
  (`guardado`|`recusado`|`apagado`), `motivo_recusa` (português simples), `criado_em`, `apagado_em`. Sem caminho de disco e sem o
  nome que o remetente deu (o produto não o guarda). **404** `anexo_desconhecido`; **503** `not_ready`.
- `GET /api/canais/anexos/{id}/conteudo`: o arquivo, com o mime guardado, `Content-Disposition: attachment;
  filename="anexo-<id>.<ext>"` e `X-Content-Type-Options: nosniff`. **404** `anexo_sem_arquivo` (recusado, ou o arquivo sumiu
  do disco); **410** `anexo_apagado` (a retenção do 28.16 o apagou).

Comportamento da conversa (sem rota nova):
- Só o chat do dono tem anexo baixado. Foto (a de maior tamanho), documento e legenda: a legenda (`caption`) vale como o texto da
  mensagem. Tipos aceitos, configuráveis dentro desta lista fixa: `image/jpeg`, `image/png`, `image/webp`, `application/pdf` e
  `text/plain`, até `avisos.entrada.anexos.max_bytes` (10 MB, no máximo 20 MB, o que o Bot API baixa). O teto vale antes (tamanho
  declarado, `file_size` do `getFile`) e durante o download, que para ao passar dele. Voz, áudio, vídeo, GIF, figurinha e tipo
  declarado fora da lista são recusados sem baixar; `application/octet-stream` não conta como declaração.
- O tipo vem da assinatura do conteúdo (JPEG, PNG, WEBP, PDF; texto = UTF-8 válido sem byte nulo); o declarado só serve para recusar a
  divergência. O arquivo vai a `data/anexos/<2 primeiros do sha256>/<sha256>.<ext>` por escrita atômica, deduplicado. A resposta ao
  dono é curta ("Recebi a imagem (123 KB). Guardei na Central (anexo 17).") ou diz o motivo da recusa. Foto sem legenda não é pedido:
  o anexo fica guardado e a mensagem, `ignorada`. O convidado recebe "Não recebo anexos de convidado." e nada é baixado.
- Saída: `ConversaDoCanal.enviar_anexo` (por id, sha256 ou caminho DENTRO de `data/anexos`; fora, `CaminhoForaDoArmazem`) e
  `enviar_conteudo` (o que o produto gerou). `sendPhoto` para imagem (cai para `sendDocument` se o Telegram recusar como foto) e
  `sendDocument` para o resto; o nome no envio é `anexo-<sha>.<ext>`. A porta `SaidaComAnexos` é do canal: o Trello, que não a cumpre, recusa
  o anexo com o motivo.
- A faxina por retenção (28.16) apaga o arquivo e a linha dos anexos vencidos, sem seguir link nem sair de `data/anexos`.
- Config: `avisos.entrada.anexos` (`enabled`, `max_bytes`, `tipos`). A leitura da imagem pela IA veio na F3 (v1.25).

Complemento (F2, mesmo item):
- **Download que falha:** a mensagem do dono e os anexos a baixar entram juntos, na mesma transação (`estado = 'pendente'`, com
  `ref_externa` e `mime_declarado`, que só existem nesse estado). A falha de rede ou da API vira `recusado` com o motivo e a resposta
  ao dono ("… Mande de novo."). Se a Central cai entre gravar e baixar, a volta seguinte (anexo `pendente` com mais de 60 s) baixa UMA
  vez e conta o resultado; se falhar, fecha como `recusado` e avisa. Escolhi avisar+uma tentativa, e não só avisar, porque a referência
  do arquivo no Telegram costuma valer por horas e a retomada poupa o reenvio.
- `POST /api/canais/anexos/{id}/trello` com `{"card": "<link, código curto ou id>", "confirmar": true}`: anexa ao cartão do Trello a
  imagem que o DONO mandou (exceção (b) do dono, 04/10 15:17Z). Atrás do mesmo login. `card` aceita o link do cartão
  (`https://trello.com/c/<código>/...`), o código curto de 8 letras e dígitos ou o id de 24 hexadecimais; o backend lê o id inteiro e
  o quadro pela API (`GET /1/cards/{código ou id}`), e a resposta traz o id inteiro (28.24 F4, revisão da fila da suíte 31). O mesmo
  arquivo no mesmo cartão vai uma vez só: se o cartão já tem o anexo de nome `anexo-<sha>.<ext>`, nada sobe e a resposta traz o que
  existe com `ja_estava: true`. **200** `{anexo_id, card, trello_anexo, ja_estava}`; **400**
  `confirmacao_necessaria`; **404** `anexo_desconhecido`; **409** `anexo_nao_permitido` (convidado, saída ou mensagem que não é do dono),
  `anexo_sem_arquivo` (recusado, apagado ou sumido do disco) ou `cartao_fora_dos_quadros` (o cartão não é de um quadro de
  `trello.quadros`, conferido pela API antes de anexar); **422** `cartao_invalido`; **502** `trello_falhou` (mensagem sem chave nem token);
  **503** `trello_desligado`. O nome no cartão é `anexo-<sha>.<ext>`; a chave e o token do Trello vão só no cabeçalho.
- Captura de aparelho ao dono (exceção (a)): `/captura <aparelho>` ou "captura do android-12" pelo canal do dono (intenção `captura`,
  sem rota HTTP). A imagem é a prévia do painel (`rt.frame`): se está velha, o pedido registra um interesse de foco por 5 s e espera o
  frame novo (até 8 s); tela sensível, loja ou aparelho fora do ar viram uma frase ao dono, sem imagem. Sai por
  `ConversaDoCanal.enviar_conteudo` (legenda "Captura do android-12", guardada em `data/anexos` como `saida`, com a retenção do 28.16).

## Adendo v1.20 (04/10/2026; número da orquestradora; item 30.52) — a pessoa recusa um pedido de validação pendente

`POST /api/aprendizado/validacoes/{id}/recusar`, sem corpo. Fecha o pedido ainda `pendente` como `recusada`, com o
motivo `recusada_pela_pessoa`, sem execução nem gasto. O motivo é neutro: não é chegada para o curador, nem evidência,
nem contestação, e não pesa contra o item. Quem recusou vai ao log do backend.
- **200:** o pedido, no mesmo formato de um item de `GET /api/aprendizado/validacoes` (`motivo_humano` incluído).
- **404** `pedido_desconhecido`: não há o pedido.
- **409** `pedido_nao_pendente`: o pedido já saiu de `pendente` (despachou, fechou ou expirou).
- **503:** a validação não foi composta.

## Adendo v1.22 (04/10/2026; número da orquestradora; item 28.25) — o que a plataforma decidiu sozinha, com o desfazer

Registro único `decisoes_automaticas` (migração 102) das decisões que a plataforma toma no lugar do dono (pedido 30.55) e
duas rotas, atrás do mesmo login do painel. O resumo no Telegram não é rota: ver `docs/dominios/canais.md` (C-23).

`GET /api/decisoes-automaticas?regra=&fila=&desde=&ate=&desfeitas=todas|sim|nao&limite=` (as mais novas primeiro;
`desde` inclusivo e `ate` exclusivo, datas ISO em UTC; `limite` 1 a 500, padrão 200).
- **200:** `{itens, total, regras, desfazer_dias}`. Cada item: `id`, `fila` (`pergunta`, `objetivo`, `aprendizado` ou
  `pedido`), `item_ref`, `regra`, `efeito` (frase curta em português), `fatos` (objeto plano e curto, sem dado pessoal),
  `decidida_em`, `resumida_em`, `desfeita`, `desfeita_em`, `desfeita_por`, `motivo_do_desfazer`, `pode_desfazer`,
  `acao_do_desfazer` (`Desligar` para o aprendizado, `Desfazer` nas outras), `por_que_nao` (em português; é o que o painel
  mostra no lugar do botão) e `prazo_ate`.
- **400:** `fila` fora do vocabulário ou data inválida. **422:** `desfeitas` ou `limite` fora do contrato.

`POST /api/decisoes-automaticas/{id}/desfazer` com `{confirmar: true, motivo?}` (`motivo` até 300 caracteres; campo
desconhecido é 422: quem desfez é o operador da SESSÃO, nunca o corpo).
- **200:** o item no formato acima, mais `desfeita_agora` (falso quando já estava desfeita). **Idempotente:** desfazer
  duas vezes não chama a fila dona de novo nem muda `desfeita_por` e o motivo.
- **400** `confirmation_required`: sem `confirmar: true`. **404** `not_found`. **409** `prazo_vencido`: passaram os
  `avisos.decisoes_automaticas.desfazer_dias` (7) desde `decidida_em`.
- **409** `sem_inversa_segura`: a fila dona não tem volta segura; a mensagem começa com "não dá para desfazer
  automaticamente: " e traz o porquê. Nada é marcado como desfeito.
- **503** `not_ready`: o registro não foi composto.

Inversa por fila (hoje): `aprendizado` DESLIGA o item (`published → disabled`, pelo mesmo caminho de
`POST /api/aprendizado/{kind}/{ref}/status`; vale como veto, a plataforma não decide de novo), nunca `published →
validated`, que o ciclo do livro não tem; `pergunta`, `objetivo` e `pedido` respondem `sem_inversa_segura`.

Entradas do registro (o adaptador do 28.25, idempotente pela `origem_ref`): os eventos `run.updated` e `objective.updated`
com `dados.vencimento = {regra, horas, desde}` (31.43; a forma antiga `dados.expirada` do 29.50 não é lida) e as linhas de
`learning_transitions` com `decided_by = 'plataforma'` e motivo `auto:<regra> v<n> — <fatos>`, também depois do prefixo
`confirmado que fica: ` (30.55). Quem decide registra direto por `app/shared/decisoes.py::registrar_decisao`.

## Adendo v1.23 (04/10/2026; número da orquestradora; item 31.43) — a pergunta parada vence sozinha: `vencimento` nos eventos

Aditivo ao v0.95. Nenhuma rota nova, nenhum status novo e nenhuma migração. O prazo da pergunta sem resposta passa a vir do
config (`execucao.pergunta_vence_h`, 24 h por padrão; `execucao.vencimento_ligado` desliga). O que muda para quem lê:

- O `data` do `run.updated` que a 29.50 emite (`needs_input` → `cancelled` pelo sistema) ganha `vencimento`; o `expirada` do v0.95
  continua, igual, para quem já o lê.
- Caso novo: o objetivo em `waiting_user` de execução já terminada (`completed_with_issues`) que ninguém retomou no prazo vai a
  `cancelled` pelo sistema, e o `objective.updated` dessa transição leva o mesmo campo. A execução segue como
  `recompute_run` a deriva (sem `cancel_requested`, sem o sinal `cancelou_execucao`).
- Formato fixo, exatamente estas quatro chaves nos dois eventos:

```json
{"objective": {"status": "cancelled", "…": "…"},
 "vencimento": {"regra": "31.43", "motivo": "vencido_sem_resposta", "horas": 24, "desde": "2026-10-03T12:00:00.000Z"}}
```

- `desde`: no `run.updated`, o `ts` da entrada em `needs_input`; no `objective.updated`, o mais tardio entre a entrada do
  objetivo em `waiting_user` e o fim da execução. `horas` é o prazo em vigor (24, ou 0.5 se o config disser meia hora).
- `status_detail` do objetivo: "Sem resposta em 24 h: o pedido venceu e foi encerrado pelo sistema. Para seguir, faça o
  pedido de novo."

Prova:
- `simulated`: `tests/test_pergunta_vence.py`, `tests/test_needs_input_expira.py`.
- `not_run`: o primeiro ciclo no central depois do deploy (fecha o estoque de 22 objetivos).
## Adendo v1.24 (04/10/2026; número da orquestradora; item 30.55) — a aprovação automática por política

`GET /api/aprendizado/aprovacao-automatica?itens=` é só leitura. `itens` é um booleano (padrão `false`).

```json
{"modo": "shadow",
 "ultima_volta": {"em": "2026-10-04T17:00:00.000Z", "modo": "shadow", "avaliados": 40,
                  "decidiria": ["receita:180", "fluxo:enviar-bom-dia-a-cada-contato-da-lista-d"], "marcados": 18,
                  "decididos": [], "fora": {"app_fora_do_qa": 11, "classe_c": 10, "sem_a_favor": 18}},
 "casos_na_sombra": 18,
 "decididos_pela_plataforma": [{"item_ref": "receita:180", "de": "validated", "para": "published",
                                "motivo": "auto:qa_para_aprovar v1 — classe B; …", "em": "…"}],
 "itens": [{"item_ref": "receita:22", "fila": "revisar", "regra": "qa_revisar", "decide": false,
            "fora": ["app_fora_do_qa", "classe_c", "sem_a_favor"], "classe": "C", "apps": ["com.instagram.android"],
            "a_favor": 0, "contra": 0, "falhas_de_reproducao": 0, "saude": "sem_evidencia"}]}
```

- `ultima_volta` é `null` antes da primeira volta deste processo (90 s depois do início).
- `decididos_pela_plataforma` traz as últimas 50 linhas da trilha com `decided_by = "plataforma"`, da mais nova para a
  mais antiga. Cada uma vem com o item de AGORA: `kind`, `ref`, `titulo`, `app` e `estado` (o painel oferece Desligar
  só ao que segue `published`); `gesto` (`publicar` ou `confirmar_que_fica`); `regra` e `versao` lidas do motivo. O
  item que saiu do livro vem com `titulo`, `app` e `estado` nulos.
- `itens` só vem com `itens=true`. Os motivos de fora são um vocabulário fechado
  (`domain/aprovacao_automatica.MotivoDeFora`).
- **503** `not_ready`: a aprovação automática não foi composta.

Na trilha (`learning_transitions` e o detalhe do item), a decisão da plataforma é uma linha com
`decided_by = "plataforma"`:
- a publicação é `validated → published`, com o motivo `auto:<regra> v<n> — <fatos>`;
- a confirmação de "Revisar" é `published → published`, com o motivo `confirmado que fica: auto:<regra> v<n> — <fatos>`.

As regras são `qa_para_aprovar` e `qa_revisar`. O regex é `(?:^|: )auto:(?P<regra>[a-z_]+) v(?P<versao>\d+)(?: — |$)`.

O desfazer é o `POST /api/aprendizado/{kind}/{ref}/status` de sempre, com `{"to": "disabled", "reason": "…"}`:
- **200** com o item em `item.state = "disabled"`;
- **409** `transition_forbidden` quando o item já está desligado.

O operador de sessão chamado `plataforma` é gravado como `painel:plataforma`, como já acontece com `sistema`.

Config: `aprendizado.aprovacao_automatica.modo` = `off` (de fábrica), `shadow` ou `on`, e `intervalo_s` (900).

- `simulated`: `tests/test_aprovacao_automatica.py`.
- `real`: `not_run` até o deploy.
## Adendo v1.27 (04/10/2026; número da orquestradora; item 28.10 F4) — o relatório do pai consolida os filhos

Aditivo ao v1.16 e ao v1.17. Sem migração e sem rota nova. Só vale com `pedidos.colaboracao.enabled` e para um pedido com filhos;
sem isso o relatório é idêntico ao de antes (a chave `consolidacao` não existe).

- **`conteudo` do relatório** (`GET /api/pedidos/{id}/relatorios`, `.../{rid}`; qualquer gatilho) ganha o objeto `consolidacao`:
  - `fontes[]`: `{filho_id, papel, estado, situacao: "com_dado"|"sem_dado", em_andamento, observacoes, memoria}`, uma por filho direto;
  - `valores[]`: `{origem: "observacao"|"memoria", alvo, nome, tipo, valor, fontes: [{filho_id, papel}], n_fontes}`: o mesmo valor
    vindo de vários filhos aparece uma vez;
  - `conflitos[]`: `{origem, alvo, nome, tipo, versoes: [{valor, fontes, n_fontes}]}`: valores diferentes para a mesma chave. Não há
    campo de vencedor: o relatório não resolve por voto nem por maioria;
  - `omitidos: {valores, conflitos}` (tetos 200 e 100) e `resumo: {filhos, com_dado, sem_dado, em_andamento, valores, conflitos}`.
- **Origem dos dados:** observações comprovadas (`observado`, com valor, ocorrência `concluida`) e memória `descoberta`, `decisao` e
  `fonte` dos filhos diretos. Nunca o texto livre das execuções, o título do filho ou nome de persona.
- **`nao_coberto`** ganha os tipos `conflito_entre_filhos`, `filho_em_andamento`, `filho_sem_dado` e `consolidacao_indisponivel`, e a
  `conclusao.situacao` fica `parcial` com qualquer um deles.
- **Aviso `relatorio_pronto`:** quando o relatório tem o bloco, `dados` ganha `filhos_lidos` e `conflitos` (inteiros) e a mensagem do
  painel termina com "Consolidou N filho(s); N conflito(s).". O texto do canal de fora (Telegram) leva só "N conflito(s) entre os
  filhos", sem conteúdo e sem nome.

Prova:
- `simulated`: `backend/tests/test_pedidos_colaboracao_consolidacao.py`.
- `not_run`: pedido pai de teste no app de teste, PostgreSQL e o central (a colaboração está desligada lá).

## Adendo v1.28 (04/10/2026; número da orquestradora; item 28.10 F5, parte 1) — só o porta-voz age; duas reações ao mesmo conteúdo são recusadas

Sem migração, atrás de `pedidos.colaboracao.enabled` (desligada, tudo como antes).

- **Teto da família.** Numa família (a raiz e os filhos diretos) com um porta-voz que não foi cancelado, o pedido SEM papel (a
  raiz ou um irmão comum) decide e executa com `observar`: `autonomia_efetiva` ganha o terceiro argumento
  `familia_com_porta_voz`. O laço leva esse teto à execução (`RunCreate.teto_de_autonomia`, 28.23) e a ocorrência registra
  "autonomia rebaixada: a família tem porta-voz e só ele age para fora: agir → observar" (só autonomias, nunca persona, conta ou
  texto do comando). O porta-voz segue com `agir` e os papéis com teto próprio mantêm o deles.
- **`PedidoView.autonomia_efetiva`** (campo novo, sempre presente): a autonomia com que o pedido decide hoje.
- **Prévia.** `alertas` ganha `autonomia_rebaixada_pela_familia` (um filho novo sem papel numa família com porta-voz) e
  `autonomia.teto` mostra o teto efetivo: a pessoa vê o rebaixamento antes de criar. O selo continua cobrindo a autonomia pedida.
- **Recusa nova, 422 `reacao_repetida`** (campo `objetivo`; prévia e criação, também na conferência dentro da transação): um
  filho cuja autonomia efetiva é `preparar` ou `agir`, com o mesmo objetivo (sem diferença de caixa ou espaços) de um pedido
  vivo da família que também tem efeito, e personas diferentes (a do alvo, ou a única do aparelho). A mensagem não cita
  persona nem texto. O que o código não lê (a mesma reação com outras palavras, uma persona citar a outra como terceiro) é do
  planejador.
- **Prova:** `simulated` (`backend/tests/test_pedidos_colaboracao_para_fora.py`). `not_run`: pedido real depois do deploy.

As regras 1 e 2 (uma conta por alvo no pedido inteiro e `approval_required` para pessoa real sem conversa prévia) são da porta de
política (`PolicyEngine.check`, item 30.62): o provedor `contexto_do_pedido(run_id)` vem num PR à parte.

## Adendo v1.29 (04/10/2026; número da orquestradora; item 28.29) — o registro das decisões diz qual item e o estado de agora

Aditivo ao v1.22. Sem rota nova e sem migração.

- **`GET /api/decisoes-automaticas`, cada item:**
  - campos novos `item_nome` (o título do item no livro do aprendizado; `null` nas outras filas ou quando o item sumiu) e
    `run_id` (a execução do objetivo ou da pergunta vencida; `null` no aprendizado);
  - `efeito` sai legível e com gênero ("Receita publicada…", "Fluxo confirmado…"), também nas linhas gravadas antes;
  - `fatos.texto` vem cortado na última palavra inteira, com "…";
  - `fatos` da decisão nova do aprendizado pode trazer `confirmacao: true`, e a do objetivo vencido traz `run_id`.
- **Estado de agora:** antes de responder, a lista reconcilia cada decisão não desfeita com a fila dona. O item do
  aprendizado que alguém desligou por outro caminho, como a rota do livro, aparece com `desfeita: true`. `desfeita_por`,
  `desfeita_em` e `motivo_do_desfazer` vêm da última transição para `disabled` na trilha (gravados uma vez, CAS).
  - O filtro `desfeitas=nao` já tira essas linhas.
  - O item que mudou de outro jeito (aposentado) volta com `pode_desfazer: false` e o porquê.
  - O `POST …/desfazer` de uma decisão desfeita por outro caminho responde `desfeita_agora: false` e não troca autor
    nem motivo.
- **Resumo no Telegram** (não é rota; `docs/dominios/canais.md`, C-23): o desfazer só é oferecido para o aprendizado. O
  encerrado diz que não reabre, e as aprovações pendentes que o vencimento encerrou junto aparecem pela contagem.
- O `ja_estava` do `POST /api/canais/anexos/{id}/trello` (o mesmo arquivo não vai duas vezes ao cartão) está descrito no
  trecho da rota, no PR do 28.24 F4.

Prova:
- `simulated`: `tests/test_decisoes_registro_coerente.py`.
- `not_run`: PostgreSQL e o central depois do deploy.

## Adendo v1.30 (04/10/2026; número da orquestradora; item 30.65) — exceção de uso único à regra de uma conta por alvo

Migração `104_excecoes_de_politica`. A porta de frota (ADR-055) recusa, sem caminho de aprovação, o efeito sobre um alvo que
outra conta da frota já tocou na janela. A exceção tira só essa recusa, só para o perfil, o alvo e a ação dela, e NÃO libera
sozinha: a etapa casada vira `approval_required` e aparece em Pendências com o motivo "exceção … à regra de uma conta por alvo
em 30 dias (ADR-055), criada por <autor> em <hora> …; autorização citada: <texto> … uso único (30.65)". "Autorizada pelo
dono" só aparece quando quem criou era operador com sessão no painel: o loopback cria sem sessão, e aí a autorização é só
texto citado. Conta retirada, espaçamento, tetos, DM fria e repetição (30.64) seguem valendo. A etapa que usa a exceção
sempre abre cartão novo: o aprovado de uma versão anterior com o mesmo texto e alvo não é reaproveitado.

- **`POST /api/politica/excecoes`** (201), atrás do login. Corpo, todos obrigatórios, campo desconhecido é recusado:
  `profile_id` (perfil de ORIGEM), `alvo` (o @, normalizado), `capability` (`SEND_MESSAGE`...), `motivo`, `autorizacao`
  (quem autorizou, por onde e quando) e `expira_em` (UTC ISO; no máximo 72 h a partir de agora). O autor é o operador da
  sessão; sem sessão (loopback), o rótulo `panel`, e `autor_com_sessao` fica falso. Resposta `{"excecao": {...}}`.
  422 `excecao_invalida`: prazo acima de 72 h ou já passado, alvo vazio, perfil inexistente, alvo que não é conta nossa
  viva (abrir para pessoa real é decisão do dono), ou já existe uma em aberto para o mesmo perfil, alvo e ação. 409
  `note_looks_secret`: `motivo` ou `autorizacao` com formato de credencial, antes de qualquer escrita (a triagem de nota;
  hora com segundos, `19:02:26Z`, cai nela: escreva `19:02 UTC`).
- **`POST /api/politica/excecoes/{id}/revogar`** (200): encerra a exceção em aberto, livre ou presa. `{"excecao": {...}}`
  com `estado: "revogada"`. 404 `not_found`; 409 `excecao_encerrada` se ela já terminou. Com o cartão da etapa presa
  ainda pendente, ele expira (sai de Pendências; o reply no Telegram é recusado como vencido) e o objetivo volta à
  porta, que recusa. Se a etapa já passou da porta (aprovada, antes do commit), a RESERVA do executor falha: logo antes
  do gesto ele faz um UPDATE condicional (`em_uso`) e, se a exceção não está em aberto (revogada, recusada, vencida, já
  em uso ou usada), a etapa falha fechada com o motivo "a exceção … antes do efeito; o efeito não foi disparado (30.65)"
  e o gesto não acontece; se a reserva levantar, também não. Se a reserva ganhou antes, revogar responde 409
  `excecao_em_uso` ("o efeito pode ter saído") e nunca grava "revogada" por cima. A reservada é liquidada no
  `settle_effect`: usada se o efeito saiu ou pode ter saído, `sem_efeito` se não saiu (não volta a aberta); a queda do
  processo a deixa `em_uso` até a etapa terminar: então ela fecha usada (etapa `succeeded`) ou `incerta` (o resto; o
  efeito pode ter saído), nunca reaberta. A vencida continua apontando a etapa (a reserva a encontra). A porta liga à
  etapa só a exceção que usou (`prender`, que falha se ela deixou de estar em aberto, e a porta recusa) e solta todas
  quando passa a etapa sem exceção: o commit reserva exatamente a que a porta usou. A reservada nunca vale para outra
  etapa. O motivo do desfecho não cita pessoa nem alvo. O cartão cita no máximo 80 caracteres da autorização (o aviso do Telegram corta em 500).
- **`GET /api/politica/excecoes?profile_id=`**: `{"excecoes": [...]}`, as mais novas primeiro (até 200). Cada uma traz
  `id`, `regra` (`uma_conta_por_alvo`), `profile_id`, `alvo`, `capability`, `motivo`, `autorizacao`, `autor`,
  `autor_com_sessao`, `criada_em`, `expira_em`, `step_id`, `presa_em`, `usada_em`, `interaction_id`, `vencida_em`,
  `em_uso_em`, `etapa_do_uso`, `run_do_uso` (a etapa e a execução que a reservaram: gravadas na reserva, nunca
  limpas; os eventos as usam), `encerrada_em`, `encerrada_por`, `encerramento` e `estado` (`ativa` | `presa` | `em_uso` | `usada` |
  `vencida` | `recusada` | `revogada` | `sem_efeito` | `incerta`). Ler encerra as vencidas.
- **Ciclo.** A porta do despacho prende a exceção à etapa que casou (outra etapa só a toma se a presa terminou sem efeito);
  o `open_effect` a gasta quando o efeito sai (uso único: a segunda volta à recusa; uma falha ao gastar não derruba o
  efeito, que fica registrado). Sem uso até `expira_em`, ela vence e solta a etapa; o vencimento roda na porta do despacho
  e na leitura da rota, não há varredura em segundo plano. Rejeitar o cartão da etapa presa a encerra (`recusada`), e a
  rota de revogar também: encerrada não volta a valer para etapa nenhuma.
- **Eventos** (persistidos, sem aviso no Telegram): `politica.excecao_criada`, `politica.excecao_usada`,
  `politica.excecao_vencida`, `politica.excecao_recusada`, `politica.excecao_revogada`, `politica.excecao_sem_efeito` e
  `politica.excecao_incerta`. Enxutos: `data` leva só `excecao_id`, `estado`, `encerrada_por`, `step_id`, `run_id` e,
  quando há, `interaction_id`; alvo, motivo e autorização ficam só no GET. Não entram no registro de decisões automáticas (28.25): é decisão de pessoa.
- **Prova:** `simulated` (`backend/tests/test_excecao_de_politica.py`). `not_run`: a exceção do 31.26, que a orquestradora
  cria depois do deploy 32.

## Adendo v1.31 (04/10/2026; número da orquestradora; item 28.31 F2a) — o pedido guarda quem o criou, e o aviso diz se é do dono ou de um lote

Migração 106 (`pedidos.criado_por_tipo`, `pedidos.lote`), decididas uma vez, na criação (`POST /api/pedidos`), nesta
ordem (contrato da orquestradora, 04/10 20:27Z):

1. `idempotency_key` que começa com `lote:`: `frente`, SEMPRE, mesmo com operador; `lote` guarda a chave.
2. Operador na lista declarada do dono: `dono`. A lista é a chave nova `pedidos.operadores_do_dono` (config por
   instalação, nomes de sessão do painel, sem diferença de caixa e espaço), mais o `trello:<trello.membro_dono>`. Vazia de
   fábrica: ninguém é o dono.
3. Outro operador: `convidado`. O `POST /api/login` aceita qualquer nome, então uma sessão das frentes ou a pessoa de
   confiança caem aqui.
4. Sem operador: `desconhecido`. O loopback sem sessão não é o dono. `ia` é do vocabulário; hoje nada a cria.

O `PedidoView` não muda. O **`AvisoDTO`** (`GET /api/pedidos/avisos` e o evento `pedido.aviso`) ganha dois campos, sempre
presentes:

- `criado_pelo_dono` (booleano): o pedido é do dono. Só aí o canal de fora pode mostrar o título; os outros saem pelo id curto.
- `de_lote` (booleano): o pedido é do lote de uma frente. O aviso vai à janela de rotina do canal, qualquer que seja o nível
  (tipo `pedido.lote.<tipo>` na fila de envio), menos `aprovacao_pendente` (só o dono decide) e `ocorrencia_incerta`
  (efeito incerto em conta real é crítico), que saem na hora.

Pedido anterior à 106: os dois `false`. **Prova:** `simulated` (`backend/tests/test_pedidos_autor.py`). `not_run`: um pedido
real de lote depois do deploy.

## Adendo v1.32 (04/10/2026; número da orquestradora; item 30.61) — a prévia da porta e a aprovação antecipada no plano

Migração `105_aprovacao_no_plano`. O dono vê, numa execução `planned`, o que a porta do despacho vai fazer com cada etapa
de efeito, e aprova antes de iniciar o que pede o aval dele. O sim do plano só afrouxa a parada para o item IDÊNTICO
(a chave), dentro da validade e uma vez; qualquer diferença volta ao fluxo de hoje (pedido na execução). Nesta fatia o
texto ainda por escrever (briefing) fica para a execução: aprovar no plano exige texto final (`content_verbatim`).

- **`GET /api/runs/{id}/porta`** (200), só leitura: não grava decisão, não abre pedido, não prende exceção, não escreve
  rascunho, não chama IA. 404 `not_found`; 409 `invalid_state` fora de `planned`; 409 `no_plan` sem etapas. Cada item:
  `objective_id`, `step_id`, `aparelho`, `titulo`, `persona_rotulo`, `profile_id`, `app`, `acao`, `alvo`, `objeto_alvo`,
  `selo`, `motivo`, `dica`, `retry_at`, `texto` (o literal, quando final), `texto_na_execucao`, `tem_imagem`,
  `imagem_sha256`, `chave` (só no selo `aprovacao`), `dependentes` (as etapas do mesmo objetivo que dependem desta,
  transitivas) e `falhou`. Selos, pela MESMA conta do despacho (`AppState.vereditos_da_porta`):
  - `permitido`: segue sem parar;
  - `aprovacao`: pede o aval (política, DM fria, teto `preparar`, mensagem repetida; o "mesmo pedido a várias contas" saiu no 31.285, ADR-083), com
    chave;
  - `adiado`: espera até `retry_at`;
  - `recusado`: não acontece (inclusive o mesmo efeito não-DM sobre o mesmo objeto duas vezes no plano);
  - `na_execucao`: decide-se na execução, sem Aprovar: texto por escrever, alvo por resolver (`{item}`, `{{saida:…}}`),
    aparelho fora do gerenciador, etapa que usa exceção de política (30.65: sempre decisão nova), DM idêntica repetida no
    plano, item cuja chave é `None` e item cuja porta falhou.
- **A chave** (`social/chave_da_aprovacao.py`, `VERSAO_DA_CHAVE = 1`): sha256 do JSON canônico de perfil, aparelho, pacote,
  ação, alvo normalizado, objeto-alvo (30.64), TODOS os argumentos da etapa (fora texto e mídia), texto literal, sha256
  dos bytes da imagem, execução e objetivo. `None` (falha fechado): objeto-alvo não declarado, ausente, vazio ou por
  resolver; alvo vazio com `counterparty` declarado; argumento ou texto com variável; texto por escrever; imagem sem
  sha256; e **ação cujo `objeto_alvo` declarado não identifica o objeto do efeito** (`OBJETO_INSUFICIENTE`, hoje só
  `REPLY_COMMENT`, que declara só `username`: a pergunta acontece na execução, com o comentário à vista; quando o
  catálogo ganhar o argumento que diz QUAL comentário, a ação sai da lista).
- **`POST /api/runs/{id}/aprovar-plano`** (200), "Aprovar N e iniciar". Corpo: `aprovar: [{step_id, chave}]` (a chave
  que o dono VIU) e `tirar: [step_id]` ("Não fazer esta"). O servidor recalcula a prévia: item que não é mais
  `aprovacao` com a MESMA chave, ou etapa tirada que não está no plano, devolve 409 `plano_mudou` com `mudaram`
  (`step_id`, `selo`, `motivo`) e `previa` (a nova), e nada é gravado. 422 `invalid_body`: a mesma etapa com duas chaves,
  ou para aprovar e tirar. Senão, numa transação: cada sim vira `pending_approvals` `approved` de origem `plano`
  (`chave_sha256`, `chave_v`, `plan_version`, `expires_at`, `midia_sha256`, `decided_by`); as tiradas e as dependentes
  delas (pela conta do servidor, nunca pela lista do cliente) vão a `cancelled`; uma `decision` registra o gesto. Cada
  item aprovado aceita `texto` opcional, o texto EDITADO no cartão: a chave conferida é a vista (a do texto da prévia),
  o texto entra na etapa (`apply_edit`) e a chave gravada é a recalculada da etapa relida (a do texto que vai sair);
  texto vazio, com marcador de modelo (`{nome}`, `{{…}}`; uma chave solta é texto) ou em ação que não escreve dá 422
  `invalid_body`, e acima de 2200 caracteres 422 `texto_longo`, sem gravar nada. O selo se refaz com o texto editado: se
  o item deixa de ser 🔒 com chave (por exemplo, o comentário repetido), 409 `plano_mudou` com "com o texto editado: …" e
  a edição desfeita. Depois,
  `runs.start`. Resposta `{run, aprovacoes, tiradas, validade_ate}`. O segundo gesto é recusado (409 `invalid_state`).
- **`POST /api/runs/{id}/porta/renovar`** (200): a validade dos sins do plano ainda VÁLIDOS volta a contar de agora,
  sem reabrir os itens. `{run_id, renovadas, vencidas, validade_ate}`. O sim que já venceu (mesmo que a faxina ainda não
  o tenha marcado) não se renova: sai como `expired` na hora; se nenhum foi renovado, 409 `sim_vencido` (`vencidas`),
  e o dono revê a prévia ou a porta pergunta na execução. Os vencidos também ganham uma `decision` (só a contagem). 404;
  409 `invalid_state` em execução terminada.
- **Validade:** `Settings.aprovacao_no_plano_validade_h` (padrão 24, de 1 a 72).
- **Na execução** (trava deste item; o 31.49 estende): o `_approval_gate`, depois do descarte do 30.65 (aprovação anterior
  a `presa_em` não vale), aceita o sim de origem `plano` só se `approved`, dentro de `expires_at`, sem `interaction_id` e
  com a chave da etapa RELIDA idêntica. Senão ele sai como `expired` (`decided_note` "sim do plano descartado: <porquê>"),
  uma `decision` registra, e a porta abre o pedido de origem `execucao` de hoje, que passa a mandar. O sim do plano não
  migra para a etapa revisada (`acompanhar_revisao`), e o de versão anterior do plano do objetivo não conta como pedido em
  aberto (frota, 30.56, 30.57). O objetivo encerrado (cancelado, vencido, abandonado) encerra o sim sem efeito, e a faxina
  periódica marca `expired` o que passou da validade; a execução `planned` em si não é cancelada.
- **O executor honra o plano (31.49):** a chave da etapa relida sai só de `chave_da_aprovacao`, depois de
  `resolver_saidas`; a execução para só pelo que só existe lá (texto gerado, item coletado em tempo de execução, estado
  mudado), e item diferente pergunta de novo. Três regras a mais: (1) depois da chave, o sim do plano de uma capacidade
  com texto só vale se o `content` gravado for o texto exato que vai sair ("o sim do plano não traz o texto que vai
  sair"): aprovação sem texto nunca libera escrita; (2) o `_draft_gate` não trata o sim de origem `plano` como texto
  já mostrado, então a etapa com briefing escreve o texto e a execução pergunta COM ele; (3) o pedido novo de origem
  `execucao` leva no `summary` "o sim dado no plano não vale: <porquê>"; (4) a mensagem repetida que surge DEPOIS do
  `decided_at` do sim (`mensagem_repetida(..., desde=)`: outra execução mandou ou teve aprovado o mesmo texto ao mesmo
  alvo; o pedido de outra etapa conta pela decisão) descarta o sim ("a repetição surgiu depois do sim"); a que já
  existia antes estava no motivo da prévia e segue coberta. O sim do
  plano anterior à exceção 30.65 presa sai como `expired`, não fica `approved`. Prova `simulated`:
  `backend/tests/test_executor_honra_o_plano.py` e
  `test_porta_do_plano.py::test_chave_solta_aprovada_no_plano_e_honrada_pela_porta_na_execucao` (`:-{`).
- **`Approval`** (lista de aprovações, eventos) ganha `origem`, `expires_at` e `plan_version`.
- **Painel** (`frontend/src/features/runs/PortaDoPlano.tsx`): na execução `planned`, a prévia em cartões por aparelho e
  persona, com o selo, "Saiba mais", o texto editável no 🔒, "Não fazer esta" (as dependentes saem junto), a seção
  "Ainda vão pedir você na execução" e a barra "Aprovar N e iniciar"; o 409 `plano_mudou` mostra o que mudou e troca pela
  prévia nova. Na execução viva, `ValidadeDoPlano` mostra a validade dos sins do plano com "Renovar" (o caso misto diz
  "N renovados, M vencidos voltam para você rever").
- **Prova:** `simulated` (`backend/tests/test_porta_do_plano.py`, `backend/tests/test_chave_da_aprovacao.py`,
  `frontend/src/features/runs/PortaDoPlano.test.tsx`). `not_run`: o percurso no navegador (depois do deploy) e qualquer
  execução real.

## Adendo v1.33 (04/10/2026; número da orquestradora; item 31.50) — `vence_em` e o lembrete antes do vencimento

- `RunSummary.vence_em`, `Objective.vence_em` e o campo `vence_em` de cada item de `GET /api/approvals` (pendentes): o
  instante ISO em que o item vence pelo sistema. O cálculo é o mais tardio entre a entrada na espera e a marca de quando
  o vencimento foi ligado (`vencimento_ligado_desde`), mais `execucao.pergunta_vence_h`. É `null` fora da espera
  (`needs_input`; objetivo `waiting_user` de execução terminada; aprovação `pending`) e com o vencimento desligado.
  A aprovação vence junto com o objetivo que bloqueia. Campo novo e opcional: cliente antigo o ignora.
- As listas (`GET /api/runs` e afins) leem as entradas em `needs_input` numa consulta só, para todas as execuções.
- Evento novo `pendencia.vence_em` (tabela de eventos acima). Sai uma vez por ESPERA, 2 h antes de vencer, com a chave
  `vencimento:lembrete:<id>:<entrada na espera>`: o objetivo retomado que volta a esperar ganha outro lembrete. O
  texto ao dono é do montador dos avisos (28.31).

## Adendo v1.34 (04/10/2026; número da orquestradora; item 30.66) — a decisão da plataforma com o nome do catálogo

`GET /api/aprendizado/aprovacao-automatica` (adendo v1.24): cada linha de `decididos_pela_plataforma` ganha três campos,
lidos do item de AGORA, em lote (os mesmos `capabilities`/`nomes_das_capabilities` da rota do livro):

- `capability`: a ação do catálogo do item (receita: a derivação do detalhe; lição e tela: a `scope_capability`), ou
  `null` quando não se sabe;
- `capability_nome`: o nome dela em português, do catálogo do app (por exemplo "Enviar a mensagem"), ou `null` sem
  catálogo;
- `etapa`: o título da etapa de origem da receita, ou `null`.

O item que saiu do livro vem com os três nulos, como `titulo`. Campos novos e opcionais: quem não os lê não muda. O painel
titula a decisão como as outras telas (`tituloDoItem`: "Enviar a mensagem (v1)"), e sem eles cai no título de antes.
Prova `simulated`: `backend/tests/test_aprovacao_automatica.py` e `frontend/src/features/aprendizado/DecididoPelaPlataforma.test.tsx`.

## Adendo v1.35 (04/10/2026; número da orquestradora; item 28.30) — comentário do dono no Trello vira confirmação no Telegram; o login recusa o prefixo dos canais

- **`POST /api/login`:** o `operator` que começa com `trello:` ou `telegram:` (sem diferença de caixa, espaços ignorados) é
  recusado como nome inválido, a mesma resposta do nome curto ou com caractere de controle. Esse é o operador das conversas
  dos canais, e `trello:<membro_dono>` é dono na criação do pedido (Adendo v1.31).
- **Aviso novo do canal de fora, tipo `trello.comentario`:** o comentário do dono num cartão do quadro sem aviso da
  Central vai à orquestradora (`canal_entradas.estado='orquestradora'`, `previa.repasse='comentario'`), recebe resposta
  no cartão e gera um pedido de confirmação no Telegram dele. A chave é `comentario:<action>:<card>`, e o aviso nunca é
  agrupado. O nome do cartão e o texto só vão se passarem inteiros pelos filtros do 28.31.
- **Repasses novos:** o sim e o não do dono, em reply àquele aviso, ficam com a orquestradora (`previa.repasse` =
  `comentario_sim` ou `comentario_nao`). Nada se executa e nada se aprova por eles.
- **Prova:** `simulated` (`backend/tests/test_canais_comentario_do_dono.py`). `not_run`: comentário real num cartão de
  teste.

## Adendo v1.37 (04/10/2026; número da orquestradora; item 28.24, F5) — a lista diz o que dá para ler, e o cartão recusa o tipo

Sem migração. Muda a v1.26 e a rota `POST .../trello`.
- `GET /api/canais/anexos`: cada item ganha três chaves fixas.
  - `pode_ler`: a imagem guardada que o dono mandou, a única que `POST .../ler` aceita.
  - `descricao`: a descrição que a IA já gravou, ou `null`.
  - `lida_em`: ISO, ou `null`.

  `pode_ir_ao_cartao` passa a exigir também um mime da lista `avisos.entrada.anexos.tipos`.
- `POST /api/canais/anexos/{id}/trello`: o anexo de tipo que a entrada não aceita, ou maior que o teto, é recusado com **422**
  `tipo_nao_aceito` antes de qualquer chamada ao Trello. O arquivo fora do armazém segue **409** `anexo_sem_arquivo`.

## Adendo v1.36 (04/10/2026; número da orquestradora; item 29.77) — o contato do site institucional

Uma rota nova, pública, desligada de fábrica (ADR-075). O `GET /api/portal/info` reservado junto saiu: os contatos da
página vão no HTML. Migração `107_portal_contatos`.

- `POST /api/portal/contato`, **sem credencial** (a exceção do portão é só este caminho e só `POST`; `GET` segue 401 de
  fora). `forbidden_host` e a checagem de `Origin` valem como em toda rota. Com `portal.contato_ligado` desligado: **404**
  sem corpo.
- Corpo: JSON `{nome, empresa?, telefone, mensagem, consentimento: true, site, token}`. `site` é a isca (vazia);
  `token` é o do campo oculto da página (`ts.hmac`). Tetos: nome e empresa 80, telefone 30 (dígitos, espaço, `+ ( ) -`,
  8 dígitos ou mais), mensagem 1500.
- **202** `{"ok": true}` com `Cache-Control: no-store`: contato aceito (gravado; entregue à Canais, retido pelo teto da
  hora ou pendente para o laço). A MESMA resposta para isca preenchida e token ausente, falso ou cedo demais, que não
  gravam nada.
- Erros (`detail.code`, com `detail.message` em português): **400** `token_expirado` (recarregar a página); **413** sem
  corpo (acima de `portal.limites.corpo_max_bytes`, lido em fluxo); **415** sem corpo (`Content-Type` não é
  `application/json`; nada é lido); **422** `campo_invalido` (`detail.campos` lista os nomes) ou `consentimento_ausente`;
  **429** `muitas_mensagens` (taxa por cliente ou teto diário; a mensagem aponta os telefones da página).
- O corpo nunca volta na resposta nem no log. Nada vai a `runs`, `pedidos`, aprovações ou IA.
- Prova: `simulated` (`backend/tests/test_portal_contato.py`). `not_run`: ligado no central.

## Adendo v1.38 (04/10/2026; número da orquestradora; item 30.68) — a prévia do texto editado antes do sim

O v1.32 conferia a chave do texto DA PRÉVIA e recalculava no servidor a do texto editado: o dono aprovava sem ver o que
a porta fazia com o texto novo (a DM repetida passa a dizer "esta conta já mandou ESTA mensagem"), e um texto que tirava
o item do 🔒 só aparecia num 409 depois do clique.

- **`POST /api/runs/{id}/porta/item`** (200), corpo `{step_id, texto}` (`texto` até 20 000 no corpo; o limite real é o
  de baixo): o item da prévia recalculado com o texto proposto no lugar do da etapa. Resposta
  `{step_id, texto, item}`: `texto` já sem espaço nas pontas; `item` é o `ItemDaPorta` do v1.32 (selo, motivo, dica,
  chave), ou `null` se a etapa não fecha mais um item. Só leitura: não grava, não chama IA; a mesma conta (`_item`) da
  `GET …/porta`, sobre uma cópia da etapa, DENTRO do laço do plano (a regra do mesmo efeito duas vezes no plano vale
  igual). 404 execução inexistente; 409 `invalid_state` fora de `planned`; 409 `plano_mudou` se a etapa saiu do plano
  ou se ela não é 🔒 com chave na prévia inteira (a 2ª DM ao mesmo alvo, o 2º comentário no mesmo objeto); 422
  `invalid_body` (texto vazio, com marcador de modelo, ou ação que não escreve) e `texto_longo` (acima de 2200
  caracteres), as mesmas recusas do gesto.
- **Gesto (`POST …/aprovar-plano`):** só o item que é 🔒 com chave na prévia inteira aceita `texto` (senão 409
  `plano_mudou`). No item com `texto` editado (diferente do da prévia), `chave` passa a ser a da
  prévia DESSE texto (a que a rota acima devolveu). O servidor a confere antes de gravar (selo 🔒 e chave iguais à do
  texto editado; senão 409 `plano_mudou` com "com o texto editado: …") e de novo na etapa relida depois do `apply_edit`
  (diferente: 409 e a edição desfeita). A chave gravada é, portanto, a que o dono viu.
- **Imagem no cartão:** o `ItemDaPorta` ganha `image_id` (a imagem da publicação, só a de sha256 conhecido, a mesma
  da chave); o cartão do plano mostra a imagem que vai ao feed, não só "com imagem" (29.30).
- **Painel:** ao sair do campo, ou depois de 500 ms sem digitar, o painel pede a prévia do texto editado; o "Aprovar"
  fica travado ("Conferindo na porta o texto editado…") até ela voltar para o texto atual (resposta de texto antigo não
  vale). Junto do item: "Com este texto: pede seu aval — <motivo>", ou, se o item deixou de ser 🔒, "Com este texto, a
  ação não pede mais o seu aval aqui (<selo>): <motivo>", com o "Aprovar" travado até o dono voltar ao texto da prévia
  ou tirar a ação. Falha ao conferir trava com o motivo; sair do campo tenta de novo.
- **Prova:** `simulated` (`backend/tests/test_porta_do_plano.py`: a rota, o só-leitura e a DM repetida com a regra
  real; `frontend/src/features/runs/PortaDoPlano.test.tsx`). Nenhuma regra real tira hoje um item do 🔒 só pela edição
  do texto; o teste desse caminho é com selo forçado. `not_run`: o percurso no navegador e qualquer execução real.

## Adendo v1.39 (04/10/2026; número da orquestradora; item 28.32) — a mensagem do visitante do site chega ao Telegram do dono

Sem rota HTTP nova. É o contrato interno que a rota de contato do Portal (29.77) chama. A regra de produto é a C-25 de
`docs/dominios/canais.md`.

- `state.avisos.avisar_contato_do_portal(ContatoDoPortal) -> ContatoAvisado`:
  - entrada: `ContatoDoPortal(contato_id: int > 0, nome: 1..80, empresa: 0..80 | None, telefone: 0..30 | None,
    mensagem: 1..1500)`, de `app.modules.avisos.domain.portal`;
  - saída: `ContatoAvisado(enfileirado: bool, motivo: "campo_invalido" | "canal_desligado" | "falha_interna" | None)`.
    `enfileirado=True` também quando o mesmo `contato_id` já estava na fila (chave `portal:<id>`, uma mensagem só).
    Quer dizer "na fila", NÃO "entregue": a entrega pode terminar em `falhou`, e a rota não fica sabendo. A página não
    promete entrega ao visitante;
  - recusa e falha não gravam nada. A rota guarda o contato como "não entregue" e chama de novo depois.
- O aviso, na fila `avisos_entregas`: tipo `portal.contato`, nível 1, sai na hora, um a um (`SEM_AGRUPAR`), sem link,
  nunca no espelho do Trello. Título fixo "ANA: 🌐 Mensagem de visitante do site (não verificada)". O corpo tem as linhas
  rotuladas, os campos de uma linha, a mensagem citada com `│ ` e os links, IPs, `/comandos` e `@menções` desarmados.
  Antes, NFKC (menos os ordinais `º` e `ª`), sem a categoria Cf, no máximo 2 marcas combinantes (Mn, Me) por
  caractere, e todo branco Unicode (Zs, braille em branco, preenchedores do hangul) vira espaço ASCII, que se junta:
  nada empurra o texto do visitante ao começo de uma linha da tela.
  Não passa por `texto_seguro` nem pelo redator (ADR-075).
- O corpo das linhas `portal.%` é apagado no estado final (`enviado`, `falhou`, `incerto`, `descartado`), e o
  `pendente` vence (`validade_h`) mesmo com o canal desligado. O adaptador corta o texto em 4000 unidades UTF-16, que é
  como o Telegram conta. A resposta do
  dono à mensagem (fato `portal:<id>`) cai em `desconhecida` e só informa.
- `state.avisos.avisar_resumo_do_portal(retidos: int, descartados: int, janela_h: int) -> ContatoAvisado`: os contatos
  acima dos tetos da rota numa mensagem só de contagens. Não pede o dono: acima do limiar, tipo `portal.resumo`,
  nível 2, sai na hora; abaixo, `portal.resumo_rotina`, nível 3, na janela da rotina, com as contagens no título. Chave
  `portal-resumo:<AAAA-MM-DDTHH>Z` (uma por hora UTC; a segunda chamada na hora volta `enfileirado=True` sem duplicar).
  Inteiros de 0 a 1 000 000, a janela de 1 a 24 h e ao menos um contato; senão, `campo_invalido`. Nunca "Espera
  você": com algum descartado ou com 20 ou mais retidos, o crítico é o possível abuso e a última linha diz que o
  formulário se protege sozinho; abaixo disso, "Crítico: nada." e "Nada a fazer". A resposta do dono a ele só informa.
- **Prova:** `simulated`. Arquivos `backend/tests/test_avisos_portal_contato.py` (domínio) e
  `backend/tests/test_avisos_portal_servico.py` (fila, entrega, corpo apagado, Trello e resposta). `not_run`: envio real
  ao Telegram e a rota do Portal.

## Adendo v1.40 (04/10/2026; número da orquestradora; item 29.79) — o rótulo de IA da publicação

Regra do dono (03/10): imagem realista gerada por IA e publicada pela automação SEMPRE leva o rótulo de IA do
Instagram ("Add AI label").

- **`rotulo_ia` na etapa:** a central grava o argumento na etapa que publica imagem (`image_id`) ao materializar o
  plano, pela origem da imagem em `persona_images.source`: `generated` e `imported_legacy` dão `"true"`; `upload` (enviada
  pelo dono) dá `"false"`. Vale por cima do que o plano disser; imagem por resolver ou inexistente fica sem o argumento.
  Como argumento da etapa, entra na chave da aprovação (v1.32): publicar sem o rótulo é outro item. O sim reaproveitado
  numa revisão do plano exige o mesmo rótulo.
- **`ItemDaPorta.rotulo_ia`** (`boolean | null`) e **`Approval.rotulo_ia`** (`boolean | null`): `true` = sai com o rótulo;
  `false` = sai sem ele (imagem enviada pelo dono; o item diz "sem rótulo de IA (imagem enviada por você)"); `null` = sem
  imagem ou sem o argumento gravado. Marcar um upload como feito por IA é o item 29.81.
- **Imagem de outra persona:** `midia_da_etapa(..., perfil=)` não fecha chave para a imagem cuja `persona_id` não é o
  perfil que publica. Na prévia, o item sai `recusado` ("a imagem é de outra persona"); na execução, a galeria já a
  recusava.
- **Catálogo `commit_switch`** (lista de `<argumento>:<seletor>`, validada na carga: o argumento é declarado pela ação e a
  ação tem `commit_selector`): com o argumento `"true"`, o interruptor do seletor tem de estar LIGADO na tela antes do
  toque de efeito: o elemento marcado, ou o ÚNICO candidato da linha, que é clicável, `checkable` ou marcado, fica à
  direita do texto e tem o centro na faixa dele alargada em meia altura. Sem ele, o toque é recusado; dois candidatos
  empatados recusam como "interruptor ambíguo". Na 2ª recusa (ou na 1ª, vinda de receita) a etapa termina
  `waiting_user`, "nada foi publicado". `UiElement.checkable` é novo e só aparece no dicionário quando é verdadeiro. O CREATE_POST
  declara `rotulo_ia:text==Add AI label`, e o `commit_selector` dele passa a `id=share_footer_button` (medido nas
  capturas da publicação manual do 8.3, android-01, 03/10 04:43Z: o texto "Share" era um TextView filho não clicável).
- **Painel:** "com rótulo de IA" ou "sem rótulo de IA (imagem enviada por você)" no cartão do plano, na aba Textos e no
  guia de aprovações.
- **Catálogo `commit_switch_mark`** (`<argumento>:<seletor>`; exige `commit_switch`): com o argumento `"true"`, depois do
  efeito comprovado a marca tem de estar colada abaixo do nome da conta, no cartão do TOPO da tela. É só leitura e pela
  árvore: a última tela da verificação e no máximo uma releitura. O CREATE_POST declara
  `rotulo_ia:id=secondary_label|text==AI info`, medido no 8.3. Sem a marca, a etapa termina `uncertain`, "publicado; o
  rótulo de IA não foi confirmado. Abra a publicação…", com `StepResult.efeito_comprovado: true`. Nos dois desfechos a
  tela da conferência fica como evidência. **Limite aceito:** se o post novo não estiver na tela e um post ANTIGO da
  mesma conta, com o rótulo, for o primeiro cartão, a conferência ainda passa. Ela só roda depois de a verificação
  comprovar a publicação, sem marca de envio pendente; a evidência mostra qual cartão confirmou.
- **`StepResult.efeito_comprovado`** (`boolean`, ausente quando falso): o efeito em si foi comprovado e só uma afirmação
  sobre ele ficou incerta. A etapa nunca se refaz: `recovery_steps` a atravessa como comprovada, e `POST
  /runs/{id}/objectives/{oid}/resolve` com `retry` responde **409 `efeito_comprovado`**. Restam confirmar (com o print) e
  abandonar. O `needs` do objetivo não oferece repetir, e o painel esconde "Tentar novamente…" nesse caso.
- **Prova:** `simulated` (`backend/tests/test_rotulo_ia.py`, `backend/tests/test_rotulo_ia_no_executor.py`,
  `frontend/src/features/runs/PortaDoPlano.test.tsx`, `frontend/src/features/runs/execution.test.tsx`). `not_run`: a
  conferência dos seletores na versão atual do app (controle manual, sem Share) e a 1ª publicação real.

## Adendo v1.41 (04/10/2026; número da orquestradora; item 28.27) — o Executar do Telegram passa pela porta do plano

Sem rota HTTP nova. A conversa do Telegram usa os MESMOS serviços das rotas do 30.61 (`GET /runs/{id}/porta` e
`POST /runs/{id}/aprovar-plano`). A regra de produto é a C-26 de `docs/dominios/canais.md`.

- O botão `Executar` (`x:<id>`) cria a execução com `mode="plan"`. Ela para em `planned` e nada inicia sozinho.
- O vigia lê `porta_do_plano.previa_da_porta` quando a execução chega a `planned`, sempre com o operador `telegram:dono`.
  - **N = 0** itens aprováveis pelo canal: chama `aprovar_plano(aprovar=[], tirar=[])`, que inicia, e manda uma linha.
  - **N > 0**: a linha volta a `pergunta`, com `previa = {fase: "porta", run_id, curta, hash_do_plano, validade_ate,
    aprovar: [[step_id, chave], …]}`. Saem a prévia e os botões `p:<id>` ("Executar (aprova N)") e `c:<id>` ("Cancelar").
  - Porta ilegível: `RunService.start`, e a porta decide no despacho.
- O botão é `p:<id>:<marca>`, com `marca` = 8 caracteres do sha256 de `[hash_do_plano, aprovar]` (`previa.marca`). Sem a
  marca, ou com a de outro retrato, responde "Essa prévia mudou" e não aprova nada.
- O `p:<id>:<marca>` chama `aprovar_plano` com exatamente os pares de `previa.aprovar`.
  - `plano_mudou` (409): nada gravado, e sai a prévia nova; se ela não tem nada a aprovar, inicia (N = 0) numa linha que
    diz que o plano mudou.
  - `invalid_state`: a recusa volta como texto; a execução que ainda está em `planned` é cancelada.
  - Vencida (`avisos.entrada.ttl_previa_s` da linha ou `validade_ate` da porta): `RunService.cancel`.
- `c:<id>` na fase da porta também cancela a execução `planned`, como todo caminho que abandona a porta (recusa, erro,
  prévia que não saiu, linha presa recuperada depois de `PRESA_S`). A linha presa antes de gravar o `run_id` acha a
  execução pela chave de idempotência do Executar (`telegram:<update_id>`).
- Item aprovável pelo canal: selo `aprovacao` com `chave`, texto que `privacidade.texto_livre` não mudaria (até 3000
  caracteres), bloco que cabe inteiro numa mensagem (3800) e, se `tem_imagem`, bytes cujo sha256 bate com
  `imagem_sha256`. Só a imagem desses itens é enviada. O resto pede o dono no painel ou na execução.
- **Prova:** `simulated`. `backend/tests/test_telegram_entrada.py` (a conversa, com o falso),
  `backend/tests/test_telegram_portas.py` (as portas reais no harness) e `backend/tests/test_avisos_porta.py` (domínio).
  `not_run`: o Telegram real e um plano real com item que pede o sim.

## Adendo v1.42 (04/10/2026; número da orquestradora; item 29.83) — exclusão de contatos do site a pedido do titular

Duas rotas novas, **atrás de sessão e só para uma pessoa nela** (ADR-075, "Exclusão a pedido do titular"). Sem
`request.state.operador` (sem o cookie de sessão do painel), **401** `sessao_exigida`, inclusive no loopback e com o
Bearer. Nenhuma entra na exceção do portão. Valem com o site e o contato ligados ou não. Migração `109_portal_exclusoes`.

- `POST /api/portal/contatos/busca`, corpo JSON `{telefone}` (é `POST` para o telefone não ir para a URL). Compara
  o número INTEIRO, nunca prefixo nem finais.
  - Aceita de 8 a 30 dígitos, contados com os zeros da frente, como o formulário conta. Na comparação, os zeros da
    frente saem.
  - No brasileiro completo (DDD + número), o `55` é opcional dos dois lados. Fora dele (sem DDD, internacional), vale
    a igualdade exata de todos os dígitos.
  - **200** `{"contatos": [{id, criado_em, estado, final}]}`, com `final` = os 4 dígitos finais do telefone guardado.
    Nome, empresa, mensagem e o número inteiro nunca saem.
  - **422** `telefone_invalido`.
  - **429** `muitas_buscas` com `Retry-After`. Há dois tetos de buscas válidas na última hora, contados em memória no
    processo (reiniciar zera):
    - `portal.limites.buscas_por_operador_hora` (30) por operador da sessão, com o nome sem espaço sobrando (os de
      dentro viram um só) e em `casefold`;
    - `portal.limites.buscas_total_hora` (60) somando todos os operadores, menos o dono (`pedidos.operadores_do_dono`,
      29.89): ele fica fora do teto somado e só tem a cota por operador.
  - O log de cada busca leva o operador e a contagem de achados, nunca o telefone.
- `POST /api/portal/contatos/excluir`, corpo JSON `{ids: [1..50 inteiros], pedido_por: "formulario" | "telefone" |
  "outro"}`. **200** `{apagados: [id], mantidos: [{id, motivo}], inexistentes: [id], mensagens_apagadas: n,
  mensagens_a_mao: [{contato_id, enviada_em}], sem_canal: bool}`. `apagados` e o registro levam só o que ESTE DELETE
  apagou: um id que outra exclusão ou a faxina levou no meio vem em `inexistentes`.
  - `motivo`: `em_envio` (a mensagem estava saindo; tentar de novo em um minuto), `falhou` (a Canais não confirmou a
    fila; nada apagado daquele contato) ou `canal_sem_exclusao` (o aviso do 28.32 está na base sem o 28.34: use o
    procedimento manual de `docs/operacao.md`).
  - `mensagens_a_mao`: as mensagens (do bot ou respostas do dono, ou a hora em que uma pode ter saído sem registro) que
    o dono apaga no chat; só ids e horas.
  - Erros: **422** `ids_invalidos` ou `pedido_por_invalido`; **415** sem JSON; **413** acima de 4096 bytes.
- O registro em `portal_exclusoes` guarda só ids, motivos, contagens, o operador e o `pedido_por`; sem prazo.
- Prova: `simulated` (`backend/tests/test_portal_exclusao.py`, com uma Canais falsa no lugar do 28.34). `not_run`: o
  28.34 real e o central.

## Adendo v1.43 (04/10/2026; número da orquestradora; item 29.82) — o worker traz as vagas que valem

Sem migração. Muda o `Worker` da v0.8 (`GET /api/workers`, `GET /api/workers/{id}`, snapshot e `worker.updated`).
- `effective_max_slots: number | null`: as vagas que valem naquela máquina, a mesma regra do agendador
  (`WorkerRegistry.capacidade`): o `max_slots` decidido no painel (`worker_limits`), ou o declarado sem decisão.
  Neste servidor, `effective_max_slots` é o `max_online_devices` vivo. A regra é uma função só
  (`WorkerRegistry.vagas_que_valem`), usada pelo agendador e pelo DTO. `max_slots` segue sendo o que o worker
  remoto declarou no `hello` (no central, o valor da subida). Ausente = backend de antes do 29.82.
- `PUT /api/servers/{worker_id}/limits` num worker remoto passa a emitir `worker.updated` com o DTO novo, para o painel
  trocar as vagas sem esperar outra mudança daquela máquina. Para ESTE servidor, `PUT /api/servers/{host}/limits` e
  `PUT /api/settings` com `max_online_devices` emitem o mesmo `worker.updated`, já com as vagas vivas.
- O painel compara a ocupação com `effective_max_slots ?? max_slots` (`frontend/src/store/metricas.ts`).
- **Prova:** `simulated` (`backend/tests/test_limites_por_servidor.py::test_api_lista_e_muda_limites_do_host_e_do_worker`,
  `frontend/src/store/metricas.test.ts`, caso 29.82). `not_run`: o topo e a Infraestrutura do central com o notebook em
  9 ligados, 9 decididas e 6 declaradas, depois do deploy.

## Adendo v1.44 (05/10/2026; número da orquestradora; item 29.81) — a foto enviada diz se foi feita por IA

Regra do dono (03/10): foto realista de IA publicada pela automação leva o rótulo de IA do Instagram. Até a v1.40 a
origem decidia sozinha (`upload` = sem rótulo), e a foto de IA que o dono subisse à mão sairia sem ele.

- **`persona_images.feita_por_ia`** (migração 108, `INTEGER` nulo): a resposta do dono para a foto ENVIADA. `1` = feita
  por IA, `0` = foto real, nulo = não informado. A migração só acrescenta a coluna: as linhas antigas ficam nulas e o
  rótulo de cada uma é o de antes (upload sem, gerada e importada com).
- **`PersonaImageDTO.feita_por_ia`** (`boolean | null`): o mesmo, na lista e no detalhe das imagens da persona.
- **`POST /personas/{id}/images?feita_por_ia=true|false`**: o parâmetro é opcional (ausente = não informado); outro
  valor responde 422.
- **`PUT /personas/{id}/images/{image_id}/feita-por-ia`** com `{"feita_por_ia": true | false | null}` (o campo é
  obrigatório; `null` volta a "não informado"): corrige a resposta. 404 sem a imagem; **409 `nao_e_upload`** na gerada ou
  importada, que saem sempre com o rótulo. A correção regrava o `rotulo_ia` e o `rotulo_ia_motivo` das etapas ABERTAS
  que publicam essa imagem; a etapa que já terminou não muda.
- **`rotulo_ia`** da etapa (v1.40): o upload sai `"true"` só com `feita_por_ia = 1`. Gerada e importada, `"true"` sempre.
- **`rotulo_ia_motivo`**, argumento novo da etapa gravado pela central junto do `rotulo_ia`: `"ia"` (gerada, importada ou
  enviada e marcada como de IA), `"foto_real"` (o dono disse) ou `"nao_informado"` (ninguém disse). **Entra na chave da
  aprovação** como argumento da etapa. Decisão: "foto real" e "não informado" saem iguais no Instagram (sem rótulo), mas
  o dono lê itens diferentes — num ele afirmou, no outro há um aviso de que ninguém afirmou. O sim dado ao aviso não pode
  valer como se ele tivesse dito "é foto real"; responder depois muda a chave, e o item volta a pedir o sim. O sim
  reaproveitado numa revisão do plano exige o mesmo porquê.
- **`ItemDaPorta.rotulo_ia_motivo`** e **`Approval.rotulo_ia_motivo`** (`"ia" | "foto_real" | "nao_informado" | null`;
  `null` sem imagem ou na etapa gravada antes do campo). O painel mostra três textos no cartão do plano, na aba Textos e
  na guia Aprovações: "com rótulo de IA"; "sem rótulo de IA (foto real, informado por você)"; e, em aviso, "sem rótulo
  de IA: ninguém informou se a foto é de IA", com o link para a guia Imagens da persona. Sem o porquê, o "sem rótulo"
  também fica em aviso. A galeria mostra em cada foto "com rótulo de IA", "sem rótulo de IA (foto real)" ou "rótulo de
  IA não informado", e a enviada se corrige na própria foto.
- **`GET /approvals`** entrega também o `rotulo_ia_motivo` (o `rotulo_ia` entrou no conserto da suíte 33, `22f641b2`:
  `Approval.to_dict` não o levava, e o selo da aba Textos e da guia Aprovações nunca aparecia).
- **Prova:** `simulated` (`backend/tests/test_upload_feito_por_ia.py`,
  `frontend/src/features/profiles/GuiaImagens.test.tsx`, `frontend/src/features/profiles/SeloRotuloIa.test.tsx`,
  `frontend/src/features/runs/PortaDoPlano.test.tsx`). `not_run`: publicação real de uma foto enviada.

## Adendo v1.45 (05/10/2026; número da orquestradora; item 31.68) — o sim do plano cobre só a prévia que o dono viu

Resíduo do #330 (31.49): o sim dado na prévia da porta valia desde o clique em "Aprovar" (`decided_at`). Uma DM igual
mandada ou aprovada ENTRE a prévia na tela e o clique não estava no motivo que o dono leu e ficava coberta pelo sim; a
repetição não muda a chave do item, então não dava `plano_mudou`.

- **`PreviaDaPorta.vista_em`** (`GET /runs/{id}/porta`) e **`vista_em`** da prévia de um item (`POST
  /runs/{id}/porta/item`): o instante ISO-8601 em que a prévia foi montada.
- **`AprovarPlanoBody.vista_em`** (`POST /runs/{id}/aprovar-plano`, `string | null`, até 40 caracteres): o `vista_em` da
  prévia que o dono VIU. Com item a aprovar, sem ele (ausente, vazio, ilegível ou no futuro) a resposta é **409
  `plano_mudou`** com a prévia nova e cada item em `mudaram` com o motivo "a prévia não diz quando foi vista: recarregue-a
  e aprove de novo"; nada é gravado (falha fechada: incerteza não conta como sim; o cliente antigo é só uma aba a
  recarregar). Sem item a aprovar (só tirar, ou N = 0) o campo não é exigido.
- **Repetição depois da prévia:** com `vista_em`, cada DM a aprovar é conferida pela mesma conta da porta
  (`mensagem_repetida` com o `desde` do 31.49) a partir desse instante, com o texto que vai (o editado, se houver). A DM
  igual mandada ou aprovada DEPOIS entra em `mudaram` com "depois da prévia que você viu: …" e a resposta é **409
  `plano_mudou`**; a que já existia quando a prévia foi vista estava no motivo lido e segue coberta pelo sim.
- **Telegram (28.27):** o retrato da prévia guarda o `vista_em` mostrado ao dono, e o "Executar (aprova N)" o devolve.
- **Painel:** o "Aprovar N e iniciar" manda o `vista_em` da prévia inteira que está na tela, não o da prévia do texto
  editado (a inteira é a mais antiga das duas: o lado mais estrito).
- **Sem limite de idade:** a prévia vista não vence pelo relógio. A aba velha só aprova se selo, chave e texto ainda
  batem e não houve repetição desde o `vista_em`, isto é, aprova o que a pessoa viu (decisão da orquestradora,
  05/10; prazo, se o dono pedir, vira regra nova).
- **Prova:** `simulated` (`backend/tests/test_porta_do_plano.py`, `backend/tests/test_telegram_entrada.py`,
  `backend/tests/test_telegram_portas.py`, `backend/tests/test_executor_honra_o_plano.py`,
  `frontend/src/features/runs/PortaDoPlano.test.tsx`). `not_run`: o gesto no central (depois do deploy 34).

## Adendo v1.46 (05/10/2026; número da orquestradora; item 31.65) — o motivo da recusa da persona fica só no detalhe da etapa

V2 da revisão do 31.63: quando a persona recusava escrever o texto de uma etapa, a dica do bloqueio (`needs` do
objetivo) era o motivo do modelo, que pode citar o pedido ou o nome de um terceiro. Essa dica viaja ao bloqueio do
objetivo, às Pendências, ao aviso no Telegram e ao Trello.

- **A dica é fixa:** "A persona se recusou a escrever este texto; o motivo dela está no detalhe da etapa. Reescreva a
  intenção e retome o item." (`social.approvals.DICA_DA_RECUSA`). O `blocked_reason` segue "a persona se recusou a
  escrever este texto".
- **`StepDTO.motivo_da_persona`** (`string | null`, aditivo; `GET /runs/{id}`): o motivo da recusa, guardado na etapa
  (`steps.draft_meta.motivo_da_recusa`, até 600 caracteres). `null` sem recusa; o texto escrito na retomada o apaga.
- **Onde o motivo aparece:** só no detalhe da execução (`GET /runs/{id}`) e, no painel, como "Motivo da persona" no
  detalhe da etapa (sessão logada do dono e dos operadores). NÃO vai ao evento `step.updated` (gravado em `events` e
  transmitido a todo navegador): sem a chave no evento, o painel mantém o valor do detalhe; com `null`, limpa. Não vai
  ao relatório da execução, à dica, às Pendências, ao aviso nem ao Trello.
- **A chave não fecha a escrita:** a marca de rascunho (`draft_meta` preenchido) ignora `motivo_da_recusa`; a retomada
  escreve de novo.
- **Prova:** `simulated` (`backend/tests/test_protecao_de_frota.py`, `frontend/src/store/reducer.test.ts`,
  `frontend/src/features/runs/CorrigirEtapa.test.tsx`). `not_run`: a recusa real no central.

## Adendo v1.47 (05/10/2026; número da orquestradora; item 29.90) — `step.updated` e `objective.updated` levam o tipo de falha

Sem migração. Aditivo no `data` do evento `step.updated` (`TaskRepository.emit_step`); o `StepDTO` não muda.
- `failure_kind: string | null`: o tipo de falha classificado da etapa (`steps.failure_kind`, ADR-054; vocabulário em
  `modules/learning/domain/falhas.py`). Nulo fora de um desfecho de falha. É o motivo estável para uma regra de aviso
  consumir a parada que pede a pessoa sem ler o `status_detail` (texto livre). Os que pedem a pessoa numa etapa em
  `waiting_user` incluem `aviso_do_app` (a folha de aviso que não fecha, ou um clicável novo por cima do botão de efeito,
  29.90), `autenticacao`, `conta_errada` e `falta_informacao`.
- `objective.updated`, na transição do objetivo para `waiting_user` pelo desfecho da etapa
  (`Scheduler`, ramo `Outcome.waiting_user`): o mesmo `failure_kind` da etapa em `data.failure_kind`, para a regra de
  aviso (28.40) escolher o texto sem cruzar com o `step.updated`. Nas outras transições, ausente.
- O painel não tipa o `data` desses eventos (`frontend/src/store/reducer.ts`, `obj<Step>(data, 'step')`): nada muda lá.
  Ausente = backend de antes do 29.90.
- **Prova:** `simulated` (`backend/tests/test_legenda_rola_e_fecha_a_folha.py::test_a_folha_que_nao_fecha_para_numa_pessoa_sem_mais_toque`).

## Adendo v1.48 (05/10/2026; número da orquestradora; item 29.96) — a sessão parada no teto aparece nas filas

Sem migração. Campo aditivo em `SessionInfo`, que vai em `PersonaDTO.session`, `InstagramProfileDTO.session`,
`PersonaDeviceDTO.session`, `PersonaOnDeviceDTO.session` e `ProfileAccountDTO.session`:
- `unknown_at_cap: boolean` (padrão `false`): a sessão está em `unknown` NO TETO do aparelho dela. O teto é o de
  `shared.vinculos.teto_de_unknown`: 1 com vínculo ativo (conta real, 29.92), senão o global
  `session_unknown_retry_cap`. A automação parou sem tocar numa tela que não reconheceu e espera uma pessoa.
- É a MESMA regra do `session.needs_person` (`SocialRepository.unknown_no_teto`): o campo liga quando o aviso entra e
  desliga quando o aviso sai.
- Não desconta o teto "velho" (gravado antes de o aparelho entrar no ar ou além da validade), ao contrário do
  `ProviderSession.unknown_capped` da prévia de recursos. Aquele responde "reler ou pedir a pessoa"; este responde
  "há um aviso aberto esperando alguém". Um reinício do emulador não tira o item da fila enquanto o aviso segue aberto.
- O `status` continua `unknown`. O painel usa o campo nas duas filas que filtravam só por estado ("Aguardando
  intervenção" de Personas e a caixa de Pendências), com o rótulo "Tela não reconhecida" em vez de "Não verificada".
- **Prova:** `simulated` (`backend/tests/test_porta_de_sessao_no_teto.py::test_parada_no_teto_aparece_no_rest_pela_regra_do_aviso`,
  `frontend/src/features/pendencias/PendenciasPage.test.tsx`, `frontend/src/features/profiles/ProfilesPage.test.tsx`).

## Adendo v1.49 (05/10/2026; número da orquestradora; item 31.71) — `MotivoDaImagem` ganha `leitura_pendente`

Sem migração: `ai_calls.image_reason` é `TEXT` sem `CHECK` (migração 080). Aditivo no vocabulário `MotivoDaImagem`
(`planning/provider.py`), que aparece em `GET /api/usage` como chave de `image_reasons`.
- `leitura_pendente`: a imagem foi porque a etapa entrega valor (`saidas`) e ainda falta saída declarada. Só existe com
  `ai.imagem_enquanto_falta_saida` ligada (desligada por padrão). Vem depois de `sensivel`, `politica_*`, `pedida`,
  `problema` e `primeira_*` na ordem da regra: a política e a tela sensível continuam mandando.
- O painel rotula o valor novo ("saída ainda não lida", `frontend/src/features/usage/usage.ts`); um valor desconhecido
  segue aparecendo pela chave crua. Ausente = backend de antes do 31.71 ou chave desligada.
- **Prova:** `simulated` (`backend/tests/test_imagem_enquanto_falta_saida.py`).

## Adendo v1.50 (05/10/2026; número da orquestradora; item 29.93) — a execução que espera você não aparece como encerrada

Antes, quando o trabalho automático acabava com um objetivo em `waiting_user` (um gesto da pessoa no aparelho: login,
aprovação), a execução ia a `completed_with_issues`, que é terminal, e grava `finished_at`: o painel, o Telegram e quem
lê o contrato a davam por encerrada, e a retomada do item a "reabria".

- **`RunStatus.awaiting_person`** (valor novo, aditivo): o trabalho automático acabou e pelo menos um objetivo está em
  `waiting_user`. NÃO é terminal. Grava `finished_at` (o fim do trabalho automático; o vencimento do 31.50 conta dali,
  como antes). Sai para `running`/`paused` (retomada do item, `finished_at` volta a nulo), `completed` (a pessoa confirma o
  último item), `completed_with_issues` (o vencimento do 31.50 cancela o objetivo parado), ou `cancelling`/`cancelled`
  (`POST /runs/{id}/cancel`, aceito como no `completed_with_issues`).
- **Só `waiting_user`:** a execução só com objetivo `uncertain` (sem ninguém esperando um gesto) segue
  `completed_with_issues`, como hoje.
- **Diferença para `needs_input`:** `needs_input` é a pergunta ANTES de agir (o plano não rodou e só sai para `cancelled`);
  `awaiting_person` é o objetivo parado depois de agir, esperando um gesto no aparelho. Nenhum consumidor do
  `needs_input` (a expiração do 29.50/31.43, o lembrete da pergunta, a caixa de Pendências) pega o estado novo.
- **`GET /snapshot`:** traz a execução `awaiting_person` enquanto `finished_at` tiver até 7 dias; mais velha, só entre as
  20 mais recentes (com o vencimento desligado nada a fecharia). As outras não terminais seguem sem corte.
- **Purga de eventos por idade** (`log_retention_days`): a execução `awaiting_person` é poupada como aberta, embora
  tenha `finished_at`.
- **`run.updated`:** passa a dizer `awaiting_person` onde dizia `completed_with_issues`. O `objective.updated` não muda (o
  objetivo continua `waiting_user`).
- **Cliente com `switch` exaustivo sobre `RunStatus`** precisa do caso novo (o painel é o único hoje: rótulo "Aguardando
  você", grupo "Pede atenção", e os botões de cancelar e o aviso "precisam de você" voltam a valer nela).
- **Telegram:** o desfecho diz "parou"; a contagem "esperando você" e o gesto seguem os do 28.40.
- **Pedidos:** a ocorrência fecha como fechava com o `completed_with_issues` (`incerta` ou `falhou`); o "esperando você"
  na ocorrência, se vier, é do 28.40.
- **Migração de dados** (`111_execucao_aguardando_pessoa`): leva a `awaiting_person` as execuções já paradas em
  `completed_with_issues` com objetivo `waiting_user`. Idempotente; 0 linhas no banco do central em 05/10.
- **Coluna interna `runs.assentada_em`** (`113_execucao_assentada_em`, itens 29.93 e 29.103): a marca de que a
  execução já foi assentada (digest, trava, pedidos), gravada por compare-and-set. Não entra em nenhum DTO nem evento;
  nada muda para o cliente. Efeito visível só no servidor: a execução cancelada sem worker vivo (29.103) passa a assentar,
  e nenhuma assenta em dobro.
- **Prova:** `simulated` (`backend/tests/test_aguardando_pessoa.py`, `backend/tests/test_learning_prova.py`,
  `frontend/src/lib/status.test.ts`, `frontend/src/features/pendencias/aguardandoPessoa.test.ts`). `not_run`: o central.

## Adendo v1.51 (05/10/2026; número da orquestradora; item 29.100) — a hora em que o estado da sessão começou

Migração 112. Campo aditivo em `SessionInfo`, nos mesmos cinco DTOs do adendo v1.48:
- `status_since: string | null`: ISO-8601 UTC de desde quando a sessão está assim.
  - É a hora da mudança de estado (ou da linha nova).
  - No `unknown`, é também a hora em que a série de reobservações CHEGOU ao teto do aparelho (`unknown_at_cap`,
    v1.48): a parada.
  - Por isso a sessão parada mostra a hora da parada, mesmo quando o `unknown` começou dias antes por um gesto
    administrativo (vínculo, wipe, logout).
- Fora disso, regravar o mesmo estado não move a hora: a reobservação abaixo do teto, o "Verificar conta" no teto e a
  invalidação de quem já estava `unknown`.
- `verified_at` continua sendo a última verificação.
- A decisão é tomada dentro do próprio upsert, contra a linha que o comando encontra, então duas gravações concorrentes
  não deixam a hora velha.
- Caso raro: a sessão pode entrar no teto SEM gravação, porque `unknown_at_cap` é lido contra o teto de agora.
  - Acontece quando o teto cai até uma série que ainda não estava parada (de 3 para 2, com a série em 2), ou quando
    outra conta no aparelho ganha vínculo ativo (o teto vira 1).
  - Nesse caso, Pendências mostra a hora do começo do `unknown` (ou do gesto), e a gravação seguinte no teto não a
    corrige.
  - Só afeta a hora mostrada.
- A invalidação e o "Verificar conta" sem `reobserved` tiram a sessão do teto, porque a série volta a zero, e ela some
  de Pendências. É assim desde antes do 29.100.
- Nulo quando a sessão não existe, ou quando era `session_ready` antes da migração 112 e não mudou de estado desde
  então. A migração preenche as outras com a última gravação. Em `session_ready` gravada depois da 112, é a hora em que
  ficou pronta.
- O painel usa `status_since` como o "desde" do item da sessão em Pendências e cai em `verified_at` quando ele falta.
  Ausente = backend de antes do 29.100.
- **Prova:** `simulated` (`backend/tests/test_sessao_status_since.py`, `frontend/src/features/pendencias/PendenciasPage.test.tsx`).

## Adendo v1.52 (05/10/2026; número da orquestradora; item 29.105) — as teclas de navegação não dependem do quadro, e o quadro velho com a captura falhando diz o porquê

`POST /api/instances/{id}/input` (`ManualInput`), sem campo novo:
- `type:'key'` com `key` em `back`, `home` ou `recents` (Voltar, Início, Recentes) não confere mais o `frame_id`: nem
  se o backend o conhece, nem a idade, nem o tamanho. A tecla de navegação não aponta para nada na tela e SAI dela. O
  lease (`not_controller`) e o aparelho no ar (`offline`) continuam valendo. O `frame_id` segue obrigatório no corpo;
  o painel manda o da imagem exibida, ou `''` quando nenhuma imagem chegou.
- `enter` e `delete` agem sobre o campo em foco (às cegas, o Enter confirmaria o que a pessoa não vê) e seguem, como
  toque, toque longo, arraste e texto, exigindo um quadro atual. Quando o quadro é desconhecido ou velho:
  - com a captura falhando (`consecutive_capture_failures > 0` no `stream` da instância): `409 {code:'capture_failing'}`,
    com a mensagem do motivo (`last_capture_error`) e da saída (Voltar, Início e Recentes passam). Esperar a imagem nova
    não resolve;
  - sem falha registrada: `409 {code:'stale_frame'}`, como antes (a corrida comum entre o painel e a tela).
  - Aceito: uma falha passageira da captura junto da corrida comum responde `capture_failing` em vez de `stale_frame`.
- `frame_mismatch` não mudou.
- O caso que motivou: na medida do 31.72 (android-09, 05/10), com a aba anônima do Chrome na frente, o quadro congelou e
  todo `/input` voltou `stale_frame`, até o Voltar: o painel ficou sem saída.
- O mesmo sintoma apareceu SEM tela protegida (android-09, 05/10, 10:28Z, deploy 36 `e5f1b22b`): o convidado
  sobrecarregado (load 18 a 20) fez o screencap estourar o prazo (`DriverTimeout: screencap (na origem) excedeu 25s`), o
  `x-frame-id` parou por cerca de 30 s e três toques voltaram `stale_frame`. Com este código, seriam `capture_failing`,
  e a tecla de navegação passaria. O congelamento do 31.72 pode ter sido sobrecarga, não a FLAG_SECURE.
- O vizinho que nenhum código pega; por isso a regra é ler o quadro antes de tocar: o toque com quadro novo que não
  foi lido, depois de a tela mudar (K-102 em `docs/conhecimento/aprendizados.md`).
- **O que o screencap faz com a FLAG_SECURE ainda é INFERRED** (falha, ou sai uma imagem preta):
  - se falha: o `stream` registra a falha, toque, texto, Enter e Apagar recebem `capture_failing`, e a tecla de
    navegação é a saída;
  - se sai preto: o quadro se renova (preto), o `stale_frame` não dispara, e o toque passa às cegas sobre uma imagem
    preta. O 29.105 não detecta isso, nem o marcador de tela sensível pela hierarquia; a tecla de navegação segue
    sendo a saída.
- O painel trata `capture_failing` com aviso próprio (o motivo do backend e "use Voltar, Início ou Recentes") e manda
  essas três teclas mesmo sem imagem exibida; Enter e Apagar sem imagem ficam no painel, com "Ainda não há imagem na
  tela". Ausente = backend de antes do 29.105 (a tecla recebia `stale_frame` com o quadro velho).
- **Prova:** `simulated` (`backend/tests/test_tela_protegida.py`, `frontend/src/app.integration.test.tsx`). `real`
  parcial, ANTES deste código (deploy 36, `e5f1b22b`): no android-04, com a captura sã, o toque com quadro de ~40 s
  voltou `stale_frame` e o Voltar com quadro recente passou (`data/diag-29-105/29-105-medida.md`); no android-09, a
  sobrecarga acima (`data/diag-29-105/a09/registro.txt`). A tela protegida em si: `not_run`.

## Adendo v1.53 (05/10/2026; número da orquestradora; item 30.73) — a falta que a validação tirou do parecer da classe B

Chave aditiva no objeto `parecer` de cada revisão do curador, que é a `learning_reviews.saida` relida. Aparece em
`GET /api/aprendizado/{kind}/{ref}` (`pareceres[]`) e na resposta do pedido de revisão
(`POST /api/aprendizado/{kind}/{ref}/revisao`, `revisao`):
- `falta_descartada: string[]`, com rótulos do mesmo vocabulário fechado de `falta` (`Falta`).
  - São os que a IA marcou fora das faltas da classe do item e a validação tirou de `falta`.
  - Hoje só acontece na classe B, com `voto_da_pessoa` e `decisao_da_pessoa`: um provedor sem esquema estrito pode
    devolvê-los mesmo fora das opções.
- AUSENTE quando não houve descarte. A `saida` dos pareceres sem descarte, inclusive todo o estoque de `curador-v1`,
  fica byte a byte como antes.
- Não entra em decisão nenhuma: nem no aceite, nem na regra da autopublicação, nem no pedido de prova. Existe para não
  esconder que o modelo insistiu.
- Nunca leva texto da IA, só rótulos.
- O mesmo descarte sai numa linha de log do curador, com o id da revisão, a classe e os rótulos.
- O painel pode ignorar a chave. Ausente também quer dizer backend de antes do 30.73.
- **Prova:** `simulated` (`backend/tests/test_curador_classe_b.py`).

## Adendo v1.54 (05/10/2026; número da orquestradora; item 30.75) — a prova sem evidência diz a causa

Valores aditivos de `motivo` nos pedidos de validação (`GET /api/aprendizado/validacoes`, `itens[].motivo`, e
`contagem` por estado sem mudança):
- `orcamento_da_prova`: um teto de IA (o do pedido, `teto_usd`, ou o do dia) encerrou a execução da prova no meio: a
  última tentativa dela terminou pelo teto. O gasto (`usd`) não deixou evidência no fluxo.
- `app_sem_sessao`: o app pediu login no aparelho escolhido, e a prova parou ali. O aparelho sai dos candidatos das
  próximas provas daquele app até a próxima verificação do app nele (que confere a instalação, não a sessão: a de
  rotina, ao ligar com mais de 24 h, também solta) ou até um objetivo concluído num fluxo do mesmo app nele.
- Os dois valem só para execução de PROVA (`prova_fluxo_id`). O estoque `sem_evidencia` de execução comum (antes do
  30.37) segue `sem_evidencia`.
- Os dois saem do mesmo lugar que `sem_evidencia`: o pedido de fluxo que fecha sem evidência a favor nem contra.
  - O pedido já fechado `sem_evidencia` pode passar a um deles, uma vez, pelo passo da curadoria.
  - Nenhum dos dois é chegada do curador.
- O texto para a pessoa vem do servidor (`MOTIVO_HUMANO`), como os demais. O painel não precisa de mudança.
- **Prova:** `simulated` (`backend/tests/test_validacao_motivos_da_prova.py`).

## Adendo v1.56 (05/10/2026; número da orquestradora; item 31.84) — `clear_first` na entrada manual de texto

`POST /api/instances/{id}/input` (`ManualInput`), campo aditivo:
- `clear_first: boolean` (padrão `false`), só para `type:'text'`; nos outros tipos é ignorado. Com `true`, o campo em
  foco é limpo antes de digitar (o mesmo `type_text(clear_first=True)` da reprodução da receita; o `type_text` da sessão
  de automação engole a falha do `clear()` e acaba acrescentando, e quem pega isso é a pós-condição da etapa). Com
  `clear_first:true` e `type` diferente de `text`: `422` (validação do corpo). Com `false` ou
  ausente, o comportamento é o de antes: o texto acrescenta ao que já está no campo.
- Sem a sessão de automação do aparelho conectada (caminho do ADB `input text`, que só acrescenta), `clear_first:true`
  responde `400 {code:'bad_input'}` em vez de digitar sem limpar. A recusa da senha na loja continua antes de tudo.
- Na leitura das sessões de treino (`GET /api/training/{id}`, `inputs[]`), numa entrada de tipo `text` o campo `key_name`
  com o valor `clear_first` é a marca de que o texto foi enviado limpando o campo; vai para coluna própria numa migração
  futura.
- O que o modo treinamento grava não muda: uma entrada `text` comum. A destilação passou a tratar `delete` antes de
  `text` como ruído e `enter` logo após o `text` como `press_enter` (ver "Teclas ao ensinar" em
  `docs/dominios/perfis-e-instagram.md`).
- O painel pode ignorar o campo. Ausente também quer dizer backend de antes do 31.84.
- Item 31.85, sem campo novo: ENQUANTO HÁ GRAVAÇÃO do treinamento, o quadro informado que é o MAIS RECENTE do backend
  é aceito mesmo acima da idade máxima (cada entrada gravada lê a hierarquia antes de agir e deixa o aparelho lento; na
  medida de 05/10 as 15 teclas seguintes a um toque de 24 s voltaram `stale_frame`). Continuam em `409 stale_frame` (ou
  `capture_failing`): quadro velho quando já existe um mais novo (a pessoa clicou numa imagem antiga), a captura com falha
  registrada, quadro com mais de 60 s (captura travada) e TODO quadro velho fora da gravação. `frame_mismatch` e quadro
  desconhecido não mudaram.
  - A folga NÃO vale às cegas para o que age no campo em foco: com o quadro aceito só por ela, `type:'text'` e as teclas
    `enter`/`delete` voltam `409 stale_frame` se a hierarquia lida antes da ação faltar, for de tela sensível ou tiver
    campo de senha em foco (a pessoa vê o quadro novo e repete). Se chegou quadro novo enquanto essa hierarquia era lida,
    qualquer entrada sob a folga volta `409 stale_frame`.
  - Toque, toque longo e arraste sob a folga só passam se o quadro informado foi capturado DEPOIS da última entrada
    manual com efeito (o quadro só é o "mais recente" porque a captura ainda não rodou depois dela; o segundo toque sobre
    ele cairia na tela nova com a coordenada da velha): senão `409 stale_frame`, e a pessoa espera a imagem nova. O
    carimbo da entrada vale também quando a ação levanta (o toque que estoura o prazo segue rodando no aparelho) e numa
    recusa anterior ao despacho: custa uma recusa a mais sob a folga, o lado seguro.
- **Prova:** `simulated` (`backend/tests/test_treino_teclas_na_destilacao.py`, `backend/tests/test_treino_quadro_velho.py`).
  `real`: `not_run`.

## Adendo v1.57 (05/10/2026; número da orquestradora; item 31.83) — o `save` do treinamento recusa a proposta que nunca funcionaria

`POST /api/training/{session_id}/save` confere a proposta ANTES de gravar (fluxo, escopo, receita, status da sessão).
Novos 400, no formato de sempre (`detail: {code, message}`; a mensagem diz o que corrigir). Os códigos antigos
(`no_proposal`, `invalid_command`, `ambiguous_command`, `unknown_profile`, `unknown_group`, `capability_required`) e o 409
`duplicate_command` ficam como estavam:
- `etapa_invalida`: etapa sem `key`, com `key` repetida ou fora de `^[a-z][a-z0-9_]{1,40}$`, sem `title` e `goal`, ou com
  `postcondition.kind` inválido. Antes: 500.
- `parametro_fora_do_comando`: parâmetro em `parameters` que o `command_template` não usa.
- `parametro_nao_declarado`: `{x}` no comando sem parâmetro declarado (`instance_id`, `run_id` e `account_label` não contam).
- `comando_generico`: o comando não começa por palavra fixa (ex.: `{pedido} no instagram`), ou tem menos de 2 palavras ou de 6 letras/dígitos fixos fora das chaves. `ligue para {contato}` passa; `siga {perfil}` não.
- `parametro_invalido`: `{…}` no comando que não é nome válido (minúsculas, sem acento, números e `_`), ou `{`/`}` sem par (`abra {contato`).
- `entrada_duplicada`: entrada em duas etapas, ou em uma etapa e em `discarded`; a mensagem lista os `#seq`. Repetida só dentro de `discarded` não recusa: fica a primeira.
- `entrada_inexistente`: `seq` numa etapa ou em `discarded` que não está entre as entradas gravadas (31.95); a mensagem lista os `#seq`.
- `pos_condicao_vazia`: etapa com `side_effect` sem `postcondition.value` nem `description` (salvo ação de catálogo).
- `entradas_sem_etapa`: entrada gravada fora de `steps[].inputs` e de `discarded`; a mensagem lista os `#seq`.
- `proposta_invalida`: tipo errado: `steps`/`parameters`/`discarded` que não é lista, item de `discarded` sem `seq` inteiro, `summary`/`app_id` que não é texto.
- `etapa_invalida` cobre também `side_effect` que não é booleano (o texto "false" contaria como verdadeiro), `inputs` que não é lista de inteiros, `bindings` que não é lista de objetos e `title`/`goal`/`value`/`description` que não é texto.
- Os códigos `entradas_sem_etapa`, `entrada_duplicada`, `entrada_inexistente`, `parametro_fora_do_comando`, `parametro_nao_declarado`, `pos_condicao_vazia`, `etapa_invalida` e `proposta_invalida` terminam a mensagem com "Peça uma nova proposta à IA." (a tela ainda não edita etapas; 31.90).

Campo aditivo na resposta de sucesso: `warnings: string[]` (vazio quando não há), com as etapas sem efeito aceitas sem
descrição de pós-condição (o objetivo serviu de critério). O painel pode ignorá-lo.
- **Prova:** `simulated` (`backend/tests/test_treino_validacao_do_salvar.py`).

## Adendo v1.58 (05/10/2026; número da orquestradora; item 31.86 B) — prévia do salvar e refazer as receitas do treino

Duas rotas novas e uma regra de gravação; nada muda nas existentes além do `reason` das etapas fora do ar.
- `POST /api/training/{session_id}/preview`, corpo igual ao do `save` (`TrainingSaveBody`, `extra="forbid"`).
  - Roda a mesma conferência e a mesma destilação do `save`, e NÃO escreve nada: nem fluxo, escopo, receita, status da
    sessão nem evento de log. Mesmos erros do `save`: os 400 do adendo v1.57 e os de sempre, o 409 `closed` (já salva) e
    o 409 `duplicate_command` (comando de fluxo ou habilidade publicada), todos antes de gravar.
  - 200: `{steps: [{key, title, recipe, reason}], warnings: string[]}`. `recipe: true` quer dizer "seria gravada"
    (`reason` "receita será gravada ao salvar"); `false` traz o motivo literal da destilação (ex.: tecla que depende
    do estado de quem ensinou, texto sigiloso, alvo sem seletor estável), ou "já havia receita ativa para esta etapa",
    ou o que falta do aparelho (abaixo). Nunca leva as ações da receita nem texto digitado.
  - Com o aparelho no ar, a primeira prévia pode levar o tempo de uma leitura do aparelho (versão do app e variante);
    as seguintes usam o cache.
- `POST /api/training/{session_id}/recipes`, sem corpo: refaz a destilação de uma sessão JÁ salva e grava a receita das
  etapas que ainda não têm (origem `training:<id>`, as mesmas regras e o mesmo veto de receita ativa do `save`).
  - 200: `{session, flow_id, steps: [{key, title, recipe, reason}], created: int}`; `created` conta as receitas
    gravadas nesta chamada. Idempotente: a segunda chamada devolve `created: 0` ("já havia receita ativa").
  - Só grava em chave VIRGEM (nenhuma receita da chave, de qualquer status): onde a chave já teve receita em
    quarentena, desligada ou substituída, a etapa sai com `recipe: false` e `reason` "a chave já teve receita (status X)",
    porque o `recipes.save` do treino trocaria a quarentena por uma ativa nova e ressuscitaria o caminho que o
    aprendizado rebaixou. O `save` normal não muda de política.
  - 409 `sessao_nao_salva` (sem `flow_id`), 409 `fluxo_inexistente` (a habilidade foi apagada), 409 `fluxo_desligado`
    (a habilidade está desligada), 404 `not_found`.
- Aparelho fora do ar no `save`/prévia/reparo: a identidade da receita (versão do app, idioma e densidade) vem do que a
  última leitura deixou; se faltar, a etapa fica sem receita com o `reason` "aparelho do treinamento fora do ar e <o que
  falta> ainda não foi lido… Refaça as receitas quando ele voltar". O painel oferece "Refazer receitas" nesse caso (31.90-B): a pessoa aciona
  `/recipes` no relatório do salvar ou na lista "Salvas" da barra de treinamento; nada chama sozinho.
- **Prova:** `simulated` (`backend/tests/test_treino_previa_e_refazer_receitas.py`); `real`: `not_run`.

## Adendo v1.60 (05/10/2026; número da orquestradora; item 30.80) — a receita que não se aplicou não conta como falha dela

Mudanças aditivas; o painel não muda.
- `GET /api/recipes` (e toda leitura que devolve a linha da receita) ganha `nao_aplicavel_seguidas: integer`
  (migração 115, padrão `0`).
  - Conta as vezes seguidas em que a receita "não se aplicou": divergiu na AÇÃO 1, antes de agir, por alvo ausente
    na tela (não ambíguo), e a etapa terminou comprovada pela IA. Nesses casos nem `replay_ok` nem `replay_fail` mudam.
  - Da 3ª seguida em diante, cada uma conta como falha comum (`replay_fail`, `consecutive_fail` e a quarentena de
    sempre), sem zerar a série.
  - O ok, a falha comum, o `PUT /api/recipes/{id}` e a reativação pelo livro zeram.
- O evento `decision` desse caso diz "tela de partida diferente" e leva no `data`, além de `text`:
  `kind: "receita_nao_aplicavel"`, `recipe_id`, `step_id` e `contou_como_falha: boolean` (`true` da 3ª seguida em diante).
- A etapa fica com `driven_by: "ai"` quando não contou (nenhuma ação da receita rodou), e `"recipe+ai"` quando contou.
- A métrica `receita.reproducao{resultado}` (em `GET /api/desempenho`) ganha o valor `nao_aplicavel`, um por tentativa
  que não contou; a que contou sai como `divergiu`. Continua um veredito por tentativa.
- **Prova:** `simulated` (`backend/tests/test_receita_nao_aplicavel.py`).
## Adendo v1.61 (05/10/2026; número da orquestradora; item 30.80 B) — o ensinado que o sistema tirou de uso avisa

Dois tipos novos de evento, persistidos e sem aparelho. São aditivos: o painel não muda, e quem os traduz para o dono é a
frente Canais (28.50).
- `learning.ensinado_rebaixado` (`level: "info"`): a receita ou o fluxo ensinado no modo treinamento
  (`training:<sessão>`), que estava em uso, saiu de uso por decisão do SISTEMA (quarentena por falhas seguidas,
  substituição, obsolescência), e outra receita ativa na mesma chave (ou outro fluxo ativo no mesmo `match_key`)
  segura o lugar.
- `learning.ensinado_sem_receita` (`level: "warn"`): o mesmo, quando nada ativo ficou no lugar; a IA volta a conduzir
  a etapa.
- Cada transição publica UM dos dois. Não publicam: o gesto de uma pessoa, outra demonstração, o item que a IA
  aprendeu e o nascimento.
- `data`, lista fechada (`domain/ensinado.py::CAMPOS_DO_PAYLOAD`):
  - `kind`: `"receita"` ou `"fluxo"`;
  - `ref`: o id da receita (só dígitos) ou do fluxo (slug `[a-z0-9-]`);
  - `app`: o pacote;
  - `treino`: o id inteiro da sessão de treino (`trn-…`);
  - `sem_receita_ativa`: `boolean`, o mesmo que o tipo diz;
  - `para`: o status NATIVO de destino (`quarantined`, `superseded`, `disabled`);
  - `desde`: ISO UTC, o instante da transição gravado na trilha (`learning_transitions.decided_at`), estável numa
    reemissão.
- Nunca conteúdo da receita ou do fluxo, seletor, conta ou texto de tela. O `message` só leva tipo, id, pacote e o
  status nativo, e quem avisa o dono não o usa. No fluxo, o `message` não leva o id (hoje o slug do resumo literal, que
  vai ao `backend.log` no `warn`); ele segue só em `data.ref` (leitura do 28.50 pela Reload).
- **Prova:** `simulated` (`backend/tests/test_learning_ensinado_rebaixado.py`).

## Adendo v1.62 (05/10/2026; número da orquestradora; item 31.100) — o `save` do treinamento recusa marcador reservado no comando

`POST /api/training/{session_id}/save` ganha um 400 novo, no formato do adendo v1.57 (`detail: {code, message}`):
- `parametro_reservado`: o `command_template` usa `{instance_id}`, `{run_id}` ou `{account_label}`. No casamento do pedido
  (`FlowStore._extract`) eles valem como texto literal, e o fluxo só casaria com quem digitasse as chaves. A mensagem
  diz quais e sugere texto fixo ou um parâmetro próprio. Não termina com "Peça uma nova proposta à IA.": o comando é
  editável na tela.
- Muda uma regra do v1.57: antes o reservado no comando passava (`parametro_nao_declarado` não o contava). Declarado em
  `parameters`, fora do comando, continua aceito: o plano o usa e a materialização o resolve.
- **Prova:** `simulated` (`backend/tests/test_treino_validacao_do_salvar.py`).

## Adendo v1.63 (05/10/2026; número da orquestradora; item 31.91) — o propose do treino recebe as respostas da pessoa

Mudanças aditivas; sem corpo, o comportamento é o de antes.
- `POST /api/training/{session_id}/propose` aceita corpo OPCIONAL `{"answers": [{"question": "...", "answer": "..."}]}`.
  - `answers`: no máximo 8 itens por pedido. `question` não vazia (sem teto de tamanho: só vale se for uma pergunta da proposta guardada) e `answer` de 1 a 500 caracteres, depois de `strip`.
    Pergunta repetida no mesmo corpo, campo a mais, JSON que não é objeto ou texto fora do formato: **400
    `invalid_answers`**, sem chamar a IA e sem mudar a sessão.
  - **A pergunta tem de ser conhecida**: uma `question` do corpo só vale se estiver nas `questions` da proposta guardada
    ou já tiver sido respondida (comparação por `strip().casefold()`). Outra é 400 `invalid_answers` ("pergunta
    desconhecida: responda a uma pergunta da proposta atual"). Responder sem proposta guardada também é 400
    `invalid_answers`. A pergunta conhecida vale como está, sem teto de tamanho.
  - Pergunta OU resposta com FORMATO de segredo (a mesma checagem das memórias): **400 `resposta_sensivel`**, mensagem "Não escreva
    senha nem código aqui: a proposta não precisa disso.", antes de qualquer chamada de IA e sem mudar a sessão.
    Falar da senha sem o valor ("sim, com a senha da conta") é aceito.
  - Sem corpo, ou com `answers` vazio e sem respostas guardadas: a proposta sai como antes (sem a chave `answers`).
- **Acúmulo.** As respostas ficam guardadas dentro da proposta da sessão, na chave `answers` (lista de
  `{question, answer}`), somando as de chamadas anteriores. A mesma pergunta (comparada por `strip().casefold()`)
  troca a resposta anterior. O teto é de 16 pares acumulados: acima disso, 400 `invalid_answers`. Um `propose` sem
  corpo mantém as guardadas.
- **Ao modelo.** Todas as respostas acumuladas entram no texto enviado ao provedor, sob a frase "Respostas da pessoa às
  suas perguntas anteriores (não pergunte de novo):" (a mesma do ensino v2), uma linha `- pergunta → resposta` cada.
  Continua UMA chamada do provedor por `propose`, com o uso contabilizado como antes.
- **Na resposta.** `proposal.questions` não repete pergunta já respondida (mesma comparação) e `proposal.answers`
  traz o acumulado. `GET /api/training/{id}` devolve a proposta com `answers`. No modo simulado o comando não muda por
  causa das respostas, mas a regra das perguntas vale.
- **Salvar e prévia.** `save` e `preview` aceitam a proposta com `answers`, mas IGNORAM o `answers` que o cliente
  mandar nela: vale o da SESSÃO (o do cliente nem forja nem apaga). As respostas ficam só na sessão e não vão para
  `flows` nem `recipes`.
- **Corrida.** O `propose` só grava a proposta se a sessão não mudou desde que ele a leu (`updated_at`). Se outra
  proposta terminou antes: **409 `proposta_concorrente`**, "Outra proposta desta gravação terminou antes; peça de
  novo.", e a proposta da outra fica intacta.
- O texto da resposta não vai a log nem a evento.
- Erros de estado como antes: gravando 409 `still_recording`, salva ou descartada 409 `closed`, sem entradas 400 `empty`.
- **Prova:** `simulated` (`backend/tests/test_treino_proposta_com_respostas.py`).

## Adendo v1.64 (05/10/2026; número da orquestradora; item 31.92) — parar ou descartar uma gravação viva exige o controle do aparelho

Mudança de comportamento em duas rotas do modo treinamento; o corpo novo é aditivo e opcional.
- `POST /api/training/{session_id}/stop` e `POST /api/training/{session_id}/discard` aceitam o corpo opcional
  `{"lease_id": "<lease do controle>"}` (`extra=forbid`; sem corpo ou `{}` equivale a sem lease).
  - Gravação VIVA = a sessão está em `recording`, o aparelho a está gravando e há um controle de usuário (`control: "user"`).
    Nesse caso o `lease_id` tem de ser o lease ATUAL do aparelho, a mesma conferência do `POST /api/instances/{id}/training`.
    Sem `lease_id` ou com outro: **409** `control_required`, com a mensagem "Só quem está com o controle do aparelho
    encerra esta gravação." (`/discard`: "…descarta esta gravação."). A recusa não muda nada: a sessão segue `recording`,
    o gravador segue ativo e nenhuma entrada se perde. Se o controle passou a outra pessoa, só o lease novo para ou descarta.
  - Aparelho hospedado por OUTRA réplica (`instances.hosted_by` de outro dono): a gravação viva dele não é órfã. `/stop` e
    `/discard` respondem **409** `gravacao_em_outro_servidor` ("Esta gravação está em outro servidor; encerre por lá."), sem
    mudar nada, com ou sem lease. Sem dono carimbado, ou do próprio processo sem gravador ativo, a sessão segue órfã.
  - Alcance: o item cobre quem NÃO tem o lease. Quem clica "Assumir" com um controle de usuário vigente recebe hoje o mesmo
    lease (`request_control`) e passa pela conferência; isso fica para o item 29.143.
  - Continuam SEM lease: a gravação órfã (sem gravador ativo, ou com o aparelho em `none` ou `ai`: controle devolvido,
    expirado ou backend reiniciado), o `/discard` de sessão que não está em `recording` (gravada ou proposta) e os
    encerramentos do sistema (devolver o controle, trocar a gravação no `start`, reconciliar após o reinício).
  - Demais respostas inalteradas: 404 `not_found`, 200 com a sessão.
- **O que o painel precisa mudar:** mandar `{"lease_id": ...}` no corpo de `/stop` e `/discard` quando a pessoa está com o
  controle (o cliente `stopTraining`/`discardTraining` já faz isso quando a aba tem lease), e, ao receber 409
  `control_required`, mostrar a mensagem do erro e manter a barra de gravação (a gravação segue viva; nada foi encerrado),
  em vez de tratá-la como sessão encerrada. Quem não tem o controle vê a gravação, mas não a encerra.
- **Prova:** `simulated` (`backend/tests/test_treino_parar_exige_controle.py`); `real`: `not_run`.

## Adendo v1.55 (05/10/2026; número da coordenação; item 31.76) — `image_omitted` ganha `capture_failed`

Sem migração e sem rota nova. `image_omitted` é um campo de `Observation` (`devices/manager.py`, contrato C1 do adendo
v0.20), interno ao backend: não aparece em DTO, evento nem evidência da API (conferido por `grep` em `backend/app`,
`frontend/src` e `docs`; as únicas ocorrências são o dataclass, o `observe` e o executor).
- `capture_failed`: a aquisição da imagem FALHOU (`DriverTimeout` ou `FalhaDeLeitura`), a árvore já estava lida, o
  tamanho da tela se sabia sem a imagem e quem chamou aceitou seguir só pela árvore (`observe(tolerar_falha_da_imagem=
  True)`, desligado por padrão). Nunca é prova nem sucesso de nada.
- Só o laço do ator do executor liga o parâmetro. Verificação, evidência, prévia do painel e controle manual não: neles a
  exceção sobe como antes, e `completar_imagem` refaz a captura de uma observação `capture_failed`.
- Campos junto, também internos: `captura_falha` ("Tipo: mensagem", sem texto de tela) e `captura_excedeu_prazo`.
- A métrica `captura.total` conta `origem=observacao, resultado=falha`; a série da prévia (`capture_failures`) não é tocada.
- **Evidência da API:** o campo não vai a ela, mas a evidência da etapa muda com ele. A observação `capture_failed` tenta
  a captura tardia (como a `policy`); sem imagem, a evidência é `kind="text"`, `redacted=0`, com o motivo na nota
  ("(captura da tela falhou)", "(imagem ausente)" ou "(imagem não adquirida: <Tipo>)"). "Tela sensível" e
  `redacted=1` ficam para a tela sensível: a da observação e a da captura tardia que devolve `None` (tela que é ou
  pode ser sensível, ou geração trocada).
- **Prova:** `simulated` (`backend/tests/test_falha_so_da_imagem.py`).

## Adendo v1.65 (05/10/2026; número da orquestradora; item 30.81) — o fluxo ensinado espera a prova

Aditivo. O fluxo salvo no modo treinamento segue nascendo `active`, mas até a prova só vale para a persona que ensinou.
- **`ensinado_em_prova`** (combinado com a Portal): `{"persona": string|null, "sessao": "trn-…"}`, AUSENTE quando não
  se aplica. Vai em:
  - `POST /api/flows/match` e cada item de `GET /api/flows/cobertura`;
  - a resposta de `POST /api/training/{id}/save`;
  - o topo da resposta de `POST /api/training/{id}/recipes`, ao lado de `flow_id`.

  `persona: null` quer dizer que o treino não tinha persona, e o fluxo não casa em aparelho nenhum até a prova.
- **`warnings`** do salvar e da prévia do treino ganham uma linha quando a sessão não tinha persona.
- **Casar:** com aparelhos, o ensinado em prova só casa quando TODOS os perfis são a persona do ensino. A prévia sem
  aparelhos casa como antes.
- **A espera acaba** com uma prova real a favor (execução com `prova_fluxo_id`, depois do nascimento, sem `invalida`)
  ou com o "Confirmar que fica" explícito de uma pessoa (adotar ou outra linha da pessoa não contam).
- **Receita do treino:** enquanto o fluxo da mesma sessão espera, a receita `training:<sessão>` só é usada para a
  persona que ensinou. Em `GET /api/desempenho`, `receita.consulta{resultado}` ganha `ensino_em_prova`. Ela passa a
  valer para todos com o "Confirmar que fica" explícito no fluxo, ou quando ela mesma rodou na prova real do fluxo
  com a etapa comprovada; a prova do fluxo sozinha não a libera, e o fluxo desligado por uma pessoa também não.
- **Pedidos de validação** (`GET /api/aprendizado/validacoes`):
  - o pedido da prova do ensinado tem `review_id` `ensino:<sessão>` e `run_origem: null`;
  - `motivo` ganha `classe_c` (classe C, ou sem dossiê de agora) e `tentativas_esgotadas` (3 pedidos sem veredito);
  - com eles, e com as recusas ao nascer (`efeito_real`, `sessao_ou_autenticacao`, `credencial`, `sem_origem`,
    `sem_caminho`), o pedido `recusada` marca que o ensinado espera a pessoa.
- **`POST /api/aprendizado/fluxo/{id}/confirmar`** aceita também o fluxo ensinado que espera a pessoa (antes, 409 fora
  de "Revisar"). O ensinado que ainda está na prova automática segue com 409.
- **Livro** (as entradas de `GET /api/aprendizado` e do detalhe): o fluxo ganha `espera_a_pessoa: string|null`, que
  traz o motivo literal quando o ensinado espera a decisão de uma pessoa e o "Confirmar que fica" vale para ele, e
  `null` em todo o resto. Fica ausente nos outros tipos. Combinado com a Portal (31.91).
- **Rebaixamento:** o veredito contrário de uma prova real leva o fluxo e as receitas ativas do mesmo treino ao estado
  `disabled` do Livro, pelo sistema (status nativo `disabled` no fluxo e `quarantined` na receita). Os eventos do 30.80 B (v1.61) saem como sempre.
- **Eventos novos**, persistidos e sem aparelho:
  - `learning.ensinado_espera_decisao` (`warn`), com `data` `{kind: "fluxo", ref, app, treino, persona, desde}`;
  - `learning.ensinado_decidido` (`info`), com `data` `{kind, ref, desde, decisao: "liberado"|"desligado"|"outro",
    decidido_em}`. É um por nascimento.

  O `desde` é o nascimento do fluxo (ISO UTC), o mesmo nos dois. O `message` não leva persona nem o id do fluxo.
- **Prova:** `simulated` (`backend/tests/test_ensinado_em_prova.py`).

## Adendo v1.67 (05/10/2026; número da orquestradora; item 29.143) — o controle manual tem dono

Mudança de comportamento em `POST /api/instances/{id}/control/take`; o corpo novo é aditivo e opcional.
- O lease do controle de usuário (e o pedido pendente com a IA) passa a ter dono: o operador da sessão do painel, ou
  `panel` sem sessão (o mesmo autor do sinal `tomou_controle`, ADR-054). Não persiste, como o lease.
- A mesma pessoa (outra aba) recebe o mesmo `lease_id`, como antes.
- Outra pessoa recebe **409** `controlled_by_other`, com `dono` e `desde` (ISO, `null` no pendente) no corpo do erro,
  em vez do lease calado. Mensagens: "<dono> está no controle de <aparelho> desde <hora>; para assumir, use a tomada
  explícita." e, com a IA no controle, "<dono> já pediu o controle de <aparelho>; aguardando a IA.". A recusa não muda nada.
- Tomada explícita: corpo `{"tomar": true}` (`extra=forbid`; sem corpo ou `false` é o pedido de sempre). Devolve 200
  `{status:'granted', lease_id}` com um lease NOVO. O antigo deixa de valer na hora (`input`, `release` e o
  `training.*` respondem como sem lease). A gravação viva de quem ensinava é encerrada (`recorded`, nem salva nem
  descartada), com um log `warn` "interrompida pela tomada de <novo>". O `control.changed` traz "<novo> tomou o
  controle de <antigo>", com `tomado_por` e `tomado_de` em `data`. Com o pedido pendente de outra pessoa, a tomada também
  é recusada (409).
- Limite: sem sessão todo chamador é `panel`, e entre eles não há como distinguir; `panel` e um operador com sessão se
  recusam um ao outro.
- **O que o painel precisa mudar (Portal):** no 409 `controlled_by_other`, mostrar quem está no controle e desde
  quando, e oferecer "Tomar o controle" com confirmação, que manda `{"tomar": true}`.
- **Prova:** `simulated` (`backend/tests/test_controle_com_dono_29_143.py`); `real`: `not_run`.

## Adendo v1.66 (05/10/2026; número da orquestradora; item 30.83) — a referência pública do fluxo é aleatória

Muda o VALOR de `ref` nos eventos de fluxo e passa a aceitar a referência nova nas rotas; nenhum campo novo no payload.
- **Antes:** o id do fluxo era o slug do `plan.summary` LITERAL e saía em `data.ref`, no `href` e no `message`. Um resumo
  como "Enviar mensagem para @maria_souza" virava `enviar-mensagem-para-maria-souza…`.
- **Referência pública:** `f-` mais 12 hex ALEATÓRIOS (`secrets`), coluna `flows.ref_publico` (migração 116). Nunca é
  derivada do resumo, do comando nem do `match_key`. O fluxo novo nasce com `id = ref_publico`; o que já existe mantém o
  id interno e ganha a referência na subida.
- **`learning.needs_person`** (adendo v0.49), com `kind: "fluxo"`: `data.ref` é a referência pública, e o `href`
  (`#/aprendizado?aba=aprendido&item=fluxo:<ref>`) também. O `message` do fluxo não cita a referência: fica com o tipo e
  o app. A receita (id só de dígitos) não muda.
- **Rotas que aceitam as duas formas** (o id interno ou a referência pública) quando `kind` é `fluxo`:
  `GET /api/aprendizado/fluxo/{ref}` e os `POST` em `…/status`, `…/evidencia-invalida`, `…/confirmar`,
  `…/parecer/{review_id}`, `…/revisao` e `/api/aprendizado/fluxo/{ref}/validacao`. A resposta segue com o id interno em
  `item.ref`. Referência desconhecida: o 404 de sempre.
- **Quem consome:** a Canais deduplica por `desde` + `ref`; a partir do deploy, o `ref` de um fluxo antigo muda uma vez
  (do slug para a referência pública). Até o deploy, a Canais segue sem transmitir o `ref` de fluxo nem o `message`.
- **Fatia 3:**
  - os eventos `learning.ensinado_rebaixado`, `learning.ensinado_sem_receita`, o da espera de decisão e o da decisão
    (adendos v1.61 e v1.65), com `kind: "fluxo"`, levam em `data.ref` a referência pública;
  - `PUT` e `DELETE /api/flows/{id}`, `POST /api/flows/{id}/adopt` e `/release` aceitam as duas formas. A resposta segue
    com o id interno (`id`, `flow_id`);
  - o `desfazer.href` de um efeito do voto (`POST /api/runs/{id}/feedback`) leva a referência pública; o `ref` do
    efeito segue com o id interno.
- **Fatia 4:** o texto das exceções de fluxo (o `detail.message` dos 404, 409 e 422 do Livro, do pedido de validação e
  da loja do Livro) não cita a referência: diz só "fluxo" ("Não há fluxo com essa referência no livro.", "O fluxo
  mudou de status…"). A receita segue com o número.
- **Fica com o id interno** (o painel casa por ele; nada disso vai a evento):
  - `item.ref` nas respostas do Livro e o `ref` do efeito do voto;
  - `GET /api/flows`, a resposta do `PUT /api/flows/{id}` (`id` e `name`) e o `flow_id` de adopt e release;
  - o id da habilidade adotada de um fluxo legado (`<app>.<slug>`) e `flow:<slug>` nas relações;
  - o texto das exceções da loja de habilidades no `detail` de adopt e release (a fatia 4 tratou só as do Livro).
- **Varredura:** `backend/tests/test_ref_do_fluxo_fora_do_log.py` confere que nenhum log nem exceção do módulo do
  aprendizado cita a referência crua.
- **Para a Canais:** a chave do aviso do ensinado (o hash do `ref`) muda uma vez no deploy, como a do
  `learning.needs_person`. Um fluxo apagado sorteia uma referência nova a cada reemissão, e a chave muda junto.

## Adendo v1.68 (05/10/2026; número da orquestradora; item 30.84) — reensinar o comando que a prova desligou

Nenhum campo novo, sem migração. Muda quando `POST /api/training/{session_id}/save` e
`POST /api/training/{session_id}/preview` respondem `409 duplicate_command`.
- **Antes:** qualquer fluxo com a mesma `match_key` recusava, inclusive o ensinado que a prova real desligou (30.81,
  adendo v1.65). A pessoa não conseguia corrigir a demonstração.
- **Agora:** o fluxo ensinado cujo desligamento PELA PROVA ainda é a última linha da trilha não recusa.
  - O `save` faz a MESMA linha renascer e devolve o `flow_id` que já existia (mesma referência pública, adendo v1.66).
  - Plano, sessão e nascimento são novos; o fluxo fica ativo e de novo em espera de prova (adendo v1.65).
  - A prévia responde como o `save` responderia, sem gravar.
- **Seguem com o 409:** o fluxo desligado por uma pessoa, o que uma pessoa mexeu depois da prova, o adotado por uma
  habilidade, o ativo, e o comando com habilidade versionada publicada. As mensagens não mudam.
- **Quem consome:** o painel do treino (`TrainingReview`) mostra o `flow_id` devolvido e oferece a adoção dele; o
  mesmo id de antes não muda nada ali.
- **Prova:** `simulated` (`backend/tests/test_reensinar_o_desligado_pela_prova.py`).

## Adendo v1.70 (05/10/2026; número da orquestradora; item 31.90-D) — desfazer a última entrada da gravação viva

Rota nova e aditiva no modo treinamento. Nada muda nas rotas que existem nem no `save`.
- `POST /api/training/{session_id}/undo`, corpo `{"lease_id": "<lease do controle>", "seq": <número, opcional>}`
  (`extra=forbid`; sem `lease_id`, ou `seq` menor que 1: **422**).
  - Tira a ÚLTIMA entrada da gravação VIVA (sessão em `recording`, o aparelho a está gravando e há controle de usuário) e
    responde **200** com a sessão, igual a `GET /api/training/{session_id}`, mais `undone: {seq, type}`.
  - O aparelho não volta: a entrada sai só da gravação. A próxima entrada gravada recebe o número seguinte ao que ficou,
    ou seja, o `seq` desfeito é REAPROVEITADO: o painel verá `training.input` N, `training.input.undone` N e
    `training.input` N de novo, e tem de casar a entrada pelo que o `GET` devolve, não guardar o `seq` como identidade.
  - O status `recording` se confere de novo dentro da transação, por um UPDATE condicional na sessão: um `stop` que chegue
    no meio espera ou vence, e a gravação parada nunca perde entrada (409 `nao_esta_gravando`).
  - `seq`: o número da entrada que a pessoa viu como última. Se outra chegou antes do pedido: **409** `entrada_mudou`
    ("A última entrada agora é a N, não a M; confira antes de desfazer."), sem apagar nada.
  - Recusas, todas sem mudar nada:
    - lease ausente do controle atual, ou gravação órfã (sem gravador ativo ou sem controle de usuário): **409**
      `control_required` ("Só quem está com o controle do aparelho desfaz a última entrada.");
    - sessão que não está em `recording`: **409** `nao_esta_gravando` (a gravação parada se corrige na revisão);
    - sem entrada: **409** `sem_entrada`;
    - aparelho hospedado por outra réplica: **409** `gravacao_em_outro_servidor` ("…desfaça por lá.");
    - sessão inexistente: **404** `not_found`.
- Evento novo `training.input.undone` (persistido), `data: {training_session_id, seq, type}`. A barra de gravação do
  painel já recarrega com qualquer evento que traga `training_session_id`.
- **O que o painel precisa mudar:** um botão "Desfazer a última" na barra de gravação, visível só para quem tem o
  controle. Ele manda o `lease_id` e o `seq` da última entrada que a tela mostra. No 409 `entrada_mudou`, recarrega e mostra
  a mensagem; no `control_required`, mostra a mensagem e mantém a barra.
- **Prova:** `simulated` (`backend/tests/test_treino_desfazer_a_ultima.py`); `real`: `not_run`.

## Adendo v1.71 (05/10/2026; número da orquestradora; item 31.88 F2) — a escolha de escopo do ensinado

Campos novos e uma rota nova; sem migração (o escopo mora na `flow_scope`, que já existia).
- **`scope_on_proof`** em `POST /api/training/{session_id}/save` e `/preview` (`TrainingSaveBody`): `"todos"` (padrão) ou
  `"quem_ensinou"`. Valor fora disso: 422. Com `todos`, vale o de sempre: `profile_ids`/`group_ids` do corpo, e vazio é
  todos. Com `quem_ensinou`, o escopo gravado é a persona do treino (`training_sessions.profile_id`) e continua valendo
  depois da prova, porque o 30.81 só prende o fluxo à persona ATÉ a prova e este escopo não sai com ela.
- **Recusas novas** (antes de qualquer escrita, na prévia e no salvar, com o mesmo código): 409 `no_teacher_persona`
  (o treino não tinha persona: não há "quem ensinou"; sem isso o escopo viraria "todos" em silêncio) e 400
  `scope_ambiguous` (`quem_ensinou` junto de `profile_ids` ou `group_ids`).
- **Resposta do salvar e da prévia** ganha `scope: {on_proof, profile_ids, group_ids}`: o que foi (ou será) gravado.
- **`PUT /api/flows/{id}/scope`**, corpo `{profile_ids[], group_ids[]}` (`EscopoDoFluxoBody`, vazio nos dois = todos): amplia
  ou restringe a quem o fluxo vale, depois de salvo. Resposta `{flow_id, profile_ids, group_ids}`; 404 `not_found`,
  400 `unknown_profile` / `unknown_group`. É gesto de PESSOA (o operador do dono), não da IA. Não muda status nem passa
  pelo Livro. A trilha é o evento `log` "Escopo da habilidade mudou", com `flow_id`, `por`, `antes` e `depois`; ela NÃO
  entra em `learning_transitions`, porque toda linha de pessoa ali tira o fluxo legado da fila "Revisar".
- **Nota de tela (Portal):** na revisão do salvar, um seletor "Vale para: todos (depois de provado) / só quem ensinou /
  escolher perfis e grupos" que manda `scope_on_proof` ou as listas. Desabilitar "só quem ensinou" com a dica do
  `warnings` quando o treino não tinha persona (a API recusa com 409). Mostrar `scope` da resposta da prévia. No Livro,
  o fluxo ensinado ganha "Mudar a quem vale", que chama o `PUT`.
- **Prova:** `simulated` (`backend/tests/test_treino_escopo_ao_provar.py`); `real`: `not_run`.

## Adendo v1.72 (05/10/2026; número da orquestradora; item 31.89 F4 e F5) — "parece com o fluxo tal" e a colisão ao salvar

Uma rota nova e avisos novos; sem migração. `POST /api/flows/match` não muda.
- **`POST /api/flows/similar`**, corpo `{command}` (o mesmo `FlowMatchBody`, 1 a 4000 caracteres): `{matches, suggestions}`.
  - `matches` é verdadeiro quando algum fluxo ativo já casa o comando por inteiro; aí `suggestions` vem vazio.
  - `suggestions` tem até 3 itens `{ref, template, score}`: a referência pública (`f-…`), o molde e a nota de 0 a 1 (mínimo
    0,9). Nunca o nome do fluxo (o resumo do treino pode trazer o valor demonstrado).
  - Só pergunta. Não cria execução e não usa IA. Olha só os fluxos (`FlowStore`), não as habilidades versionadas, e não confere o `ai.flows`.
- **Colisão ao salvar:** `warnings` do `POST /api/training/{id}/save` e `/preview` ganha um texto "O comando “…” colide com
  a habilidade “…” (ref): os dois casam o mesmo texto e o novo / o que já existe passa na frente". Duas direções: (a) o
  molde novo com os exemplos é casado por um molde ativo ou candidato; (b) o molde existente com um valor-sonda é casado
  pelo novo (só cobre a forma do existente, não os exemplos dele). Não recusa e não grava; o mesmo comando segue sendo o
  409 `duplicate_command`.
- **Fora, dito de propósito:** a pergunta "usar o fluxo X?" (`needs_input`) na criação da execução e as frases
  alternativas (`flow_phrases`, migração 119 reservada e sem uso).
- **Nota de tela (Portal):** na revisão do salvar, listar os avisos de "colide"; no rascunho do comando, chamar
  `POST /api/flows/similar` quando o `match` voltar `null` e mostrar "isto parece com <molde>"; clicar não executa, só
  leva a pessoa a reescrever o comando como o molde.
- **Prova:** `simulated` (`backend/tests/test_fluxos_parecidos_e_colisao.py`); `real`: `not_run`.

## Adendo v1.69 (05/10/2026; número da orquestradora; item 31.87 F2) — o ensino usa os dados da persona

Nenhuma rota nova e nenhum campo novo; muda o CONTEÚDO de três respostas do modo treinamento quando a sessão tem
persona e a pessoa digitou, como uma entrada inteira, um dado não sigiloso dela (`profile_variables`, genérico por chave).
- `POST /api/training/{session_id}/propose`: o parâmetro do comando cujo exemplo é esse dado sai de `parameters` e de
  `command_template`, com a palavra de ligação antes dele (lista fechada: "com", "para", "de"…). Nas etapas, `{param}`
  vira o marcador `{perfil_x}` em todo campo de texto. O valor literal vira o marcador por palavra em `title`, `goal`,
  `precondition` e `postcondition.description`, e em `bindings[].value` e `postcondition.value` só quando é o campo
  inteiro (casefold): dentro de uma frase, fica literal.
- `POST /api/training/{session_id}/save` e `/preview`: a mesma troca na proposta enviada (a editada à mão também), e
  `warnings` ganha uma linha "{perfil_x}: vem do perfil da persona de cada aparelho (o aparelho sem esse dado não roda o
  fluxo)." A proposta guardada na sessão é a trocada.
- O marcador não entra em `plan.parameters` do fluxo salvo; a receita destilada digita `{perfil_x}`, e a reprodução usa o
  dado da persona do aparelho (os parâmetros do objetivo vencem).
- Não troca: valor com menos de 3 caracteres, valor só dentro de outro texto, valor não digitado, sessão sem persona.
- **O que o painel precisa mudar:** nada obrigatório. A revisão mostra `{perfil_x}` como texto e o aviso entra na lista
  de `warnings` que ela já exibe.
- **Prova:** `simulated` (`backend/tests/test_treino_dado_da_persona.py`); `real`: `not_run`.

## Adendo v1.73 (05/10/2026; número da orquestradora; item 30.85) — o Livro leva o selo do ensinado em prova

Aditivo. Achado da Portal no percurso do deploy 42: o Livro mostrava o fluxo ensinado em prova como "Publicado", sem
selo, porque a entrada não levava o campo do adendo v1.65.
- **`ensinado_em_prova`** `{"persona": string|null, "sessao": "trn-…"}` passa a ir também em cada item de fluxo de
  `GET /api/aprendizado` (a lista do Livro) e em `GET /api/aprendizado/fluxo/{ref}` (a mesma entrada). Forma e regra são as do
  v1.65: enquanto o fluxo ensinado, ativo, espera a prova. AUSENTE quando não se aplica: provado, confirmado por uma
  pessoa, desligado, ou fluxo que não veio do treino. Receita e lição não o levam.
- `state` não muda: o fluxo em prova segue `published`. O selo é o campo.
- **O que o painel precisa mudar:** o Livro mostra o `SeloEmProva` (o mesmo de `GuiaHabilidades` e da cobertura) quando o
  item tem `ensinado_em_prova`.
- **Prova:** `simulated` (`backend/tests/test_livro_selo_em_prova.py`); `real`: `not_run`.

## Adendo v1.75 (06/10/2026; número da orquestradora; item 31.111 F1, F2 e F3) — ensinar a corrigir a partir de uma etapa que falhou

Uma rota nova e um campo novo na sessão de treino; migração 119 (três colunas). Nada muda nas rotas que existem nem no
`save`: a correção é uma sessão de treino comum, só que ligada à etapa que falhou.
- **`POST /api/training/from-run`**, corpo `{run_id, step_id, lease_id, intent?, app_id?, profile_id?}` (`extra=forbid`;
  `run_id`, `step_id` e `lease_id` obrigatórios, senão **422**). O aparelho é o da etapa. Responde **201** com a sessão
  (igual a `POST /api/instances/{id}/training`) mais `origin`.
  - A etapa precisa ter `failed`, `uncertain` ou `waiting_user` (o bloqueio que espera uma pessoa): outra coisa é **409**
    `step_not_failed`; etapa que não é daquela execução,
    ou que não existe, é **404** `step_not_found`. Etapa de aparelho que já não existe: **404** `not_found`.
  - Vale para qualquer aparelho, com as MESMAS travas do treino de hoje: só quem está com o controle (`lease_id`) abre
    (**409** `control_required`), a loja não é aparelho de treino, e persona de outro aparelho é **400**
    `profile_not_on_device`. Nada é automático: a falha só oferece o botão, quem abre é a pessoa.
  - Sem `intent`, o texto é "Corrigir a etapa «<título da etapa>»"; a pessoa pode reescrever.
- **`origin`** em `GET /api/training/{id}`, em `GET /api/training` e na resposta do `POST`: `null` na gravação comum, ou
  `{run_id, step_id, step_key, attempt_id, motivo}`. `attempt_id` é a última tentativa da etapa (pode ser `null` se nunca
  rodou). `motivo` é o literal do executor lido da etapa AGORA, sem segredo reconhecível e com no máximo 400 caracteres
  (`null` se a limpeza de execuções velhas apagou a etapa; os ids ficam como rótulo). As colunas `origin_*` não saem.
- **`origin.context`** SÓ no `GET /api/training/{id}` (a lista fica leve): `{disponivel, trilha[], esperado, tentativa,
  evidencias[]}`. `disponivel: false` (e nada mais) se a etapa foi apagada.
  - `trilha`: as etapas da execução NAQUELE aparelho e versão do plano, por ordem (até 60): `{step_id, step_key, titulo,
    status, motivo, falhou}`; `motivo` só nas que falharam ou ficaram incertas; `falhou: true` na etapa de origem.
  - `esperado`: a pós-condição da etapa, `{kind, value, description}`.
  - `tentativa`: `{number, status, erro, failure_kind, failure_screen, strategy}` da tentativa de origem, ou `null`.
  - `evidencias` (até 10): `{id, kind, nota, disponivel}`; a imagem se lê em `GET /api/evidence/{id}`, que recusa a que foi
    redigida; `disponivel` já diz isso.
  - Todo texto do executor, da etapa e da evidência passa pelo mascaramento de segredo antes de sair.
- **F3, o ensinado liga à execução:** a correção segue o caminho comum (gravar, `propose`, `save`) e nasce candidata com o
  escopo do 31.88 e a prova do 30.81, sem campo novo no corpo do `save`. O que muda é só a ligação: cada item de
  `GET /api/flows` ganha `origin`, `null` ou `{session_id, run_id, step_id, attempt_id}` (o fluxo cuja sessão de treino
  veio de uma falha); e o evento `log` do salvar leva `data.origin {run_id, step_id, attempt_id}` quando há origem.
  O `session` da resposta do `save` já traz o `origin` da sessão.
- **F4 (Aprendizado):** `origin.diagnostico`, ver o adendo v1.77.
- **O que o painel precisa mudar (F5):** um botão "Ensinar a corrigir" na etapa que falhou, que pede o controle do aparelho
  e chama `POST /api/training/from-run`; o Foco abre com `origin.context` (a trilha, o esperado e as imagens).
- **Prova:** `simulated` (`backend/tests/test_treino_a_partir_da_falha.py`); `real`: `not_run`.

## Adendo v1.74 (06/10/2026; número da orquestradora; item 29.153) — custo no detalhe da execução

Mudança aditiva em `GET /api/runs/{run_id}` (`RunDetail`), sem migração. Os números v1.72 e v1.73 permanecem
reservados pela orquestradora.

- O detalhe sempre traz `costs: {"spent_usd": 0.0407, "calls": 2}`.
- `spent_usd` é o gasto estimado em US$ da execução, calculado por `planning/costs.py::spent_usd` a partir de
  `ai_calls` e dos preços de `ai.prices`, filtrado por `run_id`, sem limitar ao dia atual. Mantém as regras do cálculo
  existente: cache, modelo que realmente respondeu, custo declarado e chamadas simuladas sem cobrança.
- `calls` é o número de linhas de `ai_calls` dessa execução, inclusive as simuladas.
- Sem chamadas de IA: `costs: {"spent_usd": 0.0, "calls": 0}`; o campo nunca é omitido nem nulo.
- **`GET /api/runs` não muda:** os itens da lista continuam sem `costs`. Execução inexistente no detalhe continua
  respondendo 404 `not_found`.
- **Prova:** `simulated` (`backend/tests/test_run_detalhe_custo.py`); `real`: `not_run`.

## Adendo v1.76 (06/10/2026; número da orquestradora; item 31.114 F1 e F2) — o arraste gravado: o gesto do dedo para a IA e, confirmado, a receita

Sem rota nova, sem migração e sem corpo novo. Muda o que o `propose` lê, o que ele guarda e uma pergunta; o `save` e o `preview`
seguem como eram.
- **O texto do arraste que a IA lê** (`planning/training.py::descrever_arraste`): o gesto do DEDO, de onde saiu e quanto percorreu,
  e não mais "rolou para cima/baixo" (que era o movimento do conteúdo e levava a ler o contrário). Exemplo (caso real da prova do
  31.111): "arrastou o dedo de cima para baixo, saindo da borda superior, por 60 % da altura". Sem a tela do aparelho, ou com
  coordenada que não cabe nela: "arrastou o dedo de cima para baixo (borda de origem desconhecida)". Borda = até 3 % da largura
  ou da altura. Arraste sem coordenada (31.97: teclado, padrão de bloqueio, tela sensível) segue "não gravado".
- **`screen`** na proposta guardada (`GET /api/training/{id}` → `proposal.screen`): `[largura, altura]` do aparelho em retrato, lida
  na hora do `POST /api/training/{id}/propose` e só quando a gravação tem arraste com coordenada. Ausente (a proposta tem as chaves
  de sempre) se não há arraste ou se o aparelho não respondeu a tempo (8 s). É a tela que a destilação usa depois; o `proposal`
  que o cliente manda no `save` não a carrega nem precisa: vale a da sessão.
- **A pergunta fixa do arraste final**: para cada etapa que TERMINA num arraste com coordenada, sem sair da borda e com a tela
  conhecida, a proposta ganha em `questions`: "A etapa «<chave>» termina num arraste. O arraste é o objetivo dela, para a receita
  repetir a rolagem? Responda sim ou não." O texto é fixo por chave de etapa. Responde-se pelo caminho do 31.91 (`propose` com
  `{"answers": [{"question", "answer"}]}`; a resposta fica em `answers`, e a pergunta respondida não volta). Arraste de borda ou
  sem tela conhecida não gera pergunta.
- **A receita**: só com "sim" (sem acento e sem ponto, qualquer caixa) à pergunta da etapa, e com a tela conhecida e o dedo fora da
  borda, a etapa ganha a receita de rolagem: um item `scroll` por arraste da cauda, na direção do conteúdo (dedo sobe = `down`),
  relativo à área rolável, nunca com pixel. Qualquer outra resposta, ou a falta dela, deixa o motivo de sempre: "rolagem sem
  ação-alvo depois dela" no relatório por etapa do `preview`/`save` (`steps[].reason`). Arraste no MEIO da etapa segue como dica
  "rolar até o alvo aparecer" da ação seguinte. Gesto de borda (a gaveta de notificações) segue sem receita: o F3 (tool de borda)
  está fora até haver demanda. `drag` continua em `UNSAFE_TO_REPLAY`.
- **O que o painel precisa mudar:** mostrar a pergunta como as outras `questions` (campo de resposta e envio pelo `propose`); nada
  além disso. O relatório por etapa já traz o motivo.
- **Prova:** `simulated` (`backend/tests/test_treino_descricao_do_arraste.py`, `test_treino_arraste_vira_receita.py`); `real`:
  `not_run`.

## Adendo v1.77 (06/10/2026; número da orquestradora; item 31.111 F4) — a causa provável abre o ensino da correção

Aditivo, sem migração e sem IA. Preenche o campo que o v1.75 reservou ao F4.
- **`origin.diagnostico`** SÓ em `GET /api/training/{id}` e na resposta do `POST /api/training/from-run` (a lista
  `GET /api/training` não o leva, como o `context`). É `null` quando a sessão tem `origin` sem tentativa
  (`attempt_id: null`), quando a tentativa não tem tipo de falha, ou quando o diagnóstico não pôde ser lido; a sessão
  abre igual. Fora disso, `{causa, rotulo, pergunta, fatos, proposta, amostra}`:
  - `causa`: o valor de `CausaProvavel` do 30.13 (`teto_de_ia`, `provedor_de_ia`, `sessao_ou_autenticacao`, `aparelho`,
    `plano`, `informacao_da_pessoa`, `catalogo_recusou`, `verificador`, `receita_divergiu`, `versao_nova`,
    `licao_atrapalha`, `tela_desconhecida`, `falta_conhecimento`, `indeterminada`). Nunca `null`.
  - `rotulo`: a causa em poucas palavras, para a pessoa. Nunca `null`.
  - `pergunta`: o que a pessoa mostra ou responde ao corrigir. Nunca `null`; na `indeterminada`, a genérica "O que a
    etapa devia ter feito nesta tela?".
  - `fatos`: `[{codigo, valor}]`, os fatos que sustentam a causa (os mesmos do relatório de falhas). Lista, pode ser
    vazia.
  - `proposta`: `{tipo, alvo}` (o `TipoDeProposta` do 30.13 e o alvo, ex. `grupo:<id>`, `receita:<id>`), ou `null`
    quando a causa não traz proposta.
  - `amostra`: `1` quando o contexto da tentativa foi lido, `0` quando só o tipo da falha decidiu.
- A regra é a do relatório de falhas para UMA tentativa: o tipo relido pelo texto quando não gravado, o app pela etapa ou
  pela execução, e os tipos de erro do provedor nas chamadas dela.
- **`POST /api/training/from-run` sem `intent`:** o texto sugerido passa a ser "Corrigir a etapa «<título>»: <rotulo>"
  quando a causa é conhecida. Na `indeterminada`, ou sem diagnóstico, continua "Corrigir a etapa «<título>»". **A
  intenção escrita pela pessoa vence** sempre.
- **O que o painel precisa mudar (F5):** mostrar `rotulo` e `pergunta` ao abrir o Foco do ensino que veio da falha.
- **Prova:** `simulated` (`backend/tests/test_treino_diagnostico_da_falha.py`); `real`: `not_run`.
## Adendo v1.78 (06/10/2026; número da orquestradora; item 15.15 F7) — a tabela de estados imposta: 409 `invalid_transition`

- **O que muda:** as tabelas de estado de execução, objetivo e tentativa passam a ser impostas pelo repositório (a etapa já era).
  Uma escrita que cairia fora da tabela não grava nada. Nenhum campo, rota ou corpo muda; só nasce um código de erro.
- **Quando ocorre:** o gesto chega depois de o estado ter mudado. Exemplo: o painel manda cancelar ou retomar uma execução
  que acabou de fechar pela rotina, na janela entre a leitura do estado e a escrita. A recusa pelo estado lido ANTES (cancelar
  execução que já terminou, iniciar a que não está planejada) segue sendo `invalid_state`.
- **Corpo do erro:** `409 {"detail": {"code": "invalid_transition", "message": "transição inválida de <run|objective|attempt|etapa>: <de> → <para>"}}`.
  Os estados são os valores do banco; a mensagem não leva id nem texto da pessoa.
- **Diferença para `invalid_state`:** `invalid_state` é a recusa da regra de negócio, lida antes (o estado já não servia); `invalid_transition`
  é a rede de baixo, a tabela de estados recusando uma escrita que a regra deixou passar por causa da corrida. Para quem chama
  é a mesma conduta: reler a execução e decidir de novo; não repetir às cegas.
- **Tabela:** a única aresta que a produção usou fora dela, `completed_with_issues → cancelled` (o vencimento do 31.50 fechando a
  execução com cancelamento pedido), foi declarada.
- **Prova:** `simulated` (`backend/tests/test_maquinas_de_estado.py`, `backend/tests/test_maquinas_de_estado_http.py`); `real`: `not_run`.

## Adendo v1.79 (06/10/2026; número da orquestradora; item 31.113 F3) — o pedido de aprovação guarda o marcador da persona

O formato das rotas não muda; muda o que o pedido GUARDA e o que a tela recebe. Sem migração.
- **`pending_approvals`** (`target`, `generated_content`, `approved_content`, `summary`) guarda o marcador da persona
  (`{perfil_nome}`…) no lugar do valor. Alvo e texto só levam o marcador quando a volta dá o texto EXATO (mesma caixa);
  senão, ficam literais. O mesmo vale para os `bindings` da etapa (rascunho e edição incluídos).
- **`GET /api/approvals`, `POST /api/approvals/decide` e `POST /api/approvals/{id}/decide`** devolvem esses campos
  (e `content`) com o valor da persona resolvido AO VIVO, sem gravar. `GET /api/runs/{id}/porta` (a prévia) devolve
  `texto` e `alvo` com o valor de agora, e a `chave` é calculada sobre ele.
- **O evento `approval.pending` e o canal (Telegram) levam o marcador**: a prévia que o canal mostra passa por
  `porta_do_plano.previa_para_o_canal` (também o `objeto_alvo`).
- A persona trocada depois do sim muda a `chave`, e a porta pergunta de novo na execução.
- **Prova:** `simulated` (`backend/tests/test_bindings_com_marcador_da_persona.py`); `real`: `not_run`.

## Adendo v1.80 (06/10/2026; número da orquestradora; item 31.116, parte 2) — a sugestão do ensino antes da sessão

- **`GET /api/runs/{run_id}/steps/{step_id}/ensino-sugerido`** → `{intent, pergunta, rotulo}`, pelo mesmo diagnóstico do
  v1.77 (`FontesDaTentativa.chave_da_tentativa`): só leitura, sem IA, sem gravar e sem o controle do aparelho. Responde
  `null` quando não há tentativa, e `pergunta`/`rotulo` nulos quando o diagnóstico falha. As recusas são as do
  `from-run` (404 `step_not_found`, 409 `step_not_failed`).
- O painel pré-preenche o formulário de "Ensinar a corrigir"; a intenção que a pessoa escrever vence (vai no
  `POST /api/training/from-run`). **Prova:** `simulated` (`backend/tests/test_ensino_sugerido.py`); `real`: `not_run`.

## Adendo v1.81 (06/10/2026; número da orquestradora; item 31.117) — o Livro lê a origem da execução do fluxo

Aditivo, sem migração. Achado da Portal no 31.79: `GET /api/aprendizado/fluxo/{ref}` não trazia a execução de onde nasceu um
fluxo ensinado a partir de uma falha (adendo v1.75), que `GET /api/flows[].origin` já trazia.
- **`conteudo.origem`** do fluxo ganha `session_id`, `run_id`, `step_id` e `attempt_id`: a mesma forma e a mesma regra de
  `flows[].origin` (a sessão de treino em `flows.source = "training:<id>"` que veio de uma falha). Os quatro são sempre
  presentes e `null` quando o fluxo não veio de uma falha.
- **`conteudo.origem.source_run_id`** cai para o `run_id` da falha quando a coluna `flows.source_run_id` é `null` (o treino não a
  grava); quando a coluna existe, ela manda.
- Não muda `state`, o selo `ensinado_em_prova` nem a lista `GET /api/aprendizado`.
- **O que o painel precisa mudar:** nada obrigatório; o Livro pode mostrar a execução de origem do fluxo ensinado.
- **Prova:** `simulated` (`backend/tests/test_treino_a_partir_da_falha.py`, `test_learning_conteudo.py`); `real`: `not_run` até o deploy, onde se
  lê `GET /api/aprendizado/fluxo/{ref}` de um fluxo com origem e compara com `GET /api/flows`.

## Adendo v1.82 (06/10/2026; número da orquestradora; item 31.116) — a sugestão do ensino diz a causa e pergunta pelo estado

- **`GET /api/runs/{run_id}/steps/{step_id}/ensino-sugerido`** → `{intent, pergunta, rotulo, causa}`. Campo novo:
  `causa` é o código do diagnóstico (`CausaProvavel`, por exemplo `teto_de_ia` ou `indeterminada`), ao lado do `rotulo`,
  e fica `null` quando o diagnóstico falha. O painel escolhe ícone e texto pelo código, sem depender da frase.
- **A `pergunta` passa a ser a do estado da etapa.** Em `waiting_user` a etapa parou esperando a pessoa, sem falhar, e a
  pergunta é a própria desse estado: o que ensinar, a partir daquela tela, para ela seguir. Isso vale mesmo com o
  diagnóstico em erro, caso em que a pergunta deixa de ser `null`. Em `failed` e `uncertain` segue a pergunta do
  diagnóstico, como no v1.80. `intent`, `rotulo`, o `null` sem tentativa e as recusas não mudam.
- **`GET /api/training/{id}` (e a resposta do `POST /api/training/from-run`)**: `origin.diagnostico.pergunta` segue a
  mesma regra, então em `waiting_user` diz o mesmo que o `ensino-sugerido`. Sem diagnóstico, `origin.diagnostico`
  continua `null`.
- Compatível: só um campo a mais e um texto diferente num estado. Pedido da leitura de UX da Portal, que consome os dois.
  **Prova:** `simulated` (`backend/tests/test_ensino_sugerido.py`, 7; `backend/tests/test_treino_diagnostico_da_falha.py`,
  1 novo); `real`: `not_run`.

## Adendo v1.83 (06/10/2026; número da orquestradora; item 31.122) — a pós-condição que já vale na tela de partida

- **`POST /api/training/{session_id}/save`** ganha um 400 novo, no formato do adendo v1.57 (`detail: {code, message}`):
  `pos_condicao_ja_vale`. Ele sai quando a pós-condição `text_visible` de uma etapa (literal, sem marcador) já aparece nos
  `screen_lines` da 1ª entrada da etapa, pela regra do verificador (contém, sem caixa nem espaço repetido). Nesse caso a
  etapa passaria sem agir. A `message` diz cada etapa e oferece até três textos da tela seguinte (a 1ª entrada depois
  da última da etapa) que não estavam na de partida. A recusa vem antes de qualquer escrita (fluxo, escopo, receita,
  status da sessão).
- **`POST /api/training/{session_id}/preview`**: a mesma linha entra em `warnings`. A forma da resposta não muda.
- O dado da persona nunca vai às sugestões, e o que sobrar dele no título ou no valor sai com o marcador (31.87 F2).
  A etapa sem tela gravada, o valor curto (menos de 3 caracteres) e o valor com marcador não são conferidos.
- **Prova:** `simulated` (`backend/tests/test_treino_partida_e_pos_condicao.py`); `real`: `not_run`.

## Adendo v1.84 (06/10/2026; número da orquestradora; item 31.123, migração 120) — a etapa que conclui num pacote vizinho

- **Etapa do plano** (o plano salvo do fluxo e as etapas de `GET /api/runs/{id}`; corrigido no v1.89: a lista de
  `GET /api/flows` devolve `plan` nulo): campo opcional
  `pacotes_aceitos: string[]`, OMITIDO quando vazio. São os pacotes, além do app da etapa, em que a tela comprova a
  conclusão (a busca do Configurações é de outro pacote). Quem preenche é o ensino, com os pacotes vistos na
  demonstração da etapa, sem o próprio app, o systemui, o lançador e os apps cadastrados. O executor aceita esses
  pacotes como o do app; pacote desconhecido continua não comprovando.
- **`POST /api/training/{session_id}/preview` e `/save`**: uma linha em `warnings` por etapa que passou a aceitar um
  pacote vizinho. A forma da resposta não muda.
- Compatível: as etapas e os fluxos anteriores não têm o campo, e o hash da receita e da etapa não muda.
  **Prova:** `simulated` (`backend/tests/test_etapa_pacotes_aceitos.py`); `real`: `not_run`.

## Adendo v1.85 (06/10/2026; número da orquestradora; item 31.127) — a etapa ensinada espera a anterior

- **Corpo de `POST /api/training/{session_id}/save` e `/preview`** (`proposal.steps[]`): campo opcional
  `independente: boolean`. Sem ele, ou com `false`, a etapa do fluxo ensinado nasce com `depends_on = [<key da etapa
  anterior>]` e só fica pronta depois de a anterior ser comprovada. Com `true`, a etapa fica sem a dependência (a marca
  da pessoa). A 1ª etapa, e a etapa que já traz dependência (a ação do catálogo), não mudam. Tipo errado (texto, número)
  dá 400 `etapa_invalida`, antes de qualquer escrita.
- Por quê: na reprodução r-20261006102728-1157c6 a 2ª etapa rodou com a 1ª em `retry_wait`, e o objetivo fechou
  `completed` na tela inicial; 7 de 9 fluxos ensinados não tinham `depends_on`. O executor já esperava a dependência
  (`Repository.promote`); faltava o ensino declará-la.
- A resposta não muda. O hash da etapa e da receita não leva `depends_on`, então as receitas existentes seguem valendo.
  Fluxos salvos antes seguem como estão.
- **Prova:** `simulated` (`backend/tests/test_treino_partida_f2_e_sequencia.py`); `real`: `not_run`.

## Adendo v1.86 (06/10/2026; número da orquestradora; itens 31.122 F2 e 31.128, migração 121) — a pós-condição que já vale, estruturada

- **`POST /api/training/{session_id}/save`**, recusa 400 `pos_condicao_ja_vale` (formato do v1.57, dentro de `detail`):
  além de `code` e `message`, a lista `pos_condicoes_ja_valem`, com uma entrada por etapa:

  ```json
  {"detail": {"code": "pos_condicao_ja_vale",
              "message": "Etapa “Abrir a busca”: o texto “Search settings” já aparece na tela em que ela começa, ...",
              "pos_condicoes_ja_valem": [{"etapa": "abrir_busca", "valor": "Search settings",
                                          "sugestoes": ["Back", "No results"],
                                          "message": "Etapa “Abrir a busca”: o texto “Search settings” já aparece ..."}]}}
  ```

- **`POST /api/training/{session_id}/preview`**: a mesma lista, ao lado de `warnings` (que continua `string[]`, com a
  mesma frase em `message`). Sem ocorrência, a lista vem vazia:

  ```json
  {"steps": [...], "warnings": ["Etapa “Abrir a busca”: o texto ..."], "scope": {...},
   "pos_condicoes_ja_valem": [{"etapa": "abrir_busca", "valor": "Search settings", "sugestoes": ["Back"], "message": "..."}]}
  ```

- `etapa` é a `key`; `valor`, a pós-condição que já vale (o texto, ou o seletor da `element_present`); `sugestoes`, até
  três textos da tela seguinte que não valem na de partida. O dado da persona sai com o marcador no valor, nas
  sugestões e na frase. É o que a tela usa para o alerta dentro da etapa, com um botão por sugestão (31.128).
- **31.122 F2 (migração 121):** a conferência usa a TELA INTEIRA da 1ª entrada da etapa, pela regra do verificador
  (`UiTree.contains_text` para `text_visible`; `find_selector` para `element_present`, que passa a ser conferida). Os
  elementos vêm de `training_inputs.screen_elements` (uso interno, fora do GET da sessão). A sessão gravada antes cai em
  `screen_lines` + `screen_title`. Antes, o id de interface (`search_action_bar_title`) e o corte de 8 linhas
  escondiam o texto, e a etapa "comprovava" na tela inicial (r-20261006102728-1157c6).
- **Prova:** `simulated` (`backend/tests/test_treino_partida_f2_e_sequencia.py`); `real`: `not_run`.

## Adendo v1.87 (06/10/2026; número da orquestradora; item 31.130, migração 122) — o fluxo nascido de uma prova

- **`POST /api/instances/{instance_id}/training`** (`TrainingStartBody`): campo opcional `nascido_de_prova: boolean`
  (padrão `false`). Com `true`, a sessão é de uma PROVA (de uma frente, de um item do plano), não de uso real. Tipo
  errado dá 422 (o corpo é `extra="forbid"`, como antes). As provas das frentes passam a abrir a sessão com a marca.
- **Sessão** (`GET /api/training/{id}` e `GET /api/training`): campo `nascido_de_prova: boolean` (sempre presente;
  `false` nas anteriores). `GET /api/training` aceita o filtro `?nascido_de_prova=true|false`.
- **Fluxo**: o `save` de uma sessão de prova leva a marca ao fluxo. `GET /api/flows` traz `nascido_de_prova: boolean`
  (sempre presente) e aceita `?nascido_de_prova=true|false`. O conteúdo do fluxo no Livro
  (`GET /api/aprendizado/fluxo/{ref}`, `conteudo.origem`) traz `nascido_de_prova: boolean`.
- **`PUT /api/flows/{id}`**: campo opcional `motivo` (texto de 1 a 300 caracteres), que vai à trilha do livro como o
  motivo da transição. Quem desliga um fluxo de prova escreve que é isso ("fluxo de prova do 31.130, desligado de
  propósito"). Sem `motivo`, o texto de sempre ("desligado na lista de fluxos do painel"). Motivo vazio, que não é
  texto ou longo demais: 400 `invalid`.
- Os fluxos de prova anteriores se marcam pelo id com `scripts/marcar-fluxo-de-prova.py` (ensaio por padrão;
  `--aplicar --backup`), que marca também a sessão de origem. Nenhum status, plano ou trilha muda por ele.
- **Prova:** `simulated` (`backend/tests/test_fluxo_nascido_de_prova.py`, `scripts/tests/test_marcar_fluxo_de_prova.py`);
  `real`: `not_run`.

## Adendo v1.88 (06/10/2026; número da orquestradora; item 31.135) — a origem de todo fluxo ensinado

- **`GET /api/flows[].origin`** e **`conteudo.origem`** do fluxo no Livro (`GET /api/aprendizado/fluxo/{ref}`, a mesma
  origem do v1.81): `session_id` passa a vir preenchido em TODO fluxo cuja fonte é `training:<id>` (antes, só quando
  veio de uma falha), e entram `instance_id` (o aparelho da sessão), `operator` (quem ensinou, como a sessão já
  guarda) e `ensinado_em` (ISO, `training_sessions.finished_at`). `run_id`, `step_id` e `attempt_id` seguem como no
  v1.81 (`null` quando o fluxo não veio de uma falha).
- Fora do treino, `GET /api/flows[].origin` continua `null`, e em `conteudo.origem` os campos novos vêm `null`. Sem
  migração: tudo já está na sessão de treino. Nada de nome de persona entra.
- **Prova:** `simulated` (`backend/tests/test_fluxo_nascido_de_prova.py::test_todo_fluxo_ensinado_traz_a_sessao_o_aparelho_quem_ensinou_e_quando`,
  `backend/tests/test_treino_a_partir_da_falha.py`, `backend/tests/test_learning_conteudo.py`); `real`: `not_run`.

## Adendo v1.89 (06/10/2026; número da orquestradora; item 31.140) — `pacotes_aceitos` por etapa na prévia e no Livro

- **`POST /api/training/{session_id}/preview`, `/save` e `/recipes`**: cada linha de `steps[]` ganha
  `pacotes_aceitos: string[]` (sempre presente; vazia = só o app da etapa). São os pacotes vizinhos em que a etapa conclui
  (31.123). Antes, só a linha de `warnings` dizia isso.
- **Conteúdo do fluxo no Livro** (`GET /api/aprendizado/fluxo/{ref}`, `conteudo.etapas[]`): `pacotes_aceitos: string[]`
  (sempre presente; vazia quando o plano não o tem).
- **Correção do v1.84:** o campo não sai "no `plan` de `GET /api/flows`", porque a lista devolve `plan` nulo. Ele fica
  no plano salvo e aparece nas etapas de `GET /api/runs/{id}`, e agora também na prévia e no Livro, por este adendo.
- **Prova:** `simulated` (`backend/tests/test_pacotes_aceitos_na_previa_e_no_livro.py`); `real`: `not_run`.

## Adendo v1.90 (06/10/2026; número da orquestradora; item 29.154, migração 123, ADR-079) — comando remoto nos workers

Seis rotas novas em `/api/workers/{worker_id}`. Todas exigem **sessão nomeada de operador** (cookie do `POST /api/login`):
sem ela, `401 {code: "sem_operador"}`, inclusive no loopback e com o token da API. No host público do portal, todas
respondem `404`, mesmo com credencial. O resto de `/api/workers*` não muda.

- **`GET .../comando-remoto`** → `{worker_id, central_ativo, worker_ligado, agente_anuncia, negociado, e_o_central}`.
- **`PUT .../comando-remoto`** `{ligado: bool}` (campos extras recusados) → o mesmo objeto. Grava o interruptor do worker,
  emite `worker.comando.interruptor` e força o agente a reconectar para renegociar. `404 worker_inexistente`,
  `409 central_fora`.
- **`POST .../comandos`** `{linha?, argv?, pasta?, timeout_s?, idempotency_key?}` (exatamente um entre `linha` e `argv`;
  `timeout_s` até 600; campos extras recusados) → `202 {id, worker_id, state, created_at}`, **sem eco da linha**. Erros:
  `409 comando_remoto_desligado` (interruptor do central ou do worker), `409 worker_sem_remote_exec` (o agente não negociou
  a feature nesta conexão), `409 central_fora`, `404 worker_inexistente`, `422 pedido_invalido`,
  `422 linha_com_credencial` (o texto não é guardado; fica um registro `rejected` de auditoria), `429 limite_por_minuto`,
  `429 fila_cheia`, `409 chave_em_uso`. A mesma `idempotency_key` devolve o mesmo registro.
- **`GET .../comandos?limite=50`** (1 a 200) → `{items: [...]}`, do mais novo ao mais velho, **sem** `stdout`/`stderr`.
- **`GET .../comandos/{exec_id}`** → o registro com a saída: `{id, worker_id, requested_by, modo, linha, pasta, timeout_s,
  state, exit_code, stdout, stderr, truncated, duration_ms, reason, created_at, dispatched_at, finished_at}`. `linha` é a
  redigida. `state`: `created`, `dispatched`, `running`, `succeeded`, `failed`, `timed_out`, `cancelled`, `uncertain`,
  `rejected`. `404 comando_inexistente`.
- **`POST .../comandos/{exec_id}/cancelar`** → o registro. Na fila: `cancelled` na hora; em voo: o pedido vai ao agente e o
  desfecho chega depois. `409 ja_encerrado`, `409 worker_desconectado`.

Eventos novos: `worker.comando` (pedido, recusa e desfecho; `data` com a linha redigida, até 500 caracteres, e sem a
saída), `worker.comando.interruptor` e `worker.comando.cancelamento`.

## Adendo v1.91 (06/10/2026; número da orquestradora; item 31.142) — sugestão de pós-condição pronta e prévia com o comando repetido

Achados da prova F2 (06/10, `trn-YjYU8iobj_V42xXx`, deploy 51). A única sugestão era "Back", a descrição do botão
voltar, sem texto. Trocar só o valor de um `element_present` deixaria o seletor puro "Back", que só olha o texto, e a
etapa nunca passaria. E a prévia parava no 409 `duplicate_command`, escondendo as pós-condições que já valem e os
avisos até a pessoa trocar o comando.

- **`pos_condicoes_ja_valem[]`** (prévia e `detail` do 400 `pos_condicao_ja_vale` do `save`, v1.86): cada entrada ganha
  `sugestoes_prontas: [{kind, value, texto}]`, na mesma ordem de `sugestoes`, que fica igual. É a pós-condição inteira
  que o botão da revisão aplica, `kind` e `value` juntos:
  - com a pós-condição original `text_visible`, `{kind: "text_visible", value: <texto>}`, porque o verificador lê texto
    e descrição;
  - com `element_present`, `{kind: "element_present", value: "text==<texto>"}` ou `"desc==<texto>"`, pelo campo em que o
    texto está na tela seguinte;
  - sem achar o elemento (a linha veio só das `screen_lines`), ou com `|` no texto, `text_visible`.

  `texto` é o rótulo do botão. O dado da persona vira o marcador em `value` e `texto`, como em `sugestoes`.
- **`POST /api/training/{session_id}/preview`**: o comando repetido não é mais 409. A resposta é 200, com:
  - `code: "duplicate_command"` e `message`, a mesma frase do 409 do `save`;
  - a mesma frase como 1ª linha de `warnings`;
  - `steps`, `pos_condicoes_ja_valem` e `scope` como sempre.

  Sem recusa, `code` e `message` são `null`. As outras recusas da prévia (400 da proposta, 409 `closed`) não mudam. O
  `save` segue com o 409 `duplicate_command`.
- **Prova:** `simulated` (`backend/tests/test_sugestao_pronta_e_previa_com_recusa.py`); `real`: `not_run` (o botão é da
  Portal, no 31.128, corte 53).

## Adendo v1.92 (06/10/2026; número da orquestradora; item 31.143) — `nascido_de_prova` na lista do Livro

Achado do percurso 52 da Portal: a marca do 31.130 só saía em `conteudo.origem` do detalhe do fluxo. O selo e o filtro
"Prova" da lista (31.131) ficavam sem dado.

- **`Entrada` do livro** (cada elemento de `itens[]` em `GET /api/aprendizado`, `/pendentes` e `/revisar`, e o `item` de
  `GET /api/aprendizado/{kind}/{ref}`): ganha `nascido_de_prova: bool`, sempre presente. Só o fluxo tem a marca
  (`flows.nascido_de_prova`, migração 122); os outros tipos vêm `false`.
- **`GET /api/aprendizado?nascido_de_prova=true|false`**: com `true`, só os itens com a marca; com `false`, o resto, os
  outros tipos inclusive. Sem o parâmetro, tudo. Soma com os outros filtros (`kind`, `state`, `app`, `origem`,
  `rotulo`) e entra antes da contagem: `total` e `contagem` já vêm filtrados. Valor inválido dá 422, como nos outros
  filtros.
- **Prova:** `simulated` (`backend/tests/test_livro_nascido_de_prova.py`); `real`: `not_run`.

## Adendo v1.94 (06/10/2026; número da orquestradora; item 31.154, migração 124) — a operação com N agentes

Uma **operação** é um objetivo único entregue a N **alvos**. Cada alvo é persona + conta (dela, no app da operação) +
aparelho e roda numa **execução própria**: `objectives` tem um objetivo por aparelho em cada execução, e 30 contas não cabem
em 9 aparelhos de uma vez. A fila por aparelho já serializa as execuções do mesmo aparelho. A operação agrega as execuções:
estado de cada alvo, resultado, capacidade e cancelamento. Nada troca de app: o alvo que não pode avançar PARA no estágio,
com o motivo.

- **`POST /api/operacoes`** `{command, app_id, alvos: [{profile_id, account_id?, instance_id?}], acao_final?, idempotency_key,
  max_usd, assunto?, fontes?}`, com campos extras recusados.
  - `max_usd` (obrigatório, até 100): o teto em US$ da operação inteira, somado em todas as execuções dos alvos. Atingido, a
    IA de qualquer alvo é recusada (`AIError` `budget`, motivo `operacao`), e o alvo ainda não criado nasce parado com
    `teto de custo`.
  - `assunto` (3 a 500 caracteres) e `fontes` (até 10 URLs `https://`): o que precisa ser compreendido e as fontes públicas
    que o operador indica. São a única origem da pesquisa externa da operação (frente de aprendizado).
  - `alvos`: de 1 a 64, sem `profile_id` repetido. Quem escolhe as personas pela IA é a sugestão de sempre
    (`POST /api/runs/targets/suggest`, que agora vai até `LimitsCfg.orquestracao_max_escolhidas`), e o painel passa a
    escolha aqui.
  - `account_id` ausente = a conta ativa da persona no app (a mesma regra da etapa que confere a conta).
  - `instance_id` ausente = o aparelho onde essa conta tem sessão pronta.
  - `acao_final`: `preparar` (padrão) para cada alvo em `acao_preparada`, com o texto gerado e a interface pronta, sem
    enviar. Toda execução de alvo nasce com o teto de autonomia `preparar` (28.23): o efeito para depois do rascunho,
    com o pedido de aprovação que carrega o texto. `executar` diz que a operação vai além disso, mas só pelo `liberar`
    (abaixo): nenhuma ação final sai sem o texto lido por uma pessoa.
  - Resposta `201` com o `OperacaoDetalhe` (abaixo). A mesma `idempotency_key` devolve a mesma operação.
  - Cria uma execução por alvo apto, com `runs.operacao_id`. O alvo sem conta ou sem sessão NÃO ganha execução: nasce
    parado em `conta` ou `sessao`, com o motivo.
  - Erros: `422 pedido_invalido`, `404 app_inexistente`, `409 credencial_no_comando`, `409 chave_em_uso` (a mesma chave
    com outro corpo).
- **`GET /api/operacoes?limite=50`** → `{items: [OperacaoResumo]}`, da mais nova à mais velha. `OperacaoResumo` =
  `{id, command, app_id, acao_final, status, created_at, finished_at, capacidade}`.
- **`GET /api/operacoes/{id}`** → `OperacaoDetalhe` = `OperacaoResumo` + `{max_usd, assunto, fontes, alvos: [AlvoDaOperacao],
  custo: {pesquisa_usd, alvos_usd, total_usd}}`. A pesquisa externa da operação roda dentro da execução de um alvo; `alvos_usd`
  já vem sem ela, e `total_usd` é o que o teto `max_usd` compara.
  `404 operacao_inexistente`.
- **`POST /api/operacoes/{id}/cancelar`** → `OperacaoDetalhe`. Cancela as execuções ainda abertas, pelo mesmo caminho do
  cancelamento de uma execução; o alvo já encerrado não muda. `409 ja_encerrada`.
- **`POST /api/operacoes/{id}/liberar`** `{itens: [{profile_id, texto}]}` (1 a 64) →
  `{liberados: [profile_id], recusados: [{profile_id, motivo}], operacao: OperacaoDetalhe}`.
  - Aprova, pelo serviço de aprovações de sempre, a ação preparada de cada alvo, decidindo item a item como o lote de
    aprovações (sem 409 geral).
  - `texto` é o eco do texto que a pessoa leu (31.49). Diferente do pedido de aprovação, o item é recusado com
    `texto_divergente`.
  - Só até `LimitsCfg.operacao_max_acoes_executadas` (padrão 3, lido a cada chamada), contando as ações já executadas. O
    excedente é recusado com `limite de ações executadas`; sem pedido pendente, com `sem ação preparada`.
  - A operação passa a `acao_final: executar`. Erros gerais: `404 operacao_inexistente` e `409 ja_encerrada`.
- **`GET /api/runs?operacao_id=…`**: o filtro novo na lista de execuções. Cada `RunSummary` ganha `operacao_id`
  (`null` fora de operação).

`status` da operação: `em_curso`, `concluida` (todos os alvos chegaram ao último estágio que a `acao_final` pede),
`concluida_com_bloqueios` (todos pararam, ao menos um bloqueado) ou `cancelada`.

**`AlvoDaOperacao`** = `{profile_id, persona_nome, app_id, account_id, conta (o @ da conta, ou null), instance_id, run_id,
estagio, estado, motivo, parou_em, estagios: [{estagio, em}], resultado}`. Os estágios seguem o vocabulário do dono, nesta ordem fixa:

`persona` → `conta` → `sessao` → `aparelho` → `instagram_aberto` → `target_localizado` → `post_localizado` →
`conteudo_lido` → `conhecimento_recuperado` → `resposta_gerada` → `interface_de_comentario_alcancada` → `acao_preparada` →
`acao_executada` | `acao_bloqueada` → `resultado_verificado`.

- `estagio` = o último alcançado, e `estagios` = os alcançados com prova, com a hora. Pode haver lacuna:
  `conteudo_lido` e `conhecimento_recuperado` só existem se a frente de aprendizado os marcar, e nenhum estágio é inferido
  por um posterior.
- `parou_em` = o estágio em que o alvo parou, só em `bloqueado`/`cancelado`, e `null` nos demais. Exemplos:
  - o alvo sem conta vem `estagio: "persona"`, `estagios: [persona]`, `motivo: "sem conta"`, `parou_em: "conta"`;
  - o alvo em `acao_preparada` numa operação `executar` vem `bloqueado`, com `parou_em: "acao_executada"` e motivo
    `aguarda liberação` ou `limite de ações executadas`.
- `acao_bloqueada` ocupa o lugar de `acao_executada`.
- O estágio de app (`instagram_aberto` … `interface_de_comentario_alcancada`) vem da ação do catálogo que o declara
  (`estagio_da_operacao` no `catalogo.yaml` do app). Nenhum nome de app fica no código: o mesmo modelo serve a qualquer app
  declarado, e `instagram_aberto` é o rótulo que o app do Instagram dá à sua abertura (`app_aberto` nos demais).
- `estado`: `pendente` (sem execução ainda na fila do aparelho), `em_curso`, `concluido`, `bloqueado` (com `motivo`) ou
  `cancelado`.
- `motivo` é uma frase curta e estável, a mesma que entra na contagem de `capacidade.motivos`: `sem conta`, `sem sessão`,
  `conta <status>`, `aparelho indisponível` (com a recusa da execução depois de dois-pontos), `teto de custo`,
  `aguarda liberação`, `limite de ações executadas`, ou o motivo do objetivo (uma linha, até 120 caracteres).
- `resultado` = `{texto, conhecimento_ids, evidencia_id, acao_final: {tipo, verificada, evidencia_id}}` (`null` antes de
  haver texto).
  - `texto` é o rascunho fechado do alvo, na voz da persona.
  - `conhecimento_ids` vem da frente de Aprendizado (fatos da operação usados no texto) e é lista vazia sem eles.
  - `evidencia_id` é a captura mais recente da execução fora da etapa de efeito (a tela lida até o texto).
  - `acao_final.tipo` é a chave da ação de efeito (ex.: `CREATE_COMMENT`), `verificada` é `true` só com a pós-condição
    comprovada, e `acao_final.evidencia_id` é a prova dela.

**`capacidade`** = `{solicitados, contas_existentes, sessoes_validas, contas_disponiveis, concluidas, bloqueadas,
em_curso, motivos: {<motivo>: n}}`. Conta só os alvos desta operação:
- `contas_existentes`: a persona tem conta ativa no app;
- `sessoes_validas`: e essa conta tem sessão pronta em algum aparelho;
- `contas_disponiveis`: e o aparelho está apto agora (ligado, sem quarentena, sem conta travada).

O déficit aparece aqui, não é escondido.

Eventos novos:
- `operacao.criada` com `{operacao_id, solicitados}`;
- `operacao.alvo` com `{operacao_id, profile_id, estagio, estado, motivo}`, a cada mudança de estágio ou de estado;
- `operacao.encerrada` com `{operacao_id, status, capacidade, custo}`. O `custo` (`{pesquisa_usd, alvos_usd, total_usd}`, o
  mesmo da leitura da operação) entra no 28.62, para o aviso do fim da operação; consumidor que o ignora segue valendo.

## Adendo v1.95 (06/10/2026; número da orquestradora; item 31.154, migração 127) — custo por alvo, vínculo principal e parâmetros fixos da operação

Cinco mudanças na operação do adendo v1.94, para a rodada de 07/10. Nada do v1.94 deixa de valer, exceto o `409 ja_encerrada` do liberar na operação encerrada e não cancelada (item 4).

**1. Custo por alvo.** `AlvoDaOperacao` ganha `custo_usd`: o gasto da execução do alvo (`planning.costs.spent_usd` pelo
`run_id`, com a pesquisa externa se ela rodou ali), `null` sem execução. Quando há `resultado`, ele traz o mesmo valor em
`resultado.custo_usd`. Antes do texto o `resultado` é `null`, então o painel lê o custo do alvo em `custo_usd`.

**2. Vínculo principal.** No `POST /api/operacoes`, o alvo sem `instance_id` cuja conta tem sessão pronta em MAIS de um
aparelho vai ao aparelho do vínculo **principal** da persona, não à sessão mais recente. Sem sessão no principal, o alvo
nasce parado em `sessao`, com o motivo novo `sessão fora do aparelho principal` (entra em `capacidade.motivos`). Com
`instance_id`, ou com sessão num aparelho só, nada muda.

**3. Parâmetros fixos.** `POST /api/operacoes` aceita `parametros?: {nome: valor}`, por exemplo
`{"username": "<perfil>", "caption_contains": "<trecho da legenda>"}`.
- Validação (`422 pedido_invalido`):
  - até 10 pares;
  - nome `^[a-z][a-z0-9_]{0,39}$`, fora de `instance_id`, `run_id`, `account_label`, `item` e `item_index` e sem os
    prefixos `perfil_` e `conta_` (os dados da persona), que a materialização poria por cima;
  - valor de 1 a 300 caracteres, sem `{` nem `}`;
  - dois parâmetros com o mesmo valor (sem `@`, sem espaços, sem caixa).
- Credencial nunca (ADR-040): `409 credencial_no_comando` quando o NOME diz segredo (`senha`, `codigo`, `token`, `otp`,
  `pin`…), quando o par `nome=valor` tem formato de credencial, ou quando o VALOR sozinho tem cara de senha ou de código
  (palavra única que mistura três tipos de caractere, ou de 6 a 8 dígitos). Nada é gravado.
- Os `parametros` entram na identidade do corpo: a mesma `idempotency_key` com outros parâmetros dá `409 chave_em_uso`.
- O `OperacaoDetalhe` devolve `parametros` (`null` quando ausentes).
- No plano de cada execução de alvo, o planejador não renomeia:
  - o parâmetro do plano com o MESMO valor de um fixo (comparado sem `@`, sem espaços e sem caixa) é renomeado para o
    nome fixo em `parameters` e em toda ocorrência `{antigo}` no texto das etapas; o valor fixo vale (o `@` do
    planejador sai junto). `{{saida:…}}` não muda. Nunca são renomeados o dado da persona (`perfil_email`,
    `conta_<app>_usuario`…) nem o parâmetro de nome sensível (o mesmo conjunto que a receita nunca grava);
  - as etapas cuja `capability` o app declara no bloco `operacao` do `app.yaml` ganham a chave `<capability>_<n>`
    (`open_profile_1`, `open_post_1`, `open_comments_1`), com `depends_on` e `for_each` remapeados. Se a chave nova já
    for de outra etapa, ou sair do formato de chave, nenhuma chave muda e a execução registra o motivo numa decisão.
- Vale para todo plano de execução com `operacao_id` (do planejador, de skill ou de fluxo), antes de gravar o plano. Fora
  de operação, nada muda.
- Para que serve: a receita ensinada é achada pela chave da etapa e pela pós-condição com os nomes dos parâmetros, e se
  reproduz com `objective.parameters`. Com os nomes e as chaves fixos, ela casa; com os do planejador, dava
  `RecipeDiverged` "parâmetro ausente".

**4. Liberar, cancelar e os tetos (achados da revisão automática do corte 56).**
- `POST .../liberar` aceita a operação já encerrada que não foi cancelada. Em `preparar`, com todos os alvos na ação
  preparada, a operação fecha antes de a pessoa ler os textos, e o liberar dava `409 ja_encerrada`. Só a `cancelada`
  dá 409. Quando libera algum alvo, a operação volta a `em_curso` (`finished_at: null`) e fecha de novo quando nenhum
  alvo estiver em curso (o evento `operacao.encerrada` sai outra vez). O alvo com a ação já aprovada segue o estado da
  execução dele; só o alvo sem a ação aprovada fica `bloqueado`, motivo `aguarda liberação`.
- A contagem do limite `operacao_max_acoes_executadas` e as aprovações rodam numa transação, com a linha da operação
  travada antes de contar. Duas liberações se enfileiram, também em dois processos sobre o mesmo PostgreSQL.
- `POST .../cancelar` pula a execução já terminada (`completed`, `cancelled`, `failed`) e segue quando uma termina entre
  a leitura e o pedido. Antes, o primeiro alvo terminado dava 500, e uma parte dos alvos ficava rodando.
- Teto `max_usd`: cada chamada paga em voo de um alvo da operação conta pelo custo médio das chamadas já gravadas da
  operação. Antes, N alvos em paralelo liam o mesmo gasto abaixo do teto e o estouravam juntos. A reserva fica na
  memória do processo (o deploy é um processo só). Antes da primeira resposta não há média, então o estouro possível
  fica em uma chamada por vaga de IA. A mensagem da recusa diz quantas chamadas estavam em voo.

**5. Regra da frota configurável, motivo sem @ e recusa da porta (ADR-081 e o percurso da Portal, 06/10).**
- `GET/PUT /api/settings` ganham dois limites, lidos ao vivo:
  - `frota_max_contas_por_alvo` (int, padrão 10, de 1 a 64): quantas contas diferentes da frota podem seguir, mandar
    mensagem ou comentar para o mesmo alvo dentro de `fleet_target_window_days` (int, padrão 30, de 1 a 365, que já
    existia). Antes era 1, fixo no código;
  - `frota_conta_nossa_fora_da_regra` (bool, padrão `true`): o alvo que é conta nossa viva não entra nessa contagem.
    Pessoa real sempre entra.
- O `motivo` do alvo da operação sai sem @ de conta: o @ vira "o perfil alvo". Vale para o GET, o evento
  `operacao.alvo`, a contagem por motivo e o relatório.
- A ação final recusada por uma porta antes de rodar (objetivo com `blocked_kind=policy`: regra da frota, conduta, teto)
  passa ao estágio `acao_bloqueada`, com o estado `bloqueado`, `parou_em: acao_bloqueada` e o motivo. Antes, o alvo
  ficava no último estágio de navegação com `parou_em: acao_preparada`. O pedido de aprovação que espera o liberar NÃO
  é recusa: continua em `acao_preparada`.
- A hora de `resposta_gerada` e de `acao_preparada` da etapa que espera o liberar é a do pedido de aprovação. Antes era
  a do início do objetivo.
- O plano da operação não fixa o nome que o plano JÁ usa com outro valor. A execução registra uma decisão com o
  motivo, e o nome fica o do planejador.
- O teto `max_usd` também reserva o POST do Jev (`conferir_gasto(reservar=True)`, segurado até a linha de custo). A
  média da reserva conta só as chamadas cobradas.
- A ação final aprovada POR FORA do liberar (Pendências, Telegram) vale como liberação: a operação passa a `executar`
  e reabre. Em todo fechamento, o `finished_at` é a hora do último estágio alcançado, não a da leitura. Na onda 1 de
  06/10, ele ficava em 19:44:58, antes da ação verificada às 19:48:05.
- A reabertura pela aprovação por fora vem antes da leitura dos alvos. O mesmo GET não fecha a operação de novo. Ela
  é condicional (`status<>'cancelada'`): o cancelar concorrente vence.
- A recusa de `parametros` (`credencial_no_comando`, `pedido_invalido`) diz a POSIÇÃO do parâmetro ("o 2º
  parâmetro"), nunca o nome nem o valor.
- Alvo cuja execução foi recusada no planejamento (sem objetivo): `estado=bloqueado`, `estagio=parou_em=acao_bloqueada`,
  com o motivo da recusa. Inclui o nome fixo em conflito: o plano usa um nome de `parametros` com outro valor, o
  `plan.refused` sai com `{"motivo": "parametro_em_conflito", "parametros": [<nomes>]}`, e o motivo do alvo começa por
  "parâmetro em conflito:". Os valores não entram.
- `OperacaoDetalhe.fontes_da_pesquisa: string[]`: as URLs que a pesquisa externa achou (`pedido_observacoes`,
  `tipo='url'`, sem repetição, na ordem da captura). `fontes` continua sendo só a entrada do pedido.

Migração `127_operacoes_parametros` (`operacoes.parametros`, só `ADD COLUMN`). Código: `backend/app/taskqueue/plano_da_operacao.py`,
`RunService._plano_da_operacao`, `backend/app/modules/operacoes/`. Testes: `backend/tests/test_plano_da_operacao.py`,
`backend/tests/test_operacoes.py`, `backend/tests/test_migracao_127.py`.

## Adendo v1.93 (06/10/2026; número da orquestradora; item 31.148) — a proposta já avisa a pós-condição que vale na partida

Achado das provas reais de 06/10: 2 de 2 propostas (ai_calls 4939 e 4942) puseram em "abrir a busca" um texto que já
estava na tela de partida. O 31.122 e o 31.142 só pegavam o erro depois, no `preview` e no `save`.

- **`POST /api/training/{session_id}/propose`:** o prompt da IA passa a levar, por entrada, os textos da tela inteira
  (de `screen_elements`, texto e descrição, até 20, cada um até 60 caracteres) e os que apareceram DEPOIS dela (os da
  tela da entrada seguinte que não estavam nesta, até 12). A instrução: a pós-condição de uma etapa não pode estar na
  tela da 1ª entrada dela.
  - Todo dado da persona vira o marcador (`{perfil_nome}`…), como nas `screen_lines`. Isso vale também para o texto e
    a descrição do elemento tocado (`target`), que antes iam crus.
  - Sem `screen_elements` (gravação anterior ao 31.122 F2), o prompt fica como antes.
- **A proposta (`proposal`)** ganha `pos_condicoes_ja_valem` quando, mesmo assim, a IA propôs uma que já vale. O
  formato é o do `preview` (v1.86 e v1.91, com `sugestoes` e `sugestoes_prontas`). Nada é trocado sozinho: quem decide
  é a pessoa. Sem caso, a chave não vem.
- **Prova:**
  - `simulated`: `backend/tests/test_treino_pos_condicao_na_proposta.py`;
  - `real`: `not_run` até o deploy. São 3 gravações do Configurações (~US$ 0,04), e uma sessão fica em `proposed`
    para a Portal.


## Adendo v1.96 (06/10/2026; prova30 A3, extensão do 31.157) — o aprendizado de uma operação nas 10 perguntas do dono

`GET /api/operacoes/{operacao_id}/aprendizado?persona=<profile_id>&simulados=false`: só leitura, sem IA. As 10
perguntas do aprendizado do dono, respondidas pelas execuções da operação (`runs.operacao_id`, 124).

- `200 {operacao_id, gerado_em, simulados, persona, perguntas: [{chave, titulo, itens}], contagem: {chave: n},
  nao_coberto: [{chave, motivo}]}`.
- `404 operacao_desconhecida`: a operação não tem execução nem memória, ou a 124 ainda não está no banco.
- As `chave`, na ordem: `plataforma_aprendeu`, `persona_aprendeu`, `do_app`, `do_processo`, `conhecimento_geral`,
  `fontes_externas`, `fontes_que_sustentam`, `reutilizavel`, `revisar_ou_descartar` e
  `falhas_que_geraram_aprendizado`.
- O item tem estes campos:
  - `ref`: `receita:<id>`, `fluxo:<id>`, `licao:<id>`, `tela:<id>`, `memoria:<id>`, `interacao:<id>`,
    `fato:<chave>`, `fonte:<chave>`, `registro:<chave>`, `observacao:<id>`, `queda:<item_ref>`, `sinal:<id>` ou
    `backlog:<id>`;
  - `tipo`, `escopo` (`app`, `processo`, `persona`, `operacao` ou `falha`) e `resumo` (redigido, até 200
    caracteres);
  - `origem`, `confianca` (`confirmado` ou `hipotese`) e `estado`, o valor original: estado do Livro, situação da
    observação ou confiança de 0 a 1 da memória da persona;
  - `evidencia`: run ids, ids de observação e refs;
  - `persona` (`null` = da operação inteira), `observado_em`, `frescor_ate`, `a_favor`, `contra` e `inferida`;
  - em `revisar_ou_descartar`, também `motivo`.
- O item de `fontes_que_sustentam` é `{ref, confianca, fontes: [{ref, resumo?, observacao?}]}`.
- **Régua única de confiança:**
  - o Livro conta `active`, `published` e `validated` como `confirmado`; o resto, como `hipotese`;
  - a memória da persona conta 0,7 ou mais como `confirmado`;
  - a memória da operação já guarda `confirmado` ou `hipotese`.
- **Reutilizável:** confirmado, dentro do frescor, sem evidência contra e, no Livro, com evidência a favor.
- **Revisar:** evidência contra, leitura incerta, vencido, estado do Livro em quarentena, desligado, obsoleto ou
  candidato, ou hipótese.
- **`persona`:** deixa o que é dela e o que é da operação inteira.
- **`simulados=true`:** inclui `learning_evidence` e `learning_signals` simulados.
- **O backlog de falhas** não guarda a execução: casa por app, ação e tipo de falha dos sinais, e sai com
  `inferida: true`.
- **`estado`** de um item do Livro é o de agora. A transição que uma execução da operação fez aparece à parte, como a
  falha `queda:<item_ref>`.
- **`nao_coberto`** diz o que a rota não responde:
  - a promoção a conhecimento geral pela curadoria;
  - o relatório do pedido, porque a operação não é um pedido;
  - o vínculo direto do backlog;
  - a memória da persona aprendida de observação de tela, que não guarda execução. Só a aprendida de uma interação
    se liga à operação.

**`resultado.conhecimento_ids` do alvo da operação (contrato da 124), preenchido a partir daqui.** A porta de escrita grava
em `operacao_alvos.marcas.conhecimento_ids` as refs no mesmo formato desta rota (`fato:<chave>`, `fonte:<chave>`,
`registro:<chave>`). São o que o texto do agente RECEBEU da operação: as linhas do `<fatos_da_operacao>` e a leitura da
operação (`fato:alvo.conteudo`), quando ela é igual à tela do agente. Quais delas o modelo usou de fato ele não diz.

**O assunto da operação no texto.** Com `operacoes.assunto`, o escritor recebe o bloco `<assunto_da_operacao>` logo
depois de `<intencao>`, com o pedido de relacionar o texto ao assunto só quando fizer sentido com a publicação. O
`draft_meta.fatos_da_operacao` da etapa ganha `assunto: true`. Sem assunto, nada muda.

**Revisão do PR 480 (corte 57).**
- A evidência de uma lição, voz, preferência ou tela é lida pelo id cru do item (`li-…`, como o Livro a grava). A lição
  reforçada por uma execução da operação aparece mesmo sem `provenance` que a cite.
- `a_favor` e `contra` seguem a regra de todo leitor do Livro (`promocao.efetivas`/`contrarias`): a linha neutralizada
  por `forma` ou `invalida` da mesma origem não conta, e `conflict` conta contra. A reprodução da receita conta, porque é
  o registro do uso nesta operação.
- Voz e preferência com `scope_profile_id` saem com `escopo: persona` e `persona` = a dona.
- Campo novo por item, `personas: [profile_id]` (revisão do Codex no PR 482): a lição citada por execuções de duas ou
  mais personas sai com `persona: null` e o conjunto em `personas`. No filtro `?persona=`, ela aparece só para elas;
  `persona: null` com `personas: []` continua querendo dizer "da operação inteira".
- Campo novo `avisos: [{run_id, step_id, aviso}]`: a etapa cujo `conhecimento_ids` não foi gravado. O texto sai, e o
  `draft_meta.fatos_da_operacao.conhecimento_ids` da etapa fica `nao_gravados`.

Código: `modules/pedidos/{domain,infrastructure,presentation}/aprendizado_da_operacao.py`. Prova `simulated`:
`backend/tests/test_aprendizado_da_operacao.py`.

## Adendo v1.102 (06/10/2026; número da orquestradora; item 31.180) — as amostras do host

`GET /api/host/amostras?horas=N` (só leitura, atrás do login; `N` de 1 a 168, padrão 24). Ela lê o CSV diário do
amostrador permanente do host (29.156, `scripts/amostrador-host.ps1`, um arquivo por dia UTC em
`<data_dir>/observabilidade/host/AAAAMMDD.csv`) e devolve as amostras das últimas `N` horas, da mais antiga à mais nova. O formato é o contrato do painel do Portal
(`frontend/src/features/host/contratoDoHost.ts`):

```json
{"items": [{"ts_utc": "2026-10-06T22:39:00Z", "cpu_host_pct": 41.5, "vm_convidado_nucleos": 2.25, "vmmem_ws_mb": 5120,
            "qemu_host_pct": 30.2, "ram_livre_mb": 8192, "disco_livre_gb": 120.5,
            "processos_top": [{"nome": "python", "pct": 12.3}],
            "avisos_pressao": [{"instance_id": "android-05", "n": 3}]},
           {"ts_utc": "2026-10-06T22:40:00Z", "erro": "IOException"}]}
```

- Uma medida vazia no CSV vira `null`. Um par malformado em `processos_top` ou em `avisos_pressao` fica de fora.
- A linha de falha do amostrador (`ts,erro,<tipo>`) vira `{ts_utc, erro}`, com o tipo da exceção.
- Sem CSV dos dias da janela: 404 `{"code": "sem_amostras"}`. Com o arquivo e sem linha na janela: 200 com
  `items: []`.
- A resposta não leva nome de máquina nem caminho. Os nomes de processo são só o nome, como o amostrador os grava.

Código: `backend/app/modules/fleet/infrastructure/amostras_do_host.py`,
`backend/app/modules/fleet/presentation/host.py`. Teste: `backend/tests/test_amostras_do_host.py`. A tela é do Portal.

## Adendo v1.104 (06/10/2026; número da orquestradora; item 31.173) — a sessão na operação

- `AlvoDaOperacao.sessao_verificada_em` (string ou `null`): a última verificação, na tela, da sessão da conta do alvo
  naquele aparelho (`account_sessions.verified_at`).
- O alvo `pendente` (objetivo esperando antes do aparelho) leva no `motivo` o porquê da espera, por exemplo a porta de
  sessão relendo a sessão vencida. Antes ele vinha sem motivo.
- A porta de sessão do despacho para no teto de 3 releituras seguidas da sessão vencida que falham, por conta e
  aparelho (`TETO_DE_RELEITURAS_DA_SESSAO`). O objetivo fica bloqueado com o motivo "a sessão não pôde ser relida em N
  tentativas seguidas em <aparelho>", e retomar tenta de novo. Uma releitura boa zera a contagem.

Código: `backend/app/modules/operacoes/` e `backend/app/state.py::_releitura_da_sessao`. Testes:
`backend/tests/test_operacoes.py` e `backend/tests/test_operacoes_estagios.py`.

## Adendo v1.105 (06/10/2026; número da orquestradora; item 31.174) — o pool elegível para operação

`GET /api/operacoes/elegiveis?app_id=<app>` (só leitura, atrás do login; vem antes de `/operacoes/{operacao_id}`):

```json
{"app_id": "instagram",
 "itens": [{"profile_id": "ig-…", "persona_nome": "…", "account_id": "acc-…", "instance_id": "android-01",
            "elegivel": true, "parou_em": null, "motivo": null,
            "sessao_verificada_em": "2026-10-06T19:31:59.290Z", "sessao_vencida": false}],
 "contagem": {"personas": 16, "elegiveis": 3, "com_sessao_vencida": 1, "motivos": {"sem conta": 11}}}
```

- A conferência é a mesma da criação (persona → conta → sessão → aparelho, com os motivos do v1.94) mais o aparelho
  apto (online, fora da loja, sem conta travada). O motivo do aparelho inapto é "aparelho fora do ar ou com conta
  travada".
- A sessão vencida continua elegível, porque a porta relê a tela antes da tarefa, e vem com `sessao_vencida: true`.
- Um app inexistente dá 404 `app_inexistente`, e um pedido sem `app_id` dá 422.

Código: `ServicoDeOperacoes.elegiveis` e `backend/app/modules/operacoes/presentation/router.py`. Testes:
`backend/tests/test_operacoes.py`.


## Adendo v1.97 (06/10/2026; número da orquestradora; item 31.150, K-106) — o fluxo de prova religado para uso real

Todo fluxo de prova (`nascido_de_prova`, 31.130) termina desligado. A volta era o `PUT /api/flows/{id}` genérico, com o
motivo opcional, e o Livro não distinguia "fluxo de prova em uso real" de "esquecido ligado".

- **`PUT /api/flows/{id}` com `status: "active"` num fluxo `nascido_de_prova`:**
  - `motivo` passa a ser obrigatório: sem ele, `400 motivo_obrigatorio`. O fluxo de uso real segue como antes.
  - A trilha grava `religado para uso real: <motivo>`.
  - `escopo: {profile_ids: [...], group_ids: [...]}` (opcional) troca a quem o fluxo vale no mesmo gesto e na mesma
    transação, com as recusas do `/scope` (`400 unknown_profile`, `400 unknown_group`). O evento `log` "Escopo da
    habilidade mudou" sai como no `/scope`.
  - Fora desse caso (desligar, ou ligar um fluxo de uso real), `escopo` no corpo dá `400 invalid`; o caminho é o `/scope`.
  - A marca `nascido_de_prova` nunca se apaga.
- **`POST /api/aprendizado/fluxo/{id}/status` com `to: "published"` num fluxo de prova desligado** (o "Ligar" do Livro;
  achado da Portal): a mesma regra. Motivo só com espaços dá `400 motivo_obrigatorio`, e a trilha grava "religado para
  uso real: <reason>", que acende `em_uso_real_desde`. O escopo continua sendo pelo `PUT` ou pelo `/scope`.
- **`em_uso_real_desde`** (texto ISO ou `null`): sai em cada fluxo de `GET /api/flows` e na resposta do `PUT`. Sai também
  em cada `Entrada` do Livro (`GET /api/aprendizado`, `/pendentes`, `/revisar` e o `item` do detalhe) e em
  `conteudo.origem` do detalhe do fluxo. É a data da linha "religado para uso real" enquanto ela for a última da trilha
  e o fluxo estiver ligado. Desligar de novo, pela pessoa ou pelo sistema, volta a `null`.
- **Prova:** `simulated`
  (`backend/tests/test_fluxo_nascido_de_prova.py::test_religar_fluxo_de_prova_exige_motivo_troca_o_escopo_e_sela_o_uso_real`,
  que reprova no código anterior). `real`: `not_run` (religar um fluxo de prova para uma persona real só com o sim do
  dono).

## Adendo v1.98 (06/10/2026; número da orquestradora; item 31.149, P-014 b) — a correção ensinada volta ao comando que falhou

- **`POST /api/training/{id}/save` de uma sessão de correção** (`origin`, 31.111) ganha `correcao`. Além das receitas de
  sempre, a demonstração é gravada na chave da etapa que FALHOU (`steps.template_hash` e a chave dela). Assim, a próxima
  execução do mesmo comando a acha nessa etapa.
  - Ligada: `{ligada: true, step_key, recipe_id, reason, comando, texto}`. `texto` = "esta correção vale para o comando
    “<molde>”", e o molde é o comando da execução que falhou com os valores trocados pelos nomes (parâmetros e dados
    da persona).
  - Não ligada: `{ligada: false, step_key?, motivo}`. Os motivos:
    - a etapa que falhou foi apagada, ou não tem identidade de receita;
    - há efeito (na etapa que falhou ou numa ensinada);
    - uma etapa ensinada não virou ação;
    - a correção é de outro app;
    - a receita usaria `{nome}` que a execução não tem (só o nome vai na resposta);
    - a versão do app é desconhecida no aparelho.
  - Sessão sem `origin`: sem `correcao`.
- **Quais etapas ensinadas são a correção:** a de mesma chave da que falhou; senão, todas, em sequência.
- **Parâmetros:** os da proposta são renomeados para os do objetivo que falhou pelo valor (sem o @ e sem caixa).
- **Receita:** segue o 30.81 (vale para quem ensinou até o "Confirmar que fica") e a regra de uma viva por chave (30.79).
- **Evento `log` do salvar:** ganha `correcao_ligada` e `recipe_id`.
- **Não feito aqui:** a "lição do planejador" como caminho alternativo quando a receita não liga; veio no adendo v1.100.
- **Prova:** `simulated` (`backend/tests/test_correcao_volta_ao_comando.py`, 2 testes, que reprovam no código anterior).
  `real`: `not_run`. As 2 sessões reais salvas não teriam receita: uma sem app, outra em Configurações, sem versão
  conhecida no catálogo.

## Adendo v1.99 (06/10/2026; número da orquestradora; item 31.169) — a pesquisa externa na criação da operação

- **`POST /api/operacoes`** agenda a pesquisa externa da operação (31.158). Ela roda UMA vez, logo depois da criação e
  antes de qualquer alvo: a tarefa pega a trava da operação, a mesma da porta de escrita. A resposta da rota não muda e
  não espera a pesquisa.
- **Os alvos não pesquisam mais:** a porta de escrita só reusa a memória da operação. Sem a pesquisa da criação (por
  exemplo, desligada naquela hora), o texto sai sem os fatos da pesquisa.
- **Custo:**
  - paga na execução do 1º alvo com execução (`operacao_alvos` por `seq`), pelo caminho de IA dela (tetos e vaga);
  - aparece em `custo.pesquisa_usd` da operação;
  - o teto por operação (`ai.pesquisa.teto_usd_por_operacao`) segue valendo;
  - sem alvo com execução, não pesquisa.
- **Sem lacuna** (o assunto já tem fato de pesquisa válido), ou na repetição idempotente: não pesquisa.
- **Consulta:** leva só o assunto e as fontes indicadas. A leitura do alvo, que antes ia de contexto, ainda não existe
  na criação.
- **Prova:** `simulated`, em `backend/tests/test_pesquisa_da_operacao.py`:
  - `::test_criar_a_operacao_pela_rota_pesquisa_uma_vez_antes_dos_alvos`;
  - `::test_os_alvos_nao_pesquisam_so_reusam`, que reprova no código anterior;
  - `::test_duas_execucoes_uma_pesquisa_e_os_fatos_chegam_ao_texto`.

  `real`: a operação de 07/10.

## Adendo v1.100 (06/10/2026; número da orquestradora; item 31.149, caminho alternativo) — a correção não ligada ensina o planejador

- **`POST /api/training/{id}/save` de uma sessão de correção:** quando `correcao.ligada` é `false` (adendo v1.98),
  `correcao` ganha `licao`.
  - Lição proposta: `{id, estado, texto}`, com `estado` = `candidate`. `texto` = "Em <pacote>: quando a etapa <chave>
    falhar, o caminho que uma pessoa ensinou foi <chave> → <chave>."
  - Lição não proposta: `{id: null, motivo}`. Os motivos:
    - a etapa que falhou foi apagada;
    - a lição foi recusada, com o motivo do vocabulário das lições (`simulada`, `app_invalido`, `acao_invalida`,
      `acao_de_sessao`, `valor_de_parametro`, `longa`);
    - o Livro não a aceitou (veto ou cara de credencial).
  - Correção ligada: sem `licao`.
- **No Livro:** item `licao` no escopo do planejador do app (`scope_role = planner`), com `source_kind` =
  `teaching_correction`.
  - É de origem humana: só o dono a publica.
  - Só publicada, e com `aprendizado.licoes.modo: on` (global ou `por_app`), ela vai ao prompt do planejador.
  - `provenance.sessao` = `training:<sessão>`.
- **O que entra no texto:** só chaves de etapa que passam pela régua da chave livre. Título, comando e valor nunca
  entram. A chave que contém um valor do objetivo, do exemplo ou da persona é recusada, seja o valor inteiro ou
  qualquer palavra dele de 3 letras ou mais.
- **Evento `log` do salvar:** ganha `licao_id`, que é `null` sem lição.
- **Prova:** `simulated`, em `backend/tests/test_correcao_vira_licao_do_planejador.py` (3 testes; a lição publicada
  cabe no bloco do planejador do app). `real`: `not_run`.

## Adendo v1.101 (06/10/2026; número da orquestradora; item 31.177) — o rendimento de uma sessão de ensino

- **`GET /api/training/{id}/rendimento`** (nova, só leitura, nenhuma IA): o que a sessão de ensino gerou e quanto
  disso foi usado. Sessão desconhecida: 404 `not_found`.
- **Corpo:** `{sessao, resumo, fluxo, execucoes_do_fluxo, receitas, licoes, vizinhos}`.
  - `resumo`: `{receitas, receitas_liberadas, etapas_sem_ia, execucoes_do_fluxo, licoes, vizinhos, usado_de_verdade}`.
  - `fluxo`: `{id, status, uses, nascido_de_prova, em_uso_real_desde}` ou `null`.
  - `receitas[]`: `{id, step_key, app, status, liberada, sem_ia, caiu_na_ia, outras, usd_da_ia_na_retencao}`.
  - `licoes[]`: `{id, estado, papel, texto}`.
  - `vizinhos[]`: `{app, pacote, etapas_em_planos_livres}`.
- **Contagens por uso** (`{real, prova, simulada}`), com a régua da medida de 06/10:
  - `simulada`: a execução simulada;
  - `prova`: a prova de fluxo (`prova_fluxo_id`) ou a chave `lote:`;
  - `real`: o resto.
- **Por receita:**
  - `sem_ia`: a tentativa só `recipe` que comprovou;
  - `caiu_na_ia`: a cadeia `recipe>…`;
  - `outras`: o resto;
  - `usd_da_ia_na_retencao`: o US$ das chamadas ainda em `ai_calls`, pela regra de preço de `spent_usd`;
  - `liberada`: vale fora da persona que ensinou (30.81).
- **Vizinhos:** contam as etapas de planos LIVRES (sem fluxo e sem prova de fluxo) que aceitaram o pacote.
- **Prova:** `simulated` (`backend/tests/test_rendimento_do_ensino.py`, 2 testes). `real`: `not_run`.

## Adendo v1.103 (06/10/2026; número da orquestradora; item 31.151) — o pedido parecido chega ao fluxo pelo planejador

- **`POST /api/runs` com planejamento livre:** os fluxos ativos e no escopo que o comando PARECE (até 3, nota mínima
  0,3) vão ao planejador como habilidades conhecidas.
  - Vão a referência pública, o molde, os nomes dos parâmetros (sem os reservados) e os apps.
  - O molde com literal de alvo (um @, um endereço, um número longo) não vai.
  - Nunca vão o valor demonstrado nem o nome do fluxo.
- **Escolha:** o planejador pode devolver, com o plano, a habilidade e os valores tirados do comando. O código confere
  a referência oferecida, os parâmetros exatos e que cada valor está no comando.
  - Valendo, o plano gravado é o do fluxo: `plan.planner.model = "fluxo:<id>"` e `runs.flow_id` = o fluxo. A trilha
    (`decision`) diz "Plano do fluxo <ref> “<molde>” por semelhança, nota N".
  - Recusada, fica o plano livre, com o motivo na trilha.
  - Sem escolha, a trilha lista as oferecidas.
- **Comportamento (ajustado pelo 31.210, P-032, decisão do dono em 07/10):** a execução pedida com `mode: "execute"`
  cujo plano veio por semelhança segue direto para `running`, sem parar em `planned` para a prévia. A trilha
  (`decision`) diz "seguiu por semelhança". `flows.uses` sobe na escolha. As portas de aprovação de efeito externo
  continuam: a etapa com efeito pede aprovação no despacho, como em qualquer plano. Até o 31.210 a execução parava em
  `planned` ("aguarda a prévia aprovada").
- **O plano gravado não muda de forma:** a escolha não é gravada nele.
- **Prova:** `simulated` (`backend/tests/test_fluxo_por_semelhanca.py`, 5 testes; o 31.210 no mesmo arquivo). `real`:
  `not_run` (pede o deploy que leve o 31.151 e o 31.210).

## Adendo v1.106 (06/10/2026; número da orquestradora; item 31.157) — as personas e o valor no resumo do fluxo

Este adendo estende o `GET /api/operacoes/{operacao_id}/aprendizado` do adendo v1.96. Ele corrige dois achados do
percurso real da Portal no 57.

- **`personas`:** campo novo no topo da resposta, com a lista dos `profile_id` das execuções da operação, ordenados e
  sem repetição. É o filtro `persona` do painel. Sem execução, a lista vem vazia.
- **O resumo do item `fluxo:<id>`:** passa a trazer o valor do parâmetro quando a operação tem UM valor só para ele em
  todos os objetivos. Fica como marcador `{nome}` o parâmetro que:
  - varia por alvo;
  - a operação não tem;
  - é da execução (`instance_id`, `run_id`, `account_label`).

  O resumo continua redigido e com até 200 caracteres.
- Nada mais muda na forma da resposta.
- **Prova:** `simulated` (`backend/tests/test_aprendizado_da_operacao.py`). `real`: `not_run`, pede o deploy do corte
  59.

## Adendo v1.107 (07/10/2026; número da orquestradora; item 31.181) — quem pode usar o quê num app, por persona

`GET /api/aprendizado/alcance?app=<app_id>`: só leitura, sem IA. Diz, por persona vinculada ao app (vínculo ativo em
`device_profile_bindings`), cada receita ativa do pacote do app e cada fluxo ligado ou candidato do app, com `pode` e o
motivo do não.

- `200 {app_id, pacote, gerado_em, personas: [{profile_id, aparelhos}], resumo: {profile_id: {receita: n, fluxo: n}},
  itens: [...]}`.
- O item é `{tipo, id, chave, origem, estado, ..., por_persona: {profile_id: {pode, motivo}}}`:
  - `tipo` é `receita` ou `fluxo`;
  - `id` é o id da receita, ou a `ref_publico` do fluxo: o id interno do fluxo nunca sai (30.83), e a ref que faltar
    é preenchida na hora;
  - `chave` é a etapa da receita (vazia no fluxo);
  - `origem` é `ensino` ou `execucao`;
  - na receita, também `reproducoes_ok` e `reproducoes_falha`;
  - no fluxo, também `usos` e `nascido_de_prova`.
- O `motivo` (nulo quando `pode`) vem de um vocabulário fechado:
  - `presa_a_quem_ensinou`: a receita do ensino sem "Confirmar que fica" nem prova real do fluxo (30.81);
  - `fora_do_escopo`: o escopo do fluxo (personas ou grupos) não inclui a persona;
  - `fluxo_nao_ligado`: o fluxo é candidato.
- As regras são as do executor (`RecipeStore._restrita_ao_ensino` e `FlowStore._no_escopo`), não uma cópia.
- `404 app_desconhecido`: o app não está cadastrado. `422`: `app` ausente.
- **Prova:** `simulated` (`backend/tests/test_alcance_por_persona.py`). `real`: `not_run`, pede o deploy.

## Adendo v1.109 (07/10/2026; número da orquestradora; item 31.183) — a proposta do ensino para exibir

`GET /api/training/{id}` ganha `proposal_exibicao`, uma cópia da `proposal` só para exibir:

- todo dado da persona vira o marcador `{nome}`, sem diferença de caixa, também depois do @ e com qualquer espaço;
- vale em título, objetivo, resumo, comando, perguntas e conferência;
- os identificadores ficam como estão: `capability`, `app_id`, `kind`, `name`, `inputs`, `seq` e as marcas de etapa;
- sem proposta, ou sem persona, a cópia é igual à `proposal`.

Toda resposta do treino que traz a sessão leva o campo: a lista, o GET, parar, descartar, desfazer, a proposta e,
dentro de `session`, salvar e refazer receitas. Na cópia, a `key` da etapa também é mascarada: o `_` conta como
separador. O relatório por etapa (`steps[]`) da prévia, do salvar e do refazer receitas sai
com `title` e `reason` mascarados; a `key` fica (achado da Portal no 31.189). A `proposal` não muda, porque é ela que o painel devolve na prévia e no salvar. Limite: o valor só é trocado
inteiro. O painel passa a exibir a cópia no 31.189 (Portal).

**Prova:** `simulated` (`backend/tests/test_proposta_para_exibir.py`). `real`: `not_run`, pede o deploy.

## Adendo v1.110 (07/10/2026; número da orquestradora; item 31.191) — o rendimento de uma receita

`GET /api/aprendizado/receitas/{id}/rendimento`: só leitura, sem IA. Usa o leitor e a régua do uso real do
`GET /api/training/{id}/rendimento` (31.177) e vale para receita do ensino e de execução.

- `200 {id, step_key, app, status, liberada, sem_ia, caiu_na_ia, outras, usd_da_ia_na_retencao, origem, sessao,
  reproducoes, custo_medio_ia_por_etapa_usd, custo_evitado_usd, ultimo_uso_em, gerado_em}`.
- Os campos:
  - `app` é o pacote. `liberada` diz se a receita vale fora da persona que ensinou (30.81); na de execução é sempre
    `true`.
  - `sem_ia`, `caiu_na_ia` e `outras` são `{real, prova, simulada}`. `sem_ia` conta as etapas que a receita conduziu
    e comprovou. `caiu_na_ia` conta as em que divergiu e a IA assumiu.
  - `origem` é `ensino` ou `execucao`. `sessao` é a sessão de ensino, ou `null`.
  - `reproducoes` é `{ok, falha}`, da loja de receitas.
  - `usd_da_ia_na_retencao` é o US$ de IA das tentativas dela, das chamadas ainda guardadas.
  - `custo_medio_ia_por_etapa_usd` é o US$ médio de IA por tentativa conduzida pela IA, sem receita. Conta só as etapas
    com a mesma identidade da receita (`steps.template_hash`), em execução não simulada, nas 200 mais recentes.
  - `custo_evitado_usd` = `sem_ia.real` × esse médio.
- **Sem dado:** as contagens vêm `0`, nunca `null`. Os dois campos de custo vêm `null` quando não há referência de
  custo na retenção: o custo evitado não é inventado. `ultimo_uso_em` vem `null` na receita nunca usada.
- `404 receita_desconhecida`.
- **Prova:** `simulated` (`backend/tests/test_rendimento_por_receita.py`). `real`: `not_run`, pede o deploy.

## Adendo v1.113 (07/10/2026; número da orquestradora; item 31.200) — o escopo de assunto no Livro

`GET /api/aprendizado` ganha um campo e um filtro. Nada mais muda na rota.

- Campo `assunto` em cada item: o assunto canônico do item (minúsculas, sem acento, sem pontuação), ou `null` no item
  sem assunto e nos tipos que não têm o eixo (receita, fluxo, habilidade, memória). Hoje só o fato da pesquisa de uma
  operação (31.190, `source_kind` `operation_fact`) nasce com assunto.
- Filtro `?assunto=` (até 200 caracteres; mais que isso, `422`): só os itens daquele assunto, comparado na forma
  canônica ("Festival de Inverno!" acha "festival de inverno"). Entra antes da contagem, como os outros filtros.
- Regra que acompanha (sem rota): a lição com assunto só vai ao prompt de quem pede o MESMO assunto; a lição sem
  assunto segue indo a todos do papel no app. O fato da operação sem assunto, ou com identificador no assunto, não
  nasce mais no Livro.
- Migração 128 (`learning_items.scope_subject`).
- **Prova:** `simulated` (`backend/tests/test_livro_escopo_de_assunto.py`, `backend/tests/test_migracao_128.py`, em
  SQLite). PostgreSQL e `real`: `not_run`, pedem a vez da suíte e o deploy.

## Adendo v1.115 (07/10/2026; número da orquestradora; item 31.202) — a sombra da quarentena nos sinais

`GET /api/aprendizado/sinais` aceita um valor novo em `?kind=`: `sombra_da_quarentena`. Nada mais muda na rota.

- Um sinal por receita ensinada ativa, gravado pelo passo da curadoria e sobrescrito a cada passo.
  - `source_ref` é `receita:<id>` e `created_by` é `sistema`.
  - `reason`: `liberaria`, `prenderia_de_volta` ou `nenhuma`. `note` é o motivo, em português, só com contagens.
  - `polarity`: `positive` em liberaria, `negative` em prenderia de volta e `neutral` em nenhuma.
- **Fora da lista padrão:** sem `kind`, a rota não traz este sinal, como os das sombras do 30.34 (`autopublicaria`) e do
  30.55 (`aprovaria`). Não é gesto de pessoa.
- **Nada se aplica:** a sugestão não muda a receita, a quarentena nem a regra do 30.81.
- **Prova:** `simulated` (`backend/tests/test_sombra_da_quarentena.py`). `real`: `not_run`, pede o deploy.

## Adendo v1.117 (07/10/2026; número da orquestradora; parte do item 31.190) — a proveniência do fato da operação

Para o painel mostrar origem, evidência, confiança e frescor da lição que nasceu de um fato da pesquisa (31.214).

- `GET /api/aprendizado`: cada item ganha `source_kind`. É a origem do item de `learning_items` (ex.
  `operation_fact`, `recovery`, `manual`); `null` em receita, fluxo, habilidade e memória.
- `GET /api/aprendizado/licao/{id}`: o detalhe ganha `proveniencia`. É `null` em toda lição que não seja do modelo
  `fato_da_operacao`. Nesse modelo, as chaves são FECHADAS:
  - `operacao`: o id da operação de que o fato veio;
  - `assunto`: o assunto da operação, como foi escrito ("" quando tinha identificador);
  - `fontes`: os domínios das fontes, sem `www.`;
  - `frescor_ate`: até quando o fato vale (UTC), ou `null`;
  - `usado_em`: quantos alvos receberam o fato no texto;
  - `execucoes`: os ids das execuções da operação (até 20);
  - `confianca`: sempre `"confirmado"`, porque só o fato confirmado nasce no Livro.
- A regra e a chave da memória não saem.
- **Publicar** a candidata é `POST /api/aprendizado/licao/{id}/status` com `{"to": "published", "reason": …}`, num gesto
  só (passa por `validated`). Só uma pessoa publica: `human_origin` = `true`.
- **Prova:** `simulated` (`backend/tests/test_fatos_da_operacao_no_livro.py`). `real`: `not_run`, pede o deploy.

## Adendo v1.118 (07/10/2026; número da orquestradora; item 31.218) — a lição do planejador por persona

A correção ensinada a partir de uma falha (31.149) vira lição do planejador da PERSONA que falhou, não do app inteiro.

- **`GET /api/aprendizado/alcance?app=`** (v1.107): cada entrada de `personas` ganha `correcoes`, a lista das lições
  vivas (`candidate`, `validated`, `published`) do planejador nascidas de correção ensinada no app, com origem nessa
  persona. Cada uma traz:
  - `id`, `estado`;
  - `acao`: a chave da etapa que falhou;
  - `caminho`: as chaves das etapas que a pessoa ensinou;
  - `texto`: o que vai ao prompt;
  - `escopo`: `persona` (vale só para ela) ou `app` (lição anterior ao 31.218, que vale para todas; a persona dela é a
    do objetivo da etapa que falhou).
- **`GET /api/aprendizado/licoes/previa`** ganha `persona=` (até 64). Sem ele, a lição de uma persona não entra no
  bloco, como no planejamento de várias personas.
- **Regra (sem rota):** a lição com persona só vai ao planejamento de uma execução cujos aparelhos são todos dessa
  persona. A mesma correção ensinada a partir de duas personas são dois itens, cada um publicado pelo dono.
- **Prova:** `simulated` (`backend/tests/test_licao_do_planejador_por_persona.py`). `real`: `not_run`, pede o deploy e
  uma correção ensinada de uma falha real.

## Adendo v1.108 (06/10/2026; número da orquestradora; item 31.187) — a latência por estágio e por alvo no GET da operação

`GET /api/operacoes/{id}` ganha três campos, todos aditivos e derivados das horas dos estágios (nada novo é gravado). A
latência é métrica de primeira classe do dono, ao lado de custo e sucesso. A tela é da Portal (31.185).

- `alvos[].estagios[].etapa_ms` (int ou `null`): os milissegundos desde o evento ANTERIOR no tempo. Os eventos são a
  criação da operação, as horas dos outros estágios do alvo e o liberar. A conta segue o tempo, não a ordem fixa do dono:
  o rascunho sai depois de a interface de comentário abrir, e a lista os mostra na ordem do dono. Um estágio com a mesma
  hora do anterior dá `0`; uma hora ilegível dá `null`, sem derrubar os outros.
- `alvos[].latencia`: `{duracao_ms, espera_do_liberar_ms}`.
  - `duracao_ms` vai da criação da operação ao último estágio alcançado.
  - `espera_do_liberar_ms` vai da ação preparada (o pedido de aprovação) ao liberar, isto é, ao início da etapa com
    efeito. Fica `null` sem liberar. Essa espera pela pessoa NÃO entra no `etapa_ms` da ação executada, que conta desde
    o liberar.
- `latencia_por_estagio`: `{<estagio>: {n, p50_ms, p95_ms, max_ms}}` entre os alvos, só com etapa medida. O percentil é
  pelo posto mais próximo, o mesmo de `scripts/latencia-por-etapa.py`.

As horas vêm da correção do 31.175 (9a4e8e29). Resposta gerada e ação preparada ficam com a hora do pedido de aprovação,
ou com a do início da etapa que roda sem pedido; ação executada e resultado verificado, com a do fim da etapa. Antes, as
quatro saíam com a hora do liberar.

Código: `backend/app/modules/operacoes/domain/latencia.py`, `Leitura.liberado_em` em `domain/estagios.py` e
`ServicoDeOperacoes.ler`. Testes: `backend/tests/test_operacoes_estagios.py::test_a_latencia_de_cada_estagio_e_desde_o_evento_anterior_no_tempo_e_o_liberar_sai_a_parte`
e `backend/tests/test_operacoes.py::test_o_get_traz_a_latencia_por_estagio_por_alvo_e_da_operacao`.

## Adendo v1.111 (07/10/2026; número da orquestradora; item 31.195) — o relatório consolidado da operação

`GET /api/operacoes/{id}/relatorio` (só leitura, sem IA): a mesma leitura para a Canais (28.66) e a Portal (31.197). É
montado a partir do GET da operação (com a latência do v1.108) e do aprendizado da operação (v1.96). Os nomes seguem o
relatório que a Portal montava sozinha (`frontend/src/features/operacao/relatorio.ts`). Uma operação inexistente dá 404
`operacao_inexistente`.

**Não medido nunca vira zero.** Número, texto ou id sem dado vem `null`, e o estado de três vias vem `"nao_medido"`.
Nenhum @ de conta sai: ele vira `@[omitido]` em motivo e texto.

- `gerado_em`; `ambiente`: `real` (houve chamada de IA a um provedor real), `simulado` (só o provedor simulado) ou
  `nao_medido` (nenhuma chamada).
- `operacao`: `{id, comando, app_id, acao_final, status, criada_em, encerrada_em, assunto, fontes, fontes_da_pesquisa}`.
  O `status` vem como código (`em_curso`, `concluida`, `concluida_com_bloqueios`, `cancelada`).
- `capacidade`: a do GET, com `motivos` em lista `[{motivo, n}]`, o maior primeiro (no GET é `{motivo: n}`).
- `identidades`: `{solicitadas, executam_hoje, deficit, nao_executam: [{motivo, n}]}`. É a resposta objetiva a quantas das
  identidades pedidas executam hoje (conta no app, sessão válida e aparelho apto). Os motivos são `sem conta no app`,
  `sem sessão válida` e `aparelho indisponível`.
- `agentes[]`: `{profile_id, persona (o rótulo, nunca o @), aparelho, estado, estagio, parou_em, motivo,
  estagios: [{estagio, em, etapa_ms}], texto, conhecimento_ids, acao_final: {tipo, verificada, evidencia_id} | null, custo_usd,
  duracao_ms, espera_do_liberar_ms}`. `verificada` é `sim`, `nao`, `nao_conferida` ou `sem_acao`, como na Portal.
- `falhas_por_motivo`: `[{motivo, parou_em, agentes}]`, dos alvos bloqueados ou cancelados, o maior primeiro.
- `textos`: `{total, distintos, repetidos: [{texto, agentes}], lista: [{agente, texto}]}`. Repetido é o mesmo texto sem
  diferença de caixa nem de espaço.
- `criterios[]`: os 16 critérios mínimos do dono (prova30, `diagnostico.md` § 1a), com 2b, 3b e 11b: 19 linhas
  `{id, nome, estado, nesta_operacao, evidencia}`.
  - `nesta_operacao` é `sim`, `nao` ou `nao_medido`, lido dos dados desta operação.
  - `estado` é `implementado`, `testado_em_simulacao`, `provado_real`, `bloqueado` ou `nao_implementado`.
    A base é o estado do diagnóstico da prova (§ 1a, 06/10), revisto no código do corte 60 onde a entrega mudou
    (4, 6 e 8); ela vem dita em `criterios_base`.
  - A operação só sobe o estado, nunca o rebaixa: `nesta_operacao == "sim"` com `ambiente == "real"` dá
    `provado_real`, e com o simulado dá `testado_em_simulacao`.
- `aprendizado`: o corpo do `GET /api/operacoes/{id}/aprendizado` (v1.96) inteiro, com `disponivel: true`, sem repetir
  as 10 perguntas em outro formato. Sem memória da operação, `{disponivel: false, motivo}`.
- `latencia`: `{por_estagio (o `latencia_por_estagio` do v1.108), duracao_mediana_ms, mais_lento: {profile_id,
  duracao_ms} | null}`.
- `custo`: `{pesquisa_usd, alvos_usd, total_usd, teto_usd, por_peca_usd}`. `por_peca_usd` é o total dividido pelas ações
  executadas e verificadas, e fica `null` sem nenhuma.

Código: `backend/app/modules/operacoes/domain/relatorio.py`, `ServicoDeOperacoes.relatorio` e a rota em
`modules/operacoes/presentation/router.py`. Testes: `backend/tests/test_operacoes.py::test_o_relatorio_consolidado_da_operacao`
e `test_rota_http_do_relatorio`.

## Adendo v1.112 (07/10/2026; número da orquestradora; item 31.193) — cancelar alvos por filtro sem fechar a operação

`POST /api/operacoes/{id}/cancelar-alvos` `{profile_ids?, estados?, estagios?, instance_ids?}`. É uma rota própria: um
corpo esquecido no `…/cancelar` não pode virar "cancelar tudo".

- Os filtros se somam (E). O alvo entra quando casa com todos os filtros dados.
- Cancela só a execução ainda aberta de cada alvo que casa, inclusive a que espera o liberar (lida como `bloqueado`, mas
  com a execução aberta). A operação segue com os outros alvos e fecha pela leitura, como sempre.
- Resposta 200: `{cancelados: [profile_id], ignorados: [{profile_id, motivo}], operacao: <o GET da operação>}`. O
  motivo de `ignorados` é `ja_terminou` (execução terminada, inclusive entre a leitura e o pedido) ou `sem_execucao`
  (o alvo parou na criação).
- Erros:
  - 422 `filtro_vazio`, sem nenhum filtro (a operação inteira é `…/cancelar`);
  - 422 `estado_desconhecido`, para estado fora de `pendente`, `em_curso`, `concluido`, `bloqueado` e `cancelado`;
  - 422 para campo desconhecido no corpo;
  - 409 `ja_encerrada`, com a operação terminada ou cancelada;
  - 404 `operacao_inexistente`.

Código: `ServicoDeOperacoes.cancelar_alvos` e `modules/operacoes/presentation/router.py`. Testes:
`backend/tests/test_operacoes.py::test_cancelar_alvos_por_filtro_cancela_so_os_que_casam_e_a_operacao_segue` e
`test_rota_http_cancelar_alvos`.

## Adendo v1.114 (07/10/2026; número da orquestradora; item 31.206) — a fila por aparelho do alvo pendente

`GET /api/operacoes/{id}` ganha `alvos[].fila`, aditivo, para a Portal mostrar onde o alvo está na fila do aparelho
dele.

- `fila`: `{posicao, a_frente, previsao_inicio_em, base_ms}` no alvo `pendente`. Vem `null` no alvo que já começou,
  terminou ou parou.
  - `a_frente` (int): quantos trabalhos abertos do MESMO aparelho, de qualquer operação ou execução avulsa, passam antes
    dele. A ordem é a do despacho: o que já roda, depois a maior `prioridade` e, entre iguais, a execução mais antiga.
    `posicao` é `a_frente + 1`; 1 é o próximo.
  - `base_ms` (int ou `null`): a duração mediana de trabalho dos alvos desta operação que já terminaram, do estágio
    `aparelho` ao último alcançado.
  - `previsao_inicio_em` (ISO UTC ou `null`): agora + `a_frente` × `base_ms`. É estimativa. Sem nenhum alvo terminado
    não há amostra, e `base_ms` e `previsao_inicio_em` vêm `null`, nunca um número inventado.

Junto, sem adendo próprio (31.205): o alvo que ainda ia começar com a operação já no `max_usd` é recusado no
planejamento e fica em `acao_bloqueada` com o motivo literal `"teto da operação"`. Esse motivo aparece em
`alvos[].motivo` e como chave de `capacidade.motivos`. O evento `plan.refused` sai com `motivo: "teto_da_operacao"`.

Código: `backend/app/modules/operacoes/domain/fila.py` e `ServicoDeOperacoes._anotar_filas`. Testes:
`backend/tests/test_operacoes_estagios.py::test_a_fila_do_aparelho_conta_quem_roda_a_prioridade_e_a_idade_e_a_previsao_so_com_amostra`
e `backend/tests/test_operacoes.py::test_o_get_traz_a_fila_do_aparelho_do_alvo_pendente`.

## Adendo v1.116 (07/10/2026; número da orquestradora; item 31.213) — a lista de operações por persona ou aparelho

`GET /api/operacoes?profile_id=&instance_id=`: os dois são opcionais e se somam. Com algum deles, a lista traz só as
operações que têm alvo daquela persona e/ou daquele aparelho, as mais recentes primeiro (`limite`, como antes). Cada
item ganha `alvos`, com o resumo SÓ dos alvos que casam:

`{profile_id, instance_id, estado, estagio, motivo, parou_em, acao_verificada, custo_usd, duracao_ms}`

`acao_verificada` é o `verificada` da ação final (`true`, `false` ou `null` sem ação). `duracao_ms` é a do v1.108.
Sem filtro, a lista é a de sempre, sem `alvos`. Um filtro vazio (`?profile_id=`) dá 422. É o histórico da persona
(31.212) sem ler o detalhe das 20 operações mais recentes.

Código: `ServicoDeOperacoes.listar` e a rota em `modules/operacoes/presentation/router.py`. Teste:
`backend/tests/test_operacoes.py::test_a_lista_filtrada_por_persona_ou_aparelho_traz_so_as_operacoes_dela_com_o_resumo_do_alvo`.

### Nota do 31.216 (sem número novo): a leitura repetida não grava

`GET /api/operacoes/{id}` (e a lista, o relatório e as respostas dos POSTs, que leem por ele) **grava só o que mudou
desde a leitura anterior**: o estágio derivado de cada alvo, com o evento `operacao.alvo`, a reabertura da operação
cuja ação foi aprovada por fora do liberar e o fechamento com `operacao.encerrada`. Cada escrita é condicional, e a
mesma leitura repetida não grava nada nem emite evento. "Só lê" quer dizer "não grava de novo", não "nunca grava": não
há laço do sistema que avance a operação, e é a leitura (a do painel, a da Canais ou a de um POST) que a avança.
Teste: `backend/tests/test_operacoes.py::test_ler_varias_vezes_nao_grava_de_novo_nem_avisa_de_novo`, com uma mutação
(sem a guarda de `_anotar`) pega pelo teste.


## Adendo v1.124 (07/10/2026; número da orquestradora; item 31.229) — o custo e o modelo por passo

Fonte da linha do tempo do alvo (31.228, Portal) e da medida da política de modelos (31.223, Aprendizado). Só expõe:
não muda a política. Não há migração, porque o vínculo já existe na origem: o executor grava `ai_calls.step_id` em
toda chamada `decide`/`verify` de uma etapa, e `actions.ai_call_id` (088) aponta para o `decide` que escolheu a ação.
O planejamento (`plan`) não tem etapa e entra em `sem_passo`.

**Onde:**
- `GET /api/runs/{id}`: o campo novo `custo_por_passo`, da execução;
- `GET /api/operacoes/{id}`: `alvos[].custo_por_passo`, do alvo (o objeto da execução dele; `null` sem execução), e,
  na operação, `custo_por_modelo` e `custo_por_estagio`, somados entre os alvos.

**`custo_por_passo`:**

```
{
  "passos": [{
    "step_id": "...", "seq": 3, "key": "...", "capability": "OPEN_POST" | null,
    "efeito": true,                       // a etapa declara efeito externo (steps.side_effect)
    "estagio": "post_localizado" | null,  // o estágio da operação que a capability marca (app.yaml operacao.estagios)
    "modelo": "claude-sonnet-..." | null, // id CRU do modelo do último decide ok da etapa; null = só receita
    "commit": {"fonte": "ai" | "recipe", "modelo": "claude-opus-..." | null, "tier": 1 | null,
               "escalate": "efeito" | null} | null,
                                          // a última ação com efeito NÃO rejeitada (actions.side_effect=1 e
                                          // status<>'rejected') e o decide que a escolheu (actions.ai_call_id): o
                                          // modelo, o tier e o escalate dele; a decisão descartada fica sem ação
                                          // ligada; null = a etapa não chegou ao commit
    "chamadas": 4, "custo_usd": 0.0123,
    "por_modelo": [{"modelo": "...", "chamadas": 3, "custo_usd": 0.01}]
  }],
  "sem_passo": {"chamadas": 1, "custo_usd": 0.004, "por_modelo": [...]},  // planejamento e chamadas sem etapa
  "por_modelo": [{"modelo": "...", "chamadas": 5, "custo_usd": 0.0163}], // passos + sem_passo
  "por_estagio": {"post_localizado": {"chamadas": 2, "custo_usd": 0.005}, "sem_estagio": {...}, "sem_passo": {...}}
}
```

Os passos vêm na ordem de `seq` e incluem as versões replanejadas. `custo_usd` segue a regra de `spent_usd`: o custo
declarado onde há, tokens vezes o preço do modelo onde não há, e a chamada simulada a US$ 0. `chamadas` conta todas,
inclusive as simuladas e as que falharam. Na operação, `custo_por_modelo` é a lista `por_modelo` somada entre os alvos,
e `custo_por_estagio` o mapa `por_estagio` somado. O modelo vem sempre como o id cru gravado em `ai_calls.model`; o
rótulo para pessoa fica com a tela.

Código: `backend/app/planning/custo_por_passo.py` (`por_execucao`, com quatro consultas para qualquer número de execuções,
e `somar`), chamado pela rota `GET /api/runs/{id}` e por `ServicoDeOperacoes.ler`. Testes:
`backend/tests/test_custo_por_passo.py` (a trilha do 31.223 com a decisão descartada e a refeita; a soma fecha com
`spent_usd`; o GET do alvo igual ao da execução; o alvo sem execução vem `null`).

**Nota no v1.124, as amostras do host (31.180, v1.102):** `GET /api/host/amostras` passa a trazer as três colunas do
amostrador v2, `cpu_media_pct`, `demais_processos_pct` e `nao_atribuido_pct`: números, ou `null` na linha gravada pelo v1
(9 colunas), que segue válida. Antes, a leitura as descartava (achado da Portal no percurso do 31.211). Código:
`COLUNAS` em `modules/fleet/infrastructure/amostras_do_host.py`. Teste:
`backend/tests/test_amostras_do_host.py::test_o_csv_do_amostrador_v2_traz_as_tres_colunas_novas_e_o_v1_segue_valido`.
## Adendo v1.120 (07/10/2026; próximo livre da reserva; item 31.221) — o ensino a partir da execução

- **`GET /api/aprendizado/execucao/{run_id}/ensino`**: `{run_id, status, simulada, ensinaveis, etapas}`. Cada etapa traz:
  - `step_id`, `key`, `capability`, `status`, `driven_by`;
  - `persona`: o id, nunca o nome;
  - `receita`: `{id, status, replay_ok}` da mais nova nascida dela, ou `null`;
  - `ensinavel`;
  - `motivo`: `null` ou um destes: `execucao_simulada`, `com_efeito`, `nao_concluida`, `ja_por_receita`, `sem_ator`,
    `caminho_nao_reproduzivel`, `sem_receita`, `receita_ja_vale`, `receita_fora_de_circulacao`;
  - `ferramentas_nao_reproduziveis`: só com ferramenta da tentativa que a receita não reproduz.

  Nenhum argumento de ação nem texto de trava sai. 404 `execucao_desconhecida`.
- **`POST /api/aprendizado/execucao/{run_id}/ensino`** (sem corpo; decide o operador da sessão): promove as candidatas
  ensináveis a `active` pelo Livro (candidate → validated → published), com o motivo
  `ensino_da_execucao:<run> persona:<id>`. Responde o mesmo corpo do GET, relido depois, mais:
  - `promovidas`: `[{recipe_id, step_key}]`;
  - `recusadas`: `[{recipe_id, step_key, code, message}]`.

  Sem candidata: 200 com `promovidas` vazio.
- **Prova:** `simulated` (`backend/tests/test_ensino_da_execucao.py`). `real`: `not_run`, pede o deploy e uma execução
  real da onda 2.

## Adendo v1.122 (07/10/2026; número da orquestradora; item 31.223) — o modelo forte só no commit

Sem rota nova (só dois campos de leitura no `GET /api/ai`). A exposição por passo (modelo que decidiu, custo, commit) é da Jev, no 31.229 (v1.124).

- **`ai.strong_model_only_on_commit`** (`config.yaml`, padrão `true`; vale na subida): quando a etapa com efeito sobe
  ao modelo de escalonamento (`ai.strong_model_for_side_effect`), ele decide SÓ o commit. A etapa começa no modelo de
  ação. A primeira decisão que dispararia o efeito é descartada e refeita no forte, que segue até o fim da tentativa.
  `false` = a etapa inteira no forte, o modo de antes.
- **`GET /api/ai`** (`AiStatus`, aditivo e só leitura) ganha dois campos opcionais, com os valores em vigor na subida:
  - `strong_model_for_side_effect`: `by_risk`, `true` ou `false`;
  - `strong_model_only_on_commit`: booleano.

  Os dois mudam no `config.yaml` e valem na subida da farm-central. Nada muda em `PUT /api/settings`.
- **O que muda no registro, sem campo novo:**
  - a decisão de commit descartada fica em `ai_calls` como `decide` tier 0, sem ação ligada (`actions.ai_call_id`);
  - a refeita é `decide` tier 1 com `escalate=efeito`, e a ação dela leva `side_effect`;
  - a linha de escalonamento da execução ganha "; só a decisão do commit (31.223)".
- **Prova:** `simulated` (`backend/tests/test_forte_so_no_commit.py`). `real`: `not_run`; o custo por alvo da primeira
  operação depois do deploy 61, contra a onda 2.

## Adendo v1.125 (07/10/2026; número da orquestradora; item 31.235) — a pesquisa da operação no GET

Sem rota nova. O `GET /api/operacoes/{id}` ganha um campo aditivo e só de leitura. O contrato é o mesmo combinado com o
Portal (31.234, `.claude/handoffs/jev-para-portal-31-234.md`).

- **`OperacaoDetalhe.pesquisa`**: objeto ou `null`.
  - `null` quando a operação não pediu pesquisa (sem `assunto`), e também no central anterior a este campo.
  - Os campos do objeto:
    - `estado`: `reaproveitada_do_livro` (o Livro cobriu o pedido, sem chamada paga, 31.231), `paga`, `falhou` (a
      última tentativa falhou e espera para tentar de novo) ou `nao_rodou` (pediu, mas nada rodou ainda: desligada,
      teto, antes da execução);
    - `criterio`: o texto por extenso. No reaproveitamento, é o critério de cobertura. Na paga, as contagens (fatos,
      fontes, buscas), precedidas de "o Livro não cobriu o pedido" quando o reaproveitamento está ligado. Na falha, a
      espera. Em `nao_rodou`, se a pesquisa está desligada;
    - `minimo_fatos`: `ai.pesquisa.reaproveitar_min_fatos` em vigor (`0` = reaproveitamento desligado);
    - `frescor_ate`: o menor frescor dos fatos usados (do Livro ou da paga); `null` na falha e em `nao_rodou`;
    - `custo_usd`: o mesmo valor de `custo.pesquisa_usd`;
    - `fatos`: no reaproveitamento, um elemento `{item, origem, frescor_ate, confianca}` por fato do Livro usado, com
      `item` igual ao id do item do Livro; vazio nos outros estados (as fontes da paga seguem em `fontes_da_pesquisa`).
- **Fonte:** só a memória da operação: `pesquisa.estado`, `livro.<item>` e os fatos `pesquisa.<hash>`.
- **O que não entra:** nem o texto de um fato nem uma URL.
- **Código:** `backend/app/modules/pedidos/domain/resumo_da_pesquisa.py` e `ServicoDeOperacoes._pesquisa`.
- **Prova:** `simulated` (`backend/tests/test_pesquisa_no_get_da_operacao.py`, os quatro estados escritos pela própria
  pesquisa da operação). `real`: `not_run`, até o GET da primeira operação com assunto depois do deploy.

## Adendo v1.126 (07/10/2026; número da orquestradora; itens 31.258 e 31.251) — o alvo adiado pela frota e o que espera resposta

Sem rota nova. O `GET /api/operacoes/{id}` ganha campos aditivos e só de leitura; ausentes no central anterior.

- **`alvos[].retomada_em`**: sempre `null` desde o ADR-083, que tirou o espaçamento entre contas sobre o mesmo alvo
  (31.240). O campo fica para não quebrar quem já o lê; o motivo "espaçamento da frota" não sai mais.
- **`alvos[].aguarda_resposta`**: `{pergunta, desde}` ou `null`. Preenchido quando a execução do alvo está em
  `needs_input`: `pergunta` é o `status_detail` da execução, redigido e cortado em 300 caracteres; `desde` é o último
  `run.updated` dela (`null` se não houver).
- **`capacidade.aguardando_resposta`**: quantos alvos têm `aguarda_resposta` não nulo.
- **Código:** `ServicoDeOperacoes._adiadas_pela_frota` e `_perguntas_abertas`
  (`backend/app/modules/operacoes/infrastructure/servico.py`); o texto que casa a espera é
  `app.social.policy.ESPACO_DA_FROTA`.
- **Prova:** `simulated` (`backend/tests/test_frota_adiada_no_get.py`). `real`: `not_run` até o deploy.

## Adendo v1.127 (07/10/2026; número da orquestradora; item 31.259) — a pesquisa no relatório consolidado (v1.111)

Sem rota nova. O `GET /api/operacoes/{id}/relatorio` muda em dois pontos.

- **`pesquisa`**: o mesmo objeto de `OperacaoDetalhe.pesquisa` (v1.125), com `criterio` passado pelo `sem_arroba`. É
  `null` quando a operação não pediu pesquisa. Não traz texto de fato nem URL.
- **Critérios 5 e 6**: o `nesta_operacao` lê `pesquisa.estado`.

  | `pesquisa.estado` | 5 | 6 |
  |---|---|---|
  | `reaproveitada_do_livro` | `sim` | `sim` |
  | `paga` | `sim` | `sim` só com `fontes_da_pesquisa` não vazio; senão `nao` |
  | `falhou` | `sim` | `nao` |
  | `nao_rodou` | `nao_medido` | `nao_medido` |

  - Sem o campo, vale a regra anterior (fontes e custo).
  - O `estado` do critério só sobe (`_ORDEM`). Numa operação já existente, o `nesta_operacao` de 5 e 6 pode mudar
    na releitura; a Canais o exibe.
- **Código:** `app/modules/operacoes/domain/relatorio.py` (`criterios`) e `ServicoDeOperacoes.relatorio`.
- **Prova:** `simulated` (`backend/tests/test_pesquisa_no_relatorio.py`). `real`: `not_run` até o deploy.
## Adendo v1.119 (07/10/2026; número da orquestradora; item 31.220) — o laço que avança a operação

`Settings.operacao_laco_s` (inteiro, 0 a 3600, padrão **0 = desligado**): de quanto em quanto tempo o laço do sistema lê
as operações abertas (sem `finished_at`) e as avança sem leitura externa, pelo mesmo `ServicoDeOperacoes.ler` do
`GET /api/operacoes/{id}`. É relido a cada volta, e ligar é `PUT /api/settings {"operacao_laco_s": 15}`, sem reinício.
Desligado, o laço só confere a configuração a cada 30 s. Ele roda só na réplica que hospeda (não com `ROLE=api`) e
dispensa a trava de líder, porque a leitura é idempotente: o estágio de cada alvo e o fechamento da operação são
gravados por `UPDATE` condicional, e só quem grava emite `operacao.alvo` ou `operacao.encerrada`. O laço e um GET que
leem juntos avisam uma vez. Sem operação aberta, a volta é uma consulta e nada mais.

No corte 61 o laço sai desligado: a prova de 07/10 usa o laço da Canais (120 s) e a tela, e ele só liga depois dela.

Código: `backend/app/modules/operacoes/infrastructure/laco.py` (`LacoDasOperacoes`), montado em `bootstrap.py` e
iniciado em `state.py` (tarefa `operacoes`). Testes: `backend/tests/test_operacoes_laco.py` (relógio falso: desligado
não lê; ligado fecha sem GET e não repete; duas leituras ou dois fechamentos com a mesma linha velha avisam uma vez; a
falha numa operação não para as outras).
## Adendo v1.121 (07/10/2026; número da orquestradora; item 31.224) — `parametros` conferidos com o app

`POST /api/operacoes` confere cada parâmetro fixo com o app ANTES de gravar a operação e de criar qualquer execução,
para que um erro de digitação não custe chamada paga. Vale depois das recusas que já existiam (credencial, formato do
nome, tamanho do valor, até 10 parâmetros):

- `username` vai sem arroba e sem espaço, porque a prova local compara o texto da tela, que não traz o `@`;
- no app com catálogo de ações (hoje o Instagram e o Outlook), a chave tem de ser uma das que as ações do catálogo usam
  (`bindings`, `optional_bindings` e `inherited_bindings`). O app sem catálogo (o QA Messenger) segue com a chave livre.

A conferência vem DEPOIS da repetição: o mesmo corpo com a mesma `idempotency_key` de uma operação já criada (antes
desta regra, ou antes de o catálogo mudar) devolve a operação que existe, e não um 422.

A recusa é 422, e o corpo para no primeiro problema:

`{"detail": {"code": "pedido_invalido", "message": "...", "motivo": "username_com_arroba" | "username_com_espaco" |
"parametro_desconhecido", "posicao": N, "campo": "username" (só nos dois motivos de username), "aceitos": [...] (só em
parametro_desconhecido)}}`

`posicao` conta de 1, na ordem de `parametros`. O nome que veio nunca volta no corpo (convenção do PR 487: a credencial
pode estar no próprio nome); `aceitos` vem do catálogo do app, não do pedido.

Código: `_conferir_contra_o_app` em `modules/operacoes/infrastructure/servico.py`. Testes em
`backend/tests/test_plano_da_operacao.py`: a recusa sem gravar nem criar execução, o caminho aceito, o app sem catálogo
e o 422 pela rota.

## Adendo v1.123 (07/10/2026; número da orquestradora; item 31.227) — a forma do parâmetro vem do catálogo

O `catalogo.yaml` do app ganha a seção opcional `parametros: {nome: {forma, max}}`. `forma` é `handle` (sem arroba
nem espaço) ou `texto`, e `max` vai de 1 a 300. A carga recusa nome que nenhuma ação usa, forma fora do vocabulário e
máximo fora da faixa. O Instagram declara `username` e `post_author` como `handle` de até 30 caracteres.

A conferência do v1.121 passa a ler essa declaração: o parâmetro declarado segue a forma e o tamanho dele, e o que não
está declarado vale o teto genérico de 300. O app sem catálogo não tem conferência de chave nem de forma; isso muda o
v1.121, onde a regra do `username` valia em qualquer app. Os motivos do 422 são `<nome>_com_arroba`,
`<nome>_com_espaco` e `<nome>_longo` (este último com `max`), todos com `posicao` e `campo` = o nome declarado, além de
`parametro_desconhecido`. Para `username`, os dois primeiros têm o mesmo valor do v1.121.

Código: `FormaDoParametro` e `_parametros` em `planning/capabilities.py`, e `_conferir_contra_o_app` em
`modules/operacoes/infrastructure/servico.py`. Testes em `backend/tests/test_plano_da_operacao.py` (recusa por
`post_author` com arroba e por `username` acima de 30; os 30 exatos passam; declaração errada recusada na carga).

## Adendo v1.129 (07/10/2026; número da orquestradora; item 31.274 parte 2, ADR-085) — o que a automação prepara na sugestão

Sem rota nova. `POST /api/runs/targets/suggest` ganha um campo aditivo; a forma do resto não muda.

- **`escolhidas[].preparo`**: `string[]`, vazio por padrão. Uma frase curta por item, no futuro do presente, dizendo o que
  A AUTOMAÇÃO fará antes de agir: "vai ligar o aparelho android-02", "vai esperar vaga para ligar o aparelho android-02",
  "vai conferir a sessão no preparo". Com `auto_start_devices` desligado, a frase diz o contrário ("o aparelho … está
  desligado e o religamento automático não o liga: ligue-o"), em vez de prometer. É aviso, nunca pedido à pessoa.
- **`descartadas[].motivo`**: texto como antes, mas agora só o impossível sai por código, com o motivo dito e o que fazer:
  conta bloqueada; senha não guardada com consentimento, senha guardada sem consentimento ou senha recusada pelo app
  (ADR-040: o app tem login gerenciado e a sessão do par ainda não está pronta). Aparelho desligado, sessão não conferida
  e app fechado deixam de ser motivo de descarte, do código e do modelo.
- O padrão de `limits.auto_start_devices` passa a `true`.
- **Prova:** `simulated` (`backend/tests/test_automacao_prepara_o_que_falta.py`). `real`: `not_run`, pede o deploy.

## Adendo v1.128 (07/10/2026; item 31.271) — a prova da receita candidata na linha do Livro

Sem rota nova. Campo aditivo na linha do Livro (`GET /api/aprendizado` e o item aberto, que compartilham a mesma
serialização), a forma proposta pelo Portal em `portal-para-jev-receita-candidata.md` e lida pelo 31.270.

- **`prova_da_candidata`**, SÓ em receita com `state = candidate` (ausente em todo o resto):
  - `concordancias`: `recipes.shadow_agree`, a sequência (a divergência zera);
  - `necessarias`: `ai.recipes_promote_after` (ou `ai.recipes_promote_after_com_efeito`, se a receita tem `commit`; 31.287), lido a cada resposta;
  - `ultima_consulta`: `{em, resultado}` ou `null` quando nunca foi consultada;
  - `substitui`: `{ref, versao, estado: "active"}` ou `null`. É a ATIVA da mesma chave (pacote, versão do app,
    assinatura, variante e etapa) que a candidata assume ao ser promovida; não é a versão anterior do detalhe.
- **`ultima_consulta.resultado`** (a migração 129 grava `recipes.ultima_consulta_em` e `_resultado`):
  - `concordou` e `divergiu`: o veredito da sombra (`RecipeStore.shadow`);
  - `nao_aplicavel`: a candidata não se aplicou na tela de partida e não teve veredito (31.262). A 3ª seguida vira
    `divergiu`, gravado pelo `shadow` que o executor chama em seguida. Este valor é ADITIVO ao contrato proposto
    (`concordou | divergiu | outro_escopo | quarentena`): o leitor do Portal já mostra o código cru de um resultado novo;
  - `outro_escopo`: a consulta achou a receita, mas o alvo é de outro escopo (31.249);
  - `quarentena`: a consulta não achou receita viva e achou uma posta de lado. A gravação cai na receita em
    quarentena, que a linha do Livro não mostra como candidata; **hoje o valor não aparece em `prova_da_candidata`**.
    Fica gravado para o dossiê e para quem ler o banco.
  - a concordância de execução SIMULADA que não conta para a candidata (RA-19 B) NÃO grava consulta.
- **Compatibilidade:** `necessarias` sai `null` onde o leitor não conhece a configuração (os leitores de teste); o painel
  trata `null` como "o central não diz", nunca zero. Receita ativa, fluxo, habilidade e memória não ganham o campo.
- **Prova:** `simulated` (`backend/tests/test_prova_da_candidata.py`, 7; `backend/tests/test_migracao_129.py`, 2). `real`:
  `not_run`; o `GET /api/aprendizado` de uma candidata depois da primeira operação pós-deploy.


## Adendo v1.132 (10/10/2026; número concedido ao 31.281, migração 131; ADR-087) — a conta planejada: estados, credencial preparada e a ação estruturada do refinador

Contrato do 31.281 (backend), consumido pelo 31.282 (refinador) e pelo 31.283 (painel). Tudo aditivo: nenhuma rota e nenhum campo existente muda de significado. A migração 131 só adiciona colunas a `profile_accounts`; as linhas existentes entram `confirmada`. Quatro coisas separadas (ADR-087): a persona, a conta externa (desejada ou existente), a credencial (cofre) e a sessão observada. **A senha nunca tem campo de resposta**: nem na conta, nem no evento, nem na ação do refinador.

### 1. Estados e campos

`ProfileAccountDTO` ganha o objeto **`provisioning`** (sempre presente; `state: "confirmada"` nas contas que já existiam):

| Campo | Tipo | Sentido |
|---|---|---|
| `state` | `planejada`, `credencial_preparada`, `aguardando_cadastro_externo`, `aguardando_verificacao`, `confirmada` ou `falha` | Ciclo de provisionamento (coluna `provisioning_state`). |
| `desired_handle` | `string` ou `null` | Endereço DESEJADO (editável até `confirmada`). |
| `detail` | `string` ou `null` | Motivo da última falha ou o que falta, redigido e cortado em 300 caracteres. |
| `resume_state` | estado ou `null` | Só em `falha`: para onde `retomar` volta. |
| `confirmed_at` | ISO ou `null` | Quando virou `confirmada` (conta que já existia: `null`). |
| `evidence` | `{kind, ref}` ou `null` | `kind` = `sessao` (`ref` = id da sessão observada), `declarada` (`ref` = quem declarou) ou `igfarm` (`ref` = `igfarm_account_id`, ADR-088). |
| `actions` | lista de eventos | Os `evento` que a rota de transição aceita AGORA neste estado (como `session_actions`). |
| `authenticated` | `bool` | Derivado e só de leitura: `confirmada` e sessão `session_ready` no aparelho vinculado. NÃO é estado gravado. |

- **Desejado x confirmado:** `desired_handle` é o que a pessoa quer; `handle` (campo que já existe) segue sendo o endereço CONFIRMADO e fica `""` enquanto a conta não está `confirmada`. `login_identifier` e `credential` seguem como estão.
- A conta que não está `confirmada` não é "conta real logada": sai do roteamento do Automático, do pré-voo de sessão e da lista de dados ao plano (seção 5).

### 2. Rotas (em `/api/instagram/profiles/{profile_id}`)

- **`POST /accounts/planned`**, corpo `{app_id, host?, desired_handle?}` → `ProfileAccountDTO`. 201 na primeira vez (`state: planejada`, `handle: ""`); **200 idempotente** se já existe conta planejada deste (perfil, app, host): devolve a mesma linha, e um `desired_handle` diferente só a EDITA (enquanto não `confirmada`). Erros: 404 `not_found` (perfil ou app), 409 `duplicate_account` (já existe conta confirmada neste app/host), 422 `desired_handle_invalido`. O `POST /accounts` de sempre continua criando conta já `confirmada`.
- **`GET /accounts/handle-suggestions?app_id=`** → `{suggestions: [{handle, source}]}`, `source` = `persona_nome`, `persona_dados` ou `alternativa`. Local e determinístico (nome, sobrenome e ano de nascimento da persona; alternativas numeradas), sem IA e sem rede, sem os endereços que já são de outra conta deste app aqui. É a parte local do endereço (antes do `@`): apps de e-mail pedem o domínio na tela. Não afirma disponibilidade no provedor.
- **`PATCH /accounts/{account_id}`** ganha `desired_handle`; 409 `conta_confirmada` depois de confirmada.
- **`POST /accounts/{account_id}/credential/prepare`**, corpo `{modo, consent, substituir?, password?, clonar_de?, tamanho?}`:
  - `modo: "gerar"`: o servidor gera a senha com `secrets` (CSPRNG; `tamanho` 16 a 64, padrão 20; maiúscula, minúscula, dígito e símbolo), grava direto no cofre e **não a devolve**. A IA nunca participa.
  - `modo: "digitar"`: `password` (`SecretStr`, como no `PUT …/credential`); o 422 do validador não devolve o corpo.
  - `modo: "reutilizar"`: `clonar_de` (o mesmo campo do `credential/clone`) = id de OUTRA conta da MESMA persona que já tem credencial; nunca é o padrão e a escolha é expressa. Copia como o `credential/clone` (31.103).
  - `consent` tem de ser `true`: gerar ou digitar na tela com a caixa marcada vale como o consentimento do ADR-040 para ESTA conta. Reutilizar não herda o consentimento da origem; vale a caixa marcada agora.
  - Estado: `planejada` passa a `credencial_preparada`. Em `credencial_preparada`, só com `substituir: true` (troca a senha antes da confirmação; sem ele, 409 `credencial_ja_preparada`). De `aguardando_cadastro_externo` em diante, 409 `estado_nao_permite_credencial` (depois de `confirmada` é a troca de senha pela rota de hoje).
  - Resposta: `ProfileAccountDTO` (credencial só com metadados). Erros: 409 `consentimento_de_credencial`, 422 `credencial_modo_invalido` (campo do modo errado, ou `password` junto com `clonar_de`), os do `credential/clone` para a origem (404 `not_found`, 409 `credencial_de_outra_persona`, `clonar_de_si_mesma`, `no_credential`) e 503 `secret_store_unavailable`.
  - O `PUT …/credential` e o `…/credential/clone` numa conta `planejada` fazem o mesmo que `digitar` e `reutilizar` e avançam o estado; o `DELETE …/credential` de uma conta `credencial_preparada` a devolve a `planejada`, e com o cadastro em andamento (`aguardando_*`) dá 409 `estado_nao_permite_credencial`.
- **`POST /accounts/{account_id}/provisioning`**, corpo `{evento, estado_esperado, motivo?, evidencia?}` → `ProfileAccountDTO`. **`estado_esperado` é obrigatório (comparar e trocar):**

| De | `evento` | Para | Regra |
|---|---|---|---|
| `credencial_preparada` | `iniciar_cadastro` | `aguardando_cadastro_externo` | Exige credencial gravada e consentimento. O cadastro no provedor é da pessoa (CAPTCHA, código e e-mail são dela, ADR-009); a automação preenche e dita a senha pelo canal sensível. |
| `aguardando_cadastro_externo` | `enviado` | `aguardando_verificacao` | A pessoa enviou o formulário. |
| `aguardando_verificacao` | `confirmar` | `confirmada` | Exige `evidencia` (abaixo). `handle` passa a ser o endereço confirmado e `confirmed_at` é gravado. |
| qualquer ativo (`planejada` a `aguardando_verificacao`) | `falhar` (`motivo`) | `falha` | Grava `resume_state`; nada é apagado. |
| `falha` | `retomar` | `resume_state` | Preserva credencial e dados; não reinicia. |
| `planejada` a `aguardando_verificacao`, ou `falha` | `cancelar` | (conta removida) | Só apaga a senha do cofre se nenhuma outra conta a referencia (regra do clone). Resposta `{removida: true, credencial_removida: bool}`. |

  - `evidencia` do `confirmar`: `{tipo: "sessao", sessao_id}` (sessão observada cujo usuário é IGUAL ao desejado; o `handle` vem dela) ou `{tipo: "declarada", handle_confirmado}` (marcação nominal da pessoa, gravada como evidência `declarada` com o autor). `confirmar` sem evidência não existe.
  - **Idempotência:** repetir o evento quando a conta já está no estado de destino dele devolve 200 com a conta, sem efeito e sem novo evento. Estado diferente do esperado e do destino: 409 `estado_inesperado` (`detail.details.estado_atual`).
  - Erros: 409 `transicao_invalida` (evento que o estado não aceita), 409 `sem_credencial`, 409 `sem_consentimento`, 422 `sem_evidencia`, 409 `evidencia_nao_confere` (sessão de outro usuário), 404 `not_found`.
- **Eventos:** `identity.conta.provisionamento` `{profile_id, account_id, app_id, evento, de, para, evidencia_tipo?}`. Sem `handle`, sem `desired_handle` e sem nenhum valor de segredo.

### 3. A ação estruturada do refinador

`POST /api/commands/refine` (e o refinar com `run_id`) ganha, aditivo, **`acoes_de_conta`**: lista de itens, um por par (persona, app) do comando cuja credencial não está pronta. O backend calcula isso ANTES de chamar o modelo (o estado de cada par vai ao prompt, nunca valor), e a lista não passa pela triagem de resposta: não tem campo de texto.

```json
{"persona_id": "…", "persona_nome": "…", "app_id": "…", "app_nome": "Outlook", "host": null,
 "estado": "sem_conta",
 "acoes": ["preparar_credencial", "abrir_contas_e_acesso", "usar_credencial_existente", "continuar"],
 "reutilizavel_de": [{"account_id": "…", "app_id": "…", "app_nome": "…"}]}
```

- `host`: `null` = conta do app inteiro; preenchido quando o comando aponta um site (apps de navegador). O painel o repassa ao `POST …/accounts/planned`; sem `host` no item, planeja sem `host`.
- `estado`: `sem_conta` (nenhuma linha), `planejada` ou `falha`. Pares em `credencial_preparada` ou depois não entram na lista.
- Os pares são (persona escolhida: `profile_ids` ou a persona dos aparelhos) × (app de CONTA que o comando cita). Sem persona escolhida (o Automático escolhe depois) a lista vem vazia e `notes` manda preparar a credencial de cada uma em Contas e acesso.
- `acoes` (nunca texto livre; o painel executa cada uma pelos ids): `preparar_credencial` (planejar se for `sem_conta`, com `desired_handle` sugerido, e `credential/prepare`), `abrir_contas_e_acesso` (navegar à guia da persona), `usar_credencial_existente` (só quando `reutilizavel_de` não está vazio; `credential/prepare` com `modo: "reutilizar"`, `clonar_de` e a escolha expressa), `continuar` (refazer o refinar com o mesmo corpo: a pendência é reavaliada).
- `ready` é `false` enquanto houver item na lista. **Senha nunca vira pergunta:** pergunta do modelo que a triagem marca como sensível é descartada; se o app não tem credencial pronta, aparece como item de `acoes_de_conta`. A pergunta "a senha já está guardada ou será definida?" deixa de existir.
- Retomada: o `successor` e o refinar com `run_id` seguem preservando alvos, intenção, dados e etapas concluídas; credencial preparada não pede recomeçar.

### 4. Cenários do brief

A) conta existente (`confirmada`): nada na lista; o plano autentica. B) conta nova: `sem_conta` e `preparar_credencial` direto na persona. C) credencial indisponível: o item acima em vez de pergunta ao modelo. D) cadastro externo parcial: o estado (`aguardando_*` ou `falha`) fica na conta e `retomar` volta à etapa certa.

### 5. Consumidores e dados do plano

- Conta com `provisioning.state` diferente de `confirmada`: fora de `orquestrador._candidatas` e `_impossiveis`; `session/connect`, `verify` e `logout` respondem 409 `conta_nao_confirmada`; os dados ao plano trazem só `conta_<app>_usuario` (o `desired_handle`) e `conta_<app>_senha` (sigiloso, `type_secret`), para o plano que preenche o cadastro.
- A conta da ponte igfarm (ADR-088) nasce `confirmada`, com `desired_handle` = sugestão, `handle` = @ confirmado e `evidence {kind: "igfarm", ref: igfarm_account_id}`.

### 6. Compatibilidade e prova

- Aditivo: cliente antigo ignora `provisioning` e `acoes_de_conta`. Central anterior: sem `provisioning` (o painel trata como `confirmada`) e sem `acoes_de_conta` (lista vazia).
- Prova: `not_run` até o código (31.281) e a validação (31.284, provedor SIMULADO; nunca conta real na Microsoft).

## Adendo v1.133 (09/10/2026; número reservado pelo usuário com a migração 132) — ponte android ⇄ igfarm

Três rotas novas em `/api/instagram` e a migração 132 (`persona_reservas`, `caixas_email`, `contas_igfarm`). Detalhe do
fluxo e do e-mail: [`email-do-parque.md`](email-do-parque.md).

- **`POST /api/instagram/contas`, campo `egresso`** (aditivo, sem mudar o que já existia): lista com um item por aparelho
  vinculado à persona, `{instance_id, estado, motivo}`, com `estado` em `atribuido` (o perfil `igfarm-{account_id}` foi pedido
  agora), `ja_atribuido` (o aparelho já o pedia; repetir o POST não reatribui nem rebaixa `exigida_com_bloqueio`) ou
  `pendente_confirmacao` (o aparelho tem conta real de OUTRA persona: só a pessoa confirma; vai também no evento
  `identity.egresso.pendente`). Aparelho que só tem a conta da própria persona é confirmado pela ponte. Lista vazia: sem
  vínculo ou conta sem proxy.

- **`GET /api/instagram/personas-pendentes`**: query `dominio` (obrigatório, allowlist `EMAIL_ALLOWLIST_DOMINIOS`, senão
  422 `dominio_nao_permitido`), `limite` (1–50, padrão 10), `locale`, `com_imagem` (padrão `true`), `reservar` (padrão
  `false`). Devolve lista de `{persona_id, nome, primeiro_nome, sobrenome, nome_exibicao, birth_date, genero, biografia,
  visual, resumo, email_sugerido, username_sugerido, imagem_perfil, imagem_pendente}`.
  - A sugestão (e-mail + @ por IA) é persistente por persona. A imagem só é gerada com `reservar=true`; sem reserva,
    `imagem_perfil` é a existente ou `null` com `imagem_pendente: true`. A URL é `/api/personas/{id}/images/{image_id}`.
  - Erros: `ai_unavailable`/`ai_budget`/`ai_error` (503), `image_not_configured` (409).
- **`POST /api/instagram/contas`**: corpo `{persona_id, dominio, email, email_senha, instagram_username, instagram_senha,
  igfarm_account_id, criada_em}` (`extra="forbid"`; as duas senhas são `SecretStr`). 201 na primeira vez, 200 com
  `idempotente: true` na repetição (chave `persona_id` + `instagram_username`). Resposta `{persona_id, account_id,
  igfarm_account_id, email, instagram_username, criada_em, registrada_em, senhas: "••••", criada, idempotente}`: a senha
  nunca volta. Erros: `not_found` (404), `dominio_nao_permitido`/`dominio_divergente`/`email_invalido` (422),
  `duplicate_username`, `conta_retirada`, `email_em_uso` (409).
- **`GET /api/instagram/contas/{id}/codigo`**: `{codigo, recebido_em, remetente}`; `sem_codigo` (404), `email_indisponivel`
  e `remetente_nao_configurado` (503).
- **Eventos:** `identity.persona.reservada` e `identity.conta.registrada`, sem senhas.
- **Transporte:** a API 2 recebe senhas; fora do loopback exige `API_TOKEN` e TLS.
- **Compatibilidade:** rotas e tabelas novas, nada muda nas existentes. `AIProvider` ganhou `generate_text` (os dublês de
  teste foram atualizados).
- **Prova:** `simulated` (`backend/tests/test_personas_pendentes_api.py`, `test_contas_igfarm_api.py`,
  `test_email_do_parque.py`, `test_migracao_132.py`, `test_generate_text.py`). `real`: ver `CHANGELOG.md` (09/10/2026).

## Adendo v1.130 (10/10/2026; número da orquestradora; item 31.273, ADR-084) — a etapa descoberta por exploração

Sem rota nova. Campos aditivos; a forma do resto não muda.

- **`steps[].exploratoria`** (GET do run/objetivo): `boolean`, `false` por padrão. `true` = a etapa foi criada por EXPLORAÇÃO,
  porque o catálogo do app não cobria o pedido (migração 130, `steps.exploratoria`). Só proveniência.
- **`PlanStep.exploratoria`** (o plano em `runs.plan`): fora da serialização quando falso, como `opcional`; os planos já
  gravados e o hash das etapas não mudam.
- **`nasceu_de_exploracao`** na linha do Livro (`GET /api/aprendizado` e o item aberto): `boolean`, presente em toda
  linha. `true` só na receita cuja etapa de origem é exploratória; é o selo "nasceu de exploração" da lista e do dossiê.
- **`ai.descobertas_sem_uso_dias`** (config, lido ao vivo por `PUT /api/settings`): inteiro, padrão 90, `0` desliga o
  piso. A receita descoberta sem uso (ou sem nascer) há mais dias que isso deixa de ser oferecida ao planejador; a receita
  não é apagada.
- **Prompt do planejador:** a etapa descoberta e comprovada vai num bloco `<etapas_descobertas>` SEPARADO do
  `<etapas_ensinadas>` (o planejador sabe que nenhuma pessoa a demonstrou), só com nome, app e parâmetros. A ensinada por
  pessoa vence a descoberta de mesmo nome. A trilha do plano diz "etapa DESCOBERTA pela IA numa exploração (31.273;
  ninguém a demonstrou)".
- **`limits.exploracao_ligada`, `exploracao_max_acoes`, `exploracao_max_chamadas_ia`, `exploracao_max_usd`,
  `exploracao_max_por_dia`** (config, lidos ao vivo por `PUT /api/settings`): padrões `true`, 25, 30, 0,60 e 5. Com
  `exploracao_ligada: false` o pedido fora do catálogo é recusado como antes (31.33). **31.331:** `exploracao_max_usd` conta só as
  chamadas das etapas exploratórias (o planejamento não consome o teto) e a conferência olha a PRÓXIMA chamada (gasto + média das já
  feitas); a etapa exploratória não sofre o orçamento por etapa do histórico (`ai.step_budget`), só o teto próprio; o aviso
  `exploracao.efeito_liberado` sai uma vez por execução, alvo e chave.
- **Exploração no planejamento:** o plano do pedido fora do catálogo deixa de vir sem etapas: traz uma etapa por pedido,
  com `exploratoria: true` e a chave `explorar_<verbo>_<objeto…>`. Evento novo `exploracao.iniciada`
  (`data: {etapas, reaproveitadas, tetos {acoes, chamadas_ia, usd}}`). `plan.refused` ganha o motivo
  `teto_de_exploracao_por_dia` (`data: {app_id, feitas, teto}`; conta só a exploração nova, com IA, e `exploracao.iniciada`
  leva `app_ids` com o app do pedido sem receita descoberta); o pedido de efeito segue `sem_acao_do_catalogo`.
- **`GET` do relatório do aprendizado (`saude.exploracoes`):** `{por_conducao: {<condução>: n}, pct_sem_ia: number|null}`,
  das etapas exploratórias que terminaram na janela; `pct_sem_ia` = parte conduzida por receita ou atalho.
- **Acréscimo do 31.298 (pedido misto e aviso):** o planejador com catálogo agora devolve, no pedido misto, as ações do
  catálogo em `steps` E o que falta em `fora_do_catalogo` (os prompts de sistema `PLANNER_CAPABILITY_SYSTEM`,
  `PLANNER_MULTIAPP_SYSTEM` e o curto mudaram de propósito); o serviço junta a exploração depois da última etapa do catálogo
  (a primeira etapa de exploração depende dela), e a recusa continua substituindo o plano inteiro. Evento novo
  `exploracao.concluida` (`data: {run_id, step_id, resultado: concluida|falhou|cancelada|parou_no_teto}`), uma vez por etapa
  exploratória; `exploracao.iniciada` ganha `run_id` em `data`. Os dois viram aviso no Telegram, na hora (nível 3, fora da
  janela da rotina): o início só quando há exploração com IA (`app_ids` não vazio), o fim com custo e chamadas lidos do banco.
- **Prova:** `simulated` (`backend/tests/test_etapas_descobertas.py`, `test_exploracao_fora_do_catalogo.py`,
  `test_migracao_130.py`, `test_learning_backlog.py`). `real`: `not_run` (a exploração real no Outlook depende do sim do
  dono para o custo de API).

## Adendo v1.131 (10/10/2026; número da orquestradora; item 31.269) — o evento `profile.policy_group`

Sem rota nova. `PATCH /api/instagram/profiles/{id}` com `policy_group_id` que MUDA o grupo de política da persona emite o
evento `profile.policy_group` (nível `info`): `data: {profile_id, anterior, novo, autor}`, com os ids dos dois grupos
(`null` = sem grupo) e o autor (`operador_atual()` ou `painel`). Nunca o nome do grupo, o @ nem o nome da pessoa na
mensagem ou nos dados. Sem mudança de fato (mesmo grupo) ou sem o campo no corpo, nenhum evento. O
`scripts/grupo-liberado-todas.py` troca o grupo por esse mesmo PATCH, então gera o mesmo evento, sem código novo.
**Prova:** `simulated` (`backend/tests/test_evento_grupo_de_politica.py`). `real`: `not_run` (a troca de grupo pela tela
depois do deploy).
## Adendo v1.134 (10/10/2026; item 31.291) — a saída esperada de um perfil só muda pelo app

Uma rota nova em `/api/network` e nenhuma tabela nova. Motivo: no android-05 o `egress_esperado` do perfil
`igfarm-{conta}` foi regravado FORA do app (o IP da criação virou o IP medido), sem evento, e a comparação saída
medida × esperada deixou de provar algo. Antes, nenhum código do app editava um perfil.

- **`PUT /api/network/profiles/{id}`**: corpo `{egress_esperado?, egress_esperado_ipv6?, motivo?}` (`extra="forbid"`).
  Campo omitido fica como está; `null` tira a saída esperada daquela família; pelo menos um dos dois campos de IP é
  obrigatório (422 `ValidationError`). Resposta: o `NetworkProfileDTO` (sem segredo). Nome, endpoint, protocolo e segredo
  não mudam por aqui.
  - Validação igual à do cadastro (`_saida_esperada_valida`): IP público da família certa, em texto. Recusa: 422
    `invalid_egress`.
  - **409 `egress_esperado_protegido`**: o perfil é `igfarm-{account_id}`, a conta existe em `contas_igfarm` e algum
    aparelho o pede. O corpo traz `message` (por que e o que fazer), `profile_id`, `account_id`, `in_use`,
    `motivo_obrigatorio: true`, `antes` e `depois`. Repetir o PUT com `motivo` (até 300 caracteres, sem segredo: a
    redação por formato o recusa) aplica a troca.
  - 404 `not_found`; valor igual ao atual: 200 sem gravar nem emitir.
- **Evento** `network.updated`, `acao: "perfil_atualizado"`: `profile_id`, `profile_name`, `antes` e `depois`
  (`{egress_esperado, egress_esperado_ipv6}`), `motivo`, `quem`, `in_use`, `conta_igfarm`. Nunca segredo nem os outros
  `params` do perfil.
- **Não faz:** não reavalia a rede do aparelho; a próxima medição (`POST …/verify`) compara com o valor novo.
- **Compatibilidade:** aditivo. O `POST /api/network/profiles` e o `DELETE` seguem como estavam.
- **Prova:** `simulated` (`backend/tests/test_rede_saida_esperada_edicao.py`, 7 testes). `real`: `not_run`.

## Adendo v1.135 (10/10/2026; número da orquestradora; item 31.305) — `etapas_exploratorias` no resumo da execução

Campo aditivo no `RunSummary`, em `GET /api/runs` (cada item de `runs`), `GET /api/runs/{id}` (herdado pelo `RunDetail`) e em todo lugar
que devolve o resumo: `etapas_exploratorias` (number, inteiro >= 0, **sempre presente**, 0 na maioria). É a contagem das etapas
da execução que nasceram de exploração (`steps.exploratoria`, 31.273: o catálogo não cobria o pedido, ADR-084). Na lista vem de
UMA consulta para a página inteira, para o painel filtrar e contar "descoberta pela IA" nas Execuções sem ler cada execução (31.306).
Sem rota nova, sem migração. **Prova:** `simulated` (`backend/tests/test_run_etapas_exploratorias.py`, rota HTTP de verdade).

## Adendo v1.136 (10/10/2026; número da orquestradora; item 31.302) — a releitura periódica da sessão da conta âncora

Sem rota nova. Um laço do central relê, a cada N horas e SEM IA, a tela da conta âncora de cada persona com sessão
`session_ready` num aparelho ligado e livre (`ensure_session(observe_only=True)`: não digita, não autentica, não liga aparelho).
Chaves de `config.yaml` (bloco `contas`, **desligado de fábrica**): `verificacao_periodica_h` (0 desliga; padrão 0),
`verificacao_periodica_cpu_max_percent` (50), `verificacao_periodica_tique_s` (300). Só vale na subida do central.

- **Comando:** cada releitura é o verbo `session.verify` com `requested_by: verificacao-periodica`, pelo mesmo despacho da rota
  "Verificar conta" (aparece no histórico de comandos do aparelho). O desafio visto na tela cai no caminho de sempre do motor de
  sessão (ADR-068): a persona vai a `blocked`, o aparelho a quarentena e `session.needs_person` avisa o dono.
- **Evento novo `session.verificacao_periodica`** (nível `info`; `warn` quando a sessão mudou ou a releitura quebrou):
  `data: {profile_id, account_id, instance_id, resultado, motivo?, anterior?, status?, perfil?}`. `resultado`:
  `verificada` (segue pronta), `mudou` (`status` traz o novo; `perfil` traz o status da persona, `blocked` quando o desafio a
  bloqueou), `erro` (a releitura não terminou; só uma nova tentativa depois de meia janela) ou `pulada` (`motivo` fechado:
  `aparelho_fora_do_ar`, `controle_manual`, `aparelho_ocupado`, `quarentena`, `pausa_de_reparo`, `host_carregado`,
  `worker_em_manutencao`, `portao_da_sessao`, `sem_provedor_de_sessao`, `nao_e_ancora`, `tentada_ha_pouco`). Só ids e códigos:
  nunca o @, o nome da pessoa nem texto da tela. O pulo vira evento só quando o motivo MUDA para aquela conta e aparelho.
  **Não entra nos avisos do Telegram**: `session.needs_person` já avisa o dono quando há o que fazer.
- **Relatório:** `GET /api/learning/falhas` (e o markdown) ganha `saude.verificacoes_de_sessao`, um mapa `{resultado: n}` da
  janela (mais `bloqueou`, as releituras que acharam a persona bloqueada). Campo aditivo; vazio com o recurso desligado.
- **Limites, por desenho:** uma conta por volta e uma releitura por vez no parque; a mais antiga (ou sem leitura) primeiro. Não
  há sinal dentro do processo para um funil ou uma suíte rodando por fora: o que protege é a CPU do host (aparelho da máquina do
  central: a amostra do gerente de aparelhos, a mesma do reparo e do boot; aparelho de worker: a última batida dele; sem
  medição recente, pula), a pausa de reparo do dono e o recurso desligado de fábrica.
- **Prova:** `simulated` (`backend/tests/test_verificacao_periodica_de_sessao.py`: 21 casos, com o motor de sessão de verdade
  sobre o Instagram de mentira para a cadeia do desafio). `real`: `not_run` (nada liga sozinho no deploy; ligar no central é
  decisão de config).

## Adendo v1.137 (10/10/2026; número concedido pela orquestradora; item 31.310, ADR-087) — o cadastro guiado da conta planejada

Fecha o ciclo da conta planejada (adendo v1.132): a plataforma GUIA o cadastro no app, lê o código de confirmação da caixa de e-mail da
própria conta e comprova a conta pela sessão observada. Determinístico (sem IA), uma conta por vez, e sempre devolve a pessoa nas telas
que só ela resolve (CAPTCHA, "confirme que você é humano", telefone). Aditivo: nada que o painel usa hoje muda.

- **Rota nova:** `POST /api/instagram/profiles/{profile_id}/accounts/{account_id}/provisioning/signup` → **202** com o comando
  (`verb: session.cadastrar`, `requested_by` do operador, `instance_id`; aparece em `GET /api/commands`). Corpo opcional
  `{ "instance_id": "android-07" }` (o aparelho vinculado da persona por padrão). Erros: **404** conta inexistente; **409**
  `estado_inesperado` (`details.estado_atual`; só parte de `credencial_preparada`, de `aguardando_cadastro_externo` e de
  `aguardando_verificacao`; de `falha`, `retomar` primeiro), **409** `sem_usuario_desejado`, **409** `sem_conhecimento_de_cadastro` (o app não
  declara `cadastro.yaml`), **409** `sem_caixa_de_email` (o app declara passo de código por e-mail e a conta não tem caixa),
  **409** `sem_credencial` / `sem_consentimento` (as de `iniciar_cadastro`), **409** `sem_nascimento` / `persona_menor_de_idade` (31.324,
  aditivos: o app declara campo de data de nascimento e a persona não tem `birth_date` válido, ou tem menos de 18 anos; a conferência é
  ANTES de tocar no aparelho e a mensagem nunca traz a data; adendo v1.144), **409** `cadastro_em_andamento` (outro
  `session.cadastrar` aberto no parque: uma conta por vez), **409** `aparelho_ocupado` (trabalho, controle manual ou aparelho
  fora do ar; nunca liga o aparelho).
- **O que o comando faz** (cada passo é uma transição do ciclo do v1.132 com `estado_esperado`; reiniciar no meio retoma pelo
  estado gravado e pela tela, e NUNCA envia o formulário duas vezes):
  1. `credencial_preparada` → `iniciar_cadastro`. Preenche o formulário declarado: o @ desejado, o nome e o e-mail como texto
     comum, conferindo o que ficou no campo; a senha só pelo canal sensível, direto do cofre.
  2. Toca em enviar UMA vez; ao sair da tela do formulário sem erro, `enviado` (`aguardando_verificacao`).
  3. Se o app pede o código por e-mail: lê a caixa da conta, só um e-mail recebido DEPOIS do envio, digita pelo canal sensível e
     toca em continuar uma vez.
  4. Lê a conta na tela de sucesso declarada; com o @ igual ao desejado grava a sessão observada e faz `confirmar` com evidência
     `sessao` (`ref` = o aparelho). Qualquer outra coisa não confirma.
- **Paradas** (a pessoa assume; o comando termina `failed`; sem segunda tentativa, sem solver, sem proxy): o ciclo vai a `falha`
  pelo evento `falhar`, com `resume_state` e o motivo fechado em `provisioning.detail`, e `provisioning.proximo_passo` o repete
  como código. **Enviado é enviado:** o `enviado` (`aguardando_verificacao`) é gravado ANTES do toque em "Cadastrar", não depois: se
  o processo cair ou a tarefa for cancelada com o toque já feito, a conta já está em `aguardando_verificacao` e nenhuma execução nova
  reabre o formulário (a gravação que falha impede o toque). Toda parada depois do envio guarda `resume_state: aguardando_verificacao`;
  a única exceção é `usuario_indisponivel` (o provedor recusou o @ e nada foi criado), que guarda `aguardando_cadastro_externo` por
  um parâmetro interno do serviço, nunca da rota. Antes de tocar em QUALQUER campo, na senha e no código o motor relê a tela e para em
  `desafio` (ou `app_fora_do_ar`, se outro app passou para a frente): nada é digitado às cegas.
- **Campo novo (aditivo) `ProvisioningInfo.proximo_passo`:** `null` ou um de `aguardando_pessoa:captcha`,
  `aguardando_pessoa:desafio` ("confirme que você é humano"; a conta pode estar a caminho de ser perdida: nada toca nela),
  `aguardando_pessoa:telefone`, `aguardando_pessoa:usuario_indisponivel` (o @ desejado foi recusado pelo provedor: edite o @ e
  retome), `aguardando_pessoa:tela_desconhecida`, `aguardando_pessoa:codigo_nao_chegou`, `aguardando_pessoa:conta_nao_lida`,
  `aguardando_pessoa:app_fora_do_ar`, `aguardando_pessoa:falha_interna` (erro nosso, como cofre ou caixa de e-mail; o log e o evento
  trazem só o nome do tipo do erro, nunca a mensagem). Derivado de `state == falha` e do `detail`; nunca guarda texto livre, o @, a senha nem o código.
  Para retomar: `POST …/provisioning` com `evento: retomar` e depois `…/provisioning/signup` de novo (o formulário recomeça do
  início; a tela decide, não um campo lembrado).
- **Evento:** o `identity.conta.provisionamento` de sempre (v1.132), mais `passo` (código fechado do passo do cadastro) nas
  transições que o comando faz. Nunca o @, a senha, o código nem texto da tela. Mais um evento `identity.cadastro` (`resultado`:
  `confirmada` | `parada`, `motivo` fechado, `instance_id`) para o aviso do dono.
- **Código do e-mail:** só um e-mail recebido DEPOIS do envio do formulário (ou, na retomada, depois da última mudança de estado da
  conta); o código mais velho que a caixa ainda guarda nunca serve. Uma tentativa por cadastro: o código recusado, vencido ou ausente
  devolve a conta à pessoa (`aguardando_pessoa:codigo_nao_chegou`), sem segundo código nem reenvio.
- **Conhecimento do app:** `app/conhecimento/apps/<pacote>/cadastro.yaml`, ao lado do `sessao.yaml` (ADR-052), validado na carga:
  telas por sinal, campos (o @ desejado, nome, e-mail, senha), botões, tela do código, tela de sucesso e telas de parada. O motor
  não conhece app nenhum. Sem `cadastro.yaml`, 409 `sem_conhecimento_de_cadastro`; nenhum app real o declara ainda.
- **Sem migração.** A caixa da conta é a de `caixas_email` (migração 132). Verbo novo `session.cadastrar` em `APP_COMMAND_VERBS`.
- **Prova:** `simulated` (app de teste com `cadastro.yaml` próprio, aparelho e caixa de e-mail falsos). `real`: `not_run`; criar uma
  conta de verdade num provedor depende de autorização do dono em chat e das decisões D1 (de onde vem a caixa de e-mail da conta
  planejada) e D2 (primeiro app).

## Adendo v1.138 (10/10/2026; número da orquestradora; item 31.312) — a exploração que parou vira pedido de ensino

Sem rota nova e sem migração. A etapa exploratória (31.273, ADR-084) que termina `failed`/`uncertain` (inclusive `parou_no_teto`) já era
"ensinável" pelo `POST /api/training/from-run` (31.111); agora o ensino lê que ela veio de exploração:

- `GET /api/runs/{run_id}/steps/{step_id}/ensino-sugerido`: para a etapa exploratória a resposta traz `intent` =
  `Ensinar à IA como fazer: <frase da chave>` (a frase é o vocabulário fechado da chave `explorar_<verbo>_<objeto…>`, **nunca o
  pedido**, que pode ter um nome e viajaria na intenção da sessão), `pergunta` = "A IA explorou e (parou no teto da exploração) sem
  chegar lá. Mostre, a partir desta tela, o caminho toque a toque: ensinado uma vez, ele serve a todas as personas.", `rotulo` e
  `causa` do diagnóstico (quando houver) e os campos novos `exploracao: true` e `parou_no_teto` (boolean). Etapa que não é de
  exploração: a resposta é a de sempre, sem esses campos.
- `POST /api/training/from-run`: sem `intent` escrito pela pessoa, a sessão de uma etapa exploratória nasce com a intenção acima
  (a falha comum segue «Corrigir a etapa «…»»).
- `origin.exploracao: true` na sessão de ensino (em `POST /training/from-run`, `GET /training/{id}` e `GET /training`), só quando
  verdadeiro: o painel rotula e abre o formulário já na tela e no app onde a IA parou (o aparelho e a execução são os da etapa).
  O pedido de origem continua visível no contexto da falha (`origin.context.esperado.description`, com o dado da persona mascarado).
- O aviso do Telegram `exploracao.concluida` com `resultado` `parou_no_teto` ou `falhou` ganha a linha "Dá para ensinar o caminho na
  execução (Ensinar a corrigir)…".

**Prova:** `simulated` (`backend/tests/test_ensino_da_exploracao_que_parou.py`, 6 casos). `real`: `not_run` (o ensino de uma
exploração real e o painel, que é do Portal).

## Adendo v1.139 (10/10/2026; número da orquestradora; item 31.314, migração 134) — persona de TESTE

- **Campo aditivo `teste: bool`** (padrão `false`) em `PersonaDTO` (= `InstagramProfileDTO`), portanto em `GET /api/personas`,
  `GET /api/personas/{id}`, e nas respostas de `POST /api/personas` e `PATCH /api/personas/{id}`. **Entrada:** `PersonaCreate.teste`
  (padrão `false`) e `PersonaPatch.teste` (`null` ou ausente = não mexer; qualquer outra coisa que não seja booleano é 422). Sem rota
  nova. A marca vive em `instagram_profiles.teste` (migração 134, aditiva, `DEFAULT 0`); a migração marca por id a persona
  `ig-d3n4tia1rHELrY10` ("TESTE Portal 31.283"), e as demais se marcam por `PATCH`.
- **O que a marca faz** (a persona de teste existe para provar o produto, nunca para trabalhar sozinha):
  - **Fora do automático:** a sugestão automática de alvos (`POST /api/runs/targets/suggest`, 31.274; a persona nunca entra em
    `escolhidas`) e a distribuição por app (`distribuir`: o aparelho vinculado SÓ a ela não é candidato de um app que exige conta).
  - **Fora do em massa:** o pool elegível das operações em lote (`GET /api/operacoes/elegiveis`) não a lista, e criar uma operação
    com ela como alvo para na etapa `persona` com o motivo "persona de teste: fora das operações em lote (31.314)". A contagem
    `accounts` de `GET /api/apps-overview` não soma as contas dela.
  - **Fora dos avisos ao dono:** o evento (`session.needs_person`, `objective.updated`, `approval.pending`, `run.updated`) cujos
    perfis são SÓ de teste não vira aviso no Telegram, e a pendência dela (aprovação ou pergunta) não entra no espelho do Trello. A
    execução que mistura persona de verdade e de teste segue avisando.
  - **O que NÃO muda:** citada pelo nome ou pelo id, num comando ou numa execução avulsa, ela serve como qualquer outra (o teste é
    feito de propósito, por uma pessoa). O painel (Portal, 31.315) faz o selo e o filtro a partir do campo.

**Prova:** `simulated` (`backend/tests/test_persona_de_teste.py`, 11 casos). `real`: `not_run`; PostgreSQL da 134: `not_run`.

## Adendo v1.140 (10/10/2026; número tomado da fila de `.claude/reservas.md`; item 31.297, ADR-091) — a exploração de EFEITO pela política do perfil

- **Interruptor novo `limits.exploracao_efeito_ligada`** (booleano, padrão `false`), lido ao vivo por `GET`/`PUT /api/settings` como os outros
  `exploracao_*`. Desligado, o pedido de efeito segue recusado (`plan.refused`, `sem_acao_do_catalogo`) como no ADR-091 original. Ligado, o
  pedido vira UMA etapa exploratória com `side_effect: true`, chave `explorar_<verbo>_<objeto…>` (vocabulário fechado, sinônimos num verbo só:
  mandar/encaminhar → `enviar`, postar → `publicar`, excluir/remover → `apagar`, mudar/editar → `alterar`…) e a ordem de não digitar senha.
- **A política do perfil e do grupo ganha duas chaves** em `capabilities` de `GET`/`PUT /api/instagram/profiles/{id}/policy` e dos grupos:
  `explorar_efeito` (vale para toda exploração de efeito do app) e `explorar_<verbo de efeito>[_<objeto>…]` (a do pedido, que vence a genérica).
  Valores: os de sempre (`autonomous`, `approval_required`, `manual_only`, `disabled`). Padrão sem escolha: `approval_required`. Qualquer outra
  chave de exploração (leitura, objeto fora do vocabulário, o sufixo de letras de um pedido sem objeto) é recusada com `unknown_capability`
  (400). Afrouxar abaixo do padrão deixa o aviso de sempre (ação de risco alto). Na resposta do `GET` as chaves aparecem em `capabilities`,
  `defaults`, `origin`, `own` e `group` **só quando o perfil ou o grupo as escolheu**; sem escolha a resposta não muda.
- **A porta 13.2 julga a etapa** (`GET /api/runs/{id}/porta` incluso): uma ação SINTÉTICA (risco alto, sem texto gerado) faz a etapa passar
  por `policies.check` e pela aprovação como qualquer ação do catálogo. Sem perfil vinculado não passa. O teto `preparar` exige o sim. A etapa
  de efeito escrita pelo modelo, sem a marca do sistema (campo `exploratoria` e chave `explorar_…`), segue recusada pela 13.2.
- **Eventos:** `exploracao.iniciada` e `exploracao.concluida` ganham `data.com_efeito` (booleano); evento novo `exploracao.efeito_liberado`
  (`data: {run_id, step_id, profile_id, chave, politica, origem: own|group|default, aprovada, dispensada_pelo_grupo}`), emitido quando a porta
  libera (política autônoma, grupo sem aprovação ou o sim do dono). Vira aviso no Telegram, na hora, só com ids e códigos fechados.
- **Nunca explora, nem com o interruptor ligado:** o pedido que mexe em credencial ou sessão, em QUALQUER posição e em qualquer forma (`entrar`/`entre`, `logar`/`login`/`logout`, `sair`/`saia`, `autenticar`/`autentique`, `cadastrar`/`cadastre`/`cadastro`, `registrar`, `inscrever`, `conectar`/`desconectar`, e as palavras `senha`, `password`, `credencial`, `token`, `2FA`/`MFA`/`OTP`; `entre` só como imperativo (logo antes de conta/app/perfil/senha/e-mail/usuário) e `código` só com verificação/confirmação/acesso/segurança/SMS/e-mail/enviado (nunca com cupom, barras, postal, rastreio…: "escolha entre as fotos" e "ler o código de barras" seguem leitura); mais "criar/adicionar/abrir/trocar/alternar conta"; lista única em `app/contracts/credencial_e_sessao.py`) segue recusado como antes (`sem_acao_do_catalogo`): credencial e sessão têm mecanismo próprio (ADR-040, ADR-087, `sessao.yaml`). Esses verbos também não têm chave de política. "Caixa de entrada" e "configurações da conta" seguem leitura.
- **Fora da sintética (decisão do dono, não bug):** a ação sintética não tem balde de limite nem contraparte, então o limite diário, a coordenação de frota (ADR-083) e a regra "pessoa real sem conversa passa por aprovação" (30.62) NÃO se aplicam a ela; com `explorar_efeito: autonomous` o dono libera o efeito sem teto por dia. O padrão é aprovação. Dentro de cada camada a chave do pedido vence a genérica; a camada do perfil vence a do grupo inteira (a genérica do perfil vence a específica do grupo).
- A chave do efeito leva palavras inteiras (no máximo 40 caracteres, sem cortar objeto no meio).
- **O efeito descoberto nunca é oferecido a outra execução** (`molde_da_exploracao` devolve `None` para `side_effect`): a política julga de novo
  cada vez.

**Prova:** `simulated` (`backend/tests/test_exploracao_de_efeito.py`, 99 casos). `real`: `not_run` (efeito numa conta real; o dono liga o
interruptor e escolhe a política).

## Adendo v1.141 (10/10/2026; número da fila de `.claude/reservas.md`; item 31.325, ADR-091) — a política da exploração de efeito por VERBO

Muda só o que o v1.140 deixou para a exploração de efeito (sem rota nova, sem campo novo):

- **Regra do dono:** "executar sem pedir aprovação por request, salvo ação destrutiva". Lista única dos verbos **destrutivos** (canônicos da chave):
  `apagar` (excluir, deletar, remover, esvaziar, limpar), `comprar` (pagar, assinar), `transferir`, `encerrar`, `desinstalar`, `resetar`
  (`app/contracts/efeito_destrutivo.py`). `desconectar` e os de credencial seguem fora da exploração (v1.140).
- **Chaves de política em `capabilities`** (perfil e grupo): `explorar_efeito` (genérica), **`explorar_<verbo>`** (novo, o verbo canônico) e
  `explorar_<verbo>_<objeto…>` (a do pedido). Ordem da busca: pedido → verbo → genérica; a camada do perfil vence a do grupo.
- **Padrão por verbo** (o que vale sem escolha do dono): verbo comum = `autonomous` (roda sem pedir o sim; o dono é avisado na hora, `origem: default`);
  verbo destrutivo = `approval_required`, risco alto. **A genérica `explorar_efeito` não alcança o destrutivo**: só `explorar_<verbo>` ou a do pedido o libera.
  `GET .../policy` mostra o padrão certo em `defaults` (`explorar_apagar` = `approval_required`; `explorar_efeito` e os comuns = `autonomous`); o aviso de
  afrouxamento de ação de risco alto sai só para o destrutivo.
- **Texto dos avisos:** o início da exploração diz que a etapa destrutiva pede o sim; `exploracao.efeito_liberado` com `origem: default` diz que o padrão da
  exploração roda sem pedir, salvo destrutiva.

**Prova:** `simulated` (`backend/tests/test_exploracao_politica_por_verbo.py`, `test_exploracao_de_efeito.py`). `real`: `not_run`.

## Adendo v1.142 (10/10/2026; número da orquestradora; item 31.322, migração 136) — tela humana = bloqueio definitivo, com o motivo como dado

Decisão do dono (10/10): a tela humana ("Confirm you're human", subtipo `conta_travada`) é bloqueio DEFINITIVO. A conta sai da plataforma
na hora (ADR-055/068, como antes) e agora o MOTIVO fica registrado como dado. Tudo ADITIVO; nenhuma rota nova.

- **Migração 136:** coluna `contas_retiradas.motivo_do_bloqueio` (texto JSON, anulável). As lápides anteriores ficam com `NULL`.
- **O objeto** (sem segredo e sem o @): `egresso_esperado` (o `ip_criacao` do igfarm), `egresso_medido` (a última saída medida de um
  aparelho com sessão da conta), `egresso_divergente` (booleano; `null` se falta um dos dois), `ips_distintos_desde_criacao` (saídas
  diferentes medidas desde `criada_em_igfarm`), `minutos_ate_o_primeiro_login` (da criação à primeira tentativa) e `trecho_da_tela`
  (evidência já sem o rastro da conta, até 200 caracteres). Campo desconhecido é `null`: nada se inventa.
- **Onde aparece:** no evento `profile.account_retired` (`data.motivo_do_bloqueio`, objeto ou `null`) e no GET da persona
  (`GET /api/personas/{id}` e o perfil do Instagram): campo novo `contas_retiradas: [{app_id, retirada_em, motivo_do_bloqueio}]`, da mais
  recente para a mais antiga, uma entrada por retirada (a âncora grava duas lápides e aparece uma). Sem o @ nem o hash.
- **Rótulo:** o evento `session.needs_person` ganha `data.rotulo` = `"bloqueada"` (detalhe com `conta_travada:` ou a retirada por
  bloqueio) ou `"aguardando"` (o código por e-mail e o desafio comum seguem esperando uma pessoa). O enum `SessionStatus` NÃO muda; a mensagem
  do evento passa a dizer `(<status>, <rótulo>)`. Mostrar o rótulo no painel é do Portal (31.326).
- Falha ao montar o motivo nunca impede a retirada: a lápide fica com `NULL` e o evento com `null`.

**Prova:** `simulated` (`backend/tests/test_motivo_do_bloqueio.py`, 7 casos; catracas, arquitetura e mypy no teto). `real`: a H1 do teste de
bifurcação (10/10 19:02Z, android-07) caiu na tela humana com o egresso casado e foi retirada antes do deploy desta migração; o motivo dela
está só em `.claude/handoffs/ponte-bifurcacao-3-contas.json`.

## Adendo v1.143 (10/10/2026; número da orquestradora; item 31.329) — o login mede o egresso DENTRO da janela e aborta se não casar

Aditivo: nenhuma rota, coluna ou migração nova. A regra vale para aparelho com proxy pedido cujo perfil de rede declara `egress_esperado`
(as contas do igfarm); nos demais o login segue como sempre.

- **Regra.** Antes de digitar a senha (e antes de abrir a tentativa de login, então sem gastar o teto diário da conta), o motor de
  sessão mede a saída do aparelho na hora (sonda de IP como uid 2000). Aborta quando a saída medida difere de `egress_esperado`, quando a
  medição não obtém IP, ou quando a distância entre o fim da medição e a decisão passa de 30 s (`JANELA_DO_LOGIN_S`). Não toca na tela.
- **Desfecho.** `UNCERTAIN` com o motivo `egresso não casou: …` (saída medida e esperada, ou "a medição não obteve IP", ou "ficou N s antes
  do login"). Não grava a sessão, não põe a credencial em `review` e não para o login automático: a próxima chamada mede de novo. O
  `ensure_session` devolve esse motivo em `AuthResult.detail`, que o `session.connect` e a conta mostram.
- **Evento novo `session.egresso_na_janela`** (nível `info` se casou, `warn` se não; `instance_id` no envelope). `data`:
  `{profile_id, account_id, esperado, medido (IPv4 ou null), medido_em (ISO 8601 UTC), distancia_s (número, segundos), casou (booleano),
  medicao_id (número ou null), detalhe (texto, até 300 caracteres)}`. Sem segredo: só IPs públicos de saída. Um evento por tentativa
  de login que chegou à conferência.
- **`network_measurements`.** Cada medição da janela vira uma linha com `method = "sonda de IP na janela do login (uid 2000)"`,
  `egress_ipv4`/`egress_ipv6` medidos e `detail`; sem `per_app`, `leak_blocked`, DNS nem UDP. É só leitura: não muda `device_network` (a
  deriva de uma rede pedida segue sendo achado da convergência). `medicao_id` do evento aponta para esta linha.

**Prova:** `simulated` (`backend/tests/test_egresso_na_janela.py`, 8 casos). `real`: `not_run`; a regra nasceu da ressalva da H2 de 10/10
(login 10 min depois da última medição, sticky girou até a seguinte).

## Adendo v1.144 (10/10/2026; número da orquestradora; item 31.324 G1) — o cadastro guiado confere a data de nascimento da persona

Aditivo; nenhuma rota nova, nenhuma migração. O cadastro guiado (v1.137, `POST …/provisioning/signup`) passa a poder digitar a data de
nascimento da persona quando o `cadastro.yaml` do app a pede, e a recusar ANTES de tocar no aparelho quando não pode.

- **Dois 409 novos na rota** (somam-se aos do v1.137), conferidos só quando o `cadastro.yaml` do app declara algum campo `nascimento_*`:
  - `sem_nascimento`: a persona não tem `birth_date` válido (`AAAA-MM-DD`, não futuro);
  - `persona_menor_de_idade`: a persona tem menos de 18 anos (`MAIORIDADE`, a mesma regra do resto da identidade).
  A mensagem nunca traz a data nem o ano. Nenhum comando é despachado e o aparelho não é tocado. App que não pede a data não exige nada.
- **Vocabulário do `cadastro.yaml`** (dado do app, validado na carga): `nascimento_dia`, `nascimento_mes` e `nascimento_ano` (números sem zero à
  esquerda; a conferência na tela compara só os dígitos); critérios do alvo `classe`, `abaixo_do_rotulo`, `ordem` e `senha`; telas
  `dispara_codigo` e `antes_do_envio` (o código que o app manda antes de criar a conta); `envia` também em `tocar`. Ver
  `docs/dominios/persona.md` (cadastro guiado).
- **31.334 (L1, 11/10), aditivo:** quando o app declara e-mail ou código por e-mail e a conta planejada não tem caixa, a rota CRIA a caixa do
  parque (catch-all, só uma linha de banco) em vez de recusar; o 409 `sem_caixa_de_email` fica para o parque sem domínio de e-mail permitido.
- **Nada mais muda no contrato:** estados, eventos (`identity.conta.provisionamento`, `identity.cadastro`) e paradas (`Parada`) são os do v1.137.
  A data de nascimento é dado pessoal e não entra em evento, log, evidência nem mensagem.

**Prova:** `simulated` (`backend/tests/test_cadastro_instagram_like.py`, 48 casos, e `test_cadastro_guiado.py`, 58). `real`: `not_run`; nenhum app real
declara o cadastro ainda (o do Instagram espera a captura da igfarm).

## Adendo v1.145 (10/10/2026; número da orquestradora; item 31.333) — `GET /api/instagram/contas/{id}/ciclo`

Aditivo: uma rota de leitura nova na ponte android ⇄ igfarm. Nenhuma coluna, migração, evento nem enum novo.

- **`GET /api/instagram/contas/{id}/ciclo`** (`{id}` é o `account_id` da central ou o `igfarm_account_id`): o que aconteceu com
  a conta que o igfarm criou. Resposta `{account_id, igfarm_account_id, criada_em, registrada_em,
  estado, retirada_em, minutos_ate_o_primeiro_contato, ultimo_desfecho, contatos: [{iniciado_em,
  minutos_desde_a_criacao, desfecho, etapa, detalhe}]}`. `estado` é `ativa` (a conta segue na persona) ou `retirada` (o @ está
  na lápide, 29.23, com `retirada_em`). `contatos` são as tentativas de login no app, em ordem, com os minutos desde a
  criação no igfarm (`criada_em`) e o desfecho do motor de sessão (`session_ready`, `auth_challenge`, `uncertain`,
  `conta_nao_encontrada`, …). Erro: `not_found` (404) para conta que a ponte não registrou.
- **Sem segredo nem identificador:** só ids, horas, minutos e desfechos; nem e-mail, nem senha, nem URL de proxy, nem IP de
  criação, nem @, nem texto de tela; o `detalhe` é o que o motor já grava
  (cortado em 200 caracteres) e traz, quando há, o identificador usado já mascarado (31.332).
- **Por quê:** a bifurcação de 10/10/2026 mostrou conta que nasce, é tocada duas vezes e some; o intervalo "criada → 1º
  contato → desfecho" só existia espalhado em `contas_igfarm`, `authentication_attempts` e `contas_retiradas`.

**Prova:** `simulated` (`backend/tests/test_ciclo_da_conta_igfarm.py`, 5 casos). `real`: `not_run`.

## Adendo v1.146 (10/10/2026; número da orquestradora; item 31.335) — a criação de conta pela API do igfarm foi aposentada

Aditivo na configuração e restritivo em UMA rota; nenhuma coluna, migração nem evento novo. Decisão do dono (10/10/2026): a API do
igfarm deixou de ser a fonte da conta (ela cria `is_active:false` e o login para em 2FA sem contexto); o cadastro é feito NO APP
e o igfarm passa a ser apoio (e-mail, código, SMS, proxy).

- **Config nova `contas.criacao_pela_api_do_igfarm`** (booleano, padrão `false`; `config.example.yaml` traz a chave). Reversível:
  nada se apaga.
- **`GET /api/instagram/personas-pendentes` com a flag desligada:** `409 criacao_pela_api_aposentada`, sem reservar a pessoa, sem
  gravar sugestão e sem gerar foto (a foto é paga). Com a flag ligada, o comportamento é o de antes.
- **Não mudam:** `POST /api/instagram/contas` (registro, consentimento, proxy), `GET /api/instagram/contas/{id}/codigo` e
  `GET /api/instagram/contas/{id}/ciclo` (v1.145). Elas seguem como apoio.

**Prova:** `simulated` (`backend/tests/test_criacao_pela_api_aposentada.py`, 4 casos; os testes de `personas-pendentes` e de
registro ligam a flag). `real`: `not_run`.

## Adendo v1.147 (11/10/2026; número da orquestradora; item 31.336) — `GET /api/instagram/contas/{id}/cabecalhos`

Aditivo: uma rota de leitura nova na ponte android ⇄ igfarm. Nenhuma coluna, migração, evento nem enum novo.

- **`GET /api/instagram/contas/{id}/cabecalhos?horas=48&limite=20`** (`horas` 1–336, `limite` 1–50; `{id}` é o `account_id` da central
  ou o `igfarm_account_id`): só os CABEÇALHOS das mensagens que chegaram à caixa da conta. Resposta `{account_id, horas, total,
  mensagens: [{recebida_em, remetente, assunto, autenticacao, devolucao}]}`, da mais recente para a mais antiga.
  `autenticacao` é `{spf, dkim, dmarc}` → o resultado que o servidor de entrada anotou em `Authentication-Results` (`pass`, `fail`,
  `softfail`, `none`...; chave ausente = não anotado). `devolucao` é `true` para aviso de falha de entrega (`mailer-daemon`/
  `postmaster` ou assunto de devolução).
- **Nunca o corpo, nunca o destinatário:** o adaptador IMAP pede só `BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE
  AUTHENTICATION-RESULTS)]` com `INBOX` em somente leitura; seis dígitos seguidos no assunto viram `######` (podem ser o código).
- **Erros:** `not_found` (404) conta sem caixa registrada; `email_indisponivel` (503) sem IMAP configurado.
- **Por quê:** responde, sem abrir mensagem, se o app mandou e-mail de confirmação à caixa da conta, se alguém devolveu e como o
  servidor de entrada autenticou (31.333/31.336).

**Prova:** `simulated` (`backend/tests/test_cabecalhos_da_caixa.py`, 8 casos). `real`: `not_run` (nenhuma IMAP real nesta entrega).

## Adendo v1.148 (11/10/2026; número da orquestradora; item 31.337) — o proxy sticky da conta PLANEJADA e o egresso medido na janela do cadastro

Aditivo: uma rota nova, um campo novo num evento existente e um código de parada novo. Nenhuma coluna, migração nem enum novo.

- **`POST /api/instagram/profiles/{profile_id}/accounts/{account_id}/proxy`**, corpo `{proxy_url}` (`extra="forbid"`, `SecretStr`: a senha do
  proxy nunca volta, nem em log, evento ou resposta). Cria (idempotente pelo nome) o perfil de rede `igfarm-<account_id>` SEM
  `egress_esperado` e o atribui aos aparelhos da persona (mesma regra de atribuição do registro do igfarm: política `exigida`,
  `pendente_confirmacao` quando o aparelho tem conta real de OUTRA persona). Resposta `{profile_id, account_id, network_profile_id,
  egresso: [{instance_id, estado, motivo}], egress_esperado: null}`. Só para conta que ainda vai se cadastrar (os estados de partida do
  cadastro guiado); depois, `409 estado_inesperado`. Erros: `404` conta inexistente, `422 proxy_invalido`, `503 rede_indisponivel`.
  O igfarm só ENTREGA a URL do proxy; ele não cria a conta.
- **`POST …/provisioning/signup` mede o egresso na janela ANTES do primeiro toque** quando o aparelho tem o proxy planejado DESTA conta
  (`igfarm-<conta>`). Sem `egress_esperado`, a primeira medição com IPv4 público vira o esperado (o IP de criação), gravado pelo
  caminho com rastro (`network.updated`); com ele, vale a regra do login (31.329: casar e estar dentro de 30 s). Se não casar, ou a
  medição não obtiver IP, o cadastro nem começa: nada é tocado, a conta continua no estado de partida e o comando termina `failed`
  com a parada **`egresso_nao_casou`** (evento `identity.cadastro`, `resultado: parada`). Sem o proxy desta conta no aparelho, nada muda.
- **`session.egresso_na_janela`** ganha o campo `fase` em `data` (`"cadastro"` quando vem do cadastro guiado; ausente no login, como antes).

**Prova:** `simulated` (`backend/tests/test_proxy_da_conta_planejada.py`, 8 casos; sonda de IP, app e aparelho falsos). `real`: `not_run`
(depende de o igfarm entregar uma sessão sticky por conta sem criar a conta, pergunta 6 do cartão COG65yCO, e do sim do dono para uma conta real).

## Adendo v1.149 (11/10/2026; número da orquestradora; item 31.341) — `GET /api/instagram/contas/{id}/ciclo` cobre a conta criada no app

Aditivo na resposta da rota do v1.145: nenhum campo some e a conta do igfarm responde igual. Nenhuma coluna, migração nem evento novo.

- **Campos novos:** `origem` (`igfarm` | `app`) e `referencia` (`criacao` | `planejamento`: de onde contam os minutos dos contatos; `planejamento`
  enquanto a conta do app não foi confirmada). `igfarm_account_id`, `criada_em` e `registrada_em` passam a poder ser `null`: na
  conta do app o igfarm não a conhece (`igfarm_account_id: null`), `criada_em` é a CONFIRMAÇÃO do cadastro (`null` até lá) e
  `registrada_em` é o planejamento da conta (`null` quando só restam os rastros).
- **Fonte da conta do app** (sem `contas_igfarm`): a linha da conta (planejamento, confirmação), as tentativas de login
  (`authentication_attempts`), os eventos do cadastro guiado (`identity.cadastro`, que viram um contato com `etapa: "cadastro"` e
  `desfecho` = `confirmada` ou `parada`, `detalhe` = o código fechado da parada) e a retirada (`profile.account_retired`). A conta
  retirada, que some de `profile_accounts`, ainda tem ciclo: `estado: "retirada"` e `retirada_em` vêm do evento.
- **Continua sem segredo nem identificador:** só ids, horas, minutos e desfechos; nenhum @ nem e-mail (o `detalhe` troca qualquer trecho
  com `@` por `<e-mail omitido>`). `404 not_found` só para conta sem nenhum rastro.

**Prova:** `simulated` (`backend/tests/test_ciclo_da_conta_do_app.py`, 5 casos, e os 5 de `test_ciclo_da_conta_igfarm.py` seguem iguais).
`real`: `not_run`.

## Adendo v1.150 (11/10/2026; número da orquestradora; item 31.342) — a criação pelo igfarm foi REABERTA e o consentimento da conta ganhou rota

Decisão do dono (11/10/2026): **quem cria a conta é o igfarm, pela API dele; o android hospeda e o app usa.** O v1.146 (31.335) tinha
aposentado esse caminho e a ponte devolvia `409 criacao_pela_api_aposentada`; isso foi um excesso e está revertido. Aditivo: uma
rota nova, um padrão de configuração que muda e nenhuma coluna, migração nem evento novo. Os acréscimos v1.147 a v1.149 (cabeçalhos, proxy
da conta planejada, ciclo da conta do app) seguem como estão.

**O que existe (ponte android ⇄ igfarm, todas com o token de serviço):**

| Rota | Papel | Estado |
|---|---|---|
| `GET /api/instagram/personas-pendentes` | pessoas sem conta, com sugestão e foto, para o igfarm criar | **reaberta** (v1.146 revogado) |
| `POST /api/instagram/contas` | registra a conta que o igfarm criou (senhas, caixa, marca do igfarm) | inalterada |
| `POST /api/instagram/contas/{id}/consentimento` | consente a credencial da conta que o igfarm criou (ADR-040) | **NOVA** |
| `GET /api/instagram/contas/{id}/codigo` | código do e-mail da conta | inalterada |
| `GET /api/instagram/contas/{id}/ciclo` | ciclo da conta (v1.145, v1.149) | inalterada |
| `GET /api/instagram/contas/{id}/cabecalhos` | cabeçalhos da caixa, sem corpo (v1.147) | inalterada |
| `POST /api/instagram/profiles/{pid}/accounts/{aid}/proxy` | proxy sticky da conta planejada (v1.148) | inalterada |

- **`contas.criacao_pela_api_do_igfarm` volta a `true` por padrão** (`config.example.yaml` também). `false` continua existindo como
  chave para desligar: `GET /api/instagram/personas-pendentes` responde `409 criacao_pela_api_aposentada`, sem reservar, sem sugerir
  e sem gerar foto paga. Registro, consentimento, código, ciclo, cabeçalhos e proxy não dependem dela.
- **Rota de consentimento (nova).** O igfarm chamava `POST /api/credential/consent`, que **nunca existiu** (404/405: a central não
  tem rota `/api/credential/*`; o consentimento é por conta, ADR-040, e ficava na rota do painel
  `POST /api/instagram/profiles/{pid}/accounts/{aid}/credential/consent`, que exige conhecer o `profile_id`). A rota correta para o
  igfarm é **`POST /api/instagram/contas/{conta_id}/consentimento`**, sem corpo:
  - `conta_id` aceita o `account_id` da central ou o `igfarm_account_id` que o igfarm mandou no registro.
  - Só vale para conta registrada pela ponte (`contas_igfarm`): outra conta responde `404 not_found`.
  - **Idempotente.** Conta que já consentiu devolve o consentimento que tem (`ja_consentida: true`) sem regravá-lo. Conta cujo
    consentimento foi limpo é consentida de novo, como `consent_by: "igfarm"` (`ja_consentida: false`). Sem senha guardada: `409 no_credential`.
  - **Resposta `200`:** `{account_id, igfarm_account_id, consent: true, consent_at, consent_by, ja_consentida}`. Nunca traz senha.
  - O registro (`POST /api/instagram/contas`) já guarda a senha COM o consentimento de quem registra; a rota é a confirmação explícita
    (e o reparo), não uma etapa obrigatória depois do registro.
- **Não houve alias `/api/credential/consent`.** Se o igfarm preferir manter o caminho antigo, é uma linha na ponte; o contrato oficial
  é a rota acima.

**O v1.146 fica SUPERADO** quanto à aposentadoria (a flag e o 409 existem, só que desligados por padrão); o texto dele fica como histórico.

**Prova:** `simulated` (`backend/tests/test_criacao_pela_api_reaberta.py`, 7 casos, no lugar de `test_criacao_pela_api_aposentada.py`).
`real`: `not_run`.

## Adendo v1.151 (11/10/2026; número da orquestradora; item 31.344) — toda chamada à rota de consentimento deixa o evento `identity.consentimento_igfarm`

Aditivo: nenhum campo de resposta, rota, coluna ou migração muda. Antes, a chamada idempotente (`ja_consentida`) e a recusada não deixavam
rastro, e o registro (`POST /api/instagram/contas`) já consente, então o igfarm podia chamar `POST /api/instagram/contas/{id}/consentimento`
sem que nada provasse (v1.150).

- **Evento `identity.consentimento_igfarm`, um por chamada,** com a hora do evento (`ts`) e `data`: `conta_id` (o id recebido, cortado em 80),
  `resultado` (`consentida` | `ja_consentida` | `recusada`) e, quando a conta existe, `account_id`, `igfarm_account_id` e `ja_consentida`;
  na recusa, `codigo` (`not_found`, `no_credential`).
- **Sem segredo:** só ids, resultado e código; nunca senha, @ nem e-mail.
- A prova `real` do 31.342 é a primeira linha desse evento vinda do igfarm.

**Prova:** `simulated` (`backend/tests/test_criacao_pela_api_reaberta.py::test_toda_chamada_do_consentimento_deixa_evento_sem_segredo`).
`real`: `not_run`.
