import type {
  Action, AppConfig, Attempt, Command, DeviceAppState, EventRecord, Evidence, FrameInfo, Health, Instance,
  Metrics, Objective, RunDetail, RunSummary, Settings, Snapshot, Step, Worker,
} from '../api/types';
import { isRecord } from '../lib/format';

/**
 * Núcleo puro da camada de dados: recebe o estado + um evento e devolve o novo estado.
 * Sem React, sem rede, sem relógio — por isso é testável com vitest em ambiente node.
 */

export interface DetailError {
  message: string;
  hint: string;
  status: number;
}

export interface RunDetailState {
  runId: string;
  status: 'loading' | 'ready' | 'error';
  data: RunDetail | null;
  error: DetailError | null;
  /** Linha do tempo: somente eventos persistidos (id numérico) desta execução, em ordem crescente de id. */
  events: EventRecord[];
  eventsStatus: 'loading' | 'ready' | 'error';
}

export interface DataState {
  /** Já recebemos ao menos um snapshot? */
  hydrated: boolean;
  /** Quantas vezes o snapshot foi aplicado (muda a cada reconexão; útil para recarregar listas auxiliares). */
  hydrateCount: number;
  /** Maior id de evento persistido já aplicado. */
  lastEventId: number;
  health: Health | null;
  metrics: Metrics | null;
  instances: Record<string, Instance>;
  instanceOrder: string[];
  apps: AppConfig[];
  runs: RunSummary[];
  settings: Settings | null;
  detail: RunDetailState | null;
  /** Últimos eventos persistidos de qualquer origem (anel), para o painel de diagnóstico. */
  recentEvents: EventRecord[];
  /**
   * Último comando de cada aparelho. Existe para o cartão poder dizer "reset recusado" em vez de deixar a recusa
   * só num log escondido — era assim que uma ação não executada ficava indistinguível de sucesso.
   */
  lastCommand: Record<string, Command>;
  /** Máquinas que hospedam aparelhos, por id. Vazio = só o servidor central. */
  workers: Record<string, Worker>;
  /**
   * Estado do APP por aparelho×pacote, ao vivo (`app_state.updated`). A tela de Aplicativos vivia de uma carga
   * ao montar: o desfecho de uma instalação existia no banco e só aparecia se a pessoa recarregasse na hora
   * certa — depois de um toast que prometia "o resultado aparece aqui".
   */
  appState: Record<string, DeviceAppState>;
}

/** Chave de `appState`: um aparelho pode ter mais de um pacote, e um pacote está em vários aparelhos. */
export function chaveDoApp(instanceId: string, packageName: string): string {
  return `${instanceId}|${packageName}`;
}

/**
 * Ordem dos estados de um comando, para o store nunca REGREDIR um desfecho.
 *
 * A guarda anterior (`anterior.id !== cmd.id || anterior.created_at <= cmd.created_at`) era uma tautologia:
 * `created_at` do MESMO comando nunca muda, então qualquer evento atrasado — um `progress` que chegou depois do
 * desfecho, por exemplo — sobrescrevia "concluído" por "em andamento", e o cartão voltava a trancar o aparelho.
 * Comparar a ORDEM do estado resolve: o que anda para frente passa (inclusive `uncertain` → `succeeded`, que é
 * a verificação pelo estado real ou a decisão de uma pessoa), o que anda para trás é ignorado.
 */
const ORDEM_DO_ESTADO: Record<string, number> = {
  created: 0, dispatched: 1, acked: 2, running: 3, cancel_requested: 4,
  uncertain: 5, succeeded: 6, failed: 6, rejected: 6, cancelled: 6,
};

/** Este evento de comando deve substituir o que o store já tem para aquele aparelho? */
export function aceitaComando(anterior: Command | undefined, cmd: Command): boolean {
  if (!anterior) return true;
  // Comando diferente: vence o mais novo — o cartão fala do que está acontecendo AGORA naquele aparelho.
  if (anterior.id !== cmd.id) return anterior.created_at <= cmd.created_at;
  return (ORDEM_DO_ESTADO[cmd.state] ?? 0) >= (ORDEM_DO_ESTADO[anterior.state] ?? 0);
}

export const MAX_TIMELINE_EVENTS = 3000;
export const MAX_RECENT_EVENTS = 300;
export const MAX_RUNS = 100;

export const initialDataState: DataState = {
  hydrated: false,
  hydrateCount: 0,
  lastEventId: 0,
  health: null,
  metrics: null,
  instances: {},
  instanceOrder: [],
  apps: [],
  runs: [],
  settings: null,
  detail: null,
  recentEvents: [],
  lastCommand: {},
  workers: {},
  appState: {},
};

// ---- utilidades ---------------------------------------------------------------------------------

function obj<T>(data: Record<string, unknown> | null, key: string): T | null {
  if (!data) return null;
  const v = data[key];
  return isRecord(v) ? (v as T) : null;
}

function upsertBy<T, K>(list: readonly T[], item: T, keyOf: (t: T) => K, merge?: (prev: T, next: T) => T): T[] {
  const key = keyOf(item);
  const idx = list.findIndex((t) => keyOf(t) === key);
  if (idx === -1) return [...list, item];
  const copy = list.slice();
  copy[idx] = merge ? merge(list[idx] as T, item) : item;
  return copy;
}

function sortInstances(instances: Record<string, Instance>): string[] {
  return Object.values(instances)
    .sort((a, b) => (a.index - b.index) || a.id.localeCompare(b.id))
    .map((i) => i.id);
}

function sortRuns(runs: RunSummary[]): RunSummary[] {
  return runs
    .slice()
    .sort((a, b) => (b.created_at > a.created_at ? 1 : b.created_at < a.created_at ? -1 : 0))
    .slice(0, MAX_RUNS);
}

/** `deduplicated` só faz sentido na resposta de criação; não guardamos no estado. */
function cleanSummary(run: RunSummary): RunSummary {
  if (!('deduplicated' in run)) return run;
  const { deduplicated: _ignored, ...rest } = run;
  return rest;
}

// ---- snapshot -----------------------------------------------------------------------------------

export function hydrateFromSnapshot(state: DataState, snap: Snapshot): DataState {
  const instances: Record<string, Instance> = {};
  for (const inst of snap.instances ?? []) instances[inst.id] = inst;
  return {
    ...state,
    hydrated: true,
    hydrateCount: state.hydrateCount + 1,
    // Sempre o valor do servidor (se o backend foi reiniciado com base limpa, o id pode ter voltado).
    lastEventId: typeof snap.last_event_id === 'number' ? snap.last_event_id : 0,
    health: snap.health ?? null,
    metrics: snap.metrics ?? null,
    instances,
    instanceOrder: sortInstances(instances),
    apps: snap.apps ?? [],
    runs: sortRuns((snap.runs ?? []).map(cleanSummary)),
    settings: snap.settings ?? null,
    // O snapshot manda os workers (v0.8). Backend antigo não manda: aí preserva o que já havia em vez de apagar.
    workers: snap.workers ? Object.fromEntries(snap.workers.map((w) => [w.id, w])) : state.workers,
    // Comandos em voo vêm no snapshot: recarregar a página no meio de um comando não pode fazer o painel
    // esquecer que o aparelho está ocupado e reoferecer o botão.
    lastCommand: snap.commands
      ? { ...state.lastCommand, ...Object.fromEntries(snap.commands.map((c) => [c.instance_id, c])) }
      : state.lastCommand,
  };
}

// ---- mutações vindas de respostas REST ----------------------------------------------------------

export function upsertRun(state: DataState, run: RunSummary): DataState {
  const clean = cleanSummary(run);
  const runs = sortRuns(upsertBy(state.runs, clean, (r) => r.id));
  let detail = state.detail;
  if (detail?.data && detail.runId === clean.id) {
    detail = { ...detail, data: { ...detail.data, ...clean } };
  }
  return { ...state, runs, detail };
}

export function mergeRuns(state: DataState, list: RunSummary[]): DataState {
  let runs = state.runs;
  for (const r of list) runs = upsertBy(runs, cleanSummary(r), (x) => x.id);
  return { ...state, runs: sortRuns(runs) };
}

export function upsertInstance(state: DataState, inst: Instance): DataState {
  const instances = { ...state.instances, [inst.id]: inst };
  const instanceOrder = state.instances[inst.id] ? state.instanceOrder : sortInstances(instances);
  return { ...state, instances, instanceOrder };
}

export function patchInstance(state: DataState, id: string, patch: Partial<Instance>): DataState {
  const prev = state.instances[id];
  if (!prev) return state;
  return { ...state, instances: { ...state.instances, [id]: { ...prev, ...patch } } };
}

export function upsertObjective(state: DataState, objective: Objective): DataState {
  const d = state.detail;
  if (!d?.data || d.runId !== objective.run_id) return state;
  return { ...state, detail: { ...d, data: { ...d.data, objectives: upsertBy(d.data.objectives, objective, (o) => o.id) } } };
}

// ---- detalhe da execução ------------------------------------------------------------------------

function mergeAttempt(prev: Attempt, next: Attempt): Attempt {
  // `attempt.updated` chega SEM `actions`: preserva as que já temos.
  const actions = Array.isArray(next.actions) && next.actions.length > 0 ? mergeActions(prev.actions, next.actions) : prev.actions;
  return { ...prev, ...next, actions };
}

function mergeActions(prev: readonly Action[], incoming: readonly Action[]): Action[] {
  let out: Action[] = prev.slice();
  for (const a of incoming) out = upsertBy(out, a, (x) => x.id);
  return out.sort((a, b) => (a.seq - b.seq) || (a.id - b.id));
}

function sameDecision(a: RunDetail['decisions'][number], b: RunDetail['decisions'][number]): boolean {
  return a.ts === b.ts && a.instance_id === b.instance_id && a.text === b.text;
}

/**
 * A qual execução o evento pertence. Normalmente `ev.run_id` basta; os demais caminhos são só uma rede
 * de segurança caso o backend deixe o campo nulo (o id da etapa começa com o id da execução).
 */
export function eventRunId(ev: EventRecord): string | null {
  if (ev.run_id) return ev.run_id;
  const data = ev.data;
  const run = obj<RunSummary>(data, 'run');
  if (run?.id) return run.id;
  for (const key of ['objective', 'step', 'evidence']) {
    const entity = obj<{ run_id?: unknown }>(data, key);
    if (entity && typeof entity.run_id === 'string') return entity.run_id;
  }
  const attempt = obj<Attempt>(data, 'attempt');
  const stepId = ev.step_id ?? (typeof data?.step_id === 'string' ? data.step_id : null) ?? attempt?.step_id ?? null;
  if (typeof stepId === 'string' && stepId.includes(':')) return stepId.slice(0, stepId.indexOf(':'));
  return null;
}

/**
 * Aplica um evento ao RunDetail carregado. Idempotente: reaplicar o mesmo evento não duplica nada,
 * o que permite reprocessar o buffer de eventos recebidos enquanto o GET /runs/{id} estava em voo.
 */
export function reduceDetail(detail: RunDetail, ev: EventRecord): RunDetail {
  const data = ev.data;
  switch (ev.kind) {
    case 'run.updated': {
      const run = obj<RunSummary>(data, 'run');
      if (!run || run.id !== detail.id) return detail;
      return { ...detail, ...cleanSummary(run) };
    }
    case 'objective.updated': {
      const objective = obj<Objective>(data, 'objective');
      if (!objective || objective.run_id !== detail.id) return detail;
      return { ...detail, objectives: upsertBy(detail.objectives, objective, (o) => o.id) };
    }
    case 'step.updated': {
      const step = obj<Step>(data, 'step');
      if (!step || step.run_id !== detail.id) return detail;
      return { ...detail, steps: upsertBy(detail.steps, step, (s) => s.id) };
    }
    case 'attempt.updated': {
      const attempt = obj<Attempt>(data, 'attempt');
      if (!attempt || eventRunId(ev) !== detail.id) return detail;
      const normalized: Attempt = { ...attempt, actions: Array.isArray(attempt.actions) ? attempt.actions : [] };
      return { ...detail, attempts: upsertBy(detail.attempts, normalized, (a) => a.id, mergeAttempt) };
    }
    case 'action.logged': {
      const action = obj<Action>(data, 'action');
      if (!action || eventRunId(ev) !== detail.id) return detail;
      const existing = detail.attempts.find((a) => a.id === action.attempt_id);
      if (existing) {
        const updated: Attempt = { ...existing, actions: mergeActions(existing.actions, [action]) };
        return { ...detail, attempts: upsertBy(detail.attempts, updated, (a) => a.id) };
      }
      // A ação chegou antes da tentativa: cria uma "casca" que o próximo `attempt.updated` completa.
      const stepId = typeof data?.step_id === 'string' ? data.step_id : ev.step_id;
      if (!stepId) return detail;
      const siblings = detail.attempts.filter((a) => a.step_id === stepId).length;
      const shell: Attempt = {
        id: action.attempt_id,
        step_id: stepId,
        number: siblings + 1,
        status: 'running',
        started_at: action.intent_at,
        finished_at: null,
        error: null,
        recovery: null,
        observed_result: null,
        actions: [action],
      };
      return { ...detail, attempts: [...detail.attempts, shell] };
    }
    case 'evidence.added': {
      const evidence = obj<Evidence>(data, 'evidence');
      if (!evidence || evidence.run_id !== detail.id) return detail;
      return { ...detail, evidence: upsertBy(detail.evidence, evidence, (e) => e.id) };
    }
    case 'decision': {
      if (eventRunId(ev) !== detail.id) return detail;
      const text = typeof data?.text === 'string' ? data.text : ev.message;
      if (!text) return detail;
      const entry = { ts: ev.ts, instance_id: ev.instance_id, text };
      if (detail.decisions.some((d) => sameDecision(d, entry))) return detail;
      return { ...detail, decisions: [...detail.decisions, entry] };
    }
    default:
      return detail;
  }
}

/** Insere eventos persistidos na linha do tempo sem duplicar, mantendo a ordem por id. */
export function mergeTimeline(current: readonly EventRecord[], incoming: readonly EventRecord[]): EventRecord[] {
  if (incoming.length === 0) return current as EventRecord[];
  const last = current.length > 0 ? (current[current.length - 1] as EventRecord).id ?? 0 : 0;
  const allNewer = incoming.every((e, i) => {
    if (typeof e.id !== 'number') return false;
    const prev = i === 0 ? last : (incoming[i - 1] as EventRecord).id ?? 0;
    return e.id > prev;
  });
  let merged: EventRecord[];
  if (allNewer) {
    merged = [...current, ...incoming];
  } else {
    const byId = new Map<number, EventRecord>();
    for (const e of current) if (typeof e.id === 'number') byId.set(e.id, e);
    for (const e of incoming) if (typeof e.id === 'number') byId.set(e.id, e);
    merged = Array.from(byId.values()).sort((a, b) => (a.id as number) - (b.id as number));
  }
  return merged.length > MAX_TIMELINE_EVENTS ? merged.slice(merged.length - MAX_TIMELINE_EVENTS) : merged;
}

function reduceDetailState(d: RunDetailState | null, ev: EventRecord): RunDetailState | null {
  if (!d) return d;
  if (eventRunId(ev) !== d.runId) return d;
  const data = d.data ? reduceDetail(d.data, ev) : d.data;
  const events = typeof ev.id === 'number' ? mergeTimeline(d.events, [ev]) : d.events;
  if (data === d.data && events === d.events) return d;
  return { ...d, data, events };
}


// ---- evento → estado global ---------------------------------------------------------------------

export function applyEvent(state: DataState, ev: EventRecord): DataState {
  let next = state;

  // Idempotência: só eventos persistidos têm id; efêmeros (frame, metrics…) chegam com id = null
  // e nunca podem ser descartados por essa regra.
  if (typeof ev.id === 'number') {
    if (ev.id <= state.lastEventId) return state;
    const recent = [...state.recentEvents, ev];
    next = {
      ...state,
      lastEventId: ev.id,
      recentEvents: recent.length > MAX_RECENT_EVENTS ? recent.slice(recent.length - MAX_RECENT_EVENTS) : recent,
    };
  }

  const data = ev.data;
  switch (ev.kind) {
    case 'instance.updated': {
      const inst = obj<Instance>(data, 'instance');
      if (inst && typeof inst.id === 'string') next = upsertInstance(next, inst);
      break;
    }
    case 'frame': {
      const id = typeof data?.instance_id === 'string' ? data.instance_id : ev.instance_id;
      const frame = obj<FrameInfo>(data, 'frame');
      if (id && frame) next = patchInstance(next, id, { frame });
      break;
    }
    case 'control.changed': {
      const id = typeof data?.instance_id === 'string' ? data.instance_id : ev.instance_id;
      const control = data?.control;
      if (id && (control === 'none' || control === 'ai' || control === 'user')) {
        const prev = next.instances[id];
        next = patchInstance(next, id, {
          control,
          control_pending: data?.pending === true,
          control_since: prev && prev.control === control ? prev.control_since : ev.ts,
        });
      }
      break;
    }
    case 'worker.updated': {
      const w = obj<Worker>(data, 'worker');
      if (w && typeof w.id === 'string') next = { ...next, workers: { ...next.workers, [w.id]: w } };
      break;
    }
    case 'worker.removed': {
      const workerId = typeof data?.worker_id === 'string' ? data.worker_id : null;
      if (workerId && workerId in next.workers) {
        const { [workerId]: _removido, ...restantes } = next.workers;
        next = { ...next, workers: restantes };
      }
      break;
    }
    case 'command.updated': {
      const cmd = obj<Command>(data, 'command');
      if (cmd && typeof cmd.instance_id === 'string' && typeof cmd.id === 'string') {
        const anterior = next.lastCommand[cmd.instance_id];
        if (aceitaComando(anterior, cmd)) {
          next = { ...next, lastCommand: { ...next.lastCommand, [cmd.instance_id]: cmd } };
        }
      }
      break;
    }
    case 'app_state.updated': {
      const app = obj<DeviceAppState>(data, 'app_state');
      if (app && typeof app.instance_id === 'string' && typeof app.package_name === 'string') {
        next = { ...next,
          appState: { ...next.appState, [chaveDoApp(app.instance_id, app.package_name)]: app } };
      }
      break;
    }
    case 'metrics': {
      const metrics = obj<Metrics>(data, 'metrics');
      if (metrics) next = { ...next, metrics };
      break;
    }
    case 'health.updated': {
      const health = obj<Health>(data, 'health');
      if (health) next = { ...next, health };
      break;
    }
    case 'apps.updated': {
      if (data && Array.isArray(data.apps)) next = { ...next, apps: data.apps as AppConfig[] };
      break;
    }
    case 'settings.updated': {
      const settings = obj<Settings>(data, 'settings');
      if (settings) next = { ...next, settings };
      break;
    }
    case 'run.updated': {
      const run = obj<RunSummary>(data, 'run');
      if (run && typeof run.id === 'string') {
        next = { ...next, runs: sortRuns(upsertBy(next.runs, cleanSummary(run), (r) => r.id)) };
      }
      break;
    }
    default:
      break;
  }

  const detail = reduceDetailState(next.detail, ev);
  if (detail !== next.detail) next = { ...next, detail };
  return next;
}
