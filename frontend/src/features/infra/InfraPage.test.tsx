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

// ---------------------------------------------------------------- aparelho novo sem editar YAML (item 4.5)
describe('adotar aparelho anunciado', () => {
  it('o aparelho que o worker anuncia e não é instância aparece com botão de adotar', async () => {
    // Antes, o inventário do `hello` era descartado: acrescentar aparelho era editar config.yaml, reinstalar a
    // tarefa do túnel e reiniciar o backend. Aqui é um clique, e a resposta diz o mapa do túnel novo.
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          devices: [{ serial: 'emulator-5560', avd_name: 'worker-07', state: 'online', adb_port: 5561,
                      instance_id: null }],
        }),
      },
    });
    backend.on('POST', /\/api\/workers\/worker-lan-01\/devices\/adopt$/, () => json({
      instance: makeInstance(16, { id: 'android-16' }),
      tunnel_map: '15555:5555,15556:5561', tunnel_map_file: 'C:/git/android/data/tunnel/worker-lan-01.map',
    }, 201));
    await render();
    expect(text()).toContain('emulator-5560');
    expect(text()).toContain('adb 5561');

    await click(byRole('button', /^Adotar$/i));
    await waitFor(() => backend.callsTo('POST', /devices\/adopt$/).length === 1);
    const chamadas = backend.callsTo('POST', /devices\/adopt$/);
    expect(chamadas[0]?.body).toEqual({ serial: 'emulator-5560' });
  });

  it('aparelho anunciado sem porta de ADB não pode ser adotado, e a tela diz por quê', async () => {
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          devices: [{ serial: 'emulator-5560', avd_name: null, state: 'online', adb_port: null,
                      instance_id: null }],
        }),
      },
    });
    await render();
    expect(text()).toContain('sem porta de ADB declarada');
    // O motivo entra no NOME acessível do botão, por isso ele não bate mais com /^Adotar$/.
    expect(byRole('button', /Adotar.*indisponível/i).getAttribute('aria-disabled')).toBe('true');
    expect(backend.callsTo('POST', /devices\/adopt$/)).toHaveLength(0);
  });

  it('inventário divergente aparece no aparelho do servidor', async () => {
    const inst = makeInstance(3, {
      worker_id: 'worker-lan-01', kind: 'external', inventory_state: 'divergent',
      inventory_detail: 'o worker declara a porta de ADB 5557 para android-03 e o túnel encaminha para 5555',
    });
    useAppStore.setState({ instances: { [inst.id]: inst }, instanceOrder: [inst.id] });
    await render();
    expect(text()).toContain('inventário divergente');
  });
});

// Item 10.1 / achado #137: a cópia do agente na máquina do worker não é checkout, e as duas pontas diziam
// `0.1.0` — nada no painel dizia que aquela máquina roda código velho. E item 10.4 / achado #180: sem KVM o
// emulador não sobe em tempo útil, e isso só aparecia como comando estourando prazo do outro lado da rede.
describe('o que o cartão do worker passou a denunciar', () => {
  it('mostra "agente defasado" com a versão que o central espera', async () => {
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          agent_version: '0.1.0+abc1234', expected_agent_version: '0.1.0+def5678', agent_outdated: true,
        }),
      },
    });
    await render();
    expect(text()).toContain('agente defasado');
    const etiqueta = [...document.querySelectorAll('[title]')]
      .find((e) => e.textContent?.includes('agente defasado'));
    expect(etiqueta?.getAttribute('title')).toContain('0.1.0+def5678');
  });

  it('não mostra nada quando o agente está na mesma versão do central', async () => {
    useAppStore.setState({
      workers: {
        'worker-lan-01': worker({
          agent_version: '0.1.0+abc1234', expected_agent_version: '0.1.0+abc1234', agent_outdated: false,
        }),
      },
    });
    await render();
    expect(text()).not.toContain('agente defasado');
  });

  it('denuncia o KVM sem permissão do worker Linux', async () => {
    useAppStore.setState({
      workers: { 'worker-lan-01': worker({ os: 'linux', accel: 'kvm-inacessivel' }) },
    });
    await render();
    expect(text()).toContain('KVM sem permissão');
  });

  it('não denuncia nada quando o KVM está utilizável', async () => {
    useAppStore.setState({ workers: { 'worker-lan-01': worker({ os: 'linux', accel: 'kvm' }) } });
    await render();
    expect(text()).not.toContain('sem KVM');
    expect(text()).not.toContain('KVM sem permissão');
  });
});
