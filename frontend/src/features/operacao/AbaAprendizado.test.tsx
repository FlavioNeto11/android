// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, apiError, byRole, click, esperarElemento, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { PERGUNTAS_DO_DONO, lerAprendizado, licoesDaOperacao } from './aprendizadoDaOperacao';
import { OperacaoPage } from './OperacaoPage';

/**
 * 31.166: a aba "Aprendizado" da tela Operação lê `GET /api/operacoes/{id}/aprendizado` (adendo v1.96 com o campo `avisos` do
 * corte 57). Prova `simulated`: servidor falso e respostas inventadas no formato do contrato.
 */

const item = (ref: string, over: Record<string, unknown> = {}) => ({
  ref, tipo: 'licao', escopo: 'app', resumo: `Resumo de ${ref}`, origem: 'treino', confianca: 'confirmado', estado: 'published', evidencia: ['r1'],
  persona: null, observado_em: '2026-10-07T18:00:00Z', frescor_ate: null, a_favor: 2, contra: 0, inferida: false, ...over,
});

const RESPOSTA = {
  operacao_id: 'op-1', gerado_em: '2026-10-07T19:00:00Z', simulados: false, persona: null,
  perguntas: [
    { chave: 'plataforma_aprendeu', titulo: 'O que a plataforma aprendeu com esta operação', itens: [item('licao:li-forte'), item('licao:li-contestada', { a_favor: 3, contra: 1 })] },
    { chave: 'persona_aprendeu', titulo: 'O que cada persona aprendeu', itens: [
      item('memoria:m1', { tipo: 'memoria', escopo: 'persona', persona: 'p1', resumo: 'gosta de falar com @alguem.real sobre fotos', a_favor: null, contra: null }),
      item('memoria:m2', { tipo: 'memoria', escopo: 'persona', persona: 'p-que-nao-esta-na-tela' })] },
    { chave: 'do_app', titulo: 'O que veio do app', itens: [] },
    { chave: 'fontes_que_sustentam', titulo: 'Que fontes sustentam o que se aprendeu', itens: [{ ref: 'fato:f', confianca: 'confirmado', fontes: [{ ref: 'fonte:a', resumo: 'Página do assunto' }] }] },
    { chave: 'revisar_ou_descartar', titulo: 'O que revisar ou descartar', itens: [item('licao:li-contestada', { confianca: 'hipotese', motivo: 'evidência contra', a_favor: 3, contra: 1 })] },
    { chave: 'falhas_que_geraram_aprendizado', titulo: 'Que falhas geraram aprendizado', itens: [item('backlog:7', { tipo: 'backlog', escopo: 'falha', inferida: true, a_favor: null, contra: null })] },
  ],
  contagem: {},
  nao_coberto: [{ chave: 'conhecimento_geral', motivo: 'a promoção a conhecimento geral é da curadoria' }],
  avisos: [{ run_id: 'r-20261007-aaaa', step_id: 'resposta', aviso: 'conhecimento_ids não gravados nesta etapa' }],
};

const DETALHE = {
  id: 'op-1', command: 'x', status: 'concluida',
  alvos: [
    { profile_id: 'p1', persona_nome: 'Persona 01', run_id: 'r1', estagio: 'persona', estado: 'concluido', estagios: [], resultado: null },
    { profile_id: 'p2', persona_nome: 'Persona 02', run_id: 'r2', estagio: 'persona', estado: 'concluido', estagios: [], resultado: null },
  ],
};

describe('a aba Aprendizado da operação', () => {
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
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(DETALHE));
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
  });
  const abrir = async (id = 'op-1') => {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: [id], query: {} } });
    await act(async () => root.render(<OperacaoPage />));
    await waitFor(() => byRole('tab', /^Aprendizado$/, container));
  };
  const abaAprendizado = async () => { await click(byRole('tab', /^Aprendizado$/, container)); };
  const pedidos = () => backend.callsTo('GET', /\/aprendizado$/);

  it('só lê o aprendizado quando a aba abre, e a aba dos agentes continua sendo a primeira', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => json(RESPOSTA));
    await abrir();
    expect(byRole('tab', /^Agentes$/, container).getAttribute('aria-selected')).toBe('true');
    expect(container.querySelector('tbody tr[data-alvo]')).not.toBeNull();
    expect(pedidos()).toHaveLength(0);
    await abaAprendizado();
    await waitFor(() => expect(pedidos()).toHaveLength(1));
    expect(pedidos()[0]!.query.get('simulados')).toBe('false');
    expect(pedidos()[0]!.query.get('persona')).toBeNull();
    expect(container.querySelector('tbody tr[data-alvo]')).toBeNull();           // a aba troca o painel
  });

  it('mostra as 10 perguntas com a contagem, o "não veio" e o vazio, e a persona só pelo rótulo da tela', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => json(RESPOSTA));
    await abrir();
    await abaAprendizado();
    await waitFor(() => expect(text(container)).toContain('O que a plataforma aprendeu com esta operação (2)'));
    const t = text(container);
    // As 10 perguntas aparecem sempre, na ordem do dono (mais as duas listas de lições e o "não cobre").
    expect(PERGUNTAS_DO_DONO).toHaveLength(10);
    expect(container.querySelectorAll('h3')).toHaveLength(10 + 2 + 1);
    expect(t).toContain('O que veio do app (0)');
    expect(t).toContain('Nada nesta operação.');
    expect(t).toContain('O que virou conhecimento geral (0)');                          // título de reserva: o backend não mandou essa pergunta
    expect(t).toContain('O central não respondeu esta pergunta.');
    expect(t).toContain('· Persona 01');
    expect(t).toContain('· uma persona');                                        // persona fora da operação: nunca o id
    expect(t).not.toMatch(/\bp1\b|p-que-nao-esta-na-tela/);
    expect(t).toContain('Página do assunto');                                    // a fonte que sustenta
    expect(t).toContain('Inferida');                                             // o backlog deduzido
    expect(t).toContain('Por quê: evidência contra');
    expect(t).toContain('a promoção a conhecimento geral é da curadoria');       // o que a leitura não cobre
  });

  it('separa as lições reforçadas das contestadas, com a contagem efetiva e a contrária, e a contagem que falta não vira zero', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => json(RESPOSTA));
    await abrir();
    await abaAprendizado();
    const forte = await esperarElemento<HTMLElement>('section[aria-label="Lições reforçadas"]', container);
    const contestada = container.querySelector('section[aria-label="Lições contestadas"]') as HTMLElement;
    expect(text(forte)).toContain('Lições reforçadas (1)');
    expect(text(forte)).toContain('licao:li-forte');
    expect(text(forte)).toContain('2 a favor · 0 contra');
    expect(text(forte)).not.toContain('licao:li-contestada');
    expect(text(contestada)).toContain('Lições contestadas (1)');
    expect(text(contestada)).toContain('3 a favor · 1 contra');
    expect(text(container.querySelector('section[aria-label="O que cada persona aprendeu"]') as HTMLElement)).not.toContain('0 a favor');   // memória sem contagem: nada de zero inventado
  });

  it('põe os avisos em destaque com o link da execução e tira o @ de conta do texto', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => json(RESPOSTA));
    await abrir();
    await abaAprendizado();
    const aviso = await esperarElemento<HTMLElement>('[role="status"]', container);
    expect(text(aviso)).toContain('1 aviso sobre o conhecimento que o texto recebeu');
    expect(text(aviso)).toContain('conhecimento_ids não gravados nesta etapa');
    expect(aviso.querySelector('a[href="#/execucoes/r-20261007-aaaa"]')).not.toBeNull();
    expect(text(container)).toContain('gosta de falar com @[omitido] sobre fotos');
    expect(text(container)).not.toContain('@alguem.real');
  });

  it('o filtro de persona e o "incluir simulados" refazem o pedido com os valores novos', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => json(RESPOSTA));
    await abrir();
    await abaAprendizado();
    await waitFor(() => expect(pedidos()).toHaveLength(1));
    expect(Array.from((byRole('combobox', 'Persona', container) as HTMLSelectElement).options).map((o) => o.textContent)).toEqual(['Todas e a operação inteira', 'Persona 01', 'Persona 02']);
    await setValue(byRole('combobox', 'Persona', container) as HTMLSelectElement, 'p2');
    await waitFor(() => expect(pedidos()).toHaveLength(2));
    expect(pedidos()[1]!.query.get('persona')).toBe('p2');
    const caixa = Array.from(container.querySelectorAll('label')).find((l) => l.textContent === 'Incluir simulados')!.querySelector('input') as HTMLInputElement;
    await click(caixa);
    await waitFor(() => expect(pedidos()).toHaveLength(3));
    expect(pedidos()[2]!.query.get('simulados')).toBe('true');
    expect(pedidos()[2]!.query.get('persona')).toBe('p2');
  });

  it('rota ausente vira "Aprendizado não disponível" com o motivo, e "Ler de novo" pede de novo', async () => {
    let n = 0;
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => { n += 1; return n === 1 ? apiError(404, 'not_found', 'sem rota') : json(RESPOSTA); });
    await abrir();
    await abaAprendizado();
    await waitFor(() => expect(text(container)).toContain('Aprendizado não disponível'));
    expect(text(container)).toContain('O central ainda não oferece o aprendizado da operação.');
    expect(text(container)).not.toContain('Nada nesta operação.');                // indisponível nunca vira "nada aprendido"
    await click(byRole('button', /^Ler de novo$/, container));
    await waitFor(() => expect(text(container)).toContain('O que a plataforma aprendeu com esta operação (2)'));
    expect(pedidos()).toHaveLength(2);
  });

  it('o exemplo não pede nada ao central e diz que é um exemplo', async () => {
    await abrir('op-exemplo');
    await abaAprendizado();
    expect(text(container)).toContain('É um exemplo');
    expect(backend.callsTo('GET', /aprendizado/)).toHaveLength(0);
  });
});

describe('o leitor do aprendizado com os campos novos', () => {
  it('lê a_favor, contra e avisos; número inválido e aviso sem texto não valem', () => {
    const a = lerAprendizado({
      perguntas: [{ chave: 'do_app', itens: [{ ref: 'licao:a', a_favor: 2, contra: -1 }, { ref: 'licao:b', a_favor: 'x' }] }],
      avisos: [{ run_id: 'r1', step_id: 's1', aviso: 'não gravado' }, { run_id: 'r2', aviso: '' }, 7],
    })!;
    const itens = a.perguntas.find((p) => p.chave === 'do_app')!.itens;
    expect(itens.map((i) => [i.a_favor, i.contra])).toEqual([[2, null], [null, null]]);
    expect(a.avisos).toEqual([{ run_id: 'r1', step_id: 's1', aviso: 'não gravado' }]);
    expect(lerAprendizado({ perguntas: [] })!.avisos).toEqual([]);
  });

  it('licoesDaOperacao junta a lição repetida (a maior contagem vale) e ignora o que não é lição', () => {
    const base = { tipo: null, escopo: null, resumo: null, confianca: null, persona: null, motivo: null, inferida: false, evidencias: 0, fontes: [] };
    const p = (itens: Array<{ ref: string; a_favor: number | null; contra: number | null }>) => ({ chave: 'do_app' as const, titulo: 't', veio: true, itens: itens.map((i) => ({ ...base, ...i })) });
    const r = licoesDaOperacao([
      p([{ ref: 'licao:a', a_favor: 1, contra: null }, { ref: 'receita:9', a_favor: 5, contra: 5 }, { ref: 'licao:sem', a_favor: null, contra: null }]),
      p([{ ref: 'licao:a', a_favor: 3, contra: 0 }, { ref: 'licao:c', a_favor: 0, contra: 2 }]),
    ]);
    expect(r.reforcadas.map((i) => [i.ref, i.a_favor])).toEqual([['licao:a', 3]]);
    expect(r.contestadas.map((i) => i.ref)).toEqual(['licao:c']);
  });
});
