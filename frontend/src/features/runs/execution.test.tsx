// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Approval, RunDetail, UsageReport } from '../../api/types';
import { ACTION, ATTEMPT, RUN_ID, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { InstancesTab } from './InstancesTab';
import { RunUsageCard } from './RunUsageCard';
import { TextsTab, useRunApprovals } from './TextsTab';

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

describe('Textos — os N rascunhos da execução, lidos e decididos juntos', () => {
  function rascunho(id: string, objetivo: string, conteudo: string): Approval {
    return {
      id, profile_id: `p-${id}`, run_id: RUN_ID, objective_id: objetivo, step_id: `${RUN_ID}:x:v1:comment`,
      capability: 'CREATE_COMMENT', target: '@secretaria', summary: 'Comentar na publicação',
      generated_content: conteudo, approved_content: null, content: conteudo,
      status: 'pending', created_at: '2026-09-17T12:00:00.000Z', decided_at: null, decided_note: null,
      interaction_id: null,
    };
  }

  /** Usa o mesmo gancho da tela real: assim o teste cobre a busca por run_id, não só a pintura. */
  function Textos({ detail }: { detail: RunDetail }) {
    const approvals = useRunApprovals(detail.id);
    return <TextsTab detail={detail} approvals={approvals} />;
  }

  it('mostra um texto por aparelho, busca só os desta execução e decide todos em uma chamada', async () => {
    const dois = [rascunho('a-1', 'obj-1', 'Trabalho impecável, parabéns.'), rascunho('a-2', 'obj-2', 'Que orgulho desse time! 🎉')];
    backend.on('GET', /^\/api\/approvals$/, () => json(dois));
    backend.on('POST', /^\/api\/approvals\/decide$/, (call) => json({
      decided: dois, refused: [{ id: (call.body as { decisions: { id: string }[] }).decisions[0]!.id, reason: 'already_decided' }],
    }));

    const el = await render(<Textos detail={makeRunDetail()} />);
    await waitFor(() => expect(text(el)).toContain('Trabalho impecável'));

    const busca = backend.callsTo('GET', /approvals$/)[0];
    expect(busca?.query.get('run_id')).toBe(RUN_ID);
    expect(busca?.query.get('status')).toBe('pending');

    // cada rascunho aparece ao lado do aparelho que vai escrevê-lo
    const cartoes = Array.from(el.querySelectorAll('li'));
    expect(text(cartoes[0]!)).toContain('android-01');
    expect(text(cartoes[1]!)).toContain('android-02');
    const areas = Array.from(el.querySelectorAll('textarea'));
    expect(areas.map((a) => a.value)).toEqual(['Trabalho impecável, parabéns.', 'Que orgulho desse time! 🎉']);

    // edito o primeiro e descarto o segundo: a decisão de cada um é independente
    await setValue(areas[0]!, 'Trabalho impecável — parabéns a toda a equipe.');
    await click(byRole('button', /Não enviar este/, cartoes[1]!));
    expect(text(el)).toContain('1 para enviar');
    expect(text(el)).toContain('1 editado(s)');
    // `readOnly`, não `disabled`: quem usa leitor de tela continua podendo ler o texto que vai ser descartado
    expect(el.querySelectorAll('textarea')[1]!.readOnly).toBe(true);

    await click(byRole('button', /Decidir os 2 de uma vez/, el));
    await waitFor(() => expect(backend.callsTo('POST', /approvals\/decide$/)).toHaveLength(1));
    const enviado = backend.callsTo('POST', /approvals\/decide$/)[0]?.body as { decisions: unknown[] };
    expect(enviado.decisions).toEqual([
      { id: 'a-1', verb: 'edit', content: 'Trabalho impecável — parabéns a toda a equipe.' },
      { id: 'a-2', verb: 'reject' },
    ]);

    // o que o backend recusou é dito, não engolido
    await waitFor(() => expect(text(el)).toContain('already_decided'));
  });

  it('esvaziar a caixa não vira "aprovar o texto original": o lote fica travado até resolver', async () => {
    // O verbo é inferido do estado da caixa. Sem esta guarda, apagar o texto cairia no mesmo ramo de "nada mudou"
    // e o aparelho digitaria exatamente a frase que a pessoa apagou — sem nenhum sinal na tela.
    const um = [rascunho('a-1', 'obj-1', 'Trabalho impecável, parabéns.')];
    backend.on('GET', /^\/api\/approvals$/, () => json(um));
    backend.on('POST', /^\/api\/approvals\/decide$/, () => json({ decided: um, refused: [] }));

    const el = await render(<Textos detail={makeRunDetail()} />);
    await waitFor(() => expect(text(el)).toContain('Trabalho impecável'));
    await setValue(el.querySelector('textarea')!, '   ');

    expect(text(el)).toContain('em branco');
    const botao = byRole('button', /Decidir o/, el);
    expect(botao.getAttribute('aria-disabled')).toBe('true');
    await click(botao);
    expect(backend.callsTo('POST', /approvals\/decide$/)).toHaveLength(0);
  });

  it('aprovação sem texto (seguir) não ganha caixa de escrever nem é contada como texto', async () => {
    // Digitar numa caixa dessas viraria guarda de commit de uma etapa que não escreve nada — guarda que a tela
    // nunca satisfaz, e a etapa morre depois de tentar.
    const seguir = { ...rascunho('a-2', 'obj-2', ''), capability: 'FOLLOW', generated_content: null, content: null };
    backend.on('GET', /^\/api\/approvals$/, () => json([rascunho('a-1', 'obj-1', 'Que post lindo!'), seguir]));

    const el = await render(<Textos detail={makeRunDetail()} />);
    await waitFor(() => expect(text(el)).toContain('Que post lindo!'));

    expect(el.querySelectorAll('textarea')).toHaveLength(1);        // só o que escreve tem caixa
    expect(text(el)).toContain('1 texto(s) desta execução');        // e a contagem não conta o follow
    expect(text(el)).toContain('1 ação(ões) sem texto');
    expect(text(el)).toContain('não escreve nada, só precisa do seu aval');
  });

  it('trocar de aba não apaga o que a pessoa já reescreveu', async () => {
    // O RunView monta só a aba ativa, então a aba é DESMONTADA ao sair dela. Quem reescreveu oito textos não pode
    // perdê-los por ter ido conferir uma evidência — por isso o texto em edição mora no gancho, que fica acima.
    function ComoNoRunView({ detail, aberta }: { detail: RunDetail; aberta: boolean }) {
      const approvals = useRunApprovals(detail.id);
      return aberta ? <TextsTab detail={detail} approvals={approvals} /> : <p>outra aba</p>;
    }
    backend.on('GET', /^\/api\/approvals$/, () => json([rascunho('a-1', 'obj-1', 'Que post lindo!')]));

    const el = await render(<ComoNoRunView detail={makeRunDetail()} aberta />);
    await waitFor(() => expect(text(el)).toContain('Que post lindo!'));
    await setValue(el.querySelector('textarea')!, 'Escrevi do meu jeito.');

    await render(<ComoNoRunView detail={makeRunDetail()} aberta={false} />);
    expect(text(el)).toContain('outra aba');
    await render(<ComoNoRunView detail={makeRunDetail()} aberta />);

    await waitFor(() => expect(el.querySelector('textarea')!.value).toBe('Escrevi do meu jeito.'));
  });

  it('erro do backend mostra a causa, não só a dica', async () => {
    backend.on('GET', /^\/api\/approvals$/, () => apiError(500, 'db_locked', 'database is locked'));
    const el = await render(<Textos detail={makeRunDetail()} />);
    await waitFor(() => expect(text(el)).toContain('database is locked'));
  });

  it('sem rascunhos pendentes, explica quando eles aparecem em vez de mostrar uma lista vazia', async () => {
    backend.on('GET', /^\/api\/approvals$/, () => json([]));
    const el = await render(<Textos detail={makeRunDetail()} />);
    await waitFor(() => expect(text(el)).toContain('Nenhum texto aguardando aprovação'));
    expect(el.querySelectorAll('textarea')).toHaveLength(0);
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
