import type { PersonaDTO } from '../../api/types';
import { casaCapacidade, CAPACIDADES, ROTULO_DO_RECORTE, type RecorteDeCapacidade } from './capacidadeDaPersona';
import { handleDe, nomeDe } from './pessoa';

/**
 * Busca, filtros e ordenação da lista de Personas (tarefa UX 05). Tudo aqui é função pura sobre a lista que
 * `GET /personas` devolve e sobre a query do link (`#/personas?situacao=bloqueada&q=ana`): a tela só lê e grava a URL.
 *
 * Códigos na URL (registrados em `lib/rotas.ts`):
 * - `situacao`: `ativa` | `bloqueada` (bloqueada pela plataforma, `status = blocked`: o mesmo critério do contador
 *   "N personas bloqueadas" da saúde do ambiente) | `pausada` | `sem-conta` (sem conta de cadastro) | `atencao`
 *   (ativa, mas a cadeia aparelho → app → sessão tem um problema que alguém precisa resolver).
 * - `vinculo`: `com` | `sem` (tem ou não aparelho vinculado).
 * - `capacidade` (31.255): `prontas` | `sem-conta` | `sem-senha` | `sem-consentimento` | `sem-aparelho` | `sessao` (o que falta para a persona operar).
 * - `grupo`: id do grupo de acesso, ou `nenhum`.
 * - `app`: id do app de algum vínculo da persona (`instagram`, `outlook`…).
 * - `q`: texto; casa com nome e @ (sem acento, sem caixa, com ou sem o "@").
 * - `ordem`: `nome` (padrão, omitido) | `situacao` | `atividade`.
 * - `visao`: `cards` | `tabela`. Sem `visao` no link vale a última escolhida neste navegador (`lib/visao.ts`, decisão
 *   D3); por isso ela vai sempre explícita no link (um link sem ela abriria na preferência de quem o recebe).
 */

export const SITUACOES = ['ativa', 'atencao', 'bloqueada', 'pausada', 'sem-conta'] as const;
export type Situacao = (typeof SITUACOES)[number];
/** Rótulo do chip de cada situação. */
export const ROTULO_SITUACAO: Record<Situacao, string> = {
  ativa: 'Ativas', atencao: 'Precisam de atenção', bloqueada: 'Bloqueadas pela plataforma', pausada: 'Pausadas',
  'sem-conta': 'Sem conta de cadastro',
};

const NOMES_DE_APP: Record<string, string> = { instagram: 'Instagram', outlook: 'Outlook', tiktok: 'TikTok', chrome: 'Chrome' };

/** Nome do app para o filtro (o vínculo guarda o id curto: `instagram`). */
export function nomeDoApp(id: string): string {
  return NOMES_DE_APP[id] ?? (id ? id[0]!.toUpperCase() + id.slice(1) : id);
}

export const ORDENS_PERSONA =['nome', 'situacao', 'atividade'] as const;
export type OrdemPersona = (typeof ORDENS_PERSONA)[number];
export type Visao = 'cards' | 'tabela';
export const VISOES: readonly Visao[] = ['cards', 'tabela'];
/** Chave da preferência no navegador (`lib/storage` põe o prefixo). */
export const CHAVE_VISAO = 'personas.visao';

export interface FiltroPersonas {
  q: string;
  situacao: Situacao | null;
  vinculo: 'com' | 'sem' | null;
  capacidade: RecorteDeCapacidade | null;
  grupo: string | null;
  app: string | null;
  ordem: OrdemPersona;
  visao: Visao;
}

export const FILTRO_VAZIO: FiltroPersonas = {
  q: '', situacao: null, vinculo: null, capacidade: null, grupo: null, app: null, ordem: 'nome', visao: 'cards',
};

function umDe<T extends string>(lista: readonly T[], v: string | undefined): T | null {
  return v && (lista as readonly string[]).includes(v) ? (v as T) : null;
}

/**
 * Lê a query do link. Valor desconhecido vira "sem filtro" (um link velho não esvazia a lista sem dizer por quê). A
 * visão do link manda; sem ela, vale `preferida` (a do navegador).
 */
export function lerFiltroPersonas(query: Readonly<Record<string, string>>, preferida: Visao = 'cards'): FiltroPersonas {
  return {
    q: (query.q ?? '').trim() ? query.q ?? '' : '',
    situacao: umDe(SITUACOES, query.situacao),
    vinculo: umDe(['com', 'sem'] as const, query.vinculo),
    capacidade: umDe(CAPACIDADES, query.capacidade),
    grupo: query.grupo || null,
    app: query.app || null,
    ordem: umDe(ORDENS_PERSONA, query.ordem) ?? 'nome',
    visao: umDe(VISOES, query.visao) ?? preferida,
  };
}

/**
 * O filtro na forma de `trocarQuery`: padrão sai do link (`undefined`), para o link ficar curto e estável. A visão é a
 * exceção: vai sempre, porque o padrão dela é de cada navegador.
 */
export function queryDoFiltro(f: Partial<FiltroPersonas>): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  if ('q' in f) out.q = f.q || undefined;
  if ('situacao' in f) out.situacao = f.situacao ?? undefined;
  if ('vinculo' in f) out.vinculo = f.vinculo ?? undefined;
  if ('capacidade' in f) out.capacidade = f.capacidade ?? undefined;
  if ('grupo' in f) out.grupo = f.grupo ?? undefined;
  if ('app' in f) out.app = f.app ?? undefined;
  if ('ordem' in f) out.ordem = f.ordem && f.ordem !== 'nome' ? f.ordem : undefined;
  if ('visao' in f) out.visao = f.visao === 'tabela' ? 'tabela' : 'cards';
  return out;
}

/** Algum filtro que esconde gente está ligado? (Ordem e visão não escondem ninguém.) */
export function filtroAtivo(f: FiltroPersonas): boolean {
  return !!(f.q.trim() || f.situacao || f.vinculo || f.capacidade || f.grupo || f.app);
}

/** Limpa só o que filtra; ordem e visão são preferência de leitura e ficam. */
export const LIMPAR_FILTROS: Record<string, undefined> = {
  q: undefined, situacao: undefined, vinculo: undefined, capacidade: undefined, grupo: undefined, app: undefined,
};

/** Sem acento e sem caixa: "Vinícius" acha com "vinicius". */
export function normalizar(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
}

/** Fases da sessão que pedem alguém (a persona está ativa, mas não vai trabalhar sozinha). */
const FASES_DE_ATENCAO = new Set(['app_missing', 'challenge', 'wrong_account', 'logged_out', 'no_credential', 'app_unknown']);

/** A situação da persona num código só, para filtro e ordenação. Bloqueada e pausada ganham de "sem conta". */
export function situacaoDe(p: PersonaDTO): Situacao {
  if (p.status === 'blocked') return 'bloqueada';
  if (p.status === 'disabled') return 'pausada';
  if (!handleDe(p)) return 'sem-conta';
  const fase = p.session_actions?.phase;
  if (fase && FASES_DE_ATENCAO.has(fase)) return 'atencao';
  return 'ativa';
}

/** Casa com um código de situação? `ativa` inclui quem pede atenção (continua ativa); `atencao` é só o recorte. */
export function casaSituacao(p: PersonaDTO, s: Situacao): boolean {
  const atual = situacaoDe(p);
  if (s === 'ativa') return atual === 'ativa' || atual === 'atencao';
  return atual === s;
}

export interface EstadoComposto {
  /** Um rótulo só, sem contradição ("Ativa · app não instalado"), que cabe num selo. */
  rotulo: string;
  tom: 'success' | 'warning' | 'danger' | 'neutral' | 'info';
  /** O porquê, para o tooltip e a tabela. */
  explicacao: string;
  /** O que resolver, quando há; a tela só NAVEGA para onde se resolve (não instala nem conecta nada daqui). */
  acao?: { rotulo: string; guia: string };
}

/**
 * Situação e sessão viram UM estado explicado. Antes o cartão mostrava "Ativa" em verde ao lado de "app não
 * instalado" em vermelho, e quem lia não sabia se a persona trabalhava ou não.
 */
export function estadoComposto(p: PersonaDTO): EstadoComposto {
  if (p.status === 'blocked') {
    return { rotulo: 'Bloqueada pela plataforma', tom: 'danger',
             explicacao: 'A plataforma bloqueou a conta. Nenhuma tarefa é despachada até alguém reativar a persona.' };
  }
  if (p.status === 'disabled') {
    return { rotulo: 'Pausada', tom: 'neutral', explicacao: 'Pausada pelo dono: não recebe tarefas.' };
  }
  const fase = p.session_actions?.phase;
  const detalhe = p.session_actions?.detail;
  if (!handleDe(p)) {
    return { rotulo: 'Ativa · sem conta', tom: 'neutral',
             explicacao: 'A pessoa existe, mas ainda não tem conta em nenhum app. Cadastre uma conta na guia Contas e acesso.',
             acao: { rotulo: 'Adicionar conta', guia: 'contas' } };
  }
  switch (fase) {
    case 'app_missing':
      return { rotulo: 'Ativa · app não instalado', tom: 'warning',
               explicacao: detalhe || 'O app da conta não está instalado no aparelho vinculado.',
               acao: { rotulo: 'Instalar app', guia: 'aparelhos' } };
    case 'app_unknown':
      return { rotulo: 'Ativa · app não verificado', tom: 'warning',
               explicacao: detalhe || 'Ainda não se sabe se o app está instalado no aparelho vinculado.',
               acao: { rotulo: 'Ver aparelho', guia: 'aparelhos' } };
    case 'no_credential':
      return { rotulo: 'Ativa · sem senha guardada', tom: 'warning',
               explicacao: detalhe || 'Guarde a senha da conta para a automação poder entrar.',
               acao: { rotulo: 'Guardar senha', guia: 'contas' } };
    case 'logged_out':
      return { rotulo: 'Ativa · deslogada', tom: 'warning',
               explicacao: detalhe || 'A conta está fora do app no aparelho.', acao: { rotulo: 'Ver conta', guia: 'contas' } };  // só leva à guia: "Conectar" prometeria um efeito
    case 'challenge':
      return { rotulo: 'Ativa · desafio de segurança', tom: 'danger',
               explicacao: detalhe || 'O app pediu uma verificação que só uma pessoa resolve.',
               acao: { rotulo: 'Resolver', guia: 'contas' } };
    case 'wrong_account':
      return { rotulo: 'Ativa · outra conta aberta', tom: 'danger',
               explicacao: detalhe || 'O app do aparelho está com outra conta.', acao: { rotulo: 'Resolver', guia: 'contas' } };
    case 'no_device':
      return { rotulo: 'Ativa · sem aparelho', tom: 'neutral',
               explicacao: detalhe || 'Sem aparelho vinculado, a persona não recebe tarefas.',
               acao: { rotulo: 'Vincular aparelho', guia: 'aparelhos' } };
    case 'app_installing':
      return { rotulo: 'Ativa · instalando o app', tom: 'info', explicacao: detalhe || 'O app está sendo instalado.' };
    case 'authenticating':
      return { rotulo: 'Ativa · entrando na conta', tom: 'info', explicacao: detalhe || 'A automação está entrando na conta.' };
    case 'authenticated':
      return { rotulo: 'Ativa · conectada', tom: 'success', explicacao: detalhe || 'Conta aberta no app do aparelho.' };
    default:
      return { rotulo: 'Ativa', tom: 'success', explicacao: detalhe || 'Recebe tarefas; a sessão ainda não foi observada.' };
  }
}

/** Os apps dos vínculos da persona (sem repetir). */
export function appsDe(p: Pick<PersonaDTO, 'devices'>): string[] {
  return [...new Set((p.devices ?? []).map((d) => d.app_id).filter((a): a is string => !!a))];
}

function temAparelho(p: PersonaDTO): boolean {
  return !!p.instance_id || (p.devices?.length ?? 0) > 0;
}

/** Casa com o texto? Nome, @ e nome de exibição, sem acento; "@ana" e "ana" dão no mesmo. */
export function casaBusca(p: PersonaDTO, q: string): boolean {
  const alvo = normalizar(q).replace(/^@/, '');
  if (!alvo) return true;
  const campos = [nomeDe(p), handleDe(p) ?? '', p.display_name ?? ''].map(normalizar);
  return campos.some((c) => c.includes(alvo));
}

/** Aplica os filtros (todos juntos, "e"); o que fica de fora de `f` não filtra. */
export function filtrarPersonas(pessoas: readonly PersonaDTO[], f: FiltroPersonas, excluir?: keyof FiltroPersonas): PersonaDTO[] {
  return pessoas.filter((p) =>
    (excluir === 'q' || casaBusca(p, f.q))
    && (excluir === 'situacao' || !f.situacao || casaSituacao(p, f.situacao))
    && (excluir === 'vinculo' || !f.vinculo || (f.vinculo === 'com') === temAparelho(p))
    && (excluir === 'capacidade' || !f.capacidade || casaCapacidade(p, f.capacidade))
    && (excluir === 'grupo' || !f.grupo || (f.grupo === 'nenhum' ? !p.policy_group_id : p.policy_group_id === f.grupo))
    && (excluir === 'app' || !f.app || appsDe(p).includes(f.app)));
}

/** Ordem da situação: o que pede alguém primeiro. */
const PESO_SITUACAO: Record<Situacao, number> = { bloqueada: 0, atencao: 1, 'sem-conta': 2, pausada: 3, ativa: 4 };

const COLLATOR = new Intl.Collator('pt-BR', { sensitivity: 'base', numeric: true });

/** Última atividade conhecida: a do trabalho; senão a última verificação; senão a última edição. */
export function ultimaAtividade(p: PersonaDTO): string | null {
  return p.last_activity_at ?? p.last_verified_at ?? p.updated_at ?? null;
}

export function ordenarPersonas(pessoas: readonly PersonaDTO[], ordem: OrdemPersona): PersonaDTO[] {
  const porNome = (a: PersonaDTO, b: PersonaDTO) => COLLATOR.compare(nomeDe(a), nomeDe(b));
  const lista = [...pessoas];
  if (ordem === 'situacao') {
    return lista.sort((a, b) => PESO_SITUACAO[situacaoDe(a)] - PESO_SITUACAO[situacaoDe(b)] || porNome(a, b));
  }
  if (ordem === 'atividade') {
    // Mais recente primeiro; quem nunca teve atividade vai para o fim, em ordem de nome.
    return lista.sort((a, b) => {
      const ta = ultimaAtividade(a) ?? '';
      const tb = ultimaAtividade(b) ?? '';
      if (ta !== tb) return tb.localeCompare(ta);
      return porNome(a, b);
    });
  }
  return lista.sort(porNome);
}

/**
 * Quantas personas cada chip mostraria, com os OUTROS filtros aplicados (o número do chip é o que a pessoa vai ver
 * ao clicar, não o total da base).
 */
export function contagemPorSituacao(pessoas: readonly PersonaDTO[], f: FiltroPersonas): Record<Situacao | 'todas', number> {
  const base = filtrarPersonas(pessoas, f, 'situacao');
  const out = { todas: base.length } as Record<Situacao | 'todas', number>;
  for (const s of SITUACOES) out[s] = base.filter((p) => casaSituacao(p, s)).length;
  return out;
}

/** O texto do estado vazio: diz QUAL filtro esvaziou a lista ("Nenhuma persona bloqueada"). */
export function textoSemResultado(f: FiltroPersonas): string {
  const partes: string[] = [];
  const porSituacao: Record<Situacao, string> = {
    ativa: 'ativa', atencao: 'precisando de atenção', bloqueada: 'bloqueada', pausada: 'pausada',
    'sem-conta': 'sem conta de cadastro',
  };
  if (f.situacao) partes.push(porSituacao[f.situacao]);
  if (f.vinculo) partes.push(f.vinculo === 'com' ? 'com aparelho' : 'sem aparelho');
  if (f.capacidade) partes.push(ROTULO_DO_RECORTE[f.capacidade].toLowerCase());
  if (f.q.trim()) partes.push(`com "${f.q.trim()}" no nome ou no @`);
  const base = `Nenhuma persona ${partes.join(', ')}`.trim();
  return partes.length || f.grupo || f.app ? `${base}${f.grupo || f.app ? ' nesse recorte' : ''}.` : 'Nenhuma persona.';
}

/** Quantas personas cada recorte de capacidade mostraria, com os OUTROS filtros aplicados (o número do chip é o que se vê ao clicar). */
export function contagemPorCapacidade(pessoas: readonly PersonaDTO[], f: FiltroPersonas): Record<RecorteDeCapacidade | 'todas', number> {
  const base = filtrarPersonas(pessoas, f, 'capacidade');
  const out = { todas: base.length } as Record<RecorteDeCapacidade | 'todas', number>;
  for (const r of CAPACIDADES) out[r] = base.filter((p) => casaCapacidade(p, r)).length;
  return out;
}
