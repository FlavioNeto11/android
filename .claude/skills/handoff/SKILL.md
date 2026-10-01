---
name: handoff
description: Preparar a troca para uma sessão limpa — verificar o Git e o trabalho em andamento, reescrever `.claude/handoff-current.md` (substituindo o estado obsoleto) e dizer se é seguro abrir outra sessão. Use quando o contexto passar de ~400k, ao fim de uma rodada, ou quando o usuário pedir handoff/troca de sessão.
---

## Gatilho

Contexto acima de ~400k, fim de rodada de trabalho, ou pedido explícito de handoff. Não abre sessão nem agent novo: só prepara.

## Passos (nesta ordem; saídas curtas)

1. **Git:** `git status -sb`, `git log -1 --oneline`, `git rev-list --left-right --count main...origin/main`, `git worktree list`.
   Só números e nomes; não despejar diff (`git diff --stat` se precisar).
2. **Trabalho em andamento:** há agent ou tarefa em segundo plano rodando? Há alteração não commitada? Qual é o próximo passo
   concreto e o que o bloqueia?
3. **Reescrever `.claude/handoff-current.md`** com as seções fixas (Objetivo, Git, Concluído, Decisões vigentes, Arquivos
   relevantes, Validação, Pendências, Bloqueios, Próximo passo, Worktrees/agentes). Se o arquivo não existir (clone novo), **crie-o** com essas seções. **Substituir** o estado antigo; remover o que
   ficou obsoleto; sem histórico, sem changelog. Máximo ~60 linhas. Estado durável longo vai para `docs/` e entra só como link.
4. **Registro:** se esta sessão vai ser substituída, atualizar a linha dela em `.claude/session-registry.md` (se não existir, criar com a tabela Sessão/Papel/Branch-worktree/Estado/Último contexto/Reativar?/Substituída por handoff?/Observação) (estado, último
   contexto conhecido, "não reativar").
5. **Memória:** só fato durável (preferência, decisão, armadilha) vai para a memória; hash, fila e "o que está rodando" ficam no handoff.
6. **Não commitar nem dar push** a menos que o usuário peça.

## Saída

Quatro linhas: (1) estado do Git, (2) o que mudou no handoff, (3) pendência principal, (4) **"Seguro abrir sessão limpa: sim/não"**
com o motivo se for não (alteração não commitada, agent ativo, worktree com trabalho aberto). Uma sessão nova começa lendo
apenas `.claude/handoff-current.md`.
