/**
 * 31.234: a pesquisa da operação na tela Operação. Dois casos: a pesquisa paga (o custo em `custo.pesquisa_usd` e as fontes que ela achou, em
 * `fontes_da_pesquisa`) e a reaproveitada do Livro (31.231, Aprendizado e Jev): o critério, os fatos usados com origem e frescor. O campo
 * `pesquisa` do GET da operação AINDA NÃO EXISTE no contrato (hoje `pesquisa.estado` e `livro.<item>` são chaves da memória da operação):
 * este leitor segue a forma PROPOSTA à Jev e é tolerante, de modo que sem o campo a tela só afirma o que o central já diz.
 * Campo torto vira "não informado", nunca zero.
 */

export type EstadoDaPesquisa = 'reaproveitada_do_livro' | 'paga' | 'nao_rodou' | 'falhou';
const ESTADOS: readonly string[] = ['reaproveitada_do_livro', 'paga', 'nao_rodou', 'falhou'];

export interface FatoDaPesquisa {
  /** A referência do item no Livro (o `livro.<item>` da memória da operação). */
  item: string;
  origem: string | null;
  /** Até quando o fato vale; passada essa data a pesquisa paga volta. */
  frescorAte: string | null;
  confianca: 'confirmado' | 'hipotese' | null;
}

export interface PesquisaDaOperacao {
  /** `null` = o central não disse (ou disse um estado que este painel não conhece). */
  estado: EstadoDaPesquisa | null;
  /** O critério que fez o Livro bastar, por extenso (do servidor). */
  criterio: string | null;
  minimoDeFatos: number | null;
  /** O MENOR frescor dos fatos usados: a pesquisa paga reabre junto com o primeiro que vence. */
  frescorAte: string | null;
  custoUsd: number | null;
  fatos: FatoDaPesquisa[];
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);

function lerFato(v: unknown): FatoDaPesquisa | null {
  const o = registro(v);
  const item = o ? texto(o.item) : null;
  if (!o || !item) return null;
  return { item, origem: texto(o.origem), frescorAte: texto(o.frescor_ate), confianca: o.confianca === 'confirmado' || o.confianca === 'hipotese' ? o.confianca : null };
}

/** `null` quando ausente ou sem forma de objeto. Um fato sem `item` é descartado (não há o que mostrar dele). */
export function lerPesquisaDaOperacao(v: unknown): PesquisaDaOperacao | null {
  const o = registro(v);
  if (!o) return null;
  const estado = texto(o.estado);
  return {
    estado: estado && ESTADOS.includes(estado) ? (estado as EstadoDaPesquisa) : null,
    criterio: texto(o.criterio), minimoDeFatos: inteiro(o.minimo_fatos), frescorAte: texto(o.frescor_ate),
    custoUsd: typeof o.custo_usd === 'number' && Number.isFinite(o.custo_usd) && o.custo_usd >= 0 ? o.custo_usd : null,
    fatos: (Array.isArray(o.fatos) ? o.fatos : []).flatMap((f) => { const x = lerFato(f); return x ? [x] : []; }),
  };
}

/** `fontes_da_pesquisa`: as URLs que a pesquisa externa achou. Só texto não vazio, sem repetição. */
export function lerFontesDaPesquisa(v: unknown): string[] | null {
  if (!Array.isArray(v)) return null;
  return Array.from(new Set(v.filter((x): x is string => typeof x === 'string' && x.trim() !== '').map((x) => x.trim())));
}

export const ROTULO_DO_ESTADO_DA_PESQUISA: Record<EstadoDaPesquisa, string> = {
  reaproveitada_do_livro: 'Reaproveitada do Livro, sem chamada paga',
  paga: 'Paga: o Livro não cobria o assunto',
  nao_rodou: 'Não rodou',
  falhou: 'Falhou',
};

/** Um item do conhecimento que o texto do agente recebeu, em palavras. Prefixo que o painel não conhece fica como veio. */
export interface ConhecimentoEmPalavras { tipo: 'Fato do Livro' | 'Fato da operação' | 'Fonte da pesquisa' | 'Registro da operação' | null; chave: string }

export function conhecimentoEmPalavras(ref: string): ConhecimentoEmPalavras {
  const dois = /^(fato|fonte|registro):(.+)$/.exec(ref);
  if (!dois) return { tipo: null, chave: ref };
  const [, prefixo, chave] = dois;
  if (prefixo === 'fato' && chave!.startsWith('livro.')) return { tipo: 'Fato do Livro', chave: chave!.slice('livro.'.length) };
  if (prefixo === 'fato') return { tipo: 'Fato da operação', chave: chave! };
  if (prefixo === 'fonte') return { tipo: 'Fonte da pesquisa', chave: chave! };
  return { tipo: 'Registro da operação', chave: chave! };
}
