import { CircleCheck, CircleSlash, Clock, Hourglass, Loader2 } from 'lucide-react';
import type { StatusMeta } from '../../lib/status';
import { isLivroKind, rotuloDoKind } from './model';

/**
 * Os pedidos de validação automática no painel (30.38 b; rota `GET /api/aprendizado/validacoes`, adendo v1.02). Os
 * tipos espelham `presentation/validacoes.py`; a leitura é tolerante (um campo ausente não derruba a aba). O motivo
 * vem em código e em texto (`motivo_humano`, montado no backend): a tela mostra o texto e guarda o código no `title`.
 */

export type EstadoDaValidacao = 'pendente' | 'rodando' | 'feita' | 'recusada' | 'expirada';

/** A ordem das fichas: o que está andando primeiro, depois o que espera, depois o que fechou. */
export const ESTADOS_DA_VALIDACAO: readonly EstadoDaValidacao[] = ['rodando', 'pendente', 'feita', 'recusada', 'expirada'];

export const META_DA_VALIDACAO: Record<EstadoDaValidacao, StatusMeta> = {
  rodando: { label: 'Rodando', tone: 'info', icon: Loader2, spin: true, description: 'A execução de validação está em curso.' },
  pendente: { label: 'Pendente', tone: 'warning', icon: Clock, description: 'Esperando a vez: aparelho ocioso, central quieto e verba na janela.' },
  feita: { label: 'Feita', tone: 'success', icon: CircleCheck, description: 'A execução deixou evidência a favor; o curador revê o item.' },
  recusada: { label: 'Recusada', tone: 'neutral', icon: CircleSlash, description: 'Não se validou, ou rodou sem provar: o motivo diz por quê.' },
  expirada: { label: 'Expirada', tone: 'muted', icon: Hourglass, description: 'Passou do prazo sem rodar; a volta seguinte pede de novo se ainda faltar.' },
};

export const isEstadoDaValidacao = (v: unknown): v is EstadoDaValidacao =>
  typeof v === 'string' && (ESTADOS_DA_VALIDACAO as readonly string[]).includes(v);

export interface PedidoDeValidacao {
  id: string;
  estado: EstadoDaValidacao;
  motivo: string | null;
  motivo_humano: string | null;
  item_ref: string;
  item_kind: string | null;
  app: string | null;
  app_nome: string | null;
  grupo: string | null;
  run_id: string | null;
  run_origem: string | null;
  aparelho: string | null;
  usd: number;
  teto_usd: number | null;
  created_at: string;
  feito_em: string | null;
  expira_em: string | null;
  comando: string;
}

export interface ListaDeValidacoes {
  itens: PedidoDeValidacao[];
  contagem: Record<EstadoDaValidacao, number>;
  total: number;
  /** O modo do despachante agora: `off` = pausado (nada nasce e nada roda). `null` em backend que não o manda. */
  modo: string | null;
}

const texto = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const numero = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function lerPedido(raw: unknown): PedidoDeValidacao | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = texto(r.id);
  if (!id || !isEstadoDaValidacao(r.estado)) return null;
  return {
    id, estado: r.estado, motivo: texto(r.motivo), motivo_humano: texto(r.motivo_humano),
    item_ref: texto(r.item_ref) ?? '', item_kind: texto(r.item_kind), app: texto(r.app), app_nome: texto(r.app_nome),
    grupo: texto(r.grupo), run_id: texto(r.run_id), run_origem: texto(r.run_origem), aparelho: texto(r.aparelho),
    usd: numero(r.usd) ?? 0, teto_usd: numero(r.teto_usd), created_at: texto(r.created_at) ?? '',
    feito_em: texto(r.feito_em), expira_em: texto(r.expira_em), comando: typeof r.comando === 'string' ? r.comando : '',
  };
}

export function lerListaDeValidacoes(raw: unknown): ListaDeValidacoes {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const itens = Array.isArray(r.itens) ? r.itens.map(lerPedido).filter((p): p is PedidoDeValidacao => p !== null) : [];
  const c = (r.contagem && typeof r.contagem === 'object' ? r.contagem : {}) as Record<string, unknown>;
  const contagem = Object.fromEntries(ESTADOS_DA_VALIDACAO.map((e) => [e, numero(c[e]) ?? 0])) as Record<EstadoDaValidacao, number>;
  const total = numero(r.total) ?? Object.values(contagem).reduce((a, b) => a + b, 0);
  return { itens, contagem, total, modo: texto(r.modo) };
}

/** O tipo do item como a pessoa lê ("Receita", "Fluxo"); o `item_ref` fica no `title`. O `item_kind` manda; sem ele, o
 *  prefixo do `item_ref` (`receita:7`). */
export function rotuloDoItem(p: Pick<PedidoDeValidacao, 'item_kind' | 'item_ref'>): string {
  const prefixo = p.item_ref.includes(':') ? p.item_ref.slice(0, p.item_ref.indexOf(':')) : null;
  const kind = isLivroKind(p.item_kind) ? p.item_kind : isLivroKind(prefixo) ? prefixo : null;
  return kind ? rotuloDoKind(kind) : 'Item do aprendizado';
}

/** Os motivos de recusa em que a execução rodou mas a prova não vale (30.42): "inválida", nunca "contra" nem "a favor". */
const MOTIVOS_DE_PROVA_INVALIDA: readonly string[] = ['efeito_repetido', 'ponto_de_partida', 'ator_sem_acao'];

/**
 * 30.43: o veredito do ITEM numa execução de validação, em palavras curtas. A execução pode terminar "concluída" e o
 * item levar evidência contra (caso da e1b7d0); por isso a tela da execução mostra isto no lugar de "sucesso
 * comprovado". `null` = nenhum pedido achado para a execução (a legenda de sempre).
 */
export function vereditoDoPedido(p: Pick<PedidoDeValidacao, 'estado' | 'motivo' | 'motivo_humano'> | null): string | null {
  if (!p) return null;
  if (p.estado === 'feita') return 'a favor';
  if (p.estado === 'pendente' || p.estado === 'rodando') return 'em andamento';
  const porque = p.motivo_humano ?? p.motivo;
  if (p.motivo === 'evidencia_contra') return 'contra';
  if (p.motivo === 'divergencia_de_forma') return 'só a forma (não conta)';
  if (p.motivo && MOTIVOS_DE_PROVA_INVALIDA.includes(p.motivo)) return `inválida (${porque})`;
  return `sem evidência (${porque ?? 'sem motivo registrado'})`;
}

/**
 * 30.43: a leitura do histórico de um item. O MESMO motivo de recusa tem dois sentidos: com execução (`run_id`) o
 * pedido rodou e foi reclassificado depois ("Rodou; depois: …"); sem ela foi recusado ao despachar, sem gasto
 * ("Não rodou: …"). Sem motivo, vale o rótulo do estado.
 */
export function leituraDoPedido(p: Pick<PedidoDeValidacao, 'estado' | 'motivo' | 'motivo_humano' | 'run_id'>): string {
  const porque = p.motivo_humano ?? p.motivo;
  if (!porque) return META_DA_VALIDACAO[p.estado].label;
  if (p.estado === 'recusada' || p.estado === 'expirada') return `${p.run_id ? 'Rodou; depois' : 'Não rodou'}: ${porque}`;
  return porque;
}
