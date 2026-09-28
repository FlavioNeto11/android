// Coletor de saldos (ADR-051) — roda DENTRO da página de faturamento (e no iframe de pagamentos do Google).
//
// Espera o cartão de saldo aparecer (a página é carregada por JavaScript) e manda só `{conta, balance, currency}`
// ao service worker. Nada mais sai daqui: nem texto da página, nem cookie, nem dado de pagamento.
(function () {
  'use strict';
  var leitura = globalThis.coletorLeitura;
  var conta = leitura && leitura.contaDoHost(location.hostname);
  if (!conta) return;

  var PRAZO_MS = 45000;
  var inicio = Date.now();
  var enviado = false;
  var observador = null;

  // Texto visível pelos nós de texto, sem `script`/`style`. `innerText` depende de layout e, no Claude Console, NÃO
  // traz o cartão de saldo (medido em 28/09/2026); `textContent` traz os scripts inteiros junto.
  function textoDaPagina() {
    if (!document.body) return '';
    var partes = [];
    var andador = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var tag = n.parentNode && n.parentNode.nodeName;
        return (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'TEMPLATE')
          ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      },
    });
    var n;
    while ((n = andador.nextNode())) {
      var t = n.nodeValue.trim();
      if (t) partes.push(t);
    }
    return partes.join('\n');
  }

  function tentar() {
    if (enviado) return true;
    var r = leitura.extrair(conta, textoDaPagina());
    if (!r) return false;
    enviado = true;
    try {
      chrome.runtime.sendMessage({ tipo: 'saldo', conta: conta, balance: r.balance, currency: r.currency });
    } catch (e) { /* extensão recarregada no meio: a próxima coleta resolve */ }
    return true;
  }

  function parar() {
    if (observador) { observador.disconnect(); observador = null; }
  }

  if (tentar()) return;
  observador = new MutationObserver(function () {
    if (tentar() || Date.now() - inicio > PRAZO_MS) parar();
  });
  observador.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  setTimeout(function () { tentar(); parar(); }, PRAZO_MS);
})();
