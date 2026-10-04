import { apiRequest } from '../../api/client';
import type {
  FiltroDePedidos, ListaDeAvisos, ListaDeOcorrencias, ListaDePedidos, OcorrenciaDTO, PedidoCancelado, PedidoCriacao,
  PedidoCriado, PedidoDetalhe, PedidoEdicao, PedidoEdicaoResultado, PedidoCorpo, PedidoPrevia, PedidoRetomado,
  PedidoSemMudanca, PedidoView,
} from '../../api/pedidos';
import type { RunSummary } from '../../api/types';

/**
 * As rotas de `/api/pedidos` (adendo v0.45). Ficam aqui, e não no objeto `api` do cliente, como as do aprendizado: são
 * de UM contexto. Filtro ausente vai como `undefined` (o `buildUrl` o omite), nunca como texto vazio.
 */
const enc = encodeURIComponent;

type QueryDeAvisos = { lido?: 0 | 1; requer_pessoa?: 0 | 1; pedido_id?: string; limit?: number; cursor?: string };

/**
 * A mesma leitura de avisos pedida por dois lugares ao mesmo tempo (o selo do menu e a caixa, por exemplo) vira UMA
 * chamada. A chamada de rede não leva o `signal` de ninguém: cada quem recebe a resposta, e quem sai (aborta) só deixa
 * de esperar, sem derrubar a leitura dos outros.
 */
const avisosEmVoo = new Map<string, Promise<ListaDeAvisos>>();

function esperarAte<T>(leitura: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return leitura;
  return new Promise<T>((resolve, reject) => {
    const abortou = () => reject(new DOMException('Leitura cancelada', 'AbortError'));
    if (signal.aborted) { abortou(); return; }
    signal.addEventListener('abort', abortou, { once: true });
    leitura.then(resolve, reject).finally(() => signal.removeEventListener('abort', abortou));
  });
}

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
  /** (28.21) Dá a ocorrência `incerta` por resolvida (a nota é obrigatória). Só marca: não reexecuta nem muda o estado.
   *  Repetir é idempotente (200, devolve o que foi gravado antes). 404/409 se não existe ou não está incerta. */
  resolverIncerta: (id: string, ocorrenciaId: string, nota: string) =>
    apiRequest<OcorrenciaDTO>('POST', `/pedidos/${enc(id)}/ocorrencias/${enc(ocorrenciaId)}/resolver`, { body: { nota } }),
  ocorrencias: (id: string, q: { estado?: string; limit?: number; antes_de?: string } = {}, signal?: AbortSignal) =>
    apiRequest<ListaDeOcorrencias>('GET', `/pedidos/${enc(id)}/ocorrencias`, { query: q, signal }),
  execucoes: (id: string, limit = 20, signal?: AbortSignal) =>
    apiRequest<RunSummary[]>('GET', `/pedidos/${enc(id)}/execucoes`, { query: { limit }, signal }),

  /** A caixa de avisos. `requer_pessoa=0` é o filtro do painel: o que depende de uma pessoa mora nas Pendências. */
  avisos: (q: QueryDeAvisos = {}, signal?: AbortSignal) => {
    const chave = JSON.stringify(q);
    let leitura = avisosEmVoo.get(chave);
    if (!leitura) {
      leitura = apiRequest<ListaDeAvisos>('GET', '/pedidos/avisos', { query: q }).finally(() => avisosEmVoo.delete(chave));
      avisosEmVoo.set(chave, leitura);
    }
    return esperarAte(leitura, signal);
  },
  /** Idempotente. `todos` marca os informativos (os da caixa), só os de `pedido_id` quando ele vem; `ids` marca exatamente os dados. */
  lerAvisos: (corpo: { ids?: string[]; todos?: true; pedido_id?: string }) =>
    apiRequest<{ lidos: number; nao_lidos: number }>('POST', '/pedidos/avisos/ler', { body: corpo }),
};
