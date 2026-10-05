// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { Settings } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, botaoPronto, byRole, click, installBrowserStubs, json, setValue, waitFor } from '../../test/harness';
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
