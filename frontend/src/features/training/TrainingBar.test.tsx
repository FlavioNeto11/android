// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { TrainingBar } from './TrainingBar';
import { useTrainingStore } from './trainingStore';

// Fase L (P2.6): descartar a gravação em andamento é irreversível e passa a pedir confirmação, como toda ação
// destrutiva do painel; enquanto uma ação corre, a outra explica por que espera em vez de só apagar.

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const GRAVANDO = {
  id: 'trn-9', instance_id: 'android-01', profile_id: null, app_id: null, intent: 'Responder a DM', status: 'recording',
  operator: null, proposal: null, flow_id: null, created_at: '', finished_at: null, updated_at: '',
  inputs: [{ session_id: 'trn-9', seq: 1, ts: '', type: 'tap', x: 1, y: 1, x2: null, y2: null, key_name: null, text: null,
             has_text: false, text_len: null, package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null,
             screen_lines: [], sensitive: false }],
};

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training$/, () => json([GRAVANDO]));
  backend.on('GET', /\/training\/trn-9$/, () => json(GRAVANDO));
  backend.on('POST', /\/training\/trn-9\/discard$/, () => json({ ...GRAVANDO, status: 'discarded' }));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
  useTrainingStore.setState({ gravando: {}, recusadas: {} });
});

it('"Descartar" a gravação pede confirmação: cancelar não chama a rota; confirmar descarta', async () => {
  await act(async () => root.render(<><TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));

  await click(byRole('button', /^Descartar$/));
  await waitFor(() => expect(text()).toContain('Descartar a gravação?'));
  expect(text()).toContain('1 entrada gravada');
  await click(byRole('button', /^Cancelar$/, byRole('dialog', /Descartar a gravação/)));
  await waitFor(() => expect(allByRole('dialog', /Descartar a gravação/)).toHaveLength(0));
  expect(backend.callsTo('POST', /discard$/)).toHaveLength(0);
  expect(text()).toContain('Gravando: Responder a DM');

  backend.on('GET', /\/training$/, () => json([{ ...GRAVANDO, status: 'discarded' }]));
  await click(byRole('button', /^Descartar$/));
  await waitFor(() => expect(text()).toContain('Descartar a gravação?'));
  await click(byRole('button', /^Descartar gravação$/, byRole('dialog', /Descartar a gravação/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/training\/trn-9\/discard$/)).toHaveLength(1));
  await waitFor(() => expect(text()).not.toContain('Gravando: Responder a DM'));
});

// 31.80: depois de um reinício do backend a sessão `recording` fica órfã; sem o controle na mão a barra não pode
// dizer "Gravando", só avisar e deixar concluir ou descartar.
it('sessão "recording" com o controle fora das suas mãos: avisa, sem "Gravando", com Concluir e Descartar', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'ai' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Há uma gravação aberta neste aparelho que não está mais gravando'));
  expect(text()).not.toContain('Gravando:');
  expect(byRole('button', /^Concluir e revisar$/)).toBeTruthy();
  expect(byRole('button', /^Descartar$/)).toBeTruthy();
});

// 31.86: o aparelho que entrou em erro logo depois de gravar não pode deixar as gravações inalcançáveis.
it('modo "só revisão" (aparelho fora do ar): lista "Para revisar" e a nota, sem o formulário de iniciar', async () => {
  backend.on('GET', /\/training$/, () => json([{ ...GRAVANDO, id: 'trn-3', status: 'recorded' }]));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'error', control: 'ai' })} leaseId={null} mine={false} somenteRevisao />));
  await waitFor(() => expect(text()).toContain('Para revisar:'));
  expect(text()).toContain('Aparelho fora do ar: dá para revisar e salvar o fluxo');
  expect(allByRole('button', /Iniciar treinamento/)).toHaveLength(0);
  expect(document.querySelector('input[placeholder^="Ex.:"]')).toBeNull();
  expect(byRole('button', /Responder a DM/)).toBeTruthy();
});

it('contador de entradas recusadas: aparece só enquanto grava e some quando a gravação termina', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  expect(text()).not.toContain('recusada(s)');
  await act(async () => { useTrainingStore.getState().registrarRecusa('android-01'); useTrainingStore.getState().registrarRecusa('android-01'); });
  expect(text()).toContain('2 entrada(s) recusada(s): refaça');
  backend.on('GET', /\/training$/, () => json([{ ...GRAVANDO, status: 'discarded' }]));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine={false} />));
  await waitFor(() => expect(useTrainingStore.getState().recusadas['android-01']).toBeUndefined());
  expect(text()).not.toContain('recusada(s)');
});
