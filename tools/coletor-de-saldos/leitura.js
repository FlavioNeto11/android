// Coletor de saldos (ADR-051): acha o SALDO no texto da página de faturamento de cada provedor.
//
// Script clássico (content script do Chrome não é módulo) e também módulo CommonJS, para o teste no Node. Não lê
// nada além do número: procura o rótulo do saldo e o primeiro valor em dinheiro logo depois dele.
(function (raiz) {
  'use strict';

  // Rótulo do cartão de saldo em cada console, em inglês e português (segue o idioma do navegador). Medido em
  // 28/09/2026: Claude Console "Credit balance … US$ 5,19"; OpenAI "API credit balance $8.25"; AI Studio (iframe
  // de payments.google.com) "Saldo de crédito R$ 29,37".
  var ROTULOS = {
    anthropic: [/Credit balance/i, /Saldo de cr[eé]dito/i],
    openai: [/API credit balance/i, /Saldo de cr[eé]dito da API/i, /Credit balance/i],
    gemini: [/Saldo de cr[eé]dito/i, /Credit balance/i],
  };
  // Até quantos caracteres depois do rótulo o valor pode estar. Curto de propósito: enquanto o cartão mostra
  // "Loading", o próximo dinheiro da página é OUTRO número (no Claude Console, o "$50.81 spent" do limite de gasto).
  // No Claude Console há uma frase explicativa de ~105 caracteres entre o rótulo e o valor.
  var JANELA = { anthropic: 200, openai: 60, gemini: 60 };
  var CARREGANDO = /Loading|Carregando/i;
  var DINHEIRO = /(R\$|US\$|\$)\s?(-?\d[\d.,\s]*\d|\d)/;

  // "5,19" / "8.25" / "1.467,58" / "1,467.58" / "29" → número. A regra: o ÚLTIMO separador seguido de exatamente
  // 2 dígitos é o decimal; qualquer outro separador é de milhar (saldo nesses consoles sempre tem centavos).
  function numero(txt) {
    var t = String(txt).replace(/[\s]/g, '');
    var neg = t.charAt(0) === '-';
    if (neg) t = t.slice(1);
    var ult = Math.max(t.lastIndexOf(','), t.lastIndexOf('.'));
    var valor;
    if (ult === -1) {
      valor = Number(t);
    } else if (t.length - ult - 1 === 2) {
      valor = Number(t.slice(0, ult).replace(/[.,]/g, '') + '.' + t.slice(ult + 1));
    } else {
      valor = Number(t.replace(/[.,]/g, ''));
    }
    return neg ? -valor : valor;
  }

  // { balance, currency } ou null. null = a página ainda não mostrou o saldo (carregando, deslogada, outra tela).
  function extrair(conta, texto) {
    var rotulos = ROTULOS[conta];
    if (!rotulos || !texto) return null;
    for (var i = 0; i < rotulos.length; i++) {
      // Todas as ocorrências do rótulo, não só a primeira: o mesmo texto pode aparecer antes num menu ou dica.
      var re = new RegExp(rotulos[i].source, 'gi');
      var m;
      while ((m = re.exec(texto)) !== null) {
        var depois = texto.slice(m.index + m[0].length, m.index + m[0].length + JANELA[conta]);
        var d = DINHEIRO.exec(depois);
        if (!d || CARREGANDO.test(depois.slice(0, d.index))) continue;
        var valor = numero(d[2]);
        if (!isFinite(valor)) continue;
        return { balance: Math.round(valor * 100) / 100, currency: d[1] === 'R$' ? 'BRL' : 'USD' };
      }
    }
    return null;
  }

  // Qual conta esta PÁGINA representa. No Google o saldo mora num iframe de payments.google.com, que também aparece
  // em outras páginas do Google: quem confere que o topo é o AI Studio é o service worker (`sender.tab.url`).
  function contaDoHost(host) {
    if (host === 'platform.claude.com') return 'anthropic';
    if (host === 'platform.openai.com') return 'openai';
    if (host === 'payments.google.com') return 'gemini';
    return null;
  }

  // O topo da aba tem de ser o console da conta — nunca aceitar um número de outra página.
  function topoValido(conta, urlDoTopo) {
    var host;
    try { host = new URL(urlDoTopo).hostname; } catch (e) { return false; }
    return (conta === 'anthropic' && host === 'platform.claude.com')
      || (conta === 'openai' && host === 'platform.openai.com')
      || (conta === 'gemini' && host === 'aistudio.google.com');
  }

  var api = { extrair: extrair, numero: numero, contaDoHost: contaDoHost, topoValido: topoValido };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else raiz.coletorLeitura = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
