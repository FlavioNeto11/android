import type { AiBalance } from '../../api/types';
import { balanceShortName, balanceStateLabel, balanceTone, balancesByUrgency, money } from '../../lib/aiBalance';
import type { Tone } from '../../lib/status';
import { formatGb, formatPercent, ratio } from '../../lib/format';
import { formatUsd as formatUsdRaw } from '../usage/usage';

/**
 * Item 11.7 — painel de decisão: cinco azulejos com semáforo, no topo da tela de diagnóstico, para responder
 * de relance "está tudo bem?" sem descer 17 mil pixels de JSON. Puro (sem React) para caber em teste de unidade.
 */

export type DecisionTileKey = 'health' | 'cost' | 'balance' | 'devices' | 'machine' | 'acceleration';

export interface DecisionTile {
  key: DecisionTileKey;
  title: string;
  value: string;
  sub: string;
  tone: Tone;
  /** id do elemento para o qual o clique no azulejo rola a tela. */
  anchor: string;
}

function formatUsd(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? formatUsdRaw(n) : '—';
}

export function healthTile(problemsCount: number, status: 'ok' | 'degraded' | 'error' | null | undefined): DecisionTile {
  const tone: Tone = status === 'ok' ? 'success' : status === 'degraded' ? 'warning' : status === 'error' ? 'danger' : 'muted';
  const value = status === 'ok' ? 'OK' : status === 'degraded' ? 'Degradado' : status === 'error' ? 'Com erro' : 'Desconhecida';
  const sub = problemsCount === 0 ? 'Nenhum problema' : `${problemsCount} problema${problemsCount === 1 ? '' : 's'}`;
  return { key: 'health', title: 'Saúde', value, sub, tone, anchor: 'diag-problemas' };
}

export function costTile(spendTodayUsd: number | null | undefined, dailyLimitUsd: number | null | undefined): DecisionTile {
  const hasLimit = typeof dailyLimitUsd === 'number' && dailyLimitUsd > 0;
  const spend = typeof spendTodayUsd === 'number' ? spendTodayUsd : null;
  const frac = hasLimit && spend !== null ? spend / dailyLimitUsd! : 0;
  const tone: Tone = !hasLimit ? 'muted' : frac >= 1 ? 'danger' : frac >= 0.8 ? 'warning' : 'success';
  const value = spend === null ? '—' : formatUsd(spend);
  const sub = hasLimit ? `de ${formatUsd(dailyLimitUsd)} hoje (teto)` : 'sem teto diário configurado';
  return { key: 'cost', title: 'Custo de IA hoje', value, sub, tone, anchor: 'diag-custo' };
}

/** Saldo das contas de IA (ADR-051): mostra a conta EM USO mais urgente; as outras vão no subtítulo. */
export function balanceTile(balances: AiBalance[] | null | undefined): DecisionTile {
  const lista = balancesByUrgency(balances);
  const pior = lista[0];
  if (!pior) {
    return { key: 'balance', title: 'Saldo de IA', value: '—', sub: 'nenhuma conta paga em uso', tone: 'muted', anchor: 'diag-custo' };
  }
  const tom = balanceTone(pior);
  const tone: Tone = tom === 'neutral' ? 'muted' : tom;
  const valor = pior.estimated_balance === null ? 'sem leitura' : money(pior.estimated_balance, pior.currency);
  const outras = lista.slice(1).map((b) => `${balanceShortName(b.account)} ${b.estimated_balance === null ? '?' : money(b.estimated_balance, b.currency)}`);
  const sub = [`${balanceShortName(pior.account)} · ${balanceStateLabel(pior.state)}`, ...outras].join(' · ');
  return { key: 'balance', title: 'Saldo de IA', value: valor, sub, tone, anchor: 'diag-custo' };
}

export function devicesTile(online: number, maxOnline: number | null | undefined, estimatedMax: number | null | undefined): DecisionTile {
  const hasSlots = typeof maxOnline === 'number' && maxOnline > 0;
  const tone: Tone = hasSlots && online >= maxOnline! ? 'warning' : 'info';
  const value = `${online} online`;
  const slotsTxt = hasSlots ? `${maxOnline} vaga${maxOnline === 1 ? '' : 's'}` : 'vagas não configuradas';
  const estTxt = typeof estimatedMax === 'number' ? ` · até ${estimatedMax} estimado(s)` : '';
  return { key: 'devices', title: 'Aparelhos', value, sub: `${slotsTxt}${estTxt}`, tone, anchor: 'diag-capacidade' };
}

export function machineTile(
  cpuPercent: number | null | undefined,
  memAvailableGb: number | null | undefined,
  memTotalGb: number | null | undefined,
  diskFreeGb: number | null | undefined = null,
  diskTotalGb: number | null | undefined = null,
): DecisionTile {
  const memUsedFrac = typeof memAvailableGb === 'number' && typeof memTotalGb === 'number' && memTotalGb > 0
    ? 1 - ratio(memAvailableGb, memTotalGb) : null;
  const diskUsedFrac = typeof diskFreeGb === 'number' && typeof diskTotalGb === 'number' && diskTotalGb > 0
    ? 1 - ratio(diskFreeGb, diskTotalGb) : null;
  const tone: Tone = memUsedFrac !== null && memUsedFrac >= 0.9 ? 'danger'
    : diskUsedFrac !== null && diskUsedFrac >= 0.9 ? 'danger'
    : memUsedFrac !== null && memUsedFrac >= 0.75 ? 'warning'
    : diskUsedFrac !== null && diskUsedFrac >= 0.85 ? 'warning'
    : (typeof cpuPercent === 'number' && cpuPercent >= 90) ? 'warning' : 'success';
  const value = typeof cpuPercent === 'number' ? `CPU ${formatPercent(cpuPercent)}` : '—';
  const ramTxt = typeof memAvailableGb === 'number' ? `RAM livre ${formatGb(memAvailableGb)}${typeof memTotalGb === 'number' ? ` de ${formatGb(memTotalGb)}` : ''}` : null;
  const diskTxt = typeof diskFreeGb === 'number' ? `disco livre ${formatGb(diskFreeGb)}${typeof diskTotalGb === 'number' ? ` de ${formatGb(diskTotalGb)}` : ''}` : null;
  const sub = [ramTxt, diskTxt].filter(Boolean).join(' · ') || 'sem medição de RAM/disco';
  return { key: 'machine', title: 'Máquina', value, sub, tone, anchor: 'diag-maquina' };
}

export function accelerationTile(accelOk: boolean | null): DecisionTile {
  const tone: Tone = accelOk === true ? 'success' : accelOk === false ? 'danger' : 'muted';
  const value = accelOk === true ? 'Disponível' : accelOk === false ? 'Indisponível' : 'Desconhecida';
  const sub = accelOk === false ? 'Emuladores lentos sem ela' : accelOk === true ? 'Virtualização de hardware ativa' : 'Sem informação';
  return { key: 'acceleration', title: 'Aceleração', value, sub, tone, anchor: 'diag-aceleracao' };
}

export interface DecisionTilesInput {
  healthStatus: 'ok' | 'degraded' | 'error' | null | undefined;
  problemsCount: number;
  spendTodayUsd: number | null | undefined;
  dailyLimitUsd: number | null | undefined;
  onlineDevices: number;
  maxOnlineDevices: number | null | undefined;
  estimatedMaxDevices: number | null | undefined;
  cpuPercent: number | null | undefined;
  memAvailableGb: number | null | undefined;
  memTotalGb: number | null | undefined;
  diskFreeGb?: number | null;
  diskTotalGb?: number | null;
  accelOk: boolean | null;
  balances?: AiBalance[] | null;
}

/** Ordem fixa: Saúde, Custo, Aparelhos, Máquina, Aceleração — do que mais importa pro que é mais um detalhe. */
export function buildDecisionTiles(input: DecisionTilesInput): DecisionTile[] {
  return [
    healthTile(input.problemsCount, input.healthStatus),
    costTile(input.spendTodayUsd, input.dailyLimitUsd),
    balanceTile(input.balances),
    devicesTile(input.onlineDevices, input.maxOnlineDevices, input.estimatedMaxDevices),
    machineTile(input.cpuPercent, input.memAvailableGb, input.memTotalGb, input.diskFreeGb, input.diskTotalGb),
    accelerationTile(input.accelOk),
  ];
}
