import { describe, expect, it } from 'vitest';
import { SETTINGS } from '../../test/fixtures';
import {
  ALL_LIMIT_FIELDS, ALL_TOGGLE_FIELDS, LIMIT_GROUPS, buildSettingsPatch, crossValidate, draftToInput, limitToText, parseNumber,
  validateApp, validateLimit, type AppDraft, type NumericSettingKey,
} from './validation';

const field = (key: NumericSettingKey) => {
  const f = ALL_LIMIT_FIELDS.find((x) => x.key === key);
  if (!f) throw new Error(`campo ${key} não mapeado`);
  return f;
};

describe('limites', () => {
  it('cobre todos os campos de Settings exatamente uma vez (22 numéricos + 1 interruptor)', () => {
    const keys = ALL_LIMIT_FIELDS.map((f) => f.key).sort();
    expect(keys).toEqual([
      'ai_max_calls_per_objective', 'ai_max_tokens_per_run', 'boot_parallelism', 'capture_focus_interval_s',
      'capture_grid_interval_s', 'driver_call_timeout_s', 'evidence_retention_days', 'for_each_max_items', 'frame_max_age_ms',
      'idle_stop_s', 'log_retention_days', 'max_actions_per_step', 'max_active_devices', 'max_ai_concurrency',
      'max_attempts_per_step', 'max_online_devices', 'max_steps_per_objective', 'min_online_dwell_s', 'no_progress_limit',
      'objective_timeout_s', 'retry_backoff_s', 'step_timeout_s',
    ]);
    expect(ALL_TOGGLE_FIELDS.map((t) => t.key)).toEqual(['auto_start_devices']);
    // nada de Settings fica de fora do formulário
    const covered = [...keys, ...ALL_TOGGLE_FIELDS.map((t) => t.key)].sort();
    expect(covered).toEqual(Object.keys(SETTINGS).sort());
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

describe('rodízio de aparelhos (v0.2)', () => {
  const rotation = LIMIT_GROUPS.find((g) => g.id === 'rotation');

  it('grupo com o interruptor e os três campos, cada um com uma linha de ajuda', () => {
    expect(rotation?.title).toBe('Rodízio de aparelhos');
    expect(rotation?.toggles?.map((t) => [t.key, t.label])).toEqual([['auto_start_devices', 'Ligar aparelhos sob demanda']]);
    expect(rotation?.fields.map((f) => f.key)).toEqual(['max_online_devices', 'min_online_dwell_s', 'idle_stop_s']);
    expect(field('max_online_devices').label).toBe('Vagas de RAM (aparelhos ligados ao mesmo tempo)');
    expect(field('idle_stop_s').hint).toBe('0 = só desliga para ceder vaga');
    for (const f of rotation?.fields ?? []) expect(f.hint).not.toBe('');
    for (const t of rotation?.toggles ?? []) expect(t.hint).not.toBe('');
  });

  it('valida as faixas do backend: vagas 1–10, tempos inteiros, 0 permitido onde faz sentido', () => {
    expect(validateLimit(field('max_online_devices'), '0')).toBe('O mínimo é 1.');
    expect(validateLimit(field('max_online_devices'), '11')).toBe('O máximo é 10.');
    expect(validateLimit(field('max_online_devices'), '3')).toBeNull();
    expect(validateLimit(field('idle_stop_s'), '0')).toBeNull();
    expect(validateLimit(field('min_online_dwell_s'), '0')).toBeNull();
    expect(validateLimit(field('min_online_dwell_s'), '1,5')).toBe('Use um número inteiro.');
    expect(validateLimit(field('idle_stop_s'), '86401')).toBe('O máximo é 86400.');
  });

  it('ida e volta: valores do servidor → formulário → patch → servidor', () => {
    // 1) sem rascunho nada muda — o formulário mostra o que o servidor mandou
    expect(buildSettingsPatch(SETTINGS, {})).toEqual({ errors: {}, patch: {}, dirtyCount: 0 });
    expect(limitToText(SETTINGS.max_online_devices)).toBe('3');
    expect(limitToText(SETTINGS.idle_stop_s)).toBe('0');

    // 2) o usuário liga o rodízio e mexe nos três campos
    const edited = buildSettingsPatch(SETTINGS, { auto_start_devices: true, max_online_devices: '4', min_online_dwell_s: '90', idle_stop_s: '300' });
    expect(edited.errors).toEqual({});
    expect(edited.dirtyCount).toBe(4);
    expect(edited.patch).toEqual({ auto_start_devices: true, max_online_devices: 4, min_online_dwell_s: 90, idle_stop_s: 300 });
    expect(typeof edited.patch.auto_start_devices).toBe('boolean'); // o interruptor vai como booleano, não como texto

    // 3) o servidor devolve o Settings salvo: o mesmo rascunho deixa de ser uma alteração
    const saved = { ...SETTINGS, ...edited.patch };
    expect(buildSettingsPatch(saved, { auto_start_devices: true, max_online_devices: '4', min_online_dwell_s: '90', idle_stop_s: '300' }))
      .toEqual({ errors: {}, patch: {}, dirtyCount: 0 });
    expect(limitToText(saved.idle_stop_s)).toBe('300');

    // 4) e desligar de novo gera só o campo que mudou
    expect(buildSettingsPatch(saved, { auto_start_devices: false }).patch).toEqual({ auto_start_devices: false });
  });

  it('só o que mudou entra no patch; valor inválido bloqueia o campo mas não os outros', () => {
    const r = buildSettingsPatch(SETTINGS, { auto_start_devices: false, max_online_devices: '3', idle_stop_s: '-1', min_online_dwell_s: '120' });
    expect(r.dirtyCount).toBe(2); // o interruptor e as vagas ficaram iguais ao servidor
    expect(r.errors).toEqual({ idle_stop_s: 'O mínimo é 0.' });
    expect(r.patch).toEqual({ min_online_dwell_s: 120 });
  });

  it('continua aplicando as regras entre campos e a vírgula decimal dos campos antigos', () => {
    const r = buildSettingsPatch(SETTINGS, { boot_parallelism: '9', max_active_devices: '4', capture_focus_interval_s: '0,25' });
    expect(r.errors).toHaveProperty('boot_parallelism');
    expect(r.patch.capture_focus_interval_s).toBe(0.25);
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
