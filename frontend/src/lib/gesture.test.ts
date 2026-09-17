import { describe, expect, it } from 'vitest';
import { isDrag, resolveGesture } from './gesture';

const FRAME = { width: 1080, height: 2400 };
const BOX = { width: 1000, height: 1200 }; // imagem ocupa x ∈ [230, 770] (escala 0,5)

describe('resolveGesture', () => {
  it('clique rápido = tap em pixels do aparelho', () => {
    expect(resolveGesture({ x: 500, y: 600, t: 0 }, { x: 502, y: 601, t: 120 }, false, BOX, FRAME)).toEqual({ type: 'tap', x: 540, y: 1200 });
  });

  it('segurar ≥ 600 ms sem mover = long_press com a duração real', () => {
    expect(resolveGesture({ x: 500, y: 600, t: 0 }, { x: 500, y: 600, t: 599 }, false, BOX, FRAME)?.type).toBe('tap');
    expect(resolveGesture({ x: 500, y: 600, t: 0 }, { x: 503, y: 600, t: 850 }, false, BOX, FRAME)).toEqual({
      type: 'long_press', x: 540, y: 1200, duration_ms: 850,
    });
  });

  it('arrastar = swipe com duration_ms', () => {
    expect(resolveGesture({ x: 500, y: 1000, t: 0 }, { x: 500, y: 400, t: 300 }, true, BOX, FRAME)).toEqual({
      type: 'swipe', x: 540, y: 2000, x2: 540, y2: 800, duration_ms: 300,
    });
  });

  it('arrasto muito rápido ganha duração mínima e muito lento é limitado', () => {
    const fast = resolveGesture({ x: 500, y: 1000, t: 0 }, { x: 500, y: 400, t: 10 }, true, BOX, FRAME);
    expect(fast).toMatchObject({ type: 'swipe', duration_ms: 80 });
    const slow = resolveGesture({ x: 500, y: 1000, t: 0 }, { x: 500, y: 400, t: 60_000 }, true, BOX, FRAME);
    expect(slow).toMatchObject({ type: 'swipe', duration_ms: 5000 });
  });

  it('rejeita gestos que começam na margem', () => {
    expect(resolveGesture({ x: 100, y: 600, t: 0 }, { x: 100, y: 600, t: 50 }, false, BOX, FRAME)).toBeNull();
    expect(resolveGesture({ x: 100, y: 600, t: 0 }, { x: 600, y: 600, t: 200 }, true, BOX, FRAME)).toBeNull();
  });

  it('fim do arrasto na margem é trazido para a borda do aparelho', () => {
    expect(resolveGesture({ x: 500, y: 600, t: 0 }, { x: 950, y: 600, t: 200 }, true, BOX, FRAME)).toEqual({
      type: 'swipe', x: 540, y: 1200, x2: 1079, y2: 1200, duration_ms: 200,
    });
  });

  it('considera arrasto quando o ponteiro passou do limiar, mesmo voltando perto da origem', () => {
    const g = resolveGesture({ x: 500, y: 600, t: 0 }, { x: 504, y: 600, t: 700 }, true, BOX, FRAME);
    expect(g?.type).toBe('swipe');
  });
});

describe('isDrag', () => {
  it('usa limiar de 10 px', () => {
    expect(isDrag({ x: 0, y: 0, t: 0 }, { x: 6, y: 8, t: 0 })).toBe(false); // distância 10
    expect(isDrag({ x: 0, y: 0, t: 0 }, { x: 7, y: 8, t: 0 })).toBe(true);
  });
});
