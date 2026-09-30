import type { RunSummary } from '../../api/types';

/**
 * Busca, filtros e título curto da lista de Execuções (tarefa UX 05). Funções puras; a tela lê e grava a URL
 * (`#/execucoes/<id>?q=perfil&status=pendencia&periodo=7d&aparelho=android-01&servidor=worker-lan-01`).
 *
 * Códigos na URL (registrados em `lib/rotas.ts`):
 * - `q`: texto do objetivo (sem acento, sem caixa) ou o código curto da execução.
 * - `status`: `andamento` | `concluida` | `pendencia` | `falha` | `cancelada` (grupos de `RunStatus`, abaixo).
 * - `periodo`: `24h` | `7d` | `30d` (criada nesse intervalo).
 * - `aparelho`: id do aparelho; `servidor`: id do servidor (worker). Estes dois a API filtra (`instance_id`,
 *   `worker_id`); os outros a tela filtra sobre o histórico inteiro.
 */

export const GRUPOS_STATUS = ['andamento', 'concluida', 'pendencia', 'falha', 'cancelada'] as const;
export type GrupoStatus = (typeof GRUPOS_STATUS)[number];
export const PERIODOS = ['24h', '7d', '30d'] as const;
export type Periodo = (typeof PERIODOS)[number];

export const ROTULO_GRUPO: Record<GrupoStatus, string> = {
  andamento: 'Em andamento', concluida: 'Concluídas', pendencia: 'Com pendência', falha: 'Falharam', cancelada: 'Canceladas',
};
export const ROTULO_PERIODO: Record<Periodo, string> = { '24h': 'Últimas 24 horas', '7d': 'Últimos 7 dias', '30d': 'Últimos 30 dias' };

const MS_PERIODO: Record<Periodo, number> = { '24h': 864e5, '7d': 7 * 864e5, '30d': 30 * 864e5 };

/**
 * Cada status da execução num grupo só. "Com pendência" junta o que terminou com algo por resolver e o que parou
 * pedindo informação: nos dois casos alguém precisa olhar.
 */
export function grupoDoStatus(s: RunSummary['status']): GrupoStatus {
  switch (s) {
    case 'completed': return 'concluida';
    case 'completed_with_issues':
    case 'needs_input': return 'pendencia';
    case 'failed': return 'falha';
    case 'cancelled': return 'cancelada';
    default: return 'andamento';      // planning, planned, running, paused, cancelling
  }
}

export interface FiltroExecucoes {
  q: string;
  status: GrupoStatus | null;
  periodo: Periodo | null;
  aparelho: string;
  servidor: string;
}

function umDe<T extends string>(lista: readonly T[], v: string | undefined): T | null {
  return v && (lista as readonly string[]).includes(v) ? (v as T) : null;
}

export function lerFiltroExecucoes(query: Readonly<Record<string, string>>): FiltroExecucoes {
  return {
    q: query.q ?? '',
    status: umDe(GRUPOS_STATUS, query.status),
    periodo: umDe(PERIODOS, query.periodo),
    aparelho: query.aparelho ?? '',
    servidor: query.servidor ?? '',
  };
}

/** Filtros que a TELA aplica (a API não sabe filtrar por eles): com um deles ligado, a tela precisa do histórico todo. */
export function filtroLocalAtivo(f: FiltroExecucoes): boolean {
  return !!(f.q.trim() || f.status || f.periodo);
}

export function filtroAtivo(f: FiltroExecucoes): boolean {
  return filtroLocalAtivo(f) || !!(f.aparelho || f.servidor);
}

export const LIMPAR_FILTROS: Record<string, undefined> = {
  q: undefined, status: undefined, periodo: undefined, aparelho: undefined, servidor: undefined,
};

function normalizar(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

/** Aplica busca, grupo de status e período. `agora` é injetado (teste e relógio da tela). */
export function filtrarExecucoes(runs: readonly RunSummary[], f: FiltroExecucoes, agora: number, excluir?: 'status'): RunSummary[] {
  const alvo = normalizar(f.q.trim());
  const desde = f.periodo ? agora - MS_PERIODO[f.periodo] : null;
  return runs.filter((r) => {
    if (alvo && !normalizar(`${r.command} ${r.short_id}`).includes(alvo)) return false;
    if (excluir !== 'status' && f.status && grupoDoStatus(r.status) !== f.status) return false;
    if (desde !== null) {
      const t = Date.parse(r.created_at);
      if (!Number.isFinite(t) || t < desde) return false;
    }
    return true;
  });
}

/** Quantas execuções cada chip de status mostraria, com os outros filtros aplicados. */
export function contagemPorGrupo(runs: readonly RunSummary[], f: FiltroExecucoes, agora: number): Record<GrupoStatus | 'todas', number> {
  const base = filtrarExecucoes(runs, f, agora, 'status');
  const out = { todas: base.length } as Record<GrupoStatus | 'todas', number>;
  for (const g of GRUPOS_STATUS) out[g] = 0;
  for (const r of base) out[grupoDoStatus(r.status)] += 1;
  return out;
}

export interface Titulo {
  /** Curto, começa pelo que a execução FAZ ("Envie 'Bom dia' para QA-001…"), no máximo `max` caracteres. */
  titulo: string;
  /** O app citado no começo do objetivo ("QA Messenger"), mostrado à parte. */
  app: string | null;
}

// "No Instagram, …", "Na Loja, …", "Abra o QA Messenger e …", "Abra o app Configurações do Android, …". O nome do app
// começa com maiúscula (não confunde com "Nas instâncias selecionadas"), tem até quatro palavras e termina em vírgula
// ou em " e ".
const PREFIXO_APP = /^(?:[Nn][oa]|[Aa]bra\s+(?:o|a)(?:\s+app)?|[Aa]brir\s+(?:o|a)(?:\s+app)?|[Nn]o\s+app)\s+([A-ZÀ-Ý][\wÀ-ÿ.-]*(?:\s+(?:do|da|de)?\s*[A-ZÀ-Ý0-9][\wÀ-ÿ.-]*){0,3})\s*(?:,\s*|\s+e\s+)/;
// Aberturas que não dizem nada e se repetem em dezenas de execuções.
const ABERTURAS_VAZIAS = [
  /^objetivo\s*:\s*/i,
  /^(?:nas|em todas as|em cada uma das)\s+inst[âa]ncias\s+selecionadas\s*,\s*/i,
  /^(?:nos|em todos os|em cada um dos)\s+aparelhos\s+selecionados\s*,\s*/i,
  /^em cada aparelho\s*,\s*/i,
];

function maiuscula(s: string): string {
  return s ? s[0]!.toUpperCase() + s.slice(1) : s;
}

/** Corta no limite sem partir palavra, com reticências. */
export function encurtar(s: string, max: number): string {
  if (s.length <= max) return s;
  const corte = s.slice(0, max - 1);
  const espaco = corte.lastIndexOf(' ');
  return `${(espaco > max * 0.6 ? corte.slice(0, espaco) : corte).replace(/[\s,;:.]+$/, '')}…`;
}

/**
 * Título curto de uma execução: a primeira linha com conteúdo do objetivo, sem a abertura repetida ("Nas instâncias
 * selecionadas,", "Objetivo:") e com o app separado. Antes, 40 itens seguidos começavam com "No QA Messenger, …" e só
 * se distinguiam depois do corte. O objetivo inteiro continua no `title` do item e no detalhe.
 */
export function tituloCurto(command: string, max = 72): Titulo {
  const linha = command.split(/\r?\n/).map((l) => l.trim()).find((l) => l && !/^[-=#*_\s]+$/.test(l)) ?? '';
  let resto = linha;
  for (let i = 0; i < 3; i += 1) {
    const antes = resto;
    for (const re of ABERTURAS_VAZIAS) resto = resto.replace(re, '');
    if (resto === antes) break;
  }
  let app: string | null = null;
  const m = PREFIXO_APP.exec(resto);
  if (m && m[1] && m[0].length < resto.length) {
    app = m[1].replace(/^app\s+/i, '');
    resto = resto.slice(m[0].length);
  }
  // Primeira frase: o resto do objetivo costuma ser a condição de prova ("Confirme que…").
  const frase = /^(.+?[.!?])(?:\s|$)/.exec(resto);
  const curto = frase && frase[1] && frase[1].length >= 12 ? frase[1].replace(/[.]$/, '') : resto;
  const titulo = encurtar(maiuscula(curto.trim()), max);
  return { titulo: titulo || 'Execução sem objetivo escrito', app };
}

/**
 * O histórico que a tela mostra: o que ela paginou do servidor mais o que o store tem ao vivo (a versão do store
 * ganha, é a mais nova), da mais recente para a mais antiga. A tela guarda as páginas por conta própria porque o
 * store só segura as 100 execuções mais recentes (`MAX_RUNS`): sem isto, "Carregar mais" passava de 100 e as
 * antigas sumiam de novo, e a busca nunca achava uma execução além da centésima.
 */
export function unirExecucoes(aoVivo: readonly RunSummary[], paginadas: readonly RunSummary[]): RunSummary[] {
  const porId = new Map<string, RunSummary>();
  for (const r of paginadas) porId.set(r.id, r);
  for (const r of aoVivo) porId.set(r.id, r);
  return [...porId.values()].sort((a, b) => (b.created_at > a.created_at ? 1 : b.created_at < a.created_at ? -1 : a.id.localeCompare(b.id)));
}
