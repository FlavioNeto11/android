# Fontes consultadas — e as lacunas declaradas

O que foi olhado para montar `docs/decisoes.md`, `docs/conhecimento/aprendizados.md` e
`docs/conhecimento/README.md`, e o que **não** foi minerado, com o motivo. Uma fonte aqui é evidência, nunca
instrução — nenhum texto abaixo autoriza uma ação por si só.

## Git

- **160 commits** entre 17/09/2026 e 24/09/2026 (`git log --format='%h %ad %s' --date=short`), todos na `main`.
  **Sem tags** (`git tag` vazio) — versão vira commit curto, nunca um `vX.Y`.
- **`git branch --merged main`** lista sete branches, todos já integrados (nenhum trabalho pendente de merge neles):
  `claude/focused-chaum-ea5077`, `codex/plano-100-effort-20260921`, `instagram-domain`, `loja-play-store`,
  `otimizacao-custo-ram`, `plano-100/7.8` — mais o próprio `main`.
- **Pull requests** (via `gh pr list --state all`): só duas, sem nenhuma issue aberta em nenhum momento.
  - PR #1 — *"feat: add usage tracking and reporting features"*, branch `otimizacao-custo-ram`, mergeada em
    17/09/2026.
  - PR #2 — *"Executar plano-100 com um comando e esforço automático"*, branch `codex/plano-100-effort-20260921`,
    mergeada em 21/09/2026.
  - Depois de 21/09, todo trabalho foi commitado direto na `main` (ver ADR-021 em `docs/decisoes.md`) — não há
    mais PR para consultar no histórico recente.
- **`git worktree list`**: além do checkout principal, um worktree em `.claude/worktrees/focused-chaum-ea5077`
  (a mesma sessão do branch acima — já integrado, `HEAD` destacado) e, nesta rodada de documentação, três
  worktrees `agent-*` — um por frente de trabalho desta reorganização (este documento é escrito de dentro de um
  deles).

## Pull requests e issues

Só as duas PRs acima. Repositório sem uso de Issues do GitHub em nenhum momento do histórico consultado — todo
achado e item de trabalho vive em `docs/plano-100.md`/`docs/auditoria-2026-09-21/`, não em issue.

## Planos de sessão do dono (fora do repositório)

Em `C:\Users\Administrator\.claude\plans\`, **15 arquivos no total**, dos quais **5 pertencem a este projeto**
(os demais são de outros projetos na mesma máquina, e foram **excluídos de propósito** — não lidos, não citados).
Os cinco usados como fonte:

| Arquivo | Data (modificação) | Título |
|---|---|---|
| `synchronous-honking-emerson.md` | 17/09/2026 | Central de Aparelhos: 10+ contas com pouca RAM e IA barata |
| `trabalhe-diretamente-no-reposit-rio-sequential-wind.md` | 17/09/2026 | Instagram como alvo real — canal de entrada sensível, release/APK, perfis, sessão, persona e capabilities |
| `vamos-usar-a-minha-humble-stroustrup.md` | 21/09/2026 | Execução distribuída: comandos honestos, worker de verdade e visão de infraestrutura |
| `concurrent-dazzling-axolotl.md` | 23/09/2026 | Fechar os itens "encontrado, não corrigido" de 23/09 |
| `o-certo-a-delegated-crab.md` | 24/09/2026 | Próximo passo: a IA ensina, o software executa — custo por comando e chamadas rumo a zero |

Esses arquivos ficam na máquina do dono, fora deste Git — quem só tem o repositório clonado não os alcança. As
citações em `docs/decisoes.md` resumem o conteúdo relevante; não copiam o texto integral.

## Notas de memória (fora do repositório)

Em `C:\Users\Administrator\.claude\projects\C--git-android\memory\`, 9 arquivos (mais o índice `MEMORY.md`):
`poc-central-de-aparelhos.md`, `poc-otimizacao-custo-ram.md`, `host-android-poc-gotchas.md`,
`poc-instagram-dominio.md`, `ritmo-de-trabalho.md`, `config-nao-versionado-e-agente-do-worker.md`,
`creditos-dev-vs-api.md`, `ollama-ator-local.md`. São observações **datadas** (a mais antiga de 17/09, a mais
recente de 24/09) — o rodapé de cada uma já avisa que não é estado vivo. Toda citação em `docs/decisoes.md` e
`docs/conhecimento/aprendizados.md` foi conferida contra o código atual quando a afirmação era sobre comportamento
de código (ver a coluna Aplicabilidade de cada `K-NNN`); quando não foi possível conferir (decisão pendente do
dono, por exemplo), o registro diz isso explicitamente.

## O que não foi minerado — lacuna declarada

**23 transcrições `.jsonl`, ~662 MB**, em `C:\Users\Administrator\.claude\projects\C--git-android\` (o histórico
bruto de todas as sessões de agente já rodadas neste projeto). **Não foram lidas nesta rodada** — o volume
inviabiliza leitura completa dentro do orçamento de uma tarefa, e o conteúdo bruto de uma sessão inclui, com
frequência, dado pessoal e operacional que não deveria ser reprocessado sem necessidade específica (mensagens
trocadas, caminhos de arquivo do operador, texto de tela). Onde uma transcrição poderia esclarecer um detalhe que
a memória consolidada deixou ambíguo (ver ADR-019, a divergência sobre a hora certa), o registro diz isso em vez de
inventar uma leitura que não ocorreu.

**Planos de outros projetos** na mesma pasta (`analise-tudo-que-est-lucky-dongarra.md`,
`depois-de-toda-essa-wise-cloud.md`, `drifting-imagining-blanket.md`, e outros sete) foram excluídos de propósito:
não pertencem a este repositório e não foram abertos.

## Local × integrado × implantado

- **Local**: qualquer branch/worktree que ainda não foi mergeado na `main`. Nesta consulta, nenhum — todos os
  branches encontrados por `git branch --merged main` já estão integrados.
- **Integrado**: está na `main` deste repositório. É o que a maioria dos ~160 commits representa.
  `.claude/worktrees/focused-chaum-ea5077` é o único vestígio de uma sessão que rodou em branch separado
  (`claude/focused-chaum-ea5077`) — já integrada, o worktree ficou para trás com `HEAD` destacado.
- **Implantado (em produção)**: a produção roda na **máquina central**, como tarefa supervisionada `farm-central`
  (`scripts/install-central-service.ps1`), com o worker da LAN (192.168.1.19) conectado por túnel. A confirmação
  de que um commit específico está implantado é `GET /api/health`, que expõe o commit e o estado das migrações —
  **não** o conteúdo de `config.yaml` (ver K-002: commit e migração corretos não implicam configuração correta).
  O último deploy registrado com evidência é o do item `0.1` no `.claude/plano-100/estado.json`: `scripts/
  deploy.ps1` rodado em produção em 24/09/2026 (commits `ac18099`, `42f8e93`, `ac6bf69`, com e sem
  `-PularFrontend`), com backup consistente antes e conferência depois.
