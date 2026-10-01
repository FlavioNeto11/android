---
modelo_recomendado: Opus
forca_recomendada: high
---

> **Rodar em:** Opus · esforço high

# Tarefa 05: busca, filtros, ordenação e visão em tabela

## Problema
Personas (14), Aparelhos (15) e Execuções (246 registros no histórico) são listas sem busca, sem filtros úteis e sem ordenação. Cards de persona têm alturas e campos inconsistentes: alguns com foto e outros com iniciais; "Ativa" convive com "app não instalado" em vermelho; "Bloqueada pela platafo…" trunca; "localidade não registrada" se sobrepõe ao texto vizinho. Ações confusas: "Marcar bloqueada" ao lado da lixeira com peso parecido; "Reativar" e "Abrir" misturados.

## O que fazer
1. Criar um **componente reutilizável de barra de listagem**: busca textual, filtros (chips ou dropdown), ordenação e alternância cards/tabela. Usar nas três telas.
2. **Personas**: busca por nome e handle; filtros por situação (ativa, bloqueada pela plataforma, sem conta de cadastro), por aparelho vinculado (sim/não), por grupo de acesso e por app; ordenação por nome, situação e última atividade. Visão tabela com colunas: persona, handle, contas, aparelho, situação, grupo, ações.
3. **Card de persona** com layout fixo: mesma altura, avatar com fallback de iniciais consistente, no máximo 2 selos de situação, texto truncado com tooltip com o conteúdo completo, sem sobreposição. Situações contraditórias devem virar um único estado composto e explicado (ex.: "Ativa · app não instalado" com ação "Instalar app").
4. **Ações**: "Abrir" é a ação primária; "Marcar bloqueada/Reativar" e "Excluir" vão para menu "⋯". Excluir sempre com confirmação e em vermelho só dentro do menu.
5. **Execuções**: busca por texto do objetivo, filtro por status (concluída, com pendência, falha, em andamento), por aparelho e por servidor, e período. Lista à esquerda com **título curto** (primeira linha significativa do objetivo ou título gerado), data e status, sem todos os itens começando com o mesmo prefixo.
6. Estado de busca, filtros e ordenação **na URL** (compatível com a tarefa 01, se já aplicada; se não, use parâmetros de query no hash).
7. Estados vazios úteis ("Nenhuma persona bloqueada. Limpar filtros").
8. Paginação ou virtualização para o histórico de execuções.

## Critérios de aceite
- Encontrar uma persona pelo handle em menos de 3 segundos.
- Filtros combinados funcionam e persistem ao recarregar.
- Cards de persona com altura uniforme, sem truncamento sem tooltip.
- Alternar cards/tabela não perde seleção.
- Testes para a lógica de filtro/ordenação.

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
