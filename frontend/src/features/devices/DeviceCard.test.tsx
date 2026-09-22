// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance, makeRunDetail, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { DeviceCard } from './DeviceCard';

const noop = () => undefined;
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

async function renderCard(instance: Instance): Promise<HTMLElement> {
  await act(async () => {
    root.render(<DeviceCard instance={instance} appName="QA Messenger" selected={false} focused={false} onToggle={noop} onRange={noop} onOpen={noop} />);
  });
  return container;
}

function setHibernationFeature(enabled: boolean): void {
  const health = makeSnapshot().health;
  useAppStore.setState({ health: { ...health, features: { ...health.features, hibernation: enabled } } });
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.on('POST', /^\/api\/instances\/[^/]+\/actions\/[^/]+$/, () => json({ accepted: true }, 202));
  backend.install();
  useAppStore.setState({ ...initialDataState, settings: makeSnapshot().settings });
  setHibernationFeature(true);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('DeviceCard — hibernado', () => {
  it('mostra o estado, o espaço reservado explicativo e nenhuma miniatura', async () => {
    const el = await renderCard(makeInstance(6, { state: 'hibernated', state_detail: 'hibernado (snapshot salvo)' }));
    expect(el.querySelector('article')?.getAttribute('aria-label')).toBe('Instância android-06 — Hibernado');
    expect(text(el)).toContain('Hibernado — acorda em segundos, sem ocupar RAM');
    expect(text(el)).toContain('hibernado (snapshot salvo)');
    expect(el.querySelector('img')).toBeNull();
    // sem tela ao vivo não há "abrir pela miniatura" nem idade de frame
    expect(allByRole('button', /na visão de foco$/, el)).toHaveLength(0);
    expect(text(el)).not.toContain('Último frame');
  });

  it('a ação principal é "Acordar" e envia POST …/actions/wake', async () => {
    const el = await renderCard(makeInstance(6, { state: 'hibernated' }));
    expect(allByRole('button', 'Iniciar', el)).toHaveLength(0);
    await click(byRole('button', 'Acordar', el));
    await waitFor(() => expect(backend.callsTo('POST', /\/actions\//)).toHaveLength(1));
    const call = backend.callsTo('POST', /\/actions\//)[0];
    expect(call?.path).toBe('/api/instances/android-06/actions/wake');
    // Cada clique leva uma chave de idempotência: se a rede duplicar a requisição, o backend devolve o MESMO
    // comando em vez de acordar o aparelho duas vezes.
    const body = call?.body as { idempotency_key?: string } | undefined;
    expect(body?.idempotency_key).toMatch(/^android-06:wake:/);
    expect(Object.keys(body ?? {})).toEqual(['idempotency_key']);
  });

  it('parada continua com "Iniciar" (start), sem "Acordar"', async () => {
    const el = await renderCard(makeInstance(7, { state: 'stopped' }));
    expect(allByRole('button', 'Acordar', el)).toHaveLength(0);
    await click(byRole('button', 'Iniciar', el));
    await waitFor(() => expect(backend.callsTo('POST', /android-07\/actions\/start$/)).toHaveLength(1));
  });
});

describe('DeviceCard — Hibernar em aparelho online', () => {
  const online = () => makeInstance(1, {
    state: 'online',
    frame: { id: 'f1', ts: new Date().toISOString(), width: 1080, height: 2400, orientation: 'portrait', stale: false },
  });

  it('aparece só quando health.features.hibernation é true e envia …/actions/hibernate', async () => {
    const el = await renderCard(online());
    await click(byRole('button', 'Hibernar android-01', el));
    await waitFor(() => expect(backend.callsTo('POST', /android-01\/actions\/hibernate$/)).toHaveLength(1));
  });

  it('some quando a hibernação está desligada no backend', async () => {
    setHibernationFeature(false);
    const el = await renderCard(online());
    expect(allByRole('button', /^Hibernar/, el)).toHaveLength(0);
  });

  it('nunca aparece em aparelho que não está online', async () => {
    const el = await renderCard(makeInstance(7, { state: 'stopped' }));
    expect(allByRole('button', /^Hibernar/, el)).toHaveLength(0);
  });
});

describe('DeviceCard — rodízio', () => {
  it('mostra "aguardando vaga (k/K ligados)" na linha da tarefa do aparelho desligado', async () => {
    const base = makeRunDetail();
    const waiting = { ...base.objectives[0]!, instance_id: 'android-06', status: 'pending' as const, status_detail: 'aguardando vaga (3/3 ligados)' };
    useAppStore.setState({
      detail: { runId: base.id, status: 'ready', data: { ...base, objectives: [waiting] }, error: null, events: [], eventsStatus: 'ready' },
    });
    const el = await renderCard(makeInstance(6, { state: 'hibernated' }));
    expect(text(el)).toContain('aguardando vaga (3/3 ligados)');
    expect(text(el)).not.toContain('Sem tarefa em andamento');
  });

  it('sem objetivo esperando vaga, a linha continua "Sem tarefa em andamento"', async () => {
    const el = await renderCard(makeInstance(6, { state: 'hibernated' }));
    expect(text(el)).toContain('Sem tarefa em andamento');
  });
});

describe('DeviceCard — perfil do Instagram vinculado', () => {
  it('mostra a conta e o estado da sessão quando há perfil', async () => {
    const perfil = {
      id: 'ig-1', username: 'mariana.costa91182', display_name: null, first_name: null, last_name: null,
      birth_date: null, email: null, persona_id: null, persona_name: null, status: 'active',
      instance_id: 'android-06',
      credential: { configured: true, login_identifier: null, status: 'active', failed_attempts: 0,
                    blocked_until: null, updated_at: null, last_used_at: null },
      session: { status: 'session_ready' as const, instance_id: 'android-06', observed_username: 'mariana.costa91182',
                 verified_at: '2026-09-17T11:00:00Z', detail: null, stale: false },
      last_verified_at: null, last_activity_at: null,
      created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z',
    };
    await act(async () => {
      root.render(<DeviceCard instance={makeInstance(6, { state: 'online' })} appName="Instagram" profile={perfil}
                              selected={false} focused={false} onToggle={noop} onRange={noop} onOpen={noop} />);
    });
    expect(text(container)).toContain('@mariana.costa91182');
    expect(text(container)).toContain('Conectado');
  });

  it('aparelho sem perfil não ganha linha nenhuma a mais', async () => {
    const el = await renderCard(makeInstance(6, { state: 'online' }));
    expect(text(el)).not.toContain('@');
  });
});

describe('aparelho-loja na grade', () => {
  it('mostra a etiqueta Loja e NÃO oferece caixa de seleção: a loja nunca é alvo de comando', async () => {
    await renderCard(makeInstance(11, { kind: 'store', app_id: null, account_label: null }));
    expect(text()).toContain('Loja');
    expect(allByRole('checkbox', /Selecionar/i)).toHaveLength(0);
  });

  it('Ctrl+clique na loja não seleciona nada', async () => {
    let alternados = 0;
    await act(async () => {
      root.render(
        <DeviceCard instance={makeInstance(11, { kind: 'store' })} appName={null} selected={false} focused={false}
                    onToggle={() => { alternados += 1; }} onRange={() => { alternados += 1; }} onOpen={noop} />,
      );
    });
    const cartao = container.querySelector('article') as HTMLElement;
    await act(async () => {
      cartao.dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: true }));
      cartao.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
    });
    expect(alternados).toBe(0);
  });

  it('aparelho de tarefa continua com a caixa de seleção', async () => {
    await renderCard(makeInstance(1));
    expect(allByRole('checkbox', /Selecionar android-01/i)).toHaveLength(1);
    expect(text()).not.toContain('Loja');
  });
});

describe('DeviceCard — um aparelho, uma operação', () => {
  // O defeito: `busy` cobria só a duração do POST. Em aparelho remoto o 202 volta em milissegundos e o boot leva
  // até 480 s, então o botão voltava a ficar clicável no meio da ação e um segundo clique criava outro comando.
  const emVoo = (verb: string, state = 'running') => ({
    id: 'c-1', instance_id: 'android-07', worker_id: 'worker-lan-01', verb, state,
    fence: 3, requested_by: 'panel', reason: null, attempt: 0,
    created_at: '2026-09-22T10:00:00.000Z', dispatched_at: '2026-09-22T10:00:01.000Z',
    acked_at: null, started_at: null, finished_at: null,
  });

  it('com comando aberto no aparelho, o verbo fica indisponível COM o motivo e não dispara requisição', async () => {
    useAppStore.setState({ lastCommand: { 'android-07': emVoo('start') as never } });
    const el = await renderCard(makeInstance(7, { state: 'stopped' }));
    const botao = byRole('button', /^Iniciar/, el);
    expect(botao.getAttribute('aria-disabled')).toBe('true');
    expect(text(el)).toContain('android-07 está ocupado');
    await click(botao);
    expect(backend.callsTo('POST', /\/actions\//)).toHaveLength(0);
  });

  it('comando já terminado não bloqueia nada', async () => {
    useAppStore.setState({ lastCommand: { 'android-07': emVoo('start', 'succeeded') as never } });
    const el = await renderCard(makeInstance(7, { state: 'stopped' }));
    await click(byRole('button', 'Iniciar', el));
    await waitFor(() => expect(backend.callsTo('POST', /android-07\/actions\/start$/)).toHaveLength(1));
  });
});

describe('DeviceCard — o comando fica visível, inclusive o que ficou sem desfecho', () => {
  const comando = (over: Record<string, unknown>) => ({
    id: 'c-9', instance_id: 'android-07', worker_id: 'worker-lan-01', verb: 'start', state: 'uncertain',
    fence: 4, requested_by: 'panel', reason: 'o aparelho não completou o boot em 480 s', attempt: 0,
    created_at: '2026-09-21T17:23:22.000Z', dispatched_at: '2026-09-21T17:23:22.000Z',
    acked_at: '2026-09-21T17:23:23.000Z', started_at: '2026-09-21T17:23:30.000Z',
    finished_at: '2026-09-21T17:31:25.000Z', ...over,
  });

  it('comando incerto de ontem continua visível no cartão de hoje', async () => {
    // O defeito: o `uncertain` sumia junto com o toast. O de 21/09 ficou um dia inteiro aberto no banco sem
    // aparecer em nenhuma tela — e a dica do toast mandava olhar um histórico que a interface não exibia.
    useAppStore.setState({ lastCommand: { 'android-07': comando({}) as never } });
    const el = await renderCard(makeInstance(7, { state: 'online' }));
    expect(text(el)).toContain('Desconhecido');
    expect(text(el)).toContain('Iniciar');
  });

  it('comando em voo aparece com o verbo em andamento', async () => {
    useAppStore.setState({
      lastCommand: { 'android-07': comando({ state: 'running', finished_at: null, reason: null }) as never },
    });
    const el = await renderCard(makeInstance(7, { state: 'booting' }));
    expect(text(el)).toContain('Executando');
  });

  it('comando concluído não polui o cartão', async () => {
    useAppStore.setState({ lastCommand: { 'android-07': comando({ state: 'succeeded', reason: null }) as never } });
    const el = await renderCard(makeInstance(7, { state: 'online' }));
    expect(text(el)).not.toContain('Concluído');
  });
});

// Achado #61: o cartão de um aparelho a seis metros daqui tinha a mesma cara do emulador local, e "desligado de
// propósito no worker" era apresentado como problema de conexão do ADB.
describe('DeviceCard — em que servidor o aparelho está', () => {
  const WORKER = {
    id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [{ serial: 'emulator-5554', avd_name: 'worker-01', state: 'stopped',
                               adb_port: 5555, instance_id: 'android-13' }],
    enrolled_at: '2026-09-17T10:00:00Z',
  };

  it('emulador desligado de propósito no worker não vira "sem conexão ADB", e o servidor aparece', async () => {
    useAppStore.setState({ workers: { 'worker-lan-01': WORKER as never } });
    const el = await renderCard(makeInstance(13, { state: 'stopped', kind: 'external', worker_id: 'worker-lan-01',
                                                  state_detail: 'emulador desligado em Notebook da LAN' }));
    expect(text(el)).toContain('Emulador desligado em Notebook da LAN');
    expect(text(el)).not.toContain('Sem conexão ADB');
    // O backend manda a MESMA frase em `state_detail`: repeti-la logo abaixo não informa nada.
    expect(text(el).match(/Emulador desligado em Notebook da LAN/g)).toHaveLength(1);
    expect(byRole('button', /Notebook da LAN/, el)).toBeTruthy();
  });

  it('servidor fora do ar não afirma nada sobre o emulador de lá', async () => {
    useAppStore.setState({ workers: { 'worker-lan-01': { ...WORKER, connected: false } as never } });
    const el = await renderCard(makeInstance(13, { state: 'stopped', kind: 'external', worker_id: 'worker-lan-01' }));
    expect(text(el)).toContain('Servidor Notebook da LAN fora do ar — estado desconhecido');
  });

  it('aparelho do central não ganha selo de servidor', async () => {
    const el = await renderCard(makeInstance(1, { state: 'stopped' }));
    expect(text(el)).toContain('Emulador desligado');
    expect(text(el)).not.toContain('Notebook da LAN');
  });
});
