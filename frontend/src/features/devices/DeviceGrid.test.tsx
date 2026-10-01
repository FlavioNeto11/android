// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makeInstance, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text } from '../../test/harness';
import { DeviceGrid, agruparPorServidor, estadoDoFiltro } from './DeviceGrid';

/**
 * Tarefa 04 (revisão de UX): a grade agrupa por servidor, recolhe as paradas, alterna Cartões/Lista e aplica
 * `?estado=`. Prova `simulated` (backend falso, nenhum aparelho real).
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const WORKER = {
  id: 'worker-lan-01', name: 'Notebook da LAN', appium_mode: 'local', max_slots: 6, verbs: [],
  state: 'online', observed_state: 'online', maintenance: false, connected: true, local: false,
  resources: {}, devices: [], enrolled_at: '2026-09-17T10:00:00Z',
};
const CENTRAL = { ...WORKER, id: 'central', name: 'Servidor central', local: true };

function semear(workerConectado = true): void {
  const lista = [
    makeInstance(1, { state: 'online', worker_id: 'central' }),
    makeInstance(2, { state: 'stopped', worker_id: 'central' }),
    makeInstance(3, { state: 'stopped', worker_id: 'central' }),
    makeInstance(9, { state: 'stopped', kind: 'external', worker_id: 'worker-lan-01' }),
    makeInstance(10, { state: 'online', kind: 'external', worker_id: 'worker-lan-01' }),
  ];
  const snap = makeSnapshot();
  useAppStore.setState({
    ...initialDataState, settings: snap.settings, health: snap.health, hydrated: true,
    instances: Object.fromEntries(lista.map((i) => [i.id, i])), instanceOrder: lista.map((i) => i.id),
    workers: { central: CENTRAL as never, 'worker-lan-01': { ...WORKER, connected: workerConectado } as never },
  });
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.on('GET', /personas/, () => json([]));
  backend.install();
  try { window.localStorage.clear(); } catch { /* sem armazenamento */ }
  useUiStore.setState({ selectedIds: [], rota: { tela: 'painel', segmentos: [], query: {} } });
  semear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function renderGrade(): Promise<HTMLElement> {
  await act(async () => { root.render(<DeviceGrid />); });
  return container;
}

describe('funções puras', () => {
  it('estadoDoFiltro só aceita estados de aparelho', () => {
    expect(estadoDoFiltro('desconhecido')).toBe('desconhecido');
    expect(estadoDoFiltro('stopped')).toBe('stopped');
    expect(estadoDoFiltro('banana')).toBeNull();
    expect(estadoDoFiltro(undefined)).toBeNull();
  });

  it('agruparPorServidor: central primeiro e, em cada servidor, ativos e paradas separados', () => {
    const s = useAppStore.getState();
    const lista = s.instanceOrder.map((id) => s.instances[id]!);
    const g = agruparPorServidor(lista, s.workers);
    expect(g.map((x) => x.nome)).toEqual(['Servidor central', 'Notebook da LAN']);
    expect(g[0]!.ativos.map((i) => i.id)).toEqual(['android-01']);
    expect(g[0]!.paradas.map((i) => i.id)).toEqual(['android-02', 'android-03']);
    expect(g[1]!.paradas.map((i) => i.id)).toEqual(['android-09']);
  });
});

describe('DeviceGrid — paradas compactas e agrupamento', () => {
  it('mostra os grupos por servidor, o botão "Parados" e o compacto sem "Emulador desligado"', async () => {
    const el = await renderGrade();
    expect(text(el)).toContain('Notebook da LAN');
    expect(text(el)).toContain('Parados (2)');
    expect(text(el)).toContain('Parados (1)');
    expect(text(el)).not.toContain('Emulador desligado');
    expect(text(el)).not.toContain('Sem tarefa em andamento');
  });

  it('recolher "Parados" esconde os cartões e a escolha fica lembrada', async () => {
    const el = await renderGrade();
    expect(el.querySelector('[data-instance-card="android-02"]')).toBeTruthy();
    await click(byRole('button', /Parados \(2\)/, el));
    expect(el.querySelector('[data-instance-card="android-02"]')).toBeNull();
    expect(byRole('button', /Parados \(2\)/, el).getAttribute('aria-expanded')).toBe('false');
    expect(window.localStorage.getItem('cda.painel.paradasRecolhidas')).toContain('"');
    // o servidor remoto segue aberto
    expect(el.querySelector('[data-instance-card="android-09"]')).toBeTruthy();
  });
});

describe('DeviceGrid — alternância Cartões / Lista', () => {
  it('a Lista mostra todos os aparelhos em linhas, e a escolha vai para o localStorage', async () => {
    const el = await renderGrade();
    expect(el.querySelectorAll('tbody tr')).toHaveLength(0);
    await click(byRole('button', /^Lista$/, el));
    expect(el.querySelectorAll('tbody tr')).toHaveLength(5);
    expect(byRole('button', /^Lista$/, el).getAttribute('aria-pressed')).toBe('true');
    expect(window.localStorage.getItem('cda.painel.visao')).toBe('"lista"');
    expect(allByRole('checkbox', /Selecionar android-/, el)).toHaveLength(5);
  });

  it('a preferência gravada abre a Lista de saída', async () => {
    window.localStorage.setItem('cda.painel.visao', '"lista"');
    const el = await renderGrade();
    expect(el.querySelectorAll('tbody tr')).toHaveLength(5);
  });

  it('valor estranho no armazenamento volta para Cartões', async () => {
    window.localStorage.setItem('cda.painel.visao', '"mosaico"');
    const el = await renderGrade();
    expect(el.querySelectorAll('tbody tr')).toHaveLength(0);
    expect(byRole('button', /^Cartões$/, el).getAttribute('aria-pressed')).toBe('true');
  });
});

describe('DeviceGrid — visão no link e preferência no navegador (D3, RF-08)', () => {
  it('sem `?visao=` (o menu leva à tela limpa) abre a preferência; trocar grava no link, sem empilhar, e no navegador', async () => {
    window.localStorage.setItem('cda.painel.visao', '"lista"');
    const el = await renderGrade();
    expect(el.querySelectorAll('tbody tr')).toHaveLength(5);
    const antes = window.history.length;
    await click(byRole('button', /^Cartões$/, el));
    expect(el.querySelectorAll('tbody tr')).toHaveLength(0);
    expect(useUiStore.getState().rota.query.visao).toBe('cards');
    expect(window.location.hash).toMatch(/^#\/painel\?(.*&)?visao=cards/);
    expect(window.history.length).toBe(antes);
    expect(window.localStorage.getItem('cda.painel.visao')).toBe('"cards"');
  });

  it('`?visao=` no link manda sobre a preferência (link colado ou Voltar)', async () => {
    window.localStorage.setItem('cda.painel.visao', '"lista"');
    useUiStore.getState().navegar({ tela: 'painel', query: { visao: 'cards' } }, 'replace');
    const el = await renderGrade();
    expect(el.querySelectorAll('tbody tr')).toHaveLength(0);
    expect(byRole('button', /^Cartões$/, el).getAttribute('aria-pressed')).toBe('true');
    await act(async () => useUiStore.getState().navegar({ tela: 'painel', query: { visao: 'lista' } }, 'replace'));
    expect(el.querySelectorAll('tbody tr')).toHaveLength(5);
    // Ler o link não mexe na preferência: ela muda só quando a pessoa escolhe.
    expect(window.localStorage.getItem('cda.painel.visao')).toBe('"lista"');
  });
});

describe('DeviceGrid — filtro ?estado=', () => {
  it('estado=desconhecido mostra só os aparelhos do servidor fora do ar e oferece "Limpar filtro"', async () => {
    semear(false);
    useUiStore.setState({ rota: { tela: 'painel', segmentos: [], query: { estado: 'desconhecido' } } });
    const el = await renderGrade();
    expect(text(el)).toContain('Filtro: desconhecidos (2)');
    expect(el.querySelector('[data-instance-card="android-09"]')).toBeTruthy();
    expect(el.querySelector('[data-instance-card="android-10"]')).toBeTruthy();
    expect(el.querySelector('[data-instance-card="android-01"]')).toBeNull();
    expect(byRole('button', /Limpar filtro/, el)).toBeTruthy();
  });

  it('estado inválido na URL não filtra nada', async () => {
    useUiStore.setState({ rota: { tela: 'painel', segmentos: [], query: { estado: 'banana' } } });
    const el = await renderGrade();
    expect(text(el)).not.toContain('Filtro:');
    expect(el.querySelector('[data-instance-card="android-01"]')).toBeTruthy();
  });

  it('RF-01: com filtro, a seleção escondida não entra na ação e "Selecionar todos" pega só o que se vê', async () => {
    // android-01 (online) marcado antes, e o link abre só os parados: ele fica fora da vista.
    useUiStore.setState({ selectedIds: ['android-01'], rota: { tela: 'painel', segmentos: [], query: { estado: 'stopped' } } });
    const el = await renderGrade();
    // A barra avisa, mas não oferece ação sobre quem não aparece.
    expect(text(el)).toContain('0 selecionados');
    expect(text(el)).toContain('(1 fora do filtro atual)');
    expect(el.querySelector('[role="toolbar"]')).toBeNull();
    const barra = el.querySelector('[data-barra-de-selecao]') as HTMLElement;
    expect(allByRole('button', /^Iniciar$|^Parar$|^Reiniciar$|Mais ações/, barra)).toHaveLength(0);

    await click(byRole('button', /^Selecionar tod/, el));
    // Só os três parados visíveis (02, 03 e 09); o online escondido saiu da seleção.
    expect(useUiStore.getState().selectedIds.slice().sort()).toEqual(['android-02', 'android-03', 'android-09']);
    expect(byRole('toolbar', /Ação em 3 aparelhos/, el)).toBeTruthy();
    expect(text(el)).not.toContain('fora do filtro');
  });

  it('RF-01: seleção lembrada com ids escondidos: a barra age só nos visíveis e conta os demais à parte', async () => {
    useUiStore.setState({ selectedIds: ['android-01', 'android-02'], rota: { tela: 'painel', segmentos: [], query: { estado: 'stopped' } } });
    const el = await renderGrade();
    expect(byRole('toolbar', /Ação em 1 aparelho$/, el)).toBeTruthy();
    expect(text(el)).toContain('1 selecionado');
    expect(text(el)).toContain('(1 fora do filtro atual)');
  });

  it('filtro sem resultado explica e oferece limpar', async () => {
    useUiStore.setState({ rota: { tela: 'painel', segmentos: [], query: { estado: 'error' } } });
    const el = await renderGrade();
    expect(text(el)).toContain('Nenhum aparelho neste estado');
  });
});

// RF-40 (prova simulada 13): o aparelho de servidor fora do ar é "Desconhecido" na linha e no cartão, e nada ali
// oferece "Iniciar" clicável; a barra em lote não age nele e diz quantos ficaram de fora.
describe('DeviceGrid — servidor sem resposta (RF-40)', () => {
  const linha = (el: HTMLElement, id: string) => el.querySelector(`[data-instance-row="${id}"]`) as HTMLElement;
  const cartao = (el: HTMLElement, id: string) => el.querySelector(`[data-instance-card="${id}"]`) as HTMLElement;

  it('na Lista: selo "Desconhecido" e o verbo do estado guardado indisponível com o motivo no botão', async () => {
    semear(false);
    useUiStore.setState({ rota: { tela: 'painel', segmentos: [], query: { visao: 'lista' } } });
    const el = await renderGrade();
    expect(text(linha(el, 'android-09'))).toContain('Desconhecido');
    const iniciar = byRole('button', /^Iniciar/, linha(el, 'android-09'));
    expect(iniciar.getAttribute('aria-disabled')).toBe('true');
    expect(text(iniciar)).toContain('Servidor sem resposta');
    // O aparelho do central parado continua com "Iniciar" de verdade.
    expect(byRole('button', /^Iniciar/, linha(el, 'android-02')).getAttribute('aria-disabled')).toBeNull();
  });

  it('nos Cartões: o mesmo, inclusive o "Hibernar" do aparelho que constava online', async () => {
    semear(false);
    // A hibernação vem ligada no snapshot de teste (`fixtures.makeSnapshot`).
    const el = await renderGrade();
    const iniciar = byRole('button', /^Iniciar/, cartao(el, 'android-09'));
    expect(iniciar.getAttribute('aria-disabled')).toBe('true');
    expect(text(iniciar)).toContain('Servidor sem resposta');
    const hibernar = byRole('button', /^Hibernar android-10/, cartao(el, 'android-10'));
    expect(hibernar.getAttribute('aria-disabled')).toBe('true');
    expect(hibernar.getAttribute('aria-label')).toContain('Servidor sem resposta');
  });

  it('barra em lote: age só nos de estado conhecido e conta os ignorados', async () => {
    semear(false);
    useUiStore.setState({ selectedIds: ['android-02', 'android-09'], rota: { tela: 'painel', segmentos: [], query: {} } });
    const el = await renderGrade();
    expect(byRole('toolbar', /Ação em 1 aparelho$/, el)).toBeTruthy();
    expect(text(el)).toContain('2 selecionados');
    expect(text(el)).toContain('(1 ignorado: servidor sem resposta)');
  });

  it('barra em lote: só desconhecidos marcados, nenhuma ação, e a nota diz por quê', async () => {
    semear(false);
    useUiStore.setState({ selectedIds: ['android-09', 'android-10'], rota: { tela: 'painel', segmentos: [], query: { estado: 'desconhecido' } } });
    const el = await renderGrade();
    expect(el.querySelector('[role="toolbar"]')).toBeNull();
    const barra = el.querySelector('[data-barra-de-selecao]') as HTMLElement;
    expect(allByRole('button', /^Iniciar$|^Parar$|^Reiniciar$|Mais ações/, barra)).toHaveLength(0);
    expect(text(barra)).toContain('(2 ignorados: servidor sem resposta)');
    expect(text(barra)).toContain('servidor deles não está respondendo');
    expect(text(barra)).not.toContain('Nenhum aparelho marcado aparece neste filtro');
  });
});
