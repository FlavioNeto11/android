import { describe, expect, it } from 'vitest';
import { falaDoTotal, numeroExibido, origemFalhou } from './exibicao';

describe('numeroExibido (B8: falha de leitura não pode parecer número)', () => {
  it('leitura completa: o número, com o teto opcional', () => {
    expect(numeroExibido(4, false)).toBe('4');
    expect(numeroExibido(0, false)).toBe('0');
    expect(numeroExibido(120, false, 99)).toBe('99+');
  });
  it('uma origem fora: o número é um piso ("4+") e, sem nada contado, "?" (nunca "0")', () => {
    expect(numeroExibido(4, true)).toBe('4+');
    expect(numeroExibido(0, true)).toBe('?');
    expect(numeroExibido(120, true, 99)).toBe('99+');
  });
});

describe('falaDoTotal', () => {
  it('sem falha: "4 aguardando você" e vazio no zero', () => {
    expect(falaDoTotal(4, false, 'aguardando você')).toBe('4 aguardando você');
    expect(falaDoTotal(0, false, 'aguardando você')).toBe('');
  });
  it('com falha: diz "ou mais" e o motivo, e no zero admite que não foi possível contar', () => {
    expect(falaDoTotal(4, true, 'aguardando você')).toBe('4 ou mais aguardando você; alguma origem não carregou');
    expect(falaDoTotal(0, true, 'aguardando você')).toBe('não foi possível contar; alguma origem não carregou');
  });
});

describe('origemFalhou', () => {
  it('cada origem depende da sua leitura; a execução vem do snapshot e não falha por leitura', () => {
    const f = { aprendizado: true, aprovacoes: false, personas: false };
    expect(origemFalhou('aprendizado', f)).toBe(true);
    expect(origemFalhou('persona', f)).toBe(false);
    expect(origemFalhou('persona', { ...f, aprovacoes: true })).toBe(true);
    expect(origemFalhou('intervencao', { ...f, personas: true })).toBe(true);
    expect(origemFalhou('execucao', { aprendizado: true, aprovacoes: true, personas: true })).toBe(false);
  });
});
