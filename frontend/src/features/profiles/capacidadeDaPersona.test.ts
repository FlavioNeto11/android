import { describe, expect, it } from 'vitest';
import type { PersonaDTO } from '../../api/types';
import { capacidadeDe, casaCapacidade, CAPACIDADES } from './capacidadeDaPersona';
import { contagemPorCapacidade, FILTRO_VAZIO, filtrarPersonas, lerFiltroPersonas, queryDoFiltro, textoSemResultado } from './filtroPersonas';

/** Uma persona pronta: ativa, com @, senha guardada e consentida, aparelho e sessão pronta. */
function pronta(over: Partial<PersonaDTO> = {}): PersonaDTO {
  return {
    id: 'ig-1', name: 'Pessoa Um', summary: null, username: 'pessoa.um', display_name: 'Pessoa Um', first_name: 'Pessoa', last_name: 'Um',
    birth_date: null, email: null, persona_id: 'ig-1', persona_name: 'Pessoa Um', status: 'active', instance_id: 'android-01', accounts_count: 1,
    locality: null, offline_policy: 'wait', policy_group_name: 'Liberado', policy_group_id: 'g1',
    credential: { configured: true, login_identifier: 'segredo@exemplo.com', status: 'active', failed_attempts: 0, blocked_until: null, updated_at: null,
                  last_used_at: '2026-10-07T10:00:00Z', consent_at: '2026-10-06T09:00:00Z' },
    session: { status: 'session_ready', instance_id: 'android-01', observed_username: 'pessoa.um', verified_at: '2026-10-07T09:00:00Z', detail: null, stale: false },
    last_verified_at: null, last_activity_at: '2026-10-07T08:00:00Z', created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-01T10:00:00Z',
    ...over,
  } as PersonaDTO;
}
const credencial = (over: Record<string, unknown>) => ({ ...pronta().credential!, ...over });

describe('a capacidade da persona (31.255)', () => {
  it('pronta: conta, aparelho, senha consentida e sessão pronta; o último uso é o da senha', () => {
    expect(capacidadeDe(pronta())).toEqual({
      conta: true, aparelho: true, cofre: 'pronto', sessao: 'pronta', grupo: 'Liberado', ultimoUso: '2026-10-07T10:00:00Z', bloqueio: null, passo: null,
    });
  });

  it('o primeiro que falta, na ordem: inativa, conta, senha, consentimento, aparelho, sessão; com o atalho que leva à guia certa', () => {
    expect(capacidadeDe(pronta({ status: 'blocked' })).bloqueio).toBe('inativa');
    expect(capacidadeDe(pronta({ username: null }))).toMatchObject({ conta: false, cofre: 'sem_conta', sessao: 'sem_sessao', bloqueio: 'sem_conta', passo: { rotulo: 'Adicionar conta', guia: 'contas' } });
    expect(capacidadeDe(pronta({ credential: credencial({ configured: false }) }))).toMatchObject({ cofre: 'sem_senha', bloqueio: 'sem_senha', passo: { rotulo: 'Guardar senha', guia: 'contas' } });
    expect(capacidadeDe(pronta({ credential: credencial({ consent_at: null }) }))).toMatchObject({ cofre: 'sem_consentimento', bloqueio: 'sem_consentimento', passo: { rotulo: 'Dar consentimento', guia: 'contas' } });
    expect(capacidadeDe(pronta({ instance_id: null }))).toMatchObject({ aparelho: false, bloqueio: 'sem_aparelho', passo: { rotulo: 'Vincular aparelho', guia: 'aparelhos' } });
    // senha e consentimento têm prioridade sobre o aparelho; a conta, sobre tudo
    expect(capacidadeDe(pronta({ username: null, instance_id: null })).bloqueio).toBe('sem_conta');
    expect(capacidadeDe(pronta({ instance_id: null, credential: credencial({ configured: false }) })).bloqueio).toBe('sem_senha');
  });

  it('a sessão: pronta, vencida (verificada há tempo demais), desconhecida, pede uma pessoa e deslogada; sem atalho no cartão', () => {
    const com = (status: string, extra: Record<string, unknown> = {}) => capacidadeDe(pronta({ session: { ...pronta().session, status, ...extra } as PersonaDTO['session'] }));
    expect(com('session_ready').sessao).toBe('pronta');
    expect(com('session_ready', { stale: true })).toMatchObject({ sessao: 'vencida', bloqueio: 'sessao', passo: null });
    expect(com('unknown').sessao).toBe('desconhecida');
    expect(com('unknown', { unknown_at_cap: true }).sessao).toBe('precisa_de_pessoa');
    expect(com('auth_challenge').sessao).toBe('precisa_de_pessoa');
    expect(com('auth_required').sessao).toBe('deslogada');
  });

  it('o consentimento que o central não manda NÃO vira "sem consentimento"; bloqueio por tentativas aparece como tal', () => {
    const { consent_at: _c, ...semConsent } = pronta().credential!;
    expect(capacidadeDe(pronta({ credential: semConsent as PersonaDTO['credential'] }))).toMatchObject({ cofre: 'sem_informacao', bloqueio: null });
    expect(capacidadeDe(pronta({ credential: credencial({ blocked_until: '2999-01-01T00:00:00Z' }) })).cofre).toBe('bloqueado');
    expect(capacidadeDe(pronta({ credential: credencial({ blocked_until: '2000-01-01T00:00:00Z' }) })).cofre).toBe('pronto');
  });

  it('o resultado não carrega o identificador de login nem a senha', () => {
    expect(JSON.stringify(capacidadeDe(pronta()))).not.toMatch(/segredo|exemplo\.com|password|senha"/i);
  });

  it('o filtro "capacidade": cada recorte pega só quem tem aquele primeiro bloqueio; vem e vai pelo link e conta com os outros filtros', () => {
    const lista = [
      pronta({ id: 'a' }), pronta({ id: 'b', username: null }), pronta({ id: 'c', username: null }),
      pronta({ id: 'd', credential: credencial({ configured: false }) }), pronta({ id: 'e', instance_id: null }),
    ];
    const f = lerFiltroPersonas({ capacidade: 'sem-conta' });
    expect(f.capacidade).toBe('sem-conta');
    expect(filtrarPersonas(lista, f).map((p) => p.id)).toEqual(['b', 'c']);
    expect(lerFiltroPersonas({ capacidade: 'invalido' }).capacidade).toBeNull();
    expect(queryDoFiltro({ capacidade: 'sem-senha' })).toEqual({ capacidade: 'sem-senha' });
    expect(queryDoFiltro({ capacidade: null }).capacidade).toBeUndefined();
    const n = contagemPorCapacidade(lista, FILTRO_VAZIO);
    expect(n).toMatchObject({ todas: 5, prontas: 1, 'sem-conta': 2, 'sem-senha': 1, 'sem-aparelho': 1, 'sem-consentimento': 0, sessao: 0 });
    expect(CAPACIDADES.every((r) => lista.filter((p) => casaCapacidade(p, r)).length === n[r])).toBe(true);
    expect(textoSemResultado({ ...FILTRO_VAZIO, capacidade: 'sem-senha' })).toBe('Nenhuma persona sem senha guardada.');
  });
});
