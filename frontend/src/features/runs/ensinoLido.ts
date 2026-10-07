/**
 * 31.226: o ensino a partir de UMA execução (adendo v1.120, 31.221): por etapa, a receita candidata que nasceu dela ou o motivo de não ter
 * nascido; e o resultado de "Ensinar a partir da execução" (candidate → validated → published). Leitura tolerante: campo torto vira
 * "não informado", nunca um valor inventado, e um motivo que o painel não conhece aparece com o código cru em vez de sumir.
 */
export const MOTIVOS_DO_ENSINO = [
  'execucao_simulada', 'com_efeito', 'nao_concluida', 'ja_por_receita', 'sem_ator',
  'caminho_nao_reproduzivel', 'sem_receita', 'receita_ja_vale', 'receita_fora_de_circulacao',
] as const;
export type MotivoDoEnsino = (typeof MOTIVOS_DO_ENSINO)[number];

export interface ReceitaDaEtapa { id: string; status: string | null; replayOk: boolean | null }

export interface EtapaDoEnsino {
  stepId: string;
  chave: string;
  capacidade: string | null;
  status: string | null;
  drivenBy: string | null;
  /** O id da persona (o servidor nunca manda o nome). */
  persona: string | null;
  receita: ReceitaDaEtapa | null;
  ensinavel: boolean;
  /** `null` = sem motivo; um texto fora da lista fechada é guardado cru em `motivoDesconhecido`. */
  motivo: MotivoDoEnsino | null;
  motivoDesconhecido: string | null;
  ferramentasNaoReproduziveis: string[];
}

export interface Promovida { receitaId: string; etapa: string }
export interface Recusada { receitaId: string; etapa: string; codigo: string | null; mensagem: string | null }

export interface EnsinoDaExecucao {
  runId: string;
  status: string | null;
  simulada: boolean | null;
  ensinaveis: number | null;
  etapas: EtapaDoEnsino[];
  /** Só na resposta do POST. */
  promovidas: Promovida[] | null;
  recusadas: Recusada[] | null;
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
const id = (v: unknown): string | null => texto(v) ?? (typeof v === 'number' && Number.isFinite(v) ? String(v) : null);
const eMotivo = (v: string): v is MotivoDoEnsino => (MOTIVOS_DO_ENSINO as readonly string[]).includes(v);

function lerReceita(v: unknown): ReceitaDaEtapa | null {
  const o = registro(v);
  const rid = o ? id(o.id) : null;
  if (!o || !rid) return null;
  return { id: rid, status: texto(o.status), replayOk: typeof o.replay_ok === 'boolean' ? o.replay_ok : null };
}

function lerEtapa(v: unknown): EtapaDoEnsino | null {
  const o = registro(v);
  if (!o) return null;
  const stepId = texto(o.step_id);
  const chave = texto(o.key);
  if (!stepId || !chave) return null;
  const bruto = texto(o.motivo);
  return {
    stepId, chave, capacidade: texto(o.capability), status: texto(o.status), drivenBy: texto(o.driven_by), persona: id(o.persona),
    receita: lerReceita(o.receita), ensinavel: o.ensinavel === true,
    motivo: bruto && eMotivo(bruto) ? bruto : null, motivoDesconhecido: bruto && !eMotivo(bruto) ? bruto : null,
    ferramentasNaoReproduziveis: Array.isArray(o.ferramentas_nao_reproduziveis)
      ? o.ferramentas_nao_reproduziveis.filter((f): f is string => typeof f === 'string' && f.trim() !== '') : [],
  };
}

export function lerEnsinoDaExecucao(v: unknown): EnsinoDaExecucao | null {
  const o = registro(v);
  const runId = o ? texto(o.run_id) : null;
  if (!o || !runId || !Array.isArray(o.etapas)) return null;
  const promovidas = Array.isArray(o.promovidas)
    ? o.promovidas.flatMap((p): Promovida[] => { const r = registro(p); const rid = r ? id(r.recipe_id) : null; return r && rid ? [{ receitaId: rid, etapa: texto(r.step_key) ?? '' }] : []; }) : null;
  const recusadas = Array.isArray(o.recusadas)
    ? o.recusadas.flatMap((p): Recusada[] => {
      const r = registro(p);
      const rid = r ? id(r.recipe_id) : null;
      return r && rid ? [{ receitaId: rid, etapa: texto(r.step_key) ?? '', codigo: texto(r.code), mensagem: texto(r.message) }] : [];
    }) : null;
  return {
    runId, status: texto(o.status), simulada: typeof o.simulada === 'boolean' ? o.simulada : null,
    ensinaveis: typeof o.ensinaveis === 'number' && Number.isFinite(o.ensinaveis) && o.ensinaveis >= 0 ? Math.trunc(o.ensinaveis) : null,
    etapas: o.etapas.flatMap((e) => { const x = lerEtapa(e); return x ? [x] : []; }),
    promovidas, recusadas,
  };
}

const ROTULO_DA_FERRAMENTA: Record<string, string> = { press_back: 'Voltar (press_back)', commit_guard: 'trava de efeito (commit_guard)' };

/** O motivo de a etapa não ter virado receita, em palavras. */
export function motivoEmPalavras(e: EtapaDoEnsino): string | null {
  if (e.motivoDesconhecido) return `Motivo que este painel não conhece: ${e.motivoDesconhecido}.`;
  switch (e.motivo) {
    case null: return null;
    case 'execucao_simulada': return 'A execução foi simulada: não há o que ensinar a partir dela.';
    case 'com_efeito': return 'A etapa tem efeito no app (publica, comenta, envia): não vira receita.';
    case 'nao_concluida': return 'A etapa não terminou com sucesso.';
    case 'ja_por_receita': return 'A etapa já rodou por uma receita: nada novo a ensinar.';
    case 'sem_ator': return 'Nenhum ator (IA) conduziu a etapa: não há caminho a gravar.';
    case 'caminho_nao_reproduzivel': {
      const f = e.ferramentasNaoReproduziveis.map((x) => ROTULO_DA_FERRAMENTA[x] ?? x);
      return f.length ? `O caminho usou ferramentas que a receita não reproduz: ${f.join(', ')}.` : 'O caminho usou ferramentas que a receita não reproduz.';
    }
    case 'sem_receita': return 'Nenhuma receita candidata nasceu desta etapa.';
    case 'receita_ja_vale': return 'A receita nascida dela já vale (publicada).';
    case 'receita_fora_de_circulacao': return 'A receita nascida dela está fora de circulação.';
  }
}

const ROTULO_DA_RECEITA: Record<string, string> = {
  candidate: 'candidata', validated: 'validada', published: 'publicada', active: 'ativa', retired: 'aposentada', rejected: 'rejeitada', archived: 'arquivada',
};
export const rotuloDaReceita = (status: string | null): string => (status ? ROTULO_DA_RECEITA[status] ?? status : 'estado não informado');

export function receitaEmPalavras(r: ReceitaDaEtapa): string {
  const replay = r.replayOk === null ? 'repetição não conferida' : r.replayOk ? 'repetição conferida' : 'repetição falhou';
  return `Receita ${r.id}: ${rotuloDaReceita(r.status)}, ${replay}`;
}
