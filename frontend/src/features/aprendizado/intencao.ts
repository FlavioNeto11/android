import { apiRequest } from '../../api/client';

/**
 * O rótulo de intenção (item 30.25): execuções que deram certo, com prova, sem que o sistema reconhecesse o pedido.
 * A pessoa diz qual habilidade do catálogo era aquela intenção, ou que nenhuma era. É uma pergunta CEGA: a tela não
 * mostra o palpite do sistema (o empate, a ordem da cadeia); os candidatos vêm em ordem alfabética.
 */

export const NENHUM = 'nenhum';

export interface Candidato {
  skill_id: string;
  nome: string;
}

export interface PerguntaDeIntencao {
  review_id: string;
  run_id: string;
  criado_em: string | null;
  terminou_em: string | null;
  app: string | null;
  app_nome: string | null;
  /** O comando da execução, lido na hora (nunca guardado no registro do rótulo). */
  comando: string | null;
  candidatos: Candidato[];
}

export interface PerguntasDeIntencao {
  itens: PerguntaDeIntencao[];
  total: number;
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);

function lerCandidato(v: unknown): Candidato | null {
  if (!isRecord(v)) return null;
  const id = str(v.skill_id);
  return id ? { skill_id: id, nome: str(v.nome) ?? id } : null;
}

/** Ordem alfabética pelo nome, sem repetição: a ordem não pode sugerir resposta. */
export function ordenarCandidatos(cs: readonly Candidato[]): Candidato[] {
  const vistos = new Set<string>();
  return [...cs]
    .filter((c) => (vistos.has(c.skill_id) ? false : (vistos.add(c.skill_id), true)))
    .sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR') || a.skill_id.localeCompare(b.skill_id));
}

function lerPergunta(v: unknown): PerguntaDeIntencao | null {
  if (!isRecord(v)) return null;
  const review = str(v.review_id);
  const run = str(v.run_id);
  if (!review || !run) return null;
  const candidatos = ordenarCandidatos((Array.isArray(v.candidatos) ? v.candidatos : [])
    .map(lerCandidato).filter((c): c is Candidato => c !== null));
  return {
    review_id: review, run_id: run, criado_em: str(v.criado_em), terminou_em: str(v.terminou_em), app: str(v.app),
    app_nome: str(v.app_nome), comando: str(v.comando), candidatos,
  };
}

export function lerPerguntas(raw: unknown): PerguntasDeIntencao {
  const r = isRecord(raw) ? raw : {};
  const itens = (Array.isArray(r.itens) ? r.itens : []).map(lerPergunta)
    .filter((p): p is PerguntaDeIntencao => p !== null && p.candidatos.length > 0);
  const total = typeof r.total === 'number' && Number.isFinite(r.total) ? Math.max(r.total, itens.length) : itens.length;
  return { itens, total };
}

/** O filtro da lista longa: nome ou id, sem acento e sem caixa. */
export function filtrarCandidatos(cs: readonly Candidato[], termo: string): Candidato[] {
  const norm = (s: string) => s.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
  const t = norm(termo.trim());
  return t ? cs.filter((c) => norm(c.nome).includes(t) || norm(c.skill_id).includes(t)) : [...cs];
}

/** Acima disto a lista ganha o campo de filtro. */
export const LIMITE_SEM_FILTRO = 8;

const enc = encodeURIComponent;

export const apiIntencao = {
  pendentes: async (signal?: AbortSignal): Promise<PerguntasDeIntencao> =>
    lerPerguntas(await apiRequest<unknown>('GET', '/aprendizado/intencao', { query: { limite: 50 }, signal })),
  /** 404 sem pergunta; 422 fora do catálogo gravado; 409 já respondida. */
  responder: (runId: string, escolha: string) =>
    apiRequest<unknown>('POST', `/aprendizado/execucao/${enc(runId)}/intencao`, { body: { escolha } }),
};
