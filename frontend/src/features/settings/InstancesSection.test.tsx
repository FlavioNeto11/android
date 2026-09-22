// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance, Worker } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { InstancesSection } from './InstancesSection';

// Achado #64: amarrar/desamarrar aparelho a servidor só existia por chamada manual à API, e o aviso de órfão
// mandava o usuário a um controle que não estava aqui. A coluna "Servidor" é esse controle.

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z', last_seen_at: '2026-09-22T10:00:00Z',
    ...over,
  };
}

const CENTRAL = worker({ id: 'central', name: 'Este servidor', local: true });

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

async function render(instances: Instance[], workers: Worker[]): Promise<HTMLElement> {
  useAppStore.setState({
    ...initialDataState,
    instances: Object.fromEntries(instances.map((i) => [i.id, i])),
    instanceOrder: instances.map((i) => i.id),
    workers: Object.fromEntries(workers.map((w) => [w.id, w])),
  });
  await act(async () => {
    root.render(<InstancesSection />);
  });
  return container;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('InstancesSection — coluna Servidor', () => {
  it('lista os workers inscritos e marca o central quando o aparelho não tem worker', async () => {
    const el = await render([makeInstance(1, { worker_id: null })], [CENTRAL, worker()]);
    const select = byRole('combobox', /Servidor de android-01/, el) as HTMLSelectElement;
    expect(select.value).toBe('');
    expect([...select.options].map((o) => o.textContent)).toEqual(['Este servidor (central)', 'Notebook da LAN']);
  });

  it('o worker local conta como central (o aparelho não aparece amarrado a um worker remoto)', async () => {
    const el = await render([makeInstance(1, { worker_id: 'central' })], [CENTRAL, worker()]);
    const select = byRole('combobox', /Servidor de android-01/, el) as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('amarrar a um worker manda worker_id no PUT e o aparelho passa a ser daquele servidor', async () => {
    backend.on('PUT', /^\/api\/instances\/android-01$/, (c) =>
      json({ ...makeInstance(1), ...(c.body as object), worker_id: 'worker-lan-01' }));
    const el = await render([makeInstance(1, { worker_id: null })], [CENTRAL, worker()]);
    await setValue(byRole('combobox', /Servidor de android-01/, el) as HTMLSelectElement, 'worker-lan-01');
    await act(async () => { await click(byRole('button', /^Salvar$/, el)); });
    await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)[0]!.body).toEqual({ worker_id: 'worker-lan-01' });
    await waitFor(() => expect(useAppStore.getState().instances['android-01']!.worker_id).toBe('worker-lan-01'));
  });

  it('voltar para o central manda worker_id null — é o "desamarre o aparelho" do aviso de órfão', async () => {
    backend.on('PUT', /^\/api\/instances\/android-01$/, () => json(makeInstance(1, { worker_id: null })));
    const el = await render([makeInstance(1, { worker_id: 'worker-lan-01' })], [CENTRAL, worker()]);
    await setValue(byRole('combobox', /Servidor de android-01/, el) as HTMLSelectElement, '');
    await act(async () => { await click(byRole('button', /^Salvar$/, el)); });
    await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)).toHaveLength(1));
    expect(backend.callsTo('PUT', /^\/api\/instances\/android-01$/)[0]!.body).toEqual({ worker_id: null });
  });

  it('servidor que saiu da lista aparece como "não inscrito" em vez de virar central sem aviso', async () => {
    const el = await render([makeInstance(1, { worker_id: 'worker-sumido' })], [CENTRAL]);
    const select = byRole('combobox', /Servidor de android-01/, el) as HTMLSelectElement;
    expect(select.value).toBe('worker-sumido');
    expect(text(el)).toContain('worker-sumido (não inscrito)');
  });
});
