/**
 * A leitura das operações (rascunho do adendo v1.94): `GET /api/operacoes`, `GET /api/operacoes/{id}` e
 * `POST /api/operacoes/{id}/cancelar`. Enquanto o central não tem a rota (404 que NÃO seja `operacao_inexistente`), a tela cai
 * no exemplo fixo e AVISA; assim que a rota responder, a mesma tela passa a ler o real sem mudança.
 */
import { ApiError, apiRequest, toApiError } from '../../api/client';
import { lerLiberacao, lerLista, lerOperacao, type Operacao, type ResultadoDaLiberacao, type ResumoDaOperacao } from './modelo';
import { OPERACAO_DE_EXEMPLO } from './operacaoDeExemplo';

const enc = encodeURIComponent;

/** 404 de ROTA que não existe (backend anterior ao módulo); o 404 de uma operação que não existe é `operacao_inexistente`. */
const rotaAusente = (e: unknown): boolean => {
  const err = toApiError(e);
  return err.status === 404 && err.code !== 'operacao_inexistente';
};

export const EXEMPLO = (): Operacao => lerOperacao(OPERACAO_DE_EXEMPLO, true)!;

export interface ListaDeOperacoes { itens: ResumoDaOperacao[]; exemplo: boolean }

export const apiOperacoes = {
  async lista(signal?: AbortSignal): Promise<ListaDeOperacoes> {
    try {
      const bruto = await apiRequest<unknown>('GET', '/operacoes', { query: { limite: '50' }, signal });
      // Resposta sem `items` não é "nenhuma operação": é resposta inválida (erro visível, não lista vazia).
      if (!Array.isArray((bruto as { items?: unknown } | null)?.items)) throw new ApiError(502, 'resposta_invalida', 'A lista de operações veio em formato inesperado.');
      return { itens: lerLista(bruto), exemplo: false };
    } catch (e) {
      if (rotaAusente(e)) return { itens: [EXEMPLO()], exemplo: true };
      throw e;
    }
  },
  async detalhe(id: string, signal?: AbortSignal): Promise<Operacao> {
    try {
      const bruto = await apiRequest<unknown>('GET', `/operacoes/${enc(id)}`, { signal });
      const op = lerOperacao(bruto);
      // Sem a lista de alvos a operação não tem o que mostrar nem relatar: "0 agentes" seria falso.
      if (!op || !Array.isArray((bruto as { alvos?: unknown }).alvos)) throw new ApiError(502, 'resposta_invalida', 'A resposta não é uma operação completa.');
      return op;
    } catch (e) {
      if (rotaAusente(e) && id === OPERACAO_DE_EXEMPLO.id) return EXEMPLO();
      throw e;
    }
  },
  /** Libera os alvos parados no teto de ações executadas: eles seguem até a ação final. */
  async liberar(id: string, itens: { profile_id: string; texto: string }[]): Promise<ResultadoDaLiberacao> {
    // O corpo é o eco do texto que a pessoa viu; a decisão é item a item: o servidor recusa o que mudou (`texto_divergente`).
    const r = lerLiberacao(await apiRequest<unknown>('POST', `/operacoes/${enc(id)}/liberar`, { body: { itens } }));
    if (!r) throw new ApiError(502, 'resposta_invalida', 'A resposta da liberação não tem o formato esperado.');
    return r;
  },
  async cancelar(id: string): Promise<Operacao> {
    const op = lerOperacao(await apiRequest<unknown>('POST', `/operacoes/${enc(id)}/cancelar`, { body: {} }));
    if (!op) throw new ApiError(502, 'resposta_invalida', 'A resposta não é uma operação.');
    return op;
  },
};
