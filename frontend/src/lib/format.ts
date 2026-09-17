const nf0 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export function formatInt(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? nf0.format(n) : '—';
}

export function formatDecimal(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? nf1.format(n) : '—';
}

export function formatPercent(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? `${nf0.format(n)}%` : '—';
}

export function formatGb(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? `${nf1.format(n)} GB` : '—';
}

export function formatMb(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  return n >= 1024 ? `${nf1.format(n / 1024)} GB` : `${nf0.format(n)} MB`;
}

export function clamp01(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.min(1, Math.max(0, n));
}

export function ratio(done: number, total: number): number {
  if (!Number.isFinite(done) || !Number.isFinite(total) || total <= 0) return 0;
  return clamp01(done / total);
}

/** Plural simples em pt-BR: plural(2, 'instância', 'instâncias') → "2 instâncias". */
export function plural(n: number, one: string, many: string): string {
  return `${formatInt(n)} ${n === 1 ? one : many}`;
}

export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/** Junta classes ignorando valores vazios. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

/** JSON legível e seguro (nunca lança). */
export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Converte `snake_case`/`camelCase` em rótulo legível: "emulator_version" → "Emulator version". */
export function humanizeKey(key: string): string {
  const s = key.replace(/[_-]+/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : key;
}

/** Valor escalar → texto curto em pt-BR. */
export function scalarToText(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'Sim' : 'Não';
  if (typeof v === 'number') return Number.isInteger(v) ? formatInt(v) : String(Math.round(v * 100) / 100).replace('.', ',');
  if (typeof v === 'string') return v === '' ? '—' : v;
  return prettyJson(v);
}

/** Copia texto para a área de transferência, com fallback para contextos sem Clipboard API. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* tenta o fallback abaixo */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
