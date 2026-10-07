// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { compararRelatorios, NAO_MEDIDO } from './comparacao';
import { OperacaoPage } from './OperacaoPage';
import { relatorioDoServidor } from './relatorioDoServidor';

/**
 * 31.204: duas operações lado a lado (custo, latência, critérios, identidades), a partir do relatório v1.111 de cada uma. Prova `simulated`:
 * servidor falso no formato do contrato. O número que um lado não mediu fica "não medido": a diferença nunca usa zero inventado.
 */

const agente = (n: number, extra: Record<string, unknown> = {}) => ({
  profile_id: `p${n}`, persona: `Persona 0${n}`, aparelho: `android-0${n}`, estado: 'concluido', estagio: 'resultado_verificado', parou_em: null, motivo: null,
  estagios: [], texto: `Texto ${n}.`, acao_final: { tipo: 'CREATE_COMMENT', verificada: 'sim' }, custo_usd: 0.1, duracao_ms: 90_000, espera_do_liberar_ms: null, ...extra,
});
const base = (id: string, comando: string) => ({
  gerado_em: '2026-10-07T20:00:00Z',
  operacao: { id, comando, app_id: 'com.instagram.android', acao_final: 'preparar', status: 'concluida', criada_em: '2026-10-07T18:00:00Z', encerrada_em: '2026-10-07T19:00:00Z', fontes: [], fontes_da_pesquisa: [] },
  capacidade: { solicitados: 5, contas_existentes: 3, sessoes_validas: 3, contas_disponiveis: 3, concluidas: 3, bloqueadas: 2, em_curso: 0, motivos: [] },
  identidades: { solicitadas: 5, executam_hoje: 3, deficit: 2, nao_executam: [] },
  agentes: [agente(1)],
  falhas_por_motivo: [],
  criterios: [
    { id: '1', nome: 'Pesquisa externa', estado: 'provado_real', nesta_operacao: 'sim', evidencia: null },
    { id: '16', nome: 'Custo por peça', estado: 'implementado', nesta_operacao: 'nao_medido', evidencia: null },
  ],
  aprendizado: { gerado_em: null, avisos: [], perguntas: [], nao_coberto: [] },
  latencia: { por_estagio: { conta: { n: 3, p50_ms: 2000, p95_ms: 2500, max_ms: 3000 } }, duracao_mediana_ms: 90_000, mais_lento: { profile_id: 'p1', duracao_ms: 120_000 } },
  custo: { pesquisa_usd: 0.07, alvos_usd: 0.3, total_usd: 0.37, teto_usd: 4.5, por_peca_usd: 0.185 },
});
const A = base('op-a', 'Comentar no post da loja');
const B = {
  ...base('op-b', 'Comentar no post da loja (segunda rodada)'),
  capacidade: { solicitados: 5, contas_existentes: 5, sessoes_validas: 5, contas_disponiveis: 5, concluidas: 5, bloqueadas: 0, em_curso: 0, motivos: [] },
  identidades: { solicitadas: 5, executam_hoje: 5, deficit: 0, nao_executam: [] },
  criterios: [
    { id: '1', nome: 'Pesquisa externa', estado: 'provado_real', nesta_operacao: 'sim', evidencia: null },
    { id: '16', nome: 'Custo por peça', estado: 'implementado', nesta_operacao: 'sim', evidencia: null },
  ],
  latencia: { por_estagio: { conta: { n: 5, p50_ms: 1000, p95_ms: 1500, max_ms: 2000 }, sessao: { n: 5, p50_ms: 4000, p95_ms: 5000, max_ms: 6000 } }, duracao_mediana_ms: 60_000, mais_lento: null },
  custo: { pesquisa_usd: 0.07, alvos_usd: 0.25, total_usd: 0.32, teto_usd: 4.5, por_peca_usd: null },
};
const lido = (v: unknown) => relatorioDoServidor(v)!;
const linha = (ls: { rotulo: string }[], rotulo: string) => ls.find((l) => l.rotulo === rotulo)!;

describe('compararRelatorios', () => {
  const c = compararRelatorios(lido(A), lido(B));

  it('custo e agentes: lado a lado, com B menos A onde os dois mediram', () => {
    expect(linha(c.custo, 'Total')).toMatchObject({ a: 'US$ 0,3700', b: 'US$ 0,3200', delta: '-US$ 0,0500', diferente: true });
    expect(linha(c.custo, 'Pesquisa externa')).toMatchObject({ delta: 'igual', diferente: false });
    expect(linha(c.agentes, 'Concluídas')).toMatchObject({ a: '3', b: '5', delta: '+2' });
    expect(linha(c.agentes, 'Identidades que faltam')).toMatchObject({ a: '2', b: '0', delta: '-2' });
  });

  it('o que um lado não mediu fica "não medido" e sem diferença: nunca zero', () => {
    expect(linha(c.custo, 'Por peça (ação executada e verificada)')).toMatchObject({ a: 'US$ 0,1850', b: NAO_MEDIDO, delta: null, diferente: true });
    expect(linha(c.latencia, 'Duração do agente mais lento')).toMatchObject({ b: NAO_MEDIDO, delta: null });
    expect(linha(c.porEstagio, 'Sessão')).toMatchObject({ a: NAO_MEDIDO, delta: null });
  });

  it('latência por estágio na ordem do pipeline, só os medidos em algum dos lados', () => {
    expect(c.porEstagio.map((l) => l.rotulo)).toEqual(['Conta', 'Sessão']);
    expect(linha(c.porEstagio, 'Conta')).toMatchObject({ a: '2 s', b: '1 s', delta: '-1 s' });
    expect(linha(c.latencia, 'Duração mediana por agente')).toMatchObject({ delta: '-30 s' });
  });

  it('critérios: um por linha, com a diferença marcada; sem os critérios de um lado, nenhuma tabela', () => {
    expect(c.criterios!.map((k) => [k.id, k.diferente])).toEqual([['1', false], ['16', true]]);
    expect(c.criterios![1]).toMatchObject({ a: `implementado · nesta operação: ${NAO_MEDIDO}`, b: 'implementado · nesta operação: sim' });
    expect(compararRelatorios({ ...lido(A), criterios: null }, lido(B)).criterios).toBeNull();
  });

  it('falhas por motivo: a união dos motivos, zero só quando o lado mediu e não teve', () => {
    const comFalha = { ...lido(A), falhas_por_motivo: [{ motivo: 'sem conta', parou_em: 'Conta', agentes: 2 }] };
    const f = compararRelatorios(comFalha, lido(B)).falhas;
    expect(f).toEqual([{ rotulo: 'sem conta', a: '2', b: '0', delta: '-2', diferente: true }]);
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
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [], query: {} } });
  });

  const resumo = (id: string, command: string) => ({ id, command, status: 'concluida', created_at: '2026-10-07T18:00:00Z', capacidade: { solicitados: 5, concluidas: 3, bloqueadas: 2 } });
  const naRota = async (segmentos: string[], query: Record<string, string> = {}) => {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos, query } });
    await act(async () => root.render(<OperacaoPage />));
  };
  const comparar = () => byRole('button', /Comparar as marcadas/, container) as HTMLButtonElement;

  it('na lista: marca duas, a terceira trava, o botão diz o que falta e leva à comparação com os dois ids', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: [resumo('op-a', 'Primeira'), resumo('op-b', 'Segunda'), resumo('op-c', 'Terceira')] }));
    await naRota([]);
    await waitFor(() => expect(container.querySelectorAll('input[type="checkbox"]')).toHaveLength(3));
    expect(comparar().getAttribute('aria-disabled')).toBe('true');
    expect(text(comparar())).toContain('Marque 2 operações');
    const caixa = (n: string) => container.querySelector(`input[aria-label="Comparar: ${n}"]`) as HTMLInputElement;
    await click(caixa('Primeira'));
    expect(text(comparar())).toContain('1 marcada');
    await click(caixa('Terceira'));
    expect(comparar().getAttribute('aria-disabled')).toBeNull();
    expect(caixa('Segunda').disabled).toBe(true);                                            // só duas
    await click(comparar());
    const rota = useUiStore.getState().rota;
    expect([rota.tela, rota.segmentos, rota.query]).toEqual(['operacoes', ['comparar'], { a: 'op-a', b: 'op-c' }]);
  });

  it('a comparação lê o relatório do central das duas e mostra custo, latência, critérios e identidades lado a lado', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-a\/relatorio$/, () => json(A));
    backend.on('GET', /^\/api\/operacoes\/op-b\/relatorio$/, () => json(B));
    await naRota(['comparar'], { a: 'op-a', b: 'op-b' });
    await waitFor(() => expect(container.querySelector('tr[data-linha="Total"]')).not.toBeNull());
    const total = container.querySelector('tr[data-linha="Total"]')!;
    expect(text(total)).toContain('US$ 0,3700');
    expect(text(total.querySelector('[data-delta]')!)).toBe('-US$ 0,0500');
    expect(text(container.querySelector('tr[data-linha="Identidades que executam hoje"] [data-delta]')!)).toBe('+2');
    expect(text(container.querySelector('tr[data-linha="Por peça (ação executada e verificada)"]')!)).toContain(NAO_MEDIDO);
    expect(container.querySelector('tr[data-criterio="16"]')!.getAttribute('data-diferente')).toBe('sim');
    expect(container.querySelector('tr[data-criterio="1"]')!.getAttribute('data-diferente')).toBe('nao');
    expect(container.querySelector('[role="status"]')).toBeNull();                          // os dois do central: sem aviso de reserva
    expect(backend.callsTo('GET', /operacoes\/op-a$/)).toHaveLength(0);                     // nem precisou do detalhe
  });

  it('sem a rota do relatório numa das duas, monta a dela no painel, AVISA e não mostra os critérios', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-a\/relatorio$/, () => json(A));
    backend.on('GET', /^\/api\/operacoes\/op-b\/relatorio$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    backend.on('GET', /^\/api\/operacoes\/op-b$/, () => json({
      id: 'op-b', command: 'Segunda', status: 'concluida', max_usd: 4.5, custo: { total_usd: 0.3, alvos_usd: 0.3 },
      alvos: [{ run_id: 'r1', profile_id: 'p1', persona_nome: 'Persona 01', estado: 'concluido', estagio: 'resultado_verificado', estagios: [] }],
    }));
    await naRota(['comparar'], { a: 'op-a', b: 'op-b' });
    await waitFor(() => expect(container.querySelector('tr[data-linha="Total"]')).not.toBeNull());
    expect(text(container.querySelector('[role="status"]')!)).toContain('Parte da comparação foi montada no painel');
    expect(container.querySelector('tr[data-criterio]')).toBeNull();
    expect(container.querySelector('[data-sem-criterios]')).not.toBeNull();
    expect(text(container.querySelector('tr[data-linha="Identidades que executam hoje"]')!)).toContain(NAO_MEDIDO);
  });

  it('a mesma operação duas vezes, ou só uma, não chama nada; erro vira estado de erro com nova tentativa', async () => {
    await naRota(['comparar'], { a: 'op-a', b: 'op-a' });
    expect(text(container)).toContain('mesma operação');
    await act(async () => root.unmount());
    root = createRoot(container);
    await naRota(['comparar'], { a: 'op-a' });
    expect(text(container)).toContain('Escolha duas operações');
    expect(backend.callsTo('GET', /operacoes/)).toHaveLength(0);
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /^\/api\/operacoes\/op-a\/relatorio$/, () => apiError(500, 'erro_interno', 'banco indisponível'));
    backend.on('GET', /^\/api\/operacoes\/op-b\/relatorio$/, () => json(B));
    await naRota(['comparar'], { a: 'op-a', b: 'op-b' });
    await waitFor(() => expect(text(container)).toContain('Tentar de novo'));
    expect(container.querySelector('tr[data-linha="Total"]')).toBeNull();
  });
});
