import { CircleCheck, History, ShieldAlert } from 'lucide-react';
import type { Recipe } from '../../api/types';
import { formatInt } from '../../lib/format';
import type { StatusMeta } from '../../lib/status';

/** Regras puras da seção "Fluxos e receitas" (sem React): testáveis em node. */

export const LEARN_ONCE_NOTE =
  'A IA aprende o caminho uma vez; as próximas execuções repetem por seletores, sem custo de modelo. Se a tela mudar, a IA assume só aquela etapa.';

export interface TemplatePart {
  text: string;
  placeholder: boolean;
}

/** Quebra o comando-modelo em texto e marcadores `{assim}` para destacar os marcadores na tela. */
export function splitTemplate(template: string): TemplatePart[] {
  const parts: TemplatePart[] = [];
  const re = /\{[^{}\s][^{}]*\}/g;
  let last = 0;
  for (let m = re.exec(template); m !== null; m = re.exec(template)) {
    if (m.index > last) parts.push({ text: template.slice(last, m.index), placeholder: false });
    parts.push({ text: m[0], placeholder: true });
    last = m.index + m[0].length;
  }
  if (last < template.length) parts.push({ text: template.slice(last), placeholder: false });
  return parts;
}

/**
 * O contrato tem três situações; o pedido de tela fala em "Ativa / Quarentena". `superseded` (substituída por
 * uma versão mais nova) só o backend atribui e não pode ser revertida pelo `PUT`.
 */
export const RECIPE_STATUS: Record<Recipe['status'], StatusMeta> = {
  active: { label: 'Ativa', tone: 'success', icon: CircleCheck, description: 'Usada nas próximas execuções desta etapa.' },
  quarantined: { label: 'Quarentena', tone: 'warning', icon: ShieldAlert, description: 'Fora de uso: falhou ao reproduzir ou foi pausada por você. A IA conduz a etapa.' },
  superseded: { label: 'Substituída', tone: 'muted', icon: History, description: 'Há uma versão mais nova desta receita.' },
};

/** Para onde o botão leva: Ativa → quarentena; Quarentena → ativa; Substituída → sem ação. */
export function recipeToggleTarget(status: Recipe['status']): 'active' | 'quarantined' | null {
  if (status === 'active') return 'quarantined';
  if (status === 'quarantined') return 'active';
  return null;
}

/** Concordância em modo sombra: "12/15 (80%)"; `null` quando ainda não houve comparação. */
export function shadowText(r: Pick<Recipe, 'shadow_agree' | 'shadow_total'>): string | null {
  if (!(r.shadow_total > 0)) return null;
  const pct = Math.round((r.shadow_agree / r.shadow_total) * 100);
  return `${formatInt(r.shadow_agree)}/${formatInt(r.shadow_total)} (${pct}%)`;
}

type RecipeAction = Recipe['actions'][number];
type RecipeSelector = NonNullable<RecipeAction['selectors']>[number];

/** Seletor legível: `resource-id=…`, `texto="…"`, `descrição="…"` (na ordem em que a receita tenta). */
export function selectorText(sel: RecipeSelector): string {
  const bits: string[] = [];
  if (sel.rid) bits.push(`resource-id=${sel.rid}`);
  if (sel.text) bits.push(`texto="${sel.text}"`);
  if (sel.desc) bits.push(`descrição="${sel.desc}"`);
  const body = bits.join(' · ');
  return body ? `${sel.kind}: ${body}` : sel.kind;
}

export function scrollText(scroll: RecipeAction['scroll']): string | null {
  if (!scroll) return null;
  const dir: Record<string, string> = { up: 'para cima', down: 'para baixo', left: 'para a esquerda', right: 'para a direita' };
  return `rola ${dir[scroll.direction] ?? scroll.direction} até ${formatInt(scroll.max)}× procurando o elemento`;
}
