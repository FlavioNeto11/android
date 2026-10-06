// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import type { EntradaDoLivro } from './model';

/**
 * 31.131 (adendo v1.87): o fluxo que nasceu de uma prova (sessão de treino aberta como prova) leva o selo "Nascido de uma
 * prova" no Livro; o filtro "Prova" separa prova de uso real; o motivo do desligamento aparece como o backend o escreveu.
 * Prova `simulated`: nenhuma rota real foi chamada.
 */

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const MOTIVO = 'desligado de propósito: o fluxo nasceu de uma prova e não vale como uso real';

function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'fluxo', ref: 'f-1', state: 'disabled', native_status: 'disabled', title: 'Pesquise nas configurações', app: 'com.android.settings',
    origin: 'treino', side_effect: false, human_origin: false, requires_owner: false, created_at: '2026-10-06T10:00:00Z',
    state_at: '2026-10-06T10:29:00Z', last_used_at: null, uses: 0, evidence: { for: 0, against: 0 }, count: null,
    detail: null, acoes: [], por_que_nao_publica: null, ...over,
  };
}
const DE_PROVA = entrada({ ref: 'f-prova', title: 'Pesquise nas configurações por {termo}', nascido_de_prova: true,
  por_que_nao_publica: { codigo: 'vetado', espera_o_dono: false, detalhe: MOTIVO } });
const REAL = entrada({ ref: 'f-real', title: 'Abra o Wi-Fi', state: 'published', native_status: 'active' });
const DESLIGADO_POR_FALHA = entrada({ ref: 'f-falha', title: 'Abra o Bluetooth', nascido_de_prova: false,
  por_que_nao_publica: { codigo: 'vetado', espera_o_dono: false, detalhe: 'desligado depois de 3 falhas seguidas' } });
const RECEITA_COM_A_MARCA = entrada({ kind: 'receita', ref: '9', title: 'Tocar no Wi-Fi', nascido_de_prova: true });

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.install();
  for (const rota of ['pendentes', 'revisar', 'intencao']) backend.on('GET', new RegExp(`^/api/aprendizado/${rota}$`), () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], sem_eixo: null, nao_resolvido: null }));
  // O backend desta prova IGNORA o parâmetro `nascido_de_prova`: quem separa é a guarda do painel.
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [DE_PROVA, REAL, DESLIGADO_POR_FALHA, RECEITA_COM_A_MARCA], total: 4, contagem: {} }));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: {} } });
});

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<AprendizadoPage />));
  await click(byRole('tab', /^Aprendido/, container));
}

const item = (ref: string) => container.querySelector(`[data-item="${ref}"]`) as HTMLElement | null;
const selos = (el: HTMLElement) => Array.from(el.querySelectorAll('span')).filter((x) => !x.children.length && x.textContent === 'Nascido de uma prova');
const rotulosDosItens = () => Array.from(container.querySelectorAll('[data-item]')).map((e) => e.getAttribute('data-item'));

describe('31.131: o fluxo nascido de uma prova no Livro', () => {
  it('só o fluxo com a marca leva o selo, e o motivo do desligamento aparece como o backend o escreveu', async () => {
    await montar();
    await waitFor(() => expect(item('fluxo:f-prova')).toBeTruthy());
    expect(selos(item('fluxo:f-prova')!)).toHaveLength(1);
    expect(item('fluxo:f-prova')!.querySelector('[title^="Nasceu de uma prova"]')).toBeTruthy();   // a explicação está no selo
    expect(text(item('fluxo:f-prova')!)).toContain(MOTIVO);                                          // o motivo vai como veio
    expect(text(item('fluxo:f-prova')!)).toContain('Desligado');
    expect(selos(item('fluxo:f-real')!)).toHaveLength(0);                                            // sem o campo (backend anterior)
    expect(selos(item('fluxo:f-falha')!)).toHaveLength(0);                                           // `false`: desligado por falha, não por prova
    expect(text(item('fluxo:f-falha')!)).toContain('desligado depois de 3 falhas seguidas');
    expect(selos(item('receita:9')!)).toHaveLength(0);                                               // a marca é do fluxo
  });

  it('o filtro "Prova" manda o parâmetro e separa prova de uso real, mesmo com um backend que ainda ignora o parâmetro', async () => {
    await montar();
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(4));
    const filtro = () => byRole('combobox', /^Prova/, container) as HTMLSelectElement;
    expect(Array.from(filtro().options).map((o) => o.textContent)).toEqual(['Todos', 'Só os nascidos de uma prova', 'Sem os de prova (uso real)']);
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.has('nascido_de_prova')).toBe(false);

    await setValue(filtro(), 'so_prova');
    await waitFor(() => expect(rotulosDosItens()).toEqual(['fluxo:f-prova', 'receita:9']));
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.get('nascido_de_prova')).toBe('true');

    await setValue(filtro(), 'sem_prova');
    await waitFor(() => expect(rotulosDosItens()).toEqual(['fluxo:f-real', 'fluxo:f-falha']));
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.get('nascido_de_prova')).toBe('false');

    await setValue(filtro(), '');
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(4));
    expect(backend.callsTo('GET', /^\/api\/aprendizado$/).at(-1)!.query.has('nascido_de_prova')).toBe(false);
  });

  it('o filtro "Só os nascidos de uma prova" sem nenhum diz que não há, e não mostra a lista inteira', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [REAL, DESLIGADO_POR_FALHA], total: 2, contagem: {} }));
    await montar();
    await waitFor(() => expect(rotulosDosItens()).toHaveLength(2));
    await setValue(byRole('combobox', /^Prova/, container) as HTMLSelectElement, 'so_prova');
    await waitFor(() => expect(text(container)).toContain('Nada aprendido com este filtro'));
    expect(allByRole('listitem', /.*/, container).filter((e) => e.hasAttribute('data-item'))).toHaveLength(0);
  });
});
