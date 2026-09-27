// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { Worker } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeInstance } from '../../test/fixtures';
import { FakeBackend, byRole, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { RunsPage } from './RunsPage';

// Auditoria UX 27/09 (P3.6): os filtros da lista eram <select> nativos e o servidor aparecia como worker_id cru.

const lan: Worker = {
  id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
  state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
  resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z', last_seen_at: '2026-09-22T10:00:00Z',
};

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/runs$/, () => json({ runs: [], total: 0, limit: 50, offset: 0 }));
  const instances = [makeInstance(1, { worker_id: null }), makeInstance(2, { worker_id: 'worker-lan-01' })];
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    instances: Object.fromEntries(instances.map((i) => [i.id, i])),
    instanceOrder: instances.map((i) => i.id),
    workers: { [lan.id]: lan },
    runs: [],
  });
  useUiStore.setState({ selectedRunId: null });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it('o filtro por servidor mostra o NOME do worker, não o worker_id cru, e os filtros são o Select compartilhado', async () => {
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(text()).toContain('Nenhuma execução ainda'));

  const servidor = byRole('combobox', /Filtrar por servidor/) as HTMLSelectElement;
  const rotulos = [...servidor.options].map((o) => o.textContent);
  expect(rotulos).toEqual(['Todos os servidores', 'Notebook da LAN']);
  // O VALOR continua sendo o id: é o que a API filtra.
  expect([...servidor.options].map((o) => o.value)).toEqual(['', 'worker-lan-01']);

  const aparelho = byRole('combobox', /Filtrar por aparelho/) as HTMLSelectElement;
  expect([...aparelho.options].map((o) => o.value)).toEqual(['', 'android-01', 'android-02']);
  // Ambos vêm de components/Field (classe do módulo compartilhado), não de <select> cru.
  expect(servidor.className).toMatch(/select/);
  expect(aparelho.className).toMatch(/select/);
});
