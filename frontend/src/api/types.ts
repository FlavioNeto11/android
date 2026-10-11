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

// awaiting_person (29.93): o trabalho automático acabou e um objetivo espera um gesto da pessoa no aparelho; não é fim.
type RunStatus = 'planning' | 'needs_input' | 'planned' | 'running' | 'paused' | 'cancelling' | 'awaiting_person'
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

/**
 * Saúde da TELA ao vivo, separada da saúde do aparelho (`backend/app/devices/stream.py`). "Desatualizado" cobria
 * cinco situações; aqui cada uma tem nome. `stale` = aparelho online sem frame novo — NUNCA "offline".
 */
type StreamStatus = 'live' | 'stale' | 'capture_error' | 'no_frame' | 'device_offline' | 'device_hibernated'
  | 'worker_offline'
  | 'paused';  // v0.20 (C3) — aparelho online, prévia suspensa por falta de interesse: não é `stale` nem erro

interface StreamInfo {
  status: StreamStatus;
  detail: string;
  last_frame_at: string | null;
  frame_age_s: number | null;
  last_capture_error: string | null;
  last_capture_error_at: string | null;
  consecutive_capture_failures: number;
}

/** Internet DENTRO do aparelho (backend `devices/conectividade.py`). `online` não implica `healthy`. */
export interface ConnectivityInfo {
  state: 'unknown' | 'healthy' | 'degraded' | 'unavailable';
  route: boolean | null;
  dns: boolean | null;
  tcp_443: boolean | null;
  validated: boolean | null;
  checked_at: string | null;
  detail: string;
}

/** Degrau da escada de prontidão (backend `ReadinessInfo`): `online` exige o framework respondendo. */
export interface ReadinessInfo {
  phase: 'not_running' | 'process_running' | 'adb_device' | 'boot_completed' | 'android_responsive' | 'ready';
  detail: string;
  since: string | null;
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
  /** Opcional: backend antigo não manda — e aí vale o `frame.stale` de antes. */
  stream?: StreamInfo | null;
  connectivity?: ConnectivityInfo;
  readiness?: ReadinessInfo;
  current: InstanceCurrent | null;
  attention: string | null;           // texto curto quando exige atenção do usuário
  /** Pausa do reparo AUTOMÁTICO deste aparelho (experimento/manutenção); nulo/ausente = o reparo age normalmente. */
  repair_pause?: { until: string; since: string; reason: string; by: string; remaining_s: number } | null;
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
   * Renderizador do EMULADOR (29.11): o `gpu_mode` pedido e o que o emulador selecionou de fato — são dois fatos,
   * porque ele aceita um `-gpu` e usa outro sem reclamar. `gles`/`vulkan` vêm do log do emulador a cada entrada no
   * ar (nulos fora do ar); `fallback` = o selecionado não é o pedido, e aí o motivo vem também em `attention`.
   * Nulo/ausente = não é emulador que se conheça (físico, ou worker com agente antigo).
   */
  renderer?: { configured: string | null; gles: string | null; vulkan: string | null; fallback: boolean } | null;
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
  /** A versão que "Instalar" instalaria AGORA (a maior promovida). Ausente/nulo = nada promovido: o backend recusa. */
  promoted_release_id?: string | null;
  promoted_version_name?: string | null;
  promoted_version_code?: number | null;
  /** Categoria da vitrine (loja de apps). `null` = sem categoria. */
  category?: AppCategory | null;
}

/** Categorias da vitrine: lista fixa decidida pelo dono em 26/09. Espelha `APP_CATEGORIES` do backend. */
export type AppCategory = 'social' | 'mensagens' | 'email' | 'rede' | 'utilitario' | 'qa';

interface PlanStep {
  key: string;                // estável dentro do plano: 'open_app', 'open_conversation', …
  title: string;
  goal: string;               // objetivo em linguagem natural (nunca coordenadas)
  depends_on: string[];       // keys
  side_effect: boolean;       // true = repetição NÃO é segura (ex.: enviar)
  /** 31.299 (adendo v1.130): a etapa foi criada por EXPLORAÇÃO (o catálogo do app não cobria o pedido). Fora da serialização quando falso. */
  exploratoria?: boolean;
  precondition: string | null;
  postcondition: { kind: 'text_visible' | 'app_foreground' | 'element_present' | 'model_judged' | 'items_collected'; value: string; description: string };
  timeout_s: number;
  max_attempts: number;
  /** Item 12.1/ADR-058: o app em que ESTA etapa roda — um comando pode atravessar apps. `null`/ausente = o app do
   *  plano (`Plan.app_id`). */
  app_id?: string | null;
  /** De que habilidade, versão e nó a etapa saiu, quando o plano foi compilado de uma habilidade (`StepOrigin`).
   *  Ausente no plano do planejador. É o que permite corrigir a etapa no ensino (plano 22.7). */
  origin?: StepOrigin;
  /** Contrato C2 (ADR-058): os nomes dos valores que a etapa lê e deixa para as seguintes (`{{saida:<nome>}}`).
   *  Ausente quando a etapa não produz nada. */
  saidas?: string[];
  /** 31.129 (adendo v1.84): os pacotes, além do app da etapa, em que a tela também comprova a conclusão. Omitido quando vazio. */
  pacotes_aceitos?: string[];
}

interface StepOrigin {
  skill_id: string;
  skill_version: number;
  node_id: string;
  strategies?: string[];
}

interface Plan {
  summary: string;
  app_id: string | null;
  app_package: string | null;
  /** ADR-058 (T18): os apps de que este plano PRECISA, por id — sempre preenchido quando o plano veio do planejador
   *  com catálogo. Vazio (plano antigo, ou compilado de skill de um app só) = só `app_id`, como sempre foi. */
  required_apps?: string[];
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
  /** Apps que a execução toca: o do plano e o de cada etapa (item 12.1). */
  app_ids?: string[];
  /** v0.45 (pedidos, 28.9): de que pedido e de que ocorrência esta execução nasceu; `null` ou ausente em toda
   *  execução anterior ou avulsa. Aditivo e opcional: backend antigo não manda. */
  pedido_id?: string | null;
  ocorrencia_id?: string | null;
  /** v0.97 (30.37): o fluxo que esta execução PROVA (validação do curador); `null` ou ausente em toda execução comum.
   *  Não é pedido de pessoa: o painel a rotula "Prova de fluxo (validação)". Aditivo e opcional. */
  prova_fluxo_id?: string | null;
  /** 30.38 (a): de onde a execução veio, derivado no backend pela regra única de `app/contracts/origem.py`; `null` ou
   *  ausente no pedido de pessoa pelo painel. `origem_ref`: o fluxo, o pedido de validação ou o id externo do canal. */
  origem?: OrigemDaExecucao | null;
  origem_ref?: string | null;
  /** 31.154: a operação com N agentes de que esta execução é um alvo; ausente/nulo fora dela. */
  operacao_id?: string | null;
  /** 31.305 (adendo v1.135): quantas etapas da execução nasceram de exploração (o catálogo não cobria o pedido). Inteiro >= 0, sempre
   *  presente no central novo; ausente em backend anterior (vale 0). */
  etapas_exploratorias?: number;
  /** v1.74 (29.153): o custo de IA da execução, só no detalhe (`GET /runs/{id}`); a lista não o traz. Ausente em backend
   *  de antes do 29.153: o painel mostra "custo não lido", nunca erro. */
  costs?: RunCosts | null;
}

/** v1.74: `spent_usd` é a estimativa em US$ de `planning/costs.py` (sem limite de dia); `calls`, as linhas de `ai_calls`. */
export interface RunCosts { spent_usd: number; calls: number }

/** O vocabulário fechado de `app/contracts/origem.py` (`ORIGENS_DA_EXECUCAO`), o mesmo do backend. */
export type OrigemDaExecucao = 'prova_fluxo' | 'validacao_qa' | 'telegram' | 'trello';

/** 29.58: o efeito que apareceu repetido (o 29.60 mostra no painel). */
export interface EfeitoRepetido { copias: number; fonte: 'verificador' | 'acoes' | 'provedor' }

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
  // `evidence_id`: na confirmação manual, o print em que a pessoa se baseou (ADR-055)
  // `efeito_repetido` (29.58, adendo v1.08): a etapa com efeito que mostrou 2 ou mais cópias e fechou `uncertain`;
  // ausente quando não há repetição. `fonte`: quem contou (o verificador na tela, ou as ações gravadas).
  // `efeito_comprovado` (29.79, adendo v1.40): o efeito saiu e foi comprovado, só uma afirmação sobre ele ficou incerta
  // (o rótulo de IA); a etapa nunca se repete. Ausente quando falso.
  result: { verified: boolean; evidence_text: string | null; delivery_level?: DeliveryLevel; evidence_id?: number | null;
            efeito_repetido?: EfeitoRepetido; efeito_comprovado?: boolean } | null;
  claimed_by?: string | null;                        // backend que assumiu a etapa; null = nunca despachada
  driven_by: 'ai' | 'recipe' | 'recipe+ai' | 'sem_ator' | null;   // v0.2 — quem decidiu as ações; `sem_ator`: fechou sem ator (caminho rápido 1)
  /** Item 12.1: app em que esta etapa roda. `null`/ausente = o app do plano (`Plan.app_id`). */
  app_id?: string | null;
  /** Item 31.65: o motivo da recusa da persona ao escrever o texto desta etapa (texto do modelo). Só no detalhe da
   *  etapa: a dica do bloqueio, que viaja para Pendências, aviso e Trello, é fixa. Ausente sem recusa. */
  motivo_da_persona?: string | null;
  /** 31.129 (adendo v1.84): os pacotes, além do app da etapa, em que a tela também comprova a conclusão. Omitido quando vazio. */
  pacotes_aceitos?: string[];
  /** 31.299 (adendo v1.130): a etapa foi criada por EXPLORAÇÃO, só proveniência. Ausente em backend anterior. */
  exploratoria?: boolean;
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
  /** 28.61: id de um grupo de política cujas personas não passam pela aprovação de política (recusas e tetos seguem); vazio = desligado. Backend anterior ao corte 57 não manda. */
  grupo_sem_aprovacao?: string;
  ai_max_calls_per_objective: number; ai_max_tokens_per_run: number;
  // item 17.12 — o teto de chamadas cresce por item do for_each (base + por_item × (itens − 1)), até o absoluto
  ai_max_calls_per_item: number; ai_max_calls_absolute: number;
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
  // v0.20 (C2) — prévia sob demanda. Opcional: backend anterior ao adendo não manda (e captura sempre).
  preview_mode?: PreviewMode;
  // Prova de 07/10 (J1): quantas personas a sugestão de alvos escolhe e quantas candidatas vão ao modelo. Tipados como número
  // (a lista de campos numéricos depende disso); backend anterior ao J1 não manda, e então o formulário não mostra o grupo.
  orquestracao_max_escolhidas: number;
  orquestracao_max_candidatas: number;
  /** Quantas contas executam a ação final no post nosso numa operação (as demais ficam paradas até serem liberadas). */
  operacao_max_acoes_executadas: number;
  /**
   * ADR-082 (31.253): ligado (padrão), numa operação com ação final "executar" o alvo cuja persona está no grupo `grupo_sem_aprovacao` nasce com o
   * teto `agir`: comenta sem aprovação nem Liberar. Desligado, todos os alvos voltam a "preparar". Backend anterior ao corte 61 não manda.
   */
  operacao_grupo_liberado_executa?: boolean;
  /** v1.119 (31.220): de quanto em quanto tempo o laço do sistema avança as operações abertas, em segundos; 0 = desligado. Backend anterior não manda. */
  operacao_laco_s: number;
}

/** v0.20 (C2): `on_demand` só captura prévia de aparelho que alguém olha; `always` é o laço antigo. */
type PreviewMode = 'on_demand' | 'always';

interface AiStatus {
  provider: 'anthropic' | 'simulated' | string;
  model: string | null;
  configured: boolean;            // chave presente
  simulated: boolean;             // modo simulado de desenvolvimento
  sends_data_externally: boolean; // screenshots/textos saem da máquina
  notice: string;                 // texto para exibir
  effort: string | null;
  // v0.2
  models?: { plan: string; decide: string; verify: string; escalation: string; social?: string; persona?: string } | null;
  recipes?: 'off' | 'shadow' | 'replay' | null; flows?: boolean | null;
  image_policy?: 'always' | 'auto' | 'never' | null;
  // v1.122 (31.223, Aprendizado): a política de escalada do modelo forte, SÓ LEITURA (mora no config.yaml e vale na subida da farm-central).
  // Aditivos e opcionais: o central anterior não manda, e a tela não afirma nada.
  strong_model_only_on_commit?: boolean | null;
  strong_model_for_side_effect?: string | boolean | null;
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
  // v0.27 — gerador de IMAGEM da persona: porta própria, fora dos papéis de `roles`.
  image?: AiImageStatus | null;
  // v0.28 — saldo estimado de cada conta de IA (ADR-051), o mesmo de GET /api/ai/balances.
  balances?: AiBalance[];
  // v0.87 — o formato da etapa livre do plano (`ai.esquema_do_plano`), os perfis de IA (`ai.profiles`) e se a leitura
  // visual está ligada (`ai.leitura_visual.enabled`). Ausentes em backend anterior.
  esquema_do_plano?: 'longo' | 'curto' | string | null;
  profiles?: AiProfileStatus[];
  leitura_visual?: boolean | null;
  // v0.90 — a porta da decisão fechada (Jev); null sem consumidor em sombra ou ligado. Ausente em backend anterior.
  decisao_fechada?: AiDecisaoFechada | null;
}

/** O bloco `decisao_fechada` de `GET /api/ai` (adendo v0.90; `transparencia.py::status`). */
interface AiDecisaoFechada {
  provider: 'typesafe' | string;
  name: string;
  consumers: Record<string, 'shadow' | 'on' | string>;  // só os ligados, por origem (curador, intencao…)
  classes: string[];
  send_approved: boolean;
  key: 'configurada' | 'não configurada' | string;
  decider: 'nulo' | 'jev' | string;
  sending: boolean;                      // sai algo AGORA (as quatro condições juntas)
  retention_days: number | null;
}

/** Um perfil de IA (v0.87): só as funções que ele muda; as outras são as do padrão. */
interface AiProfileStatus {
  name: string;
  note: string;
  roles: { role: string; provider: string; model: string; effort: string | null; sends_data_externally: boolean }[];
  canary_fraction: number | null;       // a fatia das execuções sem perfil que vai para ele; null = não é o canário
  screenshot_max_side: number | null;
  rich_tree_min_elements: number | null;
  /** 31.35 B: quantas ações por decisão o perfil permite (null/ausente = o global). */
  acoes_por_decisao?: number | null;
}

interface AiImageStatus {
  provider: 'simulated' | 'openai' | string;
  model: string;
  quality: string;
  configured: boolean;
  simulated: boolean;
  sends_data_externally: boolean;
  per_persona: number;
  on_create: boolean;
  price_per_image_usd?: number | null;
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
  // v0.84 — "o ator pensa?": adaptive | desligado_na_funcao | nao_declarado | recusado_pelo_modelo; null = provedor sem thinking.
  thinking?: string | null;
}

interface Health {
  /** Identidade estável do backend ("este HTTP é a Farm?"); não muda com o commit. */
  service?: 'android-farm-central';
  status: 'ok' | 'degraded' | 'error';
  version: string;
  // Qual código está NO AR. `version` é uma constante do backend e responde igual antes e depois de um deploy;
  // estes dois respondem a pergunta que importa. `null` quando a instalação veio por cópia, sem `.git`.
  commit: string | null;
  migration: string | null;
  // Qual banco este backend está usando e se ele respondeu AGORA. Opcional porque um backend anterior a esta
  // entrega não manda o campo — e o painel prefere não mostrar nada a mostrar "sqlite" por chute.
  database?: { dialect: 'sqlite' | 'postgres'; reachable: boolean; target: string | null; open_connections?: number; slow_queries_in_loop?: number } | null;
  ai: AiStatus;
  appium: { running: boolean; port: number; detail: string | null };
  sdk: { found: boolean; root: string | null; emulator_version: string | null; accel: string | null };
  problems: { code: string; message: string; hint: string }[];
  // v0.2
  features: { hibernation: boolean; recipes: string; flows: boolean; image_policy: string;
              system_image: string;
              // fase F — `skills.enabled`: a lista de habilidades e o "Gerar habilidade deste fluxo" só aparecem com isto
              // ligado. Ausente = backend anterior à fase F = desligado.
              skills?: boolean };
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
  /** Só com `type: 'text'` (contrato v1.56): limpa o campo em foco antes de digitar. Padrão false. */
  clear_first?: boolean;
  key?: 'back' | 'home' | 'recents' | 'enter' | 'delete';
}

// ---- Adendo v0.2 — custo de IA, fluxos e receitas (copiado do contrato) ----

interface UsageGroup { role: 'plan' | 'decide' | 'verify' | 'social' | 'persona'; model: string; tier: 0 | 1; calls: number;
  fresh: number; cache_read: number; cache_write: number; output: number;        // tokens
  with_image: number; errors: number; avg_ms: number; usd: number | null }       // usd null = modelo sem preço
interface UsageReport { scope: { run_id: string | null; days: number | null }; groups: UsageGroup[]; total_usd: number;
  // v0.28 — custo por CONTA de IA (anthropic, openai, gemini), em US$ (ADR-051), da janela ou da execução.
  by_account?: Record<string, number>;
  objectives_with_ai: number; calls_per_objective: number; usd_per_objective: number;
  steps_driven_by: Record<string, number>; unpriced_models: string[];
  // v0.3 — item 7.2. `fallbacks`: quantas chamadas foram servidas por outro modelo, e por quê ('refusal' = recusa
  // reexecutada pelo provedor; nome de provedor = o endpoint da função falhou e ela declarou para onde cair).
  // `spend_today_usd` é o gasto do dia UTC, independente da janela deste relatório.
  fallbacks?: { fallback: string; requested_model: string | null; model: string; calls: number }[];
  spend_today_usd?: number | null
  // Item 7.3 (achado #101): chamadas com erro, por TIPO (refusal | budget | billing | not_configured |
  // invalid_output | error) — sem isto, saber por que uma chamada falhou exigia casar horário de log.
  errors_by_kind?: Record<string, number>;
  // v0.75 (RA-10, migração 080): custo por ORIGEM da chamada (execucao, curador, leitura, social, decisao_fechada…;
  // `sem_origem` = linha anterior à coluna). Opcional: servidor antigo.
  by_origin?: Record<string, { calls: number; usd: number }>;
  // v0.75 (RA-10; tela no 31.16): por que a chamada subiu ao modelo forte (`MotivoDeEscalonamento` do backend: efeito,
  // nova_tentativa, erros_seguidos, piso, bloqueio, ciclo, nivel, sim_com_efeito). Opcional: servidor antigo.
  escalations?: Record<string, { calls: number; usd: number }>;
  // O rejulgamento do verificador (7.10 e 17.10): custo e quanto o modelo forte DESFEZ o veredito do barato.
  rejudges?: { calls: number; usd: number; by_kind: Record<string, { calls: number; usd: number }>;
    judged: number; disagreements: number; disagreement_rate: number | null;
    by_app: Record<string, { judged: number; disagreements: number; disagreement_rate: number }> };
  // A cascata do bloqueio (17.10): o modelo forte olhou a tela que o barato largou; `unblocked` = não bloqueou de novo.
  cascades?: { calls: number; usd: number; unblocked: number; by_verdict: Record<string, number> };
  // Por que a imagem foi, ou não, junto (`MotivoDaImagem`); `with_image` = foi de fato (a captura pode falhar).
  image_reasons?: Record<string, { calls: number; with_image: number }>;
  // Etapas terminadas com decisão de IA e sem `driven_by`: o aceite do RA-10 é zero.
  steps_driven_by_null?: number }
interface Flow { id: string; name: string; command_template: string; app_id: string | null; source_run_id: string | null;
  // D1 (ADR-054): o fluxo aprendido de execução nasce `candidate` (inerte: o planejador segue sendo chamado) e a sombra
  // no digest o publica sozinho quando não tem efeito externo; com efeito, para em `validated` e espera o dono.
  status: 'candidate' | 'validated' | 'active' | 'disabled'; uses: number; created_at: string; last_used_at: string | null;
  // 29.42: ids dos apps que o fluxo exige, na ordem em que o plano os usa ("QA Messenger → Chrome"). Opcional: servidor antigo.
  required_apps?: string[];
  // 31.150 (adendo v1.97): quando uma pessoa religou o fluxo de prova para uso real; `null` enquanto está desligado. Backend anterior não manda.
  em_uso_real_desde?: string | null }
interface Recipe { id: number; app_package: string; app_version: string; step_key: string; step_hash: string;
  // `candidate`: aprendida e ainda em prova — a IA conduz a etapa e a receita só é comparada (modo sombra); vira
  // `active` depois de `ai.recipes_promote_after` execuções seguidas em que a IA fez exatamente o caminho dela.
  // `validated` (D1, ADR-054): concordou, mas tem ação de efeito externo — inerte até o dono aprovar no livro.
  version: number; status: 'candidate' | 'validated' | 'active' | 'quarantined' | 'superseded';
  actions: { tool: string; args: Record<string, unknown>; commit: boolean; why: string;
             selectors?: { kind: string; rid?: string; text?: string; desc?: string }[];
             scroll?: { direction: string; max: number } }[];
  replay_ok: number; replay_fail: number; consecutive_fail: number; shadow_agree: number; shadow_total: number;
  learned_from_step: string | null; created_at: string; last_used_at: string | null }

export type {
  InstanceState, ControlOwner, AutomationState, RunStatus, ObjectiveStatus, StepStatus, AttemptStatus,
  ActionStatus, DeliveryLevel, FrameInfo, StreamInfo, StreamStatus, InstanceCurrent, Instance, AppConfig, PlanStep, StepOrigin, Plan, RunSummary,
  Step, Action, Attempt, Evidence, Objective, PlanVersion, RunDetail, EventRecord, Settings, PreviewMode, AiStatus, AiRoleStatus, AiProfileStatus, AiImageStatus,
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
export type SessionStatus = 'unknown' | 'auth_required' | 'auth_challenge' | 'wrong_account' | 'session_ready'
  // v0.28: o que só uma pessoa resolve sem ser desafio nem conta errada (`auth_required` é o antigo `logged_out`).
  | 'needs_person';

export interface CredentialInfo {
  configured: boolean;
  login_identifier: string | null;
  status: string | null;
  failed_attempts: number;
  blocked_until: string | null;
  updated_at: string | null;
  last_used_at: string | null;
  /** v0.28 (ADR-040): consentimento POR CONTA. Nulo = guardada, mas ninguém a digita. */
  consent_at?: string | null;
  consent_by?: string | null;
}

export interface ActionGate { allowed: boolean; reason: string | null }

/** `state=null` = nunca inspecionado: não se sabe se está instalado, que é diferente de "não está". */
export interface AppOnDevice {
  package: string;
  state: string | null;
  version_name: string | null;
  version_code: number | null;
  verified_at: string | null;
  pending_op: string | null;
  detail: string | null;
}

export type SessionPhase = 'no_device' | 'app_unknown' | 'app_missing' | 'app_installing' | 'no_credential'
  | 'authenticating' | 'logged_out' | 'authenticated' | 'challenge' | 'wrong_account' | 'unknown';

export interface SessionActions {
  phase: SessionPhase;
  detail: string;
  connect: ActionGate;
  verify: ActionGate;
  logout: ActionGate;
  inspect_app: ActionGate;
}

/** `GET /instances/{id}/operational-context`: servidor → aparelho → tela → apps → perfil → sessão. */
export interface OperationalContext {
  instance_id: string;
  server: { id: string; name: string; local: boolean; connected: boolean; state: string;
            transport_state: string | null; verbs: string[] } | null;
  device: { state: InstanceState; state_detail: string | null; kind: string; supported_verbs: string[];
            automation: { state: string; detail: string | null }; attention: string | null };
  stream: StreamInfo | null;
  connectivity?: ConnectivityInfo;
  readiness?: ReadinessInfo;
  apps: {
    app_id: string; name: string; package: string;
    presence: 'installed' | 'absent' | 'in_progress' | 'unknown';
    state: string | null; installed_version_name: string | null; installed_version_code: number | null;
    verified_at: string | null; pending_op: string | null; detail: string | null;
    promoted_release_id: string | null; promoted_version_name: string | null; promoted_version_code: number | null;
  }[];
  profiles: {
    profile_id: string; username: string; display_name: string | null; persona_id: string | null;
    persona_name: string | null; has_avatar?: boolean; credential_configured: boolean; credential_status: string | null;
    session: SessionInfo; app_on_device: AppOnDevice | null; session_actions: SessionActions | null;
    /** v0.28: as contas da persona vinculada, cada uma com credencial (metadados), consentimento e a sessão
     *  NESTE aparelho. */
    accounts?: ProfileAccount[];
  }[];
}

/**
 * 31.322 (adendo v1.142): por que a conta caiu na tela humana, como DADO (sem segredo e sem o @). Vem no evento
 * `profile.account_retired` e na lápide; campo que o servidor não soube fica `null`, e a tela nunca o inventa.
 */
export interface MotivoDoBloqueio {
  egresso_esperado: string | null;
  egresso_medido: string | null;
  egresso_divergente: boolean | null;
  ips_distintos_desde_criacao: number | null;
  minutos_ate_o_primeiro_login: number | null;
  trecho_da_tela: string | null;
}

export interface SessionInfo {
  status: SessionStatus;
  instance_id: string | null;
  observed_username: string | null;
  verified_at: string | null;
  detail: string | null;
  /** "Conectado" verificado há tempo demais: o aparelho é relido antes da próxima tarefa. */
  stale: boolean;
  /**
   * 29.96 (adendo v1.48): `unknown` NO TETO do aparelho — a automação parou sem tocar numa tela que não reconheceu e
   * espera uma pessoa (a mesma regra do `session.needs_person`). Opcional só para os dublês de teste antigos: o backend
   * sempre manda; ausente vale `false`.
   */
  unknown_at_cap?: boolean;
  /**
   * 29.100 (adendo v1.51): desde quando a sessão está assim: a mudança de estado e, no `unknown`, a chegada ao teto (a
   * parada); regravar o mesmo estado fora disso não a move. Nulo na sessão
   * `session_ready` de antes da migração 112. Opcional pelo mesmo motivo de `unknown_at_cap`.
   */
  status_since?: string | null;
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

/** 31.326 (adendo v1.142): uma conta que saiu da plataforma por bloqueio (lápide), sem o @. `motivo_do_bloqueio` é `null` nas antigas. */
export interface ContaRetirada {
  app_id: string;
  retirada_em: string;
  motivo_do_bloqueio: MotivoDoBloqueio | null;
}

export interface InstagramProfile {
  /** 31.326 (v1.142): as contas retiradas por bloqueio, da mais nova à mais antiga. Ausente em backend anterior (vale lista vazia). */
  contas_retiradas?: ContaRetirada[];
  /** 31.315 (adendo v1.139, `personas.teste`): persona criada só para provas; não é uma pessoa do parque. Ausente em backend anterior (vale falso). */
  teste?: boolean;
  id: string;
  username: string;
  display_name: string | null;
  first_name: string | null;
  last_name: string | null;
  birth_date: string | null;
  email: string | null;
  persona_id: string | null;
  persona_name: string | null;
  /** Grupo de acesso: políticas e limites herdados; o que o perfil muda deliberadamente sobrepõe o grupo. */
  policy_group_id?: string | null;
  policy_group_name?: string | null;
  status: string;
  instance_id: string | null;
  /** `null` quando não há vínculo: sem aparelho não há localidade a afirmar. */
  locality: ProfileLocality | null;
  offline_policy: OfflinePolicy;
  credential: CredentialInfo;
  session: SessionInfo;
  /** O app da conta no aparelho vinculado, como foi OBSERVADO. `null` = sem vínculo. */
  app_on_device?: AppOnDevice | null;
  /** Fonte única dos botões Conectar / Verificar conta / Sair (backend `social/sessao_gate.py`). */
  session_actions?: SessionActions | null;
  last_verified_at: string | null;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
  // v0.27 — a persona É o perfil (`InstagramProfileDTO = PersonaDTO` no backend): os campos da pessoa vêm no mesmo
  // objeto. Opcionais aqui porque as fixtures antigas não os têm; `PersonaDTO` (abaixo) exige o nome.
  name?: string;
  summary?: string | null;
  persona_prompt?: string;
  traits?: PersonaTraits;
  biography?: PersonaBiography;
  visual?: PersonaVisual;
  generation?: PersonaGeneration;
  /** Calculada de `birth_date`; senão `biography.approx_age`. Nunca gravada. */
  age?: number | null;
  gender?: string | null;
  locale?: string | null;
  voice_gaps?: string[];
  images?: PersonaImage[];
  primary_image_id?: string | null;
  /** Há foto principal pronta (29.26): sem ela o painel mostra as iniciais e não pede `/avatar` (404). */
  has_avatar?: boolean;
  accounts_count?: number;
  /** v0.29 (ADR-043): TODOS os aparelhos da persona (N:N), o principal primeiro. `instance_id` (acima) passa a ser o
   *  PRINCIPAL. Opcional: backend anterior e fixtures antigas não o mandam — aí vale só o `instance_id`. */
  devices?: PersonaDevice[];
}

/**
 * Um vínculo da persona com um aparelho (v0.29, migração 051): para que app (`null` = os apps sem conta gerenciada),
 * se é o principal, onde o aparelho roda e a sessão da conta daquele app NESTE aparelho. O mesmo aparelho pode
 * aparecer duas vezes, com apps diferentes.
 */
export interface PersonaDevice {
  instance_id: string;
  app_id: string | null;
  is_primary: boolean;
  /** Estado do aparelho quando o backend o conhece (`InstanceState`); o store ao vivo ganha dele na tela. */
  state: string | null;
  worker_id: string | null;
  bound_at: string | null;
  /** `null` quando a persona não tem conta que sirva ao vínculo. */
  session: SessionInfo | null;
}

/** `GET /api/instances/{id}/personas` (v0.29): quem está neste aparelho, por vínculo, com a sessão da conta AQUI. */
export interface PersonaOnDevice {
  profile_id: string;
  username: string | null;
  display_name: string | null;
  name: string;
  status: string;
  app_id: string | null;
  /** Este aparelho é o PRINCIPAL desta persona. */
  is_primary: boolean;
  bound_at: string | null;
  session: SessionInfo | null;
  /** Como em `InstagramProfile.has_avatar` (29.26). */
  has_avatar?: boolean;
}

/** `POST /api/personas/{id}/devices`: soma um aparelho à persona para um app, sem tirar ninguém de lá. */
export interface PersonaDeviceBindRequest {
  instance_id: string;
  app_id?: string | null;
  primary?: boolean;
}

/**
 * A pessoa inteira (`GET /api/personas`, v0.27): o mesmo objeto do perfil, mas `username` é `null` quando a pessoa
 * ainda não tem conta de cadastro (a coluna guarda `''`; a tradução é na borda do backend).
 */
export interface PersonaDTO extends Omit<InstagramProfile, 'username' | 'name' | 'summary'> {
  username: string | null;
  name: string;
  summary: string | null;
  profile_id?: string | null;
  profile_username?: string | null;
}

export interface PersonaVisual {
  appearance?: string | null;
  visual_style?: string | null;
  photo_scenario?: string | null;
  palette?: string | null;
  age_presentation?: string | null;
  gender_presentation?: string | null;
}

export interface PersonaBiography {
  schema_version?: number;
  approx_age?: number | null;
  origin?: { birthplace?: string | null; hometown?: string | null; nationality?: string | null };
  home?: { city?: string | null; state?: string | null; country?: string | null; residence?: string | null };
  work?: { profession?: string | null; employer?: string | null; education?: string[] };
  life?: { marital_status?: string | null; children?: number | null; history?: string[] };
  /**
   * v0.31 (ADR-048, `schema_version` 2) — crenças RICAS, que VÃO ao modelo e moldam a voz. `null` = sem crença
   * registrada (e é o que um PATCH manda para apagar). Um backend anterior manda texto (`string`, a v1): a tela o
   * lê como o resumo da crença.
   */
  beliefs?: { religion?: BioReligion | string | null; politics?: BioPolitics | string | null };
  tastes?: { interests?: string[]; hobbies?: string[]; preferences?: string[]; dislikes?: string[] };
}

/** v0.31 — quanto a pessoa pratica a religião. */
export type PraticaReligiosa = 'nao_pratica' | 'ocasional' | 'regular' | 'devota';
/** v0.31 — ponto no espectro, ou fora dele: `apolitica` (não se interessa) e `nao_declara` (tem posição e não diz). */
export type OrientacaoPolitica =
  'esquerda' | 'centro_esquerda' | 'centro' | 'centro_direita' | 'direita' | 'apolitica' | 'nao_declara';
export type EngajamentoPolitico = 'nenhum' | 'baixo' | 'medio' | 'alto';

/** v0.31 — a religião como a pessoa a vive (`models.py::BioReligion`). Tudo opcional. */
export interface BioReligion {
  affiliation?: string | null;
  practice?: PraticaReligiosa | null;
  practices?: string[];
  importance?: string | null;
  in_speech?: string | null;
  values?: string[];
  sensitive_topics?: string[];
  summary?: string | null;
}

/** v0.31 — uma pauta com a posição da pessoa. */
export interface BioIssue {
  topic: string;
  stance?: string | null;
}

/** v0.31 — o jeito político da pessoa (`models.py::BioPolitics`). Tudo opcional. */
export interface BioPolitics {
  orientation?: OrientacaoPolitica | null;
  engagement?: EngajamentoPolitico | null;
  issues?: BioIssue[];
  discussion_style?: string | null;
  sources?: string[];
  values?: string[];
  summary?: string | null;
}

/** Proveniência: `manual`, `ai` (gerada por modelo) ou `legacy_persona` (dobrada pela 047). */
export interface PersonaGeneration {
  source?: 'manual' | 'ai' | 'legacy_persona' | string | null;
  persona_id?: string | null;
  prompt?: string | null;
  provider?: string | null;
  model?: string | null;
  usd?: number | null;
  at?: string | null;
  enriched_at?: string | null;
}

/** Uma imagem da galeria da persona (048). `url` é a rota que a serve; nunca um caminho de disco. */
export interface PersonaImage {
  id: string;
  persona_id: string;
  status: 'pending' | 'ready' | 'failed' | 'refused' | string;
  source: 'generated' | 'upload' | 'imported_legacy' | string;
  is_primary: boolean;
  width: number | null;
  height: number | null;
  provider: string | null;
  model: string | null;
  seed: number | null;
  aspect: string | null;
  cost_usd: number;
  error: string | null;
  created_at: string;
  url: string;
  /** 29.81 (migração 108): o dono disse se a foto ENVIADA foi feita por IA. `true` = sai com o rótulo de IA do
   *  Instagram; `false` = foto real; `null`/ausente = não informado (sai sem rótulo). Só o upload usa: a gerada e a
   *  importada saem sempre com o rótulo. */
  feita_por_ia?: boolean | null;
}

/** `POST /personas` (e o rascunho de `POST /personas/generate`, que tem este formato e não é gravado). */
export interface PersonaCreateRequest {
  /** 31.315: cria a persona já marcada como de teste. Só se manda quando verdadeiro. */
  teste?: boolean;
  name: string;
  summary?: string | null;
  persona_prompt?: string;
  traits?: PersonaTraits;
  first_name?: string | null;
  last_name?: string | null;
  birth_date?: string | null;
  gender?: string | null;
  locale?: string | null;
  biography?: PersonaBiography;
  visual?: PersonaVisual;
  generation?: PersonaGeneration | null;
}

/** `PATCH /personas/{id}`: POR SEÇÃO. `traits`/`visual`/`biography` são mesclados no servidor; `null` apaga. */
export type PersonaPatchRequest = Partial<Omit<PersonaCreateRequest, 'generation'>> & { display_name?: string | null };
// `teste` no PATCH: verdadeiro marca, falso desmarca, ausente (ou null) não mexe (v1.139); por isso o PATCH manda o booleano explícito.

/** `POST /personas/generate`: chamada PAGA pelo papel social; devolve um rascunho não gravado. */
export interface PersonaGenerateRequest {
  prompt: string;
  locale?: string | null;
  constraints?: Record<string, string>;
}

/** 202 de `POST /personas/{id}/images` com `{count}`: as imagens chegam pelo evento `persona.image.updated`. */
export interface PersonaImagesAccepted {
  accepted: boolean;
  persona_id: string;
  count: number;
  provider: string;
  simulated: boolean;
}

export interface ProfileCreateRequest {
  username: string;
  first_name?: string | null;
  last_name?: string | null;
  display_name?: string | null;
  birth_date?: string | null;
  email?: string | null;
  persona_id?: string | null;
  policy_group_id?: string | null;
  instance_id?: string | null;
  login_identifier?: string | null;
  password?: string | null;
}

export type ProfilePatchRequest = Partial<Omit<ProfileCreateRequest, 'username' | 'password' | 'login_identifier'>> & {
  status?: 'active' | 'blocked' | 'disabled';
  offline_policy?: OfflinePolicy;
  /** Mudar de SERVIDOR um perfil com sessão pronta é decisão de pessoa: sem isto a API recusa com 409. */
  confirm_locality_change?: boolean;
};

/** 23.9 (ADR-057): usar a senha de outra conta da persona. Sem valor e sem consentimento: o da conta é dado à parte. */
export interface CredentialCloneRequest {
  clonar_de: string;
  login_identifier?: string | null;
}

export interface CredentialUpdateRequest {
  login_identifier?: string | null;
  password: string;
  /** v0.28 (ADR-040): a pessoa autoriza a automação a digitar esta senha. Conta que nunca consentiu sem isto → 409. */
  consent?: boolean;
}

/** Resposta 202 de connect/verify/logout: o trabalho roda no aparelho e o resultado aparece no perfil. */
export interface SessionJobAccepted {
  accepted: boolean;
  profile_id: string;
  instance_id: string;
  /** v0.28: nas rotas por conta. */
  account_id?: string;
  command_id?: string;
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
  /** App de onde o fato veio; null = fato geral da identidade (item 12.1). */
  app_id?: string | null;
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
  /** App a que o fato pertence; vazio = fato geral da identidade. */
  app_id?: string | null;
}

export type InteractionStatus = 'pending' | 'confirmed' | 'failed' | 'uncertain' | 'cancelled';

export interface SocialInteraction {
  id: string;
  profile_id: string;
  app_id?: string | null;
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
  /** É o app da conta de CADASTRO da persona (o @ que a identifica). No máximo um app registrado é âncora (23.10:
   *  é o que o painel usa no lugar de comparar nome ou pacote — "ehInstagram" fixo). */
  profile_anchor: boolean;
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
  /** Só para `distribute`: os aparelhos escolhidos. Sem este nem `count`, o parque inteiro. */
  instance_ids?: string[];
  /** Só para `distribute`: N aparelhos, escolhidos pelo backend entre os que podem receber e não estão na versão. */
  count?: number;
  /** Só para `distribute`: prévia — o que aconteceria em cada aparelho, sem gravar nem instalar nada. */
  dry_run?: boolean;
}

/** O que aconteceu com cada aparelho do parque ao distribuir uma versão. */
export interface DistributeDevice {
  id: string;
  /** `incompatible` = o aparelho não roda esta versão (API, ABI ou GMS); a versão desejada NEM foi gravada. */
  /** `would_start` só aparece na prévia (`dry_run`): ligado, instalaria agora se estivesse livre. */
  /** `kept` só aparece ao promover (ADR-026): o aparelho fica na versão que tem, e `reason` diz por quê (versão
   *  mais nova em prova, entrega que falhou esperando a nova tentativa diária, operação em andamento). */
  outcome: 'started' | 'pending' | 'already' | 'incompatible' | 'would_start' | 'kept';
  reason: string;
  worker_id?: string | null;
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
  needs_draft: boolean;
  bindings: string[];
  /** Nas ações que escrevem, o texto é opcional: o normal é vir `content_brief` e cada perfil escrever o seu. */
  optional_bindings: string[];
}

export type PolicyName = 'autonomous' | 'approval_required' | 'manual_only' | 'disabled';

export interface ProfilePolicy {
  /** O app deste catálogo (23.10). `null` só quando nenhum app se resolveu (sem âncora e sem escolha explícita). */
  package?: string | null;
  capabilities: Record<string, PolicyName>;
  defaults: Record<string, PolicyName>;
  /** Chaves de `capabilities` mais frouxas que `defaults` — achado #114: afrouxar sempre foi aceito, isto marca. */
  loosened: string[];
  /** Grupo de acesso. Ordem: o que o perfil mudou (`own`) → o grupo (`group`) → o padrão do catálogo. */
  group_id?: string | null;
  group_name?: string | null;
  own?: Record<string, PolicyName>;
  group?: Record<string, PolicyName>;
  origin?: Record<string, PolicyOrigin>;
}

/** De onde vem o valor que vale: escolha própria do perfil, herdado do grupo, ou padrão do catálogo. */
export type PolicyOrigin = 'own' | 'group' | 'default';

/** `null` numa chave apaga a escolha própria: a ação volta a herdar do grupo/padrão. */
export interface ProfilePolicyPatch {
  capabilities?: Record<string, PolicyName | null>;
}

export interface PolicyGroup {
  id: string;
  name: string;
  description: string;
  /** O app deste recorte do grupo (23.10): `capabilities` e `loosened` são só dele. */
  package?: string | null;
  /** Só o que o grupo muda em relação ao padrão do catálogo DESTE app. */
  capabilities: Record<string, PolicyName>;
  loosened: string[];
  /** `username` vem vazio ou nulo quando a conta da persona foi retirada (29.23); `name` (29.25) é a pessoa. */
  members: { id: string; username: string | null; name?: string | null }[];
  created_at: string;
  updated_at: string;
}

export interface PolicyGroupCreateRequest {
  name: string;
  description?: string;
  capabilities?: Record<string, PolicyName>;
  from_profile_id?: string | null;
  profile_ids?: string[];
}

export interface PolicyGroupPatchRequest {
  name?: string;
  description?: string;
  capabilities?: Record<string, PolicyName | null>;
  /** Lista COMPLETA de membros: quem sai volta a herdar só do padrão. */
  profile_ids?: string[];
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
  /** 29.30: a imagem da persona que a etapa vai publicar (CREATE_POST), para quem aprova ver o que sai. Ausente nas
   *  aprovações sem imagem (e no backend anterior ao campo). */
  image_id?: string | null;
  /** 29.79: a publicação sai com o rótulo de IA do Instagram; `null` sem imagem. */
  rotulo_ia?: boolean | null;
  /** 29.81: o porquê do rótulo: `ia`, `foto_real` (o dono disse) ou `nao_informado` (ninguém disse); `null` sem
   *  imagem ou na etapa gravada antes do campo. */
  rotulo_ia_motivo?: 'ia' | 'foto_real' | 'nao_informado' | null;
  /** 30.61: `plano` (o sim dado na prévia da porta) ou `execucao`. Ausente nas respostas antigas = `execucao`. */
  origem?: 'plano' | 'execucao';
  /** 30.61: até quando o sim do plano vale; nulo nas de execução. */
  expires_at?: string | null;
  plan_version?: number | null;
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
  /** O que a máquina declarou no `hello` (o `worker.yaml` dela). */
  max_slots: number;
  /** 29.82: as vagas que valem — o decidido no painel, ou o declarado sem decisão (o teto do agendador). Ausente =
   *  backend de antes do 29.82. */
  effective_max_slots?: number | null;
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

/** Comando remoto (29.154, ADR-079): o estado dos três interruptores de um worker e o que o agente negociou. */
export interface ComandoRemotoInterruptor {
  worker_id: string;
  /** `comando_remoto.ativo` do config do central (vale na subida). */
  central_ativo: boolean;
  /** O interruptor deste worker no painel (ao vivo). */
  worker_ligado: boolean;
  /** O `worker.yaml` dele anuncia a feature `remote_exec`. */
  agente_anuncia: boolean;
  /** Os três juntos: só com isso um comando é aceito. */
  negociado: boolean;
  /** O central não entra: é a máquina do dono. */
  e_o_central: boolean;
}

export type EstadoDoComando =
  'created' | 'dispatched' | 'running' | 'succeeded' | 'failed' | 'timed_out' | 'cancelled' | 'uncertain' | 'rejected';

/** Um comando remoto. `linha` é a REDIGIDA; `stdout`/`stderr` só vêm no detalhe e já saem redigidos e cortados. */
export interface ComandoRemoto {
  id: string;
  worker_id: string;
  requested_by: string;
  modo: 'linha' | 'argv';
  linha: string;
  pasta: string | null;
  timeout_s: number;
  state: EstadoDoComando;
  exit_code: number | null;
  stdout?: string;
  stderr?: string;
  truncated: boolean;
  duration_ms: number | null;
  reason: string | null;
  created_at: string;
  dispatched_at: string | null;
  finished_at: string | null;
}

export interface ComandoRemotoPedido { linha: string; pasta?: string; timeout_s?: number; idempotency_key?: string }

/** O que `POST /instances/{id}/actions/{action}` devolve agora: algo para ACOMPANHAR, não uma promessa. */
export interface CommandAccepted {
  command_id: string; state: CommandState; deduplicated: boolean;
  /** Só em `install_apk`: o que o backend resolveu instalar, dito ANTES do desfecho. */
  install_target?: { app_id: string; app_name: string; package: string; release_id: string; version_name: string;
                     version_code: number; mechanism: string };
}

/** Resposta de `POST /commands/{id}/verify`. `changed=false` com `verifiable=true` significa "o estado do
 *  aparelho ainda não comprova nada" — o comando segue incerto, esperando uma pessoa. */
export interface CommandVerified { command: Command; changed: boolean; verifiable: boolean }

/**
 * De onde veio a decisão sobre um comando. `panel`: o backend acrescenta ", no painel a partir de <aparelho>" ao
 * motivo, depois de "por <autor>" (só ", a partir de <aparelho>" quando o autor já é `panel`). O contexto não vai na
 * nota: a nota passa pela triagem de credencial, e um id de aparelho fora do padrão (`Pixel_7a-Lab.02`) recusava a
 * decisão inteira (409 `note_looks_secret`) por causa do prefixo.
 */
export type CommandDecisionOrigin = 'panel';

/** A decisão humana sobre um comando incerto. `note` é só o que a pessoa observou. */
export interface CommandResolution {
  outcome: 'succeeded' | 'failed' | 'cancelled'; note?: string; requested_by?: string; origin?: CommandDecisionOrigin;
}

/** O pedido de cancelamento de um comando aberto: não há `outcome` a escolher — quem dá o desfecho é quem executa. */
export interface CommandCancelRequest { note?: string; requested_by?: string; origin?: CommandDecisionOrigin }

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
  /** "Distribuir entre servidores": o backend escolhe `count` aparelhos pela carga de cada máquina.
   *  Exclusivo com `instance_ids` (vai vazio). Item 24.6: sem `app_id`, os apps são os que o COMANDO usa (o painel
   *  não escolhe mais "um app"); com ele, restringe a esse app. */
  distribute?: { count: number; app_id?: string };
  // v0.28 (ADR-040): `credentials`/`consent_credentials` SAÍRAM — a execução não carrega credencial (422 se vier).
  // A senha mora na conta da persona, com consentimento por conta.
  /** v0.29: com `instance_ids`, INTERSEÇÃO (antes substituía). */
  profile_ids?: string[];
  /** v0.29 (ADR-044): os alvos explícitos — o eco da prévia. Destino tirado do texto só executa ecoado aqui
   *  (senão 409 `alvos_nao_confirmados`). Exclusivo com `distribute` (422). */
  targets?: RunTarget[];
  /** v0.29: quantos aparelhos de UMA persona recebem a tarefa. Padrão `one`. */
  device_policy?: DevicePolicy;
}

/** `one` = a pessoa faz uma vez (padrão); `primary` = o aparelho principal; `all` = todos os aparelhos dela, aptos. */
export type DevicePolicy = 'one' | 'primary' | 'all';

/** Um alvo explícito (v0.29): a persona e, opcionalmente, os aparelhos dela e o app da conta que a tarefa usa.
 *  `instance_ids` vazio = o sistema escolhe pela `device_policy`. */
export interface RunTarget {
  profile_id: string;
  instance_ids?: string[];
  app_id?: string | null;
}

/** De onde veio cada alvo da prévia: a seleção (`ui`), o texto do comando, o vínculo (sessão pronta ou principal)
 *  ou o desempate pela carga dos servidores. */
export type TargetOrigin = 'ui' | 'texto' | 'vinculo' | 'balanceamento';

export interface ResolvedTarget {
  instance_id: string;
  profile_id: string | null;
  app_id: string | null;
  origem: TargetOrigin;
  /** Contrato C5: o conjunto de apps do alvo; `app_id` é o primeiro. Vazio quando não há app. Opcional porque o
   *  alvo que chega no `detail` de uma recusa (409 da criação) vem do resolvedor, sem este campo. */
  app_ids?: string[];
}

/**
 * O que a pessoa precisa decidir antes (persona num aparelho com duas, homônimos, texto × seleção). As opções são
 * ids — de persona quando `field` é `profile_id`, de aparelho quando é `instance_id`. O backend tipa a lista como
 * `dict` genérico; este é o formato de `Pergunta.as_dict()` (`alvos.py`).
 */
export interface TargetQuestion {
  code: string;
  question: string;
  field: 'profile_id' | 'instance_id';
  options: string[];
  instance_id: string | null;
  profile_id: string | null;
}

/** `POST /api/runs/targets/resolve`: a prévia dos alvos, com a mesma seleção de `POST /runs`, sem criar nada. */
export interface ResolveTargetsRequest {
  command: string;
  instance_ids?: string[];
  profile_ids?: string[];
  targets?: RunTarget[];
  device_policy?: DevicePolicy;
}

export interface ResolveTargetsResponse {
  targets: ResolvedTarget[];
  questions: TargetQuestion[];
  /** O comando sem os trechos de destino: é o que vai ao casamento de habilidade e ao planejador. */
  command_sem_destinos: string;
  warnings: string[];
}

/** Limites de UMA máquina (tela Limites → Por servidor). `null` = não definido / segue o valor da máquina. */
export interface ServerLimitValues {
  max_slots: number | null;
  boot_parallelism: number | null;
  max_working: number | null;
  min_free_ram_mb: number | null;
  /** v0.26: teto de aparelhos EXISTENTES na máquina, conferido ao provisionar. `null` = sem teto. Só decisão do dono. */
  max_devices?: number | null;
}

export type ServerLimitKey = keyof ServerLimitValues;

export interface ServerLimits {
  worker_id: string;
  name: string;
  is_host: boolean;
  connected: boolean;
  maintenance: boolean;
  /** O que a máquina declara (worker.yaml; para este servidor, config.yaml). */
  declared: ServerLimitValues;
  /** O que foi decidido no painel. `null` = segue o declarado. */
  decided: ServerLimitValues;
  /** O que o agendador e o agente estão usando agora. */
  effective: ServerLimitValues;
  /** Campos que não se editam por aqui nesta máquina → motivo. */
  locked: Partial<Record<ServerLimitKey, string>>;
  online: number;
  working: number;
  devices: number;
  cpu_percent: number | null;
  cpu_count: number | null;
  ram_free_mb: number | null;
  ram_total_mb: number | null;
}

/** Campo ausente = não mexe; `null` = volta ao valor da máquina. */
export type ServerLimitsPatch = Partial<Record<ServerLimitKey, number | null>>;

export interface DistributionPick { instance_id: string; server_id: string; server_name: string; needs_start: boolean }

export interface DistributionPreview {
  requested: number;
  picks: DistributionPick[];
  /** nome do servidor → quantos aparelhos dele */
  per_server: Record<string, number>;
  missing: number;
  reasons: string[];
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
/** `evidence_id`: o print (evidência `screenshot` do item) em que a confirmação se baseia. O servidor o exige no
 * `confirm_done` de etapa com efeito externo (ADR-055): só nota livre não dizia que tela a pessoa viu. */
export interface ResolveRequest { resolution: Resolution; note?: string; evidence_id?: number }

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
/**
 * Corpo de `PUT /api/flows/{id}`. `motivo` (1 a 500 caracteres) vai à trilha e é OBRIGATÓRIO ao ligar um fluxo nascido de prova
 * (400 `motivo_obrigatorio`); `escopo` só vale nesse mesmo gesto (31.150, adendo v1.97): fora dele, `PUT /flows/{id}/scope`.
 */
export interface FlowStatusUpdate {
  status: 'active' | 'disabled';
  motivo?: string;
  escopo?: { profile_ids: string[]; group_ids: string[] };
}

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
  // v0.20 (C2): interesse em prévia desta conexão — substitui o anterior, vale `ttl_s` segundos (5–60).
  | { type: 'watch'; grid: string[]; focus: string | null; ttl_s: number }
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

/** 30.81 (adendo v1.65): o fluxo ensinado no modo treinamento que ainda espera a prova. Só vale para a persona que
 *  ensinou (`persona: null`: a gravação não tinha persona, e ele não casa em aparelho nenhum até a prova). `sessao` é a
 *  gravação de origem (`trn-…`). AUSENTE quando não se aplica (e em backend anterior). */
export interface EnsinadoEmProva {
  persona: string | null;
  sessao: string;
}

/** `GET /api/flows/cobertura` e o campo `flows[]` de `GET /api/instagram/profiles/{id}/capacidades`: quantas
 * etapas do plano-modelo têm receita ativa para a versão promovida do app — os "caminhos mapeados". */
/** `POST /api/flows/similar` (adendo v1.72): só pergunta. `matches` = algum fluxo ativo já casa o comando por inteiro (e então não há sugestão). */
export interface FlowSimilar {
  matches: boolean;
  /** Até 3, nota de 0 a 1 (mínimo 0,9). Nunca o nome do fluxo: o resumo do treino pode trazer o valor demonstrado. */
  suggestions: { ref: string; template: string; score: number }[];
}

export interface FlowCoverage {
  flow_id: string;
  name: string;
  command_template: string;
  package: string | null;
  target_version: string | null;
  steps_total: number;
  steps_with_recipe: number;
  /** zero = só reprodução; parcial = parte por receita; total = a IA faz tudo; desconhecido = plano ilegível. */
  ai_cost: 'zero' | 'parcial' | 'total' | 'desconhecido';
  /** Item 7.7 — US$ esperado por aparelho ao repetir o fluxo (etapas sem receita × custo mediano de `decide` +
   * etapas totais × custo mediano de `verify`, últimos 7 dias). `null` sem histórico de `ai_calls` — "sem base". */
  estimated_usd: number | null;
  status?: string;
  uses?: number;
  /** Só na visão por perfil: quantas vezes este perfil concluiu o fluxo e quando foi a última. */
  times?: number;
  last_at?: string | null;
  /** 30.81: o ensinado ainda sem prova (também em `POST /flows/match`). */
  ensinado_em_prova?: EnsinadoEmProva;
}

/** `GET /api/instagram/profiles/{id}/capacidades` — o que a persona já fez e quanto disso roda sem IA. */
export interface ProfileCapabilities {
  profile_id: string;
  flows: FlowCoverage[];
  steps_driven_by: Record<string, number>;
  recipe_share: number | null;
  interactions: Record<string, number>;
  /** Habilidades ensinadas no modo treinamento que valem para este perfil (item 13.3). */
  trained?: { flow_id: string; name: string; command_template: string; uses: number; status: string;
              last_at: string | null; scope: string }[];
}


// ---------------------------------------------------------------- perfil com contas em vários apps (item 12.1)
/** 31.346 (adendos v1.145 e v1.149): uma tentativa de contato da conta com o app; só ids, horas, minutos e desfechos (sem @ nem e-mail). */
export interface ContatoDaConta {
  iniciado_em: string | null;
  minutos_desde_a_criacao: number | null;
  /** `session_ready`, `auth_challenge`, `uncertain`, `conta_nao_encontrada`, `confirmada`, `parada`, … (aberto: o motor pode ganhar desfechos). */
  desfecho: string;
  /** `cadastro` nos contatos do cadastro guiado; ausente/nulo nas tentativas de login. */
  etapa?: string | null;
  /** O que o motor gravou (até 200 caracteres), já sem identificador. */
  detalhe?: string | null;
}

/** `GET /api/instagram/contas/{id}/ciclo`: o que aconteceu com uma conta do Instagram, da criação à retirada. */
export interface CicloDaConta {
  account_id: string;
  igfarm_account_id: string | null;
  /** v1.149: quem criou a conta. Ausente (central anterior) vale `igfarm`. */
  origem?: 'igfarm' | 'app';
  /** v1.149: de onde contam os minutos dos contatos. Ausente vale `criacao`. */
  referencia?: 'criacao' | 'planejamento';
  criada_em: string | null;
  registrada_em: string | null;
  estado: 'ativa' | 'retirada' | string;
  retirada_em: string | null;
  minutos_ate_o_primeiro_contato: number | null;
  ultimo_desfecho: string | null;
  contatos: ContatoDaConta[];
}

export interface ProfileAccount {
  id: string;
  profile_id: string;
  app_id: string;
  app_name: string | null;
  package: string | null;
  handle: string;
  /** v0.28: conta de PORTAL ou site (app de navegador): onde a credencial pode ser digitada. Nulo = o app inteiro. */
  host?: string | null;
  /** v0.28: com que identificador a conta entra (e-mail no Instagram; usuário no portal). Não é segredo. */
  login_identifier?: string | null;
  status: 'active' | 'disabled' | string;
  session_status: string;
  session_detail: string | null;
  session_verified_at: string | null;
  /** v0.28: a sessão completa no aparelho vinculado (com `stale`). */
  session?: SessionInfo;
  /** v0.28: Conectar / Verificar / Sair desta conta, pela mesma regra que a rota recusa. */
  session_actions?: SessionActions | null;
  /** Login automático existe para este app (hoje só o Instagram); sem ele, a pessoa entra pelo Foco. */
  automated_login: boolean;
  credential_configured: boolean;
  /** v0.28: só metadados; a senha não tem campo. */
  credential?: CredentialInfo;
  consent_at?: string | null;
  /** v1.132 (ADR-087): ciclo de provisionamento. Ausente (central anterior) = `confirmada`. */
  provisioning?: ProvisioningInfo;
  notes: string;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------- conta planejada (v1.132, ADR-087, 31.281/31.283)
export type ProvisioningState = 'planejada' | 'credencial_preparada' | 'aguardando_cadastro_externo'
  | 'aguardando_verificacao' | 'confirmada' | 'falha';
export type ProvisioningEvent = 'iniciar_cadastro' | 'enviado' | 'confirmar' | 'falhar' | 'retomar' | 'cancelar';

export interface ProvisioningInfo {
  state: ProvisioningState;
  /** Endereço DESEJADO (editável até confirmada). O `handle` da conta é o CONFIRMADO e fica vazio até lá. */
  desired_handle: string | null;
  detail: string | null;
  resume_state: ProvisioningState | null;
  confirmed_at: string | null;
  evidence: { kind: 'sessao' | 'declarada' | 'igfarm'; ref: string } | null;
  /** Os eventos que a rota de transição aceita AGORA neste estado. */
  actions: ProvisioningEvent[];
  /** Derivado e só de leitura: confirmada e com sessão pronta no aparelho vinculado. */
  authenticated: boolean;
}

export interface PlannedAccountRequest {
  app_id: string;
  host?: string | null;
  desired_handle?: string | null;
}

export interface HandleSuggestion {
  handle: string;
  source: 'persona_nome' | 'persona_dados' | 'alternativa';
}

/** `POST …/credential/prepare`. A senha só vai em `digitar`; `gerar` e `reutilizar` não a carregam nem a devolvem. */
export interface CredentialPrepareRequest {
  modo: 'gerar' | 'digitar' | 'reutilizar';
  /** Obrigatório e `true`: a caixa marcada na tela vale como o consentimento do ADR-040 para ESTA conta. */
  consent: boolean;
  substituir?: boolean;
  password?: string;
  clonar_de?: string;
  tamanho?: number;
}

export type ProvisioningEvidence =
  | { tipo: 'sessao'; sessao_id: string }
  | { tipo: 'declarada'; handle_confirmado: string };

export interface ProvisioningTransitionRequest {
  evento: ProvisioningEvent;
  /** Comparar e trocar: o estado que a tela viu. */
  estado_esperado: ProvisioningState;
  motivo?: string;
  evidencia?: ProvisioningEvidence;
}

/** `cancelar` apaga a conta: a resposta é esta, não a conta. */
export interface ProvisioningCancelled {
  removida: true;
  credencial_removida: boolean;
}

export type AcaoDeConta = 'preparar_credencial' | 'abrir_contas_e_acesso' | 'usar_credencial_existente' | 'continuar';

/** Um par (persona, app) do comando cuja credencial não está pronta. Sem texto livre: o painel age pelos ids. */
export interface AcaoDeContaItem {
  persona_id: string;
  persona_nome: string;
  app_id: string;
  app_nome: string;
  /** Site da conta (apps de navegador); nulo = o app inteiro. Sem host no item, o painel não inventa um. */
  host?: string | null;
  estado: 'sem_conta' | 'planejada' | 'falha';
  acoes: AcaoDeConta[];
  reutilizavel_de: { account_id: string; app_id: string; app_nome: string }[];
}

export interface ProfileAccountCreateRequest {
  app_id: string;
  handle?: string;
  /** v0.28: conta de portal (normalizado no servidor: minúsculo, sem esquema, caminho nem porta). */
  host?: string | null;
  login_identifier?: string | null;
  password?: string | null;
  /** v0.28: com `password`, obrigatório — sem ele, 409 `consentimento_de_credencial`. */
  consent?: boolean;
  /**
   * 23.9 (ADR-057): id de outra conta DESTA persona cuja senha o cofre clona para a conta nova, sem o valor sair
   * dele. Exclui `password` e `consent` (422); a conta nova nasce sem consentimento. Outra persona → 409
   * `credencial_de_outra_persona`.
   */
  clonar_de?: string | null;
  notes?: string;
}

export interface ProfileAccountPatchRequest {
  handle?: string;
  host?: string | null;
  status?: 'active' | 'disabled';
  /** v1.132: endereço DESEJADO da conta planejada (409 `conta_confirmada` depois de confirmada). */
  desired_handle?: string | null;
  /** v0.28: `auth_required` é o antigo `logged_out` (que agora dá 422). */
  session_status?: 'unknown' | 'session_ready' | 'auth_required' | 'needs_person';
  notes?: string;
}

// ---------------------------------------------------------------- provisionar e aposentar aparelho (v0.26)
/** `POST /api/instances`. `worker_id` nulo = este servidor; worker remoto ainda dá 409. */
export interface InstanceProvisionRequest {
  worker_id?: string | null;
  app_id?: string | null;
  /** Formato do SDK: `system-images;android-34;google_apis_playstore;x86_64`. */
  system_image?: string | null;
  ram_mb?: number | null;
  create?: boolean;
  start?: boolean;
  idempotency_key?: string | null;
}

export interface InstanceProvisionAccepted {
  instance: Instance;
  instance_id: string;
  command_id: string | null;
  command_state: string | null;
  deduplicated: boolean;
  start: 'not_requested' | 'after_create';
}

export interface InstanceRetired {
  instance_id: string;
  retired_at: string;
  avd_removed: boolean;
}

// ---------------------------------------------------------------- visão por aplicativo (item 12.2)
export interface AppOverview {
  app_id: string;
  name: string;
  package: string;
  has_catalog: boolean;
  automated_login: boolean;
  accounts: number;
  accounts_ready: number;
  devices: Record<string, number>;
  default_on_devices: number;
  runs: number;
  runs_completed: number;
  last_run_at: string | null;
  ai_usd: number;
  recipes: Record<string, number>;
  flows: number;
  releases: number;
  days: number;
}

export interface AppDetail {
  app_id: string;
  name: string;
  package: string;
  activity: string | null;
  has_catalog: boolean;
  automated_login: boolean;
  accounts: { id: string; profile_id: string; username: string; handle: string; status: string;
              session_status: string; session_verified_at: string | null }[];
  devices: { instance_id: string; state: string; observed_version_name: string | null; verified_at: string | null;
             drift_kind: string | null }[];
  runs: RunSummary[];
  ai_usd_by_day: { day: string; usd: number }[];
  steps: { origem: string; status: string; n: number }[];
  recent_failures: { title: string; status_detail: string | null; finished_at: string | null; run_id: string }[];
  recipes: { id: number; step_key: string | null; app_version: string; status: string; replay_ok: number;
             replay_fail: number; created_at: string; last_used_at: string | null }[];
  flows: { id: string; name: string; command_template: string; uses: number; status: string }[];
  days: number;
}


// ---------------------------------------------------------------- modo treinamento (itens 13.1–13.3)
/** O alvo do toque (`recorder`): o elemento, e os filhos rotulados quando o contêiner não tem identidade própria. */
export interface TrainingTarget {
  text?: string; desc?: string; resource_id?: string; class_name?: string; unique?: string[];
  filhos?: TrainingTarget[];
}

export interface TrainingInput {
  session_id: string;
  seq: number;
  ts: string;
  type: 'tap' | 'long_press' | 'swipe' | 'text' | 'key' | 'open_app';
  x: number | null; y: number | null; x2: number | null; y2: number | null;
  key_name: string | null;
  /** null quando não pôde ser gravado (senha, código, tela sensível). */
  text: string | null;
  has_text: boolean;
  text_len: number | null;
  package: string | null;
  app_id: string | null;
  target: TrainingTarget | null;
  screen_title: string | null;
  screen_lines: string[];
  sensitive: boolean;
}

export interface TrainingStep {
  key: string;
  title: string;
  goal: string;
  inputs: number[];
  side_effect: boolean;
  capability: string | null;
  bindings: { name: string; value: string }[];
  app_id: string | null;
  postcondition: { kind: 'text_visible' | 'app_foreground' | 'element_present' | 'model_judged'; value: string; description: string };
  /** 31.129 (adendo v1.84): preenchido pelo ensino ao salvar, com os pacotes vistos na demonstração; a proposta da IA não o traz. */
  pacotes_aceitos?: string[];
}

export interface TrainingProposal {
  summary: string;
  command_template: string;
  parameters: { name: string; example: string; description: string }[];
  steps: TrainingStep[];
  discarded: { seq: number; why: string }[];
  questions: string[];
  app_id?: string | null;
  /** v1.63: respostas acumuladas da pessoa (a mesma pergunta substitui a anterior); backend anterior não manda. */
  answers?: TrainingAnswer[];
}

/** Uma resposta da pessoa a uma pergunta da proposta (v1.63): pergunta 1–300 e resposta 1–500 caracteres. */
export interface TrainingAnswer {
  question: string;
  answer: string;
}

export interface TrainingSession {
  id: string;
  instance_id: string;
  profile_id: string | null;
  app_id: string | null;
  intent: string;
  status: 'recording' | 'recorded' | 'proposed' | 'saved' | 'discarded';
  operator: string | null;
  proposal: TrainingProposal | null;
  /** 31.189 (adendo v1.109): cópia da `proposal` com o dado da persona trocado por `{nome}`, só para EXIBIR; o painel nunca a devolve. Ausente = backend anterior. */
  proposal_exibicao?: TrainingProposal | null;
  flow_id: string | null;
  created_at: string;
  finished_at: string | null;
  updated_at: string;
  inputs?: TrainingInput[];
  input_count?: number;
  /** 31.111 (adendo v1.75): `null` na gravação comum; na sessão aberta a partir de uma etapa que falhou, de onde ela veio. */
  origin?: TrainingOrigin | null;
  /** 31.131 (adendo v1.87): a sessão foi aberta como prova (não é uso real); o fluxo que ela salva leva a mesma marca. Ausente em backend anterior. */
  nascido_de_prova?: boolean;
}

/** A etapa que falhou e deu origem ao treino (adendo v1.75). `context` só vem no `GET /training/{id}`. */
export interface TrainingOrigin {
  run_id: string;
  step_id: string;
  step_key: string;
  /** A última tentativa da etapa; `null` se ela nunca rodou. */
  attempt_id: string | null;
  /** O motivo literal do executor lido AGORA; `null` se a limpeza de execuções velhas apagou a etapa. */
  motivo: string | null;
  /** 31.313 (adendo v1.138): a sessão nasceu de uma exploração que não chegou lá; só vem quando verdadeiro. */
  exploracao?: boolean;
  context?: TrainingOriginContext;
  /** 31.116 (F4): o diagnóstico determinístico da falha. Ausente ou `null` em backend anterior e na sessão sem tentativa lida. */
  diagnostico?: TrainingDiagnostico | null;
}

/** `origin.diagnostico` (learning/domain/ensino_da_falha.py): a causa provável dita em palavras, o que mostrar e os fatos. */
export interface TrainingDiagnostico {
  /** O código fechado da causa (`indeterminada` = "não deu para saber"). */
  causa: string;
  /** Em poucas palavras: "o app mudou de versão". */
  rotulo: string;
  /** O que a pessoa mostra ou responde ao corrigir. */
  pergunta: string;
  fatos: { codigo: string; valor: string }[];
  proposta?: { tipo: string; alvo: string } | null;
  /** 1 = a própria tentativa; 0 = só o tipo da falha decidiu. */
  amostra?: number;
}

export interface TrainingOriginContext {
  /** `false` (e nada mais) se a etapa foi apagada. */
  disponivel: boolean;
  trilha?: { step_id: string; step_key: string; titulo: string; status: StepStatus; motivo?: string | null; falhou: boolean }[];
  esperado?: { kind: string; value: string | null; description: string | null } | null;
  tentativa?: { number: number; status: string; erro: string | null; failure_kind: string | null; failure_screen: string | null;
                strategy: string | null } | null;
  evidencias?: { id: number; kind: string; nota: string | null; disponivel: boolean }[];
}

/** Corpo de `POST /api/training/from-run` (adendo v1.75): o aparelho é o da etapa; o controle é o da pessoa. */
/**
 * 31.116 parte 2 (adendo v1.80): o que "Ensinar a corrigir" pré-preenche antes de existir a sessão, pelo diagnóstico do F4.
 * A resposta é `null` quando a etapa não tem tentativa; `rotulo` e `causa` vêm nulos quando o diagnóstico falhou.
 * Adendo v1.82: `causa` é o código do diagnóstico (`indeterminada` = "não deu para saber") e a `pergunta` é a do estado
 * da etapa (em `waiting_user`, a própria dela, mesmo com o diagnóstico em erro). Ausente = backend anterior ao v1.82.
 */
export interface EnsinoSugerido {
  intent: string;
  pergunta: string | null;
  rotulo: string | null;
  causa?: string | null;
  /** 31.313 (adendo v1.138): a etapa é de EXPLORAÇÃO (a IA a descobriu e não chegou lá). `intent` já vem como "Ensinar à IA como fazer: <frase da chave>",
   *  nunca com o pedido. Ausente em etapa comum e em backend anterior. */
  exploracao?: boolean;
  /** 31.313: só com `exploracao`: a exploração parou no teto (de ações, chamadas ou custo), em vez de falhar. */
  parou_no_teto?: boolean;
}

export interface TrainingFromRunBody {
  run_id: string;
  step_id: string;
  lease_id: string;
  intent?: string;
  app_id?: string | null;
  profile_id?: string | null;
}

/** Resposta do desfazer a última entrada (adendo v1.70): a sessão, mais a entrada que saiu. */
export interface TrainingUndoResult extends TrainingSession {
  undone: { seq: number; type: string };
}

/** Uma etapa no relatório do salvar, da prévia e do refazer: `recipe` diz se roda (ou rodaria) sem IA, e `reason` por quê. */
export interface TrainingStepReport {
  key: string;
  title: string;
  recipe: boolean;
  reason: string;
  /** 31.141 (31.140 no backend): os pacotes vizinhos que a etapa também aceita, como a prévia os calcula; ausente em backend anterior. */
  pacotes_aceitos?: string[];
}

/** 31.88 F2 (adendo v1.71): o escopo que o salvar gravou (ou a prévia gravaria). `on_proof` é a escolha "Vale para". */
export interface TrainingScope {
  on_proof: 'todos' | 'quem_ensinou';
  profile_ids: string[];
  group_ids: string[];
}

export interface TrainingSaveResult {
  session: TrainingSession;
  flow_id: string;
  steps: TrainingStepReport[];
  /** 31.83 (adendo v1.57): o que o salvar aceitou mas vale avisar; backend anterior não manda. */
  warnings?: string[];
  /** 30.81: o fluxo salvo espera a prova; só vale para a persona que ensinou. */
  ensinado_em_prova?: EnsinadoEmProva;
  scope?: TrainingScope;
}

/** `POST /training/{id}/preview` (adendo v1.58): o que o salvar faria, sem gravar. */
/** 31.128 (adendo v1.86): a etapa cuja pós-condição `text_visible` já aparece na tela de partida, com até 3 textos da tela seguinte. */
export interface SugestaoPronta {
  kind: string;
  value: string;
  texto: string;
}

export interface PosCondicaoQueJaVale {
  /** A `key` da etapa na proposta. */
  etapa: string;
  /** O texto da pós-condição que já vale. */
  valor: string;
  sugestoes: string[];
  /** 31.142 (adendo v1.91): na mesma ordem de `sugestoes`; o que o botão aplica (`kind` e `value`) e o texto do botão. */
  sugestoes_prontas: SugestaoPronta[];
  /** A mesma frase da linha de `warnings` (e da recusa). */
  message: string;
}

export interface TrainingPreview {
  steps: TrainingStepReport[];
  warnings: string[];
  /** 31.142 (adendo v1.91): `duplicate_command` quando o comando já existe (200, não 409); nulo sem recusa; backend anterior não manda. */
  code?: string | null;
  message?: string | null;
  /** 31.128 (adendo v1.86): ao lado de `warnings`; vazia sem ocorrência; backend anterior não manda. */
  pos_condicoes_ja_valem?: PosCondicaoQueJaVale[];
  /** Adendo v1.71; backend anterior não manda. */
  scope?: TrainingScope;
}

/** `POST /training/{id}/recipes` (adendo v1.58): `created` conta as receitas gravadas nesta chamada. */
export interface TrainingRecipesResult {
  session: TrainingSession;
  flow_id: string;
  steps: TrainingStepReport[];
  created: number;
  /** 30.81: no topo, ao lado de `flow_id`; ausente quando o fluxo já não espera. */
  ensinado_em_prova?: EnsinadoEmProva;
}

// ---------------------------------------------------------------- ensino v2 e habilidades (fase F, `features.skills`)
export type SkillState = 'draft' | 'candidate' | 'validated' | 'published' | 'deprecated' | 'disabled';
export type TeachingStatus = 'open' | 'demonstrating' | 'proposing' | 'asking' | 'validating' | 'ready' | 'published'
  | 'discarded';
export type TeachingSource = 'instruction' | 'demonstration' | 'hybrid' | 'correction' | 'successful_execution';
export type TeachingQuestionKind = 'ambiguity' | 'missing_parameter' | 'effect_confirmation' | 'scope' | 'policy';

/** Um nó do documento `automation/v1alpha1`, só com o que o painel mostra. */
export interface SkillNodeView {
  id: string;
  capability?: string;
  goal?: { title: string; goal: string };
  app?: string;
  side_effect?: boolean;
  depends_on?: string[];
  with?: Record<string, string>;
  verification?: { postcondition?: { kind: string; value: string; description?: string } };
}

export interface SkillDocumentView {
  apiVersion: string;
  kind: string;
  metadata: { id: string; name: string; app: string; description?: string };
  spec: {
    invocation: { command_template: string; examples?: string[] };
    parameters?: { name: string; type: string; required?: boolean; example?: string; description?: string }[];
    nodes: SkillNodeView[];
  };
}

export interface SkillCandidateAnnotations {
  evidence: Record<string, number[]>;
  discarded: { seq: number; why: string }[];
  assumptions: string[];
  parameters: { name: string; type: string; examples: string[]; description: string; required: boolean }[];
  preconditions: string[];
  postconditions: { node: string; kind: string; value: string }[];
  suggested_proofs: Record<string, unknown>[];
  effects: { node: string; capability: string | null; description: string }[];
  risks: string[];
}

export interface SkillCandidate {
  id: string;
  teaching_id: string;
  seq: number;
  status: 'proposed' | 'rejected' | 'accepted' | 'superseded';
  validation_status: string;
  generated_by: string;
  content_hash: string;
  version_id: string | null;
  document: SkillDocumentView;
  annotations: SkillCandidateAnnotations;
  created_at: string;
  updated_at: string;
}

export interface TeachingQuestion {
  id: number;
  kind: TeachingQuestionKind;
  key: string | null;
  origin: 'ai' | 'compiler' | 'person' | 'system';
  text: string | null;
  target: Record<string, unknown> | null;
  candidate_id: string | null;
}

export interface TeachingTurn {
  id: number;
  kind: 'instruction' | 'question' | 'answer' | 'correction' | 'note';
  author: 'person' | 'ai' | 'compiler' | 'system';
  reply_to: number | null;
  /** O alvo do turno: na correção, `{run_id, step_id}` da etapa corrigida. */
  target?: Record<string, unknown> | null;
  body: string | null;
  candidate_id: string | null;
  created_by: string | null;
  created_at: string;
}

export interface TeachingSessionSummary {
  id: string;
  instruction: string;
  skill_id: string | null;
  /** O ensino que melhora a versão N da habilidade. */
  base_version?: number | null;
  app_id: string | null;
  status: TeachingStatus;
  validation_status: string;
  result_version_id: string | null;
  source: TeachingSource;
  created_at: string;
  updated_at: string;
}

export interface TeachingSessionView extends TeachingSessionSummary {
  base_version: number | null;
  profile_id: string | null;
  operator: string | null;
  closed_at: string | null;
  demonstrations: { id: string; seq: number; kind: 'recording' | 'run'; training_session_id: string | null;
                    run_id: string | null }[];
  turns: TeachingTurn[];
  candidates: SkillCandidate[];
  current_candidate: SkillCandidate | null;
  open_questions: TeachingQuestion[];
  /** Erros de compilação da candidata atual, lidos agora. */
  errors: string[];
}

export interface SkillSummary {
  ref: string;
  skill_id: string;
  version: number;
  name: string;
  app_id: string | null;
  state: SkillState;
  command_template: string | null;
  schema_version: number;
  content_hash: string;
  intact: boolean;
  state_at: string;
  /** Fase J: o fluxo que esta habilidade adotou (conversão). Ausente em backend anterior à fase J. */
  legacy_flow_id?: string | null;
}

/** Uma versão de habilidade com o conteúdo e o histórico (`GET/POST /api/skills/{id}/versions/{n}…`). */
export interface SkillVersionDetail {
  ref: string;
  skill_id: string;
  version: number;
  state: SkillState;
  schema_version: number;
  content_hash: string;
  command_template: string | null;
  parent_version: number | null;
  state_detail: string | null;
  history: { from: SkillState | null; to: SkillState; reason: string | null; by: string | null; at: string }[];
}

/** Problema da conversão de fluxo (fase J): da ida e volta pelo compilador (`origin: plan`) ou do compilador. */
export interface SkillIssue {
  code: string;
  message: string;
  path: string;
  severity: 'error' | 'warning';
  origin: 'plan' | 'document';
}

/** `POST /api/flows/{id}/adopt`: v1 publicada (o plano do fluxo) e v2 em rascunho (o documento descompilado). */
export interface FlowConversion {
  flow_id: string;
  skill_id: string;
  published: SkillVersionDetail;
  draft: SkillVersionDetail;
  warnings: SkillIssue[];
}

/** `POST /api/flows/{id}/release`: o fluxo religado e os rascunhos da conversão apagados. */
export interface FlowConversionUndone {
  flow_id: string;
  skill_id: string;
  flow_status: 'active';
  discarded_drafts: string[];
  versions: SkillSummary[];
}

/** Uma versão resumida, como a vitrine a mostra. */
export interface StoreVersion {
  id: string;
  version_name: string;
  version_code: number;
  status: string;
  channel: ReleaseChannel;
}

/** Um cartão da vitrine (`GET /api/app-store`). As contagens excluem a loja (Play Store), que não é destino. */
export interface AppStoreEntry {
  app_id: string;
  name: string;
  package: string;
  category: AppCategory | null;
  builtin: boolean;
  has_catalog: boolean;
  label: string | null;
  /** Release de onde servir o ícone (`releaseIconUrl`); `null` = nenhuma versão com ícone servível. */
  icon_release_id: string | null;
  promoted: StoreVersion | null;
  latest: StoreVersion | null;
  releases: number;
  devices_with_app: number;
  by_version: { release_id: string; version_name: string; version_code: number; channel: ReleaseChannel;
                devices: number }[];
  /** Aparelhos com o app numa versão que o catálogo não conhece (instalada por fora). */
  other_version: number;
  /** Aparelhos numa versão MENOR que a promovida: é a "atualização disponível". */
  outdated: number;
  /** Versão pedida e ainda não instalada, sem falha: vai chegar sozinha. */
  pending: number;
  installing: number;
  failed: number;
  attention: string[];
}

/** Proxy HTTP nomeado. Sem usuário e senha: o proxy global do Android não tem autenticação. */
export interface ProxyProfile {
  id: string;
  name: string;
  host: string;
  port: number;
  created_at: string;
  created_by: string | null;
  /** Aparelhos que têm este proxy pedido. */
  devices: number;
}

export interface ProxyDeviceState {
  instance_id: string;
  worker_id: string | null;
  device_state: string;
  /** `false` = ninguém pediu nada para este aparelho: o proxy dele é o que já estava lá. */
  managed: boolean;
  desired_proxy_id: string | null;
  observed_value: string | null;
  state: 'pending' | 'applying' | 'applied' | 'failed' | null;
  detail: string | null;
  verified_at: string | null;
}

export interface ProxyList {
  profiles: ProxyProfile[];
  devices: ProxyDeviceState[];
}

// ---- Rede por aparelho (contrato C3, ADR-056) ----------------------------------------------------------------------
// Só os tipos: as rotas `/api/network/*` são do 25.2/25.8. O proxy acima é o legado da 041.

export type NetworkProfileKind = 'vpn' | 'proxy';
export type NetworkProtocol = 'wireguard' | 'singbox' | 'http' | 'socks5';
/** `livre` = sem exigência (padrão); `exigida` = tarefa só com a rede verificada; `exigida_com_bloqueio` = idem, e o
 *  aparelho bloqueia o tráfego fora da VPN. */
export type NetworkPolicy = 'livre' | 'exigida' | 'exigida_com_bloqueio';
/** Só `trafego_verificado` libera tarefa com política exigida (ADR-056 §3). */
export type NetworkState = 'pendente' | 'configurado' | 'conectado' | 'trafego_verificado' | 'parcial';

/** Perfil de VPN ou de proxy. Nunca traz segredo: só `has_secret`. */
export interface NetworkProfile {
  id: string;
  name: string;
  kind: NetworkProfileKind;
  protocol: NetworkProtocol;
  endpoint_host: string;
  endpoint_port: number;
  has_secret: boolean;
  params: Record<string, unknown>;
  created_at: string;
  created_by: string | null;
}

/** Desejado × observado da rede de um aparelho. */
export interface DeviceNetwork {
  instance_id: string;
  vpn_profile_id: string | null;
  proxy_profile_id: string | null;
  policy: NetworkPolicy;
  desired_rev: number;
  applied_rev: number | null;
  state: NetworkState;
  detail: string | null;
  error: string | null;
  egress_ipv4: string | null;
  egress_ipv6: string | null;
  verified_at: string | null;
  updated_at: string;
  updated_by: string | null;
  /** A prova do teste de vazamento (política `exigida_com_bloqueio`), guardada na linha do aparelho: vale quando
   *  `leak_rev === desired_rev` e `leak_result === true`. `false` = vazou; `null` = não concluiu, ou nunca testado
   *  (`leak_rev` nulo). Sobrevive a reinício do backend; revisão nova, cliente VPN novo, wipe e "Testar" a apagam. */
  leak_rev: number | null;
  /** A instalação do cliente VPN testada: `<versão> (<código>) <pasta de instalação>`. */
  leak_client: string | null;
  leak_result: boolean | null;
  leak_at: string | null;
  leak_detail: string | null;
  /** Ensaio marcado e ainda sem desfecho (em curso, ou interrompido por um reinício do backend). */
  leak_pending: boolean;
}

/** Uma medição da saída feita de dentro do aparelho; `null` nos booleanos = não medido. */
export interface NetworkMeasurement {
  id: number;
  instance_id: string;
  measured_at: string;
  method: string;
  egress_ipv4: string | null;
  egress_ipv6: string | null;
  dns_resolver: string | null;
  udp_ok: boolean | null;
  per_app: Record<string, unknown>;
  leak_blocked: boolean | null;
  detail: string | null;
  /** As duas pernas de UDP (29.5), só em `last_measurement` da listagem (`rede.listar_aparelhos`), derivadas do
   *  `detail`: DNS por UDP e NTP (o UDP que não é DNS). `udp_ok` é o E das duas. `null` = o `detail` não diz;
   *  ausente = backend de antes do 29.5. UDP ainda NÃO decide `trafego_verificado`. */
  udp_dns_ok?: boolean | null;
  udp_ntp_ok?: boolean | null;
}

// ---- Rede por aparelho: envelope das rotas (25.8) ------------------------------------------------------------------
// As entidades acima (NetworkProfile, DeviceNetwork, NetworkMeasurement) são o contrato C3; os tipos abaixo
// embrulham cada rota EXATAMENTE como `backend/app/devices/rede.py` devolve (a frente D1: `listar_perfis`,
// `listar_aparelhos`, `atribuir`, `_resposta_de_pedido`) — não são um desenho livre do painel. Achado do revisor
// no 25.8: a versão anterior inventava envelopes soltos que o backend real nunca manda.

/** `POST /api/network/profiles`: o segredo só entra aqui (canal sensível), nunca volta em nenhuma leitura. */
export interface NetworkProfileCreateRequest {
  name: string;
  kind: NetworkProfileKind;
  protocol: NetworkProtocol;
  endpoint_host: string;
  endpoint_port: number;
  secret?: string;
  params?: Record<string, unknown>;
}

/**
 * `PUT /api/network/profiles/{id}` (31.291, adendo v1.134): a única edição de perfil, só a saída esperada. Campo omitido
 * fica como está; `null` tira a saída esperada daquela família; pelo menos um dos dois é obrigatório.
 */
export interface NetworkProfileSaidaRequest {
  egress_esperado?: string | null;
  egress_esperado_ipv6?: string | null;
  /** Até 300 caracteres, sem segredo. Obrigatório (409 `egress_esperado_protegido`) no perfil de uma conta do igfarm em uso. */
  motivo?: string;
}

/** Um perfil na listagem, com os aparelhos que o pedem hoje (`rede.listar_perfis` → `_em_uso`). */
export interface NetworkProfileListed extends NetworkProfile {
  in_use: string[];
}

/** `GET /api/network/profiles`. */
export interface NetworkProfileList {
  profiles: NetworkProfileListed[];
}

/** O proxy HTTP global legado (migração 041) rebaixado: `applied` vale `configurado` NO MÁXIMO (ADR-056 §2). */
export interface LegacyProxyState {
  proxy_id: string | null;
  name: string | null;
  value: string | null;
  state: 'pending' | 'applying' | 'applied' | 'failed';
  observed_value: string | null;
  verified_at: string | null;
  detail: string | null;
  effective_state: NetworkState | null;
}

/** Uma linha de `GET /api/network/devices` (`rede.listar_aparelhos`): o aparelho, o desejado × observado novo
 *  (`network`, `null` = nunca pedido nada), o legado da 041 e o que falta — tudo já resolvido pelo backend, nunca
 *  recombinado no painel (o combinar client-side era a causa do achado do revisor: heurística de conta real que
 *  ficava silenciosa quando a chamada separada falhava). */
export interface NetworkDeviceRow {
  instance_id: string;
  worker_id: string | null;
  external: boolean;
  device_state: string;
  network: DeviceNetwork | null;
  effective_state: NetworkState | null;
  legacy_proxy: LegacyProxyState | null;
  /** Motivo de quarentena, se houver (`st.quarentena`); aparelho em quarentena não recebe rede (ADR-056 §7). */
  restriction: string | null;
  /** `null` = nenhuma persona vinculada; senão, os `@usuario` vinculados — já resolvido pelo backend
   *  (`rede._conta_real`), nunca uma heurística do painel. */
  real_account: string | null;
  pending: 'aplicar' | 'verificar' | null;
  last_measurement: NetworkMeasurement | null;
  /** Os OUTROS aparelhos com a mesma última saída medida (v4 ou v6) — aviso, não bloqueio (ADR-056 §1, 25.5). */
  egress_shared_with: string[];
  /** A saída que o perfil da saída final (o proxy, se há; senão a VPN) declara em `params.egress_esperado*` (29.6);
   *  `null` = o perfil não declara. Ausente = backend de antes do 29.6. */
  egress_expected?: NetworkExpectedEgress | null;
  /** A saída medida NESTA revisão é a esperada? `null` = sem esperada, ou a revisão pedida ainda não foi medida.
   *  `false` deixa o aparelho `parcial` (com política exigida, a tarefa espera). */
  egress_matches?: boolean | null;
  /** Este aparelho ainda sai pela rede da casa? A saída medida dele contra a do próprio central (29.20). Ausente =
   *  backend de antes do 29.20. `null` em qualquer campo = sem medida (nunca "limpo"). */
  egress_home?: NetworkEgressHome;
}

/** O veredito "sai pela casa" de um aparelho (`rede_saida_central.SaidaPelaCasa.como_dict()`): `ipv4`/`ipv6` = a saída
 *  medida é a do central (IPv6: mesmo /64); `ipv6_outside_profile` = IPv6 medido com o perfil de VPN sem IPv6 (sai
 *  direto, fora do túnel); `leaves_by_home` = resumo (`true` se algum acusa; `false` só com o IPv4 medido diferente e
 *  nada incerto; senão `null`). `reason` diz o porquê, inclusive o que não foi medido. */
export interface NetworkEgressHome {
  ipv4: boolean | null;
  ipv6: boolean | null;
  ipv6_outside_profile: boolean | null;
  leaves_by_home: boolean | null;
  /** Em que `leaves_by_home` se apoia: `measured` = a saída medida contra a do central; `presumed` = aparelho SEM rede
   *  pedida e sem medida que o contradiga (sem perfil, a saída é a da casa; `leaves_by_home` é `true`); `null` = sem
   *  veredito. Ausente = backend de antes da extensão do 29.20. */
  basis?: 'measured' | 'presumed' | null;
  /** Os IPs em que o veredito se apoia e de onde vieram: `device_network` (rede pedida) ou `probe_no_network` (a sonda de
   *  IP do aparelho sem rede). `null` = nada medido que valha. */
  measured?: { ipv4: string | null; ipv6: string | null; measured_at: string | null; source: 'device_network' | 'probe_no_network' } | null;
  reason: string;
}

/** A saída pública medida do PRÓPRIO central (`central_egress` de `GET /api/network/devices`): `null` na família =
 *  não medida, com o motivo em `reason` (`"ok"` quando as duas valem). */
export interface NetworkCentralEgress {
  ipv4: string | null;
  ipv6: string | null;
  measured_at: string | null;
  reason: string;
}

/** A saída esperada de um aparelho e o perfil que a declara (`rede.SaidaEsperada.como_dict()`). */
export interface NetworkExpectedEgress {
  ipv4: string | null;
  ipv6: string | null;
  profile_id: string;
  profile_name: string;
}

/** Um aviso de SAÍDA na prévia da atribuição (`rede._avisar_da_saida`, 29.6). Nunca recusa: `saida_dedicada_compartilhada`
 *  = o perfil com saída esperada fica em mais de um aparelho (`shared_with`); `saida_dedicada_trocada_por_compartilhada`
 *  = o aparelho sai de um perfil com saída esperada para um sem. */
export interface NetworkEgressWarning {
  code: string;
  message: string;
  profile_id?: string;
  shared_with?: string[];
}

/** `GET /api/network/devices`. */
export interface NetworkDeviceList {
  devices: NetworkDeviceRow[];
  /** A saída do central, referência de `egress_home` (29.20). Ausente = backend de antes do 29.20. */
  central_egress?: NetworkCentralEgress;
}

/** `POST /api/network/assign`: perfis e política para os aparelhos escolhidos; `dry_run` = só prévia (sem gravar).
 *  Campo omitido (não `null`) = fica como está — `policy` incluída só ganha valor quando a pessoa mexe nela.
 *  `confirm_real_account`: os aparelhos com conta real que a pessoa autoriza a mudar de saída, UM A UM
 *  (ADR-056 §7) — nunca um booleano para o lote inteiro. */
export interface NetworkAssignRequest {
  instance_ids: string[];
  vpn_profile_id?: string | null;
  proxy_profile_id?: string | null;
  policy?: NetworkPolicy;
  confirm_real_account?: string[];
  dry_run?: boolean;
}

/** O desejado de um aparelho (`rede._Desejo.como_dict()`), no "de" e no "para" da prévia. */
export interface NetworkDesejo {
  vpn_profile_id: string | null;
  proxy_profile_id: string | null;
  policy: NetworkPolicy;
}

/** Um aparelho na prévia (`dry_run`) ou no resultado do lote (`rede._Item.como_dict()`). `code` só vem com
 *  `outcome: 'refused'` — `real_account_confirm_required` é o que o painel usa para pedir a confirmação extra. */
export interface NetworkAssignDevice {
  id: string;
  outcome: 'refused' | 'unchanged' | 'would_assign' | 'assigned';
  code?: string;
  reason: string;
  from: NetworkDesejo;
  to: NetworkDesejo;
  reapply: boolean;
  warnings: string[];
  /** Avisos sobre a saída esperada (29.6); ausente num backend de antes. */
  egress_warnings?: NetworkEgressWarning[];
}

export interface NetworkAssignResult {
  accepted: boolean;
  dry_run: boolean;
  devices: NetworkAssignDevice[];
}

/** Leitura do Firewall do Windows do central para os aparelhos de OUTRA máquina (25.7, `rede_firewall.avaliar`).
 *  Só leitura: `commands` é o que o DONO roda num PowerShell de administrador — a plataforma nunca mexe no firewall. */
export type NetworkFirewallState = 'liberado' | 'bloqueado' | 'sem_regra' | 'regra_obsoleta' | 'desligado' | 'desconhecido';

export interface NetworkFirewallReading {
  state: NetworkFirewallState;
  detail: string;
  endpoint: string | null;
  profile: string | null;
  interface: string | null;
  endpoint_is_local: boolean | null;
  allowing_rules: string[];
  blocking_rules: string[];
  commands: string[];
  checked_at: string;
  /** Da leitura por porta, interface, origem e perfil (29.8); ausentes numa leitura antiga em cache. */
  lan_subnet?: string | null;
  warnings?: string[];
  stale_rules?: string[];
  ignored_rules?: string[];
  missing?: string[];
  inspect_command?: string | null;
  revert_command?: string | null;
}

/** `remote_access` de `GET /api/network/server` e corpo de `POST /api/network/server/firewall-check` (25.7). */
export interface NetworkRemoteAccess {
  lan_endpoint: string | null;
  wireguard_udp_port: number;
  remote_peers: string[];
  /** `null` = ainda não lido (o GET não roda PowerShell; lê com par remoto ou pelo POST). */
  firewall: NetworkFirewallReading | null;
}

export interface NetworkServerPeer {
  instance_id: string;
  address: string;
  public_key: string;
  last_connection: string | null;
  remote: boolean;
}

/** `GET /api/network/server` — o sing-box do central (25.4), sem segredo nenhum. */
export interface NetworkServerStatus {
  binary_present: boolean;
  running: boolean;
  pid: number | null;
  started_at: string | null;
  signature: string | null;
  in_sync: boolean;
  server_address: string;
  subnet: string;
  wireguard_udp_port: number;
  proxy: string | null;
  server_public_key: string | null;
  peers: NetworkServerPeer[];
  proxy_users: string[];
  remote_access: NetworkRemoteAccess;
  detail: string | null;
}

/** `POST /api/network/devices/{iid}/verify` e `.../reapply` — os dois são 202 "pedido registrado" com o MESMO
 *  desenho (`rede._resposta_de_pedido`), nunca o aparelho medido: quem aplica (25.4) e mede (a sonda de saída,
 *  25.5) é a convergência, no próximo ponto seguro do aparelho, e o resultado chega depois na lista de aparelhos.
 *  `executed` é sempre `false`; ler isso como sucesso é o achado do revisor no 25.8. */
export interface NetworkRequestAccepted {
  accepted: true;
  instance_id: string;
  action: 'verify' | 'reapply';
  pending: 'aplicar' | 'verificar' | null;
  desired_rev: number;
  applied_rev: number | null;
  state: NetworkState;
  executed: false;
  reason: string;
}

// ---- Assistente do comando (ADR-047) -------------------------------------------------------------------------------
// Bloco próprio, no fim do arquivo: não se mistura com os tipos de persona que outras ondas mexem.

/** Uma resposta da pessoa a uma pergunta do assistente ou do planejador. */
export interface RefineAnswer {
  field: string;
  question: string;
  answer: string;
}

/** `POST /api/commands/refine`. Com `run_id`, as perguntas pendentes e os alvos vêm daquela execução. */
export interface RefineCommandRequest {
  command: string;
  answers?: RefineAnswer[];
  instance_ids?: string[];
  profile_ids?: string[];
  run_id?: string;
}

export interface RefineQuestion {
  field: string;
  question: string;
  options: string[];
  why: string;
}

/** O comando reescrito em blocos, o que ainda falta e se, pela IA, já dá para planejar (o planejador decide). */
export interface CommandRefinement {
  command: string;
  summary: string;
  questions: RefineQuestion[];
  ready: boolean;
  notes: string[];
  /** v1.132: pares (persona, app) sem credencial pronta; `ready` é false enquanto houver item. Ausente = vazio. */
  acoes_de_conta?: AcaoDeContaItem[];
}

/** `POST /api/runs/{id}/successor`: responde a uma execução em `needs_input` com o comando refinado. */
export interface RunSuccessorRequest {
  command: string;
  mode: RunMode;
}

// =====================================================================================
// Saldo das contas de IA (ADR-051). Estimativa: última leitura do console − gasto em `ai_calls` desde ela.
export type AiBalanceAccount = 'anthropic' | 'openai' | 'gemini' | 'typesafe';
export type AiBalanceState = 'unknown' | 'ok' | 'low' | 'blocked' | 'exhausted';

export interface AiBalance {
  account: AiBalanceAccount;
  label: string;
  console: string;                 // onde ler o saldo de verdade
  currency: 'USD' | 'BRL' | string;
  units_per_usd: number;           // câmbio: quanto da moeda vale US$ 1
  warn_below: number | null;
  block_below: number | null;
  key_configured: boolean;
  roles: string[];                 // funções de IA que esta conta paga hoje
  image: boolean;                  // o gerador de imagem da persona usa esta conta
  // A decisão fechada (Fase 31) usa esta conta (só a typesafe); fora de `roles`, que segura os pedidos pelo saldo.
  closed_decision?: 'shadow' | 'on' | null;
  in_use: boolean;
  anchor_balance: number | null;
  anchor_at: string | null;
  anchor_source: 'manual' | 'console' | 'recarga' | 'fechamento' | 'provider_error' | string | null;
  anchor_note: string | null;
  spent_since_usd: number;
  estimated_balance: number | null;
  estimated_balance_usd: number | null;
  age_h: number | null;
  // Conciliação pelo relatório de custo do provedor (chave de administrador no .env; Gemini não tem).
  admin_key_configured: boolean;
  provider_usd: number | null;     // o que o provedor cobrou na janela da leitura
  external_usd: number;            // além do registrado aqui; já sai do saldo estimado
  reconciled_at: string | null;
  reconcile_error: string | null;
  state: AiBalanceState;
  stale: boolean;                  // com chave de administrador: sem conciliação recente (ou com erro)
  message: string;
}

/**
 * `GET /api/context-retrieval/status` (ADR-063). Só leitura: não consulta código, não chama provedor, não gasta nada e
 * nunca devolve a chave, a pergunta, código ou caminhos de arquivo. A visão do painel é a mesma que o backend decide.
 */
export type ContextRetrievalMode = 'disabled' | 'local_only' | 'shadow' | 'hybrid' | string;
export type ContextRetrievalVisibility = 'public' | 'private' | 'unverified' | 'not_applicable' | string;

export interface ContextRetrievalStatus {
  enabled: boolean;
  mode: ContextRetrievalMode;
  top_k: number;
  provider: { name: string; model: string; available: boolean; unavailable_reason: string | null };
  external_send: {
    allowed: boolean;
    reason: string;
    repository_class: string;
    configured_for_remote: boolean;
    visibility: ContextRetrievalVisibility;
    visibility_verified: boolean;
    remote_visibility_verified: boolean;
    head_public_verified: boolean;
    /** `null` quando não se aplica ou o git não respondeu. */
    worktree_clean: boolean | null;
  };
  budget: { timeout_ms: number; max_calls: number; max_cost_usd: number };
  summary: {
    requests: number;
    by_mode: Record<string, number>;
    cache: { hit: number; miss: number };
    latency_ms: { p50: number | null; p95: number | null; n: number };
    cost_usd: number;
    input_tokens: number;
    fallbacks: Record<string, number>;
    privacy_blocks: Record<string, number>;
  };
}

export interface AiBalancesReport {
  accounts: AiBalance[];
  blocked: AiBalanceAccount[];
  estimated: true;
  note: string;
}

export interface AiBalanceReadingIn {
  balance: number;
  source?: 'manual' | 'console';
  observed_at?: string | null;
  note?: string | null;
}

export interface AiBalanceRuleIn {
  currency?: 'USD' | 'BRL';
  units_per_usd?: number;
  warn_below?: number | null;
  block_below?: number | null;
}

/** Compra de crédito no console do provedor: soma ao saldo estimado de agora (livro-caixa, ADR-051). */
export interface AiBalanceRechargeIn {
  amount: number;
  currency?: 'USD' | 'BRL';
  note?: string | null;
}

// ---- Modo Automático: quem faz e onde (ADR-050) ----------------------------------------------------------------------
// Bloco próprio, no fim do arquivo, como o do assistente do comando.

export interface RunTargetsSuggestRequest {
  command: string;
  max_personas?: number;
}

export interface PersonaEscolhida {
  profile_id: string;
  nome: string;
  motivo: string;
  aderencia: 'alta' | 'media' | 'baixa';
  instance_id: string | null;
  servidor: string | null;
  /** ADR-085 (31.274): o que a automação vai preparar antes de agir (ligar o aparelho, conferir a sessão). Opcional: o
   *  central que ainda não manda não mostra nada. */
  preparo?: string[];
}

export interface PersonaDescartada {
  profile_id: string;
  nome: string;
  motivo: string;
}

export interface PersonaNaoAvaliavel {
  profile_id: string;
  nome: string;
  falta: string;
}

/** A sugestão do modo Automático. `modo`: `ia` (pelo perfil), `texto` (o comando dizia), `distribuir` (app sem conta,
 *  pela carga) ou `nenhuma` (sem o que sugerir — ver `perguntas`/`warnings`). */
export interface RunTargetsSuggestion {
  modo: 'ia' | 'texto' | 'distribuir' | 'nenhuma';
  app_id: string | null;
  /** Contrato C5: o conjunto de apps do comando; `app_id` é o primeiro. */
  app_ids: string[];
  targets: ResolvedTarget[];
  escolhidas: PersonaEscolhida[];
  descartadas: PersonaDescartada[];
  nao_avaliaveis: PersonaNaoAvaliavel[];
  perguntas: string[];
  questions: TargetQuestion[];
  command_sem_destinos: string;
  resumo: string;
  warnings: string[];
}

// ---------------------------------------------------------------- personas em lote (v0.34)
/** `POST /personas/generate/batch`: o mesmo pedido de `generate`, `count` vezes (1 a 10). `create: true` grava cada
 *  rascunho válido; `false` deixa os rascunhos no estado do lote para revisar. */
export interface PersonaBatchRequest extends PersonaGenerateRequest {
  count: number;
  create?: boolean;
}

/** 202 do lote: o progresso chega por `persona.batch.updated`; o estado, por `GET …/batch/{batch_id}`. */
export interface PersonaBatchAccepted {
  batch_id: string;
  count: number;
}

export type PersonaBatchItemStatus = 'pending' | 'generating' | 'ready' | 'created' | 'failed';

export interface PersonaBatchItem {
  index: number;
  status: PersonaBatchItemStatus;
  name: string | null;
  /** Só em `created`. */
  persona_id: string | null;
  /** Só em `ready` (lote sem `create`): o corpo de `POST /personas` para criar a escolhida. */
  draft: PersonaCreateRequest | null;
  error: string | null;
}

/** Estado do lote. Vive na memória do servidor: um reinício o perde (404). */
export interface PersonaBatch {
  batch_id: string;
  prompt: string;
  count: number;
  create: boolean;
  items: PersonaBatchItem[];
  done: boolean;
  created_at: string;
}

// ---- Canais (item 32.5): `GET /api/canais/estado`, só leitura --------------------------------------------------
// A resposta é uma lista fechada de números, horas e códigos: nunca título, texto de mensagem, id de chat, de quadro ou
// de cartão (`backend/app/modules/avisos/infrastructure/estado_sql.py`). O que a tela escreve vem destes códigos.

/** Por que o último envio falhou (lista fechada do backend). */
export type MotivoDeFalhaDeAviso = 'rede' | '401' | '429' | 'tempo_esgotado' | 'outro';

export interface CanalAvisoTelegram {
  ligado: boolean;
  /** Só se os dois segredos (token do bot e chat) estão no `.env`; o valor nunca vem. */
  segredo_presente: boolean;
  /** Linhas da fila por estado: pendente, enviando, enviado, falhou, incerto, descartado. */
  fila: Record<string, number>;
  ultimo_envio_em: string | null;
  ultima_falha: { em: string; motivo: MotivoDeFalhaDeAviso } | null;
  problemas: string[];
}

export interface CanalConversaTelegram {
  ligada: boolean;
  ultima_leitura_em: string | null;
  entradas: Record<string, number>;
  problemas: string[];
}

export interface CanalTrello {
  ligado: boolean;
  webhook_ligado: boolean;
  cadastro_automatico: boolean;
  ultima_reconciliacao_em: string | null;
  cartoes: Record<string, number>;
  entradas: Record<string, number>;
  problemas: string[];
}

export interface CanaisEstado {
  gerado_em: string;
  aviso_telegram: CanalAvisoTelegram;
  conversa_telegram: CanalConversaTelegram;
  trello: CanalTrello;
}

/** 28.24 F4: uma linha de `GET /api/canais/anexos`. Sem caminho de disco, sem `sha256` e sem o nome de quem mandou. */
export interface CanalAnexo {
  id: number;
  canal: string;
  direcao: 'entrada' | 'saida';
  mime: string | null;
  bytes: number;
  estado: 'guardado' | 'recusado' | 'apagado';
  motivo_recusa: string | null;
  criado_em: string;
  apagado_em: string | null;
  /** A mensagem de origem é do dono (só a entrada tem dono; a saída é da Central). */
  do_dono: boolean;
  /** O arquivo sai por `/conteudo`: imagem ou PDF guardado, nunca o de convidado. */
  tem_conteudo: boolean;
  /** Pode ir a um cartão do Trello (`POST .../trello`): só a mensagem do dono, guardada, de um tipo que a entrada aceita. */
  pode_ir_ao_cartao: boolean;
  /** F5: a IA pode descrevê-lo (`POST .../ler`): só a imagem guardada que o dono mandou. */
  pode_ler: boolean;
  /** F5: a descrição que a IA já fez (gravada; ler de novo não custa), ou `null`. */
  descricao: string | null;
  lida_em: string | null;
}

export interface CanalAnexosPagina {
  items: CanalAnexo[];
  total: number;
  limit: number;
  offset: number;
}

export interface CanalAnexoAoCartao {
  anexo_id: number;
  card: string;
  trello_anexo: string;
  /** F5: o anexo já estava no cartão (a rota não anexa duas vezes). */
  ja_estava?: boolean;
}

/** 28.24 F5: resposta de `POST /api/canais/anexos/{id}/ler`. `do_cache`: a descrição já existia e não custou nada. */
export interface CanalAnexoLeitura {
  anexo_id: number;
  descricao: string;
  custo_usd: number;
  do_cache: boolean;
  modelo: string | null;
}

/** 30.61 (adendo v1.32): o selo de uma etapa na prévia da porta. */
export type SeloDaPorta = 'permitido' | 'aprovacao' | 'adiado' | 'recusado' | 'na_execucao';

/** Uma etapa com efeito na prévia da porta (`GET /runs/{id}/porta`). */
export interface ItemDaPorta {
  objective_id: string;
  step_id: string;
  aparelho: string;
  titulo: string;
  persona_rotulo?: string | null;
  profile_id?: string | null;
  app?: string | null;
  acao?: string | null;
  alvo?: string | null;
  objeto_alvo?: Record<string, string> | null;
  selo: SeloDaPorta;
  motivo: string;
  dica: string;
  retry_at: string | null;
  texto?: string | null;
  texto_na_execucao?: boolean;
  tem_imagem?: boolean;
  /** A imagem que a publicação leva (29.30/30.68), só a de sha256 conhecido; o painel a mostra no cartão. */
  image_id?: string | null;
  /** 29.79: a publicação sai com o rótulo de IA do Instagram; `null` sem imagem. */
  rotulo_ia?: boolean | null;
  /** 29.81: o porquê do rótulo: `ia`, `foto_real` (o dono disse) ou `nao_informado` (ninguém disse); `null` sem
   *  imagem ou na etapa gravada antes do campo. */
  rotulo_ia_motivo?: 'ia' | 'foto_real' | 'nao_informado' | null;
  imagem_sha256?: string | null;
  chave: string | null;
  dependentes: string[];
  falhou: boolean;
}

export interface PreviaDaPorta {
  run_id: string;
  hash_do_plano: string;
  /** G1b: quando a prévia foi montada; volta no gesto (sem ele, com item a aprovar, o servidor responde 409 `plano_mudou`). */
  vista_em?: string;
  validade_ate: string;
  custo_rascunhos_usd: number;
  estimativa: boolean;
  parcial: boolean;
  total: boolean;
  itens: ItemDaPorta[];
  na_execucao: { textos_da_tela: number; itens_for_each: number };
}

export interface AprovarPlanoItem {
  step_id: string;
  /** A chave que o dono viu: a da prévia, ou a da prévia do texto EDITADO (`previaDoItem`) quando há `texto` (30.68). */
  chave: string;
  /** O texto editado no cartão; omitido = o da prévia. */
  texto?: string;
}

/** 30.68: a prévia de UM item com o texto proposto (só leitura; `item` null = a etapa não fecha mais um item). */
export interface PreviaDoItem {
  step_id: string;
  /** O texto conferido, já sem espaço nas pontas. */
  texto: string;
  item: ItemDaPorta | null;
}

export interface RenovarPlanoResultado {
  run_id: string;
  renovadas: number;
  vencidas: number;
  validade_ate: string;
}

export interface AprovarPlanoResultado {
  run: RunSummary;
  aprovacoes: string[];
  tiradas: string[];
  validade_ate: string;
}

/** 29.83: um contato do site achado pelo telefone. Só o final do número: nome, empresa e mensagem nunca saem da API. */
export interface PortalContatoAchado {
  id: number;
  /** ISO UTC. */
  criado_em: string;
  estado: 'pendente' | 'entregue' | 'retido' | 'descartado';
  /** Os 4 últimos dígitos do telefone guardado. */
  final: string;
}

export interface PortalContatosBusca {
  contatos: PortalContatoAchado[];
}

export type PortalPedidoPor = 'formulario' | 'telefone' | 'outro';

export type PortalMotivoMantido = 'em_envio' | 'falhou' | 'canal_sem_exclusao';

/** 29.83: resposta de `POST /api/portal/contatos/excluir`. */
export interface PortalExclusaoResultado {
  apagados: number[];
  mantidos: { id: number; motivo: PortalMotivoMantido }[];
  inexistentes: number[];
  /** Mensagens do bot no Telegram que o próprio bot apagou (enviadas há menos de 48 h). */
  mensagens_apagadas: number;
  /** As que o bot não pode apagar (mais de 48 h): a pessoa apaga à mão no chat. */
  mensagens_a_mao: { contato_id: number; enviada_em: string }[];
  sem_canal: boolean;
}
