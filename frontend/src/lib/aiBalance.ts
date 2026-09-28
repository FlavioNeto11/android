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

/** Nome curto para o chip do cabeçalho. */
export function balanceShortName(account: string): string {
  return account === 'anthropic' ? 'Anthropic' : account === 'openai' ? 'OpenAI' : account === 'gemini' ? 'Gemini' : account;
}

/** O que esta conta paga hoje, em pt-BR: "Decidir, Verificar e imagem". */
export function balanceUsage(b: Pick<AiBalance, 'roles' | 'image'>): string {
  // `escalation` não está no rótulo das tabelas de custo (lá ele entra como tier): traduzido aqui.
  const rotulo = (r: string) => (r === 'escalation' ? 'Escalonamento' : aiRoleLabel(r));
  const partes = [...b.roles.map(rotulo), ...(b.image ? ['imagem da persona'] : [])];
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
