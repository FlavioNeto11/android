import { describe, expect, it } from 'vitest';
import type {
  Action, Attempt, EventRecord, Instance, Objective, RunDetail, RunSummary, Snapshot, Step, Worker,
} from '../api/types';
import {
  aceitaComando, applyEvent, chaveDoApp, eventRunId, hydrateFromSnapshot, initialDataState, mergeTimeline,
  reduceDetail, upsertRun, type DataState,
} from './reducer';

// ---- fábricas -----------------------------------------------------------------------------------

function instance(n: number, over: Partial<Instance> = {}): Instance {
  const id = `android-${String(n).padStart(2, '0')}`;
  return {
    id, index: n, avd_name: `poc_${n}`, serial: `emulator-${5552 + n * 2}`, console_port: 5552 + n * 2,
    ports: { system: 8200 + n, mjpeg: 9100 + n, chromedriver: 9500 + n },
    state: 'online', state_detail: null, pid: 1000 + n, boot_seconds: 30, app_id: 'qa', account_label: null,
    account_evidence: null, account_evidence_ts: null, control: 'none', control_since: null, control_pending: false,
    automation: { state: 'ready', detail: null }, frame: null, current: null, attention: null, resources: null, kind: 'emulator',
    ...over,
  };
}

function run(id: string, over: Partial<RunSummary> = {}): RunSummary {
  return {
    id, short_id: id.slice(0, 6), command: 'abrir app', status: 'running', simulated: false,
    instance_ids: ['android-01'], instances_requested: 1, instances_used: 1,
    created_at: '2026-09-17T12:00:00.000Z', started_at: null, finished_at: null,
    counts: { succeeded: 0, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 1, pending: 0 },
    progress: 0, status_detail: null, ...over,
  };
}

function detail(id: string): RunDetail {
  return { ...run(id), plan: null, objectives: [], steps: [], attempts: [], evidence: [], plan_versions: [], decisions: [] };
}

function event(id: number | null, kind: string, data: Record<string, unknown> | null, over: Partial<EventRecord> = {}): EventRecord {
  return {
    id, ts: '2026-09-17T12:00:01.000Z', kind, level: 'info', run_id: null, instance_id: null, objective_id: null,
    step_id: null, attempt_id: null, message: kind, data, ...over,
  };
}

function worker(id: string, over: Partial<Worker> = {}): Worker {
  return {
    id, name: id, appium_mode: 'central', max_slots: 4, verbs: [], state: 'online', observed_state: 'online',
    maintenance: false, connected: true, local: false,
    resources: { cpu_percent: 10, cpu_count: 8, ram_total_mb: 32000, ram_free_mb: 20000 },
    devices: [], enrolled_at: '2026-09-17T12:00:00.000Z', ...over,
  };
}

function snapshot(over: Partial<Snapshot> = {}): Snapshot {
  return {
    last_event_id: 100,
    server_time: '2026-09-17T12:00:00.000Z',
    health: {
      status: 'ok', version: '0.1', commit: null, migration: '017_busca_sem_acento', problems: [],
      ai: { provider: 'simulated', model: null, configured: false, simulated: true, sends_data_externally: false, notice: '', effort: null },
      appium: { running: true, port: 4723, detail: null },
      sdk: { found: true, root: null, emulator_version: null, accel: null },
      features: { hibernation: false, recipes: 'off', flows: false, image_policy: 'always', system_image: '' },
    },
    metrics: null,
    instances: [instance(2), instance(1)],
    apps: [],
    runs: [run('run-a'), run('run-b', { created_at: '2026-09-17T13:00:00.000Z' })],
    settings: {} as Snapshot['settings'],
    ...over,
  };
}

function hydrated(): DataState {
  return hydrateFromSnapshot(initialDataState, snapshot());
}

function withDetail(state: DataState, runId: string): DataState {
  return { ...state, detail: { runId, status: 'ready', data: detail(runId), error: null, events: [], eventsStatus: 'ready' } };
}

const step = (over: Partial<Step> = {}): Step => ({
  id: 'run-a:android-01:v1:open_app', run_id: 'run-a', objective_id: 'obj-1', instance_id: 'android-01',
  plan_version: 1, seq: 1, key: 'open_app', title: 'Abrir app', goal: 'Abrir o app', depends_on: [],
  side_effect: false, precondition: null, postcondition: { kind: 'app_foreground', value: 'x', description: '' },
  timeout_s: 60, max_attempts: 3, attempts: 1, status: 'running', status_detail: null, next_retry_at: null,
  started_at: null, finished_at: null, result: null, driven_by: null, ...over,
});

const action = (id: number, seq: number, over: Partial<Action> = {}): Action => ({
  id, attempt_id: 'att-1', seq, tool: 'tap', args: { target: 'Enviar' }, rationale: 'Tocar em Enviar', status: 'done',
  side_effect: false, intent_at: '2026-09-17T12:00:02.000Z', done_at: null, result: null, error: null, source: 'ai', ...over,
});

// ---- testes -------------------------------------------------------------------------------------

describe('hydrateFromSnapshot', () => {
  it('ordena instâncias por índice, execuções por criação (mais nova primeiro) e adota o last_event_id', () => {
    const s = hydrated();
    expect(s.hydrated).toBe(true);
    expect(s.instanceOrder).toEqual(['android-01', 'android-02']);
    expect(s.runs.map((r) => r.id)).toEqual(['run-b', 'run-a']);
    expect(s.lastEventId).toBe(100);
  });

  it('aceita last_event_id menor em um novo snapshot (backend reiniciado)', () => {
    const s = hydrateFromSnapshot(hydrated(), snapshot({ last_event_id: 3 }));
    expect(s.lastEventId).toBe(3);
    expect(s.hydrateCount).toBe(2);
  });

  it('semeia os comandos em voo: recarregar a página no meio de um comando não pode esquecer que o aparelho está ocupado', () => {
    const cmd = {
      id: 'c-1', instance_id: 'android-01', worker_id: 'worker-lan-01', verb: 'start', state: 'running',
      fence: 2, requested_by: 'panel', reason: null, attempt: 0, created_at: '2026-09-17T12:00:00.000Z',
      dispatched_at: '2026-09-17T12:00:01.000Z', acked_at: null, started_at: null, finished_at: null,
    } as Snapshot['commands'] extends (infer T)[] | undefined ? T : never;
    const s = hydrateFromSnapshot(initialDataState, snapshot({ commands: [cmd] }));
    expect(s.lastCommand['android-01']?.id).toBe('c-1');
    // Backend antigo não manda o campo: preserva o que já havia em vez de apagar.
    expect(hydrateFromSnapshot(s, snapshot()).lastCommand['android-01']?.id).toBe('c-1');
  });

  it('semeia também o comando SEM DESFECHO: um `uncertain` de ontem não pode sumir no primeiro F5', () => {
    const incerto = {
      id: 'c-20260921172322-6f7fdc', instance_id: 'android-01', worker_id: 'worker-lan-01', verb: 'start',
      state: 'uncertain', fence: 1, requested_by: 'panel',
      reason: 'o aparelho não completou o boot em 480 s', attempt: 0, created_at: '2026-09-21T17:23:22.000Z',
      dispatched_at: '2026-09-21T17:23:22.000Z', acked_at: '2026-09-21T17:23:23.000Z',
      started_at: '2026-09-21T17:23:30.000Z', finished_at: '2026-09-21T17:31:25.000Z',
    } as Snapshot['commands'] extends (infer T)[] | undefined ? T : never;
    const s = hydrateFromSnapshot(initialDataState, snapshot({ commands: [incerto] }));
    expect(s.lastCommand['android-01']?.state).toBe('uncertain');
  });
});

describe('applyEvent — idempotência por id', () => {
  it('ignora eventos persistidos com id ≤ último aplicado', () => {
    const s0 = hydrated();
    const stale = event(100, 'run.updated', { run: run('run-a', { status: 'failed' }) }, { run_id: 'run-a' });
    expect(applyEvent(s0, stale)).toBe(s0);
    const older = event(42, 'instance.updated', { instance: instance(1, { state: 'error' }) });
    expect(applyEvent(s0, older)).toBe(s0);
  });

  it('aplica uma vez e ignora a reentrega do mesmo evento', () => {
    const s0 = hydrated();
    const ev = event(101, 'run.updated', { run: run('run-a', { status: 'paused' }) }, { run_id: 'run-a' });
    const s1 = applyEvent(s0, ev);
    expect(s1.lastEventId).toBe(101);
    expect(s1.runs.find((r) => r.id === 'run-a')?.status).toBe('paused');
    expect(applyEvent(s1, ev)).toBe(s1);
  });

  it('NUNCA descarta eventos efêmeros (id = null), mesmo repetidos', () => {
    const s0 = hydrated();
    const frame = { id: 'f1', ts: '2026-09-17T12:00:01.000Z', width: 1080, height: 2400, orientation: 'portrait', stale: false };
    const s1 = applyEvent(s0, event(null, 'frame', { instance_id: 'android-01', frame }));
    expect(s1.instances['android-01']?.frame?.id).toBe('f1');
    expect(s1.lastEventId).toBe(100);
    const s2 = applyEvent(s1, event(null, 'frame', { instance_id: 'android-01', frame: { ...frame, id: 'f2' } }));
    expect(s2.instances['android-01']?.frame?.id).toBe('f2');
  });
});

describe('applyEvent — estado global', () => {
  it('instance.updated substitui a instância; control.changed altera só o controle', () => {
    let s = hydrated();
    s = applyEvent(s, event(101, 'instance.updated', { instance: instance(1, { state: 'booting', state_detail: 'adb' }) }));
    expect(s.instances['android-01']?.state).toBe('booting');
    s = applyEvent(s, event(102, 'control.changed', { instance_id: 'android-01', control: 'ai', pending: true }));
    expect(s.instances['android-01']).toMatchObject({ state: 'booting', control: 'ai', control_pending: true });
    s = applyEvent(s, event(103, 'control.changed', { instance_id: 'android-01', control: 'user', pending: false }));
    expect(s.instances['android-01']).toMatchObject({ control: 'user', control_pending: false, control_since: '2026-09-17T12:00:01.000Z' });
  });

  it('metrics, health, apps e settings são substituídos', () => {
    let s = hydrated();
    s = applyEvent(s, event(null, 'metrics', { metrics: { ts: 'x', cpu_percent: 41, mem_total_gb: 64, mem_available_gb: 20, mem_used_percent: 68, emulators: [] } }));
    expect(s.metrics?.cpu_percent).toBe(41);
    s = applyEvent(s, event(null, 'health.updated', { health: { ...s.health, status: 'degraded' } }));
    expect(s.health?.status).toBe('degraded');
    s = applyEvent(s, event(null, 'apps.updated', { apps: [{ id: 'a', name: 'A', package: 'p', activity: null, apk_path: null, nav_hints: null, known_selectors: null, builtin: false }] }));
    expect(s.apps).toHaveLength(1);
    s = applyEvent(s, event(null, 'settings.updated', { settings: { max_active_devices: 7 } }));
    expect(s.settings?.max_active_devices).toBe(7);
  });

  it('worker.updated grava o worker; worker.metrics só atualiza recurso de um worker já conhecido (achados #17/#143)', () => {
    let s = hydrated();
    // worker.metrics antes de qualquer worker.updated: nada para atualizar, ignora sem quebrar.
    s = applyEvent(s, event(null, 'worker.metrics', { worker_id: 'worker-lan-01', resources: { cpu_percent: 90 } }));
    expect(s.workers['worker-lan-01']).toBeUndefined();

    s = applyEvent(s, event(101, 'worker.updated', { worker: worker('worker-lan-01', { state: 'online' }) }));
    expect(s.workers['worker-lan-01']).toMatchObject({ state: 'online', resources: { cpu_percent: 10 } });

    // Efêmero: chega sem id persistido, troca só `resources`, preserva o resto do worker.
    s = applyEvent(s, event(null, 'worker.metrics', { worker_id: 'worker-lan-01', resources: { cpu_percent: 77, ram_free_mb: 500 } }));
    expect(s.workers['worker-lan-01']).toMatchObject({ state: 'online', resources: { cpu_percent: 77, ram_free_mb: 500 } });
    expect(s.lastEventId).toBe(101);  // efêmero não tem id: não avança o cursor de replay

    // A batida traz a hora, e é ela que mantém "Último contato" vivo: sem isto a Infraestrutura aberta acusava
    // "dados desatualizados" de todo worker vivo depois de um minuto (medido "há 2 min 33 s" com a API em 4 s).
    s = applyEvent(s, event(null, 'worker.metrics', { worker_id: 'worker-lan-01', last_seen_at: '2026-09-23T16:36:09.000Z', resources: { cpu_percent: 1 } }));
    expect(s.workers['worker-lan-01']).toMatchObject({ last_seen_at: '2026-09-23T16:36:09.000Z', resources: { cpu_percent: 1 } });
    // Sem recurso (protocolo antigo) a hora ainda conta — e o recurso anterior fica.
    s = applyEvent(s, event(null, 'worker.metrics', { worker_id: 'worker-lan-01', last_seen_at: '2026-09-23T16:36:19.000Z' }));
    expect(s.workers['worker-lan-01']).toMatchObject({ last_seen_at: '2026-09-23T16:36:19.000Z', resources: { cpu_percent: 1 } });
  });

  it('run.updated insere execuções novas mantendo a ordem', () => {
    const s = applyEvent(hydrated(), event(101, 'run.updated', { run: run('run-c', { created_at: '2026-09-17T14:00:00.000Z' }) }, { run_id: 'run-c' }));
    expect(s.runs.map((r) => r.id)).toEqual(['run-c', 'run-b', 'run-a']);
  });

  it('payload malformado não quebra nem altera dados', () => {
    const s0 = hydrated();
    const s1 = applyEvent(s0, event(101, 'instance.updated', { instance: 'oops' }));
    expect(s1.instances).toBe(s0.instances);
    const s2 = applyEvent(s1, event(102, 'frame', null));
    expect(s2.instances).toBe(s0.instances);
    expect(s2.lastEventId).toBe(102);
  });

  it('guarda os eventos persistidos recentes', () => {
    const s = applyEvent(hydrated(), event(101, 'log', { anything: 1 }));
    expect(s.recentEvents.map((e) => e.id)).toEqual([101]);
  });
});

describe('applyEvent — detalhe da execução selecionada', () => {
  it('ignora eventos de outras execuções no detalhe, mas atualiza a lista', () => {
    const s0 = withDetail(hydrated(), 'run-a');
    const s1 = applyEvent(s0, event(101, 'run.updated', { run: run('run-b', { status: 'completed' }) }, { run_id: 'run-b' }));
    expect(s1.detail).toBe(s0.detail);
    expect(s1.runs.find((r) => r.id === 'run-b')?.status).toBe('completed');
  });

  it('run.updated mescla o resumo no detalhe sem perder plano/etapas', () => {
    let s = withDetail(hydrated(), 'run-a');
    s = applyEvent(s, event(101, 'step.updated', { step: step() }, { run_id: 'run-a' }));
    s = applyEvent(s, event(102, 'run.updated', { run: run('run-a', { status: 'completed', progress: 1 }) }, { run_id: 'run-a' }));
    expect(s.detail?.data?.status).toBe('completed');
    expect(s.detail?.data?.steps).toHaveLength(1);
    expect(s.detail?.events.map((e) => e.id)).toEqual([101, 102]);
  });

  it('attempt.updated chega sem actions e preserva as já registradas', () => {
    let s = withDetail(hydrated(), 'run-a');
    const attempt: Attempt = {
      id: 'att-1', step_id: 'run-a:android-01:v1:open_app', number: 1, status: 'running',
      started_at: '2026-09-17T12:00:01.000Z', finished_at: null, error: null, recovery: null, observed_result: null, actions: [],
    };
    const { actions: _omit, ...attemptWithoutActions } = attempt;
    s = applyEvent(s, event(101, 'attempt.updated', { attempt: attemptWithoutActions }, { run_id: 'run-a' }));
    expect(s.detail?.data?.attempts[0]?.actions).toEqual([]);
    s = applyEvent(s, event(102, 'action.logged', { action: action(2, 2), instance_id: 'android-01', step_id: attempt.step_id }, { run_id: 'run-a' }));
    s = applyEvent(s, event(103, 'action.logged', { action: action(1, 1), instance_id: 'android-01', step_id: attempt.step_id }, { run_id: 'run-a' }));
    s = applyEvent(s, event(104, 'attempt.updated', { attempt: { ...attemptWithoutActions, status: 'succeeded' } }, { run_id: 'run-a' }));
    const merged = s.detail?.data?.attempts[0];
    expect(merged?.status).toBe('succeeded');
    expect(merged?.actions.map((a) => a.id)).toEqual([1, 2]); // ordenadas por seq, nada perdido
  });

  it('action.logged atualiza a mesma ação (intended → done) sem duplicar', () => {
    let s = withDetail(hydrated(), 'run-a');
    const stepId = 'run-a:android-01:v1:open_app';
    s = applyEvent(s, event(101, 'action.logged', { action: action(7, 1, { status: 'intended' }), step_id: stepId }, { run_id: 'run-a' }));
    s = applyEvent(s, event(102, 'action.logged', { action: action(7, 1, { status: 'done' }), step_id: stepId }, { run_id: 'run-a' }));
    const attempts = s.detail?.data?.attempts ?? [];
    expect(attempts).toHaveLength(1); // casca criada porque a ação chegou antes da tentativa
    expect(attempts[0]?.step_id).toBe(stepId);
    expect(attempts[0]?.actions).toHaveLength(1);
    expect(attempts[0]?.actions[0]?.status).toBe('done');
  });

  it('objective, evidence e decision entram no detalhe sem duplicatas', () => {
    let s = withDetail(hydrated(), 'run-a');
    const objective = { id: 'obj-1', run_id: 'run-a', instance_id: 'android-01', status: 'waiting_user' } as Objective;
    s = applyEvent(s, event(101, 'objective.updated', { objective }, { run_id: 'run-a' }));
    s = applyEvent(s, event(102, 'objective.updated', { objective: { ...objective, status: 'succeeded' } }, { run_id: 'run-a' }));
    expect(s.detail?.data?.objectives).toHaveLength(1);
    expect(s.detail?.data?.objectives[0]?.status).toBe('succeeded');

    const evidence = { id: 5, run_id: 'run-a', instance_id: 'android-01', step_id: null, attempt_id: null, ts: 't', kind: 'screenshot', note: null, url: null, redacted: false };
    s = applyEvent(s, event(103, 'evidence.added', { evidence }, { run_id: 'run-a' }));
    expect(s.detail?.data?.evidence).toHaveLength(1);

    s = applyEvent(s, event(104, 'decision', { text: 'Reabrir o app' }, { run_id: 'run-a', instance_id: 'android-01' }));
    expect(s.detail?.data?.decisions).toEqual([{ ts: '2026-09-17T12:00:01.000Z', instance_id: 'android-01', text: 'Reabrir o app' }]);
  });
});

describe('reduceDetail — reaplicação do buffer após o GET', () => {
  it('é idempotente: reaplicar eventos já contidos no detalhe não duplica', () => {
    const base = detail('run-a');
    const events = [
      event(101, 'step.updated', { step: step({ status: 'running' }) }, { run_id: 'run-a' }),
      event(102, 'decision', { text: 'Tentar de novo' }, { run_id: 'run-a' }),
      event(103, 'step.updated', { step: step({ status: 'succeeded' }) }, { run_id: 'run-a' }),
    ];
    const once = events.reduce(reduceDetail, base);
    const twice = events.reduce(reduceDetail, once);
    expect(twice.steps).toHaveLength(1);
    expect(twice.steps[0]?.status).toBe('succeeded');
    expect(twice.decisions).toHaveLength(1);
  });
});

describe('eventRunId', () => {
  it('usa run_id e cai para o payload/step_id quando o campo vem nulo', () => {
    expect(eventRunId(event(1, 'x', null, { run_id: 'r1' }))).toBe('r1');
    expect(eventRunId(event(1, 'run.updated', { run: run('r2') }))).toBe('r2');
    expect(eventRunId(event(1, 'step.updated', { step: step() }))).toBe('run-a');
    expect(eventRunId(event(1, 'attempt.updated', { attempt: { id: 'a', step_id: 'r3:android-01:v1:k' } }))).toBe('r3');
    expect(eventRunId(event(1, 'log', null))).toBeNull();
  });
});

describe('mergeTimeline', () => {
  it('anexa eventos novos e deduplica/ordena quando há sobreposição', () => {
    const a = [event(1, 'log', null), event(3, 'log', null)];
    expect(mergeTimeline(a, [event(4, 'log', null)]).map((e) => e.id)).toEqual([1, 3, 4]);
    expect(mergeTimeline(a, [event(2, 'log', null), event(3, 'log', null), event(5, 'log', null)]).map((e) => e.id)).toEqual([1, 2, 3, 5]);
    expect(mergeTimeline(a, [])).toBe(a);
  });

  it('descarta eventos efêmeros', () => {
    expect(mergeTimeline([], [event(null, 'frame', null), event(9, 'log', null)]).map((e) => e.id)).toEqual([9]);
  });
});

describe('upsertRun (resposta REST)', () => {
  it('não guarda o marcador deduplicated e atualiza o detalhe aberto', () => {
    const s0 = withDetail(hydrated(), 'run-a');
    const s1 = upsertRun(s0, run('run-a', { status: 'paused', deduplicated: true }));
    expect(s1.runs.find((r) => r.id === 'run-a')).not.toHaveProperty('deduplicated');
    expect(s1.detail?.data?.status).toBe('paused');
  });
});


// ---- comandos: o desfecho não regride ----------------------------------------------------------

function comando(over: Record<string, unknown> = {}): never {
  return {
    id: 'c-1', instance_id: 'android-01', worker_id: 'worker-lan-01', verb: 'start', state: 'running',
    fence: 2, requested_by: 'panel', reason: null, attempt: 0, created_at: '2026-09-17T12:00:00.000Z',
    dispatched_at: '2026-09-17T12:00:01.000Z', acked_at: null, started_at: null, finished_at: null, ...over,
  } as never;
}

describe('command.updated — a trilha anda para frente', () => {
  it('evento atrasado não faz um comando concluído voltar a "em andamento"', () => {
    // A guarda antiga comparava `created_at` do MESMO comando consigo mesmo: sempre passava, e um `progress`
    // atrasado devolvia o cartão para "ocupado" depois de o comando ter terminado.
    const s0 = applyEvent(hydrated(), event(200, 'command.updated', { command: comando({ state: 'succeeded' }) }));
    expect(s0.lastCommand['android-01']?.state).toBe('succeeded');
    const s1 = applyEvent(s0, event(201, 'command.updated', { command: comando({ state: 'running' }) }));
    expect(s1.lastCommand['android-01']?.state).toBe('succeeded');
  });

  it('a trilha avança: created → dispatched → acked → running → concluído', () => {
    let s = hydrated();
    let id = 300;
    for (const estado of ['created', 'dispatched', 'acked', 'running', 'succeeded']) {
      s = applyEvent(s, event(id++, 'command.updated', { command: comando({ state: estado }) }));
      expect(s.lastCommand['android-01']?.state).toBe(estado);
    }
  });

  it('incerto ainda pode ser fechado: é a verificação pelo estado real ou a decisão de uma pessoa', () => {
    expect(aceitaComando(comando({ state: 'uncertain' }), comando({ state: 'succeeded' }))).toBe(true);
    expect(aceitaComando(comando({ state: 'uncertain' }), comando({ state: 'failed' }))).toBe(true);
    // E o contrário não: um desfecho conhecido nunca volta a ser "não sei".
    expect(aceitaComando(comando({ state: 'succeeded' }), comando({ state: 'uncertain' }))).toBe(false);
  });

  it('comando NOVO no mesmo aparelho substitui o anterior', () => {
    const anterior = comando({ id: 'c-1', state: 'succeeded' });
    const novo = comando({ id: 'c-2', state: 'created', created_at: '2026-09-17T12:05:00.000Z' });
    expect(aceitaComando(anterior, novo)).toBe(true);
    expect(aceitaComando(novo, anterior)).toBe(false);
  });
});


it('app_state.updated entra no store: o desfecho de uma instalação deixa de depender de recarregar a página', () => {
  const linha = {
    instance_id: 'android-02', package_name: 'com.instagram.android', desired_release_id: 'rel-1',
    installed_release_id: 'rel-1', observed_version_name: '447.0.0', observed_version_code: 447,
    observed_splits: ['base'], expected_splits: ['base'], first_install_time: null, last_update_time: null,
    state: 'ready', pending_op: null, verified_at: '2026-09-22T10:00:00Z', drift_kind: null,
    detail: 'app abriu e permaneceu em primeiro plano', previous_release_id: null, last_operation: 'install',
  };
  const s = applyEvent(initialDataState, {
    id: 1, ts: '2026-09-22T10:00:00Z', kind: 'app_state.updated', level: 'info',
    message: 'android-02: com.instagram.android — ready', instance_id: 'android-02',
    data: { app_state: linha },
  } as never);
  expect(s.appState[chaveDoApp('android-02', 'com.instagram.android')]?.state).toBe('ready');
  // Evento sem corpo não apaga o que já se sabia.
  const s2 = applyEvent(s, { id: 2, ts: 'x', kind: 'app_state.updated', level: 'info', message: '', data: {} } as never);
  expect(s2.appState).toEqual(s.appState);
});
