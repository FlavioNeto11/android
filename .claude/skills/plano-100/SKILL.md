---
name: plano-100
description: Executar itens de docs/plano-100.md pela sessão da IDE, com pacote por item, modelo por item e conferência.
disable-model-invocation: true
---

Leia `docs/claude-plano-100.md` se ainda não estiver no contexto.

Com um bloco ou item pedido pelo usuário (ex.: `0-contratos`, ou `1.3`):

1. `python scripts/plano-100-pacotes.py --fila --bloco <bloco>` — gera os pacotes e a fila. Sem IA.
2. `Workflow({scriptPath: ".claude/workflows/plano-100.js", args: {bloco, fila}})` — um agente por grupo, com o
   modelo e o esforço que vêm da fila. Não invente modelo nem esforço: eles saem do pacote.
3. Salve o retorno num JSON e rode `python scripts/claude-plan-100.py aplicar <arquivo>`.
4. Olhe o `git diff` — principalmente o que a conferência questionou — antes de commitar. **Quem commita é você,
   não o agente.**

Sem bloco indicado, rode `python scripts/claude-plan-100.py check` e ofereça o próximo pendente na ordem do plano.

Não rode `python scripts/claude-plan-100.py run`: o transporte antigo (subprocesso `claude -p`) não existe aqui, e
o comando só explica isso. Não abra sessão Claude externa nem remova a proteção contra sessão aninhada.
