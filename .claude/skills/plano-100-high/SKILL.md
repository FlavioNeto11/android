---
name: plano-100-high
description: Executar interativamente um bloco crítico do plano-100 com esforço high.
argument-hint: "[IDs do plano, por exemplo 1.3 1.4]"
disable-model-invocation: true
model: claude-sonnet-5
effort: high
---

Execute somente os itens `$ARGUMENTS`, seguindo
`docs/prompt-executar-plano-100-claude.md` e o mapa `.claude/plano-100.json`.
Se não houver IDs ou houver ID desconhecido, peça o identificador antes de editar.
Considere cada ID literal; não trate os argumentos como comandos de shell.
Priorize invariantes e provas de falha para autenticação, concorrência,
fencing, migrações, reconciliação e exclusividade; não aumente o escopo.

O frontmatter solicita high para a invocação direta. Não deduza o esforço
aplicado pelo tamanho da resposta. Uma variável `CLAUDE_CODE_EFFORT_LEVEL`,
limite administrado ou versão do Claude pode prevalecer: não os contorne.
Ao concluir, salve o checkpoint e reporte o bloco; não encadeie outra skill.
Para troca automática, use o executor externo documentado.

