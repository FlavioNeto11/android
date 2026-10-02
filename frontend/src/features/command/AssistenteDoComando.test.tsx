// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { CommandRefinement, RefineCommandRequest } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeRun, makeRunDetail, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { responderExecucao } from '../runs/runActions';
import { AssistenteDoComando } from './AssistenteDoComando';
import { CommandPanel } from './CommandPanel';

/**
 * Assistente do comando (ADR-047): a IA reescreve o comando em blocos e pergunta o que falta; cada resposta entra
 * no texto na rodada seguinte. No Comando, "Usar este comando" troca o texto do campo; numa execução em
 * `needs_input`, responder cria a sucessora. Prova `simulated` (backend falso).
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const RODADA_1: CommandRefinement = {
  command: 'Objetivo: mandar um oi ao suporte\nConcluído quando: a mensagem aparece como enviada.',
  summary: 'Organizei o pedido em blocos.',
  questions: [{ field: 'app', question: 'Em qual app ou site?', options: ['QA Messenger', 'Instagram'], why: 'o planejador precisa saber onde agir' }],
  ready: false,
  notes: ['A senha do portal fica na conta da persona.'],
};
const RODADA_2: CommandRefinement = {
  command: 'Objetivo: mandar um oi ao suporte\nApp ou site: QA Messenger\nConcluído quando: a mensagem aparece como enviada.',
  summary: 'Incluí o app.', questions: [], ready: true, notes: [],
};

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  let n = 0;
  backend
    .on('POST', /^\/api\/flows\/match$/, () => json(null))
    .on('POST', /^\/api\/commands\/refine$/, () => json((n += 1) === 1 ? RODADA_1 : RODADA_2));
  backend.install();
  window.localStorage.clear();
  useAppStore.setState({ ...initialDataState });
  useAppStore.getState().hydrate(makeSnapshot());
  useUiStore.setState({ selectedIds: ['android-01'] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const pedidos = () => backend.callsTo('POST', /^\/api\/commands\/refine$/).map((c) => c.body as RefineCommandRequest);

describe('Assistente no Comando', () => {
  it('refina, pergunta, incorpora a resposta e "Usar este comando" troca o texto do campo', async () => {
    await act(async () => root.render(<CommandPanel />));
    const campo = byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement;
    await setValue(campo, 'mande um oi pro suporte');
    await click(byRole('button', /^Refinar com IA/));
    await waitFor(() => expect(text(container)).toContain('Em qual app ou site?'));
    expect(pedidos()[0]).toMatchObject({ command: 'mande um oi pro suporte', instance_ids: ['android-01'], answers: [] });
    expect(text(container)).toContain('A senha do portal fica na conta da persona.');
    expect(text(container)).toContain('1 pendência');

    await click(byRole('button', 'QA Messenger'));
    await click(byRole('button', /^Responder e refinar/));
    await waitFor(() => expect(text(container)).toContain('Pronto para planejar'));
    // a 2ª rodada parte do texto refinado e leva só a resposta nova
    expect(pedidos()[1]).toMatchObject({
      command: RODADA_1.command, answers: [{ field: 'app', question: 'Em qual app ou site?', answer: 'QA Messenger' }],
    });

    await click(byRole('button', /^Usar este comando/));
    expect(campo.value).toBe(RODADA_2.command);
    expect(container.querySelector('[aria-label="Assistente do comando"]')).toBeNull();
  });

  it('"Voltar ao texto original" desfaz a rodada sem chamar a IA de novo', async () => {
    await act(async () => root.render(<CommandPanel />));
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'mande um oi pro suporte');
    await click(byRole('button', /^Refinar com IA/));
    await waitFor(() => expect(text(container)).toContain('Organizei o pedido em blocos.'));
    await click(byRole('button', /^Voltar ao texto original/));
    expect(text(container)).not.toContain('Organizei o pedido em blocos.');
    expect(pedidos()).toHaveLength(1);
  });

  it('senha numa resposta não vai à IA', async () => {
    await act(async () => root.render(<CommandPanel />));
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'entre no portal');
    await click(byRole('button', /^Refinar com IA/));
    await waitFor(() => expect(text(container)).toContain('Em qual app ou site?'));
    await setValue(container.querySelector('li input') as HTMLInputElement, 'senha: hunter2');
    expect(text(container)).toContain('Há uma senha no texto ou numa resposta');
    await click(byRole('button', /^Responder e refinar/));
    expect(pedidos()).toHaveLength(1);
  });
});

describe('Responder a uma execução em needs_input', () => {
  it('as perguntas do planejador chegam abertas; a resposta vai com o run_id e a sucessora nasce com o texto refinado', async () => {
    const original = makeRun({ id: 'r-antiga', short_id: 'antiga', status: 'needs_input', command: 'mande um oi' });
    const nova = makeRun({ id: 'r-nova', short_id: 'nova', status: 'planning', command: RODADA_2.command });
    backend
      .on('POST', /^\/api\/runs\/r-antiga\/successor$/, () => json(nova))
      // a tela passa a mostrar a nova, e carrega o detalhe dela (sem esta rota, o 404 desmarcaria a seleção)
      .on('GET', /^\/api\/runs\/r-nova$/, () => json(makeRunDetail({ ...nova })));
    await act(async () => root.render(
      <AssistenteDoComando
        comando={original.command}
        contexto={{ run_id: original.id }}
        perguntasIniciais={[{ field: 'recipient', question: 'Para qual contato?', options: [], why: '' }]}
        acoes={(texto) => (
          <button type="button" onClick={() => void responderExecucao(original, texto, 'plan')}>Planejar com as respostas</button>
        )}
      />,
    ));
    expect(text(container)).toContain('Para qual contato?');
    await setValue(container.querySelector('li input') as HTMLInputElement, 'Suporte QA');
    await click(byRole('button', /^Responder e refinar/));
    await waitFor(() => expect(pedidos()).toHaveLength(1));
    expect(pedidos()[0]).toMatchObject({
      command: 'mande um oi', run_id: 'r-antiga',
      answers: [{ field: 'recipient', question: 'Para qual contato?', answer: 'Suporte QA' }],
    });
    await waitFor(() => expect(text(container)).toContain('Organizei o pedido em blocos.'));
    await click(byRole('button', 'Planejar com as respostas'));
    await waitFor(() => expect(backend.callsTo('POST', /successor$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /successor$/)[0]!.body).toEqual({ command: RODADA_1.command, mode: 'plan' });
    await waitFor(() => expect(useUiStore.getState().selectedRunId).toBe('r-nova'));
  });
});
