/**
 * Aprendizado contínuo no painel (ADR-054, pacote A6): tipos, rótulos e regras puras da página Aprendizado e do botão
 * "Deu certo / Deu errado" (D2).
 *
 * O painel não decide nada sobre o ciclo de vida: o domínio (`backend/app/modules/learning/domain/ciclo.py`) é quem
 * recusa. Aqui só se espelha a tabela de transições da PESSOA para não oferecer botão que o backend recusaria, e se
 * traduz o vocabulário fechado para português.
 *
 * As rotas do livro (`/api/aprendizado`, `/pendentes`, `/revisar`, `/{kind}/{ref}`, `POST …/status`) já existem (A1).
 * As do "o que mais falha" (A3), do voto e dos sinais (A4) chegam em pacotes paralelos: por isso a leitura delas é
 * TOLERANTE (`ler*`) — campo ausente vira `null`, linha que não é objeto some, e nada quebra a página.
 */
import { Archive, CircleCheck, CircleDashed, CircleOff, FilePen, ShieldCheck } from 'lucide-react';
import type { SkillState } from '../../api/types';
import { isRecord } from '../../lib/format';
import type { StatusMeta } from '../../lib/status';
import { formatQuando } from '../../lib/time';
import type { BlocoDoCurador, ModoDoCurador, ParecerDaIA, ParecerNaFila } from './parecer';

// ---------------------------------------------------------------- vocabulário do livro

export type LivroKind = 'receita' | 'fluxo' | 'habilidade' | 'memoria' | 'tela' | 'licao' | 'voz' | 'preferencia';
export const LIVRO_KINDS: readonly LivroKind[] = ['receita', 'fluxo', 'habilidade', 'memoria', 'tela', 'licao', 'voz',
                                                  'preferencia'];
/** O livro não tem rascunho: o item nasce congelado (`ciclo.py::ESTADOS`). */
export type EstadoDoLivro = Exclude<SkillState, 'draft'>;
export const ESTADOS_DO_LIVRO: readonly EstadoDoLivro[] = ['candidate', 'validated', 'published', 'deprecated', 'disabled'];
export type Origem = 'execucao' | 'treino' | 'pessoa' | 'ensino' | 'sistema';
export const ORIGENS: readonly Origem[] = ['execucao', 'treino', 'pessoa', 'ensino', 'sistema'];
/**
 * Filtro `rotulo` do livro (RA-19): de que conjunto de apps. Os de teste são os de `apps.category='qa'` (o QA
 * embutido); a lista padrão os esconde, e o acervo continua no livro (o detalhe do app e as filas leem tudo).
 */
export type Rotulo = 'produto' | 'qa' | 'todos';
export const ROTULOS: readonly Rotulo[] = ['produto', 'qa', 'todos'];
export const ROTULO_LABEL: Record<Rotulo, string> = { produto: 'Produto', qa: 'QA', todos: 'Todos' };
export const ROTULO_DICA: Record<Rotulo, string> = {
  produto: 'Os apps de verdade, sem o app de teste (QA)',
  qa: 'Só o app de teste (QA)',
  todos: 'Todos os apps, com o de teste',
};

/** O que o `rotulo` escondeu, em uma linha curta ao lado do seletor ("94 do QA ocultos"); vazio sem ocultos. */
export function textoDosOcultos(rotulo: Rotulo, ocultos: number | undefined): string {
  if (!ocultos || rotulo === 'todos') return '';
  return `${ocultos.toLocaleString('pt-BR')} ${rotulo === 'produto' ? 'do QA' : 'de produto'} ${ocultos === 1 ? 'oculto' : 'ocultos'}`;
}

export function isLivroKind(v: unknown): v is LivroKind {
  return typeof v === 'string' && (LIVRO_KINDS as readonly string[]).includes(v);
}

export function isEstadoDoLivro(v: unknown): v is EstadoDoLivro {
  return typeof v === 'string' && (ESTADOS_DO_LIVRO as readonly string[]).includes(v);
}

/** Uma linha do livro, como `presentation/livro.py::_entrada` devolve. */
/** 30.24: "Confirmar que fica" lido pelo backend (quem, quando e o motivo livre, sem o prefixo da trilha). */
export interface Confirmacao {
  por: string;
  em: string;
  motivo: string | null;
}

export interface EntradaDoLivro {
  kind: LivroKind;
  /** Id na fonte: receita = número, habilidade = `id@versão`, memória = perfil, item = `li-…`. */
  ref: string;
  state: SkillState | null;
  native_status: string | null;
  title: string;
  app: string | null;
  /** O nome do app (declarado, da loja ou o próprio pacote), para o painel não mostrar o pacote onde já sabe o nome
   *  (validação do deploy 4). Ausente no backend anterior. */
  app_nome?: string | null;
  /** 30.33-C: os pacotes do fluxo que atravessa apps, na ordem do plano (só com mais de um; vazio no resto), e os
   *  nomes deles na mesma ordem. O `app` continua o principal. Ausentes no backend anterior. */
  apps?: string[];
  apps_nomes?: string[];
  /** Receita: o título da etapa de que foi aprendida ("Digitar a mensagem"), o nome legível quando o app não tem
   *  catálogo. Ausente no backend anterior. */
  etapa?: string | null;
  /** A capability da linha (hierarquia App → Capability → Item) e o nome dela em português, do catálogo do app
   *  (`OPEN_PROFILE` → "Abrir o perfil"). Ausentes no backend anterior; `null` quando não se sabe. */
  capability?: string | null;
  capability_nome?: string | null;
  origin: Origem;
  side_effect: boolean;
  human_origin: boolean;
  /** Derivado no backend (`side_effect OR human_origin`, ou habilidade): nenhuma rota o edita. */
  requires_owner: boolean;
  created_at: string | null;
  state_at: string | null;
  last_used_at: string | null;
  uses: number | null;
  evidence: { for: number; against: number };
  /** Memória: quantas lembranças (o conteúdo nunca sai no livro). */
  count: number | null;
  /** `state_detail`: `em_prova`, `medida:ajuda`, `absorvida:<commit>`… */
  detail: string | null;
  /** O que a PESSOA pode fazer agora, calculado no backend (`ciclo.TRANSICOES` + D1): o painel não decide. */
  acoes: AcaoPermitida[];
  /** Por que o sistema não publica sozinho (D1, veto, modo); `null` quando publica. */
  por_que_nao_publica: MotivoDeNaoPublicar | null;
  /** A saúde do item (30.4), do backend: o painel só a exibe. Ausente em backend antigo; `null` na memória. */
  saude?: SaudeDoItem | null;
  /** A execução de que a receita ou o fluxo foi aprendido (30.23); `null` no treino e nos outros tipos. */
  nasceu_de?: string | null;
  /** (Re)nasceu no escopo de uma evidência inválida (30.23): espera o dono. Derivado no backend. */
  reaprendido?: Reaprendido | null;
  /** 30.24, receita e fluxo: está em "Revisar" agora (`false`: uma pessoa já decidiu o legado). */
  em_revisar?: boolean;
  /** 30.24: a confirmação que vale ("Confirmar que fica"), com o motivo livre de quem confirmou. */
  confirmado?: Confirmacao | null;
  /** 30.24: a confirmação que a evidência contrária derrubou (o item voltou para "Revisar" por isso). */
  confirmacao_contestada?: Confirmacao | null;
  /** O parecer pendente da IA (30.17): só nas listas Para aprovar e Revisar, e só com o curador em `on`. */
  parecer?: ParecerNaFila | null;
}

/** 30.23: a execução do sucesso falso e o item que ela ensinou (no fluxo, a própria linha, que renasce nela). */
export interface Reaprendido {
  run_invalidada: string;
  item: { kind: string; ref: string };
}

/** Chave estável do passo (`ItemDoLivro` mapeia para o texto em português). */
export type RotuloDaAcao = 'validar' | 'aprovar' | 'rejeitar' | 'aposentar' | 'desligar' | 'reativar' | 'devolver';

export interface AcaoPermitida {
  to: EstadoDoLivro;
  rotulo: RotuloDaAcao;
  exige_motivo: boolean;
}

export type CodigoDeNaoPublicar =
  'habilidade' | 'efeito_externo' | 'texto_de_pessoa' | 'reaprendido' | 'vetado' | 'modo_desligado';

export interface MotivoDeNaoPublicar {
  codigo: CodigoDeNaoPublicar;
  /** Só o dono decide (D1); falso para veto e modo desligado. */
  espera_o_dono: boolean;
  detalhe: string | null;
}

export interface ListaDoLivro {
  itens: EntradaDoLivro[];
  total: number;
  /** Só em `GET /api/aprendizado`: {tipo: {estado: n}}. */
  contagem?: Record<string, Record<string, number>>;
  /** Só em `GET /api/aprendizado` (RA-19): o conjunto que valeu (sem pedir: `produto`, ou `todos` com um app escolhido). */
  rotulo?: Rotulo;
  /** Quantos itens os outros filtros deixavam e o `rotulo` escondeu. */
  ocultos?: number;
  /** Nas listas Para aprovar e Revisar (30.17): o modo do curador; `null` sem curador composto. */
  curador?: { modo: ModoDoCurador } | null;
}

export interface EvidenciaDoLivro {
  stance: 'for' | 'against' | 'conflict';
  origin_ref: string;
  run_id: string | null;
  instance_id: string | null;
  app_version: string | null;
  simulated: boolean;
  detail: string | null;
  observed_at: string;
  /** 30.23: a execução desta evidência foi marcada como evidência inválida no item; não prova nada. */
  invalidada?: boolean;
}

export interface TransicaoDoLivro {
  id: number;
  from: SkillState | null;
  to: SkillState;
  reason: string;
  decided_by: string;
  decided_at: string;
  run_id: string | null;
  /** 30.23: o desligamento por evidência inválida já vem lido do motivo (o painel nunca interpreta o formato).
   *  30.24: `confirmacao` é a linha published → published de "Confirmar que fica". */
  tipo?: 'evidencia_invalida' | 'confirmacao' | null;
  run_invalidada?: string | null;
  /** 30.24: o motivo livre de quem confirmou, sem o prefixo (nulo: confirmou sem motivo). */
  motivo_da_pessoa?: string | null;
}

export interface DetalheDoLivro {
  item: EntradaDoLivro;
  evidencias: EvidenciaDoLivro[];
  trilha: TransicaoDoLivro[];
  exposicoes: unknown[];
  /** O conteúdo legível (v0.50); `null` onde não sai. Os três campos abaixo são ausentes em backend antigo. */
  conteudo?: ConteudoDoItem | null;
  versao?: VersaoDoItem;
  relacoes?: RelacaoDoItem[];
  /** 30.23: a execução de origem que a pessoa pode marcar como evidência inválida agora; `null` quando não cabe. */
  invalidar_evidencia?: { run_id: string } | null;
  /** 30.17: as revisões do curador que o modo deixa aparecer (a mais recente primeiro) e o bloco do curador.
   *  Ausentes em backend antigo; `curador: null` sem curador composto. */
  pareceres?: ParecerDaIA[];
  curador?: BlocoDoCurador | null;
}

// ---------------------------------------------------------------- o detalhe rico (30.16): o que o backend manda

/** `domain/saude.py::Rotulo` (vocabulário fechado; o painel só traduz). */
export type RotuloDeSaude = 'inativo' | 'em_prova' | 'degradando' | 'obsoleto_provavel' | 'sem_evidencia' | 'parado' | 'pouca_amostra'
  | 'saudavel' | 'indeterminado';

/** Um fato que produziu o rótulo: `valor` é o medido, `limite` o cruzado, `detalhe` a janela, a amostra ou o motivo da trilha. */
export interface MotivoDeSaude {
  codigo: string;
  dimensao: string | null;
  valor: number | string | null;
  limite: number | null;
  detalhe: string | null;
}

/** `desconhecida` com `valor: null` é "sem dado": nunca 0. */
export interface DimensaoDeSaude {
  nome: string;
  estado: 'medida' | 'desconhecida';
  valor: number | string | null;
  amostra: number | null;
  fonte: string;
}

export interface SaudeDoItem {
  rotulo: RotuloDeSaude;
  motivos: MotivoDeSaude[];
  dimensoes: DimensaoDeSaude[];
}

/** O alvo de uma ação da receita: um seletor; só o que ele tem de `rid`, `texto` e `desc` (nunca valor de parâmetro). */
export interface AlvoDaAcao {
  tipo: string;
  rid?: string;
  texto?: string;
  desc?: string;
}

export interface AcaoDaReceita {
  indice: number;
  ferramenta: string | null;
  commit: boolean;
  alvo: AlvoDaAcao[];
  /** Só os NOMES dos parâmetros não sigilosos. */
  parametros: string[];
  /** A ação digita um segredo (`type_secret` ou parâmetro sigiloso): não sai nome nem valor. */
  segredo: boolean;
  digita?: { limpa_antes: boolean; enter: boolean; so_parametro: boolean };
  pacote?: string;
  duracao_ms?: number;
  coleta?: { seletor_do_item: string | null; exclusoes: number };
  rolagem?: { direcao: string | null; max: number | null };
}

export interface VizinhaDaReceita {
  id: number | string;
  versao: number;
  estado: string;
}

export type OrigemDaReceita =
  | { tipo: 'execucao'; step_id: string; run_id: string | null }
  | { tipo: 'treino'; ref: string }
  | { tipo: 'desconhecida' };

export interface ConteudoDaReceita {
  tipo: 'receita';
  identidade: {
    app: string | null; app_version: string | null; assinatura: string | null; variante: string | null;
    step_key: string | null; step_hash: string | null; versao: number; estado: string;
  };
  acoes: AcaoDaReceita[];
  efeito: { externo: boolean; acoes_commit: number[] };
  capability: { nomes: string[]; ambigua: boolean; fonte: 'origem' | 'mesmo_step_hash' } | null;
  origem: OrigemDaReceita;
  uso: { replay_ok: number; replay_fail: number; consecutive_fail: number; last_used_at: string | null };
  sombra: { shadow_agree: number; shadow_total: number };
  substitui: VizinhaDaReceita | null;
  substituida_por: VizinhaDaReceita | null;
}

export interface EtapaDoFluxo {
  indice: number;
  chave: string | null;
  capability: string | null;
  alvo: string | null;
  efeito: boolean;
  pos_condicao: { tipo: string | null; descricao: string | null } | null;
  parametros: string[];
  segredo: boolean;
}

export interface ConteudoDoFluxo {
  tipo: 'fluxo';
  nome: string | null;
  /** Os apps exigidos na ordem do plano (29.42); o principal do plano vem em `app`. Ausente em backend antigo. */
  apps?: string[];
  comando_modelo: string | null;
  origem: { tipo: 'execucao' | 'treino'; fonte: string | null; source_run_id: string | null };
  etapas: EtapaDoFluxo[];
  efeito: { externo: boolean; etapas_com_efeito: number[] };
}

export interface ConteudoDaHabilidade {
  tipo: 'habilidade';
  skill_id: string;
  versao: number;
  schema_version: number | string | null;
  estado: string | null;
  source_kind: string | null;
  source_ref: string | null;
  parent_version: number | null;
  command_template: string | null;
  content_hash: string | null;
  parametros: string[];
  nos: { id: string; tipo: string | null }[];
  total_de_nos: number;
  rota: string;
}

export interface ConteudoDaLicao {
  tipo: 'licao';
  texto: string | null;
  modelo: string | null;
  acao: string | null;
  /** `valor` de `parametro` é o NOME do parâmetro. */
  alvo: { tipo: string | null; valor: string | null } | null;
  escopo: { app: string | null; capability: string | null; step_hash: string | null; role: string | null };
  tokens: number | null;
}

export interface ConteudoDaTela {
  tipo: 'tela';
  tela: string | null;
  casa: boolean;
  autenticada: boolean;
  ids_todos: string[];
  razao: string | null;
}

/** `null` na memória, na voz e na preferência (texto de pessoa não sai). */
export type ConteudoDoItem = ConteudoDaReceita | ConteudoDoFluxo | ConteudoDaHabilidade | ConteudoDaLicao | ConteudoDaTela;

/** `domain/versao.py::EstadoDeVersao`, mais o "não sei" explícito. */
export type EstadoDeVersao = 'independente' | 'comprovado' | 'nao_testado' | 'em_prova' | 'falhando' | 'incompativel'
  | 'superseded' | 'versao_aposentada' | 'desconhecido';

export interface VersaoDoItem {
  estado: EstadoDeVersao;
  app: string | null;
  app_version: string | null;
  vivas: { versao: string; aparelhos: number }[];
  nao_testada_em: string[];
  por_versao: { versao: string; viva: boolean; aparelhos: number; estado: EstadoDeVersao; receita_ref: string | null }[];
}

export type TipoDeRelacao =
  'substitui' | 'substituida_por' | 'derivado_de' | 'reaprende' | 'reaprendida_por' | 'absorvida' | 'contradiz';

/** `kind`/`ref` apontam para o detalhe do alvo; `regra_declarada` e `commit` (absorvida) não são itens do Livro. */
export interface RelacaoDoItem {
  tipo: TipoDeRelacao;
  kind: string;
  ref: string;
  rotulo: string | null;
  fonte: string;
}

// ---------------------------------------------------------------- rótulos

export const ESTADO_META: Record<SkillState, StatusMeta> = {
  draft: { label: 'Rascunho', tone: 'muted', icon: FilePen, description: 'Ainda em edição.' },
  candidate: { label: 'Candidato', tone: 'info', icon: CircleDashed,
               description: 'Nasceu de uma observação e ainda não se repetiu o bastante: não é usado.' },
  validated: { label: 'Validado', tone: 'warning', icon: ShieldCheck,
               description: 'Repetiu (ou uma pessoa validou). Sem efeito externo o sistema publica sozinho; com efeito, espera o dono.' },
  published: { label: 'Publicado', tone: 'success', icon: CircleCheck, description: 'Em uso nas execuções.' },
  deprecated: { label: 'Aposentado', tone: 'muted', icon: Archive,
                description: 'Saiu de circulação (sem uso, efeito neutro, versão nova do app ou absorvido). Uma pessoa pode reativar.' },
  disabled: { label: 'Desligado', tone: 'danger', icon: CircleOff,
              description: 'Refutado ou rejeitado. O sistema não o traz de volta; uma pessoa pode reativar ou, no fluxo, devolver à prova.' },
};

export function rotuloDoEstado(s: SkillState | null | undefined): string {
  return s ? ESTADO_META[s]?.label ?? s : 'Sem estado';
}

const KIND_LABEL: Record<LivroKind, string> = {
  receita: 'Receita', fluxo: 'Fluxo', habilidade: 'Habilidade', memoria: 'Memória da persona', tela: 'Tela aprendida',
  licao: 'Lição', voz: 'Voz da persona', preferencia: 'Preferência',
};

export function rotuloDoKind(k: string | null | undefined): string {
  return k && isLivroKind(k) ? KIND_LABEL[k] : k ?? '—';
}

// ---------------------------------------------------------------- o título que a pessoa lê (deploy 3, P3 e P4)

/** A capability com o nome do catálogo na frente, "Abrir o perfil (OPEN_PROFILE)"; sem nome, só o código. */
export function capabilityComNome(codigo: string, nome?: string | null): string {
  return nome ? `${nome} (${codigo})` : codigo;
}

/**
 * O texto com a capability nomeada na primeira vez que aparece: "Em OPEN_PROFILE: …" → "Em Abrir o perfil
 * (OPEN_PROFILE): …". Só na tela: o texto gravado da lição é o que vai ao prompt, e lá o código é o que serve.
 */
export function nomearCapabilityNoTexto(texto: string, codigo?: string | null, nome?: string | null): string {
  if (!codigo || !nome || codigo === '*') return texto;
  const nomeada = capabilityComNome(codigo, nome);
  if (texto.includes(nomeada)) return texto;
  const re = new RegExp(`\\b${codigo.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`);
  return texto.replace(re, nomeada);
}

const RE_VERSAO = /^(.*) \(v(\d+)\)$/;
/** A lacuna de parâmetro do modelo ("{subject_prefix}", "\"{query}\""), como o `_LACUNA` de `domain/conteudo.py`. */
const RE_LACUNA = /"?\{[^{}]*\}"?/g;

/**
 * O título sem as lacunas de parâmetro em claro (validação dos deploys 9–12: "{contact_position}", "{instance_id}
 * {run_id}" nos títulos): cada lacuna vira "…", e lacunas seguidas viram uma só. O título cru continua no `title` da
 * linha, para quem desenvolve.
 */
export function semLacunas(texto: string): string {
  if (!texto.includes('{')) return texto;
  return texto.replace(RE_LACUNA, '…').replace(/…(\s*…)+/g, '…').replace(/\s+/g, ' ').trim();
}

/**
 * O título de uma linha do livro para a pessoa. A receita troca a chave da etapa pelo nome da capability
 * ("send_message_i1 (v1)" → "Enviar a mensagem (v1)"); a lição nomeia a capability no texto; o resto vem como o backend
 * manda. O título cru continua no `title` da linha, para quem desenvolve.
 */
export function tituloDoItem(
  e: Pick<EntradaDoLivro, 'kind' | 'ref' | 'title' | 'capability' | 'capability_nome' | 'etapa'>,
): string {
  return semLacunas(tituloComNomes(e));
}

function tituloComNomes(
  e: Pick<EntradaDoLivro, 'kind' | 'ref' | 'title' | 'capability' | 'capability_nome' | 'etapa'>,
): string {
  const t = e.title || e.ref;
  if (e.kind === 'receita' && e.capability && e.capability_nome) {
    const m = RE_VERSAO.exec(t);
    return m ? `${e.capability_nome} (v${m[2]})` : t;
  }
  // App sem catálogo (validação do deploy 4): o título da etapa de origem. A chave crua ("fill_message") saiu do texto
  // (UX do deploy 8) e fica no `title` da linha; duas do mesmo nome se distinguem em `titulosDaLista`.
  if (e.kind === 'receita' && e.etapa) {
    const m = RE_VERSAO.exec(t);
    return m ? `${e.etapa} (v${m[2]})` : e.etapa;
  }
  return e.kind === 'licao' ? nomearCapabilityNoTexto(t, e.capability, e.capability_nome) : t;
}

function repetidos(titulos: Iterable<string>): Set<string> {
  const vistos = new Set<string>();
  const dobrados = new Set<string>();
  for (const t of titulos) (vistos.has(t) ? dobrados : vistos).add(t);
  return dobrados;
}

/**
 * Os títulos de uma lista sem repetição (P4: duas receitas da mesma etapa saíam iguais). Quem empata ganha quando foi
 * aprendido ("· de hoje, 17:22"); se ainda empata, a referência ("· nº 40"). A chave é a própria entrada da lista.
 */
export function titulosDaLista(itens: readonly EntradaDoLivro[]): Map<EntradaDoLivro, string> {
  const base = new Map(itens.map((e) => [e, tituloDoItem(e)] as const));
  const dobrados = repetidos(base.values());
  const comData = new Map([...base].map(([e, t]) => [e, dobrados.has(t) && e.created_at ? `${t} · de ${formatQuando(e.created_at)}` : t] as const));
  const ainda = repetidos(comData.values());
  return new Map([...comData].map(([e, t]) => [e, ainda.has(t) ? `${t} · ${e.kind === 'receita' ? `nº ${e.ref}` : e.ref}` : t] as const));
}

export const ORIGEM_LABEL: Record<Origem, string> = {
  execucao: 'Aprendido de execução', treino: 'Demonstrado no treino', pessoa: 'Texto ou decisão de pessoa',
  ensino: 'Ensino de habilidade', sistema: 'Observação automática',
};

/** `domain/falhas.py::Camada`. */
export const CAMADAS = ['ia_ator', 'plano', 'verificacao', 'conhecimento_do_app', 'aparelho', 'automacao',
                        'conta_sessao', 'provedor_ia', 'orcamento', 'execucao', 'pessoa', 'indefinida'] as const;
export type Camada = (typeof CAMADAS)[number];

const CAMADA_LABEL: Record<Camada, string> = {
  ia_ator: 'IA (ator)', plano: 'Plano', verificacao: 'Verificação', conhecimento_do_app: 'Conhecimento do app',
  aparelho: 'Aparelho', automacao: 'Automação', conta_sessao: 'Conta e sessão', provedor_ia: 'Provedor de IA',
  orcamento: 'Orçamento', execucao: 'Fila de execução', pessoa: 'Pessoa (falta informação)',
  indefinida: 'Indefinida (classificador)',
};

function isCamada(v: string): v is Camada {
  return (CAMADAS as readonly string[]).includes(v);
}

export function rotuloDaCamada(c: string | null | undefined): string {
  if (!c) return '—';
  return isCamada(c) ? CAMADA_LABEL[c] : c;
}

/** `domain/falhas.py::FailureKind` → frase curta e a camada (o mesmo mapa `CAMADA` do domínio). */
const FALHA: Record<string, { label: string; camada: Camada }> = {
  prazo_da_etapa: { label: 'Prazo da etapa esgotado', camada: 'aparelho' },
  ui_ocupada: { label: 'Interface ocupada', camada: 'aparelho' },
  app_anr: { label: 'App sem resposta (ANR)', camada: 'aparelho' },
  aparelho_travado: { label: 'Aparelho travado', camada: 'aparelho' },
  sessao_de_automacao: { label: 'Sessão de automação caiu', camada: 'automacao' },
  interrompida: { label: 'Tentativa interrompida', camada: 'execucao' },
  autenticacao: { label: 'Autenticação', camada: 'conta_sessao' },
  conta_errada: { label: 'Conta errada', camada: 'conta_sessao' },
  ia_indisponivel: { label: 'IA indisponível', camada: 'provedor_ia' },
  ia_recusa: { label: 'IA recusou', camada: 'provedor_ia' },
  ia_orcamento: { label: 'Orçamento de IA', camada: 'orcamento' },
  ia_saldo: { label: 'Saldo da conta de IA', camada: 'provedor_ia' },
  ia_chamada_invalida: { label: 'Chamada de IA inválida', camada: 'ia_ator' },
  ia_declarou_bloqueio: { label: 'IA declarou bloqueio', camada: 'ia_ator' },
  ciclo_sem_progresso: { label: 'Ciclo sem progresso', camada: 'ia_ator' },
  alvo_ausente: { label: 'Alvo ausente na tela', camada: 'ia_ator' },
  efeito_alvo_errado: { label: 'Efeito no alvo errado', camada: 'ia_ator' },
  efeito_guarda_nao_atendida: { label: 'Guarda do efeito não atendida', camada: 'ia_ator' },
  efeito_nao_comprovado: { label: 'Efeito não comprovado', camada: 'verificacao' },
  pos_condicao_nao_comprovada: { label: 'Pós-condição não comprovada', camada: 'verificacao' },
  coleta_vazia: { label: 'Coleta vazia', camada: 'conhecimento_do_app' },
  coleta_incompleta: { label: 'Coleta incompleta', camada: 'conhecimento_do_app' },
  digitacao_incompleta: { label: 'Digitação incompleta', camada: 'automacao' },
  defeito_do_plano: { label: 'Defeito do plano', camada: 'plano' },
  falta_informacao: { label: 'Falta informação de quem pediu', camada: 'pessoa' },
  outro: { label: 'Outro (sem regra)', camada: 'indefinida' },
  // Categorias do backlog (A3), não tipos de tentativa: o sucesso mascarado e o fracasso que era sucesso.
  verificacao_falso_positivo: { label: 'Verificador aceitou o que deu errado', camada: 'verificacao' },
  verificacao_falso_negativo: { label: 'Verificador recusou o que deu certo', camada: 'verificacao' },
  verificacao_lacuna: { label: 'Confirmado à mão (verificação não comprovou)', camada: 'verificacao' },
};

export function rotuloDaFalha(k: string | null | undefined): string {
  if (!k) return '—';
  return FALHA[k]?.label ?? k;
}

export function camadaDaFalha(k: string | null | undefined): Camada | null {
  return k ? FALHA[k]?.camada ?? null : null;
}

/** 30.23: por que o reaprendido espera o dono, com a execução do sucesso falso quando o backend a manda. */
export function textoDoReaprendido(run: string | null | undefined): string {
  return run
    ? `foi reaprendido depois de uma evidência inválida (a execução ${run} terminou como sucesso sem comprovar o que fez)`
    : 'foi reaprendido depois de uma evidência inválida';
}

/** O texto do motivo que o backend mandou (apresentação: a regra é do domínio). */
export function porQueOSistemaNaoPublica(e: Pick<EntradaDoLivro, 'por_que_nao_publica'>): string | null {
  const m = e.por_que_nao_publica;
  if (!m) return null;
  switch (m.codigo) {
    case 'habilidade': return 'habilidade: publicar é sempre de uma pessoa';
    case 'efeito_externo': return 'tem efeito externo';
    case 'texto_de_pessoa': return 'tem texto de pessoa';
    case 'reaprendido': return textoDoReaprendido(m.detalhe);
    case 'vetado': return m.detalhe ?? 'vetado pelo sistema';
    case 'modo_desligado': return 'o modo deste tipo não está ligado';
    default: return m.codigo;
  }
}

/** `state_detail` em português (o que a medida disse, a prova em andamento). */
export function rotuloDoDetalhe(d: string | null | undefined): string | null {
  if (!d) return null;
  const fixos: Record<string, string> = {
    em_prova: 'em prova (braço de controle)', fila_de_prova: 'na fila da prova', 'medida:ajuda': 'efeito medido: ajuda',
    'medida:neutra': 'efeito medido: neutro', 'medida:atrapalha': 'efeito medido: atrapalha', contradita: 'contradita',
  };
  if (fixos[d]) return fixos[d] ?? d;
  if (d.startsWith('absorvida:')) return `absorvida pelo YAML (${d.slice('absorvida:'.length)})`;
  // A receita manda "sombra <iguais>/<total>": a comparação com a IA no modo sombra. Sem comparação, não há o que dizer.
  const sombra = /^sombra (\d+)\/(\d+)$/.exec(d);
  if (sombra) return Number(sombra[2]) === 0 ? null : `na sombra, concordou com a IA em ${sombra[1]} de ${sombra[2]}`;
  return d;
}

// ---------------------------------------------------------------- o que a PESSOA pode fazer

export interface AcaoDoItem {
  to: EstadoDoLivro;
  label: string;
  /** Rótulo do botão que confirma, depois do motivo. */
  confirmar: string;
  perigo: boolean;
  /** 30.24: "Confirmar que fica" não muda o estado (rota própria) e o motivo é opcional. */
  confirmaQueFica?: boolean;
}

const A = (to: EstadoDoLivro, label: string, confirmar: string, perigo = false): AcaoDoItem => ({ to, label, confirmar, perigo });

/** Texto dos botões por chave do backend (apresentação). `perigo` pinta o que tira o item de circulação. */
const TEXTO_DA_ACAO: Record<RotuloDaAcao, { label: string; confirmar: string; perigo: boolean }> = {
  validar: { label: 'Validar', confirmar: 'Confirmar validação', perigo: false },
  aprovar: { label: 'Aprovar', confirmar: 'Confirmar aprovação', perigo: false },
  rejeitar: { label: 'Rejeitar', confirmar: 'Confirmar rejeição', perigo: true },
  aposentar: { label: 'Aposentar', confirmar: 'Confirmar aposentadoria', perigo: false },
  desligar: { label: 'Desligar', confirmar: 'Confirmar desligamento', perigo: true },
  reativar: { label: 'Reativar', confirmar: 'Confirmar reativação', perigo: false },
  // 30.31: o fluxo desligado volta a provar-se (inerte, sem publicar); a evidência conta de novo a partir daqui.
  devolver: { label: 'Devolver à prova', confirmar: 'Confirmar volta à prova', perigo: false },
};

/**
 * As ações que a PESSOA pode fazer, como o backend as calculou (`acoes`: tabela do ciclo, D1 e regras de cada fonte).
 * Aqui só se põe o texto em português; nenhuma regra de transição mora no painel. Chave desconhecida (backend mais
 * novo) aparece como veio, sem botão de perigo.
 */
export function acoesDoItem(e: Pick<EntradaDoLivro, 'acoes'>): AcaoDoItem[] {
  return (e.acoes ?? []).map((a) => {
    const t = TEXTO_DA_ACAO[a.rotulo];
    return t ? { to: a.to, ...t } : A(a.to, a.rotulo, `Confirmar ${a.rotulo}`);
  });
}

/** O passo "para cima" que a fila Para aprovar oferece (e a aprovação em lote aplica): validar ou aprovar. */
export function acaoDeAprovar(e: Pick<EntradaDoLivro, 'acoes'>): AcaoDoItem | null {
  const a = e.acoes?.find((x) => x.rotulo === 'validar' || x.rotulo === 'aprovar');
  return a ? acoesDoItem({ acoes: [a] })[0] ?? null : null;
}

export function acaoDeRejeitar(e: Pick<EntradaDoLivro, 'acoes'>): AcaoDoItem | null {
  const a = e.acoes?.find((x) => x.rotulo === 'rejeitar');
  return a ? acoesDoItem({ acoes: [a] })[0] ?? null : null;
}

// ---------------------------------------------------------------- habilidade na fila (a rota das habilidades)

/** Onde fica o ciclo completo da habilidade no painel (`settings/FlowsRecipesSection.tsx`, seção Habilidades). */
export const ONDE_FICAM_AS_HABILIDADES = 'Configuração → Fluxos e receitas → Habilidades';

/**
 * `skill_versions.id` = `<skill_id>@<versão>`, lido como `skills/domain/refs.py::SkillRef.parse`: parte no ÚLTIMO `@`
 * e a versão é inteira, a partir de 1. Fora disso, `null` (e a fila não oferece botão).
 */
export function refDaHabilidade(ref: string): { skillId: string; version: number } | null {
  const i = ref.lastIndexOf('@');
  const numero = ref.slice(i + 1);
  if (i <= 0 || !/^\d+$/.test(numero)) return null;
  const version = Number(numero);
  return Number.isSafeInteger(version) && version >= 1 ? { skillId: ref.slice(0, i), version } : null;
}

/**
 * As ações da fila Para aprovar. O livro põe TODA versão de habilidade validada nesta fila (publicar é sempre de uma
 * pessoa), mas não a move: a rota do livro devolve 409 `use_skills_route`. Então, para a habilidade, a fila oferece o
 * passo da PESSOA no ciclo dela (`skills/domain/lifecycle.py::TRANSITIONS`: validated → published | disabled) e
 * `aplicarTransicao` o manda pela rota das habilidades. Os outros tipos seguem as `acoes` do backend (`acoesDoItem`).
 */
export function acoesNaFila(e: Pick<EntradaDoLivro, 'kind' | 'state' | 'ref' | 'acoes'>): AcaoDoItem[] {
  if (e.kind !== 'habilidade') return [acaoDeAprovar(e), acaoDeRejeitar(e)].filter((a): a is AcaoDoItem => a !== null);
  if (e.state !== 'validated' || !refDaHabilidade(e.ref)) return [];
  return [A('published', 'Publicar', 'Confirmar publicação'), A('disabled', 'Rejeitar', 'Confirmar rejeição', true)];
}

/** O passo "para cima" da fila, que a aprovação em lote aplica: para a habilidade validada, publicar. */
export function acaoDeAprovarNaFila(e: Pick<EntradaDoLivro, 'kind' | 'state' | 'ref' | 'acoes'>): AcaoDoItem | null {
  return e.kind === 'habilidade' ? acoesNaFila(e).find((a) => a.to === 'published') ?? null : acaoDeAprovar(e);
}

/** Limite do motivo no backend (`CorpoDeStatus.reason`, 1 a 500). */
export const MOTIVO_MAX = 500;

export function erroDoMotivo(motivo: string): string | null {
  const m = motivo.trim();
  if (!m) return 'Diga o motivo: fica na trilha do item.';
  if (m.length > MOTIVO_MAX) return `No máximo ${MOTIVO_MAX} caracteres.`;
  return null;
}

// ---------------------------------------------------------------- ordenação

function tempo(ts: string | null | undefined): number {
  const n = ts ? Date.parse(ts) : Number.NaN;
  return Number.isFinite(n) ? n : 0;
}

/** Fila do D1: o validado (que espera SÓ o dono) primeiro; dentro de cada grupo, o mais recente primeiro. */
export function ordenarPendentes(itens: readonly EntradaDoLivro[]): EntradaDoLivro[] {
  const peso = (e: EntradaDoLivro) => (e.state === 'validated' ? 0 : 1);
  return itens.slice().sort((a, b) => peso(a) - peso(b) || tempo(b.state_at ?? b.created_at) - tempo(a.state_at ?? a.created_at)
    || a.ref.localeCompare(b.ref));
}

// ---------------------------------------------------------------- o que mais falha (A3)

export interface ExemploDeFalha {
  run_id: string;
  attempt_id: string | null;
  erro: string | null;
}

export interface GrupoDeFalha {
  /** Chave estável `fk-…` (a mesma do md e do backlog). */
  id: string;
  app: string;
  capability: string;
  /** O nome em português, do catálogo do app; `null` sem catálogo (o painel mostra o código). */
  capability_nome: string | null;
  /** O nome do app como o agrupamento do Aprendido o mostra; ausente em backend antigo, `null` no app `*`. */
  app_nome?: string | null;
  failure_kind: string;
  failure_screen: string | null;
  /** O título de quem desenvolve (`pacote · CÓDIGO: motivo`): vai na cópia para a sessão, não na linha do painel. */
  titulo: string | null;
  camada: string | null;
  onde_alterar: string[];
  doc: string | null;
  prova: string | null;
  ocorrencias: number;
  taxa: number | null;
  execucoes: number | null;
  aparelhos: number | null;
  usd_perdido: number | null;
  min_perdidos: number | null;
  intervencoes: number | null;
  /** O que ORDENA (US$ + minutos + intervenções, pesos do config): vem pronto do backend, nunca recalculado aqui. */
  custo_total: number | null;
  tendencia: { atual: number; anterior: number } | null;
  exemplos: ExemploDeFalha[];
  /** Legado classificado na leitura (nunca gravado). */
  retroativo: boolean;
  estado_backlog: string | null;
  /** O sucesso mascarado: sempre no topo. */
  falso_positivo: boolean;
  /** O md do item, quando o backend o manda pronto. */
  md: string | null;
}

export interface RelatorioDeFalhas {
  dias: number | null;
  grupos: GrupoDeFalha[];
  /** Porcentagem classificada como `outro` (acima de 15% o classificador precisa de regra nova). */
  outro_pct: number | null;
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function bool(v: unknown): boolean {
  return v === true || v === 1;
}

/** O primeiro campo presente entre os nomes dados (o contrato de A3/A4 ainda pode escolher um ou outro). */
function campo(o: Record<string, unknown>, ...nomes: string[]): unknown {
  for (const n of nomes) if (o[n] !== undefined && o[n] !== null) return o[n];
  return undefined;
}

function lista(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function listaDeTextos(v: unknown): string[] {
  return lista(v).filter((x): x is string => typeof x === 'string' && x !== '');
}

function lerExemplo(v: unknown): ExemploDeFalha | null {
  if (!isRecord(v)) return null;
  const run = str(v.run_id);
  if (!run) return null;
  return { run_id: run, attempt_id: str(v.attempt_id), erro: str(campo(v, 'erro', 'error')) };
}

function lerGrupo(linha: unknown): GrupoDeFalha | null {
  if (!isRecord(linha)) return null;
  // A linha do relatório pode embrulhar o grupo (`{grupo: {...}, estado, licoes_ativas}`) e o grupo pode trazer a
  // chave aninhada (`chave: {app, capability, tipo, tela}`, a `ChaveDoGrupo` do domínio): tudo vira um nível só.
  const v: Record<string, unknown> = isRecord(linha.grupo) ? { ...linha.grupo, ...linha } : linha;
  const chave = isRecord(v.chave) ? v.chave : null;
  const tipo = str(campo(v, 'failure_kind', 'tipo')) ?? str(chave?.tipo) ?? 'outro';
  const app = str(campo(v, 'app_package', 'app')) ?? str(chave?.app) ?? '*';
  const capability = str(campo(v, 'capability', 'acao')) ?? str(chave?.capability) ?? '*';
  const tela = str(campo(v, 'failure_screen', 'tela')) ?? str(chave?.tela);
  // Sem a chave estável `fk-*` (que o backend deriva por sha1), a `cluster_key` identifica o grupo do mesmo jeito.
  const id = str(campo(v, 'id', 'key')) ?? (typeof v.chave === 'string' ? v.chave : null)
    ?? str(v.cluster_key) ?? (chave ? [app, capability, tipo, tela ?? ''].join('|') : null);
  if (!id) return null;
  // `onde_alterar` pode vir como o `OndeAlterar` do domínio ({arquivos, doc, prova}), lista ou texto.
  const onde = campo(v, 'onde_alterar', 'where');
  const ondeObj = isRecord(onde) ? onde : null;
  const arquivos = ondeObj ? listaDeTextos(ondeObj.arquivos) : typeof onde === 'string' ? [onde] : listaDeTextos(onde);
  const tend = campo(v, 'tendencia', 'trend');
  const tAtual = isRecord(tend) ? num(campo(tend, 'atual', 'ultimos_7d')) : null;
  const tAnterior = isRecord(tend) ? num(campo(tend, 'anterior', 'anteriores_7d')) : null;
  const ocorrencias = num(campo(v, 'ocorrencias', 'occurrences', 'n')) ?? 0;
  const elegiveis = num(v.elegiveis);
  return {
    id,
    app,
    capability,
    capability_nome: str(v.capability_nome),
    app_nome: str(v.app_nome),
    failure_kind: tipo,
    failure_screen: tela,
    titulo: str(campo(v, 'title', 'titulo')),
    camada: str(v.camada) ?? camadaDaFalha(tipo),
    onde_alterar: arquivos,
    doc: str(ondeObj?.doc) ?? str(v.doc),
    prova: str(ondeObj?.prova) ?? str(campo(v, 'prova', 'prova_sugerida')),
    ocorrencias,
    // A taxa é sobre as tentativas elegíveis; sem elas (ausente não é zero), não há taxa.
    taxa: num(campo(v, 'taxa', 'rate')) ?? (elegiveis ? ocorrencias / elegiveis : null),
    execucoes: num(campo(v, 'execucoes', 'runs')),
    aparelhos: num(campo(v, 'aparelhos', 'devices')),
    usd_perdido: num(v.usd_perdido),
    min_perdidos: num(v.min_perdidos),
    intervencoes: num(campo(v, 'intervencoes', 'interventions')),
    custo_total: num(v.custo_total),
    tendencia: tAtual !== null && tAnterior !== null ? { atual: tAtual, anterior: tAnterior } : null,
    exemplos: lista(v.exemplos).map(lerExemplo).filter((x): x is ExemploDeFalha => x !== null),
    retroativo: bool(v.retroativo) || (num(v.retroativas) ?? 0) > 0,
    estado_backlog: str(campo(v, 'estado_backlog', 'backlog_state', 'estado', 'state')),
    falso_positivo: bool(v.falso_positivo) || tipo === 'verificacao_falso_positivo',
    md: str(v.md),
  };
}

function lerOutroPct(raw: Record<string, unknown>): number | null {
  const direto = num(campo(raw, 'outro_pct', 'pct_outro'));
  if (direto !== null) return direto;
  const outro = isRecord(raw.outro) ? raw.outro : null;
  const n = num(outro?.ocorrencias);
  const total = num(outro?.total);
  return n !== null && total ? (100 * n) / total : null;
}

export function lerRelatorioDeFalhas(raw: unknown): RelatorioDeFalhas {
  if (!isRecord(raw)) return { dias: null, grupos: [], outro_pct: null };
  // Os grupos do topo e, à parte, os da verificação (falso positivo e negativo): entram todos, sem repetir.
  const linhas = [...lista(campo(raw, 'grupos', 'itens', 'top')), ...lista(raw.verificacao)];
  const vistos = new Set<string>();
  const grupos: GrupoDeFalha[] = [];
  for (const g of linhas.map(lerGrupo)) {
    if (g && !vistos.has(g.id)) {
      vistos.add(g.id);
      grupos.push(g);
    }
  }
  const janela = isRecord(raw.janela) ? raw.janela : null;
  return { dias: num(raw.dias) ?? num(janela?.dias), grupos, outro_pct: lerOutroPct(raw) };
}

/** O falso positivo do verificador (o sucesso mascarado) SEMPRE no topo; depois o custo total, depois a frequência.
 *  Sem o custo total em todos (os pesos ficam no config do backend e não se repetem aqui), vale a ordem em que o
 *  backend mandou — que já é a do custo. */
export function ordenarFalhas(grupos: readonly GrupoDeFalha[]): GrupoDeFalha[] {
  const comCusto = grupos.every((g) => g.custo_total !== null);
  const indice = new Map(grupos.map((g, i) => [g.id, i]));
  const custo = (g: GrupoDeFalha) => g.custo_total ?? 0;
  return grupos.slice().sort((a, b) => Number(b.falso_positivo) - Number(a.falso_positivo)
    || (comCusto ? custo(b) - custo(a) || b.ocorrencias - a.ocorrencias || a.id.localeCompare(b.id)
      : (indice.get(a.id) ?? 0) - (indice.get(b.id) ?? 0)));
}

function usd(n: number | null): string {
  return n === null ? '—' : `US$ ${n.toFixed(2).replace('.', ',')}`;
}

/** O que "Copiar para sessão" põe na área de transferência: o item pronto para colar numa sessão de desenvolvimento
 *  (a mesma chave `fk-*` do backlog, onde alterar e como provar a correção). */
export function mdDoItem(g: GrupoDeFalha): string {
  if (g.md) return g.md;
  const onde = g.capability === '*' ? g.app : `${g.app} › ${g.capability}`;
  const linhas = [
    `## ${g.id} — ${rotuloDaFalha(g.failure_kind)}${g.titulo ? `: ${g.titulo}` : ''}`,
    '',
    `- Onde: ${onde}${g.failure_screen ? ` (tela ${g.failure_screen})` : ''}`,
    `- Tipo: \`${g.failure_kind}\` · camada: ${rotuloDaCamada(g.camada)}${g.retroativo ? ' · classificado na leitura (retroativo)' : ''}`,
    `- Ocorrências: ${g.ocorrencias}${g.taxa !== null ? ` (taxa ${(g.taxa * 100).toFixed(0)}%)` : ''}`
      + `${g.execucoes !== null ? ` em ${g.execucoes} execução(ões)` : ''}${g.aparelhos !== null ? `, ${g.aparelhos} aparelho(s)` : ''}`,
    `- Custo: ${usd(g.usd_perdido)} perdidos · ${g.min_perdidos ?? '—'} min · ${g.intervencoes ?? '—'} intervenção(ões) humana(s)`,
  ];
  if (g.estado_backlog) linhas.push(`- Backlog: ${g.estado_backlog}`);
  if (g.onde_alterar.length > 0) linhas.push(`- Onde alterar: ${g.onde_alterar.map((a) => `\`${a}\``).join(', ')}${g.doc ? ` (doc: ${g.doc})` : ''}`);
  if (g.prova) linhas.push(`- Prova da correção: ${g.prova}`);
  if (g.exemplos.length > 0) {
    linhas.push('- Exemplos:');
    for (const x of g.exemplos) linhas.push(`  - ${x.run_id}${x.attempt_id ? ` / ${x.attempt_id}` : ''}${x.erro ? ` — ${x.erro}` : ''}`);
  }
  return linhas.join('\n');
}

// ---------------------------------------------------------------- sinais (A4)

export type Polaridade = 'positive' | 'negative' | 'neutral';

export interface Sinal {
  id: number | null;
  kind: string;
  polarity: Polaridade | null;
  verdict: Veredito | null;
  reason: string | null;
  /** Já redigida pelo backend (e recusada quando parece credencial). */
  note: string | null;
  source_ref: string;
  created_by: string;
  created_at: string | null;
  run_id: string | null;
  objective_id: string | null;
  app_package: string;
  capability: string;
  /** Os nomes em português do app e da capability (os do Aprendido); `null` quando não se sabe. */
  app_nome: string | null;
  capability_nome: string | null;
  failure_kind: string | null;
  simulated: boolean;
  /** `data.template_id` do `parecer_decidido`: `curador` (parecer da IA) ou `intencao` (o rótulo do 30.25). */
  template: string | null;
}

export const SINAL_LABEL: Record<string, string> = {
  feedback: 'Voto (deu certo / deu errado)', confirmou_a_mao: 'Confirmou à mão', repetiu_item: 'Repetiu o item',
  abandonou_item: 'Abandonou o item', repetiu_execucao: 'Repetiu a execução', cancelou_execucao: 'Cancelou a execução',
  tomou_controle: 'Tomou o controle', respondeu_pergunta: 'Respondeu uma pergunta',
  escolheu_habilidade: 'Escolheu a habilidade', aprovacao_decidida: 'Decidiu uma aprovação',
  comando_incerto_resolvido: 'Resolveu um comando incerto', correcao_de_ensino: 'Corrigiu no ensino',
  tela_vista: 'Tela vista', tela_desconhecida_chamou_pessoa: 'Tela desconhecida chamou uma pessoa',
  pediu_revisao: 'Pediu revisão ao curador', parecer_decidido: 'Decidiu um parecer do curador',
};

export const SINAL_KINDS: readonly string[] = Object.keys(SINAL_LABEL);

export function rotuloDoSinal(k: string): string {
  return SINAL_LABEL[k] ?? k;
}

/** O rótulo de UMA linha: a resposta a "Qual era o pedido?" (30.25) também é um `parecer_decidido`, sem IA nenhuma. */
export function rotuloDaLinhaDeSinal(s: Pick<Sinal, 'kind' | 'template'>): string {
  return s.kind === 'parecer_decidido' && s.template === 'intencao' ? 'Disse qual era o pedido' : rotuloDoSinal(s.kind);
}

function isPolaridade(v: unknown): v is Polaridade {
  return v === 'positive' || v === 'negative' || v === 'neutral';
}

function lerSinal(v: unknown): Sinal | null {
  if (!isRecord(v)) return null;
  const kind = str(v.kind);
  if (!kind) return null;
  return {
    id: num(v.id), kind, polarity: isPolaridade(v.polarity) ? v.polarity : null,
    verdict: isVeredito(v.verdict) ? v.verdict : null, reason: str(v.reason), note: str(v.note),
    source_ref: str(v.source_ref) ?? '', created_by: str(v.created_by) ?? '', created_at: str(v.created_at),
    run_id: str(v.run_id), objective_id: str(v.objective_id), app_package: str(v.app_package) ?? '',
    capability: str(v.capability) ?? '', app_nome: str(v.app_nome), capability_nome: str(v.capability_nome),
    failure_kind: str(v.failure_kind), simulated: bool(v.simulated),
    template: isRecord(v.data) ? str(v.data.template_id) : null,
  };
}

export function lerSinais(raw: unknown): Sinal[] {
  const linhas = Array.isArray(raw) ? raw : isRecord(raw) ? campo(raw, 'sinais', 'itens', 'signals') : undefined;
  return lista(linhas).map(lerSinal).filter((s): s is Sinal => s !== null);
}

// ---------------------------------------------------------------- o voto do D2 (A4)

export type Veredito = 'certo' | 'errado';

export function isVeredito(v: unknown): v is Veredito {
  return v === 'certo' || v === 'errado';
}

export type Motivo = 'fez_outra_coisa' | 'alvo_errado' | 'nao_terminou' | 'texto_ruim' | 'demorou_ou_gastou'
  | 'pediu_ajuda_a_toa' | 'outro';

/** `vocabulario.py::MotivoDoVoto`. Os de navegação rebaixam o que o item usou e o que aprendeu. */
export const MOTIVOS: readonly { id: Motivo; label: string; efeito: string; navegacao: boolean }[] = [
  { id: 'fez_outra_coisa', label: 'Fez outra coisa', efeito: 'desliga o fluxo e as receitas envolvidos', navegacao: true },
  { id: 'alvo_errado', label: 'Alvo errado', efeito: 'desliga o fluxo e as receitas envolvidos', navegacao: true },
  { id: 'nao_terminou', label: 'Não terminou', efeito: 'desliga o fluxo e as receitas envolvidos', navegacao: true },
  { id: 'texto_ruim', label: 'Texto ruim', efeito: 'vira evidência para a voz da persona', navegacao: false },
  { id: 'demorou_ou_gastou', label: 'Demorou ou gastou demais', efeito: 'só entra no relatório', navegacao: false },
  { id: 'pediu_ajuda_a_toa', label: 'Pediu ajuda à toa', efeito: 'evidência para tela e lição da etapa', navegacao: false },
  { id: 'outro', label: 'Outro', efeito: 'só entra no relatório', navegacao: false },
];

export function rotuloDoMotivo(m: string | null | undefined): string | null {
  if (!m) return null;
  return MOTIVOS.find((x) => x.id === m)?.label ?? m;
}

export function isMotivo(v: unknown): v is Motivo {
  return typeof v === 'string' && MOTIVOS.some((m) => m.id === v);
}

/** Limite da nota do voto (`servico.py::NOTA_MAX`). */
export const NOTA_MAX = 500;

export interface CorpoDoVoto {
  objective_id?: string;
  verdict: Veredito;
  reason?: Motivo;
  note?: string;
}

export interface Voto {
  objective_id: string | null;
  verdict: Veredito;
  reason: string | null;
  created_by: string | null;
}

export interface EfeitoDoVoto {
  kind: string;
  ref: string;
  de: string | null;
  para: string | null;
  /** Quando o backend diz: `false` = o efeito não foi aplicado (e `erro` diz por quê). */
  aplicado?: boolean | null;
  erro?: string | null;
  /** Opaco: verdadeiro, ou a chamada de volta (`{method, href, body: {to, reason}}`), quando dá para desfazer. */
  desfazer: unknown;
}

export interface RespostaDoVoto {
  efeitos: EfeitoDoVoto[];
  resumo: string;
}

function lerEfeito(v: unknown): EfeitoDoVoto | null {
  if (!isRecord(v)) return null;
  const kind = str(v.kind);
  const ref = typeof v.ref === 'number' ? String(v.ref) : str(v.ref);
  if (!kind || !ref) return null;
  return { kind, ref, de: str(v.de), para: str(v.para), aplicado: typeof v.aplicado === 'boolean' ? v.aplicado : null,
           erro: str(v.erro), desfazer: v.desfazer ?? null };
}

export function lerRespostaDoVoto(raw: unknown): RespostaDoVoto {
  if (!isRecord(raw)) return { efeitos: [], resumo: '' };
  return { efeitos: lista(raw.efeitos).map(lerEfeito).filter((e): e is EfeitoDoVoto => e !== null),
           resumo: str(raw.resumo) ?? '' };
}

/** O que "Aprendizado desta execução" mostra, quando o backend o manda (bloco opcional de `GET …/feedback`). */
export interface ItemAprendidoNaExecucao {
  grupo: 'receita' | 'fluxo' | 'falha' | 'candidata' | 'licao';
  kind: string | null;
  ref: string | null;
  texto: string;
  estado: string | null;
  papel: string | null;
}

export interface FeedbackDaExecucao {
  votos: Voto[];
  sinais: Sinal[];
  aprendizado: ItemAprendidoNaExecucao[] | null;
}

function lerVoto(v: unknown): Voto | null {
  if (!isRecord(v) || !isVeredito(v.verdict)) return null;
  return { objective_id: str(v.objective_id), verdict: v.verdict, reason: str(v.reason), created_by: str(v.created_by) };
}

const GRUPOS_DO_APRENDIZADO: readonly [ItemAprendidoNaExecucao['grupo'], string][] = [
  ['receita', 'receitas'], ['fluxo', 'fluxos'], ['falha', 'falhas'], ['candidata', 'candidatas'], ['licao', 'licoes'],
];

function lerAprendizado(v: unknown): ItemAprendidoNaExecucao[] | null {
  if (!isRecord(v)) return null;
  const saida: ItemAprendidoNaExecucao[] = [];
  for (const [grupo, chave] of GRUPOS_DO_APRENDIZADO) {
    for (const linha of lista(v[chave])) {
      if (!isRecord(linha)) continue;
      const ref = typeof linha.ref === 'number' ? String(linha.ref) : str(linha.ref);
      const tipo = str(linha.failure_kind);
      const n = num(linha.n);
      const texto = str(campo(linha, 'titulo', 'title'))
        ?? (grupo === 'falha' && tipo ? `${rotuloDaFalha(tipo)}${n !== null ? ` × ${n}` : ''}` : ref ?? '');
      if (!texto) continue;
      const braco = str(linha.braco);
      saida.push({ grupo, kind: str(linha.kind) ?? (grupo === 'receita' || grupo === 'fluxo' ? grupo : grupo === 'licao' ? 'licao' : null),
                   ref, texto, estado: str(campo(linha, 'estado', 'state')),
                   papel: str(linha.papel) ?? (braco ? (braco === 'with' ? 'exposta ao prompt' : 'braço de controle') : null) });
    }
  }
  return saida;
}

export function lerFeedbackDaExecucao(raw: unknown): FeedbackDaExecucao {
  if (!isRecord(raw)) return { votos: [], sinais: [], aprendizado: null };
  return {
    votos: lista(raw.votos).map(lerVoto).filter((v): v is Voto => v !== null),
    sinais: lerSinais(raw.sinais),
    aprendizado: lerAprendizado(raw.aprendizado),
  };
}

/** O voto de um item (`objectiveId`) ou da execução inteira (`null`). Com `quem`, só o voto DESSA pessoa: é um voto
 *  por pessoa e por item, e marcar o botão com o voto de outra pessoa diria que foi ela quem votou. */
export function votoDoItem(f: FeedbackDaExecucao | null | undefined, objectiveId: string | null,
                           quem?: string | null): Voto | null {
  if (!f) return null;
  return f.votos.find((v) => v.objective_id === objectiveId && (!quem || v.created_by === quem)) ?? null;
}

// ---------------------------------------------------------------- desfazer (um clique, com trilha)

/** Status nativo → estado do livro (`domain/livro.py::ESTADO_DA_RECEITA` e `ESTADO_DO_FLUXO`). */
const NATIVO: Partial<Record<LivroKind, Record<string, EstadoDoLivro>>> = {
  receita: { active: 'published', candidate: 'candidate', validated: 'validated', quarantined: 'disabled', superseded: 'deprecated' },
  fluxo: { active: 'published', candidate: 'candidate', validated: 'validated', disabled: 'disabled' },
};

/** O estado do livro de um valor que pode vir no vocabulário do livro ou no status nativo da fonte. */
export function estadoDoLivro(kind: LivroKind, valor: string | null | undefined): EstadoDoLivro | null {
  if (!valor) return null;
  const nativo = NATIVO[kind]?.[valor];
  if (nativo) return nativo;
  return isEstadoDoLivro(valor) ? valor : null;
}

/** O motivo gravado na trilha quando o backend não sugere um. */
export const MOTIVO_DO_DESFAZER = 'Desfeito no painel, logo depois do voto';

/** Desfazer um efeito do voto é a transição de volta pelo livro (`POST /api/aprendizado/{kind}/{ref}/status`): o
 *  `to` sai sempre no vocabulário do livro, porque a rota recusa (422) o status nativo. O backend (A4) manda a
 *  chamada pronta em `desfazer.body`; sem ela, a volta é para o estado `de`. */
export function desfazerDoEfeito(e: EfeitoDoVoto): { kind: LivroKind; ref: string; to: EstadoDoLivro; reason: string } | null {
  if (!e.desfazer || !isLivroKind(e.kind) || e.aplicado === false) return null;
  const pedido = isRecord(e.desfazer) ? e.desfazer : null;
  const corpo = pedido && isRecord(pedido.body) ? pedido.body : pedido;
  const kind = pedido && isLivroKind(pedido.kind) ? pedido.kind : e.kind;
  const ref = (pedido && str(pedido.ref)) ?? e.ref;
  const to = estadoDoLivro(kind, (corpo && str(corpo.to)) ?? e.de);
  return to ? { kind, ref, to, reason: (corpo && str(corpo.reason)) ?? MOTIVO_DO_DESFAZER } : null;
}

/** "fluxo f-1 desligado" — a linha do "o que mudou" quando o backend não manda o resumo. */
export function textoDoEfeito(e: EfeitoDoVoto): string {
  const alvo = isLivroKind(e.kind) ? rotuloDoKind(e.kind).toLowerCase() : e.kind;
  const para = isLivroKind(e.kind) ? estadoDoLivro(e.kind, e.para) : null;
  const base = `${alvo} ${e.ref}${para ? `: ${rotuloDoEstado(para).toLowerCase()}` : e.para ? `: ${e.para}` : ''}`;
  return e.aplicado === false ? `${base} (não aplicado${e.erro ? `: ${e.erro}` : ''})` : base;
}
