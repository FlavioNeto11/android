import type { Attempt, EventRecord, Evidence, Objective, RunDetail, RunSummary, Step } from '../../api/types';
import type { StackedSegment } from '../../components/ProgressBar';
import { isRecord } from '../../lib/format';

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

/** A etapa que o "Marcar como concluído" fecha: a primeira da versão atual parada (incerta, aguardando ou com falha)
 *  — a mesma consulta do servidor (`RunService.resolve`). */
export function etapaAConfirmar(detail: RunDetail, objective: Objective): Step | null {
  return currentSteps(detail, objective).find((s) => s.status === 'uncertain' || s.status === 'waiting_user' || s.status === 'failed') ?? null;
}

/**
 * O print que a confirmação manual cita (ADR-055): a captura de tela MAIS RECENTE da etapa a confirmar, com imagem.
 * Em 19/09 uma DM confirmada só com nota livre não dizia que tela a pessoa viu — e a da beatriz estava com "Sending…"
 * congelado. `null` quando a etapa não tem print; numa etapa com efeito externo o servidor então recusa a confirmação.
 */
export function printParaConfirmar(detail: RunDetail, objective: Objective): Evidence | null {
  const etapa = etapaAConfirmar(detail, objective);
  if (!etapa) return null;
  let ultimo: Evidence | null = null;
  for (const e of detail.evidence) {
    if (e.step_id === etapa.id && e.kind === 'screenshot' && e.url && (!ultimo || e.id > ultimo.id)) ultimo = e;
  }
  return ultimo;
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

/** Uma pergunta de uma execução em `needs_input`: o campo que falta, a pergunta e as opções (ids, quando há). */
export interface PerguntaDaExecucao {
  field: string;
  question: string;
  options: string[];
}

/**
 * As perguntas de uma execução que nasceu `needs_input` SEM plano: o roteamento por persona (ADR-044: a persona num
 * aparelho com duas, homônimos, texto × seleção) e a resolução de habilidade (v0.22) as mandam no evento `log`, em
 * `data.questions`, no mesmo formato (`field`, `question`, `options`). Vale a mais recente — a execução pode ter
 * perguntado mais de uma vez.
 */
export function perguntasDosEventos(events: readonly EventRecord[] | null): PerguntaDaExecucao[] {
  if (!events) return [];
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const d = events[i]?.data;
    if (!isRecord(d) || !Array.isArray(d.questions)) continue;
    const perguntas = d.questions.flatMap((q): PerguntaDaExecucao[] => (isRecord(q) && typeof q.question === 'string'
      ? [{ field: typeof q.field === 'string' ? q.field : '', question: q.question,
           options: Array.isArray(q.options) ? q.options.map((o) => String(o)) : [] }]
      : []));
    if (perguntas.length > 0) return perguntas;
  }
  return [];
}
