// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { Disclosure } from './Disclosure';

let root: Root;
let container: HTMLElement;

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(openWhen: boolean): Promise<void> {
  await act(async () => { root.render(<Disclosure summary="Receita" openWhen={openWhen}>corpo</Disclosure>); });
}

const detalhes = () => container.querySelector('details') as HTMLDetailsElement;

async function fecharAMao(): Promise<void> {
  await act(async () => {
    detalhes().open = false;
    detalhes().dispatchEvent(new Event('toggle'));
  });
}

// 29.112/29.119: o `openWhen` abre na subida e nunca fecha; o que a pessoa fechou fica fechado enquanto ele não subir de
// novo, mesmo parado em verdadeiro e com o bloco renderizado outra vez.
it('o bloco fechado à mão segue fechado com o openWhen parado em verdadeiro, e reabre só numa nova subida', async () => {
  await montar(true);
  expect(detalhes().open).toBe(true);
  await fecharAMao();
  await montar(true);
  expect(detalhes().open).toBe(false);

  await montar(false);
  expect(detalhes().open).toBe(false);
  await montar(true);
  expect(detalhes().open).toBe(true);
});
