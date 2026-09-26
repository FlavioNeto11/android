# Estado atual — handoff

**Revisado em 26/09/2026, depois do deploy de `3da3bb5` (PRs #8, #9 e #10).** Atualize este arquivo ao fechar cada tarefa (skill `fechar-tarefa`). Mantenha-o curto:
o que muda de sessão para sessão fica aqui, e o resto aponta para a fonte principal ([índice](README.md)).

## Onde estamos

- **Git.** A `main` foi publicada no `origin/main`; o SHA exato sai de `git log -1`. Fora da `main`, em PR:
  - PR #11 `claude/rekey-ordem-deterministica` (cofre: relatório da recifragem em ordem determinística, K-030);
  - `claude/prontidao-sem-efeito-atrasado` (efeitos tardios não idempotentes fora do portão de prontidão e os 8
    achados da revisão pós-merge do PR #7), em andamento.
  - O worktree `.claude/worktrees/focused-chaum-ea5077` é de outra sessão, já está integrado e fica preservado.
- **Implantado em 26/09 ~18:55 UTC (`3da3bb5`: PR #9 credenciais, PR #10 loja de apps, PR #8 CI):** central em
  `3da3bb5`, migrações 040 e 041 (ensaiadas antes numa cópia do banco real), health `ok`, `problems: []`, porta 8010
  escutando, `config.yaml` intacto, android-01/04/06 readotados `ready`, worker de volta em ~10 s (agente segue em
  `0.1.0+5b81c1a`; atualiza junto com a prontidão, que muda o `worker/executor.py`). Prova `real`, sem IA e sem
  conta: `POST /api/runs` com credencial sem consentimento → 409 `consentimento_de_credencial`; senha no texto →
  409 `credencial_no_comando`; nenhuma execução criada, cofre com os mesmos 8 segredos, o valor não voltou nem foi
  gravado. Loja: `GET /api/app-store` com categorias, `POST /api/proxies/apply` sem alvo → 400 `target_required`,
  prévias sem gravar, e uma distribuição real (QA no android-04) → `already`. Chrome cadastrado em `apps`
  (`chrome`, `utilitario`). O login real num site com credencial fica `not_run`: é disparado pelo dono, pelo
  painel, com a URL no comando.
- **Implantado em 26/09 ~17:30 UTC (PR #7, prontidão por subsistema):** central e agente do worker em `5b81c1a`
  (`0.1.0+5b81c1a`), migração 039, health `ok`. Prova `real`: readoção de android-01/06 pela escada nova; android-04
  (1470 MB, sob pressão) teve o preparo estourado → `booting`, e voltou 12 s depois no mesmo PID (a limitação
  entre tentativas, documentada em `devices/prontidao.py`); um cold start do android-09 (`from_snapshot:false`)
  fechou `succeeded` → `online`, internet `healthy`, stream `live`, e depois `stop` → `succeeded`. Hibernação do
  worker segue desligada.
  - Follow-ups abertos: tirar do caminho de prontidão os efeitos tardios não idempotentes (`input tap` do diálogo,
    `cmd alarm set-time`); `_set_state(online)` sobrescreve `readiness.detail` com o texto antigo do PR #5; nenhum log
    do sucesso por degrau; PostgreSQL com a falha preexistente `test_estimativa_de_custo_por_fluxo` e um flake de
    ordenação em `test_secret_store`.
- **Deploy anterior** (25/09 ~14:19 UTC, conferido em `GET /api/health`, `/api/ai`, `/api/workers` e no painel
  pelo Chrome):
  - central no commit `8169fd3`, migração `039_limites_por_servidor`, `cryptography` 50.0.0, **ator de IA no
    Sonnet 5** (ADR-023; o Ollama saiu do caminho principal), porta 8010 escutando, 15 aparelhos;
  - worker `worker-lan-01` online com agente em `0.1.0+c0c982d`, marcado **`agent_outdated`** (esperado
    `0.1.0+8169fd3`). A diferença para ele é só o teto de `boot_parallelism` na mensagem `limits` (10.6), que o
    agente antigo já aceita porque o dele é maior; atualizar é opcional e mexe na máquina do worker (procedimento em
    `operacao.md` §9).
- **Saúde depois do deploy:** `ok`, sem problemas.
- **Plano-100:**
  - 86 de 91 itens `implemented` (ver [`execucao-plano-100-runner.md`](execucao-plano-100-runner.md));
  - pendentes: 7.4 (`partial`: bateria feita em 25/09; falta o cache do verificador e medir o ator local), 8.3,
    8.4, 12.3 e T.2. O 0.10 fechou em 24/09 com a decisão 2 (sem revogação); 7.9, 10.6 e T.4 nasceram e fecharam
    em 24–25/09 a partir do backlog B1–B3 e B13;
  - o que falta em cada um, separado por tipo, está em [`roadmap.md`](roadmap.md).

## Entregas recentes

- **26/09 (código, PR #9, implantado em `3da3bb5`):** a automação entra com a credencial que a pessoa fornece, com
  consentimento (ADR-025, substitui a recusa do ADR-009; **confirmado pelo dono em 26/09**, na sessão coordenadora,
  ao autorizar merge e deploy). Migração `040_credenciais_da_execucao`. Chrome cadastrado em `apps`. **Troque a senha
  do portal usado na `22d65f`**: ela foi ao provedor de IA antes da correção. A senha da execução `22d65f`, que ficou em claro, foi mascarada
  no banco de produção; o histórico do painel limpa sozinho a entrada antiga ao abrir.
- **24/09 (código, implantado):** a lista está no [`CHANGELOG.md`](../CHANGELOG.md#2026-09-24--custo-de-ia-painel-perfis-multi-app-treinamento-limites-por-servidor).
  - custo de IA: 7.5–7.8, e 7.1 ligado no Ollama;
  - painel: 11.1–11.9;
  - grupos de acesso: 11.10;
  - perfis multi-app: 12.1 e 12.2;
  - modo treinamento: 13.1–13.3;
  - limites por servidor: 10.5.
- **25/09 (bateria de avaliação, real, ~US$ 2,57):** rejulgamento 41/56 (6 falsos positivos do Haiku), linha de
  base 16/17 (QA no android-09 remoto e Instagram no android-01), HTTP 500 do verificador em 0,7 % e recuperados.
  O ator rodou no fallback (Sonnet), porque o Ollama estava fora do ar. Detalhe em
  [`relatorio-validacao.md`](relatorio-validacao.md) §11.1.
- **24/09 (documentação e processo, esta sessão):**
  - criados `CLAUDE.md`, o índice, `produto`, `arquitetura`, `dominios/*`, `ia`, `operacao`, `decisoes` (22 ADRs), o
    knowledge lake (23 aprendizados), `roadmap`, `CHANGELOG` e este handoff;
  - skills `retomar`, `preparar-tarefa` e `fechar-tarefa`, e as regras em `.claude/rules/`;
  - `scripts/docs-check.py` e o job `docs` no CI;
  - o mapa do plano-100 corrigido: o 10.5 estava fora e isso quebrava `check`/`relatorio`;
  - o relatório de execução regenerado;
  - o teste do livro-razão reescrito: o antigo testava o executor aposentado.

## Em curso

- **Aparelho × persona × app × sessão (android-06), fase cloud** na branch `claude/awesome-lamport-s602ai`, não
  integrada nem implantada. Falta a fase local: [`handoffs/android-device-persona-runtime.md`](handoffs/android-device-persona-runtime.md).

- **Loja de aplicativos e proxy do aparelho** (pedido do dono, 26/09): PR #10, implantada em `3da3bb5` (migração
  041). Prova `real` limitada à vitrine, às prévias e a uma distribuição `already`; instalação de app secundário e
  proxy aplicado num aparelho real ficam `not_run`. Detalhes em
  [`dominios/apps-e-loja.md`](dominios/apps-e-loja.md). Pendências do dono:
  - se a volta de UM aparelho deve continuar rebaixando a versão para o parque inteiro;
  - se promover deve continuar atualizando sozinho os aparelhos que têm o app como principal (hoje, sim).

## Bloqueios e validações pendentes

- **Decisões do dono:**
  - escolher o primeiro app do 12.3;

  A decisão 7 foi executada em 25/09: bateria de ~US$ 2,57, do saldo de US$ 12,72 ([ADR-018](decisoes.md)).

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
| B14 | **Corrigido e implantado em 25/09 (7.10, ADR-024).** O verificador Haiku errou nos dois sentidos no rejulgamento (6 falsos positivos, 9 falsos negativos em 56 capturas de 19–20/09) e recusou `delivered` onde bastava `sent`. Agora a recusa com nível suficiente é rejulgada uma vez pelo modelo de escalonamento, e "sim" sobre tela sem elementos não prova nada. Os falsos positivos de "tela errada" seguem possíveis quando a prova local não casa | `backend/app/taskqueue/executor.py` (`_verify`) | bateria de 25/09 |
| B15 | **Corrigido e implantado em 25/09 (7.11, ADR-023).** A saúde dizia `ok` com o ator local fora do ar e tudo no fallback; agora lista as chamadas em fallback dos últimos 30 min (`ai_fallback_em_uso`). E o ator passou a ser declarado no Sonnet | `backend/app/state.py` (`_ia_em_fallback`) | bateria de 25/09 |
| B16 | Depois do deploy, aparelhos remotos parados ficam com o detalhe "servidor Notebook da LAN fora do ar" mesmo com o worker online; o texto só muda quando o aparelho muda de estado | `backend/app/devices/manager.py` (`_motivo_do_externo_parado`) | deploy de 25/09 |
| B13 | **Corrigido e implantado em 25/09 (T.4).** O CI estava vermelho desde pelo menos `bfffb0d`, por ambiente: cofre sem chave fora do Windows, scripts PowerShell do Windows no pwsh do Linux, `apksigner` novo (defeito real no inspetor), mock de frame com `Blob` do jsdom no Node 22, saúde dependente de SDK/KVM do host, `cryptography` 46.0.3. **Verde no run 36078946300 (`9e12baf`).** Implantado em `e6b00db` com `cryptography` 50.0.0 | `.github/workflows/ci.yml`, `backend/tests/`, `frontend/src/app.integration.test.tsx` | T.4 no livro-razão |

## Próxima ação concreta

1. Rode a skill `retomar` para conferir que o git e este arquivo estão de acordo.
2. **Agente do worker** (opcional, precisa de autorização: mexe na máquina do worker): atualizar para `e6b00db` e
   limpar o `agent_outdated`. Procedimento em `operacao.md` §9.
3. **Decisões de 25/09 aplicadas** (delegadas pelo dono, por custo-benefício): verificador Haiku com rejulgamento
   escalado e guarda de tela vazia (7.10, ADR-024); ator declarado no Sonnet (ADR-023); saúde acusa fallback (7.11).
   Falta prova real do 7.10, que só aparece quando o erro medido se repetir; acompanhar as decisões do verificador
   em Execuções. Reavaliar o verificador com a próxima bateria (capturas novas).
4. **Sem gasto e sem mundo real:** B6 (vocabulário de prova) é decisão do dono; B4 (cerca após restauração) é o
   próximo item de código de risco baixo.
5. **Com autorização do dono:** o ensaio do aceite 6, derrubando o túnel no meio de um `start`. É o de menor risco
   entre os reais; o procedimento está em [`worker.md`](worker.md).
