/**
 * 31.204: duas operações lado a lado, a partir do relatório de cada uma (o do central, v1.111, ou o montado no painel como reserva).
 * Puro: recebe dois `RelatorioDaOperacao` e devolve linhas prontas, com a diferença (B menos A) onde o número é medido nos dois. O que um
 * dos lados não mediu fica "não medido" e a diferença fica em branco: nunca se compara com zero inventado.
 */
import { formatInt, formatUsd4 } from '../../lib/format';
import { formatSpan } from '../../lib/time';
import { ESTAGIOS } from './modelo';
import { ROTULO_DO_AMBIENTE, type CriterioDoRelatorio, type RelatorioDaOperacao } from './relatorio';

export const NAO_MEDIDO = 'não medido';

export interface LinhaComparada {
  rotulo: string;
  a: string;
  b: string;
  /** B menos A, em palavras (`+2 s`, `-US$ 0,0100`); `null` quando um dos lados não mediu ou o valor não é número. */
  delta: string | null;
  /** Os dois lados dizem coisas diferentes. */
  diferente: boolean;
}

const sinal = (d: number): string => (d > 0 ? '+' : d < 0 ? '-' : '±');

function numerica(rotulo: string, a: number | null, b: number | null, fmt: (n: number) => string): LinhaComparada {
  const d = a !== null && b !== null ? b - a : null;
  return {
    rotulo, a: a === null ? NAO_MEDIDO : fmt(a), b: b === null ? NAO_MEDIDO : fmt(b),
    delta: d === null ? null : d === 0 ? 'igual' : `${sinal(d)}${fmt(Math.abs(d))}`, diferente: a !== b,
  };
}
const textual = (rotulo: string, a: string | null, b: string | null): LinhaComparada =>
  ({ rotulo, a: a ?? NAO_MEDIDO, b: b ?? NAO_MEDIDO, delta: null, diferente: (a ?? NAO_MEDIDO) !== (b ?? NAO_MEDIDO) });

const span = (n: number): string => formatSpan(n);
const usd = (n: number): string => formatUsd4(n);
const inteiro = (n: number): string => formatInt(n);

export interface ComparacaoDeOperacoes {
  resumo: LinhaComparada[];
  agentes: LinhaComparada[];
  custo: LinhaComparada[];
  latencia: LinhaComparada[];
  /** A mediana de cada estágio medido em pelo menos um dos lados, na ordem do pipeline. */
  porEstagio: LinhaComparada[];
  /** `null` quando um dos lados não traz os critérios (só o relatório do central traz). */
  criterios: { id: string; nome: string; a: string; b: string; diferente: boolean }[] | null;
  falhas: LinhaComparada[];
}

const ESTADO_DO_CRITERIO: Record<NonNullable<CriterioDoRelatorio['estado']>, string> = {
  implementado: 'implementado', testado_em_simulacao: 'testado em simulação', provado_real: 'provado de verdade', bloqueado: 'bloqueado', nao_implementado: 'não implementado',
};
const NESTA: Record<CriterioDoRelatorio['nesta_operacao'], string> = { sim: 'sim', nao: 'não', nao_medido: NAO_MEDIDO };
const doCriterio = (c: CriterioDoRelatorio | undefined): string =>
  (c ? `${c.estado ? ESTADO_DO_CRITERIO[c.estado] : 'estado não informado'} · nesta operação: ${NESTA[c.nesta_operacao]}` : 'ausente');

export function compararRelatorios(a: RelatorioDaOperacao, b: RelatorioDaOperacao): ComparacaoDeOperacoes {
  const ia = a.identidades;
  const ib = b.identidades;
  const lat = (r: RelatorioDaOperacao) => r.latencia;
  const estagios = ESTAGIOS.filter((e) => lat(a)?.por_estagio.some((x) => x.estagio === e.id) || lat(b)?.por_estagio.some((x) => x.estagio === e.id));
  const p50 = (r: RelatorioDaOperacao, id: string): number | null => lat(r)?.por_estagio.find((x) => x.estagio === id)?.p50_ms ?? null;
  const motivos = [...new Set([...a.falhas_por_motivo, ...b.falhas_por_motivo].map((f) => f.motivo))].sort();
  const dosMotivos = (r: RelatorioDaOperacao, m: string): number => r.falhas_por_motivo.filter((f) => f.motivo === m).reduce((s, f) => s + f.agentes, 0);
  const ids = [...new Set([...(a.criterios ?? []), ...(b.criterios ?? [])].map((c) => c.id))];
  return {
    resumo: [
      textual('Objetivo', a.operacao.comando || null, b.operacao.comando || null),
      textual('App', a.operacao.app_id, b.operacao.app_id),
      textual('Estado', a.operacao.status, b.operacao.status),
      textual('Ambiente', a.ambiente ? ROTULO_DO_AMBIENTE[a.ambiente] : null, b.ambiente ? ROTULO_DO_AMBIENTE[b.ambiente] : null),
      textual('Relatório montado por', a.fonte === 'servidor' ? 'o central' : 'o painel', b.fonte === 'servidor' ? 'o central' : 'o painel'),
    ],
    agentes: [
      numerica('Solicitados', a.capacidade.solicitados, b.capacidade.solicitados, inteiro),
      numerica('Concluídas', a.capacidade.concluidas, b.capacidade.concluidas, inteiro),
      numerica('Bloqueadas', a.capacidade.bloqueadas, b.capacidade.bloqueadas, inteiro),
      numerica('Identidades que executam hoje', ia?.executam_hoje ?? null, ib?.executam_hoje ?? null, inteiro),
      numerica('Identidades que faltam', ia?.deficit ?? null, ib?.deficit ?? null, inteiro),
    ],
    custo: [
      numerica('Total', a.custo.total_usd, b.custo.total_usd, usd),
      numerica('Agentes', a.custo.alvos_usd, b.custo.alvos_usd, usd),
      numerica('Pesquisa externa', a.custo.pesquisa_usd, b.custo.pesquisa_usd, usd),
      numerica('Por peça (ação executada e verificada)', a.custo.por_peca_usd, b.custo.por_peca_usd, usd),
    ],
    latencia: [
      numerica('Duração mediana por agente', lat(a)?.duracao_mediana_ms ?? null, lat(b)?.duracao_mediana_ms ?? null, span),
      numerica('Duração do agente mais lento', lat(a)?.mais_lento?.duracao_ms ?? null, lat(b)?.mais_lento?.duracao_ms ?? null, span),
    ],
    porEstagio: estagios.map((e) => numerica(e.rotulo, p50(a, e.id), p50(b, e.id), span)),
    criterios: a.criterios && b.criterios
      ? ids.map((id) => {
        const ca = a.criterios!.find((c) => c.id === id);
        const cb = b.criterios!.find((c) => c.id === id);
        return { id, nome: (ca ?? cb)!.nome, a: doCriterio(ca), b: doCriterio(cb), diferente: doCriterio(ca) !== doCriterio(cb) };
      })
      : null,
    falhas: motivos.map((m) => numerica(m, dosMotivos(a, m), dosMotivos(b, m), inteiro)),
  };
}
