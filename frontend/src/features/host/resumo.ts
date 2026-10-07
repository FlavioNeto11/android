/** 31.180: o que o painel do host mostra, calculado das amostras (sem React). Nada aqui troca "não medido" por zero. */
import type { AmostraDoHost } from './contratoDoHost';

const validos = (xs: readonly (number | null)[]): number[] => xs.filter((x): x is number => x !== null);

/** O último valor medido da série (o mais novo que não é `null`). */
const ultimoMedido = (xs: readonly (number | null)[]): number | null => validos([...xs].reverse())[0] ?? null;

function resumoDaSerie(amostras: readonly AmostraDoHost[], valor: (a: AmostraDoHost) => number | null): { media: number | null; pico: number | null; picoEm: string | null } {
  const v = validos(amostras.map(valor));
  const pico = v.length ? Math.max(...v) : null;
  return { media: v.length ? v.reduce((s, x) => s + x, 0) / v.length : null, pico, picoEm: pico === null ? null : amostras.find((a) => valor(a) === pico)?.ts_utc ?? null };
}

export interface ResumoDoHost {
  /** A amostra mais nova (o "agora" do painel). */
  ultima: AmostraDoHost | null;
  cpu: { media: number | null; pico: number | null; picoEm: string | null };
  ramLivreMinimaMb: number | null;
  /** O último valor medido de `qemu_host_pct` (a 1ª linha de cada execução vem vazia). */
  qemuPct: number | null;
  /** 31.211: a CPU como MÉDIA do minuto (`cpu_media_pct`, v2 do amostrador); `cpu` acima é o instantâneo. Tudo `null` sem a coluna. */
  cpuMedia: { agora: number | null; media: number | null; pico: number | null; picoEm: string | null };
}

export function resumoDaJanela(amostras: readonly AmostraDoHost[]): ResumoDoHost {
  const cpus = validos(amostras.map((a) => a.cpu_host_pct));
  const pico = cpus.length ? Math.max(...cpus) : null;
  const rams = validos(amostras.map((a) => a.ram_livre_mb));
  return {
    ultima: amostras.at(-1) ?? null,
    cpu: {
      media: cpus.length ? cpus.reduce((s, x) => s + x, 0) / cpus.length : null,
      pico, picoEm: pico === null ? null : amostras.find((a) => a.cpu_host_pct === pico)?.ts_utc ?? null,
    },
    ramLivreMinimaMb: rams.length ? Math.min(...rams) : null,
    cpuMedia: { agora: ultimoMedido(amostras.map((a) => a.cpu_media_pct)), ...resumoDaSerie(amostras, (a) => a.cpu_media_pct) },
    qemuPct: validos([...amostras].reverse().map((a) => a.qemu_host_pct))[0] ?? null,
  };
}

export interface ParaOndeFoiACpu {
  /** Quantos minutos entram na conta: só os que têm TODAS as partes medidas (a 1ª linha de cada execução e o amostrador anterior não têm). */
  minutos: number;
  /** A média do minuto (`cpu_media_pct`) e as partes, em % do host, médias dos mesmos minutos. */
  total: number;
  emuladores: number;
  topo: number;
  demais: number;
  naoAtribuido: number;
}

/**
 * 31.211: para onde foi a CPU do host, nas médias dos minutos com tudo medido: emuladores (qemu) + os 3 processos do topo + os demais
 * processos + o não atribuído (o que nasce e morre dentro do minuto, núcleo/interrupções e a VM). As partes somam o total a menos de
 * arredondamento. `null` sem nenhum minuto completo: a tela diz que o amostrador v2 ainda não grava, nunca mostra zero.
 */
export function paraOndeFoiACpu(amostras: readonly AmostraDoHost[]): ParaOndeFoiACpu | null {
  const completos = amostras.flatMap((a) => (
    a.cpu_media_pct !== null && a.qemu_host_pct !== null && a.demais_processos_pct !== null && a.nao_atribuido_pct !== null
      ? [{ total: a.cpu_media_pct, emuladores: a.qemu_host_pct, topo: a.processos_top.reduce((s, p) => s + p.pct, 0), demais: a.demais_processos_pct, naoAtribuido: a.nao_atribuido_pct }]
      : []));
  if (completos.length === 0) return null;
  const media = (campo: 'total' | 'emuladores' | 'topo' | 'demais' | 'naoAtribuido') => completos.reduce((s, x) => s + x[campo], 0) / completos.length;
  return { minutos: completos.length, total: media('total'), emuladores: media('emuladores'), topo: media('topo'), demais: media('demais'), naoAtribuido: media('naoAtribuido') };
}

export interface ProcessoNoTopo { nome: string; mediaPct: number; picoPct: number }

/**
 * Os processos que mais pesaram na janela: a média em % do host por MINUTO da janela (o minuto em que o processo não estava entre
 * os três do topo conta zero: a média diz quanto ele pesou, não quanto pesou quando apareceu) e o maior valor de um minuto.
 */
export function processosNoTopo(amostras: readonly AmostraDoHost[], quantos = 5): ProcessoNoTopo[] {
  if (amostras.length === 0) return [];
  const soma = new Map<string, { total: number; pico: number }>();
  for (const a of amostras) {
    for (const p of a.processos_top) {
      const x = soma.get(p.nome) ?? { total: 0, pico: 0 };
      x.total += p.pct;
      x.pico = Math.max(x.pico, p.pct);
      soma.set(p.nome, x);
    }
  }
  return [...soma.entries()].map(([nome, x]) => ({ nome, mediaPct: x.total / amostras.length, picoPct: x.pico }))
    .sort((a, b) => b.mediaPct - a.mediaPct || a.nome.localeCompare(b.nome)).slice(0, quantos);
}

export interface PressaoDoAparelho { instanceId: string; avisos: number; minutos: number; ultimoEm: string; /** Dos minutos da janela, em % (31.211). */ pctDaJanela: number }

/** Os avisos "Convidado sob pressão de CPU" somados por aparelho na janela, o aparelho com mais avisos primeiro. */
export function pressaoPorAparelho(amostras: readonly AmostraDoHost[]): PressaoDoAparelho[] {
  const por = new Map<string, PressaoDoAparelho>();
  for (const a of amostras) {
    for (const p of a.avisos_pressao) {
      const x = por.get(p.instance_id) ?? { instanceId: p.instance_id, avisos: 0, minutos: 0, ultimoEm: a.ts_utc, pctDaJanela: 0 };
      x.avisos += p.n;
      x.minutos += 1;
      x.ultimoEm = a.ts_utc;
      por.set(p.instance_id, x);
    }
  }
  for (const x of por.values()) x.pctDaJanela = (x.minutos / amostras.length) * 100;
  return [...por.values()].sort((a, b) => b.avisos - a.avisos || a.instanceId.localeCompare(b.instanceId));
}

/** Minutos inteiros desde a última amostra; `null` sem amostra. O amostrador grava uma por minuto: acima de 3 ele parou (ou o central não lê). */
export function defasagemEmMinutos(ultimaTs: string | null | undefined, agoraMs: number): number | null {
  const t = ultimaTs ? Date.parse(ultimaTs) : NaN;
  return Number.isFinite(t) ? Math.max(0, Math.floor((agoraMs - t) / 60_000)) : null;
}

export const LIMITE_DE_DEFASAGEM_MIN = 3;

export interface PontoDaSerie { x: number; y: number }

/**
 * A série como trechos de linha (um trecho por sequência sem buraco): onde o valor não foi medido, a linha ABRE, não cai a zero.
 * `max` fixa o topo da escala (100 para CPU); sem ele, o maior valor da série.
 */
export function trechosDaSerie(valores: readonly (number | null)[], largura: number, altura: number, max?: number): PontoDaSerie[][] {
  const topo = max ?? Math.max(1, ...validos(valores));
  const passo = valores.length > 1 ? largura / (valores.length - 1) : 0;
  const trechos: PontoDaSerie[][] = [];
  let atual: PontoDaSerie[] = [];
  valores.forEach((v, i) => {
    if (v === null) {
      if (atual.length) trechos.push(atual);
      atual = [];
      return;
    }
    atual.push({ x: Math.round(i * passo * 10) / 10, y: Math.round((altura - Math.min(1, Math.max(0, v / topo)) * altura) * 10) / 10 });
  });
  if (atual.length) trechos.push(atual);
  return trechos;
}
