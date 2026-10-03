import type { AiBalance, AiBalanceState } from '../api/types';
import { aiRoleLabel } from './aiLabels';

/** Saldo das contas de IA (ADR-051): textos e tons do painel. O saldo é ESTIMADO e o texto diz isso. */

export type BalanceTone = 'success' | 'warning' | 'danger' | 'neutral';

const STATE_LABEL: Record<AiBalanceState, string> = {
  unknown: 'Sem leitura',
  ok: 'OK',
  low: 'Saldo baixo',
  blocked: 'Bloqueada',
  exhausted: 'Sem crédito',
};

export function balanceStateLabel(state: string): string {
  return Object.prototype.hasOwnProperty.call(STATE_LABEL, state) ? STATE_LABEL[state as AiBalanceState] : state;
}

export function balanceTone(b: Pick<AiBalance, 'state' | 'stale'>): BalanceTone {
  if (b.state === 'blocked' || b.state === 'exhausted') return 'danger';
  if (b.state === 'low' || b.state === 'unknown' || b.stale) return 'warning';
  return 'success';
}

export function money(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return '—';
  const symbol = currency === 'BRL' ? 'R$' : currency === 'USD' ? 'US$' : currency;
  const txt = Math.abs(value).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${value < 0 ? '−' : ''}${symbol} ${txt}`;
}

/** Consumo em US$: abaixo de um centavo mostra até 4 casas, porque o Jev custa frações de centavo por chamada e o consumo
 *  dele virava "US$ 0,00" no cartão. Do centavo para cima, igual a `money`. */
export function consumptionUsd(value: number): string {
  if (value > 0 && value < 0.0001) return 'menos de US$ 0,0001';
  if (value > 0 && value < 0.01) {
    return `US$ ${value.toLocaleString('pt-BR', { minimumFractionDigits: 3, maximumFractionDigits: 4 })}`;
  }
  return money(value, 'USD');
}

/** Saldo sempre em US$ (a moeda do cabeçalho): a conta em outra moeda entra convertida pelo câmbio que o backend já
 *  informa. `null` quando não há leitura. */
export function balanceUsd(b: AiBalance): number | null {
  if (b.estimated_balance === null) return null;
  return emUsd(b);
}

/** "US$ 5,65" para o chip do cabeçalho; "?" sem leitura. */
export function balanceUsdLabel(b: AiBalance): string {
  const v = balanceUsd(b);
  return v === null || !Number.isFinite(v) ? '?' : money(v, 'USD');
}

/** Nome curto para o chip do cabeçalho. */
export function balanceShortName(account: string): string {
  return account === 'anthropic' ? 'Anthropic' : account === 'openai' ? 'OpenAI' : account === 'gemini' ? 'Gemini'
    : account === 'typesafe' ? 'TypeSafe' : account;
}

/** O que esta conta paga hoje, em pt-BR: "Decidir, Verificar e imagem", "decisão fechada (sombra)". */
export function balanceUsage(b: Pick<AiBalance, 'roles' | 'image' | 'closed_decision'>): string {
  // `escalation` não está no rótulo das tabelas de custo (lá ele entra como tier): traduzido aqui.
  const rotulo = (r: string) => (r === 'escalation' ? 'Escalonamento' : aiRoleLabel(r));
  const fechada = b.closed_decision === 'shadow' ? ['decisão fechada (sombra)']
    : b.closed_decision === 'on' ? ['decisão fechada'] : [];
  const partes = [...b.roles.map(rotulo), ...(b.image ? ['imagem da persona'] : []), ...fechada];
  if (partes.length === 0) return 'Nenhuma função usa esta conta';
  if (partes.length === 1) return partes[0] as string;
  return `${partes.slice(0, -1).join(', ')} e ${partes[partes.length - 1]}`;
}

export function balanceAge(ageH: number | null | undefined): string {
  if (ageH === null || ageH === undefined) return '—';
  if (ageH < 1) return 'há menos de 1 h';
  if (ageH < 48) return `há ${Math.round(ageH)} h`;
  return `há ${Math.round(ageH / 24)} dias`;
}

const SOURCE_LABEL: Record<string, string> = {
  manual: 'digitado no painel',
  console: 'lido no console',
  recarga: 'recarga registrada',
  fechamento: 'fechamento diário',
  provider_error: 'erro de cobrança do provedor',
};

export function balanceSourceLabel(source: string | null | undefined): string {
  if (!source) return '—';
  return SOURCE_LABEL[source] ?? source;
}

/** Contas que merecem chip no cabeçalho: em uso, ou com alerta (sem leitura não entra se ninguém a usa). */
export function headerBalances(list: AiBalance[] | undefined | null): AiBalance[] {
  return (list ?? []).filter((b) => b.in_use || b.state === 'blocked' || b.state === 'exhausted');
}

function emUsd(b: AiBalance): number {
  if (b.estimated_balance === null) return Infinity;
  return b.currency === 'USD' ? b.estimated_balance : (b.estimated_balance_usd ?? b.estimated_balance / (b.units_per_usd || 1));
}

const SEVERIDADE: Record<BalanceTone, number> = { danger: 3, warning: 2, neutral: 1, success: 0 };

/** A conta que paga esta função de IA hoje (pela lista `roles` de cada conta), se houver. */
export function balanceOfRole(list: AiBalance[] | undefined | null, role: string): AiBalance | null {
  return (list ?? []).find((b) => b.roles.includes(role)) ?? null;
}

/** "Anthropic · US$ 5,17" — o resumo de uma linha usado ao lado do modelo. */
export function balanceBrief(b: AiBalance): string {
  return `${balanceShortName(b.account)} · ${b.estimated_balance === null ? 'sem leitura' : money(b.estimated_balance, b.currency)}`;
}

/** Contas em uso, da mais urgente para a menos (tom, depois o menor saldo em US$). */
export function balancesByUrgency(list: AiBalance[] | undefined | null): AiBalance[] {
  return (list ?? []).filter((b) => b.in_use).sort((a, b) =>
    (SEVERIDADE[balanceTone(b)] - SEVERIDADE[balanceTone(a)])
    || (emUsd(a) - emUsd(b)));
}

/** Contas em uso que pedem ação agora: saldo baixo, sem leitura, bloqueada ou sem crédito. */
export function balanceAlerts(list: AiBalance[] | undefined | null): AiBalance[] {
  return balancesByUrgency(list).filter((b) => b.state !== 'ok');
}
