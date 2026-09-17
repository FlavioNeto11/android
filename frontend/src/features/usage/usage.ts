import type { UsageGroup, UsageReport } from '../../api/types';
import { aiRoleLabel } from '../../lib/aiLabels';
import { formatDecimal, formatInt } from '../../lib/format';

/**
 * Regras puras do relatório de custo de IA (`GET /api/usage`): formatação em pt-BR e o tratamento de
 * modelos sem preço. Sem React — testável em node.
 */

/** Texto no lugar de US$ quando o backend não tem preço para o modelo (`usd: null`, ex.: "simulado"). */
export const NO_PRICE = 'sem preço';

const usdFormat = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 });

/** "US$ 0,0123" — o backend arredonda em 4 casas; valores de centavos de dólar precisam delas. `null` → "sem preço". */
export function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return NO_PRICE;
  return `US$ ${usdFormat.format(n)}`;
}

/**
 * - `priced`: todos os modelos têm preço;
 * - `partial`: há modelos sem preço MISTURADOS com modelos cobrados — o total cobre só os cobrados;
 * - `unpriced`: nenhum grupo tem preço (ex.: modo simulado) — mostrar "sem preço" em vez de "US$ 0,00".
 */
export type Pricing = 'priced' | 'partial' | 'unpriced';

export function pricingOf(report: Pick<UsageReport, 'groups' | 'unpriced_models'>): Pricing {
  const groups = report.groups ?? [];
  const hasUnpriced = (report.unpriced_models ?? []).length > 0 || groups.some((g) => g.usd === null);
  if (!hasUnpriced) return 'priced';
  return groups.some((g) => typeof g.usd === 'number') ? 'partial' : 'unpriced';
}

export interface UsageRow {
  key: string;
  role: string;
  roleLabel: string;
  model: string;
  /** `tier: 1` = chamada escalonada para o modelo mais forte. */
  escalated: boolean;
  calls: string;
  fresh: string;
  cacheRead: string;
  output: string;
  withImage: string;
  errors: number;
  usd: string;
  priced: boolean;
}

const ROLE_ORDER: Record<string, number> = { plan: 0, decide: 1, verify: 2 };

function roleRank(role: string): number {
  return ROLE_ORDER[role] ?? 99;
}

/** Linhas da tabela função × modelo, na ordem Planejar → Decidir → Verificar. */
export function usageRows(report: Pick<UsageReport, 'groups'>): UsageRow[] {
  const groups: UsageGroup[] = (report.groups ?? []).slice();
  groups.sort((a, b) => roleRank(a.role) - roleRank(b.role) || a.model.localeCompare(b.model) || a.tier - b.tier);
  return groups.map((g) => ({
    key: `${g.role}|${g.model}|${g.tier}`,
    role: g.role,
    roleLabel: aiRoleLabel(g.role),
    model: g.model,
    escalated: g.tier === 1,
    calls: formatInt(g.calls),
    fresh: formatInt(g.fresh),
    cacheRead: formatInt(g.cache_read),
    output: formatInt(g.output),
    withImage: formatInt(g.with_image),
    errors: typeof g.errors === 'number' ? g.errors : 0,
    usd: formatUsd(g.usd),
    priced: typeof g.usd === 'number',
  }));
}

export interface DrivenBySplit {
  /** Etapas 100% por receita (sem nenhuma chamada de modelo). */
  recipe: number;
  /** Etapas em que a receita começou e a IA assumiu (`recipe+ai`). */
  mixed: number;
  /** Etapas decididas só pela IA. */
  ai: number;
  total: number;
  /** % de etapas 100% por receita; `null` sem etapas concluídas. */
  recipePercent: number | null;
}

/** `steps_driven_by` → contagens. Só as três chaves do contrato entram; `recipe+ai` NÃO conta como "por receita". */
export function drivenBySplit(stepsDrivenBy: Record<string, number> | null | undefined): DrivenBySplit {
  const pick = (k: string): number => {
    const v = stepsDrivenBy?.[k];
    return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0;
  };
  const recipe = pick('recipe');
  const mixed = pick('recipe+ai');
  const ai = pick('ai');
  const total = recipe + mixed + ai;
  return { recipe, mixed, ai, total, recipePercent: total > 0 ? Math.round((recipe / total) * 100) : null };
}

/** "4 por receita × 2 por IA" (+ "· 1 receita + IA" quando houver). */
export function drivenByText(split: DrivenBySplit): string {
  if (split.total === 0) return '—';
  const base = `${formatInt(split.recipe)} por receita × ${formatInt(split.ai)} por IA`;
  return split.mixed > 0 ? `${base} · ${formatInt(split.mixed)} receita + IA` : base;
}

export function recipeShareText(split: DrivenBySplit): string {
  if (split.recipePercent === null) return '—';
  return `${split.recipePercent}% (${formatInt(split.recipe)} de ${formatInt(split.total)})`;
}

export interface UsageTotals {
  pricing: Pricing;
  /** Total em US$; "sem preço" quando nada tem preço. Em `partial`, cobre só os modelos cobrados. */
  totalUsd: string;
  usdPerObjective: string;
  callsPerObjective: string;
  objectivesWithAi: string;
  calls: string;
  drivenBy: string;
  recipeShare: string;
  /** Modelos sem preço, para o aviso. */
  unpricedModels: string[];
}

export function usageTotals(report: UsageReport): UsageTotals {
  const pricing = pricingOf(report);
  const money = (n: number): string => (pricing === 'unpriced' ? NO_PRICE : formatUsd(n));
  const split = drivenBySplit(report.steps_driven_by);
  const fromGroups = (report.groups ?? []).filter((g) => g.usd === null).map((g) => g.model);
  const unpricedModels = Array.from(new Set([...(report.unpriced_models ?? []), ...fromGroups])).sort();
  return {
    pricing,
    totalUsd: money(report.total_usd),
    usdPerObjective: report.objectives_with_ai > 0 ? money(report.usd_per_objective) : '—',
    callsPerObjective: report.objectives_with_ai > 0 ? formatDecimal(report.calls_per_objective) : '—',
    objectivesWithAi: formatInt(report.objectives_with_ai),
    calls: formatInt((report.groups ?? []).reduce((sum, g) => sum + (g.calls ?? 0), 0)),
    drivenBy: drivenByText(split),
    recipeShare: recipeShareText(split),
    unpricedModels,
  };
}

/** Nada para mostrar: nenhuma chamada de IA e nenhuma etapa concluída no período. */
export function isUsageEmpty(report: UsageReport): boolean {
  return (report.groups ?? []).length === 0 && drivenBySplit(report.steps_driven_by).total === 0;
}
