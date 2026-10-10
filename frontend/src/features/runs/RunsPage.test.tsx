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
  // Prazo próprio: desenhar 50 e depois 100 cartões estourou os 5 s padrão sob a suíte em prioridade Idle (suíte 20,
  // 04/10), com o mesmo resultado isolado. A asserção não muda; muda só quanto o teste espera a máquina ocupada.
}, 20_000);

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

it('D1: o chip de `completed_with_issues` + `needs_input` não usa a palavra "pendência" e o link antigo continua valendo', async () => {
  servirHistorico();
  // O link de antes (`?status=pendencia`) abre o mesmo recorte: o código na URL não mudou, só o rótulo.
  useUiStore.getState().navegar({ tela: 'execucoes', query: { status: 'pendencia' } }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  // i % 3 === 1 são `completed_with_issues`: 82 das 246.
  await waitFor(() => expect(byRole('button', /^Pede atenção/).getAttribute('aria-pressed')).toBe('true'));
  // O chip marcado vem da URL; a contagem, do fetch (29.104).
  await waitFor(() => expect(text(byRole('button', /^Pede atenção/))).toBe('Pede atenção82'));
  const chips = document.querySelector('[role="group"][aria-label="Situação da execução"]') as HTMLElement;
  expect(text(chips).toLowerCase()).not.toContain('pendência');
  // A dica (no foco de teclado também) diz o que o conjunto é, e onde estão as que dependem de você.
  await act(async () => byRole('button', /^Pede atenção/).focus());
  await waitFor(() => expect(text(document.querySelector('[role="tooltip"]') as HTMLElement)).toMatch(/pararam pedindo informação/));
  expect(text(document.querySelector('[role="tooltip"]') as HTMLElement)).toContain('Pendências');
});

it('D2: as execuções `planned` têm chip próprio "Planejadas", fora de "Em andamento", e o link `?status=planejada` vale', async () => {
  const runs = [
    makeRun({ id: 'r-plano', short_id: 'plano', status: 'planned', command: 'Plano para inspecionar', created_at: new Date(Date.now() - 3 * 864e5).toISOString() }),
    makeRun({ id: 'r-roda', short_id: 'roda', status: 'running', command: 'Rodando agora', created_at: new Date().toISOString() }),
  ];
  backend.on('GET', /^\/api\/runs$/, () => json({ runs, total: runs.length, limit: 200, offset: 0 }));
  backend.on('GET', /^\/api\/runs\/[^/]+/, () => json(null, 404));
  useUiStore.getState().navegar({ tela: 'execucoes', query: { status: 'planejada' } }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(itens()).toEqual(['Plano para inspecionar']));
  expect(byRole('button', /^Planejadas/).getAttribute('aria-pressed')).toBe('true');
  expect(text(byRole('button', /^Planejadas/))).toBe('Planejadas1');
  expect(text(byRole('button', /^Em andamento/))).toBe('Em andamento1');
  await act(async () => byRole('button', /^Planejadas/).focus());
  await waitFor(() => expect(text(document.querySelector('[role="tooltip"]') as HTMLElement))
    .toBe('Plano pronto para inspeção; ainda não foi executado'));
});

// ---------------------------------------------------------------- 31.306: etapa descoberta pela IA (adendo v1.135)

function servirComExploracao() {
  const todas = [
    makeRun({ id: 'r-3', short_id: 'c3', command: 'No QA Messenger, envie "A" para QA-001.', status: 'completed', etapas_exploratorias: 2 }),
    makeRun({ id: 'r-2', short_id: 'c2', command: 'No QA Messenger, envie "B" para QA-001.', status: 'completed', etapas_exploratorias: 0 }),
    makeRun({ id: 'r-1', short_id: 'c1', command: 'No QA Messenger, envie "C" para QA-001.', status: 'completed' }),   // backend anterior
  ];
  backend.on('GET', /^\/api\/runs$/, (c) => {
    const limit = Number(c.query.get('limit'));
    const offset = Number(c.query.get('offset'));
    return json({ runs: todas.slice(offset, offset + limit), total: todas.length, limit, offset });
  });
  backend.on('GET', /^\/api\/runs\/[^/]+/, () => json(null, 404));
}

it('31.306: a execução com etapa descoberta leva o selo e o filtro da origem mostra a contagem', async () => {
  servirComExploracao();
  useUiStore.getState().navegar({ tela: 'execucoes', query: {} }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(itens()).toHaveLength(3));
  const lista = document.querySelector('[aria-label="Lista de execuções"] ul') as HTMLElement;
  expect(text(lista).match(/Descoberta pela IA/g)).toHaveLength(1);      // só a de contador > 0
  const origem = byRole('combobox', /Filtrar pela origem das etapas/) as HTMLSelectElement;
  expect([...origem.options].map((o) => o.textContent)).toEqual(['Todas as origens', 'Com etapa descoberta pela IA (1)']);
  expect(origem.value).toBe('');
});

it('31.306: `?exploracao=1` mostra só as descobertas, marca o filtro e "Limpar filtros" o tira do link', async () => {
  servirComExploracao();
  useUiStore.getState().navegar({ tela: 'execucoes', query: { exploracao: '1' } }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(itens()).toEqual(['No QA Messenger, envie "A" para QA-001.']));
  expect((byRole('combobox', /Filtrar pela origem das etapas/) as HTMLSelectElement).value).toBe('1');
  await click(byRole('button', /Limpar filtros/));
  await waitFor(() => expect(itens()).toHaveLength(3));
  expect(window.location.hash).not.toMatch(/exploracao=/);
});

it('31.306: sem nenhuma execução descoberta, o filtro ligado diz que é o filtro e oferece limpar', async () => {
  backend.on('GET', /^\/api\/runs$/, () => json({ runs: [makeRun({ id: 'r-9', short_id: 'c9', etapas_exploratorias: 0 })], total: 1, limit: 200, offset: 0 }));
  useUiStore.getState().navegar({ tela: 'execucoes', query: { exploracao: '1' } }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(text()).toContain('Nenhuma execução com esse filtro'));
});

it('achado da varredura 70/71: o filtro de exploração sem nenhuma execução descoberta diz que ainda não há nenhuma', async () => {
  backend.on('GET', /^\/api\/runs$/, () => json({ runs: [makeRun({ id: 'r-9', short_id: 'c9', etapas_exploratorias: 0 })], total: 1, limit: 200, offset: 0 }));
  useUiStore.getState().navegar({ tela: 'execucoes', query: { exploracao: '1' } }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  await waitFor(() => expect(text()).toContain('Nenhuma execução tem etapa descoberta pela IA ainda'));
});
