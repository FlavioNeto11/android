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
    expect(call?.body).toEqual({});
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
                 verified_at: '2026-09-17T11:00:00Z', detail: null },
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
