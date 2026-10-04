// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { leituraDoPedido, lerListaDeValidacoes, rotuloDoItem, vereditoDoPedido } from './validacao';

/**
 * 30.38 (b): a aba Validação contra o contrato do adendo v1.02 (`GET /api/aprendizado/validacoes`), com o backend
 * simulado. Prova `simulated`: nenhuma rota real foi chamada.
 */
const CONTAGEM = { pendente: 1, rodando: 0, feita: 1, recusada: 1, expirada: 0 };
const PEDIDOS = [
  { id: 'lv-3', estado: 'pendente', motivo: 'sem_aparelho', motivo_humano: 'Nenhum aparelho ocioso servia; tenta na volta seguinte.',
    item_ref: 'receita:7', item_kind: 'receita', app: 'com.pocqa.messenger', app_nome: 'QA Messenger', grupo: 'qa',
    run_id: null, run_origem: 'r-origem', aparelho: null, usd: 0, teto_usd: null, created_at: '2026-10-03T12:00:00Z',
    feito_em: null, expira_em: '2026-10-06T12:00:00Z', revisao_nova_id: null, comando: 'No QA Messenger, envie oi' },
  { id: 'lv-2', estado: 'recusada', motivo: 'sem_evidencia', motivo_humano: 'A execução terminou sem deixar evidência no item.',
    item_ref: 'fluxo:12', item_kind: 'fluxo', app: 'com.pocqa.messenger', app_nome: 'QA Messenger', grupo: 'qa',
    run_id: 'r-v2', run_origem: 'r-origem', aparelho: 'android-02', usd: 0.0512, teto_usd: 0.1, created_at: '2026-10-03T11:00:00Z',
    feito_em: '2026-10-03T11:05:00Z', expira_em: '2026-10-06T11:00:00Z', revisao_nova_id: null, comando: 'No QA Messenger, abra a conversa' },
  { id: 'lv-1', estado: 'feita', motivo: null, motivo_humano: null, item_ref: 'receita:3', item_kind: 'receita', app: null,
    app_nome: null, grupo: 'leitura', run_id: 'r-v1', run_origem: 'r-o1', aparelho: 'android-05', usd: 0.03, teto_usd: null,
    created_at: '2026-10-03T10:00:00Z', feito_em: '2026-10-03T10:04:00Z', expira_em: '2026-10-06T10:00:00Z', revisao_nova_id: 'lr-9',
    comando: 'Abra o feed' },
];

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;
let modo = 'on';
let vazio = false;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  modo = 'on';
  vazio = false;
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/aprendizado\/validacoes$/, (c) => {
    if (vazio) return json({ itens: [], contagem: { pendente: 0, rodando: 0, feita: 0, recusada: 0, expirada: 0 }, total: 0, modo });
    const estado = c.query.get('estado');
    return json({ itens: PEDIDOS.filter((p) => !estado || p.estado === estado), contagem: CONTAGEM, total: 3, modo });
  });
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'validacao' } }, 'replace');
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<AprendizadoPage />);
  });
}

const lista = () => container.querySelector('ul[aria-label="Pedidos de validação"]') as HTMLElement | null;
const cartao = (id: string) => container.querySelector(`li[data-pedido="${id}"]`) as HTMLElement | null;

describe('Aprendizado: a aba Validação', () => {
  it('lista os pedidos com o estado, o motivo em texto (código no title), o app, o custo e os caminhos', async () => {
    await montar();
    expect(byRole('tab', /^Validação/, container).getAttribute('aria-selected')).toBe('true');
    await waitFor(() => expect(lista()).toBeTruthy());
    expect(lista()!.querySelectorAll('li[data-pedido]')).toHaveLength(3);

    const recusada = cartao('lv-2')!;
    expect(text(recusada)).toContain('Recusada');
    expect(text(recusada)).toContain('Fluxo · QA Messenger');
    expect(text(recusada)).toContain('A execução terminou sem deixar evidência no item.');
    expect(text(recusada)).not.toContain('sem_evidencia');
    expect(recusada.querySelector('[title="sem_evidencia"]')).toBeTruthy();
    expect(text(recusada)).toContain('Rodou no android-02');
    expect(text(recusada)).toContain('US$ 0,0512 de teto US$ 0,10');
    expect((recusada.querySelector('a[href*="aba=aprendido"]') as HTMLAnchorElement).getAttribute('href'))
      .toBe('#/aprendizado?aba=aprendido&item=fluxo%3A12');

    // O pedido que não rodou não tem execução para abrir; o que rodou leva a ela.
    expect(text(cartao('lv-3')!)).not.toContain('Abrir execução');
    await click(byRole('button', /Abrir execução/, recusada));
    expect(useUiStore.getState().selectedRunId).toBe('r-v2');
  });

  it('as fichas por estado trazem a contagem de todos e filtram pela consulta', async () => {
    await montar();
    await waitFor(() => expect(lista()).toBeTruthy());
    const ficha = byRole('button', /^Recusada/, container);
    expect(text(ficha)).toContain('1');
    await click(ficha);
    await waitFor(() => {
      const ultima = backend.callsTo('GET', /^\/api\/aprendizado\/validacoes$/).at(-1);
      expect(ultima?.query.get('estado')).toBe('recusada');
    });
    await waitFor(() => expect(lista()!.querySelectorAll('li[data-pedido]')).toHaveLength(1));
    expect(useUiStore.getState().rota.query.estado).toBe('recusada');
  });

  it('pausada: o aviso explica por quê; sem pedido nenhum, o vazio explica o que é a validação', async () => {
    modo = 'off';
    vazio = true;
    await montar();
    await waitFor(() => expect(text(container)).toContain('Nenhum pedido de validação ainda'));
    expect(text(container)).toContain('A validação automática está pausada');
    expect(text(container)).toContain('o sistema grava aqui um pedido');
    expect(text(container)).toContain('fica desligada até ser ligada de propósito');
    expect(lista()).toBeNull();
  });

  it('ligada, sem aviso de pausa', async () => {
    await montar();
    await waitFor(() => expect(lista()).toBeTruthy());
    expect(text(container)).not.toContain('está pausada');
  });
});

describe('leitura tolerante', () => {
  it('descarta o item sem id ou com estado fora do vocabulário e completa a contagem com zero', () => {
    const l = lerListaDeValidacoes({ itens: [{ id: 'lv-1', estado: 'feita' }, { estado: 'feita' }, { id: 'x', estado: 'inventado' }],
                                     contagem: { feita: 1 } });
    expect(l.itens.map((p) => p.id)).toEqual(['lv-1']);
    expect(l.contagem).toEqual({ rodando: 0, pendente: 0, feita: 1, recusada: 0, expirada: 0 });
    expect(l.total).toBe(1);
    expect(l.modo).toBeNull();
  });

  it('rotuloDoItem: o tipo manda; sem ele, o prefixo do item; sem nada, um texto neutro', () => {
    expect(rotuloDoItem({ item_kind: 'fluxo', item_ref: 'fluxo:1' })).toBe('Fluxo');
    expect(rotuloDoItem({ item_kind: null, item_ref: 'receita:7' })).toBe('Receita');
    expect(rotuloDoItem({ item_kind: null, item_ref: 'li-3' })).toBe('Item do aprendizado');
  });
});

describe('30.43: o veredito e a leitura do pedido', () => {
  it('o mesmo motivo de recusa: com execução "Rodou; depois", sem ela "Não rodou"; sem motivo, o rótulo do estado', () => {
    const base = { estado: 'recusada' as const, motivo: 'sem_caminho', motivo_humano: 'sem caminho até o item' };
    expect(leituraDoPedido({ ...base, run_id: 'r-1' })).toBe('Rodou; depois: sem caminho até o item');
    expect(leituraDoPedido({ ...base, run_id: null })).toBe('Não rodou: sem caminho até o item');
    expect(leituraDoPedido({ ...base, motivo_humano: null, run_id: null })).toBe('Não rodou: sem_caminho');
    expect(leituraDoPedido({ estado: 'feita', motivo: null, motivo_humano: null, run_id: 'r-1' })).toBe('Feita');
    expect(leituraDoPedido({ estado: 'pendente', motivo: null, motivo_humano: null, run_id: null })).toBe('Pendente');
  });

  it('30.45: o pedido feito cuja evidência foi reclassificada inválida depois não é "a favor"', () => {
    const invalida_depois = { motivo: 'efeito_repetido', motivo_humano: 'O efeito saiu mais de uma vez.' };
    const feita = { estado: 'feita' as const, motivo: null, motivo_humano: null, run_id: 'r-1' };
    expect(vereditoDoPedido({ ...feita, invalida_depois })).toBe('inválida (O efeito saiu mais de uma vez.)');
    expect(leituraDoPedido({ ...feita, invalida_depois })).toBe('Rodou; depois: inválida — O efeito saiu mais de uma vez.');
    expect(vereditoDoPedido({ ...feita, invalida_depois: null })).toBe('a favor');
    expect(vereditoDoPedido({ ...feita, invalida_depois: { motivo: 'sem_evidencia', motivo_humano: null } })).toBe('inválida (sem_evidencia)');
    // o backend de antes do 30.45 não manda o campo: a lista lê null e o veredito segue o estado
    const l = lerListaDeValidacoes({ itens: [{ id: 'lv-1', estado: 'feita', item_ref: 'fluxo:x' }] });
    expect(l.itens[0]?.invalida_depois).toBeNull();
    const n = lerListaDeValidacoes({ itens: [{ id: 'lv-1', estado: 'feita', item_ref: 'fluxo:x', invalida_depois }] });
    expect(n.itens[0]?.invalida_depois).toEqual(invalida_depois);
  });

  it('30.31 (fatia 2): o ensaio só de leitura não é "sem evidência" nem falha', () => {
    expect(vereditoDoPedido({ estado: 'recusada', motivo: 'ensaio_so_leitura', motivo_humano: 'Ensaio só de leitura…' }))
      .toBe('ensaio só de leitura (parou antes do efeito; não conta)');
  });

  it('vereditoDoPedido: sem pedido é null (a legenda de sempre); expirada sem motivo não afirma nada', () => {
    expect(vereditoDoPedido(null)).toBeNull();
    expect(vereditoDoPedido({ estado: 'expirada', motivo: null, motivo_humano: null })).toBe('sem evidência (sem motivo registrado)');
  });
});
