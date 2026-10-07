// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { lerOperacao } from './modelo';
import { OperacaoPage } from './OperacaoPage';
import { relatorioEmMarkdown } from './relatorio';
import { relatorioDoServidor } from './relatorioDoServidor';

/**
 * 31.197: a tela Operação lê o relatório que o CENTRAL monta (`GET /api/operacoes/{id}/relatorio`, adendo v1.111, Jev 31.195) e mantém a
 * montagem do painel só como reserva. Prova `simulated`: servidor falso no formato do RASCUNHO do Jev (o adendo ainda não foi publicado).
 */

const hora = (m: number, s: number) => `2026-10-07T18:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}Z`;
const agente = (n: number, extra: Record<string, unknown> = {}) => ({
  profile_id: `p${n}`, persona: `Persona 0${n}`, aparelho: `android-0${n}`, estado: 'concluido', estagio: 'resultado_verificado', parou_em: null, motivo: null,
  estagios: [{ estagio: 'persona', em: hora(0, 0), etapa_ms: 0 }, { estagio: 'conta', em: hora(0, 2), etapa_ms: 2000 }, { estagio: 'sessao', em: null, etapa_ms: null }],
  texto: `Texto ${n}.`, acao_final: { tipo: 'CREATE_COMMENT', verificada: 'sim' }, custo_usd: 0.1, duracao_ms: 90_000, espera_do_liberar_ms: 12_000, ...extra,
});
const RELATORIO = {
  gerado_em: '2026-10-07T20:00:00Z',
  operacao: {
    id: 'op-1', comando: 'Comentar no post da loja', app_id: 'com.instagram.android', acao_final: 'preparar', status: 'concluida_com_bloqueios',
    criada_em: '2026-10-07T18:00:00Z', encerrada_em: '2026-10-07T19:00:00Z', assunto: 'A embalagem nova.', fontes: ['https://exemplo.com.br/a'], fontes_da_pesquisa: ['https://exemplo.com.br/b'],
  },
  capacidade: { solicitados: 5, contas_existentes: 3, sessoes_validas: 3, contas_disponiveis: 3, concluidas: 3, bloqueadas: 2, em_curso: 0, motivos: [{ motivo: 'sem conta', n: 2 }] },
  identidades: { solicitadas: 5, executam_hoje: 3, nao_executam: [{ motivo: 'sem conta', n: 2 }] },
  agentes: [
    agente(1), agente(2, { texto: 'texto  1.' }),
    agente(3, { estado: 'bloqueado', estagio: 'conta', parou_em: 'conta', motivo: 'conta(s) da frota já mexeram com @fulano', texto: null, acao_final: null, custo_usd: null, duracao_ms: null, espera_do_liberar_ms: null }),
  ],
  falhas_por_motivo: [{ motivo: 'conta(s) da frota já mexeram com @fulano', parou_em: 'conta', agentes: 1 }],
  criterios: [
    { id: '1', nome: 'Pesquisa externa', estado: 'provado_real', nesta_operacao: 'sim', evidencia: 'op-20261006194323' },
    { id: '2b', nome: 'Texto sem repetição', estado: 'testado_em_simulacao', nesta_operacao: 'nao_medido', evidencia: null },
    { id: '16', nome: 'Custo por peça', estado: 'implementado', nesta_operacao: 'nao', evidencia: null },
  ],
  aprendizado: { gerado_em: '2026-10-07T20:00:00Z', avisos: [], perguntas: [], nao_coberto: [] },
  latencia: {
    por_estagio: { conta: { n: 3, p50_ms: 2000, p95_ms: 2500, max_ms: 3000 }, persona: { n: 3, p50_ms: 0, p95_ms: 0, max_ms: 0 } },
    duracao_mediana_ms: 90_000, mais_lento: { profile_id: 'p2', duracao_ms: 120_000 },
  },
  custo: { pesquisa_usd: 0.07, alvos_usd: 0.3, total_usd: 0.37, teto_usd: 4.5, por_peca_usd: 0.185 },
};

describe('relatorioDoServidor (o leitor do rascunho v1.111)', () => {
  const r = relatorioDoServidor(RELATORIO)!;

  it('vira o relatório do painel: operação, capacidade, custo com o por peça, identidades e fonte', () => {
    expect(r.fonte).toBe('servidor');
    expect(r.operacao).toMatchObject({ id: 'op-1', comando: 'Comentar no post da loja', status: 'Concluída com bloqueios', fontes_da_pesquisa: ['https://exemplo.com.br/b'] });
    expect(r.custo).toEqual({ pesquisa_usd: 0.07, alvos_usd: 0.3, total_usd: 0.37, teto_usd: 4.5, por_peca_usd: 0.185 });
    expect(r.identidades).toEqual({ solicitadas: 5, executam_hoje: 3, nao_executam: [{ motivo: 'sem conta', n: 2 }] });
    expect(r.capacidade.motivos).toEqual([{ motivo: 'sem conta', n: 2 }]);
  });

  it('o agente: 14 estágios com a hora e a etapa; o que o central não traz (conhecimento, evidência) é não informado, nunca "nenhum"', () => {
    const a = r.agentes[0]!;
    expect(a.estagios).toHaveLength(14);
    expect(a.estagios[1]).toMatchObject({ estagio: 'conta', em: hora(0, 2), alcancado: true, etapa_ms: 2000 });
    expect(a.estagios[2]).toMatchObject({ estagio: 'sessao', em: null, alcancado: true, etapa_ms: null });   // alcançado, hora ilegível
    expect(a.estagios[3]).toMatchObject({ alcancado: false });
    expect(a.conhecimento_ids).toBeNull();
    expect(a.evidencia_id).toBeNull();
    expect(a.acao_final).toEqual({ tipo: 'CREATE_COMMENT', verificada: 'sim', evidencia_id: null });
    expect(a).toMatchObject({ duracao_ms: 90_000, espera_do_liberar_ms: 12_000, custo_usd: 0.1 });
  });

  it('o @ de conta sai do motivo e da falha; o estado e a parada ficam em palavras; null não vira zero', () => {
    const b = r.agentes[2]!;
    expect(b).toMatchObject({ estado: 'Bloqueado', parou_em: 'Conta', motivo: 'conta(s) da frota já mexeram com @[omitido]', texto: null, acao_final: null, custo_usd: null, duracao_ms: null });
    expect(r.falhas_por_motivo).toEqual([{ motivo: 'conta(s) da frota já mexeram com @[omitido]', parou_em: 'Conta', agentes: 1 }]);
  });

  it('os textos irmãos saem dos agentes (o mesmo texto, sem diferença de caixa nem espaço, conta como repetido)', () => {
    const x = relatorioDoServidor({ ...RELATORIO, agentes: [agente(1, { texto: 'Igual.' }), agente(2, { texto: 'igual.' })] })!;
    expect(x.textos).toMatchObject({ total: 2, distintos: 1 });
    expect(x.textos.repetidos[0]!.agentes).toEqual(['Persona 01', 'Persona 02']);
  });

  it('critérios: o estado de 3 vias fica como veio; vocabulário desconhecido vira não medido / não informado', () => {
    expect(r.criterios!.map((c) => [c.id, c.estado, c.nesta_operacao])).toEqual([['1', 'provado_real', 'sim'], ['2b', 'testado_em_simulacao', 'nao_medido'], ['16', 'implementado', 'nao']]);
    const t = relatorioDoServidor({ ...RELATORIO, criterios: [{ id: 3, nome: 'X', estado: 'inventado', nesta_operacao: 'talvez' }, { nome: 'sem id' }] })!;
    expect(t.criterios).toEqual([{ id: '3', nome: 'X', estado: null, nesta_operacao: 'nao_medido', evidencia: null }]);
    expect(relatorioDoServidor({ ...RELATORIO, criterios: undefined })!.criterios).toBeNull();
  });

  it('latência: por estágio na ordem do pipeline, mediana por agente e o mais lento pelo rótulo (nunca pelo id)', () => {
    expect(r.latencia!.por_estagio.map((e) => [e.estagio, e.n, e.p50_ms, e.p95_ms, e.max_ms])).toEqual([['persona', 3, 0, 0, 0], ['conta', 3, 2000, 2500, 3000]]);
    expect(r.latencia!.duracao_mediana_ms).toBe(90_000);
    expect(r.latencia!.mais_lento).toEqual({ agente: 'Persona 02', duracao_ms: 120_000 });
    expect(relatorioDoServidor({ ...RELATORIO, latencia: { por_estagio: {}, duracao_mediana_ms: null, mais_lento: null } })!.latencia).toMatchObject({ duracao_mediana_ms: null, mais_lento: null });
  });

  it('aprendizado: o corpo do v1.96 vira as 10 perguntas; {disponivel:false} diz o motivo, nunca "nada aprendido"', () => {
    expect(r.aprendizado.disponivel).toBe(true);
    expect(r.aprendizado.perguntas).toHaveLength(10);
    const nd = relatorioDoServidor({ ...RELATORIO, aprendizado: { disponivel: false, motivo: 'A operação ainda não tem execução.' } })!;
    expect(nd.aprendizado).toMatchObject({ disponivel: false, motivo: 'A operação ainda não tem execução.' });
  });

  it('v1.111: ambiente (real/simulado/não medido) e a evidência da ação final; ambiente fora do vocabulário é null, nunca "real"', () => {
    const x = relatorioDoServidor({ ...RELATORIO, ambiente: 'simulado', agentes: [agente(1, { acao_final: { tipo: 'CREATE_COMMENT', verificada: 'sim', evidencia_id: 204 } })] })!;
    expect(x.ambiente).toBe('simulado');
    expect(x.agentes[0]!.acao_final).toEqual({ tipo: 'CREATE_COMMENT', verificada: 'sim', evidencia_id: 204 });
    expect(relatorioDoServidor({ ...RELATORIO, ambiente: 'producao' })!.ambiente).toBeNull();
    expect(relatorioDoServidor(RELATORIO)!.ambiente).toBeNull();
    expect(relatorioEmMarkdown(x)).toContain('- **Ambiente:** simulado');
    expect(relatorioEmMarkdown({ ...x, ambiente: null })).not.toContain('**Ambiente:**');
  });

  it('resposta que não é o relatório (sem operação ou sem agentes) é null: erro de leitura, nunca relatório vazio', () => {
    expect(relatorioDoServidor(null)).toBeNull();
    expect(relatorioDoServidor({ operacao: { id: 'x' } })).toBeNull();
    expect(relatorioDoServidor({ agentes: [] })).toBeNull();
  });

  it('o Markdown traz as seções novas e diz que o central o montou; o relatório do painel não tem identidades nem critérios', () => {
    const md = relatorioEmMarkdown(r);
    for (const s of ['## Identidades', '**3 de 5** identidades solicitadas executam hoje.', '## Latência', '## Critérios do diagnóstico', '| 2b. Texto sem repetição | testado em simulação | não medido | nenhuma |']) {
      expect(md).toContain(s);
    }
    expect(md).toContain('Por peça (o total dividido pelas ações executadas e verificadas): US$ 0.1850');
    expect(md).toContain('**Montado por:** o central');
    expect(md).toContain('- **Conhecimento usado:** não informado pelo relatório do central');
    expect(md).toMatch(/\| Conta \| 2026-10-07T18:00:02Z \([^)]* no painel\) \(\+2 s\) \|/);   // a hora do 31.171 + a etapa do v1.111
    expect(md).toContain('- **Duração:** 1 min 30 s · **Espera pela aprovação:** 12 s');
    expect(md).not.toContain('@fulano');
    const painel = relatorioEmMarkdown({ ...r, fonte: 'painel', identidades: null, criterios: null, latencia: null });
    expect(painel).not.toContain('## Identidades');
    expect(painel).not.toContain('## Critérios do diagnóstico');
    expect(painel).toContain('**Montado por:** o painel');
  });
});

describe('o botão Relatório', () => {
  let root: Root;
  let container: HTMLElement;
  let backend: FakeBackend;
  let blobs: Blob[];

  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    blobs = [];
    URL.createObjectURL = (b: Blob | MediaSource) => { blobs.push(b as Blob); return `blob:r-${blobs.length}`; };
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() { /* o download é do navegador */ });
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(OPERACAO));
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    container.remove();
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
  });

  const OPERACAO = {
    id: 'op-1', command: 'Comentar no post da loja', status: 'concluida_com_bloqueios', max_usd: 4.5,
    alvos: [{ run_id: 'r1', profile_id: 'p1', persona_nome: 'Persona 01', estado: 'concluido', estagio: 'resultado_verificado', estagios: [] }],
  };
  const abrir = async () => {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'], query: {} } });
    await act(async () => root.render(<OperacaoPage />));
    await click(await waitFor(() => byRole('button', /^Relatório$/, container)));
    return waitFor(() => byRole('dialog', /Relatório da operação/));
  };
  const lerBlob = (b: Blob) => new Promise<string>((ok) => { const f = new FileReader(); f.onload = () => ok(String(f.result)); f.readAsText(b); });

  it('com o relatório do central: diz que veio dele, mostra a resposta objetiva, os critérios, a latência e o custo por peça; baixa o dele', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/relatorio$/, () => json(RELATORIO));
    const d = await abrir();
    await waitFor(() => expect(d.querySelector('[data-fonte="servidor"]')).not.toBeNull());
    expect(text(d.querySelector('[data-identidades]')!)).toBe('3 de 5 identidades executam hoje.');
    expect(text(d.querySelector('[data-criterios]')!)).toContain('Critérios do diagnóstico: 3; valeram nesta operação 1, não valeram 1, 1 não medidos.');
    expect(text(d.querySelector('[data-latencia-do-relatorio]')!)).toContain('Duração mediana por agente: 1 min 30 s; o mais lento, Persona 02, levou 2 min 00 s.');
    expect(text(d.querySelector('[data-custo-por-peca]')!)).toContain('US$ 0.1850');
    expect(backend.callsTo('GET', /operacoes\/op-1\/aprendizado/)).toHaveLength(0);              // o aprendizado já vem no relatório
    await click(byRole('button', /^Baixar JSON$/, d));
    const js = JSON.parse(await lerBlob(blobs[0]!)) as { fonte: string; custo: { por_peca_usd: number }; criterios: unknown[] };
    expect(js).toMatchObject({ fonte: 'servidor', custo: { por_peca_usd: 0.185 } });
    expect(js.criterios).toHaveLength(3);
  });

  it('central sem a rota (404): monta no painel, lendo o aprendizado, e diz por quê', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/relatorio$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    backend.on('GET', /operacoes\/op-1\/aprendizado/, () => json({ gerado_em: null, avisos: [], perguntas: [], nao_coberto: [] }));
    const d = await abrir();
    await waitFor(() => expect(d.querySelector('[data-fonte="painel"]')).not.toBeNull());
    expect(text(d.querySelector('[data-fonte]')!)).toContain('O central ainda não oferece o relatório da operação.');
    expect(d.querySelector('[data-identidades]')).toBeNull();
    expect(backend.callsTo('GET', /operacoes\/op-1\/aprendizado/)).toHaveLength(1);
    await click(byRole('button', /^Baixar Markdown$/, d));
    expect(await lerBlob(blobs[0]!)).toContain('**Montado por:** o painel');
  });

  it('relatório do central em formato torto ou erro do central: reserva do painel, com o motivo, nunca relatório vazio', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/relatorio$/, () => json({ operacao: { id: 'op-1' } }));
    backend.on('GET', /operacoes\/op-1\/aprendizado/, () => apiError(404, 'operacao_desconhecida', 'sem execução'));
    const d = await abrir();
    await waitFor(() => expect(d.querySelector('[data-fonte="painel"]')).not.toBeNull());
    expect(text(d.querySelector('[data-fonte]')!)).toContain('formato inesperado');
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /^\/api\/operacoes\/op-1\/relatorio$/, () => apiError(500, 'erro_interno', 'banco indisponível'));
    const d2 = await abrir();
    await waitFor(() => expect(d2.querySelector('[data-fonte="painel"]')).not.toBeNull());
    expect(text(d2.querySelector('[data-fonte]')!)).toContain('banco indisponível');
  });

  it('o rascunho lido do mesmo formato do painel: lerOperacao continua aceitando a operação de sempre', () => {
    expect(lerOperacao(OPERACAO)).not.toBeNull();
  });
});
