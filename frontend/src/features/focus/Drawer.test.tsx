// @vitest-environment jsdom
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { Popover } from '../../components/Popover';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { initialDataState } from '../../store/reducer';
import { aplicarHash, useUiStore } from '../../store/ui';
import { makeInstance, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, byRole, click, flush, installBrowserStubs, json, waitFor } from '../../test/harness';
import { Drawer, elementosFocaveis } from './Drawer';
import { FocusPanel } from './FocusPanel';

/**
 * Tarefa 03 (revisão de UX): o Foco é um drawer sobreposto e modal. Esc e clique no fundo fecham; o teclado fica
 * preso dentro e volta para quem abriu; `aria-modal` é verdadeiro. Prova `simulated` (jsdom: sem layout).
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.on('GET', /^\/api\/instances\/[^/]+\/hierarchy/, () => json({ elements: [] }));
  backend.install();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

function Casca({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  return <Drawer panelRef={ref} ariaLabel="Teste" onClose={onClose} restoreSelector="#origem">{children}</Drawer>;
}

function tecla(alvo: Element, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  act(() => { alvo.dispatchEvent(ev); });
  return ev;
}

describe('Drawer — casca modal do Foco', () => {
  it('é um diálogo modal com nome, e o painel recebe o foco ao abrir', async () => {
    await act(async () => root.render(<Casca onClose={() => undefined}><button type="button">Um</button></Casca>));
    const painel = byRole('dialog', 'Teste');
    expect(painel.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(painel);
  });

  it('Esc e clique no fundo escurecido fecham', async () => {
    const fechar = vi.fn();
    await act(async () => root.render(<Casca onClose={fechar}><button type="button">Um</button></Casca>));
    tecla(byRole('button', 'Um'), 'Escape');
    expect(fechar).toHaveBeenCalledTimes(1);
    await click(document.querySelector('[data-drawer-scrim]') as HTMLElement);
    expect(fechar).toHaveBeenCalledTimes(2);
  });

  it('clicar na página fecha e o clique é engolido; menu lateral, cartões e o topo seguem clicáveis', async () => {
    const fechar = vi.fn();
    const agiu = vi.fn();
    const pagina = document.createElement('div');
    pagina.innerHTML = `
      <header><button type="button" id="topo">Topo</button></header>
      <nav><a href="#/personas" id="menu">Personas</a></nav>
      <main id="conteudo">
        <button type="button" id="acao">Ação da página</button>
        <article data-instance-card="android-02"><button type="button" id="cartao">Abrir android-02</button></article>
      </main>`;
    document.body.appendChild(pagina);
    for (const id of ['topo', 'menu', 'acao', 'cartao']) pagina.querySelector(`#${id}`)!.addEventListener('click', (e) => { e.preventDefault(); agiu(id); });
    await act(async () => root.render(<Casca onClose={fechar}><button type="button">Um</button></Casca>));

    for (const id of ['topo', 'menu', 'cartao']) await click(pagina.querySelector(`#${id}`)!);
    expect(fechar).not.toHaveBeenCalled();
    expect(agiu.mock.calls.map((c) => c[0])).toEqual(['topo', 'menu', 'cartao']);

    agiu.mockClear();
    await click(pagina.querySelector('#acao')!);
    expect(fechar).toHaveBeenCalledTimes(1);
    expect(agiu).not.toHaveBeenCalled(); // o clique só fechou: não agiu sobre o que estava embaixo
    pagina.remove();
  });

  it('Esc num campo de texto, num popover ou numa caixa de confirmação NÃO fecha o drawer', async () => {
    const fechar = vi.fn();
    await act(async () => root.render(
      <Casca onClose={fechar}>
        <input aria-label="Campo" />
        <Popover label="Abrir menu" trigger="Menu"><button type="button">Dentro do popover</button></Popover>
        <dialog open><button type="button">Na caixa</button></dialog>
      </Casca>,
    ));
    tecla(byRole('textbox', 'Campo'), 'Escape');
    await click(byRole('button', 'Abrir menu'));
    const popover = await waitFor(() => byRole('dialog', 'Abrir menu'));
    tecla(byRole('button', 'Dentro do popover', popover), 'Escape');
    tecla(byRole('button', 'Na caixa'), 'Escape');
    expect(fechar).not.toHaveBeenCalled();
  });

  it('o teclado fica preso: Tab no último vai ao primeiro e Shift+Tab no primeiro vai ao último', async () => {
    await act(async () => root.render(
      <Casca onClose={() => undefined}>
        <button type="button">Primeiro</button>
        <button type="button">Meio</button>
        <button type="button">Último</button>
      </Casca>,
    ));
    const primeiro = byRole('button', 'Primeiro');
    const ultimo = byRole('button', 'Último');
    ultimo.focus();
    const adiante = tecla(ultimo, 'Tab');
    expect(adiante.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(primeiro);
    const atras = tecla(primeiro, 'Tab', { shiftKey: true });
    expect(atras.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(ultimo);
    // no meio o Tab segue o fluxo normal do navegador
    expect(tecla(byRole('button', 'Meio'), 'Tab').defaultPrevented).toBe(false);
  });

  it('o corpo de uma seção fechada (details) não conta: o "último" é o resumo dela', async () => {
    await act(async () => root.render(
      <Casca onClose={() => undefined}>
        <button type="button">Primeiro</button>
        <details><summary>Hierarquia</summary><button type="button">Carregar</button></details>
      </Casca>,
    ));
    const lista = elementosFocaveis(byRole('dialog', 'Teste'));
    expect(lista.map((e) => e.textContent)).toEqual(['Primeiro', 'Hierarquia']);
  });

  it('ao fechar, o teclado volta a quem abriu (o botão do cartão)', async () => {
    const origem = document.createElement('button');
    origem.id = 'origem';
    origem.textContent = 'Abrir';
    document.body.appendChild(origem);
    origem.focus();
    await act(async () => root.render(<Casca onClose={() => undefined}><button type="button">Um</button></Casca>));
    expect(document.activeElement).not.toBe(origem);
    await act(async () => root.render(<></>));
    expect(document.activeElement).toBe(origem);
    origem.remove();
  });

  it('se quem abriu já não existe, o teclado vai ao botão do cartão pelo seletor', async () => {
    const cartao = document.createElement('button');
    cartao.id = 'origem';
    document.body.appendChild(cartao);
    const fugaz = document.createElement('button');
    document.body.appendChild(fugaz);
    fugaz.focus();
    await act(async () => root.render(<Casca onClose={() => undefined}><button type="button">Um</button></Casca>));
    fugaz.remove();
    await act(async () => root.render(<></>));
    expect(document.activeElement).toBe(cartao);
    cartao.remove();
  });
});

describe('FocusPanel dentro do drawer', () => {
  beforeEach(() => {
    const snap = makeSnapshot();
    useAppStore.setState({ ...initialDataState, settings: snap.settings, health: snap.health });
    useControlStore.setState({ leases: {}, busy: {} });
    window.history.replaceState(null, '', '#/painel');
    aplicarHash(true);
  });

  it('abre como diálogo modal; Esc fecha pelo histórico e o aparelho em foco sai da URL', async () => {
    useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online' }) }, instanceOrder: ['android-01'] });
    useUiStore.getState().openFocus('android-01');
    expect(window.location.hash).toContain('foco=android-01');
    await act(async () => root.render(<FocusPanel instanceId="android-01" />));
    const painel = byRole('dialog', /Visão de foco: android-01/);
    expect(painel.getAttribute('aria-modal')).toBe('true');
    tecla(painel, 'Escape');
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBeNull());
    await flush(30);
    expect(window.location.hash).not.toContain('foco=');
  });

  it('o fundo escurecido fecha o Foco pelo mesmo caminho', async () => {
    useAppStore.setState({ instances: { 'android-01': makeInstance(1, { state: 'online' }) }, instanceOrder: ['android-01'] });
    useUiStore.getState().openFocus('android-01');
    await act(async () => root.render(<FocusPanel instanceId="android-01" />));
    await click(document.querySelector('[data-drawer-scrim]') as HTMLElement);
    await waitFor(() => expect(useUiStore.getState().focusInstanceId).toBeNull());
  });
});
