// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useSessionStore } from '../../store/session';
import { makeInstance, makeRun, makeSnapshot } from '../../test/fixtures';
import { installBrowserStubs, text, waitFor } from '../../test/harness';
import { usePendenciasStore } from '../pendencias/store';
import { TopBar } from './TopBar';

// Rodada 2 da revisão de UX, tarefa 10: abaixo de 768 px o cabeçalho é UMA linha (menu, marca, semáforo, "Resumo") e o
// resto mora no painel "Resumo"; de 768 a 1023 px, CPU, RAM e custos viram o chip "Recursos"; de 1024 px em diante a
// régua é a de sempre. O que se prova aqui: cada faixa mostra o que deve e o painel reaproveita os MESMOS números.

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
  delete (window as { matchMedia?: unknown }).matchMedia;
  useSessionStore.setState({ operator: null });
  usePendenciasStore.setState({ aprendizado: null, aprovacoes: null, personas: null, falhou: false,
                               falhas: { aprendizado: false, aprovacoes: false, personas: false } });
});

/** O jsdom não tem `matchMedia`: o dublê responde pelo `max-width` da consulta, como o navegador numa janela dessa largura. */
function fingirLargura(px: number): void {
  const matchMedia = (query: string): MediaQueryList => {
    const m = /max-width:\s*(\d+)px/.exec(query);
    return {
      matches: m ? px <= Number(m[1]) : false, media: query, onchange: null,
      addEventListener: () => undefined, removeEventListener: () => undefined,
      addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
    };
  };
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: matchMedia });
}

const conta = {
  label: '', console: 'https://x', units_per_usd: 1, warn_below: 2, block_below: null,
  key_configured: true, image: false, anchor_balance: 9.25, anchor_at: '2026-09-28T15:00:00Z', anchor_source: 'console',
  anchor_note: null, spent_since_usd: 0, estimated_balance_usd: null, age_h: 1, stale: false, message: 'ok',
  admin_key_configured: false, provider_usd: null, external_usd: 0, reconciled_at: null, reconcile_error: null,
} as const;

async function montar(opts: { pendencias?: number } = {}): Promise<void> {
  const snap = makeSnapshot();
  const lista = [makeInstance(1, { state: 'online' }), makeInstance(2, { state: 'online' }), makeInstance(3), makeInstance(4)];
  const balances = [
    { ...conta, account: 'openai', label: 'OpenAI', currency: 'USD', roles: ['decide'], in_use: true, estimated_balance: 1.5, state: 'low' },
  ];
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    health: { ...snap.health, ai: { ...snap.health.ai, balances } } as typeof snap.health,
    settings: snap.settings,
    metrics: { ts: new Date().toISOString(), cpu_percent: 37, mem_total_gb: 64, mem_available_gb: 40.5, mem_used_percent: 37, emulators: [] },
    instances: Object.fromEntries(lista.map((i) => [i.id, i])),
    instanceOrder: lista.map((i) => i.id),
    runs: [makeRun()],
  });
  const n = opts.pendencias ?? 0;
  usePendenciasStore.setState({
    aprendizado: [], personas: [], falhou: false,
    aprovacoes: Array.from({ length: n }, (_, i) => ({
      id: `a${i}`, profile_id: 'p1', run_id: null, objective_id: null, step_id: null, capability: 'comentar', target: '@x',
      summary: 'Comentário', generated_content: 'oi', approved_content: null, content: 'oi', status: 'pending',
      created_at: '2026-09-29T10:00:00Z', decided_at: null, decided_note: null, decided_by: null, interaction_id: null,
    })),
  });
  await act(async () => root.render(<TopBar />));
}

const cabecalho = () => container.querySelector('header') as HTMLElement;
const botaoDoCabecalho = (nome: string) =>
  Array.from(cabecalho().querySelectorAll('button')).find((b) => text(b) === nome) as HTMLButtonElement | undefined;

async function abrir(nome: string): Promise<HTMLElement> {
  await act(async () => botaoDoCabecalho(nome)!.click());
  return waitFor(() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) throw new Error('painel fechado');
    return d as HTMLElement;
  });
}

const indicadores = (raiz: ParentNode) => raiz.querySelector('[aria-label="Indicadores"]') as HTMLElement | null;

describe('Cabeçalho — celular (< 768 px)', () => {
  it('é uma linha só: menu, marca, semáforo e "Resumo"; sem régua nem ferramentas soltas', async () => {
    fingirLargura(390);
    await montar();
    const h = cabecalho();
    expect(h.children).toHaveLength(1);                                       // só a linha de cima
    expect(h.querySelector('#botao-menu')).not.toBeNull();
    expect(h.querySelector('a[aria-label^="Central de Aparelhos"]')).not.toBeNull();
    expect(h.querySelector('[role="group"][aria-label="Saúde"] button')?.getAttribute('aria-label')).toMatch(/^Ambiente /);
    expect(botaoDoCabecalho('Resumo')).toBeDefined();
    expect(indicadores(h)).toBeNull();
    expect(h.querySelector('[aria-label="Custos: saldo das contas de IA"]')).toBeNull();
    expect(h.querySelector('[aria-label^="Modelo de IA"]')).toBeNull();
  });

  it('o "Resumo" traz os MESMOS indicadores da régua larga (capacidade, execuções, aguardando você, CPU e RAM)', async () => {
    fingirLargura(1440);
    await montar({ pendencias: 2 });
    const daRegua = text(indicadores(cabecalho())!);
    expect(daRegua).toContain('2/4');
    expect(daRegua).toContain('2aguardando você');

    await act(async () => root.render(<></>));
    fingirLargura(390);
    await act(async () => root.render(<TopBar />));
    const painel = await abrir('Resumo');
    expect(text(indicadores(painel)!)).toBe(daRegua);
    expect(text(painel)).toContain('CPU');
    expect(text(painel)).toContain('RAM');
  });

  it('o "Resumo" traz os custos, a IA, a conexão e a sessão que a régua larga mostrava', async () => {
    fingirLargura(390);
    useSessionStore.setState({ operator: 'ana' });
    await montar();
    const painel = await abrir('Resumo');
    const custos = painel.querySelector('[aria-label="Custos: saldo das contas de IA"]') as HTMLElement;
    expect(custos).not.toBeNull();
    expect(text(custos)).toContain('US$ 1,50');
    expect(text(painel)).toContain('MODO SIMULADO');
    expect(text(painel)).toContain('Modelo de IA');
    expect(text(painel)).toContain('Conexão');
    expect(text(painel)).toContain('ana');
    expect(Array.from(painel.querySelectorAll('button')).some((b) => text(b) === 'Sair')).toBe(true);
    // sem popover aninhado: o painel não tem gatilho de outro painel
    expect(painel.querySelector('[aria-haspopup="dialog"]')).toBeNull();
  });

  it('o rótulo do botão começa pelo texto visível e avisa quantas pendências esperam', async () => {
    fingirLargura(390);
    await montar({ pendencias: 3 });
    const b = botaoDoCabecalho('Resumo')!;
    expect(b.getAttribute('aria-label')).toMatch(/^Resumo, 3 aguardando você\./);
  });
});

describe('Botão "Menu" — selo de pendências (D1)', () => {
  it('mostra o total da caixa de Pendências e o diz no nome; zero não vira selo', async () => {
    fingirLargura(390);
    await montar({ pendencias: 2 });
    const menu = container.querySelector('#botao-menu') as HTMLButtonElement;
    expect(text(menu)).toBe('Menu, 2 aguardando você2');
    await montar({ pendencias: 0 });
    expect(text(container.querySelector('#botao-menu') as HTMLElement)).toBe('Menu');
  });
});

describe('Botão "Menu" e "Resumo" — origem que não carregou (B8)', () => {
  it('o selo do Menu vira "2+", o nome diz que pode ser mais e o Resumo repete; sem nada contado, o selo é "?"', async () => {
    fingirLargura(390);
    await montar({ pendencias: 2 });
    await act(async () => usePendenciasStore.setState({ falhou: true, falhas: { aprendizado: true, aprovacoes: false, personas: false } }));
    const menu = container.querySelector('#botao-menu') as HTMLButtonElement;
    expect(text(menu)).toBe('Menu, 2 ou mais aguardando você; alguma origem não carregou2+');
    expect(botaoDoCabecalho('Resumo')!.getAttribute('aria-label'))
      .toMatch(/^Resumo, 2 ou mais aguardando você; alguma origem não carregou\./);
    await montar({ pendencias: 0 });
    await act(async () => usePendenciasStore.setState({ falhou: true }));
    expect(text(container.querySelector('#botao-menu') as HTMLElement)).toBe('Menu, não foi possível contar; alguma origem não carregou?');
    await act(async () => usePendenciasStore.setState({ falhou: false, falhas: { aprendizado: false, aprovacoes: false, personas: false } }));
  });
});

describe('Cabeçalho — tablet (768 a 1023 px)', () => {
  it('CPU, RAM e custos saem da régua e vão para o chip "Recursos"', async () => {
    fingirLargura(800);
    await montar({ pendencias: 1 });
    const h = cabecalho();
    expect(h.children).toHaveLength(2);                                       // marca + régua
    const regua = text(indicadores(h)!);
    expect(regua).toContain('2/4');
    expect(regua).toContain('aguardando você');
    expect(regua).not.toContain('CPU');
    expect(regua).not.toContain('RAM');
    expect(h.querySelector('[aria-label="Custos: saldo das contas de IA"]')).toBeNull();
    expect(botaoDoCabecalho('Resumo')).toBeUndefined();

    const painel = await abrir('Recursos');
    expect(text(painel)).toContain('CPU');
    expect(text(painel)).toContain('RAM');
    expect(text(painel.querySelector('[aria-label="Custos: saldo das contas de IA"]') as HTMLElement)).toContain('US$ 1,50');
  });
});

describe('Cabeçalho — largo (≥ 1024 px)', () => {
  it('a régua inteira, como antes: sem "Resumo" nem "Recursos"', async () => {
    fingirLargura(1440);
    await montar();
    const h = cabecalho();
    expect(botaoDoCabecalho('Resumo')).toBeUndefined();
    expect(botaoDoCabecalho('Recursos')).toBeUndefined();
    expect(text(indicadores(h)!)).toContain('CPU');
    expect(text(indicadores(h)!)).toContain('RAM');
    expect(h.querySelector('[aria-label="Custos: saldo das contas de IA"]')).not.toBeNull();
    expect(h.querySelector('[aria-label^="Modelo de IA"]')).not.toBeNull();
  });
});
