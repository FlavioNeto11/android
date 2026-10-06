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
  /** O modo EFETIVO de lições e telas neste app (30.20) e a origem; `null` no backend anterior. */
  modos_do_app: ModosDoApp | null;
}

export interface ModosDaVisao {
  receitas: string | null;
  fluxos: boolean | null;
  habilidades: boolean | null;
  licoes: string | null;
  telas: string | null;
  /** As exceções por app do config (`aprendizado.<tipo>.por_app`, §8.10): pacote → modo. Vazio = todos no global. */
  licoes_por_app: Record<string, string>;
  telas_por_app: Record<string, string>;
}

/** Um modo por app: o que vale e de onde vem (`app` = override no config; `global` = segue o modo global). */
export interface ModoDoApp {
  modo: string | null;
  origem: 'app' | 'global';
}

export interface ModosDoApp {
  licoes: ModoDoApp;
  telas: ModoDoApp;
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
    modos_do_app: lerModosDoApp(r.modos_do_app),
  };
}

function lerModoDoApp(raw: unknown): ModoDoApp {
  const m = obj(raw);
  return { modo: str(m.modo), origem: m.origem === 'app' ? 'app' : 'global' };
}

function lerModosDoApp(raw: unknown): ModosDoApp | null {
  if (raw == null || typeof raw !== 'object') return null;
  const m = obj(raw);
  return { licoes: lerModoDoApp(m.licoes), telas: lerModoDoApp(m.telas) };
}

function lerPorApp(raw: unknown): Record<string, string> {
  const saida: Record<string, string> = {};
  for (const [pacote, modo] of Object.entries(obj(raw))) if (typeof modo === 'string') saida[pacote] = modo;
  return saida;
}

export function lerModos(raw: unknown): ModosDaVisao {
  const m = obj(raw);
  return {
    receitas: str(m.receitas), fluxos: bool(m.fluxos), habilidades: bool(m.habilidades), licoes: str(m.licoes), telas: str(m.telas),
    licoes_por_app: lerPorApp(m.licoes_por_app), telas_por_app: lerPorApp(m.telas_por_app),
  };
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
  loja: { label: 'Sem declaração', tone: 'neutral', dica: 'Conhecido pela loja (ou é do sistema), mas sem arquivos declarados: o que se sabe dele foi aprendido.' },
  so_aprendido: { label: 'Só aprendido', tone: 'warning', dica: 'Só existe porque algo foi aprendido; nada foi declarado nem instalado pela loja.' },
};

export const ARQUIVO_LABEL: Record<string, string> = {
  app: 'Manifesto do app', catalogo: 'Catálogo de ações', telas: 'Telas', sessao: 'Sessão', loja: 'Loja',
};

export function rotuloDoArquivo(tipo: string): string {
  return ARQUIVO_LABEL[tipo] ?? tipo;
}

/** RA-24 (`GET /api/apps/{pacote}/conhecimento`): um arquivo de conhecimento como o processo o carregou. */
export interface ArquivoProvado {
  nome: string;
  sha256: string;
  git_blob: string;
  modificado_em: string | null;
  /** Gravado depois que o servidor subiu: o que está no ar pode ser a versão anterior (ou falso positivo do
   *  arquivo lido sob demanda; um reinício tira a dúvida). */
  mudou_depois_do_inicio: boolean;
}

export interface ProvaDoConhecimento {
  app: string;
  processo_iniciado_em: string | null;
  arquivos: ArquivoProvado[];
}

/** Leitura tolerante da prova: o arquivo sem nome ou sem sha256 não entra (não há o que mostrar dele). */
export function lerProvaDoConhecimento(raw: unknown): ProvaDoConhecimento {
  const r = obj(raw);
  const arquivos = (Array.isArray(r.arquivos) ? r.arquivos : []).flatMap((x): ArquivoProvado[] => {
    const a = obj(x);
    const nome = str(a.nome);
    const sha256 = str(a.sha256);
    if (!nome || !sha256) return [];
    return [{ nome, sha256, git_blob: str(a.git_blob) ?? '', modificado_em: str(a.modificado_em),
      mudou_depois_do_inicio: a.mudou_depois_do_inicio === true }];
  });
  return { app: str(r.app) ?? '', processo_iniciado_em: str(r.processo_iniciado_em), arquivos };
}

/** Os 7 primeiros do sha256: o bastante para comparar de relance; o inteiro vai no `title`. */
export const shaCurto = (sha: string): string => sha.slice(0, 7);

/** O arquivo provado de um item declarado: o `tipo` (app, catalogo, telas, sessao) é o nome do arquivo. */
export function provaDoArquivo(prova: ProvaDoConhecimento | null | undefined, tipo: string): ArquivoProvado | undefined {
  return prova?.arquivos.find((a) => a.nome === `${tipo}.yaml`);
}

/** A linha do resumo: quantos arquivos o processo carregou e se algum mudou depois que o servidor subiu. */
export function resumoDaProva(prova: ProvaDoConhecimento): { texto: string; mudaram: number } {
  const n = prova.arquivos.length;
  const mudaram = prova.arquivos.filter((a) => a.mudou_depois_do_inicio).length;
  const conferidos = `${n} ${n === 1 ? 'arquivo conferido' : 'arquivos conferidos'}`;
  if (mudaram === 0) return { texto: `${conferidos} · sem mudança desde que o servidor subiu`, mudaram };
  return { texto: `${conferidos} · ${mudaram} ${mudaram === 1 ? 'mudou' : 'mudaram'} depois que o servidor subiu`, mudaram };
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

/** Os dois tipos que têm modo por app (§8.10): a chave do config e o nome no painel. */
export const TIPOS_COM_MODO_POR_APP = [
  { chave: 'licoes', nome: 'Lições' },
  { chave: 'telas', nome: 'Telas aprendidas' },
] as const;
export type TipoComModoPorApp = (typeof TIPOS_COM_MODO_POR_APP)[number]['chave'];

/** O que cada modo FAZ, em uma frase, por tipo (o rótulo sozinho, "só observa", não diz o efeito). */
const EFEITO_DO_MODO: Record<TipoComModoPorApp, Record<string, string>> = {
  licoes: {
    off: 'não coleta nem usa lições neste app',
    shadow: 'coleta e mede as lições, mas nenhuma vai ao prompt',
    on: 'as lições publicadas vão ao prompt do ator e do planejador',
  },
  telas: {
    off: 'não observa as telas deste app',
    observe: 'observa e valida as telas, mas nenhuma é publicada sozinha',
    on: 'as telas publicadas são entregues à sessão do app',
  },
};

export interface ModoDoAppEmTexto {
  chave: TipoComModoPorApp;
  tipo: string;
  modo: string;
  efeito: string | null;
  doApp: boolean;
}

/** Lições e telas de um app em texto: o rótulo do modo, o efeito e se é do app ou do global. */
export function modosDoAppEmTexto(m: ModosDoApp): ModoDoAppEmTexto[] {
  return TIPOS_COM_MODO_POR_APP.map(({ chave, nome }) => {
    const x = m[chave];
    return { chave, tipo: nome, modo: rotuloDoModo(x.modo), efeito: x.modo ? EFEITO_DO_MODO[chave][x.modo] ?? null : null,
             doApp: x.origem === 'app' };
  });
}

/** O que o app tem de próprio (para o chip do cartão): só os tipos com override. */
export function modosProprios(m: ModosDoApp | null): ModoDoAppEmTexto[] {
  return m ? modosDoAppEmTexto(m).filter((x) => x.doApp) : [];
}

/** As exceções por app da visão global, com o nome do app quando se sabe; ordenadas por app e tipo. */
export function excecoesPorApp(m: ModosDaVisao, nomes?: ReadonlyMap<string, string>): { pacote: string; app: string; tipo: string; modo: string }[] {
  const saida = TIPOS_COM_MODO_POR_APP.flatMap(({ chave, nome }) => {
    const mapa = chave === 'licoes' ? m.licoes_por_app : m.telas_por_app;
    return Object.entries(mapa).map(([pacote, modo]) => ({ pacote, app: nomes?.get(pacote) ?? pacote, tipo: nome, modo: rotuloDoModo(modo) }));
  });
  return saida.sort((a, b) => a.app.localeCompare(b.app, 'pt-BR') || a.tipo.localeCompare(b.tipo, 'pt-BR'));
}

/**
 * O trecho do `config.yaml` com o modo que vale HOJE neste app, para a pessoa trocar o valor (o painel não grava o
 * config: mostra onde e como mudar). O formato é o do bloco `aprendizado:` (§8.10).
 */
export function trechoDeConfig(pacote: string, m: ModosDoApp): string {
  const linhas = ['aprendizado:'];
  for (const { chave } of TIPOS_COM_MODO_POR_APP) {
    linhas.push(`  ${chave}:`, '    por_app:', `      ${pacote}: ${m[chave].modo ?? (chave === 'licoes' ? 'shadow' : 'observe')}`);
  }
  return linhas.join('\n');
}

/** Os valores que cada tipo aceita, com o que fazem, na ordem do mais ligado ao desligado. */
export function valoresDoModo(tipo: TipoComModoPorApp): { valor: string; rotulo: string; efeito: string }[] {
  const ordem = tipo === 'licoes' ? ['on', 'shadow', 'off'] : ['on', 'observe', 'off'];
  return ordem.map((valor) => ({ valor, rotulo: rotuloDoModo(valor), efeito: EFEITO_DO_MODO[tipo][valor] ?? '' }));
}

/** O que há de declarado, em uma frase: "3 arquivos · 12 ações · 8 telas". `null` fora do registro de apps. */
export function resumoDoDeclarado(d: DeclaradoDoApp | null): string | null {
  if (!d) return null;
  const arquivos = Object.values(d.arquivos).filter(Boolean).length;
  return `${arquivos} arquivo${arquivos === 1 ? '' : 's'} · ${d.acoes} ${d.acoes === 1 ? 'ação' : 'ações'} · ${d.telas} tela${d.telas === 1 ? '' : 's'}`;
}

/** O balde dos itens que o backend não conseguiu ligar a um aplicativo (30.2): não é um pacote de verdade. */
export const PACOTE_NAO_RESOLVIDO = 'nao_resolvido';

/** 31.126: como a pessoa lê o balde. O código `nao_resolvido` nunca aparece como nome de aplicativo. */
export const NOME_DO_APP_NAO_IDENTIFICADO = 'App não identificado';
export const EXPLICACAO_DO_APP_NAO_IDENTIFICADO =
  'Ainda não foi ligado a um aplicativo: o pacote não foi identificado quando o item foi aprendido.';

/**
 * O nome de um app para a pessoa: o balde vira "App não identificado"; o resto, o nome conhecido ou, sem ele, o próprio
 * pacote (que ainda é o melhor palpite). `nome` igual ao pacote conta como "sem nome".
 */
export function nomeDoApp(pacote: string, nome?: string | null): string {
  if (pacote === PACOTE_NAO_RESOLVIDO) return NOME_DO_APP_NAO_IDENTIFICADO;
  return nome && nome !== pacote ? nome : pacote;
}

/** O texto de apoio ao passar o mouse num link de app: no balde, a explicação em vez do código. */
export function dicaDoApp(pacote: string): string {
  return pacote === PACOTE_NAO_RESOLVIDO ? EXPLICACAO_DO_APP_NAO_IDENTIFICADO : `Abrir este aplicativo (${pacote})`;
}

/** Abre o detalhe de um app (`#/aprendizado?aba=apps&app=<pacote>`); "voltar" do navegador retorna de onde veio. */
export function abrirApp(pacote: string): void {
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'apps', app: pacote } });
}

/** Abre o catálogo Aprendido já filtrado por um app (`#/aprendizado?aba=aprendido&app=<pacote>`). */
export function abrirAprendidoDoApp(pacote: string): void {
  useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido', app: pacote } });
}
