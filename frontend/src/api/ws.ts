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
const FOCUS_RENEW_MS = 5_000;

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
  private focusTimer: ReturnType<typeof setInterval> | null = null;
  private focusId: string | null = null;
  private readonly handlers: LiveSocketHandlers;

  constructor(lastEventId: number, handlers: LiveSocketHandlers, initialFocus: string | null) {
    this.handlers = handlers;
    this.focusId = initialFocus;
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
      if (this.focusId) this.sendFocusNow();
      this.armFocusTimer();
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

  /** Define (ou limpa) o aparelho em foco; renova a cada 5 s enquanto houver foco (expira em 15 s no servidor). */
  setFocus(instanceId: string | null): void {
    const changed = this.focusId !== instanceId;
    this.focusId = instanceId;
    if (changed) this.sendFocusNow();
    this.armFocusTimer();
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
        if (ws.readyState === WebSocket.OPEN && this.focusId) {
          ws.send(JSON.stringify({ type: 'focus', instance_id: null } satisfies ClientMessage));
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

  private sendFocusNow(): void {
    this.send({ type: 'focus', instance_id: this.focusId });
  }

  private armFocusTimer(): void {
    if (this.focusTimer) clearInterval(this.focusTimer);
    this.focusTimer = null;
    if (this.focusId && !this.closed) {
      this.focusTimer = setInterval(() => this.sendFocusNow(), FOCUS_RENEW_MS);
    }
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
    if (this.focusTimer) clearInterval(this.focusTimer);
    if (this.helloTimer) clearTimeout(this.helloTimer);
    this.clearPongTimer();
    this.pingTimer = this.focusTimer = null;
    this.helloTimer = null;
  }

  private fail(reason: string, code?: number): void {
    if (this.closed) return;
    this.close();
    this.handlers.onClose(reason, code);
  }
}
