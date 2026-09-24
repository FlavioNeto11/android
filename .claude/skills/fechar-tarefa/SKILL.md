---
name: fechar-tarefa
description: Fechar uma tarefa depois de implementada — validar, registrar prova real/simulada/não executada, atualizar docs e estado, rodar docs-check, commitar. Use quando o usuário diz que terminou uma mudança e quer encerrar ou commitar.
---

## Gatilho

Usuário pede para fechar/encerrar uma tarefa, ou para commitar o que foi feito.

## Entradas

Nenhuma obrigatória; se a tarefa é item do plano-100, o ID (para achar o pacote/linha correspondente).

## Contexto mínimo

`git diff`/`git status` (o que de fato mudou). Se for item do plano-100, o pacote ou a linha do plano para saber o
critério de aceite. **Não rodar a suíte completa** fora do passo 7 abaixo (~11 min; ver `.claude/rules/testes.md`).

## Passos

1. Rodar teste **direcionado** cobrindo a mudança — não a suíte inteira.
2. Registrar a prova separando real / simulado / não executado. Nunca inventar rodada nem prova que não aconteceu.
3. Atualizar o estado pelo mecanismo certo:
   - Item do plano-100: `python scripts/claude-plan-100.py aplicar <resultado.json>` — **nunca** editar
     `.claude/plano-100/estado.json` à mão.
   - Trabalho fora da esteira: descrever em `docs/estado-atual.md`, sem forçar no formato do plano-100.
4. Atualizar o doc principal do assunto; `docs/decisoes.md` se houve decisão nova; `docs/conhecimento/
   aprendizados.md` se apareceu uma armadilha (e marcar "superado" em qualquer registro antigo que este trabalho
   tenha tornado obsoleto — ver `docs/conhecimento/README.md`, seção Revisão); `CHANGELOG.md`.
5. Atualizar `docs/estado-atual.md` (handoff para a próxima sessão).
6. Rodar `python scripts/docs-check.py`; corrigir todo ERRO. AVISO pode ficar, mas relate ao usuário.
7. Se este commit fecha a rodada de trabalho: disparar a suíte completa em segundo plano (não esperar ociosamente —
   seguir com outra coisa enquanto ela roda), conforme `.claude/rules/testes.md`.
8. Commitar direto na `main` (preferência do dono — ADR-021 em `docs/decisoes.md`), mensagem convencional. A
   preferência registrada inclui dar push sem pedir confirmação — **exceto** quando a sessão roda isolada num
   worktree de integração (várias frentes em paralelo): nesse caso quem dá push é o coordenador, e a sessão avisa
   isso em vez de empurrar por conta própria.

## Saídas

Tarefa fechada: prova registrada, docs atualizados, `docs-check` sem ERRO (ou avisos explicados), commit criado.

## Critério de conclusão

Commit criado na `main` (ou no branch do worktree de integração) com mensagem convencional; `docs/estado-atual.md`
reflete o novo estado; nenhum ERRO pendente em `docs-check.py`.
