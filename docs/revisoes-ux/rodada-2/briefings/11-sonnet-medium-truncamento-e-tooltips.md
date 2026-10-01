---
modelo_recomendado: Sonnet
forca_recomendada: medium
---

> **Rodar em:** Sonnet · esforço medium

# Tarefa 11: texto truncado sem tooltip

## Problema
Textos cortados com reticências (`text-overflow: ellipsis`) sem `title` ou `aria-label`, impedindo ler o dado completo:
- **Infraestrutura em 390 px: 15 casos** e em 768 px: 6 casos. Exemplo repetido nas linhas de aparelho: "· API 34 · x86_64 · Play Services · renderiza…".
- Casos isolados em Aplicativos (1280, 1024 px) e em Execuções (1280, 1024, 768 px), além do painel de execução no Painel, onde o objetivo aparece cortado ("Guarde o nome do perfil qu…").

## O que fazer
1. Fazer um levantamento por script (percorrer o DOM em 390, 768 e 1280 px e listar elementos com `scrollWidth > clientWidth` e `text-overflow: ellipsis`) e listar os componentes responsáveis.
2. Corrigir **na origem**, com critério por tipo de conteúdo:
   - Metadados técnicos longos (API, arquitetura, imagem): em telas estreitas **quebrar em linhas** ou virar duas linhas legíveis em vez de cortar.
   - Títulos e objetivos (execução, apps): permitir até 2 linhas (`line-clamp`) e oferecer "ver mais" ou tooltip com o texto completo.
   - Quando o truncamento for inevitável, adicionar `title` com o texto integral (e `aria-label` se o texto cortado for o único rótulo).
3. Criar um pequeno componente ou utilitário reutilizável (ex.: `TruncatedText`) que aplica `title` automaticamente quando o texto transborda, e usar nos pontos encontrados.
4. No painel de execução do Painel, mostrar o objetivo completo em um bloco expansível ("Ver objetivo completo").

## Critérios de aceite
- O mesmo script de levantamento retorna **0 casos** de elipse sem tooltip em 390, 768 e 1280 px nas 9 telas.
- Nenhum dado importante da Infraestrutura fica ilegível em 390 px.
- Sem rolagem horizontal nova.

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
