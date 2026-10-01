---
modelo_recomendado: Sonnet
forca_recomendada: medium
---

> **Rodar em:** Sonnet · esforço medium

# Tarefa 10: cabeçalho compacto no celular e tablet

## Problema
Em 390 px o cabeçalho (chip "Ambiente em atenção", métricas, custos Anthropic/OpenAI e ícone de aviso) empilha em várias linhas e ocupa cerca de **29% da altura da tela** antes do conteúdo da página. Em 768 px também consome espaço demais. Os controles de custo são pills de peso visual igual ao do estado de saúde.

## O que fazer
1. Abaixo de 768 px, o cabeçalho mostra **uma única linha**: hambúrguer, logo/título curto, chip de saúde (com contador) e botão "Resumo".
2. "Resumo" abre um painel (sheet ou popover) com as métricas hoje espalhadas: capacidade (online/total), execuções, aguardando você, CPU, RAM e custos. Reaproveite os componentes já existentes do popover de saúde; não duplique cálculo.
3. Entre 768 e 1024 px, agrupe métricas secundárias (CPU, RAM, custos) em um único chip "Recursos" com popover.
4. A área de métricas continua visível por completo a partir de 1024 px, como hoje.
5. O chip de saúde mantém cor e texto ("Ambiente OK" / "em atenção" / "crítico"), e o contador de pendências do menu continua visível em todas as larguras.
6. O cabeçalho não deve ser `sticky` com mais de 56 px de altura no celular.

## Critérios de aceite
- Em 390 px o cabeçalho tem no máximo **56 px** de altura (hoje ~245 px) e a primeira tela de Painel, Personas e Execuções mostra conteúdo útil acima da dobra.
- Todas as informações antes visíveis continuam acessíveis em até 1 toque.
- Alvos de clique do cabeçalho com pelo menos 40 px de altura em telas de toque.
- Sem regressão em 1024 px e acima.
- Teste visual em 390, 768, 1024 e 1440 px.

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
