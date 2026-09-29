import { CircleCheck, History, Hourglass, ShieldAlert, Stamp } from 'lucide-react';
import type { Recipe } from '../../api/types';
import { formatInt } from '../../lib/format';
import { OWNER_QUEUE, type StatusMeta } from '../../lib/status';

// O estado do fluxo e a fila do dono moram no mapa central (Aplicativos e a guia Habilidades também os mostram).
export { FLOW_STATUS, OWNER_QUEUE } from '../../lib/status';

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
 * O pedido de tela fala em "Ativa / Quarentena". `candidate` (aprendida, ainda em prova), `validated` (provou-se, mas
 * tem ação de efeito externo: espera o dono — D1, ADR-054) e `superseded` (substituída por uma versão mais nova) só o
 * backend atribui; a substituída não pode ser revertida pelo `PUT`.
 */
export const RECIPE_STATUS: Record<Recipe['status'], StatusMeta> = {
  candidate: {
    label: 'Candidata', tone: 'info', icon: Hourglass,
    description: 'Aprendida e ainda em prova: a IA conduz a etapa e a receita só é comparada com o que a IA fez. Vira ativa depois de execuções seguidas em que a IA fizer exatamente o caminho dela; uma divergência recomeça a contagem.',
  },
  validated: {
    label: 'Esperando o dono', tone: 'warning', icon: Stamp,
    description: `Concordou com a IA nas execuções seguidas, mas tem ação de efeito externo (envia, publica, segue): o sistema não a publica sozinho. Enquanto você não aprovar em ${OWNER_QUEUE}, a IA segue conduzindo a etapa.`,
  },
  active: { label: 'Ativa', tone: 'success', icon: CircleCheck, description: 'Usada nas próximas execuções desta etapa.' },
  quarantined: { label: 'Quarentena', tone: 'warning', icon: ShieldAlert, description: 'Fora de uso: falhou ao reproduzir ou foi pausada por você. A IA conduz a etapa.' },
  superseded: { label: 'Substituída', tone: 'muted', icon: History, description: 'Há uma versão mais nova desta receita.' },
};

/**
 * Para onde o botão leva: Ativa, Candidata ou Esperando o dono → quarentena; Quarentena → ativa; Substituída → sem
 * ação. A candidata não ganha "ativar" aqui: ela vira ativa pela prova (concordância em sombra), não por um clique; a
 * que espera o dono é aprovada no livro de aprendizado, onde a decisão fica na trilha.
 */
export function recipeToggleTarget(status: Recipe['status']): 'active' | 'quarantined' | null {
  if (status === 'active' || status === 'candidate' || status === 'validated') return 'quarantined';
  if (status === 'quarantined') return 'active';
  return null;
}

/**
 * Concordância em modo sombra, por execução da etapa: "12/15 (80%)"; `null` quando ainda não houve comparação. Na
 * candidata a divergência zera os dois números (a prova recomeça), então ali o primeiro é a sequência atual.
 */
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
