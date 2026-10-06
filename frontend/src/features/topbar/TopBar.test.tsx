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
import { useUiStore } from '../../store/ui';
import { MenuLateral } from './MenuLateral';
import { TopBar } from './TopBar';

// P2.5 da auditoria UX (27/09): o contador "online / total" tinha um 10 fixo (`Math.max(10, …)`) — um parque de
// 4 aparelhos aparecia como "4/10". Revisão de UX de 30/09 (tarefa 01): as seções saíram da faixa do topo (cortadas
// sem pista) para o menu lateral, que abaixo de 1024 px vira gaveta aberta pelo botão "Menu".

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
  // Topo e menu juntos, como no App: o botão "Menu" do topo abre a gaveta do menu lateral.
  await act(async () => {
    root.render(<><TopBar /><MenuLateral /></>);
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

describe('Menu lateral — as nove seções sempre alcançáveis', () => {
  beforeEach(() => {
    useUiStore.getState().navegar({ tela: 'painel', query: { foco: undefined } }, 'replace');
    useUiStore.setState({ menuAberto: false, menuRecolhido: false });
  });

  const menu = (el: HTMLElement) => el.querySelector('nav[aria-label="Seções"]') as HTMLElement;

  it('lista as treze seções como links canônicos, com aria-current só na atual', async () => {
    const el = await renderBar([]);
    const links = Array.from(menu(el).querySelectorAll('a'));
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '#/painel', '#/personas', '#/aplicativos', '#/execucoes', '#/pedidos', '#/pendencias', '#/aprendizado', '#/infraestrutura', '#/configuracao',
      '#/diagnostico', '#/canais', '#/operacoes', '#/host',
    ]);
    expect(links.map((a) => text(a))).toContain('Personas');
    expect(links.filter((a) => a.getAttribute('aria-current') === 'page').map((a) => text(a))).toEqual(['Painel']);
    await act(async () => useUiStore.getState().setView('personas'));
    expect(links.filter((a) => a.getAttribute('aria-current') === 'page').map((a) => text(a))).toEqual(['Personas']);
  });

  it('tarefa 13: cada item tem nome explícito (aria-label), recolhido ou não, e a contagem vai junto no nome', async () => {
    const backend = new FakeBackend();
    backend.install();
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 3 }));
    useSessionStore.setState({ operator: 'ana' });
    const el = await renderBar([]);
    const link = (h: string) => menu(el).querySelector(`a[href="${h}"]`) as HTMLElement;
    expect(link('#/diagnostico').getAttribute('aria-label')).toBe('Diagnóstico');
    await waitFor(() => expect(link('#/aprendizado').getAttribute('aria-label')).toBe('Aprendizado, 3 para aprovar'));
    // B1 (WCAG 2.5.3): o nome COMEÇA pelo texto visível ("Aprendizado 3"); a explicação vem depois, e o selo não repete
    // a legenda em texto escondido (o axe soma o texto escondido ao visível e acusava `label-content-name-mismatch`).
    const aprendizado = link('#/aprendizado');
    const curar = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, '').replace(/\s+/g, ' ').trim();
    expect(aprendizado.textContent).toBe('Aprendizado 3');
    expect(curar(aprendizado.getAttribute('aria-label') ?? '').startsWith(curar(aprendizado.textContent ?? ''))).toBe(true);
    await act(async () => useUiStore.getState().setMenuRecolhido(true));
    // Recolhido o rótulo some da vista, mas o nome acessível segue o mesmo e o item atual continua marcado.
    expect(link('#/diagnostico').getAttribute('aria-label')).toBe('Diagnóstico');
    expect(link('#/painel').getAttribute('aria-current')).toBe('page');
    for (const a of Array.from(menu(el).querySelectorAll('a'))) expect(a.getAttribute('aria-label')).toBeTruthy();
    useSessionStore.setState({ operator: null });
    useContagemDoAprendizado.setState({ pendentes: null });
  });

  it('o aparelho em Foco vai junto na troca de seção', async () => {
    const el = await renderBar([]);
    await act(async () => useUiStore.getState().openFocus('android-01'));
    expect(menu(el).querySelector('a[href="#/infraestrutura?foco=android-01"]')).not.toBeNull();
  });

  it('recolher deixa só os ícones sem tirar o nome acessível dos links', async () => {
    const el = await renderBar([]);
    const botao = Array.from(menu(el).querySelectorAll('button')).find((b) => text(b) === 'Recolher menu') as HTMLElement;
    expect(botao.getAttribute('aria-expanded')).toBe('true');
    await act(async () => botao.click());
    expect(useUiStore.getState().menuRecolhido).toBe(true);
    expect(menu(el).hasAttribute('data-recolhido')).toBe(true);
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    expect(text(menu(el).querySelector('a[href="#/diagnostico"]') as HTMLElement)).toBe('Diagnóstico');
    expect(window.localStorage.getItem('cda.menuRecolhido')).toBe('true');
  });

  it('gaveta: o botão "Menu" abre, o teclado entra no item atual, Esc fecha e devolve o foco ao botão', async () => {
    const el = await renderBar([]);
    const botao = el.querySelector('#botao-menu') as HTMLButtonElement;
    expect(botao.getAttribute('aria-controls')).toBe('menu-principal');
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    await act(async () => botao.click());
    expect(botao.getAttribute('aria-expanded')).toBe('true');
    expect(menu(el).hasAttribute('data-aberto')).toBe(true);
    expect(document.activeElement?.getAttribute('href')).toBe('#/painel');
    await act(async () => {
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(useUiStore.getState().menuAberto).toBe(false);
    expect(document.activeElement).toBe(botao);
  });

  // RF-26: o jsdom não move o foco no Tab; o teste dispara o Tab e confere o que o ouvinte da gaveta decide.
  function tab(alvo: Element | null, shift = false): KeyboardEvent {
    const ev = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: shift, bubbles: true, cancelable: true });
    (alvo ?? document.body).dispatchEvent(ev);
    return ev;
  }

  it('gaveta aberta prende o Tab: do último item volta ao primeiro, e Shift+Tab do primeiro vai ao último', async () => {
    const el = await renderBar([]);
    await act(async () => (el.querySelector('#botao-menu') as HTMLButtonElement).click());
    const nav = menu(el);
    const focaveis = Array.from(nav.querySelectorAll<HTMLElement>('a[href], button'));
    const primeiro = focaveis[0]!;
    const ultimo = focaveis[focaveis.length - 1]!;
    expect(focaveis.length).toBeGreaterThan(9);

    ultimo.focus();
    expect(tab(ultimo).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(primeiro);
    expect(tab(primeiro, true).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(ultimo);

    // No meio da lista o Tab é do navegador: nada a interceptar.
    const meio = focaveis[3]!;
    meio.focus();
    expect(tab(meio).defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(meio);
    expect(nav.querySelectorAll('a[aria-current="page"]')).toHaveLength(1);
  });

  it('gaveta aberta traz o foco de volta se ele já estava fora, e o Esc fecha mesmo com o foco fora', async () => {
    const el = await renderBar([]);
    const botao = el.querySelector('#botao-menu') as HTMLButtonElement;
    const fora = document.createElement('button');                 // o "Reconectar agora" atrás do fundo escurecido
    fora.textContent = 'Reconectar agora';
    document.body.appendChild(fora);
    await act(async () => botao.click());
    const nav = menu(el);
    const focaveis = Array.from(nav.querySelectorAll<HTMLElement>('a[href], button'));

    fora.focus();
    expect(tab(fora).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(focaveis[0]);
    fora.focus();
    expect(tab(fora, true).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(focaveis[focaveis.length - 1]);

    fora.focus();
    await act(async () => {
      fora.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(useUiStore.getState().menuAberto).toBe(false);
    expect(document.activeElement).toBe(botao);                    // fechou sem escolher seção: o foco volta ao "Menu"
    fora.remove();
  });

  it('gaveta fechada não intercepta Tab nem Esc', async () => {
    const el = await renderBar([]);
    const fora = document.createElement('button');
    document.body.appendChild(fora);
    fora.focus();
    expect(tab(fora).defaultPrevented).toBe(false);
    await act(async () => {
      fora.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(menu(el).hasAttribute('data-aberto')).toBe(false);
    expect(document.activeElement).toBe(fora);
    fora.remove();
  });

  it('escolher uma seção na gaveta leva o foco ao conteúdo, não ao botão "Menu"', async () => {
    const main = document.createElement('main');
    main.id = 'conteudo';
    main.tabIndex = -1;
    document.body.appendChild(main);
    const el = await renderBar([]);
    await act(async () => useUiStore.getState().setMenuAberto(true));
    const link = el.querySelector('a[href="#/diagnostico"]') as HTMLAnchorElement;
    await act(async () => {
      link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    expect(useUiStore.getState().menuAberto).toBe(false);
    expect(document.activeElement).toBe(main);
    main.remove();
  });

  it('trocar de seção fecha a gaveta', async () => {
    await renderBar([]);
    await act(async () => useUiStore.getState().setMenuAberto(true));
    await act(async () => useUiStore.getState().setView('diagnostico'));
    expect(useUiStore.getState().menuAberto).toBe(false);
  });
});

describe('Menu lateral — contagem "Para aprovar" do Aprendizado (ADR-054)', () => {
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
    await waitFor(() => expect(link().getAttribute('aria-label')).toBe('Aprendizado, 3 para aprovar'));
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
    const grupo = el.querySelector('[aria-label="Custos: saldo das contas de IA"]') as HTMLElement;
    expect(grupo).not.toBeNull();
    expect(text(grupo)).toContain('US$ 1,50');
    expect(text(grupo)).not.toContain('R$');
    const chips = Array.from(grupo.querySelectorAll('[data-tone]')).map((c) => c.getAttribute('data-tone'));
    expect(chips).toEqual(['warning', 'danger']);
  });

  it('padroniza em US$: a conta em reais entra convertida e o ícone avisa que o valor é estimado', async () => {
    const snap = makeSnapshot();
    const balances = [
      { ...base, account: 'gemini', label: 'Gemini', currency: 'BRL', units_per_usd: 5.2, roles: ['decide'], in_use: true,
        estimated_balance: 29.37, estimated_balance_usd: 5.6481, state: 'ok' },
    ];
    const el = await renderBar([]);
    await act(async () => {
      useAppStore.setState({ health: { ...snap.health, ai: { ...snap.health.ai, balances } } as typeof snap.health });
    });
    const grupo = el.querySelector('[aria-label="Custos: saldo das contas de IA"]') as HTMLElement;
    expect(text(grupo)).toContain('US$ 5,65');
    expect(text(grupo)).not.toContain('R$');
    expect(grupo.querySelector('[aria-label="Valores estimados, em US$"]')).not.toBeNull();
  });
});
