// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { Worker } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { InfraPage } from './InfraPage';

// Achados #168/#150: "remova o worker no painel" precisa ser um botão de verdade, não só uma frase na doc.

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', os: 'windows', os_version: '11', agent_version: '0.1.0',
    appium_mode: 'local', appium_url: 'http://127.0.0.1:4723', max_slots: 6, verbs: ['stop', 'hibernate'],
    state: 'online', observed_state: 'online', maintenance: false, state_detail: null, connected: true,
    resources: { cpu_percent: 10, cpu_count: 12, ram_total_mb: 65273, ram_free_mb: 46367, disk_free_gb: 400 },
    devices: [], enrolled_at: '2026-09-17T10:00:00Z', last_seen_at: '2026-09-22T10:00:00Z',
    ...over,
  };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    workers: { 'worker-lan-01': worker() },
    instances: {},
    instanceOrder: [],
  });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(): Promise<void> {
  await act(async () => {
    root.render(<><InfraPage /><ConfirmHost /></>);
  });
  await waitFor(() => text().includes('Notebook da LAN'));
}

function noDialogo(nome: RegExp): HTMLElement {
  return byRole('button', nome, byRole('dialog', /.+/));
}

it('remover pede confirmação e só chama a rota depois do sim', async () => {
  backend.on('DELETE', /workers\/worker-lan-01/, () => json({ ok: true, worker_id: 'worker-lan-01' }));
  await render();

  await click(byRole('button', /^Remover$/));
  await waitFor(() => text().includes('Remover "Notebook da LAN"?'));
  expect(backend.callsTo('DELETE', /workers/)).toHaveLength(0);            // nada saiu antes da confirmação

  await click(noDialogo(/Remover servidor/));
  await waitFor(() => backend.callsTo('DELETE', /workers/).length === 1);
  const chamada = backend.callsTo('DELETE', /workers/)[0]!;
  // Worker conectado: a confirmação vira force=true, senão a remoção seria recusada com 409.
  expect(chamada.body).toEqual({ force: true });
});

it('rotacionar credencial mostra o token novo uma única vez', async () => {
  backend.on('POST', /workers\/worker-lan-01\/rotate-credential/, () => json({ credential: 'novo-token-secreto-123' }));
  await render();

  await click(byRole('button', /Rotacionar credencial/));
  await waitFor(() => text().includes('Rotacionar credencial de'));
  await click(noDialogo(/^Rotacionar credencial$/));

  await waitFor(() => text().includes('novo-token-secreto-123'));
  expect(text()).toContain('Credencial rotacionada');
});
