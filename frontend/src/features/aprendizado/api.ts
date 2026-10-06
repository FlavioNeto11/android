import { ApiError, apiRequest } from '../../api/client';
import {
  lerDetalheDoApp, lerProvaDoConhecimento, lerVisaoDeApps, type DetalheDoApp, type ProvaDoConhecimento, type VisaoDeApps,
} from './apps';
import {
  type DetalheDoLivro, type EstadoDoLivro, type FeedbackDaExecucao, type ListaDoLivro, type LivroKind, type Origem, type Rotulo,
  type RelatorioDeFalhas, type RespostaDoVoto, type Sinal, type CorpoDoVoto, lerFeedbackDaExecucao,
  lerRelatorioDeFalhas, lerRespostaDoVoto, lerSinais,
} from './model';
import { lerMetricas, lerPaginaDeRevisoes, type MetricasDoAprendizado, type PaginaDeRevisoes } from './metricas';
import type { RespostaDoPedido } from './parecer';
import { lerListaDeValidacoes, type ListaDeValidacoes } from './validacao';
import { lerRelatorioDaAprovacao, type RelatorioDaAprovacao } from './aprovacaoAutomatica';

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
  rotulo?: Rotulo;
  /** 31.131: `so_prova` só os fluxos nascidos de uma prova; `sem_prova` o que sobra (uso real). Sem o campo, tudo. */
  prova?: 'so_prova' | 'sem_prova';
}

export const PROVAS_DO_LIVRO: readonly NonNullable<FiltroDoLivro['prova']>[] = ['so_prova', 'sem_prova'];
export const PROVA_LABEL: Record<NonNullable<FiltroDoLivro['prova']>, string> = {
  so_prova: 'Só os nascidos de uma prova',
  sem_prova: 'Sem os de prova (uso real)',
};

/**
 * A fila "Para aprovar" é lida por três lugares que costumam pedir juntos (o selo do topo, a caixa de Pendências e a
 * própria aba): na carga do Para aprovar eram 3 leituras iguais (validação do deploy 12). Leituras sem `signal` que
 * chegam dentro de `JANELA_DOS_PENDENTES_MS` dividem a mesma; toda decisão no livro zera a janela, para a releitura
 * depois dela ser nova.
 */
const JANELA_DOS_PENDENTES_MS = 2000;
let leituraDosPendentes: { promessa: Promise<ListaDoLivro>; em: number } | null = null;

function lerPendentes(signal?: AbortSignal): Promise<ListaDoLivro> {
  if (signal) return apiRequest<ListaDoLivro>('GET', '/aprendizado/pendentes', { signal });
  const agora = Date.now();
  if (leituraDosPendentes && agora - leituraDosPendentes.em < JANELA_DOS_PENDENTES_MS) return leituraDosPendentes.promessa;
  const promessa = apiRequest<ListaDoLivro>('GET', '/aprendizado/pendentes');
  const esta = { promessa, em: agora };
  leituraDosPendentes = esta;
  promessa.catch(() => { if (leituraDosPendentes === esta) leituraDosPendentes = null; });
  return promessa;
}

/** Esquece a leitura dividida (o backend falso de cada teste é um mundo novo). */
export function esquecerLeituraDosPendentes(): void {
  leituraDosPendentes = null;
}

/** Uma decisão no livro: a próxima leitura da fila vai ao servidor. */
function decisao<T>(p: Promise<T>): Promise<T> {
  return p.finally(() => { leituraDosPendentes = null; });
}

export const apiAprendizado = {
  /** 31.146: os fluxos nascidos de prova (`GET /api/flows?nascido_de_prova=true`, 31.130); a guarda cobre o backend que ignora o parâmetro. */
  fluxosDeProva: async (signal?: AbortSignal): Promise<number> => {
    const fluxos = await apiRequest<{ nascido_de_prova?: boolean }[]>('GET', '/flows', { query: { nascido_de_prova: 'true' }, signal });
    return (Array.isArray(fluxos) ? fluxos : []).filter((f) => f?.nascido_de_prova === true).length;
  },
  livro: (f: FiltroDoLivro = {}, signal?: AbortSignal) =>
    apiRequest<ListaDoLivro>('GET', '/aprendizado', { query: { kind: f.kind, state: f.state, app: f.app || undefined, origem: f.origem, rotulo: f.rotulo,
                                           nascido_de_prova: f.prova === 'so_prova' ? 'true' : f.prova === 'sem_prova' ? 'false' : undefined }, signal }),
  /** A visão por aplicativo (Global): um resumo por app, o balde `nao_resolvido` e o que não tem eixo de app. */
  apps: async (signal?: AbortSignal): Promise<VisaoDeApps> =>
    lerVisaoDeApps(await apiRequest<unknown>('GET', '/aprendizado/apps', { signal })),
  /** O detalhe de um app (também `nao_resolvido`): o declarado, o aprendido e o absorvido. 404 se nenhuma fonte o conhece. */
  app: async (pacote: string, signal?: AbortSignal): Promise<DetalheDoApp> =>
    lerDetalheDoApp(await apiRequest<unknown>('GET', `/aprendizado/apps/${enc(pacote)}`, { signal })),
  /** RA-24: os arquivos de conhecimento que o processo carregou, com o sha256. `null` quando o app não tem
   *  conhecimento declarado (404 `not_found`): não é erro, é o caso da maioria dos apps. */
  conhecimento: async (pacote: string, signal?: AbortSignal): Promise<ProvaDoConhecimento | null> => {
    try {
      return lerProvaDoConhecimento(await apiRequest<unknown>('GET', `/apps/${enc(pacote)}/conhecimento`, { signal }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  /** A fila do D1 ("Para aprovar") e a contagem da barra do topo. */
  pendentes: (signal?: AbortSignal) => lerPendentes(signal),
  /** O legado ativo com efeito anterior ao D1, que nenhuma pessoa decidiu ainda. */
  revisar: (signal?: AbortSignal) => apiRequest<ListaDoLivro>('GET', '/aprendizado/revisar', { signal }),
  detalhe: (kind: LivroKind, ref: string, signal?: AbortSignal) =>
    apiRequest<DetalheDoLivro>('GET', `/aprendizado/${kind}/${enc(ref)}`, { signal }),
  /** Move o item com a trilha; o motivo é obrigatório. Habilidade devolve 409 com o endereço da rota dela.
   *  `reviewId`: o parecer da IA que a pessoa via ao decidir (30.17); a decisão fica registrada contra ele. */
  mudarEstado: (kind: LivroKind, ref: string, to: EstadoDoLivro, reason: string, reviewId?: string | null) =>
    decisao(apiRequest<DetalheDoLivro>('POST', `/aprendizado/${kind}/${enc(ref)}/status`, {
      body: { to, reason: reason.trim(), ...(reviewId ? { review_id: reviewId } : {}) },
    })),
  /** 30.24: "Confirmar que fica" o legado de Revisar. O motivo é opcional; 409 quando o item já não está em Revisar. */
  confirmarQueFica: (kind: LivroKind, ref: string, motivo: string, reviewId?: string | null) =>
    decisao(apiRequest<DetalheDoLivro>('POST', `/aprendizado/${kind}/${enc(ref)}/confirmar`, {
      body: { ...(motivo.trim() ? { motivo: motivo.trim() } : {}), ...(reviewId ? { review_id: reviewId } : {}) },
    })),
  /** 30.23: a execução de origem terminou como sucesso sem comprovar o que fez. O motivo é estruturado pelo backend. */
  invalidarEvidencia: (kind: LivroKind, ref: string, runId: string) =>
    decisao(apiRequest<DetalheDoLivro>('POST', `/aprendizado/${kind}/${enc(ref)}/evidencia-invalida`, { body: { run_id: runId } })),
  /** Aceitar ou recusar o parecer da IA (30.17). 409 com o `code` quando o gesto não vale (classe A, C em lote,
   *  simulado, já decidido, item mudou); devolve o detalhe atualizado. */
  responderParecer: (kind: LivroKind, ref: string, reviewId: string,
                     corpo: { resposta: 'aceitar' | 'recusar'; motivo: string; em_lote?: boolean }) =>
    decisao(apiRequest<DetalheDoLivro>('POST', `/aprendizado/${kind}/${enc(ref)}/parecer/${enc(reviewId)}`, {
      body: { resposta: corpo.resposta, motivo: corpo.motivo.trim(), em_lote: !!corpo.em_lote },
    })),
  /** Pede revisão ao curador (só com ele ligado). `pedido: false` = o estado de agora do item já tem revisão. */
  pedirRevisao: (kind: LivroKind, ref: string) =>
    apiRequest<RespostaDoPedido>('POST', `/aprendizado/${kind}/${enc(ref)}/revisao`),

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

  /** As métricas do §10 (30.8; aba Métricas, 30.33). Ausente é `null`, com o `n` ao lado; `dias` vai de 1 a 90. */
  metricas: async (q: { dias: number; app?: string }, signal?: AbortSignal): Promise<MetricasDoAprendizado> =>
    lerMetricas(await apiRequest<unknown>('GET', '/aprendizado/metricas', { query: { dias: q.dias, app: q.app || undefined }, signal })),
  /** Os pareceres do curador, do mais novo ao mais velho; `cursor` é o `proximo` da página anterior. */
  revisoes: async (q: { app?: string; decisao?: string; limite?: number; cursor?: string | null }, signal?: AbortSignal): Promise<PaginaDeRevisoes> =>
    lerPaginaDeRevisoes(await apiRequest<unknown>('GET', '/aprendizado/revisoes', {
      query: { app: q.app || undefined, decisao: q.decisao || undefined, limite: q.limite ?? 50, cursor: q.cursor || undefined }, signal,
    })),

  /** 30.38 (b): os pedidos de validação automática, só leitura; `antes` é o `created_at` do último da página anterior.
   *  30.43: `item` (`<kind>:<ref>`) e `run` (id da execução) filtram o histórico de UM item e o veredito de UMA execução. */
  validacoes: async (q: { estado?: string; limite?: number; antes?: string | null; item?: string; run?: string },
                     signal?: AbortSignal): Promise<ListaDeValidacoes> =>
    lerListaDeValidacoes(await apiRequest<unknown>('GET', '/aprendizado/validacoes', {
      query: { estado: q.estado || undefined, limite: q.limite ?? 50, antes: q.antes || undefined, item: q.item || undefined, run: q.run || undefined }, signal,
    })),

  /** 30.55: a aprovação automática (modo, última volta e as decisões da plataforma, com o item de agora). */
  aprovacaoAutomatica: async (signal?: AbortSignal): Promise<RelatorioDaAprovacao> =>
    lerRelatorioDaAprovacao(await apiRequest<unknown>('GET', '/aprendizado/aprovacao-automatica', { signal })),

  /** Os votos por item e os sinais implícitos de uma execução (A4). */
  feedback: async (runId: string, signal?: AbortSignal): Promise<FeedbackDaExecucao> =>
    lerFeedbackDaExecucao(await apiRequest<unknown>('GET', `/runs/${enc(runId)}/feedback`, { signal })),
  /** O botão do D2. 409 `note_looks_secret`: a nota tem cara de credencial e NADA foi gravado. */
  votar: async (runId: string, corpo: CorpoDoVoto): Promise<RespostaDoVoto> =>
    lerRespostaDoVoto(await apiRequest<unknown>('POST', `/runs/${enc(runId)}/feedback`, { body: corpo })),
};
