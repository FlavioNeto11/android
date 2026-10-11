// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Approval, Evidence, RunDetail, UsageReport } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { ACTION, APPS, ATTEMPT, RUN_ID, makeRunDetail } from '../../test/fixtures';
import {
  FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, openDetails, setValue, text, waitFor,
} from '../../test/harness';
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

describe('Por aparelho — selos de receita e rodízio', () => {
  it('linha do objetivo pendente mostra "aguardando vaga (k/K ligados)" em vez da próxima etapa', async () => {
    const el = await render(<InstancesTab detail={detailWithRecipes()} />);
    const row = byRole('button', /android-02/, el);
    expect(text(row)).toContain('aguardando vaga (3/3 ligados)');
    expect(text(row)).toContain('Pendente');
    expect(text(row)).not.toContain('Abrir o QA Messenger');
    // quem não espera vaga continua mostrando a etapa atual
    expect(text(byRole('button', /android-01/, el))).toContain('Enviar a mensagem');
  });

  it('31.350 (B): o estado da etapa tem trecho próprio que não trunca; só o título trunca (no celular o "Falhou" saía da linha)', async () => {
    const el = await render(<InstancesTab detail={detailWithRecipes()} />);
    const row = byRole('button', /android-01/, el);
    const estado = row.querySelector('[class*="objStepEstado"]') as HTMLElement;
    const titulo = row.querySelector('[class*="objStepTitulo"]') as HTMLElement;
    expect(estado).not.toBeNull();
    expect(titulo).not.toBeNull();
    expect(text(titulo)).toContain('Enviar a mensagem');
    expect(text(estado)).toMatch(/^· \S+/);                    // o estado fica fora do título
    expect(titulo.classList.contains('truncate')).toBe(true);
    expect(estado.closest('.truncate')).toBeNull();            // e fora de qualquer trecho truncado
    expect(text(estado)).not.toContain('Enviar a mensagem');
    // o objetivo que espera vaga não tem etapa na linha: segue como antes
    expect(byRole('button', /android-02/, el).querySelector('[class*="objStepEstado"]')).toBeNull();
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

describe('App de cada etapa — comando entre apps (item 24.6, ADR-058)', () => {
  /** A etapa "send" de android-01 roda em outro app (`notes`) — o plano inteiro continua sendo `qa`. */
  function detailComOutroApp(): RunDetail {
    const base = makeRunDetail();
    const [openApp, send, openApp2, send2] = base.steps;
    return { ...base, steps: [openApp!, { ...send!, app_id: 'notes' }, openApp2!, send2!] };
  }

  it('cabeçalho da etapa em outro app ganha o selo "app: <nome>"; a do app do plano, não', async () => {
    useAppStore.setState({ apps: APPS });
    const el = await render(<InstancesTab detail={detailComOutroApp()} />);
    await click(byRole('button', /android-01/, el));
    const steps01 = allByRole('button', /.*/, el)
      .filter((b) => b.getAttribute('aria-controls')?.startsWith(`step-body-${RUN_ID}:android-01:`));
    expect(steps01).toHaveLength(2);
    expect(text(steps01[0]!)).not.toContain('app:');            // open_app: sem app_id próprio, é o do plano
    expect(text(steps01[1]!)).toContain('app: Notas');           // send: app_id = 'notes', diferente do plano ('qa')
  });

  it('"Detalhes técnicos" da etapa mostra o app resolvido — o da etapa quando há, senão o do plano', async () => {
    useAppStore.setState({ apps: APPS });
    const el = await render(<InstancesTab detail={detailComOutroApp()} />);
    await click(byRole('button', /android-01/, el));
    const sendBtn = allByRole('button', /.*/, el)
      .find((b) => b.getAttribute('aria-controls') === `step-body-${RUN_ID}:android-01:v1:send`);
    await click(sendBtn!);
    const openBtn = allByRole('button', /.*/, el)
      .find((b) => b.getAttribute('aria-controls') === `step-body-${RUN_ID}:android-01:v1:open_app`);
    await click(openBtn!);
    const corpoSend = document.getElementById(`step-body-${RUN_ID}:android-01:v1:send`)!;
    const corpoOpen = document.getElementById(`step-body-${RUN_ID}:android-01:v1:open_app`)!;
    await openDetails(/Detalhes técnicos/, corpoSend);
    await openDetails(/Detalhes técnicos/, corpoOpen);
    expect(text(corpoSend)).toContain('Notas');       // app da própria etapa
    expect(text(corpoOpen)).toContain('QA Messenger'); // sem app_id: cai no app do plano
  });
});

describe('Marcar como concluído cita o print (ADR-055)', () => {
  /** android-01 incerto no envio (efeito externo): a DM que o verificador não conseguiu provar. */
  function detailIncerto(evidencias: Evidence[], comprovado = false): RunDetail {
    const base = makeRunDetail();
    const [openApp, send, ...resto] = base.steps;
    const result = comprovado
      ? { verified: false, evidence_text: 'publicado; o rótulo de IA não foi confirmado', efeito_comprovado: true } : null;
    return {
      ...base,
      objectives: [{ ...base.objectives[0]!, status: 'uncertain', needs: null, blocked_reason: null,
                     status_detail: 'O efeito foi disparado, mas não foi possível comprová-lo' }, base.objectives[1]!],
      steps: [openApp!, { ...send!, status: 'uncertain', ...(result ? { result } : {}) }, ...resto],
      evidence: evidencias,
    };
  }
  const envio = `${RUN_ID}:android-01:v1:send`;
  const print = (id: number, over: Partial<Evidence> = {}): Evidence => ({
    id, run_id: RUN_ID, instance_id: 'android-01', step_id: envio, attempt_id: 'att-2', ts: '2026-09-19T21:00:00.000Z',
    kind: 'screenshot', note: 'Pós-condição NÃO comprovada', url: `/api/evidence/${id}`, redacted: false, ...over,
  });

  it('cita o print mais recente da etapa parada e manda o id dele junto da decisão', async () => {
    backend.on('POST', /\/objectives\/[^/]+\/resolve$/, () => json({ ...makeRunDetail().objectives[0]!, status: 'running' }));
    const el = await render(<><InstancesTab detail={detailIncerto([
      print(10), print(11), print(12, { url: null, redacted: true }), print(13, { kind: 'verifier', url: null }),
      print(14, { step_id: `${RUN_ID}:android-01:v1:open_app` }),
    ])} /><ConfirmHost /></>);
    await click(byRole('button', /Marcar como concluído/, el));
    const dialogo = await waitFor(() => byRole('dialog', /Marcar como concluído\?/));
    expect(text(dialogo)).toContain('print #11');           // o último print COM imagem da etapa a confirmar
    expect(backend.callsTo('POST', /\/resolve$/)).toHaveLength(0);
    await click(byRole('button', /Sim, está concluído/, dialogo));
    await waitFor(() => expect(backend.callsTo('POST', /\/resolve$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /\/resolve$/)[0]?.body as { resolution: string; evidence_id?: number };
    expect(corpo).toMatchObject({ resolution: 'confirm_done', evidence_id: 11 });
  });

  it('29.79 (d): efeito comprovado (publicou, o rótulo não se confirmou) esconde "Tentar novamente" e diz por quê', async () => {
    // Os botões "Tentar novamente…" da tela: o do android-01 (incerto) e o do android-02 (aguardando você).
    const repetir = (raiz: HTMLElement) =>
      [...raiz.querySelectorAll('button')].filter((b) => text(b).includes('Tentar novamente…')).length;
    const comum = await render(<><InstancesTab detail={detailIncerto([print(11)])} /><ConfirmHost /></>);
    expect(repetir(comum)).toBe(2);
    const el = await render(<><InstancesTab detail={detailIncerto([print(11)], true)} /><ConfirmHost /></>);
    expect(repetir(el)).toBe(1);                         // só o do android-02 fica
    expect(text(el)).toContain('O efeito já saiu e foi comprovado');
    expect(text(el)).toContain('Marcar como concluído');
    expect(text(el)).toContain('Abandonar');
  });

  it('etapa com efeito externo sem print avisa que a confirmação será recusada e não inventa um id', async () => {
    backend.on('POST', /\/objectives\/[^/]+\/resolve$/, () => apiError(422, 'evidence_required', 'indique o print'));
    const el = await render(<><InstancesTab detail={detailIncerto([print(12, { url: null, redacted: true })])} /><ConfirmHost /></>);
    await click(byRole('button', /Marcar como concluído/, el));
    const dialogo = await waitFor(() => byRole('dialog', /Marcar como concluído\?/));
    expect(text(dialogo)).toContain('não há print');
    await click(byRole('button', /Sim, está concluído/, dialogo));
    await waitFor(() => expect(backend.callsTo('POST', /\/resolve$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /\/resolve$/)[0]?.body as { evidence_id?: number };
    expect(corpo.evidence_id).toBeUndefined();
  });
});

describe('Textos — os N rascunhos da execução, lidos e decididos juntos', () => {
  function rascunho(id: string, objetivo: string, conteudo: string): Approval {
    return {
      id, profile_id: `p-${id}`, run_id: RUN_ID, objective_id: objetivo, step_id: `${RUN_ID}:x:v1:comment`,
      capability: 'CREATE_COMMENT', target: '@secretaria', summary: 'Comentar na publicação',
      generated_content: conteudo, approved_content: null, content: conteudo,
      status: 'pending', created_at: '2026-09-17T12:00:00.000Z', decided_at: null, decided_note: null,
      decided_by: null, interaction_id: null,
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

  it('29.30: a publicação mostra a imagem que vai ao feed ao lado da legenda; o comentário não ganha imagem', async () => {
    const post = { ...rascunho('a-3', 'obj-3', 'Fim de tarde'), capability: 'CREATE_POST', target: null,
                   summary: 'Publicar a imagem no feed', image_id: 'img-9', rotulo_ia: true };
    backend.on('GET', /^\/api\/approvals$/, () => json([rascunho('a-1', 'obj-1', 'Que post lindo!'), post]));

    const el = await render(<Textos detail={makeRunDetail()} />);
    await waitFor(() => expect(text(el)).toContain('Fim de tarde'));

    const imagens = el.querySelectorAll('img');
    expect(imagens).toHaveLength(1);
    expect(imagens[0]?.getAttribute('src')).toBe('/api/personas/p-a-3/images/img-9');
    expect(imagens[0]?.getAttribute('alt')).toContain('Imagem que será publicada');
    expect(text(el)).toContain('com rótulo de IA');             // 29.79: o rótulo de IA aparece só na publicação
    expect(text(el).match(/com rótulo de IA/g)).toHaveLength(1);
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

  // Item 18.3: o formato de `taskqueue/projecao.py::projetar`, com a janela efetiva menor que a configurada.
  const PROJECAO = {
    janela_dias: 14, janela_configurada: 30, minimo_de_amostras: 5, amostras_sem_custo: 3,
    chamadas: { p50: 10, p90: 17 }, segundos: { p50: 180, p90: 420 }, usd: { p50: 0.2, p90: 0.38 }, sem_base: ['curtir'],
    etapas: [
      { key: 'abrir', title: 'Abrir o perfil', action: 'abrir_perfil', samples: 12, calls: { p50: 4, p90: 7 },
        seconds: { p50: 60, p90: 150 }, usd: { p50: 0.08, p90: 0.15 }, no_baseline: false, samples_without_cost: 3 },
      { key: 'curtir', title: 'Curtir', action: '*', samples: 2, calls: { p50: 6, p90: 10 },
        seconds: { p50: 120, p90: 270 }, usd: { p50: 0.12, p90: 0.23 }, no_baseline: true, samples_without_cost: 0 },
    ],
  };

  it('mostra o normal medido do plano ao lado do custo, com o rótulo da janela efetiva', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json(REPORT));
    backend.on('GET', /^\/api\/runs\/[^/]+\/projection$/, () => json(PROJECAO));
    const el = await render(<RunUsageCard run={{ id: RUN_ID, status: 'running' }} />);
    expect(backend.callsTo('GET', /projection$/)).toHaveLength(0);           // recolhido: nada lido ainda
    await openDetails(/Custo de IA desta execução/, el);
    await waitFor(() => expect(text(el)).toContain('Normal medido para este plano'));
    expect(backend.callsTo('GET', /projection$/)[0]?.path).toBe(`/api/runs/${RUN_ID}/projection`);
    expect(text(el)).toContain('Normal medido nos últimos 14 dias — a janela configurada é de 30 dias, limitada pela '
                               + 'retenção dos registros de IA');
    expect(text(el)).toContain('10–17');
    expect(text(el)).toContain('US$ 0,20–0,38');
    expect(text(el)).toContain('3–7 min');
    expect(text(el)).toContain('1 etapa sem base própria (menos de 5 amostras da ação)');
    expect(text(el)).toContain('3 amostras não fizeram chamada de IA');
    // O custo real continua lá, e a tabela por etapa só monta quando aberta.
    await waitFor(() => expect(text(el)).toContain('US$ 0,0868'));
    expect(el.querySelectorAll('tbody tr')).toHaveLength(2);
    await openDetails(/Por etapa/, el);
    await waitFor(() => expect(el.querySelectorAll('tbody tr')).toHaveLength(4));
    expect(text(el)).toContain('Curtir · sem base própria');
  });

  it('409 no_plan não é erro: diz que a projeção vem com o plano, e relê quando a situação muda', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json(REPORT));
    backend.on('GET', /^\/api\/runs\/[^/]+\/projection$/, () => apiError(409, 'no_plan', 'A execução ainda não tem plano.'));
    const el = await render(<RunUsageCard run={{ id: RUN_ID, status: 'planning' }} />);
    await openDetails(/Custo de IA/, el);
    await waitFor(() => expect(text(el)).toContain('A execução ainda não tem plano: a projeção aparece quando ele ficar pronto.'));
    expect(text(el)).not.toContain('Projeção indisponível');
    backend.on('GET', /^\/api\/runs\/[^/]+\/projection$/, () => json(PROJECAO));
    await render(<RunUsageCard run={{ id: RUN_ID, status: 'planned' }} />);
    await waitFor(() => expect(text(el)).toContain('US$ 0,20–0,38'));
    expect(backend.callsTo('GET', /projection$/)).toHaveLength(2);
  });

  it('sem histórico: diz que a primeira execução mede, sem inventar número', async () => {
    backend.on('GET', /^\/api\/usage$/, () => json(REPORT));
    backend.on('GET', /^\/api\/runs\/[^/]+\/projection$/, () => json({
      ...PROJECAO, janela_dias: 30, chamadas: { p50: 0, p90: 0 }, usd: { p50: 0, p90: 0 }, sem_base: ['abrir', 'curtir'],
    }));
    const el = await render(<RunUsageCard run={{ id: RUN_ID, status: 'planned' }} />);
    await openDetails(/Custo de IA/, el);
    await waitFor(() => expect(text(el)).toContain('Sem histórico suficiente para projetar as 2 etapas — a primeira execução mede.'));
    expect(text(el)).toContain('Normal medido nos últimos 30 dias.');
    expect(text(el)).not.toContain('limitada pela retenção');
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

describe('Repetir execução (11.5)', () => {
  it('cria execução nova com o mesmo comando, os mesmos aparelhos e chave de idempotência nova', async () => {
    const { repeatRun } = await import('./runActions');
    backend.on('POST', /^\/api\/runs$/, (call) => json({ ...makeRunDetail(), id: 'r-nova', short_id: 'nova', body: call.body }));
    const original = { id: RUN_ID, short_id: 'orig', command: 'abra o QA Messenger', instance_ids: ['android-01', 'android-02'] };
    const nova = await repeatRun(original);
    expect(nova?.id).toBe('r-nova');
    const [chamada] = backend.callsTo('POST', /^\/api\/runs$/);
    const corpo = chamada?.body as { command: string; instance_ids: string[]; mode: string; idempotency_key: string };
    expect(corpo.command).toBe('abra o QA Messenger');
    expect(corpo.instance_ids).toEqual(['android-01', 'android-02']);
    expect(corpo.mode).toBe('execute');
    expect(corpo.idempotency_key).toMatch(new RegExp(`^repetir-${RUN_ID}-\\d+$`));
  });
});
