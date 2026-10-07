// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { FILTRO_VAZIO, alvosDoFiltro, corpoDoCancelamento, filtroVazio, lerResultadoDoCancelamento, motivoProvavelDeIgnorar } from './filtroDeCancelamento';
import { OperacaoPage } from './OperacaoPage';

/**
 * 31.199: cancelar só alguns alvos de uma operação por filtro (adendo v1.112, `POST /api/operacoes/{id}/cancelar-alvos`). Prova `simulated`:
 * servidor falso no formato do contrato (commit 38e722a4 da Jev; a rota ainda não está no central).
 */

const A = (id: string, persona: string, estado: string, estagio: string, instancia: string | null, run: string | null = `r-${id}`) => ({
  run_id: run, profile_id: id, persona_nome: persona, instance_id: instancia, estado, estagio, estagios: [], motivo: null, parou_em: null,
});
const ALVOS = [
  A('p1', 'Persona 01', 'em_curso', 'conteudo_lido', 'android-01'),
  A('p2', 'Persona 02', 'bloqueado', 'acao_preparada', 'android-02'),
  A('p3', 'Persona 03', 'concluido', 'resultado_verificado', 'android-02'),
  A('p4', 'Persona 04', 'bloqueado', 'conta', null, null),
];
const OPERACAO = { id: 'op-1', command: 'Comentar', status: 'em_curso', alvos: ALVOS };

describe('o filtro', () => {
  const alvos = ALVOS.map((a) => ({ profile_id: a.profile_id, estado: a.estado as never, estagio: a.estagio as never, instance_id: a.instance_id, run_id: a.run_id }));
  it('sem filtro nenhum não atinge ninguém (nunca "todos")', () => {
    expect(filtroVazio(FILTRO_VAZIO)).toBe(true);
    expect(alvosDoFiltro(alvos, FILTRO_VAZIO)).toEqual([]);
  });
  it('os filtros se SOMAM (E); o que o filtro não cita não restringe', () => {
    expect(alvosDoFiltro(alvos, { ...FILTRO_VAZIO, estados: ['bloqueado'] }).map((a) => a.profile_id)).toEqual(['p2', 'p4']);
    expect(alvosDoFiltro(alvos, { ...FILTRO_VAZIO, estados: ['bloqueado'], instance_ids: ['android-02'] }).map((a) => a.profile_id)).toEqual(['p2']);
    expect(alvosDoFiltro(alvos, { ...FILTRO_VAZIO, profile_ids: ['p1', 'p3'], estagios: ['conteudo_lido'] }).map((a) => a.profile_id)).toEqual(['p1']);
  });
  it('alvo sem o campo (sem aparelho, sem estágio) não casa com um filtro que o cita', () => {
    expect(alvosDoFiltro(alvos, { ...FILTRO_VAZIO, instance_ids: ['android-01'] }).map((a) => a.profile_id)).toEqual(['p1']);
  });
  it('o corpo leva só os filtros usados', () => {
    expect(corpoDoCancelamento({ ...FILTRO_VAZIO, estados: ['bloqueado'] })).toEqual({ estados: ['bloqueado'] });
  });
  it('o que o painel já sabe que o central ignora: sem execução e já terminou', () => {
    expect(motivoProvavelDeIgnorar({ run_id: null, estado: 'bloqueado' })).toBe('sem_execucao');
    expect(motivoProvavelDeIgnorar({ run_id: 'r', estado: 'concluido' })).toBe('ja_terminou');
    expect(motivoProvavelDeIgnorar({ run_id: 'r', estado: 'bloqueado' })).toBeNull();      // bloqueado pode ser o que espera o liberar: a execução segue aberta
  });
  it('o leitor do resultado: sem a lista cancelados não é resposta; ignorado torto cai', () => {
    expect(lerResultadoDoCancelamento({})).toBeNull();
    expect(lerResultadoDoCancelamento(null)).toBeNull();
    const r = lerResultadoDoCancelamento({ cancelados: ['p1', 7], ignorados: [{ profile_id: 'p3', motivo: 'ja_terminou' }, { motivo: 'x' }], operacao: { id: 'op-1' } })!;
    expect(r.cancelados).toEqual(['p1']);
    expect(r.ignorados).toEqual([{ profile_id: 'p3', motivo: 'ja_terminou' }]);
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
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json(OPERACAO));
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useToastStore.setState({ toasts: [] });
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
  });

  const abrir = async () => {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'], query: {} } });
    await act(async () => root.render(<><OperacaoPage /><ConfirmHost /></>));
    await click(await waitFor(() => byRole('button', /^Cancelar alvos$/, container)));
    return waitFor(() => byRole('dialog', /Cancelar alguns alvos/));
  };
  const marcar = async (d: HTMLElement, nome: RegExp) => { await click(byRole('checkbox', nome, d)); };

  it('sem filtro marcado não há o que revisar; marcar um mostra quantos alvos atinge (e quantos o central ignora)', async () => {
    const d = await abrir();
    const revisar = byRole('button', /^Revisar e cancelar/, d);
    expect(revisar.getAttribute('aria-disabled')).toBe('true');
    expect(text(d)).toContain('Nenhum filtro marcado.');
    await marcar(d, /^Estado Bloqueado$/);
    expect(text(d.querySelector('[data-atingidos]')!)).toContain('O filtro atinge 2 alvos, dos quais 1 tem execução aberta.');   // p4 não tem execução
    expect(byRole('button', /^Revisar e cancelar$/, d).getAttribute('aria-disabled')).not.toBe('true');
    await marcar(d, /^Aparelho android-01$/);                                                    // bloqueado E android-01: ninguém
    expect(text(d.querySelector('[data-atingidos]')!)).toContain('O filtro atinge 0 alvos');
    expect(byRole('button', /^Revisar e cancelar/, d).getAttribute('aria-disabled')).toBe('true');
  });

  it('confirma com a lista dos atingidos, envia só os filtros marcados e ecoa cancelados e ignorados', async () => {
    backend.on('POST', /^\/api\/operacoes\/op-1\/cancelar-alvos$/, () => json({
      cancelados: ['p2'], ignorados: [{ profile_id: 'p3', motivo: 'ja_terminou' }], operacao: OPERACAO,
    }));
    const d = await abrir();
    await marcar(d, /^Aparelho android-02$/);
    await click(byRole('button', /^Revisar e cancelar$/, d));
    const lista = d.querySelector('ul[aria-label="Alvos atingidos"]')!;
    expect(text(lista)).toContain('Persona 02');
    expect(text(lista)).toContain('Persona 03');
    expect(text(lista)).toContain('já terminou: o central o ignora');
    expect(text(d)).toContain('Cancelar 1 alvo:');
    expect(backend.callsTo('POST', /cancelar-alvos/)).toHaveLength(0);                          // nada foi enviado antes de confirmar
    await click(byRole('button', /^Cancelar 1 alvo$/, d));
    await waitFor(() => expect(d.querySelector('[data-resultado]')).not.toBeNull());
    expect(backend.callsTo('POST', /cancelar-alvos/)[0]!.body).toEqual({ instance_ids: ['android-02'] });
    expect(text(d.querySelector('[data-resultado]')!)).toContain('1 alvo cancelado; 1 ignorado pelo central.');
    expect(text(d.querySelector('[data-cancelado="p2"]')!)).toBe('Persona 02');
    expect(text(d.querySelector('[data-ignorado="p3"]')!)).toContain('já tinha terminado');
    const lidas = backend.callsTo('GET', /^\/api\/operacoes\/op-1$/).length;
    await click(byRole('button', /^Fechar$/, d));
    await waitFor(() => expect(backend.callsTo('GET', /^\/api\/operacoes\/op-1$/).length).toBeGreaterThan(lidas));   // a página relê a operação
  });

  it('"Voltar" na confirmação volta ao filtro sem enviar nada', async () => {
    const d = await abrir();
    await marcar(d, /^Estado Em andamento$/);
    await click(byRole('button', /^Revisar e cancelar$/, d));
    await click(byRole('button', /^Voltar$/, d));
    expect(d.querySelector('[data-atingidos]')).not.toBeNull();
    expect(backend.callsTo('POST', /cancelar-alvos/)).toHaveLength(0);
  });

  it('recusa do central (422 filtro_vazio, 409 já encerrada): mostra o motivo e volta ao filtro', async () => {
    backend.on('POST', /cancelar-alvos$/, () => apiError(409, 'ja_encerrada', 'A operação já terminou (concluida).'));
    const d = await abrir();
    await marcar(d, /^Estado Bloqueado$/);
    await click(byRole('button', /^Revisar e cancelar$/, d));
    await click(byRole('button', /^Cancelar 1 alvo$/, d));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.tone === 'danger' && t.title === 'Não foi possível cancelar os alvos')).toBe(true));
    expect(d.querySelector('[data-resultado]')).toBeNull();
    expect(d.querySelector('[data-atingidos]')).not.toBeNull();
  });

  it('o botão fica travado com o motivo quando a operação já terminou', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...OPERACAO, status: 'concluida' }));
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'], query: {} } });
    await act(async () => root.render(<OperacaoPage />));
    const b = await waitFor(() => byRole('button', /^Cancelar alvos/, container));
    expect(b.getAttribute('aria-disabled')).toBe('true');
  });
});
