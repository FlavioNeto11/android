// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { RunSummary, Worker } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeInstance, makeRun } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { RunsPage } from './RunsPage';

// Auditoria UX 27/09 (P3.6): os filtros da lista eram <select> nativos e o servidor aparecia como worker_id cru.

const lan: Worker = {
  id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
  state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
  resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z', last_seen_at: '2026-09-22T10:00:00Z',
};

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/runs$/, () => json({ runs: [], total: 0, limit: 50, offset: 0 }));
  const instances = [makeInstance(1, { worker_id: null }), makeInstance(2, { worker_id: 'worker-lan-01' })];
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    instances: Object.fromEntries(instances.map((i) => [i.id, i])),
    instanceOrder: instances.map((i) => i.id),
    workers: { [lan.id]: lan },
    runs: [],
  });
  useUiStore.setState({ selectedRunId: null });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it('o filtro por servidor mostra o NOME do worker, não o worker_id cru, e os filtros são o Select compartilhado', async () => {
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(text()).toContain('Nenhuma execução ainda'));

  const servidor = byRole('combobox', /Filtrar por servidor/) as HTMLSelectElement;
  const rotulos = [...servidor.options].map((o) => o.textContent);
  expect(rotulos).toEqual(['Todos os servidores', 'Notebook da LAN']);
  // O VALOR continua sendo o id: é o que a API filtra.
  expect([...servidor.options].map((o) => o.value)).toEqual(['', 'worker-lan-01']);

  const aparelho = byRole('combobox', /Filtrar por aparelho/) as HTMLSelectElement;
  expect([...aparelho.options].map((o) => o.value)).toEqual(['', 'android-01', 'android-02']);
  // Ambos vêm de components/Field (classe do módulo compartilhado), não de <select> cru.
  expect(servidor.className).toMatch(/select/);
  expect(aparelho.className).toMatch(/select/);
});

// ---------------------------------------------------------------- busca, filtros e paginação (tarefa UX 05)

/** 246 execuções, a mais nova primeiro, como `GET /runs` pagina (o histórico real tinha 246 em 30/09). */
function historico(): RunSummary[] {
  return Array.from({ length: 246 }, (_, i) => makeRun({
    id: `r-${String(1000 - i)}`, short_id: `c${String(1000 - i)}`, simulated: false,
    command: i === 200 ? 'No Instagram, abra o perfil @nasa e confirme o nome.' : `No QA Messenger, envie "Bom dia ${i}" para QA-001.`,
    status: i % 3 === 0 ? 'completed' : i % 3 === 1 ? 'completed_with_issues' : 'failed',
    // Relativo ao relógio de verdade: o filtro de período usa o `useNow` da tela.
    created_at: new Date(Date.now() - i * 3600e3 - 60e3).toISOString(),
  }));
}

function servirHistorico() {
  const todas = historico();
  backend.on('GET', /^\/api\/runs$/, (c) => {
    const limit = Number(c.query.get('limit'));
    const offset = Number(c.query.get('offset'));
    return json({ runs: todas.slice(offset, offset + limit), total: todas.length, limit, offset });
  });
  backend.on('GET', /^\/api\/runs\/[^/]+/, () => json(null, 404));
}

function itens(): string[] {
  const lista = document.querySelector('[aria-label="Lista de execuções"] ul') as HTMLElement | null;
  return lista ? allByRole('button', /./, lista).map((b) => b.getAttribute('title') ?? '').filter(Boolean) : [];
}

it('a lista desenha 50 por vez, com título curto sem a abertura repetida, e "Mostrar mais" pagina', async () => {
  servirHistorico();
  useUiStore.getState().navegar({ tela: 'execucoes', query: {} }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(itens()).toHaveLength(50));
  const lista = document.querySelector('[aria-label="Lista de execuções"] ul') as HTMLElement;
  expect(text(lista)).toContain('Envie "Bom dia 0" para QA-001');
  expect(text(lista)).not.toContain('No QA Messenger, envie');     // o objetivo inteiro fica no detalhe e no `title`
  expect(text(lista)).toContain('QA Messenger');
  const mais = byRole('button', /Mostrar mais \(196 restantes\)/);
  await click(mais);
  await waitFor(() => expect(itens()).toHaveLength(100));
});

it('buscar traz o histórico inteiro (páginas de 200) e acha a execução antiga; o filtro fica no link', async () => {
  servirHistorico();
  useUiStore.getState().navegar({ tela: 'execucoes', query: {} }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(itens()).toHaveLength(50));
  const antes = window.history.length;
  await setValue(byRole('textbox', /Buscar execuções/) as HTMLInputElement, 'nasa');
  // A 201ª execução: além das 100 que o store segura (MAX_RUNS) — só a paginação própria da tela a alcança.
  await waitFor(() => expect(itens()).toEqual(['No Instagram, abra o perfil @nasa e confirme o nome.']));
  expect(window.location.hash).toMatch(/[?&]q=nasa/);
  expect(window.history.length).toBe(antes);
  const paginas = backend.callsTo('GET', /^\/api\/runs$/).map((c) => c.query.get('limit'));
  expect(paginas).toContain('200');
});

it('status e período combinados vêm do link e mostram as contagens com o histórico inteiro', async () => {
  servirHistorico();
  useUiStore.getState().navegar({ tela: 'execucoes', query: { status: 'falha', periodo: '24h' } }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  // Nas últimas 24 h (i = 0..24), falharam as de i % 3 === 2: 2, 5, … 23 = 8 execuções.
  await waitFor(() => expect(itens()).toHaveLength(8));
  expect(byRole('button', /^Falharam/).getAttribute('aria-pressed')).toBe('true');
  expect((byRole('combobox', /Filtrar por período/) as HTMLSelectElement).value).toBe('24h');
  await click(byRole('button', /Limpar filtros/));
  await waitFor(() => expect(itens()).toHaveLength(50));
  expect(window.location.hash).not.toMatch(/status=|periodo=/);
});

it('sem resultado, o vazio diz que é o filtro e oferece limpar', async () => {
  servirHistorico();
  useUiStore.getState().navegar({ tela: 'execucoes', query: { q: 'nada-assim' } }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(text()).toContain('Nenhuma execução com esse filtro'));
  expect(byRole('button', /^Limpar filtros$/)).toBeTruthy();
});
