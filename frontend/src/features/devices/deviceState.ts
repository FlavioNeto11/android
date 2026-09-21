import type { Instance, InstanceAction, InstanceState } from '../../api/types';

/**
 * Regras puras "estado da instância → o que o painel oferece". Ficam fora dos componentes para serem
 * testáveis em node e para que cartão, foco e barra em lote decidam do mesmo jeito.
 */

export type PrimaryAction = Extract<InstanceAction, 'create' | 'start' | 'wake' | 'restart'>;

/**
 * Lista de verbos suportados pelo aparelho. Quando o backend não a manda (instância antiga num snapshot velho),
 * vale "suporta tudo" — não travar o painel por falta de campo é mais importante que a recusa preventiva, porque
 * o pré-voo do backend recusa de todo modo, e com a explicação.
 */
export function supports(inst: Pick<Instance, 'supported_verbs'> | undefined, action: InstanceAction): boolean {
  const lista = inst?.supported_verbs;
  return !lista || lista.length === 0 || lista.includes(action);
}

/**
 * Ação principal do cartão. `null` = não há o que oferecer (online, em transição, ou verbo não suportado).
 *
 * O aparelho entra na conta porque oferecer "Criar AVD" para um aparelho de outra máquina criava um AVD fantasma,
 * e "Acordar" para quem nunca hiberna era só ruído.
 */
export function primaryActionFor(state: InstanceState, inst?: Pick<Instance, 'supported_verbs'>): PrimaryAction | null {
  const candidato: PrimaryAction | null = (() => {
    switch (state) {
      case 'absent': return 'create';
      case 'stopped': return 'start';
      case 'hibernated': return 'wake';
      case 'error': return 'restart';
      default: return null;
    }
  })();
  if (candidato === null) return null;
  if (supports(inst, candidato)) return candidato;
  // Sem o verbo principal, "Iniciar" ainda serve a aparelho externo: ali significa reconectar o ADB.
  return supports(inst, 'start') && candidato !== 'start' ? 'start' : null;
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

/** `Hibernar` só faz sentido com o aparelho ligado, a hibernação habilitada E o aparelho aceitando o verbo. */
export function canHibernate(state: InstanceState, hibernationEnabled: boolean,
                             inst?: Pick<Instance, 'supported_verbs'>): boolean {
  return hibernationEnabled && state === 'online' && supports(inst, 'hibernate');
}

const BULK_ACTIONS: readonly InstanceAction[] = ['start', 'stop', 'restart', 'install_apk', 'open_app'];

export interface BulkContext {
  /** A seleção tem instância sem AVD → oferece "Criar AVD". */
  hasAbsent: boolean;
  /** A seleção tem instância hibernada → oferece "Acordar". */
  hasHibernated: boolean;
  /** `health.features.hibernation`: sem isso o backend responde 409 a `hibernate`. */
  hibernation: boolean;
  /**
   * Os aparelhos selecionados. Um verbo só é oferecido quando TODOS o aceitam: numa seleção mista, `create` ia
   * para o aparelho de outra máquina e criava um AVD fantasma lá. Vazio = sem filtro por capacidade.
   */
  selected?: readonly Pick<Instance, 'supported_verbs'>[];
}

/**
 * Ações da barra em lote: `create`/`wake` só quando a seleção precisa; `hibernate` só com o recurso ligado; e
 * nada que algum aparelho da seleção não aceite.
 */
export function bulkActionsFor({ hasAbsent, hasHibernated, hibernation, selected }: BulkContext): InstanceAction[] {
  const todos = (a: InstanceAction): boolean => !selected?.length || selected.every((i) => supports(i, a));
  const actions: InstanceAction[] = [];
  if (hasAbsent && todos('create')) actions.push('create');
  if (hasHibernated && todos('wake')) actions.push('wake');
  for (const a of BULK_ACTIONS) {
    if (todos(a)) actions.push(a);
    if (a === 'stop' && hibernation && todos('hibernate')) actions.push('hibernate');
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
