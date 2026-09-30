// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance, PersonaOnDevice } from '../../api/types';
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
    expect(el.querySelector('article')?.getAttribute('aria-label')).toBe('Aparelho android-06 — Hibernado');
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

  it('sem objetivo esperando vaga, a linha "Sem tarefa em andamento" nem existe (ruído, tarefa 04)', async () => {
    const el = await renderCard(makeInstance(6, { state: 'hibernated' }));
    expect(text(el)).not.toContain('Sem tarefa em andamento');
  });
});

// Tarefa 04 (revisão de UX): o cartão não repete o que está vazio, e o parado vira compacto.
describe('DeviceCard — sem ruído e versão compacta', () => {
  it('ninguém no controle e sem tarefa: nem "Controle: —" nem "Sem tarefa em andamento" nem "Desconhecido"', async () => {
    const el = await renderCard(makeInstance(2, { state: 'online', control: 'none' }));
    expect(text(el)).not.toContain('Controle');
    expect(text(el)).not.toContain('Sem tarefa em andamento');
    expect(text(el)).not.toContain('Desconhecido');
  });

  it('sem conta e sem app associado, as linhas somem', async () => {
    const el = await renderCard(makeInstance(2, { state: 'online', account_label: null, app_id: null }));
    expect(text(el)).not.toContain('Sem rótulo de conta');
    expect(text(el)).not.toContain('Sem app associado');
  });

  it('com a IA no controle, o selo do controle continua', async () => {
    const el = await renderCard(makeInstance(2, { state: 'online', control: 'ai' }));
    expect(text(el)).toContain('Controle: IA');
  });

  it('evidência por seletor sai em português e guarda o original no title', async () => {
    const cru = 'seletor id=com.pocqa.messenger:id/account_label|text=qa-user-10: 1 elemento(s)';
    const el = await renderCard(makeInstance(10, { state: 'online', account_evidence: cru }));
    expect(text(el)).toContain('Confirmado na tela: “qa-user-10”');
    expect(text(el)).not.toContain('com.pocqa.messenger:id');
    expect(el.querySelector(`[title="${cru}"]`)).toBeTruthy();
  });

  it('o compacto é cabeçalho + estado + conta + botão: sem miniatura e sem "Emulador desligado"', async () => {
    await act(async () => {
      root.render(<DeviceCard instance={makeInstance(7, { state: 'stopped' })} appName="QA Messenger" selected={false}
                              focused={false} onToggle={noop} onRange={noop} onOpen={noop} compacto />);
    });
    expect(container.querySelector('article')?.getAttribute('aria-label')).toBe('Aparelho android-07 — Parada');
    expect(text(container)).not.toContain('Emulador desligado');
    expect(container.querySelector('img')).toBeNull();
    expect(byRole('button', /^Iniciar$/, container)).toBeTruthy();
    expect(text(container)).toContain('qa-user-7');
  });

  it('servidor fora do ar: o selo vira "Desconhecido" (ícone + texto) e a linha diz por quê, também no compacto', async () => {
    const worker = { id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
                     state: 'online', observed_state: 'online', maintenance: false, connected: false, local: false,
                     resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z' };
    useAppStore.setState({ workers: { 'worker-lan-01': worker as never } });
    await act(async () => {
      root.render(<DeviceCard instance={makeInstance(13, { state: 'stopped', kind: 'external', worker_id: 'worker-lan-01' })}
                              appName={null} selected={false} focused={false} onToggle={noop} onRange={noop} onOpen={noop} compacto />);
    });
    expect(container.querySelector('article')?.getAttribute('aria-label')).toBe('Aparelho android-13 — Desconhecido');
    expect(text(container)).toContain('Servidor Notebook da LAN fora do ar — estado desconhecido');
  });
});

describe('DeviceCard — perfil do Instagram vinculado', () => {
  // Desde o N:N (v0.29) o cartão recebe as personas DO APARELHO (a forma de `GET /instances/{id}/personas`), cada
  // uma com a sessão da conta AQUI — não mais o perfil inteiro, cuja sessão é a do principal.
  const mariana: PersonaOnDevice = {
    profile_id: 'ig-1', username: 'mariana.costa91182', display_name: null, name: 'Mariana Costa', status: 'active',
    app_id: 'instagram', is_primary: true, bound_at: '2026-09-17T10:00:00Z',
    session: { status: 'session_ready', instance_id: 'android-06', observed_username: 'mariana.costa91182',
               verified_at: '2026-09-17T11:00:00Z', detail: null, stale: false },
  };

  it('mostra a conta e o estado da sessão quando há perfil', async () => {
    await act(async () => {
      root.render(<DeviceCard instance={makeInstance(6, { state: 'online' })} appName="Instagram" personas={[mariana]}
                              selected={false} focused={false} onToggle={noop} onRange={noop} onOpen={noop} />);
    });
    expect(text(container)).toContain('@mariana.costa91182');
    expect(text(container)).toContain('Conectado');
  });

  it('com N personas (uma por app), mostra os N avatares, a primeira com conta e "+N"', async () => {
    const rafael: PersonaOnDevice = { ...mariana, profile_id: 'ig-2', username: null, name: 'Rafael Lima', app_id: 'chrome',
                                      is_primary: false, session: null };
    await act(async () => {
      root.render(<DeviceCard instance={makeInstance(6, { state: 'online' })} appName="Instagram" personas={[rafael, mariana]}
                              selected={false} focused={false} onToggle={noop} onRange={noop} onOpen={noop} />);
    });
    const linha = container.querySelector('[title*="Rafael Lima"]') as HTMLElement;
    expect(linha.getAttribute('title')).toBe('Mariana Costa (@mariana.costa91182) · Rafael Lima');
    expect(text(linha)).toContain('@mariana.costa91182');            // quem tem conta primeiro
    expect(text(linha.parentElement as HTMLElement)).toContain('+1');  // fora do corte das reticências
    expect(text(linha)).toContain('Personas:');
    const avatares = (linha.parentElement as HTMLElement).querySelector('[aria-hidden]') as HTMLElement;
    expect(avatares.children).toHaveLength(2);
    expect(text(container)).toContain('Conectado');                 // a sessão AQUI da primeira
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
    expect(text(el)).toContain('Sem resposta');
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
