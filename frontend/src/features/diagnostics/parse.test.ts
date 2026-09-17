import { describe, expect, it } from 'vitest';
import { accelerationOk, parseTools, toolFromValue } from './parse';

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
