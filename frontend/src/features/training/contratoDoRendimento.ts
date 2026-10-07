/**
 * 31.203: o rendimento de UMA sessão de ensino: o que ela gerou (fluxo, receitas, lições, pacotes vizinhos) e quanto disso foi usado, por
 * uso `{real, prova, simulada}`. Num arquivo só (contrato, leitor, exemplo e leitura): quando a rota existir no central, o exemplo deixa de
 * ser usado e o resto fica. Contrato: adendo v1.101, `GET /api/training/{id}/rendimento` (31.177; só leitura, sem IA; sessão desconhecida:
 * 404 `not_found`). A régua de uso: `simulada` é a execução simulada; `prova`, a prova de fluxo ou a chave `lote:`; `real`, o resto. Prova e
 * simulada NUNCA somam ao uso real; contagem ausente é "não informado", nunca zero.
 */
import { ApiError, apiRequest, toApiError } from '../../api/client';

export interface PorUso { real: number | null; prova: number | null; simulada: number | null }

export interface ReceitaDoRendimento {
  id: number | null;
  stepKey: string | null;
  app: string | null;
  status: string | null;
  /** Vale fora da persona que ensinou (30.81); `null` = não informado. */
  liberada: boolean | null;
  semIa: PorUso;
  caiuNaIa: PorUso;
  outras: PorUso;
  /** O US$ de IA das tentativas ainda guardadas (retenção); `null` = não informado. */
  usdDaIaNaRetencao: number | null;
}

export interface RendimentoDoEnsino {
  sessao: string | null;
  resumo: {
    receitas: number | null; receitasLiberadas: number | null; etapasSemIa: PorUso; execucoesDoFluxo: PorUso;
    licoes: number | null; vizinhos: number | null; usadoDeVerdade: boolean | null;
  };
  fluxo: { id: string | null; status: string | null; uses: number | null; nascidoDeProva: boolean | null; emUsoRealDesde: string | null } | null;
  execucoesDoFluxo: PorUso;
  receitas: ReceitaDoRendimento[];
  licoes: { id: string; estado: string | null; papel: string | null; texto: string | null }[];
  vizinhos: { app: string | null; pacote: string; etapasEmPlanosLivres: PorUso }[];
  /** Os dados vêm do exemplo (a rota ainda não existe no central), não do parque. */
  exemplo: boolean;
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const lista = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);
const usd = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);

const lerPorUso = (v: unknown): PorUso => {
  const o = registro(v);
  return { real: inteiro(o?.real), prova: inteiro(o?.prova), simulada: inteiro(o?.simulada) };
};

function lerReceita(v: unknown): ReceitaDoRendimento | null {
  const o = registro(v);
  if (!o) return null;
  return {
    id: inteiro(o.id), stepKey: texto(o.step_key), app: texto(o.app), status: texto(o.status), liberada: bool(o.liberada),
    semIa: lerPorUso(o.sem_ia), caiuNaIa: lerPorUso(o.caiu_na_ia), outras: lerPorUso(o.outras), usdDaIaNaRetencao: usd(o.usd_da_ia_na_retencao),
  };
}

/** `null` quando a resposta não é o rendimento (sem a lista de receitas): erro de leitura, nunca "a sessão não rendeu nada". */
export function lerRendimentoDoEnsino(v: unknown, exemplo = false): RendimentoDoEnsino | null {
  const o = registro(v);
  if (!o || !Array.isArray(o.receitas)) return null;
  const r = registro(o.resumo);
  const f = registro(o.fluxo);
  return {
    sessao: texto(o.sessao),
    resumo: {
      receitas: inteiro(r?.receitas), receitasLiberadas: inteiro(r?.receitas_liberadas), etapasSemIa: lerPorUso(r?.etapas_sem_ia),
      execucoesDoFluxo: lerPorUso(r?.execucoes_do_fluxo), licoes: inteiro(r?.licoes), vizinhos: inteiro(r?.vizinhos), usadoDeVerdade: bool(r?.usado_de_verdade),
    },
    fluxo: f ? { id: texto(f.id), status: texto(f.status), uses: inteiro(f.uses), nascidoDeProva: bool(f.nascido_de_prova), emUsoRealDesde: texto(f.em_uso_real_desde) } : null,
    execucoesDoFluxo: lerPorUso(o.execucoes_do_fluxo),
    receitas: o.receitas.map(lerReceita).filter((x): x is ReceitaDoRendimento => x !== null),
    licoes: lista(o.licoes).flatMap((l) => { const x = registro(l); const id = x ? texto(x.id) : null; return x && id ? [{ id, estado: texto(x.estado), papel: texto(x.papel), texto: texto(x.texto) }] : []; }),
    vizinhos: lista(o.vizinhos).flatMap((n) => { const x = registro(n); const p = x ? texto(x.pacote) : null; return x && p ? [{ app: texto(x.app), pacote: p, etapasEmPlanosLivres: lerPorUso(x.etapas_em_planos_livres) }] : []; }),
    exemplo,
  };
}

/** Inventado: uma sessão cujo fluxo só rodou em prova e simulação, com uma receita liberada e outra presa a quem ensinou. */
export function rendimentoDeExemplo(sessao: string): RendimentoDoEnsino {
  return {
    sessao, exemplo: true,
    resumo: { receitas: 2, receitasLiberadas: 1, etapasSemIa: { real: 0, prova: 3, simulada: 4 }, execucoesDoFluxo: { real: 0, prova: 2, simulada: 1 }, licoes: 1, vizinhos: 1, usadoDeVerdade: false },
    fluxo: { id: 'fluxo-exemplo', status: 'active', uses: 3, nascidoDeProva: true, emUsoRealDesde: null },
    execucoesDoFluxo: { real: 0, prova: 2, simulada: 1 },
    receitas: [
      { id: 1, stepKey: 'abrir_busca', app: 'com.exemplo.app', status: 'published', liberada: true, semIa: { real: 0, prova: 2, simulada: 3 }, caiuNaIa: { real: 0, prova: 0, simulada: 1 }, outras: { real: 0, prova: 0, simulada: 0 }, usdDaIaNaRetencao: 0.02 },
      { id: 2, stepKey: 'enviar', app: 'com.exemplo.app', status: 'published', liberada: false, semIa: { real: 0, prova: 1, simulada: 1 }, caiuNaIa: { real: 0, prova: 0, simulada: 0 }, outras: { real: 0, prova: 0, simulada: 0 }, usdDaIaNaRetencao: null },
    ],
    licoes: [{ id: 'li-exemplo', estado: 'candidate', papel: 'caminho_alternativo', texto: 'Se a busca vier vazia, abra o perfil pelo menu.' }],
    vizinhos: [{ app: 'exemplo', pacote: 'com.exemplo.vizinho', etapasEmPlanosLivres: { real: 0, prova: 0, simulada: 2 } }],
  };
}

/** 404 de ROTA que não existe (o central anterior ao 31.177); o 404 de sessão é `not_found`. */
const rotaAusente = (e: unknown): boolean => { const x = toApiError(e); return x.status === 404 && x.code !== 'not_found'; };

export const apiRendimentoDoEnsino = {
  async daSessao(id: string, signal?: AbortSignal): Promise<RendimentoDoEnsino> {
    try {
      const lido = lerRendimentoDoEnsino(await apiRequest<unknown>('GET', `/training/${encodeURIComponent(id)}/rendimento`, { signal }));
      if (!lido) throw new ApiError(502, 'resposta_invalida', 'A resposta do rendimento veio em formato inesperado.');
      return lido;
    } catch (e) {
      if (rotaAusente(e)) return rendimentoDeExemplo(id);
      throw e;
    }
  },
};
