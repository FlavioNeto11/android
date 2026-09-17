/** Acesso ao localStorage sempre protegido: modo privado, cota cheia ou JSON corrompido nunca quebram a UI. */
const PREFIX = 'cda.';

export function loadJson<T>(key: string, validate: (v: unknown) => v is T): T | null {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return validate(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveJson(key: string, value: unknown): void {
  try {
    if (value === null || value === undefined) window.localStorage.removeItem(PREFIX + key);
    else window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* sem persistência — segue funcionando em memória */
  }
}

export const isString = (v: unknown): v is string => typeof v === 'string';
export const isStringArray = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === 'string');
