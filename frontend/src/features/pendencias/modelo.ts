/**
 * A caixa única de pendências (revisão de UX, tarefa 06): tudo o que espera uma decisão da pessoa, de quatro origens
 * que antes viviam em telas diferentes.
 *
 * - **Aprendizado**: itens da fila "Para aprovar" (efeito fora do sistema ou texto de pessoa), um por item.
 * - **Persona**: textos que uma persona quer publicar e esperam aprovação, um por aprovação.
 * - **Execução**: execuções com objetivos aguardando uma ação sua ou com resultado incerto, UMA LINHA POR EXECUÇÃO
 *   (o número de objetivos vai no texto da linha).
 * - **Intervenção**: sessões de conta que só uma pessoa resolve (login, desafio, conta errada), uma por persona — a
 *   fila "Aguardando intervenção" de Personas (RF-03 da revisão final).
 *
 * O contador do menu e a lista saem da mesma função (`montarPendencias`): o total é sempre o número de linhas. O
 * "aguardando você" do cabeçalho continua contando OBJETIVOS (definição da tarefa 02, `store/metricas.ts`); as duas
 * leituras vêm do mesmo seletor de execuções (`execucoesAguardando`) e a diferença está escrita na tela.
 *
 * A ação primária de cada linha LEVA à tela onde se decide (com a evidência ao lado); a caixa não aprova nem recusa
 * nada: decidir sem ver o contexto é o erro que a aprovação existe para evitar.
 */
import type { Approval, PersonaDTO, RunSummary } from '../../api/types';
import { ACCOUNT_SESSION_STATUS, metaOf } from '../../lib/status';
import type { Destino } from '../../store/ui';
import { execucoesAguardando } from '../../store/metricas';
import { rotuloDoKind, type EntradaDoLivro } from '../aprendizado/model';
import { tituloCurto } from '../runs/filtroExecucoes';

export type OrigemDaPendencia = 'aprendizado' | 'persona' | 'execucao' | 'intervencao';

export const ROTULO_DA_ORIGEM: Record<OrigemDaPendencia, string> = {
  aprendizado: 'Aprendizado',
  persona: 'Persona',
  execucao: 'Execução',
  intervencao: 'Intervenção',
};

/**
 * Estados de sessão que só uma pessoa resolve (login, desafio de segurança, conta errada) — mesmo conjunto do backend
 * (achado #106). Um só lugar: a fila "Aguardando intervenção" de Personas e a caixa de pendências leem daqui.
 */
export const PRECISA_DE_PESSOA: ReadonlySet<string> = new Set(['auth_challenge', 'wrong_account', 'needs_person']);

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
    destino: { tela: 'aprendizado', query: { aba: 'aprovar' } },
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

export function pendenciasDeExecucoes(runs: readonly RunSummary[]): Pendencia[] {
  return execucoesAguardando(runs).map(({ run, objetivos }) => {
    // O MESMO título da lista de Execuções (RF-09): a primeira linha crua repetia "Nas instâncias selecionadas, No
    // QA Messenger, …" e a mesma execução aparecia com dois títulos. O app citado vai para o detalhe, como lá.
    const { titulo, app } = tituloCurto(run.command);
    return {
      chave: `execucao:${run.id}`,
      origem: 'execucao',
      titulo,
      detalhe: `${app ? `${app} · ` : ''}${objetivos} ${objetivos === 1 ? 'objetivo aguarda' : 'objetivos aguardam'} você ou ${objetivos === 1 ? 'tem' : 'têm'} resultado incerto`,
      desde: run.created_at,
      acao: 'Abrir execução',
      destino: { tela: 'execucoes', segmentos: [run.id] },
    };
  });
}

/**
 * Sessões que esperam uma pessoa (RF-03 da revisão final): a mesma fila "Aguardando intervenção" de Personas — persona
 * com conta e sessão em login, desafio ou conta errada. A decisão continua lá (assumir o controle do aparelho e
 * resolver na tela); a caixa só leva até ela.
 */
export function pendenciasDeSessoes(personas: readonly PersonaDTO[]): Pendencia[] {
  return personas.filter((p) => p.username && PRECISA_DE_PESSOA.has(p.session.status)).map((p) => {
    const nome = p.display_name || p.name;
    const aparelho = p.session.instance_id ?? p.instance_id;
    return {
      chave: `intervencao:${p.id}`,
      origem: 'intervencao',
      titulo: nome && nome !== p.username ? `${nome} (@${p.username})` : `@${p.username}`,
      detalhe: `${metaOf(ACCOUNT_SESSION_STATUS, p.session.status).label} · ${aparelho ?? 'sem aparelho vinculado'}`
        + ' · só uma pessoa resolve',
      desde: p.session.verified_at,
      acao: 'Resolver',
      destino: { tela: 'personas' },
    };
  });
}

export interface EntradasDaCaixa {
  aprendizado: readonly EntradaDoLivro[] | null;
  aprovacoes: readonly Approval[] | null;
  execucoes: readonly RunSummary[];
  /** Personas (`GET /personas`, a mesma leitura da tela Personas): delas saem as sessões que pedem pessoa. */
  personas?: readonly PersonaDTO[] | null;
  nomeDaPersona?: (id: string | null) => string | null;
}

/** A lista e o total do menu: a mesma conta. Mais antigas primeiro (é o que está esperando há mais tempo). */
export function montarPendencias(e: EntradasDaCaixa): Pendencia[] {
  const todas = [
    ...pendenciasDeAprendizado(e.aprendizado ?? []),
    ...pendenciasDeAprovacoes(e.aprovacoes ?? [], e.nomeDaPersona ?? (() => null)),
    ...pendenciasDeExecucoes(e.execucoes),
    ...pendenciasDeSessoes(e.personas ?? []),
  ];
  return todas.sort((a, b) => tempo(a.desde) - tempo(b.desde) || a.chave.localeCompare(b.chave));
}
