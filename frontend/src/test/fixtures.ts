import type {
  Action, AppConfig, Attempt, EventRecord, Evidence, Flow, Instance, Objective, PersonaDevice, PersonaDTO, Plan, Recipe,
  RunDetail, RunSummary, SessionInfo, Settings, Snapshot, Step, UsageReport,
} from '../api/types';
import type { AvisoDTO, OcorrenciaDTO, PedidoDetalhe, PedidoView } from '../api/pedidos';

export const SETTINGS: Settings = {
  max_active_devices: 10, max_ai_concurrency: 4, boot_parallelism: 2,
  max_steps_per_objective: 20, max_actions_per_step: 25, max_attempts_per_step: 3, for_each_max_items: 25,
  step_timeout_s: 120, objective_timeout_s: 900, driver_call_timeout_s: 30,
  retry_backoff_s: 5, no_progress_limit: 6,
  session_unknown_retry_cap: 3,
  grupo_sem_aprovacao: '',
  ai_max_calls_per_objective: 60, ai_max_calls_per_item: 12, ai_max_calls_absolute: 300, ai_max_tokens_per_run: 2_000_000,
  ai_max_usd_per_run: 15, ai_max_usd_per_day: 0,
  capture_grid_interval_s: 2, capture_focus_interval_s: 0.5, frame_max_age_ms: 5000,
  log_retention_days: 14, evidence_retention_days: 14,
  auto_start_devices: false, max_online_devices: 3, min_online_dwell_s: 60, idle_stop_s: 0,
  preview_mode: 'on_demand',
  orquestracao_max_escolhidas: 30, orquestracao_max_candidatas: 60, operacao_max_acoes_executadas: 3,
  operacao_grupo_liberado_executa: true,
};

export const APPS: AppConfig[] = [
  { id: 'qa', name: 'QA Messenger', package: 'com.poc.qamessenger', activity: '.MainActivity', apk_path: 'C:\\apks\\qa.apk', nav_hints: 'Conversas na aba Chats', known_selectors: { send: 'id/send' }, builtin: true },
  { id: 'notes', name: 'Notas', package: 'com.poc.notes', activity: null, apk_path: null, nav_hints: null, known_selectors: null, builtin: false },
];

export function makeInstance(n: number, over: Partial<Instance> = {}): Instance {
  const id = `android-${String(n).padStart(2, '0')}`;
  return {
    id, index: n, avd_name: `poc_avd_${n}`, serial: `emulator-${5552 + n * 2}`, console_port: 5552 + n * 2,
    ports: { system: 8200 + n, mjpeg: 9100 + n, chromedriver: 9500 + n },
    state: 'stopped', state_detail: null, pid: null, boot_seconds: null, app_id: 'qa', account_label: `qa-user-${n}`,
    account_evidence: null, account_evidence_ts: null, control: 'none', control_since: null, control_pending: false,
    automation: { state: 'none', detail: null }, frame: null, current: null, attention: null, resources: null, kind: 'emulator',
    ...over,
  };
}

export const RUN_ID = 'run-0001';

export function makeRun(over: Partial<RunSummary> = {}): RunSummary {
  return {
    id: RUN_ID, short_id: 'r-0001', command: 'Abra o QA Messenger e envie “Teste POC”', status: 'running', simulated: true,
    instance_ids: ['android-01', 'android-02'], instances_requested: 2, instances_used: 2,
    created_at: '2026-09-17T12:00:00.000Z', started_at: '2026-09-17T12:00:05.000Z', finished_at: null,
    counts: { succeeded: 0, failed: 0, waiting_user: 1, uncertain: 0, cancelled: 0, running: 1, pending: 0 },
    progress: 0, status_detail: null,
    ...over,
  };
}

export function makeSnapshot(over: Partial<Snapshot> = {}): Snapshot {
  const instances: Instance[] = [];
  for (let n = 1; n <= 10; n++) {
    if (n === 1) {
      instances.push(makeInstance(n, {
        state: 'online', pid: 4321, boot_seconds: 41, control: 'ai', automation: { state: 'ready', detail: null },
        frame: { id: 'frame-a1', ts: '2026-09-17T12:00:09.000Z', width: 1080, height: 2400, orientation: 'portrait', stale: false },
        account_evidence: 'Conta: qa-user-1',
        account_evidence_ts: '2026-09-17T12:00:08.000Z',
        current: { run_id: RUN_ID, objective_id: 'obj-1', objective_status: 'running', step_id: `${RUN_ID}:android-01:v1:open_app`, step_title: 'Abrir o QA Messenger', step_status: 'running', steps_done: 1, steps_total: 4 },
      }));
    } else if (n === 2) {
      instances.push(makeInstance(n, {
        state: 'online', pid: 4322, control: 'ai', automation: { state: 'ready', detail: null },
        frame: { id: 'frame-b1', ts: '2026-09-17T11:59:00.000Z', width: 1080, height: 2400, orientation: 'portrait', stale: true },
        attention: 'Login necessário no QA Messenger',
        current: { run_id: RUN_ID, objective_id: 'obj-2', objective_status: 'waiting_user', step_id: null, step_title: 'Entrar na conversa', step_status: 'waiting_user', steps_done: 1, steps_total: 4 },
      }));
    } else if (n === 3) {
      instances.push(makeInstance(n, { state: 'booting', state_detail: 'Aguardando o adb responder' }));
    } else if (n === 4) {
      instances.push(makeInstance(n, { state: 'error', state_detail: 'O emulador encerrou com código 1' }));
    } else if (n === 5) {
      instances.push(makeInstance(n, { state: 'absent', app_id: null, account_label: null }));
    } else {
      instances.push(makeInstance(n));
    }
  }
  return {
    last_event_id: 100,
    server_time: new Date().toISOString(),
    health: {
      status: 'degraded', version: '0.1.0', commit: null, migration: '017_busca_sem_acento',
      ai: {
        provider: 'simulated', model: 'simulador-local', configured: false, simulated: true, sends_data_externally: false,
        notice: 'Modo simulado de desenvolvimento.', effort: null,
        models: { plan: 'sim-planejador', decide: 'sim-decisor', verify: 'sim-verificador', escalation: 'sim-escalonado' },
        recipes: 'replay', flows: true, image_policy: 'auto',
      },
      appium: { running: true, port: 4723, detail: null },
      sdk: { found: true, root: 'C:\\Android\\Sdk', emulator_version: '35.1.4', accel: 'WHPX' },
      problems: [{ code: 'ai_simulated', message: 'A IA está em modo simulado', hint: 'Defina a chave no .env para usar a IA real.' }],
      features: { hibernation: true, recipes: 'replay', flows: true, image_policy: 'auto', system_image: 'system-images;android-35;google_apis;x86_64' },
    },
    metrics: { ts: new Date().toISOString(), cpu_percent: 37, mem_total_gb: 64, mem_available_gb: 40.5, mem_used_percent: 37, emulators: [] },
    instances,
    apps: APPS,
    runs: [makeRun()],
    settings: SETTINGS,
    ...over,
  };
}

const PLAN: Plan = {
  summary: 'Abrir o app, entrar na conversa com QA-001 e enviar a mensagem de teste.',
  app_id: 'qa', app_package: 'com.poc.qamessenger',
  parameters: { recipient: 'QA-001', message_template: 'Teste POC {instance_id} {run_id}' },
  success_criteria: ['A mensagem aparece na conversa como enviada'],
  steps: [
    { key: 'open_app', title: 'Abrir o QA Messenger', goal: 'Deixar o app em primeiro plano', depends_on: [], side_effect: false, precondition: null, postcondition: { kind: 'app_foreground', value: 'com.poc.qamessenger', description: 'App visível' }, timeout_s: 60, max_attempts: 3 },
    { key: 'send', title: 'Enviar a mensagem', goal: 'Digitar e enviar o texto', depends_on: ['open_app'], side_effect: true, precondition: 'Conversa aberta', postcondition: { kind: 'text_visible', value: 'Teste POC', description: 'Mensagem na tela' }, timeout_s: 90, max_attempts: 1 },
  ],
  missing: [],
  planner: { provider: 'simulated', model: 'simulador-local', simulated: true },
};

function makeStep(instanceId: string, objectiveId: string, key: string, seq: number, over: Partial<Step> = {}): Step {
  const src = PLAN.steps.find((s) => s.key === key);
  if (!src) throw new Error(`etapa ${key} inexistente`);
  return {
    id: `${RUN_ID}:${instanceId}:v1:${key}`, run_id: RUN_ID, objective_id: objectiveId, instance_id: instanceId,
    plan_version: 1, seq, key, title: src.title, goal: src.goal, depends_on: src.depends_on, side_effect: src.side_effect,
    precondition: src.precondition, postcondition: src.postcondition, timeout_s: src.timeout_s, max_attempts: src.max_attempts,
    attempts: 1, status: 'pending', status_detail: null, next_retry_at: null, started_at: null, finished_at: null, result: null,
    driven_by: null,
    ...over,
  };
}

function makeObjective(id: string, instanceId: string, over: Partial<Objective> = {}): Objective {
  return {
    id, run_id: RUN_ID, instance_id: instanceId, status: 'running', status_detail: null, blocked_reason: null, needs: null,
    plan_version: 1, parameters: { recipient: 'QA-001' }, steps_done: 1, steps_total: 2, delivery_level: null, effects: [],
    started_at: '2026-09-17T12:00:05.000Z', finished_at: null, ai_calls: 3, ai_input_tokens: 1200, ai_output_tokens: 300,
    ...over,
  };
}

export const ACTION: Action = {
  id: 1, attempt_id: 'att-1', seq: 1, tool: 'open_app', args: { package: 'com.poc.qamessenger' }, rationale: 'Abrir o app pelo pacote configurado',
  status: 'done', side_effect: false, intent_at: '2026-09-17T12:00:06.000Z', done_at: '2026-09-17T12:00:07.500Z', result: { ok: true }, error: null,
  source: 'ai',
};

export const ATTEMPT: Attempt = {
  id: 'att-1', step_id: `${RUN_ID}:android-01:v1:open_app`, number: 1, status: 'succeeded',
  started_at: '2026-09-17T12:00:06.000Z', finished_at: '2026-09-17T12:00:08.000Z', error: null, recovery: null, observed_result: 'App em primeiro plano', actions: [ACTION],
};

export const EVIDENCE: Evidence[] = [
  { id: 1, run_id: RUN_ID, instance_id: 'android-01', step_id: `${RUN_ID}:android-01:v1:open_app`, attempt_id: 'att-1', ts: '2026-09-17T12:00:08.000Z', kind: 'screenshot', note: 'App aberto', url: '/api/evidence/1', redacted: false },
  { id: 2, run_id: RUN_ID, instance_id: 'android-02', step_id: null, attempt_id: null, ts: '2026-09-17T12:00:09.000Z', kind: 'screenshot', note: 'Tela de login', url: null, redacted: true },
  { id: 3, run_id: RUN_ID, instance_id: 'android-01', step_id: null, attempt_id: null, ts: '2026-09-17T12:00:10.000Z', kind: 'verifier', note: 'Verificador: app em primeiro plano', url: null, redacted: false },
];

export function makeRunDetail(over: Partial<RunDetail> = {}): RunDetail {
  return {
    ...makeRun(),
    plan: PLAN,
    objectives: [
      makeObjective('obj-1', 'android-01'),
      makeObjective('obj-2', 'android-02', { status: 'waiting_user', needs: 'Faça login com a conta de teste', blocked_reason: 'Tela de login detectada', effects: [] }),
    ],
    steps: [
      makeStep('android-01', 'obj-1', 'open_app', 1, { status: 'succeeded', started_at: '2026-09-17T12:00:06.000Z', finished_at: '2026-09-17T12:00:08.000Z', result: { verified: true, evidence_text: 'QA Messenger', delivery_level: 'none' } }),
      makeStep('android-01', 'obj-1', 'send', 2, { status: 'running', started_at: '2026-09-17T12:00:08.500Z' }),
      makeStep('android-02', 'obj-2', 'open_app', 1, { status: 'waiting_user', status_detail: 'Login necessário' }),
      makeStep('android-02', 'obj-2', 'send', 2),
    ],
    attempts: [ATTEMPT],
    evidence: EVIDENCE,
    plan_versions: [{ objective_id: 'obj-1', version: 1, reason: 'Plano inicial', created_at: '2026-09-17T12:00:04.000Z', steps: PLAN.steps }],
    decisions: [{ ts: '2026-09-17T12:00:07.000Z', instance_id: 'android-02', text: 'Tela de login detectada: pedir ajuda ao usuário.' }],
    ...over,
  };
}

export function makeEvent(id: number | null, kind: string, data: Record<string, unknown> | null, over: Partial<EventRecord> = {}): EventRecord {
  return {
    id, ts: new Date().toISOString(), kind, level: 'info', run_id: null, instance_id: null, objective_id: null,
    step_id: null, attempt_id: null, message: `evento ${kind}`, data, ...over,
  };
}

export const RUN_EVENTS: EventRecord[] = [
  makeEvent(90, 'run.updated', { run: makeRun() }, { run_id: RUN_ID, message: 'Execução iniciada' }),
  makeEvent(95, 'decision', { text: 'Tela de login detectada' }, { run_id: RUN_ID, instance_id: 'android-02', level: 'warn', message: 'Decisão: pedir ajuda ao usuário' }),
];

export const DIAGNOSTICS = {
  collected_at: '2026-09-17T11:00:00.000Z',
  host: { os: 'Windows Server 2025', cpu: 'Xeon', cpu_cores: 32, ram_gb: 64 },
  tools: { adb: { found: true, version: '35.0.2', path: 'C:\\Android\\Sdk\\platform-tools\\adb.exe' }, appium: { found: false, error: 'não encontrado no PATH' }, java: '17.0.9' },
  acceleration: { available: true, hypervisor: 'WHPX' },
  capacity: { estimated_max_devices: 10, limiting_factor: 'RAM' },
  measurements: [{ devices: 2, boot_seconds: 38.5, cpu_percent: 41 }, { devices: 4, boot_seconds: 52.1, cpu_percent: 63 }],
  surprise_field: ['a', 'b'],
};

export const REPORT = {
  run: makeRun({ status: 'completed_with_issues' }),
  totals: { succeeded: 1, failed: 0, waiting_user: 1 },
  per_instance: [
    { instance_id: 'android-01', status: 'succeeded', summary: 'Mensagem enviada', delivery_level: 'sent' },
    { instance_id: 'android-02', status: 'waiting_user', summary: 'Login pendente' },
  ],
  untested: ['Confirmação de leitura pelo destinatário'],
  markdown: '# Relatório\n\n- android-01: sucesso',
};

export const FLOWS: Flow[] = [
  {
    id: 'enviar-mensagem', name: 'Enviar mensagem de teste', command_template: 'Abra o QA Messenger, fale com {recipient} e envie “{message_template}”.',
    app_id: 'qa', source_run_id: RUN_ID, status: 'active', uses: 7, created_at: '2026-09-10T09:00:00.000Z', last_used_at: '2026-09-17T11:30:00.000Z',
  },
  {
    id: 'abrir-config', name: 'Abrir Configurações', command_template: 'Abra o QA Messenger e vá até Configurações.',
    app_id: null, source_run_id: null, status: 'disabled', uses: 0, created_at: '2026-09-12T09:00:00.000Z', last_used_at: null,
  },
];

export const RECIPES: Recipe[] = [
  {
    id: 11, app_package: 'com.poc.qamessenger', app_version: '1.4.2', step_key: 'send', step_hash: 'h-send', version: 2, status: 'active',
    actions: [
      { tool: 'type_text', args: { text: '{message_template}' }, commit: false, why: 'Digitar a mensagem', selectors: [{ kind: 'rid', rid: 'com.poc.qamessenger:id/input' }] },
      { tool: 'tap', args: {}, commit: true, why: 'Tocar em Enviar', selectors: [{ kind: 'rid', rid: 'com.poc.qamessenger:id/send' }, { kind: 'desc', desc: 'Enviar' }], scroll: { direction: 'down', max: 3 } },
    ],
    replay_ok: 24, replay_fail: 1, consecutive_fail: 0, shadow_agree: 12, shadow_total: 15, learned_from_step: `${RUN_ID}:android-01:v1:send`,
    created_at: '2026-09-11T10:00:00.000Z', last_used_at: '2026-09-17T11:31:00.000Z',
  },
  {
    id: 12, app_package: 'com.exemplo.desconhecido', app_version: '0.9', step_key: 'open_app', step_hash: 'h-open', version: 1, status: 'quarantined',
    actions: [{ tool: 'open_app', args: {}, commit: false, why: 'Abrir o app' }],
    replay_ok: 3, replay_fail: 4, consecutive_fail: 3, shadow_agree: 0, shadow_total: 0, learned_from_step: null,
    created_at: '2026-09-09T10:00:00.000Z', last_used_at: null,
  },
  {
    id: 10, app_package: 'com.poc.qamessenger', app_version: '1.4.1', step_key: 'send', step_hash: 'h-send', version: 1, status: 'superseded',
    actions: [], replay_ok: 5, replay_fail: 2, consecutive_fail: 0, shadow_agree: 0, shadow_total: 0, learned_from_step: null,
    created_at: '2026-09-08T10:00:00.000Z', last_used_at: '2026-09-10T10:00:00.000Z',
  },
];

/** Relatório de custo em modo simulado: nenhum modelo tem preço. */
export const USAGE_SIMULATED: UsageReport = {
  scope: { run_id: null, days: 7 },
  groups: [
    { role: 'plan', model: 'simulado', tier: 0, calls: 3, fresh: 9000, cache_read: 0, cache_write: 0, output: 2400, with_image: 0, errors: 0, avg_ms: 12, usd: null },
    { role: 'decide', model: 'simulado', tier: 0, calls: 22, fresh: 48_300, cache_read: 0, cache_write: 0, output: 5100, with_image: 9, errors: 0, avg_ms: 9, usd: null },
  ],
  total_usd: 0, objectives_with_ai: 5, calls_per_objective: 5, usd_per_objective: 0,
  steps_driven_by: { recipe: 6, 'recipe+ai': 1, ai: 5 }, unpriced_models: ['simulado'],
};

/** A sessão de uma conta num aparelho (`account_sessions`), como os DTOs a mandam. */
export function makeSession(status: SessionInfo['status'], instanceId: string | null = null): SessionInfo {
  return { status, instance_id: instanceId, observed_username: null, verified_at: null, detail: null, stale: false };
}

/** Um vínculo da persona com um aparelho (v0.29, N:N). */
export function makeBinding(instanceId: string, over: Partial<PersonaDevice> = {}): PersonaDevice {
  return { instance_id: instanceId, app_id: 'instagram', is_primary: false, state: 'online', worker_id: null,
           bound_at: '2026-09-28T10:00:00Z', session: makeSession('unknown', instanceId), ...over };
}

/** Uma persona como `GET /personas` a devolve (v0.29): `instance_id` é o principal e `devices` traz todos. */
export function makePersona(id: string, name: string, over: Partial<PersonaDTO> = {}): PersonaDTO {
  const devices = over.devices ?? [];
  return {
    id, name, summary: null, username: null, display_name: name, first_name: name.split(' ')[0] ?? name,
    last_name: null, birth_date: null, email: null, persona_id: id, persona_name: name, status: 'active',
    instance_id: devices.find((d) => d.is_primary)?.instance_id ?? devices[0]?.instance_id ?? null,
    locality: null, offline_policy: 'wait',
    credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                  updated_at: null, last_used_at: null },
    session: makeSession('unknown'), last_verified_at: null, last_activity_at: null,
    created_at: '2026-09-28T10:00:00Z', updated_at: '2026-09-28T10:00:00Z', devices,
    ...over,
  };
}

// ---- pedidos (item 28.9, adendo v0.45) -----------------------------------------------------------------------
export function makePedido(over: Partial<PedidoView> = {}): PedidoView {
  return {
    id: 'ped_a1', titulo: 'Resumo diário do feed', objetivo: 'Abra o app e resuma o feed.', contexto: null, criterios_sucesso: null,
    alvos: null, autonomia: 'observar', fuso: 'America/Sao_Paulo', inicio_em: null, fim_em: null, max_ocorrencias: null,
    orcamento_total_usd: null, orcamento_ocorrencia_usd: null, sobreposicao: 'pular', janela_recuperacao_s: null, coalescer: true,
    max_tentativas: 2, pausa_por_falha: 3, estado: 'ativo', versao: 1, proxima_em: '2026-10-03T11:00:00Z', criado_por: 'ana',
    pausado_motivo: null, encerrado_motivo: null, pai_id: null, papel: null, criado_em: '2026-10-01T10:00:00Z', atualizado_em: '2026-10-02T10:00:00Z',
    gatilhos_resumo: [{ tipo: 'recorrencia', descricao: 'Todo dia às 08:00 (America/Sao_Paulo)' }],
    personas: [{ profile_id: 'p1', nome: 'Ana Lima' }], proxima_local: '2026-10-03 08:00 -03:00', ultima_ocorrencia: null,
    ocorrencias_por_estado: {}, gasto_usd: 0, orcamento_usado: null, avisos_nao_lidos: 0,
    acoes_permitidas: ['editar', 'pausar', 'cancelar'], ...over,
  };
}

export function makeOcorrencia(over: Partial<OcorrenciaDTO> = {}): OcorrenciaDTO {
  return {
    id: 'oc_1', pedido_id: 'ped_a1', pedido_versao: 1, gatilho_id: 'g1', previsto_para: '2026-10-02T11:00:00Z',
    chave: 'ped:ped_a1:g1:2026-10-02T11:00:00Z', origem: 'agenda', estado: 'concluida', tentativa: 1, run_id: 'run-0001',
    run: { id: 'run-0001', short_id: 'run-0001', status: 'completed', status_detail: null }, run_disponivel: true, motivo: null,
    custo_usd: 0.021, resumo: 'Feed resumido.', criada_em: '2026-10-02T10:59:00Z', iniciada_em: '2026-10-02T11:00:01Z',
    terminada_em: '2026-10-02T11:03:00Z', ...over,
  };
}

export function makePedidoDetalhe(over: Partial<PedidoDetalhe> = {}): PedidoDetalhe {
  return {
    ...makePedido(), gatilhos: [], proximas: [], ocorrencias_recentes: [makeOcorrencia()], execucoes_em_curso: [],
    pendencias: [], memoria: null, relatorios_recentes: null, observacoes_recentes: null, ...over,
  };
}

export function makeAviso(over: Partial<AvisoDTO> = {}): AvisoDTO {
  return {
    id: 'av_1', pedido_id: 'ped_a1', pedido_titulo: 'Resumo diário do feed', ocorrencia_id: null, tipo: 'orcamento_80', nivel: 'warn',
    mensagem: 'O pedido gastou 80% do orçamento.', dados: {}, requer_pessoa: false, criado_em: '2026-10-02T12:00:00Z', lido_em: null, ...over,
  };
}
