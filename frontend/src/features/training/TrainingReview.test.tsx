// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { ConfirmHost } from '../../components/Confirm';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, allByRole, apiError, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { alvoReconhecido, ESPERA_DA_PREVIA_MS, TrainingReview } from './TrainingReview';
import type { TrainingInput } from '../../api/types';

/** O atraso máximo do fetch falso (modo ATRASO_DO_FETCH_MS): a resposta que o teste solta depois ainda pode estar a caminho. */
const ATRASO_MAXIMO = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);

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
  steps: [{ key: 'abrir', title: 'Abrir a conversa com {contato}', goal: 'abrir', inputs: [1, 2], side_effect: false,
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
  expect(text()).toContain('(texto não gravado)');
  expect(text()).toContain('O que você fez (2 entradas)');
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

// 31.91: caminho único de ensino. Com `features.skills`, a revisão não gera candidata: o fluxo salvo vira habilidade
// pela conversão da fase J, e o ensino v2 que a gravação já tinha aparece só para leitura.
it('com features.skills ligado e sem ensino: nada de candidata; depois de salvar, "Gerar habilidade deste fluxo" converte o fluxo', async () => {
  comHabilidades(true);
  backend.on('GET', /\/teaching-sessions$/, () => json([]));
  backend.on('POST', /\/flows\/mandar-mensagem\/adopt$/, () => json({
    flow_id: 'mandar-mensagem', skill_id: 'qa-messenger.mandar_mensagem', warnings: [],
    published: { ref: 'qa-messenger.mandar_mensagem@1' }, draft: { ref: 'qa-messenger.mandar_mensagem@2' },
  }));
  await act(async () => root.render(<><TrainingReview sessionId="trn-1" onClose={() => {}} /><ConfirmHost /></>));
  await waitFor(() => expect(backend.callsTo('GET', /teaching-sessions$/)).toHaveLength(1));
  await flush(ATRASO_MAXIMO + 30);
  expect(text()).not.toContain('Habilidade versionada');
  expect(allByRole('button', /Gerar candidata de habilidade/i)).toHaveLength(0);
  expect(byRole('button', /Pedir proposta à IA/i).className).toMatch(/btnPrimary/);
  await click(byRole('button', /Pedir proposta à IA/i));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  expect(byRole('button', /^Salvar como fluxo/).className).toMatch(/btnPrimary/);

  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  expect(text()).toContain('Parâmetros com tipo, riscos e versões só existem na habilidade.');
  await click(byRole('button', /^Gerar habilidade deste fluxo$/));
  await click(await waitFor(() => byRole('button', /^Gerar habilidade$/, byRole('dialog', /Gerar a habilidade deste fluxo/))));
  await waitFor(() => expect(text()).toContain('qa-messenger.mandar_mensagem@2 em rascunho'));
  expect(backend.callsTo('POST', /\/adopt$/)).toHaveLength(1);
  expect(backend.callsTo('POST', /\/teaching-sessions$/)).toHaveLength(0);
});

it('com features.skills ligado e ensino antigo na gravação: o ensino aparece só para leitura, sem responder, gerar nem descartar', async () => {
  comHabilidades(true);
  const pergunta = { id: 7, kind: 'effect_confirmation', key: 'efeito:enviar', origin: 'ai',
                     text: 'A etapa “Enviar” muda algo fora do aparelho?', target: null, candidate_id: 'cand-1' };
  backend.on('GET', /\/teaching-sessions$/, () => json([{ id: 'ens-1' }]));
  backend.on('GET', /\/teaching-sessions\/ens-1$/, () => json(ensino('asking', { current_candidate: candidata(1), open_questions: [pergunta] })));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('Habilidade versionada'));
  expect(text()).toContain('muda algo fora do aparelho');
  expect(text()).toContain('fica só para leitura');
  expect(allByRole('textbox', /Resposta à pergunta/)).toHaveLength(0);
  for (const nome of [/^Responder$/, /Gerar de novo/, /Pedir outra candidata/, /Salvar como rascunho/, /^Descartar$/]) {
    expect(allByRole('button', nome)).toHaveLength(0);
  }
});

// ---------------------------------------------------------------- 31.90-A: quem ensinou corrige as entradas
// O harness não dá papel implícito a `section` nem a `ul`: as regiões e listas da revisão se acham pelo rótulo.
const rotulados = (tag: string, nome: string) => [...document.querySelectorAll<HTMLElement>(`${tag}[aria-label="${nome}"]`)];
function regiao(nome: string, tag = 'section'): HTMLElement {
  const [el] = rotulados(tag, nome);
  if (!el) throw new Error(`Nenhum ${tag} com aria-label "${nome}"`);
  return el;
}
const toque = (seq: number, alvo: string) => ({
  ...SESSAO.inputs[0]!, seq, target: { text: alvo, resource_id: 'x:id/b', unique: ['rid+text'] }, screen_title: null,
});
const etapa = (key: string, title: string, inputs: number[]) => ({ ...PROPOSTA.steps[0]!, key, title, inputs });

/** Cada entrada da sessão aparece em exatamente um lugar do corpo do save: numa etapa OU no descarte. */
function lugaresNoSave(): Map<number, number> {
  const corpo = backend.callsTo('POST', /\/save$/)[0]!.body as { proposal: { steps: { inputs: number[] }[]; discarded: { seq: number }[] } };
  const m = new Map<number, number>();
  for (const s of corpo.proposal.steps) for (const n of s.inputs) m.set(n, (m.get(n) ?? 0) + 1);
  for (const d of corpo.proposal.discarded) m.set(d.seq, (m.get(d.seq) ?? 0) + 1);
  return m;
}

async function abrirComProposta(sessao: object, proposta: object) {
  backend.on('GET', /\/training\/trn-1$/, () => json(sessao));
  backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...sessao, status: 'proposed', proposal: proposta }));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
}

it('entrada sem etapa e sem descarte: bloco "Sem destino" no topo, Salvar travado com o motivo, e as duas ações resolvem', async () => {
  const sessao = { ...SESSAO, inputs: [...SESSAO.inputs, toque(3, 'Enviar')] };
  await abrirComProposta(sessao, { ...PROPOSTA, steps: [etapa('abrir', 'Abrir', [1])] });
  const bloco = regiao('Sem destino');
  expect(bloco.textContent).toContain('#2');
  expect(bloco.textContent).toContain('#3');
  expect(bloco.textContent).toContain('texto não gravado');
  // No topo da proposta: antes do comando.
  expect(bloco.compareDocumentPosition(byRole('textbox', /Comando/)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const salvar = () => byRole('button', /^Salvar como fluxo/);
  expect(salvar().getAttribute('aria-disabled')).toBe('true');
  expect(salvar().getAttribute('aria-label') ?? salvar().textContent).toContain('Falta destino para #2, #3');
  await click(salvar());
  expect(backend.callsTo('POST', /\/save$/)).toHaveLength(0);

  await click(byRole('button', /^Descartar a entrada #2$/, bloco));
  await waitFor(() => expect(regiao('Descartadas').textContent).toContain('descartada por quem ensinou'));
  expect(regiao('Sem destino').textContent).not.toContain('#2');
  expect(salvar().getAttribute('aria-disabled')).toBe('true');                 // ainda falta o #3

  // R1: escolher a etapa não move; quem move é o "Devolver".
  await setValue(byRole('combobox', /Devolver a entrada #3 à etapa/, regiao('Sem destino')) as HTMLSelectElement, '0');
  expect(regiao('Sem destino').textContent).toContain('#3');
  await click(byRole('button', /^Devolver a entrada #3$/, regiao('Sem destino')));
  await waitFor(() => expect(rotulados('section', 'Sem destino')).toHaveLength(0));
  expect(regiao('Entradas da etapa 1', 'ul').textContent).toContain('Enviar');
  expect(salvar().getAttribute('aria-disabled')).toBeNull();
  await click(salvar());
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  const corpo = backend.callsTo('POST', /\/save$/)[0]!.body as { proposal: { steps: { inputs: number[] }[]; discarded: unknown[] } };
  expect(corpo.proposal.steps[0]!.inputs).toEqual([1, 3]);
  expect(corpo.proposal.discarded).toEqual([{ seq: 2, why: 'descartada por quem ensinou' }]);
});

it('Descartar tira da etapa e Devolver tira do descarte: a entrada nunca fica em dois lugares (entrada_duplicada)', async () => {
  const sessao = { ...SESSAO, inputs: [...SESSAO.inputs, toque(3, 'Enviar'), toque(4, 'Voltar')] };
  // A proposta da IA já vem com o #2 em dois lugares: na etapa 1 e no descarte.
  await abrirComProposta(sessao, {
    ...PROPOSTA,
    steps: [etapa('abrir', 'Abrir', [1, 2]), etapa('enviar', 'Enviar', [3])],
    discarded: [{ seq: 2, why: 'vai-e-volta' }, { seq: 4, why: 'engano' }],
  });
  const salvar = () => byRole('button', /^Salvar como fluxo/);
  expect(salvar().getAttribute('aria-label') ?? salvar().textContent).toContain('#2 está em mais de um lugar');
  expect(text()).toContain('em mais de um lugar');

  // Descartar o #2 da etapa 1: sai da etapa e fica uma vez só no descarte, com o motivo da IA.
  await click(byRole('button', /^Descartar a entrada #2$/, regiao('Entradas da etapa 1', 'ul')));
  await waitFor(() => expect(regiao('Entradas da etapa 1', 'ul').textContent).not.toContain('#2'));
  expect(salvar().getAttribute('aria-disabled')).toBeNull();

  // Descartar o #1 da etapa 1 e devolvê-lo à etapa 2; devolver o #4 (descartado pela IA) à etapa 2.
  await click(byRole('button', /^Descartar a entrada #1$/, regiao('Entradas da etapa 1', 'ul')));
  await waitFor(() => expect(regiao('Descartadas').textContent).toContain('#1'));
  expect(rotulados('ul', 'Entradas da etapa 1')).toHaveLength(0);           // etapa vazia: a IA conduz
  expect(text()).toContain('sem entradas: a IA conduz esta etapa');
  await setValue(byRole('combobox', /Devolver a entrada #1 à etapa/) as HTMLSelectElement, '1');
  await click(byRole('button', /^Devolver a entrada #1$/));
  await waitFor(() => expect(regiao('Entradas da etapa 2', 'ul').textContent).toContain('#1'));
  await setValue(byRole('combobox', /Devolver a entrada #4 à etapa/) as HTMLSelectElement, '1');
  await click(byRole('button', /^Devolver a entrada #4$/));
  await waitFor(() => expect(regiao('Entradas da etapa 2', 'ul').textContent).toContain('Voltar'));
  expect(regiao('Descartadas').textContent).not.toContain('#1');
  expect(regiao('Descartadas').textContent).not.toContain('#4');

  await click(salvar());
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  const lugares = lugaresNoSave();
  expect([...lugares.keys()].sort()).toEqual([1, 2, 3, 4]);
  expect([...lugares.values()].every((n) => n === 1)).toBe(true);
  const corpo = backend.callsTo('POST', /\/save$/)[0]!.body as { proposal: { steps: { inputs: number[] }[]; discarded: unknown[] } };
  expect(corpo.proposal.steps.map((s) => s.inputs)).toEqual([[], [1, 3, 4]]);   // na ordem em que foram feitas
  expect(corpo.proposal.discarded).toEqual([{ seq: 2, why: 'vai-e-volta' }]);
});

it('texto não gravado nunca aparece, nem marcado como sensível com valor; os avisos do save vão no toast de sucesso', async () => {
  useToastStore.setState({ toasts: [] });
  const vazado = { ...SESSAO.inputs[1]!, seq: 3, text: 'segredo-123', sensitive: true };
  await abrirComProposta({ ...SESSAO, inputs: [...SESSAO.inputs, vazado] }, { ...PROPOSTA, steps: [etapa('abrir', 'Abrir', [1, 2, 3])] });
  expect(text()).not.toContain('segredo-123');
  expect(regiao('Entradas da etapa 1', 'ul').textContent).toContain('texto não gravado');

  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem',
    steps: [{ key: 'abrir', title: 'Abrir', recipe: true, reason: 'receita gravada' }],
    warnings: ['A etapa "Abrir" não confere o efeito no servidor.'],
  }));
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  const sucesso = useToastStore.getState().toasts.find((t) => t.tone === 'success');
  expect(sucesso?.message).toContain('A etapa "Abrir" não confere o efeito no servidor.');
});

// ---------------------------------------------------------------- 31.90-B: foco, anúncio e "editado" que compara
it('descartar e devolver: o foco vai para a entrada no lugar novo, o status diz para onde, e voltar ao que era não pede "Sair sem salvar"', async () => {
  let fechado = 0;
  const sessao = { ...SESSAO, inputs: [...SESSAO.inputs, toque(3, 'Enviar')] };
  const proposta = { ...PROPOSTA, steps: [etapa('abrir', 'Abrir', [1, 2]), etapa('enviar', 'Enviar', [3])] };
  backend.on('GET', /\/training\/trn-1$/, () => json(sessao));
  backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...sessao, status: 'proposed', proposal: proposta }));
  await act(async () => root.render(<><TrainingReview sessionId="trn-1" onClose={() => { fechado += 1; }} /><ConfirmHost /></>));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  const status = () => [...document.querySelectorAll('[role="status"]')].map((s) => s.textContent).join(' | ');

  await click(byRole('button', /^Descartar a entrada #3$/, regiao('Entradas da etapa 2', 'ul')));
  await waitFor(() => expect(document.activeElement?.getAttribute('aria-label')).toBe('Devolver a entrada #3 à etapa'));
  expect(regiao('Descartadas').contains(document.activeElement)).toBe(true);
  expect(status()).toContain('#3 foi para Descartadas');

  await setValue(byRole('combobox', /Devolver a entrada #3 à etapa/) as HTMLSelectElement, '1');
  await click(byRole('button', /^Devolver a entrada #3$/));
  await waitFor(() => expect(document.activeElement?.getAttribute('aria-label')).toBe('Descartar a entrada #3'));
  expect(regiao('Entradas da etapa 2', 'ul').contains(document.activeElement)).toBe(true);
  expect(status()).toContain('#3 voltou à etapa 2 (Enviar)');

  // A proposta voltou a ser a que a IA mandou: não há o que perder, e "Depois" fecha sem perguntar.
  await click(byRole('button', /^Depois$/));
  await waitFor(() => expect(fechado).toBe(1));
  expect(text()).not.toContain('Sair sem salvar o fluxo?');
});

it('descarte repetido conta uma vez (#442); a entrada na etapa e no descarte sai da etapa com "Manter descartada"', async () => {
  const sessao = { ...SESSAO, inputs: [...SESSAO.inputs, toque(3, 'Enviar')] };
  await abrirComProposta(sessao, {
    ...PROPOSTA, steps: [etapa('abrir', 'Abrir', [1, 2, 3])],
    discarded: [{ seq: 3, why: 'engano' }, { seq: 3, why: 'engano de novo' }],
  });
  expect(regiao('Descartadas').textContent).toContain('Descartadas (1)');
  const salvar = () => byRole('button', /^Salvar como fluxo/);
  expect(salvar().getAttribute('aria-label') ?? salvar().textContent).toContain('#3 está em mais de um lugar');

  await click(byRole('button', /^Manter a entrada #3 só em Descartadas$/, regiao('Descartadas')));
  await waitFor(() => expect(regiao('Entradas da etapa 1', 'ul').textContent).not.toContain('#3'));
  // O descarte segue com as duas linhas da IA, e isso não trava o salvar.
  expect(salvar().getAttribute('aria-disabled')).toBeNull();
  expect(allByRole('button', /^Manter a entrada #3/)).toHaveLength(0);
});

it('toque sem alvo nas palavras do backend (#440), o "Confere" em português e a mesma tecla seguida numa linha só', async () => {
  const tecla = (seq: number) => ({ ...SESSAO.inputs[1]!, seq, type: 'key', key_name: 'delete', has_text: false, text_len: null });
  const pin = { ...SESSAO.inputs[0]!, seq: 3, target: null, x: null, y: null, sensitive: true };
  const solto = { ...SESSAO.inputs[0]!, seq: 4, target: null, x: 50, y: 60 };
  const sessao = { ...SESSAO, inputs: [...SESSAO.inputs, pin, solto, tecla(5), tecla(6), tecla(7)] };
  await abrirComProposta(sessao, { ...PROPOSTA, steps: [etapa('abrir', 'Abrir', [1, 2, 3, 4, 5, 6, 7])] });
  expect(text()).toContain('toque em teclado ou tela sensível (não gravado)');
  expect(text()).toContain('toque sem elemento identificado');
  expect(text()).toContain('Confere: aparece o texto “{contato}” (aberta)');
  // Na coluna da gravação, uma linha; na etapa, cada uma com o seu Descartar.
  expect(text()).toContain('#5–#7');
  expect(text()).toContain('tecla delete ×3');
  for (const n of [5, 6, 7]) expect(byRole('button', new RegExp(`^Descartar a entrada #${n}$`), regiao('Entradas da etapa 1', 'ul'))).toBeTruthy();
});

// ---------------------------------------------------------------- 31.90-B: prévia do salvar (v1.58), recusas no campo e refazer receitas
const PREVIA_OK = { steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita será gravada ao salvar' }], warnings: [] };
// Sem receita, para o motivo aparecer na tela (com receita, o painel mostra a frase dele: 29.146).
const reasonDa = (reason: string) => ({ ...PREVIA_OK, steps: [{ ...PREVIA_OK.steps[0]!, recipe: false, reason }] });

async function abrirEProporComPrevia() {
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
}

it('a prévia mostra o que cada etapa vira; a recusa do comando vai no campo e trava o Salvar até corrigir', async () => {
  backend.on('POST', /\/training\/trn-1\/preview$/, (c) => {
    const comando = (c.body as { proposal: { command_template: string } }).proposal.command_template;
    return comando.startsWith('{') ? apiError(400, 'comando_generico', 'O comando precisa começar por palavra fixa.') : json(PREVIA_OK);
  });
  await abrirEProporComPrevia();
  await waitFor(() => expect(text()).toContain('Ao salvar: a receita desta etapa é gravada.'));
  const comando = () => byRole('textbox', /Comando/) as HTMLInputElement;
  const salvar = () => byRole('button', /^Salvar como fluxo/);
  expect(comando().getAttribute('aria-invalid')).toBeNull();
  // O corpo da prévia é o do salvar (v1.58: `extra="forbid"`).
  expect(Object.keys(backend.callsTo('POST', /\/preview$/)[0]!.body as object).sort()).toEqual(['group_ids', 'profile_ids', 'proposal']);

  await setValue(comando(), '{contato} mande');
  await waitFor(() => expect(comando().getAttribute('aria-invalid')).toBe('true'));
  expect(text()).toContain('O comando precisa começar por palavra fixa.');
  expect(salvar().getAttribute('aria-label') ?? salvar().textContent).toContain('Corrija o comando');
  expect(text()).not.toContain('Ao salvar:');                      // a prévia velha não fica ao lado da recusa

  await setValue(comando(), 'mande para {contato}');
  await waitFor(() => expect(text()).toContain('Ao salvar: a receita desta etapa é gravada.'));
  expect(comando().getAttribute('aria-invalid')).toBeNull();
  expect(salvar().getAttribute('aria-disabled')).toBeNull();
});

it('salvar recusado por parâmetro reservado (v1.62) fica no campo e sem toast; entrada_inexistente vira o motivo do Salvar', async () => {
  useToastStore.setState({ toasts: [] });
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA_OK));
  backend.on('POST', /\/training\/trn-1\/save$/, () => apiError(400, 'parametro_reservado', 'O nome {run_id} é reservado: escolha outro.'));
  await abrirEProporComPrevia();
  await waitFor(() => expect(text()).toContain('Ao salvar:'));
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(byRole('textbox', /Comando/).getAttribute('aria-invalid')).toBe('true'));
  expect(text()).toContain('O nome {run_id} é reservado: escolha outro.');
  expect(useToastStore.getState().toasts.filter((t) => t.title === 'Não foi possível salvar o fluxo')).toHaveLength(0);

  // Recusa que não é do comando: vira o motivo do "Salvar" travado, com a mensagem literal.
  backend.on('POST', /\/training\/trn-1\/preview$/, () => apiError(400, 'entrada_inexistente', 'As entradas #9 não existem na gravação. Peça uma nova proposta à IA.'));
  await setValue(byRole('textbox', /Título da etapa 1/) as HTMLInputElement, 'Abrir');
  const salvar = () => byRole('button', /^Salvar como fluxo/);
  await waitFor(() => expect(salvar().getAttribute('aria-label') ?? salvar().textContent).toContain('As entradas #9 não existem'));
  expect(byRole('textbox', /Comando/).getAttribute('aria-invalid')).toBeNull();
});

it('duas prévias fora de ordem: vale a da última edição', async () => {
  let soltarPrimeira: (r: Response) => void = () => {};
  let pedidas = 0;
  backend.on('POST', /\/training\/trn-1\/preview$/, () => {
    pedidas += 1;
    if (pedidas === 1) return new Promise<Response>((r) => { soltarPrimeira = r; });
    return json(reasonDa('da segunda'));
  });
  await abrirEProporComPrevia();
  await waitFor(() => expect(pedidas).toBe(1));
  await setValue(byRole('textbox', /Título da etapa 1/) as HTMLInputElement, 'Abrir de novo');
  await waitFor(() => expect(text()).toContain('Ao salvar: da segunda'));
  soltarPrimeira(json(reasonDa('da primeira')));
  await flush(ATRASO_MAXIMO + 30);
  expect(text()).toContain('Ao salvar: da segunda');
  expect(text()).not.toContain('da primeira');
});

it('etapa sem receita no salvar: "Refazer receitas" só com o clique, chama /recipes e diz quantas gravou', async () => {
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA_OK));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem', warnings: [],
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: false,
              reason: 'aparelho do treinamento fora do ar e a versão do app ainda não foi lida. Refaça as receitas quando ele voltar' }],
  }));
  backend.on('POST', /\/training\/trn-1\/recipes$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem', created: 1,
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }],
  }));
  await abrirEProporComPrevia();
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  expect(text()).toContain('com IA');
  expect(backend.callsTo('POST', /\/recipes$/)).toHaveLength(0);       // quem aciona é a pessoa

  await click(byRole('button', /^Refazer receitas$/));
  await waitFor(() => expect(text()).toContain('1 receita gravada agora.'));
  expect(text()).toContain('— receita gravada');                        // o relatório troca pelo do refazer
  expect(backend.callsTo('POST', /\/recipes$/)).toHaveLength(1);
});

// ---------------------------------------------------------------- 31.91 F2 (v1.63): responder às perguntas da proposta
it('as respostas vão no corpo do propose e a proposta nova as mostra; sem resposta, o corpo não vai', async () => {
  let pedidas = 0;
  backend.on('POST', /\/training\/trn-1\/propose$/, () => {
    pedidas += 1;
    return json({ ...SESSAO, status: 'proposed', proposal: pedidas === 1 ? PROPOSTA
      : { ...PROPOSTA, questions: [], answers: [{ question: 'O texto muda?', answer: 'Muda a cada cliente.' }] } });
  });
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  expect(backend.callsTo('POST', /\/propose$/)[0]!.body).toBeUndefined();
  expect(text()).toContain('Não escreva senha nem código');
  expect(byRole('button', /^Pedir outra proposta$/)).toBeTruthy();

  await setValue(byRole('textbox', /^O texto muda\?$/) as HTMLInputElement, '  Muda a cada cliente.  ');
  await click(byRole('button', /^Pedir nova proposta com as respostas$/));
  await waitFor(() => expect(regiao('Respostas já dadas', 'ul').textContent).toContain('Muda a cada cliente.'));
  expect(backend.callsTo('POST', /\/propose$/)[1]!.body).toEqual({ answers: [{ question: 'O texto muda?', answer: 'Muda a cada cliente.' }] });
  expect(rotulados('section', 'Perguntas da IA')).toHaveLength(0);     // respondida, não volta como pergunta
});

it('resposta com cara de senha (resposta_sensivel) fica no campo da pergunta, sem toast, e a proposta não muda', async () => {
  useToastStore.setState({ toasts: [] });
  let pedidas = 0;
  backend.on('POST', /\/training\/trn-1\/propose$/, () => {
    pedidas += 1;
    return pedidas === 1 ? json({ ...SESSAO, status: 'proposed', proposal: PROPOSTA })
      : apiError(400, 'resposta_sensivel', 'A resposta parece senha ou código: não mande segredo para a IA.');
  });
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  const campo = () => byRole('textbox', /^O texto muda\?$/) as HTMLInputElement;
  await setValue(campo(), 'Senha123!');
  await click(byRole('button', /^Pedir nova proposta com as respostas$/));
  await waitFor(() => expect(campo().getAttribute('aria-invalid')).toBe('true'));
  expect(text()).toContain('A resposta parece senha ou código');
  expect(useToastStore.getState().toasts.filter((t) => t.title === 'A IA não conseguiu propor o fluxo')).toHaveLength(0);
  expect((byRole('textbox', /Comando/) as HTMLInputElement).value).toBe('mande para {contato}');   // a proposta atual fica
  // Mexer na resposta tira o aviso.
  await setValue(campo(), 'Muda a cada cliente.');
  expect(campo().getAttribute('aria-invalid')).toBeNull();
});

it('com entrada sem destino a prévia não é pedida (a tela já diz o motivo); dado o destino, ela roda', async () => {
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA_OK));
  const sessao = { ...SESSAO, inputs: [...SESSAO.inputs, toque(3, 'Enviar')] };
  await abrirComProposta(sessao, { ...PROPOSTA, steps: [etapa('abrir', 'Abrir', [1, 2])] });
  await flush(ESPERA_DA_PREVIA_MS + ATRASO_MAXIMO + 50);
  expect(backend.callsTo('POST', /\/preview$/)).toHaveLength(0);
  await click(byRole('button', /^Descartar a entrada #3$/, regiao('Sem destino')));
  await waitFor(() => expect(backend.callsTo('POST', /\/preview$/)).toHaveLength(1));
});

it('proposta_concorrente: mostra o motivo e relê a sessão, ficando com a proposta que valeu', async () => {
  useToastStore.setState({ toasts: [] });
  let pedidas = 0;
  backend.on('POST', /\/training\/trn-1\/propose$/, () => {
    pedidas += 1;
    return pedidas === 1 ? json({ ...SESSAO, status: 'proposed', proposal: PROPOSTA })
      : apiError(409, 'proposta_concorrente', 'Outra proposta desta gravação terminou antes; peça de novo.');
  });
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  backend.on('GET', /\/training\/trn-1$/, () => json({ ...SESSAO, status: 'proposed', proposal: { ...PROPOSTA, command_template: 'mande a {contato}' } }));
  await click(byRole('button', /^Pedir outra proposta$/));
  await waitFor(() => expect((byRole('textbox', /Comando/) as HTMLInputElement).value).toBe('mande a {contato}'));
  expect(useToastStore.getState().toasts.find((t) => t.title === 'A proposta mudou enquanto você pedia')?.message)
    .toContain('Outra proposta desta gravação terminou antes');
});

it('mais de 8 respostas: o pedido trava com o motivo em vez de cortar a 9ª', async () => {
  const perguntas = Array.from({ length: 9 }, (_, i) => `Pergunta ${i + 1}`);
  backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...SESSAO, status: 'proposed', proposal: { ...PROPOSTA, questions: perguntas } }));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
  await waitFor(() => expect(text()).toContain('Pergunta 9'));
  for (const p of perguntas) await setValue(byRole('textbox', new RegExp(`^${p}$`)) as HTMLInputElement, 'sim');
  const pedir = byRole('button', /^Pedir nova proposta com as respostas/);
  expect(pedir.getAttribute('aria-disabled')).toBe('true');
  expect(pedir.getAttribute('aria-label') ?? pedir.textContent).toContain('Mande até 8 respostas por vez');
  const antes = backend.callsTo('POST', /\/propose$/).length;
  await click(pedir);
  expect(backend.callsTo('POST', /\/propose$/)).toHaveLength(antes);
  await setValue(byRole('textbox', /^Pergunta 9$/) as HTMLInputElement, '');
  expect(byRole('button', /^Pedir nova proposta com as respostas/).getAttribute('aria-disabled')).toBeNull();
});

// ---------------------------------------------------------------- 31.90-F: como a gravação reconhece o elemento tocado
it('31.90-F: a coluna da gravação diz por que a gravação reconhece o elemento (seletor e id, sem o pacote)', async () => {
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('QA-001'));
  expect(text()).toContain('(reconhecido por id e texto, id conversation_name)');
  expect(text()).not.toContain('x:id/conversation_name');
});

it('31.90-F: alvoReconhecido cobre cada seletor, o alvo sem identificador e o que não é toque', () => {
  const entrada = (parcial: Partial<TrainingInput>) => ({ ...SESSAO.inputs[0], ...parcial }) as unknown as TrainingInput;
  expect(alvoReconhecido(entrada({ target: { desc: 'Enviar', unique: ['desc'] } }))).toBe('reconhecido por descrição');
  expect(alvoReconhecido(entrada({ target: { resource_id: 'a:id/ok', unique: ['rid', 'text'], text: 'OK' } }))).toBe('reconhecido por id (ou texto), id ok');
  expect(alvoReconhecido(entrada({ target: { class_name: 'android.view.View', unique: [] } }))).toBe('sem identificador único: este toque não vira receita');
  expect(alvoReconhecido(entrada({ target: { class_name: 'android.view.View', unique: [], filhos: [{}] } }))).toBe('reconhecido pelo que o elemento contém');
  // O contêiner sem identidade: a pessoa vê o filho rotulado (e o id) pelo qual a receita o acha, até três.
  expect(alvoReconhecido(entrada({ target: { unique: [], filhos: [
    { text: 'Fulano', resource_id: 'a:id/row_name', unique: ['text'] }, { desc: 'Foto', unique: ['desc'] }, { resource_id: 'a:id/so_id' }, { text: 'quarto' }] } })))
    .toBe('reconhecido pelo que o elemento contém: “Fulano”, id row_name; “Foto”; id so_id');
  expect(alvoReconhecido(entrada({ target: null }))).toBeNull();                      // sem alvo: a frase é de toqueSemAlvo
  expect(alvoReconhecido(entrada({ type: 'text', target: { unique: ['text'], text: 'x' } }))).toBeNull();
});
