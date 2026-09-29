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

/** Abre o objetivo do android-01 (o que falhou não abre sozinho: só o bloqueado) e as duas etapas dele. */
async function abrir(d: RunDetail): Promise<void> {
  await act(async () => root.render(<InstancesTab detail={d} />));
  await click(byRole('button', /android-01/, container));
  for (const b of Array.from(container.querySelectorAll<HTMLElement>('[aria-controls^="step-body-"]'))) {
    if (b.getAttribute('aria-controls')?.includes(':android-01:')) await click(b);
  }
}

const botoes = () => allByRole('button', /^Corrigir esta etapa/, container);
const criacoes = () => backend.callsTo('POST', /^\/api\/teaching-sessions$/);
const correcoes = () => backend.callsTo('POST', /\/corrections$/);

async function escreverEEnviar(textoDaCorrecao: string): Promise<void> {
  if (!container.querySelector('form')) await click(byRole('button', /^Corrigir esta etapa/, container));
  await setValue(byRole('textbox', /O que devia ter acontecido/, container) as HTMLTextAreaElement, textoDaCorrecao);
  await click(byRole('button', /^Enviar correção/, container));
}

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

  it('não aparece na etapa comprovada, na que não veio de habilidade, nem com as habilidades desligadas', async () => {
    await abrir(detalhe('succeeded'));
    expect(botoes()).toHaveLength(0);
    await act(async () => root.unmount());
    root = createRoot(container);

    await abrir(detalhe('failed', false));
    expect(botoes()).toHaveLength(0);
    await act(async () => root.unmount());
    root = createRoot(container);

    ligarHabilidades(false);
    await abrir(detalhe('failed'));
    expect(botoes()).toHaveLength(0);
    expect(backend.calls.filter((c) => c.path.startsWith('/api/teaching-sessions'))).toHaveLength(0);
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
    await waitFor(() => expect(byRole('alert', /parece conter uma senha, um código ou uma chave/, container)).toBeTruthy());
    expect((byRole('textbox', /O que devia ter acontecido/, container) as HTMLTextAreaElement).value).toBe('texto que o servidor recusou');
    expect(text(container)).not.toContain('Correção registrada');

    backend.on('POST', /\/corrections$/, () => json(visao('ens-1', { turns: [correcao(RUN_ID, ETAPA)] })));
    await escreverEEnviar('texto sem o segredo');
    await waitFor(() => expect(text(container)).toContain('Correção registrada'));
    expect(criacoes()).toHaveLength(1);                     // não deixou um ensino vazio para trás
    expect(correcoes().map((c) => c.path)).toEqual(['/api/teaching-sessions/ens-1/corrections',
                                                   '/api/teaching-sessions/ens-1/corrections']);
  });

  it.each([
    ['corrections', apiError(400, 'step_not_correctable', "A etapa está 'succeeded'."), 'só essas se corrigem'],
    ['corrections', apiError(409, 'teaching_state', "O ensino está 'asking'."), 'um ensino novo será aberto'],
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
