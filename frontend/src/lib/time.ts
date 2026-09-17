import { useSyncExternalStore } from 'react';

/**
 * Relógio compartilhado.
 *  - `serverOffsetMs` = relógio do servidor − relógio local, calculado a partir de `hello.server_time`
 *    (e do `snapshot.server_time` como primeira estimativa). Toda idade/"há Xs" usa `serverNow()`.
 *  - Um ÚNICO `setInterval` de 1 s alimenta `useNow()`; só os componentes que mostram tempo relativo assinam.
 */

let serverOffsetMs = 0;

export function computeServerOffset(serverTimeIso: string, localNowMs: number): number | null {
  const t = Date.parse(serverTimeIso);
  if (!Number.isFinite(t)) return null;
  return t - localNowMs;
}

export function setServerTime(serverTimeIso: string): void {
  const off = computeServerOffset(serverTimeIso, Date.now());
  if (off !== null) serverOffsetMs = off;
}

export function getServerOffsetMs(): number {
  return serverOffsetMs;
}

/** "Agora" no relógio do servidor, em ms. */
export function serverNow(): number {
  return Date.now() + serverOffsetMs;
}

// ---- timer único de 1 s -------------------------------------------------------------------------

const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let tickValue = Math.floor(Date.now() / 1000);

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (timer === null) {
    timer = setInterval(() => {
      tickValue = Math.floor(Date.now() / 1000);
      for (const l of listeners) l();
    }, 1000);
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getTick(): number {
  return tickValue;
}

/** Re-renderiza o componente a cada segundo e devolve o "agora" do servidor em ms. */
export function useNow(): number {
  useSyncExternalStore(subscribe, getTick, getTick);
  return serverNow();
}

// ---- formatação ----------------------------------------------------------------------------------

export function parseTs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

/** Idade em ms (nunca negativa) de um instante ISO em relação a `nowMs`. */
export function ageMs(iso: string | null | undefined, nowMs: number): number | null {
  const t = parseTs(iso);
  if (t === null) return null;
  return Math.max(0, nowMs - t);
}

/** "há 5 s", "há 3 min", "há 2 h", "há 4 d". */
export function formatAgo(iso: string | null | undefined, nowMs: number): string {
  const age = ageMs(iso, nowMs);
  if (age === null) return '—';
  return `há ${formatSpan(age)}`;
}

/** Duração compacta: "850 ms", "12 s", "3 min 05 s", "2 h 10 min". */
export function formatSpan(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return ms < 1 ? '0 s' : `${Math.round(ms)} ms`;
  const totalS = Math.floor(ms / 1000);
  if (totalS < 60) return `${totalS} s`;
  const totalMin = Math.floor(totalS / 60);
  if (totalMin < 60) {
    const s = totalS % 60;
    return s ? `${totalMin} min ${String(s).padStart(2, '0')} s` : `${totalMin} min`;
  }
  const totalH = Math.floor(totalMin / 60);
  if (totalH < 24) {
    const m = totalMin % 60;
    return m ? `${totalH} h ${String(m).padStart(2, '0')} min` : `${totalH} h`;
  }
  const d = Math.floor(totalH / 24);
  return `${d} d`;
}

/** Versão para "há X" sem milissegundos (idades abaixo de 1 s viram "agora"). */
export function formatAgoCoarse(iso: string | null | undefined, nowMs: number): string {
  const age = ageMs(iso, nowMs);
  if (age === null) return '—';
  if (age < 1000) return 'agora';
  return `há ${formatSpan(Math.floor(age / 1000) * 1000)}`;
}

/** Duração entre dois instantes (ou até agora, se `end` for nulo). */
export function formatDuration(start: string | null | undefined, end: string | null | undefined, nowMs: number): string {
  const s = parseTs(start);
  if (s === null) return '—';
  const e = parseTs(end) ?? nowMs;
  return formatSpan(Math.max(0, e - s));
}

const timeFmt = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const dateTimeFmt = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
});

export function formatClock(iso: string | null | undefined): string {
  const t = parseTs(iso);
  return t === null ? '—' : timeFmt.format(t);
}

export function formatDateTime(iso: string | null | undefined): string {
  const t = parseTs(iso);
  return t === null ? '—' : dateTimeFmt.format(t);
}
