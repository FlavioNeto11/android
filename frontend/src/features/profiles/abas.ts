/**
 * As guias da persona, na ordem da tela, e as 5 seções que as agrupam. Ficam num módulo próprio porque a lista
 * (pedido vindo do Foco), o shell e a Visão geral (atalhos "ir para") falam delas, e o shell importa as guias — sem
 * ciclo de import.
 *
 * "Contas e acesso" funde as antigas Contas + Autenticação; "Aparelhos" substitui "Aparelho"; "Imagens" é nova.
 *
 * A URL carrega a GUIA (`#/personas/<persona>/memoria`), nunca a seção: a seção é derivada da guia (`secaoDaAba`).
 * Por isso agrupar em 5 seções não quebrou nenhum link antigo — `…/memoria` abre a Memória, agora dentro de Perfil.
 */
export type Aba = 'visao' | 'persona' | 'contas' | 'imagens' | 'aparelhos' | 'memoria' | 'interacoes'
  | 'habilidades' | 'aprovacoes' | 'execucoes' | 'config';

export const ABAS: readonly Aba[] = ['visao', 'persona', 'contas', 'imagens', 'aparelhos', 'memoria', 'interacoes',
                                     'habilidades', 'aprovacoes', 'execucoes', 'config'];

/** A guia pedida por outra tela (`openPersona(id, tab)`): texto livre, então só vale se for uma guia desta tela. */
export function abaDoPedido(tab: string | null | undefined): Aba {
  return tab && (ABAS as readonly string[]).includes(tab) ? (tab as Aba) : 'visao';
}

export type SecaoId = 'visao' | 'perfil' | 'contas' | 'atividade' | 'avancado';

export interface SecaoDef {
  id: SecaoId;
  rotulo: string;
  /** As guias da seção, na ordem em que aparecem; a primeira é a que abre ao escolher a seção. */
  guias: readonly Aba[];
}

/**
 * Primeiro nível da navegação: no máximo 5 itens. "Contas e aparelhos" (e não "dispositivos") segue o glossário do
 * portal, o mesmo título do cartão da Visão geral.
 */
export const SECOES: readonly SecaoDef[] = [
  { id: 'visao', rotulo: 'Visão geral', guias: ['visao'] },
  { id: 'perfil', rotulo: 'Perfil', guias: ['persona', 'imagens', 'memoria'] },
  { id: 'contas', rotulo: 'Contas e aparelhos', guias: ['contas', 'aparelhos'] },
  { id: 'atividade', rotulo: 'Atividade', guias: ['interacoes', 'execucoes', 'aprovacoes'] },
  { id: 'avancado', rotulo: 'Avançado', guias: ['habilidades', 'config'] },
];

/** Em que seção a guia mora. Toda guia tem uma (o teste de `abas.test.ts` garante). */
export function secaoDaAba(aba: Aba): SecaoDef {
  return SECOES.find((s) => s.guias.includes(aba)) ?? SECOES[0]!;
}

/** A guia que abre ao escolher a seção (a primeira dela). */
export function abaPadraoDaSecao(secao: SecaoDef): Aba {
  return secao.guias[0]!;
}

/**
 * A guia que abre ao clicar na seção, sabendo do que o selo dela conta (B3, rodada 2): o selo da "Atividade" conta as
 * Aprovações que esperam a pessoa, então, havendo alguma, o clique leva às Aprovações (a guia que explica o número) e
 * não às Interações. Sem pendência, vale a primeira guia da seção.
 */
export function abaAoEscolherSecao(secao: SecaoDef, aprovacoesPendentes: number | null): Aba {
  if (secao.id === 'atividade' && aprovacoesPendentes) return 'aprovacoes';
  return abaPadraoDaSecao(secao);
}
