// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { installBrowserStubs, text } from '../../test/harness';
import { AppsDoItem } from './AppsDoItem';
import { DetalheRico } from './DetalheRico';
import { EXPLICACAO_DO_APP_NAO_IDENTIFICADO, NOME_DO_APP_NAO_IDENTIFICADO, dicaDoApp, nomeDoApp } from './apps';
import { ItemDoLivro } from './ItemDoLivro';
import type { DetalheDoLivro, EntradaDoLivro } from './model';

/**
 * 31.126: o balde `nao_resolvido` (o item que o backend não ligou a um aplicativo) aparece como "App não identificado",
 * com a explicação ao passar o mouse, na linha do Livro, em Identidade e em "Aplicativos" do item; o código cru some.
 * Prova `simulated`.
 */

function entrada(over: Partial<EntradaDoLivro> = {}): EntradaDoLivro {
  return {
    kind: 'fluxo', ref: 'f-6d07590e1db6', state: 'disabled', native_status: 'quarantined', title: 'Abrir o painel de notificações',
    app: 'nao_resolvido', origin: 'treino', side_effect: false, human_origin: false, requires_owner: false,
    created_at: '2026-10-06T01:00:00Z', state_at: '2026-10-06T01:00:00Z', last_used_at: null, uses: 0,
    evidence: { for: 0, against: 0 }, count: null, detail: null, acoes: [], por_que_nao_publica: null, saude: null, ...over,
  };
}

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  installBrowserStubs();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const botaoDoApp = (raiz: ParentNode) => Array.from(raiz.querySelectorAll('button')).find((b) => /^Abrir este aplicativo|^Ainda não foi ligado/.test(b.title));

describe('31.126: "App não identificado" no lugar do código cru', () => {
  it('as funções puras: o balde ganha o nome e a explicação; o resto segue o nome conhecido ou o pacote', () => {
    expect(nomeDoApp('nao_resolvido')).toBe(NOME_DO_APP_NAO_IDENTIFICADO);
    expect(nomeDoApp('nao_resolvido', 'nao_resolvido')).toBe('App não identificado');
    expect(nomeDoApp('com.exemplo.app', 'Exemplo')).toBe('Exemplo');
    expect(nomeDoApp('com.exemplo.app', 'com.exemplo.app')).toBe('com.exemplo.app');
    expect(nomeDoApp('com.exemplo.app')).toBe('com.exemplo.app');
    expect(dicaDoApp('nao_resolvido')).toBe(EXPLICACAO_DO_APP_NAO_IDENTIFICADO);
    expect(dicaDoApp('com.exemplo.app')).toBe('Abrir este aplicativo (com.exemplo.app)');
  });

  it('a linha do Livro mostra "App não identificado" com a explicação, não o código', async () => {
    await act(async () => root.render(<ItemDoLivro entrada={entrada()} acoes={[]} onMudou={() => {}} />));
    expect(text(container)).toContain('App: App não identificado');
    expect(text(container)).not.toContain('nao_resolvido');
    expect(botaoDoApp(container)?.title).toBe(EXPLICACAO_DO_APP_NAO_IDENTIFICADO);
  });

  it('a linha de um app comum não muda: o nome se houver, senão o pacote', async () => {
    await act(async () => root.render(<ItemDoLivro entrada={entrada({ app: 'com.whatsapp', app_nome: 'WhatsApp' })} acoes={[]} onMudou={() => {}} />));
    expect(text(container)).toContain('App: WhatsApp');
    expect(botaoDoApp(container)?.title).toBe('Abrir este aplicativo (com.whatsapp)');
    await act(async () => root.render(<ItemDoLivro entrada={entrada({ app: 'com.whatsapp', app_nome: null })} acoes={[]} onMudou={() => {}} />));
    expect(text(container)).toContain('App: com.whatsapp');
  });

  it('em Identidade, o fluxo sem app mostra "App não identificado" e a explicação', async () => {
    const d: DetalheDoLivro = { item: entrada(), evidencias: [], trilha: [], exposicoes: [], conteudo: null, versao: null, relacoes: [] } as never;
    await act(async () => root.render(<DetalheRico detalhe={d} />));
    const identidade = Array.from(container.querySelectorAll('section')).find((s) => /Identidade/.test(s.textContent ?? ''))!;
    expect(text(identidade)).toContain('App não identificado');
    expect(text(identidade)).not.toContain('nao_resolvido');
    expect(botaoDoApp(identidade)?.title).toBe(EXPLICACAO_DO_APP_NAO_IDENTIFICADO);
  });

  it('o fluxo que atravessa apps também nomeia o balde sem o código', async () => {
    await act(async () => root.render(<AppsDoItem apps={['com.whatsapp', 'nao_resolvido']} nomes={['WhatsApp', 'nao_resolvido']} principal="com.whatsapp" />));
    expect(text(container)).toContain('WhatsApp');
    expect(text(container)).toContain('App não identificado');
    expect(text(container)).not.toContain('nao_resolvido');
  });
});
