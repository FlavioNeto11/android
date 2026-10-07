/**
 * 31.185: a latência por estágio e por alvo, calculada SÓ dos carimbos que o central já manda (`estagios[].em`). Nada é inventado:
 * onde dois estágios têm a mesma hora (hoje, os da liberação), o intervalo é "mesma hora" e fica fora da mediana; onde a hora vem
 * fora da ordem do pipeline, o intervalo é "fora de ordem" e também fica fora. Quem olha vê o que há e o que falta, não um número
 * bonito sobre carimbo torto.
 *
 * v1.108 (31.187): quando o central manda a conta feita (`estagios[].etapa_ms`, `latencia`, `latencia_por_estagio`), a tela usa a DELE, que
 * segue o tempo e não a ordem fixa do pipeline e conta a duração desde a criação da operação; o cálculo local fica só para o central que ainda
 * não manda (e para o exemplo). Nunca se mistura: um alvo é todo do central ou todo local.
 */
import { ESTAGIOS, type Alvo, type EstagioId, type Operacao } from './modelo';

export type SituacaoDoPasso = 'ok' | 'mesma_hora' | 'fora_de_ordem';

export interface PassoDeLatencia {
  estagio: EstagioId;
  em: string;
  /** O estágio de referência: o anterior COM hora e em ordem; `null` no primeiro. */
  deEstagio: EstagioId | null;
  /** Duração desde o anterior; `null` no primeiro e no fora de ordem (hora anterior à do estágio de antes). */
  ms: number | null;
  /** `null` no primeiro passo. */
  situacao: SituacaoDoPasso | null;
}

export type FonteDaLatencia = 'central' | 'local';

export interface LatenciaDoAlvo {
  passos: PassoDeLatencia[];
  /** Do carimbo mais cedo ao mais tarde do alvo; `null` com menos de dois carimbos. */
  totalMs: number | null;
  /** O intervalo medido (situação `ok`) mais longo; `null` sem nenhum. */
  maisLento: { estagio: EstagioId; ms: number } | null;
  mesmaHora: number;
  foraDeOrdem: number;
  /** De onde veio a conta: do central (v1.108) ou dos carimbos, no painel. */
  fonte: FonteDaLatencia;
  /** v1.108: da ação preparada ao liberar (a espera pela pessoa); `null` sem liberar ou quando o central não manda. */
  esperaDoLiberarMs: number | null;
}

const ORDEM: readonly string[] = ESTAGIOS.map((e) => e.id);

/** O central mandou a conta (`etapa_ms` em algum estágio, ou a duração do alvo)? Então é a dele que vale. */
const doCentral = (alvo: Pick<Alvo, 'estagios' | 'latencia'>): boolean => !!alvo.latencia || alvo.estagios.some((e) => e.etapa_ms !== undefined);

function latenciaDoCentral(alvo: Pick<Alvo, 'estagios' | 'latencia'>): LatenciaDoAlvo {
  const vistos = new Set<EstagioId>();
  const lista = alvo.estagios.filter((e) => !vistos.has(e.estagio) && vistos.add(e.estagio));
  const passos: PassoDeLatencia[] = lista.map((e) => {
    const ms = e.etapa_ms ?? null;
    // O evento anterior é no TEMPO (a criação da operação, o liberar…): o painel não o nomeia.
    return { estagio: e.estagio, em: e.em ?? '', deEstagio: null, ms, situacao: ms === null ? null : ms === 0 ? 'mesma_hora' : 'ok' };
  });
  const medidos = passos.filter((p) => p.situacao === 'ok' && p.ms !== null);
  const maior = medidos.reduce<PassoDeLatencia | null>((m, p) => (m === null || (p.ms ?? 0) > (m.ms ?? 0) ? p : m), null);
  return {
    passos, totalMs: alvo.latencia?.duracao_ms ?? null, maisLento: maior ? { estagio: maior.estagio, ms: maior.ms! } : null,
    mesmaHora: passos.filter((p) => p.situacao === 'mesma_hora').length, foraDeOrdem: 0, fonte: 'central',
    esperaDoLiberarMs: alvo.latencia?.espera_do_liberar_ms ?? null,
  };
}

export function latenciaDoAlvo(alvo: Pick<Alvo, 'estagios' | 'latencia'>): LatenciaDoAlvo {
  if (doCentral(alvo)) return latenciaDoCentral(alvo);
  const vistos = new Set<EstagioId>();
  const comHora = alvo.estagios
    .flatMap((e) => { const t = e.em ? Date.parse(e.em) : NaN; return Number.isFinite(t) && !vistos.has(e.estagio) && vistos.add(e.estagio) ? [{ estagio: e.estagio, em: e.em!, t }] : []; })
    .sort((a, b) => ORDEM.indexOf(a.estagio) - ORDEM.indexOf(b.estagio));
  // A referência é o último estágio EM ORDEM: um carimbo fora de ordem não serve de base para o seguinte (inflaria o intervalo).
  let ref: (typeof comHora)[number] | null = null;
  const passos: PassoDeLatencia[] = comHora.map((x) => {
    if (!ref) { ref = x; return { estagio: x.estagio, em: x.em, deEstagio: null, ms: null, situacao: null }; }
    const d = x.t - ref.t;
    const de = ref.estagio;
    if (d < 0) return { estagio: x.estagio, em: x.em, deEstagio: de, ms: null, situacao: 'fora_de_ordem' };
    ref = x;
    return { estagio: x.estagio, em: x.em, deEstagio: de, ms: d, situacao: d === 0 ? 'mesma_hora' : 'ok' };
  });
  const medidos = passos.filter((p) => p.situacao === 'ok' && p.ms !== null);
  const maior = medidos.reduce<PassoDeLatencia | null>((m, p) => (m === null || (p.ms ?? 0) > (m.ms ?? 0) ? p : m), null);
  const ts = comHora.map((x) => x.t);
  return {
    passos,
    totalMs: ts.length >= 2 ? Math.max(...ts) - Math.min(...ts) : null,
    maisLento: maior ? { estagio: maior.estagio, ms: maior.ms! } : null,
    mesmaHora: passos.filter((p) => p.situacao === 'mesma_hora').length,
    foraDeOrdem: passos.filter((p) => p.situacao === 'fora_de_ordem').length, fonte: 'local', esperaDoLiberarMs: null,
  };
}

export function mediana(valores: readonly number[]): number | null {
  if (valores.length === 0) return null;
  const v = [...valores].sort((a, b) => a - b);
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m]! : (v[m - 1]! + v[m]!) / 2;
}

/** `p95Ms` só quando o central manda a conta (v1.108); o painel não calcula percentil de poucas amostras. */
export interface LatenciaDoEstagio { estagio: EstagioId; medianaMs: number; maiorMs: number; agentes: number; p95Ms?: number | null }

export interface LatenciaDaOperacao {
  /** Quantos agentes têm o total medido (pelo menos dois carimbos) e quantos há. */
  comTempo: number;
  agentes: number;
  medianaDoTotalMs: number | null;
  /** Por estágio de chegada, na ordem do pipeline, só os que têm intervalo medido. */
  porEstagio: LatenciaDoEstagio[];
  /** O estágio de maior mediana. */
  maisLento: LatenciaDoEstagio | null;
  mesmaHora: number;
  foraDeOrdem: number;
  /** A tabela por estágio veio do central (v1.108: com p95) ou foi calculada aqui. */
  fonte: FonteDaLatencia;
}

export function latenciaDaOperacao(alvos: readonly Pick<Alvo, 'estagios' | 'latencia'>[], doServidor?: Operacao['latencia_por_estagio']): LatenciaDaOperacao {
  const por = alvos.map(latenciaDoAlvo);
  const acumulado = new Map<EstagioId, number[]>();
  for (const l of por) for (const p of l.passos) if (p.situacao === 'ok' && p.ms !== null) acumulado.set(p.estagio, [...(acumulado.get(p.estagio) ?? []), p.ms]);
  const porEstagio = [...acumulado.entries()]
    .sort((a, b) => ORDEM.indexOf(a[0]) - ORDEM.indexOf(b[0]))
    .map(([estagio, ms]) => ({ estagio, medianaMs: mediana(ms)!, maiorMs: Math.max(...ms), agentes: ms.length }));
  const totais = por.map((l) => l.totalMs).filter((t): t is number => t !== null);
  // v1.108: o consolidado do central (mediana e p95 por estágio, só com etapa medida) vale no lugar do local.
  const doCentralPorEstagio: LatenciaDoEstagio[] | null = doServidor
    ? ESTAGIOS.flatMap((e) => { const x = doServidor[e.id]; return x ? [{ estagio: e.id, medianaMs: x.p50_ms, maiorMs: x.max_ms, agentes: x.n, p95Ms: x.p95_ms }] : []; })
    : null;
  const tabela = doCentralPorEstagio ?? porEstagio;
  return {
    comTempo: totais.length, agentes: alvos.length, medianaDoTotalMs: mediana(totais), porEstagio: tabela,
    maisLento: tabela.reduce<LatenciaDoEstagio | null>((m, e) => (m === null || e.medianaMs > m.medianaMs ? e : m), null),
    mesmaHora: por.reduce((s, l) => s + l.mesmaHora, 0), foraDeOrdem: por.reduce((s, l) => s + l.foraDeOrdem, 0),
    fonte: doCentralPorEstagio ? 'central' : 'local',
  };
}
