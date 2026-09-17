/** UUID v4. Usa `crypto.randomUUID` quando existe; senão monta a partir de `getRandomValues`/Math.random. */
export function uuid(): string {
  const c: Crypto | undefined = typeof crypto !== 'undefined' ? crypto : undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === 'function') {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
}

let counter = 0;
/** Id curto e único na sessão, para chaves de lista, toasts e `aria-*`. */
export function localId(prefix = 'id'): string {
  counter += 1;
  return `${prefix}-${counter.toString(36)}`;
}

/** 'android-03' → '03' (para rótulos compactos). */
export function instanceShort(id: string): string {
  const m = /(\d+)$/.exec(id);
  return m?.[1] ?? id;
}
