// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { Approval } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useSessionStore } from '../../store/session';
import { useUiStore } from '../../store/ui';
import { makePersona, makeRun, makeSession, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { ParaAprovarTab } from '../aprendizado/ParaAprovarTab';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
import type { EntradaDoLivro } from '../aprendizado/model';
import { MenuLateral } from '../topbar/MenuLateral';
import { TopBar } from '../topbar/TopBar';
import { montarPendencias } from './modelo';
import { PendenciasPage } from './PendenciasPage';
import { usePendenciasStore } from './store';

/**
 * Rodada 2 da revisão de UX, tarefa 14: a regra de contagem das pendências. Uma pendência é o que depende de uma
 * pessoa, de QUATRO origens (aprendizado, persona, execução, intervenção), e o número é sempre o tamanho da lista que
 * `montarPendencias` devolve. Dados de exemplo em cada origem, o caso todas vazias, e a prova de que o selo do menu, o
 * chip do topo e a lista da caixa mostram o mesmo número. Prova `simulated`.
 */

const SEM_OBJETIVOS = { succeeded: 0, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 };

const ITEM = (ref: string, over: Partial<EntradaDoLivro> = {}): EntradaDoLivro => ({
  kind: 'receita', ref, state: 'validated', native_status: 'validated', title: `Receita ${ref}`, app: 'instagram',
  origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true,
  created_at: '2026-09-28T10:00:00Z', state_at: '2026-09-28T10:00:00Z', last_used_at: null, uses: 2,
  evidence: { for: 3, against: 0 }, count: null, detail: null, acoes: [], por_que_nao_publica: null, ...over,
});

const APROVACAO = (id: string, over: Partial<Approval> = {}): Approval => ({
  id, profile_id: 'p1', run_id: null, objective_id: null, step_id: null, capability: 'comentar', target: '@alvo',
  summary: `Comentário ${id}`, generated_content: 'oi', approved_content: null, content: 'oi', status: 'pending',
  created_at: '2026-09-29T10:00:00Z', decided_at: null, decided_note: null, decided_by: null, interaction_id: null, ...over,
});

const EXECUCAO_PARADA = makeRun({
  id: 'r-parada', short_id: 'r-parada', command: 'Siga o perfil X', status: 'needs_input', counts: SEM_OBJETIVOS,
  created_at: new Date(Date.now() - 2 * 3_600_000).toISOString(), status_detail: 'Qual perfil seguir?',
});
const EXECUCAO_OK = makeRun({ id: 'r-ok', status: 'completed', counts: { ...SEM_OBJETIVOS, succeeded: 1 } });

const PRESA = makePersona('p2', 'Bia Nunes', {
  username: 'bia.nunes', session: { ...makeSession('auth_challenge', 'android-02'), verified_at: '2026-09-30T07:00:00Z' },
});
const PRONTA = makePersona('p3', 'Caio Souza', { username: 'caio', session: makeSession('session_ready', 'android-04') });

describe('regra de contagem das pendências (puro)', () => {
  const vazio = { aprendizado: [], aprovacoes: [], execucoes: [], personas: [] };

  it('todas as origens vazias: zero pendências (e fontes ainda não lidas também)', () => {
    expect(montarPendencias(vazio)).toHaveLength(0);
    expect(montarPendencias({ aprendizado: null, aprovacoes: null, execucoes: [], personas: null })).toHaveLength(0);
    expect(montarPendencias({ aprendizado: null, aprovacoes: null, execucoes: [] })).toHaveLength(0);
  });

  it('origem Aprendizado: cada item da fila "Para aprovar" é uma pendência', () => {
    const lista = montarPendencias({ ...vazio, aprendizado: [ITEM('1'), ITEM('2')] });
    expect(lista.map((p) => p.origem)).toEqual(['aprendizado', 'aprendizado']);
  });

  it('origem Persona: só a aprovação pendente conta; a já decidida não', () => {
    const lista = montarPendencias({
      ...vazio, aprovacoes: [APROVACAO('a1'), APROVACAO('a2', { status: 'approved' }), APROVACAO('a3', { status: 'rejected' })],
    });
    expect(lista.map((p) => p.chave)).toEqual(['persona:a1']);
  });

  it('origem Execução: só `needs_input` conta; concluída, em andamento e com problemas não', () => {
    const lista = montarPendencias({
      ...vazio,
      execucoes: [EXECUCAO_PARADA, EXECUCAO_OK, makeRun({ id: 'r-roda', status: 'running' }),
                  makeRun({ id: 'r-cwi', status: 'completed_with_issues', counts: { ...SEM_OBJETIVOS, waiting_user: 1 } })],
    });
    expect(lista.map((p) => p.chave)).toEqual(['execucao:r-parada']);
  });

  it('origem Intervenção: só a persona com conta cuja sessão pede uma pessoa; sessão pronta e persona sem conta não', () => {
    const lista = montarPendencias({
      ...vazio, personas: [PRESA, PRONTA, makePersona('p4', 'Sem Conta', { session: makeSession('needs_person') })],
    });
    expect(lista.map((p) => p.chave)).toEqual(['intervencao:p2']);
  });

  it('as quatro origens juntas somam quatro, e o total é o tamanho da lista', () => {
    const lista = montarPendencias({
      aprendizado: [ITEM('1')], aprovacoes: [APROVACAO('a1')], execucoes: [EXECUCAO_PARADA], personas: [PRESA],
    });
    expect(lista).toHaveLength(4);
    expect(new Set(lista.map((p) => p.origem))).toEqual(new Set(['aprendizado', 'persona', 'execucao', 'intervencao']));
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  const snap = makeSnapshot();
  useAppStore.setState({
    ...initialDataState, hydrated: true, health: snap.health, settings: snap.settings, runs: [EXECUCAO_PARADA, EXECUCAO_OK],
  });
  usePendenciasStore.setState({ aprendizado: null, aprovacoes: null, personas: null, falhou: false,
                               falhas: { aprendizado: false, aprovacoes: false, personas: false } });
  useContagemDoAprendizado.setState({ pendentes: null });
  useSessionStore.setState({ operator: 'ana' });
  useUiStore.getState().navegar({ tela: 'pendencias' }, 'replace');
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useSessionStore.setState({ operator: null });
});

const chipDoTopo = () => [...container.querySelectorAll('[aria-label="Indicadores"] button')]
  .find((b) => text(b as HTMLElement).includes('aguardando você')) as HTMLElement | undefined;
const seloDoMenu = () => container.querySelector('nav a[href="#/pendencias"]') as HTMLElement | null;

describe('o mesmo número em três lugares', () => {
  it('selo do menu, chip do topo e lista da caixa mostram 4 com uma pendência de cada origem', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [ITEM('1')], total: 1 }));
    backend.on('GET', /^\/api\/approvals/, () => json([APROVACAO('a1')]));
    backend.on('GET', /^\/api\/personas$/, () => json([makePersona('p1', 'Ana Lima'), PRESA, PRONTA]));
    await act(async () => { root.render(<><MenuLateral /><TopBar /><PendenciasPage /></>); });
    await waitFor(() => expect(container.querySelectorAll('li[data-origem]')).toHaveLength(4));
    expect(new Set([...container.querySelectorAll('li[data-origem]')].map((l) => l.getAttribute('data-origem'))))
      .toEqual(new Set(['aprendizado', 'persona', 'execucao', 'intervencao']));
    await waitFor(() => expect(seloDoMenu()?.getAttribute('aria-label')).toBe('Pendências, 4 aguardando você'));
    expect(text(chipDoTopo() as HTMLElement)).toBe('4aguardando você');
    expect(text(container.querySelector('[role="radiogroup"]') as HTMLElement)).toContain('Todas (4)');
  });

  it('tudo vazio: o menu não mostra selo, o chip do topo mostra 0 e a caixa diz que nada espera', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
    backend.on('GET', /^\/api\/approvals/, () => json([]));
    backend.on('GET', /^\/api\/personas$/, () => json([PRONTA]));
    useAppStore.setState({ runs: [EXECUCAO_OK] });
    await act(async () => { root.render(<><MenuLateral /><TopBar /><PendenciasPage /></>); });
    await waitFor(() => expect(text(container)).toContain('Nada aguardando você'));
    expect(container.querySelectorAll('li[data-origem]')).toHaveLength(0);
    expect(text(chipDoTopo() as HTMLElement)).toBe('0aguardando você');
    expect(seloDoMenu()?.getAttribute('aria-label')).toBe('Pendências');
  });
});

describe('B8: uma origem que não carregou não pode parecer número', () => {
  it('leitura do Aprendizado falha: topo, menu e chips mostram o piso ("3+") e o aviso, não "3" nem "0"', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => apiError(500, 'internal', 'falhou'));
    backend.on('GET', /^\/api\/approvals/, () => json([APROVACAO('a1')]));
    backend.on('GET', /^\/api\/personas$/, () => json([makePersona('p1', 'Ana Lima'), PRESA, PRONTA]));
    await act(async () => { root.render(<><MenuLateral /><TopBar /><PendenciasPage /></>); });
    await waitFor(() => expect(container.querySelectorAll('li[data-origem]')).toHaveLength(3));
    // Topo: o valor é "3+", com o motivo para quem não vê o "+".
    await waitFor(() => expect(text(chipDoTopo() as HTMLElement)).toContain('3+aguardando você'));
    expect(text(chipDoTopo() as HTMLElement)).toContain('Alguma origem não carregou');
    // Menu: o nome COMEÇA pelo texto visível e diz o motivo; o selo é "3+".
    const item = seloDoMenu() as HTMLElement;
    expect(item.getAttribute('aria-label')).toBe('Pendências, 3 ou mais aguardando você; alguma origem não carregou');
    expect(item.textContent).toBe('Pendências 3+');
    // Caixa: a origem que falhou diz "?" (não há número a mostrar), as outras seguem exatas e o total é um piso.
    const chips = text(container.querySelector('[role="radiogroup"]') as HTMLElement);
    expect(chips).toContain('Todas (3+)');
    expect(chips).toContain('Aprendizado (?)');
    expect(chips).toContain('Persona (1)');
    expect(text(container)).toContain('Não foi possível ler todas as origens agora');
  });

  it('com todas as leituras certas nada muda: o número sai sem "+" e sem aviso', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [ITEM('1')], total: 1 }));
    backend.on('GET', /^\/api\/approvals/, () => json([APROVACAO('a1')]));
    backend.on('GET', /^\/api\/personas$/, () => json([makePersona('p1', 'Ana Lima'), PRESA]));
    await act(async () => { root.render(<><MenuLateral /><TopBar /><PendenciasPage /></>); });
    await waitFor(() => expect(container.querySelectorAll('li[data-origem]')).toHaveLength(4));
    expect(text(chipDoTopo() as HTMLElement)).toBe('4aguardando você');
    expect((seloDoMenu() as HTMLElement).getAttribute('aria-label')).toBe('Pendências, 4 aguardando você');
    expect(text(container.querySelector('[role="radiogroup"]') as HTMLElement)).toContain('Todas (4)');
  });
});

describe('Aprendizado "Para aprovar" e Pendências mostram os mesmos itens', () => {
  it('a fila do Aprendizado e as pendências de origem Aprendizado são o mesmo conjunto', async () => {
    const fila = [
      ITEM('12', { title: 'Enviar oi para o contato' }),
      ITEM('li-abc', { kind: 'licao', state: 'candidate', native_status: null, side_effect: false, human_origin: true,
                       title: 'Role a lista antes de procurar o contato' }),
      ITEM('instagram.abrir-conversa@2', { kind: 'habilidade', side_effect: false, title: 'Abrir a conversa com o contato' }),
    ];
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: fila, total: fila.length }));
    backend.on('GET', /^\/api\/aprendizado\/revisar$/, () => json({ itens: [ITEM('40', { title: 'Curtir a última foto' })], total: 1 }));
    backend.on('GET', /^\/api\/approvals/, () => json([]));
    backend.on('GET', /^\/api\/personas$/, () => json([]));
    useAppStore.setState({ runs: [] });

    // Pendências.
    await act(async () => { root.render(<PendenciasPage />); });
    await waitFor(() => expect(container.querySelectorAll('li[data-origem="aprendizado"]')).toHaveLength(3));
    const naCaixa = montarPendencias({ aprendizado: usePendenciasStore.getState().aprendizado, aprovacoes: [], execucoes: [] })
      .map((p) => p.chave.replace(/^aprendizado:/, '')).sort();
    const titulosNaCaixa = [...container.querySelectorAll('li[data-origem="aprendizado"]')].map((l) => text(l as HTMLElement));
    for (const f of fila) expect(titulosNaCaixa.some((t) => t.includes(f.title))).toBe(true);

    // Aprendizado, aba "Para aprovar": a fila (o legado "Revisar" é outra lista e fica de fora de propósito).
    await act(async () => root.render(<ParaAprovarTab />));
    await waitFor(() => expect(container.querySelectorAll('ul[aria-label="Itens para aprovar"] [data-item]')).toHaveLength(3));
    const noAprendizado = [...container.querySelectorAll('ul[aria-label="Itens para aprovar"] [data-item]')]
      .map((l) => l.getAttribute('data-item') as string).sort();
    expect(noAprendizado).toEqual(naCaixa);
    // O item legado a revisar não é pendência: continua valendo até a pessoa decidir (ADR-054).
    expect(naCaixa).not.toContain('receita:40');
  });
});
