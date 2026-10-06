// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, allByRole, apiError, byRole, click, flush, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { TrainingBar } from './TrainingBar';
import { TrainingReview } from './TrainingReview';

/**
 * 31.119: descartar a sessão de treino que já terminou de gravar, na lista "Para revisar" e na revisão. Pede confirmação,
 * chama `POST /api/training/{id}/discard` e devolve o controle do aparelho se ele é desta aba. Prova `simulated`.
 */

/** O atraso máximo do fetch falso (modo ATRASO_DO_FETCH_MS): a resposta que o teste solta depois ainda pode estar a caminho. */
const ATRASO_MAXIMO = Number(process.env.ATRASO_DO_FETCH_MS ?? 0);

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const sessao = (id: string, intent: string, status: string, extra: object = {}) => ({
  id, instance_id: 'android-01', profile_id: null, app_id: null, intent, status, operator: null, proposal: null, flow_id: null,
  created_at: '', finished_at: '', updated_at: '', input_count: 2, inputs: [], ...extra,
});
const GRAVADA = sessao('trn-a', 'Corrigir a etapa «Abrir o app»', 'recorded');
const COM_PROPOSTA = sessao('trn-b', 'Responder a DM', 'proposed');

function comControleNaAba(): void {
  useControlStore.setState({ leases: { 'android-01': { leaseId: 'lease-1', status: 'granted', acquiredAt: Date.now() } }, busy: {} });
}

const barra = () => (
  <>
    <TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId={null} mine={false} />
    <ConfirmHost />
  </>
);

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training$/, () => json([GRAVADA, COM_PROPOSTA]));
  backend.on('POST', /\/training\/trn-[ab]\/discard$/, (c) => json({ ...(c.path.includes('trn-a') ? GRAVADA : COM_PROPOSTA), status: 'discarded' }));
  backend.on('POST', /\/instances\/android-01\/control\/release$/, () => json({ released: true }));
  useToastStore.setState({ toasts: [] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
  useControlStore.setState({ leases: {}, busy: {} });
});

it('lista "Para revisar": cada sessão concluída tem o seu Descartar; cancelar não chama a rota', async () => {
  await act(async () => root.render(barra()));
  await waitFor(() => expect(text()).toContain('Para revisar:'));
  expect(allByRole('button', /^Descartar “/)).toHaveLength(2);
  await click(byRole('button', /^Descartar “Corrigir a etapa «Abrir o app»”$/));
  const dialogo = await waitFor(() => byRole('dialog', /Descartar o treinamento/));
  expect(dialogo.textContent).toContain('“Corrigir a etapa «Abrir o app»” e 2 entradas gravadas se perdem; nada vira fluxo nem habilidade.');
  expect(dialogo.textContent).not.toContain('volta para a IA');                              // sem o controle desta aba, nada de devolver
  await click(byRole('button', /^Cancelar$/, dialogo));
  await waitFor(() => expect(allByRole('dialog', /Descartar o treinamento/)).toHaveLength(0));
  expect(backend.callsTo('POST', /discard$/)).toHaveLength(0);
  expect(allByRole('button', /^Descartar “/)).toHaveLength(2);
});

it('lista "Para revisar": confirmar descarta só aquela sessão, relê a lista e não mexe no controle sem o lease desta aba', async () => {
  await act(async () => root.render(barra()));
  await waitFor(() => expect(allByRole('button', /^Descartar “/)).toHaveLength(2));
  backend.on('GET', /\/training$/, () => json([COM_PROPOSTA]));                              // depois do descarte a lista vem sem ela
  await click(byRole('button', /^Descartar “Corrigir a etapa «Abrir o app»”$/));
  await click(byRole('button', /^Descartar treinamento$/, await waitFor(() => byRole('dialog', /Descartar o treinamento/))));
  await waitFor(() => expect(backend.callsTo('POST', /\/training\/trn-a\/discard$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /discard$/)).toHaveLength(1);                               // só a escolhida
  expect(backend.callsTo('POST', /discard$/)[0]!.body).toBeUndefined();
  await waitFor(() => expect(allByRole('button', /^Descartar “/)).toHaveLength(1));
  expect(allByRole('button', /Revisar “Corrigir a etapa/)).toHaveLength(0);
  expect(useToastStore.getState().toasts.some((t) => t.title === 'Treinamento descartado')).toBe(true);
  await flush(ATRASO_MAXIMO + 30);
  expect(backend.callsTo('POST', /control\/release$/)).toHaveLength(0);
});

it('com o controle do aparelho nesta aba, o diálogo avisa e o descarte o devolve à IA', async () => {
  comControleNaAba();
  await act(async () => root.render(barra()));
  await waitFor(() => expect(allByRole('button', /^Descartar “/)).toHaveLength(2));
  backend.on('GET', /\/training$/, () => json([COM_PROPOSTA]));
  await click(byRole('button', /^Descartar “Corrigir a etapa «Abrir o app»”$/));
  const dialogo = await waitFor(() => byRole('dialog', /Descartar o treinamento/));
  expect(dialogo.textContent).toContain('O controle de android-01 volta para a IA.');
  await click(byRole('button', /^Descartar treinamento$/, dialogo));
  await waitFor(() => expect(backend.callsTo('POST', /control\/release$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /control\/release$/)[0]!.body).toEqual({ lease_id: 'lease-1' });
  expect(backend.callsTo('POST', /discard$/)).toHaveLength(1);                               // o descarte veio ANTES, e uma vez só
  await waitFor(() => expect(useControlStore.getState().leases['android-01']).toBeUndefined());
});

it('recusa do backend: avisa, a sessão fica na lista e o controle não é devolvido', async () => {
  comControleNaAba();
  backend.on('POST', /\/training\/trn-a\/discard$/, () => apiError(409, 'sessao_encerrada', 'Esta sessão já foi encerrada.'));
  await act(async () => root.render(barra()));
  await waitFor(() => expect(allByRole('button', /^Descartar “/)).toHaveLength(2));
  await click(byRole('button', /^Descartar “Corrigir a etapa «Abrir o app»”$/));
  await click(byRole('button', /^Descartar treinamento$/, await waitFor(() => byRole('dialog', /Descartar o treinamento/))));
  await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'Não foi possível descartar o treinamento')).toBe(true));
  expect(allByRole('button', /^Descartar “/)).toHaveLength(2);
  await flush(ATRASO_MAXIMO + 30);
  expect(backend.callsTo('POST', /control\/release$/)).toHaveLength(0);
  expect(useControlStore.getState().leases['android-01']).toBeDefined();
});

// ---------------------------------------------------------------- a revisão
const revisao = (onClose: () => void, id = 'trn-a') => (
  <>
    <TrainingReview sessionId={id} onClose={onClose} />
    <ConfirmHost />
  </>
);

it('revisão de sessão só gravada ou com proposta: Descartar pede confirmação, descarta, devolve o controle e fecha a revisão', async () => {
  comControleNaAba();
  backend.on('GET', /\/training\/trn-a$/, () => json(GRAVADA));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('GET', /\/instagram\/profiles$/, () => json([]));
  let fechou = 0;
  await act(async () => root.render(revisao(() => { fechou += 1; })));
  const descartar = await waitFor(() => byRole('button', /^Descartar$/));
  await click(descartar);
  const dialogo = await waitFor(() => byRole('dialog', /Descartar o treinamento/));
  expect(dialogo.textContent).toContain('O controle de android-01 volta para a IA.');
  await click(byRole('button', /^Cancelar$/, dialogo));
  await waitFor(() => expect(allByRole('dialog', /Descartar o treinamento/)).toHaveLength(0));
  expect(backend.callsTo('POST', /discard$/)).toHaveLength(0);
  expect(fechou).toBe(0);

  await click(byRole('button', /^Descartar$/));
  await click(byRole('button', /^Descartar treinamento$/, await waitFor(() => byRole('dialog', /Descartar o treinamento/))));
  await waitFor(() => expect(backend.callsTo('POST', /\/training\/trn-a\/discard$/)).toHaveLength(1));
  await waitFor(() => expect(fechou).toBe(1));
  await waitFor(() => expect(backend.callsTo('POST', /control\/release$/)).toHaveLength(1));
});

it('revisão: sessão já salva ou descartada não oferece Descartar', async () => {
  backend.on('GET', /\/training\/trn-a$/, () => json({ ...GRAVADA, status: 'saved', flow_id: 'f-1' }));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  backend.on('GET', /\/instagram\/profiles$/, () => json([]));
  await act(async () => root.render(revisao(() => {})));
  await waitFor(() => expect(text()).toContain('Treinamento: Corrigir a etapa'));
  await flush(ATRASO_MAXIMO + 30);
  expect(allByRole('button', /^Descartar$/)).toHaveLength(0);
});
