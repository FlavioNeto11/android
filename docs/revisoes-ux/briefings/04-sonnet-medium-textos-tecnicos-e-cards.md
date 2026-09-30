---
modelo_recomendado: Sonnet
forca_recomendada: medium
---

> **Rodar em:** Sonnet · esforço medium

# Tarefa 04: textos técnicos e ruído dos cards de aparelho

## Problema
Cada card de aparelho repete "Sem tarefa em andamento", "Controle: —" e "Desconhecido" mesmo sem informação. Há texto técnico cru: `app.distribute`, `seletor id=com.pocqa.messenger:id/account_label|text=qa-user-10: 1 elemento(s)`, "observado", "há 161 h". A grade tem 15 cards altos, a maioria "Emulador desligado".

## O que fazer
1. **Mapa de tradução**: criar um único módulo/arquivo de rótulos que converte identificadores técnicos em português claro (ex.: `app.distribute` -> "Distribuição de app"; `device.network` -> "Rede do aparelho"; "Desconhecido há 161 h" -> "Sem resposta há 6 dias"). Cobrir todos os comandos que aparecem hoje em cards, logs e execuções. O identificador original fica acessível em "ver detalhes" ou tooltip.
2. **Tempo relativo humano**: "há 161 h" -> "há 6 dias"; "há 1 min 14 s" -> "há 1 min".
3. **Ocultar o que está vazio**: não renderizar "Sem tarefa em andamento" nem "Controle: —" quando não há dado; mostrar só se houver tarefa ou controle ativo.
4. **Card de aparelho parado** em versão compacta (cabeçalho + estado + botão Iniciar), sem o bloco grande de "Emulador desligado". Agrupar por servidor e permitir recolher grupo "Paradas".
5. Acrescentar alternância **Cards / Lista** (visão compacta em linhas) na seção Aparelhos, lembrando a escolha em localStorage.
6. Padronizar os selos de estado (Online, Parada, Desatualizado, Desconhecido) com ícone + texto, sem depender só de cor.

## Critérios de aceite
- Nenhum identificador técnico cru aparece por padrão em cards, listas de execuções e logs visíveis.
- Aparelho parado ocupa no máximo metade da altura atual.
- A visão em lista mostra os 15 aparelhos sem rolar mais de uma tela em 1440x900.
- Todo texto novo está em português do Brasil.

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
