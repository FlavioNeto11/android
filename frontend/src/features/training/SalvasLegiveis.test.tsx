// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { TrainingBar } from './TrainingBar';
import { useTrainingStore } from './trainingStore';

/**
 * 31.133: "Salvas" legível. Cada linha ganha a data e o começo do id (duas sessões com o mesmo texto deixam de ser iguais), há
 * como ver todas (antes só as 5 mais novas, as outras ficavam sem acesso) e o aviso "Etapa sem receita porque o aparelho estava
 * fora do ar?" deixa de se repetir em cada linha: vira dica do botão. Prova `simulated`.
 */

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const HORAS = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
const SALVA = (n: number, intent: string, horas: number) => ({
  id: `trn-S${n}abcdefghij`, instance_id: 'android-01', profile_id: null, app_id: null, intent, status: 'saved', operator: null,
  proposal: null, flow_id: `f-${n}`, created_at: HORAS(horas + 1), finished_at: HORAS(horas), updated_at: HORAS(horas), input_count: 2,
});
// A lista vem da mais nova para a mais velha; duas delas têm o mesmo texto.
const OITO = [
  SALVA(1, 'Pesquisar por wifi', 1), SALVA(2, 'Pesquisar por wifi', 3), SALVA(3, 'Abrir o perfil', 5), SALVA(4, 'Abrir a busca', 7),
  SALVA(5, 'Abrir as notificações', 9), SALVA(6, 'Voltar para a lista', 11), SALVA(7, 'Responder a DM', 13), SALVA(8, 'Curtir a foto', 15),
];

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training$/, () => json(OITO));
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

const montar = async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Salvas (as 5 mais novas de 8)'));
};
const linhas = () => Array.from(container.querySelectorAll('ul li')).filter((li) => li.querySelector('button[aria-label^="Ver o treinamento salvo"]')) as HTMLElement[];

it('cada linha tem a data e o começo do id, e as duas sessões com o mesmo texto se distinguem', async () => {
  await montar();
  const [a, b] = linhas();
  expect(a!.textContent).toMatch(/há .* · trn-S1ab/);
  expect(b!.textContent).toMatch(/há .* · trn-S2ab/);
  expect(a!.textContent).toContain('Pesquisar por wifi');
  expect(b!.textContent).toContain('Pesquisar por wifi');
  expect(a!.querySelector('[title^="sessão trn-S1abcdefghij"]')?.getAttribute('title')).toBe('sessão trn-S1abcdefghij · fluxo f-1');
});

it('mostra as 5 mais novas, e "Ver todas as 8" abre as outras (e volta às 5)', async () => {
  await montar();
  expect(linhas()).toHaveLength(5);
  expect(text()).not.toContain('Curtir a foto');
  await click(byRole('button', /^Ver todas as 8$/));
  expect(linhas()).toHaveLength(8);
  expect(text()).toContain('Curtir a foto');
  expect(text()).toContain('Salvas (8)');
  await click(byRole('button', /^Ver só as 5 mais novas$/));
  expect(linhas()).toHaveLength(5);
});

it('com 5 ou menos salvas não há "Ver todas"', async () => {
  backend.on('GET', /\/training$/, () => json(OITO.slice(0, 5)));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Salvas (5)'));
  expect(allByRole('button', /^Ver todas/)).toHaveLength(0);
});

it('o aviso de etapa sem receita não se repete nas linhas: vira dica do botão e o resultado do refazer continua dizendo o que houve', async () => {
  backend.on('POST', /\/training\/trn-S1abcdefghij\/recipes$/, () => json({
    session: OITO[0], flow_id: 'f-1', created: 0, steps: [{ key: 'a', title: 'Abrir', recipe: false, reason: 'já havia receita ativa' }],
  }));
  await montar();
  expect(text()).not.toContain('Etapa sem receita porque o aparelho estava fora do ar');
  const botao = byRole('button', /^Refazer receitas de “Pesquisar por wifi”$/, linhas()[0]);
  expect(botao.getAttribute('title')).toContain('ficaram sem ela porque o aparelho estava fora do ar');
  await click(botao);
  await waitFor(() => expect(text(linhas()[0]!)).toContain('Nenhuma receita nova.'));
  expect(text(linhas()[0]!)).toContain('Abrir (já havia receita ativa)');
  expect(text(linhas()[1]!)).not.toContain('Nenhuma receita nova.');
});
