// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { RunDetail, StepStatus, TeachingSessionSummary, TeachingSessionView } from '../../api/types';
import { useAppStore } from '../../store/app';
import { RUN_ID, makeRunDetail, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { origemDaEtapa } from './CorrigirEtapa';
import { InstancesTab } from './InstancesTab';

/**
 * Plano 22.7: a correção de ensino nasce na visão da execução (aba Por aparelho). Só a etapa `failed`/`uncertain` que
 * veio de uma habilidade ganha "Corrigir esta etapa"; enviar acha (ou abre) o ensino da habilidade e posta a correção
 * com a execução e a LINHA de `steps`. Prova `simulated` (backend falso).
 */

const ORIGEM = { skill_id: 'qa.abrir_conversa', skill_version: 3, node_id: 'open_app' };
const ETAPA = `${RUN_ID}:android-01:v1:open_app`;
const INSTRUCAO = 'Corrigir a habilidade qa.abrir_conversa (versão 3).';

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

function ligarHabilidades(ligado = true): void {
  const health = makeSnapshot().health;
  useAppStore.setState({ health: { ...health, features: { ...health.features, skills: ligado } } });
}

/** A execução de sempre, com a etapa `open_app` do android-01 no estado pedido e (ou não) vinda da habilidade. */
function detalhe(status: StepStatus, comOrigem = true): RunDetail {
  const base = makeRunDetail();
  const passos = base.plan_versions[0]!.steps.map((p) => (p.key === 'open_app' && comOrigem ? { ...p, origin: ORIGEM } : p));
  return {
    ...base,
    steps: base.steps.map((s) => (s.id === ETAPA ? { ...s, status, result: null } : s)),
    plan_versions: [{ ...base.plan_versions[0]!, steps: passos }],
  };
}

function visao(id: string, over: Partial<TeachingSessionView> = {}): TeachingSessionView {
  return {
    id, instruction: INSTRUCAO, skill_id: ORIGEM.skill_id, base_version: 3, app_id: 'qa', status: 'open',
    validation_status: 'none', result_version_id: null, source: 'instruction', created_at: '2026-09-29T10:00:00Z',
    updated_at: '2026-09-29T10:00:00Z', profile_id: null, operator: null, closed_at: null, demonstrations: [], turns: [],
    candidates: [], current_candidate: null, open_questions: [], errors: [], ...over,
  };
}

function resumo(id: string, skillId: string, baseVersion: number | null): TeachingSessionSummary {
  const { turns: _t, demonstrations: _d, candidates: _c, current_candidate: _cc, open_questions: _q, errors: _e, ...r } = visao(id);
  return { ...r, skill_id: skillId, base_version: baseVersion };
}

function correcao(runId: string, stepId: string) {
  return { id: 7, kind: 'correction' as const, author: 'person' as const, reply_to: null, target: { run_id: runId, step_id: stepId },
           body: 'texto', candidate_id: null, created_by: 'panel', created_at: '2026-09-29T10:00:00Z' };
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/teaching-sessions$/, () => json([]));
  backend.on('POST', /^\/api\/teaching-sessions$/, () => json(visao('ens-1'), 201));
  backend.on('POST', /^\/api\/teaching-sessions\/[^/]+\/corrections$/, (c) => json(visao(c.path.split('/')[3]!, {
    source: 'correction', turns: [correcao(RUN_ID, ETAPA)] })));
  ligarHabilidades();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

/** As linhas (recolhidas ou não) das etapas visíveis: o botão que abre o detalhe. */
const cabecas = () => Array.from(container.querySelectorAll<HTMLElement>('[aria-controls^="step-body-"]'));

/** Abre só o objetivo do android-01 (o que falhou não abre sozinho: só o bloqueado); as etapas ficam recolhidas. */
async function abrirObjetivo(d: RunDetail): Promise<void> {
  await act(async () => root.render(<InstancesTab detail={d} />));
  await click(byRole('button', /android-01/, container));
}

/** Abre o objetivo do android-01 e as duas etapas dele. */
async function abrir(d: RunDetail): Promise<void> {
  await abrirObjetivo(d);
  for (const b of cabecas()) {
    if (b.getAttribute('aria-controls')?.includes(':android-01:')) await click(b);
  }
}

async function remontar(): Promise<void> {
  await act(async () => root.unmount());
  root = createRoot(container);
}

const botoes = () => allByRole('button', /^Corrigir esta etapa/, container);
const marcadas = () => cabecas().filter((b) => text(b).includes('corrigível'));
const buscas = () => backend.callsTo('GET', /^\/api\/teaching-sessions$/);
const criacoes = () => backend.callsTo('POST', /^\/api\/teaching-sessions$/);
const correcoes = () => backend.callsTo('POST', /\/corrections$/);

/** Todo estado de etapa que NÃO se corrige (`teaching.py::CORRECTABLE_STEP` = failed, uncertain). */
const NAO_CORRIGIVEIS: StepStatus[] = ['pending', 'ready', 'running', 'verifying', 'succeeded', 'retry_wait',
                                       'waiting_user', 'cancelled', 'skipped'];

async function escreverEEnviar(textoDaCorrecao: string): Promise<void> {
  if (!container.querySelector('form')) await click(byRole('button', /^Corrigir esta etapa/, container));
  await setValue(byRole('textbox', /O que devia ter acontecido/, container) as HTMLTextAreaElement, textoDaCorrecao);
  await click(byRole('button', /^Enviar correção/, container));
}

describe('Motivo da persona no detalhe da etapa (31.65)', () => {
  it('a recusa da persona aparece só no detalhe da etapa que a teve', async () => {
    const d = detalhe('waiting_user', false);
    const motivo = 'o texto atribuía um recado a um terceiro';
    await abrir({ ...d, steps: d.steps.map((s) => (s.id === ETAPA ? { ...s, motivo_da_persona: motivo } : s)) });
    expect(text(container)).toContain('Motivo da persona');
    expect(text(container).split(motivo)).toHaveLength(2);           // uma vez: a outra etapa aberta não tem
  });

  it('sem recusa, nenhuma linha de motivo', async () => {
    await abrir(detalhe('waiting_user', false));
    expect(text(container)).not.toContain('Motivo da persona');
  });
});

describe('Corrigir esta etapa (plano 22.7)', () => {
  it('a origem vem do passo da mesma versão do plano do objetivo, pela key; sem ela, nenhuma', () => {
    const d = detalhe('failed');
    expect(origemDaEtapa(d, d.steps.find((s) => s.id === ETAPA)!)).toEqual(ORIGEM);
    expect(origemDaEtapa(d, d.steps.find((s) => s.key === 'send' && s.objective_id === 'obj-1')!)).toBeNull();
  });

  it.each<StepStatus>(['failed', 'uncertain'])('aparece na etapa %s que veio de habilidade — e só nela', async (status) => {
    await abrir(detalhe(status));
    expect(botoes()).toHaveLength(1);                       // a outra etapa aberta (send) não veio de habilidade
    expect(text(container)).toContain('Veio da habilidade qa.abrir_conversa (versão 3)');
    expect(document.querySelector('[role="dialog"], dialog, [aria-modal="true"]')).toBeNull();
  });

  it.each(NAO_CORRIGIVEIS)('não aparece na etapa %s, mesmo vinda de habilidade (nem a ação, nem a marca)', async (status) => {
    await abrir(detalhe(status));
    expect(botoes()).toHaveLength(0);
    expect(marcadas()).toHaveLength(0);
  });

  it('não aparece na etapa que não veio de habilidade, nem com as habilidades desligadas', async () => {
    await abrir(detalhe('failed', false));
    expect(botoes()).toHaveLength(0);
    expect(marcadas()).toHaveLength(0);
    await remontar();

    ligarHabilidades(false);
    await abrir(detalhe('failed'));
    expect(botoes()).toHaveLength(0);
    expect(marcadas()).toHaveLength(0);
    expect(backend.calls.filter((c) => c.path.startsWith('/api/teaching-sessions'))).toHaveLength(0);
  });

  it('a linha recolhida da etapa corrigível leva a marca "corrigível" (a ação mora no detalhe, fechado de início)', async () => {
    await abrirObjetivo(detalhe('failed'));
    expect(botoes()).toHaveLength(0);                       // nenhuma etapa aberta
    expect(marcadas().map((b) => b.getAttribute('aria-controls'))).toEqual([`step-body-${ETAPA}`]);
    await click(marcadas()[0]!);
    expect(botoes()).toHaveLength(1);                       // a marca promete o que o detalhe mostra
  });

  it('a origem é a do passo do MESMO objetivo e da MESMA versão, mesmo com outras versões antes na lista', async () => {
    // Iscas antes da entrada certa: sem casar o objetivo, acharia a do obj-2; sem casar a versão, a v2 do obj-1.
    const base = detalhe('failed');
    const certa = base.plan_versions[0]!;
    const comOrigem = (origem: typeof ORIGEM) => certa.steps.map((p) => (p.key === 'open_app' ? { ...p, origin: origem } : p));
    const d: RunDetail = {
      ...base,
      plan_versions: [
        { ...certa, objective_id: 'obj-2', steps: comOrigem({ skill_id: 'qa.do_outro_objetivo', skill_version: 7, node_id: 'open_app' }) },
        { ...certa, version: 2, reason: 'Replanejado', steps: comOrigem({ ...ORIGEM, skill_version: 4 }) },
        certa,
      ],
    };
    expect(origemDaEtapa(d, d.steps.find((s) => s.id === ETAPA)!)).toEqual(ORIGEM);

    await abrir(d);
    expect(botoes()).toHaveLength(1);
    expect(text(container)).toContain('Veio da habilidade qa.abrir_conversa (versão 3)');
    expect(text(container)).not.toContain('versão 4');
    expect(text(container)).not.toContain('qa.do_outro_objetivo');
    await escreverEEnviar('o contato certo é o QA-001');
    await waitFor(() => expect(correcoes()).toHaveLength(1));
    expect(criacoes()[0]?.body).toEqual({ instruction: INSTRUCAO, skill_id: 'qa.abrir_conversa', base_version: 3 });
  });

  it('envia: abre o ensino da habilidade (versão base) e posta a correção com a execução e a linha de steps', async () => {
    await abrir(detalhe('failed'));
    await click(byRole('button', /^Corrigir esta etapa/, container));
    expect(byRole('button', /^Enviar correção/, container).getAttribute('aria-disabled')).toBe('true');   // vazio não sai
    await escreverEEnviar('devia ter tocado no contato QA-001, não no primeiro da lista');
    await waitFor(() => expect(correcoes()).toHaveLength(1));

    const busca = backend.callsTo('GET', /^\/api\/teaching-sessions$/);
    expect(busca).toHaveLength(1);
    expect(busca[0]?.query.get('status')).toBe('open');
    expect(criacoes()).toHaveLength(1);
    expect(criacoes()[0]?.body).toEqual({ instruction: INSTRUCAO, skill_id: 'qa.abrir_conversa', base_version: 3 });
    expect(correcoes()[0]?.path).toBe('/api/teaching-sessions/ens-1/corrections');
    expect(correcoes()[0]?.body).toEqual({ body: 'devia ter tocado no contato QA-001, não no primeiro da lista',
                                           run_id: RUN_ID, step_id: ETAPA });
    await waitFor(() => expect(text(container)).toContain('Correção registrada no ensino da habilidade qa.abrir_conversa (versão 3)'));
    expect(text(container)).toContain('ens-1');
    expect(container.querySelector('form')).toBeNull();
  });

  it('foco e ligação acessível: abrir leva ao campo; enviar e cancelar devolvem ao botão (nunca ao body)', async () => {
    await abrir(detalhe('failed'));
    const alternar = byRole('button', /^Corrigir esta etapa/, container);
    await click(alternar);
    const form = container.querySelector('form')!;
    expect(form.id).not.toBe('');
    expect(alternar.getAttribute('aria-controls')).toBe(form.id);
    expect(alternar.getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(byRole('textbox', /O que devia ter acontecido/, container));

    await click(byRole('button', /^Cancelar/, container));
    expect(container.querySelector('form')).toBeNull();
    expect(document.activeElement).toBe(alternar);

    await escreverEEnviar('o botão certo é Enviar');
    await waitFor(() => expect(byRole('status', /Correção registrada/, container)).toBeTruthy());
    expect(container.querySelector('form')).toBeNull();
    expect(document.activeElement).toBe(alternar);
  });

  it('o aviso de sucesso do envio anterior some quando o envio seguinte falha', async () => {
    await abrir(detalhe('failed'));
    await escreverEEnviar('primeira correção');
    await waitFor(() => expect(text(container)).toContain('Correção registrada'));

    backend.on('POST', /\/corrections$/, () => apiError(400, 'step_not_correctable', "A etapa está 'succeeded'."));
    await escreverEEnviar('segunda correção');
    await waitFor(() => expect(byRole('alert', /só essas se corrigem/, container)).toBeTruthy());
    expect(text(container)).not.toContain('Correção registrada');
  });

  it('reaproveita o ensino aberto da mesma habilidade que já corrige esta execução', async () => {
    backend.on('GET', /^\/api\/teaching-sessions$/, () => json([
      resumo('ens-outra', 'qa.outra_coisa', 3), resumo('ens-v2', ORIGEM.skill_id, 2), resumo('ens-9', ORIGEM.skill_id, 3),
    ]));
    backend.on('GET', /^\/api\/teaching-sessions\/ens-9$/, () => json(visao('ens-9', { turns: [correcao(RUN_ID, `${RUN_ID}:android-02:v1:open_app`)] })));
    await abrir(detalhe('uncertain'));
    await escreverEEnviar('a prova certa é a conversa aberta');
    await waitFor(() => expect(correcoes()).toHaveLength(1));
    expect(criacoes()).toHaveLength(0);
    expect(correcoes()[0]?.path).toBe('/api/teaching-sessions/ens-9/corrections');
    // outra habilidade e outra versão base nem são abertas
    expect(backend.callsTo('GET', /^\/api\/teaching-sessions\/ens-(outra|v2)$/)).toHaveLength(0);
  });

  it('reaproveita o ensino vazio que esta ação abriu (a correção falhou depois de abrir), sem abrir outro', async () => {
    backend.on('GET', /^\/api\/teaching-sessions$/, () => json([resumo('ens-vazio', ORIGEM.skill_id, 3)]));
    backend.on('GET', /^\/api\/teaching-sessions\/ens-vazio$/, () => json(visao('ens-vazio')));
    await abrir(detalhe('failed'));
    await escreverEEnviar('o contato certo é o QA-001');
    await waitFor(() => expect(correcoes()).toHaveLength(1));
    expect(criacoes()).toHaveLength(0);
    expect(correcoes()[0]?.path).toBe('/api/teaching-sessions/ens-vazio/corrections');
  });

  it('o que já corrige esta execução vence o vazio desta ação, mesmo vindo depois na lista', async () => {
    backend.on('GET', /^\/api\/teaching-sessions$/, () => json([resumo('ens-vazio', ORIGEM.skill_id, 3),
                                                              resumo('ens-9', ORIGEM.skill_id, 3)]));
    backend.on('GET', /^\/api\/teaching-sessions\/ens-vazio$/, () => json(visao('ens-vazio')));
    backend.on('GET', /^\/api\/teaching-sessions\/ens-9$/, () => json(visao('ens-9', { turns: [correcao(RUN_ID, ETAPA)] })));
    await abrir(detalhe('failed'));
    await escreverEEnviar('o contato certo é o QA-001');
    await waitFor(() => expect(correcoes()).toHaveLength(1));
    expect(criacoes()).toHaveLength(0);
    expect(correcoes()[0]?.path).toBe('/api/teaching-sessions/ens-9/corrections');
  });

  it.each<[string, Partial<TeachingSessionView>]>([
    ['com outra instrução (um ensino da pessoa)', { instruction: 'Ensinar a abrir a conversa pelo contato certo' }],
    ['com uma demonstração', { demonstrations: [{ id: 'dem-1', seq: 1, kind: 'recording', training_session_id: 'trn-1', run_id: null }] }],
  ])('o ensino aberto sem correção mas %s não é o vazio desta ação: abre um novo', async (_caso, over) => {
    backend.on('GET', /^\/api\/teaching-sessions$/, () => json([resumo('ens-alheio', ORIGEM.skill_id, 3)]));
    backend.on('GET', /^\/api\/teaching-sessions\/ens-alheio$/, () => json(visao('ens-alheio', over)));
    await abrir(detalhe('failed'));
    await escreverEEnviar('o contato certo é o QA-001');
    await waitFor(() => expect(correcoes()).toHaveLength(1));
    expect(criacoes()).toHaveLength(1);
    expect(correcoes()[0]?.path).toBe('/api/teaching-sessions/ens-1/corrections');
  });

  it('o ensino aberto de outra execução não serve: abre um novo', async () => {
    backend.on('GET', /^\/api\/teaching-sessions$/, () => json([resumo('ens-9', ORIGEM.skill_id, 3)]));
    backend.on('GET', /^\/api\/teaching-sessions\/ens-9$/, () => json(visao('ens-9', { turns: [correcao('run-outra', 'run-outra:android-01:v1:open_app')] })));
    await abrir(detalhe('failed'));
    await escreverEEnviar('o botão é Enviar');
    await waitFor(() => expect(correcoes()).toHaveLength(1));
    expect(criacoes()).toHaveLength(1);
    expect(correcoes()[0]?.path).toBe('/api/teaching-sessions/ens-1/corrections');
  });

  it('texto com credencial (400): aviso em linha em português, o texto fica no campo e o reenvio usa o MESMO ensino', async () => {
    backend.on('POST', /\/corrections$/, () => apiError(400, 'credential_in_text', 'A correção contém uma credencial.'));
    await abrir(detalhe('failed'));
    await escreverEEnviar('texto que o servidor recusou');
    const aviso = await waitFor(() => byRole('alert', /parece conter uma senha, um código ou uma chave/, container));
    // O ensino já foi aberto um pedido antes: o que não foi gravado é a correção, não "nada".
    expect(text(aviso)).toContain('não foi gravada');
    expect(text(aviso)).not.toContain('nada foi registrado');
    expect((byRole('textbox', /O que devia ter acontecido/, container) as HTMLTextAreaElement).value).toBe('texto que o servidor recusou');
    expect(text(container)).not.toContain('Correção registrada');

    backend.on('POST', /\/corrections$/, () => json(visao('ens-1', { turns: [correcao(RUN_ID, ETAPA)] })));
    await escreverEEnviar('texto sem o segredo');
    await waitFor(() => expect(text(container)).toContain('Correção registrada'));
    expect(buscas()).toHaveLength(1);                       // o reenvio nem procurou: usou o ensino guardado
    expect(criacoes()).toHaveLength(1);                     // não deixou um ensino vazio para trás
    expect(correcoes().map((c) => c.path)).toEqual(['/api/teaching-sessions/ens-1/corrections',
                                                   '/api/teaching-sessions/ens-1/corrections']);
  });

  it.each([
    ['409 teaching_state', () => apiError(409, 'teaching_state', "O ensino está 'asking'."), 'seguiu adiante'],
    ['404', () => apiError(404, 'not_found', 'Ensino não encontrado: ens-1.'), 'o ensino não existe mais'],
  ] as const)('depois de %s na correção, o reenvio esquece o ensino guardado: procura e abre de novo', async (_caso, falha, aviso) => {
    let n = 0;
    backend.on('POST', /^\/api\/teaching-sessions$/, () => json(visao(`ens-${++n}`), 201));
    backend.on('POST', /\/corrections$/, falha);
    await abrir(detalhe('failed'));
    await escreverEEnviar('o contato certo é o QA-001');
    await waitFor(() => expect(allByRole('alert', /./, container).map((a) => text(a)).join(' | ')).toContain(aviso));
    expect([buscas().length, criacoes().length]).toEqual([1, 1]);

    backend.on('POST', /\/corrections$/, (c) => json(visao(c.path.split('/')[3]!, { turns: [correcao(RUN_ID, ETAPA)] })));
    await escreverEEnviar('o contato certo é o QA-001');
    await waitFor(() => expect(text(container)).toContain('Correção registrada'));
    expect([buscas().length, criacoes().length]).toEqual([2, 2]);
    expect(correcoes().map((c) => c.path)).toEqual(['/api/teaching-sessions/ens-1/corrections',
                                                   '/api/teaching-sessions/ens-2/corrections']);
  });

  it.each([
    ['corrections', apiError(400, 'step_not_correctable', "A etapa está 'succeeded'."), 'só essas se corrigem'],
    ['corrections', apiError(409, 'teaching_state', "O ensino está 'asking'."), 'outro ensino aberto desta habilidade, ou para um novo'],
    ['corrections', apiError(404, 'not_found', 'Etapa x não é da execução y.'), 'A etapa não foi encontrada nesta execução'],
    ['corrections', json({ detail: [{ loc: ['body', 'body'], msg: 'String should have at most 2000 characters' }] }, 422), 'de 1 a 2000 caracteres'],
    ['start', apiError(404, 'not_found', 'Habilidade não encontrada: qa.abrir_conversa.'), 'não existe mais neste servidor'],
    ['start', apiError(404, 'skills_disabled', 'As habilidades versionadas estão desligadas.'), 'As habilidades estão desligadas neste servidor'],
  ] as const)('erro em %s → mensagem em português, sem jargão (%#)', async (onde, resposta, esperado) => {
    if (onde === 'start') backend.on('POST', /^\/api\/teaching-sessions$/, () => resposta.clone());
    else backend.on('POST', /\/corrections$/, () => resposta.clone());
    await abrir(detalhe('failed'));
    await escreverEEnviar('o contato certo é outro');
    // o objetivo do android-02 (bloqueado) tem o seu próprio alerta: procura-se o aviso entre todos
    await waitFor(() => expect(allByRole('alert', /./, container).map((a) => text(a)).join(' | ')).toContain(esperado));
    expect(text(container)).not.toContain('Correção registrada');
  });
});
