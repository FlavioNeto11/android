// @vitest-environment jsdom
import { act } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { WATCH_TTL_S } from '../api/ws';
import { makeSnapshot } from '../test/fixtures';
import { FakeBackend, FakeWebSocket, flush, installBrowserStubs, json, waitFor } from '../test/harness';
import { WATCH_COALESCE_MS, startLive, stopLive } from './live';
import { usePreviewStore } from './preview';
import { useUiStore } from './ui';

/**
 * Controlador ao vivo × interesse em prévia (contrato C2): o que ele manda no `watch` quando a grade rola, o foco
 * abre, a aba some e a conexão cai. O socket em si (renovação, deduplicação) é testado em `api/ws.test.ts`.
 */

const backend = new FakeBackend();
let visibility: DocumentVisibilityState = 'visible';

type Watch = { type: 'watch'; grid: string[]; focus: string | null; ttl_s: number };

function watches(ws: FakeWebSocket = FakeWebSocket.last): Watch[] {
  return ws.sent.filter((m): m is Watch => (m as { type?: string }).type === 'watch');
}

function lastWatch(ws: FakeWebSocket = FakeWebSocket.last): Watch | undefined {
  return watches(ws).at(-1);
}

async function setTabVisibility(state: DocumentVisibilityState): Promise<void> {
  visibility = state;
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
}

async function setVisible(id: string, on: boolean): Promise<void> {
  await act(async () => {
    usePreviewStore.getState().setVisible(id, on);
  });
}

async function openSocket(): Promise<FakeWebSocket> {
  const ws = FakeWebSocket.last;
  await act(async () => {
    ws.serverOpen();
    ws.serverSend({ type: 'hello', server_time: new Date().toISOString(), last_event_id: 0 });
  });
  return ws;
}

beforeAll(async () => {
  installBrowserStubs();
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  // Sem execução ativa no snapshot: o teste é só sobre a conexão, não sobre o detalhe de execução.
  backend.on('GET', /^\/api\/snapshot$/, () => json(makeSnapshot({ runs: [], last_event_id: 0 })));
  backend.install();
  usePreviewStore.setState({ visible: {} });
  useUiStore.setState({ focusInstanceId: null, selectedRunId: null });
  startLive();
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  await openSocket();
});

afterEach(async () => {
  await setTabVisibility('visible');
});

afterAll(() => {
  stopLive();
  delete (document as { visibilityState?: unknown }).visibilityState;
});

describe('live — watch da prévia', () => {
  it('manda os cartões visíveis, juntando a rajada da rolagem numa mensagem só', async () => {
    const ws = FakeWebSocket.last;
    expect(lastWatch(ws)).toEqual({ type: 'watch', grid: [], focus: null, ttl_s: WATCH_TTL_S });
    const antes = watches(ws).length;

    await setVisible('android-03', true);
    await setVisible('android-01', true);
    await setVisible('android-02', true);
    await flush(WATCH_COALESCE_MS + 50);

    expect(watches(ws)).toHaveLength(antes + 1);
    expect(lastWatch(ws)).toEqual({ type: 'watch', grid: ['android-01', 'android-02', 'android-03'], focus: null, ttl_s: WATCH_TTL_S });
  });

  it('cartão que sai da viewport sai do conjunto', async () => {
    await setVisible('android-03', false);
    await waitFor(() => expect(lastWatch()?.grid).toEqual(['android-01', 'android-02']));
  });

  it('abrir e fechar o foco vai na hora, junto com a grade', async () => {
    const ws = FakeWebSocket.last;
    await act(async () => useUiStore.getState().openFocus('android-02'));
    expect(lastWatch(ws)).toEqual({ type: 'watch', grid: ['android-01', 'android-02'], focus: 'android-02', ttl_s: WATCH_TTL_S });
    await act(async () => useUiStore.getState().closeFocus());
    expect(lastWatch(ws)).toEqual({ type: 'watch', grid: ['android-01', 'android-02'], focus: null, ttl_s: WATCH_TTL_S });
    await act(async () => useUiStore.getState().openFocus('android-01'));
  });

  it('aba oculta manda o watch vazio; visível de novo, reenvia o conjunto (com o foco guardado)', async () => {
    const ws = FakeWebSocket.last;
    await setTabVisibility('hidden');
    expect(lastWatch(ws)).toEqual({ type: 'watch', grid: [], focus: null, ttl_s: WATCH_TTL_S });

    // oculta, a grade pode mudar (layout), mas nada muda para o servidor: a aba não olha nada
    const antes = watches(ws).length;
    await setVisible('android-04', true);
    await flush(WATCH_COALESCE_MS + 50);
    expect(watches(ws)).toHaveLength(antes);

    await setTabVisibility('visible');
    expect(lastWatch(ws)).toEqual({
      type: 'watch', grid: ['android-01', 'android-02', 'android-04'], focus: 'android-01', ttl_s: WATCH_TTL_S,
    });
  });

  it('a conexão caiu: o socket novo reenvia o conjunto atual assim que abre', async () => {
    const velho = FakeWebSocket.last;
    await act(async () => velho.serverClose(1006, 'queda'));
    // backoff de ~1 s (±30 %) até o novo ciclo snapshot → WebSocket
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2), 4000);
    const novo = await openSocket();
    expect(novo).not.toBe(velho);
    expect(watches(novo)[0]).toEqual({
      type: 'watch', grid: ['android-01', 'android-02', 'android-04'], focus: 'android-01', ttl_s: WATCH_TTL_S,
    });
    expect(novo.sent.some((m) => (m as { type?: string }).type === 'focus')).toBe(false);
  });
});
