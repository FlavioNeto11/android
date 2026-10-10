// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { RunDetail } from '../../api/types';
import { SeloEtapaExploratoria, SeloNasceuDeExploracao } from '../../components/SeloDeExploracao';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import { APPS, makeRunDetail } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { PlanTab } from '../runs/PlanTab';
import { AprendizadoPage } from './AprendizadoPage';
import type { EntradaDoLivro } from './model';

/**
 * 31.299 (adendo v1.130): a etapa descoberta por exploração (`steps[].exploratoria`, `PlanStep.exploratoria`) e a receita que
 * nasceu dela (`nasceu_de_exploracao`) levam um selo; sem a marca, ou em backend anterior, nada aparece. Prova `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  useAppStore.setState({ apps: APPS });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: {} } });
});

describe('selos', () => {
  it('só a marca verdadeira desenha o selo', async () => {
    for (const v of [false, undefined, null]) {
      await act(async () => root.render(<><SeloEtapaExploratoria exploratoria={v} /><SeloNasceuDeExploracao nasceu={v} /></>));
      expect(container.textContent).toBe('');
    }
    await act(async () => root.render(<><SeloEtapaExploratoria exploratoria /><SeloNasceuDeExploracao nasceu /></>));
    expect(text(container)).toContain('Descoberta pela IA');
    expect(text(container)).toContain('Nasceu de exploração');
  });
});

describe('Plano da execução', () => {
  it('a etapa exploratória do plano leva o selo; a do catálogo não', async () => {
    const base = makeRunDetail();
    const [a, b] = base.plan!.steps;
    const detail: RunDetail = { ...base, plan: { ...base.plan!, steps: [{ ...a!, exploratoria: true }, { ...b!, exploratoria: false }] } };
    await act(async () => root.render(<PlanTab detail={detail} />));
    const itens = Array.from(container.querySelector('ol')?.children ?? []) as HTMLElement[];
    expect(text(itens[0]!)).toContain('Descoberta pela IA');
    expect(text(itens[1]!)).not.toContain('Descoberta pela IA');
  });
});

describe('Livro', () => {
  function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
    return {
      kind: 'receita', ref: '9', state: 'published', native_status: 'active', title: 'Tocar no Wi-Fi', app: 'com.android.settings',
      origin: 'execucao', side_effect: false, human_origin: false, requires_owner: false, created_at: '2026-10-06T10:00:00Z',
      state_at: '2026-10-06T10:29:00Z', last_used_at: null, uses: 0, evidence: { for: 0, against: 0 }, count: null,
      detail: null, acoes: [], por_que_nao_publica: null, ...over,
    };
  }

  it('a receita que nasceu de exploração leva o selo na linha; a de catálogo, não', async () => {
    backend = new FakeBackend();
    backend.install();
    for (const rota of ['pendentes', 'revisar', 'intencao']) backend.on('GET', new RegExp(`^/api/aprendizado/${rota}$`), () => json({ itens: [], total: 0 }));
    backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], sem_eixo: null, nao_resolvido: null }));
    backend.on('GET', /^\/api\/aprendizado$/, () => json({
      itens: [entrada({ ref: '9', nasceu_de_exploracao: true }), entrada({ ref: '10', title: 'Abrir o Bluetooth', nasceu_de_exploracao: false })],
      total: 2, contagem: {},
    }));
    await act(async () => root.render(<AprendizadoPage />));
    await click(byRole('tab', /^Aprendido/, container));
    await waitFor(() => expect(container.querySelector('[data-item="receita:9"]')).toBeTruthy());
    expect(text(container.querySelector('[data-item="receita:9"]')!)).toContain('Nasceu de exploração');
    expect(text(container.querySelector('[data-item="receita:10"]')!)).not.toContain('Nasceu de exploração');
  });
});
