import {
  Ban, Bot, Check, CircleAlert, CircleCheck, CircleDashed, CircleDot, CircleHelp, CirclePause, CircleSlash,
  CircleX, Clock, Eye, Hand, Hourglass, Info, ListChecks, LoaderCircle, Minus, Moon, OctagonAlert, Power, PowerOff,
  Route, ScanSearch, ScrollText, Send, SkipForward, TriangleAlert, Unplug, Wifi, WifiOff,
  type LucideIcon,
} from 'lucide-react';
import type {
  ActionStatus, AttemptStatus, AutomationState, ControlOwner, DeliveryLevel, EventRecord, Health,
  InstanceState, Objective, ObjectiveStatus, RunStatus, Step, StepStatus,
} from '../api/types';

/**
 * Mapa central: cada valor de enum do contrato → rótulo (pt-BR) + tom (cor) + ícone (forma).
 * Nenhum status na interface é comunicado só por cor: o StatusBadge sempre mostra ícone + texto.
 */
export type Tone = 'neutral' | 'muted' | 'info' | 'accent' | 'success' | 'warning' | 'danger';

export interface StatusMeta {
  label: string;
  tone: Tone;
  icon: LucideIcon;
  /** Ícone deve girar (respeitando prefers-reduced-motion). */
  spin?: boolean;
  /** Texto auxiliar para tooltip/explicação. */
  description?: string;
}

export type ConnStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export const INSTANCE_STATE: Record<InstanceState, StatusMeta> = {
  absent: { label: 'AVD ausente', tone: 'muted', icon: CircleDashed, description: 'O AVD ainda não foi criado nesta máquina.' },
  stopped: { label: 'Parada', tone: 'neutral', icon: PowerOff, description: 'O emulador está desligado.' },
  hibernated: { label: 'Hibernado', tone: 'info', icon: Moon, description: 'Desligado com snapshot salvo: acorda em segundos e não ocupa RAM.' },
  booting: { label: 'Iniciando', tone: 'info', icon: LoaderCircle, spin: true, description: 'O emulador está inicializando.' },
  online: { label: 'Online', tone: 'success', icon: Power, description: 'Pronta para receber comandos.' },
  stopping: { label: 'Parando', tone: 'info', icon: LoaderCircle, spin: true, description: 'O emulador está sendo desligado.' },
  error: { label: 'Erro', tone: 'danger', icon: OctagonAlert, description: 'A instância falhou. Veja o detalhe e tente novamente.' },
};

export const CONTROL_OWNER: Record<ControlOwner, StatusMeta> = {
  none: { label: 'Livre', tone: 'muted', icon: Minus, description: 'Ninguém está controlando esta instância.' },
  ai: { label: 'IA', tone: 'accent', icon: Bot, description: 'O agente de IA está operando esta instância.' },
  user: { label: 'Usuário', tone: 'warning', icon: Hand, description: 'Um usuário está com o controle manual.' },
};

export const AUTOMATION_STATE: Record<AutomationState, StatusMeta> = {
  none: { label: 'Automação inativa', tone: 'muted', icon: Minus },
  starting: { label: 'Automação iniciando', tone: 'info', icon: LoaderCircle, spin: true },
  ready: { label: 'Automação pronta', tone: 'success', icon: Check },
  error: { label: 'Automação com erro', tone: 'danger', icon: CircleAlert },
};

export const RUN_STATUS: Record<RunStatus, StatusMeta> = {
  planning: { label: 'Planejando', tone: 'info', icon: LoaderCircle, spin: true },
  needs_input: { label: 'Precisa de informações', tone: 'warning', icon: CircleHelp },
  planned: { label: 'Planejada', tone: 'info', icon: ListChecks },
  running: { label: 'Em execução', tone: 'accent', icon: LoaderCircle, spin: true },
  paused: { label: 'Pausada', tone: 'warning', icon: CirclePause },
  cancelling: { label: 'Cancelando', tone: 'warning', icon: LoaderCircle, spin: true },
  completed: { label: 'Concluída', tone: 'success', icon: CircleCheck },
  completed_with_issues: { label: 'Concluída com pendências', tone: 'warning', icon: TriangleAlert },
  cancelled: { label: 'Cancelada', tone: 'muted', icon: Ban },
  failed: { label: 'Falhou', tone: 'danger', icon: CircleX },
};

export const OBJECTIVE_STATUS: Record<ObjectiveStatus, StatusMeta> = {
  pending: { label: 'Pendente', tone: 'muted', icon: Clock },
  running: { label: 'Em andamento', tone: 'accent', icon: LoaderCircle, spin: true },
  waiting_user: { label: 'Bloqueado — aguardando usuário', tone: 'warning', icon: Hand },
  succeeded: { label: 'Sucesso', tone: 'success', icon: CircleCheck },
  failed: { label: 'Falhou', tone: 'danger', icon: CircleX },
  cancelled: { label: 'Cancelado', tone: 'muted', icon: Ban },
  uncertain: { label: 'Incerto — requer revisão', tone: 'warning', icon: CircleHelp },
};

export const STEP_STATUS: Record<StepStatus, StatusMeta> = {
  pending: { label: 'Pendente', tone: 'muted', icon: Clock },
  ready: { label: 'Pronta', tone: 'info', icon: CircleDot },
  running: { label: 'Executando', tone: 'accent', icon: LoaderCircle, spin: true },
  verifying: { label: 'Verificando', tone: 'info', icon: ScanSearch },
  succeeded: { label: 'Concluída', tone: 'success', icon: CircleCheck },
  retry_wait: { label: 'Aguardando nova tentativa', tone: 'warning', icon: Hourglass },
  waiting_user: { label: 'Aguardando usuário', tone: 'warning', icon: Hand },
  failed: { label: 'Falhou', tone: 'danger', icon: CircleX },
  cancelled: { label: 'Cancelada', tone: 'muted', icon: Ban },
  uncertain: { label: 'Incerto — requer revisão', tone: 'warning', icon: CircleHelp },
  skipped: { label: 'Ignorada (plano revisado)', tone: 'muted', icon: SkipForward },
};

export const ATTEMPT_STATUS: Record<AttemptStatus, StatusMeta> = {
  running: { label: 'Em andamento', tone: 'accent', icon: LoaderCircle, spin: true },
  succeeded: { label: 'Bem-sucedida', tone: 'success', icon: CircleCheck },
  failed: { label: 'Falhou', tone: 'danger', icon: CircleX },
  interrupted: { label: 'Interrompida', tone: 'warning', icon: CircleSlash },
  uncertain: { label: 'Incerta', tone: 'warning', icon: CircleHelp },
  cancelled: { label: 'Cancelada', tone: 'muted', icon: Ban },
};

export const ACTION_STATUS: Record<ActionStatus, StatusMeta> = {
  intended: { label: 'Registrada (ainda não confirmada)', tone: 'info', icon: CircleDot },
  done: { label: 'Feita', tone: 'success', icon: Check },
  failed: { label: 'Falhou', tone: 'danger', icon: CircleX },
  unknown: { label: 'Resultado desconhecido', tone: 'warning', icon: CircleHelp },
  rejected: { label: 'Rejeitada', tone: 'muted', icon: Ban },
};

/** Quem decidiu as ações da etapa (v0.2). `recipe` = reproduzida por seletores, sem chamada de modelo. */
export const DRIVEN_BY: Record<NonNullable<Step['driven_by']>, StatusMeta> = {
  recipe: { label: 'Receita', tone: 'success', icon: ScrollText, description: 'Etapa reproduzida por receita (seletores aprendidos): nenhuma chamada de modelo.' },
  'recipe+ai': { label: 'Receita + IA', tone: 'info', icon: Route, description: 'A receita começou a etapa, a tela divergiu e a IA assumiu o restante.' },
  ai: { label: 'IA', tone: 'accent', icon: Bot, description: 'Ações decididas pela IA nesta etapa.' },
};

/** Selo de `driven_by`; `null` enquanto o backend não disser quem conduziu (etapa ainda não executada). */
export function drivenByMeta(value: string | null | undefined): StatusMeta | null {
  return value ? metaOf(DRIVEN_BY, value) : null;
}

export const DELIVERY_LEVEL: Record<DeliveryLevel, StatusMeta> = {
  none: { label: 'Sem confirmação de envio', tone: 'muted', icon: Minus },
  appeared: { label: 'Apareceu na conversa', tone: 'info', icon: Eye },
  sent: { label: 'Enviada', tone: 'success', icon: Send },
  delivered: { label: 'Entregue', tone: 'success', icon: Check },
  read: { label: 'Lida', tone: 'success', icon: CircleCheck },
};

export const HEALTH_STATUS: Record<Health['status'], StatusMeta> = {
  ok: { label: 'Ambiente OK', tone: 'success', icon: CircleCheck },
  degraded: { label: 'Ambiente degradado', tone: 'warning', icon: TriangleAlert },
  error: { label: 'Ambiente com erro', tone: 'danger', icon: OctagonAlert },
};

export const CONN_STATUS: Record<ConnStatus, StatusMeta> = {
  connecting: { label: 'Conectando…', tone: 'info', icon: LoaderCircle, spin: true },
  connected: { label: 'Conectado', tone: 'success', icon: Wifi },
  reconnecting: { label: 'Reconectando…', tone: 'warning', icon: Unplug },
  disconnected: { label: 'Desconectado', tone: 'danger', icon: WifiOff },
};

export const EVENT_LEVEL: Record<EventRecord['level'], StatusMeta> = {
  info: { label: 'Info', tone: 'info', icon: Info },
  warn: { label: 'Aviso', tone: 'warning', icon: TriangleAlert },
  error: { label: 'Erro', tone: 'danger', icon: CircleX },
};

export const UNKNOWN_STATUS: StatusMeta = { label: 'Desconhecido', tone: 'muted', icon: CircleHelp };

/** Busca tolerante: se o backend mandar um valor fora do contrato, mostramos o texto cru em tom neutro. */
export function metaOf<K extends string>(map: Record<K, StatusMeta>, value: string | null | undefined): StatusMeta {
  if (value && Object.prototype.hasOwnProperty.call(map, value)) return map[value as K];
  return value ? { ...UNKNOWN_STATUS, label: value } : UNKNOWN_STATUS;
}

export const POSTCONDITION_KIND: Record<string, string> = {
  text_visible: 'Texto visível na tela',
  app_foreground: 'App em primeiro plano',
  element_present: 'Elemento presente',
  model_judged: 'Avaliado pela IA',
  items_collected: 'Itens lidos pelo executor',
};

export const EVIDENCE_KIND: Record<string, string> = {
  screenshot: 'Captura de tela',
  hierarchy: 'Hierarquia de elementos',
  text: 'Texto',
  verifier: 'Verificador',
};

const ACTIVE_RUN: ReadonlySet<RunStatus> = new Set<RunStatus>(['planning', 'running', 'paused', 'cancelling']);
const TERMINAL_RUN: ReadonlySet<RunStatus> = new Set<RunStatus>(['completed', 'completed_with_issues', 'cancelled', 'failed']);

/** Execuções que contam como "ativas" no contador da barra superior. */
export function isRunActive(status: RunStatus): boolean {
  return ACTIVE_RUN.has(status);
}

export function isRunTerminal(status: RunStatus): boolean {
  return TERMINAL_RUN.has(status);
}

/**
 * Rodízio (v0.2): com `auto_start_devices`, o objetivo de um aparelho desligado fica `pending` com
 * `status_detail` "aguardando vaga (k/K ligados)". Devolve esse texto, ou `null` se não for o caso.
 */
export function slotWaitDetail(o: Pick<Objective, 'status' | 'status_detail'> | null | undefined): string | null {
  if (!o || o.status !== 'pending' || typeof o.status_detail !== 'string') return null;
  const detail = o.status_detail.trim();
  return /^aguardando vaga/i.test(detail) ? detail : null;
}
