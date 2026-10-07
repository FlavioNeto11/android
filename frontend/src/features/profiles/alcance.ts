/**
 * 31.186: o alcance de receitas e fluxos por persona, num arquivo só (contrato, leitor, exemplo e leitura): quando a rota do 31.181
 * estiver no ar, o exemplo deixa de ser usado e o resto fica. Contrato: adendo v1.107, `GET /api/aprendizado/alcance?app=<app_id>`
 * (só leitura, sem IA): por persona vinculada ao app, cada receita ativa do pacote e cada fluxo ligado ou candidato, com `pode` e,
 * quando não, o `motivo` de um vocabulário fechado (`presa_a_quem_ensinou`, `fora_do_escopo`, `fluxo_nao_ligado`).
 */
import { ApiError, apiRequest, toApiError } from '../../api/client';

export type TipoDoItem = 'receita' | 'fluxo';
export type MotivoConhecido = 'presa_a_quem_ensinou' | 'fora_do_escopo' | 'fluxo_nao_ligado';

export interface AlcanceNaPersona { pode: boolean; motivo: string | null }

export interface ItemDoAlcance {
  tipo: TipoDoItem;
  id: string;
  /** A etapa da receita ou o `ref_publico` do fluxo. */
  chave: string;
  origem: 'ensino' | 'execucao' | null;
  estado: string | null;
  reproducoesOk: number | null;
  reproducoesFalha: number | null;
  usos: number | null;
  nascidoDeProva: boolean | null;
  porPersona: Record<string, AlcanceNaPersona>;
}

export interface AlcanceDoApp {
  appId: string;
  pacote: string | null;
  geradoEm: string | null;
  /** As personas com vínculo ativo ao app (só delas o central calcula o alcance). */
  personas: { profileId: string; aparelhos: string[] }[];
  itens: ItemDoAlcance[];
  /** Os dados vêm do exemplo (a rota ainda não existe no central), não do parque. */
  exemplo: boolean;
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);

function lerItem(v: unknown): ItemDoAlcance | null {
  const o = registro(v);
  const tipo = o?.tipo === 'receita' || o?.tipo === 'fluxo' ? o.tipo : null;
  const id = o ? texto(o.id) : null;
  if (!o || !tipo || !id) return null;
  const por = Object.fromEntries(Object.entries(registro(o.por_persona) ?? {}).flatMap(([pid, x]) => {
    const r = registro(x);
    return r && typeof r.pode === 'boolean' ? [[pid, { pode: r.pode, motivo: r.pode ? null : texto(r.motivo) }] as const] : [];
  }));
  return {
    tipo, id, chave: texto(o.chave) ?? id, origem: o.origem === 'ensino' || o.origem === 'execucao' ? o.origem : null, estado: texto(o.estado),
    reproducoesOk: inteiro(o.reproducoes_ok), reproducoesFalha: inteiro(o.reproducoes_falha), usos: inteiro(o.usos),
    nascidoDeProva: typeof o.nascido_de_prova === 'boolean' ? o.nascido_de_prova : null, porPersona: por,
  };
}

/** `null` quando a resposta não é o alcance (sem `itens` nem `personas`): erro de leitura, não "nada a usar". */
export function lerAlcance(v: unknown, exemplo = false): AlcanceDoApp | null {
  const o = registro(v);
  if (!o || !Array.isArray(o.itens) || !Array.isArray(o.personas)) return null;
  const appId = texto(o.app_id);
  if (!appId) return null;
  return {
    appId, pacote: texto(o.pacote), geradoEm: texto(o.gerado_em),
    personas: o.personas.flatMap((p) => { const r = registro(p); const id = r ? texto(r.profile_id) : null; return r && id ? [{ profileId: id, aparelhos: Array.isArray(r.aparelhos) ? r.aparelhos.filter((a): a is string => typeof a === 'string') : [] }] : []; }),
    itens: o.itens.map(lerItem).filter((i): i is ItemDoAlcance => i !== null), exemplo,
  };
}

export const MOTIVO_EM_PALAVRAS: Record<MotivoConhecido, { titulo: string; explica: string }> = {
  presa_a_quem_ensinou: {
    titulo: 'Só vale para quem ensinou',
    explica: 'Foi ensinada e ainda não foi confirmada para ficar nem provada em uso real: outra persona não a usa até alguém confirmar.',
  },
  fora_do_escopo: { titulo: 'Fora do escopo do fluxo', explica: 'O fluxo vale para outras personas ou grupos; esta não está no escopo dele.' },
  fluxo_nao_ligado: { titulo: 'Fluxo ainda não ligado', explica: 'O fluxo é candidato: ninguém o ligou, então nenhuma persona o usa.' },
};

export const motivoConhecido = (m: string | null): m is MotivoConhecido => m !== null && m in MOTIVO_EM_PALAVRAS;

export interface AlcanceDaPersona {
  pode: ItemDoAlcance[];
  nao: { item: ItemDoAlcance; motivo: string | null }[];
  /** O que o central não disse sobre esta persona (item sem a chave dela): fica de fora, nunca como "pode". */
  semResposta: number;
  vinculada: boolean;
}

/** O que UMA persona pode usar e o que não, no alcance do app. Item sem resposta para ela não vira "pode" nem "não pode". */
export function alcanceDaPersona(a: AlcanceDoApp, profileId: string): AlcanceDaPersona {
  const pode: ItemDoAlcance[] = [];
  const nao: AlcanceDaPersona['nao'] = [];
  let semResposta = 0;
  for (const item of a.itens) {
    const r = item.porPersona[profileId];
    if (!r) semResposta += 1;
    else if (r.pode) pode.push(item);
    else nao.push({ item, motivo: r.motivo });
  }
  return { pode, nao, semResposta, vinculada: a.personas.some((p) => p.profileId === profileId) };
}

// ---- exemplo (até a rota existir) -----------------------------------------------------------------------------------------------------

/** Inventado: uma receita livre, uma presa a quem ensinou, um fluxo ligado, um fora do escopo e um candidato, para a persona pedida. */
export function alcanceDeExemplo(appId: string, profileId: string): AlcanceDoApp {
  const por = (pode: boolean, motivo: string | null) => ({ [profileId]: { pode, motivo } });
  return {
    appId, pacote: null, geradoEm: null, exemplo: true, personas: [{ profileId, aparelhos: ['android-01'] }],
    itens: [
      { tipo: 'receita', id: 'rec-exemplo-1', chave: 'abrir_busca', origem: 'execucao', estado: 'published', reproducoesOk: 8, reproducoesFalha: 0, usos: null, nascidoDeProva: null, porPersona: por(true, null) },
      { tipo: 'receita', id: 'rec-exemplo-2', chave: 'comentar_na_publicacao', origem: 'ensino', estado: 'published', reproducoesOk: 1, reproducoesFalha: 0, usos: null, nascidoDeProva: null, porPersona: por(false, 'presa_a_quem_ensinou') },
      { tipo: 'fluxo', id: 'flx-exemplo-1', chave: 'comentar-em-post-proprio', origem: 'execucao', estado: 'active', reproducoesOk: null, reproducoesFalha: null, usos: 12, nascidoDeProva: false, porPersona: por(true, null) },
      { tipo: 'fluxo', id: 'flx-exemplo-2', chave: 'responder-comentario', origem: 'ensino', estado: 'active', reproducoesOk: null, reproducoesFalha: null, usos: 3, nascidoDeProva: true, porPersona: por(false, 'fora_do_escopo') },
      { tipo: 'fluxo', id: 'flx-exemplo-3', chave: 'seguir-perfil', origem: 'execucao', estado: 'candidate', reproducoesOk: null, reproducoesFalha: null, usos: 0, nascidoDeProva: false, porPersona: por(false, 'fluxo_nao_ligado') },
    ],
  };
}

// ---- a leitura -------------------------------------------------------------------------------------------------------------------------

/** 404 de ROTA que não existe (o central anterior ao 31.181); o 404 de app cadastrado é `app_desconhecido`. */
const rotaAusente = (e: unknown): boolean => { const x = toApiError(e); return x.status === 404 && x.code !== 'app_desconhecido'; };

export const apiAlcance = {
  async doApp(appId: string, profileId: string, signal?: AbortSignal): Promise<AlcanceDoApp> {
    try {
      const lido = lerAlcance(await apiRequest<unknown>('GET', '/aprendizado/alcance', { query: { app: appId }, signal }));
      if (!lido) throw new ApiError(502, 'resposta_invalida', 'A resposta do alcance veio em formato inesperado.');
      return lido;
    } catch (e) {
      if (rotaAusente(e)) return alcanceDeExemplo(appId, profileId);
      throw e;
    }
  },
};
