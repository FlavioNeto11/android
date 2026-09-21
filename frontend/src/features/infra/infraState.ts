import type { Instance } from '../../api/types';
import type { Tone } from '../../lib/status';

/**
 * Regras puras da visão de infraestrutura, fora do componente para serem testáveis em node — mesmo padrão de
 * `features/devices/deviceState.ts`.
 */

const ESTADO: Record<Instance['state'], { label: string; tone: Tone }> = {
  online: { label: 'online', tone: 'success' },
  booting: { label: 'iniciando', tone: 'info' },
  stopping: { label: 'parando', tone: 'info' },
  hibernated: { label: 'hibernado', tone: 'muted' },
  stopped: { label: 'parado', tone: 'neutral' },
  absent: { label: 'sem AVD', tone: 'neutral' },
  error: { label: 'com erro', tone: 'danger' },
};

export function instanceStateMeta(state: Instance['state']): { label: string; tone: Tone } {
  return ESTADO[state] ?? { label: state, tone: 'neutral' };
}

/** Aparelhos agrupados por servidor. `null` é o servidor central — ele também é um servidor. */
export function groupByWorker(instances: readonly Instance[]): Map<string | null, Instance[]> {
  const out = new Map<string | null, Instance[]>();
  for (const i of instances) {
    const k = i.worker_id ?? null;
    const lista = out.get(k);
    if (lista) lista.push(i);
    else out.set(k, [i]);
  }
  return out;
}

/**
 * Aparelhos que apontam para um servidor que não está inscrito. É um estado real e silencioso: o aparelho fica
 * sem ciclo de vida e nada explica por quê, então a tela precisa dizer em voz alta.
 */
export function orphanInstances(instances: readonly Instance[], workerIds: readonly string[]): Instance[] {
  const conhecidos = new Set(workerIds);
  return instances.filter((i) => i.worker_id && !conhecidos.has(i.worker_id));
}

/** A batida ficou velha o bastante para o que está na tela não valer mais? */
export const STALE_HEARTBEAT_MS = 35_000;

export function isStale(ageMs: number | null): boolean {
  return ageMs !== null && ageMs > STALE_HEARTBEAT_MS;
}
