/**
 * Prova 07/10 (FULL INSTAGRAM): a tela "Operação" acompanha ~30 agentes, cada um com persona, conta, aparelho e o estado
 * individual ao longo do pipeline do Instagram. O contrato real é o adendo v1.94 da Jev (`POST/GET /api/operacoes`); até ele
 * existir, a tela lê um JSON fixo (`operacaoDeExemplo.ts`) com o MESMO formato que este leitor aceita, e o leitor é tolerante:
 * campo ausente vira "não informado", nunca erro. Os nomes dos campos são a suposição do Portal (a Jev confirma ou troca num
 * lugar só: este arquivo). Credencial, login e e-mail de entrada NÃO têm campo aqui, de propósito: a conta é só um rótulo.
 */

/** Os estágios do pipeline, na ordem do dono (16:38Z). O alvo parou no primeiro que não alcançou. */
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
  { id: 'interface_de_comentario', rotulo: 'Interface de comentário alcançada' },
  { id: 'acao_preparada', rotulo: 'Ação preparada' },
  { id: 'acao_executada', rotulo: 'Ação executada' },
  { id: 'resultado_verificado', rotulo: 'Resultado verificado' },
] as const;

export type EstagioId = (typeof ESTAGIOS)[number]['id'];
const IDS_DOS_ESTAGIOS: readonly string[] = ESTAGIOS.map((e) => e.id);
export const isEstagio = (v: unknown): v is EstagioId => typeof v === 'string' && IDS_DOS_ESTAGIOS.includes(v);

export type EstadoDoAlvo = 'na_fila' | 'em_andamento' | 'concluido' | 'bloqueado' | 'falhou' | 'cancelado';
export const ESTADOS_DO_ALVO: readonly EstadoDoAlvo[] = ['na_fila', 'em_andamento', 'concluido', 'bloqueado', 'falhou', 'cancelado'];
export const isEstadoDoAlvo = (v: unknown): v is EstadoDoAlvo => typeof v === 'string' && (ESTADOS_DO_ALVO as readonly string[]).includes(v);

export type Verificada = 'sim' | 'nao' | 'nao_conferida';

export interface Bloqueio {
  /** O estágio em que o alvo parou (o primeiro que não alcançou). */
  estagio: EstagioId | null;
  /** O motivo em português, como o backend o escreve; nunca um código. */
  motivo: string;
}

export interface Alvo {
  /** A chave da linha: o id do alvo (ou da execução); nunca o aparelho, que se repete em ondas. */
  id: string;
  profile_id: string | null;
  persona: string | null;
  account_id: string | null;
  /** O rótulo da conta (`@handle`); nunca login nem e-mail de entrada. */
  conta: string | null;
  /** Estado da sessão da conta: `conectada`, `vencida`, `sem_sessao`… como veio; o painel traduz o que conhece. */
  sessao: string | null;
  app: string | null;
  instance_id: string | null;
  servidor: string | null;
  estado: EstadoDoAlvo | null;
  /** O último estágio alcançado; `null` = ainda nenhum (ou o backend não disse). */
  estagio: EstagioId | null;
  bloqueio: Bloqueio | null;
  /** "Conhecimento usado": quantos itens; `null` = não informado. */
  conhecimento_n: number | null;
  /** A ação final (texto da resposta/comentário), quando existe. */
  acao: string | null;
  verificada: Verificada | null;
  evidencia_id: number | null;
  run_id: string | null;
}

export interface Capacidade {
  solicitados: number | null;
  contas_existentes: number | null;
  sessoes_validas: number | null;
  disponiveis: number | null;
  concluidas: number | null;
  bloqueadas: number | null;
  /** Os motivos agrupados dos bloqueios (já em português) com quantos alvos cada um. */
  motivos: { motivo: string; n: number }[];
}

export interface Operacao {
  id: string;
  objetivo: string;
  app: string | null;
  estado: string | null;
  criada_em: string | null;
  capacidade: Capacidade;
  alvos: Alvo[];
  /** Os dados vêm do exemplo fixo, não do backend. */
  exemplo: boolean;
}

const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);
const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

function lerBloqueio(v: unknown): Bloqueio | null {
  const o = registro(v);
  const motivo = o ? texto(o.motivo) : null;
  return o && motivo ? { estagio: isEstagio(o.estagio) ? o.estagio : null, motivo } : null;
}

export function lerAlvo(v: unknown, posicao: number): Alvo | null {
  const o = registro(v);
  if (!o) return null;
  const verificada = o.verificada === 'sim' || o.verificada === 'nao' || o.verificada === 'nao_conferida' ? o.verificada : null;
  return {
    id: texto(o.id) ?? texto(o.run_id) ?? `alvo-${posicao + 1}`,
    profile_id: texto(o.profile_id), persona: texto(o.persona), account_id: texto(o.account_id), conta: texto(o.conta), sessao: texto(o.sessao),
    app: texto(o.app), instance_id: texto(o.instance_id), servidor: texto(o.servidor),
    estado: isEstadoDoAlvo(o.estado) ? o.estado : null,
    estagio: isEstagio(o.estagio) ? o.estagio : null,
    bloqueio: lerBloqueio(o.bloqueio),
    conhecimento_n: inteiro(o.conhecimento_n), acao: texto(o.acao), verificada,
    evidencia_id: inteiro(o.evidencia_id), run_id: texto(o.run_id),
  };
}

function lerCapacidade(v: unknown): Capacidade {
  const o = registro(v) ?? {};
  const motivos = (Array.isArray(o.motivos) ? o.motivos : []).flatMap((m) => {
    const r = registro(m);
    const motivo = r ? texto(r.motivo) : null;
    const n = r ? inteiro(r.n) : null;
    return motivo && n !== null ? [{ motivo, n }] : [];
  });
  return {
    solicitados: inteiro(o.solicitados), contas_existentes: inteiro(o.contas_existentes), sessoes_validas: inteiro(o.sessoes_validas),
    disponiveis: inteiro(o.disponiveis), concluidas: inteiro(o.concluidas), bloqueadas: inteiro(o.bloqueadas), motivos,
  };
}

/** `null` quando o corpo não é uma operação (sem id): a tela diz que não leu, não inventa. */
export function lerOperacao(v: unknown, exemplo = false): Operacao | null {
  const o = registro(v);
  const id = o ? texto(o.id) : null;
  if (!o || !id) return null;
  const alvos = (Array.isArray(o.alvos) ? o.alvos : []).map(lerAlvo).filter((a): a is Alvo => a !== null);
  return {
    id, objetivo: texto(o.objetivo) ?? '', app: texto(o.app), estado: texto(o.estado), criada_em: texto(o.criada_em),
    capacidade: lerCapacidade(o.capacidade), alvos, exemplo,
  };
}

/** Quantos estágios o alvo alcançou (0 a 14); sem estágio, 0. */
export function estagiosAlcancados(a: Pick<Alvo, 'estagio'>): number {
  return a.estagio ? IDS_DOS_ESTAGIOS.indexOf(a.estagio) + 1 : 0;
}

export function rotuloDoEstagio(id: EstagioId | null): string {
  return ESTAGIOS.find((e) => e.id === id)?.rotulo ?? '—';
}

/** O estágio onde o alvo está parado: o que o bloqueio diz, ou o seguinte ao último alcançado. */
export function estagioDeParada(a: Pick<Alvo, 'estagio' | 'bloqueio'>): EstagioId | null {
  if (a.bloqueio?.estagio) return a.bloqueio.estagio;
  const prox = estagiosAlcancados(a);
  return prox < ESTAGIOS.length ? ESTAGIOS[prox]!.id : null;
}

export const ROTULO_DO_ESTADO: Record<EstadoDoAlvo, string> = {
  na_fila: 'Na fila', em_andamento: 'Em andamento', concluido: 'Concluído', bloqueado: 'Bloqueado', falhou: 'Falhou', cancelado: 'Cancelado',
};

const ROTULO_DA_SESSAO: Record<string, string> = {
  conectada: 'Conectada', vencida: 'Vencida', sem_sessao: 'Sem sessão', sem_conta: 'Sem conta', entrando: 'Entrando', saindo: 'Saindo',
};
/** O estado da sessão em palavras; o que o painel não conhece fica como veio. */
export const rotuloDaSessao = (s: string | null): string => (s ? ROTULO_DA_SESSAO[s] ?? s : 'Não informada');

export const ROTULO_DA_VERIFICACAO: Record<Verificada, string> = { sim: 'Verificada', nao: 'Não verificada', nao_conferida: 'Não conferida' };

/** A contagem por estado, para o resumo e o filtro. O que o backend não classificou conta como `null`. */
export function contarPorEstado(alvos: readonly Pick<Alvo, 'estado'>[]): Record<EstadoDoAlvo | 'nao_informado', number> {
  const c: Record<EstadoDoAlvo | 'nao_informado', number> = { na_fila: 0, em_andamento: 0, concluido: 0, bloqueado: 0, falhou: 0, cancelado: 0, nao_informado: 0 };
  for (const a of alvos) c[a.estado ?? 'nao_informado'] += 1;
  return c;
}

/**
 * O agregado por app (Instagram, hoje o único): alvos, concluídos, verificados, bloqueados e falhos. "Concluído" e "verificado"
 * são contagens separadas: concluído sem verificação nunca conta como verificado.
 */
export interface AgregadoDoApp { app: string; alvos: number; concluidos: number; verificados: number; bloqueados: number; falhos: number }
export function agregadoPorApp(alvos: readonly Alvo[]): AgregadoDoApp[] {
  const m = new Map<string, AgregadoDoApp>();
  for (const a of alvos) {
    const app = a.app ?? 'não informado';
    const g = m.get(app) ?? { app, alvos: 0, concluidos: 0, verificados: 0, bloqueados: 0, falhos: 0 };
    g.alvos += 1;
    if (a.estado === 'concluido') g.concluidos += 1;
    if (a.verificada === 'sim') g.verificados += 1;
    if (a.estado === 'bloqueado') g.bloqueados += 1;
    if (a.estado === 'falhou') g.falhos += 1;
    m.set(app, g);
  }
  return Array.from(m.values());
}
