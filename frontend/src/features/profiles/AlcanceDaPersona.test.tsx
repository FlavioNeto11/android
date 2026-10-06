// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { APPS } from '../../test/fixtures';
import { FakeBackend, apiError, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { AlcanceDaPersona } from './AlcanceDaPersona';
import { alcanceDaPersona, alcanceDeExemplo, lerAlcance } from './alcance';
import type { Pessoa } from './pessoa';

/**
 * 31.186: o alcance de receitas e fluxos na persona (adendo v1.107, `GET /api/aprendizado/alcance`). Prova `simulated`: servidor falso
 * no formato do contrato; sem a rota, a tela lê um exemplo e diz isso.
 */

const item = (tipo: 'receita' | 'fluxo', id: string, chave: string, por: Record<string, { pode: boolean; motivo: string | null }>, extra: Record<string, unknown> = {}) =>
  ({ tipo, id, chave, origem: 'execucao', estado: 'published', por_persona: por, ...extra });

const RESPOSTA = {
  app_id: 'instagram', pacote: 'com.instagram.android', gerado_em: '2026-10-07T10:00:00Z',
  personas: [{ profile_id: 'p1', aparelhos: ['android-01'] }, { profile_id: 'p2', aparelhos: ['android-02'] }],
  resumo: { p1: { receita: 1, fluxo: 1 } },
  itens: [
    item('receita', 'r1', 'abrir_busca', { p1: { pode: true, motivo: null }, p2: { pode: true, motivo: null } }, { reproducoes_ok: 8, reproducoes_falha: 1 }),
    item('receita', 'r2', 'comentar_na_publicacao', { p1: { pode: false, motivo: 'presa_a_quem_ensinou' }, p2: { pode: true, motivo: null } }, { origem: 'ensino', reproducoes_ok: 1, reproducoes_falha: 0 }),
    item('fluxo', 'f1', 'comentar-em-post', { p1: { pode: true, motivo: null } }, { usos: 12, nascido_de_prova: false }),
    item('fluxo', 'f2', 'responder', { p1: { pode: false, motivo: 'fora_do_escopo' }, p2: { pode: true, motivo: null } }, { usos: 1, nascido_de_prova: true }),
    item('fluxo', 'f3', 'seguir', { p1: { pode: false, motivo: 'fluxo_nao_ligado' } }, { usos: 0 }),
    item('fluxo', 'f4', 'sem-resposta-para-p1', { p2: { pode: true, motivo: null } }),
    item('receita', 'r9', 'motivo_novo', { p1: { pode: false, motivo: 'motivo_que_a_tela_nao_conhece' } }),
  ],
};

describe('o leitor e a regra', () => {
  it('lerAlcance: sem itens/personas não é resposta; item torto cai; motivo só vem quando não pode', () => {
    expect(lerAlcance({ app_id: 'x' })).toBeNull();
    expect(lerAlcance(null)).toBeNull();
    expect(lerAlcance({ personas: [], itens: [] })).toBeNull();                              // sem app_id
    const a = lerAlcance({ app_id: 'x', personas: [{ profile_id: 'p1' }, 7], itens: [{ tipo: 'outro', id: 'a' }, { tipo: 'receita' }, item('receita', 'r', 'k', { p1: { pode: true, motivo: 'ignorado' }, p2: { pode: 'sim', motivo: null } as never })] })!;
    expect(a.personas).toEqual([{ profileId: 'p1', aparelhos: [] }]);
    expect(a.itens).toHaveLength(1);
    expect(a.itens[0]!.porPersona).toEqual({ p1: { pode: true, motivo: null } });             // pode:'sim' (não booleano) cai
  });
  it('alcanceDaPersona: separa quem pode, quem não e o que o central não respondeu para ela; sem vínculo diz isso', () => {
    const a = alcanceDaPersona(lerAlcance(RESPOSTA)!, 'p1');
    expect(a.pode.map((i) => i.id)).toEqual(['r1', 'f1']);
    expect(a.nao.map((x) => [x.item.id, x.motivo])).toEqual([['r2', 'presa_a_quem_ensinou'], ['f2', 'fora_do_escopo'], ['f3', 'fluxo_nao_ligado'], ['r9', 'motivo_que_a_tela_nao_conhece']]);
    expect(a.semResposta).toBe(1);
    expect(a.vinculada).toBe(true);
    expect(alcanceDaPersona(lerAlcance(RESPOSTA)!, 'p9')).toMatchObject({ vinculada: false, pode: [], nao: [], semResposta: 7 });
  });
  it('o exemplo é inventado e cobre os três motivos', () => {
    const e = alcanceDeExemplo('instagram', 'p1');
    expect(e.exemplo).toBe(true);
    expect(alcanceDaPersona(e, 'p1').nao.map((x) => x.motivo)).toEqual(['presa_a_quem_ensinou', 'fora_do_escopo', 'fluxo_nao_ligado']);
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/apps$/, () => json([{ ...APPS[0]!, id: 'instagram', name: 'Instagram' }, APPS[1]]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const abrir = async (id = 'p1') => { await act(async () => root.render(<AlcanceDaPersona profile={{ id } as unknown as Pessoa} />)); };

describe('a tela', () => {
  it('mostra quanto pode usar, a lista, e o que não pode agrupado por motivo em palavras', async () => {
    backend.on('GET', /^\/api\/aprendizado\/alcance$/, () => json(RESPOSTA));
    await abrir();
    await waitFor(() => expect(container.querySelector('[data-resumo]')).not.toBeNull());
    expect(backend.callsTo('GET', /aprendizado\/alcance/)[0]!.query.get('app')).toBe('instagram');
    expect(text(container.querySelector('[data-resumo]')!)).toContain('Pode usar 1 receita e 1 fluxo; 4 não valem para ela; 1 item sem resposta do central não entram na conta.');
    expect(container.querySelector('section[aria-label="Pode usar"] [data-item="r1"]')).not.toBeNull();
    expect(text(container.querySelector('li[data-item="r1"]')!)).toContain('8 reproduções certas, 1 falha');
    const presa = container.querySelector('[data-motivo="presa_a_quem_ensinou"]')!;
    expect(text(presa)).toContain('Só vale para quem ensinou');
    expect(text(presa)).toContain('comentar_na_publicacao');
    expect(text(presa)).toContain('ensinada');
    expect(text(container.querySelector('[data-motivo="fora_do_escopo"]')!)).toContain('nascido de prova');
    expect(text(container.querySelector('[data-motivo="fluxo_nao_ligado"]')!)).toContain('Fluxo ainda não ligado');
    const novo = container.querySelector('[data-motivo="motivo_que_a_tela_nao_conhece"]')!;
    expect(text(novo)).toContain('que esta tela ainda não descreve');
    expect(container.querySelector('[data-item="f4"]')).toBeNull();                          // sem resposta para ela: nem "pode" nem "não pode"
  });

  it('o alcance é desta persona: p2 vê o que vale para p2', async () => {
    backend.on('GET', /^\/api\/aprendizado\/alcance$/, () => json(RESPOSTA));
    await abrir('p2');
    await waitFor(() => expect(container.querySelector('[data-resumo]')).not.toBeNull());
    expect(text(container.querySelector('[data-resumo]')!)).toContain('Pode usar 2 receitas e 2 fluxos; 0 não valem para ela; 3 itens sem resposta do central não entram na conta.');
    expect(container.querySelector('[data-motivo]')).toBeNull();
  });

  it('trocar o app relê com o app novo', async () => {
    backend.on('GET', /^\/api\/aprendizado\/alcance$/, () => json(RESPOSTA));
    await abrir();
    await waitFor(() => expect(container.querySelector('[data-resumo]')).not.toBeNull());
    await setValue(container.querySelector('select')!, 'notes');
    await waitFor(() => expect(backend.callsTo('GET', /aprendizado\/alcance/).some((c) => c.query.get('app') === 'notes')).toBe(true));
  });

  it('persona sem vínculo com o app: diz isso e não lista nada', async () => {
    backend.on('GET', /^\/api\/aprendizado\/alcance$/, () => json({ ...RESPOSTA, personas: [{ profile_id: 'outra', aparelhos: [] }] }));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('não tem vínculo ativo com este app'));
    expect(container.querySelector('[data-resumo]')).toBeNull();
  });

  it('sem a rota (404) lê o exemplo e AVISA; app desconhecido e outros erros viram erro, nunca exemplo', async () => {
    backend.on('GET', /^\/api\/aprendizado\/alcance$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Dados de exemplo'));
    expect(container.querySelector('[data-motivo="fora_do_escopo"]')).not.toBeNull();
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /^\/api\/aprendizado\/alcance$/, () => apiError(404, 'app_desconhecido', 'O app não está cadastrado.'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Tentar de novo'));
    expect(text(container)).not.toContain('Dados de exemplo');
  });

  it('app sem receita nem fluxo: diz que não há', async () => {
    backend.on('GET', /^\/api\/aprendizado\/alcance$/, () => json({ ...RESPOSTA, itens: [] }));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('não tem receita ativa nem fluxo ligado ou candidato'));
  });
});
