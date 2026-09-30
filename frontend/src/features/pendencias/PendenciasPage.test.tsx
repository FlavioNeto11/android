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
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { useContagemDoAprendizado } from '../aprendizado/contagem';
import type { EntradaDoLivro } from '../aprendizado/model';
import { tituloCurto } from '../runs/filtroExecucoes';
import { MenuLateral } from '../topbar/MenuLateral';
import { PRECISA_DE_PESSOA, montarPendencias } from './modelo';
import { PendenciasPage } from './PendenciasPage';
import { usePendenciasStore } from './store';

const ITEM = (ref: string, over: Partial<EntradaDoLivro> = {}): EntradaDoLivro => ({
  kind: 'receita', ref, state: 'validated', native_status: 'validated', title: `Receita ${ref}`, app: 'instagram',
  origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true,
  created_at: '2026-09-28T10:00:00Z', state_at: '2026-09-28T10:00:00Z', last_used_at: null, uses: 2,
  evidence: { for: 3, against: 0 }, count: null, detail: null, ...over,
});

const APROVACAO = (id: string, over: Partial<Approval> = {}): Approval => ({
  id, profile_id: 'p1', run_id: null, objective_id: null, step_id: null, capability: 'comentar', target: '@alvo',
  summary: `Comentário ${id}`, generated_content: 'oi', approved_content: null, content: 'oi', status: 'pending',
  created_at: '2026-09-29T10:00:00Z', decided_at: null, decided_note: null, decided_by: null, interaction_id: null, ...over,
});

const EXECUCAO = makeRun({
  id: 'r-espera', short_id: 'r-espera', command: 'Siga o perfil X', status: 'running',
  created_at: '2026-09-30T08:00:00Z',
  counts: { succeeded: 0, failed: 0, waiting_user: 2, uncertain: 1, cancelled: 0, running: 0, pending: 0 },
});

const PRESA = makePersona('p2', 'Bia Nunes', {
  username: 'bia.nunes', session: { ...makeSession('auth_challenge', 'android-02'), verified_at: '2026-09-30T07:00:00Z' },
});

describe('montarPendencias (puro)', () => {
  it('uma linha por item, por aprovação e por execução; o total é o tamanho da lista, mais antigas primeiro', () => {
    const lista = montarPendencias({
      aprendizado: [ITEM('1'), ITEM('2', { state_at: '2026-09-20T10:00:00Z' })],
      aprovacoes: [APROVACAO('a1'), APROVACAO('a2', { status: 'approved' })],
      execucoes: [EXECUCAO, makeRun({ id: 'r-ok', counts: { succeeded: 1, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 } })],
      nomeDaPersona: (id) => (id === 'p1' ? 'Ana Lima' : null),
    });
    // 2 do aprendizado + 1 aprovação pendente (a decidida não entra) + 1 execução (com 3 objetivos: uma linha só).
    expect(lista).toHaveLength(4);
    expect(lista.map((p) => p.origem)).toEqual(['aprendizado', 'aprendizado', 'persona', 'execucao']);
    expect(lista[0]?.chave).toBe('aprendizado:receita:2');
    const persona = lista.find((p) => p.origem === 'persona');
    expect(persona?.detalhe).toContain('Ana Lima');
    expect(persona?.destino).toEqual({ tela: 'personas', segmentos: ['p1', 'aprovacoes'] });
    const exec = lista.find((p) => p.origem === 'execucao');
    expect(exec?.detalhe).toContain('3 objetivos');
    expect(exec?.destino).toEqual({ tela: 'execucoes', segmentos: ['r-espera'] });
  });

  it('RF-09: o título da execução é o título curto de Execuções, sem a abertura repetida', () => {
    const run = makeRun({ ...EXECUCAO, id: 'r-curto', command: 'Nas instâncias selecionadas, no QA Messenger, leia o nome do contato. Confirme na tela.' });
    const [linha] = montarPendencias({ aprendizado: [], aprovacoes: [], execucoes: [run] });
    expect(linha?.titulo).toBe(tituloCurto(run.command).titulo);
    expect(linha?.titulo).not.toMatch(/^Nas instâncias/);
  });

  it('RF-03: sessões que só uma pessoa resolve entram, uma por persona com conta; as demais não', () => {
    const lista = montarPendencias({
      aprendizado: [], aprovacoes: [], execucoes: [],
      personas: [
        PRESA,
        makePersona('p3', 'Caio Souza', { username: 'caio', session: makeSession('session_ready', 'android-04') }),
        makePersona('p4', 'Sem Conta', { session: makeSession('needs_person') }),   // sem conta: não é a fila
        makePersona('p5', 'Dora Reis', { username: 'dora', session: makeSession('wrong_account', null) }),
      ],
    });
    expect(lista.map((p) => p.chave)).toEqual(['intervencao:p2', 'intervencao:p5']);
    expect(lista[0]).toMatchObject({ origem: 'intervencao', titulo: 'Bia Nunes (@bia.nunes)', desde: '2026-09-30T07:00:00Z',
                                     destino: { tela: 'personas' } });
    expect(lista[0]?.detalhe).toContain('android-02');
    expect(lista[1]?.detalhe).toContain('sem aparelho vinculado');
    // O conjunto é o da fila "Aguardando intervenção" de Personas.
    expect([...PRECISA_DE_PESSOA].sort()).toEqual(['auth_challenge', 'needs_person', 'wrong_account']);
  });

  it('fontes ainda não lidas (null) não quebram nem inventam linhas', () => {
    expect(montarPendencias({ aprendizado: null, aprovacoes: null, execucoes: [] })).toEqual([]);
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [ITEM('1'), ITEM('2')], total: 2 }));
  backend.on('GET', /^\/api\/approvals/, () => json([APROVACAO('a1')]));
  backend.on('GET', /^\/api\/personas$/, () => json([makePersona('p1', 'Ana Lima')]));
  const snap = makeSnapshot();
  useAppStore.setState({ ...initialDataState, hydrated: true, health: snap.health, settings: snap.settings, runs: [EXECUCAO] });
  usePendenciasStore.setState({ aprendizado: null, aprovacoes: null, personas: null, falhou: false });
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

describe('caixa de pendências', () => {
  it('lista as três origens, e o contador do menu é o número de linhas da lista', async () => {
    await act(async () => { root.render(<><MenuLateral /><PendenciasPage /></>); });
    await waitFor(() => expect(container.querySelectorAll('li[data-origem]')).toHaveLength(4));
    const origens = Array.from(container.querySelectorAll('li[data-origem]')).map((l) => l.getAttribute('data-origem'));
    expect(origens.sort()).toEqual(['aprendizado', 'aprendizado', 'execucao', 'persona']);
    const itemDoMenu = container.querySelector('nav a[href="#/pendencias"]') as HTMLElement;
    expect(text(itemDoMenu)).toContain('4 esperando você');
    expect(text(container.querySelector('[role="radiogroup"]') as HTMLElement)).toContain('Todas (4)');
    // O nome da persona vem da lista de personas.
    await waitFor(() => expect(text(container)).toContain('Persona Ana Lima'));
  });

  it('RF-03: a sessão que pede pessoa aparece na caixa, e o contador do menu continua igual à lista', async () => {
    backend.on('GET', /^\/api\/personas$/, () => json([makePersona('p1', 'Ana Lima'), PRESA]));
    await act(async () => { root.render(<><MenuLateral /><PendenciasPage /></>); });
    await waitFor(() => expect(container.querySelectorAll('li[data-origem]')).toHaveLength(5));
    const linha = container.querySelector('li[data-origem="intervencao"]') as HTMLElement;
    expect(text(linha)).toContain('Bia Nunes (@bia.nunes)');
    expect(linha.querySelector('a')?.getAttribute('href')).toBe('#/personas');
    const itemDoMenu = container.querySelector('nav a[href="#/pendencias"]') as HTMLElement;
    expect(text(itemDoMenu)).toContain('5 esperando você');
    expect(text(container.querySelector('[role="radiogroup"]') as HTMLElement)).toContain('Intervenção (1)');
    expect(backend.callsTo('POST', /./)).toHaveLength(0);
  });

  it('a ação primária só leva à tela onde se decide (nada é aprovado aqui) e o filtro por origem fica no link', async () => {
    await act(async () => { root.render(<PendenciasPage />); });
    await waitFor(() => expect(container.querySelectorAll('li[data-origem]')).toHaveLength(4));
    const hrefs = Array.from(container.querySelectorAll('li a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('#/aprendizado?aba=aprovar');
    expect(hrefs).toContain('#/personas/p1/aprovacoes');
    expect(hrefs).toContain('#/execucoes/r-espera');
    expect(backend.callsTo('POST', /./)).toHaveLength(0);
    await click(byRole('radio', /^Persona \(1\)/, container));
    expect(useUiStore.getState().rota.query.origem).toBe('persona');
    expect(container.querySelectorAll('li[data-origem]')).toHaveLength(1);
    await click(byRole('radio', /^Todas/, container));
    expect(useUiStore.getState().rota.query.origem).toBeUndefined();
  });

  it('caixa vazia diz "Nada esperando você"; leitura que falha avisa que a lista pode estar incompleta', async () => {
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
    backend.on('GET', /^\/api\/approvals/, () => json({ detail: 'erro' }, 500));
    useAppStore.setState({ runs: [] });
    await act(async () => { root.render(<PendenciasPage />); });
    await waitFor(() => expect(text(container)).toContain('Nada esperando você'));
    await waitFor(() => expect(text(container)).toContain('pode estar incompleta'));
    expect(allByRole('listitem', /./, container).filter((l) => l.hasAttribute('data-origem'))).toHaveLength(0);
  });
});
