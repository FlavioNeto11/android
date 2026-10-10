// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import { APPS } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { AprendizadoPage } from './AprendizadoPage';
import type { EntradaDoLivro } from './model';

/**
 * 31.304: o filtro "só as descobertas pela IA" do Livro, com contagem. O servidor não filtra por `nasceu_de_exploracao`
 * (31.299): o painel conta e filtra as linhas carregadas, e diz quando a lista veio cortada. Prova `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  useAppStore.setState({ apps: APPS });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: {} } });
});

function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'receita', ref: '9', state: 'published', native_status: 'active', title: 'Tocar no Wi-Fi', app: 'com.android.settings',
    origin: 'execucao', side_effect: false, human_origin: false, requires_owner: false, created_at: '2026-10-06T10:00:00Z',
    state_at: '2026-10-06T10:29:00Z', last_used_at: null, uses: 0, evidence: { for: 0, against: 0 }, count: null,
    detail: null, acoes: [], por_que_nao_publica: null, ...over,
  };
}

async function abrirLivro(itens: EntradaDoLivro[], total = itens.length): Promise<void> {
  backend = new FakeBackend();
  backend.install();
  for (const rota of ['pendentes', 'revisar', 'intencao']) backend.on('GET', new RegExp(`^/api/aprendizado/${rota}$`), () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], sem_eixo: null, nao_resolvido: null }));
  backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens, total, contagem: { receita: { published: itens.length } } }));
  await act(async () => root.render(<AprendizadoPage />));
  await click(byRole('tab', /^Aprendido/, container));
  await waitFor(() => expect(container.querySelector('[aria-label="Contagem por tipo e estado"]')).toBeTruthy());
}

const refs = () => Array.from(container.querySelectorAll('[data-item]')).map((e) => e.getAttribute('data-item'));
const botao = () => Array.from(container.querySelectorAll('button')).find((b) => /descobertas? pela IA$/.test(b.textContent ?? ''));

const TRES = [
  entrada({ ref: '1', title: 'Tocar no Wi-Fi', nasceu_de_exploracao: true }),
  entrada({ ref: '2', title: 'Abrir o Bluetooth', nasceu_de_exploracao: false }),
  entrada({ ref: '3', title: 'Abrir a câmera', nasceu_de_exploracao: true }),
];

it('a contagem mostra quantas receitas a IA descobriu; clicar filtra a lista e clicar de novo tira o filtro', async () => {
  await abrirLivro(TRES);
  expect(text(botao()!)).toBe('2 descobertas pela IA');
  expect(botao()!.getAttribute('aria-pressed')).toBe('false');
  expect(refs()).toEqual(['receita:1', 'receita:2', 'receita:3']);
  await click(botao()!);
  expect(botao()!.getAttribute('aria-pressed')).toBe('true');
  expect(refs()).toEqual(['receita:1', 'receita:3']);
  expect(useUiStore.getState().rota.query.descoberta).toBe('1');
  expect(text(container.querySelector('[data-descobertas]')!)).toContain('2 de 3 itens são receitas descobertas pela IA.');
  await click(botao()!);
  expect(refs()).toEqual(['receita:1', 'receita:2', 'receita:3']);
  expect(useUiStore.getState().rota.query.descoberta).toBeUndefined();
});

it('sem receita descoberta (ou backend sem a marca) o botão não aparece', async () => {
  await abrirLivro([entrada({ ref: '2', nasceu_de_exploracao: false }), entrada({ ref: '4', title: 'Outra' })]);
  expect(botao()).toBeUndefined();
});

it('o filtro vem do endereço, soma com a busca e avisa que olha só os itens carregados quando a lista veio cortada', async () => {
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: { descoberta: '1', busca: 'câmera' } } });
  await abrirLivro(TRES, 40);
  expect(refs()).toEqual(['receita:3']);
  expect(text(container.querySelector('[data-descobertas]')!)).toContain('O filtro olha os 3 itens carregados, de 40.');
  expect(text(container.querySelector('[data-busca]')!)).toContain('1 de 2 itens com “câmera”.');
});

it('com o filtro ligado e nenhuma descoberta na lista, diz isso e deixa o botão para desligar', async () => {
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: { descoberta: '1' } } });
  await abrirLivro([entrada({ ref: '2', nasceu_de_exploracao: false })]);
  await waitFor(() => expect(text(container)).toContain('Nenhuma receita descoberta pela IA nesta lista'));
  expect(text(botao()!)).toBe('0 descobertas pela IA');
  await click(botao()!);
  expect(refs()).toEqual(['receita:2']);
});
