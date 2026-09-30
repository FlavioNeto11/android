/*
 * Auditoria de interface do portal — sem dependências, só DOM.
 *
 * NÃO é o axe nem o Lighthouse. Cobre apenas o que dá para medir com getComputedStyle/getBoundingClientRect:
 *   1. tamanho de fonte do texto visível (abaixo de 13 px);
 *   2. contraste WCAG 2.x do texto contra o fundo EFETIVO (composição das camadas translúcidas de trás para frente);
 *   3. alvos de clique (button, a[href], input, select, textarea, [role=button|tab|menuitem|switch|checkbox|...],
 *      summary) com altura ou largura abaixo de 32 px;
 *   4. presença de ícones sem nome acessível (botão sem texto nem aria-label).
 * NÃO cobre: ordem de tabulação, leitores de tela, contraste de ícones/bordas/gráficos, texto sobre imagem ou
 * gradiente, estados de hover/foco/desabilitado, conteúdo fora da tela ou em abas não abertas, nem as regras de
 * ARIA. Fundos com `backdrop-filter`, imagens ou gradientes são aproximados pelo ancestral opaco mais próximo.
 *
 * Uso (console do navegador, ou javascript_tool):
 *   const r = window.__uiAuditoria();          // objeto com resumo e listas
 *   JSON.stringify(window.__uiAuditoria({ resumo: true }))   // só contagens
 * Depois de colar/injetar este arquivo, `window.__uiAuditoria` fica definido. Somente leitura: não clica nada.
 */
(function () {
  'use strict';

  function parseCor(s) {
    // getComputedStyle devolve rgb(r, g, b) ou rgba(r, g, b, a) (ou color(srgb ...) em navegadores novos).
    if (!s) return null;
    var m = s.match(/rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)/);
    if (m) {
      var a = m[4] === undefined ? 1 : (m[4].indexOf('%') > -1 ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
      return { r: +m[1], g: +m[2], b: +m[3], a: a };
    }
    m = s.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+%?))?\s*\)/);
    if (m) {
      var a2 = m[4] === undefined ? 1 : (m[4].indexOf('%') > -1 ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
      return { r: +m[1] * 255, g: +m[2] * 255, b: +m[3] * 255, a: a2 };
    }
    return null;
  }

  function sobre(topo, base) {
    // composição "source-over": topo translúcido sobre base (base já opaca).
    var a = topo.a;
    return {
      r: topo.r * a + base.r * (1 - a),
      g: topo.g * a + base.g * (1 - a),
      b: topo.b * a + base.b * (1 - a),
      a: 1,
    };
  }

  function lum(c) {
    function canal(v) {
      v = v / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    }
    return 0.2126 * canal(c.r) + 0.7152 * canal(c.g) + 0.0722 * canal(c.b);
  }

  function razao(c1, c2) {
    var l1 = lum(c1);
    var l2 = lum(c2);
    var hi = Math.max(l1, l2);
    var lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  }

  // Fundo efetivo: sobe pelos ancestrais juntando os fundos translúcidos até achar um opaco; sem opaco, usa o <body>
  // e, por último, preto.
  function fundoEfetivo(el) {
    var camadas = [];
    var n = el;
    while (n && n.nodeType === 1) {
      var cs = getComputedStyle(n);
      var bg = parseCor(cs.backgroundColor);
      if (bg && bg.a > 0) {
        camadas.push(bg);
        if (bg.a >= 0.999) break;
      }
      n = n.parentElement;
    }
    var base = { r: 0, g: 0, b: 0, a: 1 };
    if (!camadas.length || camadas[camadas.length - 1].a < 0.999) {
      var bb = parseCor(getComputedStyle(document.body).backgroundColor);
      base = bb && bb.a >= 0.999 ? bb : base;
    } else {
      base = camadas.pop();
    }
    for (var i = camadas.length - 1; i >= 0; i--) base = sobre(camadas[i], base);
    return base;
  }

  function opacidadeEfetiva(el) {
    var o = 1;
    var n = el;
    while (n && n.nodeType === 1) {
      var v = parseFloat(getComputedStyle(n).opacity);
      if (!isNaN(v)) o *= v;
      n = n.parentElement;
    }
    return o;
  }

  function visivel(el) {
    if (!el.getClientRects().length) return false;
    var cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    var r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    // `.sr-only` e afins: 1 px, recortados
    return true;
  }

  function descricao(el) {
    var t = el.tagName.toLowerCase();
    if (el.id) t += '#' + el.id;
    var cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 2);
    if (cls.length) t += '.' + cls.join('.');
    var txt = (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    return t + (txt ? ' "' + txt + '"' : '');
  }

  function textoProprio(el) {
    var s = '';
    for (var i = 0; i < el.childNodes.length; i++) {
      if (el.childNodes[i].nodeType === 3) s += el.childNodes[i].nodeValue;
    }
    return s.replace(/\s+/g, ' ').trim();
  }

  var SELETOR_ALVO =
    'button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=menuitem], ' +
    '[role=switch], [role=checkbox], [role=radio], [role=link], [role=option]';

  function auditar(opcoes) {
    opcoes = opcoes || {};
    var minFonte = opcoes.minFonte || 13;
    var minAlvo = opcoes.minAlvo || 32;
    var out = {
      rota: location.hash,
      largura: window.innerWidth,
      fontes: [],
      contraste: [],
      alvos: [],
      semNome: [],
      contagens: {},
    };
    var vistoFonte = {};
    var todos = document.body.querySelectorAll('*');
    var nTexto = 0;

    for (var i = 0; i < todos.length; i++) {
      var el = todos[i];
      var tag = el.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'svg' || tag === 'path') continue;
      if (!visivel(el)) continue;
      var txt = textoProprio(el);
      if (txt) {
        nTexto++;
        var cs = getComputedStyle(el);
        var px = parseFloat(cs.fontSize);
        var peso = parseInt(cs.fontWeight, 10) || 400;
        // font-size 0 é o truque de texto só para leitor de tela (.sr-only / ícone sem rótulo): não é texto visível.
        if (px > 0 && px < minFonte) {
          var chave = px + '|' + descricao(el);
          if (!vistoFonte[chave]) {
            vistoFonte[chave] = 1;
            out.fontes.push({ px: px, el: descricao(el) });
          }
        }
        var fg = parseCor(cs.color);
        if (fg) {
          var bg = fundoEfetivo(el);
          var op = opacidadeEfetiva(el);
          var fgOpaco = sobre({ r: fg.r, g: fg.g, b: fg.b, a: fg.a * op }, bg);
          var r = razao(fgOpaco, bg);
          var grande = px >= 24 || (px >= 18.66 && peso >= 700);
          var meta = grande ? 3 : 4.5;
          var desab = el.closest('[disabled], [aria-disabled=true]');
          if (r < meta && !desab) {
            out.contraste.push({
              razao: Math.round(r * 100) / 100,
              meta: meta,
              px: px,
              el: descricao(el),
              cor: cs.color,
            });
          }
        }
      }
    }

    var alvos = document.body.querySelectorAll(SELETOR_ALVO);
    var vistoAlvo = new Set();
    for (var j = 0; j < alvos.length; j++) {
      var a = alvos[j];
      if (!visivel(a)) continue;
      if (a.closest('[inert], [aria-hidden=true]')) continue;
      var rc = a.getBoundingClientRect();
      var tipo = (a.getAttribute('type') || '').toLowerCase();
      // checkbox/radio dentro de <label> clicável: vale a maior área entre o campo e o rótulo.
      if ((tipo === 'checkbox' || tipo === 'radio') && a.closest('label')) {
        var rl = a.closest('label').getBoundingClientRect();
        if (Math.min(rl.width, rl.height) > Math.min(rc.width, rc.height)) rc = rl;
      }
      if (rc.width < minAlvo - 0.5 || rc.height < minAlvo - 0.5) {
        var d = descricao(a);
        if (vistoAlvo.has(d + Math.round(rc.x) + Math.round(rc.y))) continue;
        vistoAlvo.add(d + Math.round(rc.x) + Math.round(rc.y));
        out.alvos.push({ w: Math.round(rc.width), h: Math.round(rc.height), el: d });
      }
      if (
        (a.tagName === 'BUTTON' || a.tagName === 'A' || a.getAttribute('role') === 'button') &&
        !(a.getAttribute('aria-label') || '').trim() &&
        !(a.getAttribute('aria-labelledby') || '').trim() &&
        !(a.textContent || '').trim() &&
        !(a.getAttribute('title') || '').trim()
      ) {
        out.semNome.push({ el: descricao(a) });
      }
    }

    out.contagens = {
      elementosComTexto: nTexto,
      fonteAbaixoDe13: out.fontes.length,
      contrasteAbaixoDaMeta: out.contraste.length,
      alvosAbaixoDe32: out.alvos.length,
      botoesSemNome: out.semNome.length,
      rolagemHorizontal: document.documentElement.scrollWidth > window.innerWidth + 1,
    };

    if (opcoes.resumo) {
      var dist = {};
      out.fontes.forEach(function (f) {
        dist[f.px] = (dist[f.px] || 0) + 1;
      });
      return { rota: out.rota, largura: out.largura, contagens: out.contagens, fontesPorTamanho: dist };
    }
    // Ordena as piores primeiro, para o relatório.
    out.contraste.sort(function (x, y) { return x.razao - y.razao; });
    out.alvos.sort(function (x, y) { return x.w * x.h - y.w * y.h; });
    return out;
  }

  window.__uiAuditoria = auditar;

  // Utilitário para validar tokens sem página: window.__uiRazao('#8492a3', '#12161b').
  window.__uiRazao = function (fg, bg) {
    function hex(h) {
      h = h.replace('#', '');
      if (h.length === 3) h = h.split('').map(function (c) { return c + c; }).join('');
      return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: 1 };
    }
    return Math.round(razao(hex(fg), hex(bg)) * 100) / 100;
  };
})();
