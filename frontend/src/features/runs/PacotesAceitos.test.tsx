// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { RunDetail } from '../../api/types';
import { PacotesAceitos } from '../../components/PacotesAceitos';
import { useAppStore } from '../../store/app';
import { APPS, RUN_ID, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, text } from '../../test/harness';
import { InstancesTab } from './InstancesTab';
import { PlanTab } from './PlanTab';

/**
 * 31.129 (adendo v1.84): a etapa que o ensino fez aceitar a conclusão num pacote vizinho (`pacotes_aceitos`) diz isso no
 * plano e no detalhe da etapa da execução, em "Também aceita concluir em: <pacote>"; sem o campo, nada muda. `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const PACOTE = 'com.google.android.googlequicksearchbox';

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  useAppStore.setState({ apps: APPS });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

/** A etapa "send" do plano e a do aparelho 01 aceitam o pacote vizinho; a "open_app" não tem o campo. */
function comPacoteVizinho(): RunDetail {
  const base = makeRunDetail();
  const [openApp, send] = base.plan!.steps;
  return {
    ...base,
    plan: { ...base.plan!, steps: [openApp!, { ...send!, pacotes_aceitos: [PACOTE] }] },
    steps: base.steps.map((s) => (s.id === `${RUN_ID}:android-01:v1:send` ? { ...s, pacotes_aceitos: [PACOTE, 'com.exemplo.outro'] } : s)),
  };
}

it('o componente: lista os pacotes em ordem, ignora vazio e não desenha nada sem eles', async () => {
  await act(async () => root.render(<PacotesAceitos pacotes={['a.b', 'c.d']} />));
  expect(text(container)).toBe('Também aceita concluir em: a.b, c.d');
  for (const vazio of [undefined, null, [], ['', '  ']]) {
    await act(async () => root.render(<PacotesAceitos pacotes={vazio} />));
    expect(container.textContent).toBe('');
  }
});

it('o Plano mostra a linha só na etapa que aceita o pacote vizinho', async () => {
  await act(async () => root.render(<PlanTab detail={comPacoteVizinho()} />));
  const etapas = Array.from(container.querySelector('ol')!.children) as HTMLElement[];
  expect(text(etapas[0]!)).not.toContain('Também aceita');
  expect(text(etapas[1]!)).toContain(`Também aceita concluir em: ${PACOTE}`);                 // à vista: não está dentro de "Detalhes técnicos"
});

it('o detalhe da etapa da execução mostra a linha, com todos os pacotes, e só nessa etapa', async () => {
  await act(async () => root.render(<InstancesTab detail={comPacoteVizinho()} />));
  await click(byRole('button', /android-01/));
  const etapas = allByRole('button', /.*/).filter((b) => b.getAttribute('aria-controls')?.startsWith(`step-body-${RUN_ID}:android-01:`));
  expect(etapas).toHaveLength(2);
  await click(etapas[1]!);                                                       // "send"
  expect(text(container)).toContain(`Também aceita concluir em: ${PACOTE}, com.exemplo.outro`);
  expect(text(container).match(/Também aceita concluir em/g)).toHaveLength(1);
  await click(etapas[0]!);                                                       // "open_app": sem o campo
  expect(text(container).match(/Também aceita concluir em/g)).toHaveLength(1);
});
