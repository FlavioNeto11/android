// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import type { TrainingOrigin } from '../../api/types';
import { text } from '../../test/harness';
import { OrigemDoTreino } from './OrigemDoTreino';

/** 31.313 (adendo v1.138): a sessão de ensino aberta numa exploração que parou se rotula por `origin.exploracao`. Prova `simulated`. */

let root: Root;
let container: HTMLElement;

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const ORIGEM: TrainingOrigin = {
  run_id: 'r-1', step_id: 'r-1:android-01:v1:explorar_criar_regra', step_key: 'explorar_criar_regra', attempt_id: null, motivo: 'parou_no_teto',
  context: { disponivel: true, esperado: { kind: 'model_judged', value: null, description: 'Crie uma regra no Outlook para a caixa de {nome}' } },
};

it('com origin.exploracao: selo "ensina onde a IA parou", aviso e o pedido de origem como contexto', async () => {
  await act(async () => root.render(<OrigemDoTreino origin={{ ...ORIGEM, exploracao: true }} />));
  const secao = container.querySelector('section[aria-label="Origem do treino"]') as HTMLElement;
  expect(text(secao)).toContain('ensina onde a IA parou');
  expect(text(secao)).not.toContain('corrige uma falha');
  expect(text(secao)).toContain('A IA explorou o app para este pedido e não chegou lá');
  // O contexto fica num Disclosure, mas o rótulo está no DOM, com o dado da persona ainda mascarado.
  expect(text(secao)).toContain('Pedido de origem: Crie uma regra no Outlook para a caixa de {nome}');
  expect(text(secao)).not.toContain('A etapa esperava');
});

it('sem origin.exploracao (falha comum ou backend anterior): rótulo de sempre', async () => {
  await act(async () => root.render(<OrigemDoTreino origin={ORIGEM} />));
  const secao = container.querySelector('section[aria-label="Origem do treino"]') as HTMLElement;
  expect(text(secao)).toContain('corrige uma falha');
  expect(text(secao)).toContain('A etapa esperava: Crie uma regra');
  expect(text(secao)).not.toContain('ensina onde a IA parou');
  expect(text(secao)).not.toContain('A IA explorou o app');
});
