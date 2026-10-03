import type { AiStatus, Health, UsageGroup } from '../api/types';

/** Funções da IA (papéis) em pt-BR — usadas no chip da barra superior, na seção IA e nas tabelas de custo. */
export const AI_ROLE_LABEL: Record<UsageGroup['role'], string> = {
  plan: 'Planejar',
  decide: 'Decidir',
  verify: 'Verificar',
  social: 'Responder',
  persona: 'Gerar persona',
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
    { key: 'persona', label: AI_ROLE_LABEL.persona, value: m.persona ?? '' },
  ];
  return rows.filter((r) => typeof r.value === 'string' && r.value !== '');
}

/** Rótulo de cada função do hub, incluindo as que não aparecem no relatório de custo. */
const HUB_ROLE_LABEL: Record<string, string> = {
  ...AI_ROLE_LABEL,
  escalation: 'Escalonamento',
  leitura: 'Ler a tela (leitura visual)',
};

const EFFORT_LABEL: Record<string, string> = {
  minimal: 'mínimo', low: 'baixo', medium: 'médio', high: 'alto', xhigh: 'muito alto', max: 'máximo',
};

/** Esforço de raciocínio (`low`, `high`…) em pt-BR; o valor cru se vier algo fora do contrato. */
export function effortLabel(effort: string | null | undefined): string {
  return lookup(EFFORT_LABEL, effort);
}

const THINKING_LABEL: Record<string, string> = {
  adaptive: 'adaptativo',
  desligado_na_funcao: 'desligado nesta função',
  nao_declarado: 'o modelo não declara',
  recusado_pelo_modelo: 'recusado pelo modelo (desligado até reiniciar)',
};

/** A sonda "o ator pensa?" (v0.84). `—` quando o provedor não tem raciocínio estendido (OpenAI, simulado). */
export function thinkingLabel(thinking: string | null | undefined): string {
  return lookup(THINKING_LABEL, thinking);
}

const ESQUEMA_LABEL: Record<string, string> = {
  longo: 'longo (cada etapa com descrição, pré-condição e tentativas)',
  curto: 'curto (o backend preenche descrição, pré-condição e tentativas)',
};

/** `ai.esquema_do_plano` (v0.87). */
export function esquemaDoPlanoLabel(esquema: string | null | undefined): string {
  return lookup(ESQUEMA_LABEL, esquema);
}

/** "ligada · gemini-3.1-flash-lite (gemini)" a partir da função `leitura` e do `leitura_visual` (v0.87). `null` quando o
 * backend não informa. */
export function leituraVisualLabel(ai: Pick<AiStatus, 'leitura_visual' | 'roles'>): string | null {
  if (ai.leitura_visual === undefined || ai.leitura_visual === null) return null;
  const leitor = (ai.roles ?? []).find((r) => r.role === 'leitura');
  if (!ai.leitura_visual) return 'desligada';
  return leitor ? `ligada · ${leitor.model} (${leitor.provider})` : 'ligada, sem leitor configurado (recusa toda leitura)';
}

const ORIGEM_DA_DECISAO_FECHADA: Record<string, string> = {
  curador: 'curador', intencao: 'intenção', desempate: 'desempate', apps: 'apps',
};
const MODO_DA_DECISAO_FECHADA: Record<string, string> = { shadow: 'em sombra', on: 'ligado' };

/** "curador: em sombra · intenção: em sombra" a partir de `decisao_fechada.consumers` (v0.90). `null` sem o bloco ou
 * sem consumidor ligado. O aviso do backend (`notice`) traz a mesma lista com o modo do YAML. */
export function decisaoFechadaConsumidoresLabel(ai: Pick<AiStatus, 'decisao_fechada'>): string | null {
  const consumidores = Object.entries(ai.decisao_fechada?.consumers ?? {});
  if (consumidores.length === 0) return null;
  return consumidores
    .map(([origem, modo]) => `${lookup(ORIGEM_DA_DECISAO_FECHADA, origem)}: ${lookup(MODO_DA_DECISAO_FECHADA, modo)}`)
    .join(' · ');
}

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
  /** Esforço e raciocínio em pt-BR (17.14); `—` quando não se aplica. */
  effort: string;
  thinking: string;
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
      effort: effortLabel(r.effort),
      thinking: thinkingLabel(r.thinking),
      refusalFallback: r.refusal_fallback,
      warnings,
    };
  });
}

export interface AiProfileRow {
  name: string;
  note: string;
  /** "Planejar: claude-sonnet-5-5 (anthropic, esforço baixo)", uma por função que o perfil muda. */
  changes: string[];
  /** "imagem até 768 px", "árvore rica a partir de 12 elementos". */
  adjustments: string[];
  /** "25 % das execuções sem perfil"; `null` quando não é o canário. */
  canary: string | null;
  external: boolean;
}

/** Os perfis de IA (v0.87) como a aba IA os mostra. Vazio em backend anterior ou sem `ai.profiles`. */
export function aiProfileRows(ai: Pick<AiStatus, 'profiles'>): AiProfileRow[] {
  return (ai.profiles ?? []).map((p) => {
    const adjustments: string[] = [];
    if (p.screenshot_max_side) adjustments.push(`imagem até ${p.screenshot_max_side} px`);
    if (p.rich_tree_min_elements !== null && p.rich_tree_min_elements !== undefined) {
      adjustments.push(`árvore rica a partir de ${p.rich_tree_min_elements} elementos`);
    }
    return {
      name: p.name,
      note: p.note,
      changes: p.roles.map((r) => `${hubRoleLabel(r.role)}: ${r.model} (${r.provider}${r.effort ? `, esforço ${effortLabel(r.effort)}` : ''})`),
      adjustments,
      canary: p.canary_fraction !== null && p.canary_fraction !== undefined
        ? `${Math.round(p.canary_fraction * 100)} % das execuções sem perfil`
        : null,
      external: p.roles.some((r) => r.sends_data_externally),
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
