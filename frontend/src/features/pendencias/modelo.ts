/**
 * A caixa única de pendências (revisão de UX, tarefa 06): tudo o que espera uma decisão da pessoa, de quatro origens
 * que antes viviam em telas diferentes.
 *
 * - **Aprendizado**: itens da fila "Para aprovar" (efeito fora do sistema ou texto de pessoa), um por item.
 * - **Persona**: textos que uma persona quer publicar e esperam aprovação, um por aprovação.
 * - **Execução**: execuções que PARARAM pedindo informação (`needs_input`), uma linha por execução, TODAS, por mais
 *   antigas que sejam (o snapshot traz todas e o store não as descarta). Decisão D1 da revisão de UX: "pendência" é o
 *   que depende de uma pessoa. Antes a origem eram os objetivos `waiting_user`/`uncertain` das 20 execuções mais
 *   recentes: um número de janela (4 no parque real, que tinha 27 `needs_input`) e de execuções que já tinham
 *   terminado. Essas continuam visíveis em Execuções, no chip "Pede atenção".
 * - **Intervenção**: sessões de conta que só uma pessoa resolve (login, desafio, conta errada), uma por persona — a
 *   fila "Aguardando intervenção" de Personas (RF-03 da revisão final).
 *
 * O contador do menu e a lista saem da mesma função (`montarPendencias`): o total é sempre o número de linhas. O
 * "aguardando você" do cabeçalho e o semáforo leem o MESMO total (`usePendencias`) e levam a `#/pendencias`.
 *
 * A ação primária de cada linha LEVA à tela onde se decide (com a evidência ao lado); a caixa não aprova nem recusa
 * nada: decidir sem ver o contexto é o erro que a aprovação existe para evitar.
 */
import type { PedidoView } from '../../api/pedidos';
import type { Approval, PersonaDTO, RunSummary } from '../../api/types';
import { metaDaSessao } from '../../lib/status';
import type { Destino } from '../../store/ui';
import { rotuloDoKind, type EntradaDoLivro } from '../aprendizado/model';
import { encurtar, tituloCurto } from '../runs/filtroExecucoes';

export type OrigemDaPendencia = 'aprendizado' | 'persona' | 'execucao' | 'intervencao' | 'pedido';

export const ROTULO_DA_ORIGEM: Record<OrigemDaPendencia, string> = {
  aprendizado: 'Aprendizado',
  persona: 'Persona',
  execucao: 'Execução',
  intervencao: 'Intervenção',
  pedido: 'Pedido',
};

/**
 * Estados de sessão que só uma pessoa resolve (login, desafio de segurança, conta errada) — mesmo conjunto do backend
 * (achado #106). Um só lugar: a fila "Aguardando intervenção" de Personas e a caixa de pendências leem daqui.
 */
export const PRECISA_DE_PESSOA: ReadonlySet<string> = new Set(['auth_challenge', 'wrong_account', 'needs_person']);

/**
 * A sessão espera uma pessoa: um dos estados de `PRECISA_DE_PESSOA` ou o `unknown` NO TETO do aparelho (29.96,
 * `unknown_at_cap`: a automação parou sem tocar numa tela que não reconheceu). O filtro das duas filas.
 */
export function precisaDePessoa(session: { status: string; unknown_at_cap?: boolean }): boolean {
  return PRECISA_DE_PESSOA.has(session.status) || !!session.unknown_at_cap;
}

/**
 * Desde quando a sessão espera (29.100): a hora da mudança de estado ou, no `unknown`, a da parada no teto. `verified_at` é a última verificação,
 * que na parada fica vazia ou de dias atrás, e fazia uma parada de agora parecer antiga. Ele só vale quando o
 * backend não manda `status_since` (anterior ao 29.100, ou `session_ready` de antes da migração 112).
 */
export function desdeDaSessao(session: { status_since?: string | null; verified_at: string | null }): string | null {
  return session.status_since ?? session.verified_at;
}

export interface Pendencia {
  /** Estável entre leituras (a lista não pisca quando a caixa é relida). */
  chave: string;
  origem: OrigemDaPendencia;
  titulo: string;
  /** O que falta e por quê, em uma linha. */
  detalhe: string;
  /** Quando passou a esperar (ISO). `null` quando a fonte não diz. */
  desde: string | null;
  /** Texto do botão primário. */
  acao: string;
  destino: Destino;
  /**
   * Só em `pedido` (emenda à ADR-062): as decisões que já têm item próprio (a aprovação e a execução `needs_input` das
   * execuções do pedido) aparecem AGRUPADAS sob ele e não contam de novo: o item do pedido é o que conta.
   */
  filhas?: readonly Pendencia[];
}

const tempo = (iso: string | null): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : Number.POSITIVE_INFINITY;
};

export function pendenciasDeAprendizado(itens: readonly EntradaDoLivro[]): Pendencia[] {
  return itens.map((e) => ({
    chave: `aprendizado:${e.kind}:${e.ref}`,
    origem: 'aprendizado',
    titulo: e.title,
    detalhe: `${rotuloDoKind(e.kind)} · ${
      e.kind === 'habilidade' ? 'publicar é sempre decisão de uma pessoa'
        : e.side_effect ? 'tem efeito fora do sistema' : e.human_origin ? 'texto escrito por uma pessoa' : 'espera a sua aprovação'}`,
    desde: e.state_at ?? e.created_at,
    acao: 'Revisar',
    // Direto ao item (o mesmo link do aviso externo): quem clica "Revisar" quer ESTE item, não a fila inteira.
    destino: { tela: 'aprendizado', query: { aba: 'aprendido', item: `${e.kind}:${e.ref}` } },
  }));
}

export function pendenciasDeAprovacoes(
  aprovacoes: readonly Approval[], nomeDaPersona: (id: string | null) => string | null,
): Pendencia[] {
  return aprovacoes.filter((a) => a.status === 'pending').map((a) => {
    const nome = nomeDaPersona(a.profile_id);
    return {
      chave: `persona:${a.id}`,
      origem: 'persona',
      titulo: a.summary || 'Texto para aprovar',
      detalhe: nome ? `Persona ${nome}${a.target ? ` · ${a.target}` : ''}` : (a.target ?? 'Persona sem nome'),
      desde: a.created_at,
      acao: 'Decidir',
      destino: a.profile_id
        ? { tela: 'personas', segmentos: [a.profile_id, 'aprovacoes'] }
        : { tela: 'personas' },
    };
  });
}

/** As execuções que pararam esperando uma resposta sua. Todas, sem janela: é o seletor da caixa e do contador. */
export function execucoesPedindoResposta(runs: readonly RunSummary[]): RunSummary[] {
  return runs.filter((r) => r.status === 'needs_input');
}

/** A primeira pergunta do `status_detail` (o servidor junta as perguntas com " | ") e quantas há. */
function perguntas(detalhe: string | null): { primeira: string; total: number } {
  const lista = (detalhe ?? '').split(' | ').map((p) => p.trim()).filter(Boolean);
  return { primeira: lista[0] ?? '', total: lista.length };
}

export function pendenciasDeExecucoes(runs: readonly RunSummary[]): Pendencia[] {
  return execucoesPedindoResposta(runs).map((run) => {
    // O MESMO título da lista de Execuções (RF-09): a primeira linha crua repetia "Nas instâncias selecionadas, No
    // QA Messenger, …" e a mesma execução aparecia com dois títulos. O app citado vai para o detalhe, como lá.
    const { titulo, app } = tituloCurto(run.command);
    const { primeira, total } = perguntas(run.status_detail);
    const pergunta = primeira
      ? ` · ${encurtar(primeira, 110)}${total > 1 ? ` (e mais ${total - 1} ${total === 2 ? 'pergunta' : 'perguntas'})` : ''}`
      : '';
    return {
      chave: `execucao:${run.id}`,
      origem: 'execucao',
      titulo,
      detalhe: `${app ? `${app} · ` : ''}Parou pedindo informação${pergunta}`,
      desde: run.created_at,
      acao: 'Responder',
      destino: { tela: 'execucoes', segmentos: [run.id] },
    };
  });
}

/**
 * Emenda à ADR-062 (item 28.9, confirmada pelo dono em 02/10): o pedido em `aguardando_pessoa` é uma origem da caixa.
 * Uma linha por pedido, com as decisões dele (aprovação, execução parada pedindo informação) agrupadas embaixo, sem
 * contar duas vezes. A decisão em si continua na tela do pedido e nas telas de sempre; a caixa só leva até ela.
 */
export function pendenciasDePedidos(
  pedidos: readonly PedidoView[], filhasPorPedido: ReadonlyMap<string, readonly Pendencia[]> = new Map(),
): Pendencia[] {
  return pedidos.filter((p) => p.estado === 'aguardando_pessoa').map((p) => {
    const filhas = filhasPorPedido.get(p.id) ?? [];
    return {
      chave: `pedido:${p.id}`,
      origem: 'pedido',
      titulo: p.titulo,
      // O motivo em palavras: a ocorrência incerta é o caso que mais confunde (não há prova de que a ação aconteceu).
      detalhe: `${(p.ocorrencias_por_estado?.incerta ?? 0) > 0 ? 'Ocorrência incerta: confira se a ação aconteceu' : 'Uma decisão sua está aberta neste pedido'}${filhas.length > 0 ? ` · ${filhas.length} ${filhas.length === 1 ? 'decisão' : 'decisões'} dentro dele` : ''}. Abra o pedido para decidir e retomar.`,
      desde: p.atualizado_em ?? null,
      acao: 'Decidir',
      destino: { tela: 'pedidos', segmentos: [p.id] },
      ...(filhas.length > 0 ? { filhas } : {}),
    };
  });
}

/**
 * Sessões que esperam uma pessoa (RF-03 da revisão final): a mesma fila "Aguardando intervenção" de Personas — persona
 * com conta e sessão em login, desafio ou conta errada. A decisão continua lá (assumir o controle do aparelho e
 * resolver na tela); a caixa só leva até ela.
 */
export function pendenciasDeSessoes(personas: readonly PersonaDTO[]): Pendencia[] {
  return personas.filter((p) => p.username && precisaDePessoa(p.session)).map((p) => {
    const nome = p.display_name || p.name;
    const aparelho = p.session.instance_id ?? p.instance_id;
    return {
      chave: `intervencao:${p.id}`,
      origem: 'intervencao',
      titulo: nome && nome !== p.username ? `${nome} (@${p.username})` : `@${p.username}`,
      detalhe: `${metaDaSessao(p.session).label} · ${aparelho ?? 'sem aparelho vinculado'}`
        + ' · só uma pessoa resolve',
      desde: desdeDaSessao(p.session),
      acao: 'Resolver',
      destino: { tela: 'personas' },
    };
  });
}

/** Execução parada há mais que isto vai para "Antigas": continua contando, só não disputa a vista com as recentes. */
export const DIAS_PARA_ANTIGA = 7;

/**
 * Triagem por idade (decisão D1): a execução que parou pedindo informação há mais de 7 dias raramente é a próxima
 * coisa a fazer, e 27 linhas seguidas escondiam o que chegou hoje. Só a origem Execução: uma intervenção de sessão
 * continua parada até alguém agir, por mais antiga que seja, e um login travado de verdade não pode ir parar numa seção
 * recolhida.
 */
export function ehAntiga(p: Pendencia, agora: number): boolean {
  if (p.origem !== 'execucao' || !p.desde) return false;
  const t = Date.parse(p.desde);
  return Number.isFinite(t) && agora - t > DIAS_PARA_ANTIGA * 86_400_000;
}

export interface EntradasDaCaixa {
  aprendizado: readonly EntradaDoLivro[] | null;
  aprovacoes: readonly Approval[] | null;
  execucoes: readonly RunSummary[];
  /** Personas (`GET /personas`, a mesma leitura da tela Personas): delas saem as sessões que pedem pessoa. */
  personas?: readonly PersonaDTO[] | null;
  nomeDaPersona?: (id: string | null) => string | null;
  /** Pedidos lidos de `GET /api/pedidos?estado=aguardando_pessoa`; os que não estão nesse estado são ignorados. */
  pedidos?: readonly PedidoView[] | null;
}

/**
 * A REGRA DE CONTAGEM DAS PENDÊNCIAS — o único lugar onde ela existe (ADR-062, D1). Pendência é o que depende de uma
 * pessoa, e só entra aqui o que cabe numa destas quatro origens:
 *
 * 1. Aprendizado: cada item da fila "Para aprovar" (`GET /aprendizado/pendentes`). O "Revisar" (legado ativo) fica de
 *    fora de propósito: continua valendo até a pessoa decidir, não espera ninguém para seguir;
 * 2. Persona: cada aprovação de texto com status `pending` (a decidida, aprovada ou recusada, não conta);
 * 3. Execução: cada execução com status `needs_input`, por mais antiga que seja (a de um objetivo `waiting_user`
 *    dentro de uma execução que já terminou não conta: está em Execuções, no chip "Pede atenção");
 * 4. Intervenção: cada persona COM conta cuja sessão está em login, desafio, conta errada ou parada no teto (`precisaDePessoa`);
 * 5. Pedido (emenda à ADR-062, 28.9): cada pedido em `aguardando_pessoa`. A aprovação e a execução `needs_input` das
 *    execuções DELE saem das origens 2 e 3 e aparecem agrupadas sob o pedido (`filhas`): o item do pedido é o que conta.
 *
 * O total é `montarPendencias(...).length`. O selo do menu, o chip "aguardando você" do topo, o aviso do semáforo e as
 * linhas da caixa leem `usePendencias()`, que chama esta função: não há segunda conta em lugar nenhum. Quem somar por
 * fora (por exemplo "Para aprovar" + aprovações) vai divergir da caixa; mude a regra aqui e o teste
 * `contagem.test.tsx` mostra o que muda. A idade (`ehAntiga`) só separa "Antigas" na tela: continua contando.
 *
 * A lista e o total do menu: a mesma conta. Mais antigas primeiro (é o que está esperando há mais tempo).
 */
export function montarPendencias(e: EntradasDaCaixa): Pendencia[] {
  const aguardando = (e.pedidos ?? []).filter((p) => p.estado === 'aguardando_pessoa');
  const idsDosPedidos = new Set(aguardando.map((p) => p.id));
  const pedidoDaExecucao = new Map<string, string>();
  for (const r of e.execucoes) if (r.pedido_id && idsDosPedidos.has(r.pedido_id)) pedidoDaExecucao.set(r.id, r.pedido_id);
  const nomeDaPersona = e.nomeDaPersona ?? (() => null);
  // O que já tem dono (um pedido aguardando) sai da origem de sempre e vira filha dele: não conta duas vezes.
  const filhasPorPedido = new Map<string, Pendencia[]>();
  const filha = (pedidoId: string, p: Pendencia) => filhasPorPedido.set(pedidoId, [...(filhasPorPedido.get(pedidoId) ?? []), p]);
  const aprovacoes = (e.aprovacoes ?? []).filter((a) => {
    const dono = a.run_id ? pedidoDaExecucao.get(a.run_id) : undefined;
    if (!dono) return true;
    for (const p of pendenciasDeAprovacoes([a], nomeDaPersona)) filha(dono, p);
    return false;
  });
  const execucoes = e.execucoes.filter((r) => {
    const dono = pedidoDaExecucao.get(r.id);
    if (!dono) return true;
    for (const p of pendenciasDeExecucoes([r])) filha(dono, p);
    return false;
  });
  const todas = [
    ...pendenciasDeAprendizado(e.aprendizado ?? []),
    ...pendenciasDeAprovacoes(aprovacoes, nomeDaPersona),
    ...pendenciasDeExecucoes(execucoes),
    ...pendenciasDeSessoes(e.personas ?? []),
    ...pendenciasDePedidos(aguardando, filhasPorPedido),
  ];
  return todas.sort((a, b) => tempo(a.desde) - tempo(b.desde) || a.chave.localeCompare(b.chave));
}
