# Estado atual — handoff

**Revisado em 24/09/2026.** Atualize este arquivo ao fechar cada tarefa (skill `fechar-tarefa`). Mantenha-o curto:
o que muda de sessão para sessão fica aqui, e o resto aponta para a fonte principal ([índice](README.md)).

## Onde estamos

- **Git.** A `main` foi publicada no `origin/main` e está limpa. A base de documentação é deste commit; o SHA exato
  sai de `git log -1`. Nenhuma branch tem trabalho fora da `main`: todas já estão integradas (`git branch --merged
  main`).
  - Os worktrees de agente desta sessão (`.claude/worktrees/agent-*`) foram integrados e podem ser removidos.
  - O worktree `.claude/worktrees/focused-chaum-ea5077` é de outra sessão, já está integrado e fica preservado.
- **Implantado** (conferido em `GET /api/health` do central em 24/09):
  - central no commit `f443a90`, migração `039_limites_por_servidor`, ator de IA no Ollama local
    (`qwen3-vl:4b-instruct-16k`);
  - worker `worker-lan-01` com agente em `0.1.0+c0c982d`.

  O que veio depois disso é só documentação, CI e scripts de manutenção, sem mudança de backend. Por isso não é
  preciso implantar.
- **Saúde naquele momento:** `degraded`. `android-01` responde ao ADB mas não está utilizável, e o gasto de IA do dia
  estava em US$ 8,34 de US$ 10,00.
- **Plano-100:**
  - 82 de 88 itens `implemented` (ver [`execucao-plano-100-runner.md`](execucao-plano-100-runner.md));
  - pendentes: 0.10, 7.4, 8.3, 8.4, 12.3 e T.2;
  - o que falta em cada um, separado por tipo, está em [`roadmap.md`](roadmap.md).

## Entregas recentes

- **24/09 (código, implantado):** a lista está no [`CHANGELOG.md`](../CHANGELOG.md#2026-09-24--custo-de-ia-painel-perfis-multi-app-treinamento-limites-por-servidor).
  - custo de IA: 7.5–7.8, e 7.1 ligado no Ollama;
  - painel: 11.1–11.9;
  - grupos de acesso: 11.10;
  - perfis multi-app: 12.1 e 12.2;
  - modo treinamento: 13.1–13.3;
  - limites por servidor: 10.5.
- **24/09 (documentação e processo, esta sessão):**
  - criados `CLAUDE.md`, o índice, `produto`, `arquitetura`, `dominios/*`, `ia`, `operacao`, `decisoes` (22 ADRs), o
    knowledge lake (23 aprendizados), `roadmap`, `CHANGELOG` e este handoff;
  - skills `retomar`, `preparar-tarefa` e `fechar-tarefa`, e as regras em `.claude/rules/`;
  - `scripts/docs-check.py` e o job `docs` no CI;
  - o mapa do plano-100 corrigido: o 10.5 estava fora e isso quebrava `check`/`relatorio`;
  - o relatório de execução regenerado;
  - o teste do livro-razão reescrito: o antigo testava o executor aposentado.

## Em curso

Nada. Nenhuma sessão deixou trabalho sem commit.

## Bloqueios e validações pendentes

- **Decisões do dono:**
  - 2: confirmar que a chave antiga foi revogada;
  - 7: autorizar gasto com a bateria de avaliação (o saldo da API está perto de US$ 3);
  - escolher o primeiro app do 12.3.

  As duas primeiras estão em [`decisoes.md`](decisoes.md) (ADR-017 e ADR-018); a do 12.3, em [`roadmap.md`](roadmap.md) §1.
- **Divergência a conferir:** decisão 6, relógio das duas máquinas ([ADR-019](decisoes.md)). Custa um comando
  `w32tm /stripchart` em cada máquina.
- **Provas reais que dependem de autorização:**
  - os nove aceites (T.1);
  - 1.3–1.8;
  - 6.x num remoto;
  - 8.1, 8.2 e 8.4;
  - 9.4.

  Os procedimentos estão prontos em [`relatorio-validacao.md`](relatorio-validacao.md) §13.1, e a lista completa em
  [`roadmap.md`](roadmap.md) §3.
- **Provas que dependem de infraestrutura que não existe:** uma segunda máquina para o aceite 5 (2.1, 4.2) e uma
  máquina Linux com KVM (10.4).

## Backlog encontrado pela documentação

São lacunas funcionais, **não implementadas** nesta sessão. Cada uma vira item do plano-100 ou tarefa própria,
por decisão do dono.

| # | Lacuna | Onde | Origem |
|---|---|---|---|
| B1 | O `/api/health` informa `ai.sends_data_externally: true` com o provedor local (Ollama), enquanto o aviso ao lado diz que os dados não saem da máquina | `backend/app/planning/routing.py` / `ProviderCfg.sends_data_externally` | leitura do health em 24/09 |
| B2 | O comentário de `workers/protocol.py` diz que `limits` sai logo depois do `welcome`; na verdade sai na primeira batida da conexão | `backend/app/workers/protocol.py:220` e `workers/registry.py` (`on_heartbeat`) | frente 1 |
| B3 | O painel aceita `boot_parallelism` até 10 (`ServerLimitsPatch`), mas a mensagem `Limits` aceita até 16 | `backend/app/models.py`, `backend/app/workers/protocol.py` | frente 1 |
| B4 | Depois de restaurar o banco, a cerca regride e o agente recusa `start`. O conserto sugerido é o `hello` do agente informar a maior cerca por aparelho | `backend/app/commands/store.py`, `worker/agent.py` | [K-004](conhecimento/aprendizados.md) |
| B5 | O `start` remoto `c-20260921172322-6f7fdc` está `uncertain` desde 21/09, sem reconciliação registrada | banco de produção; `commands/reconciler.py` | `relatorio-validacao.md` §13 |
| B6 | O vocabulário de prova: os registros escritos à mão usam `tests`/`unit`. Falta decidir entre registrar essas provas como `simulated` via `aplicar` ou estender `ESTADOS`/`PROVAS` junto com o enum de `plano-100.js` | `scripts/claude-plan-100.py:35`, `.claude/workflows/plano-100.js` | [`claude-plano-100.md`](claude-plano-100.md) |
| B7 | O CI não roda `npm run build`, e os testes de `scripts/tests` que usam pwsh só rodam localmente | `.github/workflows/ci.yml` | frente 2 |
| B8 | O app de QA embutido não foi migrado para o fluxo de release; `apps` e `app_releases` continuam como duas tabelas | plano-100 6.3 (bloqueio registrado) | frente 1 |
| B9 | Estado do worker e controle manual não são compartilhados entre backends | `backend/app/main.py` (achado #27) | `banco.md` |
| B10 | O `api-contract.md` tem dois adendos chamados "v0.9", e o `InstanceState` da base não lista `hibernated` | `docs/api-contract.md` (anotado no adendo v0.11) | frente 1 |
| B11 | Oito campos de voz das personas reais estão vazios | plano-100 8.1 | frente 2 |
| B12 | Sobras do executor antigo em `.claude/plano-100.json`: `model`, `prompt` e `batches[].effort`. Nenhum script as lê | `.claude/plano-100.json` | inventário |

## Próxima ação concreta

1. Rode a skill `retomar` para conferir que o git e este arquivo estão de acordo.
2. **Sem gasto e sem mundo real:** triagem do backlog com o dono.
   - B1 e B3 são pequenos, de risco baixo, e dão bons itens 10.x ou T.x. Para cada um: criar a linha no plano, pôr o
     ID num bloco do `plano-100.json`, rodar `plano-100-pacotes.py` e executar pela skill `plano-100`.
   - B6 é uma decisão do dono.
3. **Com autorização do dono:** o ensaio do aceite 6, derrubando o túnel no meio de um `start`. É o de menor risco
   entre os reais; o procedimento está em [`worker.md`](worker.md).
