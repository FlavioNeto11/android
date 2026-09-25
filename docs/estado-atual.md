# Estado atual — handoff

**Revisado em 25/09/2026.** Atualize este arquivo ao fechar cada tarefa (skill `fechar-tarefa`). Mantenha-o curto:
o que muda de sessão para sessão fica aqui, e o resto aponta para a fonte principal ([índice](README.md)).

## Onde estamos

- **Git.** A `main` foi publicada no `origin/main` e está limpa. A base de documentação é deste commit; o SHA exato
  sai de `git log -1`. Nenhuma branch tem trabalho fora da `main`: todas já estão integradas (`git branch --merged
  main`).
  - Os três worktrees de agente desta sessão foram integrados e removidos.
  - O worktree `.claude/worktrees/focused-chaum-ea5077` é de outra sessão, já está integrado e fica preservado.
- **Implantado** (deploy de 25/09 ~01:20 UTC, conferido em `GET /api/health`, `/api/ai` e `/api/workers`):
  - central no commit `e6b00db`, migração `039_limites_por_servidor`, `cryptography` 50.0.0, ator de IA no Ollama
    local (`qwen3-vl:4b-instruct-16k`), porta 8010 escutando, 15 aparelhos;
  - worker `worker-lan-01` online com agente em `0.1.0+c0c982d`, marcado **`agent_outdated`** (esperado
    `0.1.0+e6b00db`). A diferença para ele é só o teto de `boot_parallelism` na mensagem `limits` (10.6), que o
    agente antigo já aceita porque o dele é maior; atualizar é opcional e mexe na máquina do worker (procedimento em
    `operacao.md` §9).
- **Saúde depois do deploy:** `ok`, sem problemas.
- **Plano-100:**
  - 86 de 91 itens `implemented` (ver [`execucao-plano-100-runner.md`](execucao-plano-100-runner.md));
  - pendentes: 7.4, 8.3, 8.4, 12.3 e T.2 (o 0.10 fechou em 24/09 com a decisão 2: sem revogação; 7.9, 10.6 e T.4 nasceram e fecharam
    em 24–25/09 a partir do backlog B1–B3 e B13);
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
  - 7: autorizar o gasto com a bateria de avaliação. A estimativa (~US$ 3–5,50 no recomendado) foi entregue em 24/09
    e está em [`roadmap.md`](roadmap.md) §1; exige recarregar o saldo da API (~US$ 3,30);
  - escolher o primeiro app do 12.3.

  A decisão 2 foi tomada em 24/09: **sem revogação da chave** ([ADR-017](decisoes.md)); o 0.10 fechou.
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
| B1 | **Corrigido em 24/09 (7.9), não implantado.** Não era dado errado: o agregado `true` está certo (plan, verify, escalation e social são externos). Era o aviso, que abria com a frase do ator local ("os dados NÃO saem") sem dizer de quem era | `backend/app/planning/routing.py` / `ProviderCfg.sends_data_externally` | leitura do health em 24/09 |
| B2 | **Corrigido em 24/09 (10.6).** O comentário de `workers/protocol.py` dizia que `limits` sai logo depois do `welcome`; sai na primeira batida da conexão | `backend/app/workers/protocol.py:220` e `workers/registry.py` (`on_heartbeat`) | frente 1 |
| B3 | **Corrigido em 24/09 (10.6), não implantado.** O painel aceitava `boot_parallelism` até 10 e a mensagem `Limits` até 16; agora os três tetos são 10, com teste de alinhamento | `backend/app/models.py`, `backend/app/workers/protocol.py` | frente 1 |
| B4 | Depois de restaurar o banco, a cerca regride e o agente recusa `start`. O conserto sugerido é o `hello` do agente informar a maior cerca por aparelho | `backend/app/commands/store.py`, `worker/agent.py` | [K-004](conhecimento/aprendizados.md) |
| B5 | O `start` remoto `c-20260921172322-6f7fdc` está `uncertain` desde 21/09, sem reconciliação registrada | banco de produção; `commands/reconciler.py` | `relatorio-validacao.md` §13 |
| B6 | O vocabulário de prova: os registros escritos à mão usam `tests`/`unit`. Falta decidir entre registrar essas provas como `simulated` via `aplicar` ou estender `ESTADOS`/`PROVAS` junto com o enum de `plano-100.js` | `scripts/claude-plan-100.py:35`, `.claude/workflows/plano-100.js` | [`claude-plano-100.md`](claude-plano-100.md) |
| B7 | O CI não roda `npm run build`, e os testes de `scripts/tests` que usam pwsh só rodam localmente | `.github/workflows/ci.yml` | frente 2 |
| B8 | O app de QA embutido não foi migrado para o fluxo de release; `apps` e `app_releases` continuam como duas tabelas | plano-100 6.3 (bloqueio registrado) | frente 1 |
| B9 | Estado do worker e controle manual não são compartilhados entre backends | `backend/app/main.py` (achado #27) | `banco.md` |
| B10 | O `api-contract.md` tem dois adendos chamados "v0.9", e o `InstanceState` da base não lista `hibernated` | `docs/api-contract.md` (anotado no adendo v0.11) | frente 1 |
| B11 | 8 dos 15 campos de voz das personas reais estão vazios (achado #107) | plano-100 8.1 | frente 2 |
| B12 | Sobras do executor antigo em `.claude/plano-100.json`: `model`, `prompt` e `batches[].effort`. Nenhum script as lê | `.claude/plano-100.json` | inventário |
| B13 | **Corrigido em 25/09 (T.4).** O CI estava vermelho desde pelo menos `bfffb0d`, por ambiente: cofre sem chave fora do Windows, scripts PowerShell do Windows no pwsh do Linux, `apksigner` novo (defeito real no inspetor), mock de frame com `Blob` do jsdom no Node 22, saúde dependente de SDK/KVM do host, `cryptography` 46.0.3. **Verde no run 36078946300 (`9e12baf`).** A produção ainda roda `cryptography` 46.0.3: ver próxima ação | `.github/workflows/ci.yml`, `backend/tests/`, `frontend/src/app.integration.test.tsx` | T.4 no livro-razão |

## Próxima ação concreta

1. Rode a skill `retomar` para conferir que o git e este arquivo estão de acordo.
2. **Agente do worker** (opcional, precisa de autorização: mexe na máquina do worker): atualizar para `e6b00db` e
   limpar o `agent_outdated`. Procedimento em `operacao.md` §9.
3. **Bateria de avaliação (decisão 7):** aguarda a autorização do dono sobre a estimativa (~US$ 3–5,50 recomendado),
   a recarga do saldo da API e o teto diário do dia; ver [`roadmap.md`](roadmap.md) §1.
4. **Sem gasto e sem mundo real:** B6 (vocabulário de prova) é decisão do dono; B4 (cerca após restauração) é o
   próximo item de código de risco baixo.
5. **Com autorização do dono:** o ensaio do aceite 6, derrubando o túnel no meio de um `start`. É o de menor risco
   entre os reais; o procedimento está em [`worker.md`](worker.md).
