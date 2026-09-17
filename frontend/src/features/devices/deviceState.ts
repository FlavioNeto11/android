import type { Instance, InstanceAction, InstanceState } from '../../api/types';

/**
 * Regras puras "estado da instância → o que o painel oferece". Ficam fora dos componentes para serem
 * testáveis em node e para que cartão, foco e barra em lote decidam do mesmo jeito.
 */

export type PrimaryAction = Extract<InstanceAction, 'create' | 'start' | 'wake' | 'restart'>;

/** Ação principal do cartão. `null` = não há o que oferecer (online ou em transição). */
export function primaryActionFor(state: InstanceState): PrimaryAction | null {
  switch (state) {
    case 'absent': return 'create';
    case 'stopped': return 'start';
    case 'hibernated': return 'wake';
    case 'error': return 'restart';
    default: return null;
  }
}

/** Título do espaço reservado da miniatura quando não há tela ao vivo (tudo que não é `online`). */
export const NO_FRAME_TITLE: Record<Exclude<InstanceState, 'online'>, string> = {
  absent: 'AVD ainda não criado',
  stopped: 'Emulador desligado',
  hibernated: 'Hibernado — acorda em segundos, sem ocupar RAM',
  booting: 'Iniciando o emulador…',
  stopping: 'Desligando…',
  error: 'Falha na instância',
};

/** `Hibernar` só faz sentido com o aparelho ligado E com a hibernação habilitada no backend (senão é 409). */
export function canHibernate(state: InstanceState, hibernationEnabled: boolean): boolean {
  return hibernationEnabled && state === 'online';
}

const BULK_ACTIONS: readonly InstanceAction[] = ['start', 'stop', 'restart', 'install_apk', 'open_app'];

export interface BulkContext {
  /** A seleção tem instância sem AVD → oferece "Criar AVD". */
  hasAbsent: boolean;
  /** A seleção tem instância hibernada → oferece "Acordar". */
  hasHibernated: boolean;
  /** `health.features.hibernation`: sem isso o backend responde 409 a `hibernate`. */
  hibernation: boolean;
}

/** Ações da barra em lote: `create`/`wake` só quando a seleção precisa; `hibernate` só com o recurso ligado. */
export function bulkActionsFor({ hasAbsent, hasHibernated, hibernation }: BulkContext): InstanceAction[] {
  const actions: InstanceAction[] = [];
  if (hasAbsent) actions.push('create');
  if (hasHibernated) actions.push('wake');
  for (const a of BULK_ACTIONS) {
    actions.push(a);
    if (a === 'stop' && hibernation) actions.push('hibernate');
  }
  return actions;
}

/** Ordem fixa do resumo por estado na grade (do que ocupa RAM para o que não ocupa). */
export const STATE_SUMMARY_ORDER: readonly InstanceState[] = ['online', 'booting', 'stopping', 'hibernated', 'stopped', 'absent', 'error'];

/** Rótulo curto [singular, plural] de cada estado no resumo da grade. */
export const STATE_SUMMARY_LABEL: Record<InstanceState, readonly [string, string]> = {
  online: ['online', 'online'],
  booting: ['iniciando', 'iniciando'],
  stopping: ['parando', 'parando'],
  hibernated: ['hibernado', 'hibernados'],
  stopped: ['parada', 'paradas'],
  absent: ['sem AVD', 'sem AVD'],
  error: ['com erro', 'com erro'],
};

/** Quantas instâncias há em cada estado — só os estados presentes, na ordem de `STATE_SUMMARY_ORDER`. */
export function countByState(instances: readonly Pick<Instance, 'state'>[]): { state: InstanceState; count: number }[] {
  const counts = new Map<InstanceState, number>();
  for (const i of instances) counts.set(i.state, (counts.get(i.state) ?? 0) + 1);
  return STATE_SUMMARY_ORDER.filter((s) => counts.has(s)).map((s) => ({ state: s, count: counts.get(s) ?? 0 }));
}
