import { describe, expect, it } from 'vitest';
import { estadoDaSessao } from './estadoSessao';

describe('estadoDaSessao', () => {
  it('"Não verificada" leva ao fluxo de verificação (guia Contas e acesso)', () => {
    const e = estadoDaSessao({ status: 'unknown', verified_at: null });
    expect(e.meta.label).toBe('Não verificada');
    expect(e.acao).toEqual({ rotulo: 'Verificar conta', guia: 'contas' });
    expect(e.confirmadaEm).toBeNull();
  });

  it('sem sessão nenhuma vale como "Não verificada"', () => {
    expect(estadoDaSessao(undefined).acao?.rotulo).toBe('Verificar conta');
    expect(estadoDaSessao(null).meta.label).toBe('Não verificada');
  });

  it('"Conectado" não pede nada e traz desde quando, se o dado existir', () => {
    expect(estadoDaSessao({ status: 'session_ready', verified_at: '2026-10-01T10:00:00Z' }))
      .toMatchObject({ acao: null, confirmadaEm: '2026-10-01T10:00:00Z' });
    expect(estadoDaSessao({ status: 'session_ready', verified_at: null }).confirmadaEm).toBeNull();
    expect(estadoDaSessao({ status: 'session_ready' }).meta.label).toBe('Conectado');
  });

  it('29.96: o `unknown` no teto é "Tela não reconhecida" e pede "Resolver", não "Verificar conta"', () => {
    const e = estadoDaSessao({ status: 'unknown', verified_at: null, unknown_at_cap: true });
    expect(e.meta.label).toBe('Tela não reconhecida');
    expect(e.acao).toEqual({ rotulo: 'Resolver', guia: 'contas' });
    expect(estadoDaSessao({ status: 'unknown', unknown_at_cap: false }).acao?.rotulo).toBe('Verificar conta');
  });

  it('o que só uma pessoa resolve vira "Resolver"; o resto, "Ver conta"', () => {
    for (const status of ['auth_challenge', 'needs_person', 'wrong_account']) {
      expect(estadoDaSessao({ status }).acao?.rotulo).toBe('Resolver');
    }
    for (const status of ['auth_required', 'logged_out', 'algo_novo']) {
      expect(estadoDaSessao({ status }).acao).toEqual({ rotulo: 'Ver conta', guia: 'contas' });
    }
  });
});
