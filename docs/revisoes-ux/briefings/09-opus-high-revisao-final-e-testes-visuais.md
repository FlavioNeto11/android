---
modelo_recomendado: Opus
forca_recomendada: high
---

> **Rodar em:** Opus · esforço high

# Tarefa 09: revisão final, regressão e testes de responsividade

Execute **depois** das tarefas 01 a 08. Você é um revisor independente e exigente.

## O que fazer
1. Ler os diffs das tarefas anteriores e procurar: regressões de rota, estado perdido entre telas, duplicação de lógica, acessibilidade quebrada, textos em inglês, estilos conflitantes e código morto.
2. Rodar o portal e testar **8 telas x 6 larguras** (1920, 1440, 1280, 1024, 768, 390). Para cada combinação verificar: menu acessível, sem rolagem horizontal da página, sem sobreposição de elementos, barra de ação não cobre conteúdo, drawer funcional, textos sem truncamento indevido.
3. Verificar os fluxos: abrir persona por URL direta, Voltar do navegador, filtro persistente após recarregar, seleção de aparelhos e barra de seleção, drawer de foco, caixa de pendências, semáforo de saúde com servidor offline simulado.
4. Conferir a tabela de métricas da tarefa 02: o mesmo número em todas as telas.
5. Auditoria de acessibilidade (axe/Lighthouse) e navegação por teclado.
6. Testar sem executar ações com efeito real (Executar, Resetar dados, Instalar).

## Entregável
Relatório em `docs/revisao-final.md` com:
- Tabela **tela x largura** (OK / problema / gravidade) e capturas de tela dos problemas.
- Lista de regressões encontradas, com passos para reproduzir.
- Pendências que ficaram de fora e recomendação de próximos passos.
- Veredito: pronto para uso, pronto com ressalvas ou não pronto.

Não corrija nada sem antes reportar; abra correções pequenas separadamente e liste-as.

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
