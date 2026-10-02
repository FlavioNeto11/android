import { describe, expect, it } from 'vitest';
import { agendaLegivel, dataCompacta, dataCurta, fusoDoNavegador, fusoParaMostrar, horaEscrita } from './formato';
import { ApiError } from '../../api/client';
import { formatUsd, mensagemDoErro } from './modelo';

describe('formato da tela Pedidos', () => {
  it('dataCurta: dia da semana, dia/mês e hora, na hora do fuso do pedido', () => {
    // 22:00Z em 02/10/2026 (sexta) são 19:00 em São Paulo (UTC-3).
    expect(dataCurta('2026-10-02T22:00:00Z', 'America/Sao_Paulo')).toBe('sex, 02/10 às 19:00');
    expect(dataCompacta('2026-10-02T22:00:00Z', 'America/Sao_Paulo')).toBe('sex 02/10 19:00');
  });

  it('dataCurta: aceita o ISO com deslocamento e não mostra segundos nem fuso', () => {
    const s = dataCurta('2026-10-02T19:00:00-03:00', 'America/Sao_Paulo');
    expect(s).toBe('sex, 02/10 às 19:00');
    expect(s).not.toMatch(/T|:00:00|America/);
  });

  it('dataCurta: fuso desconhecido não quebra e instante inválido vira travessão', () => {
    expect(dataCurta('2026-10-02T22:00:00Z', 'Lugar/Nenhum')).toMatch(/^\w{3}, \d{2}\/\d{2} às \d{2}:\d{2}$/);
    expect(dataCurta(null)).toBe('—');
    expect(dataCurta('lixo')).toBe('—');
  });

  it('fusoParaMostrar: só o fuso diferente do navegador', () => {
    expect(fusoParaMostrar(fusoDoNavegador() || 'UTC')).toBeNull();
    expect(fusoParaMostrar('Pacific/Auckland')).toBe(fusoDoNavegador() === 'Pacific/Auckland' ? null : 'Pacific/Auckland');
    expect(fusoParaMostrar('')).toBeNull();
  });

  it('horaEscrita: a hora que está escrita no ISO local', () => {
    expect(horaEscrita('2026-10-02T19:00:00-03:00')).toBe('19:00');
    expect(horaEscrita('2026-10-02T07:05:00')).toBe('07:05');
    expect(horaEscrita(null)).toBeNull();
  });

  it('agendaLegivel: tira o fuso e põe a hora nas repetições por dia, semana e mês', () => {
    expect(agendaLegivel('Todo dia (America/Sao_Paulo)', 'America/Sao_Paulo', '19:00')).toBe('Todo dia às 19:00');
    expect(agendaLegivel('Toda semana (seg, qua) (America/Sao_Paulo)', 'America/Sao_Paulo', '08:30')).toBe('Toda semana (seg, qua) às 08:30');
    expect(agendaLegivel('A cada 2 dias (America/Sao_Paulo)', 'America/Sao_Paulo', '19:00')).toBe('A cada 2 dias às 19:00');
    // a hora que o backend já escreveu não se repete; de hora em hora e "uma vez" não ganham hora
    expect(agendaLegivel('Todo dia às 08:00 (America/Sao_Paulo)', 'America/Sao_Paulo', '19:00')).toBe('Todo dia às 08:00');
    expect(agendaLegivel('A cada 3 horas (America/Sao_Paulo)', 'America/Sao_Paulo', '19:00')).toBe('A cada 3 horas');
    expect(agendaLegivel('Uma vez, agora', 'America/Sao_Paulo', '19:00')).toBe('Uma vez, agora');
  });

  it('formatUsd: formato brasileiro, como o resto do painel', () => {
    expect(formatUsd(0)).toBe('US$ 0,00');
    expect(formatUsd(1.5)).toBe('US$ 1,50');
    expect(formatUsd(null)).toBe('—');
  });
});

describe('erros dos gatilhos do 28.8', () => {
  it('explica o código e mantém o campo que o backend apontou', () => {
    const e = new ApiError(422, 'gatilho_invalido', '`kinds` precisa ser uma lista de 1 a 10 tipos de evento');
    expect(mensagemDoErro(e)).toBe('Os dados do gatilho não são aceitos. `kinds` precisa ser uma lista de 1 a 10 tipos de evento');
    expect(mensagemDoErro(new ApiError(422, 'condicao_sem_observacao', 'x'))).toContain('outro gatilho que observe');
    expect(mensagemDoErro(new ApiError(422, 'gatilho_nao_suportado', 'x'))).not.toContain('28.8');
  });
});
