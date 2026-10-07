// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { Settings } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, botaoPronto, byRole, click, installBrowserStubs, json, setValue, waitFor } from '../../test/harness';
import { useToastStore } from '../../store/toasts';
import { LimitsSection } from './LimitsSection';

// 29.115: a resposta do PUT zera os rascunhos. Com os campos livres durante o envio, o que a pessoa mexesse com o
// pedido em voo sumia sem ser salvo nem avisado.

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  const settings: Settings = { ...makeSnapshot().settings, auto_start_devices: false, preview_mode: 'on_demand' };
  useAppStore.setState({ ...initialDataState, settings });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it('enquanto salva, os campos ficam desabilitados; com a resposta, voltam com o valor salvo e o próximo PUT leva só a mudança nova', async () => {
  let soltar: (() => void) | null = null;
  backend.on('PUT', /^\/api\/settings$/, (c) => new Promise<Response>((r) => {
    const salvo = { ...useAppStore.getState().settings!, ...(c.body as Partial<Settings>) };
    soltar = () => r(json(salvo));
  }));
  await act(async () => { root.render(<LimitsSection />); });
  const sobDemanda = Array.from(container.querySelectorAll('label'))
    .find((l) => l.textContent === 'Ligar aparelhos sob demanda')?.querySelector('input') as HTMLInputElement;
  const previa = () => byRole('combobox', 'Prévia dos aparelhos') as HTMLSelectElement;

  await click(sobDemanda);
  await click(byRole('button', /^Salvar limites/));
  await waitFor(() => soltar !== null);
  expect(previa().matches(':disabled')).toBe(true);
  expect(sobDemanda.matches(':disabled')).toBe(true);
  // O harness, como a pessoa, não digita em campo desabilitado.
  await expect(setValue(previa(), 'always')).rejects.toThrow('está desabilitado');

  await act(async () => { soltar!(); });
  await waitFor(() => !previa().matches(':disabled'));
  expect(sobDemanda.checked).toBe(true);
  expect(previa().value).toBe('on_demand');

  soltar = null;
  await setValue(previa(), 'always');
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => backend.callsTo('PUT', /settings$/).length === 2);
  expect(backend.callsTo('PUT', /settings$/)[1]?.body).toEqual({ preview_mode: 'always' });
});

// 29.130: o Enter num campo envia o formulário, e o fieldset desabilitado tiraria o foco do campo para o body.
it('Enter num campo envia e o foco vai para o Salvar, que segue focável enquanto envia', async () => {
  let soltar: (() => void) | null = null;
  backend.on('PUT', /^\/api\/settings$/, (c) => new Promise<Response>((r) => {
    soltar = () => r(json({ ...useAppStore.getState().settings!, ...(c.body as Partial<Settings>) }));
  }));
  await act(async () => { root.render(<LimitsSection />); });
  const previa = byRole('combobox', 'Prévia dos aparelhos') as HTMLSelectElement;
  await setValue(previa, 'always');
  previa.focus();
  await act(async () => { previa.form!.requestSubmit(); });
  await waitFor(() => soltar !== null);
  const salvar = byRole('button', /^Salvar limites/);
  expect(previa.matches(':disabled')).toBe(true);
  expect(document.activeElement).toBe(salvar);
  expect(salvar.getAttribute('aria-busy')).toBe('true');
  await act(async () => { soltar!(); });
  await waitFor(() => !previa.matches(':disabled'));
});

// Prova de 07/10 (J1): os dois limites da sugestão de alvos. O grupo só existe se o servidor os manda.
const campo = (rotulo: string) => byRole('textbox', new RegExp(`^${rotulo}`)) as HTMLInputElement;

it('J1: o grupo "Orquestração de operações" mostra os dois limites e salva só o que mudou', async () => {
  backend.on('PUT', /^\/api\/settings$/, (c) => json({ ...useAppStore.getState().settings!, ...(c.body as Partial<Settings>) }));
  await act(async () => { root.render(<LimitsSection />); });
  expect(container.textContent).toContain('Orquestração de operações');
  expect(campo('Personas escolhidas por operação').value).toBe('30');
  expect(campo('Candidatas avaliadas pela IA').value).toBe('60');
  expect(campo('Contas que executam a ação final').value).toBe('3');
  await setValue(campo('Personas escolhidas por operação'), '40');
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(1));
  expect(backend.callsTo('PUT', /^\/api\/settings$/)[0]!.body).toEqual({ orquestracao_max_escolhidas: 40 });
});

it('J1: candidatas menores que as escolhidas não salvam, e o erro cai no campo das candidatas', async () => {
  await act(async () => { root.render(<LimitsSection />); });
  await setValue(campo('Candidatas avaliadas pela IA'), '10');
  await click(byRole('button', /^Salvar limites/));
  await waitFor(() => expect(container.textContent).toContain('Deve ser maior ou igual às personas escolhidas.'));
  expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(0);
});

it('J1: backend anterior (sem os campos) não mostra o grupo nem campo vazio', async () => {
  const sem = { ...useAppStore.getState().settings! } as Partial<Settings>;
  delete sem.orquestracao_max_escolhidas;
  delete sem.orquestracao_max_candidatas;
  delete sem.operacao_max_acoes_executadas;
  useAppStore.setState({ settings: sem as Settings });
  await act(async () => { root.render(<LimitsSection />); });
  expect(container.textContent).not.toContain('Orquestração de operações');
  expect(container.textContent).toContain('Limites por objetivo');
});

it('J1: cada limite explica o que é, o padrão e o teto, e a recusa do backend aparece e não perde o que foi digitado', async () => {
  backend.on('PUT', /^\/api\/settings$/, () => apiError(422, 'validation_error', 'orquestracao_max_escolhidas: deve ser menor ou igual a 64'));
  await act(async () => { root.render(<LimitsSection />); });
  expect(container.textContent).toContain('Padrão 30; vai de 1 a 64.');
  expect(container.textContent).toContain('Padrão 60; vai de 1 a 120.');
  expect(container.textContent).toContain('Padrão 3; vai de 1 a 64.');
  await setValue(campo('Personas escolhidas por operação'), '50');
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(1));
  await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'Não foi possível salvar os limites' && String(t.message).includes('deve ser menor ou igual a 64'))).toBe(true));
  expect(campo('Personas escolhidas por operação').value).toBe('50');
});

// ADR-083 (07/10): a regra da frota sobre o mesmo alvo (teto, janela e interruptor) saiu do código por decisão do dono; os testes
// do ADR-081 saíram junto com o grupo da tela.

// 28.61: grupo de política dispensado da aprovação (grupo_sem_aprovacao): o dono escolhe pelo nome, o servidor guarda o id.
const GRUPO = 'Grupo dispensado da aprovação de política';
const grupos = [
  { id: 'g-b', name: 'Operação própria', description: '', capabilities: {}, limits: {}, loosened: [] },
  { id: 'g-a', name: 'Análise', description: '', capabilities: {}, limits: {}, loosened: [] },
];
const caixaDoGrupo = () => byRole('combobox', GRUPO) as HTMLSelectElement;

it('28.61: lista os grupos de política pelo nome, "Nenhum" vem marcado e salvar manda o id escolhido', async () => {
  backend.on('GET', /^\/api\/instagram\/policy-groups/, () => json(grupos));
  backend.on('PUT', /^\/api\/settings$/, (c) => json({ ...useAppStore.getState().settings!, ...(c.body as Partial<Settings>) }));
  await act(async () => { root.render(<LimitsSection />); });
  await waitFor(() => expect(caixaDoGrupo().disabled).toBe(false));
  expect(caixaDoGrupo().value).toBe('');
  expect(Array.from(caixaDoGrupo().options).map((o) => o.textContent)).toEqual(['Nenhum (desligado)', 'Análise', 'Operação própria']);   // nome, em ordem alfabética
  expect(container.textContent).toContain('as recusas e a proteção de conta continuam');
  await setValue(caixaDoGrupo(), 'g-b');
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(1));
  expect(backend.callsTo('PUT', /^\/api\/settings$/)[0]!.body).toEqual({ grupo_sem_aprovacao: 'g-b' });
});

it('28.61: voltar para "Nenhum" desliga (manda texto vazio) e um id salvo que não está mais na lista não vira "Nenhum" calado', async () => {
  useAppStore.setState({ settings: { ...useAppStore.getState().settings!, grupo_sem_aprovacao: 'g-sumiu' } });
  backend.on('GET', /^\/api\/instagram\/policy-groups/, () => json(grupos));
  backend.on('PUT', /^\/api\/settings$/, (c) => json({ ...useAppStore.getState().settings!, ...(c.body as Partial<Settings>) }));
  await act(async () => { root.render(<LimitsSection />); });
  await waitFor(() => expect(caixaDoGrupo().disabled).toBe(false));
  expect(caixaDoGrupo().value).toBe('g-sumiu');
  expect(Array.from(caixaDoGrupo().options).map((o) => o.textContent)).toContain('g-sumiu — grupo não encontrado');
  await setValue(caixaDoGrupo(), '');
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(1));
  expect(backend.callsTo('PUT', /^\/api\/settings$/)[0]!.body).toEqual({ grupo_sem_aprovacao: '' });
});

it('28.61: se a lista de grupos não carrega, o id continua editável como texto e a ajuda diz o porquê', async () => {
  backend.on('GET', /^\/api\/instagram\/policy-groups/, () => apiError(500, 'erro_interno', 'falhou'));
  backend.on('PUT', /^\/api\/settings$/, (c) => json({ ...useAppStore.getState().settings!, ...(c.body as Partial<Settings>) }));
  await act(async () => { root.render(<LimitsSection />); });
  await waitFor(() => expect(container.textContent).toContain('Não consegui listar os grupos agora'));
  await setValue(byRole('textbox', GRUPO) as HTMLInputElement, 'g-a');
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(1));
  expect(backend.callsTo('PUT', /^\/api\/settings$/)[0]!.body).toEqual({ grupo_sem_aprovacao: 'g-a' });
});

it('28.61: backend anterior ao corte 57 (sem o campo) não mostra o grupo de aprovação', async () => {
  const sem = { ...useAppStore.getState().settings! } as Partial<Settings>;
  delete sem.grupo_sem_aprovacao;
  useAppStore.setState({ settings: sem as Settings });
  await act(async () => { root.render(<LimitsSection />); });
  expect(container.textContent).not.toContain('Aprovação de política');
  expect(backend.callsTo('GET', /policy-groups/)).toHaveLength(0);
});
