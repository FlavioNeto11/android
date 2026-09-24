import { describe, expect, it } from 'vitest';
import type { ServerLimits } from '../../api/types';
import { buildServerPatch, declaredText, gb, ramUsed } from './serverLimits';

const base: ServerLimits = {
  worker_id: 'worker-lan-01', name: 'Notebook da LAN', is_host: false, connected: true, maintenance: false,
  declared: { max_slots: 6, boot_parallelism: 1, max_working: null, min_free_ram_mb: 4096 },
  decided: { max_slots: null, boot_parallelism: null, max_working: null, min_free_ram_mb: null },
  effective: { max_slots: 6, boot_parallelism: 1, max_working: null, min_free_ram_mb: 4096 },
  locked: {}, online: 6, working: 1, devices: 6, cpu_percent: 23, cpu_count: 12, ram_free_mb: 38_000, ram_total_mb: 64_000,
};

describe('limites por servidor', () => {
  it('manda só o que mudou, com número', () => {
    expect(buildServerPatch(base, { max_slots: '10', boot_parallelism: '1' })).toEqual({ patch: { max_slots: 10 }, errors: {}, dirty: 1 });
  });

  it('vazio em "trabalhando" é "sem teto próprio"; nos outros é erro', () => {
    const decidido = { ...base, decided: { ...base.decided, max_working: 3 }, effective: { ...base.effective, max_working: 3 } };
    expect(buildServerPatch(decidido, { max_working: '' }).patch).toEqual({ max_working: null });
    expect(buildServerPatch(base, { max_working: '' }).dirty).toBe(0);
    expect(buildServerPatch(base, { max_slots: '' }).errors.max_slots).toMatch(/Informe um número/);
  });

  it('valida inteiro e faixa', () => {
    expect(buildServerPatch(base, { max_slots: '2,5' }).errors.max_slots).toBe('Use um número inteiro.');
    expect(buildServerPatch(base, { boot_parallelism: '40' }).errors.boot_parallelism).toMatch(/Entre 1 e 10/);
  });

  it('"voltar ao da máquina" só vira patch quando havia decisão', () => {
    expect(buildServerPatch(base, { max_slots: null }).dirty).toBe(0);
    const decidido = { ...base, decided: { ...base.decided, max_slots: 9 }, effective: { ...base.effective, max_slots: 9 } };
    expect(buildServerPatch(decidido, { max_slots: null }).patch).toEqual({ max_slots: null });
  });

  it('campo travado nunca entra no patch', () => {
    const host = { ...base, is_host: true, locked: { min_free_ram_mb: 'guarda do boot' } };
    expect(buildServerPatch(host, { min_free_ram_mb: '1000' }).dirty).toBe(0);
  });

  it('textos de apoio', () => {
    expect(declaredText(base, 'max_slots')).toBe('6');
    expect(declaredText(base, 'max_working')).toBe('sem teto próprio');
    expect(ramUsed(base)).toBeCloseTo(0.40625);
    expect(gb(38_000)).toBe('37,1 GB');
  });
});
