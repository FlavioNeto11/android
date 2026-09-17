import { describe, expect, it } from 'vitest';
import { ageMs, computeServerOffset, formatAgo, formatAgoCoarse, formatDuration, formatSpan } from './time';

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

  it('formata "há X"', () => {
    expect(formatAgo('2026-09-17T12:00:05.000Z', now)).toBe('há 5 s');
    expect(formatAgoCoarse('2026-09-17T12:00:09.700Z', now)).toBe('agora');
    expect(formatAgoCoarse('2026-09-17T11:58:10.000Z', now)).toBe('há 2 min');
    expect(formatAgo(null, now)).toBe('—');
  });

  it('formata duração com fim aberto', () => {
    expect(formatDuration('2026-09-17T12:00:00.000Z', null, now)).toBe('10 s');
    expect(formatDuration('2026-09-17T12:00:00.000Z', '2026-09-17T12:00:03.000Z', now)).toBe('3 s');
    expect(formatDuration(null, null, now)).toBe('—');
  });
});
