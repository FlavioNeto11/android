import type { AppConfigInput, Settings } from '../../api/types';

// ---- Limites ------------------------------------------------------------------------------------

// `-?`: sem ele, uma chave opcional de `Settings` (v0.20: `preview_mode`) poria `undefined` na união de chaves.
/** Chaves numéricas de `Settings` (todas, menos os interruptores e as escolhas). */
export type NumericSettingKey = { [K in keyof Settings]-?: NonNullable<Settings[K]> extends number ? K : never }[keyof Settings];
/** Chaves booleanas de `Settings` (v0.2: `auto_start_devices`). */
export type BooleanSettingKey = { [K in keyof Settings]-?: NonNullable<Settings[K]> extends boolean ? K : never }[keyof Settings];
/** Escolha de um grupo de política pelo id (28.61: `grupo_sem_aprovacao`; texto no backend, id vazio = desligado). */
export type GroupSettingKey = 'grupo_sem_aprovacao';
/** Escolhas entre valores nomeados (v0.20: `preview_mode`). */
export type ChoiceSettingKey = 'preview_mode';

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

/** Escolha de um grupo de política: a tela lista os grupos pelo nome, mas o que vai ao servidor é o id. */
export interface GroupPickField {
  key: GroupSettingKey;
  label: string;
  hint: string;
}

/** Escolha entre poucos valores nomeados, com o padrão dito no rótulo da opção. */
export interface ChoiceField {
  key: ChoiceSettingKey;
  label: string;
  hint: string;
  options: { value: NonNullable<Settings[ChoiceSettingKey]>; label: string }[];
}

export interface LimitGroup {
  /** Identifica grupos que ganham conteúdo extra na tela (ex.: a linha de recursos do backend no rodízio). */
  id?: 'rotation' | 'capture';
  /** O grupo só mostra os campos e os interruptores que o servidor manda (backend anterior à mudança que os criou não manda). */
  soSeOServidorManda?: boolean;
  title: string;
  description: string;
  toggles?: ToggleField[];
  choices?: ChoiceField[];
  grupos?: GroupPickField[];
  fields: LimitField[];
}

const int = (key: NumericSettingKey, label: string, unit: string, hint: string, min: number, max: number): LimitField =>
  ({ key, label, unit, hint, min, max, integer: true });
const dec = (key: NumericSettingKey, label: string, unit: string, hint: string, min: number, max: number): LimitField =>
  ({ key, label, unit, hint, min, max, integer: false });

/** Faixas propositalmente largas: o backend é quem valida de verdade; aqui só barramos o absurdo. */
export const LIMIT_GROUPS: LimitGroup[] = [
  {
    title: 'Capacidade do parque',
    description: 'Tetos que somam TODOS os servidores. Os de cada máquina ficam em “Por servidor”, acima.',
    fields: [
      int('max_active_devices', 'Teto geral de aparelhos trabalhando', 'aparelhos', 'Soma dos servidores: além disto, os objetivos esperam na fila mesmo que alguma máquina tenha folga.', 1, 64),
      int('max_ai_concurrency', 'Chamadas de IA em paralelo', 'chamadas', 'A conta da API é uma só para o parque inteiro.', 1, 50),
    ],
  },
  {
    id: 'rotation',
    title: 'Rodízio de aparelhos',
    description: 'N contas sobre K vagas de RAM: o agendador liga quem tem tarefa e desliga quem está ocioso. As vagas de cada máquina ficam em “Por servidor”.',
    toggles: [
      {
        key: 'auto_start_devices',
        label: 'Ligar aparelhos sob demanda',
        hint: 'Com tarefa na fila, o aparelho desligado ou hibernado é ligado sozinho; sem vaga, o objetivo fica “aguardando vaga”.',
      },
    ],
    fields: [
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
      int('ai_max_calls_per_objective', 'Chamadas de IA por objetivo', 'chamadas', 'Sem repetição (for_each) é o teto do objetivo; com lista, é o ponto de partida.', 1, 10_000),
      int('ai_max_calls_per_item', 'Chamadas a mais por item da lista', 'chamadas', 'Cada item além do primeiro soma isto ao teto do objetivo (o rejulgamento conta). 0 desliga a proporção.', 0, 200),
      int('ai_max_calls_absolute', 'Teto absoluto de chamadas por objetivo', 'chamadas', 'Limita o crescimento por item; nunca baixa o teto de cima.', 1, 5000),
      int('ai_max_tokens_per_run', 'Tokens por execução', 'tokens', 'Soma de entrada e saída em todas as instâncias.', 1000, 1_000_000_000),
      dec('ai_max_usd_per_run', 'US$ por execução', 'US$', 'Teto em dinheiro, pelos preços de ai.prices. Em 80 % sai um aviso; em 100 % as chamadas de IA são recusadas. 0 desliga.', 0, 10_000),
      dec('ai_max_usd_per_day', 'US$ por dia (UTC)', 'US$', 'Vale também para o que nasce fora de uma execução, como a prévia de persona. 0 desliga.', 0, 100_000),
    ],
  },
  {
    id: 'capture',
    title: 'Captura de tela',
    description: 'Frequência das imagens e quando uma imagem passa a ser “desatualizada”.',
    choices: [
      {
        key: 'preview_mode',
        label: 'Prévia dos aparelhos',
        hint: 'Sob demanda: só captura a prévia de quem está visível na grade ou aberto no foco; a IA continua vendo '
          + 'a tela quando age. Sempre: captura todos o tempo todo, como antes — vale na hora, sem reiniciar.',
        options: [
          { value: 'on_demand', label: 'Sob demanda (padrão)' },
          { value: 'always', label: 'Sempre (modo antigo)' },
        ],
      },
    ],
    fields: [
      dec('capture_grid_interval_s', 'Intervalo na grade', 'segundos', '', 0.1, 120),
      dec('capture_focus_interval_s', 'Intervalo no aparelho em foco', 'segundos', '', 0.05, 60),
      int('frame_max_age_ms', 'Idade máxima da imagem', 'milissegundos', 'Acima disso a imagem é marcada como desatualizada e entradas manuais são recusadas.', 100, 600_000),
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
  {
    soSeOServidorManda: true,
    title: 'Orquestração de operações',
    description: 'Quantas personas uma operação com vários agentes escolhe, e quantas das mais disponíveis a IA avalia (prova de 07/10).',
    fields: [
      int('orquestracao_max_escolhidas', 'Personas escolhidas por operação', 'personas', 'O teto da sugestão de alvos: a operação não passa disto. Padrão 30; vai de 1 a 64.', 1, 64),
      int('orquestracao_max_candidatas', 'Candidatas avaliadas pela IA', 'personas', 'As mais disponíveis que vão ao modelo para a escolha; nunca menos que as escolhidas. Padrão 60; vai de 1 a 120.', 1, 120),
      int('operacao_max_acoes_executadas', 'Contas que executam a ação final', 'contas', 'Quantas contas comentam de verdade no post nosso; as outras param até alguém liberar. Padrão 3; vai de 1 a 64.', 1, 64),
    ],
  },
  {
    soSeOServidorManda: true,
    title: 'Aprovação de política',
    description: 'Por padrão, a ação de uma persona que a política deixa sob aprovação espera o dono. Aqui um grupo de política fica dispensado dessa espera (28.61); vale na hora, sem reiniciar.',
    grupos: [
      {
        key: 'grupo_sem_aprovacao',
        label: 'Grupo dispensado da aprovação de política',
        hint: 'As personas desse grupo não passam pela aprovação de política; as recusas e a proteção de conta continuam. Em “Nenhum” a regra fica desligada.',
      },
    ],
    fields: [],
  },
  {
    title: 'Sinais e limites do Instagram',
    description: 'Quando parar de insistir sozinho.',
    fields: [
      int('session_unknown_retry_cap', 'Reobservações antes de pedir uma pessoa', 'tentativas',
         'Tela não reconhecida repetidas vezes seguidas vira "precisa de pessoa" em vez de insistir a cada tick.', 1, 20),
    ],
  },
];

export const ALL_LIMIT_FIELDS: LimitField[] = LIMIT_GROUPS.flatMap((g) => g.fields);
export const ALL_TOGGLE_FIELDS: ToggleField[] = LIMIT_GROUPS.flatMap((g) => g.toggles ?? []);
export const ALL_CHOICE_FIELDS: ChoiceField[] = LIMIT_GROUPS.flatMap((g) => g.choices ?? []);
export const ALL_GROUP_FIELDS: GroupPickField[] = LIMIT_GROUPS.flatMap((g) => g.grupos ?? []);

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
  const escolhidas = values.orquestracao_max_escolhidas;
  const candidatas = values.orquestracao_max_candidatas;
  if (typeof escolhidas === 'number' && typeof candidatas === 'number' && candidatas < escolhidas) {
    errors.orquestracao_max_candidatas = 'Deve ser maior ou igual às personas escolhidas.';
  }
  // "Ligando ao mesmo tempo ≤ aparelhos ativos" saiu daqui: boots em paralelo são de CADA servidor (cartão em
  // Limites → Por servidor), e o erro cairia num campo que este formulário nem mostra — bloqueando o salvar sem
  // nada destacado.
  return errors;
}

/** Rascunho do formulário: texto para os números (como digitado), booleano para os interruptores e o valor da escolha. */
export type LimitDrafts = Partial<Record<NumericSettingKey, string>> & Partial<Record<BooleanSettingKey, boolean>>
  & Partial<{ [K in ChoiceSettingKey]: NonNullable<Settings[K]> }> & Partial<Record<GroupSettingKey, string>>;

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

  for (const c of ALL_CHOICE_FIELDS) {
    const draft = drafts[c.key];
    // Valor fora das opções não vai ao servidor: a tela só oferece as opções, então isto seria rascunho corrompido.
    if (draft === undefined || draft === settings[c.key] || !c.options.some((o) => o.value === draft)) continue;
    dirtyCount += 1;
    patch[c.key] = draft;
  }

  for (const g of ALL_GROUP_FIELDS) {
    const draft = drafts[g.key];
    if (draft === undefined || draft === (settings[g.key] ?? '')) continue;
    dirtyCount += 1;
    patch[g.key] = draft;
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
