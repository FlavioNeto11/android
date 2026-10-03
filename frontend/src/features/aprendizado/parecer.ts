/**
 * O parecer da IA no painel (30.17): os tipos do que o backend manda (`presentation/livro.py::_revisao`,
 * `_parecer_na_fila`, `_bloco_da_ia`) e o texto em português de cada rótulo fechado. Nenhuma regra mora aqui: o
 * backend já diz o passo do aceite (`acao`), por que o gesto não vale (`recusa`) e se o parecer aparece (o modo).
 */
import type { EstadoDoLivro, RotuloDaAcao } from './model';

export type ModoDoCurador = 'off' | 'shadow' | 'on';
export type ClasseDeRisco = 'A' | 'B' | 'C';
export type DecisaoDaIA =
  | 'aprovar' | 'observar' | 'pedir_evidencia' | 'rebaixar' | 'desativar' | 'substituir' | 'fundir'
  | 'possivelmente_obsoleto' | 'manter';

/** O passo que aceitar dá (`null` = aceitar é concordar, sem transição). */
export interface PassoDoAceite {
  to: EstadoDoLivro;
  rotulo: RotuloDaAcao;
}

/** A saída validada da IA (`Parecer.como_dados`): rótulos fechados e a `conclusao`, o único texto livre. */
export interface SaidaDoParecer {
  decisao: DecisaoDaIA;
  alvo: string | null;
  faixa: ClasseDeRisco | null;
  causa: string | null;
  confianca: 'baixa' | 'media' | 'alta' | null;
  probabilidade: number | null;
  evidencias_citadas: string[];
  riscos: string[];
  inconsistencias: string[];
  falta: string[];
  conclusao: string | null;
}

/** Uma revisão do curador, como o detalhe a recebe. */
export interface ParecerDaIA {
  id: string;
  criado_em: string;
  gatilho: string;
  /** `ok`, `invalida:<motivo>` ou `recusada:<motivo>`. */
  validade: string;
  classe: ClasseDeRisco | null;
  simulated: boolean;
  modelo: string | null;
  estado_no_parecer: string | null;
  parecer: SaidaDoParecer | null;
  /** É o parecer que uma decisão de agora responde. */
  atual: boolean;
  acao: PassoDoAceite | null;
  /** Por que aceitar ou recusar não vale agora (simulado, classe A...); `null` quando vale. */
  recusa: string | null;
  /** `aceitou`, `recusou` ou, às cegas, o rótulo da ação; `null` = ainda sem decisão. */
  decisao_final: string | null;
  decidido_por: string | null;
  override: boolean;
  override_motivo: string | null;
  transicao_id: number | null;
}

/** O parecer pendente de um item da fila (só com o curador em `on`). */
export interface ParecerNaFila {
  id: string;
  criado_em: string;
  decisao: DecisaoDaIA;
  confianca: 'baixa' | 'media' | 'alta' | null;
  classe: ClasseDeRisco | null;
  simulated: boolean;
  acao: PassoDoAceite | null;
  recusa: string | null;
  /** Por que ele fica fora do aceite em lote (classe C, simulado...); `null` = entra. */
  recusa_no_lote: string | null;
}

export interface BlocoDoCurador {
  modo: ModoDoCurador;
  /** Pareceres pendentes que o modo esconde: aparecem depois da decisão da pessoa. */
  pendentes_ocultos: number;
  pode_pedir_revisao: boolean;
}

export interface RespostaDoPedido {
  /** O pedido entrou; `false` quando o estado de agora do item já tem revisão (`revisao`). */
  pedido: boolean;
  revisao: ParecerDaIA | null;
}

// ---------------------------------------------------------------- textos

const DECISAO: Record<DecisaoDaIA, string> = {
  aprovar: 'Aprovar',
  observar: 'Observar mais um pouco',
  pedir_evidencia: 'Pedir mais evidência',
  rebaixar: 'Rebaixar',
  desativar: 'Desligar',
  substituir: 'Trocar por outro item',
  fundir: 'Juntar com outro item',
  possivelmente_obsoleto: 'Pode estar obsoleto',
  manter: 'Manter como está',
};

export function textoDaDecisao(d: string | null | undefined): string {
  return d ? DECISAO[d as DecisaoDaIA] ?? d : 'sem decisão';
}

/** O lado da sugestão, para o tom do selo: subir, descer ou esperar. */
export function ladoDaDecisao(d: string | null | undefined): 'sobe' | 'desce' | 'espera' {
  if (d === 'aprovar') return 'sobe';
  if (d === 'rebaixar' || d === 'desativar' || d === 'possivelmente_obsoleto' || d === 'substituir' || d === 'fundir') return 'desce';
  return 'espera';
}

const CONFIANCA: Record<string, string> = { baixa: 'confiança baixa', media: 'confiança média', alta: 'confiança alta' };

export function textoDaConfianca(c: string | null | undefined): string {
  return c ? CONFIANCA[c] ?? c : 'confiança sem medida';
}

const CLASSE: Record<ClasseDeRisco, { politica: string; sentido: string; gesto: string }> = {
  A: { politica: 'só registro', sentido: 'Navegação e leitura.', gesto: 'Quem decide é a regra automática; o parecer fica só registrado.' },
  B: { politica: 'aceite em lote', sentido: 'Efeito médio.', gesto: 'Você pode aceitar vários pareceres de uma vez.' },
  C: { politica: 'item a item', sentido: 'Alto risco (envio, conta, sessão).', gesto: 'Decida um item de cada vez.' },
};

/**
 * O selo da classe. Com o gesto valendo, diz a regra da classe ("Classe B · aceite em lote"); quando o parecer não
 * se decide (simulado, classe A), só a classe, e `registro` e a dica dizem por quê: nem o selo nem a dica prometem um
 * aceite que não há.
 */
export function seloDaClasse(c: ClasseDeRisco | null | undefined, recusa: string | null = null):
  { selo: string; explica: string; registro: string | null } | null {
  const k = c ? CLASSE[c] : undefined;
  if (!c || !k) return null;
  if (!recusa) return { selo: `Classe ${c} · ${k.politica}`, explica: `${k.sentido} ${k.gesto}`, registro: null };
  const registro = recusa === 'parecer_simulado' ? 'simulado · só registro' : recusa === 'so_registro_na_classe_a' ? 'só registro' : null;
  return { selo: `Classe ${c}`, explica: `${k.sentido} ${textoDaRecusa(recusa) ?? ''}`.trim(), registro };
}

const CAUSA: Record<string, string> = {
  reproduz_bem: 'reproduz bem', falha_recorrente: 'falha recorrente', evidencia_contraditoria: 'evidência contraditória',
  evidencia_insuficiente: 'pouca evidência', versao_nova_do_app: 'versão nova do app',
  substituido_por_outro: 'substituído por outro item', duplicado: 'duplicado',
  intervencao_humana: 'precisou de intervenção de uma pessoa', risco_do_efeito: 'risco do efeito', outra: 'outra causa',
};
const RISCO: Record<string, string> = {
  efeito_externo: 'efeito fora do sistema', irreversivel: 'irreversível', alvo_errado: 'alvo errado',
  conta_ou_sessao: 'conta ou sessão', texto_de_pessoa: 'texto de pessoa', versao_incompativel: 'versão incompatível',
  custo: 'custo',
};
const INCONSISTENCIA: Record<string, string> = {
  evidencia_a_favor_e_contra: 'evidência a favor e contra', voto_contra_reproducao: 'voto contra a reprodução',
  catalogo_diverge_do_conteudo: 'o catálogo do app diverge do conteúdo', versao_divergente: 'versão divergente',
  saude_diverge_do_estado: 'a saúde diverge do estado',
};
const FALTA: Record<string, string> = {
  reproducao_em_outro_aparelho: 'reproduzir em outro aparelho', reproducao_na_versao_viva: 'reproduzir na versão em uso',
  execucao_real: 'uma execução real', sombra: 'rodar em sombra', voto_da_pessoa: 'um voto seu',
  decisao_da_pessoa: 'a sua decisão',
};
const GATILHO: Record<string, string> = {
  nova_pendencia_do_dono: 'item novo à sua espera', a_revisar: 'legado a revisar', degradando: 'saúde caindo',
  obsoleto_provavel: 'provavelmente obsoleto', conflito: 'conflito com outro item', versao_nova: 'versão nova do app',
  grupo_de_falha_acima_do_minimo: 'falha recorrente', pedido_da_pessoa: 'pedido de uma pessoa',
  evidencia_chegou: 'chegou a evidência que o curador pediu',
};

const traduz = (mapa: Record<string, string>) => (k: string | null | undefined): string => (k ? mapa[k] ?? k : '');
export const textoDaCausa = traduz(CAUSA);
export const textoDoRisco = traduz(RISCO);
export const textoDaInconsistencia = traduz(INCONSISTENCIA);
export const textoDaFalta = traduz(FALTA);
export const textoDoGatilho = traduz(GATILHO);

/** Por que o gesto não vale (o `code` do 409 e o `recusa` do backend). */
const RECUSA: Record<string, string> = {
  parecer_invalido: 'A resposta do curador foi descartada: não há parecer para decidir.',
  parecer_ja_decidido: 'Este parecer já foi decidido.',
  parecer_desatualizado: 'O item mudou depois do parecer: decida pelo estado de agora.',
  parecer_simulado: 'Parecer de teste (provedor simulado): fica só como registro e não move item real.',
  parecer_oculto: 'Com o curador fora do modo ligado, o parecer pendente não aparece.',
  so_registro_na_classe_a: 'Classe A: o parecer é só registro; quem decide é a regra automática.',
  lote_na_classe_c: 'Classe C: decida item a item, fora do lote.',
  curador_fora_do_on: 'Pedir revisão só com o curador ligado.',
};

export function textoDaRecusa(code: string | null | undefined): string | null {
  return code ? RECUSA[code] ?? code : null;
}

/** A validade de uma revisão que não deu parecer. */
export function textoDaValidade(v: string): string | null {
  if (v === 'ok') return null;
  if (v === 'recusada:custo') return 'Não revisado: caro demais para o orçamento desta janela.';
  if (v === 'recusada:triagem') return 'Não revisado: o conteúdo parecia ter credencial e não saiu para o curador.';
  if (v.startsWith('invalida:')) return 'A resposta do curador veio fora do contrato e foi descartada.';
  return v;
}

const ACAO_NO_TEXTO: Record<RotuloDaAcao, string> = {
  validar: 'validar', aprovar: 'aprovar', rejeitar: 'rejeitar', aposentar: 'aposentar', desligar: 'desligar',
  reativar: 'reativar', devolver: 'devolver à prova',
};

/** O botão do aceite: "Aceitar e validar" quando aceitar transiciona; "Concordar" quando só registra. */
export function rotuloDoAceite(acao: PassoDoAceite | null): { label: string; confirmar: string; perigo: boolean } {
  if (!acao) return { label: 'Concordar', confirmar: 'Confirmar que concorda', perigo: false };
  const verbo = ACAO_NO_TEXTO[acao.rotulo] ?? acao.rotulo;
  const perigo = acao.rotulo === 'rejeitar' || acao.rotulo === 'desligar';
  return { label: `Aceitar e ${verbo}`, confirmar: `Confirmar: ${verbo}`, perigo };
}

const EFEITO_DO_PASSO: Record<RotuloDaAcao, string> = {
  validar: 'validado (ainda não publicado)', aprovar: 'publicado', rejeitar: 'rejeitado', aposentar: 'aposentado',
  desligar: 'desligado', reativar: 'reativado', devolver: 'devolvido à prova (inerte até provar de novo)',
};

/** O que o aceite fez com o item, para o aviso do lote: "validado" não é "publicado". */
export function efeitoDoAceite(acao: PassoDoAceite | null): string {
  return acao ? EFEITO_DO_PASSO[acao.rotulo] ?? acao.rotulo : 'concordância registrada; o item não muda';
}

/** Quem decidiu e como, em uma frase (o histórico do parecer). */
export function textoDaDecisaoFinal(p: Pick<ParecerDaIA, 'decisao_final' | 'decidido_por' | 'override' | 'override_motivo'>): string | null {
  const quem = p.decidido_por ?? 'alguém';
  if (!p.decisao_final) return null;
  if (p.decisao_final === 'aceitou') return `${quem} aceitou.`;
  if (p.decisao_final === 'recusou') return `${quem} recusou${p.override_motivo ? `: ${p.override_motivo}` : '.'}`;
  const acao = ACAO_NO_TEXTO[p.decisao_final as RotuloDaAcao] ?? p.decisao_final;
  return `${quem} decidiu sem ver o parecer (${acao}) e ${p.override ? 'foi para outro lado' : 'concordou com o curador'}.`;
}
