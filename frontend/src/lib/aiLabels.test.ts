import { describe, expect, it } from 'vitest';
import {
  aiEscaladaRows, aiFeatureRows, aiModelRows, aiProfileRows, aiRoleLabel, aiRoleRows, decisaoFechadaConsumidoresLabel, effortLabel,
  esquemaDoPlanoLabel, flowsLabel, hubRoleLabel, imagePolicyLabel, leituraVisualLabel, recipesModeLabel, thinkingLabel,
} from './aiLabels';

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

describe('I2 da validação do deploy 7 (v0.87): a aba IA em português', () => {
  it('esforço e raciocínio traduzidos; valor fora do contrato passa cru; vazio é —', () => {
    expect(effortLabel('low')).toBe('baixo');
    expect(effortLabel('xhigh')).toBe('muito alto');
    expect(effortLabel('turbo')).toBe('turbo');
    expect(effortLabel(null)).toBe('—');
    expect(thinkingLabel('adaptive')).toBe('adaptativo');
    expect(thinkingLabel('nao_declarado')).toBe('o modelo não declara');
    expect(thinkingLabel(null)).toBe('—');
  });

  it('o esquema do plano e a função de leitura têm nome', () => {
    expect(esquemaDoPlanoLabel('curto')).toContain('curto');
    expect(esquemaDoPlanoLabel(undefined)).toBe('—');
    expect(hubRoleLabel('leitura')).toBe('Ler a tela (leitura visual)');
  });

  it('a leitura visual diz ligada com o leitor, desligada, ou nada em backend anterior', () => {
    const leitor = {
      role: 'leitura', provider: 'gemini', kind: 'openai', model: 'gemini-3.1-flash-lite', endpoint: 'g', sends_data_externally: true,
      configured: true, priced: true, vision: true, tools: true, refusal_fallback: false,
    };
    expect(leituraVisualLabel({ leitura_visual: true, roles: [leitor] })).toBe('ligada · gemini-3.1-flash-lite (gemini)');
    expect(leituraVisualLabel({ leitura_visual: true, roles: [] })).toContain('sem leitor');
    expect(leituraVisualLabel({ leitura_visual: false, roles: [leitor] })).toBe('desligada');
    expect(leituraVisualLabel({ roles: [leitor] })).toBeNull();
  });

  it('a linha da função leva esforço e raciocínio', () => {
    const [linha] = aiRoleRows({ roles: [{
      role: 'plan', provider: 'anthropic', kind: 'anthropic', model: 'claude-opus-5-5', endpoint: 'api.anthropic.com',
      sends_data_externally: true, configured: true, priced: true, vision: true, tools: true, refusal_fallback: false,
      effort: 'low', thinking: 'adaptive',
    }] });
    expect([linha?.effort, linha?.thinking]).toEqual(['baixo', 'adaptativo']);
  });

  it('perfis: o que muda, os ajustes, o canário e se os dados saem', () => {
    const [sonnet, dieta] = aiProfileRows({ profiles: [
      { name: 'planejador-sonnet', note: 'plano no Sonnet', canary_fraction: 0.25, screenshot_max_side: null, rich_tree_min_elements: null,
        roles: [{ role: 'plan', provider: 'anthropic', model: 'claude-sonnet-5-5', effort: 'low', sends_data_externally: true }] },
      { name: 'img-768', note: '', canary_fraction: null, screenshot_max_side: 768, rich_tree_min_elements: 0, roles: [] },
    ] });
    expect(sonnet?.changes).toEqual(['Planejar: claude-sonnet-5-5 (anthropic, esforço baixo)']);
    expect([sonnet?.canary, sonnet?.external]).toEqual(['25 % das execuções sem perfil', true]);
    expect(dieta?.adjustments).toEqual(['imagem até 768 px', 'árvore rica a partir de 0 elementos']);
    expect([dieta?.canary, dieta?.external]).toEqual([null, false]);
    expect(aiProfileRows({})).toEqual([]);
  });
});

describe('polimento do deploy 10: os consumidores da decisão fechada (v0.90)', () => {
  it('origem e modo em palavras; valor fora do contrato passa cru; sem bloco é null', () => {
    const bloco = (consumers: Record<string, string>) => ({ decisao_fechada: {
      provider: 'typesafe', name: 'Jev', consumers, classes: ['C0'], send_approved: true, key: 'configurada', decider: 'jev',
      sending: true, retention_days: 180,
    } });
    expect(decisaoFechadaConsumidoresLabel(bloco({ curador: 'shadow', intencao: 'shadow' })))
      .toBe('curador: em sombra · intenção: em sombra');
    expect(decisaoFechadaConsumidoresLabel(bloco({ apps: 'on', novo: 'turbo' }))).toBe('apps: ligado · novo: turbo');
    expect(decisaoFechadaConsumidoresLabel(bloco({}))).toBeNull();
    expect(decisaoFechadaConsumidoresLabel({ decisao_fechada: null })).toBeNull();
    expect(decisaoFechadaConsumidoresLabel({})).toBeNull();
  });
});

describe('31.223: a política de escalada do modelo forte', () => {
  it('sem os campos não há linha (central anterior); cada campo vira uma linha em palavras', () => {
    expect(aiEscaladaRows({})).toEqual([]);
    expect(aiEscaladaRows({ strong_model_only_on_commit: null, strong_model_for_side_effect: null })).toEqual([]);
    expect(aiEscaladaRows({ strong_model_only_on_commit: true }).map((r) => r.key)).toEqual(['strong_model_only_on_commit']);
    expect(aiEscaladaRows({ strong_model_for_side_effect: 'by_risk' })[0]!.value).toBe('pelo risco da etapa');
  });
  it('o valor de strong_model_for_side_effect em palavras: verdadeiro, falso, por risco e um texto novo cru', () => {
    const v = (x: string | boolean) => aiEscaladaRows({ strong_model_for_side_effect: x })[0]!.value;
    expect(v(true)).toBe('sempre que a etapa tem efeito');
    expect(v('false')).toContain('nunca');
    expect(v('by_risk')).toBe('pelo risco da etapa');
    expect(v('politica_nova')).toBe('politica_nova');
  });
});
