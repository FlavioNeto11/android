/**
 * 31.199: cancelar só alguns alvos de uma operação, por filtro, sem fechá-la (rodada de 30 alvos: o aparelho travado, ou os que esperam o
 * liberar e não vão ser liberados). Contrato: adendo v1.112, `POST /api/operacoes/{id}/cancelar-alvos {profile_ids?, estados?, estagios?,
 * instance_ids?}`: os filtros se SOMAM (E); sem nenhum, 422 `filtro_vazio` (a operação inteira é `/cancelar`); o central cancela só a
 * execução ainda aberta de cada alvo que casa (a que espera o liberar incluída), pula a que já terminou e deixa a operação seguir.
 * Resposta: `{cancelados: [profile_id], ignorados: [{profile_id, motivo}], operacao}`.
 */
import type { Alvo, EstadoDoAlvo, EstagioId } from './modelo';

export interface FiltroDeCancelamento {
  profile_ids: string[];
  estados: EstadoDoAlvo[];
  estagios: EstagioId[];
  instance_ids: string[];
}

export const FILTRO_VAZIO: FiltroDeCancelamento = { profile_ids: [], estados: [], estagios: [], instance_ids: [] };

export const filtroVazio = (f: FiltroDeCancelamento): boolean =>
  f.profile_ids.length === 0 && f.estados.length === 0 && f.estagios.length === 0 && f.instance_ids.length === 0;

/** O corpo do POST: só os filtros usados (o central aceita lista vazia, mas o corpo fica enxuto). */
export const corpoDoCancelamento = (f: FiltroDeCancelamento): Partial<FiltroDeCancelamento> =>
  Object.fromEntries(Object.entries(f).filter(([, v]) => (v as unknown[]).length > 0));

/**
 * Os alvos que o filtro atinge, com a MESMA regra do central: todos os filtros dados valem ao mesmo tempo, e o que o filtro não cita não
 * restringe. Sem filtro nenhum, nenhum alvo (nunca "todos": é o que o central recusa).
 */
export function alvosDoFiltro<A extends Pick<Alvo, 'profile_id' | 'estado' | 'estagio' | 'instance_id'>>(alvos: readonly A[], f: FiltroDeCancelamento): A[] {
  if (filtroVazio(f)) return [];
  return alvos.filter((a) =>
    (f.profile_ids.length === 0 || (a.profile_id !== null && f.profile_ids.includes(a.profile_id)))
    && (f.estados.length === 0 || (a.estado !== null && f.estados.includes(a.estado)))
    && (f.estagios.length === 0 || (a.estagio !== null && f.estagios.includes(a.estagio)))
    && (f.instance_ids.length === 0 || (a.instance_id !== null && f.instance_ids.includes(a.instance_id))));
}

/**
 * O que o painel já sabe que o central vai ignorar (o central decide pela execução, não por isto): o alvo sem execução e o que já
 * terminou. Serve para a pessoa ver, antes de confirmar, quantos atos de verdade vêm.
 */
export function motivoProvavelDeIgnorar(a: Pick<Alvo, 'run_id' | 'estado'>): 'sem_execucao' | 'ja_terminou' | null {
  if (!a.run_id) return 'sem_execucao';
  return a.estado === 'concluido' || a.estado === 'cancelado' ? 'ja_terminou' : null;
}

export const MOTIVO_DO_IGNORADO: Record<string, string> = {
  sem_execucao: 'não tinha execução',
  ja_terminou: 'já tinha terminado',
};

export interface ResultadoDoCancelamento {
  /** Os `profile_id` cujo cancelamento foi pedido ao central. */
  cancelados: string[];
  ignorados: { profile_id: string; motivo: string }[];
  /** A operação como ela ficou (o central devolve o detalhe); `null` quando a resposta não o traz. */
  operacao: unknown;
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

/** `null` quando a resposta não é o resultado (sem a lista `cancelados`): erro de leitura, nunca "nada foi cancelado". */
export function lerResultadoDoCancelamento(v: unknown): ResultadoDoCancelamento | null {
  const o = registro(v);
  if (!o || !Array.isArray(o.cancelados)) return null;
  return {
    cancelados: o.cancelados.filter((x): x is string => typeof x === 'string' && x.trim() !== ''),
    ignorados: (Array.isArray(o.ignorados) ? o.ignorados : []).flatMap((x) => {
      const r = registro(x);
      const id = r ? texto(r.profile_id) : null;
      return r && id ? [{ profile_id: id, motivo: texto(r.motivo) ?? 'sem motivo informado' }] : [];
    }),
    operacao: o.operacao ?? null,
  };
}
