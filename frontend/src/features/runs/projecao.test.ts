import { describe, expect, it } from 'vitest';
import { lerProjecao, rotuloDaJanela, semHistorico, textoDasChamadas, textoDoTempo, textoDoUsd } from './projecao';

/** Item 18.3: a leitura tolerante de `GET /api/runs/{id}/projection` e o rótulo da janela efetiva. Prova `simulated`. */

/** O formato de `backend/app/taskqueue/projecao.py::projetar` (e de `tests/test_projecao.py`). */
const PROJECAO = {
  janela_dias: 14, janela_configurada: 30, minimo_de_amostras: 5, amostras_sem_custo: 3,
  chamadas: { p50: 10, p90: 17 }, segundos: { p50: 180, p90: 420 }, usd: { p50: 0.2, p90: 0.38 },
  sem_base: ['curtir'],
  etapas: [
    { key: 'abrir', title: 'Abrir o perfil', action: 'abrir_perfil', samples: 12, calls: { p50: 4, p90: 7 },
      seconds: { p50: 60, p90: 150 }, usd: { p50: 0.08, p90: 0.15 }, no_baseline: false, samples_without_cost: 3 },
    { key: 'curtir', title: 'Curtir', action: '*', samples: 2, calls: { p50: 6, p90: 10 },
      seconds: { p50: 120, p90: 270 }, usd: { p50: 0.12, p90: 0.23 }, no_baseline: true, samples_without_cost: 0 },
  ],
};

describe('projeção do plano (18.3)', () => {
  it('lê o formato do backend e ignora o que não é etapa', () => {
    const p = lerProjecao({ ...PROJECAO, etapas: [...PROJECAO.etapas, 3, { title: 'sem chave' }] });
    expect(p?.janela_dias).toBe(14);
    expect(p?.etapas.map((e) => [e.key, e.no_baseline])).toEqual([['abrir', false], ['curtir', true]]);
    expect(p?.sem_base).toEqual(['curtir']);
    expect(lerProjecao({ detail: { code: 'no_plan' } })).toBeNull();
    expect(lerProjecao('x')).toBeNull();
    // Campo ausente vira zero, nunca quebra.
    expect(lerProjecao({ chamadas: {} })).toMatchObject({ janela_dias: null, chamadas: { p50: 0, p90: 0 }, etapas: [] });
  });

  it('a janela efetiva diz por que é menor que a configurada', () => {
    expect(rotuloDaJanela({ janela_dias: 14, janela_configurada: 30 })).toBe(
      'Normal medido nos últimos 14 dias — a janela configurada é de 30 dias, limitada pela retenção dos registros de IA');
    expect(rotuloDaJanela({ janela_dias: 30, janela_configurada: 30 })).toBe('Normal medido nos últimos 30 dias');
    expect(rotuloDaJanela({ janela_dias: 1, janela_configurada: 1 })).toBe('Normal medido no último dia');
    expect(rotuloDaJanela({ janela_dias: null, janela_configurada: 30 })).toContain('janela não informada');
  });

  it('sem histórico: todas as etapas sem base e nada somado (o corte de projecao.py::resumo)', () => {
    const p = lerProjecao(PROJECAO);
    expect(p && semHistorico(p)).toBe(false);
    const vazia = lerProjecao({ ...PROJECAO, chamadas: { p50: 0, p90: 0 }, sem_base: ['abrir', 'curtir'] });
    expect(vazia && semHistorico(vazia)).toBe(true);
  });

  it('faixas em pt-BR: chamadas, US$ com o prefixo uma vez, segundos ou minutos', () => {
    expect(textoDasChamadas({ p50: 10, p90: 17 })).toBe('10–17');
    expect(textoDasChamadas({ p50: 3, p90: 3 })).toBe('3');
    expect(textoDoUsd({ p50: 0.2, p90: 0.38 })).toBe('US$ 0,20–0,38');
    expect(textoDoUsd({ p50: 0.2, p90: 0.2 })).toBe('US$ 0,20');
    expect(textoDoTempo({ p50: 12, p90: 40 })).toBe('12–40 s');
    expect(textoDoTempo({ p50: 180, p90: 420 })).toBe('3–7 min');
    expect(textoDoTempo({ p50: 20, p90: 100 })).toBe('1–2 min');
  });
});
