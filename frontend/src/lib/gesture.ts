import { distance, mapPointToDevice, type DevicePoint, type Size } from './coords';

export const LONG_PRESS_MS = 600;
export const DRAG_THRESHOLD_PX = 10;
const MIN_SWIPE_MS = 80;
const MAX_GESTURE_MS = 5000;

export interface PointerSample {
  /** CSS px relativos à caixa da tela */
  x: number;
  y: number;
  /** ms (performance.now ou Date.now — só a diferença importa) */
  t: number;
}

export type Gesture =
  | { type: 'tap'; x: number; y: number }
  | { type: 'long_press'; x: number; y: number; duration_ms: number }
  | { type: 'swipe'; x: number; y: number; x2: number; y2: number; duration_ms: number };

/** O ponteiro já se afastou o bastante do ponto inicial para ser um arrasto? */
export function isDrag(start: PointerSample, current: PointerSample): boolean {
  return distance(start.x, start.y, current.x, current.y) > DRAG_THRESHOLD_PX;
}

/**
 * Converte um gesto do ponteiro em entrada para o aparelho:
 *   clique = tap · segurar ≥ 600 ms sem mover = long_press · arrastar = swipe (com duration_ms).
 * O INÍCIO precisa cair dentro da imagem (margens são rejeitadas → null); o FIM de um arrasto que
 * escapou para a margem é trazido para a borda da tela do aparelho.
 * `dragged` indica se em algum momento o ponteiro passou do limiar (mesmo que tenha voltado).
 */
export function resolveGesture(start: PointerSample, end: PointerSample, dragged: boolean, box: Size, frame: Size): Gesture | null {
  const p1: DevicePoint | null = mapPointToDevice(start.x, start.y, box, frame);
  if (!p1) return null;
  const elapsed = Math.max(0, Math.round(end.t - start.t));

  if (dragged || isDrag(start, end)) {
    const p2 = mapPointToDevice(end.x, end.y, box, frame, { clampOutside: true });
    if (!p2) return null;
    if (p2.x === p1.x && p2.y === p1.y) return { type: 'tap', x: p1.x, y: p1.y };
    return { type: 'swipe', x: p1.x, y: p1.y, x2: p2.x, y2: p2.y, duration_ms: Math.min(MAX_GESTURE_MS, Math.max(MIN_SWIPE_MS, elapsed)) };
  }
  if (elapsed >= LONG_PRESS_MS) {
    return { type: 'long_press', x: p1.x, y: p1.y, duration_ms: Math.min(MAX_GESTURE_MS, elapsed) };
  }
  return { type: 'tap', x: p1.x, y: p1.y };
}
