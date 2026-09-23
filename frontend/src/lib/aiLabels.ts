import type { AiStatus, Health, UsageGroup } from '../api/types';

/** Funções da IA (papéis) em pt-BR — usadas no chip da barra superior, na seção IA e nas tabelas de custo. */
export const AI_ROLE_LABEL: Record<UsageGroup['role'], string> = {
  plan: 'Planejar',
  decide: 'Decidir',
  verify: 'Verificar',
  social: 'Responder',
};

export function aiRoleLabel(role: string): string {
  return Object.prototype.hasOwnProperty.call(AI_ROLE_LABEL, role) ? AI_ROLE_LABEL[role as UsageGroup['role']] : role;
}

const RECIPES_LABEL: Record<NonNullable<AiStatus['recipes']>, string> = {
  off: 'Desligadas',
  shadow: 'Modo sombra (só compara com a IA)',
  replay: 'Reprodução (sem custo de modelo)',
};

const IMAGE_POLICY_LABEL: Record<NonNullable<AiStatus['image_policy']>, string> = {
  always: 'Sempre envia a tela',
  auto: 'Automático (só quando precisa)',
  never: 'Nunca envia a tela',
};

function lookup(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return '—';
  return Object.prototype.hasOwnProperty.call(map, value) ? (map[value] as string) : value;
}

export function recipesModeLabel(mode: string | null | undefined): string {
  return lookup(RECIPES_LABEL, mode);
}

export function imagePolicyLabel(policy: string | null | undefined): string {
  return lookup(IMAGE_POLICY_LABEL, policy);
}

export function flowsLabel(flows: boolean | null | undefined): string {
  return flows === true ? 'Ligados' : flows === false ? 'Desligados' : '—';
}

export interface LabeledValue {
  key: string;
  label: string;
  value: string;
}

/** Modelo por função (`AiStatus.models`). Vazio quando o backend não informa — aí vale só `AiStatus.model`. */
export function aiModelRows(ai: Pick<AiStatus, 'models'>): LabeledValue[] {
  const m = ai.models;
  if (!m) return [];
  const rows: LabeledValue[] = [
    { key: 'plan', label: AI_ROLE_LABEL.plan, value: m.plan },
    { key: 'decide', label: AI_ROLE_LABEL.decide, value: m.decide },
    { key: 'verify', label: AI_ROLE_LABEL.verify, value: m.verify },
    { key: 'escalation', label: 'Escalonamento', value: m.escalation },
    { key: 'social', label: AI_ROLE_LABEL.social, value: m.social ?? '' },
  ];
  return rows.filter((r) => typeof r.value === 'string' && r.value !== '');
}

/** Rótulo de cada função do hub, incluindo as que não aparecem no relatório de custo. */
const HUB_ROLE_LABEL: Record<string, string> = { ...AI_ROLE_LABEL, escalation: 'Escalonamento' };

export function hubRoleLabel(role: string): string {
  return Object.prototype.hasOwnProperty.call(HUB_ROLE_LABEL, role) ? (HUB_ROLE_LABEL[role] as string) : role;
}

export interface AiRoleRow {
  key: string;
  label: string;
  provider: string;
  model: string;
  endpoint: string;
  external: boolean;
  /** Para onde a falha DESTA função cai. `null` = não cai: o erro sobe, sem provedor pago silencioso. */
  fallback: string | null;
  refusalFallback: boolean;
  /** O que o operador precisa ver sem abrir o YAML: capacidade declarada e preço ausente. */
  warnings: string[];
}

/**
 * Uma linha por função do hub de IA (item 7.1). Vazio em backend anterior ao hub — e aí a tela mostra só o
 * bloco antigo de "modelo por função", como sempre mostrou.
 */
export function aiRoleRows(ai: Pick<AiStatus, 'roles'>): AiRoleRow[] {
  return (ai.roles ?? []).map((r) => {
    const warnings: string[] = [];
    if (!r.configured) warnings.push('sem credencial/endpoint');
    if (!r.priced) warnings.push('modelo sem preço cadastrado');
    if (!r.vision) warnings.push('modelo sem visão');
    if (!r.tools) warnings.push('modelo sem ferramentas');
    return {
      key: r.role,
      label: hubRoleLabel(r.role),
      provider: r.provider,
      model: r.model,
      endpoint: r.endpoint,
      external: r.sends_data_externally,
      fallback: r.fallback_provider ?? null,
      refusalFallback: r.refusal_fallback,
      warnings,
    };
  });
}

/** "US$ 8,88 de US$ 25,00" — ou só o gasto, quando não há teto. `null` quando o backend não informa. */
export function spendLabel(spent: number | null | undefined, limit: number | null | undefined): string | null {
  if (spent === null || spent === undefined) return null;
  const fmt = (v: number) => `US$ ${v.toFixed(2)}`;
  return limit && limit > 0 ? `${fmt(spent)} de ${fmt(limit)} (${Math.round((spent / limit) * 100)}% do teto)` : fmt(spent);
}

/**
 * Estado de receitas / fluxos / política de imagem. Vale o que o `AiStatus` disser; quando ele não informa
 * (ex.: modo simulado devolve `null`), cai para `health.features`, que traz a mesma configuração do backend.
 * Vazio quando nenhum dos dois informa (backend pré-v0.2).
 */
export function aiFeatureRows(
  ai: Pick<AiStatus, 'recipes' | 'flows' | 'image_policy'>,
  features?: Pick<Health['features'], 'recipes' | 'flows' | 'image_policy'> | null,
): LabeledValue[] {
  const recipes = ai.recipes ?? features?.recipes ?? null;
  const flows = ai.flows ?? features?.flows ?? null;
  const imagePolicy = ai.image_policy ?? features?.image_policy ?? null;
  if (recipes === null && flows === null && imagePolicy === null) return [];
  return [
    { key: 'recipes', label: 'Receitas', value: recipesModeLabel(recipes) },
    { key: 'flows', label: 'Fluxos', value: flowsLabel(flows) },
    { key: 'image_policy', label: 'Imagens da tela', value: imagePolicyLabel(imagePolicy) },
  ];
}
