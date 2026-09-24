import { describe, expect, it } from 'vitest';
import { accelerationOk, diskFreeGb, estimatedMaxDevices, parseBootMeasurements, parseTools, toolFromValue } from './parse';

describe('toolFromValue', () => {
  it('interpreta formas simples', () => {
    expect(toolFromValue('adb', '35.0.2')).toMatchObject({ ok: true, version: '35.0.2' });
    expect(toolFromValue('adb', '')).toMatchObject({ ok: false, version: null });
    expect(toolFromValue('adb', true)).toMatchObject({ ok: true });
    expect(toolFromValue('adb', null)).toMatchObject({ ok: false });
  });

  it('interpreta objetos com chaves variadas', () => {
    expect(toolFromValue('appium', { found: true, version: '2.11', path: 'C:\\appium' })).toMatchObject({ ok: true, version: '2.11', detail: 'C:\\appium' });
    expect(toolFromValue('node', { ok: false, error: 'não encontrado no PATH' })).toMatchObject({ ok: false, detail: 'não encontrado no PATH' });
    expect(toolFromValue('java', { version: 17 })).toMatchObject({ ok: true, version: '17' });
    expect(toolFromValue('x', { missing: true })).toMatchObject({ ok: false });
    expect(toolFromValue('y', { something: 'else' })).toMatchObject({ ok: null });
  });
});

describe('parseTools', () => {
  it('aceita objeto ou lista e devolve null para o resto', () => {
    expect(parseTools({ adb: '1', emulator: { found: false } })?.map((t) => [t.name, t.ok])).toEqual([['adb', true], ['emulator', false]]);
    expect(parseTools([{ name: 'adb', ok: true }, { tool: 'sdkmanager', installed: false }])?.map((t) => [t.name, t.ok])).toEqual([['adb', true], ['sdkmanager', false]]);
    expect(parseTools('nope')).toBeNull();
    expect(parseTools(undefined)).toBeNull();
  });
});

describe('accelerationOk', () => {
  it('encontra o booleano mais provável', () => {
    expect(accelerationOk({ available: true, hypervisor: 'WHPX' })).toBe(true);
    expect(accelerationOk({ ok: false })).toBe(false);
    expect(accelerationOk({ hypervisor: 'WHPX' })).toBeNull();
    expect(accelerationOk(true)).toBe(true);
    expect(accelerationOk(null)).toBeNull();
  });
});

describe('parseBootMeasurements', () => {
  it('extrai só as amostras com boot_seconds, na ordem original', () => {
    const rows = [
      { ts: '2026-09-24T10:00:00Z', kind: 'capacity', online: 2 },
      { ts: '2026-09-24T10:01:00Z', boot_seconds: 38.5, mem_available_gb: 20.1 },
      { ts: '2026-09-24T10:02:00Z', kind: 'hibernate', saved: true },
      { boot_seconds: 52.1, mem_available_gb: 14.4 },
    ];
    expect(parseBootMeasurements(rows)).toEqual([
      { index: 1, ts: '2026-09-24T10:01:00Z', bootSeconds: 38.5, memFreeGb: 20.1 },
      { index: 3, ts: null, bootSeconds: 52.1, memFreeGb: 14.4 },
    ]);
  });

  it('aceita a forma achatada do fixture de teste (boot_s) e tolera falta de memória', () => {
    expect(parseBootMeasurements([{ boot_seconds: 10 }])).toEqual([{ index: 0, ts: null, bootSeconds: 10, memFreeGb: null }]);
    expect(parseBootMeasurements([{ boot_s: 12 }])).toEqual([{ index: 0, ts: null, bootSeconds: 12, memFreeGb: null }]);
  });

  it('devolve lista vazia para entrada não-array ou sem nenhuma amostra de boot', () => {
    expect(parseBootMeasurements(undefined)).toEqual([]);
    expect(parseBootMeasurements([{ devices: 2, cpu_percent: 41 }])).toEqual([]);
  });
});

describe('diskFreeGb', () => {
  it('lê números diretos quando o backend os manda', () => {
    expect(diskFreeGb({ disk_free_gb: 120, disk_total_gb: 500 })).toEqual({ freeGb: 120, totalGb: 500 });
  });

  it('extrai de texto livre no formato usado hoje pelo backend (disk_project/disk_sdk)', () => {
    expect(diskFreeGb({ disk_project: 'C:\\git\\android — 420 GB livres de 953 GB' })).toEqual({ freeGb: 420, totalGb: 953 });
    expect(diskFreeGb({ os: 'Windows', disk_sdk: 'D:\\Sdk — 12.5 GB livres de 200 GB' })).toEqual({ freeGb: 12.5, totalGb: 200 });
  });

  it('sem nenhum campo reconhecível, devolve null', () => {
    expect(diskFreeGb({ os: 'Windows' })).toBeNull();
    expect(diskFreeGb(null)).toBeNull();
  });
});

describe('estimatedMaxDevices', () => {
  it('procura a estimativa sob os nomes de chave usados pelo backend', () => {
    expect(estimatedMaxDevices({ estimated_max_simultaneous: 6 })).toBe(6);
    expect(estimatedMaxDevices({ estimated_max_devices: 10 })).toBe(10);
    expect(estimatedMaxDevices({ max_recommended_devices: 8 })).toBe(8);
    expect(estimatedMaxDevices({ note: 'sem número' })).toBeNull();
    expect(estimatedMaxDevices(null)).toBeNull();
  });
});
