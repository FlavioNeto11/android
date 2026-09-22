// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Worker } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance, makeRun, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { InfraPage } from './InfraPage';

// Achados #168/#150: "remova o worker no painel" precisa ser um botão de verdade, não só uma frase na doc.

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: 'worker-lan-01', name: 'Notebook da LAN', os: 'windows', os_version: '11', agent_version: '0.1.0',
    appium_mode: 'local', appium_url: 'http://127.0.0.1:4723', max_slots: 6, verbs: ['stop', 'hibernate'],
    state: 'online', observed_state: 'online', maintenance: false, state_detail: null, connected: true,
    local: false,
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

// Achado #179: a queda do túnel SSH aparecia só como sintomas espalhados (aparelhos "sem ADB", worker "sem
// batida"), sem nada apontando a causa. O cartão do worker agora mostra a linha "Túnel" com o motivo.
it('mostra o túnel fora com o motivo quando transport_state é down', async () => {
  useAppStore.setState({
    workers: {
      'worker-lan-01': worker({
        transport_state: 'down',
        transport_detail: 'porta(s) local(is) do túnel recusando conexão: 127.0.0.1:15555',
        transport_since: '2026-09-22T09:00:00Z',
      }),
    },
  });
  await render();
  expect(text()).toContain('Túnel: fora');
  expect(text()).toContain('porta(s) local(is) do túnel recusando conexão');
});

it('não mostra a linha do túnel quando nunca foi sondado', async () => {
  await render();                                    // worker() default: transport_state ausente
  expect(text()).not.toContain('Túnel:');
});

// Achado #63: a tela era honesta sobre o worker e muda sobre si mesma — selo fixo "online", sem disco, sem
// capacidades, sem logs/evidências/fila/perfis por servidor.
describe('InfraPage — o central e as abas por servidor', () => {
  const evento = (kind: string, instance_id: string, message: string) => ({
    id: 1, ts: '2026-09-22T10:00:00Z', kind, level: 'info' as const, run_id: null, instance_id,
    objective_id: null, step_id: null, attempt_id: null, message, data: null,
  });

  async function comEstado(over: Record<string, unknown>): Promise<void> {
    useAppStore.setState({
      ...initialDataState, hydrated: true, hydrateCount: 1,
      workers: { 'worker-lan-01': worker({ devices: [
        { serial: 'emulator-5554', state: 'running', instance_id: 'android-13' },
      ] }) },
      instances: { 'android-13': makeInstance(13, { worker_id: 'worker-lan-01', state: 'stopped' }) },
      instanceOrder: ['android-13'],
      ...over,
    });
    await render();
  }

  it('o selo do central sai da saúde declarada, não de um "online" fixo', async () => {
    const health = { ...makeSnapshot().health, status: 'degraded' as const };
    await comEstado({ health, conn: { status: 'connected', attempt: 0, nextRetryAt: null, lastError: null,
                                      lastConnectedAt: Date.now() } });
    expect(text()).toContain('degradado');
  });

  it('as capacidades do worker aparecem (verbos e de quem é o Appium)', async () => {
    await comEstado({});
    expect(text()).toContain('verbos: stop, hibernate');
    expect(text()).toContain('Appium local');
  });

  it('a aba Logs mostra os eventos DOS APARELHOS daquele servidor, e não os dos outros', async () => {
    await comEstado({ recentEvents: [evento('log', 'android-13', 'reiniciei o system_server'),
                                     evento('log', 'android-01', 'coisa do central')] });
    await click(byRole('tab', /^Logs/, byRole('tablist', /Detalhes de worker-lan-01/)));
    expect(text()).toContain('reiniciei o system_server');
    expect(text()).not.toContain('coisa do central');
  });

  it('a aba Fila mostra a execução em voo daquele servidor', async () => {
    await comEstado({ runs: [makeRun({ instance_ids: ['android-13'], status: 'running' })] });
    await click(byRole('tab', /^Fila/, byRole('tablist', /Detalhes de worker-lan-01/)));
    expect(text()).toContain('r-0001');
  });
});
