import { create } from 'zustand';
import type { AppConfig, EventRecord, Instance, Objective, RunDetail, RunSummary, Settings, Snapshot } from '../api/types';
import { slotWaitDetail, type ConnStatus } from '../lib/status';
import {
  applyEvent, hydrateFromSnapshot, initialDataState, mergeRuns, mergeTimeline, patchInstance, reduceDetail,
  upsertInstance, upsertObjective, upsertRun,
  type DataState, type DetailError,
} from './reducer';

export interface ConnState {
  status: ConnStatus;
  /** Tentativas seguidas sem sucesso (0 quando conectado). */
  attempt: number;
  /** Quando (relógio local, ms) a próxima tentativa automática acontece. */
  nextRetryAt: number | null;
  lastError: string | null;
  /** Última vez (relógio local, ms) em que a conexão esteve boa. */
  lastConnectedAt: number | null;
}

interface AppStore extends DataState {
  conn: ConnState;

  setConn: (patch: Partial<ConnState>) => void;
  hydrate: (snap: Snapshot) => void;
  applyEvent: (ev: EventRecord) => void;

  upsertRun: (run: RunSummary) => void;
  mergeRuns: (runs: RunSummary[]) => void;
  upsertInstance: (inst: Instance) => void;
  patchInstance: (id: string, patch: Partial<Instance>) => void;
  upsertObjective: (objective: Objective) => void;
  setApps: (apps: AppConfig[]) => void;
  setSettings: (settings: Settings) => void;

  detailLoading: (runId: string) => void;
  detailLoaded: (runId: string, data: RunDetail, buffered: readonly EventRecord[]) => void;
  detailFailed: (runId: string, error: DetailError) => void;
  detailEventsLoaded: (runId: string, events: EventRecord[] | null) => void;
  detailClear: () => void;
}

export const useAppStore = create<AppStore>((set) => ({
  ...initialDataState,
  conn: { status: 'connecting', attempt: 0, nextRetryAt: null, lastError: null, lastConnectedAt: null },

  setConn: (patch) => set((s) => ({ conn: { ...s.conn, ...patch } })),
  hydrate: (snap) => set((s) => hydrateFromSnapshot(s, snap)),
  applyEvent: (ev) =>
    set((s) => {
      const next = applyEvent(s, ev);
      return next === s ? s : next;
    }),

  upsertRun: (run) => set((s) => upsertRun(s, run)),
  mergeRuns: (runs) => set((s) => mergeRuns(s, runs)),
  upsertInstance: (inst) => set((s) => upsertInstance(s, inst)),
  patchInstance: (id, patch) => set((s) => patchInstance(s, id, patch)),
  upsertObjective: (objective) => set((s) => upsertObjective(s, objective)),
  setApps: (apps) => set({ apps }),
  setSettings: (settings) => set({ settings }),

  detailLoading: (runId) =>
    set((s) => {
      if (s.detail?.runId === runId) return { detail: { ...s.detail, status: s.detail.data ? s.detail.status : 'loading', error: null } };
      return { detail: { runId, status: 'loading', data: null, error: null, events: [], eventsStatus: 'loading' } };
    }),

  detailLoaded: (runId, data, buffered) =>
    set((s) => {
      if (s.detail?.runId !== runId) return s;
      // Reaplica o que chegou pelo WebSocket enquanto o GET estava em voo (idempotente).
      const merged = buffered.reduce(reduceDetail, data);
      return { detail: { ...s.detail, status: 'ready', data: merged, error: null } };
    }),

  detailFailed: (runId, error) =>
    set((s) => {
      if (s.detail?.runId !== runId) return s;
      // Se já havia dados, mantém na tela (o erro aparece como toast); senão mostra o estado de erro.
      if (s.detail.data) return { detail: { ...s.detail, status: 'ready' } };
      return { detail: { ...s.detail, status: 'error', error } };
    }),

  detailEventsLoaded: (runId, events) =>
    set((s) => {
      if (s.detail?.runId !== runId) return s;
      if (events === null) return { detail: { ...s.detail, eventsStatus: s.detail.events.length > 0 ? 'ready' : 'error' } };
      return { detail: { ...s.detail, eventsStatus: 'ready', events: mergeTimeline(s.detail.events, events) } };
    }),

  detailClear: () => set({ detail: null }),
}));

// ---- seletores derivados ------------------------------------------------------------------------

export function selectInstanceList(s: Pick<DataState, 'instances' | 'instanceOrder'>): Instance[] {
  const out: Instance[] = [];
  for (const id of s.instanceOrder) {
    const inst = s.instances[id];
    if (inst) out.push(inst);
  }
  return out;
}

/** A IA pode planejar/executar? (chave configurada OU modo simulado) */
export function aiAvailable(s: Pick<DataState, 'health'>): boolean {
  const ai = s.health?.ai;
  return !!ai && (ai.configured || ai.simulated);
}

/**
 * Texto "aguardando vaga (k/K ligados)" do objetivo pendente desta instância. O `InstanceCurrent` do contrato
 * não traz `status_detail`, então a única fonte é o objetivo — disponível para a execução carregada no detalhe.
 */
export function selectSlotWait(s: Pick<DataState, 'detail'>, instanceId: string): string | null {
  const objectives = s.detail?.data?.objectives;
  if (!objectives) return null;
  for (const o of objectives) {
    if (o.instance_id !== instanceId) continue;
    const wait = slotWaitDetail(o);
    if (wait) return wait;
  }
  return null;
}
