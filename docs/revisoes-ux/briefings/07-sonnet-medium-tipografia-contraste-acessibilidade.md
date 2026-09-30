---
modelo_recomendado: Sonnet
forca_recomendada: medium
---

> **Rodar em:** Sonnet · esforço medium

# Tarefa 07: tipografia, contraste e alvos de clique

## Problema (medido)
- Texto principal entre **11,5 e 12,5 px**; base do corpo 13,5 px; pouca hierarquia entre títulos e corpo.
- Textos secundários e selos em cinza sobre grafite com contraste aparentemente baixo (confirmar com ferramenta, pois há fundos translúcidos).
- **78 botões e links abaixo de 32 px** de altura ou largura na tela principal.
- Barra de status superior com muitos elementos de peso igual e moedas misturadas (US$ e R$ no popover da IA, com "estimado").
- Estados só por cor em alguns selos.

## O que fazer
1. Centralizar tipografia em **tokens** (escala: 12, 13, 14, 16, 20, 24 px; pesos 400, 500, 600). Mínimo de **13 px** para texto de interface e 14 px para corpo. Definir estilos de título de página, seção, rótulo, valor e legenda.
2. Auditar contraste com axe/Lighthouse e/ou script próprio. Meta **WCAG AA**: 4,5:1 para texto normal e 3:1 para texto grande e ícones. Ajustar tokens de cor de texto secundário, bordas e selos.
3. Alvos de clique com **mínimo de 32 px** (ideal 40 px em telas de toque); aumentar a área clicável sem aumentar o visual quando preciso.
4. Selos de estado com ícone e texto, além da cor.
5. Barra de status: agrupar (Saúde | Capacidade | Custos), reduzir peso visual, padronizar a moeda (US$) e indicar quando o dado é estimado por ícone com tooltip.
6. Foco de teclado visível em todos os controles; ordem de tabulação lógica; `prefers-reduced-motion` respeitado.
7. Largura máxima do conteúdo em telas muito largas (ex.: 1600 px) com centralização, sem prejudicar a grade de aparelhos.

## Critérios de aceite
- Nenhum texto de interface abaixo de 13 px (exceto rótulos de gráfico documentados).
- Relatório do axe/Lighthouse sem falhas de contraste AA nas 8 telas.
- Contagem de alvos abaixo de 32 px cai de 78 para zero na tela principal.
- Navegação completa por teclado nas telas Painel e Personas.

## Entregáveis
Tokens, diff de componentes, relatório antes/depois do auditor e lista do que ficou como exceção com justificativa.

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
