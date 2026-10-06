// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { TrainingProposal } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { FakeBackend, allByRole, botaoPronto, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { estaMascarado, paraExibir, refs, textoNaTela } from './exibicao';
import { TrainingReview } from './TrainingReview';

/**
 * 31.189 (adendo v1.109): a tela do ensino exibe `proposal_exibicao` (a proposta com o dado da persona trocado por `{nome}`) no lugar da
 * proposta crua, e manda ao salvar a proposta ORIGINAL (com as edições da pessoa), nunca a cópia. Prova `simulated`: servidor falso.
 */

const passo = (key: string, inputs: number[], title: string, goal: string, valor: string, descricao: string) => ({
  key, title, goal, inputs, side_effect: false, capability: null, bindings: [], app_id: null,
  postcondition: { kind: 'text_visible' as const, value: valor, description: descricao },
});

/** A proposta como o central a guarda (com o dado da persona) e a cópia para exibir (com `{contato}`). */
const CRUA: TrainingProposal = {
  summary: 'Abrir o perfil de Marina Souza', command_template: 'abra o perfil de Marina Souza e curta',
  app_id: 'qa-messenger',
  parameters: [{ name: 'contato', example: 'Marina Souza', description: 'quem receberá' }],
  steps: [
    passo('abrir', [1], 'Abrir o perfil de Marina Souza', 'chegar em Marina Souza', 'Marina Souza', 'o perfil de Marina Souza aberto'),
    passo('curtir', [2], 'Curtir a primeira foto', 'curtir', 'Curtido', 'a foto curtida'),
  ],
  discarded: [{ seq: 3, why: 'toque fora do perfil de Marina Souza' }],
  questions: ['A Marina Souza muda a cada vez?'],
  answers: [{ question: 'Qual o nome do contato?', answer: 'Marina Souza' }],
};
const MASCARADA: TrainingProposal = {
  summary: 'Abrir o perfil de {contato}', command_template: 'abra o perfil de {contato} e curta',
  app_id: 'qa-messenger',
  parameters: [{ name: 'contato', example: '{contato}', description: 'quem receberá' }],
  steps: [
    passo('abrir', [1], 'Abrir o perfil de {contato}', 'chegar em {contato}', '{contato}', 'o perfil de {contato} aberto'),
    passo('curtir', [2], 'Curtir a primeira foto', 'curtir', 'Curtido', 'a foto curtida'),
  ],
  discarded: [{ seq: 3, why: 'toque fora do perfil de {contato}' }],
  questions: ['A {contato} muda a cada vez?'],
  answers: [{ question: 'Qual o nome do contato?', answer: '{contato}' }],
};
const EXIBICAO = { original: CRUA, copia: MASCARADA };

describe('exibicao.ts: qual texto aparece', () => {
  it('textoNaTela: o intocado vem da cópia; o editado, o da pessoa; sem cópia, o de sempre', () => {
    expect(textoNaTela('Marina', 'Marina', '{contato}')).toBe('{contato}');
    expect(textoNaTela('Outra', 'Marina', '{contato}')).toBe('Outra');
    expect(textoNaTela('Marina', 'Marina', undefined)).toBe('Marina');
    expect(textoNaTela('Marina', undefined, '{contato}')).toBe('Marina');
  });

  it('estaMascarado só quando o valor é o original e a cópia é outra coisa', () => {
    expect(estaMascarado('Marina', { original: 'Marina', exibido: '{contato}' })).toBe(true);
    expect(estaMascarado('Marina', { original: 'Marina', exibido: 'Marina' })).toBe(false);     // nada a esconder
    expect(estaMascarado('Marina!', { original: 'Marina', exibido: '{contato}' })).toBe(false);  // a pessoa mexeu
    expect(estaMascarado('Marina', { original: undefined, exibido: undefined })).toBe(false);
  });

  it('paraExibir troca todo texto intocado e deixa os identificadores e a estrutura da proposta', () => {
    const v = paraExibir(CRUA, EXIBICAO);
    expect(v.summary).toBe('Abrir o perfil de {contato}');
    expect(v.command_template).toBe('abra o perfil de {contato} e curta');
    expect(v.parameters).toEqual([{ name: 'contato', example: '{contato}', description: 'quem receberá' }]);
    expect(v.steps.map((s) => [s.key, s.title, s.goal, s.postcondition.value, s.postcondition.description, s.inputs])).toEqual([
      ['abrir', 'Abrir o perfil de {contato}', 'chegar em {contato}', '{contato}', 'o perfil de {contato} aberto', [1]],
      ['curtir', 'Curtir a primeira foto', 'curtir', 'Curtido', 'a foto curtida', [2]],
    ]);
    expect(v.discarded).toEqual([{ seq: 3, why: 'toque fora do perfil de {contato}' }]);
    expect(v.questions).toEqual(['A {contato} muda a cada vez?']);
    expect(v.answers).toEqual([{ question: 'Qual o nome do contato?', answer: '{contato}' }]);
    expect(JSON.stringify(CRUA)).toContain('Marina Souza');                                      // a original não foi tocada
  });

  it('o que a pessoa editou aparece como ela escreveu; casa a etapa pela chave, não pela posição', () => {
    const editada: TrainingProposal = { ...CRUA, steps: [CRUA.steps[1]!, { ...CRUA.steps[0]!, title: 'Entrar no perfil' }] };
    const v = paraExibir(editada, EXIBICAO);
    expect(v.steps.map((s) => s.title)).toEqual(['Curtir a primeira foto', 'Entrar no perfil']);
    expect(v.steps[1]!.goal).toBe('chegar em {contato}');                                       // o objetivo continua intocado
  });

  it('sem cópia (backend anterior) devolve a própria proposta', () => {
    expect(paraExibir(CRUA, { original: CRUA, copia: null })).toBe(CRUA);
    expect(paraExibir(CRUA, { original: CRUA, copia: undefined })).toBe(CRUA);
    expect(refs.comando({ original: null, copia: null })).toEqual({ original: undefined, exibido: undefined });
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const SESSAO = {
  id: 'trn-1', instance_id: 'android-01', profile_id: 'ig-1', app_id: 'qa-messenger', intent: 'Curtir', status: 'proposed', operator: null,
  proposal: CRUA, proposal_exibicao: MASCARADA, flow_id: null, created_at: '', finished_at: '', updated_at: '',
  inputs: [
    { session_id: 'trn-1', seq: 1, ts: '', type: 'tap', x: 10, y: 10, x2: null, y2: null, key_name: null, text: null, has_text: false, text_len: null,
      package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null, screen_lines: [], sensitive: false },
    { session_id: 'trn-1', seq: 2, ts: '', type: 'tap', x: 20, y: 20, x2: null, y2: null, key_name: null, text: null, has_text: false, text_len: null,
      package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null, screen_lines: [], sensitive: false },
    { session_id: 'trn-1', seq: 3, ts: '', type: 'tap', x: 30, y: 30, x2: null, y2: null, key_name: null, text: null, has_text: false, text_len: null,
      package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null, screen_lines: [], sensitive: false },
  ],
};

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training\/trn-1$/, () => json(SESSAO));
  backend.on('GET', /\/instagram\/profiles$/, () => json([{ id: 'ig-1', username: 'aluno.um' }]));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('POST', /\/training\/trn-1\/save$/, () => json({
    session: { ...SESSAO, status: 'saved' }, flow_id: 'curtir', steps: [{ key: 'abrir', title: 'Abrir', recipe: true, reason: 'receita gravada' }],
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

const abrir = async () => {
  await act(async () => root.render(<><TrainingReview sessionId="trn-1" onClose={() => {}} /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('Curtir a primeira foto'));
};
const valorDe = (nome: RegExp) => (byRole('textbox', nome) as HTMLInputElement).value;
const abrirResumo = async (nome: RegExp) => {
  const resumo = Array.from(document.querySelectorAll('summary')).find((x) => nome.test(x.textContent ?? ''));
  expect(resumo, `resumo ${String(nome)}`).toBeTruthy();
  await click(resumo!);
};
const salvoNoCorpo = () => (backend.callsTo('POST', /\/save$/)[0]!.body as { proposal: TrainingProposal }).proposal;

describe('a tela do ensino', () => {
  it('exibe a cópia mascarada em todo lugar e não deixa o dado da persona na tela', async () => {
    await abrir();
    expect(valorDe(/^Comando/)).toBe('abra o perfil de {contato} e curta');
    expect(valorDe(/Título da etapa 1/)).toBe('Abrir o perfil de {contato}');
    expect(valorDe(/Objetivo da etapa 1/)).toBe('chegar em {contato}');
    expect(text()).toContain('{contato} = {contato}');                                           // o selo do parâmetro
    expect(text()).toContain('A {contato} muda a cada vez?');
    expect(text()).toContain('Qual o nome do contato?');
    expect(text()).toContain('toque fora do perfil de {contato}');
    expect(text()).not.toContain('Marina');
    for (const el of document.querySelectorAll('input')) expect(el.value).not.toContain('Marina');
    // os campos mascarados são só leitura até a pessoa pedir
    expect((byRole('textbox', /^Comando/) as HTMLInputElement).readOnly).toBe(true);
    expect(allByRole('button', /Mostrar para editar: Comando/)).toHaveLength(1);
  });

  it('salva a proposta ORIGINAL intacta: nunca a cópia mascarada', async () => {
    await abrir();
    await click(await botaoPronto(/Salvar como fluxo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/save$/)).toHaveLength(1));
    expect(salvoNoCorpo()).toEqual(CRUA);
    expect(salvoNoCorpo().command_template).toBe('abra o perfil de Marina Souza e curta');
    expect(salvoNoCorpo().steps[0]!.title).toBe('Abrir o perfil de Marina Souza');
  });

  it('a prévia também vai com a original', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json({ steps: [], warnings: [] }));
    await abrir();
    await waitFor(() => expect(backend.callsTo('POST', /\/preview$/).length).toBeGreaterThan(0));
    for (const c of backend.callsTo('POST', /\/preview$/)) expect((c.body as { proposal: TrainingProposal }).proposal).toEqual(CRUA);
  });

  it('"Mostrar para editar" revela o texto de verdade só daquele campo; editar muda só ele e o resto segue original', async () => {
    await abrir();
    await click(byRole('button', /Mostrar para editar: Título da etapa 1/));
    expect(valorDe(/Título da etapa 1/)).toBe('Abrir o perfil de Marina Souza');
    expect((byRole('textbox', /Título da etapa 1/) as HTMLInputElement).readOnly).toBe(false);
    expect(valorDe(/^Comando/)).toBe('abra o perfil de {contato} e curta');                      // o outro continua mascarado
    await setValue(byRole('textbox', /Título da etapa 1/) as HTMLInputElement, 'Entrar no perfil de Marina Souza');
    await click(await botaoPronto(/Salvar como fluxo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/save$/)).toHaveLength(1));
    expect(salvoNoCorpo()).toEqual({ ...CRUA, steps: [{ ...CRUA.steps[0]!, title: 'Entrar no perfil de Marina Souza' }, CRUA.steps[1]!] });
  });

  it('o comando editado aparece como a pessoa escreveu e vai assim; as etapas seguem as originais', async () => {
    await abrir();
    await click(byRole('button', /Mostrar para editar: Comando/));
    await setValue(byRole('textbox', /^Comando/) as HTMLInputElement, 'abra o perfil de {contato} e curta já');
    expect(valorDe(/^Comando/)).toBe('abra o perfil de {contato} e curta já');
    expect(allByRole('button', /Mostrar para editar: Comando/)).toHaveLength(0);
    await click(await botaoPronto(/Salvar como fluxo/i));
    await waitFor(() => expect(backend.callsTo('POST', /\/save$/)).toHaveLength(1));
    expect(salvoNoCorpo().command_template).toBe('abra o perfil de {contato} e curta já');
    expect(salvoNoCorpo().steps).toEqual(CRUA.steps);
  });

  it('o exemplo do parâmetro e a conferência também são mascarados e se revelam sob pedido', async () => {
    await abrir();
    await abrirResumo(/Editar os exemplos dos parâmetros/);
    expect(valorDe(/Exemplo de \{contato\}/)).toBe('{contato}');
    await click(byRole('button', /Mostrar para editar: Exemplo de \{contato\}/));
    expect(valorDe(/Exemplo de \{contato\}/)).toBe('Marina Souza');
    await abrirResumo(/Editar o que a etapa confere/);
    expect(valorDe(/O que a tela mostra depois \(etapa 1\)/)).toBe('o perfil de {contato} aberto');
    expect(valorDe(/Texto que aparece \(etapa 1\)/)).toBe('{contato}');
  });

  it('as perguntas aparecem mascaradas, mas a resposta vai com a pergunta ORIGINAL', async () => {
    backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...SESSAO, proposal: CRUA, proposal_exibicao: MASCARADA }));
    await abrir();
    await setValue(byRole('textbox', /A \{contato\} muda a cada vez\?/) as HTMLInputElement, 'sim');
    await click(byRole('button', /Pedir nova proposta com as respostas/));
    await waitFor(() => expect(backend.callsTo('POST', /\/propose$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /\/propose$/)[0]!.body as { answers: { question: string; answer: string }[] };
    expect(corpo.answers).toEqual([{ question: 'A Marina Souza muda a cada vez?', answer: 'sim' }]);
  });

  it('proposta pedida agora (a resposta não traz a cópia): relê a sessão e passa a exibir a cópia', async () => {
    let lidas = 0;
    backend.on('GET', /\/training\/trn-1$/, () => { lidas += 1; return json(lidas === 1 ? { ...SESSAO, proposal: null, proposal_exibicao: null, status: 'recorded' } : SESSAO); });
    backend.on('POST', /\/training\/trn-1\/propose$/, () => json({ ...SESSAO, proposal: CRUA, proposal_exibicao: undefined }));
    await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
    await click(await waitFor(() => byRole('button', /Pedir proposta à IA/i)));
    await waitFor(() => expect(valorDe(/^Comando/)).toBe('abra o perfil de {contato} e curta'));
    expect(text()).not.toContain('Marina');
    expect(lidas).toBe(2);
  });

  it('backend anterior, sem a cópia: mostra a proposta como veio, sem botão de revelar', async () => {
    backend.on('GET', /\/training\/trn-1$/, () => json({ ...SESSAO, proposal_exibicao: undefined }));
    await act(async () => root.render(<TrainingReview sessionId="trn-1" onClose={() => {}} />));
    await waitFor(() => expect(valorDe(/^Comando/)).toBe('abra o perfil de Marina Souza e curta'));
    expect(allByRole('button', /Mostrar para editar/)).toHaveLength(0);
    expect((byRole('textbox', /^Comando/) as HTMLInputElement).readOnly).toBe(false);
  });
});
