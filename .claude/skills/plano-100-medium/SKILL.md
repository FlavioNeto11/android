---
name: plano-100-medium
description: Aposentada. O esforço agora vem do pacote de cada item; use a skill plano-100.
disable-model-invocation: true
---

Esta skill existia para fixar o esforço `medium` da sessão inteira. Não faz mais sentido: desde 22/09 o esforço e o
modelo são **por item**, calculados em `.claude/plano-100/pacotes/indice.json` e entregues na fila ao workflow.
Fixar um esforço para os 67 itens é a escolha que a mudança veio desfazer.

Use `/plano-100`. Ver `docs/claude-plano-100.md`.
