import { wsUrl } from './client';
import type { ClientMessage, EventRecord, ServerMessage } from './types';

export interface LiveSocketHandlers {
  onHello: (serverTime: string, lastEventId: number) => void;
  onEvent: (event: EventRecord) => void;
  onResync: () => void;
  /**
   * Chamado uma única vez quando o socket fecha (por erro, pelo servidor ou por watchdog).
   *
   * `code` é o do `CloseEvent`, e existe aqui por um motivo só: `4401` (sessão ausente/expirada) e `4403`
   * (host não permitido) são as duas recusas que NÃO se resolvem tentando de novo. Sem o código, o painel
   * ficaria em backoff eterno contra uma porta que nunca vai abrir sem login.
   */
  onClose: (reason: string, code?: number) => void;
}

const PING_INTERVAL_MS = 20_000;
const PONG_TIMEOUT_MS = 10_000;
const HELLO_TIMEOUT_MS = 10_000;
/** Validade pedida ao servidor para o interesse em prévia (contrato C2: 5–60 s). */
export const WATCH_TTL_S = 20;
/** Renovação bem antes de vencer: uma renovação perdida (aba ocupada, rede lenta) ainda não derruba a prévia. */
export const WATCH_RENEW_MS = 8_000;

/**
 * Interesse em prévia desta aba (contrato C2): `grid` = aparelhos cuja miniatura está VISÍVEL agora, `focus` = o
 * aparelho aberto no Foco. Vazio é um pedido legítimo ("não estou olhando nada"), não ausência de pedido.
 */
export interface WatchInterest {
  grid: readonly string[];
  focus: string | null;
}

export const EMPTY_WATCH: WatchInterest = { grid: [], focus: null };

function sameWatch(a: WatchInterest, b: WatchInterest): boolean {
  if (a.focus !== b.focus || a.grid.length !== b.grid.length) return false;
  const bs = new Set(b.grid);
  return a.grid.every((id) => bs.has(id));
}

function parseServerMessage(raw: unknown): ServerMessage | null {
  if (typeof raw !== 'string') return null;
  let msg: unknown;
  try {
    msg = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!msg || typeof msg !== 'object') return null;
  const m = msg as Record<string, unknown>;
  switch (m.type) {
    case 'hello':
      if (typeof m.server_time === 'string' && typeof m.last_event_id === 'number') {
        return { type: 'hello', server_time: m.server_time, last_event_id: m.last_event_id };
      }
      return null;
    case 'event':
      if (m.event && typeof m.event === 'object' && typeof (m.event as { kind?: unknown }).kind === 'string') {
        return { type: 'event', event: m.event as EventRecord };
      }
      return null;
    case 'resync':
      return { type: 'resync' };
    case 'pong':
      return { type: 'pong' };
    default:
      return null;
  }
}

/**
 * Um socket = uma conexão. Não reconecta sozinho: quem decide quando refazer snapshot + WS é o
 * controlador em `store/live.ts` (o contrato exige snapshot antes de cada abertura).
 */
export class LiveSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private pongTimer: ReturnType<typeof setTimeout> | null = null;
  private helloTimer: ReturnType<typeof setTimeout> | null = null;
  private watchTimer: ReturnType<typeof setInterval> | null = null;
  private watch: WatchInterest;
  /** O que o servidor ouviu por último desta conexão (`null` = nada ainda): evita reenviar o mesmo conjunto. */
  private sentWatch: WatchInterest | null = null;
  private readonly handlers: LiveSocketHandlers;

  constructor(lastEventId: number, handlers: LiveSocketHandlers, initialWatch: WatchInterest | null = null) {
    this.handlers = handlers;
    this.watch = initialWatch ?? EMPTY_WATCH;
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl(lastEventId));
    } catch (e) {
      this.closed = true;
      queueMicrotask(() => handlers.onClose(e instanceof Error ? e.message : 'Falha ao abrir o WebSocket'));
      return;
    }
    this.ws = ws;
    this.helloTimer = setTimeout(() => this.fail('O servidor não enviou "hello" a tempo'), HELLO_TIMEOUT_MS);

    ws.onopen = () => {
      this.pingTimer = setInterval(() => this.ping(), PING_INTERVAL_MS);
      // O interesse vale por conexão (desconectar apaga no servidor): toda abertura manda o conjunto atual, que
      // pode ter mudado enquanto o socket ainda conectava (cartões montando, IntersectionObserver respondendo).
      this.sendWatchNow();
      this.watchTimer = setInterval(() => this.sendWatchNow(), WATCH_RENEW_MS);
    };
    ws.onmessage = (ev) => {
      if (this.closed) return;
      this.clearPongTimer(); // qualquer mensagem prova que a conexão está viva
      const msg = parseServerMessage(ev.data);
      if (!msg) return;
      switch (msg.type) {
        case 'hello':
          if (this.helloTimer) clearTimeout(this.helloTimer);
          this.helloTimer = null;
          handlers.onHello(msg.server_time, msg.last_event_id);
          break;
        case 'event':
          handlers.onEvent(msg.event);
          break;
        case 'resync':
          handlers.onResync();
          break;
        case 'pong':
          break;
      }
    };
    ws.onerror = () => {
      // O navegador não expõe detalhes; o `onclose` que vem em seguida faz a limpeza.
    };
    ws.onclose = (ev) => this.fail(ev.reason || `Conexão encerrada (código ${ev.code})`, ev.code);
  }

  /**
   * Troca o interesse em prévia. Só vai ao servidor se mudou; a renovação periódica (`WATCH_RENEW_MS`) reenvia o
   * mesmo conjunto — inclusive o vazio: deixar um `watch` vazio vencer entregaria o significado ao servidor.
   * Substitui a antiga mensagem `focus` (que o backend ainda aceita): o `watch` já diz o foco, e mandar as duas
   * seria renovar duas vezes a mesma coisa com TTLs diferentes.
   */
  setWatch(interest: WatchInterest): void {
    this.watch = { grid: [...interest.grid], focus: interest.focus };
    if (this.sentWatch && sameWatch(this.sentWatch, this.watch)) return;
    this.sendWatchNow();
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.cleanupTimers();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
      try {
        // Fechar já apaga o interesse no servidor (C2); o `watch` vazio antes só antecipa isso quando havia algo.
        const sent = this.sentWatch;
        if (ws.readyState === WebSocket.OPEN && sent && (sent.grid.length > 0 || sent.focus)) {
          ws.send(JSON.stringify({ type: 'watch', grid: [], focus: null, ttl_s: WATCH_TTL_S } satisfies ClientMessage));
        }
        ws.close();
      } catch {
        /* já fechado */
      }
    }
  }

  private send(msg: ClientMessage): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify(msg));
    } catch {
      /* o onclose cuidará do resto */
    }
  }

  private sendWatchNow(): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return; // o `onopen` manda o conjunto guardado
    const w = this.watch;
    this.send({ type: 'watch', grid: [...w.grid], focus: w.focus, ttl_s: WATCH_TTL_S });
    this.sentWatch = w;
  }

  private ping(): void {
    this.send({ type: 'ping' });
    this.clearPongTimer();
    this.pongTimer = setTimeout(() => this.fail('Sem resposta do servidor (ping sem pong)'), PONG_TIMEOUT_MS);
  }

  private clearPongTimer(): void {
    if (this.pongTimer) clearTimeout(this.pongTimer);
    this.pongTimer = null;
  }

  private cleanupTimers(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.watchTimer) clearInterval(this.watchTimer);
    if (this.helloTimer) clearTimeout(this.helloTimer);
    this.clearPongTimer();
    this.pingTimer = this.watchTimer = null;
    this.helloTimer = null;
  }

  private fail(reason: string, code?: number): void {
    if (this.closed) return;
    this.close();
    this.handlers.onClose(reason, code);
  }
}
