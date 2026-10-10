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

// ---- tempo relativo ("há X") ------------------------------------------------------------------------
// Fonte única do "há quanto tempo" do portal (RF-06 da revisão final). Havia dois formatadores: este módulo dizia
// "há 1 min 14 s" e "há 6 d" (Foco, Pendências, Execuções…), e `lib/rotulos` dizia "há 1 min" e "há 6 dias" (cartão):
// o mesmo quadro aparecia com dois textos no cartão e no Foco. Ficou o da tarefa 04, a ordem de grandeza.

const S = 1000;
const MIN = 60 * S;
const H = 60 * MIN;
const D = 24 * H;

function unidades(n: number, singular: string, plural: string): string {
  return `${n} ${n === 1 ? singular : plural}`;
}

/**
 * Duração em uma unidade só, arredondada para baixo: "12 s", "1 min", "3 h", "6 dias", "2 meses".
 * "há 161 h" vira "há 6 dias" e "há 1 min 14 s" vira "há 1 min": quem olha quer a ordem de grandeza, não a precisão.
 */
export function duracaoHumana(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  if (ms < MIN) return `${Math.floor(ms / S)} s`;
  if (ms < H) return `${Math.floor(ms / MIN)} min`;
  if (ms < D) return `${Math.floor(ms / H)} h`;
  const dias = Math.floor(ms / D);
  if (dias < 30) return unidades(dias, 'dia', 'dias');
  if (dias < 365) return unidades(Math.floor(dias / 30), 'mês', 'meses');
  return unidades(Math.floor(dias / 365), 'ano', 'anos');
}

/** "agora", "há 12 s", "há 1 min", "há 3 h", "há 6 dias". Sem instante válido: "—". */
export function tempoRelativo(iso: string | null | undefined, agoraMs: number): string {
  const idade = ageMs(iso, agoraMs);
  if (idade === null) return '—';
  if (idade < 5 * S) return 'agora';
  return `há ${duracaoHumana(idade)}`;
}

// ---- durações -------------------------------------------------------------------------------------

/** Duração compacta (tempo de execução, não "há X"): "850 ms", "12 s", "3 min 05 s", "2 h 10 min". */
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

/**
 * O fuso do navegador NAQUELE instante, como diferença de UTC ("UTC-3", "UTC+5:30", "UTC"). Quem compara a tela com
 * `date -u` ou com a hora do servidor precisa saber de qual relógio é o número (31.294): as amostras e a API vêm em UTC.
 */
export function rotuloDoFuso(iso: string | null | undefined): string {
  const t = parseTs(iso);
  const min = -new Date(t ?? Date.now()).getTimezoneOffset();
  if (min === 0) return 'UTC';
  const sinal = min < 0 ? '-' : '+';
  const abs = Math.abs(min);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return `UTC${sinal}${h}${m ? `:${String(m).padStart(2, '0')}` : ''}`;
}

/** `formatClock` com o fuso à vista: "22:20:43 (UTC-3)". Sem instante, "—". */
export function formatClockComFuso(iso: string | null | undefined): string {
  const t = parseTs(iso);
  return t === null ? '—' : `${timeFmt.format(t)} (${rotuloDoFuso(iso)})`;
}

export function formatDateTime(iso: string | null | undefined): string {
  const t = parseTs(iso);
  return t === null ? '—' : dateTimeFmt.format(t);
}

const horaFmt = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });
const diaFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
const diaCurtoFmt = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit' });

/**
 * Um instante que pode ser de qualquer dia: "hoje, 20:47", "ontem, 09:12", "29/09 20:47" (outro ano: "29/09/2025").
 * `formatClock` sozinho num registro antigo engana: "último uso 20:47" de três dias atrás parece de hoje.
 */
export function formatQuando(iso: string | null | undefined, agoraMs: number = Date.now()): string {
  const t = parseTs(iso);
  if (t === null) return '—';
  const dia = (ms: number) => diaFmt.format(ms);
  const hora = horaFmt.format(t);
  if (dia(t) === dia(agoraMs)) return `hoje, ${hora}`;
  if (dia(t) === dia(agoraMs - 86_400_000)) return `ontem, ${hora}`;
  const mesmoAno = new Date(t).getFullYear() === new Date(agoraMs).getFullYear();
  return mesmoAno ? `${diaCurtoFmt.format(t)} ${hora}` : dia(t);
}
