/**
 * A leitura das operações (rascunho do adendo v1.94): `GET /api/operacoes`, `GET /api/operacoes/{id}` e
 * `POST /api/operacoes/{id}/cancelar`. Enquanto o central não tem a rota (404 que NÃO seja `operacao_inexistente`), a tela cai
 * no exemplo fixo e AVISA; assim que a rota responder, a mesma tela passa a ler o real sem mudança.
 */
import { ApiError, apiRequest, toApiError } from '../../api/client';
import { isEstadoDoAlvo, lerEstagio, lerLiberacao, lerLista, lerResumo, lerOperacao, type EstadoDoAlvo, type EstagioId, type Operacao, type ResultadoDaLiberacao, type ResumoDaOperacao } from './modelo';
import { lerAprendizado, type LeituraDoAprendizado } from './aprendizadoDaOperacao';
import { corpoDoCancelamento, lerResultadoDoCancelamento, type FiltroDeCancelamento, type ResultadoDoCancelamento } from './filtroDeCancelamento';
import type { CorpoDaOperacao } from './criar';
import { OPERACAO_DE_EXEMPLO } from './operacaoDeExemplo';
import type { RelatorioDaOperacao } from './relatorio';
import { relatorioDoServidor } from './relatorioDoServidor';

const enc = encodeURIComponent;

/** 404 de ROTA que não existe (backend anterior ao módulo); o 404 de uma operação que não existe é `operacao_inexistente`. */
const rotaAusente = (e: unknown): boolean => {
  const err = toApiError(e);
  return err.status === 404 && err.code !== 'operacao_inexistente';
};

/** O relatório do central (v1.111) ou o porquê de não haver: o painel então monta o seu, como reserva. */
export type LeituraDoRelatorio = { situacao: 'central'; relatorio: RelatorioDaOperacao } | { situacao: 'indisponivel'; motivo: string };

export const EXEMPLO = (): Operacao => lerOperacao(OPERACAO_DE_EXEMPLO, true)!;

export interface ListaDeOperacoes { itens: ResumoDaOperacao[]; exemplo: boolean }

/** Quantas operações da persona o filtro do central devolve (o `limite` da rota). */
export const LIMITE_DA_LISTA_DA_PERSONA = 50;

/** v1.116 (Jev 31.213): o resumo do alvo que casou com o filtro, em cada item da lista filtrada. */
export interface AlvoDoFiltro {
  estado: EstadoDoAlvo | null;
  estagio: EstagioId | null;
  parou_em: EstagioId | null;
  motivo: string | null;
  /** `verificada` da ação final: `true`/`false`; `null` = sem ação. */
  acao_verificada: boolean | null;
  custo_usd: number | null;
  duracao_ms: number | null;
}
export interface OperacaoDaPersona extends ResumoDaOperacao { alvos: AlvoDoFiltro[] }

const registroDe = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const naoNegativo = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);

function lerAlvoDoFiltro(v: unknown): AlvoDoFiltro | null {
  const o = registroDe(v);
  if (!o) return null;
  return {
    estado: isEstadoDoAlvo(o.estado) ? o.estado : null, estagio: lerEstagio(o.estagio), parou_em: lerEstagio(o.parou_em),
    motivo: typeof o.motivo === 'string' && o.motivo.trim() ? o.motivo : null,
    acao_verificada: typeof o.acao_verificada === 'boolean' ? o.acao_verificada : null, custo_usd: naoNegativo(o.custo_usd), duracao_ms: naoNegativo(o.duracao_ms),
  };
}

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
  /**
   * v1.116 (31.213): as operações com alvo desta persona, cada uma com o resumo SÓ dos alvos dela. `null` quando o central não sabe filtrar
   * (o central anterior ignora o parâmetro e devolve a lista de sempre, sem `alvos`) ou não tem o módulo: quem chama cai na leitura do
   * detalhe, ou diz que não há módulo (`semModulo`).
   */
  async listaDaPersona(profileId: string, signal?: AbortSignal): Promise<{ itens: OperacaoDaPersona[] } | { semModulo: true } | null> {
    try {
      const bruto = await apiRequest<unknown>('GET', '/operacoes', { query: { profile_id: profileId, limite: String(LIMITE_DA_LISTA_DA_PERSONA) }, signal });
      const itens = registroDe(bruto)?.items;
      if (!Array.isArray(itens)) throw new ApiError(502, 'resposta_invalida', 'A lista de operações veio em formato inesperado.');
      // Filtrado, todo item traz `alvos`; uma lista não vazia sem eles é a de sempre (o central ainda não filtra).
      if (itens.length > 0 && !itens.every((i) => Array.isArray(registroDe(i)?.alvos))) return null;
      return {
        itens: itens.flatMap((i) => {
          const resumo = lerResumo(i);
          const alvos = (registroDe(i)?.alvos as unknown[]).map(lerAlvoDoFiltro).filter((x): x is AlvoDoFiltro => x !== null);
          return resumo ? [{ ...resumo, alvos }] : [];
        }),
      };
    } catch (e) {
      if (rotaAusente(e)) return { semModulo: true };
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
  /**
   * O aprendizado da operação nas 10 perguntas (adendo v1.96). Rota ausente, operação sem execução ou memória (404), resposta
   * fora do formato e falha de leitura viram "indisponível" com o motivo: o relatório não pode virar "nada aprendido".
   */
  async aprendizado(id: string, signal?: AbortSignal, filtros?: { persona?: string | null; simulados?: boolean }): Promise<LeituraDoAprendizado> {
    try {
      const a = lerAprendizado(await apiRequest<unknown>('GET', `/operacoes/${enc(id)}/aprendizado`, { query: { simulados: filtros?.simulados ? 'true' : 'false', persona: filtros?.persona || undefined }, signal }));
      return a ? { situacao: 'lido', aprendizado: a } : { situacao: 'indisponivel', motivo: 'A resposta do aprendizado veio em formato inesperado.' };
    } catch (e) {
      const err = toApiError(e);
      if (err.status === 404) {
        return { situacao: 'indisponivel', motivo: err.code === 'operacao_desconhecida'
          ? 'A operação ainda não tem execução nem memória de aprendizado.' : 'O central ainda não oferece o aprendizado da operação.' };
      }
      return { situacao: 'indisponivel', motivo: `Não foi possível ler o aprendizado: ${err.message}` };
    }
  },
  /** Cria a operação (adendo v1.94): 201 com o detalhe. A mesma `idempotency_key` com o mesmo corpo devolve a mesma operação. */
  async criar(corpo: CorpoDaOperacao, idempotencyKey: string): Promise<Operacao> {
    const op = lerOperacao(await apiRequest<unknown>('POST', '/operacoes', { body: { ...corpo, idempotency_key: idempotencyKey } }));
    if (!op) throw new ApiError(502, 'resposta_invalida', 'A resposta da criação não é uma operação.');
    return op;
  },
  /**
   * O relatório que o CENTRAL monta (adendo v1.111, `GET /api/operacoes/{id}/relatorio`; 31.197). Rota ausente, resposta fora do formato e
   * falha de leitura viram "indisponível" com o motivo: o painel monta o relatório dele e diz que o do central não veio.
   */
  async relatorio(id: string, signal?: AbortSignal): Promise<LeituraDoRelatorio> {
    try {
      const r = relatorioDoServidor(await apiRequest<unknown>('GET', `/operacoes/${enc(id)}/relatorio`, { signal }));
      return r ? { situacao: 'central', relatorio: r } : { situacao: 'indisponivel', motivo: 'O relatório do central veio em formato inesperado.' };
    } catch (e) {
      const err = toApiError(e);
      if (rotaAusente(e)) return { situacao: 'indisponivel', motivo: 'O central ainda não oferece o relatório da operação.' };
      return { situacao: 'indisponivel', motivo: `Não foi possível ler o relatório do central: ${err.message}` };
    }
  },
  /**
   * Cancela só os alvos que casam com o filtro, sem fechar a operação (adendo v1.112; 31.199). O filtro vazio nem sai daqui: o central o
   * recusa com 422 `filtro_vazio` (a operação inteira é `cancelar`).
   */
  async cancelarAlvos(id: string, filtro: FiltroDeCancelamento): Promise<ResultadoDoCancelamento> {
    const r = lerResultadoDoCancelamento(await apiRequest<unknown>('POST', `/operacoes/${enc(id)}/cancelar-alvos`, { body: corpoDoCancelamento(filtro) }));
    if (!r) throw new ApiError(502, 'resposta_invalida', 'A resposta do cancelamento por filtro veio em formato inesperado.');
    return r;
  },
  async cancelar(id: string): Promise<Operacao> {
    const op = lerOperacao(await apiRequest<unknown>('POST', `/operacoes/${enc(id)}/cancelar`, { body: {} }));
    if (!op) throw new ApiError(502, 'resposta_invalida', 'A resposta não é uma operação.');
    return op;
  },
};
