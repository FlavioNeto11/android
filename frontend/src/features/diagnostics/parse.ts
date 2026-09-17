import { isRecord } from '../../lib/format';

/**
 * `GET /api/diagnostics` devolve um "objeto livre". Estas funções extraem o que dá para mostrar bem,
 * sem presumir formato: qualquer coisa inesperada continua visível na árvore genérica.
 */

export interface ToolRow {
  name: string;
  /** true = OK · false = ausente/com problema · null = não foi possível concluir */
  ok: boolean | null;
  version: string | null;
  detail: string | null;
  raw: unknown;
}

const OK_KEYS = ['ok', 'found', 'installed', 'available', 'present', 'exists', 'running'];
const VERSION_KEYS = ['version', 'ver'];
const DETAIL_KEYS = ['path', 'detail', 'error', 'message', 'hint', 'location'];

function firstString(obj: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'string' && v.trim()) return v;
    if (typeof v === 'number') return String(v);
  }
  return null;
}

export function toolFromValue(name: string, value: unknown): ToolRow {
  if (typeof value === 'boolean') return { name, ok: value, version: null, detail: null, raw: value };
  if (typeof value === 'string') return { name, ok: value.trim() !== '', version: value.trim() || null, detail: null, raw: value };
  if (typeof value === 'number') return { name, ok: true, version: String(value), detail: null, raw: value };
  if (value === null || value === undefined) return { name, ok: false, version: null, detail: null, raw: value };
  if (isRecord(value)) {
    let ok: boolean | null = null;
    for (const k of OK_KEYS) {
      if (typeof value[k] === 'boolean') {
        ok = value[k] as boolean;
        break;
      }
    }
    const version = firstString(value, VERSION_KEYS);
    if (ok === null) {
      if (typeof value.missing === 'boolean') ok = !value.missing;
      else if (version) ok = true;
      else if (typeof value.error === 'string' && value.error) ok = false;
    }
    const details = DETAIL_KEYS.map((k) => (typeof value[k] === 'string' && value[k] ? (value[k] as string) : null)).filter((v): v is string => !!v);
    return { name, ok, version, detail: details.length > 0 ? details.join(' · ') : null, raw: value };
  }
  return { name, ok: null, version: null, detail: null, raw: value };
}

/** Aceita `{adb: {...}, emulator: "35.1"}` ou `[{name: 'adb', ...}]`. */
export function parseTools(tools: unknown): ToolRow[] | null {
  if (isRecord(tools)) return Object.entries(tools).map(([name, v]) => toolFromValue(name, v));
  if (Array.isArray(tools)) {
    return tools.map((item, i) => {
      const name = isRecord(item) && typeof item.name === 'string' ? item.name : isRecord(item) && typeof item.tool === 'string' ? item.tool : `#${i + 1}`;
      return toolFromValue(name, item);
    });
  }
  return null;
}

/** Procura um booleano de "aceleração disponível" sem presumir o nome exato da chave. */
export function accelerationOk(accel: unknown): boolean | null {
  if (typeof accel === 'boolean') return accel;
  if (!isRecord(accel)) return null;
  for (const k of ['ok', 'available', 'enabled', 'usable', 'working', 'accelerated']) {
    if (typeof accel[k] === 'boolean') return accel[k] as boolean;
  }
  return null;
}
