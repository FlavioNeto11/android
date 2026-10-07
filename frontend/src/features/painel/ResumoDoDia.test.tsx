// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeBinding, makeInstance, makePersona, makeSession, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { lerOperacao, lerResumo } from '../operacao/modelo';
import { aparelhosDeContaReal, custoDasOperacoes, operacoesDoDia, perguntasAbertas, ultimoDeploy } from './contasDoResumo';
import { OPERACOES_COM_CUSTO, ResumoDoDia } from './ResumoDoDia';

/**
 * 31.215: o resumo do dia na página inicial: operações em curso e do dia (capacidade e custo), aparelhos de conta real com estado, custo
 * do dia, perguntas abertas ao dono e o que está no ar, só com rotas que já existem. Prova `simulated`: servidor falso.
 */

const AGORA = Date.parse('2026-10-07T15:00:00-03:00');
const hoje = (h: number) => new Date(AGORA - h * 3_600_000).toISOString();
const resumo = (id: string, status: string, h: number, capacidade: Record<string, number> = {}) =>
  ({ id, command: `Objetivo ${id}`, status, created_at: hoje(h), capacidade });

describe('operacoesDoDia', () => {
  const itens = [
    lerResumo(resumo('a', 'em_curso', 1, { solicitados: 5, concluidas: 2, bloqueadas: 1 }))!,
    lerResumo(resumo('b', 'concluida', 2, { solicitados: 3, concluidas: 3, bloqueadas: 0 }))!,
    lerResumo(resumo('c', 'em_curso', 30, { solicitados: 9 }))!,                 // ontem, ainda em curso
    lerResumo(resumo('d', 'concluida', 3))!,                                     // do dia, sem capacidade informada
  ];
  it('em curso (de qualquer dia) e do dia local, com a capacidade somada só do que informa', () => {
    const r = operacoesDoDia(itens, AGORA);
    expect(r.emCurso).toBe(2);
    expect(r.doDia.map((o) => o.id)).toEqual(['a', 'b', 'd']);
    expect([r.solicitados, r.concluidas, r.bloqueadas]).toEqual([8, 5, 1]);
  });
  it('nenhuma informa o número: null, nunca zero', () => {
    const r = operacoesDoDia([lerResumo(resumo('d', 'concluida', 1))!], AGORA);
    expect([r.solicitados, r.concluidas, r.bloqueadas]).toEqual([null, null, null]);
    expect(operacoesDoDia([], AGORA)).toMatchObject({ emCurso: 0, doDia: [], solicitados: null });
  });
});

describe('custoDasOperacoes', () => {
  const op = (custo: unknown) => lerOperacao({ id: 'x', command: 'x', alvos: [], ...(custo === undefined ? {} : { custo }) })!;
  it('soma o que informa; quem não informa fica fora e é contado, nunca zero', () => {
    expect(custoDasOperacoes([op({ total_usd: 0.1 }), op({ total_usd: 0.25 }), op(undefined)])).toEqual({ totalUsd: 0.35, semCusto: 1 });
    expect(custoDasOperacoes([op(undefined)])).toEqual({ totalUsd: null, semCusto: 1 });
    expect(custoDasOperacoes([])).toEqual({ totalUsd: null, semCusto: 0 });
  });
});

describe('aparelhosDeContaReal', () => {
  const instancias = { 'android-01': makeInstance(1, { state: 'online' }), 'android-03': makeInstance(3, { state: 'stopped' }) };
  it('só vínculo com app de verdade (não o QA), um por aparelho e app, na ordem do id, com estado e sessão', () => {
    const ps = [
      makePersona('p1', 'A', { devices: [makeBinding('android-03', { app_id: 'instagram', session: makeSession('session_ready') }), makeBinding('android-01', { app_id: 'qa-messenger' })] }),
      makePersona('p2', 'B', { devices: [makeBinding('android-01', { app_id: 'instagram', session: makeSession('session_ready') }), makeBinding('android-09', { app_id: null })] }),
    ];
    const r = aparelhosDeContaReal(ps, instancias);
    expect(r.map((a) => [a.instanceId, a.appId, a.estado])).toEqual([['android-01', 'instagram', 'online'], ['android-03', 'instagram', 'stopped']]);
    expect(r[0]!.sessao?.status).toBe('session_ready');
  });
  it('aparelho que o painel não conhece fica com estado null; duas personas no mesmo aparelho: vale a sessão que pede pessoa', () => {
    const ps = [
      makePersona('p1', 'A', { devices: [makeBinding('android-77', { session: makeSession('session_ready') })] }),
      makePersona('p2', 'B', { devices: [makeBinding('android-77', { session: makeSession('auth_challenge') })] }),
    ];
    const r = aparelhosDeContaReal(ps, instancias);
    expect(r).toHaveLength(1);
    expect(r[0]).toMatchObject({ estado: null });
    expect(r[0]!.sessao?.status).toBe('auth_challenge');
  });
});

describe('perguntasAbertas e ultimoDeploy', () => {
  it('só os avisos "pergunta" ainda não lidos; sem leitura é null (o bloco some)', () => {
    expect(perguntasAbertas([{ tipo: 'pergunta', lido_em: null }, { tipo: 'pergunta', lido_em: '2026-10-07T10:00:00Z' }, { tipo: 'encerramento', lido_em: null }])).toBe(1);
    expect(perguntasAbertas([])).toBe(0);
    expect(perguntasAbertas(null)).toBeNull();
  });
  it('o que está no ar: commit curto, versão e migração; sem a saúde, null; commit ausente (instalação por cópia) fica null', () => {
    expect(ultimoDeploy({ version: '0.9', commit: '42cba3cd1234567', migration: '127_x' })).toEqual({ versao: '0.9', commit: '42cba3cd', migracao: '127_x' });
    expect(ultimoDeploy({ version: '0.9', commit: null })).toEqual({ versao: '0.9', commit: null, migracao: null });
    expect(ultimoDeploy(null)).toBeNull();
  });
});

describe('a tela', () => {
  let root: Root;
  let container: HTMLElement;
  let backend: FakeBackend;

  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    const snap = makeSnapshot();
    const lista = [makeInstance(1, { state: 'online', worker_id: 'central' }), makeInstance(3, { state: 'stopped', worker_id: 'central' })];
    useAppStore.setState({
      ...initialDataState, hydrated: true, health: { ...snap.health, status: 'ok', problems: [], version: '1.2.3', commit: '0813f288abcdef', migration: '127_ok' },
      instances: Object.fromEntries(lista.map((i) => [i.id, i])), instanceOrder: lista.map((i) => i.id),
    });
    backend.on('GET', /^\/api\/personas$/, () => json([
      makePersona('p1', 'Ana', { devices: [makeBinding('android-01', { app_id: 'instagram', session: makeSession('session_ready') }), makeBinding('android-03', { app_id: 'instagram', session: makeSession('auth_challenge') })] }),
    ]));
    backend.on('GET', /^\/api\/usage$/, () => json({ scope: { run_id: null, days: 1 }, groups: [], total_usd: 1.2345, objectives_with_ai: 0, calls_per_objective: 0, usd_per_objective: 0 }));
    backend.on('GET', /^\/api\/pedidos\/avisos$/, () => json({ items: [{ id: 'a1', pedido_id: 'p', pedido_titulo: 't', ocorrencia_id: null, tipo: 'pergunta', nivel: 'info', mensagem: 'm', dados: {}, requer_pessoa: true, criado_em: '2026-10-07T10:00:00Z', lido_em: null }], nao_lidos: 1, proximo_cursor: null }));
    const agora = new Date().toISOString();
    const ontem = new Date(Date.now() - 30 * 3_600_000).toISOString();
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: [
      { id: 'op-2', command: 'Segunda', status: 'em_curso', created_at: agora, capacidade: { solicitados: 5, concluidas: 2, bloqueadas: 1 } },
      { id: 'op-1', command: 'Primeira', status: 'concluida', created_at: agora, capacidade: { solicitados: 3, concluidas: 3, bloqueadas: 0 } },
      { id: 'op-0', command: 'De ontem', status: 'em_curso', created_at: ontem, capacidade: { solicitados: 9 } },
    ] }));
    backend.on('GET', /^\/api\/operacoes\/op-2$/, () => json({ id: 'op-2', command: 'Segunda', status: 'em_curso', created_at: agora, alvos: [], custo: { total_usd: 0.4 } }));
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ id: 'op-1', command: 'Primeira', status: 'concluida', created_at: agora, alvos: [], custo: { total_usd: 0.1 } }));
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  const abrir = async () => { await act(async () => root.render(<ResumoDoDia />)); };
  const bloco = (n: string) => container.querySelector(`[data-bloco="${n}"]`) as HTMLElement;

  it('mostra os cinco blocos, cada um com o link para a tela dona', async () => {
    await abrir();
    await waitFor(() => expect(bloco('operacoes').querySelector('[data-em-curso]')).not.toBeNull());
    await waitFor(() => expect(bloco('custo').querySelector('[data-custo-do-dia]')).not.toBeNull());
    await waitFor(() => expect(bloco('perguntas')).not.toBeNull());
    await waitFor(() => expect(bloco('aparelhos').querySelector('li')).not.toBeNull());
    const href = (n: string) => bloco(n).querySelector('a.link, a[class*="link"]')!.getAttribute('href');
    expect([href('operacoes'), href('aparelhos'), href('custo'), href('perguntas'), href('deploy')]).toEqual(['#/operacoes', '#/infraestrutura', '#/diagnostico', '#/pendencias', '#/diagnostico']);
  });

  it('operações: em curso (de qualquer dia), do dia, capacidade e o custo somado dos detalhes lidos', async () => {
    await abrir();
    await waitFor(() => expect(bloco('operacoes').querySelector('[data-custo-das-operacoes]')).not.toBeNull());
    const t = text(bloco('operacoes'));
    expect(text(bloco('operacoes').querySelector('[data-em-curso]')!)).toBe('2 em andamento');
    expect(text(bloco('operacoes').querySelector('[data-do-dia]')!)).toBe('2 operações criadas hoje');
    expect(text(bloco('operacoes').querySelector('[data-capacidade]')!)).toBe('8 solicitados · 5 concluídas · 1 bloqueadas');
    expect(text(bloco('operacoes').querySelector('[data-custo-das-operacoes]')!)).toBe('Custo de IA US$ 0,5000');
    expect(t).toContain('Mais recente: Segunda');
    expect(backend.callsTo('GET', /^\/api\/operacoes\/op-0$/)).toHaveLength(0);               // a de ontem não entra no custo do dia
  });

  it('aparelhos de conta real: estado do aparelho e da sessão, sem nome de persona', async () => {
    await abrir();
    await waitFor(() => expect(bloco('aparelhos').querySelectorAll('li').length).toBe(2));
    const a1 = text(bloco('aparelhos').querySelector('[data-aparelho="android-01"]')!);
    expect(a1).toContain('online');
    expect(a1).toContain('instagram');
    const a3 = bloco('aparelhos').querySelector('[data-aparelho="android-03"]')!;
    expect(text(a3)).toContain('parado');
    expect(a3.querySelector('[data-precisa-de-pessoa]')).not.toBeNull();
    expect(text(bloco('aparelhos'))).not.toContain('Ana');
  });

  it('custo do dia, perguntas abertas e o que está no ar', async () => {
    await abrir();
    await waitFor(() => expect(bloco('perguntas')).not.toBeNull());
    expect(text(bloco('custo').querySelector('[data-custo-do-dia]')!)).toContain('US$ 1,2345');
    expect(text(bloco('perguntas').querySelector('[data-perguntas]')!)).toBe('1 pergunta aberta');
    expect(text(bloco('deploy').querySelector('[data-deploy]')!)).toBe('0813f288 · versão 1.2.3 · migração 127_ok');
    expect(text(bloco('deploy'))).toContain('A saúde não informa a hora do deploy.');
  });

  it('sem a rota das perguntas o bloco some (não mostra zero); sem o módulo de operações diz isso', async () => {
    backend.on('GET', /^\/api\/pedidos\/avisos$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    backend.on('GET', /^\/api\/operacoes$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    await abrir();
    await waitFor(() => expect(text(bloco('operacoes'))).toContain('O central ainda não oferece o módulo de operações.'));
    await waitFor(() => expect(bloco('custo').querySelector('[data-custo-do-dia]')).not.toBeNull());
    expect(bloco('perguntas')).toBeNull();
  });

  it('erro de leitura vira "não lido" no bloco, sem zero e sem derrubar os outros', async () => {
    backend.on('GET', /^\/api\/usage$/, () => apiError(500, 'erro_interno', 'banco indisponível'));
    backend.on('GET', /^\/api\/operacoes$/, () => apiError(500, 'erro_interno', 'banco indisponível'));
    await abrir();
    await waitFor(() => expect(text(bloco('operacoes'))).toContain('Não lido'));
    await waitFor(() => expect(text(bloco('custo'))).toContain('Não lido'));
    expect(bloco('custo').querySelector('[data-custo-do-dia]')).toBeNull();
    await waitFor(() => expect(bloco('aparelhos').querySelector('li')).not.toBeNull());
  });

  it('só lê o detalhe das primeiras operações do dia e diz quantas ficaram fora da soma', async () => {
    const agora = new Date().toISOString();
    const muitas = Array.from({ length: OPERACOES_COM_CUSTO + 2 }, (_, i) => ({ id: `m${i}`, command: `M${i}`, status: 'concluida', created_at: agora, capacidade: {} }));
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: muitas }));
    backend.on('GET', /^\/api\/operacoes\/m\d+$/, (c) => json({ id: c.path.split('/').pop(), command: 'x', status: 'concluida', created_at: agora, alvos: [], custo: { total_usd: 0.1 } }));
    await abrir();
    await waitFor(() => expect(bloco('operacoes').querySelector('[data-custo-das-operacoes]')).not.toBeNull());
    expect(backend.callsTo('GET', /^\/api\/operacoes\/m\d+$/)).toHaveLength(OPERACOES_COM_CUSTO);
    expect(text(bloco('operacoes').querySelector('[data-custo-das-operacoes]')!)).toBe('Custo de IA US$ 0,6000 (2 operações sem leitura fora da soma)');
  });
});
