// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, allByRole, byRole, click, flush, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { TrainingBar } from './TrainingBar';
import { TrainingReview } from './TrainingReview';

/**
 * 31.119 (leitura de UX da lista "Para revisar"): uma linha por sessão com o estado, o tempo e as entradas; o selo "corrige
 * uma falha" abre a execução de origem; gravação sem nenhuma entrada só pode ser descartada. Prova `simulated`.
 */

const ATRASO_MAXIMO = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const ha = (horas: number) => new Date(Date.now() - horas * 3_600_000).toISOString();
const sessao = (id: string, intent: string, status: string, extra: object = {}) => ({
  id, instance_id: 'android-01', profile_id: null, app_id: null, intent, status, operator: null, proposal: null, flow_id: null,
  created_at: ha(3), finished_at: ha(3), updated_at: ha(3), input_count: 2, inputs: [], ...extra,
});
const ORIGEM = { run_id: 'r-20261006053318-c04149', step_id: 'r-20261006053318-c04149:android-04:v1:check_item', step_key: 'check_item', attempt_id: null, motivo: 'A tela esperada não apareceu.' };
const ENTRADA = { session_id: 'trn-v', seq: 1, ts: '', type: 'tap', x: 1, y: 1, x2: null, y2: null, key_name: null, text: null, has_text: false,
  text_len: null, package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null, screen_lines: [], sensitive: false };

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  useToastStore.setState({ toasts: [] });
  window.location.hash = '';
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
});

const barra = (control: 'none' | 'user' = 'none', lease: string | null = null) => (
  <>
    <TrainingBar instance={makeInstance(1, { state: 'online', control })} leaseId={lease} mine={lease !== null} />
    <ConfirmHost />
  </>
);

it('"Para revisar": uma linha por sessão com estado, há quanto tempo e quantas entradas; o nome inteiro fica no rótulo', async () => {
  backend.on('GET', /\/training$/, () => json([
    sessao('trn-a', 'Corrigir a etapa «Abrir o app»', 'recorded', { input_count: 0 }),
    sessao('trn-b', 'Responder a DM', 'proposed', { input_count: 1, updated_at: ha(26) }),
  ]));
  await act(async () => root.render(barra()));
  await waitFor(() => expect(text()).toContain('Para revisar:'));
  const linhas = [...document.querySelectorAll<HTMLElement>('ul > li')].filter((li) => /Revisar|Corrigir a etapa|Responder a DM/.test(li.textContent!) && li.querySelector('button'));
  expect(linhas).toHaveLength(2);
  expect(linhas[0]!.textContent).toContain('sem proposta ainda · há 3 h · 0 entradas');
  expect(linhas[1]!.textContent).toContain('proposta pronta · há 1 dia');                    // 26 h atrás
  expect(linhas[1]!.textContent).toContain('1 entrada');
  expect(linhas[0]!.textContent).not.toContain('só gravada');
  expect(byRole('button', /^Revisar “Corrigir a etapa «Abrir o app»”, sem proposta ainda$/)).toBeTruthy();
});

it('o selo "corrige uma falha" da lista é um link para a execução de origem, fora do botão Revisar', async () => {
  backend.on('GET', /\/training$/, () => json([sessao('trn-a', 'Corrigir a etapa «Abrir o app»', 'recorded', { origin: ORIGEM }), sessao('trn-b', 'Outro', 'recorded')]));
  await act(async () => root.render(barra()));
  const selo = await waitFor(() => byRole('link', /corrige uma falha/));
  expect(selo.getAttribute('href')).toBe('#/execucoes/r-20261006053318-c04149');
  expect(selo.closest('button')).toBeNull();
  expect(allByRole('link', /corrige uma falha/)).toHaveLength(1);
});

it('gravação sem nenhuma entrada: avisa "Nada gravado", Concluir e revisar fica indisponível com o motivo e Descartar segue; com uma entrada libera', async () => {
  const viva = (inputs: unknown[]) => sessao('trn-v', 'Responder a DM', 'recording', { inputs, input_count: inputs.length });
  backend.on('GET', /\/training$/, () => json([viva([])]));
  backend.on('GET', /\/training\/trn-v$/, () => json(viva([])));
  backend.on('POST', /\/training\/trn-v\/stop$/, () => json(viva([])));
  await act(async () => root.render(barra('user', 'lease-1')));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  expect(text()).toContain('Nada gravado ainda');
  const concluir = byRole('button', /^Concluir e revisar/);
  expect(concluir.getAttribute('aria-disabled')).toBe('true');
  expect(byRole('button', /^Concluir e revisar — indisponível: Nada gravado/)).toBe(concluir);   // o motivo está no nome do botão
  await click(concluir);
  await flush(ATRASO_MAXIMO + 30);
  expect(backend.callsTo('POST', /\/stop$/)).toHaveLength(0);
  expect(byRole('button', /^Descartar$/).getAttribute('aria-disabled')).not.toBe('true');
  await act(async () => root.unmount());
  root = createRoot(container);

  backend.on('GET', /\/training$/, () => json([viva([ENTRADA])]));
  backend.on('GET', /\/training\/trn-v$/, () => json(viva([ENTRADA])));
  await act(async () => root.render(barra('user', 'lease-1')));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  await waitFor(() => expect(byRole('button', /^Concluir e revisar/).getAttribute('aria-disabled')).not.toBe('true'));
  expect(text()).not.toContain('Nada gravado ainda');
});

it('revisão de sessão sem entrada: "Pedir proposta à IA" fica indisponível com o motivo', async () => {
  backend.on('GET', /\/training\/trn-a$/, () => json(sessao('trn-a', 'Corrigir a etapa «Abrir o app»', 'recorded', { input_count: 0 })));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('GET', /\/instagram\/profiles$/, () => json([]));
  await act(async () => root.render(<TrainingReview sessionId="trn-a" onClose={() => {}} />));
  const pedir = await waitFor(() => byRole('button', /^Pedir proposta à IA/));
  expect(pedir.getAttribute('aria-disabled')).toBe('true');
  expect(byRole('button', /^Pedir proposta à IA — indisponível: Nada foi gravado/)).toBe(pedir);
  await click(pedir);
  await flush(ATRASO_MAXIMO + 30);
  expect(backend.callsTo('POST', /propose$/)).toHaveLength(0);
});

it('na revisão, o selo abre a execução de origem: a revisão fecha e o hash vai para a execução', async () => {
  backend.on('GET', /\/training\/trn-a$/, () => json(sessao('trn-a', 'Corrigir a etapa «Abrir o app»', 'recorded', { origin: ORIGEM, inputs: [ENTRADA], input_count: 1 })));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('GET', /\/instagram\/profiles$/, () => json([]));
  let fechou = 0;
  await act(async () => root.render(<TrainingReview sessionId="trn-a" onClose={() => { fechou += 1; }} />));
  const origem = await waitFor(() => document.querySelector<HTMLElement>('section[aria-label="Origem do treino"]')!);
  const selo = byRole('link', /corrige uma falha/, origem);
  expect(selo.getAttribute('href')).toBe('#/execucoes/r-20261006053318-c04149');
  await click(selo);
  await waitFor(() => expect(fechou).toBe(1));
  expect(window.location.hash).toBe('#/execucoes/r-20261006053318-c04149');
});
