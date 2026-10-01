/*
 * Levantamento de texto truncado sem tooltip (rodada 2, tarefa 11). Sem dependências: só DOM.
 *
 * Como usar: cole o arquivo inteiro no console do navegador (ou no `javascript_tool` do painel da IDE) com a tela
 * aberta na largura que se quer medir. O resultado da última expressão é o relatório; `window.uiTruncamento()` fica
 * disponível para chamar de novo depois de navegar, sem recolar.
 *
 *   uiTruncamento()            -> { valido, largura, rolagemHorizontal, total, casos: [...], cortesMudos: [...] }
 *
 * O que conta como CASO (o que o critério de aceite zera):
 *   - `elipse`: elemento visível com `text-overflow: ellipsis` cujo texto realmente transborda
 *     (`scrollWidth > clientWidth`) e que NÃO tem `title` nem `aria-label` próprio, nem `title` num ancestral;
 *   - `clamp`: elemento visível com `-webkit-line-clamp` cujo texto passa das linhas (`scrollHeight > clientHeight`)
 *     e que não tem tooltip nem "ver mais" declarado (`data-ver-mais`, `aria-expanded` no próprio ou no irmão de controle).
 * O hospedeiro do `Tooltip`/`Popover` do projeto (classe `tooltipHost`/`popoverHost`) também conta como tooltip.
 * `aria-label` de um ANCESTRAL não vale: ele rotula o botão, não devolve o texto cortado.
 *
 * `regiao` separa o cabeçalho (`header`, da tarefa 10) do conteúdo; `totalConteudo` é o número que o aceite da 11 zera.
 *
 * `cortesMudos` é informativo (ignora o texto só para leitor de tela, `.sr-only`, de 1 px): elemento que corta o texto SEM reticências (`overflow: hidden` + `nowrap`, transbordando)
 * e sem tooltip. Não entra no total, mas é o mesmo defeito (dado que some sem aviso).
 */
(function () {
  function visivel(el) {
    if (!(el instanceof HTMLElement)) return false;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  }

  function temTooltip(el) {
    if (el.getAttribute('title') || el.getAttribute('aria-label')) return true;
    for (var a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      if (a.getAttribute('title')) return true;
      // O Tooltip e o Popover do projeto não usam `title`: o hospedeiro abre o texto por foco, mouse e toque.
      if (typeof a.className === 'string' && /(^|[\s_])(tooltipHost|popoverHost)([\s_]|$)/.test(a.className)) return true;
    }
    return false;
  }

  function temVerMais(el) {
    if (el.hasAttribute('data-ver-mais')) return true;
    var p = el.parentElement;
    if (!p) return false;
    return !!p.querySelector('[aria-expanded][aria-controls]');
  }

  function caminho(el) {
    var partes = [];
    for (var n = el; n && n !== document.body && partes.length < 4; n = n.parentElement) {
      var nome = n.tagName.toLowerCase();
      var cls = (typeof n.className === 'string' ? n.className : '').split(/\s+/).filter(Boolean)[0];
      partes.unshift(cls ? nome + '.' + cls.replace(/_[A-Za-z0-9]{5,}$/, '') : nome);
    }
    return partes.join(' > ');
  }

  // O cabeçalho é de outra tarefa (10): os casos dele saem marcados para não se misturarem com os do conteúdo.
  function regiao(el) {
    return el.closest('header, [role="banner"]') ? 'cabecalho' : 'conteudo';
  }

  function texto(el) {
    return (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 90);
  }

  function levantar() {
    var casos = [];
    var cortesMudos = [];
    var todos = document.body.querySelectorAll('*');
    for (var i = 0; i < todos.length; i++) {
      var el = todos[i];
      if (!visivel(el)) continue;
      var cs = getComputedStyle(el);
      var transbordaX = el.scrollWidth > el.clientWidth + 1;
      if (cs.textOverflow === 'ellipsis' && transbordaX) {
        if (!temTooltip(el)) casos.push({ tipo: 'elipse', regiao: regiao(el), caminho: caminho(el), texto: texto(el), largura: el.clientWidth, conteudo: el.scrollWidth });
        continue;
      }
      var clamp = cs.webkitLineClamp || cs.getPropertyValue('-webkit-line-clamp');
      if (clamp && clamp !== 'none' && el.scrollHeight > el.clientHeight + 1) {
        if (!temTooltip(el) && !temVerMais(el)) casos.push({ tipo: 'clamp', regiao: regiao(el), caminho: caminho(el), texto: texto(el), largura: el.clientWidth, conteudo: el.scrollHeight });
        continue;
      }
      if (cs.overflowX === 'hidden' && cs.whiteSpace === 'nowrap' && cs.textOverflow !== 'ellipsis' && transbordaX
          && el.clientWidth > 2 && el.children.length === 0 && (el.textContent || '').trim() && !temTooltip(el)) {
        cortesMudos.push({ tipo: 'corte', regiao: regiao(el), caminho: caminho(el), texto: texto(el), largura: el.clientWidth, conteudo: el.scrollWidth });
      }
    }
    var de = document.documentElement;
    return {
      // Sem <header> é a tela de entrada (sessão perdida): a medição não vale e não pode passar por "0 casos".
      valido: !!document.querySelector('header'),
      largura: window.innerWidth,
      rota: location.hash,
      rolagemHorizontal: de.scrollWidth > de.clientWidth + 1,
      total: casos.length,
      totalConteudo: casos.filter(function (c) { return c.regiao === 'conteudo'; }).length,
      casos: casos,
      cortesMudos: cortesMudos,
    };
  }

  window.uiTruncamento = levantar;
  if (typeof module !== 'undefined' && module.exports) module.exports = levantar;
  return levantar();
})();
