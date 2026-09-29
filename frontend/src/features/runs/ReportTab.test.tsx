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
});
