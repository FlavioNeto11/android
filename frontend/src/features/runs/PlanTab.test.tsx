// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { RunDetail } from '../../api/types';
import { useAppStore } from '../../store/app';
import { APPS, makeRunDetail } from '../../test/fixtures';
import { installBrowserStubs, openDetails, text } from '../../test/harness';
import { PlanTab } from './PlanTab';

/**
 * Item 24.6 (ADR-058): o Plano mostra o app de CADA etapa, não só `plan.app_package` — e o resumo cita os
 * `required_apps` quando o comando atravessa mais de um. Prova `simulated` (`detail` é fabricado, sem backend).
 */
let root: Root;
let container: HTMLElement;

/** A etapa "send" roda em outro app (`notes`); `open_app` continua no app do plano (`qa`, sem `app_id` próprio). */
function detailComOutroApp(): RunDetail {
  const base = makeRunDetail();
  const [openApp, send] = base.plan!.steps;
  return {
    ...base,
    plan: { ...base.plan!, required_apps: ['qa', 'notes'], steps: [openApp!, { ...send!, app_id: 'notes' }] },
  };
}

/** As etapas do plano, na ordem — o `PlanStepList` é o único `<ol>` da tela quando não há versão revisada. */
function stepItems(el: HTMLElement): HTMLLIElement[] {
  return Array.from(el.querySelector('ol')?.children ?? []) as HTMLLIElement[];
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  useAppStore.setState({ apps: APPS });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(detail: RunDetail): Promise<HTMLElement> {
  await act(async () => root.render(<PlanTab detail={detail} />));
  return container;
}

it('sem etapa cruzando app, nenhuma etapa ganha o selo "app:" (só o app do plano, como sempre)', async () => {
  const el = await render(makeRunDetail());
  expect(text(el)).not.toContain('app:');
  expect(text(el)).toContain('com.poc.qamessenger'); // plan.app_package, resumo de sempre
});

it('etapa em outro app ganha o selo "app: <nome>" na lista, e o resumo cita os apps exigidos', async () => {
  const el = await render(detailComOutroApp());
  expect(text(el)).toContain('apps exigidos: QA Messenger, Notas');
  const [open, send] = stepItems(el);
  expect(text(open!)).not.toContain('app:');   // open_app: sem app_id próprio
  expect(text(send!)).toContain('app: Notas'); // send: app_id = 'notes'
});

it('"Detalhes técnicos" de cada etapa mostra o app resolvido — o da etapa quando há, senão o do plano', async () => {
  const el = await render(detailComOutroApp());
  const [open, send] = stepItems(el);
  await openDetails(/Detalhes técnicos/, open!);
  await openDetails(/Detalhes técnicos/, send!);
  expect(text(open!)).toContain('QA Messenger'); // open_app: cai no app do plano
  expect(text(send!)).toContain('Notas');         // send: app da própria etapa
});

describe('sem catálogo carregado', () => {
  it('mostra o próprio id do app, nunca inventa um nome', async () => {
    useAppStore.setState({ apps: [] });
    const el = await render(detailComOutroApp());
    expect(text(el)).toContain('app: notes');
  });
});
