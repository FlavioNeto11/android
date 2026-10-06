/**
 * O aprendizado de uma operação nas 10 perguntas do dono (adendo v1.96, `GET /api/operacoes/{id}/aprendizado`): só leitura.
 * O leitor é tolerante e nunca inventa: pergunta que não veio fica sem itens e dita como "não veio"; a rota ausente (404) é
 * "não disponível", com o motivo, nunca "nada aprendido".
 */
export const PERGUNTAS_DO_DONO = [
  'plataforma_aprendeu', 'persona_aprendeu', 'do_app', 'do_processo', 'conhecimento_geral', 'fontes_externas',
  'fontes_que_sustentam', 'reutilizavel', 'revisar_ou_descartar', 'falhas_que_geraram_aprendizado',
] as const;
export type ChaveDaPergunta = (typeof PERGUNTAS_DO_DONO)[number];

/** Títulos de reserva, só para a pergunta que o backend não titulou (o título dele vale). */
const TITULO_DE_RESERVA: Record<ChaveDaPergunta, string> = {
  plataforma_aprendeu: 'O que a plataforma aprendeu', persona_aprendeu: 'O que a persona aprendeu', do_app: 'O que veio do app',
  do_processo: 'O que veio do processo', conhecimento_geral: 'O que virou conhecimento geral', fontes_externas: 'Que fontes externas entraram',
  fontes_que_sustentam: 'Que fontes sustentam o que se aprendeu', reutilizavel: 'O que já pode ser reutilizado',
  revisar_ou_descartar: 'O que revisar ou descartar', falhas_que_geraram_aprendizado: 'Que falhas geraram aprendizado',
};

export interface ItemAprendido {
  ref: string;
  tipo: string | null;
  escopo: string | null;
  resumo: string | null;
  confianca: 'confirmado' | 'hipotese' | null;
  /** `null` = da operação inteira; senão o `profile_id` da persona (o relatório troca pelo rótulo). */
  persona: string | null;
  motivo: string | null;
  inferida: boolean;
  evidencias: number;
  /** As evidências efetivas a favor e as contrárias (as que valem; a neutralizada não conta). `null` = o backend não disse. */
  a_favor: number | null;
  contra: number | null;
  /** Só em `fontes_que_sustentam`: as fontes que sustentam o item. */
  fontes: { ref: string; resumo: string | null }[];
}

export interface PerguntaDoDono { chave: ChaveDaPergunta; titulo: string; itens: ItemAprendido[]; veio: boolean }

/** A etapa cujo `conhecimento_ids` não foi gravado: o texto saiu, mas o que ele recebeu da operação não ficou registrado. */
export interface AvisoDaOperacao { run_id: string | null; step_id: string | null; aviso: string }

export interface AprendizadoDaOperacao {
  gerado_em: string | null;
  avisos: AvisoDaOperacao[];
  perguntas: PerguntaDoDono[];
  nao_coberto: { chave: string; motivo: string }[];
}

/** O que o relatório mostra quando o aprendizado não pôde ser lido: o motivo em português, nunca "nada aprendido". */
export type LeituraDoAprendizado =
  | { situacao: 'lido'; aprendizado: AprendizadoDaOperacao }
  | { situacao: 'indisponivel'; motivo: string };

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);

const contagem = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);

function lerItem(v: unknown): ItemAprendido | null {
  const o = registro(v);
  const ref = o && texto(o.ref);
  if (!o || !ref) return null;
  const confianca = o.confianca === 'confirmado' || o.confianca === 'hipotese' ? o.confianca : null;
  return {
    ref, tipo: texto(o.tipo), escopo: texto(o.escopo), resumo: texto(o.resumo), confianca, persona: texto(o.persona), motivo: texto(o.motivo),
    inferida: o.inferida === true, evidencias: Array.isArray(o.evidencia) ? o.evidencia.length : 0,
    a_favor: contagem(o.a_favor), contra: contagem(o.contra),
    fontes: (Array.isArray(o.fontes) ? o.fontes : []).flatMap((f) => {
      const x = registro(f);
      const r = x && texto(x.ref);
      return x && r ? [{ ref: r, resumo: texto(x.resumo) }] : [];
    }),
  };
}

export function lerAprendizado(v: unknown): AprendizadoDaOperacao | null {
  const o = registro(v);
  if (!o || !Array.isArray(o.perguntas)) return null;
  const porChave = new Map<string, Record<string, unknown>>();
  for (const p of o.perguntas) {
    const r = registro(p);
    const chave = r && texto(r.chave);
    if (r && chave) porChave.set(chave, r);
  }
  // As 10, na ordem do dono: a que o backend não mandou aparece como "não veio", nunca como "sem nada".
  const perguntas = PERGUNTAS_DO_DONO.map((chave): PerguntaDoDono => {
    const p = porChave.get(chave);
    return {
      chave, titulo: (p && texto(p.titulo)) ?? TITULO_DE_RESERVA[chave], veio: p !== undefined,
      itens: (p && Array.isArray(p.itens) ? p.itens : []).map(lerItem).filter((i): i is ItemAprendido => i !== null),
    };
  });
  const nao_coberto = (Array.isArray(o.nao_coberto) ? o.nao_coberto : []).flatMap((n) => {
    const r = registro(n);
    const chave = r && texto(r.chave);
    const motivo = r && texto(r.motivo);
    return r && chave && motivo ? [{ chave, motivo }] : [];
  });
  const avisos = (Array.isArray(o.avisos) ? o.avisos : []).flatMap((a) => {
    const r = registro(a);
    const aviso = r && texto(r.aviso);
    return r && aviso ? [{ run_id: texto(r.run_id), step_id: texto(r.step_id), aviso }] : [];
  });
  return { gerado_em: texto(o.gerado_em), avisos, perguntas, nao_coberto };
}

export interface LicoesDaOperacao<I extends Pick<ItemAprendido, 'ref' | 'a_favor' | 'contra'> = ItemAprendido> { reforcadas: I[]; contestadas: I[] }

const maior = (a: number | null, b: number | null): number | null => (a === null ? b : b === null ? a : Math.max(a, b));

/**
 * As lições (`licao:<id>`) que aparecem em qualquer das 10 perguntas, uma vez cada. Contestada = alguma evidência efetiva
 * contra; reforçada = ao menos uma a favor e nenhuma contra. A lição sem contagem nenhuma não entra em nenhuma das duas
 * (a contagem que o backend não disse não vira zero): ela continua na pergunta onde apareceu.
 */
export function licoesDaOperacao<I extends Pick<ItemAprendido, 'ref' | 'a_favor' | 'contra'>>(perguntas: readonly { itens: readonly I[] }[]): LicoesDaOperacao<I> {
  const porRef = new Map<string, I>();
  for (const i of perguntas.flatMap((p) => p.itens)) {
    if (!i.ref.startsWith('licao:')) continue;
    const antes = porRef.get(i.ref);
    porRef.set(i.ref, antes ? { ...antes, a_favor: maior(antes.a_favor, i.a_favor), contra: maior(antes.contra, i.contra) } : i);
  }
  const todas = [...porRef.values()];
  return {
    contestadas: todas.filter((i) => (i.contra ?? 0) > 0),
    reforcadas: todas.filter((i) => (i.contra ?? 0) === 0 && (i.a_favor ?? 0) > 0),
  };
}
