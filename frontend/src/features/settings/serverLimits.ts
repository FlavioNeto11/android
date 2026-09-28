import type { ServerLimitKey, ServerLimits, ServerLimitsPatch } from '../../api/types';

/** Rascunho de um campo: texto digitado, `null` = "voltar ao valor da máquina", ausente = sem mudança. */
export type ServerDrafts = Partial<Record<ServerLimitKey, string | null>>;

export interface ServerLimitField {
  key: ServerLimitKey;
  label: string;
  unit: string;
  hint: string;
  min: number;
  max: number;
  /** Vazio é válido e quer dizer "sem teto próprio" (`max_working`, `max_devices`). */
  optional?: boolean;
}

export const SERVER_LIMIT_FIELDS: ServerLimitField[] = [
  { key: 'max_slots', label: 'Aparelhos ligados ao mesmo tempo', unit: 'vagas', min: 1, max: 64,
    hint: 'Vagas de RAM desta máquina: o rodízio só liga mais um se houver vaga.' },
  { key: 'boot_parallelism', label: 'Ligando ao mesmo tempo', unit: 'emuladores', min: 1, max: 10,
    hint: 'Boot é pesado: vários juntos disputam CPU e podem travar em ANR.' },
  { key: 'max_working', label: 'Trabalhando ao mesmo tempo', unit: 'aparelhos', min: 1, max: 64, optional: true,
    hint: 'Aparelhos executando objetivo nesta máquina. Vazio = sem teto próprio (vale só o teto geral).' },
  { key: 'min_free_ram_mb', label: 'RAM livre mínima', unit: 'MB', min: 0, max: 1_048_576,
    hint: 'Depois de ligar mais um aparelho, esta máquina tem de manter pelo menos isto livre.' },
  // v0.26: por último, para não mudar a ordem dos campos que já existiam. Só decisão do dono — nenhuma máquina o
  // declara —, e a faixa é a do backend (`ServerLimitsBody.max_devices`, 1–256).
  { key: 'max_devices', label: 'Teto de aparelhos', unit: 'aparelhos', min: 1, max: 256, optional: true,
    hint: 'Aparelhos existentes nesta máquina; conferido ao criar aparelho; vazio = sem teto.' },
];

export interface ServerForm {
  patch: ServerLimitsPatch;
  errors: Partial<Record<ServerLimitKey, string>>;
  dirty: number;
}

/** O valor que o campo mostra quando não há rascunho: o que está valendo. */
export function shownValue(server: ServerLimits, key: ServerLimitKey): string {
  const v = server.effective[key];
  return v === null || v === undefined ? '' : String(v);
}

/** Frase do "valor da máquina" para a dica do campo. */
export function declaredText(server: ServerLimits, key: ServerLimitKey): string {
  const v = server.declared[key];
  if (key === 'max_working') return 'sem teto próprio';
  if (key === 'max_devices') return 'sem teto (a máquina não declara este número)';
  if (v === null || v === undefined) return 'não declarado pela máquina';
  return String(v);
}

export function buildServerPatch(server: ServerLimits, drafts: ServerDrafts): ServerForm {
  const patch: ServerLimitsPatch = {};
  const errors: ServerForm['errors'] = {};
  for (const field of SERVER_LIMIT_FIELDS) {
    const draft = drafts[field.key];
    if (draft === undefined || server.locked[field.key]) continue;
    // `?? null`: backend anterior ao v0.26 não manda `max_devices` — ausente é "nada decidido", não um valor a
    // desfazer (sem isto, "Da máquina" ou apagar o campo mandaria um `null` que ninguém pediu).
    const decidido = server.decided[field.key] ?? null;
    if (draft === null) {
      if (decidido !== null) patch[field.key] = null;
      continue;
    }
    const text = draft.trim();
    if (text === '') {
      if (field.optional) {
        if (decidido !== null) patch[field.key] = null;
      } else {
        errors[field.key] = 'Informe um número (ou use o valor da máquina).';
      }
      continue;
    }
    if (!/^\d+$/.test(text)) {
      errors[field.key] = 'Use um número inteiro.';
      continue;
    }
    const n = Number(text);
    if (n < field.min || n > field.max) {
      errors[field.key] = `Entre ${field.min} e ${field.max.toLocaleString('pt-BR')}.`;
      continue;
    }
    if (n !== (server.effective[field.key] ?? null)) patch[field.key] = n;
  }
  return { patch, errors, dirty: Object.keys(patch).length };
}

/** 0..1 de RAM em uso, ou `null` sem a batida. */
export function ramUsed(server: ServerLimits): number | null {
  if (server.ram_total_mb === null || server.ram_free_mb === null || server.ram_total_mb <= 0) return null;
  return 1 - server.ram_free_mb / server.ram_total_mb;
}

export function gb(mb: number | null): string {
  return mb === null ? '—' : `${(mb / 1024).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} GB`;
}
