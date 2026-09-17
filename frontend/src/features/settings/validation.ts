import type { AppConfigInput, Settings } from '../../api/types';

// ---- Limites ------------------------------------------------------------------------------------

/** Chaves numéricas de `Settings` (todas, menos os interruptores). */
export type NumericSettingKey = { [K in keyof Settings]: Settings[K] extends number ? K : never }[keyof Settings];
/** Chaves booleanas de `Settings` (v0.2: `auto_start_devices`). */
export type BooleanSettingKey = { [K in keyof Settings]: Settings[K] extends boolean ? K : never }[keyof Settings];

export interface LimitField {
  key: NumericSettingKey;
  label: string;
  unit: string;
  hint: string;
  min: number;
  max: number;
  integer: boolean;
}

/** Interruptor liga/desliga de um grupo. */
export interface ToggleField {
  key: BooleanSettingKey;
  label: string;
  hint: string;
}

export interface LimitGroup {
  /** Identifica grupos que ganham conteúdo extra na tela (ex.: a linha de recursos do backend no rodízio). */
  id?: 'rotation';
  title: string;
  description: string;
  toggles?: ToggleField[];
  fields: LimitField[];
}

const int = (key: NumericSettingKey, label: string, unit: string, hint: string, min: number, max: number): LimitField =>
  ({ key, label, unit, hint, min, max, integer: true });
const dec = (key: NumericSettingKey, label: string, unit: string, hint: string, min: number, max: number): LimitField =>
  ({ key, label, unit, hint, min, max, integer: false });

/** Faixas propositalmente largas: o backend é quem valida de verdade; aqui só barramos o absurdo. */
export const LIMIT_GROUPS: LimitGroup[] = [
  {
    title: 'Capacidade',
    description: 'Quanto a máquina faz ao mesmo tempo.',
    fields: [
      int('max_active_devices', 'Aparelhos ativos ao mesmo tempo', 'aparelhos', 'Instâncias além deste limite esperam na fila.', 1, 10),
      int('boot_parallelism', 'Inicializações em paralelo', 'emuladores', 'Boot é pesado: valores altos deixam tudo mais lento.', 1, 10),
      int('max_ai_concurrency', 'Chamadas de IA em paralelo', 'chamadas', 'Limita o uso simultâneo do provedor de IA.', 1, 50),
    ],
  },
  {
    id: 'rotation',
    title: 'Rodízio de aparelhos',
    description: 'N contas sobre K vagas de RAM: o agendador liga quem tem tarefa e desliga quem está ocioso.',
    toggles: [
      {
        key: 'auto_start_devices',
        label: 'Ligar aparelhos sob demanda',
        hint: 'Com tarefa na fila, o aparelho desligado ou hibernado é ligado sozinho; sem vaga, o objetivo fica “aguardando vaga”.',
      },
    ],
    fields: [
      int('max_online_devices', 'Vagas de RAM (aparelhos ligados ao mesmo tempo)', 'vagas', 'De 1 a 10. Contam os ligados, os que estão ligando e os que estão desligando.', 1, 10),
      int('min_online_dwell_s', 'Tempo mínimo ligado', 'segundos', 'Anti-vaivém: antes disso o aparelho não cede a vaga.', 0, 3600),
      int('idle_stop_s', 'Desligar por ociosidade após', 'segundos', '0 = só desliga para ceder vaga', 0, 86_400),
    ],
  },
  {
    title: 'Limites por objetivo',
    description: 'Freios para a IA não insistir indefinidamente.',
    fields: [
      int('max_steps_per_objective', 'Etapas por objetivo', 'etapas', 'Máximo de etapas em um plano.', 1, 200),
      int('for_each_max_items', 'Itens por coleta', 'itens', 'Teto de itens lidos de uma lista para repetir etapas (ex.: “todos os contatos”). Acima disso a execução bloqueia em vez de truncar.', 1, 200),
      int('max_actions_per_step', 'Ações por etapa', 'ações', 'Máximo de toques/gestos dentro de uma etapa.', 1, 500),
      int('max_attempts_per_step', 'Tentativas por etapa', 'tentativas', 'Etapas com efeito externo nunca são repetidas automaticamente.', 1, 20),
      int('no_progress_limit', 'Ações sem progresso', 'ações', 'Depois disso a etapa é dada como travada.', 1, 100),
    ],
  },
  {
    title: 'Tempos',
    description: 'Tempos limite e espera entre tentativas.',
    fields: [
      dec('step_timeout_s', 'Tempo limite da etapa', 'segundos', '', 1, 3600),
      dec('objective_timeout_s', 'Tempo limite do objetivo', 'segundos', '', 1, 86_400),
      dec('driver_call_timeout_s', 'Tempo limite por chamada ao aparelho', 'segundos', '', 1, 600),
      dec('retry_backoff_s', 'Espera antes de tentar de novo', 'segundos', '', 0, 600),
    ],
  },
  {
    title: 'Orçamento de IA',
    description: 'Tetos de consumo do provedor.',
    fields: [
      int('ai_max_calls_per_objective', 'Chamadas de IA por objetivo', 'chamadas', '', 1, 10_000),
      int('ai_max_tokens_per_run', 'Tokens por execução', 'tokens', 'Soma de entrada e saída em todas as instâncias.', 1000, 1_000_000_000),
    ],
  },
  {
    title: 'Captura de tela',
    description: 'Frequência das imagens e quando um frame passa a ser “desatualizado”.',
    fields: [
      dec('capture_grid_interval_s', 'Intervalo na grade', 'segundos', '', 0.1, 120),
      dec('capture_focus_interval_s', 'Intervalo no aparelho em foco', 'segundos', '', 0.05, 60),
      int('frame_max_age_ms', 'Idade máxima do frame', 'milissegundos', 'Acima disso o frame é marcado como desatualizado e entradas manuais são recusadas.', 100, 600_000),
    ],
  },
  {
    title: 'Retenção',
    description: 'Por quanto tempo os dados locais são mantidos.',
    fields: [
      int('log_retention_days', 'Logs', 'dias', '', 1, 3650),
      int('evidence_retention_days', 'Evidências', 'dias', 'Capturas de tela e resultados do verificador.', 1, 3650),
    ],
  },
];

export const ALL_LIMIT_FIELDS: LimitField[] = LIMIT_GROUPS.flatMap((g) => g.fields);
export const ALL_TOGGLE_FIELDS: ToggleField[] = LIMIT_GROUPS.flatMap((g) => g.toggles ?? []);

/** Aceita vírgula decimal ("0,5"). Devolve NaN se não for número. */
export function parseNumber(text: string): number {
  const t = text.trim().replace(',', '.');
  if (t === '' || !/^-?\d+(\.\d+)?$/.test(t)) return Number.NaN;
  return Number(t);
}

export function validateLimit(field: LimitField, text: string): string | null {
  if (text.trim() === '') return 'Informe um valor.';
  const n = parseNumber(text);
  if (!Number.isFinite(n)) return 'Use apenas números.';
  if (field.integer && !Number.isInteger(n)) return 'Use um número inteiro.';
  if (n < field.min) return `O mínimo é ${field.min}.`;
  if (n > field.max) return `O máximo é ${field.max}.`;
  return null;
}

/** Regras que envolvem mais de um campo. */
export function crossValidate(values: Partial<Record<NumericSettingKey, number>>): Partial<Record<NumericSettingKey, string>> {
  const errors: Partial<Record<NumericSettingKey, string>> = {};
  const step = values.step_timeout_s;
  const objective = values.objective_timeout_s;
  if (typeof step === 'number' && typeof objective === 'number' && objective < step) {
    errors.objective_timeout_s = 'Deve ser maior ou igual ao tempo limite da etapa.';
  }
  const boot = values.boot_parallelism;
  const active = values.max_active_devices;
  if (typeof boot === 'number' && typeof active === 'number' && boot > active) {
    errors.boot_parallelism = 'Não pode passar do número de aparelhos ativos.';
  }
  return errors;
}

/** Rascunho do formulário: texto para os números (como digitado) e booleano para os interruptores. */
export type LimitDrafts = Partial<Record<NumericSettingKey, string>> & Partial<Record<BooleanSettingKey, boolean>>;

export interface LimitsFormState {
  errors: Partial<Record<NumericSettingKey, string>>;
  /** Só o que mudou E é válido — é o corpo do `PUT /api/settings`. */
  patch: Partial<Settings>;
  dirtyCount: number;
}

/** Texto exibido no campo para um valor do servidor (vírgula decimal). */
export function limitToText(n: number | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? String(n).replace('.', ',') : '';
}

/**
 * Compara o rascunho com o que o servidor tem: valida o que mudou e monta o patch mínimo.
 * Campo sem rascunho (ou igual ao servidor) não entra no patch nem conta como alteração.
 */
export function buildSettingsPatch(settings: Settings, drafts: LimitDrafts): LimitsFormState {
  const errors: Partial<Record<NumericSettingKey, string>> = {};
  const values: Partial<Record<NumericSettingKey, number>> = {};
  const patch: Partial<Settings> = {};
  let dirtyCount = 0;

  for (const f of ALL_LIMIT_FIELDS) {
    const text = drafts[f.key];
    if (text === undefined) {
      values[f.key] = settings[f.key];
      continue;
    }
    const n = parseNumber(text);
    if (Number.isFinite(n)) values[f.key] = n;
    if (n === settings[f.key]) continue;
    dirtyCount += 1;
    const err = validateLimit(f, text);
    if (err) errors[f.key] = err;
    else patch[f.key] = n;
  }

  for (const t of ALL_TOGGLE_FIELDS) {
    const draft = drafts[t.key];
    if (draft === undefined || draft === settings[t.key]) continue;
    dirtyCount += 1;
    patch[t.key] = draft;
  }

  // Erros do próprio campo têm prioridade sobre os cruzados.
  if (dirtyCount > 0) Object.assign(errors, { ...crossValidate(values), ...errors });
  return { errors, patch, dirtyCount };
}

// ---- Aplicativos --------------------------------------------------------------------------------

export interface SelectorRow {
  key: string;
  name: string;
  value: string;
}

export interface AppDraft {
  name: string;
  package: string;
  activity: string;
  apk_path: string;
  nav_hints: string;
  selectors: SelectorRow[];
}

export type AppErrors = Partial<Record<'name' | 'package' | 'activity' | 'apk_path' | 'selectors', string>>;

const PACKAGE_RE = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$/;

export function validateApp(draft: AppDraft): AppErrors {
  const errors: AppErrors = {};
  if (!draft.name.trim()) errors.name = 'Dê um nome ao aplicativo.';
  const pkg = draft.package.trim();
  if (!pkg) errors.package = 'Informe o pacote (ex.: com.exemplo.app).';
  else if (!PACKAGE_RE.test(pkg)) errors.package = 'Formato inválido. Use algo como com.exemplo.app.';
  const activity = draft.activity.trim();
  if (activity && /\s/.test(activity)) errors.activity = 'A activity não pode conter espaços.';
  const apk = draft.apk_path.trim();
  if (apk && !/\.apk$/i.test(apk)) errors.apk_path = 'O caminho deve apontar para um arquivo .apk.';

  const names = new Set<string>();
  for (const row of draft.selectors) {
    const name = row.name.trim();
    const value = row.value.trim();
    if (!name && !value) continue; // linha vazia é ignorada
    if (!name || !value) {
      errors.selectors = 'Cada seletor precisa de nome e valor (ou deixe a linha vazia).';
      break;
    }
    if (names.has(name)) {
      errors.selectors = `O nome “${name}” está repetido.`;
      break;
    }
    names.add(name);
  }
  return errors;
}

/** Converte o rascunho do formulário no corpo esperado pela API (strings vazias → null). */
export function draftToInput(draft: AppDraft): AppConfigInput {
  const selectors: Record<string, string> = {};
  for (const row of draft.selectors) {
    const name = row.name.trim();
    const value = row.value.trim();
    if (name && value) selectors[name] = value;
  }
  return {
    name: draft.name.trim(),
    package: draft.package.trim(),
    activity: draft.activity.trim() || null,
    apk_path: draft.apk_path.trim() || null,
    nav_hints: draft.nav_hints.trim() || null,
    known_selectors: Object.keys(selectors).length > 0 ? selectors : null,
  };
}
