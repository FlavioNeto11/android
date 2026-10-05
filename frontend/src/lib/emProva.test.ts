import { describe, expect, it } from 'vitest';
import { explicacaoEmProva, quemUsaEmProva, textoDaEsperaDaPessoa } from './emProva';

describe('30.81: o texto do ensinado em prova', () => {
  it('a explicação diferencia a persona que ensinou da gravação sem persona e nunca cita o id', () => {
    const com = explicacaoEmProva({ persona: 'ig-7' });
    expect(com).toContain('só vale para a persona que ensinou');
    expect(com).not.toContain('ig-7');
    expect(explicacaoEmProva({ persona: null })).toContain('não tinha persona: não vale em aparelho nenhum');
  });

  it('quem usa até a prova: a persona que ensinou, ou ninguém quando a gravação não tinha persona', () => {
    expect(quemUsaEmProva({ persona: 'ig-7' }).comando).toContain('só a persona que ensinou');
    expect(quemUsaEmProva({ persona: 'ig-7' }).receitas).toContain('só valem para a persona que ensinou');
    const sem = quemUsaEmProva({ persona: null });
    expect(sem.comando).toContain('não vale em aparelho nenhum');
    expect(sem.receitas).toContain('não valem em aparelho nenhum');
    expect(`${sem.comando} ${sem.receitas}`).not.toContain('só a persona que ensinou');
  });

  it('cada motivo do contrato tem frase própria, e um código novo cai na frase geral sem ecoar o código', () => {
    const codigos = ['classe_c', 'tentativas_esgotadas', 'efeito_real', 'sessao_ou_autenticacao', 'credencial', 'sem_origem', 'sem_caminho'];
    const frases = codigos.map(textoDaEsperaDaPessoa);
    expect(new Set(frases).size).toBe(codigos.length);
    for (const f of frases) expect(f).not.toMatch(/[a-z]+_[a-z]+/);
    const geral = textoDaEsperaDaPessoa('motivo_novo_do_backend');
    expect(geral).toContain('a decisão fica com a pessoa');
    expect(geral).not.toContain('motivo_novo');
  });
});
