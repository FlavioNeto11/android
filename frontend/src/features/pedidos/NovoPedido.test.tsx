// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PedidoPrevia } from '../../api/pedidos';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useToastStore } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { makePedido, makeRun, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { CommandPanel } from '../command/CommandPanel';
import { gatilhoDoQuando, quandoInicial, rruleDe } from './gatilho';
import { NovoPedido } from './NovoPedido';

/**
 * Item 28.9: criar um pedido pelo Comando, com a prévia obrigatória (`POST /api/pedidos/previa`, depois
 * `POST /api/pedidos`). Prova `simulated` — nenhuma rota real foi chamada.
 */
const PREVIA: PedidoPrevia = {
  valido: true, objetivo_sem_destinos: 'Resuma o feed.',
  alvos: { targets: [{ instance_id: 'android-01', profile_id: 'p1', app_id: 'com.x', origem: 'ui' }], questions: [], command_sem_destinos: null, warnings: [] },
  proximas: [
    { gatilho: 0, nominal: '2026-10-03T08:00:00', local: '2026-10-03 08:00 -03:00', utc: '2026-10-03T11:00:00Z', desviado: false, repetido: false },
    { gatilho: 0, nominal: '2026-11-01T01:30:00', local: '2026-11-01 01:30 -02:00', utc: '2026-11-01T03:30:00Z', desviado: false, repetido: true },
    { gatilho: 0, nominal: '2026-11-08T02:30:00', local: '2026-11-08 03:00 -02:00', utc: '2026-11-08T05:00:00Z', desviado: true, repetido: false },
  ],
  intervalo_minimo_s: 86_400, autonomia: { teto: 'observar', exige_aprovacao: [], recusado: ['publicar'] },
  custo: { base: 'sem_base', por_ocorrencia_usd: null, ocorrencias_por_mes: null, por_mes_usd: null },
  bloqueios: [], alertas: [{ codigo: 'persona_sem_sessao', mensagem: 'A persona Ana ainda não tem sessão pronta.' }],
  confirmacao: 'sha256:selo1',
};

let backend: FakeBackend;
let root: Root;
let container: HTMLDivElement;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  window.localStorage.clear();
  backend = new FakeBackend();
  backend.on('GET', /^\/api\/flows\/match$/, () => json(null));
  backend.on('POST', /^\/api\/pedidos\/previa$/, () => json(PREVIA));
  backend.on('POST', /^\/api\/pedidos$/, () => json(makePedido({ id: 'ped_novo', titulo: 'Resuma o feed.' }), 201));
  backend.install();
  useToastStore.setState({ toasts: [] });
  useUiStore.getState().navegar({ tela: 'painel' }, 'replace');
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const corpoDe = (rota: RegExp, n = 0) => backend.callsTo('POST', rota)[n]?.body as Record<string, unknown>;

async function montarDireto(onFechar = vi.fn(), resolver = vi.fn(async () => ({ instance_ids: ['android-01'] }))) {
  await act(async () => { root.render(<NovoPedido comando="Resuma o feed." resolverAlvos={resolver} onFechar={onFechar} />); });
  return { onFechar, resolver };
}

describe('montagem do gatilho (puro)', () => {
  it('agora, horário e repetir viram o gatilho do contrato; faltando o início, não há gatilho', () => {
    const q = { ...quandoInicial(new Date('2026-10-02T12:00:00')), inicio: '2026-10-03T08:00' };
    expect(gatilhoDoQuando(q)).toEqual({ tipo: 'agora', spec: {} });
    expect(gatilhoDoQuando({ ...q, modo: 'horario' })).toEqual({ tipo: 'horario', spec: { dtstart: '2026-10-03T08:00:00' } });
    expect(gatilhoDoQuando({ ...q, modo: 'repetir' })).toEqual({ tipo: 'recorrencia', spec: { dtstart: '2026-10-03T08:00:00', rrule: 'FREQ=DAILY' } });
    expect(gatilhoDoQuando({ ...q, modo: 'horario', inicio: '' })).toBeNull();
    expect(rruleDe({ ...q, frequencia: 'WEEKLY', dias: ['WE', 'MO'], intervalo: 2 })).toBe('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE');
    expect(rruleDe({ ...q, frequencia: 'HOURLY', intervalo: 6 })).toBe('FREQ=HOURLY;INTERVAL=6');
    expect(rruleDe({ ...q, frequencia: 'MONTHLY' })).toBe('FREQ=MONTHLY;BYMONTHDAY=3');
  });
});

describe('prévia e criação', () => {
  it('a prévia é obrigatória: sem ela "Confirmar e criar" não anda; com ela mostra datas, marcas, autonomia e "sem base de custo"', async () => {
    await montarDireto();
    const confirmar = () => byRole('button', /Confirmar e criar/);
    expect(confirmar().getAttribute('aria-disabled')).toBe('true');
    expect(text(confirmar())).toContain('Veja a prévia primeiro');
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(text(container)).toContain('Prévia'));
    // Sem efeito e sem IA: só a rota da prévia foi chamada.
    expect(backend.callsTo('POST', /^\/api\/pedidos$/)).toHaveLength(0);
    const previa = container.querySelector('section[aria-label="Prévia do pedido"]') as HTMLElement;
    expect(text(previa)).toContain('2026-10-03 08:00 -03:00');
    expect(text(previa)).toContain('hora repetida, roda só na primeira');
    expect(text(previa)).toContain('hora desviada');
    expect(text(previa)).toContain('android-01');
    expect(text(previa)).toContain('Fica recusado: publicar');
    expect(text(previa)).toContain('sem base de custo');
    expect(text(previa)).toContain('A persona Ana ainda não tem sessão pronta.');
    expect(corpoDe(/previa$/)).toMatchObject({
      objetivo: 'Resuma o feed.', alvos: { instance_ids: ['android-01'] }, autonomia: 'observar', gatilhos: [{ tipo: 'agora', spec: {} }], proximas: 5,
    });
    expect(confirmar().getAttribute('aria-disabled')).toBeNull();
  });

  it('Confirmar e criar manda o selo da prévia e a idempotency_key, leva ao pedido e fecha', async () => {
    const { onFechar } = await montarDireto();
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(byRole('button', /Confirmar e criar/).getAttribute('aria-disabled')).toBeNull());
    await click(byRole('button', /Confirmar e criar/));
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/pedidos$/)).toHaveLength(1));
    const corpo = corpoDe(/^\/api\/pedidos$/);
    expect(corpo.confirmacao).toBe('sha256:selo1');
    expect(String(corpo.idempotency_key)).toMatch(/^[A-Za-z0-9_.:-]{8,100}$/);
    expect(corpo).not.toHaveProperty('estado');
    await waitFor(() => expect(window.location.hash).toBe('#/pedidos/ped_novo'));
    expect(onFechar).toHaveBeenCalled();
    expect(useToastStore.getState().toasts.map((t) => t.title)).toContain('Pedido criado');
  });

  it('Repetir manda a recorrência com a rrule; mexer em qualquer campo apaga a prévia (nova confirmação)', async () => {
    await montarDireto();
    await click(byRole('button', 'Repetir'));
    await setValue(byRole('textbox', /Começa em/) as HTMLInputElement, '2026-10-03T08:00');
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(text(container)).toContain('Prévia'));
    expect(corpoDe(/previa$/).gatilhos).toEqual([{ tipo: 'recorrencia', spec: { dtstart: '2026-10-03T08:00:00', rrule: 'FREQ=DAILY' } }]);
    await setValue(byRole('textbox', /Máximo de ocorrências/) as HTMLInputElement, '10');
    expect(container.querySelector('section[aria-label="Prévia do pedido"]')).toBeNull();
    expect(byRole('button', /Confirmar e criar/).getAttribute('aria-disabled')).toBe('true');
  });

  it('Acompanhar começa de hora em hora e só observando', async () => {
    await montarDireto();
    await click(byRole('button', 'Acompanhar'));
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(backend.callsTo('POST', /previa$/)).toHaveLength(1));
    const corpo = corpoDe(/previa$/);
    expect(corpo.autonomia).toBe('observar');
    expect((corpo.gatilhos as { spec: { rrule: string } }[])[0]?.spec.rrule).toBe('FREQ=HOURLY');
  });

  it('prévia com bloqueio: mostra o código e não deixa confirmar', async () => {
    backend.on('POST', /^\/api\/pedidos\/previa$/, () => json({
      ...PREVIA, valido: false, confirmacao: null,
      bloqueios: [{ codigo: 'frequencia_abaixo_do_piso', mensagem: 'piso 3600 s, observado 900 s' }],
    }));
    await montarDireto();
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(text(container)).toContain('frequencia_abaixo_do_piso'));
    expect(byRole('button', /Confirmar e criar/).getAttribute('aria-disabled')).toBe('true');
    expect(text(byRole('button', /Confirmar e criar/))).toContain('bloqueios');
  });

  it('previa_desatualizada na criação apaga a prévia e pede outra; a mesma tentativa reaproveita a chave', async () => {
    const chaves: string[] = [];
    let falhar = true;
    backend.on('POST', /^\/api\/pedidos$/, (c) => {
      chaves.push(String((c.body as { idempotency_key: string }).idempotency_key));
      return falhar ? apiError(409, 'previa_desatualizada', 'mudou') : json(makePedido({ id: 'ped_novo' }), 201);
    });
    await montarDireto();
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(byRole('button', /Confirmar e criar/).getAttribute('aria-disabled')).toBeNull());
    await click(byRole('button', /Confirmar e criar/));
    await waitFor(() => expect(text(container)).toContain('Algo mudou desde a prévia'));
    expect(container.querySelector('section[aria-label="Prévia do pedido"]')).toBeNull();
    falhar = false;
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(byRole('button', /Confirmar e criar/).getAttribute('aria-disabled')).toBeNull());
    await click(byRole('button', /Confirmar e criar/));
    await waitFor(() => expect(chaves).toHaveLength(2));
    // Mesmo conteúdo e mesmo selo: a nova tentativa é a MESMA intenção, com a mesma chave (nada duplica).
    expect(chaves[1]).toBe(chaves[0]);
  });

  it('200 deduplicated: avisa que o pedido já existia e leva a ele', async () => {
    backend.on('POST', /^\/api\/pedidos$/, () => json({ ...makePedido({ id: 'ped_velho' }), deduplicated: true }, 200));
    await montarDireto();
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(byRole('button', /Confirmar e criar/).getAttribute('aria-disabled')).toBeNull());
    await click(byRole('button', /Confirmar e criar/));
    await waitFor(() => expect(window.location.hash).toBe('#/pedidos/ped_velho'));
    expect(useToastStore.getState().toasts.map((t) => t.title).join()).toContain('já existia');
  });

  it('erro ao resolver os alvos (ex.: o Automático não achou aparelho) aparece, e nada é enviado', async () => {
    await montarDireto(vi.fn(), vi.fn(async () => { throw Object.assign(new Error('x'), {}); }));
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull());
    expect(backend.callsTo('POST', /previa$/)).toHaveLength(0);
  });
});

describe('integração com o Comando', () => {
  async function comando() {
    window.localStorage.setItem('cda.commandTargetV2', JSON.stringify('selecao'));
    useAppStore.setState({ ...initialDataState });
    useAppStore.getState().hydrate(makeSnapshot());
    useUiStore.setState({ selectedIds: ['android-01'] });
    backend.on('POST', /^\/api\/runs$/, () => json(makeRun()));
    await act(async () => { root.render(<CommandPanel />); });
  }

  it('"Repetir ou acompanhar…" está indisponível sem comando e, com ele, leva à prévia com os aparelhos marcados', async () => {
    await comando();
    const botao = () => byRole('button', /Repetir ou acompanhar/);
    expect(botao().getAttribute('aria-disabled')).toBe('true');
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'Resuma o feed todo dia');
    expect(botao().getAttribute('aria-disabled')).toBeNull();
    await click(botao());
    await click(byRole('button', 'Ver a prévia'));
    await waitFor(() => expect(backend.callsTo('POST', /previa$/)).toHaveLength(1));
    expect(corpoDe(/previa$/)).toMatchObject({ objetivo: 'Resuma o feed todo dia', alvos: { instance_ids: ['android-01'] } });
    // Criar um pedido NÃO cria execução: o Executar de sempre segue intacto.
    expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(0);
    await click(byRole('button', /Confirmar e criar/));
    await waitFor(() => expect(window.location.hash).toBe('#/pedidos/ped_novo'));
  });

  it('comando com senha: o botão do pedido fica indisponível, como o Executar', async () => {
    await comando();
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'entre com a senha: Abc12345xyz no app');
    expect(byRole('button', /Repetir ou acompanhar/).getAttribute('aria-disabled')).toBe('true');
  });
});
