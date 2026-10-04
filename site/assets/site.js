// Site institucional (29.77). Script pequeno e sem dependência: menu, exemplos que preenchem a mensagem e o envio do
// formulário em JSON. Nada vindo de fora entra no DOM a não ser por `textContent` (o site mora na mesma origem do
// painel; ADR-075). As mensagens ao visitante são texto fixo.
(function () {
  "use strict";

  var ROTA_DO_CONTATO = "/api/portal/contato";
  var TELEFONE_VALIDO = /^[0-9+()\- ]{8,30}$/;

  function menu() {
    var botao = document.querySelector(".menu-botao");
    var nav = document.getElementById("menu");
    if (!botao || !nav) return;
    function fechar() {
      nav.classList.remove("aberto");
      botao.setAttribute("aria-expanded", "false");
    }
    botao.addEventListener("click", function () {
      var aberto = nav.classList.toggle("aberto");
      botao.setAttribute("aria-expanded", aberto ? "true" : "false");
    });
    nav.addEventListener("click", function (evento) {
      if (evento.target.closest("a")) fechar();
    });
    document.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape" && nav.classList.contains("aberto")) {
        fechar();
        botao.focus();
      }
    });
  }

  // Abas dos casos de uso (padrão WAI-ARIA: setas, Home e End). Sem JS, todos os painéis ficam visíveis.
  function abas() {
    document.querySelectorAll("[data-abas]").forEach(function (caixa) {
      var botoes = Array.prototype.slice.call(caixa.querySelectorAll('[role="tab"]'));
      if (!botoes.length) return;
      function ativar(alvo, focar) {
        botoes.forEach(function (botao) {
          var ativo = botao === alvo;
          botao.setAttribute("aria-selected", ativo ? "true" : "false");
          botao.tabIndex = ativo ? 0 : -1;
          var painel = document.getElementById(botao.getAttribute("aria-controls"));
          if (painel) painel.hidden = !ativo;
        });
        if (focar) alvo.focus();
      }
      botoes.forEach(function (botao, i) {
        botao.addEventListener("click", function () { ativar(botao, false); });
        botao.addEventListener("keydown", function (evento) {
          var destino = null;
          if (evento.key === "ArrowDown" || evento.key === "ArrowRight") destino = botoes[(i + 1) % botoes.length];
          else if (evento.key === "ArrowUp" || evento.key === "ArrowLeft") destino = botoes[(i - 1 + botoes.length) % botoes.length];
          else if (evento.key === "Home") destino = botoes[0];
          else if (evento.key === "End") destino = botoes[botoes.length - 1];
          if (destino) {
            evento.preventDefault();
            ativar(destino, true);
          }
        });
      });
      caixa.classList.add("ativas");
      ativar(botoes[0], false);
    });
  }

  function exemplos() {
    var mensagem = document.getElementById("campo-mensagem");
    if (!mensagem) return;
    document.querySelectorAll(".exemplo").forEach(function (botao) {
      botao.addEventListener("click", function () {
        mensagem.value = botao.getAttribute("data-exemplo") || "";
        document.getElementById("contato").scrollIntoView({ block: "start" });
        mensagem.focus({ preventScroll: true });
        var fim = mensagem.value.length;
        mensagem.setSelectionRange(fim, fim);
      });
    });
  }

  function formulario() {
    var form = document.getElementById("formulario-contato");
    var estado = document.getElementById("estado-contato");
    if (!form || !estado) return;
    var enviando = false;

    function avisar(texto, tipo) {
      estado.textContent = texto;
      estado.className = "formulario-estado" + (tipo ? " " + tipo : "");
    }

    function marcar(campo, invalido) {
      if (invalido) campo.setAttribute("aria-invalid", "true");
      else campo.removeAttribute("aria-invalid");
      return invalido;
    }

    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      if (enviando) return;
      var nome = form.elements.nome, telefone = form.elements.telefone, mensagem = form.elements.mensagem;
      var aceite = form.elements.consentimento;
      var erros = [
        marcar(nome, !nome.value.trim()) && nome,
        marcar(telefone, !TELEFONE_VALIDO.test(telefone.value.trim())) && telefone,
        marcar(mensagem, !mensagem.value.trim()) && mensagem
      ].filter(Boolean);
      if (erros.length) {
        avisar("Confira os campos marcados: nome, um telefone com DDD e a mensagem.", "erro");
        erros[0].focus();
        return;
      }
      if (!aceite.checked) {
        avisar("Para enviar, marque a concordância com o uso dos dados.", "erro");
        aceite.focus();
        return;
      }
      var corpo = {
        nome: nome.value.trim(),
        empresa: form.elements.empresa.value.trim(),
        telefone: telefone.value.trim(),
        mensagem: mensagem.value.trim(),
        consentimento: true,
        site: form.elements.site.value,
        token: form.elements.token ? form.elements.token.value : ""
      };
      enviando = true;
      avisar("Enviando…", "");
      fetch(ROTA_DO_CONTATO, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "omit",
        body: JSON.stringify(corpo)
      }).then(function (resposta) {
        if (resposta.ok) {
          form.reset();
          avisar("Recebemos a sua mensagem. Obrigado! Vamos retornar pelo telefone que você deixou.", "sucesso");
        } else if (resposta.status === 429) {
          avisar("Recebemos muitas mensagens agora. Tente mais tarde, ou ligue ou chame no WhatsApp.", "erro");
        } else if (resposta.status === 400) {
          avisar("A página ficou aberta por muito tempo. Recarregue a página e envie de novo.", "erro");
        } else if (resposta.status === 422) {
          avisar("Confira os campos: nome, um telefone com DDD, a mensagem e a concordância.", "erro");
        } else {
          avisar("Não foi possível enviar agora. Tente de novo em instantes, ou ligue ou chame no WhatsApp.", "erro");
        }
      }).catch(function () {
        avisar("Não foi possível enviar agora. Tente de novo em instantes, ou ligue ou chame no WhatsApp.", "erro");
      }).then(function () {
        enviando = false;
      });
    });
  }

  menu();
  abas();
  exemplos();
  formulario();
})();
