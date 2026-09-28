/**
 * As guias da persona, na ordem da tela. Ficam num módulo próprio porque a lista (pedido vindo do Foco), o shell e
 * a Visão geral (atalhos "ir para") falam delas, e o shell importa as guias — sem ciclo de import.
 *
 * "Contas e acesso" funde as antigas Contas + Autenticação; "Aparelhos" substitui "Aparelho"; "Imagens" é nova.
 */
export type Aba = 'visao' | 'persona' | 'contas' | 'imagens' | 'aparelhos' | 'memoria' | 'interacoes'
  | 'habilidades' | 'aprovacoes' | 'execucoes' | 'config';

export const ABAS: readonly Aba[] = ['visao', 'persona', 'contas', 'imagens', 'aparelhos', 'memoria', 'interacoes',
                                     'habilidades', 'aprovacoes', 'execucoes', 'config'];

/** A guia pedida por outra tela (`openPersona(id, tab)`): texto livre, então só vale se for uma guia desta tela. */
export function abaDoPedido(tab: string | null | undefined): Aba {
  return tab && (ABAS as readonly string[]).includes(tab) ? (tab as Aba) : 'visao';
}
