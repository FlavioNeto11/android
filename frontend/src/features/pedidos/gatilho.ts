import type { GatilhoSpec, TipoDeGatilho } from '../../api/pedidos';

/**
 * O "Quando" da criação (produto.md §3): agora, em um horário, repetir, acompanhar. "Quando acontecer" (evento,
 * condição) depende do item 28.8 e o backend responde `gatilho_nao_suportado`, então a tela nem o oferece.
 * O painel monta a `rrule` a partir de poucas escolhas (o subconjunto da RFC 5545 que o backend aceita: FREQ, INTERVAL,
 * BYDAY, BYMONTHDAY, BYHOUR, BYMINUTE, COUNT, UNTIL); quem valida é sempre o backend.
 */
export type ModoQuando = 'agora' | 'horario' | 'repetir' | 'acompanhar';
export type Frequencia = 'HOURLY' | 'DAILY' | 'WEEKLY' | 'MONTHLY';

export const DIAS_DA_SEMANA: readonly { id: string; rotulo: string }[] = [
  { id: 'MO', rotulo: 'seg' }, { id: 'TU', rotulo: 'ter' }, { id: 'WE', rotulo: 'qua' }, { id: 'TH', rotulo: 'qui' },
  { id: 'FR', rotulo: 'sex' }, { id: 'SA', rotulo: 'sáb' }, { id: 'SU', rotulo: 'dom' },
];

export interface EstadoDoQuando {
  modo: ModoQuando;
  /** `YYYY-MM-DDTHH:MM`, hora LOCAL no fuso do pedido (o que o `<input type="datetime-local">` entrega). */
  inicio: string;
  frequencia: Frequencia;
  intervalo: number;
  dias: string[];
}

const dois = (n: number) => String(n).padStart(2, '0');

/** A próxima hora cheia no relógio do navegador, no formato do campo. */
export function proximaHoraCheia(agora: Date = new Date()): string {
  const d = new Date(agora.getTime() + 3_600_000);
  return `${d.getFullYear()}-${dois(d.getMonth() + 1)}-${dois(d.getDate())}T${dois(d.getHours())}:00`;
}

export function quandoInicial(agora?: Date): EstadoDoQuando {
  return { modo: 'agora', inicio: proximaHoraCheia(agora), frequencia: 'DAILY', intervalo: 1, dias: [] };
}

/** `2026-10-03T08:00` → `2026-10-03T08:00:00` (hora local ingênua do contrato). */
export const dtstartDe = (inicio: string): string => (inicio.length === 16 ? `${inicio}:00` : inicio);

export function rruleDe(e: EstadoDoQuando): string {
  const partes = [`FREQ=${e.frequencia}`];
  if (e.intervalo > 1) partes.push(`INTERVAL=${e.intervalo}`);
  if (e.frequencia === 'WEEKLY' && e.dias.length > 0) partes.push(`BYDAY=${DIAS_DA_SEMANA.filter((d) => e.dias.includes(d.id)).map((d) => d.id).join(',')}`);
  if (e.frequencia === 'MONTHLY') partes.push(`BYMONTHDAY=${Number(e.inicio.slice(8, 10)) || 1}`);
  return partes.join(';');
}

/** O gatilho do corpo do pedido; `null` quando falta o que o modo pede. */
export function gatilhoDoQuando(e: EstadoDoQuando): { tipo: TipoDeGatilho; spec: GatilhoSpec } | null {
  if (e.modo === 'agora') return { tipo: 'agora', spec: {} };
  if (e.inicio.length < 16) return null;
  if (e.modo === 'horario') return { tipo: 'horario', spec: { dtstart: dtstartDe(e.inicio) } };
  if (!Number.isInteger(e.intervalo) || e.intervalo < 1) return null;
  return { tipo: 'recorrencia', spec: { dtstart: dtstartDe(e.inicio), rrule: rruleDe(e) } };
}

/** O que "Repetir" e "Acompanhar" dizem em português, para a prévia e para o rótulo do botão. */
export function falaDoQuando(e: EstadoDoQuando): string {
  if (e.modo === 'agora') return 'Uma vez, agora';
  if (e.modo === 'horario') return `Uma vez, em ${e.inicio.replace('T', ' às ')}`;
  const cada = e.intervalo > 1 ? `a cada ${e.intervalo}` : 'a cada';
  const unidade = { HOURLY: ['hora', 'horas'], DAILY: ['dia', 'dias'], WEEKLY: ['semana', 'semanas'], MONTHLY: ['mês', 'meses'] }[e.frequencia];
  return `${cada} ${e.intervalo > 1 ? unidade[1] : unidade[0]}, desde ${e.inicio.replace('T', ' às ')}`;
}
