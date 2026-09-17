import { describe, expect, it } from 'vitest';
import type { UsageGroup, UsageReport } from '../../api/types';
import { NO_PRICE, drivenBySplit, drivenByText, formatUsd, isUsageEmpty, pricingOf, recipeShareText, usageRows, usageTotals } from './usage';

function group(over: Partial<UsageGroup> = {}): UsageGroup {
  return {
    role: 'decide', model: 'claude-haiku-4-5', tier: 0, calls: 12, fresh: 34_567, cache_read: 120_000, cache_write: 9_000,
    output: 2_345, with_image: 3, errors: 0, avg_ms: 1840, usd: 0.0456, ...over,
  };
}

function report(over: Partial<UsageReport> = {}): UsageReport {
  return {
    scope: { run_id: 'run-0001', days: null },
    groups: [
      group({ role: 'verify', model: 'claude-haiku-4-5', calls: 6, fresh: 8_000, cache_read: 40_000, output: 600, with_image: 6, usd: 0.0123 }),
      group(),
      group({ role: 'decide', model: 'claude-sonnet-4-5', tier: 1, calls: 2, fresh: 5_000, cache_read: 0, output: 900, with_image: 2, errors: 1, usd: 0.0285 }),
      group({ role: 'plan', model: 'claude-sonnet-4-5', calls: 1, fresh: 4_200, cache_read: 0, output: 1_100, with_image: 0, usd: 1.5 }),
    ],
    total_usd: 1.5864, objectives_with_ai: 4, calls_per_objective: 5.3, usd_per_objective: 0.3966,
    steps_driven_by: { recipe: 9, 'recipe+ai': 1, ai: 2 }, unpriced_models: [],
    ...over,
  };
}

const SIMULATED = report({
  groups: [
    group({ role: 'plan', model: 'simulado', calls: 1, usd: null }),
    group({ role: 'decide', model: 'simulado', calls: 8, usd: null }),
  ],
  total_usd: 0, usd_per_objective: 0, calls_per_objective: 4.5, objectives_with_ai: 2, unpriced_models: ['simulado'],
});

describe('formatUsd', () => {
  it('usa vírgula decimal, 2 casas no mínimo e até 4 para frações de centavo', () => {
    expect(formatUsd(0)).toBe('US$ 0,00');
    expect(formatUsd(1.5)).toBe('US$ 1,50');
    expect(formatUsd(0.0123)).toBe('US$ 0,0123');
    expect(formatUsd(0.3966)).toBe('US$ 0,3966');
    expect(formatUsd(1234.5)).toBe('US$ 1.234,50');
    expect(formatUsd(0.00004)).toBe('US$ 0,00');
  });

  it('modelo sem preço (usd null) vira "sem preço", nunca "US$ 0,00"', () => {
    expect(formatUsd(null)).toBe('sem preço');
    expect(formatUsd(undefined)).toBe(NO_PRICE);
    expect(formatUsd(Number.NaN)).toBe(NO_PRICE);
  });
});

describe('usageRows — tabela função × modelo', () => {
  it('ordena Planejar → Decidir → Verificar, traduz a função e formata milhares em pt-BR', () => {
    const rows = usageRows(report());
    expect(rows.map((r) => [r.roleLabel, r.model, r.escalated])).toEqual([
      ['Planejar', 'claude-sonnet-4-5', false],
      ['Decidir', 'claude-haiku-4-5', false],
      ['Decidir', 'claude-sonnet-4-5', true],
      ['Verificar', 'claude-haiku-4-5', false],
    ]);
    expect(rows[1]).toMatchObject({
      calls: '12', fresh: '34.567', cacheRead: '120.000', output: '2.345', withImage: '3', usd: 'US$ 0,0456', priced: true, errors: 0,
    });
    expect(rows[2]).toMatchObject({ errors: 1, usd: 'US$ 0,0285' });
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length); // chaves únicas mesmo com função+modelo repetidos (tier)
  });

  it('linha de modelo sem preço mostra "sem preço" mas mantém chamadas e tokens', () => {
    const rows = usageRows(SIMULATED);
    expect(rows.map((r) => r.usd)).toEqual(['sem preço', 'sem preço']);
    expect(rows.every((r) => !r.priced)).toBe(true);
    expect(rows[1]).toMatchObject({ roleLabel: 'Decidir', model: 'simulado', calls: '8', fresh: '34.567' });
  });

  it('tolera relatório sem grupos e função fora do contrato', () => {
    expect(usageRows({ groups: [] })).toEqual([]);
    const odd = usageRows({ groups: [group({ role: 'summarize' as UsageGroup['role'] }), group({ role: 'plan' })] });
    expect(odd.map((r) => r.roleLabel)).toEqual(['Planejar', 'summarize']);
  });
});

describe('usageTotals', () => {
  it('execução com preços: US$ total, chamadas e US$ por aparelho, etapas por receita × por IA', () => {
    const t = usageTotals(report());
    expect(t).toMatchObject({
      pricing: 'priced', totalUsd: 'US$ 1,5864', usdPerObjective: 'US$ 0,3966', callsPerObjective: '5,3', objectivesWithAi: '4',
      calls: '21', drivenBy: '9 por receita × 2 por IA · 1 receita + IA', recipeShare: '75% (9 de 12)', unpricedModels: [],
    });
  });

  it('unpriced_models não vazio (ex.: "simulado"): "sem preço" no lugar de US$, sem fingir custo zero', () => {
    const t = usageTotals(SIMULATED);
    expect(t.pricing).toBe('unpriced');
    expect(t.totalUsd).toBe('sem preço');
    expect(t.usdPerObjective).toBe('sem preço');
    expect(t.unpricedModels).toEqual(['simulado']);
    // o que não depende de preço continua valendo
    expect(t.callsPerObjective).toBe('4,5');
    expect(t.calls).toBe('9');
  });

  it('mistura de modelos com e sem preço: total parcial em US$ + lista dos sem preço', () => {
    const mixed = report({ groups: [group({ usd: 0.0456 }), group({ role: 'verify', model: 'modelo-local', usd: null })], total_usd: 0.0456, usd_per_objective: 0.0114, unpriced_models: ['modelo-local'] });
    expect(pricingOf(mixed)).toBe('partial');
    const t = usageTotals(mixed);
    expect(t.totalUsd).toBe('US$ 0,0456');
    expect(t.unpricedModels).toEqual(['modelo-local']);
    expect(usageRows(mixed).map((r) => r.usd)).toEqual(['US$ 0,0456', 'sem preço']);
  });

  it('descobre modelos sem preço pelas linhas mesmo se unpriced_models vier vazio', () => {
    const r = report({ groups: [group({ model: 'x', usd: null })], unpriced_models: [] });
    expect(pricingOf(r)).toBe('unpriced');
    expect(usageTotals(r).unpricedModels).toEqual(['x']);
  });

  it('sem aparelho que usou IA não há média a mostrar', () => {
    const t = usageTotals(report({ groups: [], total_usd: 0, objectives_with_ai: 0, calls_per_objective: 0, usd_per_objective: 0, steps_driven_by: { recipe: 4 } }));
    expect(t).toMatchObject({ pricing: 'priced', totalUsd: 'US$ 0,00', usdPerObjective: '—', callsPerObjective: '—', drivenBy: '4 por receita × 0 por IA', recipeShare: '100% (4 de 4)' });
  });
});

describe('etapas por receita × por IA', () => {
  it('"recipe+ai" não conta como receita pura; chaves desconhecidas ficam de fora', () => {
    expect(drivenBySplit({ recipe: 3, 'recipe+ai': 1, ai: 4, outro: 50 })).toEqual({ recipe: 3, mixed: 1, ai: 4, total: 8, recipePercent: 38 });
    expect(drivenByText(drivenBySplit({ recipe: 3, ai: 1 }))).toBe('3 por receita × 1 por IA');
  });

  it('sem etapas concluídas não inventa percentual', () => {
    const empty = drivenBySplit({});
    expect(empty).toEqual({ recipe: 0, mixed: 0, ai: 0, total: 0, recipePercent: null });
    expect(drivenByText(empty)).toBe('—');
    expect(recipeShareText(empty)).toBe('—');
    expect(drivenBySplit(null).total).toBe(0);
  });

  it('isUsageEmpty: só quando não há chamadas NEM etapas', () => {
    expect(isUsageEmpty(report({ groups: [], steps_driven_by: {} }))).toBe(true);
    expect(isUsageEmpty(report({ groups: [], steps_driven_by: { recipe: 2 } }))).toBe(false);
    expect(isUsageEmpty(SIMULATED)).toBe(false);
  });
});
