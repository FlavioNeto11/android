---
modelo_recomendado: Sonnet
forca_recomendada: medium
---

> **Rodar em:** Sonnet · esforço medium

# Tarefa 14: painel de execução e regra de contagem das pendências

## Problemas
1. No painel **Execução** (Painel e tela Execuções) o objetivo da tarefa fica truncado e o contador "1 de 1 com sucesso comprovado" aparece junto de "1 Sucesso" sem explicar a diferença.
2. O painel de detalhes tem 7 abas (Plano, Por aparelho, Textos, Linha do tempo, Evidências, Decisões, Relatório) e abre em "Por aparelho". Não há resumo no topo.
3. **Pendências está zerada** e a regra de contagem do menu não foi verificada: é preciso confirmar se itens de "Para aprovar" do Aprendizado, textos de persona aguardando aprovação, execuções bloqueadas e contas pedindo intervenção entram no contador.
4. Aprendizado ainda exibe aviso longo em laranja e termos internos ("rebaixar") que foram parcialmente tratados.

## O que fazer
1. **Resumo no topo da execução** com três linhas: o que foi pedido (objetivo completo expansível), resultado ("Concluída com sucesso em 2 min 3 s") e o que precisa da pessoa, se houver. Tooltip ou legenda para "sucesso comprovado" (ex.: "confirmado por evidência na tela, não só pela resposta do agente").
2. Abrir o detalhe na aba **Relatório** (ou Resumo) por padrão quando a execução estiver concluída, e em **Linha do tempo** quando estiver em andamento.
3. **Contagem de pendências:** localizar onde o contador é calculado, documentar a regra em comentário e garantir que ela some as quatro origens (aprendizado, persona, execução, intervenção). Criar teste unitário com dados de exemplo em cada origem e com todas vazias.
4. Se existirem itens em "Para aprovar" no Aprendizado que não aparecem em Pendências, alinhar para que ambos mostrem os mesmos itens (Pendências é a caixa única).
5. Encurtar o aviso laranja do Aprendizado para uma linha com "saiba mais" e substituir "rebaixar" por "Voltar a pedir aprovação" (ou explicar em tooltip).

## Critérios de aceite
- Objetivo completo acessível sem truncamento; "sucesso comprovado" explicado.
- Execução concluída abre no resumo/relatório; em andamento, na linha do tempo.
- Teste unitário cobre a regra do contador (4 origens + vazio) e passa.
- Aprendizado e Pendências mostram a mesma lista de aprovações.
- Texto em português do Brasil, sem jargão.

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
