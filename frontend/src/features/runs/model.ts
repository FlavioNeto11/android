import type { Attempt, Objective, RunDetail, RunSummary, Step } from '../../api/types';
import type { StackedSegment } from '../../components/ProgressBar';

/** Etapas da versão ATUAL do plano do objetivo (o RunDetail traz etapas de todas as versões). */
export function currentSteps(detail: RunDetail, objective: Objective): Step[] {
  return detail.steps
    .filter((s) => s.objective_id === objective.id && s.plan_version === objective.plan_version)
    .sort((a, b) => a.seq - b.seq);
}

/** Etapas de versões anteriores do plano, agrupadas por versão (mais recente primeiro). */
export function previousVersionSteps(detail: RunDetail, objective: Objective): { version: number; steps: Step[] }[] {
  const byVersion = new Map<number, Step[]>();
  for (const s of detail.steps) {
    if (s.objective_id !== objective.id || s.plan_version === objective.plan_version) continue;
    const list = byVersion.get(s.plan_version) ?? [];
    list.push(s);
    byVersion.set(s.plan_version, list);
  }
  return Array.from(byVersion.entries())
    .sort((a, b) => b[0] - a[0])
    .map(([version, steps]) => ({ version, steps: steps.sort((a, b) => a.seq - b.seq) }));
}

export function attemptsByStep(detail: RunDetail): Map<string, Attempt[]> {
  const map = new Map<string, Attempt[]>();
  for (const a of detail.attempts) {
    const list = map.get(a.step_id) ?? [];
    list.push(a);
    map.set(a.step_id, list);
  }
  for (const list of map.values()) list.sort((a, b) => a.number - b.number);
  return map;
}

const ACTIVE_STEP = new Set(['running', 'verifying', 'retry_wait', 'waiting_user', 'uncertain']);

/** A etapa que melhor representa "onde o objetivo está agora". */
export function headlineStep(steps: readonly Step[]): Step | null {
  const active = steps.find((s) => ACTIVE_STEP.has(s.status));
  if (active) return active;
  const failed = steps.find((s) => s.status === 'failed');
  if (failed) return failed;
  const next = steps.find((s) => s.status === 'ready' || s.status === 'pending');
  if (next) return next;
  return steps.length > 0 ? (steps[steps.length - 1] ?? null) : null;
}

export function isBlocked(o: Pick<Objective, 'status'>): boolean {
  return o.status === 'waiting_user' || o.status === 'uncertain';
}

export function objectivesTotal(counts: RunSummary['counts']): number {
  return counts.succeeded + counts.failed + counts.waiting_user + counts.uncertain + counts.cancelled + counts.running + counts.pending;
}

export function countSegments(counts: RunSummary['counts']): StackedSegment[] {
  return [
    { key: 'succeeded', label: 'Sucesso', value: counts.succeeded, tone: 'success' },
    { key: 'running', label: 'Em andamento', value: counts.running, tone: 'accent' },
    { key: 'waiting_user', label: 'Bloqueio', value: counts.waiting_user, tone: 'warning', hatch: true },
    { key: 'uncertain', label: 'Incerto', value: counts.uncertain, tone: 'warning' },
    { key: 'failed', label: 'Falha', value: counts.failed, tone: 'danger', hatch: true },
    { key: 'cancelled', label: 'Cancelado', value: counts.cancelled, tone: 'muted' },
    { key: 'pending', label: 'Pendente', value: counts.pending, tone: 'neutral' },
  ];
}

export const EMPTY_COUNTS: RunSummary['counts'] = {
  succeeded: 0, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0,
};
