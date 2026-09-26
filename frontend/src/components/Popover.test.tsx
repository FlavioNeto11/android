// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { byRole, click, installBrowserStubs } from '../test/harness';
import { Popover } from './Popover';

/**
 * Foco do android-06 em viewport de 1024 px (25/09/2026): o menu "Instalar app" era cortado — pela borda da tela e,
 * virado de lado, pelo `overflow` da coluna de ações. Posição fixa presa à tela; o mesmo defeito do rótulo truncado
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

function caixas(gatilho: { left: number; width: number }, painelW: number, painelH = 300): void {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const r = this.getAttribute('role') === 'dialog'
      ? { left: 0, top: 0, width: painelW, height: painelH }
      : { left: gatilho.left, top: 500, width: gatilho.width, height: 30 };
    return { ...r, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top, toJSON: () => ({}) } as DOMRect;
  });
}

async function abrir(): Promise<HTMLElement> {
  await act(async () => root.render(<Popover label="Instalar app" trigger="Instalar app">conteúdo</Popover>));
  await click(byRole('button', 'Instalar app'));
  return byRole('dialog', 'Instalar app');
}

it('fica inteiro na tela quando abriria para fora da borda direita (o medido no Foco)', async () => {
  caixas({ left: 732, width: 120 }, 434);
  const d = await abrir();
  expect(d.style.position).toBe('fixed');
  const left = parseFloat(d.style.left);
  expect(left + 434).toBeLessThanOrEqual(1024 - 8);
  expect(d.getAttribute('data-side')).toBe('end');
});

it('mantém o lado pedido quando cabe', async () => {
  caixas({ left: 100, width: 120 }, 400);
  const d = await abrir();
  expect(parseFloat(d.style.left)).toBe(100);
  expect(d.getAttribute('data-side')).toBe('start');
});

it('painel maior que a tela encosta na margem esquerda, sem posição negativa', async () => {
  caixas({ left: 100, width: 120 }, 1150);
  expect(parseFloat((await abrir()).style.left)).toBe(8);
});

it('sem espaço abaixo, abre acima do gatilho', async () => {
  Object.defineProperty(window, 'innerHeight', { value: 700, configurable: true });
  caixas({ left: 100, width: 120 }, 400, 300);        // gatilho em 500..530: abaixo iria até 838
  expect(parseFloat((await abrir()).style.top)).toBe(500 - 8 - 300);
});
