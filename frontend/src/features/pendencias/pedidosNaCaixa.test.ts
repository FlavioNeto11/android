import { describe, expect, it } from 'vitest';
import type { Approval } from '../../api/types';
import { makePedido, makeRun } from '../../test/fixtures';
import { montarPendencias } from './modelo';

/**
 * Emenda à ADR-062 (item 28.9): o pedido `aguardando_pessoa` é uma origem da caixa e agrupa as decisões dele, sem contar
 * duas vezes. Prova `simulated` (entradas puras, sem backend).
 */
const SEM_OBJETIVOS = { succeeded: 0, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 };
const PARADA = (id: string, pedido_id: string | null) => makeRun({
  id, short_id: id, status: 'needs_input', command: 'Siga o perfil X', status_detail: 'Qual perfil?', counts: SEM_OBJETIVOS,
  created_at: '2026-10-02T09:00:00Z', pedido_id,
});
const APROVACAO = (id: string, run_id: string | null): Approval => ({
  id, profile_id: 'p1', run_id, objective_id: null, step_id: null, capability: 'comentar', target: '@alvo', summary: `Texto ${id}`,
  generated_content: 'oi', approved_content: null, content: 'oi', status: 'pending', created_at: '2026-10-02T09:30:00Z',
  decided_at: null, decided_note: null, decided_by: null, interaction_id: null,
});
const AGUARDANDO = makePedido({ id: 'ped_x', titulo: 'Vigiar preços', estado: 'aguardando_pessoa', atualizado_em: '2026-10-02T08:00:00Z' });

describe('pedido na caixa de Pendências', () => {
  it('o pedido aguardando entra como UMA linha; a execução parada e a aprovação dele ficam sob ele e não contam de novo', () => {
    const lista = montarPendencias({
      aprendizado: [], aprovacoes: [APROVACAO('a1', 'r-do-pedido'), APROVACAO('a2', null)],
      execucoes: [PARADA('r-do-pedido', 'ped_x'), PARADA('r-avulsa', null)], pedidos: [AGUARDANDO],
    });
    // 1 pedido + 1 aprovação avulsa + 1 execução avulsa; as duas do pedido não entram sozinhas.
    expect(lista.map((p) => p.chave).sort()).toEqual(['execucao:r-avulsa', 'pedido:ped_x', 'persona:a2']);
    const pedido = lista.find((p) => p.origem === 'pedido');
    expect(pedido).toMatchObject({ titulo: 'Vigiar preços', acao: 'Decidir', destino: { tela: 'pedidos', segmentos: ['ped_x'] } });
    expect(pedido?.filhas?.map((f) => f.chave).sort()).toEqual(['execucao:r-do-pedido', 'persona:a1']);
    expect(pedido?.detalhe).toContain('2 decisões');
  });

  it('o motivo vem em palavras: ocorrência incerta pede conferir se a ação aconteceu e o item leva a decidir e retomar', () => {
    const incerto = makePedido({ id: 'ped_i', titulo: 'Postar resumo', estado: 'aguardando_pessoa', ocorrencias_por_estado: { incerta: 1 } });
    const [p] = montarPendencias({ aprendizado: [], aprovacoes: [], execucoes: [], pedidos: [incerto] });
    expect(p?.detalhe).toContain('Ocorrência incerta: confira se a ação aconteceu');
    expect(p?.detalhe).toContain('decidir e retomar');
    expect(p?.destino).toEqual({ tela: 'pedidos', segmentos: ['ped_i'] });
  });

  it('só `aguardando_pessoa` conta: pausado, ativo e terminais ficam de fora', () => {
    const lista = montarPendencias({
      aprendizado: [], aprovacoes: [], execucoes: [],
      pedidos: [AGUARDANDO, makePedido({ id: 'p2', estado: 'pausado' }), makePedido({ id: 'p3', estado: 'ativo' }),
                makePedido({ id: 'p4', estado: 'cancelado' })],
    });
    expect(lista.map((p) => p.chave)).toEqual(['pedido:ped_x']);
  });

  it('sem pedido aguardando, a execução parada de um pedido continua contando como sempre', () => {
    const lista = montarPendencias({ aprendizado: [], aprovacoes: [], execucoes: [PARADA('r1', 'ped_x')], pedidos: [] });
    expect(lista.map((p) => p.chave)).toEqual(['execucao:r1']);
  });
});
