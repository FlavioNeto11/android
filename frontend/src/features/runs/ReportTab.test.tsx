// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { REPORT, RUN_ID, makeRun } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { rotuloDaFalha } from '../aprendizado/model';
import { ReportTab } from './ReportTab';

/**
 * "Aprendizado desta execução" no relatório (ADR-054, D2): o bloco `aprendizado` de `GET /api/runs/{id}/feedback`,
 * no formato que o backend emite (`presentation/feedback.py::_aprendido`), vira uma lista por grupo, com o papel e o
 * estado em português. Prova `simulated` (backend falso).
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const linha = (x: Record<string, unknown>) => ({ kind: null, ref: null, titulo: null, estado: null, papel: null,
                                                 braco: null, failure_kind: null, n: null, ...x });

const FEEDBACK = {
  run_id: RUN_ID, votos: [], sinais: [],
  aprendizado: {
    receitas: [linha({ kind: 'receita', ref: '12', titulo: 'abrir (v1)', estado: 'disabled',
                       papel: 'usada, posta em quarentena nesta execução, evidência contra' })],
    fluxos: [linha({ kind: 'fluxo', ref: 'fluxo-aprendido', titulo: 'abra o perfil de {alvo}', estado: 'candidate',
                     papel: 'aprendido nesta execução' })],
    falhas: [linha({ papel: 'classificada na leitura (retroativo)', failure_kind: 'pos_condicao_nao_comprovada', n: 2 })],
    candidatas: [],
    licoes: [linha({ kind: 'licao', ref: 'li-1', titulo: 'abra pelo atalho do perfil', estado: 'published',
                     papel: 'exposta ao prompt (ator)', braco: 'with' })],
  },
};

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/runs\/[^/]+\/report$/, () => json(REPORT));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const secao = () => container.querySelector('[aria-labelledby="relatorio-aprendizado"]') as HTMLElement;

describe('Aprendizado desta execução', () => {
  it('mostra cada grupo do bloco, com o ref quando difere do título, o papel e o estado em português', async () => {
    backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json(FEEDBACK));
    await act(async () => root.render(<ReportTab run={makeRun({ status: 'completed' })} />));
    await waitFor(() => expect(text(secao())).toContain('Receitas aprendidas ou usadas'));
    const t = text(secao());
    expect(t).toContain('abrir (v1) (12) · usada, posta em quarentena nesta execução, evidência contra · desligado');
    expect(t).toContain('Fluxos criados, usados ou desligados');
    expect(t).toContain('abra o perfil de {alvo} (fluxo-aprendido) · aprendido nesta execução · candidato');
    expect(t).toContain(`Falhas classificadas${rotuloDaFalha('pos_condicao_nao_comprovada')} × 2 · classificada na leitura`);
    expect(t).toContain('abra pelo atalho do perfil (li-1) · exposta ao prompt (ator) · publicado');
    // Grupo vazio não aparece; o estado cru do livro não vaza.
    expect(t).not.toContain('Candidatas geradas');
    expect(t).not.toMatch(/published|candidate|disabled/);
  });

  it('as cinco listas vazias dizem que nada foi aprendido (não que o servidor não informa)', async () => {
    backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json({
      ...FEEDBACK, aprendizado: { receitas: [], fluxos: [], falhas: [], candidatas: [], licoes: [] },
    }));
    await act(async () => root.render(<ReportTab run={makeRun({ status: 'completed' })} />));
    await waitFor(() => expect(text(secao())).toContain('Nada aprendido, usado do livro ou sinalizado nesta execução.'));
  });

  it('o bloco nulo (a leitura dele falhou) não vira "nada aprendido", mesmo sem votos nem sinais', async () => {
    backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json({ ...FEEDBACK, aprendizado: null }));
    await act(async () => root.render(<ReportTab run={makeRun({ status: 'completed' })} />));
    await waitFor(() => expect(text(secao())).toContain(
      'Não foi possível ler o que esta execução aprendeu ou usou do livro.'));
    const t = text(secao());
    expect(t).not.toContain('Nada aprendido');
    expect(t).not.toContain('O servidor ainda não informa');
  });

  it('com o bloco nulo, os votos e os sinais continuam aparecendo', async () => {
    backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json({
      ...FEEDBACK, aprendizado: null,
      votos: [{ id: 1, kind: 'feedback', polarity: 'positive', verdict: 'certo', reason: null, objective_id: null,
                created_by: 'dono', source_ref: `run:${RUN_ID}` }],
      sinais: [{ id: 2, kind: 'tomou_controle', polarity: 'negative', objective_id: null, created_by: 'dono',
                 source_ref: `run:${RUN_ID}` }],
    }));
    await act(async () => root.render(<ReportTab run={makeRun({ status: 'completed' })} />));
    await waitFor(() => expect(text(secao())).toContain('Votos'));
    const t = text(secao());
    expect(t).toContain('Não foi possível ler o que esta execução aprendeu ou usou do livro.');
    expect(t).toContain('Execução inteira: deu certo — dono');
    expect(t).toContain('Sinais implícitos');
    expect(t).not.toContain('Nada aprendido');
  });
});

/**
 * "Resultado por instância" em cartões (29/09): o cabeçalho diz a situação e a prova, e as três listas de etapas
 * ficam separadas — a comprovada numa versão antiga do plano não esconde a mesma etapa em aberto no plano final.
 * Linhas no formato de `RunService.report` (02ee9e e 7cfa59, texto encurtado). Prova `simulated`.
 */
describe('Resultado por instância', () => {
  const linha = (x: Record<string, unknown>) => ({
    instance_id: 'android-01', status: 'succeeded', detail: 'Todas as etapas concluídas e comprovadas por observação da tela',
    worker_id: 'WIN-CENTRAL', device_serial: 'emulator-5554', proven: true, delivery_level: 'delivered',
    blocked_reason: null, needs: null,
    effects: ["2026-09-28T23:53:45.578Z — 'Curtir a publicação': tap executado ([receita v1] Curtir a publicação alvo.)"],
    proven_steps: ['Curtir a publicação: pós-condição comprovada pela árvore local, sem IA (selector:desc==Liked)'],
    manually_confirmed_steps: [], open_steps: [], plan_versions: 1, ai_calls: 13, ai_tokens: 156652, ...x,
  });
  const FALHOU = linha({
    instance_id: 'android-06', status: 'failed', detail: 'Tempo total do objetivo esgotado.', proven: false, delivery_level: null,
    blocked_reason: 'Tempo total do objetivo esgotado.', effects: [],
    proven_steps: ['Abrir o perfil de @alvo: pós-condição comprovada pela árvore local, sem IA'],
    open_steps: ['Abrir o perfil de @alvo', 'Comentar na publicação'], plan_versions: 2,
  });
  const A_MAO = linha({
    instance_id: 'android-02', detail: 'Todas as etapas concluídas (1 confirmada(s) manualmente pelo usuário)', proven: false,
    delivery_level: 'none', manually_confirmed_steps: ['Enviar a mensagem para @alvo'],
  });

  const cartao = (id: string) => {
    const h = Array.from(container.querySelectorAll('article h4')).find((e) => e.textContent === id);
    return h?.closest('article') as HTMLElement;
  };
  const montar = async (rows: unknown[]) => {
    backend.on('GET', /^\/api\/runs\/[^/]+\/report$/, () => json({ ...REPORT, per_instance: rows }));
    backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json(FEEDBACK));
    await act(async () => root.render(<ReportTab run={makeRun({ status: 'completed' })} />));
    await waitFor(() => expect(container.querySelectorAll('article').length).toBe(rows.length));
  };

  it('sucesso comprovado: selo, onde rodou, etapa com a prova, efeito com o horário e o rodapé de custo', async () => {
    await montar([linha({})]);
    const c = cartao('android-01');
    const cabecalho = text(c.querySelector('header') as HTMLElement);
    expect(cabecalho).toContain('Sucesso');
    expect(cabecalho).toContain('Comprovado');
    expect(cabecalho).toContain('WIN-CENTRAL · emulator-5554');
    const t = text(c);
    expect(t).toContain('Curtir a publicaçãopós-condição comprovada pela árvore local, sem IA (selector:desc==Liked)');
    expect(t).toContain('tap executado ([receita v1] Curtir a publicação alvo.)');
    expect(c.querySelector('time')?.getAttribute('datetime')).toBe('2026-09-28T23:53:45.578Z');
    expect(t).toContain('1 versão do plano · 13 chamadas de IA · 156.652 tokens');
  });

  it('falha: a etapa comprovada numa versão antiga continua listada em aberto no plano final, sem selo de prova', async () => {
    await montar([FALHOU]);
    const c = cartao('android-06');
    expect(text(c.querySelector('header') as HTMLElement)).not.toContain('Comprovado');
    const t = text(c);
    expect(t).toContain('Comprovadas — em qualquer versão do plano');
    expect(t).toContain('Em aberto no plano final (v2)Abrir o perfil de @alvoComentar na publicação');
    expect(t).toContain('Nenhum efeito fora do aparelho registrado.');
    // O motivo igual ao detalhe não se repete.
    expect(t.split('Tempo total do objetivo esgotado.').length).toBe(2);
  });

  it('sucesso com etapa confirmada à mão não vira "Comprovado"', async () => {
    await montar([A_MAO]);
    const c = cartao('android-02');
    const cabecalho = text(c.querySelector('header') as HTMLElement);
    expect(cabecalho).toContain('Com etapa confirmada à mão');
    expect(cabecalho).not.toContain('Comprovado');
    expect(cabecalho).toContain('Sem confirmação de envio');
    expect(text(c)).toContain('Confirmadas à mão — sem prova da telaEnviar a mensagem para @alvo');
  });

  it('com mais de 3 instâncias, só os comprovados começam recolhidos; o resto fica à vista', async () => {
    await montar([linha({}), linha({ instance_id: 'android-03' }), linha({ instance_id: 'android-04' }), FALHOU, A_MAO]);
    const recolhido = cartao('android-01').querySelector('details') as HTMLDetailsElement;
    expect(recolhido.open).toBe(false);
    expect(text(recolhido)).toContain('Ver etapas e efeitos (1 etapa comprovada, 1 efeito externo)');
    expect(cartao('android-06').querySelector('details')).toBeNull();
    expect(text(cartao('android-06'))).toContain('Em aberto no plano final (v2)');
    expect(cartao('android-02').querySelector('details')).toBeNull();
  });

  it('linha sem o formato de objeto cai para a árvore genérica, sem cartão', async () => {
    backend.on('GET', /^\/api\/runs\/[^/]+\/report$/, () => json({ ...REPORT, per_instance: ['android-01 ok'] }));
    backend.on('GET', /^\/api\/runs\/[^/]+\/feedback$/, () => json(FEEDBACK));
    await act(async () => root.render(<ReportTab run={makeRun({ status: 'completed' })} />));
    await waitFor(() => expect(text(container)).toContain('android-01 ok'));
    expect(container.querySelectorAll('article')).toHaveLength(0);
  });
});
