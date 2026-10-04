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
import { ApiError } from '../../api/client';
import { hashDe } from '../../lib/rotas';
import { hrefDoItem } from '../aprendizado/DetalheRico';
import {
  desdeDoPeriodo, falhaDoDesfazer, fatosEmPortugues, itemDoAprendizado, lerListaDecidida, motivoDaRegra, quemDesfez, tituloDoGrupo,
} from './decididas';
import { PendenciasPage } from './PendenciasPage';
import { usePendenciasStore } from './store';

const AGORA = Date.now();
const ha = (h: number) => new Date(AGORA - h * 3_600_000).toISOString();

const PERGUNTA = {
  id: 1, fila: 'pergunta', item_ref: 'r-1', item_nome: null, run_id: 'r-20261003100000-abc123', regra: '31.43-pergunta-24h', efeito: 'A pergunta sem resposta havia 24 h foi encerrada.',
  fatos: { horas: 24, desde: '2026-10-03T10:00:00.000Z', estado_final: 'cancelled' }, decidida_em: ha(2),
  desfeita: false, desfeita_em: null, desfeita_por: null, motivo_do_desfazer: null, pode_desfazer: false,
  acao_do_desfazer: 'Desfazer', por_que_nao: 'não dá para desfazer automaticamente: a execução foi encerrada e não reabre',
  prazo_ate: ha(-100),
};
const APRENDIZADO = {
  id: 2, fila: 'aprendizado', item_ref: 'receita:180', item_nome: 'Abrir a caixa de entrada', run_id: null, regra: 'auto:qa_revisar v1', efeito: 'Receita publicada pela plataforma, sem esperar o dono.',
  fatos: { kind: 'receita', para: 'published', de: 'validated', usos: 5 }, decidida_em: ha(5), desfeita: false, desfeita_em: null,
  desfeita_por: null, motivo_do_desfazer: null, pode_desfazer: true, acao_do_desfazer: 'Desligar', por_que_nao: null, prazo_ate: ha(-100),
};
const DESFEITA = {
  ...APRENDIZADO, id: 3, item_ref: 'receita:9', desfeita: true, desfeita_em: ha(1), desfeita_por: 'panel', motivo_do_desfazer: 'não era para publicar', item_nome: null,
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
    // As decisões do vencimento viram um grupo (28.29): o cartão avulso da pergunta deu lugar ao grupo fechado.
    const p = container.querySelector('li[data-grupo-decidido]') as HTMLElement;
    expect(text(p)).toContain('1 pergunta encerrada por vencimento');
    expect(text(p)).toContain('Por quê: Pergunta sem resposta por tempo demais');
    expect(byRole('tab', /Decidido sozinho/, container).getAttribute('aria-selected')).toBe('true');
    await click(byRole('tab', /Esperando você/, container));
    expect(useUiStore.getState().rota.query.aba).toBeUndefined();
    await waitFor(() => expect(text(container)).toContain('Nada aguardando você'));
  });

  it('sem volta segura, a linha diz o porquê no lugar do botão; com volta, o botão é "Desligar"', async () => {
    await abrir();
    const p = container.querySelector('li[data-grupo-decidido]') as HTMLElement;
    expect(p.querySelector('button')).toBeNull();
    expect(text(p.querySelector('[data-sem-volta]') as HTMLElement)).toContain('não dá para desfazer automaticamente');
    const a = container.querySelector('li[data-decidida="2"]') as HTMLElement;
    expect(text(byRole('button', /^Desligar$/, a))).toBe('Desligar');
    expect(text(a)).not.toMatch(/voltar para revisão/i);
    expect(text(a)).not.toContain('(a)');
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

const OBJETIVO = (id: number, extra: Record<string, unknown> = {}) => ({
  ...PERGUNTA, id, fila: 'objetivo', item_ref: `o-${id}`, run_id: `r-20261003${String(id).padStart(6, '0')}-abc${id}`,
  regra: '31.43-objetivo-encerrado', efeito: 'O objetivo foi encerrado por vencimento.', fatos: {},
  decidida_em: '2026-10-03T14:05:09.000Z', ...extra,
});

describe('28.29: cartão, grupo do vencimento e textos', () => {
  it('o cartão do aprendizado diz qual item foi decidido, com link de verdade para o Livro', async () => {
    await abrir();
    const a = container.querySelector('li[data-decidida="2"]') as HTMLElement;
    const link = a.querySelector('a[data-item-do-livro]') as HTMLAnchorElement;
    expect(link.textContent).toBe('Abrir a caixa de entrada');
    expect(link.getAttribute('href')).toBe(hrefDoItem('receita', '180'));
    expect(link.getAttribute('href')).toContain('item=receita');
    expect(text(a)).toContain('Receita publicada pela plataforma');
  });

  it('sem item_nome mostra "<Tipo> <ref>" e o tipo vem dos fatos ou do prefixo do item_ref', async () => {
    itens = [{ ...APRENDIZADO, item_nome: null }];
    await abrir();
    const link = container.querySelector('li[data-decidida="2"] a[data-item-do-livro]') as HTMLAnchorElement;
    expect(link.textContent).toBe('Receita 180');
    expect(itemDoAprendizado({ item_ref: 'fluxo:a:b', fatos: {} })).toEqual({ kind: 'fluxo', ref: 'a:b' });
    expect(itemDoAprendizado({ item_ref: '9', fatos: { kind: 'tela' } })).toEqual({ kind: 'tela', ref: '9' });
    expect(itemDoAprendizado({ item_ref: 'solto', fatos: {} })).toEqual({ kind: null, ref: 'solto' });
  });

  it('sem link quando o tipo não é do Livro', async () => {
    itens = [{ ...APRENDIZADO, item_nome: null, item_ref: 'estranho:1', fatos: {} }];
    await abrir();
    const a = container.querySelector('li[data-decidida="2"]') as HTMLElement;
    expect(a.querySelector('a')).toBeNull();
    expect(text(a)).toContain('estranho 1');
  });

  it('a confirmação diz "segue publicado" e não repete o estado', async () => {
    itens = [{ ...APRENDIZADO, fatos: { kind: 'fluxo', para: 'published', de: 'published', confirmacao: true, usos: 3 } }];
    await abrir();
    const t = text(container.querySelector('li[data-decidida="2"]') as HTMLElement);
    expect(t).toContain('segue publicado');
    expect(t).not.toContain('passou a');
    expect(t).not.toContain('estava publicado');
    expect(t).not.toContain('confirmacao');
  });

  it('quem desfez vira frase: o painel, o Telegram, a plataforma; outro valor aparece como veio', async () => {
    await abrir();
    const t = text(container.querySelector('li[data-decidida="3"]') as HTMLElement);
    expect(t).toContain('por você, no painel');
    expect(t).not.toContain('por panel');
    expect(quemDesfez('telegram:dono')).toBe('por você, pelo Telegram');
    expect(quemDesfez('plataforma')).toBe('pela plataforma');
    expect(quemDesfez('ana')).toBe('por ana');
    expect(quemDesfez(null)).toBe('');
  });

  it('as decisões do vencimento viram um grupo por fila e regra, fechado, com link para a execução e hora local', async () => {
    itens = [...Array.from({ length: 21 }, (_, i) => OBJETIVO(100 + i)), OBJETIVO(200, { run_id: null }), APRENDIZADO];
    await abrir();
    const grupo = container.querySelector('li[data-grupo-decidido]') as HTMLElement;
    expect(container.querySelectorAll('li[data-grupo-decidido]')).toHaveLength(1);
    expect(text(grupo)).toContain('22 objetivos encerrados por vencimento');
    expect((grupo.querySelector('details') as HTMLDetailsElement).open).toBe(false);
    const linhas = grupo.querySelectorAll('ul li[data-decidida]');
    expect(linhas).toHaveLength(22);
    const primeira = linhas[0] as HTMLElement;
    const a = primeira.querySelector('a') as HTMLAnchorElement;
    expect(a.getAttribute('href')).toBe(hashDe('execucoes', { segmentos: [OBJETIVO(100).run_id] }));
    expect(a.textContent).toMatch(/^execução/);
    expect(text(primeira)).toMatch(/encerrada /);
    expect(text(primeira)).not.toMatch(/\d{4}-\d{2}-\d{2}T|\dZ/);          // nunca o ISO em UTC
    expect(linhas[21]?.querySelector('a')).toBeNull();                      // sem run_id, sem link
    expect(container.querySelector('li[data-decidida="2"]')).not.toBeNull();  // o aprendizado segue como cartão
  });

  it('uma fila e uma regra por grupo, com o plural certo', () => {
    expect(tituloDoGrupo('objetivo', 1)).toBe('1 objetivo encerrado por vencimento');
    expect(tituloDoGrupo('objetivo', 21)).toBe('21 objetivos encerrados por vencimento');
    expect(tituloDoGrupo('pergunta', 1)).toBe('1 pergunta encerrada por vencimento');
    expect(tituloDoGrupo('pergunta', 3)).toBe('3 perguntas encerradas por vencimento');
  });

  it('o grupo só junta o que não tem volta: o que pode ser desfeito segue como cartão', async () => {
    itens = [OBJETIVO(1), OBJETIVO(2, { pode_desfazer: true, por_que_nao: null })];
    await abrir();
    expect(text(container.querySelector('li[data-grupo-decidido]') as HTMLElement)).toContain('1 objetivo encerrado');
    const cartao = container.querySelector('li[data-decidida="2"]:not([data-grupo-decidido] li)') as HTMLElement;
    expect(text(byRole('button', /^Desfazer$/, cartao))).toBe('Desfazer');
  });

  it('o motivo do desfazer aceita 300 caracteres e um 422 vira frase em português', async () => {
    backend.on('POST', /desfazer$/, () => apiError(422, 'validation_error', 'String should have at most 300 characters'));
    await abrir();
    const a = container.querySelector('li[data-decidida="2"]') as HTMLElement;
    await click(byRole('button', /^Desligar$/, a));
    const campo = a.querySelector('input[type="text"], input:not([type])') as HTMLInputElement;
    expect(campo.maxLength).toBe(300);
    await click(byRole('button', /Desligar esta decisão/, a));
    await waitFor(() => expect(text(a)).toContain('O motivo tem no máximo 300 caracteres.'));
    expect(text(a)).not.toContain('String should');
    expect(falhaDoDesfazer(new ApiError(422, 'x', 'cru'))).toBe('O motivo tem no máximo 300 caracteres.');
    expect(falhaDoDesfazer(new ApiError(409, 'x', 'já mudou'))).toBe('já mudou');
    expect(falhaDoDesfazer(new Error('x'))).toBe('Não foi possível desfazer.');
  });

  it('a nota do topo não promete aviso que pode estar desligado', async () => {
    await abrir();
    expect(text(container)).toContain('Quando os avisos estão ligados, você recebe no Telegram um resumo por janela');
  });

  it('"Hoje" é a meia-noite local do dia e o 7 dias segue sendo agora − 7 d', () => {
    const tarde = new Date(2026, 9, 4, 15, 30, 0).getTime();                // 04/10 15:30 local
    expect(desdeDoPeriodo('hoje', tarde)).toBe(new Date(2026, 9, 4, 0, 0, 0, 0).toISOString());
    const cedo = new Date(2026, 9, 4, 0, 5, 0).getTime();
    expect(desdeDoPeriodo('hoje', cedo)).toBe(new Date(2026, 9, 4, 0, 0, 0, 0).toISOString());
    expect(desdeDoPeriodo('7d', tarde)).toBe(new Date(tarde - 7 * 86_400_000).toISOString());
  });

  it('o texto aparece sem rótulo e o estado não se repete', () => {
    expect(fatosEmPortugues({ texto: 'Abrir a caixa…' })).toEqual(['Abrir a caixa…']);
    expect(fatosEmPortugues({ kind: 'fluxo', para: 'published', de: 'published' })).toEqual(['segue publicado']);
    expect(fatosEmPortugues({ para: 'published', de: 'validated' })).toEqual(['passou a publicado', 'estava validado']);
    expect(fatosEmPortugues({ desde: '2026-10-03T10:00:00.000Z' })[0]).toMatch(/^parado desde /);
    expect(fatosEmPortugues({ desde: '2026-10-03T10:00:00.000Z' })[0]).not.toMatch(/UTC|T10/);
  });

  it('o leitor tolera item_nome e run_id ausentes', () => {
    const l = lerListaDecidida({ itens: [{ id: 1, fila: 'objetivo', item_nome: 'x', run_id: 'r-1' }, { id: 2, fila: 'pedido' }] });
    expect(l.itens[0]).toMatchObject({ item_nome: 'x', run_id: 'r-1' });
    expect(l.itens[1]).toMatchObject({ item_nome: null, run_id: null });
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
    expect(new Date(desdeDoPeriodo('hoje', AGORA) as string).getHours()).toBe(0);
  });
});
