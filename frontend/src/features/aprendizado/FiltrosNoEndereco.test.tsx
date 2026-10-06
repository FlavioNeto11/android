// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { lerFiltroDoEndereco, queryDoFiltro } from './filtroNoEndereco';

/**
 * 31.144: os filtros do Livro (Tipo, Estado, Origem, Prova e a visão Produto/QA/Todos) viajam no endereço: o que a pessoa
 * escolhe escreve a query, e a query na entrada (recarregar, link) volta a ser filtro. Prova `simulated`.
 */

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const consultasDoLivro = () => backend.callsTo('GET', /^\/api\/aprendizado$/);
const ultimaConsulta = () => consultasDoLivro().at(-1)?.query.toString() ?? '';
const query = () => useUiStore.getState().rota.query;
const combo = (nome: RegExp) => byRole('combobox', nome, container) as HTMLSelectElement;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  for (const rota of ['pendentes', 'revisar', 'intencao']) backend.on('GET', new RegExp(`^/api/aprendizado/${rota}$`), () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], sem_eixo: null, nao_resolvido: null }));
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [], total: 0, contagem: {} }));
});
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: {} } });
});

async function montar(queryDeEntrada: Record<string, string> = {}): Promise<void> {
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'aprendizado', segmentos: [], query: { aba: 'aprendido', ...queryDeEntrada } } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<AprendizadoPage />));
  await waitFor(() => expect(consultasDoLivro().length).toBeGreaterThan(0));
}

describe('o leitor e o escritor do endereço', () => {
  it('lerFiltroDoEndereco: lê o que vale e descarta o que não conhece', () => {
    expect(lerFiltroDoEndereco({})).toEqual({ kind: undefined, state: undefined, origem: undefined, prova: undefined, rotulo: undefined });
    expect(lerFiltroDoEndereco({ tipo: 'fluxo', estado: 'disabled', origem: 'treino', prova: 'so_prova', visao: 'qa' }))
      .toEqual({ kind: 'fluxo', state: 'disabled', origem: 'treino', prova: 'so_prova', rotulo: 'qa' });
    expect(lerFiltroDoEndereco({ tipo: 'coisa', estado: 'x', origem: '', prova: 'talvez', visao: 'tudo' }))
      .toEqual({ kind: undefined, state: undefined, origem: undefined, prova: undefined, rotulo: undefined });
  });

  it('queryDoFiltro: só escreve os campos que a mudança cita, e o campo sem valor sai do endereço', () => {
    expect(queryDoFiltro({ prova: 'so_prova' })).toEqual({ prova: 'so_prova' });
    expect(queryDoFiltro({ prova: undefined, kind: 'fluxo' })).toEqual({ prova: undefined, tipo: 'fluxo' });
    expect(queryDoFiltro({ rotulo: 'todos' })).toEqual({ visao: 'todos' });
    expect(queryDoFiltro({})).toEqual({});
  });
});

describe('a escolha escreve o endereço', () => {
  it('Tipo, Estado, Origem e Prova viram query, a consulta vai com eles e "Todos" tira o parâmetro', async () => {
    await montar();
    await setValue(combo(/^Tipo/), 'fluxo');
    await waitFor(() => expect(query().tipo).toBe('fluxo'));
    await setValue(combo(/^Estado/), 'disabled');
    await setValue(combo(/^Origem/), 'treino');
    await setValue(combo(/^Prova/), 'so_prova');
    await waitFor(() => expect(query()).toMatchObject({ aba: 'aprendido', tipo: 'fluxo', estado: 'disabled', origem: 'treino', prova: 'so_prova' }));
    await waitFor(() => expect(ultimaConsulta()).toContain('nascido_de_prova=true'));
    expect(ultimaConsulta()).toContain('kind=fluxo');
    await setValue(combo(/^Prova/), '');
    await waitFor(() => expect(query().prova).toBeUndefined());
    expect(query().tipo).toBe('fluxo');                                  // os outros ficam
  });

  it('a visão Produto/QA/Todos vira `visao`, e um link que troca de app a devolve ao padrão', async () => {
    backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({
      apps: [{ package: 'com.pocqa.messenger', name: 'QA Messenger' }], sem_eixo: null, nao_resolvido: null,
    }));
    await montar();
    await click(byRole('radio', /^QA$/, container));
    await waitFor(() => expect(query().visao).toBe('qa'));
    await waitFor(() => expect(ultimaConsulta()).toContain('rotulo=qa'));
    await act(async () => { useUiStore.getState().trocarQuery({ app: 'com.pocqa.messenger' }); });
    await waitFor(() => expect(query().visao).toBeUndefined());
    expect(consultasDoLivro().filter((c) => c.query.get('app') === 'com.pocqa.messenger').map((c) => c.query.get('rotulo'))).toEqual([null]);
  });
});

describe('o endereço escolhe o filtro', () => {
  it('a query na entrada marca os seletores e vai na consulta, sem a pessoa tocar em nada', async () => {
    await montar({ tipo: 'fluxo', prova: 'so_prova', visao: 'todos' });
    expect(combo(/^Tipo/).value).toBe('fluxo');
    expect(combo(/^Prova/).value).toBe('so_prova');
    expect(ultimaConsulta()).toContain('nascido_de_prova=true');
    expect(ultimaConsulta()).toContain('kind=fluxo');
    expect(ultimaConsulta()).toContain('rotulo=todos');
    expect(byRole('radio', /^Todos/, container).getAttribute('aria-checked')).toBe('true');
  });

  it('valor desconhecido na query é ignorado: nenhum filtro, nenhum parâmetro', async () => {
    await montar({ tipo: 'coisa', estado: 'x', prova: 'talvez', visao: 'tudo' });
    expect(combo(/^Tipo/).value).toBe('');
    expect(combo(/^Prova/).value).toBe('');
    expect(ultimaConsulta()).not.toContain('kind=');
    expect(ultimaConsulta()).not.toContain('nascido_de_prova');
    expect(ultimaConsulta()).not.toContain('rotulo=');
  });
});
