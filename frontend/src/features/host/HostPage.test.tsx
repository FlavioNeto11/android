// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBackend, apiError, byRole, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { amostrasDeExemplo, lerAmostra, lerAmostras } from './contratoDoHost';
import { HostPage, PERIODO_DA_RELEITURA_MS } from './HostPage';
import { defasagemEmMinutos, pressaoPorAparelho, processosNoTopo, resumoDaJanela, trechosDaSerie } from './resumo';

/**
 * 31.180: o painel do host. Prova `simulated`: servidor falso e amostras inventadas no formato proposto (colunas do CSV do
 * `scripts/amostrador-host.ps1`). Sem a rota, a tela lê o exemplo e diz isso.
 */

const amostra = (ts: string, over: Record<string, unknown> = {}) => ({
  ts_utc: ts, cpu_host_pct: 20, vm_convidado_nucleos: 1.5, vmmem_ws_mb: 3000, qemu_host_pct: 10, ram_livre_mb: 5000, disco_livre_gb: 200,
  processos_top: [], avisos_pressao: [], ...over,
});

describe('o leitor do contrato', () => {
  it('aceita o texto do CSV e a lista; "não medido" fica null, nunca zero; par torto cai', () => {
    const a = lerAmostra({
      ts_utc: '2026-10-07T10:00:00Z', cpu_host_pct: '41,5', vm_convidado_nucleos: '', vmmem_ws_mb: null, qemu_host_pct: 'abc', ram_livre_mb: 4096, disco_livre_gb: '120.25',
      processos_top: 'python:12.3;pwsh:4.1;sem_numero:x;:7', avisos_pressao: 'android-05:3;android-01:1',
    })!;
    expect(a).toMatchObject({ cpu_host_pct: 41.5, vm_convidado_nucleos: null, vmmem_ws_mb: null, qemu_host_pct: null, ram_livre_mb: 4096, disco_livre_gb: 120.25 });
    expect(a.processos_top).toEqual([{ nome: 'python', pct: 12.3 }, { nome: 'pwsh', pct: 4.1 }]);
    expect(a.avisos_pressao).toEqual([{ instance_id: 'android-05', n: 3 }, { instance_id: 'android-01', n: 1 }]);
    const b = lerAmostra({ ts_utc: '2026-10-07T10:01:00Z', processos_top: [{ nome: 'node', pct: 2 }, { nome: '', pct: 1 }, { pct: 3 }], avisos_pressao: [{ instance_id: 'android-02', n: '2' }] })!;
    expect(b.processos_top).toEqual([{ nome: 'node', pct: 2 }]);
    expect(b.avisos_pressao).toEqual([{ instance_id: 'android-02', n: 2 }]);
    expect(b.cpu_host_pct).toBeNull();
  });
  it('lerAmostras: sem `items` não é resposta; a linha de falha conta e não entra; sai em ordem de tempo', () => {
    expect(lerAmostras({})).toBeNull();
    expect(lerAmostras(null)).toBeNull();
    const l = lerAmostras({ items: [amostra('2026-10-07T10:02:00Z'), { ts_utc: '2026-10-07T10:01:00Z', erro: 'COMException' }, amostra('2026-10-07T10:00:00Z'), 7, { ts_utc: 'lixo' }] })!;
    expect(l.amostras.map((x) => x.ts_utc)).toEqual(['2026-10-07T10:00:00Z', '2026-10-07T10:02:00Z']);
    expect(l.falhas).toBe(1);
  });
  it('o exemplo é inventado, determinístico e marcado como exemplo', () => {
    const e = amostrasDeExemplo(1, Date.parse('2026-10-07T12:00:30Z'));
    expect(e.exemplo).toBe(true);
    expect(e.amostras).toHaveLength(60);
    expect(e.amostras.at(-1)!.ts_utc).toBe('2026-10-07T12:00:00Z');
    expect(amostrasDeExemplo(1, Date.parse('2026-10-07T12:00:30Z'))).toEqual(e);
    expect(amostrasDeExemplo(99).amostras).toHaveLength(24 * 60);                            // a janela vai até 24 h
  });
});

describe('o resumo da janela', () => {
  const l = lerAmostras({ items: [
    amostra('2026-10-07T10:00:00Z', { cpu_host_pct: 10, ram_livre_mb: 6000, qemu_host_pct: 5, processos_top: [{ nome: 'python', pct: 10 }, { nome: 'node', pct: 2 }], avisos_pressao: [] }),
    amostra('2026-10-07T10:01:00Z', { cpu_host_pct: 70, ram_livre_mb: 3000, qemu_host_pct: null, processos_top: [{ nome: 'python', pct: 30 }], avisos_pressao: [{ instance_id: 'android-05', n: 2 }] }),
    amostra('2026-10-07T10:02:00Z', { cpu_host_pct: null, ram_livre_mb: 4000, qemu_host_pct: null, processos_top: [], avisos_pressao: [{ instance_id: 'android-05', n: 1 }, { instance_id: 'android-01', n: 4 }] }),
  ] })!.amostras;

  it('CPU: média só das medidas, pico com a hora; RAM: a menor; qemu: a última medida', () => {
    const r = resumoDaJanela(l);
    expect(r.cpu).toEqual({ media: 40, pico: 70, picoEm: '2026-10-07T10:01:00Z' });
    expect(r.ramLivreMinimaMb).toBe(3000);
    expect(r.qemuPct).toBe(5);
    expect(r.ultima?.ts_utc).toBe('2026-10-07T10:02:00Z');
    expect(resumoDaJanela([])).toMatchObject({ ultima: null, cpu: { media: null, pico: null, picoEm: null }, ramLivreMinimaMb: null, qemuPct: null });
  });
  it('processos: a média é por minuto da janela (quem não apareceu conta zero), com o pico de um minuto', () => {
    expect(processosNoTopo(l)).toEqual([{ nome: 'python', mediaPct: 40 / 3, picoPct: 30 }, { nome: 'node', mediaPct: 2 / 3, picoPct: 2 }]);
    expect(processosNoTopo([])).toEqual([]);
    expect(processosNoTopo(l, 1)).toHaveLength(1);
  });
  it('pressão: somada por aparelho, o de mais avisos primeiro, com os minutos e o último', () => {
    expect(pressaoPorAparelho(l)).toEqual([
      { instanceId: 'android-01', avisos: 4, minutos: 1, ultimoEm: '2026-10-07T10:02:00Z' },
      { instanceId: 'android-05', avisos: 3, minutos: 2, ultimoEm: '2026-10-07T10:02:00Z' },
    ]);
  });
  it('defasagem em minutos inteiros; sem amostra ou data torta, null', () => {
    const t = Date.parse('2026-10-07T10:10:30Z');
    expect(defasagemEmMinutos('2026-10-07T10:07:00Z', t)).toBe(3);
    expect(defasagemEmMinutos('2026-10-07T10:10:00Z', t)).toBe(0);
    expect(defasagemEmMinutos(null, t)).toBeNull();
    expect(defasagemEmMinutos('lixo', t)).toBeNull();
  });
  it('a série abre onde não foi medido (nunca cai a zero) e respeita o topo da escala', () => {
    const t = trechosDaSerie([0, 50, null, 100], 90, 10, 100);
    expect(t).toEqual([[{ x: 0, y: 10 }, { x: 30, y: 5 }], [{ x: 90, y: 0 }]]);
    expect(trechosDaSerie([null, null], 90, 10)).toEqual([]);
    expect(trechosDaSerie([5], 90, 10, 10)).toEqual([[{ x: 0, y: 5 }]]);
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  vi.useRealTimers();
  await act(async () => root.unmount());
  container.remove();
});

const abrir = async () => { await act(async () => root.render(<HostPage />)); };
const agora = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
const minutosAtras = (n: number) => new Date(Date.now() - n * 60_000).toISOString().replace(/\.\d{3}Z$/, 'Z');

describe('a tela Host', () => {
  it('mostra as medidas, os processos e a pressão por aparelho; só leitura (nenhum botão de ação)', async () => {
    backend.on('GET', /^\/api\/host\/amostras$/, () => json({ items: [
      amostra(minutosAtras(2), { cpu_host_pct: 30, processos_top: 'python:20;node:5', avisos_pressao: 'android-05:3' }),
      amostra(minutosAtras(1), { cpu_host_pct: 90, ram_livre_mb: 2048, processos_top: 'python:40', avisos_pressao: 'android-05:1;android-01:2' }),
      amostra(agora(), { cpu_host_pct: 50, processos_top: 'node:8' }),
    ] }));
    await abrir();
    await waitFor(() => expect(container.querySelector('[aria-label="Medidas do host"]')).not.toBeNull());
    const t = text(container);
    expect(t).toContain('50%');                                                              // CPU agora
    expect(t).toMatch(/90% às \d\d:\d\d:\d\dPico de CPU/);
    expect(t).toMatch(/57%CPU média na janela/);                                             // (30+90+50)/3
    expect(t).toMatch(/2,0 GBMenor RAM livre na janela/);
    expect(container.querySelector('tr[data-processo="python"]')).not.toBeNull();
    expect(text(container.querySelector('tr[data-aparelho="android-05"]')!)).toContain('4');  // 3 + 1 avisos
    expect(container.querySelectorAll('svg[role="img"]').length).toBe(4);
    expect(container.querySelectorAll('button').length).toBe(0);
    expect(backend.callsTo('GET', /host\/amostras/)[0]!.query.get('horas')).toBe('1');
  });

  it('trocar a janela relê com horas=6 e horas=24', async () => {
    backend.on('GET', /^\/api\/host\/amostras$/, () => json({ items: [amostra(agora())] }));
    await abrir();
    await waitFor(() => expect(container.querySelector('[aria-label="Medidas do host"]')).not.toBeNull());
    await setValue(container.querySelector('select')!, '6');
    await waitFor(() => expect(backend.callsTo('GET', /host\/amostras/).some((c) => c.query.get('horas') === '6')).toBe(true));
    await setValue(container.querySelector('select')!, '24');
    await waitFor(() => expect(backend.callsTo('GET', /host\/amostras/).some((c) => c.query.get('horas') === '24')).toBe(true));
  });

  it('sem pressão: diz que vazio também vale "não medido"; amostra velha avisa que o amostrador pode ter parado', async () => {
    backend.on('GET', /^\/api\/host\/amostras$/, () => json({ items: [amostra(minutosAtras(10))] }));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Nenhum aviso de pressão na janela'));
    expect(text(container)).toContain('quando o amostrador não consegue medir');
    expect(text(container)).toContain('A última amostra é antiga');
    expect(text(container)).toContain('Nenhum processo no topo nesta janela.');
  });

  it('janela sem amostra nenhuma: estado vazio explicado, nunca zeros', async () => {
    backend.on('GET', /^\/api\/host\/amostras$/, () => json({ items: [] }));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Nenhuma amostra do host na janela'));
    expect(container.querySelector('[aria-label="Medidas do host"]')).toBeNull();
  });

  it('sem a rota (404) lê o exemplo e AVISA; outro erro vira erro com "tentar de novo", nunca exemplo', async () => {
    backend.on('GET', /^\/api\/host\/amostras$/, () => apiError(404, 'nao_encontrado', 'sem rota'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Dados de exemplo'));
    expect(text(container)).toContain('inventado');
    expect(container.querySelector('tr[data-aparelho="android-05"]')).not.toBeNull();
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.on('GET', /^\/api\/host\/amostras$/, () => apiError(500, 'falha', 'banco fora'));
    await abrir();
    await waitFor(() => expect(text(container)).toContain('Tentar de novo'));
    expect(text(container)).not.toContain('Dados de exemplo');
  });

  it('relê a cada minuto sem apagar os números que já estão na tela', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    backend.on('GET', /^\/api\/host\/amostras$/, () => json({ items: [amostra(agora())] }));
    await abrir();
    await waitFor(() => expect(container.querySelector('[aria-label="Medidas do host"]')).not.toBeNull());
    expect(backend.callsTo('GET', /host\/amostras/)).toHaveLength(1);
    await act(async () => { vi.advanceTimersByTime(PERIODO_DA_RELEITURA_MS); });
    await waitFor(() => expect(backend.callsTo('GET', /host\/amostras/)).toHaveLength(2));
    expect(container.querySelector('[aria-label="Medidas do host"]')).not.toBeNull();
    expect(byRole('combobox', /Janela/, container)).toBeTruthy();
  });
});
