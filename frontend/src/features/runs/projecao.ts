/**
 * Projeção do plano pelo histórico (item 18.3): `GET /api/runs/{id}/projection` devolve o normal medido de cada etapa
 * — mediana (p50) e p90 de chamadas de IA, segundos e US$ por ação, nas etapas concluídas da janela — somado
 * (`backend/app/taskqueue/projecao.py::projetar`). Não chama IA. 409 `no_plan` quer dizer "ainda sem plano": não é
 * erro para a pessoa.
 *
 * A janela EFETIVA (`janela_dias`) nunca passa da retenção dos registros de IA (`log_retention_days`, ADR-054 decisão
 * 8): quando ela é menor que a configurada, o rótulo diz por quê — senão "30 dias" pareceria prometido e o número
 * sairia de 14.
 *
 * Leitura TOLERANTE, como no aprendizado: campo ausente vira zero ou `null`, etapa que não é objeto some.
 */
import { formatInt, isRecord, plural } from '../../lib/format';
import { formatUsd } from '../usage/usage';

export interface Faixa {
  p50: number;
  p90: number;
}

export interface EtapaProjetada {
  key: string;
  title: string;
  /** A ação do catálogo, ou `*` (etapa livre). */
  action: string;
  samples: number;
  calls: Faixa;
  seconds: Faixa;
  usd: Faixa;
  /** Menos amostras que o mínimo: usou o `*` do app, ou entrou com zero. */
  no_baseline: boolean;
  samples_without_cost: number;
}

export interface ProjecaoDoPlano {
  /** A janela EFETIVA, em dias: `min(janela configurada, retenção dos registros de IA)`. */
  janela_dias: number | null;
  janela_configurada: number | null;
  minimo_de_amostras: number | null;
  /** Amostras que não fizeram chamada de IA (receita reproduzindo, fluxo): número pequeno de verdade. */
  amostras_sem_custo: number;
  chamadas: Faixa;
  segundos: Faixa;
  usd: Faixa;
  /** As chaves das etapas sem base própria. */
  sem_base: string[];
  etapas: EtapaProjetada[];
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function faixa(v: unknown): Faixa {
  const o = isRecord(v) ? v : {};
  return { p50: num(o.p50) ?? 0, p90: num(o.p90) ?? 0 };
}

function lerEtapa(v: unknown): EtapaProjetada | null {
  if (!isRecord(v) || typeof v.key !== 'string' || !v.key) return null;
  return {
    key: v.key, title: typeof v.title === 'string' && v.title ? v.title : v.key,
    action: typeof v.action === 'string' && v.action ? v.action : '*', samples: num(v.samples) ?? 0,
    calls: faixa(v.calls), seconds: faixa(v.seconds), usd: faixa(v.usd), no_baseline: v.no_baseline === true,
    samples_without_cost: num(v.samples_without_cost) ?? 0,
  };
}

/** `null` quando a resposta não tem nem as somas: aí não há o que mostrar. */
export function lerProjecao(raw: unknown): ProjecaoDoPlano | null {
  if (!isRecord(raw) || !isRecord(raw.chamadas)) return null;
  return {
    janela_dias: num(raw.janela_dias), janela_configurada: num(raw.janela_configurada),
    minimo_de_amostras: num(raw.minimo_de_amostras), amostras_sem_custo: num(raw.amostras_sem_custo) ?? 0,
    chamadas: faixa(raw.chamadas), segundos: faixa(raw.segundos), usd: faixa(raw.usd),
    sem_base: Array.isArray(raw.sem_base) ? raw.sem_base.filter((k): k is string => typeof k === 'string') : [],
    etapas: Array.isArray(raw.etapas) ? raw.etapas.map(lerEtapa).filter((e): e is EtapaProjetada => e !== null) : [],
  };
}

/** "Normal medido nos últimos 14 dias — a janela configurada é de 30, limitada pela retenção dos registros de IA". */
export function rotuloDaJanela(p: Pick<ProjecaoDoPlano, 'janela_dias' | 'janela_configurada'>): string {
  const dias = p.janela_dias;
  if (dias === null) return 'Normal medido no histórico (janela não informada pelo servidor)';
  const base = dias === 1 ? 'Normal medido no último dia' : `Normal medido nos últimos ${plural(dias, 'dia', 'dias')}`;
  const configurada = p.janela_configurada;
  if (configurada !== null && dias < configurada) {
    return `${base} — a janela configurada é de ${plural(configurada, 'dia', 'dias')}, limitada pela retenção dos `
      + 'registros de IA';
  }
  return base;
}

/** Nenhuma etapa com base e nada somado: "a primeira execução mede" (o mesmo corte de `projecao.py::resumo`). */
export function semHistorico(p: Pick<ProjecaoDoPlano, 'etapas' | 'sem_base' | 'chamadas'>): boolean {
  return p.etapas.length > 0 && p.sem_base.length >= p.etapas.length && !p.chamadas.p90;
}

function intervalo(a: string, b: string): string {
  return a === b ? a : `${a}–${b}`;
}

export function textoDasChamadas(f: Faixa): string {
  return intervalo(formatInt(Math.round(f.p50)), formatInt(Math.round(f.p90)));
}

/** "US$ 0,20–0,38": o prefixo uma vez só. */
export function textoDoUsd(f: Faixa): string {
  const a = formatUsd(f.p50);
  const b = formatUsd(f.p90);
  return a === b ? a : `${a}–${b.replace(/^US\$\s*/, '')}`;
}

/** Segundos quando a etapa é curta; minutos (nunca "0 min") quando passa de um minuto e meio. */
export function textoDoTempo(f: Faixa): string {
  if (f.p90 < 90) return `${intervalo(formatInt(Math.round(f.p50)), formatInt(Math.round(f.p90)))} s`;
  const min = (s: number) => formatInt(Math.max(1, Math.round(s / 60)));
  return `${intervalo(min(f.p50), min(f.p90))} min`;
}
