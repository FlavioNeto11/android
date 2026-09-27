// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { ConfirmHost } from '../../components/Confirm';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { TrainingReview } from './TrainingReview';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const SESSAO = {
  id: 'trn-1', instance_id: 'android-01', profile_id: 'ig-1', app_id: 'qa-messenger', intent: 'Mandar mensagem',
  status: 'recorded', operator: null, proposal: null, flow_id: null, created_at: '', finished_at: '', updated_at: '',
  inputs: [
    { session_id: 'trn-1', seq: 1, ts: '', type: 'tap', x: 10, y: 10, x2: null, y2: null, key_name: null, text: null,
      has_text: false, text_len: null, package: 'com.pocqa.messenger', app_id: null,
      target: { text: 'QA-001', resource_id: 'x:id/conversation_name', unique: ['rid+text'] }, screen_title: 'Conversas',
      screen_lines: ['QA-001'], sensitive: false },
    { session_id: 'trn-1', seq: 2, ts: '', type: 'text', x: null, y: null, x2: null, y2: null, key_name: null, text: null,
      has_text: true, text_len: 12, package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null,
      screen_lines: [], sensitive: false },
  ],
};

const PROPOSTA = {
  summary: 'Mandar mensagem', command_template: 'mande para {contato}', app_id: 'qa-messenger',
  parameters: [{ name: 'contato', example: 'QA-001', description: '' }], discarded: [], questions: ['O texto muda?'],
  steps: [{ key: 'abrir', title: 'Abrir a conversa com {contato}', goal: 'abrir', inputs: [1], side_effect: false,
            capability: null, bindings: [], app_id: null,
            postcondition: { kind: 'text_visible', value: '{contato}', description: 'aberta' } }],
};

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training\/trn-1$/, () => json(SESSAO));
  backend.on('GET', /\/instagram\/profiles$/, () => json([{ id: 'ig-1', username: 'aluno.um' }, { id: 'ig-2', username: 'aluno.dois' }]));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...SESSAO, status: 'proposed', proposal: PROPOSTA }));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem',
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }],
  }));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
});

it('mostra a gravação (texto sigiloso sem conteúdo), pede a proposta, e salva o fluxo com o escopo escolhido', async () => {
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('QA-001'));
  expect(text()).toContain('(sigiloso)');
  await click(byRole('button', /Pedir proposta à IA/i));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  expect(text()).toContain('{contato} = QA-001');
  await click(byRole('checkbox', /@aluno.dois/i));
  // P2.1: o rodapé salva um FLUXO e diz isso; "habilidade" fica para a versionada (ensino v2).
  expect(allByRole('button', /Salvar habilidade/i)).toHaveLength(0);
  await click(byRole('button', /Salvar como fluxo/i));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  const corpo = backend.callsTo('POST', /\/save$/)[0]!.body as { profile_ids: string[] };
  expect(corpo.profile_ids.sort()).toEqual(['ig-1', 'ig-2']);           // o perfil do aparelho já vem marcado
  expect(text()).toContain('sem IA');
});

// ---------------------------------------------------------------- fase L: P1.4 — escopo que não carregou não é "todos"
it('se a lista de perfis falha, o escopo mostra o erro com "Tentar de novo" e o "Salvar" fica bloqueado até carregar', async () => {
  backend.on('GET', /\/instagram\/profiles$/, () => apiError(500, 'internal', 'banco indisponível'));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('QA-001'));
  await click(byRole('button', /Pedir proposta à IA/i));
  await waitFor(() => expect(text()).toContain('A lista de perfis e grupos não carregou'));
  expect(text()).toContain('banco indisponível');
  expect(text()).not.toContain('Nada marcado = todos os perfis');
  expect(allByRole('checkbox', /@aluno/)).toHaveLength(0);
  const salvar = byRole('button', /^Salvar como fluxo/);
  expect(salvar.getAttribute('aria-disabled')).toBe('true');
  expect(salvar.textContent).toContain('não carregou');
  await click(salvar);
  expect(backend.callsTo('POST', /\/save$/)).toHaveLength(0);

  backend.on('GET', /\/instagram\/profiles$/, () => json([{ id: 'ig-1', username: 'aluno.um' }]));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => expect(allByRole('checkbox', /@aluno.um/)).toHaveLength(1));
  expect(text()).toContain('Nada marcado = todos os perfis');
  expect(byRole('button', /^Salvar como fluxo/).getAttribute('aria-disabled')).toBeNull();
});

// ---------------------------------------------------------------- fase L: P2.6 — edição não se perde sem perguntar
it('"Depois" e "Pedir outra proposta" pedem confirmação quando há edição; sem edição, fecham direto', async () => {
  let fechado = 0;
  await act(async () => root.render(<><TrainingReview sessionId="trn-1" onClose={() => { fechado += 1; }} /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('QA-001'));
  await click(byRole('button', /Pedir proposta à IA/i));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  await setValue(byRole('textbox', /Título da etapa 1/) as HTMLInputElement, 'Abrir a conversa certa');

  await click(byRole('button', /^Pedir outra proposta$/));
  await waitFor(() => expect(text()).toContain('Pedir outra proposta?'));
  const proposicoes = backend.callsTo('POST', /\/propose$/).length;
  await click(byRole('button', /^Voltar$/, byRole('dialog', /Pedir outra proposta\?/)));
  await waitFor(() => expect(allByRole('dialog', /Pedir outra proposta\?/)).toHaveLength(0));
  expect(backend.callsTo('POST', /\/propose$/)).toHaveLength(proposicoes);      // voltar não pede
  expect((byRole('textbox', /Título da etapa 1/) as HTMLInputElement).value).toBe('Abrir a conversa certa');

  await click(byRole('button', /^Depois$/));
  await waitFor(() => expect(text()).toContain('Sair sem salvar o fluxo?'));
  expect(fechado).toBe(0);
  await click(byRole('button', /^Sair sem salvar$/, byRole('dialog', /Sair sem salvar/)));
  await waitFor(() => expect(fechado).toBe(1));
});

// ---------------------------------------------------------------- fase F: ensino v2 atrás de `features.skills`
function comHabilidades(ligado: boolean | undefined): void {
  const health = makeSnapshot().health;
  useAppStore.setState({ health: { ...health, features: { ...health.features, skills: ligado } } });
}

const DOC = {
  apiVersion: 'automation/v1alpha1', kind: 'Skill',
  metadata: { id: 'qa-messenger.mandar_mensagem', name: 'Mandar mensagem', app: 'qa-messenger' },
  spec: { invocation: { command_template: 'Mandar mensagem — contato: {contato}' },
          nodes: [{ id: 'abrir', goal: { title: 'Abrir a conversa', goal: 'abrir' } },
                  { id: 'enviar', goal: { title: 'Enviar', goal: 'enviar' }, side_effect: true }] },
};
const ANOT = {
  evidence: {}, discarded: [], assumptions: [], preconditions: [], postconditions: [], suggested_proofs: [],
  parameters: [{ name: 'contato', type: 'string', examples: ['QA-001'], description: '', required: true }],
  effects: [{ node: 'enviar', capability: null, description: 'Enviar' }], risks: ['Etapa conferida pelo modelo.'],
};
function candidata(seq: number, status = 'proposed') {
  return { id: `cand-${seq}`, teaching_id: 'ens-1', seq, status, validation_status: 'none', generated_by: 'ai:simulado',
           content_hash: 'h', version_id: null, document: DOC, annotations: ANOT, created_at: '', updated_at: '' };
}
function ensino(status: string, extra: Record<string, unknown> = {}) {
  return { id: 'ens-1', instruction: 'Mandar mensagem', skill_id: null, base_version: null, app_id: 'qa-messenger',
           profile_id: null, status, validation_status: 'none', result_version_id: null, operator: null,
           created_at: '', updated_at: '', closed_at: null, source: 'hybrid', demonstrations: [], turns: [],
           candidates: [], current_candidate: null, open_questions: [], errors: [], ...extra };
}

it('com features.skills desligado (padrão), a revisão é a de sempre: sem ensino v2 e sem chamada nova', async () => {
  for (const ligado of [undefined, false]) {
    comHabilidades(ligado);
    await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
    await waitFor(() => expect(text()).toContain('QA-001'));
    expect(text()).not.toContain('Habilidade versionada');
    expect(backend.callsTo('GET', /teaching-sessions/)).toHaveLength(0);
  }
});

it('com features.skills ligado: gera a candidata, responde a pergunta, gera de novo e salva como rascunho', async () => {
  comHabilidades(true);
  const pergunta = { id: 7, kind: 'effect_confirmation', key: 'efeito:enviar', origin: 'ai',
                     text: 'A etapa “Enviar” muda algo fora do aparelho?', target: null, candidate_id: 'cand-1' };
  backend.on('GET', /\/teaching-sessions$/, () => json([]));
  backend.on('POST', /\/teaching-sessions$/, () => json(ensino('open'), 201));
  backend.on('POST', /\/teaching-sessions\/ens-1\/demonstrations$/, () => json(ensino('open')));
  let geracoes = 0;
  backend.on('POST', /\/teaching-sessions\/ens-1\/candidates$/, () => {
    geracoes += 1;
    return json(geracoes === 1
      ? ensino('asking', { current_candidate: candidata(1), open_questions: [pergunta] })
      : ensino('validating', { current_candidate: candidata(2) }));
  });
  backend.on('POST', /\/teaching-sessions\/ens-1\/answers$/, () => json(ensino('asking', { current_candidate: candidata(1) })));
  backend.on('POST', /\/skill-candidates\/cand-2\/validate$/, () => json(ensino('ready', { current_candidate: candidata(2) })));
  backend.on('POST', /\/skill-candidates\/cand-2\/publish$/, () => json(ensino('published', {
    current_candidate: candidata(2, 'accepted'), result_version_id: 'qa-messenger.mandar_mensagem@1' })));

  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('Habilidade versionada'));
  expect(backend.callsTo('GET', /teaching-sessions$/)[0]!.query.get('training_session_id')).toBe('trn-1');
  // P1.5: "Gerar" só aparece depois de saber que a gravação não tem ensino.
  await waitFor(() => byRole('button', /Gerar candidata de habilidade/i));
  // P2.1: com o ensino v2 ligado, ele é o caminho principal — o rodapé do fluxo deixa de ser primário.
  expect(byRole('button', /Gerar candidata de habilidade/i).className).toMatch(/btnPrimary/);
  expect(byRole('button', /^Salvar como fluxo/).className).not.toMatch(/btnPrimary/);
  expect(byRole('button', /Pedir proposta à IA/i).className).not.toMatch(/btnPrimary/);
  await click(byRole('button', /Gerar candidata de habilidade/i));
  await waitFor(() => expect(text()).toContain('muda algo fora do aparelho'));
  expect(text()).toContain('{contato} · string = QA-001');
  expect(text()).toContain('efeito externo');
  const criado = backend.callsTo('POST', /\/teaching-sessions$/)[0]!.body as { instruction: string; app_id: string };
  expect(criado).toEqual({ instruction: 'Mandar mensagem', app_id: 'qa-messenger' });
  expect((backend.callsTo('POST', /demonstrations$/)[0]!.body as { training_session_id: string }).training_session_id).toBe('trn-1');

  expect(text()).toMatch(/candidata 1 .*proposta/);                    // status da candidata em português
  await setValue(byRole('textbox', /Resposta à pergunta: A etapa “Enviar” muda algo/i) as HTMLInputElement, 'Sim, é enviar.');
  await click(byRole('button', /^Responder$/i));
  await waitFor(() => expect(text()).toContain('Gerar de novo com as respostas'));
  expect(backend.callsTo('POST', /answers$/)[0]!.body).toEqual({ question_id: 7, body: 'Sim, é enviar.' });
  await click(byRole('button', /Gerar de novo com as respostas/i));
  await waitFor(() => expect(text()).toContain('Salvar como rascunho'));
  await click(byRole('button', /Salvar como rascunho/i));
  await waitFor(() => expect(text()).toContain('qa-messenger.mandar_mensagem@1'));
  expect(backend.callsTo('POST', /validate$/)[0]!.body).toEqual({ mode: 'static' });
  expect(backend.callsTo('POST', /publish$/)).toHaveLength(1);
  expect(text()).toContain('Configuração → Fluxos e receitas → Habilidades');   // o nome da aba, como na TopBar
  expect(allByRole('button', /^Descartar/)).toHaveLength(0);                      // terminal: nada a descartar
  // o "Salvar como fluxo" de sempre continua lá, sem ponte com o ensino v2
  expect(backend.callsTo('POST', /\/save$/)).toHaveLength(0);
});
