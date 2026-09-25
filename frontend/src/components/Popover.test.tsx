// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { byRole, click, installBrowserStubs } from '../test/harness';
import { Popover } from './Popover';

/**
 * Foco do android-06 em viewport de 1024 px (25/09/2026): o menu "Instalar app" abria à esquerda do botão e o item
 * "Instalar Instagram 447.0.0.55.81 (promovida)" era cortado na borda direita — o mesmo defeito do rótulo truncado
 * que motivou o menu, só que agora dentro dele.
 */
let root: Root;
let container: HTMLElement;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

function caixa(left: number, right: number): void {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    { left, right, top: 0, bottom: 0, width: right - left, height: 0, x: left, y: 0, toJSON: () => ({}) } as DOMRect);
}

async function abrir(): Promise<HTMLElement> {
  await act(async () => root.render(<Popover label="Instalar app" trigger="Instalar app">conteúdo</Popover>));
  await click(byRole('button', 'Instalar app'));
  return byRole('dialog', 'Instalar app');
}

it('vira para o fim quando o painel sairia pela direita da tela', async () => {
  caixa(732, 1166);                                   // o medido no Foco
  expect((await abrir()).getAttribute('data-side')).toBe('end');
});

it('mantém o lado pedido quando cabe', async () => {
  caixa(100, 500);
  expect((await abrir()).getAttribute('data-side')).toBe('start');
});

it('não oscila quando não cabe em nenhum lado', async () => {
  caixa(-50, 1100);
  expect((await abrir()).getAttribute('data-side')).toBe('end');
});
