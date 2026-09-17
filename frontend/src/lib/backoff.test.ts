import { describe, expect, it } from 'vitest';
import { backoffDelay } from './backoff';

describe('backoffDelay', () => {
  const mid = () => 0.5; // sem jitter efetivo

  it('cresce exponencialmente de 1 s até o teto de 15 s', () => {
    expect([1, 2, 3, 4, 5, 6, 12].map((a) => backoffDelay(a, { random: mid }))).toEqual([
      1000, 2000, 4000, 8000, 15000, 15000, 15000,
    ]);
  });

  it('aplica jitter dentro de ±30% e respeita o teto', () => {
    expect(backoffDelay(2, { random: () => 0 })).toBe(1400);
    expect(backoffDelay(2, { random: () => 1 })).toBe(2600);
    expect(backoffDelay(10, { random: () => 1 })).toBe(15000);
    expect(backoffDelay(1, { random: () => 0 })).toBe(700);
  });

  it('trata tentativas inválidas como a primeira', () => {
    expect(backoffDelay(0, { random: mid })).toBe(1000);
    expect(backoffDelay(-3, { random: mid })).toBe(1000);
  });
});
