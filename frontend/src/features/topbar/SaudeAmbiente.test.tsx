// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Instance, Worker } from '../../api/types';
import { hashDe } from '../../lib/rotas';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance, makePersona, makeRun, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
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
    runs: [makeRun({ counts: { ...makeRun().counts, waiting_user: 1, uncertain: 1 } })],
  });
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
    expect(text(gatilho())).toBe('Ambiente em atenção2');
    // O nome acessível começa pelo que está escrito (rótulo e número), como pede o critério de a11y da tarefa.
    expect(gatilho().getAttribute('aria-label')).toMatch(/^Ambiente em atenção, 2 motivos\./);
    const pop = await abrir();
    expect(text(pop)).toContain('Servidor Notebook da LAN fora do ar');
    expect(text(pop)).toContain('2 aparelhos em estado desconhecido');
    const hrefs = [...pop.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    expect(hrefs).toContain(hashDe('infraestrutura'));
    expect(hrefs).toContain(hashDe('painel', { query: { estado: 'desconhecido' } }));
  });

  it('o popover diz o que está bloqueado (personas) e o que espera você (objetivos), com link para a lista', async () => {
    await montar({});
    const pop = await abrir();
    await waitFor(() => expect(text(pop)).toContain('2 personas bloqueadas pela plataforma'));
    expect(text(pop)).toContain('2 objetivos aguardando você nas execuções');
    const bloqueadas = [...pop.querySelectorAll('a')].find((a) => text(a).includes('personas bloqueadas'));
    expect(bloqueadas?.getAttribute('href')).toBe(hashDe('personas', { query: { situacao: 'bloqueada' } }));
    // Personas bloqueadas não mudam a cor: são estado de trabalho, não de saúde.
    expect(text(gatilho())).toBe('Ambiente OK');
  });

  it('painel sem conexão com o central: Crítico', async () => {
    await montar({});
    await act(async () => useAppStore.getState().setConn({ status: 'disconnected' }));
    expect(text(gatilho())).toBe('Ambiente crítico1');
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
});
