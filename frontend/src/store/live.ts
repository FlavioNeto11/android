import { ApiError, api, hintForError, toApiError } from '../api/client';
import type { EventRecord, RunSummary } from '../api/types';
import { EMPTY_WATCH, LiveSocket, type WatchInterest } from '../api/ws';
import { backoffDelay } from '../lib/backoff';
import { isRecord } from '../lib/format';
import { isRunTerminal } from '../lib/status';
import { setServerTime } from '../lib/time';
import { useAppStore } from './app';
import { releaseAllLeasesOnUnload, useControlStore } from './control';
import { usePreviewStore, visibleGrid } from './preview';
import { eventRunId } from './reducer';
import { toast, toastError } from './toasts';
import { useSessionStore } from './session';
import { useUiStore } from './ui';

/**
 * Controlador da conexão ao vivo. Ciclo (no carregamento e em TODA reconexão):
 *   GET /api/snapshot → hidrata o store → abre WS /api/ws?last_event_id=<snapshot.last_event_id>
 * Eventos são aplicados de forma idempotente pelo reducer. `{type:'resync'}` refaz o ciclo.
 * Este módulo só LÊ dados: reconectar nunca cria, inicia ou reenfileira execuções.
 */

/** Depois de tantas falhas seguidas o indicador muda de "reconectando…" para "desconectado". */
const DISCONNECTED_AFTER_ATTEMPTS = 3;
const EVENTS_PAGE = 500;
const EVENTS_MAX_PAGES = 10;
/** Rolar a grade dispara o IntersectionObserver em rajada: junta as mudanças antes de avisar o servidor. */
export const WATCH_COALESCE_MS = 150;

let started = false;
let socket: LiveSocket | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let attempt = 0;
let cycleToken = 0;
let focusId: string | null = null;
let watchSyncTimer: ReturnType<typeof setTimeout> | null = null;
let recentResyncs: number[] = [];
let cleanupFns: Array<() => void> = [];

// ---- detalhe da execução selecionada ----
let detailToken = 0;
let detailAbort: AbortController | null = null;
let detailFetchRunId: string | null = null;
let detailBuffer: EventRecord[] = [];
let detailRefetchTimer: ReturnType<typeof setTimeout> | null = null;

function clearRetryTimer(): void {
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
}

function scheduleRetry(reason: string): void {
  if (!started) return;
  clearRetryTimer();
  attempt += 1;
  const delay = backoffDelay(attempt);
  useAppStore.getState().setConn({
    status: attempt >= DISCONNECTED_AFTER_ATTEMPTS ? 'disconnected' : 'reconnecting',
    attempt,
    nextRetryAt: Date.now() + delay,
    lastError: reason,
  });
  retryTimer = setTimeout(() => void cycle(), delay);
}

async function cycle(): Promise<void> {
  if (!started) return;
  const token = ++cycleToken;
  clearRetryTimer();
  socket?.close();
  socket = null;

  const store = useAppStore.getState();
  if (attempt === 0 && !store.hydrated) store.setConn({ status: 'connecting', nextRetryAt: null });
  else store.setConn({ nextRetryAt: null });

  try {
    const snap = await api.snapshot();
    if (token !== cycleToken || !started) return;
    if (!isRecord(snap) || !Array.isArray(snap.instances)) {
      throw new Error('O snapshot recebido não tem o formato esperado.');
    }
    setServerTime(snap.server_time);
    useAppStore.getState().hydrate(snap);
    useUiStore.getState().pruneSelection(snap.instances.map((i) => i.id));
    for (const inst of snap.instances) useControlStore.getState().reconcile(inst);
    autoSelectRun(snap.runs ?? []);

    // O detalhe aberto pode ter perdido eventos (queda longa / resync): recarrega em segundo plano.
    const selected = useUiStore.getState().selectedRunId;
    if (selected) void loadRunDetail(selected, { silent: true });

    socket = new LiveSocket(
      snap.last_event_id,
      {
        onHello: (serverTime) => {
          if (token !== cycleToken) return;
          setServerTime(serverTime);
          attempt = 0;
          useAppStore.getState().setConn({
            status: 'connected', attempt: 0, nextRetryAt: null, lastError: null, lastConnectedAt: Date.now(),
          });
        },
        onEvent: (ev) => {
          if (token !== cycleToken) return;
          handleEvent(ev);
        },
        onResync: () => {
          if (token !== cycleToken) return;
          const now = Date.now();
          recentResyncs = recentResyncs.filter((t) => now - t < 30_000);
          recentResyncs.push(now);
          // Resync é normal após uma ausência longa; em rajada indica problema → aplica backoff.
          if (recentResyncs.length > 2) scheduleRetry('O servidor pediu ressincronização repetidas vezes');
          else void cycle();
        },
        onClose: (reason, code) => {
          if (token !== cycleToken) return;
          socket = null;
          // 4401/4403 não se resolvem tentando de novo: falta login, ou este endereço não é aceito pelo
          // backend. Insistir seria martelar o servidor e deixar o painel dizendo "reconectando…" para sempre.
          if (code === 4401 || code === 4403) {
            useAppStore.getState().setConn({ status: 'disconnected', nextRetryAt: null, lastError: reason });
            if (code === 4401) useSessionStore.getState().markUnauthorized();
            else toastError('O backend recusou este endereço', new ApiError(403, 'forbidden_host', reason),
                            { key: 'ws-host' });
            return;
          }
          scheduleRetry(reason);
        },
      },
      currentWatch(),
    );
  } catch (e) {
    if (token !== cycleToken || !started) return;
    const err = toApiError(e);
    if (err.status === 401 || err.status === 403) {
      // O snapshot é a primeira chamada do ciclo: se ela diz "sem credencial" (401) ou "host não permitido"
      // (403), repetir em backoff só atrasa a tela que resolve — login, ou o endereço certo.
      useAppStore.getState().setConn({ status: 'disconnected', nextRetryAt: null, lastError: err.message });
      if (err.status === 401) useSessionStore.getState().markUnauthorized();
      else toastError('O backend recusou a chamada', err, { key: 'snapshot-403' });
      return;
    }
    scheduleRetry(err.message);
  }
}

function autoSelectRun(runs: RunSummary[]): void {
  const ui = useUiStore.getState();
  if (ui.selectedRunId) return;
  const active = runs.find((r) => !isRunTerminal(r.status));
  if (active) ui.selectRun(active.id);
}

type LiveListener = (ev: EventRecord) => void;
const liveListeners = new Set<LiveListener>();

/** Assinatura dos eventos ao vivo para telas cujos dados não moram no store (abas do perfil: memória, interações,
 *  habilidades). Sem isto elas carregavam uma vez ao abrir e ficavam paradas enquanto o perfil agia. */
export function onLiveEvent(cb: LiveListener): () => void {
  liveListeners.add(cb);
  return () => {
    liveListeners.delete(cb);
  };
}

function handleEvent(ev: EventRecord): void {
  const before = useAppStore.getState();
  const prevRunStatus = ev.kind === 'run.updated' ? before.runs.find((r) => r.id === eventRunId(ev))?.status : undefined;
  before.applyEvent(ev);
  for (const ouvinte of liveListeners) {
    try {
      ouvinte(ev);
    } catch {
      /* uma tela com defeito não derruba o fluxo de eventos */
    }
  }

  const runId = eventRunId(ev);
  if (detailFetchRunId && runId === detailFetchRunId && typeof ev.id === 'number') detailBuffer.push(ev);

  const selected = useUiStore.getState().selectedRunId;
  if (selected && runId === selected) {
    if (ev.kind === 'plan.revised') scheduleDetailRefetch(300);
    if (ev.kind === 'run.updated') {
      const run = isRecord(ev.data) && isRecord(ev.data.run) ? (ev.data.run as unknown as RunSummary) : null;
      // Rede de segurança: ao terminar, recarrega o detalhe uma vez (com debounce).
      if (run && isRunTerminal(run.status) && (!prevRunStatus || !isRunTerminal(prevRunStatus))) scheduleDetailRefetch(1500);
      // Saiu do planejamento: o plano só vem no detalhe.
      if (run && prevRunStatus === 'planning' && run.status !== 'planning') scheduleDetailRefetch(200);
    }
  }

  // Erro do backend deixa de ser invisível. Antes, um `log` nível `error` — a recusa de uma ação, a falha de uma
  // entrega — só existia dentro de um painel fechado do Diagnóstico, e quem clicou nunca soube.
  if (ev.kind === 'log' && ev.level === 'error') {
    toast({
      tone: 'danger',
      title: ev.instance_id ? `Erro em ${ev.instance_id}` : 'Erro no backend',
      details: [ev.message],
      // Chave por aparelho: uma rajada de erros do mesmo aparelho não enterra a tela em avisos.
      key: `log-${ev.instance_id ?? 'geral'}`,
    });
  }

  // Achado #106: evento dedicado da fila "Aguardando intervenção" — não só `log`, que só quem tinha o
  // Diagnóstico aberto via. `active` distingue quem ACABOU de entrar (avisa) de quem SAIU (a reobservação
  // depois de devolver o controle resolveu sozinha; não há por que avisar disso).
  if (ev.kind === 'session.needs_person' && isRecord(ev.data) && ev.data.active === true) {
    const instanceId = typeof ev.data.instance_id === 'string' ? ev.data.instance_id : ev.instance_id;
    toast({
      tone: 'warning',
      title: instanceId ? `${instanceId}: um perfil precisa de intervenção` : 'Um perfil precisa de intervenção',
      message: typeof ev.data.detail === 'string' ? ev.data.detail : null,
      hint: 'Abra "Perfis do Instagram" — a fila "Aguardando intervenção" tem um botão para assumir o aparelho.',
      key: `needs-person-${instanceId ?? 'geral'}`,
    });
  }

  if (ev.kind === 'control.changed' || ev.kind === 'instance.updated') {
    const id = ev.instance_id
      ?? (isRecord(ev.data) && typeof ev.data.instance_id === 'string' ? ev.data.instance_id : null)
      ?? (isRecord(ev.data) && isRecord(ev.data.instance) && typeof ev.data.instance.id === 'string' ? ev.data.instance.id : null);
    const inst = id ? useAppStore.getState().instances[id] : undefined;
    if (inst) useControlStore.getState().reconcile(inst);
  }
}

function scheduleDetailRefetch(delayMs: number): void {
  if (detailRefetchTimer) clearTimeout(detailRefetchTimer);
  detailRefetchTimer = setTimeout(() => {
    detailRefetchTimer = null;
    const selected = useUiStore.getState().selectedRunId;
    if (selected) void loadRunDetail(selected, { silent: true });
  }, delayMs);
}

async function fetchAllRunEvents(runId: string, signal: AbortSignal): Promise<EventRecord[]> {
  // O contrato define `after` e `limit`; paginamos por id até vir uma página incompleta.
  const all: EventRecord[] = [];
  let after = 0;
  for (let page = 0; page < EVENTS_MAX_PAGES; page++) {
    const batch = await api.runEvents(runId, after, EVENTS_PAGE, signal);
    if (!Array.isArray(batch) || batch.length === 0) break;
    all.push(...batch);
    const ids = batch.map((e) => e.id).filter((id): id is number => typeof id === 'number');
    const maxId = ids.length > 0 ? Math.max(...ids) : after;
    if (batch.length < EVENTS_PAGE || maxId <= after) break;
    after = maxId;
  }
  return all;
}

/** Carrega (ou recarrega em silêncio) o RunDetail + a linha do tempo da execução. */
export async function loadRunDetail(runId: string | null, opts: { silent?: boolean } = {}): Promise<void> {
  const token = ++detailToken;
  detailAbort?.abort();
  detailAbort = null;
  detailFetchRunId = null;
  detailBuffer = [];

  const store = useAppStore.getState();
  if (!runId) {
    store.detailClear();
    return;
  }
  if (!opts.silent || store.detail?.runId !== runId) store.detailLoading(runId);

  const abort = new AbortController();
  detailAbort = abort;
  detailFetchRunId = runId;

  const detailPromise = api.getRun(runId, abort.signal).then(
    (data) => {
      if (token !== detailToken) return;
      const buffered = detailBuffer;
      detailBuffer = [];
      detailFetchRunId = null;
      useAppStore.getState().detailLoaded(runId, data, buffered);
      useAppStore.getState().upsertRun(summaryOf(data));
    },
    (e: unknown) => {
      if (token !== detailToken) return;
      detailFetchRunId = null;
      detailBuffer = [];
      const err = toApiError(e);
      useAppStore.getState().detailFailed(runId, { message: err.message, hint: hintForError(err), status: err.status });
      if (err.status === 404) {
        // Execução salva de uma sessão anterior que não existe mais.
        if (useUiStore.getState().selectedRunId === runId) useUiStore.getState().selectRun(null);
        toast({ tone: 'info', title: 'A execução selecionada não existe mais', message: 'A seleção foi limpa.', key: 'detail-404' });
      } else {
        toastError('Não foi possível carregar os detalhes da execução', err, { key: 'detail-error' });
      }
    },
  );

  const eventsPromise = fetchAllRunEvents(runId, abort.signal).then(
    (events) => {
      if (token !== detailToken) return;
      useAppStore.getState().detailEventsLoaded(runId, events);
    },
    (e: unknown) => {
      if (token !== detailToken) return;
      useAppStore.getState().detailEventsLoaded(runId, null);
      const err = toApiError(e);
      if (err.status !== 404) toastError('Não foi possível carregar a linha do tempo', err, { key: 'timeline-error' });
    },
  );

  await Promise.all([detailPromise, eventsPromise]);
}

function summaryOf(detail: RunSummary): RunSummary {
  return {
    id: detail.id, short_id: detail.short_id, command: detail.command, status: detail.status,
    simulated: detail.simulated, instance_ids: detail.instance_ids, instances_requested: detail.instances_requested,
    instances_used: detail.instances_used, created_at: detail.created_at, started_at: detail.started_at,
    finished_at: detail.finished_at, counts: detail.counts, progress: detail.progress, status_detail: detail.status_detail,
  };
}

/** Recarrega agora o detalhe aberto (após ações REST como resolve/retry). */
export function refreshSelectedRun(): void {
  const selected = useUiStore.getState().selectedRunId;
  if (selected) void loadRunDetail(selected, { silent: true });
}

/**
 * O que esta aba está olhando (contrato C2). Aba oculta não olha nada: manda o conjunto vazio em vez de deixar o
 * servidor capturando prévia para uma tela que ninguém vê — e o foco continua guardado para a volta.
 */
function currentWatch(): WatchInterest {
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return EMPTY_WATCH;
  return { grid: visibleGrid(usePreviewStore.getState().visible), focus: focusId };
}

/** Foco aberto/fechado e aba oculta/visível vão na hora: é o clique da pessoa esperando a tela. */
function syncWatchNow(): void {
  if (watchSyncTimer) clearTimeout(watchSyncTimer);
  watchSyncTimer = null;
  socket?.setWatch(currentWatch());
}

/** Mudança de cartões visíveis: junta a rajada da rolagem numa mensagem só. */
function syncWatchSoon(): void {
  if (watchSyncTimer) return;
  watchSyncTimer = setTimeout(() => {
    watchSyncTimer = null;
    socket?.setWatch(currentWatch());
  }, WATCH_COALESCE_MS);
}

/** Informa ao backend qual aparelho está em foco (vai no `watch`, renovado pelo socket). */
export function setFocusInstance(id: string | null): void {
  focusId = id;
  syncWatchNow();
}

/** Botão "Reconectar agora". */
export function reconnectNow(): void {
  if (!started) return;
  useAppStore.getState().setConn({ status: 'reconnecting', nextRetryAt: null });
  void cycle();
}

export function startLive(): () => void {
  if (started) return stopLive;
  started = true;
  attempt = 0;

  const unsubUi = useUiStore.subscribe((s, prev) => {
    if (s.selectedRunId !== prev.selectedRunId) void loadRunDetail(s.selectedRunId);
    if (s.focusInstanceId !== prev.focusInstanceId) setFocusInstance(s.focusInstanceId);
  });
  const onOnline = () => {
    if (useAppStore.getState().conn.status !== 'connected') reconnectNow();
  };
  const unsubPreview = usePreviewStore.subscribe((s, prev) => {
    if (s.visible !== prev.visible) syncWatchSoon();
  });
  const onVisible = () => {
    // Oculta → `watch` vazio; visível → o conjunto atual de novo (o `currentWatch` decide pelo estado da aba).
    syncWatchNow();
    if (document.visibilityState === 'visible' && useAppStore.getState().conn.status !== 'connected' && retryTimer) reconnectNow();
  };
  const onPageHide = () => releaseAllLeasesOnUnload();
  window.addEventListener('online', onOnline);
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('pagehide', onPageHide);
  cleanupFns = [
    unsubUi,
    unsubPreview,
    () => window.removeEventListener('online', onOnline),
    () => document.removeEventListener('visibilitychange', onVisible),
    () => window.removeEventListener('pagehide', onPageHide),
  ];

  focusId = useUiStore.getState().focusInstanceId;
  void cycle();
  const initialRun = useUiStore.getState().selectedRunId;
  if (initialRun) useAppStore.getState().detailLoading(initialRun); // o ciclo acima faz o GET após o snapshot
  return stopLive;
}

export function stopLive(): void {
  if (!started) return;
  started = false;
  cycleToken += 1;
  detailToken += 1;
  clearRetryTimer();
  if (watchSyncTimer) clearTimeout(watchSyncTimer);
  watchSyncTimer = null;
  if (detailRefetchTimer) clearTimeout(detailRefetchTimer);
  detailRefetchTimer = null;
  detailAbort?.abort();
  detailAbort = null;
  detailFetchRunId = null;
  detailBuffer = [];
  socket?.close();
  socket = null;
  for (const fn of cleanupFns) fn();
  cleanupFns = [];
}
