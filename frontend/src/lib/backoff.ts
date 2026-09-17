export interface BackoffOptions {
  baseMs?: number;
  maxMs?: number;
  /** Fração de variação aleatória (0.3 = ±30%). */
  jitter?: number;
  random?: () => number;
}

/**
 * Atraso exponencial com jitter: 1 s → 2 s → 4 s → 8 s → 15 s (teto), cada um com ±30%.
 * `attempt` começa em 1. O resultado nunca passa de `maxMs` nem fica abaixo de metade de `baseMs`.
 */
export function backoffDelay(attempt: number, opts: BackoffOptions = {}): number {
  const base = opts.baseMs ?? 1000;
  const max = opts.maxMs ?? 15_000;
  const jitter = opts.jitter ?? 0.3;
  const rnd = opts.random ?? Math.random;
  const n = Math.max(1, Math.floor(attempt));
  const raw = Math.min(max, base * 2 ** Math.min(n - 1, 20));
  const factor = 1 + (rnd() * 2 - 1) * jitter;
  return Math.round(Math.min(max, Math.max(base / 2, raw * factor)));
}
