import type { AiStatus, Health, UsageGroup } from '../api/types';

/** Funções da IA (papéis) em pt-BR — usadas no chip da barra superior, na seção IA e nas tabelas de custo. */
export const AI_ROLE_LABEL: Record<UsageGroup['role'], string> = {
  plan: 'Planejar',
  decide: 'Decidir',
  verify: 'Verificar',
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
  ];
  return rows.filter((r) => typeof r.value === 'string' && r.value !== '');
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
