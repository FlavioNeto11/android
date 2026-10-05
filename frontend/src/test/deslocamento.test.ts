import { describe, expect, it } from 'vitest';
import { lerDeslocamentoEmDias } from './deslocamento';

// 29.150: o valor de DESLOCAMENTO_DIAS que não é número faz a rodada FALHAR; antes virava "sem deslocamento" e a
// varredura das datas fixas parecia ter passado sem ter rodado.
describe('lerDeslocamentoEmDias', () => {
  it('ausente ou vazio é zero; número vale, com sinal e com ponto', () => {
    expect(lerDeslocamentoEmDias(undefined)).toBe(0);
    expect(lerDeslocamentoEmDias('')).toBe(0);
    expect(lerDeslocamentoEmDias('  ')).toBe(0);
    expect(lerDeslocamentoEmDias('40')).toBe(40);
    expect(lerDeslocamentoEmDias('-1')).toBe(-1);
    expect(lerDeslocamentoEmDias('0.4')).toBe(0.4);
  });

  it('vírgula decimal, unidade colada e lixo dão erro que diz o valor e como escrever', () => {
    for (const ruim of ['0,4', '40d', 'abc', 'NaN', 'Infinity']) {
      expect(() => lerDeslocamentoEmDias(ruim), ruim).toThrow(/não é um número de dias/);
    }
    expect(() => lerDeslocamentoEmDias('0,5')).toThrow('decimal com ponto');
  });
});
