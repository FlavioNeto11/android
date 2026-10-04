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

/** Item 7.3 (achado #101): rótulo pt-BR de cada `error_kind` gravado em `ai_calls`. */
const ERROR_KIND_LABEL: Record<string, string> = {
  refusal: 'recusa do provedor',
  budget: 'orçamento esgotado',
  billing: 'sem crédito',
  not_configured: 'credencial ausente/inválida',
  invalid_output: 'saída inválida do modelo',
  step_deadline: 'prazo da etapa esgotado',
  balance: 'saldo da conta abaixo do bloqueio',
  error: 'erro',
};

export function errorKindLabel(kind: string): string {
  return ERROR_KIND_LABEL[kind] ?? kind;
}

/** "3 recusa do provedor · 1 orçamento esgotado", maior contagem primeiro; `null` sem nenhum erro no período. */
export function errorsByKindText(report: Pick<UsageReport, 'errors_by_kind'>): string | null {
  const entries = Object.entries(report.errors_by_kind ?? {}).filter(([, n]) => typeof n === 'number' && n > 0);
  if (entries.length === 0) return null;
  entries.sort(([, a], [, b]) => b - a);
  return entries.map(([kind, n]) => `${formatInt(n)} ${errorKindLabel(kind)}`).join(' · ');
}

const ORIGIN_LABEL: Record<string, string> = {
  execucao: 'execução',
  curador: 'curador',
  leitura: 'leitura visual',
  social: 'responder',
  persona: 'persona',
  decisao_fechada: 'decisão fechada (Jev)',
  sem_origem: 'sem origem registrada',
};

export function originLabel(origin: string): string {
  return ORIGIN_LABEL[origin] ?? origin;
}

/** "execução US$ 1,29 · curador US$ 0,31 · …", maior custo primeiro (RA-10); `null` sem o grupo ou sem chamadas. */
export function byOriginText(report: Pick<UsageReport, 'by_origin'>): string | null {
  const entries = Object.entries(report.by_origin ?? {}).filter(([, g]) => g && g.calls > 0);
  if (entries.length === 0) return null;
  entries.sort(([, a], [, b]) => b.usd - a.usd || b.calls - a.calls);
  return entries.map(([origem, g]) => `${originLabel(origem)} ${formatUsd(g.usd)}`).join(' · ');
}

// ---- RA-10, a tela do 31.16: modelo forte, rejulgamento, cascata, imagem e etapas sem condutor (adendo v0.75) ----

const ESCALATION_LABEL: Record<string, string> = {
  efeito: 'efeito externo',
  nova_tentativa: 'nova tentativa',
  erros_seguidos: 'erros seguidos',
  piso: 'alvo inexistente',
  bloqueio: 'bloqueio relatado',
  ciclo: 'ação repetida',
  nivel: 'conferir o "não"',
  sim_com_efeito: 'conferir o "sim" com efeito',
};

/** Rótulo pt-BR de cada `MotivoDeEscalonamento` do backend; o próprio motivo quando é novo. */
export function escalationLabel(motivo: string): string {
  return ESCALATION_LABEL[motivo] ?? motivo;
}

export interface EscalationRow {
  key: string;
  label: string;
  calls: string;
  usd: string;
}

/** Uma linha por motivo de subir ao modelo forte, maior custo primeiro, sem os zerados; vazio sem escalonamento. */
export function escalationRows(report: Pick<UsageReport, 'escalations'>): EscalationRow[] {
  const entries = Object.entries(report.escalations ?? {}).filter(([, g]) => g && g.calls > 0);
  entries.sort(([, a], [, b]) => b.usd - a.usd || b.calls - a.calls);
  return entries.map(([motivo, g]) => ({ key: motivo, label: escalationLabel(motivo), calls: `${formatInt(g.calls)}×`, usd: formatUsd(g.usd) }));
}

/** "8 %" (inteiro) a partir da fração do backend; `null` sem julgamento. */
function percentText(rate: number | null | undefined): string | null {
  return typeof rate === 'number' && Number.isFinite(rate) ? `${Math.round(rate * 100)} %` : null;
}

/** "12 julgadas, 8 % de discordância (1) · US$ 0,04 em 13 chamadas"; `null` sem rejulgamento no período. */
export function rejudgeText(report: Pick<UsageReport, 'rejudges'>): string | null {
  const r = report.rejudges;
  if (!r || (r.calls <= 0 && r.judged <= 0)) return null;
  const pct = percentText(r.disagreement_rate);
  const juizo = r.judged > 0
    ? `${formatInt(r.judged)} julgada(s), ${pct ?? '—'} de discordância (${formatInt(r.disagreements)})`
    : 'nenhum veredito válido';
  return `${juizo} · ${formatUsd(r.usd)} em ${formatInt(r.calls)} chamada(s)`;
}

export interface RejudgeAppRow {
  key: string;
  appId: string;
  judged: string;
  disagreements: string;
  rate: string;
}

/** Linhas da discordância por app, mais julgadas primeiro. `appId` "*" é a etapa sem app. */
export function rejudgeByAppRows(report: Pick<UsageReport, 'rejudges'>): RejudgeAppRow[] {
  const entries = Object.entries(report.rejudges?.by_app ?? {}).filter(([, a]) => a && a.judged > 0);
  entries.sort(([ka, a], [kb, b]) => b.judged - a.judged || ka.localeCompare(kb));
  return entries.map(([appId, a]) => ({
    key: appId,
    appId,
    judged: formatInt(a.judged),
    disagreements: formatInt(a.disagreements),
    rate: percentText(a.disagreement_rate) ?? '—',
  }));
}

/** "5 subidas, 3 desbloquearam a tela · US$ 0,12"; `null` sem cascata no período. */
export function cascadeText(report: Pick<UsageReport, 'cascades'>): string | null {
  const c = report.cascades;
  if (!c || c.calls <= 0) return null;
  return `${formatInt(c.calls)} subida(s), ${formatInt(c.unblocked)} desbloquearam a tela · ${formatUsd(c.usd)}`;
}

/** Os motivos de `MotivoDaImagem` em que a imagem NÃO vai (o backend: sensivel, politica_nunca, arvore_rica). */
const IMAGE_REASON_WITHOUT = new Set(['sensivel', 'politica_nunca', 'arvore_rica']);

const IMAGE_REASON_LABEL: Record<string, string> = {
  sensivel: 'tela sensível',
  politica_nunca: 'política: nunca',
  arvore_rica: 'árvore da tela bastou',
  politica_sempre: 'política: sempre',
  pedida: 'o modelo pediu',
  problema: 'a etapa teve problema',
  primeira_julgada: 'primeira tela julgada',
  primeira_da_leitura: 'primeira tela da leitura',
  arvore_pobre: 'árvore da tela pobre',
};

export function imageReasonLabel(motivo: string): string {
  return IMAGE_REASON_LABEL[motivo] ?? motivo;
}

export interface ImageReasonRow {
  key: string;
  label: string;
  /** O motivo manda a imagem (`true`), manda NÃO mandar (`false`) ou é novo (`null`). */
  sends: boolean | null;
  calls: string;
  withImage: string;
}

/** Linhas de "por que a imagem foi junto": primeiro os que mandam a imagem, depois os que não; mais chamadas antes. */
export function imageReasonRows(report: Pick<UsageReport, 'image_reasons'>): ImageReasonRow[] {
  const entries = Object.entries(report.image_reasons ?? {}).filter(([, g]) => g && g.calls > 0);
  const sends = (motivo: string): boolean | null =>
    IMAGE_REASON_WITHOUT.has(motivo) ? false : motivo in IMAGE_REASON_LABEL ? true : null;
  const rank = (motivo: string): number => {
    const s = sends(motivo);
    return s === true ? 0 : s === false ? 1 : 2;
  };
  entries.sort(([ka, a], [kb, b]) => rank(ka) - rank(kb) || b.calls - a.calls || ka.localeCompare(kb));
  return entries.map(([motivo, g]) => ({
    key: motivo,
    label: imageReasonLabel(motivo),
    sends: sends(motivo),
    calls: formatInt(g.calls),
    withImage: formatInt(g.with_image),
  }));
}

/** Etapas com decisão de IA e sem `driven_by` (o aceite do RA-10 é zero); 0 quando o servidor não manda. */
export function stepsDrivenByNull(report: Pick<UsageReport, 'steps_driven_by_null'>): number {
  const n = report.steps_driven_by_null;
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : 0;
}
