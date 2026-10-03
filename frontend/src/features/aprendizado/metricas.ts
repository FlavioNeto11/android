import { hashDe } from '../../lib/rotas';
import { isLivroKind } from './model';

/**
 * As métricas do aprendizado e a lista de revisões do curador no painel (30.33; rotas do 30.8, adendo v0.89 do
 * contrato). Os tipos espelham `presentation/metricas.py`; a leitura é tolerante (um bloco ausente não derruba a aba) e
 * preserva a regra do backend: ausente é `null`, nunca zero. Uma taxa sem amostra continua `null`, com o `n` ao lado.
 */

export type Contagem = Record<string, number>;

export interface Tempo {
  mediana_h: number | null;
  p90_h: number | null;
  n: number;
}

export interface LadoDoProxy {
  etapas: number;
  falhas: number;
  taxa_de_falha: number | null;
}

export interface ResumoDoCurador {
  revisoes: number;
  simuladas: number;
  validade: { ok: number; invalida: number; recusada: number };
  decisoes: Contagem;
  aplicadas: number;
  overrides: number;
  usd: number | null;
  /** O balanço da sombra da autopublicação (30.34, adendo v0.93): global; `null` sem ela composta. */
  autopublicacao: SombraDaAutopublicacao | null;
}

export interface SombraDaAutopublicacao {
  modo: string | null;
  casos: number;
  abertos: number;
  limpos: number;
  regrediram: number;
  /** Limpos ÷ fechados; `null` sem caso fechado. */
  taxa_sem_regressao: number | null;
  libera: boolean;
  limiares: { casos_fechados: number | null; taxa_sem_regressao: number | null; janela_dias: number | null };
}

/** Global, na janela DO CURADOR (`janela_dias` do config), não na do pedido. */
export interface OrcamentoDoCurador {
  modo: string | null;
  janela_dias: number | null;
  gasto_da_operacao: number | null;
  gasto_da_curadoria: number | null;
  /** O B_W com as revisões já gravadas, o menor dos dois ramos; a próxima volta só pode aumentá-lo. */
  orcamento: number | null;
  /** O ramo α·G_W (a fração da operação). */
  teto_alfa: number | null;
  /** O ramo k·N_W·c̄ (pelas revisões) e qual dos dois manda (30.33-C). Ausentes no backend anterior. */
  pelas_revisoes: number | null;
  ramo: 'operacao' | 'revisoes' | null;
  k: number | null;
  revisoes_na_janela: number | null;
  /** C_W / B_W: um teto; `null` sem orçamento. */
  uso: number | null;
  aviso: boolean;
}

export interface MetricasDoAprendizado {
  app: string | null;
  janela_dias: number | null;
  desde: string | null;
  ate: string | null;
  itens: Record<string, Contagem>;
  por_origem: Contagem;
  pendentes: number | null;
  aprovacoes: { sistema: Contagem; pessoa: Contagem };
  curador: ResumoDoCurador | null;
  refutados_depois_de_promovidos: {
    desligados_pelo_sistema: number | null; desligados_por_pessoa: number | null; publicados_com_evidencia_contra: number | null;
  };
  sucesso_depois_de_promovido: { a_favor: number; contra: number; taxa: number | null };
  churn: { transicoes: number | null; itens_com_transicao: number | null; criados: number | null; desligados: number | null };
  tempos: { candidate_validated: Tempo; validated_published: Tempo };
  saude: Contagem;
  /** `null` quando o aproveitamento não está ligado. */
  economia: Record<string, number | null> | null;
  /** NÃO mede falha evitada (não há contrafactual): compara a taxa de falha nas etapas com as duas conduções. */
  falhas_evitadas_proxy: { etapas_comparadas: number; com_receita: LadoDoProxy | null; so_ia: LadoDoProxy | null };
  /** `null` sem o curador composto. */
  orcamento_do_curador: OrcamentoDoCurador | null;
  /** Transições e evidências de itens que não estão mais no livro. */
  sem_item: Contagem;
}

export interface RevisaoDoCurador {
  id: string;
  criado_em: string | null;
  item_ref: string;
  item_kind: string | null;
  /** O app principal do item (o `scope_app` da revisão) e o nome dele. */
  app: string | null;
  app_nome: string | null;
  /** O título do item no livro (com `etapa` e a capability para nomear a receita como no catálogo); `null` quando o
   *  item saiu do livro. E, no fluxo que atravessa apps, os apps dele e os nomes (30.33-C). */
  titulo: string | null;
  etapa: string | null;
  capability: string | null;
  capability_nome: string | null;
  apps: string[];
  apps_nomes: string[];
  gatilho: string | null;
  validade: string;
  simulado: boolean;
  provedor: string | null;
  modelo: string | null;
  usd: number | null;
  classe: string | null;
  decisao: string | null;
  confianca: string | null;
  decisao_final: string | null;
  decidido_por: string | null;
  override: boolean;
  /** O desfecho medido 14 dias depois (30.35); `null` antes disso ou no backend anterior. */
  resultado_posterior: string | null;
  resultado_em: string | null;
}

export interface PaginaDeRevisoes {
  revisoes: RevisaoDoCurador[];
  /** O cursor da página seguinte (`criada|id`), ou `null` no fim. */
  proximo: string | null;
}

// ---------------------------------------------------------------- leitura tolerante

const obj = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null;
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);
const textos = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x !== '') : []);
const int0 = (v: unknown): number => num(v) ?? 0;

/** `{chave: n}` só com os números; o resto some (nunca vira zero). */
function contagem(v: unknown): Contagem {
  const o = obj(v);
  const out: Contagem = {};
  if (!o) return out;
  for (const [k, x] of Object.entries(o)) {
    const n = num(x);
    if (n !== null) out[k] = n;
  }
  return out;
}

function tempo(v: unknown): Tempo {
  const o = obj(v) ?? {};
  return { mediana_h: num(o.mediana_h), p90_h: num(o.p90_h), n: int0(o.n) };
}

function lado(v: unknown): LadoDoProxy | null {
  const o = obj(v);
  return o ? { etapas: int0(o.etapas), falhas: int0(o.falhas), taxa_de_falha: num(o.taxa_de_falha) } : null;
}

function curador(v: unknown): ResumoDoCurador | null {
  const o = obj(v);
  if (!o) return null;
  const val = obj(o.validade) ?? {};
  return {
    revisoes: int0(o.revisoes), simuladas: int0(o.simuladas),
    validade: { ok: int0(val.ok), invalida: int0(val.invalida), recusada: int0(val.recusada) },
    decisoes: contagem(o.decisoes), aplicadas: int0(o.aplicadas), overrides: int0(o.overrides), usd: num(o.usd),
    autopublicacao: sombra(o.autopublicacao),
  };
}

function sombra(v: unknown): SombraDaAutopublicacao | null {
  const o = obj(v);
  if (!o) return null;
  const l = obj(o.limiares) ?? {};
  return {
    modo: str(o.modo), casos: int0(o.casos), abertos: int0(o.abertos), limpos: int0(o.limpos),
    regrediram: int0(o.regrediram), taxa_sem_regressao: num(o.taxa_sem_regressao), libera: o.libera === true,
    limiares: { casos_fechados: num(l.casos_fechados), taxa_sem_regressao: num(l.taxa_sem_regressao), janela_dias: num(l.janela_dias) },
  };
}

function orcamento(v: unknown): OrcamentoDoCurador | null {
  const o = obj(v);
  if (!o) return null;
  return {
    modo: str(o.modo), janela_dias: num(o.janela_dias), gasto_da_operacao: num(o.gasto_da_operacao),
    gasto_da_curadoria: num(o.gasto_da_curadoria), orcamento: num(o.orcamento), teto_alfa: num(o.teto_alfa),
    pelas_revisoes: num(o.pelas_revisoes), ramo: o.ramo === 'operacao' || o.ramo === 'revisoes' ? o.ramo : null,
    k: num(o.k), revisoes_na_janela: num(o.revisoes_na_janela), uso: num(o.uso), aviso: o.aviso === true,
  };
}

export function lerMetricas(raw: unknown): MetricasDoAprendizado {
  const o = obj(raw) ?? {};
  const itens: Record<string, Contagem> = {};
  for (const [k, v] of Object.entries(obj(o.itens) ?? {})) itens[k] = contagem(v);
  const apr = obj(o.aprovacoes) ?? {};
  const ref = obj(o.refutados_depois_de_promovidos) ?? {};
  const suc = obj(o.sucesso_depois_de_promovido) ?? {};
  const churn = obj(o.churn) ?? {};
  const tempos = obj(o.tempos) ?? {};
  const proxy = obj(o.falhas_evitadas_proxy) ?? {};
  const eco = obj(o.economia);
  return {
    app: str(o.app), janela_dias: num(o.janela_dias), desde: str(o.desde), ate: str(o.ate),
    itens, por_origem: contagem(o.por_origem), pendentes: num(o.pendentes),
    aprovacoes: { sistema: contagem(apr.sistema), pessoa: contagem(apr.pessoa) },
    curador: curador(o.curador),
    refutados_depois_de_promovidos: {
      desligados_pelo_sistema: num(ref.desligados_pelo_sistema), desligados_por_pessoa: num(ref.desligados_por_pessoa),
      publicados_com_evidencia_contra: num(ref.publicados_com_evidencia_contra),
    },
    sucesso_depois_de_promovido: { a_favor: int0(suc.a_favor), contra: int0(suc.contra), taxa: num(suc.taxa) },
    churn: {
      transicoes: num(churn.transicoes), itens_com_transicao: num(churn.itens_com_transicao), criados: num(churn.criados),
      desligados: num(churn.desligados),
    },
    tempos: { candidate_validated: tempo(tempos.candidate_validated), validated_published: tempo(tempos.validated_published) },
    saude: contagem(o.saude),
    economia: eco ? Object.fromEntries(Object.entries(eco).map(([k, v]) => [k, num(v)])) : null,
    falhas_evitadas_proxy: { etapas_comparadas: int0(proxy.etapas_comparadas), com_receita: lado(proxy.com_receita), so_ia: lado(proxy.so_ia) },
    orcamento_do_curador: orcamento(o.orcamento_do_curador),
    sem_item: contagem(o.sem_item),
  };
}

export function lerPaginaDeRevisoes(raw: unknown): PaginaDeRevisoes {
  const o = obj(raw) ?? {};
  const revisoes: RevisaoDoCurador[] = [];
  for (const x of Array.isArray(o.revisoes) ? o.revisoes : []) {
    const r = obj(x);
    const id = r ? str(r.id) : null;
    if (!r || !id) continue;
    revisoes.push({
      id, criado_em: str(r.criado_em), item_ref: str(r.item_ref) ?? '', item_kind: str(r.item_kind), app: str(r.app),
      app_nome: str(r.app_nome), titulo: str(r.titulo), etapa: str(r.etapa), capability: str(r.capability),
      capability_nome: str(r.capability_nome), apps: textos(r.apps), apps_nomes: textos(r.apps_nomes),
      gatilho: str(r.gatilho), validade: str(r.validade) ?? 'ok', simulado: r.simulado === true, provedor: str(r.provedor),
      modelo: str(r.modelo), usd: num(r.usd), classe: str(r.classe), decisao: str(r.decisao), confianca: str(r.confianca),
      decisao_final: str(r.decisao_final), decidido_por: str(r.decidido_por), override: r.override === true,
      resultado_posterior: str(r.resultado_posterior), resultado_em: str(r.resultado_em),
    });
  }
  return { revisoes, proximo: str(o.proximo) };
}

// ---------------------------------------------------------------- texto e regras de exibição

/** Desde o deploy 8, `app_foreground` aberta sem IA fecha `sem_ator`: `so_ia`, a economia e o proxy mudam de regime. */
export const QUEBRA_DE_SERIE = '2026-10-03T09:06:28Z';

/** A janela começa antes da quebra e termina depois dela: comparar com outra janela engana. */
export function atravessaAQuebra(desde: string | null, ate: string | null): boolean {
  const q = Date.parse(QUEBRA_DE_SERIE);
  const d = desde ? Date.parse(desde) : NaN;
  const a = ate ? Date.parse(ate) : NaN;
  return Number.isFinite(d) && Number.isFinite(a) && d < q && a > q;
}

const nf1 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf4 = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 });

/** Horas do backend na unidade que se lê: abaixo de 1 h, em minutos (0,024 h é "1 min"); acima de 2 dias, em dias. */
export function formatHoras(h: number | null): string {
  if (h === null || !Number.isFinite(h) || h < 0) return '—';
  if (h < 1 / 60) return h === 0 ? '0 min' : 'menos de 1 min';
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${nf1.format(h)} h`;
  return `${nf1.format(h / 24)} dias`;
}

/** A taxa de 0 a 1 em porcentagem; `null` não vira 0 %. */
export function formatTaxa(t: number | null): string {
  return t === null || !Number.isFinite(t) ? 'sem amostra' : `${nf1.format(t * 100)}%`;
}

/** US$ com centavos e, abaixo de US$ 1, até 4 casas (o parecer custa frações de centavo; "0,07" esconderia 0,0682). */
export function formatUsd(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return '—';
  return `US$ ${Math.abs(v) < 1 ? nf4.format(v) : nf2.format(v)}`;
}

/** O item da revisão no catálogo: a mesma leitura de `domain/aprendido.py::_alvo` (`fluxo:12` ou `li-…` com o tipo). */
export function hrefDoItemDaRevisao(r: Pick<RevisaoDoCurador, 'item_ref' | 'item_kind'>): string | null {
  const i = r.item_ref.indexOf(':');
  if (i > 0) {
    const tipo = r.item_ref.slice(0, i);
    return isLivroKind(tipo) && r.item_ref.length > i + 1 ? hashDe('aprendizado', { query: { aba: 'aprendido', item: r.item_ref } }) : null;
  }
  return r.item_ref && isLivroKind(r.item_kind)
    ? hashDe('aprendizado', { query: { aba: 'aprendido', item: `${r.item_kind}:${r.item_ref}` } }) : null;
}

const ECONOMIA: Record<string, string> = {
  etapas: 'etapas no período', elegiveis: 'podiam usar receita', por_receita: 'feitas só por receita',
  receita_mais_ia: 'receita com ajuda da IA', so_ia: 'feitas só pela IA', sem_ator: 'sem ator (só abrir o app)',
  sem_cobertura: 'sem receita para a etapa', chamadas_evitadas_estimadas: 'chamadas de IA evitadas (estimativa)',
  etapas_por_receita_sem_base: 'por receita, sem base para estimar',
};

export function rotuloDaEconomia(chave: string): string {
  return ECONOMIA[chave] ?? chave.replaceAll('_', ' ');
}

const MODO: Record<string, string> = { off: 'desligado', shadow: 'em sombra', on: 'ligado' };

export function rotuloDoModo(modo: string | null): string {
  return modo ? MODO[modo] ?? modo : 'sem modo';
}

const SEM_ITEM: Record<string, string> = { transicoes: 'transições', evidencias: 'evidências' };

export function rotuloDoSemItem(chave: string): string {
  return SEM_ITEM[chave] ?? chave.replaceAll('_', ' ');
}
