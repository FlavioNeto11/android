// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { RUN_ID, makeEvent, makePersona, makeRun, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { perguntasDosEventos } from './model';
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
