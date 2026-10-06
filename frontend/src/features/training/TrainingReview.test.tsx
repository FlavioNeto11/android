// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { ConfirmHost } from '../../components/Confirm';
import { useToastStore } from '../../store/toasts';
import { alvoReconhecido, ESPERA_DA_PREVIA_MS, TrainingReview } from './TrainingReview';
import type { TrainingInput } from '../../api/types';
import { FakeBackend, allByRole, apiError, botaoPronto, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';

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
  await click(byRole('radio', /Escolher perfis e grupos/));
  await click(byRole('checkbox', /@aluno.dois/i));
  // P2.1: o rodapé salva um FLUXO e diz isso; "habilidade" fica para a versionada (ensino v2).
  expect(allByRole('button', /Salvar habilidade/i)).toHaveLength(0);
  await click(await botaoPronto(/Salvar como fluxo/i));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  const corpo = backend.callsTo('POST', /\/save$/)[0]!.body as { profile_ids: string[]; scope_on_proof: string };
  expect(corpo.profile_ids.sort()).toEqual(['ig-1', 'ig-2']);           // o perfil do aparelho já vem marcado
  expect(corpo.scope_on_proof).toBe('todos');                             // "escolher" manda as listas e `todos` (adendo v1.71)
  expect(text()).toContain('sem IA');
});

// ---------------------------------------------------------------- fase L: P1.4 — escopo que não carregou não é "todos"
it('se a lista de perfis falha, o escopo mostra o erro com "Tentar de novo" e o "Salvar" fica bloqueado até carregar', async () => {
  backend.on('GET', /\/instagram\/profiles$/, () => apiError(500, 'internal', 'banco indisponível'));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('QA-001'));
  await click(byRole('button', /Pedir proposta à IA/i));
  await click(await waitFor(() => byRole('radio', /Escolher perfis e grupos/)));       // a lista só importa em "Escolher"
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
  await waitFor(() => expect(byRole('button', /^Salvar como fluxo/).getAttribute('aria-disabled')).toBeNull());
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

// ---------------------------------------------------------------- fase F: "Gerar habilidade" atrás de `features.skills`
function comHabilidades(ligado: boolean | undefined): void {
  const health = makeSnapshot().health;
  useAppStore.setState({ health: { ...health, features: { ...health.features, skills: ligado } } });
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
// pela conversão da fase J. O ensino v2 saiu da tela (31.91 T1, ADR-078).
it('com features.skills ligado: nada de candidata; depois de salvar, "Gerar habilidade deste fluxo" converte o fluxo', async () => {
  comHabilidades(true);
  backend.on('POST', /\/flows\/mandar-mensagem\/adopt$/, () => json({
    flow_id: 'mandar-mensagem', skill_id: 'qa-messenger.mandar_mensagem', warnings: [],
    published: { ref: 'qa-messenger.mandar_mensagem@1' }, draft: { ref: 'qa-messenger.mandar_mensagem@2' },
  }));
  await act(async () => root.render(<><TrainingReview sessionId="trn-1" onClose={() => {}} /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('QA-001'));
  await flush(ATRASO_MAXIMO + 30);
  expect(text()).not.toContain('Habilidade versionada');
  expect(allByRole('button', /Gerar candidata de habilidade/i)).toHaveLength(0);
  expect(byRole('button', /Pedir proposta à IA/i).className).toMatch(/btnPrimary/);
  await click(byRole('button', /Pedir proposta à IA/i));
  await waitFor(() => expect(text()).toContain('O texto muda?'));
  expect(byRole('button', /^Salvar como fluxo/).className).toMatch(/btnPrimary/);

  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  expect(text()).toContain('Parâmetros com tipo, riscos e versões só existem na habilidade.');
  await click(byRole('button', /^Gerar habilidade deste fluxo$/));
  await click(await waitFor(() => byRole('button', /^Gerar habilidade$/, byRole('dialog', /Gerar a habilidade deste fluxo/))));
  await waitFor(() => expect(text()).toContain('qa-messenger.mandar_mensagem@2 em rascunho'));
  expect(backend.callsTo('POST', /\/adopt$/)).toHaveLength(1);
  expect(backend.callsTo('POST', /\/teaching-sessions$/)).toHaveLength(0);
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
  await click(await botaoPronto(/^Salvar como fluxo/));          // pronto: a prévia que a devolução pediu já respondeu
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
  await waitFor(() => expect(salvar().getAttribute('aria-disabled')).toBeNull());

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
  await click(await botaoPronto(/^Salvar como fluxo/));
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
  await waitFor(() => expect(salvar().getAttribute('aria-disabled')).toBeNull());
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
  expect(Object.keys(backend.callsTo('POST', /\/preview$/)[0]!.body as object).sort()).toEqual(['group_ids', 'profile_ids', 'proposal', 'scope_on_proof']);

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
  await click(await botaoPronto(/^Salvar como fluxo/));
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
  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  expect(text()).toContain('com IA');
  expect(text()).not.toContain('em prova');                             // sem `ensinado_em_prova`, nenhum selo (30.81)
  expect(backend.callsTo('POST', /\/recipes$/)).toHaveLength(0);       // quem aciona é a pessoa

  await click(byRole('button', /^Refazer receitas$/));
  await waitFor(() => expect(text()).toContain('1 receita gravada agora.'));
  expect(text()).toContain('— receita gravada');                        // o relatório troca pelo do refazer
  expect(backend.callsTo('POST', /\/recipes$/)).toHaveLength(1);
});

// ---------------------------------------------------------------- 30.81 (v1.65): o fluxo ensinado espera a prova
const SALVO_SEM_RECEITA = [{ key: 'abrir', title: 'Abrir a conversa', recipe: false, reason: 'aparelho fora do ar' }];

it('30.81: o salvar com `ensinado_em_prova` mostra o selo e diz que só a persona que ensinou usa até a prova; o refazer mantém ou tira', async () => {
  let refeitas = 0;
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA_OK));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem', warnings: [], steps: SALVO_SEM_RECEITA,
    ensinado_em_prova: { persona: 'ig-1', sessao: 'trn-1' },
  }));
  backend.on('POST', /\/training\/trn-1\/recipes$/, () => {
    refeitas += 1;
    const base = { session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem', created: 1,
                   steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }] };
    // 1ª: o fluxo ainda espera; 2ª: uma prova ou uma pessoa já o liberou (o campo some).
    return json(refeitas === 1 ? { ...base, ensinado_em_prova: { persona: 'ig-1', sessao: 'trn-1' } } : { ...base, created: 0 });
  });
  await abrirEProporComPrevia();
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  expect(text()).toContain('em prova');
  expect(text()).toContain('Até a prova, só a persona que ensinou pode pedir pelo comando');
  expect(text()).not.toContain('Quem estiver no escopo pode pedir');
  expect(document.querySelector('[title^="Ensinado e ainda sem prova: só vale para a persona que ensinou"]')).not.toBeNull();
  expect(text()).not.toContain('ig-1');                    // o id da persona não vai para a frase

  await click(byRole('button', /^Refazer receitas$/));
  await waitFor(() => expect(text()).toContain('Em prova: as receitas só valem para a persona que ensinou.'));
  expect(text()).toContain('em prova');

  await click(byRole('button', /^Refazer receitas$/));
  await waitFor(() => expect(text()).toContain('Quem estiver no escopo pode pedir pelo comando'));
  expect(text()).not.toContain('em prova');
});

it('30.81: a gravação sem persona diz que o fluxo não vale em aparelho nenhum até a prova', async () => {
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA_OK));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem', warnings: [], steps: SALVO_SEM_RECEITA,
    ensinado_em_prova: { persona: null, sessao: 'trn-1' },
  }));
  await abrirEProporComPrevia();
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Fluxo mandar-mensagem salvo'));
  expect(text()).toContain('a gravação não tinha persona: não vale em aparelho nenhum até uma prova real dar certo');
  // a linha que apresenta o comando não diz que uma persona que ensinou pode pedir: não há nenhuma
  expect(text()).toContain('Até a prova, o comando não vale em aparelho nenhum');
  expect(text()).not.toContain('só a persona que ensinou pode pedir');

  backend.on('POST', /\/training\/trn-1\/recipes$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'mandar-mensagem', created: 1,
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }],
    ensinado_em_prova: { persona: null, sessao: 'trn-1' },
  }));
  await click(byRole('button', /^Refazer receitas$/));
  await waitFor(() => expect(text()).toContain('a gravação não tinha persona, então as receitas não valem em aparelho nenhum.'));
  expect(text()).not.toContain('as receitas só valem para a persona que ensinou');
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

// ---------------------------------------------------------------- 31.90-E: corrigir o que a etapa confere e os parâmetros
const abrirResumo = async (nome: RegExp) => {
  const resumo = Array.from(document.querySelectorAll('summary')).find((s) => nome.test(s.textContent ?? ''));
  expect(resumo, `resumo ${nome}`).toBeTruthy();
  await click(resumo!);
};
const corpoDoSave = () => backend.callsTo('POST', /\/save$/)[0]!.body as { proposal: { parameters: { name: string; example: string; description: string }[];
  steps: { postcondition: { kind: string; value: string; description: string } }[] } };

it('31.90-E: o que a etapa confere se edita (tipo, valor, descrição) e o save leva a proposta editada', async () => {
  await abrirComProposta(SESSAO, PROPOSTA);
  await abrirResumo(/Editar o que a etapa confere/);
  const tipo = byRole('combobox', /Tipo de conferência da etapa 1/) as HTMLSelectElement;
  expect(tipo.value).toBe('text_visible');
  await setValue(byRole('textbox', /Texto que aparece \(etapa 1\)/) as HTMLInputElement, 'Mensagem enviada');
  await setValue(byRole('textbox', /O que a tela mostra depois \(etapa 1\)/) as HTMLInputElement, 'a bolha aparece');
  await setValue(tipo, 'element_present');
  expect(byRole('textbox', /Elemento \(id ou texto\) \(etapa 1\)/)).toBeTruthy();   // o rótulo do valor acompanha o tipo
  await setValue(tipo, 'model_judged');
  expect(allByRole('textbox', /\(etapa 1\)$/).map((e) => e.getAttribute('aria-label'))).toEqual(['O que a tela mostra depois (etapa 1)']); // a IA julga: só a descrição
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(backend.callsTo('POST', /\/save$/)).toHaveLength(1));
  expect(corpoDoSave().proposal.steps[0]!.postcondition).toEqual({ kind: 'model_judged', value: 'Mensagem enviada', description: 'a bolha aparece' });
});

it('31.90-E: o exemplo do parâmetro se edita (a descrição não: o salvar a jogaria fora) e o save o leva', async () => {
  await abrirComProposta(SESSAO, PROPOSTA);
  await abrirResumo(/Editar os exemplos dos parâmetros/);
  expect(allByRole('textbox', /Nome de \{contato\}/)).toHaveLength(0);
  expect(allByRole('textbox', /Descrição de \{contato\}/)).toHaveLength(0);
  await setValue(byRole('textbox', /Exemplo de \{contato\}/) as HTMLInputElement, 'QA-002');
  await waitFor(() => expect(text()).toContain('{contato} = QA-002'));
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(backend.callsTo('POST', /\/save$/)).toHaveLength(1));
  expect(corpoDoSave().proposal.parameters).toEqual([{ name: 'contato', example: 'QA-002', description: '' }]);
});

it('31.90-E: a etapa com efeito e sem comprovação já abre o editor; a que comprova, não', async () => {
  const comEfeito = { ...PROPOSTA.steps[0]!, side_effect: true, postcondition: { kind: 'model_judged', value: '', description: '' } };
  await abrirComProposta(SESSAO, { ...PROPOSTA, steps: [comEfeito] });
  const editor = () => Array.from(document.querySelectorAll('details')).find((d) => /Editar o que a etapa confere/.test(d.textContent ?? ''))!;
  expect(editor().open).toBe(true);
  await setValue(byRole('textbox', /O que a tela mostra depois \(etapa 1\)/) as HTMLInputElement, 'a bolha aparece');
  expect(editor().open).toBe(true);                                                    // preencher não fecha o que está sendo digitado
});

it('31.90-E: a etapa que já comprova (ou sem efeito) deixa o editor fechado', async () => {
  await abrirComProposta(SESSAO, { ...PROPOSTA, steps: [{ ...PROPOSTA.steps[0]!, side_effect: true }] });
  const editor = Array.from(document.querySelectorAll('details')).find((d) => /Editar o que a etapa confere/.test(d.textContent ?? ''))!;
  expect(editor.open).toBe(false);
});

it('31.90-E: a recusa do servidor (pos_condicao_vazia) aparece no Salvar, como as outras', async () => {
  backend.on('POST', /\/training\/trn-1\/save$/, () => apiError(400, 'pos_condicao_vazia', 'A etapa com efeito precisa dizer como comprovar.'));
  await abrirComProposta(SESSAO, PROPOSTA);
  await click(byRole('button', /^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('A etapa com efeito precisa dizer como comprovar.'));
});

it('31.90-E: etapa com ação do catálogo não tem editor da conferência (o salvar a refaz pelo catálogo) e diz de onde ela vem', async () => {
  const doCatalogo = { ...PROPOSTA.steps[0]!, side_effect: true, capability: 'SEND_MESSAGE', postcondition: { kind: 'model_judged', value: '', description: '' } };
  await abrirComProposta(SESSAO, { ...PROPOSTA, steps: [doCatalogo] });
  expect(text()).toContain('O que esta etapa confere vem da ação do catálogo “SEND_MESSAGE” e não muda por aqui.');
  expect(allByRole('combobox', /Tipo de conferência da etapa 1/)).toHaveLength(0);
  expect(Array.from(document.querySelectorAll('summary')).some((x) => /Editar o que a etapa confere/.test(x.textContent ?? ''))).toBe(false);
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

// ---------------------------------------------------------------- 31.88 F2 (adendo v1.71): "Vale para"
const SALVO = { session: { ...SESSAO, status: 'saved' }, flow_id: 'f-0a1b2c3d4e5f', steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }] };
const corpoDe = (rota: RegExp) => backend.callsTo('POST', rota)[0]!.body as Record<string, unknown>;

it('31.88 F2: o padrão é "Todos, depois de provado": o corpo leva scope_on_proof todos e listas vazias, e a lista que falhou não trava o Salvar', async () => {
  backend.on('GET', /\/instagram\/profiles$/, () => apiError(500, 'internal', 'banco indisponível'));
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json({ ...PREVIA_OK, scope: { on_proof: 'todos', profile_ids: [], group_ids: [] } }));
  await abrirComProposta(SESSAO, PROPOSTA);
  expect((byRole('radio', /Todos, depois de provado/) as HTMLInputElement).checked).toBe(true);
  await waitFor(() => expect(text()).toContain('Ao salvar vale para todos os perfis, depois de provado.'));
  expect(text()).toContain('Até a prova passar, só a persona que ensinou usa o fluxo');
  expect(allByRole('checkbox', /@aluno/)).toHaveLength(0);                    // as listas só aparecem em "Escolher"
  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(backend.callsTo('POST', /\/save$/)).toHaveLength(1));
  expect(corpoDe(/\/save$/)).toMatchObject({ scope_on_proof: 'todos', profile_ids: [], group_ids: [] });
  expect(corpoDe(/\/preview$/)).toMatchObject({ scope_on_proof: 'todos', profile_ids: [], group_ids: [] });
});

it('31.88 F2: "Só quem ensinou" manda scope_on_proof sem listas, e a prévia e o resultado mostram o escopo que o servidor devolveu', async () => {
  const escopo = { on_proof: 'quem_ensinou' as const, profile_ids: [] as string[], group_ids: [] as string[] };
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json({ ...PREVIA_OK, scope: escopo }));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({ ...SALVO, scope: escopo }));
  await abrirComProposta(SESSAO, PROPOSTA);
  await click(byRole('radio', /Só quem ensinou/));
  await waitFor(() => expect(text()).toContain('Ao salvar vale para só a persona que ensinou, também depois da prova.'));
  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Vale para só a persona que ensinou, também depois da prova.'));
  const corpo = corpoDe(/\/save$/);
  expect(corpo.scope_on_proof).toBe('quem_ensinou');
  expect(corpo).not.toHaveProperty('profile_ids');                             // com lista junto, a API recusa (scope_ambiguous)
  expect(corpo).not.toHaveProperty('group_ids');
});

it('31.88 F2: "Escolher perfis e grupos" manda as listas com scope_on_proof todos e o resultado diz quem foi escolhido', async () => {
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({ ...SALVO, scope: { on_proof: 'todos', profile_ids: ['ig-2'], group_ids: [] } }));
  await abrirComProposta(SESSAO, PROPOSTA);
  await click(byRole('radio', /Escolher perfis e grupos/));
  await click(await waitFor(() => byRole('checkbox', /@aluno.um/)));              // o perfil do aparelho vinha marcado: sai
  await click(byRole('checkbox', /@aluno.dois/));
  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Vale para @aluno.dois.'));
  expect(corpoDe(/\/save$/)).toMatchObject({ scope_on_proof: 'todos', profile_ids: ['ig-2'], group_ids: [] });
});

it('31.88 F2: treino sem persona: "Só quem ensinou" fica desabilitado com o motivo, antes de a API recusar (no_teacher_persona)', async () => {
  await abrirComProposta({ ...SESSAO, profile_id: null }, PROPOSTA);
  const so = byRole('radio', /Só quem ensinou/) as HTMLInputElement;
  expect(so.disabled).toBe(true);
  expect(text()).toContain('Este treino não teve persona: não há “quem ensinou”.');
  expect((byRole('radio', /Todos, depois de provado/) as HTMLInputElement).checked).toBe(true);
});

// ---------------------------------------------------------------- 31.91 T1 (ADR-078): o ensino v2 saiu da tela
it('31.91 T1: mesmo com skills ligado e um ensino antigo na gravação, a revisão não o mostra nem o consulta', async () => {
  comHabilidades(true);
  backend.on('GET', /\/teaching-sessions/, () => json([{ id: 'ens-1' }]));
  backend.on('GET', /\/teaching-sessions\/ens-1$/, () => json({ id: 'ens-1', status: 'asking', open_questions: [] }));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('entradas'));
  await flush(ATRASO_MAXIMO + 30);
  expect(text()).not.toContain('Habilidade versionada');
  expect(text()).not.toContain('fica só para leitura');
  expect(backend.calls.filter((c) => /teaching-sessions/.test(c.path))).toHaveLength(0);
});

it('junção 30.81 + 31.88 F2: no resultado o selo "em prova" vem primeiro e a linha "Vale para" logo abaixo, sem repetir a frase da prova', async () => {
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA_OK));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'f-0a1b2c3d4e5f', warnings: [],
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }],
    ensinado_em_prova: { persona: 'ig-1', sessao: 'trn-1' }, scope: { on_proof: 'todos', profile_ids: [], group_ids: [] },
  }));
  await abrirEProporComPrevia();
  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Vale para todos os perfis, depois de provado.'));
  const t = text();
  expect(t.indexOf('em prova')).toBeGreaterThan(-1);
  expect(t.indexOf('em prova')).toBeLessThan(t.indexOf('Vale para todos os perfis'));          // selo primeiro, escopo logo abaixo
  expect(t.split('Até a prova').length - 1).toBeLessThanOrEqual(1);                             // a frase da prova não aparece duas vezes
  expect(t).not.toContain('Até a prova passar, só a persona que ensinou usa o fluxo');         // a do 30.81 já diz isso
});

// ---------------------------------------------------------------- 31.89 (adendo v1.72): colisão de comando ao salvar
it('31.89: o aviso de colisão do comando aparece na prévia e no resultado como AVISO: não trava o Salvar e não vira recusa', async () => {
  const colisao = 'O comando “mande {mensagem} para {contato}” colide com a habilidade “enviar mensagem” (f-0a1b2c3d4e5f): os dois casam o mesmo texto e o novo passa na frente.';
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json({ ...PREVIA_OK, warnings: [colisao] }));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'f-0a1b2c3d4e5f',
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }], warnings: [colisao],
  }));
  await abrirEProporComPrevia();
  await waitFor(() => expect(regiao('Avisos da prévia', 'ul').textContent).toContain('colide com a habilidade “enviar mensagem”'));
  expect(byRole('textbox', /Comando/).getAttribute('aria-invalid')).toBeNull();      // aviso, não recusa do comando
  expect(byRole('button', /^Salvar como fluxo/).getAttribute('aria-disabled')).toBeNull();
  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(regiao('Avisos do salvar', 'ul').textContent).toContain('colide com a habilidade “enviar mensagem”'));
});

// ---------------------------------------------------------------- 31.111 F5 (adendo v1.75): treino que nasceu de uma etapa que falhou
const ORIGEM = {
  run_id: 'run-0001', step_id: 'run-0001:android-01:v1:open_app', step_key: 'open_app', attempt_id: 'att-1',
  motivo: 'A tela esperada não apareceu.',
  context: {
    disponivel: true,
    trilha: [
      { step_id: 'run-0001:android-01:v1:login', step_key: 'login', titulo: 'Entrar', status: 'succeeded', motivo: null, falhou: false },
      { step_id: 'run-0001:android-01:v1:open_app', step_key: 'open_app', titulo: 'Abrir o app', status: 'failed', motivo: 'tela errada', falhou: true },
    ],
    esperado: { kind: 'text_visible', value: 'Conversas', description: 'a lista de conversas' },
    tentativa: { number: 2, status: 'failed', erro: 'timeout', failure_kind: 'postcondition', failure_screen: 'Configurações', strategy: 'recipe' },
    evidencias: [{ id: 41, kind: 'screenshot', nota: 'tela da falha', disponivel: true }, { id: 42, kind: 'screenshot', nota: 'tela anterior', disponivel: false }],
  },
};

it('31.111: a sessão com origem mostra o selo, o motivo, o que a execução fez, o esperado, a tentativa e as imagens', async () => {
  backend.on('GET', /\/training\/trn-1$/, () => json({ ...SESSAO, origin: ORIGEM }));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  const origem = await waitFor(() => regiao('Origem do treino'));
  expect(origem.textContent).toContain('corrige uma falha');
  expect(origem.textContent).toContain('Etapa open_app da execução run-0001');
  expect(origem.textContent).toContain('Motivo: A tela esperada não apareceu.');
  const resumoDoContexto = Array.from(document.querySelectorAll('summary')).find((x) => /O que a execução fez, o esperado e as imagens/.test(x.textContent ?? ''))!;
  await click(resumoDoContexto);
  const o = regiao('Origem do treino').textContent!;
  expect(o).toContain('Entrar');
  expect(o).toContain('a que falhou');
  expect(o).toContain('A etapa esperava: a lista de conversas (Conversas)');
  expect(o).toContain('Tentativa 2 (failed)');
  expect(o).toContain('postcondition');
  expect(o).toContain('estratégia recipe');
  expect(o).toContain('timeout');
  expect(o).toContain('tela: Configurações');
  const imagens = [...document.querySelectorAll<HTMLImageElement>('ul[aria-label="Imagens da falha"] img')];
  expect(imagens).toHaveLength(1);                                                                // a redigida não vira <img>
  expect(imagens[0]!.getAttribute('src')).toMatch(/\/evidence\/41$/);
  expect(imagens[0]!.alt).toBe('tela da falha');
  expect(o).toContain('tela anterior: não disponível (redigida)');
  expect(document.querySelector('[aria-current="step"]')!.textContent).toContain('Abrir o app');
});

it('31.111: sem origem (gravação comum) nada de selo; etapa apagada diz que já não existe; contexto indisponível diz isso', async () => {
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  await waitFor(() => expect(text()).toContain('QA-001'));
  expect(document.querySelector('section[aria-label="Origem do treino"]')).toBeNull();
  expect(text()).not.toContain('corrige uma falha');
  await act(async () => root.unmount());
  root = createRoot(container);

  backend.on('GET', /\/training\/trn-1$/, () => json({ ...SESSAO, origin: { ...ORIGEM, attempt_id: null, motivo: null, context: { disponivel: false } } }));
  await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
  const origem = await waitFor(() => regiao('Origem do treino'));
  expect(origem.textContent).toContain('A etapa já não existe');
  expect(origem.textContent).toContain('O contexto da execução já não está disponível.');
  expect(Array.from(document.querySelectorAll('summary')).some((x) => /O que a execução fez/.test(x.textContent ?? ''))).toBe(false);
});

it('31.111: o fluxo salvo nasce com o selo "corrige uma falha" e a frase de onde veio', async () => {
  backend.on('GET', /\/training\/trn-1$/, () => json({ ...SESSAO, origin: ORIGEM }));
  backend.on('POST', /\/training\/trn-1\/preview$/, () => json(PREVIA_OK));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved', origin: ORIGEM }, flow_id: 'f-0a1b2c3d4e5f', warnings: [],
    steps: [{ key: 'abrir', title: 'Abrir a conversa', recipe: true, reason: 'receita gravada' }],
  }));
  await abrirEProporComPrevia();
  await click(await botaoPronto(/^Salvar como fluxo/));
  await waitFor(() => expect(text()).toContain('Este fluxo nasceu da correção da etapa open_app da execução run-0001.'));
});
