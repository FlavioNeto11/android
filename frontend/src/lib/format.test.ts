import { describe, expect, it } from 'vitest';
import {
  clamp01, cx, formatDecimal, formatGb, formatInt, formatMb, formatPercent, formatUsd4,
  humanizeKey, isRecord, prettyJson, plural, ratio, scalarToText, truncate,
} from './format';

describe('formatUsd4', () => {
  it('formata frações de dólar com quatro casas e preserva o sinal', () => {
    expect(formatUsd4(0.0407)).toBe('US$ 0,0407');
    expect(formatUsd4(-1.2)).toBe('US$ -1,2000');
  });

  it('arredonda na quarta casa e representa zero', () => {
    expect(formatUsd4(2.34567)).toBe('US$ 2,3457');
    expect(formatUsd4(0)).toBe('US$ 0,0000');
  });
});

describe('formatInt', () => {
  it('agrupa inteiros e preserva valores negativos e zero', () => {
    expect(formatInt(1234)).toBe('1.234');
    expect(formatInt(-1234)).toBe('-1.234');
    expect(formatInt(0)).toBe('0');
  });

  it('arredonda e sinaliza valores ausentes ou não finitos', () => {
    expect(formatInt(1234.6)).toBe('1.235');
    expect(formatInt(null)).toBe('—');
    expect(formatInt(undefined)).toBe('—');
    expect(formatInt(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('formatDecimal', () => {
  it('usa uma casa decimal com vírgula e mantém o sinal', () => {
    expect(formatDecimal(1234.56)).toBe('1.234,6');
    expect(formatDecimal(-1.26)).toBe('-1,3');
    expect(formatDecimal(0)).toBe('0,0');
  });

  it('sinaliza valores ausentes ou não finitos', () => {
    expect(formatDecimal(null)).toBe('—');
    expect(formatDecimal(undefined)).toBe('—');
    expect(formatDecimal(Number.NaN)).toBe('—');
  });
});

describe('formatPercent', () => {
  it('arredonda para percentual inteiro e mantém o sinal', () => {
    expect(formatPercent(12.6)).toBe('13%');
    expect(formatPercent(-12.6)).toBe('-13%');
    expect(formatPercent(0)).toBe('0%');
  });

  it('sinaliza valores ausentes ou não finitos', () => {
    expect(formatPercent(null)).toBe('—');
    expect(formatPercent(undefined)).toBe('—');
    expect(formatPercent(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('formatGb', () => {
  it('formata gigabytes com uma casa decimal e sinal', () => {
    expect(formatGb(1.26)).toBe('1,3 GB');
    expect(formatGb(-1.26)).toBe('-1,3 GB');
    expect(formatGb(0)).toBe('0,0 GB');
  });

  it('sinaliza valores ausentes ou não finitos', () => {
    expect(formatGb(null)).toBe('—');
    expect(formatGb(undefined)).toBe('—');
    expect(formatGb(Number.NaN)).toBe('—');
  });
});

describe('formatMb', () => {
  it('mantém MB abaixo de 1024 e converte a partir do limite', () => {
    expect(formatMb(512)).toBe('512 MB');
    expect(formatMb(1023)).toBe('1.023 MB');
    expect(formatMb(1024)).toBe('1,0 GB');
    expect(formatMb(1536)).toBe('1,5 GB');
  });

  it('arredonda MB negativos e sinaliza valores ausentes ou não finitos', () => {
    expect(formatMb(-1.6)).toBe('-2 MB');
    expect(formatMb(0)).toBe('0 MB');
    expect(formatMb(null)).toBe('—');
    expect(formatMb(undefined)).toBe('—');
    expect(formatMb(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('clamp01', () => {
  it('limita valores abaixo de zero e acima de um', () => {
    expect(clamp01(-0.25)).toBe(0);
    expect(clamp01(1.25)).toBe(1);
  });

  it('preserva zero, um e valores dentro do intervalo; não finitos viram zero', () => {
    expect(clamp01(0)).toBe(0);
    expect(clamp01(1)).toBe(1);
    expect(clamp01(0.4)).toBe(0.4);
    expect(clamp01(Number.NaN)).toBe(0);
    expect(clamp01(Number.POSITIVE_INFINITY)).toBe(0);
  });
});

describe('ratio', () => {
  it('calcula a proporção e limita-a ao intervalo de zero a um', () => {
    expect(ratio(2, 8)).toBe(0.25);
    expect(ratio(12, 8)).toBe(1);
    expect(ratio(-2, 8)).toBe(0);
  });

  it('trata zero, total negativo e valores não finitos como zero', () => {
    expect(ratio(0, 8)).toBe(0);
    expect(ratio(1, 0)).toBe(0);
    expect(ratio(1, -2)).toBe(0);
    expect(ratio(Number.NaN, 2)).toBe(0);
  });
});

describe('plural', () => {
  it('usa a forma singular somente para um e a plural para zero e múltiplos', () => {
    expect(plural(1, 'item', 'itens')).toBe('1 item');
    expect(plural(0, 'item', 'itens')).toBe('0 itens');
    expect(plural(3, 'item', 'itens')).toBe('3 itens');
  });

  it('formata contagens negativas como inteiro e usa o plural', () => {
    expect(plural(-2, 'instância', 'instâncias')).toBe('-2 instâncias');
  });
});

describe('truncate', () => {
  it('mantém textos no limite ou menores e encurta textos maiores com reticências', () => {
    expect(truncate('curto', 5)).toBe('curto');
    expect(truncate('texto longo', 7)).toBe('texto…');
  });

  it('trata limites zero e um sem deixar caracteres excedentes', () => {
    expect(truncate('abc', 1)).toBe('…');
    expect(truncate('abc', 0)).toBe('…');
  });
});

describe('cx', () => {
  it('junta classes na ordem e ignora valores vazios ou falsos', () => {
    expect(cx('painel', false, 'ativo')).toBe('painel ativo');
    expect(cx('', null, undefined, 'visível')).toBe('visível');
  });

  it('retorna texto vazio quando não há classes', () => {
    expect(cx()).toBe('');
    expect(cx(false, null, undefined, '')).toBe('');
  });
});

describe('prettyJson', () => {
  it('serializa objetos de forma legível e valores primitivos em JSON', () => {
    expect(prettyJson({ nome: 'exemplo', ativo: true })).toBe('{\n  "nome": "exemplo",\n  "ativo": true\n}');
    expect(prettyJson(0)).toBe('0');
    expect(prettyJson(null)).toBe('null');
  });

  it('usa texto para undefined e retorna fallback sem lançar em referência circular', () => {
    expect(prettyJson(undefined)).toBe('undefined');
    const circular: { self?: unknown } = {};
    circular.self = circular;
    expect(prettyJson(circular)).toBe('[object Object]');
  });
});

describe('isRecord', () => {
  it('reconhece objetos e rejeita null e listas', () => {
    expect(isRecord({ chave: 'valor' })).toBe(true);
    expect(isRecord(null)).toBe(false);
    expect(isRecord([])).toBe(false);
  });

  it('rejeita primitivos e undefined', () => {
    expect(isRecord('texto')).toBe(false);
    expect(isRecord(0)).toBe(false);
    expect(isRecord(undefined)).toBe(false);
  });
});

describe('humanizeKey', () => {
  it('converte snake_case, kebab-case e camelCase em rótulos legíveis', () => {
    expect(humanizeKey('emulator_version')).toBe('Emulator version');
    expect(humanizeKey('device-name')).toBe('Device name');
    expect(humanizeKey('deviceName')).toBe('Device Name');
  });

  it('preserva texto vazio e remove separadores nas extremidades', () => {
    expect(humanizeKey('')).toBe('');
    expect(humanizeKey('__device_name--')).toBe('Device name');
  });
});

describe('scalarToText', () => {
  it('representa valores nulos, booleanos e strings vazias em português', () => {
    expect(scalarToText(null)).toBe('—');
    expect(scalarToText(undefined)).toBe('—');
    expect(scalarToText(true)).toBe('Sim');
    expect(scalarToText(false)).toBe('Não');
    expect(scalarToText('')).toBe('—');
    expect(scalarToText('texto')).toBe('texto');
  });

  it('formata inteiros e arredonda decimais para duas casas preservando o sinal', () => {
    expect(scalarToText(1234)).toBe('1.234');
    expect(scalarToText(-1234)).toBe('-1.234');
    expect(scalarToText(1.236)).toBe('1,24');
    expect(scalarToText(-1.236)).toBe('-1,24');
    expect(scalarToText(0)).toBe('0');
  });

  it('serializa valores não escalares como JSON legível', () => {
    expect(scalarToText({ ativo: true })).toBe('{\n  "ativo": true\n}');
    expect(scalarToText([1, 'dois'])).toBe('[\n  1,\n  "dois"\n]');
  });
});
