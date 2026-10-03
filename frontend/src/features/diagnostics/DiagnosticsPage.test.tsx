// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Health, Instance, UsageReport } from '../../api/types';
import { useAppStore } from '../../store/app';
import { APPS, DIAGNOSTICS, SETTINGS, makeEvent, makeInstance } from '../../test/fixtures';
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
    // Aparelhos: 1 de 2 online (fixture seedStore). As vagas são por servidor e moram na Infraestrutura (tarefa 02
    // da revisão de UX): o azulejo não põe mais o online do parque contra a vaga só do central.
    expect(text(container)).toContain('1 de 2 online');
    expect(text(container)).not.toContain('3 vagas');
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
    expect(text(container)).toContain('Nenhum problema detectado pelo servidor.');
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

describe('Custo de IA: o RA-10 (31.16, adendo v0.75)', () => {
  const RA10: UsageReport = {
    ...USAGE_REPORT,
    groups: [{ role: 'decide', model: 'barato', tier: 0, calls: 30, fresh: 1000, cache_read: 0, cache_write: 0, output: 100,
      with_image: 9, errors: 0, avg_ms: 900, usd: 0.3 }],
    total_usd: 0.3, steps_driven_by: { ai: 20, recipe: 5 },
    escalations: { efeito: { calls: 12, usd: 0.4 }, piso: { calls: 2, usd: 0.05 } },
    rejudges: {
      calls: 13, usd: 0.04, by_kind: { nivel: { calls: 13, usd: 0.04 } }, judged: 12, disagreements: 1, disagreement_rate: 0.0833,
      by_app: { qa: { judged: 10, disagreements: 1, disagreement_rate: 0.1 }, '*': { judged: 2, disagreements: 0, disagreement_rate: 0 } },
    },
    cascades: { calls: 5, usd: 0.12, unblocked: 3, by_verdict: { click: 3, step_blocked: 2 } },
    image_reasons: { arvore_rica: { calls: 40, with_image: 0 }, problema: { calls: 7, with_image: 6 } },
    steps_driven_by_null: 2,
  } as UsageReport;

  const resumo = (rotulo: string): HTMLElement => {
    const achado = [...container.querySelectorAll('summary')].find((s) => text(s as HTMLElement).includes(rotulo));
    expect(achado, `resumo "${rotulo}"`).toBeDefined();
    return achado as HTMLElement;
  };

  it('mostra o modelo forte por motivo, o rejulgamento, a cascata e o aviso do condutor nulo; os detalhes recolhidos', async () => {
    useAppStore.setState({ apps: APPS });
    backend.on('GET', /^\/api\/usage$/, () => json(RA10));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Modelo forte e conferência'));
    const tudo = text(container);
    // um motivo por linha, maior custo primeiro: o rótulo e "N× · US$"
    const motivos = [...container.querySelectorAll('section[aria-labelledby="usage-modelo-forte"] li')]
      .map((li) => [...li.children].map((s) => s.textContent));
    expect(motivos.map(([rotulo]) => rotulo)).toEqual(['efeito externo', 'alvo inexistente']);
    expect(motivos[0]?.[1]).toMatch(/^12× · US\$/);
    expect(tudo).toContain('12 julgada(s), 8 % de discordância (1)');
    expect(tudo).toContain('5 subida(s), 3 desbloquearam a tela');
    expect(tudo).toContain('Etapas sem registro de quem decidiu');
    expect(tudo).toContain('2 etapa(s) com decisão de IA');
    // por app e a imagem começam recolhidos: a tabela não está no texto
    expect(tudo).not.toContain('QA Messenger');
    expect(tudo).not.toContain('árvore da tela bastou');

    await click(resumo('Discordância do rejulgamento, por app'));
    expect(text(container)).toContain('QA Messenger'); // o nome do catálogo, não o id
    expect(text(container)).toContain('etapa sem app'); // o "*" do servidor
    expect(text(container)).toContain('10 %');

    await click(resumo('Imagem: por que foi junto'));
    expect(text(container)).toContain('árvore da tela bastou');
    expect(text(container)).toContain('não vai');
  });

  it('servidor anterior ao v0.75 (sem as chaves) e período sem nada: nem a seção nem o aviso aparecem', async () => {
    const antigo = { ...RA10 } as Partial<UsageReport>;
    for (const k of ['escalations', 'rejudges', 'cascades', 'image_reasons', 'steps_driven_by_null'] as const) delete antigo[k];
    backend.on('GET', /^\/api\/usage$/, () => json(antigo));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Ver por função e modelo'));
    expect(text(container)).not.toContain('Modelo forte e conferência');
    expect(text(container)).not.toContain('Etapas sem registro de quem decidiu');
  });
});

describe('polimento do deploy 10: as chaves da aceleração e da máquina em português', () => {
  it('Aceleração e Máquina com o formato real de /api/diagnostics: nada de "Usable", "Raw", "Guidance" cru', async () => {
    backend.on('GET', /^\/api\/diagnostics$/, () => json({
      ...DIAGNOSTICS,
      host: { os: 'Windows 2025', cores_physical: 16, cores_logical: 22, swap_total_gb: 8, disk_project: 'C: — 400 GB livres' },
      acceleration: { usable: true, detail: 'WHPX ok', hypervisor_present: true, raw: 'accel: 0', guidance: 'Use o WHPX.' },
    }));
    await montar();
    await waitFor(() => expect(text(container)).toContain('Coletado em'));
    for (const id of ['diag-aceleracao', 'diag-maquina']) {
      await click(document.getElementById(id)?.querySelector('summary') as HTMLElement);
    }
    const corpo = text(container);
    for (const rotulo of ['Utilizável', 'Saída bruta', 'Orientação', 'Hipervisor presente', 'Núcleos físicos', 'Núcleos lógicos',
      'Memória virtual (GB)', 'Disco do projeto']) {
      expect(corpo).toContain(rotulo);
    }
    for (const cru of ['Usable', 'Raw', 'Guidance', 'Hypervisor present', 'Cores physical', 'Swap total', 'Disk project']) {
      expect(corpo).not.toContain(cru);
    }
  });
});
