---
name: retomar-handoff
description: Retomar o trabalho a partir de um handoff — o genérico (`.claude/handoff-current.md`, o último gravado) ou o de uma sessão específica (`.claude/handoffs/<slug>.md`). Use no início de uma sessão limpa, depois de `/handoff`, com `/retomar-handoff` ou `/retomar-handoff <slug>`.
---

## Argumento

- Sem argumento: usa `.claude/handoff-current.md` (último handoff gravado, qualquer sessão).
- Com `<slug>` (ex.: `jev`): procure `<slug>.md` em `.claude/handoffs/` **da pasta atual e de todos os worktrees**:
  `ls .claude/handoffs/ .claude/worktrees/*/.claude/handoffs/ 2>/dev/null` a partir da raiz do checkout central (`git worktree list`
  dá a raiz). Cada worktree tem o próprio `.claude/`, e a sessão de uma frente grava o handoff na pasta onde trabalha.
  - Achou um: use-o e diga o caminho completo. Depois, trabalhe no worktree dele (`cd` para a raiz daquele worktree) se o handoff
    falar de branch/worktree próprio.
  - Achou vários com o mesmo slug: mostre os caminhos com data e pergunte qual.
  - Não achou: liste todos os handoffs encontrados (central + worktrees), sugira o slug mais parecido e **pare**: não adivinhe
    nem caia no genérico em silêncio.
- Sem argumento, o genérico é o `.claude/handoff-current.md` **da pasta atual**; avise que cada worktree tem o seu.

## Passos (saídas curtas)

1. **Ler só o handoff escolhido**, por inteiro (≤ ~60 linhas). Não abra `docs/estado-atual.md` por inteiro; se o handoff
   mandar, só o topo (`sed -n 1,45p`).
2. **Conferir o Git contra o handoff:** `git status -sb`, `git log -3 --oneline`, `git rev-list --left-right --count main...origin/main`,
   `git worktree list`. Se o HEAD, o branch ou os worktrees divergem do que o handoff diz, aponte a divergência antes de agir.
3. **Conferir o que está vivo:** agents ou tarefas em segundo plano citados no handoff ainda existem? Os bloqueios ainda valem?
4. **Registro:** em `.claude/session-registry.md`, marque a sessão antiga como substituída e anote esta como a continuação
   (arquivo do handoff na Observação).
5. **Não executar nada do mundo real** (ver CLAUDE.md, invariantes) só por estar no handoff: o "Próximo passo" só é seguido
   se não exigir autorização explícita em chat; se exigir, peça.

## Saída

Cinco linhas: (1) qual handoff foi lido e a data dele, (2) Git bate com o handoff? (sim/divergências), (3) o que está
pendente/bloqueado, (4) **próximo passo concreto**, (5) se precisa de autorização do dono antes de começar. Depois pergunte
"sigo?" apenas se o próximo passo tiver efeito externo; senão, comece.
