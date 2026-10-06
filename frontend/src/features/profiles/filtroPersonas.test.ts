import { describe, expect, it } from 'vitest';
import type { PersonaDTO, SessionPhase } from '../../api/types';
import {
  contagemPorSituacao, estadoComposto, filtrarPersonas, filtroAtivo, lerFiltroPersonas, ordenarPersonas, queryDoFiltro,
  situacaoDe, textoSemResultado,
} from './filtroPersonas';

function fase(phase: SessionPhase): PersonaDTO['session_actions'] {
  const gate = { allowed: false, reason: null };
  return { phase, detail: '', connect: gate, verify: gate, logout: gate, inspect_app: gate } as unknown as PersonaDTO['session_actions'];
}

function p(id: string, over: Partial<PersonaDTO> = {}): PersonaDTO {
  return {
    id, name: id, summary: null, username: `${id.toLowerCase()}.9000`, display_name: null, first_name: null,
    last_name: null, birth_date: null, email: null, persona_id: id, persona_name: id, status: 'active',
    instance_id: null, locality: null, offline_policy: 'wait',
    credential: { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                  updated_at: null, last_used_at: null },
    session: { status: 'unknown', instance_id: null, observed_username: null, verified_at: null, detail: null, stale: false },
    last_verified_at: null, last_activity_at: null, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    ...over,
  } as PersonaDTO;
}

const vinculo = (instance_id: string, app_id: string) => ({
  instance_id, app_id, is_primary: true, state: null, worker_id: null, bound_at: null, session: null,
});

const BASE: PersonaDTO[] = [
  p('Sérgio', { instance_id: 'android-01', devices: [vinculo('android-01', 'instagram')], policy_group_id: 'g-op',
                   session_actions: fase('app_missing'), last_activity_at: '2026-09-29T10:00:00Z' }),
  p('Sueli', { status: 'blocked', policy_group_id: 'g-rec' }),
  p('Denise', { username: null }),
  p('Ravenna', { instance_id: 'android-06', devices: [vinculo('android-06', 'instagram'), vinculo('android-12', 'outlook')],
               policy_group_id: 'g-op', session_actions: fase('authenticated'), last_activity_at: '2026-09-30T10:00:00Z' }),
  p('Wagner', { status: 'disabled' }),
];

const ids = (l: PersonaDTO[]) => l.map((x) => x.id);

describe('situação da persona', () => {
  it('um código só por persona, com bloqueada e pausada ganhando de "sem conta"', () => {
    expect(BASE.map(situacaoDe)).toEqual(['atencao', 'bloqueada', 'sem-conta', 'ativa', 'pausada']);
    expect(situacaoDe(p('X', { status: 'blocked', username: null }))).toBe('bloqueada');
  });

  it('"Ativa" e "app não instalado" viram UM estado explicado, com ação que só leva aonde se resolve', () => {
    const e = estadoComposto(BASE[0]!);
    expect(e).toMatchObject({ rotulo: 'Ativa · app não instalado', tom: 'warning', acao: { rotulo: 'Instalar app', guia: 'aparelhos' } });
    expect(estadoComposto(BASE[1]!)).toMatchObject({ rotulo: 'Bloqueada pela plataforma', tom: 'danger' });
    expect(estadoComposto(BASE[1]!).acao).toBeUndefined();
    expect(estadoComposto(BASE[2]!).rotulo).toBe('Ativa · sem conta');
    expect(estadoComposto(BASE[3]!)).toMatchObject({ rotulo: 'Ativa · conectada', tom: 'success' });
  });
});

describe('filtros de personas', () => {
  it('lê a URL: `situacao=bloqueada` (o link da saúde do ambiente) filtra as bloqueadas pela plataforma', () => {
    const f = lerFiltroPersonas({ situacao: 'bloqueada' });
    expect(ids(filtrarPersonas(BASE, f))).toEqual(['Sueli']);
    expect(filtroAtivo(f)).toBe(true);
    // Valor desconhecido não esvazia a lista.
    expect(lerFiltroPersonas({ situacao: 'xyz', ordem: 'abc', visao: 'grade' }))
      .toMatchObject({ situacao: null, ordem: 'nome', visao: 'cards' });
  });

  it('D3: sem `visao` no link vale a preferência do navegador; com `visao` no link, o link manda', () => {
    expect(lerFiltroPersonas({}, 'tabela').visao).toBe('tabela');
    expect(lerFiltroPersonas({ visao: 'cards' }, 'tabela').visao).toBe('cards');
    expect(lerFiltroPersonas({ visao: 'tabela' }, 'cards').visao).toBe('tabela');
    expect(lerFiltroPersonas({ visao: 'grade' }, 'tabela').visao).toBe('tabela');
    expect(lerFiltroPersonas({}).visao).toBe('cards');
  });

  it('busca pelo @ (com ou sem "@") e pelo nome sem acento', () => {
    expect(ids(filtrarPersonas(BASE, lerFiltroPersonas({ q: '@ravenna.9' })))).toEqual(['Ravenna']);
    expect(ids(filtrarPersonas(BASE, lerFiltroPersonas({ q: 'sergio' })))).toEqual(['Sérgio']);
    expect(ids(filtrarPersonas(BASE, lerFiltroPersonas({ q: 'SUELI.9000' })))).toEqual(['Sueli']);
  });

  it('filtros combinados ("e"): situação, vínculo, grupo e app', () => {
    const f = (q: Record<string, string>) => ids(filtrarPersonas(BASE, lerFiltroPersonas(q)));
    expect(f({ situacao: 'ativa' })).toEqual(['Sérgio', 'Ravenna']);     // "ativa" inclui quem pede atenção
    expect(f({ situacao: 'atencao' })).toEqual(['Sérgio']);
    expect(f({ situacao: 'sem-conta' })).toEqual(['Denise']);
    expect(f({ vinculo: 'sem' })).toEqual(['Sueli', 'Denise', 'Wagner']);
    expect(f({ vinculo: 'com', app: 'outlook' })).toEqual(['Ravenna']);
    expect(f({ grupo: 'g-op', situacao: 'ativa', q: 'rav' })).toEqual(['Ravenna']);
    expect(f({ grupo: 'nenhum' })).toEqual(['Denise', 'Wagner']);
    expect(f({ situacao: 'bloqueada', vinculo: 'com' })).toEqual([]);
  });

  it('ordena por nome (pt-BR), por situação (o que pede alguém primeiro) e por última atividade', () => {
    expect(ids(ordenarPersonas(BASE, 'nome'))).toEqual(['Denise', 'Ravenna', 'Sérgio', 'Sueli', 'Wagner']);
    expect(ids(ordenarPersonas(BASE, 'situacao'))).toEqual(['Sueli', 'Sérgio', 'Denise', 'Wagner', 'Ravenna']);
    expect(ids(ordenarPersonas(BASE, 'atividade')).slice(0, 2)).toEqual(['Ravenna', 'Sérgio']);
    // Não muda a lista original.
    expect(ids(BASE)[0]).toBe('Sérgio');
  });

  it('contagem dos chips com os outros filtros aplicados', () => {
    const c = contagemPorSituacao(BASE, lerFiltroPersonas({ situacao: 'bloqueada', grupo: 'g-op' }));
    expect(c).toMatchObject({ todas: 2, ativa: 2, atencao: 1, bloqueada: 0 });
  });

  it('a URL fica curta: padrão sai do link (exceto a visão), e o texto do vazio diz o filtro', () => {
    // A visão vai SEMPRE explícita (D3): o padrão dela é a preferência de cada navegador, e um link sem `visao` abriria
    // na preferência de quem recebe, não na de quem mandou.
    expect(queryDoFiltro({ ordem: 'nome', visao: 'cards', q: '' })).toEqual({ ordem: undefined, visao: 'cards', q: undefined });
    expect(queryDoFiltro({ ordem: 'atividade', visao: 'tabela' })).toEqual({ ordem: 'atividade', visao: 'tabela' });
    expect(textoSemResultado(lerFiltroPersonas({ situacao: 'bloqueada' }))).toBe('Nenhuma persona bloqueada.');
  });
});
