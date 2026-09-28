// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { ServerLimits } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
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

  // v0.26: `max_devices` é o teto de aparelhos EXISTENTES que o `POST /api/instances` confere. Nenhuma máquina o
  // declara: é só decisão do dono, e vazio quer dizer sem teto.
  it('"Teto de aparelhos" vai no PUT como max_devices; vazio volta a sem teto (null)', async () => {
    const central: ServerLimits = {
      ...servidor, worker_id: 'central', name: 'Este servidor', is_host: true,
      declared: { ...servidor.declared, max_devices: null },
      decided: { ...servidor.decided, max_devices: null },
      effective: { ...servidor.effective, max_devices: null },
    };
    backend.on('GET', /servers\/limits/, () => json([central]));
    backend.on('PUT', /servers\/central\/limits$/, (c) => {
      const body = c.body as Record<string, number | null>;
      return json({ ...central, decided: { ...central.decided, ...body }, effective: { ...central.effective, ...body } });
    });
    await render();
    await waitFor(() => expect(text()).toContain('Teto de aparelhos'));
    expect(text()).toContain('conferido ao criar aparelho');
    const teto = byRole('textbox', 'Teto de aparelhos') as HTMLInputElement;
    expect(teto.placeholder).toBe('sem teto');

    await setValue(teto, '300');
    await click(byRole('button', /^Salvar/));
    await waitFor(() => expect(text()).toContain('Entre 1 e 256.'));
    expect(backend.callsTo('PUT', /limits$/)).toHaveLength(0);

    await setValue(teto, '8');
    await click(byRole('button', /^Salvar/));
    await waitFor(() => expect(backend.callsTo('PUT', /servers\/central\/limits$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /limits$/)[0]?.body).toEqual({ max_devices: 8 });

    // Decidido 8: apagar o campo desfaz a decisão (sem teto), mandando `null`.
    await waitFor(() => expect((byRole('textbox', 'Teto de aparelhos') as HTMLInputElement).value).toBe('8'));
    await setValue(byRole('textbox', 'Teto de aparelhos') as HTMLInputElement, '');
    await click(byRole('button', /^Salvar/));
    await waitFor(() => expect(backend.callsTo('PUT', /limits$/)).toHaveLength(2));
    expect(backend.callsTo('PUT', /limits$/)[1]?.body).toEqual({ max_devices: null });
  });

  it('backend sem max_devices (anterior ao v0.26): o campo aparece vazio e nada vai no PUT sem mexer nele', async () => {
    backend.on('GET', /servers\/limits/, () => json([servidor]));
    backend.on('PUT', /servers\/worker-lan-01\/limits$/, () => json(servidor));
    await render();
    await waitFor(() => expect(text()).toContain('Teto de aparelhos'));
    expect((byRole('textbox', 'Teto de aparelhos') as HTMLInputElement).value).toBe('');
    expect(text()).not.toContain('undefined');
    // Worker remoto: o teto só é conferido ao criar aparelho, que por ora só existe no hospedeiro.
    expect(text()).toContain('Por enquanto só vale para este servidor');
    expect(() => byRole('button', /Da máquina/)).toThrow();
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
