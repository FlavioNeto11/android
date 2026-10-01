// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance, Worker } from '../../api/types';
import { hashDe } from '../../lib/rotas';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance, makePersona, makeRun, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { usePendenciasStore } from '../pendencias/store';
import { SaudeAmbiente } from './SaudeAmbiente';
import { TopBar } from './TopBar';

// Revisão de UX, tarefa 02: o "Ambiente OK" ficava verde com o notebook da LAN fora do ar e seis aparelhos sem
// estado conhecido, e o cabeçalho contava a loja ("5/15") enquanto a grade não ("de 14").

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/personas$/, () => json([
    makePersona('p1', 'Ana', { status: 'blocked' }), makePersona('p2', 'Bia', { status: 'active' }),
    makePersona('p3', 'Caio', { status: 'blocked' }),
  ]));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  document.body.innerHTML = '';
});

const LAN = 'worker-lan-01';

function worker(over: Partial<Worker> = {}): Worker {
  return {
    id: LAN, name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
    state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
    resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z',
    ...over,
  };
}

async function montar(lan: Partial<Worker>, ui: 'saude' | 'barra' = 'saude'): Promise<void> {
  const snap = makeSnapshot();
  const lista: Instance[] = [
    makeInstance(1, { state: 'online', worker_id: 'central' }),
    makeInstance(2, { state: 'stopped', worker_id: 'central' }),
    makeInstance(9, { state: 'online', worker_id: LAN, kind: 'external' }),
    makeInstance(10, { state: 'stopped', worker_id: LAN, kind: 'external' }),
    makeInstance(11, { state: 'online', worker_id: 'central', kind: 'store' }),
  ];
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    conn: { status: 'connected', attempt: 0, nextRetryAt: null, lastError: null, lastConnectedAt: Date.now() },
    health: { ...snap.health, status: 'ok', problems: [] },
    settings: snap.settings,
    instances: Object.fromEntries(lista.map((i) => [i.id, i])),
    instanceOrder: lista.map((i) => i.id),
    workers: { central: worker({ id: 'central', name: 'central', local: true, max_slots: 4 }), [LAN]: worker(lan) },
    // D1: a pendência de execução é a que PAROU pedindo informação; a que roda com objetivo esperando não conta.
    runs: [makeRun({ id: 'r-roda', counts: { ...makeRun().counts, waiting_user: 1, uncertain: 1 } }),
           makeRun({ id: 'r-pergunta', status: 'needs_input', counts: { ...makeRun().counts, waiting_user: 0, running: 0 } })],
  });
  // A caixa de Pendências: 1 aprovação de persona + a execução acima = 2. É o número que o topo e o semáforo mostram.
  usePendenciasStore.setState({ aprendizado: [], personas: [], falhou: false, aprovacoes: [{
    id: 'a1', profile_id: 'p1', run_id: null, objective_id: null, step_id: null, capability: 'comentar', target: '@x',
    summary: 'Comentário', generated_content: 'oi', approved_content: null, content: 'oi', status: 'pending',
    created_at: '2026-09-29T10:00:00Z', decided_at: null, decided_note: null, decided_by: null, interaction_id: null,
  }] });
  await act(async () => {
    root.render(ui === 'barra' ? <TopBar /> : <SaudeAmbiente />);
  });
}

const gatilho = () => document.querySelector('button[aria-haspopup="dialog"]') as HTMLButtonElement;

async function abrir(): Promise<HTMLElement> {
  await act(async () => gatilho().click());
  return waitFor(() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) throw new Error('popover fechado');
    return d as HTMLElement;
  });
}

describe('SaudeAmbiente — semáforo', () => {
  it('tudo em ordem: verde, sem contagem de motivos', async () => {
    await montar({});
    expect(text(gatilho())).toBe('Ambiente OK');
    expect(gatilho().getAttribute('aria-label')).toMatch(/^Ambiente OK\. /);
  });

  it('notebook da LAN fora do ar: Atenção com os motivos, cada um levando à tela que resolve', async () => {
    await montar({ connected: false, state: 'offline' });
    expect(text(gatilho())).toBe('Ambiente em atenção 2');
    // O nome acessível começa pelo que está escrito (rótulo e número), como pede o critério de a11y da tarefa.
    expect(gatilho().getAttribute('aria-label')).toMatch(/^Ambiente em atenção 2 motivos\./);
    const pop = await abrir();
    expect(text(pop)).toContain('Servidor Notebook da LAN fora do ar');
    expect(text(pop)).toContain('2 aparelhos em estado desconhecido');
    const hrefs = [...pop.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    expect(hrefs).toContain(hashDe('infraestrutura'));
    expect(hrefs).toContain(hashDe('painel', { query: { estado: 'desconhecido' } }));
  });

  it('o popover diz o que está bloqueado (personas) e o que espera você (as pendências da caixa), com link', async () => {
    await montar({});
    const pop = await abrir();
    await waitFor(() => expect(text(pop)).toContain('2 personas bloqueadas pela plataforma'));
    // D1: o mesmo número e o mesmo destino da caixa de Pendências (antes: objetivos, levando a Execuções).
    expect(text(pop)).toContain('2 pendências esperando você');
    const pendencias = [...pop.querySelectorAll('a')].find((a) => text(a).includes('pendências esperando'));
    expect(pendencias?.getAttribute('href')).toBe(hashDe('pendencias'));
    const bloqueadas = [...pop.querySelectorAll('a')].find((a) => text(a).includes('personas bloqueadas'));
    expect(bloqueadas?.getAttribute('href')).toBe(hashDe('personas', { query: { situacao: 'bloqueada' } }));
    // Personas bloqueadas não mudam a cor: são estado de trabalho, não de saúde.
    expect(text(gatilho())).toBe('Ambiente OK');
  });

  it('painel sem conexão com o central: Crítico', async () => {
    await montar({});
    await act(async () => useAppStore.getState().setConn({ status: 'disconnected' }));
    expect(text(gatilho())).toBe('Ambiente crítico 1');
  });

  // RF-44 (prova simulada 13): o texto visível era "Ambiente crítico" + o selo "8", sem nada entre eles
  // ("Ambiente crítico8"), e o nome "Ambiente crítico, 8 motivos…" não o continha: o Lighthouse marcava
  // `label-content-name-mismatch` (WCAG 2.5.3). A comparação imita a do axe: minúsculas, sem pontuação, espaços únicos.
  const normal = (s: string | null) => (s ?? '').toLowerCase().replace(/[.,;:!?…]/g, '').replace(/\s+/g, ' ').trim();

  it('RF-44: o nome acessível começa pelo texto visível, em todos os níveis', async () => {
    await montar({});
    expect(normal(gatilho().getAttribute('aria-label')).startsWith(normal(gatilho().textContent))).toBe(true);
    await act(async () => root.unmount());
    root = createRoot(container);
    await montar({ connected: false, state: 'offline' });
    const visivel = normal(gatilho().textContent);
    expect(visivel).toBe('ambiente em atenção 2');
    expect(normal(gatilho().getAttribute('aria-label')).startsWith(visivel)).toBe(true);
    await act(async () => useAppStore.getState().setConn({ status: 'disconnected' }));
    expect(normal(gatilho().getAttribute('aria-label')).startsWith(normal(gatilho().textContent))).toBe(true);
  });

  // RF-45 (prova simulada 13): seguir um link do popover trocava a tela e deixava o foco no `<body>` (o painel some
  // com o link focado dentro). Pelo menu, ele ia para o conteúdo. Agora os dois usam `lib/scroll::focarConteudo`.
  it('RF-45: seguir um link do popover leva o foco ao conteúdo principal, não ao início da página', async () => {
    const main = document.createElement('main');
    main.id = 'conteudo';
    main.tabIndex = -1;
    document.body.appendChild(main);
    await montar({ connected: false, state: 'offline' });
    for (const rotulo of ['Abrir Infraestrutura', 'Diagnóstico']) {
      const pop = await abrir();
      const link = [...pop.querySelectorAll('a')].find((a) => text(a) === rotulo) as HTMLAnchorElement;
      expect(link, rotulo).toBeTruthy();
      link.focus();
      await act(async () => {
        link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
      });
      expect(document.querySelector('[role="dialog"]'), rotulo).toBeNull();
      expect(document.activeElement, rotulo).toBe(main);
    }
  });
});

describe('TopBar — mesma base de contagem da grade', () => {
  it('online/total não conta a loja nem o aparelho de servidor fora do ar, e "aguardando você" nomeia o que conta', async () => {
    await montar({ connected: false, state: 'offline' }, 'barra');
    const indicadores = text(container.querySelector('[aria-label="Indicadores"]') as HTMLElement);
    // 4 aparelhos de tarefa (a loja fica à parte); online só android-01 (o 09 é de servidor fora do ar).
    expect(indicadores).toContain('1/4online');
    expect(indicadores).toContain('2aguardando você');
    expect(indicadores).not.toContain('bloquead');
  });

  it('D1: "aguardando você" é o total da caixa de Pendências e leva a ela', async () => {
    await montar({}, 'barra');
    const chip = [...container.querySelectorAll('[aria-label="Indicadores"] button')]
      .find((b) => text(b as HTMLElement).includes('aguardando você')) as HTMLElement;
    expect(text(chip)).toBe('2aguardando você');
    await act(async () => usePendenciasStore.setState({ aprendizado: [] , aprovacoes: [] }));
    expect(text(chip)).toBe('1aguardando você');
    await click(chip);
    expect(useUiStore.getState().rota.tela).toBe('pendencias');
  });
});
