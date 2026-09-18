/** O conteúdo rola por conta própria (`main#conteudo`), não a janela.
 *
 * Trocar o que está dentro dele sem voltar ao topo deixa quem lê no meio do conteúdo NOVO: com a página rolada até
 * o fim, escolher outra execução mantinha o deslocamento antigo e a tela abria no meio do relatório. Trocar de
 * seção já volta ao topo (App.tsx); isto cobre as trocas DENTRO de uma seção — outra execução, abrir e voltar de um
 * perfil.
 */
export function conteudoAoTopo(): void {
  const el = document.getElementById('conteudo');
  if (el) el.scrollTop = 0;
}
