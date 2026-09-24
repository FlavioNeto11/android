import { describe, expect, it } from 'vitest';
import { buildDecisionTiles } from './tiles';

const BASE = {
  healthStatus: 'ok' as const,
  problemsCount: 0,
  spendTodayUsd: 1.2,
  dailyLimitUsd: 5,
  onlineDevices: 3,
  maxOnlineDevices: 10,
  estimatedMaxDevices: 10,
  cpuPercent: 37,
  memAvailableGb: 40.5,
  memTotalGb: 64,
  accelOk: true,
};

describe('buildDecisionTiles', () => {
  it('monta os cinco azulejos, na ordem Saúde · Custo · Aparelhos · Máquina · Aceleração', () => {
    const tiles = buildDecisionTiles(BASE);
    expect(tiles.map((t) => t.key)).toEqual(['health', 'cost', 'devices', 'machine', 'acceleration']);
    expect(tiles[0]).toMatchObject({ value: 'OK', sub: 'Nenhum problema', tone: 'success', anchor: 'diag-problemas' });
    expect(tiles[2]).toMatchObject({ value: '3 online', tone: 'info', anchor: 'diag-capacidade' });
    expect(tiles[4]).toMatchObject({ value: 'Disponível', tone: 'success', anchor: 'diag-aceleracao' });
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

  it('aparelhos ficam em aviso quando não há mais vaga', () => {
    expect(buildDecisionTiles({ ...BASE, onlineDevices: 10, maxOnlineDevices: 10 })[2]).toMatchObject({ tone: 'warning' });
  });

  it('máquina vira perigo com pouca RAM livre, mesmo com CPU tranquila', () => {
    expect(buildDecisionTiles({ ...BASE, memAvailableGb: 2, memTotalGb: 64, cpuPercent: 5 })[3]).toMatchObject({ tone: 'danger' });
  });

  it('aceleração indisponível vira perigo; sem informação vira neutro', () => {
    expect(buildDecisionTiles({ ...BASE, accelOk: false })[4]).toMatchObject({ value: 'Indisponível', tone: 'danger' });
    expect(buildDecisionTiles({ ...BASE, accelOk: null })[4]).toMatchObject({ value: 'Desconhecida', tone: 'muted' });
  });
});
