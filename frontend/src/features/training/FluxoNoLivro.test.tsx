// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { FluxoNoLivro, linkDoFluxo, proximoPassoDoFluxo } from './FluxoNoLivro';

/**
 * 31.132: depois de salvar (e na sessão salva), a pessoa chega ao fluxo e vê o estado dele no Livro e o próximo passo.
 * Prova `simulated`: nenhuma rota real foi chamada.
 */

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const ITEM = (over: object = {}) => ({ kind: 'fluxo', ref: 'f-1', state: 'published', title: 'Abrir o perfil', ...over });
const detalhe = (item: object) => json({ item, evidencias: [], trilha: [], exposicoes: [] });

beforeEach(() => {
  installBrowserStubs();
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(flowId = 'f-1'): Promise<void> {
  await act(async () => root.render(<FluxoNoLivro flowId={flowId} />));
}
const linha = () => container.querySelector('[aria-label="Estado do fluxo no Livro"]') as HTMLElement | null;
const link = () => container.querySelector('a') as HTMLAnchorElement | null;
const selos = (nome: string) => Array.from(container.querySelectorAll('span')).filter((x) => !x.children.length && x.textContent === nome).length;

describe('31.132: o fluxo salvo no Livro', () => {
  it('em prova: diz o estado, o selo "em prova" e onde acompanhar, com o link ao item do Livro', async () => {
    backend.on('GET', /^\/api\/aprendizado\/fluxo\/f-1$/, () => detalhe(ITEM({ ensinado_em_prova: { persona: 'ig-1', sessao: 'trn-1' } })));
    await montar();
    await waitFor(() => expect(linha()).toBeTruthy());
    expect(text(linha()!)).toContain('No Livro agora: Publicado');
    expect(selos('em prova')).toBe(1);
    expect(text(linha()!)).toContain('Falta a prova: acompanhe no Livro');
    expect(link()!.getAttribute('href')).toBe('#/aprendizado?aba=aprendido&item=fluxo%3Af-1');
    expect(text(link()!)).toBe('Abrir no Livro');
  });

  it('em prova que espera a pessoa: manda para o "Confirmar que fica" com o motivo em português, sem o código', async () => {
    backend.on('GET', /^\/api\/aprendizado\/fluxo\/f-1$/, () => detalhe(ITEM({ espera_a_pessoa: 'classe_c', ensinado_em_prova: { persona: 'ig-1', sessao: 'trn-1' } })));
    await montar();
    await waitFor(() => expect(linha()).toBeTruthy());
    expect(text(linha()!)).toContain('Confirmar que fica');
    expect(text(linha()!)).toContain('risco alto');
    expect(text(linha()!)).not.toContain('classe_c');
  });

  it('desligado (e nascido de uma prova): diz Desligado e que só uma pessoa o reativa, com o selo de prova', async () => {
    backend.on('GET', /^\/api\/aprendizado\/fluxo\/f-1$/, () => detalhe(ITEM({ state: 'disabled', nascido_de_prova: true })));
    await montar();
    await waitFor(() => expect(linha()).toBeTruthy());
    expect(text(linha()!)).toContain('No Livro agora: Desligado');
    expect(text(linha()!)).toContain('só uma pessoa o reativa');
    expect(selos('Nascido de uma prova')).toBe(1);
    expect(selos('em prova')).toBe(0);
  });

  it('um passo para cada estado, e nenhum para o que o painel não conhece', () => {
    const base = { ensinado_em_prova: null, espera_a_pessoa: null };
    expect(proximoPassoDoFluxo({ ...base, state: 'published' })).toBe('Já vale para quem está no escopo escolhido.');
    expect(proximoPassoDoFluxo({ ...base, state: 'candidate' })).toContain('Para aprovar');
    expect(proximoPassoDoFluxo({ ...base, state: 'validated' })).toContain('Para aprovar');
    expect(proximoPassoDoFluxo({ ...base, state: 'deprecated' })).toContain('aposentado');
    expect(proximoPassoDoFluxo({ ...base, state: null })).toBe('');
  });

  it('o fluxo que já não existe (404) diz isso e não oferece um link morto', async () => {
    backend.on('GET', /^\/api\/aprendizado\/fluxo\/f-1$/, () => apiError(404, 'nao_encontrado', 'fluxo não encontrado'));
    await montar();
    await waitFor(() => expect(text(container)).toContain('não está mais no Livro'));
    expect(link()).toBeNull();
  });

  it('outra falha de leitura avisa que não deu para ler e mantém o link', async () => {
    backend.on('GET', /^\/api\/aprendizado\/fluxo\/f-1$/, () => apiError(500, 'erro', 'falhou'));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Não deu para ler o estado do fluxo agora'));
    expect(link()!.getAttribute('href')).toBe(linkDoFluxo('f-1'));
  });
});
