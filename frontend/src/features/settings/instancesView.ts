import type { Instance, Worker } from '../../api/types';

/**
 * Item 11.9 — regras puras de "Instâncias e contas legível" (pedido do dono em 24/09: "a tela está ruim").
 * Ficam fora do componente para serem testáveis em node, no mesmo padrão de `devices/deviceState.ts`.
 */

export type ObservedMatch = 'match' | 'diverge' | 'none';

function normText(v: string | null | undefined): string {
  return (v ?? '').replace(/\s+/g, ' ').trim().toLowerCase();
}

/**
 * Confere o rótulo configurado contra o que a IA observou no app, com a MESMA normalização usada por quem grava
 * a evidência (`norm_text` em `backend/app/util.py`: espaços colapsados, sem caixa) — a única gravação em
 * produção (`executor.py`) só grava quando o rótulo já batia na hora, então "diverge" aqui só acontece quando o
 * rótulo muda DEPOIS. `'none'` = nada observado ainda, ou nenhum rótulo configurado: sem conta esperada não há do que
 * divergir (validação do deploy 4, 03/10: o android-04, hoje de Instagram e sem rótulo, aparecia "diverge" por uma
 * observação antiga do QA Messenger).
 */
export function observedMatchOf(inst: Pick<Instance, 'account_label' | 'account_evidence'>): ObservedMatch {
  if (!inst.account_evidence) return 'none';
  const label = normText(inst.account_label);
  if (!label) return 'none';
  return normText(inst.account_evidence).includes(label) ? 'match' : 'diverge';
}

export interface ServerGroup {
  /** `''` = central (mesma convenção de `workerValueOf`). */
  key: string;
  name: string;
  /** `false` = aparelho órfão, apontando para um worker que não existe mais (mesmo achado #61 de `deviceState`). */
  enrolled: boolean;
  items: Instance[];
}

/**
 * Agrupa as instâncias por servidor: o central primeiro (é o caso comum), depois os demais em ordem alfabética.
 * `centralId` vem de `workers.find(w => w.local)`, igual ao resto de `InstancesSection` — o central tem linha em
 * `workers` desde que virou um worker como outro qualquer, então tanto `NULL` quanto o id dele significam "aqui".
 */
export function groupByServer(
  instances: readonly Instance[],
  workers: Readonly<Record<string, Worker>> | undefined,
  centralId: string | null,
): ServerGroup[] {
  const buckets = new Map<string, ServerGroup>();
  for (const inst of instances) {
    const w = inst.worker_id && inst.worker_id !== centralId ? inst.worker_id : '';
    let bucket = buckets.get(w);
    if (!bucket) {
      const worker = w ? workers?.[w] : null;
      const name = w ? worker?.name ?? w : 'Este servidor';
      bucket = { key: w, name, enrolled: w === '' || !!worker, items: [] };
      buckets.set(w, bucket);
    }
    bucket.items.push(inst);
  }
  const central = buckets.get('');
  const rest = [...buckets.values()].filter((b) => b.key !== '').sort((a, b) => a.name.localeCompare(b.name));
  return central ? [central, ...rest] : rest;
}

export interface InstanceFilters {
  /** `null` = todos; `''` = central; id do worker = só aquele servidor. */
  server: string | null;
  /** `null` = todos; `''` = sem app; id do app = só aquele app. */
  app: string | null;
  onlyDivergent: boolean;
  noProfile: boolean;
}

export const EMPTY_FILTERS: InstanceFilters = { server: null, app: null, onlyDivergent: false, noProfile: false };

export function hasActiveFilter(f: InstanceFilters): boolean {
  return f.server !== null || f.app !== null || f.onlyDivergent || f.noProfile;
}

/** Se a instância passa nos filtros em vigor. `hasProfile` decide "sem perfil" — vem de fora porque este módulo
 *  não conhece `InstagramProfile` (mantém o mesmo corte de responsabilidade do resto do arquivo). */
export function matchesFilters(
  inst: Instance,
  filters: InstanceFilters,
  centralId: string | null,
  hasProfile: (instanceId: string) => boolean,
): boolean {
  if (filters.server !== null) {
    const w = inst.worker_id && inst.worker_id !== centralId ? inst.worker_id : '';
    if (w !== filters.server) return false;
  }
  if (filters.app !== null && (inst.app_id ?? '') !== filters.app) return false;
  if (filters.onlyDivergent && observedMatchOf(inst) !== 'diverge') return false;
  if (filters.noProfile && hasProfile(inst.id)) return false;
  return true;
}
