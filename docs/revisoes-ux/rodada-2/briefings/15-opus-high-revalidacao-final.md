---
modelo_recomendado: Opus
forca_recomendada: high
---

> **Rodar em:** Opus · esforço high

# Tarefa 15: revalidação final após as correções 10 a 14

Execute **depois** das tarefas 10 a 14. Você é um revisor independente e exigente. Não corrija em silêncio: reporte primeiro.

## O que fazer
1. Ler os diffs de 10 a 14 e procurar regressões: rotas, estado perdido, duplicação de lógica, acessibilidade quebrada, textos em inglês, estilos conflitantes e código morto.
2. Rodar a matriz **9 telas x 6 larguras** (1920, 1440, 1280, 1024, 768, 390) medindo por script, em cada combinação:
   - rolagem horizontal da página (esperado: 0);
   - elementos vazando da tela (esperado: 0);
   - menu acessível (sidebar ou gaveta);
   - alvos de clique < 32 px (esperado: 0);
   - fonte mínima (esperado: 13 px);
   - falhas de contraste AA (esperado: 0);
   - elipse sem tooltip (esperado: 0).
3. Verificar os critérios específicos: cabeçalho <= 56 px em 390 px; detalhe da persona com identidade única e 5 seções; slug legível funcionando e IDs antigos compatíveis; foco voltando ao origem após fechar o drawer; sidebar expandida por padrão em >= 1280 px; resumo da execução; contador de pendências coerente com a lista.
4. Re-testar os fluxos: abrir persona por URL, Voltar do navegador, filtro persistente, seleção e barra, drawer, Pendências, semáforo com servidor degradado.
5. Rodar axe/Lighthouse nas 9 telas.
6. Testar sem executar ações com efeito real (Executar, Resetar dados, Instalar).

## Entregável
Relatório `docs/revalidacao-final.md` com tabela **tela x largura** (OK / problema / gravidade), capturas dos problemas, regressões com passos de reprodução, pendências restantes e veredito (pronto, pronto com ressalvas ou não pronto).

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
