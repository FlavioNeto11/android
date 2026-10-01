import { describe, expect, it } from 'vitest';
import { ABAS, abaAoEscolherSecao, abaDoPedido, abaPadraoDaSecao, secaoDaAba, SECOES } from './abas';

describe('seções da persona', () => {
  it('são 5 no primeiro nível, na ordem do briefing', () => {
    expect(SECOES.map((s) => s.rotulo)).toEqual(['Visão geral', 'Perfil', 'Contas e aparelhos', 'Atividade', 'Avançado']);
    expect(SECOES.length).toBeLessThanOrEqual(5);
  });

  it('cada uma das 11 guias mora em exatamente uma seção (nenhuma rota antiga fica sem casa)', () => {
    const todas = SECOES.flatMap((s) => [...s.guias]);
    expect([...todas].sort()).toEqual([...ABAS].sort());
    expect(new Set(todas).size).toBe(ABAS.length);
  });

  it('a seção sai da guia: link antigo para uma guia abre a seção certa', () => {
    expect(secaoDaAba('visao').id).toBe('visao');
    for (const g of ['persona', 'imagens', 'memoria'] as const) expect(secaoDaAba(g).id).toBe('perfil');
    for (const g of ['contas', 'aparelhos'] as const) expect(secaoDaAba(g).id).toBe('contas');
    for (const g of ['interacoes', 'execucoes', 'aprovacoes'] as const) expect(secaoDaAba(g).id).toBe('atividade');
    for (const g of ['habilidades', 'config'] as const) expect(secaoDaAba(g).id).toBe('avancado');
  });

  it('escolher a seção abre a primeira guia dela', () => {
    expect(SECOES.map(abaPadraoDaSecao)).toEqual(['visao', 'persona', 'contas', 'interacoes', 'habilidades']);
  });

  it('B3: com aprovação pendente a Atividade abre as Aprovações (o que o selo conta); as outras seções não mudam', () => {
    const atividade = SECOES.find((s) => s.id === 'atividade')!;
    expect(abaAoEscolherSecao(atividade, 2)).toBe('aprovacoes');
    expect(abaAoEscolherSecao(atividade, 0)).toBe('interacoes');
    expect(abaAoEscolherSecao(atividade, null)).toBe('interacoes');
    expect(SECOES.filter((s) => s.id !== 'atividade').map((s) => abaAoEscolherSecao(s, 3)))
      .toEqual(SECOES.filter((s) => s.id !== 'atividade').map(abaPadraoDaSecao));
  });

  it('o pedido de outra tela só vale se for uma guia desta tela', () => {
    expect(abaDoPedido('memoria')).toBe('memoria');
    expect(abaDoPedido('autenticacao')).toBe('visao');
    expect(abaDoPedido(undefined)).toBe('visao');
    expect(abaDoPedido('')).toBe('visao');
  });
});
