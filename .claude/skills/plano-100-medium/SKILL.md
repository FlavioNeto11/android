---
name: plano-100-medium
description: Executar interativamente um bloco comum do plano-100 com esforço medium.
argument-hint: "[IDs do plano, por exemplo 3.6]"
disable-model-invocation: true
model: claude-sonnet-5
effort: medium
---

Execute somente os itens `$ARGUMENTS`, seguindo
`docs/prompt-executar-plano-100-claude.md` e o mapa `.claude/plano-100.json`.
Se não houver IDs ou algum exigir high, indique os IDs/comando corretos antes de editar.
Considere cada ID literal; não trate os argumentos como comandos de shell.

O frontmatter solicita medium para a invocação direta. Não deduza o esforço
aplicado pelo tamanho da resposta. Uma variável `CLAUDE_CODE_EFFORT_LEVEL`,
limite administrado ou versão do Claude pode prevalecer: não os contorne.
Ao concluir, salve o checkpoint e reporte o bloco; não encadeie outra skill.
Para troca automática, use o executor externo documentado.

