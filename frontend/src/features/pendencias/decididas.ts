import { ApiError, apiRequest } from '../../api/client';
import { formatDateTime } from '../../lib/time';

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
  /** O título do item do aprendizado ("Abrir a caixa de entrada"); `null` quando não há (e nas outras filas). */
  item_nome: string | null;
  /** A execução do objetivo ou da pergunta vencida; `null` no aprendizado. */
  run_id: string | null;
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

/**
 * O `desde` ISO do período escolhido (`null` = sem limite). O relógio entra por argumento, para o teste. "Hoje" é a
 * meia-noite LOCAL do dia (não agora − 24 h: às 15h isso traria a tarde de ontem sob o rótulo "Hoje").
 */
export function desdeDoPeriodo(p: PeriodoDecidido, agora: number): string | null {
  if (p === 'tudo') return null;
  if (p === 'hoje') {
    const meiaNoite = new Date(agora);
    meiaNoite.setHours(0, 0, 0, 0);
    return meiaNoite.toISOString();
  }
  return new Date(agora - 7 * 86_400_000).toISOString();
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
    id: r.id, fila, item_ref: texto(r.item_ref), item_nome: textoOuNulo(r.item_nome), run_id: textoOuNulo(r.run_id), regra: texto(r.regra), efeito: texto(r.efeito), fatos: fatosDe(r.fatos),
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

/**
 * Os fatos que a regra usou, em frases curtas em português. O estado não se repete: na confirmação (`confirmacao`, ou
 * `de` igual a `para`) o item não mudou de estado, então a frase é "segue publicado". Chave que a tela não conhece
 * aparece como `chave: valor`; o `texto` (já cortado na palavra pelo servidor) aparece sem rótulo.
 */
export function fatosEmPortugues(fatos: Fatos): string[] {
  const linhas: string[] = [];
  const segue = fatos.confirmacao === true || (fatos.de !== undefined && fatos.de === fatos.para);
  for (const [k, v] of Object.entries(fatos)) {
    if (k === 'kind' || k === 'confirmacao') continue;   // o tipo já está no título; a confirmação vira "segue"
    if (k === 'horas') linhas.push(`esperou ${v} h`);
    else if (k === 'desde') linhas.push(`parado desde ${formatDateTime(String(v))}`);
    else if (k === 'estado_final') linhas.push(`ficou ${rotuloDoEstado(v)}`);
    else if (k === 'para') linhas.push(segue ? `segue ${rotuloDoEstado(v)}` : `passou a ${rotuloDoEstado(v)}`);
    else if (k === 'de') { if (!segue) linhas.push(`estava ${rotuloDoEstado(v)}`); }
    else if (k === 'usos') linhas.push(`${v} uso(s)`);
    else if (k === 'texto') linhas.push(String(v));
    else linhas.push(`${k}: ${String(v)}`);
  }
  return linhas;
}

/** Quem desfez, como a pessoa fala: o identificador cru (`panel`, `telegram:dono`) é do servidor, não da tela. */
export function quemDesfez(por: string | null): string {
  if (!por) return '';
  if (por === 'panel') return 'por você, no painel';
  if (por === 'telegram:dono') return 'por você, pelo Telegram';
  if (por === 'plataforma') return 'pela plataforma';
  return `por ${por}`;
}

/**
 * O item do aprendizado que a decisão tocou: o tipo vem dos fatos (ou do prefixo do `item_ref`, `receita:180`) e a
 * referência é o resto. Sem `:`, não há tipo conhecido e a tela mostra o `item_ref` como veio.
 */
export function itemDoAprendizado(d: Pick<Decidida, 'item_ref' | 'fatos'>): { kind: string | null; ref: string } {
  const i = d.item_ref.indexOf(':');
  const doPrefixo = i > 0 ? d.item_ref.slice(0, i) : null;
  const kind = typeof d.fatos.kind === 'string' && d.fatos.kind ? d.fatos.kind : doPrefixo;
  const ref = kind && d.item_ref.startsWith(`${kind}:`) ? d.item_ref.slice(kind.length + 1) : d.item_ref;
  return { kind, ref };
}

/** As decisões do vencimento (31.43) são rotina em volume: viram um grupo por fila e regra, em vez de um cartão cada. */
export const eDoVencimento = (d: Pick<Decidida, 'fila' | 'regra'>): boolean =>
  (d.fila === 'objetivo' || d.fila === 'pergunta') && d.regra.startsWith('31.43');

/** "21 objetivos encerrados por vencimento" (plural certo; "1 pergunta encerrada…"). */
export function tituloDoGrupo(fila: FilaDecidida, n: number): string {
  const feminino = fila === 'pergunta';
  const nome = feminino ? (n === 1 ? 'pergunta' : 'perguntas') : (n === 1 ? 'objetivo' : 'objetivos');
  const encerrado = `encerrad${feminino ? 'a' : 'o'}${n === 1 ? '' : 's'}`;
  return `${n} ${nome} ${encerrado} por vencimento`;
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

/** O limite do motivo na rota do desfazer (`POST /decisoes-automaticas/{id}/desfazer`). */
export const MOTIVO_DO_DESFAZER_MAX = 300;

/**
 * A mensagem de uma recusa do desfazer, para a linha do item (o servidor já escreve em português nas recusas de
 * negócio). Um 422 é a validação do corpo (o motivo longo demais) e traz o texto cru do pydantic: vira frase nossa.
 */
export function falhaDoDesfazer(e: unknown): string {
  if (e instanceof ApiError && e.status === 422) return `O motivo tem no máximo ${MOTIVO_DO_DESFAZER_MAX} caracteres.`;
  if (e instanceof ApiError) return e.message || 'Não foi possível desfazer.';
  return 'Não foi possível desfazer.';
}
