// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TruncatedText } from './TruncatedText';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
let observadores: Array<() => void>;

/** jsdom não mede layout: o teste diz quanto o elemento mede e quanto o conteúdo pede. */
function medidas(largura: number, conteudo: number, altura = 20, conteudoAltura = 20) {
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => largura });
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => conteudo });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => altura });
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => conteudoAltura });
}

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  observadores = [];
  vi.stubGlobal('ResizeObserver', class {
    constructor(cb: () => void) { observadores.push(cb); }
    observe() {}
    disconnect() {}
  });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  for (const k of ['clientWidth', 'scrollWidth', 'clientHeight', 'scrollHeight']) {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
  }
});

function renderiza(el: React.ReactElement) {
  act(() => root.render(el));
  return host.firstElementChild as HTMLElement;
}

describe('TruncatedText', () => {
  it('põe o texto inteiro no title só quando a linha realmente é cortada', () => {
    medidas(100, 240);
    expect(renderiza(<TruncatedText>Um nome de aparelho comprido demais</TruncatedText>).title).toBe('Um nome de aparelho comprido demais');
  });

  it('não põe title quando o texto cabe', () => {
    medidas(300, 240);
    expect(renderiza(<TruncatedText>Curto</TruncatedText>).hasAttribute('title')).toBe(false);
  });

  it('mede de novo quando o elemento muda de tamanho', () => {
    medidas(300, 240);
    const el = renderiza(<TruncatedText>Texto que cabe agora</TruncatedText>);
    expect(el.hasAttribute('title')).toBe(false);
    medidas(100, 240);
    act(() => observadores.forEach((cb) => cb()));
    expect(el.title).toBe('Texto que cabe agora');
  });

  it('com várias linhas, mede a altura e aplica o limite de linhas', () => {
    medidas(300, 300, 40, 90);
    const el = renderiza(<TruncatedText linhas={2}>Objetivo longo que passa de duas linhas</TruncatedText>);
    expect(el.title).toBe('Objetivo longo que passa de duas linhas');
    expect(el.style.getPropertyValue('-webkit-line-clamp') || el.style.webkitLineClamp).toBe('2');
  });

  it('com o texto integral diferente do mostrado, o title é sempre o integral', () => {
    medidas(300, 100);
    const el = renderiza(<TruncatedText completo="Nas instâncias selecionadas, abra o app e leia o nome do contato.">Abra o app e leia o nome do contato</TruncatedText>);
    expect(el.title).toBe('Nas instâncias selecionadas, abra o app e leia o nome do contato.');
  });

  it('sem ResizeObserver (navegador antigo) não quebra e mede no encaixe', () => {
    vi.stubGlobal('ResizeObserver', undefined);
    medidas(100, 240);
    expect(renderiza(<TruncatedText>Cortado mesmo assim</TruncatedText>).title).toBe('Cortado mesmo assim');
  });
});
