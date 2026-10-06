/**
 * Prova 07/10 (FULL INSTAGRAM): a tela "Operação" acompanha ~30 agentes, cada um com persona, conta, aparelho e o estado
 * individual ao longo do pipeline do app. O contrato é o rascunho do adendo v1.94 da Jev (`POST/GET /api/operacoes`, commit
 * 9da5017d): `OperacaoResumo`, `OperacaoDetalhe`, `AlvoDaOperacao`, `capacidade`. O leitor é tolerante (campo ausente vira
 * "não informado", nunca erro) e fica isolado aqui: se a Jev mudar um nome, muda neste arquivo. Credencial, login e e-mail de
 * entrada NÃO têm campo aqui, de propósito: a conta é só o rótulo (`conta`, o @).
 */

/** Os estágios do pipeline, na ordem fixa do dono e do adendo. `acao_executada` e `acao_bloqueada` ocupam a mesma posição. */
export const ESTAGIOS = [
  { id: 'persona', rotulo: 'Persona' },
  { id: 'conta', rotulo: 'Conta' },
  { id: 'sessao', rotulo: 'Sessão' },
  { id: 'aparelho', rotulo: 'Aparelho' },
  { id: 'instagram_aberto', rotulo: 'Instagram aberto' },
  { id: 'target_localizado', rotulo: 'Perfil-alvo localizado' },
  { id: 'post_localizado', rotulo: 'Post localizado' },
  { id: 'conteudo_lido', rotulo: 'Conteúdo lido' },
  { id: 'conhecimento_recuperado', rotulo: 'Conhecimento recuperado' },
  { id: 'resposta_gerada', rotulo: 'Resposta gerada' },
  { id: 'interface_de_comentario_alcancada', rotulo: 'Interface de comentário alcançada' },
  { id: 'acao_preparada', rotulo: 'Ação preparada' },
  { id: 'acao_executada', rotulo: 'Ação executada' },
  { id: 'resultado_verificado', rotulo: 'Resultado verificado' },
] as const;

export type EstagioId = (typeof ESTAGIOS)[number]['id'];
/** O estágio que o backend também manda no lugar de `acao_executada` quando a ação foi barrada. */
const ALIAS_DE_ESTAGIO: Readonly<Record<string, EstagioId>> = { acao_bloqueada: 'acao_executada', app_aberto: 'instagram_aberto' };
const IDS_DOS_ESTAGIOS: readonly string[] = ESTAGIOS.map((e) => e.id);

/** O estágio do vocabulário do painel para o que veio (`acao_bloqueada` e `app_aberto` têm lugar fixo); `null` se desconhecido. */
export function lerEstagio(v: unknown): EstagioId | null {
  if (typeof v !== 'string') return null;
  if (IDS_DOS_ESTAGIOS.includes(v)) return v as EstagioId;
  return ALIAS_DE_ESTAGIO[v] ?? null;
}
export const isEstagio = (v: unknown): v is EstagioId => typeof v === 'string' && IDS_DOS_ESTAGIOS.includes(v);

export type EstadoDoAlvo = 'pendente' | 'em_curso' | 'concluido' | 'bloqueado' | 'cancelado';
export const ESTADOS_DO_ALVO: readonly EstadoDoAlvo[] = ['pendente', 'em_curso', 'concluido', 'bloqueado', 'cancelado'];
export const isEstadoDoAlvo = (v: unknown): v is EstadoDoAlvo => typeof v === 'string' && (ESTADOS_DO_ALVO as readonly string[]).includes(v);

export type StatusDaOperacao = 'em_curso' | 'concluida' | 'concluida_com_bloqueios' | 'cancelada';
const STATUS: readonly string[] = ['em_curso', 'concluida', 'concluida_com_bloqueios', 'cancelada'];

export interface AcaoFinal {
  /** A chave da ação de efeito (ex.: `CREATE_COMMENT`); o painel traduz o que conhece. */
  tipo: string | null;
  /** `true` só com a pós-condição comprovada; `false`, tentada e não comprovada; `null`, não informada. */
  verificada: boolean | null;
  evidencia_id: number | null;
}

export interface Resultado {
  texto: string | null;
  conhecimento_ids: string[];
  /** A captura da tela lida para escrever. */
  evidencia_id: number | null;
  acao_final: AcaoFinal | null;
}

export interface Alvo {
  /** A chave da linha: a execução (`run_id`) ou a persona; nunca o aparelho, que se repete em ondas. */
  id: string;
  profile_id: string | null;
  persona: string | null;
  app_id: string | null;
  account_id: string | null;
  /** O rótulo da conta (o @); nunca login nem e-mail de entrada. */
  conta: string | null;
  instance_id: string | null;
  run_id: string | null;
  /** O último estágio alcançado; `null` = ainda nenhum (ou o backend não disse). */
  estagio: EstagioId | null;
  /** Os estágios alcançados, com a hora; vazio quando o backend não os manda. */
  estagios: { estagio: EstagioId; em: string | null }[];
  estado: EstadoDoAlvo | null;
  /** O estágio em que o alvo parou (só em `bloqueado`/`cancelado`); o backend manda, o painel não calcula o seguinte. */
  parou_em: EstagioId | null;
  /** Frase curta e estável do backend (`sem conta`, `sem sessão`, `aguarda aprovação`…); nunca um código. */
  motivo: string | null;
  resultado: Resultado | null;
}

export interface Capacidade {
  solicitados: number | null;
  contas_existentes: number | null;
  sessoes_validas: number | null;
  contas_disponiveis: number | null;
  concluidas: number | null;
  bloqueadas: number | null;
  em_curso: number | null;
  /** Os motivos agrupados dos bloqueios, com quantos alvos cada um. */
  motivos: { motivo: string; n: number }[];
}

export interface ResumoDaOperacao {
  id: string;
  command: string;
  app_id: string | null;
  acao_final: string | null;
  status: StatusDaOperacao | null;
  created_at: string | null;
  finished_at: string | null;
  capacidade: Capacidade;
}

export interface Operacao extends ResumoDaOperacao {
  alvos: Alvo[];
  custo_usd: number | null;
  /** Os dados vêm do exemplo fixo (a rota ainda não existe no backend), não do parque. */
  exemplo: boolean;
}

const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);
const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

function lerAcaoFinal(v: unknown): AcaoFinal | null {
  const o = registro(v);
  return o ? { tipo: texto(o.tipo), verificada: typeof o.verificada === 'boolean' ? o.verificada : null, evidencia_id: inteiro(o.evidencia_id) } : null;
}

function lerResultado(v: unknown): Resultado | null {
  const o = registro(v);
  if (!o) return null;
  return {
    texto: texto(o.texto),
    conhecimento_ids: (Array.isArray(o.conhecimento_ids) ? o.conhecimento_ids : []).filter((x): x is string => typeof x === 'string' && x.trim() !== ''),
    evidencia_id: inteiro(o.evidencia_id), acao_final: lerAcaoFinal(o.acao_final),
  };
}

export function lerAlvo(v: unknown, posicao: number): Alvo | null {
  const o = registro(v);
  if (!o) return null;
  const estagios = (Array.isArray(o.estagios) ? o.estagios : []).flatMap((e) => {
    const r = registro(e);
    const est = r ? lerEstagio(r.estagio) : null;
    return r && est ? [{ estagio: est, em: texto(r.em) }] : [];
  });
  return {
    id: texto(o.run_id) ?? texto(o.profile_id) ?? `alvo-${posicao + 1}`,
    profile_id: texto(o.profile_id), persona: texto(o.persona_nome), app_id: texto(o.app_id), account_id: texto(o.account_id),
    conta: texto(o.conta), instance_id: texto(o.instance_id), run_id: texto(o.run_id),
    estagio: lerEstagio(o.estagio), estagios, estado: isEstadoDoAlvo(o.estado) ? o.estado : null, parou_em: lerEstagio(o.parou_em), motivo: texto(o.motivo),
    resultado: lerResultado(o.resultado),
  };
}

export function lerCapacidade(v: unknown): Capacidade {
  const o = registro(v) ?? {};
  const bruto = registro(o.motivos) ?? {};
  const motivos = Object.entries(bruto).flatMap(([motivo, n]) => (motivo.trim() && inteiro(n) !== null ? [{ motivo, n: inteiro(n)! }] : []))
    .sort((a, b) => b.n - a.n);
  return {
    solicitados: inteiro(o.solicitados), contas_existentes: inteiro(o.contas_existentes), sessoes_validas: inteiro(o.sessoes_validas),
    contas_disponiveis: inteiro(o.contas_disponiveis), concluidas: inteiro(o.concluidas), bloqueadas: inteiro(o.bloqueadas),
    em_curso: inteiro(o.em_curso), motivos,
  };
}

/** A resposta do "Liberar": decisão item a item; o que foi recusado vem com o motivo, e a operação já relida. */
export interface ResultadoDaLiberacao { liberados: string[]; recusados: { profile_id: string; motivo: string }[]; operacao: Operacao | null }
export function lerLiberacao(v: unknown): ResultadoDaLiberacao | null {
  const o = registro(v);
  if (!o || !Array.isArray(o.liberados) || !Array.isArray(o.recusados)) return null;
  return {
    liberados: o.liberados.filter((x): x is string => typeof x === 'string'),
    recusados: o.recusados.flatMap((r) => { const x = registro(r); const p = x && texto(x.profile_id); const m = x && texto(x.motivo); return p && m ? [{ profile_id: p, motivo: m }] : []; }),
    operacao: lerOperacao(o.operacao),
  };
}

/** O motivo do alvo que parou no teto de ações executadas (a frase estável do backend). */
export const MOTIVO_DO_LIMITE = 'limite de ações executadas';

/**
 * Os alvos que esperam a liberação da ação final: pararam em `acao_preparada`, com o texto gerado e a pessoa do alvo, e ainda não
 * executaram. É a lista do "Liberar": a pessoa vê o texto e libera exatamente ele (nenhuma aprovação automática).
 */
export interface AlvoPreparado { profile_id: string; persona: string; conta: string | null; texto: string }
export function alvosPreparados(alvos: readonly Alvo[]): AlvoPreparado[] {
  return alvos.flatMap((a) => {
    const texto = a.resultado?.texto;
    const acaoFeita = a.resultado?.acao_final !== null && a.resultado?.acao_final !== undefined && a.estado === 'concluido';
    if (!a.profile_id || !texto || a.estagio !== 'acao_preparada' || acaoFeita || a.estado === 'concluido' || a.estado === 'cancelado') return [];
    return [{ profile_id: a.profile_id, persona: a.persona ?? 'Persona não informada', conta: a.conta, texto }];
  });
}

/** Quantas contas já executaram a ação final (contam no limite configurado). */
export const acoesJaExecutadas = (alvos: readonly Alvo[]): number =>
  alvos.filter((a) => a.estado === 'concluido' && a.resultado?.acao_final !== null && a.resultado?.acao_final !== undefined).length;

/** `null` quando o corpo não é uma operação (sem id): a tela diz que não leu, não inventa. */
export function lerResumo(v: unknown): ResumoDaOperacao | null {
  const o = registro(v);
  const id = o ? texto(o.id) : null;
  if (!o || !id) return null;
  return {
    id, command: texto(o.command) ?? '', app_id: texto(o.app_id), acao_final: texto(o.acao_final),
    status: typeof o.status === 'string' && STATUS.includes(o.status) ? (o.status as StatusDaOperacao) : null,
    created_at: texto(o.created_at), finished_at: texto(o.finished_at), capacidade: lerCapacidade(o.capacidade),
  };
}

export function lerOperacao(v: unknown, exemplo = false): Operacao | null {
  const resumo = lerResumo(v);
  const o = registro(v);
  if (!resumo || !o) return null;
  const alvos = (Array.isArray(o.alvos) ? o.alvos : []).map(lerAlvo).filter((a): a is Alvo => a !== null);
  return { ...resumo, alvos, custo_usd: typeof o.custo_usd === 'number' && Number.isFinite(o.custo_usd) ? o.custo_usd : null, exemplo };
}

export function lerLista(v: unknown): ResumoDaOperacao[] {
  const itens = registro(v)?.items;
  return (Array.isArray(itens) ? itens : []).map(lerResumo).filter((r): r is ResumoDaOperacao => r !== null);
}

/** Quantos estágios o alvo alcançou (0 a 14): a lista `estagios` quando vem, senão a posição do último. */
export function estagiosAlcancados(a: Pick<Alvo, 'estagio' | 'estagios'>): number {
  if (a.estagios.length) return new Set(a.estagios.map((e) => e.estagio)).size;
  return a.estagio ? IDS_DOS_ESTAGIOS.indexOf(a.estagio) + 1 : 0;
}

export function rotuloDoEstagio(id: EstagioId | null): string {
  return ESTAGIOS.find((e) => e.id === id)?.rotulo ?? '—';
}

/** O estágio onde o alvo está parado: o `parou_em` do backend; sem ele (backend antigo), o seguinte ao último alcançado; `null` ao fim. */
export function estagioDeParada(a: Pick<Alvo, 'estagio' | 'estagios'> & { parou_em?: EstagioId | null }): EstagioId | null {
  if (a.parou_em) return a.parou_em;
  const prox = estagiosAlcancados(a);
  return prox < ESTAGIOS.length ? ESTAGIOS[prox]!.id : null;
}

export const ROTULO_DO_ESTADO: Record<EstadoDoAlvo, string> = {
  pendente: 'Na fila', em_curso: 'Em andamento', concluido: 'Concluído', bloqueado: 'Bloqueado', cancelado: 'Cancelado',
};

export const ROTULO_DO_STATUS: Record<StatusDaOperacao, string> = {
  em_curso: 'Em andamento', concluida: 'Concluída', concluida_com_bloqueios: 'Concluída com bloqueios', cancelada: 'Cancelada',
};

const ROTULO_DA_ACAO: Record<string, string> = { CREATE_COMMENT: 'Comentário', SEND_MESSAGE: 'Mensagem', preparar: 'Só preparar', executar: 'Preparar e executar' };
/** A ação em palavras; a chave que o painel não conhece fica como veio. */
export const rotuloDaAcao = (tipo: string | null): string => (tipo ? ROTULO_DA_ACAO[tipo] ?? tipo : 'não informada');

export type Verificacao = 'verificada' | 'nao_verificada' | 'sem_acao';
/** "Verificada" só com a pós-condição comprovada; ação tentada sem prova é "não verificada"; sem ação final, nada a verificar. */
export function verificacaoDoAlvo(a: Pick<Alvo, 'resultado'>): Verificacao {
  const f = a.resultado?.acao_final;
  if (!f) return 'sem_acao';
  return f.verificada === true ? 'verificada' : 'nao_verificada';
}
export const ROTULO_DA_VERIFICACAO: Record<Verificacao, string> = { verificada: 'Verificada', nao_verificada: 'Não verificada', sem_acao: '—' };

/** A contagem por estado, para o filtro. O que o backend não classificou conta como `nao_informado`. */
export function contarPorEstado(alvos: readonly Pick<Alvo, 'estado'>[]): Record<EstadoDoAlvo | 'nao_informado', number> {
  const c: Record<EstadoDoAlvo | 'nao_informado', number> = { pendente: 0, em_curso: 0, concluido: 0, bloqueado: 0, cancelado: 0, nao_informado: 0 };
  for (const a of alvos) c[a.estado ?? 'nao_informado'] += 1;
  return c;
}

/**
 * O agregado por app: alvos, concluídos, verificados e bloqueados. "Concluído" e "verificado" são contagens separadas:
 * concluído sem verificação nunca conta como verificado.
 */
export interface AgregadoDoApp { app: string; alvos: number; concluidos: number; verificados: number; bloqueados: number }
export function agregadoPorApp(alvos: readonly Alvo[], appDaOperacao: string | null): AgregadoDoApp[] {
  const m = new Map<string, AgregadoDoApp>();
  for (const a of alvos) {
    const app = a.app_id ?? appDaOperacao ?? 'não informado';
    const g = m.get(app) ?? { app, alvos: 0, concluidos: 0, verificados: 0, bloqueados: 0 };
    g.alvos += 1;
    if (a.estado === 'concluido') g.concluidos += 1;
    if (verificacaoDoAlvo(a) === 'verificada') g.verificados += 1;
    if (a.estado === 'bloqueado') g.bloqueados += 1;
    m.set(app, g);
  }
  return Array.from(m.values());
}
