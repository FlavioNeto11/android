import type { Instance, InstanceAction, InstanceState, Worker } from '../../api/types';

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

/**
 * Título para aparelho de OUTRA máquina. "Emulador desligado" era falso: o emulador segue ligado lá, o que caiu
 * foi o ADB até aqui. Dizer a coisa errada custou tempo de diagnóstico, então o texto passa a depender do tipo.
 */
const NO_FRAME_TITLE_EXTERNO: Partial<Record<Exclude<InstanceState, 'online'>, string>> = {
  absent: 'Aparelho de outra máquina, sem AVD local',
  stopped: 'Sem conexão ADB com a outra máquina',
  stopping: 'Soltando a sessão…',
  error: 'Aparelho remoto inalcançável',
};

/**
 * Em que servidor o aparelho está, e o que aquele servidor diz do PROCESSO dele. `null` = servidor central.
 *
 * Achado #61: o painel conhecia duas realidades (local e "externo"), mas existem três — o aparelho de um worker
 * gerenciado é uma delas, e dele não dava nem para descobrir em que máquina rodava.
 */
export interface ServerHint {
  id: string;
  name: string;
  /** O servidor está inscrito? `false` = aparelho órfão, apontando para um worker que não existe mais. */
  enrolled: boolean;
  connected: boolean;
  /** Estado do PROCESSO na máquina do worker; `null` quando ele não reporta este aparelho. */
  process: string | null;
  detail: string | null;
  /** O que o worker sabe do aparelho lá — é isto que "Detalhes técnicos" deve mostrar, não o palpite local. */
  avd_name: string | null;
  serial: string | null;
  adb_port: number | null;
  /** A sonda TCP do túnel (`Worker.transport_state`): a ÚNICA base para dizer "túnel caiu". `null` = não sondado. */
  transport: 'up' | 'down' | null;
}

/** Estados de processo do worker que significam "não está rodando lá". Espelha `PROCESSO_PARADO` do backend. */
const PROCESSO_PARADO = new Set(['stopped', 'absent', 'exited', 'hibernated']);

export function serverHintOf(inst: Pick<Instance, 'id' | 'worker_id'>,
                             workers: Readonly<Record<string, Worker>> | undefined): ServerHint | null {
  if (!inst.worker_id) return null;
  const w = workers?.[inst.worker_id];
  if (!w) {
    return { id: inst.worker_id, name: inst.worker_id, enrolled: false, connected: false, process: null,
             detail: null, avd_name: null, serial: null, adb_port: null, transport: null };
  }
  if (w.local) return null;                 // o central é "aqui": selo de servidor ali seria ruído em 10 cartões
  const d = w.devices.find((x) => x.instance_id === inst.id);
  return { id: w.id, name: w.name, enrolled: true, connected: w.connected, process: d?.state ?? null,
           detail: d?.detail ?? null, avd_name: d?.avd_name ?? null, serial: d?.serial ?? null,
           adb_port: d?.adb_port ?? null, transport: w.transport_state ?? null };
}

export function noFrameTitle(state: Exclude<InstanceState, 'online'>, kind?: Instance['kind'],
                             server?: ServerHint | null): string {
  // O que o worker reporta ganha do palpite local: "desligado de propósito lá" não é problema de conexão.
  if (server && (state === 'stopped' || state === 'error')) {
    if (!server.enrolled) return `Servidor ${server.name} não está inscrito — aparelho sem ciclo de vida`;
    if (!server.connected) return `Servidor ${server.name} fora do ar — estado desconhecido`;
    if (server.process === 'hibernated') return `Hibernado em ${server.name}`;
    if (server.process && PROCESSO_PARADO.has(server.process)) return `Emulador desligado em ${server.name}`;
    // Daqui para baixo o worker diz que o processo está de pé e o ADB daqui não chega. "túnel ADB caiu" era o
    // else-branch — afirmado sem olhar a sonda do túnel. Medido: android-12 e android-15 com o emulador travado
    // no próprio notebook (adb `offline` lá) apareciam como queda de túnel, e o túnel estava inocente.
    if (server.transport === 'down') return `Túnel para ${server.name} fora — o emulador lá está ligado`;
    if (server.process === 'unknown') return `${server.name} não sabe o estado do emulador deste aparelho`;
    if (server.process) return `Emulador ligado em ${server.name}, mas o Android lá não responde ao ADB`;
  }
  if (kind === 'external') return NO_FRAME_TITLE_EXTERNO[state] ?? NO_FRAME_TITLE[state] ?? NO_FRAME_TITLE.error;
  return NO_FRAME_TITLE[state] ?? NO_FRAME_TITLE.error;
}

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

/**
 * Quais aparelhos da seleção IMPEDEM o verbo. `bulkActionsFor` só diz que o verbo saiu da barra; sem esta lista
 * o usuário não tinha como saber por quê nem quem tirar da seleção (achado #62).
 */
export function bulkBlockersFor(selected: readonly Pick<Instance, 'id' | 'supported_verbs'>[] | undefined,
                                action: InstanceAction): string[] {
  return (selected ?? []).filter((i) => !supports(i, action)).map((i) => i.id);
}

/**
 * Por que o aparelho não aceita o verbo — `null` quando aceita. O rótulo entra de fora porque este módulo é puro
 * (o `ACTION_META` carrega ícones do lucide e não roda em node).
 */
export function unsupportedReason(inst: Pick<Instance, 'kind' | 'supported_verbs'> | undefined,
                                  action: InstanceAction, label: string): string | null {
  if (supports(inst, action)) return null;
  const motivo =
    inst?.kind === 'store' ? 'ele é o aparelho-loja, que só liga, desliga e abre a Play Store'
    : inst?.kind === 'external' ? 'ele é de outra máquina e este verbo não faz parte do que ela expõe'
    : 'o backend não declarou esse verbo para ele';
  return `Este aparelho não aceita “${label}”: ${motivo}.`;
}

export interface QuickVerb {
  action: InstanceAction;
  allowed: readonly InstanceState[];
  why: string;
  /** Só aparece quando `health.features.hibernation` está ligado (sem isso o backend responde 409). */
  needsHibernation?: boolean;
}

/** Os verbos do painel de foco, e em que estados cada um vale. Fica aqui para o foco decidir igual ao cartão. */
export const QUICK_VERBS: readonly QuickVerb[] = [
  { action: 'start', allowed: ['stopped', 'error'], why: 'Disponível com a instância parada (se estiver hibernada, use “Acordar”).' },
  // "Parar" um hibernado descartaria o snapshot no backend: não oferecemos esse atalho aqui.
  { action: 'stop', allowed: ['online', 'booting', 'error'], why: 'Disponível com a instância ligada.' },
  { action: 'hibernate', allowed: ['online'], why: 'Exige a instância online.', needsHibernation: true },
  { action: 'restart', allowed: ['online', 'error'], why: 'Disponível com a instância online ou em erro.' },
  { action: 'install_apk', allowed: ['online'], why: 'Exige a instância online.' },
  { action: 'open_app', allowed: ['online'], why: 'Exige a instância online.' },
];

export interface QuickOffer { action: InstanceAction; disabledReason: string | null }

/**
 * O que o foco oferece, e o motivo de cada verbo indisponível.
 *
 * Achado #62: o foco decidia só pelo `state`, então oferecia "Instalar APK" num aparelho-loja que o backend
 * recusa no pré-voo — o usuário só descobria depois de clicar, e o clique ainda deixava um `rejected` no
 * histórico. A capacidade vem ANTES do estado: não adianta dizer "ligue o aparelho" se ligado ele também recusa.
 */
export function quickActionsFor(inst: Pick<Instance, 'state' | 'kind' | 'supported_verbs'>, hibernation: boolean,
                                labelOf: (a: InstanceAction) => string): QuickOffer[] {
  return QUICK_VERBS
    .filter((q) => !q.needsHibernation || hibernation)
    .map(({ action, allowed, why }) => ({
      action,
      disabledReason: unsupportedReason(inst, action, labelOf(action))
        ?? (allowed.includes(inst.state) ? null : why),
    }));
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
