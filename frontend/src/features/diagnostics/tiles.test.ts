import { describe, expect, it } from 'vitest';
import type { AiBalance } from '../../api/types';
import { balanceTile, buildDecisionTiles } from './tiles';

const BASE = {
  healthStatus: 'ok' as const,
  problemsCount: 0,
  spendTodayUsd: 1.2,
  dailyLimitUsd: 5,
  onlineDevices: 3,
  totalDevices: 10,
  unknownDevices: 0,
  serversOverCapacity: 0,
  estimatedMaxDevices: 10,
  cpuPercent: 37,
  memAvailableGb: 40.5,
  memTotalGb: 64,
  accelOk: true,
};

describe('buildDecisionTiles', () => {
  it('monta os seis azulejos, na ordem Saúde · Custo · Saldo · Aparelhos · Máquina · Aceleração', () => {
    const tiles = buildDecisionTiles(BASE);
    expect(tiles.map((t) => t.key)).toEqual(['health', 'cost', 'balance', 'devices', 'machine', 'acceleration']);
    expect(tiles[0]).toMatchObject({ value: 'OK', sub: 'Nenhum problema', tone: 'success', anchor: 'diag-problemas' });
    expect(tiles[2]).toMatchObject({ value: '—', sub: 'nenhuma conta paga em uso', tone: 'muted' });
    expect(tiles[3]).toMatchObject({ value: '3 de 10 online', tone: 'info', anchor: 'diag-capacidade' });
    expect(tiles[5]).toMatchObject({ value: 'Disponível', tone: 'success', anchor: 'diag-aceleracao' });
  });

  it('saldo de IA mostra a conta em uso mais urgente e as outras no subtítulo (ADR-051)', () => {
    const conta = (account: 'anthropic' | 'openai' | 'gemini', saldo: number, state: AiBalance['state'], in_use = true): AiBalance => ({
      account, label: account, console: '', currency: account === 'gemini' ? 'BRL' : 'USD', units_per_usd: 1,
      warn_below: 2, block_below: null, key_configured: true, roles: ['decide'], image: false,
      in_use, anchor_balance: saldo, anchor_at: null, anchor_source: 'console', anchor_note: null, spent_since_usd: 0,
      estimated_balance: saldo, estimated_balance_usd: saldo, age_h: 1, admin_key_configured: true, provider_usd: null,
      external_usd: 0, reconciled_at: null, reconcile_error: null, state, stale: false, message: '',
    });
    const t = balanceTile([conta('openai', 8.25, 'ok'), conta('anthropic', 1.5, 'low'), conta('gemini', 29.37, 'ok', false)]);
    expect(t).toMatchObject({ key: 'balance', value: 'US$ 1,50', tone: 'warning', anchor: 'diag-custo' });
    expect(t.sub).toBe('Anthropic · Saldo baixo · OpenAI US$ 8,25');
    expect(balanceTile([conta('openai', 0, 'exhausted')]).tone).toBe('danger');
  });

  it('saúde degradada com problemas vira aviso, com a contagem no subtítulo', () => {
    const tiles = buildDecisionTiles({ ...BASE, healthStatus: 'degraded', problemsCount: 2 });
    expect(tiles[0]).toMatchObject({ value: 'Degradado', sub: '2 problemas', tone: 'warning' });
  });

  it('custo de IA vira perigo ao estourar o teto diário e aviso perto dele', () => {
    expect(buildDecisionTiles({ ...BASE, spendTodayUsd: 6, dailyLimitUsd: 5 })[1]).toMatchObject({ tone: 'danger' });
    expect(buildDecisionTiles({ ...BASE, spendTodayUsd: 4.5, dailyLimitUsd: 5 })[1]).toMatchObject({ tone: 'warning' });
    expect(buildDecisionTiles({ ...BASE, spendTodayUsd: 1, dailyLimitUsd: 5 })[1]).toMatchObject({ tone: 'success' });
    expect(buildDecisionTiles({ ...BASE, spendTodayUsd: 1, dailyLimitUsd: 0 })[1]).toMatchObject({ tone: 'muted', sub: 'sem teto diário configurado' });
  });

  it('aparelhos: mesma conta do cabeçalho, e aviso quando um servidor passa das vagas ou há desconhecidos', () => {
    expect(buildDecisionTiles(BASE)[3]).toMatchObject({ value: '3 de 10 online', tone: 'info' });
    expect(buildDecisionTiles({ ...BASE, serversOverCapacity: 1 })[3])
      .toMatchObject({ tone: 'warning', sub: expect.stringContaining('1 servidor acima das vagas') });
    expect(buildDecisionTiles({ ...BASE, unknownDevices: 6 })[3])
      .toMatchObject({ tone: 'warning', sub: expect.stringContaining('6 em estado desconhecido') });
  });

  it('máquina vira perigo com pouca RAM livre, mesmo com CPU tranquila', () => {
    expect(buildDecisionTiles({ ...BASE, memAvailableGb: 2, memTotalGb: 64, cpuPercent: 5 })[4]).toMatchObject({ tone: 'danger' });
  });

  it('aceleração indisponível vira perigo; sem informação vira neutro', () => {
    expect(buildDecisionTiles({ ...BASE, accelOk: false })[5]).toMatchObject({ value: 'Indisponível', tone: 'danger' });
    expect(buildDecisionTiles({ ...BASE, accelOk: null })[5]).toMatchObject({ value: 'Desconhecida', tone: 'muted' });
  });
});
