// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { REPORT, RUN_ID, makeInstance, makeRun, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { RunsPage } from './RunsPage';
import { RunView } from './RunView';
import { ROTULO_DE_PROVA, ehProvaDeFluxo } from './SeloDeProva';

/**
 * 30.37, ajuste (d): a execução de prova do curador aparece como "Prova de fluxo (validação)", junto do comando de
 * origem, na lista e no detalhe, e a execução comum não ganha o selo. `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/runs$/, () => json({ runs: [], total: 0, limit: 50, offset: 0 }));
  backend.on('GET', /^\/api\/runs\/[^/]+\/report$/, () => json(REPORT));
  backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json({ run_id: RUN_ID, votos: [], sinais: [], aprendizado: null }));
  backend.on('GET', /^\/api\/approvals/, () => json([]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it('ehProvaDeFluxo: só com o fluxo provado', () => {
  expect(ehProvaDeFluxo({ prova_fluxo_id: 'fluxo-1' })).toBe(true);
  expect(ehProvaDeFluxo({ prova_fluxo_id: null })).toBe(false);
  expect(ehProvaDeFluxo({})).toBe(false);
});

it('a lista põe o selo só na execução de prova, e o comando de origem continua como título', async () => {
  const instances = [makeInstance(1, { worker_id: null })];
  const runs = [
    makeRun({ id: 'r-prova', short_id: 'prova', command: 'Abra o perfil @nasa', status: 'completed', prova_fluxo_id: 'fluxo-1' }),
    makeRun({ id: 'r-comum', short_id: 'comum', command: 'Abra o perfil @esa', status: 'completed' }),
  ];
  backend.on('GET', /^\/api\/runs$/, () => json({ runs, total: 2, limit: 50, offset: 0 }));
  useAppStore.setState({
    ...initialDataState, hydrated: true, hydrateCount: 1, runs,
    instances: Object.fromEntries(instances.map((i) => [i.id, i])), instanceOrder: instances.map((i) => i.id),
  });
  useUiStore.getState().navegar({ tela: 'execucoes', query: {} }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  const itemDaProva = () => container.querySelector('button[title="Abra o perfil @nasa"]') as HTMLElement | null;
  const itemComum = () => container.querySelector('button[title="Abra o perfil @esa"]') as HTMLElement | null;
  await waitFor(() => expect(itemDaProva()).not.toBeNull());
  expect(text(itemDaProva() as HTMLElement)).toContain(ROTULO_DE_PROVA);
  expect(text(itemComum() as HTMLElement)).not.toContain(ROTULO_DE_PROVA);
});

it('o detalhe rotula a prova e chama o texto de "Comando de origem", não de "Pedido"; a comum segue "Pedido"', async () => {
  const montar = (over: Parameters<typeof makeRun>[0]) => {
    const run = makeRun(over);
    useAppStore.setState({
      ...initialDataState, hydrated: true, hydrateCount: 1, runs: [run],
      detail: { runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready', data: makeRunDetail({ ...run }), events: [] },
    });
    useUiStore.setState({ selectedRunId: RUN_ID });
  };
  montar({ status: 'cancelled', prova_fluxo_id: 'fluxo-1' });
  await act(async () => { root.render(<RunView />); });
  const resumo = () => container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
  await waitFor(() => expect(resumo()).not.toBeNull());
  expect(text(resumo())).toContain(ROTULO_DE_PROVA);
  expect(text(resumo())).toMatch(/Comando de origem/i);
  expect(text(resumo())).not.toMatch(/\bPedido\b/i);

  await act(async () => root.unmount());
  root = createRoot(container);
  montar({ status: 'cancelled' });
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(resumo()).not.toBeNull());
  expect(text(resumo())).not.toContain(ROTULO_DE_PROVA);
  expect(text(resumo())).toMatch(/Pedido/i);
});
