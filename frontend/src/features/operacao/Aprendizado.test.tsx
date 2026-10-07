// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, apiError, botaoPronto, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { apiOperacoes } from './api';
import { PERGUNTAS_DO_DONO, lerAprendizado } from './aprendizadoDaOperacao';
import { lerOperacao } from './modelo';
import { OperacaoPage } from './OperacaoPage';
import { montarRelatorio, relatorioEmMarkdown } from './relatorio';

/**
 * 31.162: o relatório lê as 10 perguntas do aprendizado da operação (adendo v1.96, `GET /api/operacoes/{id}/aprendizado`).
 * Prova `simulated`: servidor falso e respostas inventadas no formato do contrato.
 */

const item = (ref: string, over: Record<string, unknown> = {}) => ({
  ref, tipo: 'fluxo', escopo: 'app', resumo: `Resumo de ${ref}`, origem: 'treino', confianca: 'confirmado', estado: 'published', evidencia: ['r1', 'r2'],
  persona: null, observado_em: '2026-10-07T18:00:00Z', frescor_ate: null, a_favor: 2, contra: 0, inferida: false, ...over,
});

const RESPOSTA = {
  operacao_id: 'op-1', gerado_em: '2026-10-07T19:00:00Z', simulados: false, persona: null,
  perguntas: [
    { chave: 'plataforma_aprendeu', titulo: 'O que a plataforma aprendeu com esta operação', itens: [item('fluxo:f1')] },
    { chave: 'persona_aprendeu', titulo: 'O que cada persona aprendeu', itens: [item('memoria:m1', { tipo: 'memoria', escopo: 'persona', persona: 'p1', confianca: 'hipotese', inferida: true }),
                                              item('memoria:m2', { tipo: 'memoria', escopo: 'persona', persona: 'p-desconhecida' })] },
    { chave: 'do_app', titulo: 'O que veio do app', itens: [] },
    { chave: 'fontes_que_sustentam', titulo: 'Que fontes sustentam o que se aprendeu', itens: [{ ref: 'fato:f', confianca: 'confirmado', fontes: [{ ref: 'fonte:a', resumo: 'Página da loja' }, { ref: 'fonte:b' }] }] },
    { chave: 'revisar_ou_descartar', titulo: 'O que revisar ou descartar', itens: [item('licao:l1', { tipo: 'licao', confianca: 'hipotese', motivo: 'evidência contra' })] },
  ],
  contagem: { plataforma_aprendeu: 1 },
  nao_coberto: [{ chave: 'conhecimento_geral', motivo: 'a promoção a conhecimento geral é da curadoria' }],
};

const OP = lerOperacao({
  id: 'op-1', command: 'x', alvos: [{ profile_id: 'p1', persona_nome: 'Persona 01', run_id: 'r1', estagio: 'persona', estado: 'em_curso', estagios: [], resultado: null }],
})!;

describe('lerAprendizado', () => {
  it('traz as 10 perguntas na ordem do dono; a que não veio fica "não veio" e a sem itens fica vazia', () => {
    const a = lerAprendizado(RESPOSTA)!;
    expect(a.perguntas.map((p) => p.chave)).toEqual([...PERGUNTAS_DO_DONO]);
    expect(a.perguntas.find((p) => p.chave === 'do_app')).toMatchObject({ veio: true, itens: [] });
    expect(a.perguntas.find((p) => p.chave === 'fontes_externas')).toMatchObject({ veio: false, itens: [], titulo: 'Que fontes externas entraram' });
    expect(a.perguntas[0]!.titulo).toBe('O que a plataforma aprendeu com esta operação');          // o título do backend vale
    expect(a.nao_coberto).toEqual([{ chave: 'conhecimento_geral', motivo: 'a promoção a conhecimento geral é da curadoria' }]);
  });

  it('lê o item de fontes_que_sustentam e descarta o que não tem ref; sem `perguntas` não é resposta', () => {
    const a = lerAprendizado(RESPOSTA)!;
    expect(a.perguntas.find((p) => p.chave === 'fontes_que_sustentam')!.itens[0]!.fontes).toEqual([{ ref: 'fonte:a', resumo: 'Página da loja' }, { ref: 'fonte:b', resumo: null }]);
    expect(lerAprendizado({ perguntas: [{ chave: 'do_app', itens: [{ resumo: 'sem ref' }, 7, null] }] })!.perguntas.find((p) => p.chave === 'do_app')!.itens).toEqual([]);
    expect(lerAprendizado({ itens: [] })).toBeNull();
    expect(lerAprendizado(null)).toBeNull();
  });
});

describe('o relatório com as 10 perguntas', () => {
  const lido = { situacao: 'lido', aprendizado: lerAprendizado(RESPOSTA)! } as const;
  const r = montarRelatorio(OP, new Date('2026-10-07T20:00:00Z'), lido);
  const md = relatorioEmMarkdown(r);

  it('a persona vai pelo rótulo (e "uma persona" sem rótulo), nunca pelo id; a operação inteira é "operação inteira"', () => {
    const persona = r.aprendizado.perguntas.find((p) => p.chave === 'persona_aprendeu')!.itens;
    expect(persona.map((i) => i.persona)).toEqual(['Persona 01', 'uma persona']);
    expect(r.aprendizado.perguntas[0]!.itens[0]!.persona).toBeNull();
    expect(`${JSON.stringify(r)}\n${md}`).not.toMatch(/\bp1\b|p-desconhecida/);
  });

  it('o Markdown traz a seção com as 10 perguntas, confirmado × hipótese, inferida, motivo, fontes e o não coberto', () => {
    expect(md).toContain('## O que a operação ensinou (as 10 perguntas)');
    for (const t of ['### O que a plataforma aprendeu com esta operação', '### O que veio do app', '### Que fontes externas entraram']) expect(md).toContain(t);
    expect(md).toContain('- [confirmado] Resumo de fluxo:f1 (fluxo, app, operação inteira; 2 a favor, 0 contra; 2 evidências).');
    expect(md).toContain('- [hipótese] Resumo de memoria:m1 (memoria, persona, Persona 01; inferida; 2 a favor, 0 contra; 2 evidências).');
    expect(md).toContain('motivo: evidência contra');
    expect(md).toContain('Fontes: Página da loja | fonte:b.');
    expect(md).toContain('Nada registrado nesta operação.');                       // do_app veio vazio
    expect(md).toContain('Esta pergunta não veio na resposta do central.');        // fontes_externas não veio
    expect(md).toContain('- conhecimento_geral: a promoção a conhecimento geral é da curadoria');
    expect(r.aprendizado).toMatchObject({ disponivel: true, motivo: null });
    expect(r.aprendizado.perguntas).toHaveLength(10);
  });

  it('sem a leitura ou com a rota ausente o relatório diz "não disponível" com o motivo, e não "nada aprendido"', () => {
    const sem = montarRelatorio(OP, new Date(), { situacao: 'indisponivel', motivo: 'O central ainda não oferece o aprendizado da operação.' });
    expect(sem.aprendizado).toEqual({ disponivel: false, motivo: 'O central ainda não oferece o aprendizado da operação.', gerado_em: null, perguntas: [], licoes: { reforcadas: [], contestadas: [] }, avisos: [], nao_coberto: [] });
    const mdSem = relatorioEmMarkdown(sem);
    expect(mdSem).toContain('Não disponível: O central ainda não oferece o aprendizado da operação.');
    expect(mdSem).not.toContain('Nada registrado');
    expect(relatorioEmMarkdown(montarRelatorio(OP))).toContain('Não disponível: O aprendizado da operação não foi lido');
  });
});

describe('31.167: avisos e lições reforçadas e contestadas no relatório', () => {
  const resposta = {
    ...RESPOSTA,
    perguntas: [
      { chave: 'plataforma_aprendeu', titulo: 'O que a plataforma aprendeu com esta operação', itens: [item('licao:forte', { a_favor: 4, contra: 0 }), item('licao:disputada', { a_favor: 3, contra: 2, resumo: 'falar com @alguem.real ajuda' })] },
      { chave: 'revisar_ou_descartar', titulo: 'O que revisar ou descartar', itens: [item('licao:disputada', { a_favor: 3, contra: 2 }), item('licao:sem-contagem', { a_favor: null, contra: null })] },
    ],
    avisos: [{ run_id: 'r-1', step_id: 'resposta', aviso: 'conhecimento_ids não gravados; veja @alguem.real' }, { run_id: null, step_id: null, aviso: 'sem etapa' }],
  };
  const lido = { situacao: 'lido', aprendizado: lerAprendizado(resposta)! } as const;
  const r = montarRelatorio(OP, new Date('2026-10-07T20:00:00Z'), lido);
  const md = relatorioEmMarkdown(r);

  it('o JSON separa as lições pela evidência efetiva, uma vez cada, e a lição sem contagem não entra em nenhuma das duas', () => {
    expect(r.aprendizado.licoes.reforcadas.map((i) => [i.ref, i.a_favor, i.contra])).toEqual([['licao:forte', 4, 0]]);
    expect(r.aprendizado.licoes.contestadas.map((i) => [i.ref, i.a_favor, i.contra])).toEqual([['licao:disputada', 3, 2]]);
  });

  it('o Markdown tem as duas listas com a contagem e os avisos com a execução e a etapa', () => {
    expect(md).toContain('### Lições reforçadas');
    expect(md).toContain('### Lições contestadas');
    expect(md).toMatch(/### Lições reforçadas\n\n- \[confirmado\] Resumo de licao:forte \(fluxo, app, operação inteira; 4 a favor, 0 contra/);
    expect(md).toMatch(/### Lições contestadas\n\n- \[confirmado\] falar com @\[omitido\] ajuda \(fluxo, app, operação inteira; 3 a favor, 2 contra/);
    expect(md).toContain('### Avisos sobre o conhecimento que o texto recebeu');
    expect(md).toContain('- execução r-1, etapa resposta: conhecimento_ids não gravados; veja @[omitido]');
    expect(md).toContain('- etapa não informada: sem etapa');
  });

  it('nenhum @ de conta sai no JSON nem no Markdown', () => {
    expect(`${JSON.stringify(r)}\n${md}`).not.toContain('@alguem.real');
  });

  it('sem avisos o Markdown não tem a seção; sem lição, as duas listas dizem "Nenhuma."', () => {
    const vazio = montarRelatorio(OP, new Date('2026-10-07T20:00:00Z'), { situacao: 'lido', aprendizado: lerAprendizado({ perguntas: [] })! });
    const mdVazio = relatorioEmMarkdown(vazio);
    expect(mdVazio).not.toContain('### Avisos sobre o conhecimento');
    expect(mdVazio).toMatch(/### Lições reforçadas\n\nNenhuma\.\n\n### Lições contestadas\n\nNenhuma\./);
    expect(vazio.aprendizado.avisos).toEqual([]);
  });
});

describe('apiOperacoes.aprendizado e o diálogo do Relatório', () => {
  let root: Root;
  let container: HTMLElement;
  let backend: FakeBackend;
  let blobs: Blob[];

  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    blobs = [];
    URL.createObjectURL = (b: Blob | MediaSource) => { blobs.push(b as Blob); return `blob:a-${blobs.length}`; };
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({
      id: 'op-1', command: 'x', alvos: [{ profile_id: 'p1', persona_nome: 'Persona 01', run_id: 'r1', estagio: 'persona', estado: 'em_curso', estagios: [], resultado: null }],
    }));
  });
  afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    container.remove();
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
  });
  const lerBlob = (b: Blob) => new Promise<string>((ok) => { const f = new FileReader(); f.onload = () => ok(String(f.result)); f.readAsText(b); });
  const abrir = async () => {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'], query: {} } });
    await act(async () => root.render(<OperacaoPage />));
    await click(await waitFor(() => byRole('button', /^Relatório$/, container)));
    return waitFor(() => byRole('dialog', /Relatório da operação/));
  };

  it('pede a rota com simulados=false e lê o aprendizado', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => json(RESPOSTA));
    const l = await apiOperacoes.aprendizado('op-1');
    expect(l.situacao).toBe('lido');
    expect(backend.callsTo('GET', /aprendizado$/)[0]!.query.get('simulados')).toBe('false');
  });

  it.each([
    ['404 operacao_desconhecida', () => apiError(404, 'operacao_desconhecida', 'sem'), /ainda não tem execução nem memória/],
    ['rota ausente (404 sem código do módulo)', () => apiError(404, 'not_found', 'sem rota'), /ainda não oferece o aprendizado/],
    ['erro do servidor', () => apiError(500, 'erro_interno', 'quebrou'), /Não foi possível ler o aprendizado/],
    ['formato inesperado', () => json({ itens: [] }), /formato inesperado/],
  ])('%s vira "indisponível" com o motivo, sem derrubar o relatório', async (_nome, resposta, motivo) => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, resposta);
    const l = await apiOperacoes.aprendizado('op-1');
    expect(l).toMatchObject({ situacao: 'indisponivel' });
    expect((l as { motivo: string }).motivo).toMatch(motivo);
  });

  it('o diálogo lê o aprendizado antes de liberar os arquivos, e o JSON e o Markdown saem com as 10 perguntas', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1\/aprendizado$/, () => json(RESPOSTA));
    const d = await abrir();
    await click(await botaoPronto(/^Baixar Markdown$/, d));
    await click(await botaoPronto(/^Baixar JSON$/, d));
    const md = await lerBlob(blobs[0]!);
    const js = JSON.parse(await lerBlob(blobs[1]!)) as ReturnType<typeof montarRelatorio>;
    expect(md).toContain('## O que a operação ensinou (as 10 perguntas)');
    expect(js.aprendizado.disponivel).toBe(true);
    expect(js.aprendizado.perguntas).toHaveLength(10);
    expect(js.aprendizado.perguntas.find((p) => p.chave === 'persona_aprendeu')!.itens[0]!.persona).toBe('Persona 01');
  });

  it('sem a rota no central o diálogo avisa "Aprendizado não disponível" e o relatório sai com o motivo', async () => {
    const d = await abrir();                                                   // a rota não está registrada: 404 do FakeBackend
    await waitFor(() => expect(text(d)).toContain('Aprendizado não disponível: O central ainda não oferece o aprendizado da operação.'));
    await click(await botaoPronto(/^Baixar Markdown$/, d));
    expect(await lerBlob(blobs[0]!)).toContain('Não disponível: O central ainda não oferece o aprendizado da operação.');
  });
});
