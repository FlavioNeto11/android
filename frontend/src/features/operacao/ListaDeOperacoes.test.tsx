// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { contarPorStatus, descricaoDaOperacao, filtrarOperacoes } from './modelo';
import { OperacaoPage } from './OperacaoPage';

/**
 * A lista de operações legível: o que distingue uma operação da outra (hora, app, ação) e o filtro por estado e por trecho do
 * objetivo. Prova `simulated`: servidor falso no formato do adendo v1.94.
 */

const op = (id: string, command: string, status: string | null, over: Record<string, unknown> = {}) => ({
  id, command, app_id: 'instagram', acao_final: 'preparar', status, created_at: new Date(Date.now() - 3 * 60_000).toISOString(),
  finished_at: null, capacidade: { solicitados: 2, concluidas: 1, bloqueadas: 0 }, ...over,
});
const LISTA = [
  op('op-a', 'Comentar no post da loja', 'em_curso', { custo: { total_usd: 0.2894 } }),
  op('op-b', 'Ler a publicação NOVIDADES de hoje', 'concluida', { acao_final: 'executar' }),
  op('op-c', 'Comentar na publicação antiga', 'concluida_com_bloqueios', { app_id: null, created_at: null }),
  op('op-d', 'Seguir o perfil da marca', 'cancelada'),
];

describe('o modelo da lista', () => {
  it('filtrarOperacoes: estado e trecho do objetivo, sem caixa nem acento; vazio não filtra', () => {
    const l = LISTA.map((o) => ({ status: o.status as never, command: o.command }));
    expect(filtrarOperacoes(l, '', '')).toHaveLength(4);
    expect(filtrarOperacoes(l, 'concluida', '')).toHaveLength(1);
    expect(filtrarOperacoes(l, '', 'publicacao').map((o) => o.command)).toEqual(['Ler a publicação NOVIDADES de hoje', 'Comentar na publicação antiga']);
    expect(filtrarOperacoes(l, '', 'novidades')).toHaveLength(1);
    expect(filtrarOperacoes(l, 'em_curso', 'publicação')).toHaveLength(0);
    expect(filtrarOperacoes(l, '', '   ')).toHaveLength(4);
  });
  it('contarPorStatus: operação sem estado não entra em nenhum', () => {
    expect(contarPorStatus([{ status: 'em_curso' }, { status: 'em_curso' }, { status: null }, { status: 'cancelada' }]))
      .toEqual({ em_curso: 2, concluida: 0, concluida_com_bloqueios: 0, cancelada: 1 });
  });
  it('descricaoDaOperacao: hora, app e ação em palavras; o que falta diz que falta', () => {
    expect(descricaoDaOperacao({ created_at: '2026-10-07T10:00:00Z', app_id: 'instagram', acao_final: 'executar' }, (i) => `às ${i.slice(11, 16)}`))
      .toBe('Criada às 10:00 · instagram · Preparar e executar');
    expect(descricaoDaOperacao({ created_at: null, app_id: 'a', acao_final: 'preparar', custo_usd: 0.05 })).toContain('· US$ 0,0500');
    expect(descricaoDaOperacao({ created_at: null, app_id: null, acao_final: null })).toBe('Criada em data não informada · app não informado · não informada');
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/operacoes$/, () => json({ items: LISTA }));
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: [], query: {} } });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const abrir = async () => {
  await act(async () => root.render(<OperacaoPage />));
  await waitFor(() => expect(container.querySelectorAll('ul[aria-label="Operações"] li')).toHaveLength(4));
};
const linhas = () => Array.from(container.querySelectorAll('ul[aria-label="Operações"] li')) as HTMLElement[];
const campo = (rotulo: RegExp) => {
  const l = Array.from(container.querySelectorAll('label')).find((x) => rotulo.test(x.textContent ?? ''))!;
  return document.getElementById(l.htmlFor) as HTMLSelectElement & HTMLInputElement;
};

describe('a lista na tela', () => {
  it('cada linha traz quando foi criada, o app e a ação, além do estado e das contagens', async () => {
    await abrir();
    const [a, b, c] = linhas();
    expect(text(a!.querySelector('[data-meta]')!)).toMatch(/^Criada hoje, \d\d:\d\d · instagram · Só preparar · US\$ 0,2894$/);
    expect(text(b!.querySelector('[data-meta]')!)).toContain('Preparar e executar');
    expect(text(b!.querySelector('[data-meta]')!)).not.toContain('US$');                    // sem custo no resumo, nada de zero inventado
    expect(text(c!.querySelector('[data-meta]')!)).toBe('Criada em data não informada · app não informado · Só preparar');
    expect(text(a!)).toContain('Em andamento · 2 solicitados, 1 concluídas, 0 bloqueadas');
    expect(text(container)).toContain('4 de 4 operações');
  });

  it('o filtro por estado mostra só os desse estado, com a contagem de cada um; só entram os estados que existem', async () => {
    await abrir();
    const estado = campo(/^Estado/);
    expect(Array.from(estado.options).map((o) => o.text)).toEqual(['Todos (4)', 'Em andamento (1)', 'Concluída (1)', 'Concluída com bloqueios (1)', 'Cancelada (1)']);
    await setValue(estado, 'cancelada');
    expect(linhas()).toHaveLength(1);
    expect(text(linhas()[0]!)).toContain('Seguir o perfil da marca');
    expect(text(container)).toContain('1 de 4 operações');
  });

  it('a busca no objetivo ignora caixa e acento e combina com o estado; sem resultado, o estado vazio manda limpar', async () => {
    await abrir();
    await setValue(campo(/^Buscar no objetivo/), 'PUBLICACAO');
    expect(linhas().map((l) => text(l.querySelector('a')!))).toEqual(['Ler a publicação NOVIDADES de hoje', 'Comentar na publicação antiga']);
    await setValue(campo(/^Estado/), 'em_curso');
    expect(linhas()).toHaveLength(0);
    expect(text(container)).toContain('Nenhuma operação com este filtro');
    expect(text(container)).toContain('0 de 4 operações');
    await setValue(campo(/^Estado/), '');
    expect(linhas()).toHaveLength(2);
  });

  it('sem nenhuma operação não há filtro: só o estado vazio de sempre', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: [] }));
    await act(async () => root.render(<OperacaoPage />));
    await waitFor(() => expect(text(container)).toContain('Nenhuma operação ainda'));
    expect(container.querySelectorAll('select')).toHaveLength(0);
  });
});
