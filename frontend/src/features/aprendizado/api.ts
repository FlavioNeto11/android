import { apiRequest } from '../../api/client';
import { lerDetalheDoApp, lerVisaoDeApps, type DetalheDoApp, type VisaoDeApps } from './apps';
import {
  type DetalheDoLivro, type EstadoDoLivro, type FeedbackDaExecucao, type ListaDoLivro, type LivroKind, type Origem,
  type RelatorioDeFalhas, type RespostaDoVoto, type Sinal, type CorpoDoVoto, lerFeedbackDaExecucao,
  lerRelatorioDeFalhas, lerRespostaDoVoto, lerSinais,
} from './model';

/**
 * As rotas do aprendizado (ADR-054). Ficam aqui, e não no objeto `api` do cliente, porque são de UM contexto e
 * chegam em pacotes paralelos (A1 livro, A3 falhas, A4 voto e sinais): o que vem de A3/A4 passa pela leitura
 * tolerante de `model.ts` antes de chegar à tela.
 */

const enc = encodeURIComponent;

export interface FiltroDoLivro {
  kind?: LivroKind;
  state?: EstadoDoLivro;
  app?: string;
  origem?: Origem;
}

export const apiAprendizado = {
  livro: (f: FiltroDoLivro = {}, signal?: AbortSignal) =>
    apiRequest<ListaDoLivro>('GET', '/aprendizado', { query: { kind: f.kind, state: f.state, app: f.app || undefined, origem: f.origem }, signal }),
  /** A visão por aplicativo (Global): um resumo por app, o balde `nao_resolvido` e o que não tem eixo de app. */
  apps: async (signal?: AbortSignal): Promise<VisaoDeApps> =>
    lerVisaoDeApps(await apiRequest<unknown>('GET', '/aprendizado/apps', { signal })),
  /** O detalhe de um app (também `nao_resolvido`): o declarado, o aprendido e o absorvido. 404 se nenhuma fonte o conhece. */
  app: async (pacote: string, signal?: AbortSignal): Promise<DetalheDoApp> =>
    lerDetalheDoApp(await apiRequest<unknown>('GET', `/aprendizado/apps/${enc(pacote)}`, { signal })),
  /** A fila do D1 ("Para aprovar") e a contagem da barra do topo. */
  pendentes: (signal?: AbortSignal) => apiRequest<ListaDoLivro>('GET', '/aprendizado/pendentes', { signal }),
  /** O legado ativo com efeito anterior ao D1, que nenhuma pessoa decidiu ainda. */
  revisar: (signal?: AbortSignal) => apiRequest<ListaDoLivro>('GET', '/aprendizado/revisar', { signal }),
  detalhe: (kind: LivroKind, ref: string, signal?: AbortSignal) =>
    apiRequest<DetalheDoLivro>('GET', `/aprendizado/${kind}/${enc(ref)}`, { signal }),
  /** Move o item com a trilha; o motivo é obrigatório. Habilidade devolve 409 com o endereço da rota dela. */
  mudarEstado: (kind: LivroKind, ref: string, to: EstadoDoLivro, reason: string) =>
    apiRequest<DetalheDoLivro>('POST', `/aprendizado/${kind}/${enc(ref)}/status`, { body: { to, reason: reason.trim() } }),

  /** "O que mais falha" (A3). Sem IA; o simulado fica fora por padrão. */
  falhas: async (q: { dias: number; app?: string; camada?: string; limite?: number }, signal?: AbortSignal): Promise<RelatorioDeFalhas> =>
    lerRelatorioDeFalhas(await apiRequest<unknown>('GET', '/aprendizado/falhas', {
      query: { dias: q.dias, app: q.app || undefined, camada: q.camada || undefined, limite: q.limite ?? 20, formato: 'json' },
      signal,
    })),
  /** A aba Sinais (A4). */
  sinais: async (q: { dias: number; kind?: string; app?: string }, signal?: AbortSignal): Promise<Sinal[]> =>
    lerSinais(await apiRequest<unknown>('GET', '/aprendizado/sinais', {
      query: { dias: q.dias, kind: q.kind || undefined, app: q.app || undefined }, signal,
    })),

  /** Os votos por item e os sinais implícitos de uma execução (A4). */
  feedback: async (runId: string, signal?: AbortSignal): Promise<FeedbackDaExecucao> =>
    lerFeedbackDaExecucao(await apiRequest<unknown>('GET', `/runs/${enc(runId)}/feedback`, { signal })),
  /** O botão do D2. 409 `note_looks_secret`: a nota tem cara de credencial e NADA foi gravado. */
  votar: async (runId: string, corpo: CorpoDoVoto): Promise<RespostaDoVoto> =>
    lerRespostaDoVoto(await apiRequest<unknown>('POST', `/runs/${enc(runId)}/feedback`, { body: corpo })),
};
