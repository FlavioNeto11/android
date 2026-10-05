import { describe, expect, it } from 'vitest';
import { makeRun } from '../../test/fixtures';
import {
  abaPadraoDaExecucao, efeitosRepetidos, fonteDoEfeitoRepetido, fraseDoEfeitoRepetido, objetivosComSucesso,
  oQuePrecisaDaPessoa, pedidoEhLongo, resultadoDaExecucao,
} from './resumo';

const T0 = '2026-09-17T12:00:00.000Z';
const mais = (s: number) => new Date(Date.parse(T0) + s * 1000).toISOString();
const CONTAGEM = { succeeded: 0, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 };

describe('abaPadraoDaExecucao', () => {
  it('concluída abre no Relatório; em andamento, na Linha do tempo', () => {
    expect(abaPadraoDaExecucao('completed')).toBe('relatorio');
    for (const s of ['running', 'paused', 'cancelling'] as const) expect(abaPadraoDaExecucao(s)).toBe('timeline');
  });
  it('planejando, esperando informação ou plano pronto abrem no Plano; o que terminou mal, em Por aparelho', () => {
    for (const s of ['planning', 'needs_input', 'planned'] as const) expect(abaPadraoDaExecucao(s)).toBe('plano');
    for (const s of ['completed_with_issues', 'failed', 'cancelled'] as const) expect(abaPadraoDaExecucao(s)).toBe('instancias');
    expect(abaPadraoDaExecucao(undefined)).toBe('instancias');
  });
});

describe('resultadoDaExecucao', () => {
  const agora = Date.parse(T0) + 600_000;
  it('concluída com sucesso, com a duração', () => {
    expect(resultadoDaExecucao({ status: 'completed', started_at: T0, finished_at: mais(123) }, agora))
      .toBe('Concluída com sucesso em 2 min 03 s');
  });
  it('em execução conta até agora; sem início não inventa duração', () => {
    expect(resultadoDaExecucao({ status: 'running', started_at: T0, finished_at: null }, agora)).toBe('Em execução há 10 min');
    expect(resultadoDaExecucao({ status: 'completed', started_at: null, finished_at: null }, agora)).toBe('Concluída com sucesso');
  });
  it('as demais situações dizem o que aconteceu', () => {
    expect(resultadoDaExecucao({ status: 'completed_with_issues', started_at: T0, finished_at: mais(5) }, agora)).toBe('Concluída com problemas em 5 s');
    expect(resultadoDaExecucao({ status: 'failed', started_at: T0, finished_at: mais(60) }, agora)).toBe('Falhou após 1 min');
    // 29.93: o fim do trabalho automático, e ela espera um gesto da pessoa (não "concluída").
    expect(resultadoDaExecucao({ status: 'awaiting_person', started_at: T0, finished_at: mais(60) }, agora)).toBe('Aguardando você após 1 min');
    expect(resultadoDaExecucao({ status: 'planned', started_at: null, finished_at: null }, agora)).toContain('Nada foi executado');
    expect(resultadoDaExecucao({ status: 'needs_input', started_at: null, finished_at: null }, agora)).toContain('pedindo informação');
  });
});

describe('objetivosComSucesso e pedidoEhLongo', () => {
  it('conta com o plural certo e some quando não há objetivos', () => {
    expect(objetivosComSucesso(makeRun({ counts: { ...CONTAGEM, succeeded: 1 }, instances_used: 1 }))).toBe('1 de 1 objetivo com sucesso');
    expect(objetivosComSucesso(makeRun({ counts: { ...CONTAGEM, succeeded: 2, failed: 1 }, instances_used: 3 }))).toBe('2 de 3 objetivos com sucesso');
    expect(objetivosComSucesso(makeRun({ counts: CONTAGEM, instances_used: 0 }))).toBeNull();
  });
  it('B4: nada foi executado (planejando, plano pronto, esperando informação): não conta sucesso', () => {
    for (const status of ['planning', 'planned', 'needs_input'] as const) {
      expect(objetivosComSucesso(makeRun({ status, counts: CONTAGEM, instances_used: 1 }))).toBeNull();
    }
    // Em andamento ou terminada, o "0 de N" é informação (algo rodou).
    expect(objetivosComSucesso(makeRun({ status: 'running', counts: { ...CONTAGEM, running: 1 }, instances_used: 1 })))
      .toBe('0 de 1 objetivo com sucesso');
    expect(objetivosComSucesso(makeRun({ status: 'failed', counts: { ...CONTAGEM, failed: 1 }, instances_used: 1 })))
      .toBe('0 de 1 objetivo com sucesso');
  });
  it('o pedido é longo por tamanho ou por quebra de linha', () => {
    expect(pedidoEhLongo('Abra o app')).toBe(false);
    expect(pedidoEhLongo('x'.repeat(101))).toBe(true);
    expect(pedidoEhLongo('a\nb')).toBe(true);
  });
});

describe('oQuePrecisaDaPessoa', () => {
  const base = { status: 'running' as const, perguntas: 0, bloqueados: 0, textosParaAprovar: 0, terminal: false };
  it('nada a decidir: lista vazia', () => {
    expect(oQuePrecisaDaPessoa(base)).toEqual([]);
  });
  it('pergunta, bloqueio e texto, cada um no singular e no plural, com a guia que resolve', () => {
    const l = oQuePrecisaDaPessoa({ ...base, status: 'needs_input', perguntas: 2, bloqueados: 1, textosParaAprovar: 3 });
    expect(l.map((x) => x.chave)).toEqual(['perguntas', 'bloqueios', 'textos']);
    expect(l[0]?.texto).toBe('Responder às 2 perguntas da IA para a execução seguir');
    expect(l[1]).toMatchObject({ texto: '1 objetivo espera a sua decisão', aba: 'instancias' });
    expect(l[2]).toMatchObject({ texto: '3 textos esperam a sua aprovação', aba: 'textos' });
  });
  it('objetivo bloqueado de execução terminada não é pedido à pessoa', () => {
    expect(oQuePrecisaDaPessoa({ ...base, status: 'completed_with_issues', bloqueados: 1, terminal: true })).toEqual([]);
  });
});

describe('29.60: efeito repetido', () => {
  it('fala em português quem contou e quantas vezes, e só pega etapas com 2 ou mais cópias', () => {
    expect(fonteDoEfeitoRepetido('verificador')).toBe('contado na tela pelo verificador');
    expect(fonteDoEfeitoRepetido('acoes')).toBe('contado pelas ações gravadas desta execução');
    expect(fonteDoEfeitoRepetido('provedor')).toBe('contado no próprio app de QA ao fim da validação');
    expect(fraseDoEfeitoRepetido(2)).toBe('apareceu 2 vezes');
    const passos = [
      { id: 'a', result: null },
      { id: 'b', result: { efeito_repetido: { copias: 2, fonte: 'acoes' as const } } },
      { id: 'c', result: {} },
    ];
    expect(efeitosRepetidos(passos).map((p) => p.id)).toEqual(['b']);
  });
});
