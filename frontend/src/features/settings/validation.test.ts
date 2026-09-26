import { describe, expect, it } from 'vitest';
import { SETTINGS } from '../../test/fixtures';
import {
  ALL_CHOICE_FIELDS, ALL_LIMIT_FIELDS, ALL_TOGGLE_FIELDS, LIMIT_GROUPS, buildSettingsPatch, crossValidate, draftToInput, limitToText,
  parseNumber, validateApp, validateLimit, type AppDraft, type LimitDrafts, type NumericSettingKey,
} from './validation';

const field = (key: NumericSettingKey) => {
  const f = ALL_LIMIT_FIELDS.find((x) => x.key === key);
  if (!f) throw new Error(`campo ${key} não mapeado`);
  return f;
};

describe('limites', () => {
  it('cobre todos os campos de Settings exatamente uma vez (27 numéricos + 1 interruptor + 1 escolha + 2 no cartão do servidor)', () => {
    const keys = ALL_LIMIT_FIELDS.map((f) => f.key).sort();
    expect(keys).toEqual([
      'ai_max_calls_per_objective', 'ai_max_tokens_per_run', 'ai_max_usd_per_day', 'ai_max_usd_per_run',
      'capture_focus_interval_s',
      'capture_grid_interval_s', 'driver_call_timeout_s', 'evidence_retention_days',
      'fleet_max_accounts_per_target', 'fleet_min_spacing_between_accounts_s', 'fleet_spacing_jitter_s',
      'fleet_target_window_s', 'for_each_max_items', 'frame_max_age_ms',
      'idle_stop_s', 'log_retention_days', 'max_actions_per_step', 'max_active_devices', 'max_ai_concurrency',
      'max_attempts_per_step', 'max_steps_per_objective', 'min_online_dwell_s', 'no_progress_limit',
      'objective_timeout_s', 'retry_backoff_s', 'session_unknown_retry_cap', 'step_timeout_s',
    ]);
    expect(ALL_TOGGLE_FIELDS.map((t) => t.key)).toEqual(['auto_start_devices']);
    expect(ALL_CHOICE_FIELDS.map((c) => c.key)).toEqual(['preview_mode']);
    // nada de Settings fica de fora: vagas e boots DESTE servidor são editados no cartão dele (Por servidor),
    // porque não valem para o notebook — ficavam no formulário do parque como se valessem.
    const noCartaoDoServidor = ['boot_parallelism', 'max_online_devices'];
    const covered = [...keys, ...ALL_TOGGLE_FIELDS.map((t) => t.key), ...ALL_CHOICE_FIELDS.map((c) => c.key), ...noCartaoDoServidor].sort();
    expect(covered).toEqual(Object.keys(SETTINGS).sort());
    // e nenhum campo aparece duas vezes
    expect(new Set(covered).size).toBe(covered.length);
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
    // O teto de 10 era do CÓDIGO, não do parque: com 14 aparelhos e um segundo worker ele apertava sozinho.
    expect(validateLimit(field('max_active_devices'), '65')).toBe('O máximo é 64.');
    expect(validateLimit(field('max_active_devices'), '15')).toBeNull();
    expect(validateLimit(field('capture_focus_interval_s'), '0,5')).toBeNull();
  });

  it('valida regras entre campos', () => {
    expect(crossValidate({ step_timeout_s: 120, objective_timeout_s: 60 })).toHaveProperty('objective_timeout_s');
    expect(crossValidate({ boot_parallelism: 5, max_active_devices: 3 })).toEqual({}); // boots são por servidor agora
    expect(crossValidate({ step_timeout_s: 60, objective_timeout_s: 600, boot_parallelism: 2, max_active_devices: 10 })).toEqual({});
  });
});

describe('rodízio de aparelhos (v0.2)', () => {
  const rotation = LIMIT_GROUPS.find((g) => g.id === 'rotation');

  it('grupo com o interruptor e os dois tempos; as vagas são de cada servidor', () => {
    expect(rotation?.title).toBe('Rodízio de aparelhos');
    expect(rotation?.toggles?.map((t) => [t.key, t.label])).toEqual([['auto_start_devices', 'Ligar aparelhos sob demanda']]);
    // `max_online_devices` saiu: são as vagas DESTE servidor, editadas no cartão dele (Limites → Por servidor).
    expect(rotation?.fields.map((f) => f.key)).toEqual(['min_online_dwell_s', 'idle_stop_s']);
    expect(rotation?.description).toContain('Por servidor');
    expect(field('idle_stop_s').hint).toBe('0 = só desliga para ceder vaga');
    for (const f of rotation?.fields ?? []) expect(f.hint).not.toBe('');
    for (const t of rotation?.toggles ?? []) expect(t.hint).not.toBe('');
  });

  it('valida as faixas do backend: tempos inteiros, 0 permitido onde faz sentido', () => {
    expect(validateLimit(field('idle_stop_s'), '0')).toBeNull();
    expect(validateLimit(field('min_online_dwell_s'), '0')).toBeNull();
    expect(validateLimit(field('min_online_dwell_s'), '1,5')).toBe('Use um número inteiro.');
    expect(validateLimit(field('idle_stop_s'), '86401')).toBe('O máximo é 86400.');
  });

  it('ida e volta: valores do servidor → formulário → patch → servidor', () => {
    expect(buildSettingsPatch(SETTINGS, {})).toEqual({ errors: {}, patch: {}, dirtyCount: 0 });
    expect(limitToText(SETTINGS.idle_stop_s)).toBe('0');

    const edited = buildSettingsPatch(SETTINGS, { auto_start_devices: true, min_online_dwell_s: '90', idle_stop_s: '300' });
    expect(edited.errors).toEqual({});
    expect(edited.dirtyCount).toBe(3);
    expect(edited.patch).toEqual({ auto_start_devices: true, min_online_dwell_s: 90, idle_stop_s: 300 });
    expect(typeof edited.patch.auto_start_devices).toBe('boolean'); // o interruptor vai como booleano, não como texto

    const saved = { ...SETTINGS, ...edited.patch };
    expect(buildSettingsPatch(saved, { auto_start_devices: true, min_online_dwell_s: '90', idle_stop_s: '300' }))
      .toEqual({ errors: {}, patch: {}, dirtyCount: 0 });
    expect(limitToText(saved.idle_stop_s)).toBe('300');
    expect(buildSettingsPatch(saved, { auto_start_devices: false }).patch).toEqual({ auto_start_devices: false });
  });

  it('só o que mudou entra no patch; valor inválido bloqueia o campo mas não os outros', () => {
    const r = buildSettingsPatch(SETTINGS, { auto_start_devices: false, idle_stop_s: '-1', min_online_dwell_s: '120' });
    expect(r.dirtyCount).toBe(2); // o interruptor ficou igual ao servidor
    expect(r.errors).toEqual({ idle_stop_s: 'O mínimo é 0.' });
    expect(r.patch).toEqual({ min_online_dwell_s: 120 });
  });

  it('continua aplicando as regras entre campos e a vírgula decimal dos campos antigos', () => {
    const r = buildSettingsPatch(SETTINGS, { step_timeout_s: '100', objective_timeout_s: '50', capture_focus_interval_s: '0,25' });
    expect(r.errors).toHaveProperty('objective_timeout_s');
    expect(r.patch.capture_focus_interval_s).toBe(0.25);
    // boots em paralelo não são mais validados aqui: sem o campo na tela, o erro bloquearia o salvar às cegas
    expect(buildSettingsPatch(SETTINGS, { max_active_devices: '1' }).errors).toEqual({});
  });
});

describe('prévia dos aparelhos (v0.20, contrato C2)', () => {
  const choice = ALL_CHOICE_FIELDS.find((c) => c.key === 'preview_mode');

  it('fica no grupo de captura, com as duas opções e o padrão dito no rótulo', () => {
    expect(LIMIT_GROUPS.find((g) => g.choices?.some((c) => c.key === 'preview_mode'))?.title).toBe('Captura de tela');
    expect(choice?.label).toBe('Prévia dos aparelhos');
    expect(choice?.options).toEqual([
      { value: 'on_demand', label: 'Sob demanda (padrão)' },
      { value: 'always', label: 'Sempre (modo antigo)' },
    ]);
    expect(choice?.hint).not.toBe('');
  });

  it('ida e volta: só entra no patch quando muda, e como o valor do contrato', () => {
    expect(buildSettingsPatch(SETTINGS, { preview_mode: 'on_demand' })).toEqual({ errors: {}, patch: {}, dirtyCount: 0 });
    const edited = buildSettingsPatch(SETTINGS, { preview_mode: 'always', idle_stop_s: '30' });
    expect(edited).toEqual({ errors: {}, patch: { preview_mode: 'always', idle_stop_s: 30 }, dirtyCount: 2 });
    const saved = { ...SETTINGS, ...edited.patch };
    expect(buildSettingsPatch(saved, { preview_mode: 'always' }).patch).toEqual({});
    expect(buildSettingsPatch(saved, { preview_mode: 'on_demand' }).patch).toEqual({ preview_mode: 'on_demand' });
  });

  it('valor fora das opções nunca vai ao servidor', () => {
    const drafts = { preview_mode: 'turbo' } as unknown as LimitDrafts;
    expect(buildSettingsPatch(SETTINGS, drafts)).toEqual({ errors: {}, patch: {}, dirtyCount: 0 });
  });

  it('backend sem o campo (anterior ao adendo): o formulário não inventa alteração', () => {
    const { preview_mode: _ausente, ...antigo } = SETTINGS;
    expect(buildSettingsPatch(antigo, {})).toEqual({ errors: {}, patch: {}, dirtyCount: 0 });
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
