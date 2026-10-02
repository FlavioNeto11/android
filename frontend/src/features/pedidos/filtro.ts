import type { Autonomia, EstadoPedido, FiltroDePedidos, TipoDeGatilho } from '../../api/pedidos';
import { ESTADOS_DO_PEDIDO } from './modelo';

/**
 * Os filtros da lista de Pedidos, no link (ADR-062, item 4): os nomes são os da query de `GET /api/pedidos`, então o
 * link e a chamada são a mesma coisa (`#/pedidos?estado=ativo,pausado&q=preço`). Valor desconhecido no link é
 * ignorado (nunca vira 422 do backend).
 */
export const AUTONOMIAS: readonly Autonomia[] = ['observar', 'preparar', 'agir'];
export const TIPOS_DE_GATILHO: readonly TipoDeGatilho[] = ['agora', 'horario', 'recorrencia', 'evento', 'condicao', 'persona'];
export const ORDENS = ['atualizado', 'proxima', 'criado'] as const;
export type Ordem = (typeof ORDENS)[number];

export const ROTULO_DA_ORDEM: Record<Ordem, string> = {
  atualizado: 'Mexidos há pouco', proxima: 'Próxima execução', criado: 'Mais novos',
};

const ehEstado = (v: string): v is EstadoPedido => (ESTADOS_DO_PEDIDO as readonly string[]).includes(v);

/** Os estados do link, na ordem dada, sem repetição e sem o que não é estado. */
export function estadosDoLink(valor: string | undefined): EstadoPedido[] {
  const vistos = new Set<EstadoPedido>();
  for (const parte of (valor ?? '').split(',').map((p) => p.trim())) if (ehEstado(parte)) vistos.add(parte);
  return [...vistos];
}

export interface FiltroDoLink extends FiltroDePedidos {
  ordem: Ordem;
}

/** A query da rota → o filtro da chamada. `limit` e `cursor` não moram no link. */
export function filtroDoLink(query: Record<string, string>): FiltroDoLink {
  const estados = estadosDoLink(query.estado);
  const autonomia = AUTONOMIAS.find((a) => a === query.autonomia);
  const tipo = TIPOS_DE_GATILHO.find((t) => t === query.tipo);
  const ordem = ORDENS.find((o) => o === query.ordem) ?? 'atualizado';
  const q = (query.q ?? '').trim().slice(0, 80);
  return {
    ...(estados.length > 0 ? { estado: estados.join(',') } : {}),
    ...(autonomia ? { autonomia } : {}),
    ...(tipo ? { tipo } : {}),
    ...(query.profile_id ? { profile_id: query.profile_id } : {}),
    ...(q ? { q } : {}),
    ...(query.pede_atencao === '1' ? { pede_atencao: '1' as const } : {}),
    ordem,
  };
}

/** Algum filtro (além da ordem) esconde pedidos? */
export function temFiltro(f: FiltroDoLink): boolean {
  return !!(f.estado || f.autonomia || f.tipo || f.profile_id || f.q || f.pede_atencao);
}

/** A mesma chave para o mesmo filtro: a tela só relê quando ela muda. */
export function chaveDoFiltro(f: FiltroDoLink): string {
  return JSON.stringify([f.estado ?? '', f.autonomia ?? '', f.tipo ?? '', f.profile_id ?? '', f.q ?? '', f.pede_atencao ?? '', f.ordem]);
}
