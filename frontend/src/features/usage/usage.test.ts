import { describe, expect, it } from 'vitest';
import type { UsageGroup, UsageReport } from '../../api/types';
import {
  NO_PRICE, byOriginText, cascadeText, drivenBySplit, drivenByText, errorKindLabel, errorsByKindText, escalationLabel,
  escalationRows, formatUsd, imageReasonLabel, imageReasonRows, isUsageEmpty, originLabel, pricingOf,
  recipeShareText, rejudgeByAppRows, rejudgeText, stepsDrivenByNull, usageRows, usageTotals,
} from './usage';

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

describe('errorsByKindText — item 7.3 (achado #101)', () => {
  it('maior contagem primeiro, com rótulo pt-BR; null sem nenhum erro', () => {
    expect(errorsByKindText(report({ errors_by_kind: { refusal: 3, budget: 1 } })))
      .toBe('3 recusa do provedor · 1 orçamento esgotado');
    expect(errorsByKindText(report({ errors_by_kind: {} }))).toBeNull();
    expect(errorsByKindText(report({ errors_by_kind: undefined }))).toBeNull();
    expect(errorsByKindText(report({}))).toBeNull();
  });

  it('tipo desconhecido cai no próprio nome, sem quebrar', () => {
    expect(errorKindLabel('refusal')).toBe('recusa do provedor');
    expect(errorKindLabel('algo-novo')).toBe('algo-novo');
  });
});

describe('I1 da validação do deploy 7', () => {
  it('o prazo da etapa e o saldo bloqueado têm rótulo (não saem crus)', () => {
    expect(errorKindLabel('step_deadline')).toBe('prazo da etapa esgotado');
    expect(errorsByKindText(report({ errors_by_kind: { budget: 10, step_deadline: 4, error: 1 } })))
      .toBe('10 orçamento esgotado · 4 prazo da etapa esgotado · 1 erro');
  });

  it('por origem: maior custo primeiro, com rótulo; origem desconhecida passa crua; nada sem o grupo', () => {
    const texto = byOriginText(report({ by_origin: {
      sem_origem: { calls: 1320, usd: 14.68 }, execucao: { calls: 52, usd: 1.29 }, curador: { calls: 15, usd: 0.31 },
      leitura: { calls: 128, usd: 0.04 }, vazio: { calls: 0, usd: 0 },
    } }));
    expect(texto).toBe(`sem origem registrada ${formatUsd(14.68)} · execução ${formatUsd(1.29)} · curador ${formatUsd(0.31)} · leitura visual ${formatUsd(0.04)}`);
    expect(originLabel('algo-novo')).toBe('algo-novo');
    expect(byOriginText(report({}))).toBeNull();
  });
});

describe('RA-10, a tela do 31.16 (adendo v0.75)', () => {
  const ra10: Partial<UsageReport> = {
    escalations: {
      efeito: { calls: 12, usd: 0.4 }, nova_tentativa: { calls: 3, usd: 0.1 }, ciclo: { calls: 0, usd: 0 },
      motivo_novo: { calls: 1, usd: 0.01 },
    },
    rejudges: {
      calls: 13, usd: 0.04, by_kind: { nivel: { calls: 9, usd: 0.03 }, sim_com_efeito: { calls: 4, usd: 0.01 } },
      judged: 12, disagreements: 1, disagreement_rate: 0.0833,
      by_app: { 'app-ig': { judged: 10, disagreements: 1, disagreement_rate: 0.1 }, '*': { judged: 2, disagreements: 0, disagreement_rate: 0 } },
    },
    cascades: { calls: 5, usd: 0.12, unblocked: 3, by_verdict: { step_blocked: 2, click: 3 } },
    image_reasons: {
      arvore_rica: { calls: 40, with_image: 0 }, pedida: { calls: 3, with_image: 3 }, problema: { calls: 7, with_image: 6 },
      novo: { calls: 1, with_image: 1 }, sensivel: { calls: 0, with_image: 0 },
    },
    steps_driven_by_null: 2,
  };

  it('o modelo forte por motivo, maior custo primeiro, sem os zerados; o motivo novo aparece cru', () => {
    expect(escalationRows(report(ra10)).map((r) => [r.key, r.label, r.calls, r.usd])).toEqual([
      ['efeito', 'efeito externo', '12×', formatUsd(0.4)], ['nova_tentativa', 'nova tentativa', '3×', formatUsd(0.1)],
      ['motivo_novo', 'motivo_novo', '1×', formatUsd(0.01)],
    ]);
    expect(escalationLabel('sim_com_efeito')).toBe('conferir o "sim" com efeito');
    expect(escalationRows(report({}))).toEqual([]);
  });

  it('o rejulgamento diz julgadas, discordância e custo; por app, mais julgadas primeiro', () => {
    expect(rejudgeText(report(ra10))).toBe(`12 julgada(s), 8 % de discordância (1) · ${formatUsd(0.04)} em 13 chamada(s)`);
    expect(rejudgeByAppRows(report(ra10)).map((r) => [r.appId, r.judged, r.disagreements, r.rate])).toEqual([
      ['app-ig', '10', '1', '10 %'], ['*', '2', '0', '0 %'],
    ]);
    // chamadas sem veredito válido (só erro): o custo aparece, a discordância não se inventa
    const soErro = { ...ra10.rejudges!, judged: 0, disagreements: 0, disagreement_rate: null, by_app: {} };
    expect(rejudgeText(report({ rejudges: soErro }))).toBe(`nenhum veredito válido · ${formatUsd(0.04)} em 13 chamada(s)`);
    expect(rejudgeText(report({ rejudges: { ...soErro, calls: 0 } }))).toBeNull();
    expect(rejudgeText(report({}))).toBeNull();
  });

  it('a cascata do bloqueio diz quantas subiram e quantas desbloquearam', () => {
    expect(cascadeText(report(ra10))).toBe(`5 subida(s), 3 desbloquearam a tela · ${formatUsd(0.12)}`);
    expect(cascadeText(report({ cascades: { calls: 0, usd: 0, unblocked: 0, by_verdict: {} } }))).toBeNull();
  });

  it('a imagem: primeiro os motivos que a mandam, depois os que não, e o motivo novo por último', () => {
    expect(imageReasonRows(report(ra10)).map((r) => [r.key, r.sends, r.calls, r.withImage])).toEqual([
      ['problema', true, '7', '6'], ['pedida', true, '3', '3'], ['arvore_rica', false, '40', '0'], ['novo', null, '1', '1'],
    ]);
    expect(imageReasonLabel('arvore_rica')).toBe('árvore da tela bastou');
  });

  it('etapas sem condutor', () => {
    expect(stepsDrivenByNull(report(ra10))).toBe(2);
    expect(stepsDrivenByNull(report({}))).toBe(0);
  });
});
