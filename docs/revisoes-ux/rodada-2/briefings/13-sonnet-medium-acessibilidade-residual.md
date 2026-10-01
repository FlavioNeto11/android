---
modelo_recomendado: Sonnet
forca_recomendada: medium
---

> **Rodar em:** Sonnet · esforço medium

# Tarefa 13: acessibilidade residual

## Problemas medidos
1. **Alvos de clique menores que 32 px:** 5 no Painel e 6 na Infraestrutura (identifique quais por script: `getBoundingClientRect` com altura ou largura < 32).
2. **Texto de 12 px no Diagnóstico** (as demais telas têm mínimo de 13 px).
3. **Sidebar recolhida com `font-size: 0`** nos rótulos abaixo de 1024 px. Verificar se leitores de tela ainda leem o rótulo (o texto está no DOM e há `title`). Se a leitura depender só do `title`, adicionar `aria-label` explícito.
4. **Foco ao fechar o drawer de foco:** ao fechar (Esc ou botão Fechar) o foco não volta ao card ou à linha de origem. Deve voltar ao elemento que abriu o drawer.
5. **Sidebar só com ícones por padrão**, mesmo em telas largas. Rótulos só aparecem em tooltip.

## O que fazer
1. Aumentar a área clicável dos controles pequenos para **no mínimo 32 px** (ideal 40 px em toque), usando padding ou pseudo-elemento sem alterar o visual.
2. Elevar o texto de 12 px do Diagnóstico para 13 px, usando os tokens de tipografia existentes.
3. Garantir `aria-label` em cada item da sidebar e `aria-current="page"` no ativo.
4. Implementar retorno de foco ao fechar o drawer: guardar o elemento ativo antes de abrir e restaurá-lo ao fechar; se o elemento não existir mais, focar o contêiner da lista.
5. Em telas ≥ 1280 px, abrir a sidebar **expandida por padrão** (ícone + rótulo), com botão de recolher e preferência guardada em `localStorage`. Abaixo de 1280 px, recolhida.
6. Verificar ordem de tabulação e foco visível na sidebar, na barra de seleção e no drawer.

## Critérios de aceite
- Script de medição: 0 alvos < 32 px no Painel e na Infraestrutura; mínimo de fonte 13 px nas 9 telas.
- axe/Lighthouse sem erros nas telas Painel, Personas, Infraestrutura e Diagnóstico.
- Teste manual de teclado: abrir drawer com Enter, fechar com Esc e conferir que o foco volta ao ponto de origem.
- Sidebar expandida em 1440 px na primeira visita; preferência persistida.

## Contexto comum (leia antes de agir)

Projeto: **Central de Aparelhos**, portal web (SPA com rotas por hash) que controla emuladores Android e personas de IA, em português do Brasil, tema escuro, servido em `http://127.0.0.1:8000/`.

Telas atuais: Painel, Personas, Aplicativos, Execuções, Pendências, Aprendizado, Infraestrutura, Configuração, Diagnóstico. Há drawer de foco por aparelho e detalhe de persona com abas.

**Estado atual (já entregue nas rodadas anteriores, não refazer):** sidebar de navegação (ícones por padrão, tooltip `title`; gaveta com hambúrguer abaixo de 768 px), rotas por objeto (`#/personas/<id>`, `…/memoria`, `#/painel?foco=…&visao=lista`, `?q=`), redirecionamento `#/perfis` -> `#/personas`, semáforo "Ambiente em atenção" com motivos e links, números consistentes entre telas, barra de seleção fixa, drawer sobreposto (`role=dialog`, `aria-modal`, Esc fecha), tela Pendências, busca e filtros em Personas e Execuções, textos técnicos traduzidos, fonte mínima 13 px, 0 falhas de contraste AA.

**Medições da reavaliação de 01/10/2026** (use como linha de base e como critério de aceite):
- Sem rolagem horizontal da página em 1920, 1280, 1024, 768 e 390 px, nas 9 telas.
- Alvos de clique abaixo de 32 px: 5 no Painel, 6 na Infraestrutura, 0 nas demais.
- Texto de 12 px no Diagnóstico (demais telas: mínimo 13 px).
- Em 390 px, o cabeçalho ocupa cerca de 29% da altura da tela (saúde + métricas + custos empilhados).
- Textos truncados com reticências e sem tooltip: Infraestrutura 15 casos em 390 px e 6 em 768 px; casos isolados em Aplicativos e Execuções.
- Detalhe da persona: identidade repetida 3 vezes e 11 abas.

Regras de trabalho para qualquer tarefa:
- Você NÃO conhece a base de código. Comece descobrindo stack, estrutura, roteamento, estado e componentes compartilhados. Não presuma nomes de arquivos.
- Mudanças pequenas e revisáveis; não refatore o que não faz parte da tarefa.
- Não execute ações com efeito real (Executar tarefa, Resetar dados, Instalar app, apagar personas) em testes. Use dados de exemplo ou somente leitura.
- Todo texto visível em português do Brasil, sem jargão técnico cru.
- Ao terminar, teste em 1440, 1024, 768 e 390 px, rode um auditor de acessibilidade (axe ou Lighthouse) nas telas afetadas e reporte o que foi e o que não foi verificado.
- Se algo do briefing contradisser o código, siga o código e registre a divergência no relatório final.
