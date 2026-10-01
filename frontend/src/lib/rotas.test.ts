import { describe, expect, it } from 'vitest';
import { hashDaRota, hashDe, parseHash } from './rotas';

describe('rotas', () => {
  it('lê tela simples e ignora barra final', () => {
    expect(parseHash('#/painel')).toEqual({ tela: 'painel', segmentos: [], query: {} });
    expect(parseHash('#/painel/')?.segmentos).toEqual([]);
  });

  it('lê objeto, aba e query', () => {
    expect(parseHash('#/personas/ana%20b/memoria?x=1')).toEqual({
      tela: 'personas', segmentos: ['ana b', 'memoria'], query: { x: '1' },
    });
    expect(parseHash('#/execucoes/r1?aba=linha-do-tempo')?.query).toEqual({ aba: 'linha-do-tempo' });
  });

  it('#/perfis vira personas e marca legado', () => {
    expect(parseHash('#/perfis/abc')).toEqual({ tela: 'personas', segmentos: ['abc'], query: {}, legado: true });
  });

  it('hash desconhecido ou vazio devolve null', () => {
    expect(parseHash('')).toBeNull();
    expect(parseHash('#/nada')).toBeNull();
    expect(parseHash('#/')).toBeNull();
  });

  it('monta hash canônico, sem valores vazios e com query ordenada', () => {
    expect(hashDe('personas', { query: { situacao: 'bloqueada', q: '', ordem: 'nome' } })).toBe('#/personas?ordem=nome&situacao=bloqueada');
    expect(hashDe('personas', { segmentos: ['a/b'] })).toBe('#/personas/a%2Fb');
  });

  it('ida e volta preserva a rota', () => {
    const r = parseHash('#/aplicativos/com.x?aba=versoes')!;
    expect(parseHash(hashDaRota(r))).toEqual(r);
  });
});
