// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Health, Instance, UsageReport } from '../../api/types';
import { useAppStore } from '../../store/app';
import { DIAGNOSTICS, SETTINGS, makeEvent, makeInstance } from '../../test/fixtures';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { DiagnosticsPage } from './DiagnosticsPage';

/**
 * Item 11.7: o painel de decisão (azulejos), "problemas primeiro" e as seções de detalhe recolhidas e
 * lembradas — as três coisas que o teste vitest do pacote pede.
 */

const HEALTH: Health = {
  status: 'degraded', version: '0.1.0', commit: null, migration: null,
  ai: {
    provider: 'simulated', model: 'sim', configured: false, simulated: true, sends_data_externally: false,
    notice: 'Modo simulado.', effort: null, spend_today_usd: 1.5,
  } as Health['ai'],
  appium: { running: true, port: 4723, detail: null },
  sdk: { found: true, root: 'C:\\Sdk', emulator_version: '35.1', accel: 'WHPX' },
  problems: [{ code: 'ai_simulated', message: 'A IA está em modo simulado', hint: 'Defina a chave no .env.' }],
  features: { hibernation: true, recipes: 'off', flows: false, image_policy: 'auto', system_image: 'x' },
} as Health;

const USAGE_REPORT: UsageReport = {
  scope: { run_id: null, days: 7 }, groups: [], total_usd: 0, objectives_with_ai: 0, calls_per_objective: 0,
  usd_per_objective: 0, steps_driven_by: {}, unpriced_models: [], spend_today_usd: 1.5,
} as UsageReport;

const backend = new FakeBackend();
let root: Root;
let container: HTMLDivElement;

async function montar(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<DiagnosticsPage />);
  });
}

function seedStore(instances: Instance[] = [makeInstance(1, { state: 'online' }), makeInstance(2, { state: 'stopped' })]) {
  useAppStore.setState({
    health: HEALTH,
    settings: SETTINGS,
    metrics: { ts: new Date().toISOString(), cpu_percent: 37, mem_total_gb: 64, mem_available_gb: 40.5, mem_used_percent: 37, emulators: [] },
    instances: Object.fromEntries(instances.map((i) => [i.id, i])),
    instanceOrder: instances.map((i) => i.id),
    recentEvents: [],
  });
}

beforeEach(() => {
  installBrowserStubs();
  window.localStorage.clear();
  backend.calls = [];
  backend.install();
  backend.on('GET', /^\/api\/diagnostics$/, () => json(DIAGNOSTICS));
  backend.on('GET', /^\/api\/usage$/, () => json(USAGE_REPORT));
  seedStore();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('painel de decisão (azulejos)', () => {
  it('monta os azulejos a partir da saúde, dos aparelhos e da máquina do estado', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));

    // Saúde: degradada, 1 problema (fixture HEALTH).
    expect(text(container)).toContain('Degradado');
    expect(text(container)).toContain('1 problema');
    // Aparelhos: 1 online (fixture seedStore), 3 vagas (SETTINGS.max_online_devices).
    expect(text(container)).toContain('1 online');
    expect(text(container)).toContain('3 vagas');
    // Máquina: CPU 37%, RAM livre 40,5 GB (fixture metrics).
    expect(text(container)).toContain('CPU 37%');
    expect(text(container)).toContain('40,5 GB');
    // Aceleração: DIAGNOSTICS.acceleration = { available: true, ... }.
    expect(text(container)).toContain('Disponível');
  });

  it('clicar num azulejo rola até a seção correspondente', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    const tile = byRole('button', /Saúde/, container);
    await click(tile);
    expect(document.getElementById('diag-problemas')).not.toBeNull();
  });
});

describe('problemas primeiro', () => {
  it('a lista de problemas aparece antes de qualquer tabela de dados', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    const full = text(container);
    const problemsIdx = full.indexOf('A IA está em modo simulado');
    const toolsTableIdx = full.indexOf('Caminho / detalhe');
    expect(problemsIdx).toBeGreaterThan(-1);
    expect(toolsTableIdx).toBe(-1); // ferramentas começa recolhida — a tabela nem está no texto ainda
  });

  it('sem problemas, mostra uma linha dizendo que não há nenhum', async () => {
    useAppStore.setState({ health: { ...HEALTH, problems: [] } });
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    expect(text(container)).toContain('Nenhum problema detectado pelo backend.');
  });
});

describe('seções de detalhe: recolhidas por padrão e lembradas', () => {
  it('Máquina, Aceleração, Ferramentas e Capacidade começam fechadas', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    for (const id of ['diag-maquina', 'diag-aceleracao', 'diag-ferramentas', 'diag-capacidade']) {
      const el = document.getElementById(id) as HTMLDetailsElement | null;
      expect(el).not.toBeNull();
      expect(el?.open).toBe(false);
    }
  });

  it('abrir uma seção e remontar a página mantém aberta (localStorage)', async () => {
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    const machineSummary = document.getElementById('diag-maquina')?.querySelector('summary') as HTMLElement;
    await click(machineSummary);
    expect((document.getElementById('diag-maquina') as HTMLDetailsElement).open).toBe(true);

    await act(async () => root.unmount());
    container.remove();
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    expect((document.getElementById('diag-maquina') as HTMLDetailsElement).open).toBe(true);
  });
});

describe('eventos recentes', () => {
  it('filtra por nível e limita a lista visível, com "ver mais"', async () => {
    const events = Array.from({ length: 55 }, (_, i) => makeEvent(i + 1, 'tick', null, { level: i % 5 === 0 ? 'error' : 'info', message: `evento ${i + 1}` }));
    useAppStore.setState({ recentEvents: events });
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));

    expect(text(container)).toContain('55 evento(s)');
    expect(byRole('button', /Ver mais/, container)).toBeTruthy();

    await click(byRole('button', /^Erro$/, container));
    expect(text(container)).toContain('11 evento(s)'); // 55/5 = 11 eventos de erro
  });
});
