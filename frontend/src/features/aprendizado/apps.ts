import type { Tone } from '../../lib/status';
import { useUiStore } from '../../store/ui';
import { isEstadoDoLivro, rotuloDoEstado, rotuloDoKind, type EntradaDoLivro } from './model';

/**
 * A visão por aplicativo (Fase 30, itens 30.1 e 30.2; adendo v0.47 do contrato): o aprendizado do ponto de vista de
 * cada app, do Global para o App. Os tipos espelham `presentation/apps.py`; a leitura é tolerante porque o painel pode
 * chegar antes do backend (ou depois) e uma resposta incompleta nunca deve derrubar a tela.
 *
 * Nenhum pacote aparece aqui (ADR-052): nome, pacote e existência vêm dos dados.
 */

export type Existencia = 'declarado' | 'loja' | 'so_aprendido';
const EXISTENCIAS: readonly Existencia[] = ['declarado', 'loja', 'so_aprendido'];

/** `{tipo: {estado ou camada: n}}`. */
export type Contagem = Record<string, Record<string, number>>;

export interface DeclaradoDoApp {
  arquivos: { app: boolean; catalogo: boolean; telas: boolean; sessao: boolean };
  acoes: number;
  telas: number;
  login_gerenciado: boolean;
}

export interface ResumoDoApp {
  pacote: string;
  nome: string;
  /** `null` no balde `nao_resolvido`. */
  existencia: Existencia | null;
  declarado: DeclaradoDoApp | null;
  loja: { nome: string | null; nav_hints: number; known_selectors: number } | null;
  aprendido: { total: number; contagem: Contagem };
  absorvido: number;
  uso: Contagem;
}

export interface ModosDaVisao {
  receitas: string | null;
  fluxos: boolean | null;
  habilidades: boolean | null;
  licoes: string | null;
  telas: string | null;
}

export interface VisaoDeApps {
  apps: ResumoDoApp[];
  total: number;
  nao_resolvido: ResumoDoApp | null;
  fora_do_eixo: Contagem;
  modos: ModosDaVisao;
}

export interface UsoDoItem {
  camada: string;
  porque: string | null;
}

export type TipoDeArquivo = 'app' | 'catalogo' | 'telas' | 'sessao' | 'loja';

export interface ItemDeclarado {
  tipo: string;
  arquivo: string | null;
  presente: boolean;
  quantidade: number | null;
  uso: UsoDoItem | null;
}

/** Uma linha do Livro mais o que a visão por app acrescenta. */
export type LinhaDoApp = EntradaDoLivro & {
  origem_na_visao?: string | null;
  uso?: UsoDoItem | null;
  absorvida_em?: string | null;
};

export interface DetalheDoApp {
  app: ResumoDoApp;
  declarado: ItemDeclarado[];
  aprendido: LinhaDoApp[];
  absorvido: LinhaDoApp[];
  modos: ModosDaVisao;
}

// --- leitura tolerante ---------------------------------------------------------------------------------------------

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : {});
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);

function lerContagem(raw: unknown): Contagem {
  const saida: Contagem = {};
  for (const [tipo, porChave] of Object.entries(obj(raw))) {
    const linha: Record<string, number> = {};
    for (const [chave, n] of Object.entries(obj(porChave))) linha[chave] = num(n);
    saida[tipo] = linha;
  }
  return saida;
}

export function lerResumo(raw: unknown): ResumoDoApp {
  const r = obj(raw);
  const d = r.declarado == null ? null : obj(r.declarado);
  const a = obj(d?.arquivos);
  const lj = r.loja == null ? null : obj(r.loja);
  const ap = obj(r.aprendido);
  const existencia = EXISTENCIAS.find((e) => e === r.existencia) ?? null;
  const pacote = str(r.pacote) ?? '';
  return {
    pacote,
    nome: str(r.nome) || pacote,
    existencia,
    declarado: d ? {
      arquivos: { app: !!a.app, catalogo: !!a.catalogo, telas: !!a.telas, sessao: !!a.sessao },
      acoes: num(d.acoes), telas: num(d.telas), login_gerenciado: !!d.login_gerenciado,
    } : null,
    loja: lj ? { nome: str(lj.nome), nav_hints: num(lj.nav_hints), known_selectors: num(lj.known_selectors) } : null,
    aprendido: { total: num(ap.total), contagem: lerContagem(ap.contagem) },
    absorvido: num(r.absorvido),
    uso: lerContagem(r.uso),
  };
}

export function lerModos(raw: unknown): ModosDaVisao {
  const m = obj(raw);
  return { receitas: str(m.receitas), fluxos: bool(m.fluxos), habilidades: bool(m.habilidades), licoes: str(m.licoes), telas: str(m.telas) };
}

export function lerVisaoDeApps(raw: unknown): VisaoDeApps {
  const r = obj(raw);
  const apps = Array.isArray(r.apps) ? r.apps.map(lerResumo) : [];
  return {
    apps,
    total: typeof r.total === 'number' ? r.total : apps.length,
    nao_resolvido: r.nao_resolvido ? lerResumo(r.nao_resolvido) : null,
    fora_do_eixo: lerContagem(r.fora_do_eixo),
    modos: lerModos(r.modos),
  };
}

function lerUso(raw: unknown): UsoDoItem | null {
  const u = obj(raw);
  return typeof u.camada === 'string' ? { camada: u.camada, porque: str(u.porque) } : null;
}

export function lerDetalheDoApp(raw: unknown): DetalheDoApp {
  const r = obj(raw);
  const linhas = (v: unknown): LinhaDoApp[] => (Array.isArray(v) ? (v as LinhaDoApp[]) : []);
  return {
    app: lerResumo(r.app),
    declarado: (Array.isArray(r.declarado) ? r.declarado : []).map((x): ItemDeclarado => {
      const i = obj(x);
      return { tipo: str(i.tipo) ?? '', arquivo: str(i.arquivo), presente: !!i.presente,
               quantidade: typeof i.quantidade === 'number' ? i.quantidade : null, uso: lerUso(i.uso) };
    }),
    aprendido: linhas(r.aprendido).map((l) => ({ ...l, uso: lerUso(l.uso) })),
    absorvido: linhas(r.absorvido).map((l) => ({ ...l, uso: lerUso(l.uso) })),
    modos: lerModos(r.modos),
  };
}

// --- rótulos e resumos ---------------------------------------------------------------------------------------------

export const EXISTENCIA_META: Record<Existencia, { label: string; tone: Tone; dica: string }> = {
  declarado: { label: 'Declarado', tone: 'success', dica: 'Está no registro de apps do sistema (arquivos declarados).' },
  loja: { label: 'Só na loja', tone: 'neutral', dica: 'Está na tabela de apps da loja, sem declaração própria.' },
  so_aprendido: { label: 'Só aprendido', tone: 'warning', dica: 'Só existe porque algo foi aprendido; nada foi declarado nem instalado pela loja.' },
};

export const ARQUIVO_LABEL: Record<string, string> = {
  app: 'Manifesto do app', catalogo: 'Catálogo de ações', telas: 'Telas', sessao: 'Sessão', loja: 'Loja',
};

export function rotuloDoArquivo(tipo: string): string {
  return ARQUIVO_LABEL[tipo] ?? tipo;
}

/** A camada de uso, em linguagem de gente (`domain/camada.py`). */
export const CAMADA_DE_USO: Record<string, string> = {
  decide_sem_ia: 'decide sem a IA',
  vai_ao_prompt: 'vai ao prompt da IA',
  classifica_tela: 'classifica a tela',
  login_fora_da_ia: 'entra no app sem a IA',
  pre_preenche: 'pré-preenche campos',
  contexto_da_persona: 'contexto da persona',
  medido_nao_usado: 'medido, não usado',
  nao_medido: 'não medido',
  inerte: 'inerte',
  desconhecida: 'modo não lido',
};

export function rotuloDoUso(camada: string | null | undefined): string {
  return camada ? CAMADA_DE_USO[camada] ?? camada : '—';
}

const soma = (m: Record<string, number> | undefined): number => Object.values(m ?? {}).reduce((a, b) => a + b, 0);

/** Os tipos com algo dentro (a contagem de um app vazio vem sem linhas ou com zeros). */
function tiposComConteudo(c: Contagem): [string, Record<string, number>][] {
  return Object.entries(c).filter(([, m]) => soma(m) > 0);
}

/** "Receita: 2 candidatos, 1 publicado" por tipo, na ordem em que o backend mandou. */
export function resumoDoAprendido(c: Contagem): { tipo: string; texto: string }[] {
  return tiposComConteudo(c).map(([tipo, porEstado]) => ({
    tipo: rotuloDoKind(tipo),
    texto: Object.entries(porEstado).filter(([, n]) => n > 0).map(([estado, n]) => {
      if (tipo === 'memoria') return `${n} lembrança${n === 1 ? '' : 's'}`;
      if (estado === '-') return `${n} sem estado`;
      return `${n} ${rotuloDoEstado(isEstadoDoLivro(estado) ? estado : null).toLowerCase()}${n === 1 ? '' : 's'}`;
    }).join(', '),
  }));
}

/** A linha curta "como é usado": "Receita: decide sem a IA (2); Tela aprendida: medido, não usado (1)". */
export function linhaDeUso(uso: Contagem): string {
  const partes = tiposComConteudo(uso).map(([tipo, porCamada]) => {
    const camadas = Object.entries(porCamada).filter(([, n]) => n > 0).map(([c, n]) => `${rotuloDoUso(c)} (${n})`);
    return `${rotuloDoKind(tipo)}: ${camadas.join(', ')}`;
  });
  return partes.length > 0 ? partes.join('; ') : 'Nada aprendido para usar ainda.';
}

/** O mesmo que `linhaDeUso`, um item por tipo ("Receita" → "decide sem a IA 19 · inerte 4"), para chips e linhas. */
export function usoPorTipo(uso: Contagem): { tipo: string; texto: string }[] {
  return tiposComConteudo(uso).map(([tipo, porCamada]) => ({
    tipo: rotuloDoKind(tipo),
    texto: Object.entries(porCamada).filter(([, n]) => n > 0).map(([c, n]) => `${rotuloDoUso(c)} ${n}`).join(' · '),
  }));
}

/** Há algo que o sistema mede e não usa (shadow e observe)? Vira um aviso no cartão. */
export function temMedidoNaoUsado(uso: Contagem): boolean {
  return Object.values(uso).some((m) => (m.medido_nao_usado ?? 0) > 0);
}

const MODO_LABEL: Record<string, string> = { off: 'desligado', shadow: 'só mede (sombra)', on: 'ligado', observe: 'só observa', replay: 'reproduz' };

function rotuloDoModo(v: string | boolean | null): string {
  if (v === null) return 'não lido';
  if (typeof v === 'boolean') return v ? 'ligado' : 'desligado';
  return MODO_LABEL[v] ?? v;
}

/** Os modos globais, um por tipo, para a legenda do Global. */
export function modosEmTexto(m: ModosDaVisao): { tipo: string; modo: string }[] {
  return [
    { tipo: 'Receitas', modo: rotuloDoModo(m.receitas) },
    { tipo: 'Fluxos', modo: rotuloDoModo(m.fluxos) },
    { tipo: 'Habilidades', modo: rotuloDoModo(m.habilidades) },
    { tipo: 'Lições', modo: rotuloDoModo(m.licoes) },
    { tipo: 'Telas aprendidas', modo: rotuloDoModo(m.telas) },
  ];
}

/** O que há de declarado, em uma frase: "3 arquivos · 12 ações · 8 telas". `null` fora do registro de apps. */
export function resumoDoDeclarado(d: DeclaradoDoApp | null): string | null {
  if (!d) return null;
  const arquivos = Object.values(d.arquivos).filter(Boolean).length;
  return `${arquivos} arquivo${arquivos === 1 ? '' : 's'} · ${d.acoes} ${d.acoes === 1 ? 'ação' : 'ações'} · ${d.telas} tela${d.telas === 1 ? '' : 's'}`;
}

/** Abre o detalhe de um app (`#/aprendizado?aba=apps&app=<pacote>`); "voltar" do navegador retorna de onde veio. */
export function abrirApp(pacote: string): void {
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'apps', app: pacote } });
}

/** Abre o catálogo Aprendido já filtrado por um app (`#/aprendizado?aba=aprendido&app=<pacote>`). */
export function abrirAprendidoDoApp(pacote: string): void {
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido', app: pacote } });
}
