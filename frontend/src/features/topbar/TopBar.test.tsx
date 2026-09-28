// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance, makeSnapshot } from '../../test/fixtures';
import { installBrowserStubs, text } from '../../test/harness';
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
    // as sete seções continuam todas na faixa: a pista é visual, nada some do DOM
    expect(nav?.querySelectorAll('a')).toHaveLength(7);
  });
});
