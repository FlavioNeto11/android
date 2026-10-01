// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { REPORT, RUN_ID, makeRun, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { RunView } from './RunView';
import { LEGENDA_DE_SUCESSO_COMPROVADO } from './ResumoDaExecucao';

/**
 * Rodada 2, tarefa 14: o resumo no topo da execução (pedido completo, resultado, o que precisa da pessoa), a legenda
 * de "sucesso comprovado" por foco e a guia padrão por situação, com a guia do link mandando sobre ela. `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const LONGO = 'Nas instâncias selecionadas, abra o QA Messenger, entre na conversa com QA-001, leia o nome do contato, '
  + 'envie a mensagem “Teste POC” e confirme na tela que ela aparece como enviada, sem repetir o envio se já estiver lá.';

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
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

function preparar(over: Parameters<typeof makeRun>[0], rota?: { aba?: string }) {
  const run = makeRun(over);
  useAppStore.setState({
    ...initialDataState, hydrated: true, hydrateCount: 1, runs: [run],
    detail: { runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready', data: makeRunDetail({ ...run }), events: [] },
  });
  if (rota) useUiStore.getState().navegar({ tela: 'execucoes', segmentos: [RUN_ID], query: rota.aba ? { aba: rota.aba } : {} }, 'replace');
  else useUiStore.setState({ selectedRunId: RUN_ID });
  return run;
}

/** Desmonta e remonta: um caso por situação dentro do mesmo teste. */
async function remontar(): Promise<void> {
  await act(async () => root.unmount());
  root = createRoot(container);
}

const abaSelecionada = () => text(container.querySelector('[role="tab"][aria-selected="true"]') as HTMLElement);
const CONCLUIDA = {
  status: 'completed' as const, started_at: '2026-09-17T12:00:00.000Z', finished_at: '2026-09-17T12:02:03.000Z',
  counts: { succeeded: 1, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 },
  instances_requested: 1, instances_used: 1, progress: 1,
};

describe('resumo no topo do detalhe', () => {
  it('concluída: pedido, resultado com a duração e a legenda de sucesso comprovado por foco', async () => {
    preparar({ ...CONCLUIDA, command: LONGO });
    await act(async () => root.render(<RunView />));
    const resumo = container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
    expect(text(resumo)).toContain('Concluída com sucesso em 2 min 03 s');
    expect(text(resumo)).toContain('1 de 1 objetivo com sucesso');
    expect(text(resumo)).toContain(LONGO);                       // o pedido inteiro está no texto, não cortado
    const gatilho = byRole('button', /O que é sucesso comprovado/, resumo);
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    await act(async () => gatilho.focus());
    await waitFor(() => expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(LEGENDA_DE_SUCESSO_COMPROVADO));
    expect(LEGENDA_DE_SUCESSO_COMPROVADO).toContain('evidência na tela do aparelho, não só pela resposta do agente');
  });

  it('pedido longo: "Ver pedido completo" abre e fecha, com aria-expanded; pedido curto não tem o botão', async () => {
    preparar({ ...CONCLUIDA, command: LONGO });
    await act(async () => root.render(<RunView />));
    const botao = byRole('button', /Ver pedido completo/, container);
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    await click(botao);
    expect(byRole('button', /Mostrar menos/, container).getAttribute('aria-expanded')).toBe('true');
    await remontar();
    preparar({ ...CONCLUIDA, command: 'Abra o QA Messenger' });
    await act(async () => root.render(<RunView />));
    expect(text(container)).not.toContain('Ver pedido completo');
  });

  it('o que precisa da pessoa só aparece quando há algo, e leva à guia que resolve', async () => {
    preparar(CONCLUIDA);
    await act(async () => root.render(<RunView />));
    expect(text(container)).not.toContain('Precisa de você');
    await remontar();
    preparar({ status: 'running', counts: { succeeded: 0, failed: 0, waiting_user: 1, uncertain: 0, cancelled: 0, running: 1, pending: 0 } });
    await act(async () => root.render(<RunView />));
    const resumo = container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
    expect(text(resumo)).toContain('Precisa de você');
    await click(byRole('button', /objetivo espera a sua decisão/, resumo));
    expect(abaSelecionada()).toMatch(/^Por aparelho/);
  });
});

describe('guia padrão por situação', () => {
  it('concluída abre no Relatório; em andamento, na Linha do tempo; planejada, no Plano', async () => {
    preparar(CONCLUIDA);
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Relatório/);
    await remontar();
    preparar({ status: 'running' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Linha do tempo/);
    await remontar();
    preparar({ status: 'planned' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Plano/);
  });

  it('a guia do link manda sobre o padrão (concluída com ?aba=plano abre no Plano)', async () => {
    preparar(CONCLUIDA, { aba: 'plano' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Plano/);
  });

  it('em andamento com ?aba=relatorio abre no Relatório; voltar ao link sem guia volta ao padrão', async () => {
    preparar({ status: 'running' }, { aba: 'relatorio' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Relatório/);
    await act(async () => useUiStore.getState().navegar({ tela: 'execucoes', segmentos: [RUN_ID] }, 'replace'));
    await waitFor(() => expect(abaSelecionada()).toMatch(/^Linha do tempo/));
  });
});
