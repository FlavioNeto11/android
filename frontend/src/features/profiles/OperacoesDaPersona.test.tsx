// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { lerOperacao } from '../operacao/modelo';
import { lerHistoricoDaPersona, linhasDaPersona, OPERACOES_LIDAS, somaDoHistorico } from './historicoDeOperacoes';
import { OperacoesDaPersona } from './OperacoesDaPersona';

/**
 * 31.212: o histórico de operações de uma persona, para a tela Persona. Prova `simulated`: servidor falso (a lista traz só o resumo, o
 * detalhe traz os alvos) no formato do módulo de operações. Nada é escrito: só leitura.
 */

const alvo = (profile: string, extra: Record<string, unknown> = {}) => ({
  run_id: `r-${profile}`, profile_id: profile, persona_nome: `Persona ${profile}`, estado: 'concluido', estagio: 'resultado_verificado', estagios: [],
  custo_usd: 0.05, motivo: null, resultado: { texto: 'Texto.', conhecimento_ids: [], evidencia_id: null, acao_final: { tipo: 'CREATE_COMMENT', verificada: true, evidencia_id: 1 } }, ...extra,
});
const op = (id: string, alvos: unknown[], extra: Record<string, unknown> = {}) => ({
  id, command: `Comentar na página ${id}`, status: 'concluida', created_at: '2026-10-07T10:00:00Z', app_id: 'instagram', alvos, ...extra,
});
const resumo = (o: { id: string; command: string; status: string; created_at: string }) => ({ id: o.id, command: o.command, status: o.status, created_at: o.created_at, capacidade: {} });

const OPS = [
  op('op-3', [alvo('p1', { estado: 'bloqueado', estagio: 'conta', parou_em: 'sessao', motivo: 'sem sessão', custo_usd: null, resultado: null }), alvo('p2')]),
  op('op-2', [alvo('p2'), alvo('p1', { estagio: 'acao_preparada', custo_usd: 0.1, resultado: { texto: 'x', conhecimento_ids: [], evidencia_id: null, acao_final: { tipo: 'CREATE_COMMENT', verificada: false, evidencia_id: null } } })]),
  op('op-1', [alvo('p2')]),
];

describe('linhasDaPersona', () => {
  const ops = OPS.map((o) => lerOperacao(o)!);

  it('só os alvos da persona, na ordem das operações, com estágio final, ação, verificação e custo', () => {
    const l = linhasDaPersona(ops, 'p1');
    expect(l.map((x) => x.operacaoId)).toEqual(['op-3', 'op-2']);
    expect(l[0]).toMatchObject({ estado: 'bloqueado', estagioFinal: 'Parou em Sessão', acao: null, verificacao: 'sem_acao', custoUsd: null, motivo: 'sem sessão' });
    expect(l[1]).toMatchObject({ estado: 'concluido', estagioFinal: 'Ação preparada', acao: 'CREATE_COMMENT', verificacao: 'nao_verificada', custoUsd: 0.1 });
    expect(linhasDaPersona(ops, 'p2').map((x) => x.verificacao)).toEqual(['verificada', 'verificada', 'verificada']);
    expect(linhasDaPersona(ops, 'outra')).toEqual([]);
  });

  it('o estágio final por estado: concluído, em andamento, na fila e cancelado', () => {
    const casos = [
      [{ estado: 'em_curso', estagio: 'conteudo_lido' }, 'Em Conteúdo lido'],
      [{ estado: 'pendente', estagio: null }, 'Ainda não começou'],
      [{ estado: 'cancelado', estagio: 'aparelho', parou_em: 'instagram_aberto' }, 'Parou em Instagram aberto'],
      [{ estado: 'concluido', estagio: 'resultado_verificado' }, 'Resultado verificado'],
    ] as const;
    for (const [extra, esperado] of casos) {
      expect(linhasDaPersona([lerOperacao(op('o', [alvo('p1', extra)]))!], 'p1')[0]!.estagioFinal).toBe(esperado);
    }
  });

  it('a soma: o custo ignora (e conta à parte) quem não informa, nunca como zero', () => {
    const l = linhasDaPersona(ops, 'p1');
    expect(somaDoHistorico(l)).toEqual({ alvos: 2, concluidos: 1, verificados: 0, custoUsd: 0.1, semCusto: 1 });
    expect(somaDoHistorico([])).toEqual({ alvos: 0, concluidos: 0, verificados: 0, custoUsd: null, semCusto: 0 });
    expect(somaDoHistorico(l.slice(0, 1)).custoUsd).toBeNull();
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
  });

  const servir = (ops: ReturnType<typeof op>[]) => {
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: ops.map(resumo) }));
    for (const o of ops) backend.on('GET', new RegExp(`^/api/operacoes/${o.id}$`), () => json(o));
  };
  const abrir = async (profileId = 'p1') => { await act(async () => root.render(<OperacoesDaPersona profileId={profileId} />)); };

  it('lista as operações da persona com estágio final, ação, verificação e custo, a soma e o link para a operação', async () => {
    servir(OPS);
    await abrir();
    await waitFor(() => expect(container.querySelector('tr[data-operacao]')).not.toBeNull());
    expect(Array.from(container.querySelectorAll('tr[data-operacao]')).map((r) => r.getAttribute('data-operacao'))).toEqual(['op-3', 'op-2']);
    const bloqueado = container.querySelector('tr[data-operacao="op-3"]')!;
    expect(text(bloqueado)).toContain('Parou em Sessão');
    expect(text(bloqueado)).toContain('sem sessão');
    expect(text(bloqueado.querySelector('[data-custo]')!)).toBe('—');
    const preparada = container.querySelector('tr[data-operacao="op-2"]')!;
    expect(text(preparada)).toContain('Não verificada');
    expect(text(preparada)).toContain('Comentário');
    expect(preparada.querySelector('a')!.getAttribute('href')).toBe('#/operacoes/op-2');
    expect(text(container.querySelector('[data-soma]')!)).toContain('2 alvos · 1 concluído · 0 ações verificadas · custo de IA US$ 0,1000 (1 alvo sem custo informado fica fora da soma)');
    expect(text(container)).toContain('Olhei as 3 operações.');
    expect(container.querySelectorAll('button').length).toBe(0);                            // só leitura
  });

  it('persona que nunca foi alvo: diz isso e quantas operações olhou', async () => {
    servir(OPS);
    await abrir('ninguem');
    await waitFor(() => expect(container.querySelector('[data-sem-operacoes]')).not.toBeNull());
    expect(text(container)).toContain('Esta persona não foi alvo de nenhuma operação. Olhei as 3 operações.');
  });

  it('só lê o detalhe das mais recentes e diz a conta (de quantas)', async () => {
    const muitas = Array.from({ length: OPERACOES_LIDAS + 5 }, (_, i) => op(`o${i}`, [alvo('p1')]));
    servir(muitas);
    await abrir();
    await waitFor(() => expect(container.querySelector('tr[data-operacao]')).not.toBeNull());
    expect(container.querySelectorAll('tr[data-operacao]')).toHaveLength(OPERACOES_LIDAS);
    expect(backend.callsTo('GET', /^\/api\/operacoes\/o\d+$/)).toHaveLength(OPERACOES_LIDAS);
    expect(text(container)).toContain(`Olhei as ${OPERACOES_LIDAS} operações mais recentes, de ${OPERACOES_LIDAS + 5}.`);
  });

  it('operação cujo detalhe falha não some em silêncio: a tela conta quantas ficaram de fora', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: OPS.map(resumo) }));
    backend.on('GET', /^\/api\/operacoes\/op-3$/, () => apiError(500, 'erro_interno', 'banco indisponível'));
    backend.on('GET', /^\/api\/operacoes\/op-2$/, () => json(OPS[1]));
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(OPS[2]));
    await abrir();
    await waitFor(() => expect(container.querySelector('[data-falhas]')).not.toBeNull());
    expect(text(container.querySelector('[data-falhas]')!)).toContain('1 operação não pôde ser lida e ficou fora desta lista.');
    expect(container.querySelectorAll('tr[data-operacao]')).toHaveLength(1);
  });

  it('sem o módulo de operações (404) diz isso e NÃO mostra a operação de exemplo como histórico', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('O central ainda não oferece o módulo de operações.'));
    expect(container.querySelector('tr[data-operacao]')).toBeNull();
  });

  it('erro de leitura vira aviso na própria seção, sem derrubar a aba', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => apiError(500, 'erro_interno', 'banco indisponível'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Não foi possível ler as operações'));
  });

  it('o histórico lido de uma lista sem alvo da persona devolve linhas vazias, não erro', async () => {
    servir([op('op-1', [alvo('p2')])]);
    const h = await lerHistoricoDaPersona('p1');
    expect(h).toMatchObject({ linhas: [], totalDeOperacoes: 1, lidas: 1, falhas: 0 });
  });

  // ---- v1.116 (Jev 31.213): GET /api/operacoes?profile_id= ----------------------------------------------------------------------------

  const filtrada = (itens: unknown[]) => backend.on('GET', /^\/api\/operacoes$/, () => json({ items: itens }));
  const itemFiltrado = (id: string, alvos: unknown[], extra: Record<string, unknown> = {}) =>
    ({ id, command: `Comentar na página ${id}`, status: 'concluida', created_at: '2026-10-07T10:00:00Z', capacidade: {}, alvos, ...extra });
  const alvoFiltrado = (extra: Record<string, unknown> = {}) => ({ profile_id: 'p1', instance_id: 'android-01', estado: 'concluido', estagio: 'resultado_verificado', motivo: null, parou_em: null, acao_verificada: true, custo_usd: 0.05, duracao_ms: 90_000, ...extra });

  it('com o filtro do central: uma leitura só (nenhum detalhe), mando profile_id e mostro o resumo do alvo', async () => {
    filtrada([
      itemFiltrado('op-3', [alvoFiltrado({ estado: 'bloqueado', estagio: 'conta', parou_em: 'sessao', motivo: 'sem sessão', acao_verificada: null, custo_usd: null })]),
      itemFiltrado('op-2', [alvoFiltrado({ estagio: 'acao_preparada', acao_verificada: false, custo_usd: 0.1 }), alvoFiltrado()]),
    ]);
    await abrir();
    await waitFor(() => expect(container.querySelector('tr[data-operacao]')).not.toBeNull());
    expect(backend.callsTo('GET', /^\/api\/operacoes$/)[0]!.query.get('profile_id')).toBe('p1');
    expect(backend.callsTo('GET', /^\/api\/operacoes\/op-/)).toHaveLength(0);                 // sem o detalhe das operações
    const linhas = Array.from(container.querySelectorAll('tr[data-operacao]'));
    expect(linhas.map((r) => r.getAttribute('data-operacao'))).toEqual(['op-3', 'op-2', 'op-2']);   // a persona com dois alvos numa operação
    const bloqueado = text(linhas[0]!);
    expect(bloqueado).toContain('Parou em Sessão');
    expect(bloqueado).toContain('sem sessão');
    expect(text(linhas[0]!.querySelector('[data-custo]')!)).toBe('—');
    expect(text(linhas[1]!)).toContain('Não verificada');
    expect(text(linhas[1]!)).toContain('Executada');                                            // o tipo da ação só vem do detalhe
    expect(text(linhas[2]!)).toContain('Verificada');
    expect(text(container.querySelector('[data-soma]')!)).toContain('3 alvos · 2 concluídos · 1 ação verificada');
    expect(text(container)).toContain('2 operações com alvo desta persona.');
  });

  it('com o filtro e nenhuma operação: persona sem histórico, sem tentar o detalhe', async () => {
    filtrada([]);
    await abrir();
    await waitFor(() => expect(container.querySelector('[data-sem-operacoes]')).not.toBeNull());
    expect(text(container)).toContain('Esta persona não foi alvo de nenhuma operação. 0 operações com alvo desta persona.');
    expect(backend.callsTo('GET', /^\/api\/operacoes$/)).toHaveLength(1);
  });

  it('no limite da lista avisa que pode haver operações mais antigas', async () => {
    filtrada(Array.from({ length: 50 }, (_, i) => itemFiltrado(`o${i}`, [alvoFiltrado()])));
    await abrir();
    await waitFor(() => expect(container.querySelector('tr[data-operacao]')).not.toBeNull());
    expect(text(container)).toContain('As 50 operações mais recentes desta persona; pode haver mais antigas.');
  });

  it('central anterior (ignora o filtro, lista sem alvos): cai na leitura do detalhe, como antes', async () => {
    servir(OPS);
    await abrir();
    await waitFor(() => expect(container.querySelector('tr[data-operacao]')).not.toBeNull());
    expect(backend.callsTo('GET', /^\/api\/operacoes\/op-/).length).toBe(3);
    expect(text(container)).toContain('Olhei as 3 operações.');
  });

  it('o alvo do filtro lê tolerante: campo torto vira "não informado", nunca zero', async () => {
    filtrada([itemFiltrado('op-9', [{ profile_id: 'p1', estado: 'estranho', estagio: 'inexistente', custo_usd: 'caro', acao_verificada: 'sim' }])]);
    await abrir();
    await waitFor(() => expect(container.querySelector('tr[data-operacao]')).not.toBeNull());
    const linha = container.querySelector('tr[data-operacao="op-9"]')!;
    expect(text(linha.querySelector('[data-custo]')!)).toBe('—');
    expect(text(linha)).toContain('não informado');
  });

  it('sem o módulo (404) no filtro: diz isso, sem tentar o detalhe e sem exemplo', async () => {
    backend.on('GET', /^\/api\/operacoes$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('O central ainda não oferece o módulo de operações.'));
    expect(container.querySelector('tr[data-operacao]')).toBeNull();
  });
});
