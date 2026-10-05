// @vitest-environment jsdom
import { act } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { WATCH_RENEW_MS, WATCH_TTL_S } from '../api/ws';
import { makeEvent, makeRunDetail, makeSnapshot } from '../test/fixtures';
import { FakeBackend, FakeWebSocket, flush, installBrowserStubs, json, waitFor } from '../test/harness';
import { useControlStore } from './control';
import { WATCH_COALESCE_MS, startLive, stopLive, summaryOf } from './live';
import { usePreviewStore } from './preview';
import { useToastStore } from './toasts';
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

describe('live — aba oculta com o controle manual (lease) do aparelho em foco', () => {
  const vazio = { type: 'watch', grid: [], focus: null, ttl_s: WATCH_TTL_S };

  afterEach(() => {
    useControlStore.setState({ leases: {} });
    vi.useRealTimers();
  });

  it('sem controle manual: oculta continua mandando o watch vazio', async () => {
    useControlStore.setState({ leases: {} });
    expect(lastWatch()?.focus).toBe('android-01'); // o foco está aberto
    await setTabVisibility('hidden');
    expect(lastWatch()).toEqual(vazio);
  });

  it('com o controle: oculta mantém o foco (grade vazia) e o renova — é o que segura o lease no servidor', async () => {
    // Só o setInterval (renovação e ping) no relógio falso, e num socket novo, criado já sob ele; fetch, backoff e
    // o `waitFor` seguem no relógio real.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'], shouldClearNativeTimers: true });
    const velho = FakeWebSocket.last;
    await act(async () => velho.serverClose(1006, 'queda'));
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(3), 4000);
    const ws = await openSocket();

    // visível: tomar o controle não muda o que a aba olha (o foco já vai no watch)
    const antes = watches(ws).length;
    await act(async () => {
      useControlStore.setState({ leases: { 'android-01': { leaseId: 'lease-1', status: 'granted', acquiredAt: Date.now() } } });
    });
    expect(watches(ws)).toHaveLength(antes);

    await setTabVisibility('hidden');
    const comFoco = { type: 'watch', grid: [], focus: 'android-01', ttl_s: WATCH_TTL_S };
    expect(lastWatch(ws)).toEqual(comFoco);

    const n = watches(ws).length;
    vi.advanceTimersByTime(WATCH_RENEW_MS);
    expect(watches(ws)).toHaveLength(n + 1);
    expect(lastWatch(ws)).toEqual(comFoco);
    vi.advanceTimersByTime(WATCH_RENEW_MS);
    expect(watches(ws)).toHaveLength(n + 2);
    expect(lastWatch(ws)).toEqual(comFoco);

    // soltou o controle com a aba ainda oculta: volta ao vazio na hora
    await act(async () => useControlStore.setState({ leases: {} }));
    expect(lastWatch(ws)).toEqual(vazio);
  });
});

describe('live — resumo que volta à lista quando o detalhe chega (30.38)', () => {
  it('mantém a origem, a prova de fluxo, o pedido e os apps: abrir a execução não apaga o selo da linha', () => {
    const detalhe = makeRunDetail({
      origem: 'validacao_qa', origem_ref: 'lv-1', prova_fluxo_id: null, pedido_id: 'p-1', ocorrencia_id: null,
      app_ids: ['com.qa.messenger'],
    });
    const resumo = summaryOf(detalhe);
    expect(resumo).toMatchObject({
      id: detalhe.id, origem: 'validacao_qa', origem_ref: 'lv-1', prova_fluxo_id: null, pedido_id: 'p-1',
      app_ids: ['com.qa.messenger'],
    });
    // o resumo não carrega o detalhe inteiro para a lista
    expect(resumo).not.toHaveProperty('steps');
    expect(resumo).not.toHaveProperty('plan');
  });

  it('backend anterior sem os campos opcionais: o resumo não inventa chaves', () => {
    const resumo = summaryOf(makeRunDetail());
    expect(resumo).not.toHaveProperty('origem');
    expect(resumo).not.toHaveProperty('origem_ref');
  });
});

// 29.143: na tomada o controle segue `user` (de outra pessoa); só o `tomado_por` do evento diz que o lease desta aba caiu.
describe('live — tomada do controle por outra pessoa (29.143)', () => {
  afterEach(() => useControlStore.setState({ leases: {} }));

  it('o control.changed com tomado_por derruba o lease desta aba e avisa quem tomou', async () => {
    useControlStore.setState({ leases: { 'android-01': { leaseId: 'lease-1', status: 'granted', acquiredAt: Date.now() - 60_000 } } });
    const dados = { instance_id: 'android-01', control: 'user', pending: false, tomado_por: 'Operadora B', tomado_de: 'Operador A' };
    await act(async () => FakeWebSocket.last.serverSend({ type: 'event', event: makeEvent(9001, 'control.changed', dados, { instance_id: 'android-01' }) }));
    await waitFor(() => expect(useControlStore.getState().leases['android-01']).toBeUndefined());
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain('Operadora B tomou o controle de android-01');
  });

  it('control.changed sem tomado_por (a troca de sempre) não derruba o lease concedido', async () => {
    useControlStore.setState({ leases: { 'android-01': { leaseId: 'lease-1', status: 'granted', acquiredAt: Date.now() - 60_000 } } });
    const dados = { instance_id: 'android-01', control: 'user', pending: false };
    await act(async () => FakeWebSocket.last.serverSend({ type: 'event', event: makeEvent(9002, 'control.changed', dados, { instance_id: 'android-01' }) }));
    await flush(20);
    expect(useControlStore.getState().leases['android-01']?.leaseId).toBe('lease-1');
  });
});
