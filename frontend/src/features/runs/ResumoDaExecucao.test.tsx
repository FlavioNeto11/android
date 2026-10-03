// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { REPORT, RUN_ID, makeRun, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { RunView } from './RunView';
import { LEGENDA_DE_SUCESSO_COMPROVADO } from './ResumoDaExecucao';

/**
 * Rodada 2, tarefa 14: o resumo no topo da execução (pedido completo, resultado, o que precisa da pessoa), a legenda
 * de "sucesso comprovado" por foco e a guia padrão por situação, com a guia do link mandando sobre ela. `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const LONGO = 'Nas instâncias selecionadas, abra o QA Messenger, entre na conversa com QA-001, leia o nome do contato, '
  + 'envie a mensagem “Teste POC” e confirme na tela que ela aparece como enviada, sem repetir o envio se já estiver lá.';

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/runs\/[^/]+\/report$/, () => json(REPORT));
  backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json({ run_id: RUN_ID, votos: [], sinais: [], aprendizado: null }));
  backend.on('GET', /^\/api\/approvals/, () => json([]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

function preparar(over: Parameters<typeof makeRun>[0], rota?: { aba?: string }) {
  const run = makeRun(over);
  useAppStore.setState({
    ...initialDataState, hydrated: true, hydrateCount: 1, runs: [run],
    detail: { runId: RUN_ID, status: 'ready', error: null, eventsStatus: 'ready', data: makeRunDetail({ ...run }), events: [] },
  });
  if (rota) useUiStore.getState().navegar({ tela: 'execucoes', segmentos: [RUN_ID], query: rota.aba ? { aba: rota.aba } : {} }, 'replace');
  else useUiStore.setState({ selectedRunId: RUN_ID });
  return run;
}

/** Desmonta e remonta: um caso por situação dentro do mesmo teste. */
async function remontar(): Promise<void> {
  await act(async () => root.unmount());
  root = createRoot(container);
}

const abaSelecionada = () => text(container.querySelector('[role="tab"][aria-selected="true"]') as HTMLElement);
const CONCLUIDA = {
  status: 'completed' as const, started_at: '2026-09-17T12:00:00.000Z', finished_at: '2026-09-17T12:02:03.000Z',
  counts: { succeeded: 1, failed: 0, waiting_user: 0, uncertain: 0, cancelled: 0, running: 0, pending: 0 },
  instances_requested: 1, instances_used: 1, progress: 1,
};

describe('resumo no topo do detalhe', () => {
  it('concluída: pedido, resultado com a duração e a legenda de sucesso comprovado por foco', async () => {
    preparar({ ...CONCLUIDA, command: LONGO });
    await act(async () => root.render(<RunView />));
    const resumo = container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
    expect(text(resumo)).toContain('Concluída com sucesso em 2 min 03 s');
    expect(text(resumo)).toContain('1 de 1 objetivo com sucesso');
    expect(text(resumo)).toContain(LONGO);                       // o pedido inteiro está no texto, não cortado
    const gatilho = byRole('button', /O que é sucesso comprovado/, resumo);
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    await act(async () => gatilho.focus());
    await waitFor(() => expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(LEGENDA_DE_SUCESSO_COMPROVADO));
    expect(LEGENDA_DE_SUCESSO_COMPROVADO).toContain('evidência na tela do aparelho, não só pela resposta do agente');
  });

  it('pedido longo: "Ver pedido completo" abre e fecha, com aria-expanded; pedido curto não tem o botão', async () => {
    preparar({ ...CONCLUIDA, command: LONGO });
    await act(async () => root.render(<RunView />));
    const botao = byRole('button', /Ver pedido completo/, container);
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    await click(botao);
    expect(byRole('button', /Mostrar menos/, container).getAttribute('aria-expanded')).toBe('true');
    await remontar();
    preparar({ ...CONCLUIDA, command: 'Abra o QA Messenger' });
    await act(async () => root.render(<RunView />));
    expect(text(container)).not.toContain('Ver pedido completo');
  });

  it('o que precisa da pessoa só aparece quando há algo, e leva à guia que resolve', async () => {
    preparar(CONCLUIDA);
    await act(async () => root.render(<RunView />));
    expect(text(container)).not.toContain('Precisa de você');
    await remontar();
    preparar({ status: 'running', counts: { succeeded: 0, failed: 0, waiting_user: 1, uncertain: 0, cancelled: 0, running: 1, pending: 0 } });
    await act(async () => root.render(<RunView />));
    const resumo = container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
    expect(text(resumo)).toContain('Precisa de você');
    await click(byRole('button', /objetivo espera a sua decisão/, resumo));
    expect(abaSelecionada()).toMatch(/^Por aparelho/);
  });
});

describe('guia padrão por situação', () => {
  it('concluída abre no Relatório; em andamento, na Linha do tempo; planejada, no Plano', async () => {
    preparar(CONCLUIDA);
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Relatório/);
    await remontar();
    preparar({ status: 'running' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Linha do tempo/);
    await remontar();
    preparar({ status: 'planned' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Plano/);
  });

  it('a guia do link manda sobre o padrão (concluída com ?aba=plano abre no Plano)', async () => {
    preparar(CONCLUIDA, { aba: 'plano' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Plano/);
  });

  it('em andamento com ?aba=relatorio abre no Relatório; voltar ao link sem guia volta ao padrão', async () => {
    preparar({ status: 'running' }, { aba: 'relatorio' });
    await act(async () => root.render(<RunView />));
    expect(abaSelecionada()).toMatch(/^Relatório/);
    await act(async () => useUiStore.getState().navegar({ tela: 'execucoes', segmentos: [RUN_ID] }, 'replace'));
    await waitFor(() => expect(abaSelecionada()).toMatch(/^Linha do tempo/));
  });
});

/**
 * 30.43: numa execução de validação o veredito é do ITEM (a e1b7d0 era "sucesso comprovado" com evidência contra);
 * a execução comum não faz chamada nova. `simulated`.
 */
describe('veredito da validação no resumo', () => {
  const pedido = (over: Record<string, unknown>) => ({
    id: 'lv-1', estado: 'feita', motivo: null, motivo_humano: null, item_ref: 'fluxo:abc', item_kind: 'fluxo', app: null, app_nome: null,
    grupo: null, run_id: RUN_ID, run_origem: null, aparelho: null, usd: 0, teto_usd: null, created_at: '2026-10-03T10:00:00Z',
    feito_em: null, expira_em: null, comando: 'Abra', ...over,
  });
  let itens: unknown[] = [];
  let falha = false;
  const chamadas = () => backend.callsTo('GET', /^\/api\/aprendizado\/validacoes$/);
  beforeEach(() => {
    itens = [];
    falha = false;
    backend.on('GET', /^\/api\/aprendizado\/validacoes$/, () => (falha ? apiError(500, 'boom', 'falhou')
      : json({ itens, contagem: {}, total: itens.length, modo: 'on' })));
  });
  const resumo = () => container.querySelector('[aria-label="Resumo da execução"]') as HTMLElement;
  async function mostrarComOrigem(origem: 'prova_fluxo' | 'validacao_qa' | null): Promise<void> {
    preparar({ ...CONCLUIDA, origem, prova_fluxo_id: origem === 'prova_fluxo' ? 'fluxo-abc' : null });
    await act(async () => root.render(<RunView />));
  }

  const CASOS: [string, Record<string, unknown>, string][] = [
    ['feita', { estado: 'feita' }, 'a favor'],
    ['evidencia_contra', { estado: 'recusada', motivo: 'evidencia_contra', motivo_humano: 'a evidência foi contra' }, 'contra'],
    ['efeito_repetido', { estado: 'recusada', motivo: 'efeito_repetido', motivo_humano: 'o efeito rodou duas vezes' }, 'inválida (o efeito rodou duas vezes)'],
    ['ponto_de_partida', { estado: 'recusada', motivo: 'ponto_de_partida', motivo_humano: 'partiu de outra tela' }, 'inválida (partiu de outra tela)'],
    ['ator_sem_acao', { estado: 'recusada', motivo: 'ator_sem_acao', motivo_humano: 'a IA não agiu' }, 'inválida (a IA não agiu)'],
    ['divergencia_de_forma', { estado: 'recusada', motivo: 'divergencia_de_forma', motivo_humano: 'só a redação mudou' }, 'só a forma (não conta)'],
    ['sem_evidencia', { estado: 'recusada', motivo: 'sem_evidencia', motivo_humano: 'nada foi provado' }, 'sem evidência (nada foi provado)'],
    ['execucao_falhou', { estado: 'recusada', motivo: 'execucao_falhou', motivo_humano: 'a execução falhou' }, 'sem evidência (a execução falhou)'],
    ['pendente', { estado: 'pendente' }, 'em andamento'],
    ['rodando', { estado: 'rodando' }, 'em andamento'],
  ];

  it.each(CASOS)('prova de fluxo, %s: "Veredito da validação: %s"', async (_nome, over, esperado) => {
    itens = [pedido(over)];
    await mostrarComOrigem('prova_fluxo');
    await waitFor(() => expect(text(resumo())).toContain('Veredito da validação'));
    expect(text(resumo())).toContain(`Veredito da validação: ${esperado}`);
    expect(text(resumo())).not.toContain('sucesso comprovado');            // o veredito é do item, não da execução
    const ultima = chamadas().at(-1);
    expect(ultima?.query.get('run')).toBe(RUN_ID);
    expect(ultima?.query.get('limite')).toBe('1');
  });

  it('re-execução do QA também mostra o veredito', async () => {
    itens = [pedido({ estado: 'recusada', motivo: 'evidencia_contra', motivo_humano: 'contra' })];
    await mostrarComOrigem('validacao_qa');
    await waitFor(() => expect(text(resumo())).toContain('Veredito da validação: contra'));
  });

  it('nenhum pedido achado mantém a legenda de sempre', async () => {
    await mostrarComOrigem('prova_fluxo');
    await waitFor(() => expect(chamadas().length).toBeGreaterThan(0));
    await waitFor(() => expect(byRole('button', /O que é sucesso comprovado/, resumo())).toBeTruthy());
    expect(text(resumo())).not.toContain('Veredito da validação');
  });

  it('erro de rede: nunca "sucesso comprovado"; diz que o veredito não está disponível', async () => {
    falha = true;
    await mostrarComOrigem('prova_fluxo');
    await waitFor(() => expect(text(resumo())).toContain('Veredito da validação: indisponível agora'));
    expect(text(resumo())).not.toContain('sucesso comprovado');
  });

  it('execução comum: a legenda como está e nenhuma chamada nova', async () => {
    await mostrarComOrigem(null);
    expect(byRole('button', /O que é sucesso comprovado/, resumo())).toBeTruthy();
    expect(text(resumo())).not.toContain('Veredito da validação');
    expect(chamadas()).toHaveLength(0);
  });
});
