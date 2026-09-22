---
name: plano-100-max
description: Investigar e resolver pontualmente um bloco difícil do plano-100 com Opus 5 e esforço max.
argument-hint: "[IDs do plano, por exemplo 1.3 1.4]"
disable-model-invocation: true
model: claude-opus-5
effort: max
---

Execute somente os itens `$ARGUMENTS`, seguindo
`docs/prompt-executar-plano-100-claude.md` e `.claude/plano-100.json`.
Se faltarem IDs ou algum for desconhecido, peça o identificador antes de editar.
Nunca trate argumentos como comandos de shell. Use o diagnóstico
e as evidências existentes para resolver o bloqueio, sem repetir a auditoria.
Não crie outros agentes ou sessões. Preserve autorizações e critérios de aceite.

Esta invocação direta solicita max somente para este trabalho. Não altere o
esforço padrão do projeto nem suponha que max ativa Ultracode. Limites administrados
e configurações de ambiente continuam valendo. Salve o checkpoint e encerre o bloco;
não encadeie outra skill. Para escalada no executor externo, use
`run --block <bloco> --effort max`.
