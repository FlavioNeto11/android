// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { ServerLimits } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { ServersLimits } from './ServersLimits';

// Auditoria UX 27/09, P2.11/P3.4: "Carregando servidores…" em texto puro, erro sem motivo nem saída, falhas
// engolidas depois da primeira carga e releitura a cada 10 s mesmo com a aba em segundo plano.

const servidor: ServerLimits = {
  worker_id: 'worker-lan-01', name: 'Notebook da LAN', is_host: false, connected: true, maintenance: false,
  declared: { max_slots: 6, boot_parallelism: 1, max_working: null, min_free_ram_mb: 4096 },
  decided: { max_slots: null, boot_parallelism: null, max_working: null, min_free_ram_mb: null },
  effective: { max_slots: 6, boot_parallelism: 1, max_working: null, min_free_ram_mb: 4096 },
  locked: {}, online: 6, working: 1, devices: 6, cpu_percent: 23, cpu_count: 12, ram_free_mb: 38_000, ram_total_mb: 64_000,
};

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;
let visibility: DocumentVisibilityState = 'visible';

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete (document as { visibilityState?: unknown }).visibilityState;
});

async function render(): Promise<void> {
  await act(async () => { root.render(<ServersLimits />); });
}

/** Simula sair e voltar para a aba: a releitura pausada retoma e dispara na hora. */
async function sairEVoltar(): Promise<void> {
  await act(async () => {
    visibility = 'hidden';
    document.dispatchEvent(new Event('visibilitychange'));
    visibility = 'visible';
    document.dispatchEvent(new Event('visibilitychange'));
  });
}

describe('Limites → Por servidor', () => {
  it('API caída na primeira carga mostra o motivo e "Tentar de novo"; ao voltar, os cartões aparecem', async () => {
    backend.on('GET', /servers\/limits/, () => apiError(503, 'unavailable', 'agente fora do ar'));
    await render();
    await waitFor(() => expect(text()).toContain('Não foi possível carregar os servidores'));
    expect(text()).toContain('agente fora do ar');

    backend.on('GET', /servers\/limits/, () => json([servidor]));
    await click(byRole('button', /Tentar de novo/));
    await waitFor(() => expect(text()).toContain('Notebook da LAN'));
    expect(text()).not.toContain('Não foi possível carregar');
  });

  it('releitura que falha com dado na tela vira faixa "Mostrando a última leitura", não silêncio', async () => {
    backend.on('GET', /servers\/limits/, () => json([servidor]));
    await render();
    await waitFor(() => expect(text()).toContain('Notebook da LAN'));

    backend.on('GET', /servers\/limits/, () => apiError(500, 'internal', 'consulta estourou o tempo'));
    await sairEVoltar();
    await waitFor(() => expect(text()).toContain('Mostrando a última leitura'));
    expect(text()).toContain('consulta estourou o tempo');
    expect(text()).toContain('Notebook da LAN'); // o cartão continua: o dado é velho, não inexistente

    backend.on('GET', /servers\/limits/, () => json([servidor]));
    await click(byRole('button', /Tentar de novo/));
    await waitFor(() => expect(text()).not.toContain('Mostrando a última leitura'));
  });

  it('com a aba oculta não relê; ao voltar relê na hora', async () => {
    backend.on('GET', /servers\/limits/, () => json([servidor]));
    await render();
    await waitFor(() => expect(text()).toContain('Notebook da LAN'));
    const antes = backend.callsTo('GET', /servers\/limits/).length;

    await act(async () => {
      visibility = 'hidden';
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(backend.callsTo('GET', /servers\/limits/)).toHaveLength(antes);

    await act(async () => {
      visibility = 'visible';
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(backend.callsTo('GET', /servers\/limits/)).toHaveLength(antes + 1));
  });
});
