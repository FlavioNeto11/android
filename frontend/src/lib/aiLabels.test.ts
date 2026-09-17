import { describe, expect, it } from 'vitest';
import { aiFeatureRows, aiModelRows, aiRoleLabel, flowsLabel, imagePolicyLabel, recipesModeLabel } from './aiLabels';

describe('chip da IA — modelos por função', () => {
  it('lista Planejar / Decidir / Verificar / Escalonamento a partir de AiStatus.models', () => {
    const rows = aiModelRows({ models: { plan: 'sonnet', decide: 'haiku', verify: 'haiku', escalation: 'opus' } });
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ['Planejar', 'sonnet'], ['Decidir', 'haiku'], ['Verificar', 'haiku'], ['Escalonamento', 'opus'],
    ]);
  });

  it('fica vazio quando o backend não informa (null/ausente) e pula função sem modelo', () => {
    expect(aiModelRows({ models: null })).toEqual([]);
    expect(aiModelRows({})).toEqual([]);
    expect(aiModelRows({ models: { plan: 'sonnet', decide: '', verify: 'haiku', escalation: '' } }).map((r) => r.key)).toEqual(['plan', 'verify']);
  });

  it('traduz a função e devolve o valor cru se vier algo fora do contrato', () => {
    expect(aiRoleLabel('plan')).toBe('Planejar');
    expect(aiRoleLabel('decide')).toBe('Decidir');
    expect(aiRoleLabel('verify')).toBe('Verificar');
    expect(aiRoleLabel('summarize')).toBe('summarize');
  });
});

describe('receitas / fluxos / imagens', () => {
  it('rótulos em pt-BR', () => {
    expect(recipesModeLabel('off')).toBe('Desligadas');
    expect(recipesModeLabel('shadow')).toMatch(/^Modo sombra/);
    expect(recipesModeLabel('replay')).toMatch(/^Reprodução/);
    expect(imagePolicyLabel('always')).toMatch(/^Sempre/);
    expect(imagePolicyLabel('auto')).toMatch(/^Automático/);
    expect(imagePolicyLabel('never')).toMatch(/^Nunca/);
    expect(flowsLabel(true)).toBe('Ligados');
    expect(flowsLabel(false)).toBe('Desligados');
    expect(flowsLabel(null)).toBe('—');
    expect(recipesModeLabel('turbo')).toBe('turbo');
  });

  it('usa o AiStatus e, quando ele vem nulo (modo simulado), cai para health.features', () => {
    const fromAi = aiFeatureRows({ recipes: 'shadow', flows: false, image_policy: 'never' }, { recipes: 'replay', flows: true, image_policy: 'auto' });
    expect(fromAi.map((r) => r.value)).toEqual([recipesModeLabel('shadow'), 'Desligados', imagePolicyLabel('never')]);

    const fromFeatures = aiFeatureRows({ recipes: null, flows: null, image_policy: null }, { recipes: 'replay', flows: true, image_policy: 'auto' });
    expect(fromFeatures.map((r) => [r.label, r.value])).toEqual([
      ['Receitas', 'Reprodução (sem custo de modelo)'], ['Fluxos', 'Ligados'], ['Imagens da tela', 'Automático (só quando precisa)'],
    ]);
  });

  it('sem nenhuma das fontes (backend pré-v0.2) não inventa estado', () => {
    expect(aiFeatureRows({})).toEqual([]);
    expect(aiFeatureRows({ recipes: null, flows: null, image_policy: null }, null)).toEqual([]);
  });
});
