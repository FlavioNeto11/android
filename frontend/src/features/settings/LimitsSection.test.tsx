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

// ADR-081: a regra da frota sobre o mesmo alvo (frota_max_contas_por_alvo, fleet_target_window_days e o interruptor das contas nossas; o teto das curtidas é outro campo).
const TETO = 'Contas da frota que podem agir sobre o mesmo alvo';
const CURTIDAS = 'Contas da frota que podem curtir o mesmo alvo';
const JANELA = 'Janela da regra da frota';
const INTERRUPTOR = /Alvo que é conta nossa fica fora da regra/;
const interruptor = () => Array.from(container.querySelectorAll('label')).find((l) => INTERRUPTOR.test(l.textContent ?? ''))?.querySelector('input') as HTMLInputElement;

it('ADR-081: o grupo da regra da frota mostra o teto, o das curtidas, a janela e o interruptor, com o que valem, o padrão e a faixa, e salva só o que mudou', async () => {
  backend.on('PUT', /^\/api\/settings$/, (c) => json({ ...useAppStore.getState().settings!, ...(c.body as Partial<Settings>) }));
  await act(async () => { root.render(<LimitsSection />); });
  expect(container.textContent).toContain('Regra da frota sobre o mesmo alvo');
  expect(campo(TETO).value).toBe('10');
  expect(campo(CURTIDAS).value).toBe('3');
  expect(campo(JANELA).value).toBe('30');
  expect(interruptor().checked).toBe(true);                                                   // padrão: ligado
  expect(container.textContent).toMatch(/Janela da regra da frota\s*dias/);                   // a unidade ao lado do rótulo
  expect(container.textContent).toContain('Era 1. As curtidas têm teto próprio, logo abaixo. Padrão 10; vai de 1 a 64.');
  expect(container.textContent).toContain('Só para curtidas');
  expect(container.textContent).toContain('Padrão 30; vai de 1 a 365.');
  expect(container.textContent).toContain('Pessoa real sempre entra.');
  expect(container.textContent).not.toContain('Janela da coordenação de frota');             // a janela em segundos não tem mais uso
  await setValue(campo(TETO), '12');
  await setValue(campo(JANELA), '45');
  await click(interruptor());
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(1));
  expect(backend.callsTo('PUT', /^\/api\/settings$/)[0]!.body).toEqual({ frota_max_contas_por_alvo: 12, fleet_target_window_days: 45, frota_conta_nossa_fora_da_regra: false });
});

it.each([[TETO, '0'], [TETO, '65'], [CURTIDAS, '51'], [JANELA, '0'], [JANELA, '366'], [JANELA, '3,5']])(
  'ADR-081: %s = %s não salva e o erro cai no próprio campo', async (rotulo, valor) => {
    await act(async () => { root.render(<LimitsSection />); });
    await setValue(campo(rotulo), valor);
    await click(byRole('button', /^Salvar limites/));
    await waitFor(() => expect(container.textContent).toMatch(/entre|inteiro|mínimo|máximo|maior|menor|Informe/i));
    expect(backend.callsTo('PUT', /^\/api\/settings$/)).toHaveLength(0);
  },
);

it('ADR-081: backend anterior ao corte 56 (sem o teto novo nem o interruptor) mostra só a janela e o teto das curtidas, sem caixa vazia', async () => {
  const sem = { ...useAppStore.getState().settings! } as Partial<Settings>;
  delete sem.frota_max_contas_por_alvo;
  delete sem.frota_conta_nossa_fora_da_regra;
  useAppStore.setState({ settings: sem as Settings });
  await act(async () => { root.render(<LimitsSection />); });
  expect(container.textContent).toContain('Regra da frota sobre o mesmo alvo');
  expect(campo(CURTIDAS).value).toBe('3');
  expect(container.textContent).not.toContain('Contas da frota que podem agir sobre o mesmo alvo');
  expect(container.textContent).not.toContain('Alvo que é conta nossa fica fora da regra');
});

it('ADR-081: a recusa do backend ao salvar o teto aparece sem perder o valor digitado', async () => {
  backend.on('PUT', /^\/api\/settings$/, () => apiError(400, 'unknown_setting', 'frota_max_contas_por_alvo: configuração desconhecida'));
  await act(async () => { root.render(<LimitsSection />); });
  await setValue(campo(TETO), '20');
  await click(await botaoPronto(/^Salvar limites/));
  await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'Não foi possível salvar os limites' && String(t.message).includes('frota_max_contas_por_alvo'))).toBe(true));
  expect(campo(TETO).value).toBe('20');
});
