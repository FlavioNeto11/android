import { apiRequest } from '../../api/client';
import type {
  FiltroDePedidos, ListaDeAvisos, ListaDeOcorrencias, ListaDePedidos, PedidoCancelado, PedidoCriacao,
  PedidoCriado, PedidoDetalhe, PedidoEdicao, PedidoEdicaoResultado, PedidoCorpo, PedidoPrevia, PedidoRetomado,
  PedidoSemMudanca, PedidoView,
} from '../../api/pedidos';
import type { RunSummary } from '../../api/types';

/**
 * As rotas de `/api/pedidos` (adendo v0.45). Ficam aqui, e não no objeto `api` do cliente, como as do aprendizado: são
 * de UM contexto. Filtro ausente vai como `undefined` (o `buildUrl` o omite), nunca como texto vazio.
 */
const enc = encodeURIComponent;

export const apiPedidos = {
  /** Sem efeito, sem gravação e SEM chamada de IA: devolve o que a criação decidiria (ADR-044). */
  previa: (corpo: PedidoCorpo & { proximas?: number }, signal?: AbortSignal) =>
    apiRequest<PedidoPrevia>('POST', '/pedidos/previa', { body: corpo, signal }),
  /** 201 (novo) ou 200 com `deduplicated: true` (a chave já existia). Com `confirmacao` o pedido nasce ativo. */
  criar: (corpo: PedidoCriacao) => apiRequest<PedidoCriado>('POST', '/pedidos', { body: corpo, timeoutMs: 60_000 }),
  /** Com termo de busca (`q`, texto livre), vai por `POST /pedidos/busca` com o filtro no corpo (29.26): query string
   *  vira linha de log de acesso. Sem termo, segue o GET com filtros curtos. */
  listar: (f: FiltroDePedidos = {}, signal?: AbortSignal) =>
    f.q
      ? apiRequest<ListaDePedidos>('POST', '/pedidos/busca', {
        body: { q: f.q, estado: f.estado || undefined, autonomia: f.autonomia, tipo: f.tipo,
                profile_id: f.profile_id || undefined, pede_atencao: f.pede_atencao ? true : undefined, ordem: f.ordem,
                limit: f.limit, cursor: f.cursor },
        signal,
      })
      : apiRequest<ListaDePedidos>('GET', '/pedidos', {
        query: { estado: f.estado || undefined, autonomia: f.autonomia, tipo: f.tipo, profile_id: f.profile_id || undefined,
                 pede_atencao: f.pede_atencao, ordem: f.ordem, limit: f.limit, cursor: f.cursor },
        signal,
      }),
  detalhe: (id: string, signal?: AbortSignal) => apiRequest<PedidoDetalhe>('GET', `/pedidos/${enc(id)}`, { signal }),
  /** `dry_run: true` calcula e devolve sem gravar; a edição real leva a `confirmacao` que o `dry_run` devolveu. */
  editar: (id: string, corpo: PedidoEdicao) =>
    apiRequest<PedidoEdicaoResultado>('PATCH', `/pedidos/${enc(id)}`, { body: corpo }),
  ativar: (id: string, confirmacao: string) =>
    apiRequest<PedidoView>('POST', `/pedidos/${enc(id)}/ativar`, { body: { confirmacao } }),
  pausar: (id: string, motivo?: string) =>
    apiRequest<PedidoSemMudanca>('POST', `/pedidos/${enc(id)}/pausar`, { body: motivo ? { motivo } : {} }),
  /** `modo` só vale para `pausado`; de `aguardando_pessoa` o corpo vai vazio (com `modo` o backend responde 422). */
  retomar: (id: string, modo?: 'daqui' | 'recuperar') =>
    apiRequest<PedidoRetomado>('POST', `/pedidos/${enc(id)}/retomar`, { body: modo ? { modo } : {} }),
  /** Sem `confirmar: true` responde 409 `confirmacao_necessaria` com o que seria afetado (`details`), e não muda nada. */
  cancelar: (id: string, corpo: { confirmar: boolean; motivo?: string }) =>
    apiRequest<PedidoCancelado>('POST', `/pedidos/${enc(id)}/cancelar`, { body: corpo }),
  ocorrencias: (id: string, q: { estado?: string; limit?: number; antes_de?: string } = {}, signal?: AbortSignal) =>
    apiRequest<ListaDeOcorrencias>('GET', `/pedidos/${enc(id)}/ocorrencias`, { query: q, signal }),
  execucoes: (id: string, limit = 20, signal?: AbortSignal) =>
    apiRequest<RunSummary[]>('GET', `/pedidos/${enc(id)}/execucoes`, { query: { limit }, signal }),

  /** A caixa de avisos. `requer_pessoa=0` é o filtro do painel: o que depende de uma pessoa mora nas Pendências. */
  avisos: (q: { lido?: 0 | 1; requer_pessoa?: 0 | 1; pedido_id?: string; limit?: number; cursor?: string } = {},
           signal?: AbortSignal) =>
    apiRequest<ListaDeAvisos>('GET', '/pedidos/avisos', { query: q, signal }),
  /** Idempotente. `todos` marca os informativos (os da caixa), só os de `pedido_id` quando ele vem; `ids` marca exatamente os dados. */
  lerAvisos: (corpo: { ids?: string[]; todos?: true; pedido_id?: string }) =>
    apiRequest<{ lidos: number; nao_lidos: number }>('POST', '/pedidos/avisos/ler', { body: corpo }),
};
