import { describe, expect, it } from 'vitest';
import { ageMs, computeServerOffset, duracaoHumana, formatDuration, formatSpan, tempoRelativo } from './time';

describe('computeServerOffset', () => {
  it('calcula servidor − local', () => {
    const local = Date.parse('2026-09-17T12:00:00.000Z');
    expect(computeServerOffset('2026-09-17T12:00:05.000Z', local)).toBe(5000);
    expect(computeServerOffset('2026-09-17T11:59:58.000Z', local)).toBe(-2000);
  });

  it('devolve null para data inválida', () => {
    expect(computeServerOffset('não é data', 0)).toBeNull();
  });
});

describe('formatSpan', () => {
  it('formata faixas', () => {
    expect(formatSpan(0)).toBe('0 s');
    expect(formatSpan(850)).toBe('850 ms');
    expect(formatSpan(12_000)).toBe('12 s');
    expect(formatSpan(185_000)).toBe('3 min 05 s');
    expect(formatSpan(180_000)).toBe('3 min');
    expect(formatSpan(7_800_000)).toBe('2 h 10 min');
    expect(formatSpan(4 * 24 * 3600_000)).toBe('4 d');
    expect(formatSpan(-1)).toBe('—');
  });
});

describe('idades', () => {
  const now = Date.parse('2026-09-17T12:00:10.000Z');

  it('nunca é negativa mesmo com relógio adiantado', () => {
    expect(ageMs('2026-09-17T12:00:20.000Z', now)).toBe(0);
  });

  it('formata "há X" (formatador único, RF-06: sem "1 min 14 s")', () => {
    expect(tempoRelativo('2026-09-17T12:00:05.000Z', now)).toBe('há 5 s');
    expect(tempoRelativo('2026-09-17T12:00:09.700Z', now)).toBe('agora');
    expect(tempoRelativo('2026-09-17T11:58:10.000Z', now)).toBe('há 2 min');
    expect(tempoRelativo('2026-09-17T11:58:56.000Z', now)).toBe('há 1 min');
    expect(tempoRelativo(null, now)).toBe('—');
  });

  it('formata duração com fim aberto', () => {
    expect(formatDuration('2026-09-17T12:00:00.000Z', null, now)).toBe('10 s');
    expect(formatDuration('2026-09-17T12:00:00.000Z', '2026-09-17T12:00:03.000Z', now)).toBe('3 s');
    expect(formatDuration(null, null, now)).toBe('—');
  });
});

const AGORA = Date.parse('2026-09-30T20:00:00Z');
const ha = (ms: number) => new Date(AGORA - ms).toISOString();
const MIN = 60_000;
const H = 60 * MIN;
const D = 24 * H;

describe('tempoRelativo — ordem de grandeza, sem precisão de máquina', () => {
  it('161 h vira "há 6 dias"', () => {
    expect(tempoRelativo(ha(161 * H), AGORA)).toBe('há 6 dias');
  });
  it('1 min 14 s vira "há 1 min"', () => {
    expect(tempoRelativo(ha(74_000), AGORA)).toBe('há 1 min');
  });
  it('escalas', () => {
    expect(tempoRelativo(ha(1000), AGORA)).toBe('agora');
    expect(tempoRelativo(ha(12_000), AGORA)).toBe('há 12 s');
    expect(tempoRelativo(ha(59 * MIN + 59_000), AGORA)).toBe('há 59 min');
    expect(tempoRelativo(ha(3 * H + 40 * MIN), AGORA)).toBe('há 3 h');
    expect(tempoRelativo(ha(24 * H), AGORA)).toBe('há 1 dia');
    expect(tempoRelativo(ha(45 * D), AGORA)).toBe('há 1 mês');
    expect(tempoRelativo(ha(75 * D), AGORA)).toBe('há 2 meses');
    expect(tempoRelativo(ha(800 * D), AGORA)).toBe('há 2 anos');
  });
  it('relógio adiantado nunca vira tempo negativo, e dado ruim vira travessão', () => {
    expect(tempoRelativo(new Date(AGORA + 60_000).toISOString(), AGORA)).toBe('agora');
    expect(tempoRelativo(null, AGORA)).toBe('—');
    expect(tempoRelativo('lixo', AGORA)).toBe('—');
    expect(duracaoHumana(-1)).toBe('—');
  });
});
