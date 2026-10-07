/**
 * 31.196: o rendimento de uma receita no Livro, num arquivo só (contrato, leitor, exemplo e leitura): quando a rota existir, o exemplo
 * deixa de ser usado e o resto fica. Contrato: adendo v1.110, `GET /api/aprendizado/receitas/{id}/rendimento` (Aprendizado, 31.191;
 * só leitura, sem IA), lido do RASCUNHO da Aprendizado (o adendo ainda não foi publicado). Os nomes seguem o rendimento por sessão do
 * v1.101: contagens por uso `{real, prova, simulada}`, onde `simulada` é a execução simulada, `prova` a prova de fluxo e `real` o resto.
 * Prova e simulada NUNCA somam ao uso real. Contagem ausente é "não informado" (o central manda 0 quando mediu zero); os dois US$ do
 * custo vêm `null` sem referência de custo na retenção.
 */
import { ApiError, apiRequest, toApiError } from '../../api/client';

/** Contagens pela régua de uso. `null` = o central não disse (nunca zero). */
export interface PorUso { real: number | null; prova: number | null; simulada: number | null }

export interface RendimentoDaReceita {
  id: number | null;
  stepKey: string | null;
  app: string | null;
  status: string | null;
  origem: 'ensino' | 'execucao' | null;
  /** A sessão de ensino de onde a receita veio (`origem = ensino`). */
  sessao: string | null;
  /** Vale fora da persona que ensinou (30.81); `null` = não informado. */
  liberada: boolean | null;
  reproducoes: { ok: number | null; falha: number | null };
  /** Etapas que a receita conduziu e comprovou sem IA. */
  semIa: PorUso;
  /** A receita divergiu e a IA assumiu. */
  caiuNaIa: PorUso;
  outras: PorUso;
  /** O US$ de IA gasto nas tentativas dela, das chamadas ainda guardadas (retenção). */
  usdDaIaNaRetencao: number | null;
  /** `sem_ia.real` × o custo médio de IA de uma etapa com a mesma chave no mesmo app; `null` sem referência. */
  custoEvitadoUsd: number | null;
  custoMedioDaIaPorEtapaUsd: number | null;
  /** `null` = a receita nunca foi usada. */
  ultimoUsoEm: string | null;
  geradoEm: string | null;
  /** Os dados vêm do exemplo (a rota ainda não existe no central), não do parque. */
  exemplo: boolean;
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);
const usd = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

const lerPorUso = (v: unknown): PorUso => {
  const o = registro(v);
  return { real: inteiro(o?.real), prova: inteiro(o?.prova), simulada: inteiro(o?.simulada) };
};
export const temContagem = (u: PorUso): boolean => u.real !== null || u.prova !== null || u.simulada !== null;

/** `null` quando a resposta não é o rendimento (nenhuma das três contagens): erro de leitura, não "nada aconteceu". */
export function lerRendimento(v: unknown, exemplo = false): RendimentoDaReceita | null {
  const o = registro(v);
  if (!o) return null;
  const semIa = lerPorUso(o.sem_ia);
  const caiuNaIa = lerPorUso(o.caiu_na_ia);
  const outras = lerPorUso(o.outras);
  if (![semIa, caiuNaIa, outras].some(temContagem)) return null;
  const rep = registro(o.reproducoes);
  return {
    id: inteiro(o.id), stepKey: texto(o.step_key), app: texto(o.app), status: texto(o.status),
    origem: o.origem === 'ensino' || o.origem === 'execucao' ? o.origem : null, sessao: texto(o.sessao),
    liberada: typeof o.liberada === 'boolean' ? o.liberada : null,
    reproducoes: { ok: inteiro(rep?.ok), falha: inteiro(rep?.falha) },
    semIa, caiuNaIa, outras, usdDaIaNaRetencao: usd(o.usd_da_ia_na_retencao), custoEvitadoUsd: usd(o.custo_evitado_usd),
    custoMedioDaIaPorEtapaUsd: usd(o.custo_medio_ia_por_etapa_usd), ultimoUsoEm: texto(o.ultimo_uso_em), geradoEm: texto(o.gerado_em), exemplo,
  };
}

/** Inventado: uma receita ensinada, com uso real, prova e simulações, e o custo que a IA teria tido. */
export function rendimentoDeExemplo(ref: string): RendimentoDaReceita {
  return {
    id: Number.isFinite(Number(ref)) ? Number(ref) : null, stepKey: 'abrir_busca', app: 'com.exemplo.app', status: 'published', origem: 'ensino', sessao: 'trn-exemplo',
    liberada: true, reproducoes: { ok: 8, falha: 1 },
    semIa: { real: 10, prova: 3, simulada: 5 }, caiuNaIa: { real: 2, prova: 0, simulada: 1 }, outras: { real: 1, prova: 0, simulada: 0 },
    usdDaIaNaRetencao: 0.12, custoEvitadoUsd: 0.4, custoMedioDaIaPorEtapaUsd: 0.04, ultimoUsoEm: null, geradoEm: null, exemplo: true,
  };
}

/** 404 de ROTA que não existe (o central anterior ao 31.191); o 404 de receita é `receita_desconhecida`. */
const rotaAusente = (e: unknown): boolean => { const x = toApiError(e); return x.status === 404 && x.code !== 'receita_desconhecida'; };

export const apiRendimento = {
  async daReceita(ref: string, signal?: AbortSignal): Promise<RendimentoDaReceita> {
    try {
      const lido = lerRendimento(await apiRequest<unknown>('GET', `/aprendizado/receitas/${encodeURIComponent(ref)}/rendimento`, { signal }));
      if (!lido) throw new ApiError(502, 'resposta_invalida', 'A resposta do rendimento veio em formato inesperado.');
      return lido;
    } catch (e) {
      if (rotaAusente(e)) return rendimentoDeExemplo(ref);
      throw e;
    }
  },
};
