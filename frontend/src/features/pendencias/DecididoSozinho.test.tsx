// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useSessionStore } from '../../store/session';
import { useUiStore } from '../../store/ui';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
import { desdeDoPeriodo, fatosEmPortugues, lerListaDecidida, motivoDaRegra } from './decididas';
import { PendenciasPage } from './PendenciasPage';
import { usePendenciasStore } from './store';

const AGORA = Date.now();
const ha = (h: number) => new Date(AGORA - h * 3_600_000).toISOString();

const PERGUNTA = {
  id: 1, fila: 'pergunta', item_ref: 'r-1', regra: '31.43-pergunta-24h', efeito: 'A pergunta sem resposta havia 24 h foi encerrada.',
  fatos: { horas: 24, desde: '2026-10-03T10:00:00.000Z', estado_final: 'cancelled' }, decidida_em: ha(2),
  desfeita: false, desfeita_em: null, desfeita_por: null, motivo_do_desfazer: null, pode_desfazer: false,
  acao_do_desfazer: 'Desfazer', por_que_nao: 'não dá para desfazer automaticamente: a execução foi encerrada e não reabre',
  prazo_ate: ha(-100),
};
const APRENDIZADO = {
  id: 2, fila: 'aprendizado', item_ref: 'receita:180', regra: 'auto:qa_revisar v1', efeito: 'Receita confirmado(a) pela plataforma, sem esperar o dono.',
  fatos: { kind: 'receita', para: 'published', de: 'validated', usos: 5 }, decidida_em: ha(5), desfeita: false, desfeita_em: null,
  desfeita_por: null, motivo_do_desfazer: null, pode_desfazer: true, acao_do_desfazer: 'Desligar', por_que_nao: null, prazo_ate: ha(-100),
};
const DESFEITA = {
  ...APRENDIZADO, id: 3, item_ref: 'receita:9', desfeita: true, desfeita_em: ha(1), desfeita_por: 'ana', motivo_do_desfazer: 'não era para publicar',
  pode_desfazer: false, por_que_nao: 'já foi desfeita',
};

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;
let itens: unknown[];

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  itens = [PERGUNTA, APRENDIZADO, DESFEITA];
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/pedidos$/, () => json({ items: [], proximo_cursor: null, total_por_estado: {} }));
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
  backend.on('GET', /^\/api\/approvals/, () => json([]));
  backend.on('GET', /^\/api\/personas$/, () => json([]));
  backend.on('GET', /^\/api\/decisoes-automaticas/, () => json({
    itens, total: itens.length, regras: ['31.43-pergunta-24h', 'auto:qa_revisar v1'], desfazer_dias: 7 }));
  const snap = makeSnapshot();
  useAppStore.setState({ ...initialDataState, hydrated: true, health: snap.health, settings: snap.settings, runs: [] });
  usePendenciasStore.setState({ aprendizado: null, aprovacoes: null, personas: null, falhou: false });
  useContagemDoAprendizado.setState({ pendentes: null });
  useSessionStore.setState({ operator: 'ana' });
  useUiStore.getState().navegar({ tela: 'pendencias', query: { aba: 'decididas' } }, 'replace');
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useSessionStore.setState({ operator: null });
});

const abrir = async () => {
  await act(async () => { root.render(<PendenciasPage />); });
  await waitFor(() => expect(container.querySelectorAll('li[data-decidida]').length).toBeGreaterThan(0));
};

describe('aba "Decidido sozinho"', () => {
  it('abre pela aba do link, lista o efeito, o porquê e os fatos em português, e a outra aba segue disponível', async () => {
    await abrir();
    const p = container.querySelector('li[data-decidida="1"]') as HTMLElement;
    expect(text(p)).toContain('A pergunta sem resposta havia 24 h foi encerrada.');
    expect(text(p)).toContain('Por quê: Pergunta sem resposta por tempo demais');
    expect(text(p)).toContain('esperou 24 h');
    expect(text(p)).toContain('ficou cancelado');
    expect(byRole('tab', /Decidido sozinho/, container).getAttribute('aria-selected')).toBe('true');
    await click(byRole('tab', /Esperando você/, container));
    expect(useUiStore.getState().rota.query.aba).toBeUndefined();
    await waitFor(() => expect(text(container)).toContain('Nada aguardando você'));
  });

  it('sem volta segura, a linha diz o porquê no lugar do botão; com volta, o botão é "Desligar"', async () => {
    await abrir();
    const p = container.querySelector('li[data-decidida="1"]') as HTMLElement;
    expect(p.querySelector('button')).toBeNull();
    expect(text(p.querySelector('[data-sem-volta]') as HTMLElement)).toContain('não dá para desfazer automaticamente');
    const a = container.querySelector('li[data-decidida="2"]') as HTMLElement;
    expect(text(byRole('button', /^Desligar$/, a))).toBe('Desligar');
    expect(text(a)).not.toMatch(/voltar para revisão/i);
  });

  it('desligar abre o motivo em linha (sem modal), manda a confirmação e recarrega a lista', async () => {
    backend.on('POST', /^\/api\/decisoes-automaticas\/2\/desfazer$/, () => {
      itens = [PERGUNTA, { ...APRENDIZADO, desfeita: true, desfeita_em: ha(0), desfeita_por: 'ana', motivo_do_desfazer: 'engano',
                           pode_desfazer: false, por_que_nao: 'já foi desfeita' }, DESFEITA];
      return json({ ...APRENDIZADO, desfeita: true, desfeita_agora: true });
    });
    await abrir();
    const a = container.querySelector('li[data-decidida="2"]') as HTMLElement;
    await click(byRole('button', /^Desligar$/, a));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    const campo = a.querySelector('input[type="text"], input:not([type])') as HTMLInputElement;
    await setValue(campo, 'engano');
    await click(byRole('button', /Desligar esta decisão/, a));
    await waitFor(() => expect(backend.callsTo('POST', /desfazer$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /desfazer$/)[0]?.body).toEqual({ confirmar: true, motivo: 'engano' });
    await waitFor(() => expect(text(container.querySelector('li[data-decidida="2"]') as HTMLElement)).toContain('Desfeita'));
    expect(text(container.querySelector('li[data-decidida="2"]') as HTMLElement)).toContain('engano');
  });

  it('a recusa do servidor aparece na própria linha e nada é marcado como desfeito', async () => {
    backend.on('POST', /desfazer$/, () => apiError(409, 'sem_inversa_segura', 'não dá para desfazer automaticamente: o item já mudou de estado'));
    await abrir();
    const a = container.querySelector('li[data-decidida="2"]') as HTMLElement;
    await click(byRole('button', /^Desligar$/, a));
    await click(byRole('button', /Desligar esta decisão/, a));
    await waitFor(() => expect(text(a)).toContain('o item já mudou de estado'));
    expect(text(a)).not.toContain('Desfeita');
  });

  it('o filtro por regra e o período vão para a leitura', async () => {
    await abrir();
    const select = container.querySelector('select[aria-label="Filtrar por regra"]') as HTMLSelectElement;
    await setValue(select, '31.43-pergunta-24h');
    const leituras = () => backend.callsTo('GET', /^\/api\/decisoes-automaticas$/);
    await waitFor(() => expect(leituras().some((c) => c.query.get('regra') === '31.43-pergunta-24h')).toBe(true));
    expect(leituras().at(-1)?.query.get('desde')).not.toBeNull();
    await click(byRole('radio', /^Tudo$/, container));
    await waitFor(() => expect(leituras().at(-1)?.query.has('desde')).toBe(false));
  });

  it('esconder as desfeitas pede só as não desfeitas', async () => {
    await abrir();
    await click(container.querySelector('input[type="checkbox"]') as HTMLElement);
    await waitFor(() => expect(backend.callsTo('GET', /^\/api\/decisoes-automaticas$/).some((c) => c.query.get('desfeitas') === 'nao')).toBe(true));
  });

  it('estado vazio claro', async () => {
    itens = [];
    await act(async () => { root.render(<PendenciasPage />); });
    await waitFor(() => expect(text(container)).toContain('Nada decidido sozinho neste período'));
    expect(container.querySelectorAll('li[data-decidida]')).toHaveLength(0);
  });

  it('leitura que falha avisa, sem inventar lista', async () => {
    backend.on('GET', /^\/api\/decisoes-automaticas/, () => apiError(500, 'boom', 'erro'));
    await act(async () => { root.render(<PendenciasPage />); });
    await waitFor(() => expect(text(container)).toContain('Não foi possível ler o que foi decidido'));
  });
});

describe('leitura tolerante e textos', () => {
  it('descarta o que não tem id ou fila conhecida e usa valores neutros', () => {
    const l = lerListaDecidida({ itens: [{ id: 1, fila: 'pergunta' }, { id: 'x', fila: 'pergunta' }, { id: 2, fila: 'outra' }, null], regras: ['a', 3] });
    expect(l.itens).toHaveLength(1);
    expect(l.itens[0]).toMatchObject({ pode_desfazer: false, acao_do_desfazer: 'Desfazer', fatos: {}, desfeita: false });
    expect(l.regras).toEqual(['a']);
    expect(l.desfazer_dias).toBe(7);
    expect(lerListaDecidida(undefined).itens).toEqual([]);
  });

  it('fatos em português e período', () => {
    expect(fatosEmPortugues({ horas: 24, para: 'published', de: 'validated', kind: 'licao', xis: 1 }))
      .toEqual(['esperou 24 h', 'passou a publicado', 'estava validado', 'xis: 1']);
    expect(motivoDaRegra('auto:qa_para_aprovar v1')).toContain('Aprovação automática');
    expect(motivoDaRegra('alguma')).toBe('Regra alguma');
    expect(desdeDoPeriodo('tudo', AGORA)).toBeNull();
    expect(desdeDoPeriodo('7d', AGORA)).toBe(new Date(AGORA - 7 * 86_400_000).toISOString());
  });
});
