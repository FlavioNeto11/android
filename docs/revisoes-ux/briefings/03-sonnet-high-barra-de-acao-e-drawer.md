---
modelo_recomendado: Sonnet
forca_recomendada: high
---

> **Rodar em:** Sonnet · esforço high

# Tarefa 03: barra de ação em massa e drawer de foco

## Problema
- A barra flutuante "Ação em N aparelho(s)" (Iniciar, Parar, Hibernar, Reiniciar, Instalar app, Abrir app, Resetar dados) fica sobre os cards e cobre controles. Em 768 px quebra em duas linhas.
- Com o drawer de foco aberto, a barra repete os mesmos botões que o drawer já oferece.
- O drawer **comprime** a página (a grade e o formulário Comando se reorganizam) em vez de sobrepô-la. O preview do celular fica estreito e legendas truncam ("Prévia suspensa — último frame há 1 min 1…", "Somente visualização — assuma o contr…").
- "Resetar dados…" fica ao lado de ações rotineiras na barra.
- O painel tem quatro caminhos para a mesma coisa: Comando, drawer, barra em massa e tela do aparelho.

## O que fazer
1. **Barra de seleção**: mover para uma barra fixa **no topo da grade** (sticky), que só aparece quando há seleção. Mostrar "N selecionados", ações frequentes (Iniciar, Parar, Reiniciar) e um menu "⋯" com Hibernar, Instalar app, Abrir app. **Resetar dados** só no menu "⋯" ou no drawer, dentro de "Zona de perigo", sempre com confirmação existente.
2. Em telas estreitas a barra vira uma linha rolável ou um menu único; nunca cobre os cards.
3. **Drawer** deve sobrepor (overlay lateral) sem reflow da grade, com largura mínima que permita o preview legível e legendas sem truncar (quebre a linha ou encurte o texto). Fechar com Esc e clique fora. Devolver o foco ao card de origem ao fechar.
4. Com o drawer aberto, esconder a barra em massa para não duplicar ações.
5. Foco em teclado dentro do drawer (focus trap) e `aria-modal` correto.
6. Deixar claro no formulário Comando o que são **Refinar com IA**, **Planejar** e **Executar** (etapas sequenciais): estado desabilitado do Executar com o motivo em tooltip no próprio botão, não em aviso distante.
7. Trocar o link "Automático / escolher manualmente" por um controle segmentado de duas opções.

## Critérios de aceite
- Em 390, 768, 1024 e 1440 px a barra não cobre nenhum card.
- Nenhuma ação aparece duas vezes ao mesmo tempo (barra + drawer).
- Abrir o drawer não altera o layout da grade.
- Legendas do preview sem truncamento.
- Executar desabilitado mostra o motivo ao passar o mouse/foco.

## Cuidados
Não dispare Resetar dados, Instalar ou Executar em testes reais; valide só a interface e o fluxo de confirmação.

## Contexto comum (leia antes de agir)

Projeto: **Central de Aparelhos**, portal web (SPA com rotas por hash, ex.: `#/painel`, `#/perfis`) que controla emuladores Android e personas de IA (WhatsApp/Instagram etc.) em um servidor central e workers na LAN. Idioma da UI: português do Brasil. Tema escuro. Servido em `http://127.0.0.1:8000/`.

Telas: Painel, Personas, Aplicativos, Execuções, Aprendizado, Infraestrutura, Configuração, Diagnóstico. Há ainda um drawer de foco por aparelho, um detalhe de persona com 11 abas e popovers no topo (modelo de IA, saúde do ambiente, sessão).

Origem: avaliação de UX/UI feita em 30/09/2026 testando 1920, 1440, 1280, 1024, 768 e 390 px. Meta: o portal precisa parecer uma plataforma profissional.

Regras de trabalho para qualquer tarefa:
- Você NÃO conhece a base de código ainda. Comece descobrindo stack, estrutura de pastas, roteamento, gerenciamento de estado e como o front consome o backend. Não presuma nomes de arquivos.
- Faça mudanças pequenas e revisáveis. Não refatore o que não faz parte da tarefa.
- Não execute ações com efeito real no ambiente (Executar tarefa, Resetar dados, Instalar app, apagar personas) durante testes. Use dados de exemplo ou modo somente leitura.
- Todo texto visível ao usuário fica em português do Brasil, sem jargão técnico cru.
- Ao terminar, teste visualmente em 1440, 1024 e 390 px e reporte o que foi verificado e o que não foi.
- Se algo do briefing contradisser o código, siga o código e registre a divergência no relatório final.
