/** O conteúdo rola por conta própria (`main#conteudo`), não a janela.
 *
 * Trocar o que está dentro dele sem voltar ao topo deixa quem lê no meio do conteúdo NOVO: com a página rolada até
 * o fim, escolher outra execução mantinha o deslocamento antigo e a tela abria no meio do relatório. Trocar de
 * seção já volta ao topo (App.tsx); isto cobre as trocas DENTRO de uma seção — outra execução, abrir e voltar de um
 * perfil.
 */
/** O `<main>` do App (`App.tsx`), focável por programa (`tabIndex={-1}`). */
export const ID_CONTEUDO = 'conteudo';

export function conteudoAoTopo(): void {
  const el = document.getElementById(ID_CONTEUDO);
  if (el) el.scrollTop = 0;
}

/**
 * Leva o teclado ao conteúdo principal depois de trocar de tela por um atalho que mora FORA dele (a gaveta do menu,
 * um link do popover do semáforo): um Tab já cai no primeiro controle da tela nova. Sem isto, o atalho some da tela
 * com o foco dentro e ele cai no `<body>`, e quem usa teclado recomeça do topo (RF-45). Devolve se havia onde focar.
 */
export function focarConteudo(): boolean {
  const el = document.getElementById(ID_CONTEUDO);
  el?.focus();
  return !!el;
}
