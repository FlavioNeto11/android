// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import type { EntradaDoLivro } from './model';
import { lerProvaDaCandidata, rotuloDoResultado, textoDaProva } from './provaDaCandidata';

/**
 * 31.270: a receita candidata legível na aba Aprendido: concordâncias seguidas de N, última consulta (quando e resultado) e a ativa que
 * ela quer substituir. Prova `simulated`: resposta falsa do central; o campo `prova_da_candidata` é o contrato PROPOSTO (aditivo), e sem
 * ele a linha mostra só a contagem que o backend atual já manda em `detail`.
 */

function receita(over: Partial<EntradaDoLivro> & { prova_da_candidata?: unknown }): EntradaDoLivro {
  return {
    kind: 'receita', ref: '222', state: 'candidate', native_status: 'candidate', title: 'abrir_perfil (v2)', app: 'com.exemplo.cheio',
    origin: 'execucao', side_effect: false, human_origin: false, requires_owner: false, created_at: '2026-10-06T10:00:00Z',
    state_at: '2026-10-06T10:00:00Z', last_used_at: null, uses: 0, evidence: { for: 0, against: 0 }, count: null,
    detail: 'sombra 0/0', acoes: [], por_que_nao_publica: null, ...over,
  };
}

const PROVA = {
  concordancias: 0, necessarias: 2,
  ultima_consulta: { em: '2026-10-07T11:00:00Z', resultado: 'divergiu' },
  substitui: { ref: '198', versao: 1, estado: 'active' },
};

let backend: FakeBackend;
let root: Root | undefined;
let container: HTMLDivElement | undefined;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido' } }, 'replace');
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  container?.remove();
  root = undefined; container = undefined;
});

async function abrir(itens: EntradaDoLivro[]): Promise<void> {
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens, total: itens.length, contagem: { receita: { candidate: itens.length } } }));
  const c = document.createElement('div');
  container = c;
  document.body.appendChild(c);
  const r = createRoot(c);
  root = r;
  await act(async () => { r.render(<AprendizadoPage />); });
  await waitFor(() => expect(c.querySelector('[data-item]')).not.toBeNull());
}

const linha = (ref: string) => container!.querySelector(`[data-item="receita:${ref}"]`) as HTMLElement;

describe('31.270: o leitor tolerante da prova da candidata', () => {
  it('só vale para receita candidata; o campo do contrato vence a contagem do detail', () => {
    expect(lerProvaDaCandidata(receita({ state: 'published', native_status: 'active' }))).toBeNull();
    expect(lerProvaDaCandidata(receita({ kind: 'fluxo' }))).toBeNull();
    expect(lerProvaDaCandidata(receita({ prova_da_candidata: PROVA }))).toEqual({
      concordancias: 0, necessarias: 2, fonte: 'contrato',
      consulta: { em: '2026-10-07T11:00:00Z', resultado: 'divergiu' }, substitui: { ref: '198', versao: 1, estado: 'active' },
    });
  });

  it('backend anterior: só a contagem de "sombra a/b"; sem nenhum dos dois, nada se afirma; "não informado" nunca vira zero', () => {
    expect(lerProvaDaCandidata(receita({ detail: 'sombra 1/3' }))).toEqual({ concordancias: 1, necessarias: null, consulta: null, substitui: null, fonte: 'detalhe' });
    expect(lerProvaDaCandidata(receita({ detail: null }))).toBeNull();
    expect(lerProvaDaCandidata(receita({ detail: 'em_prova' }))).toBeNull();
    // campos do contrato quebrados: a leitura segue com o que presta e não inventa
    const p = lerProvaDaCandidata(receita({ prova_da_candidata: { concordancias: 1, necessarias: 'dois', ultima_consulta: { em: 'ontem' }, substitui: {} } }))!;
    expect(p).toMatchObject({ concordancias: 1, necessarias: null, consulta: null, substitui: null });
    // sem a contagem no campo, cai para o detail
    expect(lerProvaDaCandidata(receita({ detail: 'sombra 2/2', prova_da_candidata: { necessarias: 2 } }))!.fonte).toBe('detalhe');
  });

  it('os textos: n de N, sem o total só n, e o resultado em palavras (código novo aparece como veio)', () => {
    expect(textoDaProva({ concordancias: 0, necessarias: 2, consulta: null, substitui: null, fonte: 'contrato' })).toBe('0 de 2 concordâncias seguidas com a IA');
    expect(textoDaProva({ concordancias: 1, necessarias: null, consulta: null, substitui: null, fonte: 'detalhe' })).toBe('1 concordância seguida com a IA');
    expect(rotuloDoResultado('concordou')).toBe('concordou com a IA');
    expect(rotuloDoResultado('outro_escopo')).toContain('outro escopo');
    expect(rotuloDoResultado('quarentena')).toContain('quarentena');
    expect(rotuloDoResultado('algo_novo')).toBe('algo_novo');
  });
});

describe('31.270: a linha da receita candidata na aba Aprendido', () => {
  it('com o campo do contrato: 0 de 2, última consulta com o resultado e a ativa que ela quer substituir', async () => {
    await abrir([receita({ prova_da_candidata: PROVA })]);
    const prova = linha('222').querySelector('[data-prova-da-candidata]') as HTMLElement;
    expect(text(prova)).toContain('Prova da candidata: 0 de 2 concordâncias seguidas com a IA.');
    expect(text(prova.querySelector('[data-ultima-consulta]') as HTMLElement)).toContain('divergiu da IA (a prova recomeça do zero)');
    expect(text(prova.querySelector('[data-substitui]') as HTMLElement).trim()).toBe('Quando passar, assume o lugar da receita 198 (v1), hoje ativa.');
    // a contagem crua do detail não se repete ao lado
    expect(text(linha('222'))).not.toContain('na sombra, concordou');
  });

  it('backend atual (sem o campo): a candidata com 0/0 já diz "0 concordâncias seguidas", sem consulta nem substituta inventadas', async () => {
    await abrir([receita({})]);
    const prova = linha('222').querySelector('[data-prova-da-candidata]') as HTMLElement;
    expect(text(prova)).toContain('Prova da candidata: 0 concordâncias seguidas com a IA.');
    expect(prova.querySelector('[data-ultima-consulta]')).toBeNull();
    expect(prova.querySelector('[data-substitui]')).toBeNull();
  });

  it('quem não é receita candidata não ganha a linha, e o detail continua como antes', async () => {
    await abrir([receita({ ref: '9', state: 'published', native_status: 'active', detail: 'sombra 2/3' })]);
    expect(linha('9').querySelector('[data-prova-da-candidata]')).toBeNull();
    expect(text(linha('9'))).toContain('na sombra, concordou com a IA em 2 de 3');
  });

  it('consulta sem a data: mostra só o resultado; resultado novo aparece como veio', async () => {
    await abrir([receita({ prova_da_candidata: { concordancias: 1, necessarias: 2, ultima_consulta: { resultado: 'quarentena' }, substitui: null } })]);
    const prova = linha('222').querySelector('[data-prova-da-candidata]') as HTMLElement;
    expect(text(prova)).toContain('1 de 2 concordâncias seguidas com a IA.');
    expect(text(prova)).toContain('Última consulta: a receita está em quarentena.');
    expect(prova.querySelector('[data-substitui]')).toBeNull();
  });
});
