import { ApiError, apiRequest } from '../../api/client';

/**
 * "Decidido sozinho" (item 28.25): o que a plataforma decidiu no lugar do dono, com o desfazer. Leitura tolerante: um
 * campo que o servidor não mandou vira o valor neutro, nunca derruba a aba. Contrato em `docs/api-contract.md` (v1.22).
 */
export type FilaDecidida = 'pergunta' | 'objetivo' | 'aprendizado' | 'pedido';
export type Fatos = Record<string, string | number | boolean>;

export interface Decidida {
  id: number;
  fila: FilaDecidida;
  item_ref: string;
  regra: string;
  efeito: string;
  fatos: Fatos;
  decidida_em: string;
  desfeita: boolean;
  desfeita_em: string | null;
  desfeita_por: string | null;
  motivo_do_desfazer: string | null;
  pode_desfazer: boolean;
  /** O verbo do botão: "Desligar" no aprendizado, "Desfazer" nas outras. */
  acao_do_desfazer: string;
  /** Em português; é o que a tela mostra no lugar do botão. */
  por_que_nao: string | null;
  prazo_ate: string;
}

export interface ListaDecidida { itens: Decidida[]; regras: string[]; desfazer_dias: number }

export const ROTULO_DA_FILA: Record<FilaDecidida, string> = {
  pergunta: 'Pergunta', objetivo: 'Objetivo', aprendizado: 'Aprendizado', pedido: 'Pedido',
};
const FILAS = Object.keys(ROTULO_DA_FILA) as FilaDecidida[];

export type PeriodoDecidido = 'hoje' | '7d' | 'tudo';
export const ROTULO_DO_PERIODO: Record<PeriodoDecidido, string> = { hoje: 'Hoje', '7d': '7 dias', tudo: 'Tudo' };

/** O `desde` ISO do período escolhido (`null` = sem limite). O relógio entra por argumento, para o teste. */
export function desdeDoPeriodo(p: PeriodoDecidido, agora: number): string | null {
  if (p === 'tudo') return null;
  return new Date(agora - (p === 'hoje' ? 1 : 7) * 86_400_000).toISOString();
}

const texto = (v: unknown, padrao = ''): string => (typeof v === 'string' ? v : padrao);
const textoOuNulo = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

function fatosDe(v: unknown): Fatos {
  const saida: Fatos = {};
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    for (const [k, valor] of Object.entries(v)) {
      if (typeof valor === 'string' || typeof valor === 'number' || typeof valor === 'boolean') saida[k] = valor;
    }
  }
  return saida;
}

export function lerDecidida(bruto: unknown): Decidida | null {
  if (!bruto || typeof bruto !== 'object') return null;
  const r = bruto as Record<string, unknown>;
  const fila = FILAS.find((f) => f === r.fila);
  if (typeof r.id !== 'number' || !fila) return null;
  return {
    id: r.id, fila, item_ref: texto(r.item_ref), regra: texto(r.regra), efeito: texto(r.efeito), fatos: fatosDe(r.fatos),
    decidida_em: texto(r.decidida_em), desfeita: r.desfeita === true, desfeita_em: textoOuNulo(r.desfeita_em),
    desfeita_por: textoOuNulo(r.desfeita_por), motivo_do_desfazer: textoOuNulo(r.motivo_do_desfazer),
    pode_desfazer: r.pode_desfazer === true, acao_do_desfazer: texto(r.acao_do_desfazer, 'Desfazer') || 'Desfazer',
    por_que_nao: textoOuNulo(r.por_que_nao), prazo_ate: texto(r.prazo_ate),
  };
}

export function lerListaDecidida(bruto: unknown): ListaDecidida {
  const r = (bruto && typeof bruto === 'object' ? bruto : {}) as Record<string, unknown>;
  const itens = (Array.isArray(r.itens) ? r.itens : []).map(lerDecidida).filter((d): d is Decidida => d !== null);
  return {
    itens, regras: (Array.isArray(r.regras) ? r.regras : []).filter((x): x is string => typeof x === 'string'),
    desfazer_dias: typeof r.desfazer_dias === 'number' ? r.desfazer_dias : 7,
  };
}

const ESTADO: Record<string, string> = {
  published: 'publicado', validated: 'validado', disabled: 'desligado', deprecated: 'aposentado', candidate: 'em prova',
  cancelled: 'cancelado', failed: 'com falha', needs_input: 'esperando resposta', waiting_user: 'esperando você',
};
const rotuloDoEstado = (v: string | number | boolean): string => ESTADO[String(v)] ?? String(v);

/** Os fatos que a regra usou, em frases curtas em português. Chave que a tela não conhece aparece como `chave: valor`. */
export function fatosEmPortugues(fatos: Fatos): string[] {
  const linhas: string[] = [];
  for (const [k, v] of Object.entries(fatos)) {
    if (k === 'kind') continue;                      // o tipo já está no efeito
    if (k === 'horas') linhas.push(`esperou ${v} h`);
    else if (k === 'desde') linhas.push(`parado desde ${String(v).slice(0, 16).replace('T', ' ')} (UTC)`);
    else if (k === 'estado_final') linhas.push(`ficou ${rotuloDoEstado(v)}`);
    else if (k === 'para') linhas.push(`passou a ${rotuloDoEstado(v)}`);
    else if (k === 'de') linhas.push(`estava ${rotuloDoEstado(v)}`);
    else if (k === 'usos') linhas.push(`${v} uso(s)`);
    else linhas.push(`${k}: ${String(v)}`);
  }
  return linhas;
}

/** O porquê da decisão, em português: a regra que a tomou. Regras de fábrica têm frase; as outras aparecem como são. */
export function motivoDaRegra(regra: string): string {
  if (regra.startsWith('31.43-pergunta')) return 'Pergunta sem resposta por tempo demais';
  if (regra.startsWith('31.43-objetivo')) return 'Objetivo esperando uma execução que já terminou';
  if (regra.startsWith('auto:qa_para_aprovar')) return 'Aprovação automática do que esperava você';
  if (regra.startsWith('auto:qa_revisar')) return 'Confirmação automática do que estava em revisão';
  return `Regra ${regra}`;
}

export interface FiltroDecidido { regra?: string; desde?: string | null; desfeitas?: 'todas' | 'nao' | 'sim' }

export const apiDecididas = {
  listar: async (f: FiltroDecidido = {}, signal?: AbortSignal): Promise<ListaDecidida> => {
    const q = new URLSearchParams();
    if (f.regra) q.set('regra', f.regra);
    if (f.desde) q.set('desde', f.desde);
    if (f.desfeitas && f.desfeitas !== 'todas') q.set('desfeitas', f.desfeitas);
    const s = q.toString();
    return lerListaDecidida(await apiRequest<unknown>('GET', `/decisoes-automaticas${s ? `?${s}` : ''}`, { signal }));
  },
  desfazer: (id: number, motivo: string) =>
    apiRequest<unknown>('POST', `/decisoes-automaticas/${id}/desfazer`, { body: { confirmar: true, ...(motivo ? { motivo } : {}) } }),
};

/** A mensagem de uma recusa do desfazer, para a linha do item (o servidor já escreve em português). */
export function falhaDoDesfazer(e: unknown): string {
  if (e instanceof ApiError) return e.message || 'Não foi possível desfazer.';
  return 'Não foi possível desfazer.';
}
