import type { EntradaDoLivro } from './model';

/**
 * 31.270: a receita CANDIDATA legível. Uma candidata só vira ativa depois de concordar com a IA em N execuções seguidas (a divergência
 * zera a prova) e, no fim, pode ocupar o lugar de uma ativa da mesma chave; antes, a tela só dizia "Candidato" e a prova ficava no banco
 * (a receita 222 ficou com 0 de 2 e a sombra a trocava sem ninguém ver). Aqui se lê, com tolerância, o que o central diz:
 *
 * - `prova_da_candidata` (campo aditivo da linha do Livro, contrato proposto em `portal-para-jev-receita-candidata.md`): as concordâncias
 *   seguidas, as necessárias, a última consulta (quando e o resultado) e a ativa que ela quer substituir;
 * - sem ele (backend atual), só a contagem que a linha já traz em `detail` ("sombra 0/0" = concordâncias seguidas / execuções);
 * - sem nenhum dos dois, nada se afirma.
 *
 * "Não informado" nunca vira zero: `necessarias`, `consulta` e `substitui` ausentes ficam `null` e a tela não os mostra.
 */

export type ResultadoDaConsulta = 'concordou' | 'divergiu' | 'outro_escopo' | 'quarentena';
export const RESULTADOS_DA_CONSULTA: readonly ResultadoDaConsulta[] = ['concordou', 'divergiu', 'outro_escopo', 'quarentena'];

export interface ConsultaDaCandidata {
  /** ISO da última consulta; `null` = o central não disse quando. */
  em: string | null;
  /** Um dos quatro resultados do contrato, ou o código cru de um resultado novo (mostrado como veio, sem inventar rótulo). */
  resultado: ResultadoDaConsulta | string;
}

export interface SubstituidaPelaCandidata {
  ref: string;
  versao: number | null;
  estado: string | null;
}

export interface ProvaDaCandidata {
  /** Concordâncias seguidas com a IA (a divergência zera). */
  concordancias: number;
  /** Quantas ela precisa para ser promovida (`ai.recipes_promote_after`); `null` = o central não disse. */
  necessarias: number | null;
  consulta: ConsultaDaCandidata | null;
  substitui: SubstituidaPelaCandidata | null;
  /** De onde veio a leitura: o campo do contrato, ou só a contagem da linha (backend anterior). */
  fonte: 'contrato' | 'detalhe';
}

const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null);
const objeto = (v: unknown): Record<string, unknown> | null => (v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

function lerConsulta(v: unknown): ConsultaDaCandidata | null {
  const o = objeto(v);
  const resultado = o ? texto(o.resultado) : null;
  if (!o || !resultado) return null;                       // sem resultado não há consulta a mostrar
  const em = texto(o.em);
  return { em: em && Number.isFinite(Date.parse(em)) ? em : null, resultado };
}

function lerSubstitui(v: unknown): SubstituidaPelaCandidata | null {
  const o = objeto(v);
  const ref = o ? (texto(o.ref) ?? (typeof o.ref === 'number' ? String(o.ref) : null)) : null;
  if (!o || !ref) return null;
  return { ref, versao: inteiro(o.versao), estado: texto(o.estado) };
}

/** A prova da receita candidata desta linha; `null` quando não é receita candidata ou o central não diz nada dela. */
export function lerProvaDaCandidata(e: Pick<EntradaDoLivro, 'kind' | 'state' | 'detail'> & { prova_da_candidata?: unknown }): ProvaDaCandidata | null {
  if (e.kind !== 'receita' || e.state !== 'candidate') return null;
  const o = objeto(e.prova_da_candidata);
  const concordancias = o ? inteiro(o.concordancias) : null;
  if (o && concordancias !== null) {
    return { concordancias, necessarias: inteiro(o.necessarias), consulta: lerConsulta(o.ultima_consulta), substitui: lerSubstitui(o.substitui), fonte: 'contrato' };
  }
  const sombra = /^sombra (\d+)\/(\d+)$/.exec(e.detail ?? '');
  if (!sombra) return null;
  return { concordancias: Number(sombra[1]), necessarias: null, consulta: null, substitui: null, fonte: 'detalhe' };
}

/** "0 de 2 concordâncias seguidas com a IA" (ou, sem o total, "0 concordâncias seguidas com a IA"). */
export function textoDaProva(p: ProvaDaCandidata): string {
  const n = p.concordancias;
  if (p.necessarias === null) return `${n} ${n === 1 ? 'concordância seguida' : 'concordâncias seguidas'} com a IA`;
  return `${n} de ${p.necessarias} concordâncias seguidas com a IA`;
}

const ROTULO_DO_RESULTADO: Record<ResultadoDaConsulta, string> = {
  concordou: 'concordou com a IA',
  divergiu: 'divergiu da IA (a prova recomeça do zero)',
  outro_escopo: 'a etapa era de outro escopo (outra versão, assinatura ou variante do app)',
  quarentena: 'a receita está em quarentena',
};

export function rotuloDoResultado(r: string): string {
  return (ROTULO_DO_RESULTADO as Record<string, string>)[r] ?? r;
}

/** A ativa que a candidata quer substituir, em uma frase; só o que o central disse. */
export function textoDaSubstituida(s: SubstituidaPelaCandidata): string {
  return `Quando passar, assume o lugar da receita ${s.ref}${s.versao !== null ? ` (v${s.versao})` : ''}${s.estado === 'active' ? ', hoje ativa' : s.estado ? `, hoje ${s.estado}` : ''}.`;
}
