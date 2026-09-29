import { describe, expect, it } from 'vitest';
import { lerResultado, lerResultados, seloDaProva, separarEfeito, separarProva } from './resultadoDaInstancia';

/**
 * Leitura de uma linha de `per_instance` (`RunService.report`) para o cartão da instância. As linhas abaixo têm o
 * formato das execuções reais 6eb84c, 02ee9e e 7cfa59 (texto encurtado). Prova `simulated`.
 */
const LINHA_02EE9E = {
  instance_id: 'android-06', status: 'failed', detail: 'Tempo total do objetivo esgotado.',
  worker_id: 'WIN-7S2UASNLFOP', device_serial: 'emulator-5564', proven: false, delivery_level: null,
  blocked_reason: 'Tempo total do objetivo esgotado.', needs: null, effects: [],
  proven_steps: ['Abrir o perfil de @alvo: pós-condição comprovada pela árvore local, sem IA (selector:id=action_bar_title|text=={username})'],
  manually_confirmed_steps: [],
  open_steps: ['Abrir o perfil de @alvo', 'Abrir a publicação'],
  plan_versions: 2, ai_calls: 11, ai_tokens: 140018,
};

describe('separarProva', () => {
  it('separa o título da prova no primeiro ": " (a prova pode ter dois-pontos)', () => {
    expect(separarProva('Abrir a publicação: seletor id=action_bar_title|text==Posts: 1 elemento(s)')).toEqual({
      titulo: 'Abrir a publicação', prova: 'seletor id=action_bar_title|text==Posts: 1 elemento(s)',
    });
  });

  it('sem o separador, a linha inteira é o título e não há prova inventada', () => {
    expect(separarProva('Etapa sem prova')).toEqual({ titulo: 'Etapa sem prova', prova: null });
  });
});

describe('separarEfeito', () => {
  it('lê o instante, a etapa entre aspas e o texto', () => {
    expect(separarEfeito("2026-09-28T23:53:45.578Z — 'Curtir a publicação': tap executado ([receita v1] Curtir.)")).toEqual({
      quando: '2026-09-28T23:53:45.578Z', etapa: 'Curtir a publicação', texto: 'tap executado ([receita v1] Curtir.)',
    });
  });

  it('o que não casar fica inteiro no texto', () => {
    expect(separarEfeito('efeito em formato livre')).toEqual({ quando: null, etapa: null, texto: 'efeito em formato livre' });
  });
});

describe('lerResultado', () => {
  it('não funde as listas: a etapa comprovada numa versão antiga continua em aberto no plano final', () => {
    const r = lerResultado(LINHA_02EE9E);
    expect(r.comprovadas?.map((e) => e.titulo)).toEqual(['Abrir o perfil de @alvo']);
    expect(r.emAberto).toEqual(['Abrir o perfil de @alvo', 'Abrir a publicação']);
    expect(r.versaoDoPlano).toBe(2);
    expect(r.efeitos).toEqual([]);
  });

  it('o motivo igual ao detalhe não se repete; um motivo diferente aparece', () => {
    expect(lerResultado(LINHA_02EE9E).motivo).toBeNull();
    expect(lerResultado({ ...LINHA_02EE9E, blocked_reason: 'Tela de desafio.' }).motivo).toBe('Tela de desafio.');
  });

  it('formato antigo: o summary vira o texto, a lista ausente fica null e a chave desconhecida vai para extras', () => {
    const r = lerResultado({ instance_id: 'android-01', status: 'succeeded', summary: 'Mensagem enviada', delivery_level: 'sent', attempts: 2 });
    expect(r.texto).toBe('Mensagem enviada');
    expect(r.comprovadas).toBeNull();
    expect(r.efeitos).toBeNull();
    expect(r.extras).toEqual([['attempts', 2]]);
  });

  it('um summary que diz outra coisa além do detail não some', () => {
    const r = lerResultado({ ...LINHA_02EE9E, summary: 'Resumo diferente' });
    expect(r.texto).toBe('Tempo total do objetivo esgotado.');
    expect(r.extras).toEqual([['summary', 'Resumo diferente']]);
  });
});

describe('seloDaProva', () => {
  it('"comprovado" só com proven: true', () => {
    expect(seloDaProva(lerResultado({ ...LINHA_02EE9E, status: 'succeeded', proven: true }))).toBe('comprovado');
  });

  it('sucesso com etapa confirmada à mão nunca vira comprovado', () => {
    const r = lerResultado({ ...LINHA_02EE9E, status: 'succeeded', proven: false, manually_confirmed_steps: ['Enviar'] });
    expect(seloDaProva(r)).toBe('a_mao');
  });

  it('falha não tem selo, e sem o campo proven (formato antigo) não se inventa prova', () => {
    expect(seloDaProva(lerResultado(LINHA_02EE9E))).toBeNull();
    expect(seloDaProva(lerResultado({ instance_id: 'android-01', status: 'succeeded' }))).toBeNull();
  });
});

describe('lerResultados', () => {
  it('uma linha que não é objeto derruba para a árvore genérica (null)', () => {
    expect(lerResultados([LINHA_02EE9E, 'texto solto'])).toBeNull();
    expect(lerResultados([LINHA_02EE9E])).toHaveLength(1);
  });
});
