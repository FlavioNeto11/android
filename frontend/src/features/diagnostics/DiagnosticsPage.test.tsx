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

/**
 * "Outros dados" (29/09): cada chave conhecida vira um bloco com título em português e tabela para o que é lista; a
 * desconhecida continua visível pela renderização genérica. Formato do `GET /api/diagnostics` real, encurtado.
 */
describe('outros dados', () => {
  const EXTRAS = {
    measured_on: 'este host (o backend roda na mesma máquina dos emuladores)',
    sdk: {
      root: 'C:\Android\Sdk', found: true, configured_image: 'system-images;android-34;google_apis;x86_64',
      configured_image_installed: true,
      system_images: ['system-images;android-28;default;x86_64', 'system-images;android-34;google_apis;x86_64'],
      override_images: [{ instance_id: 'android-11', image: 'system-images;android-34;google_apis_playstore;x86_64', installed: true }],
    },
    scale_test: [
      { target: 2, online: 2, ts: '2026-09-24T12:17:59', boot_wall_seconds: 65.0,
        boot_seconds_each: [{ id: 'android-01', boot_seconds: 24.2 }, { id: 'android-02', boot_seconds: 63.5 }], not_online: [],
        host_cpu_percent: 12.3, mem_available_gb: 28.4, mem_used_percent: 55.2,
        emulator_rss_mb: [{ id: 'android-01', rss_mb: 587.2, cpu: 0.0 }, { id: 'android-02', rss_mb: 2468.8, cpu: 0.0 }],
        ai_provider: 'anthropic', ai_simulated: false },
    ],
    image_probes: [{ image: 'system-images;android-34;aosp_atd;x86_64', android_release: '14', requested_ram_mb: 1536,
                     guest_memtotal: 'MemTotal:        2534552 kB', qemu_ws_gb: 2.78, qemu_private_gb: 3.32, first_boot_s: 68.0 }],
    host_script: {
      collected_at: '2026-09-17T11:51:35Z', measured_on: 'WIN-7S2UASNLFOP', cpu: 'Intel(R) Core(TM) Ultra 9 185H', cores: 16,
      disks: [{ drive: 'C', free_gb: 451.3, used_gb: 501.5 }],
      top_memory_processes: [{ name: 'python', pid: 5392, ws_gb: 13.09 }],
      accel_check: 'accel:\r\n0\r\nWHPX(10.0.26100) is installed and usable.\r\naccel',
      estimate: { per_instance_gb: 3.3, note: 'imagem Android 34', fits_now: 1 },
    },
  };

  async function abrirOutros(): Promise<HTMLDetailsElement> {
    backend.on('GET', /^\/api\/diagnostics$/, () => json({ ...DIAGNOSTICS, ...EXTRAS }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    const outros = document.getElementById('diag-outros') as HTMLDetailsElement;
    await click(outros.querySelector('summary') as HTMLElement);
    return outros;
  }

  it('um bloco por chave, com título em português e a contagem de seções no resumo', async () => {
    const outros = await abrirOutros();
    expect(text(outros.querySelector('summary') as HTMLElement)).toContain('6 seções'); // 5 conhecidas + surprise_field
    const titulos = Array.from(outros.querySelectorAll('h4')).map((h) => h.textContent);
    expect(titulos).toEqual(['Surprise field', 'Onde foi medido', 'SDK do Android', 'Teste de escala',
                             'Imagens de sistema medidas', 'Levantamento do host (script)']);
    // A chave desconhecida continua visível: um item por linha.
    const surpresa = document.getElementById('diag-outros-surprise_field')?.closest('section') as HTMLElement;
    expect(Array.from(surpresa.querySelectorAll('li')).map((li) => li.textContent)).toEqual(['a', 'b']);
  });

  it('SDK: imagens uma por linha e a imagem própria do aparelho em tabela', async () => {
    const outros = await abrirOutros();
    const sdk = outros.querySelector('[aria-labelledby="diag-outros-sdk"]') as HTMLElement;
    expect(Array.from(sdk.querySelectorAll('li')).map((li) => li.textContent)).toEqual([
      'system-images;android-28;default;x86_64', 'system-images;android-34;google_apis;x86_64']);
    expect(text(sdk)).toContain('Pasta do SDKC:\Android\Sdk');
    expect(text(sdk.querySelector('table') as HTMLElement)).toContain('android-11');
  });

  it('teste de escala e imagens medidas viram tabelas legíveis, sem a árvore aninhada', async () => {
    const outros = await abrirOutros();
    const escala = outros.querySelector('[aria-labelledby="diag-outros-scale_test"]') as HTMLElement;
    const linha = escala.querySelector('tbody tr') as HTMLElement;
    expect(Array.from(linha.querySelectorAll('td')).slice(0, 6).map((td) => td.textContent)).toEqual(['2', '2', '65', '12,3', '28,4', '55,2']);
    expect(text(escala)).toContain('Por aparelho: boot e RAM do emulador em cada leva (2 aparelhos)');
    expect(text(escala)).not.toContain('Emulator rss mb');
    const imagens = outros.querySelector('[aria-labelledby="diag-outros-image_probes"]') as HTMLElement;
    expect(text(imagens)).toContain('android-34 · aosp_atd · x86_64');
    expect(text(imagens)).toContain('2,4'); // MemTotal 2534552 kB
  });

  it('script do host: chave/valor, tabelas, PID sem separador de milhar e a saída de várias linhas em bloco', async () => {
    const outros = await abrirOutros();
    const host = outros.querySelector('[aria-labelledby="diag-outros-host_script"]') as HTMLElement;
    expect(text(host)).toContain('Máquina medidaWIN-7S2UASNLFOP');
    expect(text(host)).toContain('Processos que mais usam memória');
    expect(text(host.querySelectorAll('table')[1] as HTMLElement)).toContain('5392');
    expect(host.querySelector('pre')?.textContent).toBe('accel:\n0\nWHPX(10.0.26100) is installed and usable.\naccel');
    expect(text(host)).toContain('Cabem agora1');
  });
});
