import { toApiError } from '../../api/client';

/** O resultado de uma operação num item do lote: ok, ou falhou com o motivo que o servidor (ou o painel) deu. */
export type ResultadoDoItem = { ok: true; detalhe?: string } | { ok: false; motivo: string };

/**
 * Roda `fn` em cada item com no máximo `limite` ao mesmo tempo, na ordem da lista, e devolve um resultado por item
 * (nunca rejeita: a falha de um não para os outros). `aoTerminar` avisa cada item concluído, para o progresso.
 * Concorrência baixa de propósito: são rotas que podem chamar IA paga, e o servidor tem vagas por papel.
 */
export async function executarEmLote<T>(
  itens: readonly T[],
  limite: number,
  fn: (item: T) => Promise<string | void>,
  aoTerminar?: (indice: number, r: ResultadoDoItem) => void,
): Promise<ResultadoDoItem[]> {
  const saida: ResultadoDoItem[] = new Array(itens.length);
  let proximo = 0;
  async function trabalhador(): Promise<void> {
    while (proximo < itens.length) {
      const i = proximo++;
      let r: ResultadoDoItem;
      try {
        const detalhe = await fn(itens[i] as T);
        r = detalhe ? { ok: true, detalhe } : { ok: true };
      } catch (e) {
        r = { ok: false, motivo: e instanceof RecusaLocal ? e.message : toApiError(e).message };
      }
      saida[i] = r;
      aoTerminar?.(i, r);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limite, itens.length) }, () => trabalhador()));
  return saida;
}

/** Recusa decidida no painel, sem requisição (ex.: grupo de acesso para quem não tem conta), com o motivo para a
 *  pessoa. Vira `falhou` no resumo, como a recusa do servidor. */
export class RecusaLocal extends Error {}
