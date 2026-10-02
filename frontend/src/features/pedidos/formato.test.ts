import { describe, expect, it } from 'vitest';
import { makePersona } from '../../test/fixtures';
import { agendaLegivel, comNomesDePersonas, dataCompacta, dataCurta, fusoDoNavegador, fusoParaMostrar, horaEscrita, nomeDaPersonaNoPedido, rotuloDoAlvo } from './formato';
import { formatUsd } from './modelo';

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

describe('o alvo da prévia pelo nome (I2)', () => {
  const pessoas = [makePersona('ig-z7SD', 'Bruno Ferreira', { username: 'bruno' }), makePersona('ig-2', 'Ana Lima')];
  const apps = [{ id: 'outlook', name: 'Outlook' }];

  it('persona com @ vira "Nome (@conta)"; sem @ fica só o nome; desconhecida (ou lista ainda não lida) cai no id', () => {
    expect(nomeDaPersonaNoPedido('ig-z7SD', pessoas)).toBe('Bruno Ferreira (@bruno)');
    expect(nomeDaPersonaNoPedido('ig-2', pessoas)).toBe('Ana Lima');
    expect(nomeDaPersonaNoPedido('ig-x', pessoas)).toBe('ig-x');
    expect(nomeDaPersonaNoPedido('ig-z7SD', null)).toBe('ig-z7SD');
  });

  it('rotuloDoAlvo: aparelho, persona e app pelo nome; sem persona ou app, só o que existe', () => {
    expect(rotuloDoAlvo({ instance_id: 'android-03', profile_id: 'ig-z7SD', app_id: 'outlook' }, pessoas, apps))
      .toBe('android-03 · Bruno Ferreira (@bruno) · Outlook');
    expect(rotuloDoAlvo({ instance_id: 'android-03', profile_id: null, app_id: null }, pessoas, apps)).toBe('android-03');
    expect(rotuloDoAlvo({ instance_id: 'android-03', profile_id: null, app_id: 'x', app_ids: ['outlook', 'x'] }, pessoas, apps))
      .toBe('android-03 · Outlook, x');
  });

  it('comNomesDePersonas troca o id citado num aviso do backend pelo nome', () => {
    expect(comNomesDePersonas('mais de um aparelho (ig-2)', ['ig-2'], pessoas)).toBe('mais de um aparelho (Ana Lima)');
  });
});
