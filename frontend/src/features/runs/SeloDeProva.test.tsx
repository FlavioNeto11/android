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
import { ORIGENS, ROTULO_DE_PROVA, ehDoSistema, ehProvaDeFluxo, origemDaExecucao } from './SeloDeProva';

/**
 * 30.37, ajuste (d): a execução de prova do curador aparece como "Prova de fluxo (validação)", junto do comando de
 * origem, na lista e no detalhe, e a execução comum não ganha o selo. 30.38 (a): a origem derivada no backend
 * (`origem`) põe o selo da validação do QA e o dos canais; a do sistema troca "Pedido" por "Comando de origem", a do
 * canal continua "Pedido" (foi uma pessoa). `simulated`.
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

it('origemDaExecucao: o campo do backend manda; sem ele, a prova pelo fluxo (backend anterior); sem nada, nenhuma', () => {
  expect(origemDaExecucao({ origem: 'validacao_qa', prova_fluxo_id: null })).toBe('validacao_qa');
  expect(origemDaExecucao({ origem: 'telegram' })).toBe('telegram');
  expect(origemDaExecucao({ prova_fluxo_id: 'fluxo-1' })).toBe('prova_fluxo');
  expect(origemDaExecucao({ origem: null, prova_fluxo_id: null })).toBeNull();
  expect(ehDoSistema({ origem: 'validacao_qa' })).toBe(true);
  expect(ehDoSistema({ origem: 'trello' })).toBe(false);
  expect(ehDoSistema({})).toBe(false);
});

it('a lista põe o selo da validação do QA e o do canal, cada um com o seu rótulo', async () => {
  const instances = [makeInstance(1, { worker_id: null })];
  const runs = [
    makeRun({ id: 'r-qa', short_id: 'qa', command: 'Abra o perfil @nasa', status: 'completed', origem: 'validacao_qa', origem_ref: 'lv-1' }),
    makeRun({ id: 'r-tg', short_id: 'tg', command: 'Abra o perfil @esa', status: 'completed', origem: 'telegram', origem_ref: '9001' }),
    makeRun({ id: 'r-painel', short_id: 'painel', command: 'Abra o perfil @jaxa', status: 'completed' }),
  ];
  backend.on('GET', /^\/api\/runs$/, () => json({ runs, total: 3, limit: 50, offset: 0 }));
  useAppStore.setState({
    ...initialDataState, hydrated: true, hydrateCount: 1, runs,
    instances: Object.fromEntries(instances.map((i) => [i.id, i])), instanceOrder: instances.map((i) => i.id),
  });
  useUiStore.getState().navegar({ tela: 'execucoes', query: {} }, 'replace');
  await act(async () => { root.render(<RunsPage />); });
  const item = (cmd: string) => container.querySelector(`button[title="${cmd}"]`) as HTMLElement | null;
  await waitFor(() => expect(item('Abra o perfil @nasa')).not.toBeNull());
  expect(text(item('Abra o perfil @nasa') as HTMLElement)).toContain(ORIGENS.validacao_qa.rotulo);
  expect(text(item('Abra o perfil @esa') as HTMLElement)).toContain(ORIGENS.telegram.rotulo);
  const painel = text(item('Abra o perfil @jaxa') as HTMLElement);
  for (const o of Object.values(ORIGENS)) expect(painel).not.toContain(o.rotulo);
});

it('o detalhe: a validação do QA é "Comando de origem"; o pedido pelo Telegram continua "Pedido", com o selo', async () => {
  const montar = (over: Parameters<typeof makeRun>[0]) => {
    const run = makeRun(over);
    useAppStore.setState({
      ...initialDataState, hydrated: true, hydrateCount: 1, runs: [run],
      detail: { runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready', data: makeRunDetail({ ...run }), events: [] },
    });
    useUiStore.setState({ selectedRunId: RUN_ID });
  };
  const resumo = () => container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
  montar({ status: 'cancelled', origem: 'validacao_qa', origem_ref: 'lv-1' });
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(resumo()).not.toBeNull());
  expect(text(resumo())).toContain(ORIGENS.validacao_qa.rotulo);
  expect(text(resumo())).toMatch(/Comando de origem/i);

  await act(async () => root.unmount());
  root = createRoot(container);
  montar({ status: 'cancelled', origem: 'telegram', origem_ref: '9001' });
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(resumo()).not.toBeNull());
  expect(text(resumo())).toContain(ORIGENS.telegram.rotulo);
  // O rótulo da linha (o `dt`), não o texto corrido: o selo cola no rótulo ("PedidoPelo Telegram").
  expect(resumo().querySelector('dt')?.textContent).toBe('Pedido');
  expect(text(resumo())).not.toMatch(/Comando de origem/i);
});
