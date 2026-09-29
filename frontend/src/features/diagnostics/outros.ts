import { isRecord } from '../../lib/format';

/**
 * "Outros dados" do diagnóstico: as chaves de `GET /api/diagnostics` fora das seções próprias (máquina, aceleração,
 * ferramentas, capacidade, medições). Hoje são `measured_on`, `sdk`, `scale_test`, `image_probes` e `host_script`.
 * Estas funções só leem o que reconhecem; qualquer formato inesperado devolve `null` e a tela cai para a árvore
 * genérica — nada some.
 */

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v : null;
}

/** Ordem natural de ids de aparelho: android-2 antes de android-10. */
export function compararIds(a: string, b: string): number {
  return a.localeCompare(b, 'pt-BR', { numeric: true });
}

// ---------------------------------------------------------------- teste de escala

export interface RodadaDeEscala {
  /** Aparelhos pedidos nesta leva (`target`). */
  alvo: number | null;
  noAr: number | null;
  quando: string | null;
  /** Tempo da leva inteira, em segundos (`boot_wall_seconds`). */
  tempoDaLeva: number | null;
  cpu: number | null;
  memLivreGb: number | null;
  memUsadaPct: number | null;
  foraDoAr: string[];
  ia: string | null;
  iaSimulada: boolean | null;
  /** Boot de cada aparelho (s), por id; `null` = estava na leva sem tempo medido. */
  boot: Record<string, number | null>;
  /** RAM do emulador (MB), por id. */
  ramMb: Record<string, number | null>;
}

function porAparelho(v: unknown, campo: string): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  if (!Array.isArray(v)) return out;
  for (const item of v) {
    if (isRecord(item) && typeof item.id === 'string') out[item.id] = num(item[campo]);
  }
  return out;
}

function idsForaDoAr(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => (typeof x === 'string' ? x : isRecord(x) && typeof x.id === 'string' ? x.id : JSON.stringify(x)));
}

export function lerTesteDeEscala(v: unknown): RodadaDeEscala[] | null {
  if (!Array.isArray(v) || v.length === 0 || !v.every(isRecord)) return null;
  return v.map((r) => ({
    alvo: num(r.target),
    noAr: num(r.online),
    quando: str(r.ts),
    tempoDaLeva: num(r.boot_wall_seconds),
    cpu: num(r.host_cpu_percent),
    memLivreGb: num(r.mem_available_gb),
    memUsadaPct: num(r.mem_used_percent),
    foraDoAr: idsForaDoAr(r.not_online),
    ia: str(r.ai_provider),
    iaSimulada: typeof r.ai_simulated === 'boolean' ? r.ai_simulated : null,
    boot: porAparelho(r.boot_seconds_each, 'boot_seconds'),
    ramMb: porAparelho(r.emulator_rss_mb, 'rss_mb'),
  }));
}

export interface LinhaPorAparelho {
  id: string;
  /** O último boot medido do aparelho (o tempo se repete nas levas seguintes). */
  boot: number | null;
  /** RAM por rodada, na ordem das rodadas; `undefined` = o aparelho não aparece naquela rodada. */
  ramMb: (number | null | undefined)[];
}

/** Aparelho × rodada: a lista de aparelhos, que a árvore repetia inteira em cada leva, vira uma tabela só. */
export function matrizPorAparelho(rodadas: RodadaDeEscala[]): LinhaPorAparelho[] {
  const ids = new Set<string>();
  for (const r of rodadas) {
    for (const id of Object.keys(r.boot)) ids.add(id);
    for (const id of Object.keys(r.ramMb)) ids.add(id);
  }
  return Array.from(ids).sort(compararIds).map((id) => {
    let boot: number | null = null;
    for (const r of rodadas) {
      const b = r.boot[id];
      if (typeof b === 'number') boot = b;
    }
    return { id, boot, ramMb: rodadas.map((r) => (id in r.ramMb ? r.ramMb[id] : undefined)) };
  });
}

// ---------------------------------------------------------------- imagens medidas

export interface SondaDeImagem {
  imagem: string | null;
  android: string | null;
  ramPedidaMb: number | null;
  /** RAM que o Android enxerga (`MemTotal` do convidado), em GB; `null` se o texto não casou. */
  ramDoAndroidGb: number | null;
  ramDoAndroidBruta: string | null;
  emUsoGb: number | null;
  privadaGb: number | null;
  primeiroBootS: number | null;
}

/** `"MemTotal:        2534552 kB"` → 2,42 GB. */
export function memTotalGb(texto: string | null): number | null {
  if (!texto) return null;
  const m = /MemTotal:\s*(\d+)\s*kB/i.exec(texto);
  return m?.[1] ? Number(m[1]) / 1024 / 1024 : null;
}

export function lerSondasDeImagem(v: unknown): SondaDeImagem[] | null {
  if (!Array.isArray(v) || v.length === 0 || !v.every(isRecord)) return null;
  return v.map((r) => {
    const bruta = str(r.guest_memtotal);
    return {
      imagem: str(r.image),
      android: str(r.android_release) ?? (typeof r.android_release === 'number' ? String(r.android_release) : null),
      ramPedidaMb: num(r.requested_ram_mb),
      ramDoAndroidGb: memTotalGb(bruta),
      ramDoAndroidBruta: bruta,
      emUsoGb: num(r.qemu_ws_gb),
      privadaGb: num(r.qemu_private_gb),
      primeiroBootS: num(r.first_boot_s),
    };
  });
}

/** `system-images;android-34;google_apis;x86_64` → `android-34 · google_apis · x86_64` (o id inteiro fica no título). */
export function nomeCurtoDaImagem(id: string): string {
  const partes = id.split(';');
  return partes[0] === 'system-images' && partes.length > 1 ? partes.slice(1).join(' · ') : id;
}
