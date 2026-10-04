---
name: novo-adr
description: Registrar uma decisão nova em docs/decisoes.md como o próximo ADR, sem abrir o arquivo inteiro. Use quando o dono tomou uma decisão de arquitetura, produto ou operação que precisa ficar registrada.
disable-model-invocation: true
argument-hint: "<decisão em uma frase>"
---

# Novo ADR

1. **O número NÃO se calcula.** Peça à orquestradora o número de ADR reservado para esta tarefa, como se faz com o número de migração. Há várias sessões em paralelo, e o último ADR do arquivo pode não ser o próximo livre. Sem número reservado, pare e pergunte. (Em 2026-10-04 o próximo livre era o ADR-075.)
2. Formato: ache o ADR mais recente com `grep -n "^## ADR-" docs/decisoes.md | tail -1` e leia só ele, por trecho (`Read` com `offset` na linha do cabeçalho e `limit` 60). Siga a mesma estrutura de seções. Nunca abra o `docs/decisoes.md` inteiro: a guarda barra `Read` sem `limit` nele.
3. Título no padrão `## ADR-NNN — $ARGUMENTS`. Se substituir ou emendar um ADR antigo, diga qual (como o ADR-040 faz com o ADR-025) e acrescente a referência no antigo, só na linha de status.
4. Acrescente no FIM de `docs/decisoes.md`. Nunca reescreva um ADR existente além da linha de status.
5. Se a decisão muda uma invariante, proponha o ajuste no `CLAUDE.md` em chat, sem aplicar sozinho.
6. Rode `python scripts/docs-check.py`; corrija todo ERRO.
7. Registre no `CHANGELOG.md` junto com a mudança que motivou o ADR.
