import { describe, expect, it } from 'vitest';
import type { Pessoa } from './pessoa';
import { homonimosDoSegmento, resolverPersona, slugBase, slugDaPersona, slugify, slugsDasPersonas } from './slugPersona';

/** Só o que o slug lê: id e as formas do nome. */
function pessoa(id: string, name: string, over: Record<string, unknown> = {}): Pessoa {
  return { id, name, display_name: null, first_name: null, last_name: null, username: null, ...over } as unknown as Pessoa;
}

describe('slugify', () => {
  it('tira acentos, junta palavras com traço e põe em minúsculas', () => {
    expect(slugify('Lucas Almeida')).toBe('lucas-almeida');
    expect(slugify('Ana Beatriz Ñandú-Ávila')).toBe('ana-beatriz-nandu-avila');
    expect(slugify('João da Conceição')).toBe('joao-da-conceicao');
    expect(slugify('Søren Æble Łukasz')).toBe('soren-aeble-lukasz');
  });

  it('espaços e pontuação repetidos viram um traço só, sem traço nas pontas', () => {
    expect(slugify('   Maria   da   Silva  ')).toBe('maria-da-silva');
    expect(slugify('--Dr.  Fulano (o novo)!--')).toBe('dr-fulano-o-novo');
    expect(slugify('a_b/c')).toBe('a-b-c');
  });

  it('nome sem letra nem dígito aproveitável dá cadeia vazia', () => {
    expect(slugify('')).toBe('');
    expect(slugify('   ')).toBe('');
    expect(slugify('日本語 !!!')).toBe('');
  });

  it('nome comprido é cortado numa fronteira de palavra', () => {
    const longo = slugify('Maria Eduarda Fernandes de Albuquerque Cavalcanti Montenegro da Silva Sauro');
    expect(longo.length).toBeLessThanOrEqual(48);
    expect(longo.endsWith('-')).toBe(false);
    expect('maria-eduarda-fernandes-de-albuquerque-cavalcanti-montenegro-da-silva-sauro').toContain(longo);
  });
});

describe('slugBase', () => {
  it('nome vazio cai no nome de exibição, no @ ou em "persona"', () => {
    expect(slugBase(pessoa('ig-1', '   ', { display_name: 'Lúcia Prado' }))).toBe('lucia-prado');
    expect(slugBase(pessoa('ig-2', '', { username: 'lu.prado' }))).toBe('lu-prado');
    expect(slugBase(pessoa('ig-3', '日本語'))).toBe('persona');
    expect(slugBase(pessoa('ig-4', ''))).toBe('pessoa-sem-nome');
  });
});

describe('slugsDasPersonas', () => {
  const lucas1 = pessoa('ig-R7UM9mwweF0rFqG8', 'Lucas Almeida');
  const lucas2 = pessoa('ig-ieeUGPgFRyyw7kCB', 'Lucas Almeida');
  const ana = pessoa('ig-EydRwNVvFHQ6yTlu', 'Ana Beatriz Ñandú-Ávila');

  it('quem está sozinho com o nome fica com o slug limpo', () => {
    const slugs = slugsDasPersonas([lucas1, ana]);
    expect(slugs.get(lucas1.id)).toBe('lucas-almeida');
    expect(slugs.get(ana.id)).toBe('ana-beatriz-nandu-avila');
  });

  it('homônimos recebem sufixo curto tirado do próprio id, e ninguém fica com o slug limpo', () => {
    const slugs = slugsDasPersonas([lucas1, lucas2, ana]);
    expect(slugs.get(lucas1.id)).toBe('lucas-almeida-fqg8');
    expect(slugs.get(lucas2.id)).toBe('lucas-almeida-7kcb');
    expect(slugs.get(lucas1.id)).not.toBe(slugs.get(lucas2.id));
    expect(slugs.get(lucas1.id)).toMatch(/^lucas-almeida-[a-z0-9]{4}$/);
    expect(slugs.get(ana.id)).toBe('ana-beatriz-nandu-avila');
  });

  it('é determinístico: a ordem da lista e um terceiro homônimo não mudam o slug dos outros', () => {
    const a = slugsDasPersonas([lucas1, lucas2]);
    const b = slugsDasPersonas([lucas2, ana, lucas1]);
    expect(b.get(lucas1.id)).toBe(a.get(lucas1.id));
    expect(b.get(lucas2.id)).toBe(a.get(lucas2.id));
    const lucas3 = pessoa('ig-QwErTyUiOp12345', 'lucas ALMEIDA');
    const c = slugsDasPersonas([lucas1, lucas2, lucas3]);
    expect(c.get(lucas1.id)).toBe(a.get(lucas1.id));
    expect(c.get(lucas2.id)).toBe(a.get(lucas2.id));
    expect(new Set(c.values()).size).toBe(3);
  });

  it('ids que terminam igual aumentam o sufixo até distinguir', () => {
    const x = pessoa('ig-AAAA1111zzzz', 'Rui Costa');
    const y = pessoa('ig-BBBB2222zzzz', 'Rui Costa');
    const slugs = slugsDasPersonas([x, y]);
    expect(slugs.get(x.id)).not.toBe(slugs.get(y.id));
    expect(slugs.get(x.id)).toBe('rui-costa-1zzzz');
    expect(slugs.get(y.id)).toBe('rui-costa-2zzzz');
  });

  it('um slug que coincide com o id de outra pessoa cede: a pessoa usa o próprio id', () => {
    const dona = pessoa('maria', 'Fulana Qualquer');
    const maria = pessoa('ig-9', 'Maria');
    const slugs = slugsDasPersonas([dona, maria]);
    expect(slugs.get(maria.id)).toBe('ig-9');
    expect(slugs.get(dona.id)).toBe('fulana-qualquer');
  });

  it('slugDaPersona é o mesmo valor do mapa', () => {
    expect(slugDaPersona(lucas1, [lucas1, lucas2])).toBe(slugsDasPersonas([lucas1, lucas2]).get(lucas1.id));
    expect(slugDaPersona(ana, [])).toBe(ana.id);
  });
});

describe('resolverPersona', () => {
  const lucas1 = pessoa('ig-R7UM9mwweF0rFqG8', 'Lucas Almeida');
  const lucas2 = pessoa('ig-ieeUGPgFRyyw7kCB', 'Lucas Almeida');
  const ana = pessoa('ig-EydRwNVvFHQ6yTlu', 'Ana Souza');

  it('o id antigo continua abrindo a persona', () => {
    expect(resolverPersona('ig-EydRwNVvFHQ6yTlu', [lucas1, lucas2, ana])).toBe(ana);
    expect(resolverPersona('ig-R7UM9mwweF0rFqG8', [lucas1, lucas2, ana])).toBe(lucas1);
  });

  it('o slug abre a persona certa, com ou sem maiúsculas no link', () => {
    expect(resolverPersona('ana-souza', [lucas1, lucas2, ana])).toBe(ana);
    expect(resolverPersona('Ana-Souza', [lucas1, ana])).toBe(ana);
  });

  it('homônimos: cada slug com sufixo abre o seu; o nome sem sufixo não adivinha', () => {
    const lista = [lucas1, lucas2, ana];
    const slugs = slugsDasPersonas(lista);
    expect(resolverPersona(slugs.get(lucas1.id), lista)).toBe(lucas1);
    expect(resolverPersona(slugs.get(lucas2.id), lista)).toBe(lucas2);
    expect(resolverPersona('lucas-almeida', lista)).toBeNull();
  });

  it('link com sufixo continua valendo depois que o homônimo some', () => {
    const slugAntigo = slugsDasPersonas([lucas1, lucas2]).get(lucas1.id);
    expect(slugAntigo).toMatch(/^lucas-almeida-/);
    expect(resolverPersona(slugAntigo, [lucas1, ana])).toBe(lucas1);
  });

  it('segmento que ninguém tem, ou vazio, dá null', () => {
    expect(resolverPersona('ninguem-assim', [lucas1, ana])).toBeNull();
    expect(resolverPersona('', [lucas1])).toBeNull();
    expect(resolverPersona(null, [lucas1])).toBeNull();
    expect(resolverPersona('ana-souza', [])).toBeNull();
  });

  it('um sufixo curto demais não vale como identificação', () => {
    expect(resolverPersona('ana-souza-a', [ana])).toBeNull();
  });
});

describe('homonimosDoSegmento', () => {
  const a = pessoa('ig-AAAA0001', 'Lucas Almeida');
  const b = pessoa('ig-BBBB0002', 'Lucas Almeida');
  const c = pessoa('ig-CCCC0003', 'Ana Souza');

  it('o nome puro de dois homônimos nomeia os dois; um nome único nomeia só um', () => {
    expect(homonimosDoSegmento('lucas-almeida', [a, b, c])).toEqual([a, b]);
    expect(homonimosDoSegmento('ana-souza', [a, b, c])).toEqual([c]);
    expect(homonimosDoSegmento('ninguem', [a, b, c])).toEqual([]);
    expect(homonimosDoSegmento(null, [a])).toEqual([]);
  });
});
