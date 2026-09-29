// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useSessionStore } from '../../store/session';
import { makeInstance, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
import { TopBar, transbordoDe } from './TopBar';

// P2.5 da auditoria UX (27/09): o contador "online / total" tinha um 10 fixo (`Math.max(10, …)`) — um parque de
// 4 aparelhos aparecia como "4/10" — e a navegação rolável não dava pista de que havia mais seções.

let root: Root;
let container: HTMLElement;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function renderBar(instances: Instance[]): Promise<HTMLElement> {
  const snap = makeSnapshot();
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    health: snap.health,
    settings: snap.settings,
    instances: Object.fromEntries(instances.map((i) => [i.id, i])),
    instanceOrder: instances.map((i) => i.id),
  });
  await act(async () => {
    root.render(<TopBar />);
  });
  return container;
}

const indicadores = (el: HTMLElement) => text(el.querySelector('[aria-label="Indicadores"]') as HTMLElement);

describe('TopBar — contador de aparelhos', () => {
  it('o total é o número de aparelhos cadastrados, não um 10 fixo', async () => {
    const el = await renderBar([makeInstance(1, { state: 'online' }), makeInstance(2, { state: 'online' }), makeInstance(3), makeInstance(4)]);
    expect(indicadores(el)).toContain('2/4');
    expect(indicadores(el)).not.toContain('/10');
  });

  it('com mais de dez aparelhos o total acompanha', async () => {
    const lista = Array.from({ length: 12 }, (_, i) => makeInstance(i + 1, i < 3 ? { state: 'online' } : {}));
    const el = await renderBar(lista);
    expect(indicadores(el)).toContain('3/12');
  });

  it('sem aparelho cadastrado mostra 0/0 em vez de inventar dez', async () => {
    const el = await renderBar([]);
    expect(indicadores(el)).toContain('0/0');
  });
});

describe('TopBar — pista de rolagem da navegação', () => {
  it('transbordoDe diz de que lado ainda há seções escondidas', () => {
    expect(transbordoDe({ scrollLeft: 0, clientWidth: 300, scrollWidth: 300 })).toBe('');
    expect(transbordoDe({ scrollLeft: 0, clientWidth: 300, scrollWidth: 500 })).toBe('fim');
    expect(transbordoDe({ scrollLeft: 200, clientWidth: 300, scrollWidth: 500 })).toBe('inicio');
    expect(transbordoDe({ scrollLeft: 100, clientWidth: 300, scrollWidth: 500 })).toBe('ambos');
    // arredondamento de 1 px do navegador não conta como transbordo
    expect(transbordoDe({ scrollLeft: 0, clientWidth: 299, scrollWidth: 300 })).toBe('');
    expect(transbordoDe({ scrollLeft: 1, clientWidth: 299, scrollWidth: 300 })).toBe('');
  });

  it('a faixa fica dentro do embrulho medido e, sem transbordo (jsdom mede zero), não ganha o atributo', async () => {
    const el = await renderBar([]);
    const nav = el.querySelector('nav[aria-label="Seções"]');
    expect(nav).not.toBeNull();
    expect(nav?.parentElement?.hasAttribute('data-transborda')).toBe(false);
    // as oito seções (Aprendizado entrou com o ADR-054) continuam todas na faixa: a pista é visual, nada some do DOM
    expect(nav?.querySelectorAll('a')).toHaveLength(8);
  });
});

describe('TopBar — contagem "Para aprovar" do Aprendizado (ADR-054)', () => {
  afterEach(() => {
    useSessionStore.setState({ operator: null });
    useContagemDoAprendizado.setState({ pendentes: null });
  });

  it('com sessão, lê a fila do D1 e mostra a contagem na seção Aprendizado', async () => {
    const backend = new FakeBackend();
    backend.install();
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 3 }));
    useSessionStore.setState({ operator: 'ana' });
    const el = await renderBar([]);
    const link = () => el.querySelector('a[href="#/aprendizado"]') as HTMLElement;
    await waitFor(() => expect(text(link())).toContain('3 para aprovar'));
    expect(backend.callsTo('GET', /^\/api\/aprendizado\/pendentes$/).length).toBeGreaterThanOrEqual(1);
  });

  it('sem sessão não pergunta nada, e zero não vira selo', async () => {
    const backend = new FakeBackend();
    backend.install();
    const el = await renderBar([]);
    expect(backend.callsTo('GET', /^\/api\/aprendizado\/pendentes$/)).toHaveLength(0);
    await act(async () => {
      useContagemDoAprendizado.setState({ pendentes: 0 });
    });
    expect(text(el.querySelector('a[href="#/aprendizado"]') as HTMLElement)).toBe('Aprendizado');
  });
});

describe('TopBar — saldo das contas de IA (ADR-051)', () => {
  const base = {
    label: '', console: 'https://x', units_per_usd: 1, warn_below: 2, block_below: null,
    key_configured: true, image: false, anchor_balance: 9.25, anchor_at: '2026-09-28T15:00:00Z', anchor_source: 'console',
    anchor_note: null, spent_since_usd: 0, estimated_balance_usd: null, age_h: 1, stale: false, message: 'ok',
    admin_key_configured: false, provider_usd: null, external_usd: 0, reconciled_at: null, reconcile_error: null,
  } as const;

  it('mostra chip só da conta em uso ou barrada, com o tom do estado', async () => {
    const snap = makeSnapshot();
    const balances = [
      { ...base, account: 'openai', label: 'OpenAI', currency: 'USD', roles: ['decide'], in_use: true,
        estimated_balance: 1.5, state: 'low' },
      { ...base, account: 'gemini', label: 'Gemini', currency: 'BRL', roles: [], in_use: false,
        estimated_balance: 29.37, state: 'ok' },
      { ...base, account: 'anthropic', label: 'Anthropic', currency: 'USD', roles: [], in_use: false,
        estimated_balance: 0, state: 'exhausted' },
    ];
    const el = await renderBar([]);
    await act(async () => {
      useAppStore.setState({ health: { ...snap.health, ai: { ...snap.health.ai, balances } } as typeof snap.health });
    });
    const grupo = el.querySelector('[aria-label="Saldo das contas de IA"]') as HTMLElement;
    expect(grupo).not.toBeNull();
    expect(text(grupo)).toContain('US$ 1,50');
    expect(text(grupo)).not.toContain('R$');
    const chips = Array.from(grupo.querySelectorAll('[data-tone]')).map((c) => c.getAttribute('data-tone'));
    expect(chips).toEqual(['warning', 'danger']);
  });
});
