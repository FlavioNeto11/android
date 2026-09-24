---
name: retomar
description: Recuperar o contexto deste projeto no início de uma sessão — o que mudou desde a última vez, o que falta no plano-100, próxima ação sugerida. Use quando a sessão começa neste repositório, ou quando o usuário pede "onde paramos", "retome" ou "qual o estado atual".
---

## Gatilho

Início de sessão neste repositório, ou pedido explícito de retomada/contexto.

## Entradas

Nenhuma obrigatória.

## Contexto mínimo (nesta ordem)

1. `git status` e `git log -5 --oneline` — o que mudou desde o último commit.
2. `git worktree list` — há outra frente de trabalho rodando em paralelo?
3. `docs/estado-atual.md` — handoff da última sessão.
4. `python scripts/claude-plan-100.py check` — o que falta no plano-100, por modelo. Sem chamar IA.
5. `docs/roadmap.md`, só a seção que `estado-atual.md` aponta como próxima — não o documento inteiro.

**Não ler:** `docs/plano-100.md` inteiro, `docs/auditoria-2026-09-21/`, transcrições `.jsonl`.

## Passos

1. Rodar os 5 itens do contexto mínimo.
2. Comparar o que `estado-atual.md` diz ser "próximo" com o que `git log` mostra que já aconteceu — divergência
   entre os dois é o achado mais comum entre sessões, e vale reportar mesmo sem ser perguntado.
3. Se `claude-plan-100.py check` falhar com "mapa não casa" (ex.: um item novo em `docs/plano-100.md` sem entrada
   em `.claude/plano-100.json`), tratar como pendência de manutenção do mapa, não como bug a investigar a fundo.

## Saídas

Um resumo de até 10 linhas: branch/estado do git, o que falta no plano-100 (contagem por modelo), divergências
encontradas entre `estado-atual.md` e a realidade, e a próxima ação proposta em uma frase.

## Critério de conclusão

O resumo foi apresentado ao usuário com a próxima ação proposta. Não é preciso agir sozinho — só orientar.
