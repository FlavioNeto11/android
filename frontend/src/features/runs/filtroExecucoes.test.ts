import { describe, expect, it } from 'vitest';
import type { RunSummary } from '../../api/types';
import {
  contagemPorGrupo, encurtar, unirExecucoes, filtrarExecucoes, filtroLocalAtivo, grupoDoStatus, lerFiltroExecucoes, tituloCurto,
} from './filtroExecucoes';

function run(id: string, over: Partial<RunSummary> = {}): RunSummary {
  return {
    id, short_id: id.slice(-6), command: 'Abra o Instagram e abra o perfil @nasa.', status: 'completed', simulated: false,
    instance_ids: ['android-01'], instances_requested: 1, instances_used: 1, created_at: '2026-09-30T10:00:00Z',
    started_at: null, finished_at: null,
    counts: { succeeded: 1, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 },
    progress: 1, status_detail: null, ...over,
  };
}

const AGORA = Date.parse('2026-09-30T12:00:00Z');

describe('título curto da execução', () => {
  it('tira o app da abertura e mostra o que a execução faz', () => {
    expect(tituloCurto('No QA Messenger, envie "Bom dia" para QA-001 e para QA-002. Confirme que cada mensagem chegou.'))
      .toEqual({ titulo: 'Envie "Bom dia" para QA-001 e para QA-002', app: 'QA Messenger' });
    expect(tituloCurto('Abra o QA Messenger e vá até a tela de Perfil. Não altere nada.'))
      .toEqual({ titulo: 'Vá até a tela de Perfil', app: 'QA Messenger' });
    expect(tituloCurto('Abra o app Configurações do Android, role até "About emulated device" e confirme.'))
      .toEqual({ titulo: 'Role até "About emulated device" e confirme', app: 'Configurações do Android' });
    expect(tituloCurto('no Instagram, responda no direct da prima').app).toBe('Instagram');
  });

  it('tira as aberturas repetidas ("Nas instâncias selecionadas,", "Objetivo:")', () => {
    const t = tituloCurto('Nas instâncias selecionadas, abra o QA Messenger, entre na conversa com o contato de teste.');
    expect(t).toEqual({ titulo: 'Entre na conversa com o contato de teste', app: 'QA Messenger' });
    expect(tituloCurto('Objetivo: Com uma das personas ativas, curtir o post.').titulo).toBe('Com uma das personas ativas, curtir o post');
  });

  it('sem app reconhecível, fica a frase com maiúscula; linhas vazias e réguas no começo são puladas', () => {
    expect(tituloCurto('\n---\nmande uma das personas ativas acessar o perfil')).toEqual({
      titulo: 'Mande uma das personas ativas acessar o perfil', app: null,
    });
    // "Nas" + minúscula não é app.
    expect(tituloCurto('Nas horas vagas, leia o feed').app).toBeNull();
  });

  it('corta no limite sem partir palavra e nunca devolve vazio', () => {
    const t = tituloCurto(`No QA Messenger, ${'leia o nome do primeiro contato da lista de conversas sem abrir nem enviar nada '.repeat(2)}`, 40);
    expect(t.titulo.length).toBeLessThanOrEqual(40);
    expect(t.titulo.endsWith('…')).toBe(true);
    expect(t.titulo).not.toMatch(/\s…$/);
    expect(tituloCurto('   ').titulo).toBe('Execução sem objetivo escrito');
    expect(encurtar('curto', 10)).toBe('curto');
  });

  it('títulos de objetivos diferentes com a mesma abertura ficam diferentes logo no começo', () => {
    const a = tituloCurto('No QA Messenger, abra a tela de Perfil e salve.').titulo;
    const b = tituloCurto('No QA Messenger, envie "Entrega" para QA-003.').titulo;
    expect(a.slice(0, 10)).not.toBe(b.slice(0, 10));
  });
});

describe('filtros de execuções', () => {
  it('agrupa os status nos seis filtros', () => {
    expect(grupoDoStatus('completed')).toBe('concluida');
    expect(grupoDoStatus('completed_with_issues')).toBe('pendencia');
    expect(grupoDoStatus('needs_input')).toBe('pendencia');
    expect(grupoDoStatus('awaiting_person')).toBe('pendencia');    // 29.93: "Pede atenção", como o de antes
    expect(grupoDoStatus('failed')).toBe('falha');
    expect(grupoDoStatus('cancelled')).toBe('cancelada');
    for (const s of ['planning', 'running', 'paused', 'cancelling'] as const) expect(grupoDoStatus(s)).toBe('andamento');
  });

  it('D2: `planned` não é "em andamento": é um plano pronto para inspeção, que tem chip próprio e vale no link', () => {
    expect(grupoDoStatus('planned')).toBe('planejada');
    expect(lerFiltroExecucoes({ status: 'planejada' }).status).toBe('planejada');
    const runs = [run('r-000001', { status: 'planned' }), run('r-000002', { status: 'running' })];
    expect(contagemPorGrupo(runs, lerFiltroExecucoes({}), Date.parse('2026-09-30T12:00:00Z')))
      .toMatchObject({ todas: 2, andamento: 1, planejada: 1 });
  });

  it('lê a URL ignorando valores desconhecidos', () => {
    expect(lerFiltroExecucoes({ status: 'pendencia', periodo: '7d', q: 'nasa', aparelho: 'android-01' }))
      .toEqual({ q: 'nasa', status: 'pendencia', periodo: '7d', exploracao: false, aparelho: 'android-01', servidor: '' });
    expect(lerFiltroExecucoes({ status: 'xyz', periodo: '1ano' })).toMatchObject({ status: null, periodo: null });
    expect(filtroLocalAtivo(lerFiltroExecucoes({ aparelho: 'android-01' }))).toBe(false);
    expect(filtroLocalAtivo(lerFiltroExecucoes({ q: 'x' }))).toBe(true);
  });

  it('busca no objetivo sem acento e pelo código curto; combina com status e período', () => {
    const runs = [
      run('r-000001', { command: 'No Instagram, curta a publicação', status: 'completed' }),
      run('r-000002', { command: 'No Instagram, comente o elogio', status: 'completed_with_issues' }),
      run('r-000003', { command: 'Abra as Configurações', status: 'failed', created_at: '2026-09-20T10:00:00Z' }),
      run('r-abcdef', { command: 'No QA Messenger, envie', status: 'running', created_at: '2026-09-30T11:00:00Z' }),
    ];
    const f = (q: Record<string, string>) => filtrarExecucoes(runs, lerFiltroExecucoes(q), AGORA).map((r) => r.id);
    expect(f({ q: 'publicacao' })).toEqual(['r-000001']);
    expect(f({ q: 'ABCDEF' })).toEqual(['r-abcdef']);
    expect(f({ q: 'instagram', status: 'pendencia' })).toEqual(['r-000002']);
    expect(f({ periodo: '24h' })).toEqual(['r-000001', 'r-000002', 'r-abcdef']);
    expect(f({ periodo: '30d', status: 'falha' })).toEqual(['r-000003']);
    expect(f({ periodo: '7d', status: 'falha' })).toEqual([]);
  });

  it('as contagens dos chips respeitam os outros filtros, não o próprio status', () => {
    const runs = [
      run('a', { status: 'completed' }), run('b', { status: 'failed' }),
      run('c', { status: 'failed', created_at: '2026-09-01T00:00:00Z' }),
    ];
    const c = contagemPorGrupo(runs, lerFiltroExecucoes({ status: 'concluida', periodo: '7d' }), AGORA);
    expect(c).toMatchObject({ todas: 2, concluida: 1, falha: 1, pendencia: 0 });
  });
});

describe('histórico da tela', () => {
  it('une o que foi paginado com o que chegou ao vivo, sem repetir, a versão ao vivo ganhando, mais nova primeiro', () => {
    const antiga = run('r-1', { created_at: '2026-09-01T00:00:00Z', status: 'running' });
    const nova = run('r-2', { created_at: '2026-09-30T00:00:00Z' });
    const atualizada = run('r-1', { created_at: '2026-09-01T00:00:00Z', status: 'completed' });
    const u = unirExecucoes([nova, atualizada], [antiga, run('r-0', { created_at: '2026-08-01T00:00:00Z' })]);
    expect(u.map((r) => r.id)).toEqual(['r-2', 'r-1', 'r-0']);
    expect(u[1]!.status).toBe('completed');
  });
});

describe('31.306: execuções com etapa descoberta pela IA (etapas_exploratorias, v1.135)', () => {
  const COM = run('r-com', { etapas_exploratorias: 2 });
  const SEM = run('r-sem', { etapas_exploratorias: 0 });
  const ANTIGA = run('r-antiga');   // backend anterior ao 31.305: o campo não vem

  it('o link `exploracao=1` liga o filtro; qualquer outro valor deixa desligado', () => {
    expect(lerFiltroExecucoes({ exploracao: '1' }).exploracao).toBe(true);
    for (const v of ['0', 'true', '']) expect(lerFiltroExecucoes({ exploracao: v }).exploracao).toBe(false);
    expect(lerFiltroExecucoes({}).exploracao).toBe(false);
  });

  it('só passa quem tem contador maior que zero; sem o campo vale zero', () => {
    const f = lerFiltroExecucoes({ exploracao: '1' });
    expect(filtrarExecucoes([COM, SEM, ANTIGA], f, AGORA).map((r) => r.id)).toEqual(['r-com']);
    expect(filtrarExecucoes([COM, SEM, ANTIGA], lerFiltroExecucoes({}), AGORA)).toHaveLength(3);
  });

  it('é filtro da tela (pede o histórico inteiro) e soma com a situação', () => {
    expect(filtroLocalAtivo(lerFiltroExecucoes({ exploracao: '1' }))).toBe(true);
    const falhou = run('r-falhou', { etapas_exploratorias: 1, status: 'failed' });
    const f = lerFiltroExecucoes({ exploracao: '1', status: 'falha' });
    expect(filtrarExecucoes([COM, falhou], f, AGORA).map((r) => r.id)).toEqual(['r-falhou']);
  });
});
