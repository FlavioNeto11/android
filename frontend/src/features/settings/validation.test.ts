import { describe, expect, it } from 'vitest';
import type { Settings } from '../../api/types';
import { ALL_LIMIT_FIELDS, crossValidate, draftToInput, parseNumber, validateApp, validateLimit, type AppDraft } from './validation';

const field = (key: keyof Settings) => {
  const f = ALL_LIMIT_FIELDS.find((x) => x.key === key);
  if (!f) throw new Error(`campo ${key} não mapeado`);
  return f;
};

describe('limites', () => {
  it('cobre todos os 18 campos de Settings exatamente uma vez', () => {
    const keys = ALL_LIMIT_FIELDS.map((f) => f.key).sort();
    expect(keys).toEqual([
      'ai_max_calls_per_objective', 'ai_max_tokens_per_run', 'boot_parallelism', 'capture_focus_interval_s',
      'capture_grid_interval_s', 'driver_call_timeout_s', 'evidence_retention_days', 'frame_max_age_ms',
      'log_retention_days', 'max_actions_per_step', 'max_active_devices', 'max_ai_concurrency',
      'max_attempts_per_step', 'max_steps_per_objective', 'no_progress_limit', 'objective_timeout_s',
      'retry_backoff_s', 'step_timeout_s',
    ]);
  });

  it('parseNumber aceita vírgula decimal e rejeita lixo', () => {
    expect(parseNumber('0,5')).toBe(0.5);
    expect(parseNumber(' 12 ')).toBe(12);
    expect(parseNumber('1e3')).toBeNaN();
    expect(parseNumber('abc')).toBeNaN();
    expect(parseNumber('')).toBeNaN();
  });

  it('valida obrigatório, inteiro e faixa', () => {
    expect(validateLimit(field('max_active_devices'), '')).toBe('Informe um valor.');
    expect(validateLimit(field('max_active_devices'), 'dez')).toBe('Use apenas números.');
    expect(validateLimit(field('max_active_devices'), '2,5')).toBe('Use um número inteiro.');
    expect(validateLimit(field('max_active_devices'), '0')).toBe('O mínimo é 1.');
    expect(validateLimit(field('max_active_devices'), '11')).toBe('O máximo é 10.');
    expect(validateLimit(field('max_active_devices'), '10')).toBeNull();
    expect(validateLimit(field('capture_focus_interval_s'), '0,5')).toBeNull();
  });

  it('valida regras entre campos', () => {
    expect(crossValidate({ step_timeout_s: 120, objective_timeout_s: 60 })).toHaveProperty('objective_timeout_s');
    expect(crossValidate({ boot_parallelism: 5, max_active_devices: 3 })).toHaveProperty('boot_parallelism');
    expect(crossValidate({ step_timeout_s: 60, objective_timeout_s: 600, boot_parallelism: 2, max_active_devices: 10 })).toEqual({});
  });
});

describe('aplicativos', () => {
  const base: AppDraft = { name: 'QA Messenger', package: 'com.poc.qamessenger', activity: '', apk_path: '', nav_hints: '', selectors: [] };

  it('aceita um rascunho mínimo válido', () => {
    expect(validateApp(base)).toEqual({});
  });

  it('exige nome e pacote bem formado', () => {
    expect(validateApp({ ...base, name: ' ' })).toHaveProperty('name');
    expect(validateApp({ ...base, package: '' })).toHaveProperty('package');
    expect(validateApp({ ...base, package: 'semponto' })).toHaveProperty('package');
    expect(validateApp({ ...base, package: 'com.1bad' })).toHaveProperty('package');
  });

  it('valida activity, apk e seletores', () => {
    expect(validateApp({ ...base, activity: '.Main Activity' })).toHaveProperty('activity');
    expect(validateApp({ ...base, apk_path: 'C:\\apps\\app.zip' })).toHaveProperty('apk_path');
    expect(validateApp({ ...base, apk_path: 'C:\\apps\\app.APK' })).toEqual({});
    expect(validateApp({ ...base, selectors: [{ key: '1', name: 'send', value: '' }] })).toHaveProperty('selectors');
    expect(validateApp({ ...base, selectors: [{ key: '1', name: 'a', value: 'x' }, { key: '2', name: 'a', value: 'y' }] })).toHaveProperty('selectors');
    expect(validateApp({ ...base, selectors: [{ key: '1', name: '', value: '' }] })).toEqual({});
  });

  it('converte o rascunho no corpo da API', () => {
    expect(
      draftToInput({
        ...base, name: ' QA ', activity: ' .MainActivity ', nav_hints: '  ',
        selectors: [{ key: '1', name: 'send_button', value: 'id/send' }, { key: '2', name: '', value: '' }],
      }),
    ).toEqual({
      name: 'QA', package: 'com.poc.qamessenger', activity: '.MainActivity', apk_path: null, nav_hints: null,
      known_selectors: { send_button: 'id/send' },
    });
    expect(draftToInput(base).known_selectors).toBeNull();
  });
});
