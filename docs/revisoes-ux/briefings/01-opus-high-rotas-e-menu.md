---
modelo_recomendado: Opus
forca_recomendada: high (use xhigh só para planejar, se a base for grande)
---

> **Rodar em:** Opus · esforço high (use xhigh só para planejar, se a base for grande)

# Tarefa 01: menu de navegação e rotas por objeto

## Problema
1. O menu superior corta abas sem indicar rolagem. Em 1440 px "Infraestrutura" aparece cortada e "Configuração" e "Diagnóstico" ficam invisíveis (conteúdo do nav 744 px em 701 px). Em 1024 px "Infraestrutura" também é cortada. Em 390 px somem 5 das 8 abas.
2. Abrir uma persona, um aparelho (drawer de foco), uma execução ou as abas internas não altera a URL. O hash continua `#/perfis`. O botão Voltar do navegador sai da tela e o link não pode ser compartilhado.
3. Nomenclatura: o menu diz "Personas", a rota diz `perfis` e há "perfil" em outros pontos.

## O que fazer
- Substituir o menu horizontal por **sidebar lateral recolhível** (ícone + rótulo, recolhe para só ícone) em telas largas e por menu em gaveta (hambúrguer) abaixo de ~1024 px. Alternativa aceitável se a sidebar for inviável: itens excedentes em menu "Mais ▾". Decida, justifique em 3 linhas e implemente.
- Todas as 8 telas devem ficar acessíveis de 390 a 1920 px sem rolagem horizontal escondida.
- Criar rotas por objeto e por aba, por exemplo: `#/personas/<id>`, `#/personas/<id>/memoria`, `#/execucoes/<id>?aba=linha-do-tempo`, `#/painel?foco=android-01`, `#/aplicativos/<pacote>`.
- Estado relevante (aba ativa, aparelho em foco, filtros já existentes) deve viver na URL. Recarregar a página reabre a mesma visão.
- Voltar/Avançar do navegador funcionam; fechar o drawer volta a URL anterior sem empilhar histórico inútil.
- Padronizar o termo: usar **Personas** no menu, na rota e nos textos. Manter redirecionamento de `#/perfis` para `#/personas` para não quebrar links antigos.
- Manter foco de teclado visível e `aria-current` no item ativo.

## Critérios de aceite
- Em 390, 768, 1024, 1440 e 1920 px as 8 telas são alcançáveis pelo menu.
- Abrir persona, aparelho e execução muda a URL; colar essa URL em nova aba abre o mesmo objeto.
- Voltar do navegador fecha o detalhe e retorna à lista anterior.
- `#/perfis` redireciona para `#/personas`.
- Nenhuma regressão nas rotas existentes.

## Entregáveis
Código, resumo das decisões (sidebar vs "Mais", esquema de rotas) e lista de arquivos alterados.

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
