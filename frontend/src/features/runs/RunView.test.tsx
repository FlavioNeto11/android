// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { RUN_ID, makeEvent, makePersona, makeRun, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { perguntaSensivelDosEventos, perguntasDosEventos } from './model';
import { RunView } from './RunView';

/**
 * Execução que nasce `needs_input` por destino (ADR-044: homônimo, persona num aparelho com duas, texto × seleção):
 * não há plano, e as perguntas vêm no evento `log` com `data.questions`. A tela da execução as mostra no mesmo
 * cartão de sempre (o das perguntas do plano), com as opções pelo nome. Prova `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const PERGUNTA = {
  code: 'persona_no_aparelho', question: 'android-01 tem mais de uma persona (Marina Costa, Rafael Lima): qual delas faz isto?',
  field: 'profile_id', options: ['ig-1', 'ig-2'], instance_id: 'android-01', profile_id: null,
};

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/personas$/, () => json([makePersona('ig-1', 'Marina Costa'), makePersona('ig-2', 'Rafael Lima')]));
  const run = makeRun({ status: 'needs_input', status_detail: PERGUNTA.question });
  useAppStore.setState({
    ...initialDataState,
    hydrated: true,
    hydrateCount: 1,
    runs: [run],
    detail: {
      runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready',
      data: makeRunDetail({ ...run, plan: null, objectives: [], steps: [], attempts: [], evidence: [], plan_versions: [], decisions: [] }),
      events: [makeEvent(10, 'log', { questions: [PERGUNTA] }, { run_id: RUN_ID, level: 'warn',
        message: `Execução ${RUN_ID}: os destinos precisam de resposta antes de planejar` })],
    },
  });
  useUiStore.setState({ selectedRunId: RUN_ID });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it('needs_input por destino mostra a pergunta do evento, com o campo e as opções pelo nome', async () => {
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(text(container)).toContain('Falta decidir quem faz e onde'));
  const t = text(container);
  expect(t).toContain('tem mais de uma persona (Marina Costa, Rafael Lima)');
  expect(t).toContain('persona');                              // o campo, em português (não `profile_id`)
  await waitFor(() => expect(text(container)).toContain('Opções: Marina Costa, Rafael Lima.'));
  expect(t).not.toContain('O backend não informou quais dados faltam');
  expect(t).toContain('Por persona');                          // diz onde responder
});

it('perguntasDosEventos lê a pergunta mais recente e ignora eventos sem perguntas', () => {
  const antiga = { ...PERGUNTA, question: 'antiga' };
  const eventos = [
    makeEvent(1, 'log', { questions: [antiga] }),
    makeEvent(2, 'log', { questions: [PERGUNTA, { field: 'x' }] }),
    makeEvent(3, 'run.updated', { run: {} }),
  ];
  expect(perguntasDosEventos(eventos)).toEqual([{ field: 'profile_id', question: PERGUNTA.question, options: ['ig-1', 'ig-2'] }]);
  expect(perguntasDosEventos(null)).toEqual([]);
});

/**
 * 29.52: a pergunta que pede senha ou código não tem caixa de resposta. O painel reconhece pelo evento
 * `pergunta_sensivel` do backend (o vocabulário é um só, o da `TriagemDeCredencial`) e mostra o caminho certo.
 */
function comPerguntaDoPlano(question: string, field: string, tipo: string | null): void {
  const run = makeRun({ status: 'needs_input', status_detail: question });
  const eventos = tipo === null ? [] : [makeEvent(11, 'pergunta_sensivel', { tipo }, { run_id: RUN_ID, level: 'warn',
    message: `Execução ${RUN_ID}: a pergunta pede credencial (${tipo}), que não se responde por texto` })];
  useAppStore.setState({
    detail: {
      runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready',
      data: makeRunDetail({ ...run, plan: { ...makeRunDetail().plan!, missing: [{ field, question }] } }),
      events: eventos,
    },
    runs: [run],
  });
}

it('pergunta de senha: sem caixa de resposta, com o caminho da conta da persona', async () => {
  comPerguntaDoPlano('Qual é a senha da conta do QA Messenger?', 'password', 'senha');
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(text(container)).toContain('A senha não se responde aqui.'));
  expect(text(container)).toContain('Contas e acesso');
  expect(text(container)).not.toContain('Responda aqui');
  // o resumo diz o mesmo caminho, não "responder a pergunta da IA"
  expect(text(container)).toContain('Guardar a senha na conta da persona e pedir de novo');
  expect(text(container)).not.toContain('Responder a pergunta da IA');
  expect(container.querySelector('input')).toBeNull();
  const abrir = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Abrir Personas'));
  expect(abrir).toBeTruthy();
  await act(async () => { abrir!.click(); });
  expect(useUiStore.getState().view).toBe('personas');
});

it('pergunta de código: sem caixa de resposta, com o controle do aparelho', async () => {
  comPerguntaDoPlano('Qual o código que chegou por SMS?', 'code', 'codigo');
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(text(container)).toContain('O código não se responde aqui.'));
  expect(text(container)).toContain('Digitar o código no aparelho e pedir de novo');
  const abrir = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Abrir o aparelho'));
  expect(abrir).toBeTruthy();
  await act(async () => { abrir!.click(); });
  expect(useUiStore.getState().focusInstanceId).toBe(makeRun().instance_ids[0]);
});

it('sem o evento (execução anterior ao 29.52), a caixa de resposta continua; a recusa vem do backend', async () => {
  comPerguntaDoPlano('Qual é a senha da conta do QA Messenger?', 'password', null);
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(text(container)).toContain('Responda aqui'));
  expect(text(container)).not.toContain('não se responde aqui');
});

it('perguntaSensivelDosEventos lê o tipo do evento e ignora o resto', () => {
  expect(perguntaSensivelDosEventos([makeEvent(1, 'log', { questions: [] }), makeEvent(2, 'pergunta_sensivel', { tipo: 'codigo' })]))
    .toBe('codigo');
  expect(perguntaSensivelDosEventos([makeEvent(1, 'pergunta_sensivel', null)])).toBeNull();
  expect(perguntaSensivelDosEventos(null)).toBeNull();
});

/**
 * 31.87: o dado que a persona do aparelho não tem (`field: 'persona_data'`) NÃO é pergunta de destino nem se responde
 * numa caixa que completa o comando: o caminho é cadastrar o dado e criar a execução de novo.
 */
it('dado da persona ausente: mostra a pergunta e o caminho, sem caixa de resposta nem rodapé de destino', async () => {
  const pergunta = { code: 'dado_da_persona_ausente', field: 'persona_data', options: [], instance_id: 'android-12', profile_id: null,
    question: 'A persona do android-12 não tem sobrenome cadastrado: cadastre o dado na persona e peça de novo.' };
  const run = makeRun({ status: 'needs_input', status_detail: pergunta.question });
  useAppStore.setState({
    runs: [run],
    detail: {
      runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready',
      data: makeRunDetail({ ...run, plan: null, objectives: [], steps: [], attempts: [], evidence: [], plan_versions: [], decisions: [] }),
      events: [makeEvent(12, 'log', { questions: [pergunta] }, { run_id: RUN_ID, level: 'warn', message: 'falta dado da persona' })],
    },
  });
  await act(async () => { root.render(<RunView />); });
  await waitFor(() => expect(text(container)).toContain('Falta um dado da persona'));
  const t = text(container);
  expect(t).toContain('A persona do android-12 não tem sobrenome cadastrado');
  expect(t).toContain('Cadastre o dado na persona e crie a execução de novo.');
  expect(t).not.toContain('Responda aqui');                    // nada de caixa que completa o comando
  expect(t).not.toContain('Escolha no Comando');               // nem o rodapé de destino
  expect(t).not.toContain('Falta decidir quem faz e onde');
});
