// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeWebSocket, installBrowserStubs } from '../test/harness';
import { LiveSocket, WATCH_RENEW_MS, WATCH_TTL_S } from './ws';

/** Interesse em prévia (contrato C2, adendo v0.20): o que o socket diz ao servidor e quando. */

const noopHandlers = { onHello: () => undefined, onEvent: () => undefined, onResync: () => undefined, onClose: () => undefined };

/** Abre como o servidor faz: sem o `hello`, o vigia de 10 s derruba o socket no meio do teste de relógio. */
function open(ws: FakeWebSocket): void {
  ws.serverOpen();
  ws.serverSend({ type: 'hello', server_time: new Date().toISOString(), last_event_id: 0 });
}

function watches(ws: FakeWebSocket): unknown[] {
  return ws.sent.filter((m) => (m as { type?: string }).type === 'watch');
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  FakeWebSocket.instances = [];
});

afterEach(() => {
  vi.useRealTimers();
});

describe('LiveSocket — watch', () => {
  it('ao abrir, manda o conjunto guardado — inclusive o que mudou enquanto conectava', () => {
    const socket = new LiveSocket(0, noopHandlers, { grid: ['android-01'], focus: null });
    const ws = FakeWebSocket.last;
    // ainda conectando: nada sai, mas o conjunto novo fica guardado
    socket.setWatch({ grid: ['android-01', 'android-02'], focus: 'android-02' });
    expect(ws.sent).toEqual([]);

    ws.serverOpen();
    expect(ws.sent).toEqual([{ type: 'watch', grid: ['android-01', 'android-02'], focus: 'android-02', ttl_s: WATCH_TTL_S }]);
    socket.close();
  });

  it('só reenvia quando o conjunto muda (a ordem da grade não importa)', () => {
    const socket = new LiveSocket(0, noopHandlers, { grid: ['android-01', 'android-02'], focus: null });
    const ws = FakeWebSocket.last;
    ws.serverOpen();
    expect(watches(ws)).toHaveLength(1);

    socket.setWatch({ grid: ['android-02', 'android-01'], focus: null });
    expect(watches(ws)).toHaveLength(1);

    socket.setWatch({ grid: ['android-02'], focus: null });
    socket.setWatch({ grid: ['android-02'], focus: 'android-02' });
    expect(watches(ws)).toEqual([
      { type: 'watch', grid: ['android-01', 'android-02'], focus: null, ttl_s: WATCH_TTL_S },
      { type: 'watch', grid: ['android-02'], focus: null, ttl_s: WATCH_TTL_S },
      { type: 'watch', grid: ['android-02'], focus: 'android-02', ttl_s: WATCH_TTL_S },
    ]);
    // a mensagem legada `focus` saiu: o `watch` já diz o foco
    expect(ws.sent.some((m) => (m as { type?: string }).type === 'focus')).toBe(false);
    socket.close();
  });

  it('renova o mesmo conjunto antes de vencer, inclusive o vazio', () => {
    vi.useFakeTimers();
    // A renovação precisa caber folgada no TTL: duas perdidas ainda não derrubam a prévia.
    expect(WATCH_RENEW_MS * 2).toBeLessThan(WATCH_TTL_S * 1000);

    const socket = new LiveSocket(0, noopHandlers, { grid: ['android-03'], focus: 'android-03' });
    const ws = FakeWebSocket.last;
    open(ws);
    expect(watches(ws)).toHaveLength(1);

    vi.advanceTimersByTime(WATCH_RENEW_MS - 1);
    expect(watches(ws)).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(watches(ws)).toHaveLength(2);
    expect(watches(ws)[1]).toEqual({ type: 'watch', grid: ['android-03'], focus: 'android-03', ttl_s: WATCH_TTL_S });

    // aba oculta: o vazio também é renovado — deixá-lo vencer entregaria o significado ao servidor
    socket.setWatch({ grid: [], focus: null });
    expect(watches(ws)).toHaveLength(3);
    vi.advanceTimersByTime(WATCH_RENEW_MS);
    expect(watches(ws)).toHaveLength(4);
    expect(watches(ws)[3]).toEqual({ type: 'watch', grid: [], focus: null, ttl_s: WATCH_TTL_S });
    socket.close();
  });

  it('fechado, para de renovar; ao fechar, avisa que não olha mais nada', () => {
    vi.useFakeTimers();
    const socket = new LiveSocket(0, noopHandlers, { grid: ['android-01'], focus: null });
    const ws = FakeWebSocket.last;
    open(ws);
    socket.close();
    expect(ws.sent[ws.sent.length - 1]).toEqual({ type: 'watch', grid: [], focus: null, ttl_s: WATCH_TTL_S });
    const count = ws.sent.length;
    vi.advanceTimersByTime(WATCH_RENEW_MS * 3);
    expect(ws.sent).toHaveLength(count);
  });

  it('sem interesse anterior, fechar não manda nada além do que já foi', () => {
    const socket = new LiveSocket(0, noopHandlers, null);
    const ws = FakeWebSocket.last;
    ws.serverOpen();
    expect(ws.sent).toEqual([{ type: 'watch', grid: [], focus: null, ttl_s: WATCH_TTL_S }]);
    socket.close();
    expect(ws.sent).toHaveLength(1);
  });
});
