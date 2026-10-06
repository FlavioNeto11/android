// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { loadRunDetail } from '../../store/live';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { REPORT, RUN_ID, makeRun, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { RunView } from './RunView';
import { RunsPage } from './RunsPage';

/**
 * 29.153 (adendo v1.74): o custo de IA da execução, lido de `costs {spent_usd, calls}` do detalhe. Mostrado no resumo
 * do detalhe e, quando já foi lido, na linha da lista. Sem o campo (backend de antes do PR 465): "custo não lido", sem
 * erro. Prova `simulated` (fetch falso).
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/runs\/[^/]+\/report$/, () => json(REPORT));
  backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json({ run_id: RUN_ID, votos: [], sinais: [], aprendizado: null }));
  backend.on('GET', /^\/api\/approvals/, () => json([]));
  backend.on('GET', /^\/api\/runs$/, () => json({ runs: [], total: 0, limit: 50, offset: 0 }));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
  useUiStore.setState({ selectedRunId: null });
});

const CONCLUIDA = {
  status: 'completed' as const, started_at: '2026-09-17T12:00:00.000Z', finished_at: '2026-09-17T12:02:03.000Z',
  counts: { succeeded: 1, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 },
  instances_requested: 1, instances_used: 1, progress: 1,
};

async function abrirDetalhe(costs: unknown): Promise<HTMLElement> {
  const run = makeRun(CONCLUIDA);
  const detalhe = { ...makeRunDetail({ ...run }), ...(costs === 'ausente' ? {} : { costs }) } as ReturnType<typeof makeRunDetail>;
  useAppStore.setState({
    ...initialDataState, hydrated: true, hydrateCount: 1, runs: [run],
    detail: { runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready', data: detalhe, events: [] },
  });
  useUiStore.setState({ selectedRunId: RUN_ID });
  await act(async () => root.render(<RunView />));
  return container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
}

describe('29.153: custo no detalhe da execução', () => {
  it('mostra US$ com 4 casas e o número de chamadas', async () => {
    const resumo = await abrirDetalhe({ spent_usd: 0.0407, calls: 2 });
    expect(text(resumo)).toContain('CustoUS$ 0,0407 · 2 chamadas de IA');
  });

  it('uma chamada vai no singular; um custo grande mantém as 4 casas e o separador de milhar', async () => {
    let resumo = await abrirDetalhe({ spent_usd: 0.1, calls: 1 });
    expect(text(resumo)).toContain('US$ 0,1000 · 1 chamada de IA');
    expect(text(resumo)).not.toContain('1 chamadas');
    await act(async () => root.unmount());
    root = createRoot(container);
    resumo = await abrirDetalhe({ spent_usd: 1234.5, calls: 900 });
    expect(text(resumo)).toContain('US$ 1.234,5000 · 900 chamadas de IA');
  });

  it('zero de verdade (sem chamadas) é "US$ 0,0000", não "custo não lido"', async () => {
    const resumo = await abrirDetalhe({ spent_usd: 0, calls: 0 });
    expect(text(resumo)).toContain('US$ 0,0000 · nenhuma chamada de IA');
    expect(text(resumo)).not.toContain('custo não lido');
  });

  it('sem o campo (backend antigo) ou com costs nulo: "custo não lido", sem erro nem NaN', async () => {
    for (const costs of ['ausente', null, { spent_usd: Number.NaN, calls: 0 }]) {
      const resumo = await abrirDetalhe(costs);
      expect(text(resumo)).toContain('Custocusto não lido');
      expect(text(resumo)).not.toMatch(/NaN|undefined|US\$/);
      expect(container.querySelector('[role="alert"]')).toBeNull();
      await act(async () => root.unmount());
      root = createRoot(container);
    }
  });
});

describe('29.153: custo na lista de Execuções', () => {
  it('o custo lido no detalhe vai para a linha da lista e sobrevive à lista nova, que não o traz', async () => {
    backend.on('GET', new RegExp(`^/api/runs/${RUN_ID}$`), () => json({ ...makeRunDetail(makeRun(CONCLUIDA)), costs: { spent_usd: 0.0407, calls: 2 } }));
    useAppStore.setState({ ...initialDataState, hydrated: true, hydrateCount: 1, runs: [makeRun(CONCLUIDA)] });
    await act(async () => root.render(<RunsPage />));
    await waitFor(() => expect(text()).toContain('Concluída'));
    expect(text()).not.toContain('US$');                                     // a lista não traz o custo: nada lido, nada mostrado

    await act(async () => { await loadRunDetail(RUN_ID); });                 // abrir o detalhe lê o custo
    await waitFor(() => expect(useAppStore.getState().runs[0]!.costs).toEqual({ spent_usd: 0.0407, calls: 2 }));
    await waitFor(() => expect(text()).toContain('US$ 0,0407'));
    expect(document.querySelector('[title="2 chamadas de IA"]')).not.toBeNull();

    // Um evento ao vivo e a recarga da lista trazem o resumo SEM `costs`: o último custo lido não some.
    await act(async () => useAppStore.getState().upsertRun(makeRun({ ...CONCLUIDA, status_detail: 'mudou' })));
    await act(async () => useAppStore.getState().mergeRuns([makeRun({ ...CONCLUIDA, status_detail: 'mudou de novo' })]));
    expect(useAppStore.getState().runs[0]!.status_detail).toBe('mudou de novo');
    expect(useAppStore.getState().runs[0]!.costs).toEqual({ spent_usd: 0.0407, calls: 2 });
    expect(text()).toContain('US$ 0,0407');

    // Um custo novo (o detalhe lido de novo) vence o antigo.
    await act(async () => useAppStore.getState().upsertRun(makeRun({ ...CONCLUIDA, costs: { spent_usd: 0.05, calls: 3 } })));
    expect(useAppStore.getState().runs[0]!.costs).toEqual({ spent_usd: 0.05, calls: 3 });
  });

  it('o custo zero lido também aparece na linha (US$ 0,0000), e execução sem leitura não mostra nada', async () => {
    const lida = makeRun({ ...CONCLUIDA, costs: { spent_usd: 0, calls: 0 } });
    const outra = makeRun({ ...CONCLUIDA, id: 'run-0002', short_id: 'run-0002', created_at: '2026-09-16T12:00:00.000Z' });
    useAppStore.setState({ ...initialDataState, hydrated: true, hydrateCount: 1, runs: [lida, outra] });
    await act(async () => root.render(<RunsPage />));
    await waitFor(() => expect(text()).toContain('US$ 0,0000'));
    const linhas = [...document.querySelectorAll<HTMLElement>('button[class*="runItem"]')];
    expect(linhas).toHaveLength(2);
    expect(linhas.filter((l) => l.textContent!.includes('US$'))).toHaveLength(1);       // só a que já foi lida
    expect(linhas.find((l) => l.textContent!.includes('run-0002'))!.textContent).not.toContain('US$');
  });
});
