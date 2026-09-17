// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { RunDetail, UsageReport } from '../../api/types';
import { ACTION, ATTEMPT, RUN_ID, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { InstancesTab } from './InstancesTab';
import { RunUsageCard } from './RunUsageCard';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

async function render(node: ReactNode): Promise<HTMLElement> {
  await act(async () => root.render(node));
  return container;
}

/** jsdom não abre <details> com clique no <summary>: abre e avisa o React como o navegador faria. */
async function openDetails(summaryText: RegExp, scope: ParentNode = document): Promise<HTMLDetailsElement> {
  const summary = Array.from(scope.querySelectorAll('summary')).find((s) => summaryText.test(s.textContent ?? ''));
  if (!summary) throw new Error(`Nenhum <summary> com ${String(summaryText)}`);
  const details = summary.parentElement as HTMLDetailsElement;
  await act(async () => {
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
  });
  return details;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

function detailWithRecipes(): RunDetail {
  const base = makeRunDetail();
  const [openApp, send, openApp2, send2] = base.steps;
  return {
    ...base,
    objectives: [
      { ...base.objectives[0]!, status: 'running' },
      { ...base.objectives[1]!, status: 'pending', needs: null, blocked_reason: null, status_detail: 'aguardando vaga (3/3 ligados)' },
    ],
    steps: [
      { ...openApp!, driven_by: 'recipe' },
      { ...send!, driven_by: 'recipe+ai' },
      { ...openApp2!, status: 'pending', status_detail: null, driven_by: null },
      { ...send2!, driven_by: 'ai' },
    ],
    attempts: [{
      ...ATTEMPT,
      actions: [
        { ...ACTION, id: 1, seq: 1, source: 'recipe', rationale: null },
        { ...ACTION, id: 2, seq: 2, tool: 'tap', source: 'ai', rationale: 'Tocar em Enviar' },
      ],
    }],
  };
}

describe('Por instância — selos de receita e rodízio', () => {
  it('linha do objetivo pendente mostra "aguardando vaga (k/K ligados)" em vez da próxima etapa', async () => {
    const el = await render(<InstancesTab detail={detailWithRecipes()} />);
    const row = byRole('button', /android-02/, el);
    expect(text(row)).toContain('aguardando vaga (3/3 ligados)');
    expect(text(row)).toContain('Pendente');
    expect(text(row)).not.toContain('Abrir o QA Messenger');
    // quem não espera vaga continua mostrando a etapa atual
    expect(text(byRole('button', /android-01/, el))).toContain('Enviar a mensagem');
  });

  it('cada etapa ganha o selo de driven_by: "Receita", "Receita + IA", "IA" — e nenhum quando nulo', async () => {
    const el = await render(<InstancesTab detail={detailWithRecipes()} />);
    await click(byRole('button', /android-01/, el));
    const steps01 = allByRole('button', /^1|^2/, el).filter((b) => b.getAttribute('aria-controls')?.startsWith('step-body-'));
    expect(steps01).toHaveLength(2);
    expect(text(steps01[0]!)).toContain('Conduzida por: Receita');
    expect(text(steps01[0]!)).not.toContain('Receita + IA');
    expect(text(steps01[1]!)).toContain('Conduzida por: Receita + IA');

    await click(byRole('button', /android-02/, el));
    const steps02 = allByRole('button', /.*/, el).filter((b) => b.getAttribute('aria-controls')?.includes(':android-02:'));
    expect(steps02).toHaveLength(2);
    expect(text(steps02[0]!)).not.toContain('Conduzida por'); // driven_by: null → sem selo
    expect(text(steps02[1]!)).toContain('Conduzida por: IA');
  });

  it('ação com source "recipe" ganha o selo "receita"; ação da IA não', async () => {
    const el = await render(<InstancesTab detail={detailWithRecipes()} />);
    await click(byRole('button', /android-01/, el));
    const first = allByRole('button', /.*/, el).find((b) => b.getAttribute('aria-controls') === `step-body-${RUN_ID}:android-01:v1:open_app`);
    await click(first!);
    const actions = Array.from(el.querySelectorAll('ol[aria-label^="Ações da tentativa"] > li'));
    expect(actions).toHaveLength(2);
    expect(text(actions[0]!)).toContain('receita');
    expect(text(actions[0]!)).toContain('Passo gravado na receita'); // sem rationale, mas veio da receita
    expect(text(actions[1]!)).toContain('Tocar em Enviar');
    expect(text(actions[1]!)).not.toMatch(/\breceita\b/);
  });
});

describe('Custo de IA desta execução', () => {
  const REPORT: UsageReport = {
    scope: { run_id: RUN_ID, days: null },
    groups: [
      { role: 'plan', model: 'claude-sonnet-4-5', tier: 0, calls: 1, fresh: 4200, cache_read: 0, cache_write: 0, output: 1100, with_image: 0, errors: 0, avg_ms: 5200, usd: 0.0291 },
      { role: 'decide', model: 'claude-haiku-4-5', tier: 0, calls: 14, fresh: 31_250, cache_read: 118_400, cache_write: 9000, output: 2900, with_image: 4, errors: 0, avg_ms: 1700, usd: 0.0577 },
    ],
    total_usd: 0.0868, objectives_with_ai: 2, calls_per_objective: 7.5, usd_per_objective: 0.0434,
    steps_driven_by: { recipe: 6, ai: 2 }, unpriced_models: [],
  };

  it('fica recolhido e só busca GET /api/usage?run_id= na primeira abertura', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json(REPORT));
    const el = await render(<RunUsageCard run={{ id: RUN_ID, status: 'running' }} />);
    expect(text(el)).toContain('Custo de IA desta execução');
    expect(backend.callsTo('GET', /usage$/)).toHaveLength(0);

    await openDetails(/Custo de IA desta execução/, el);
    await waitFor(() => expect(text(el)).toContain('US$ 0,0868'));
    const call = backend.callsTo('GET', /usage$/)[0];
    expect(call?.query.get('run_id')).toBe(RUN_ID);
    expect(call?.query.has('days')).toBe(false);

    const headers = Array.from(el.querySelectorAll('thead th')).map((th) => th.textContent);
    expect(headers).toEqual(['Função', 'Modelo', 'Chamadas', 'Tokens novos', 'Cache lido', 'Saída', 'Com imagem', 'US$']);
    const rows = Array.from(el.querySelectorAll('tbody tr')).map((tr) => Array.from(tr.children).map((c) => c.textContent));
    expect(rows).toEqual([
      ['Planejar', 'claude-sonnet-4-5', '1', '4.200', '0', '1.100', '0', 'US$ 0,0291'],
      ['Decidir', 'claude-haiku-4-5', '14', '31.250', '118.400', '2.900', '4', 'US$ 0,0577'],
    ]);
    expect(text(el)).toContain('7,5'); // chamadas por aparelho
    expect(text(el)).toContain('US$ 0,0434'); // US$ por aparelho
    expect(text(el)).toContain('6 por receita × 2 por IA');
    expect(text(el)).toContain('valores parciais');
  });

  it('recarrega sozinho quando a execução termina e pelo botão "Atualizar"', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json(REPORT));
    const el = await render(<RunUsageCard run={{ id: RUN_ID, status: 'running' }} />);
    await openDetails(/Custo de IA/, el);
    await waitFor(() => expect(backend.callsTo('GET', /usage$/)).toHaveLength(1));

    await render(<RunUsageCard run={{ id: RUN_ID, status: 'completed' }} />);
    await waitFor(() => expect(backend.callsTo('GET', /usage$/)).toHaveLength(2));
    await waitFor(() => expect(text(el)).toContain('valores finais'));

    await click(byRole('button', /^Atualizar/, el));
    await waitFor(() => expect(backend.callsTo('GET', /usage$/)).toHaveLength(3));
  });

  it('modelo sem preço ("simulado"): mostra "sem preço" no lugar de US$ e explica', async () => {
    const simulated: UsageReport = {
      ...REPORT,
      groups: REPORT.groups.map((g) => ({ ...g, model: 'simulado', usd: null })),
      total_usd: 0, usd_per_objective: 0, unpriced_models: ['simulado'],
    };
    backend.on('GET', /^\/api\/usage$/, () => json(simulated));
    const el = await render(<RunUsageCard run={{ id: RUN_ID, status: 'completed' }} />);
    await openDetails(/Custo de IA/, el);
    await waitFor(() => expect(el.querySelectorAll('tbody tr')).toHaveLength(2));
    const usdCells = Array.from(el.querySelectorAll('tbody tr')).map((tr) => tr.lastElementChild?.textContent);
    expect(usdCells).toEqual(['sem preço', 'sem preço']);
    expect(text(el)).not.toContain('US$ 0,00');
    expect(text(el)).toContain('Modelo(s) sem preço: simulado');
    // o resumo recolhido também não finge custo zero
    expect(el.querySelector('summary')?.textContent).toContain('sem preço');
  });

  it('erro do backend vira estado de erro com "Tentar de novo"', async () => {
    backend.on('GET', /^\/api\/usage$/, () => apiError(503, 'db_locked', 'Banco ocupado'));
    const el = await render(<RunUsageCard run={{ id: RUN_ID, status: 'running' }} />);
    await openDetails(/Custo de IA/, el);
    await waitFor(() => expect(text(el)).toContain('Banco ocupado'));
    backend.on('GET', /^\/api\/usage$/, () => json(REPORT));
    await click(byRole('button', /^Tentar de novo/, el));
    await waitFor(() => expect(text(el)).toContain('US$ 0,0868'));
  });
});
