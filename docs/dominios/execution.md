# Domínio: execution (o ciclo de uma execução)

Como um comando vira etapas executadas e comprovadas, fase a fase, e o que as fases G, H e K2 da evolução
arquitetural acrescentaram a esse caminho ([design](../design/evolucao-arquitetural.md) §2.4, §11, §14, §16 e §18). A
skill que entra no ciclo está em [skills](skills.md); o compilador que a baixa para `Plan`, em
[runtime de skills](../skill-runtime.md); a porta de VERIFY, em [capabilities](capabilities.md). Decisões:
[ADR-035](../decisoes.md#adr-035--resourcespec-declarativo) (recursos declarativos),
[ADR-036](../decisoes.md#adr-036--receitas-como-estratégia-de-execução) (receitas como estratégia) e
[ADR-038](../decisoes.md#adr-038--máquinas-de-estado-de-execução-formais-conferir-antes-de-impor) (máquinas de estado).

Caminhos relativos a `backend/app/`, salvo indicação.

- Fase G: `3a1da01`, `aa3575b`, `4e210c4` e `fb30ef3`, integrados em `0b736f3`; correção de receita em `eb9ba02`.
- Fase H, parte 1: `14362ee`, `37752ed` e `ab211e6`, integrados em `1e69d02`.
- Fase H, parte 2: `9f76832`, `80d5fc7` e `373d45f`, integrados em `2fc09b2`.
- Fase I (resolução de intenção): `00633d5` e `0795cc7`, integrados em `21b1fff`.
- Fase K2 (corpos de requisição e máquinas de estado): `e7af6f0` e `48e76ae`, integrados em `b56e06c`.

**Estado (27/09).**

- **A fiação da G está feita.** Com `skills.enabled` ligado (o padrão é desligado), uma skill publicada vira o plano
  da execução pelo compilador, e o executor de sempre a roda, com a trilha da 045. Prova `simulated` (harness na porta 5640, `FakeInstagram`,
  `CountingProvider`).
- **H completa, sem ligação no `_tick`.** A parte 1 trouxe tipos, `diff`/`plan` puros, leitura dos quatro recursos e
  o `PlanReport`. A parte 2 trouxe `apply`, `verify` e `reconcile` pelo canal de comandos. O runtime usa só a
  **leitura**: `POST /api/runs` com `mode=plan` devolve o `plan_report`, e o `_plan` grava `objectives.resource_plan`.
  **`apply` e `reconcile` não são chamados por nada** fora dos testes: nem o `_tick`, nem uma rota. As portas do
  `_tick` continuam sendo o "apply" desses recursos.
- **Máquinas de estado na fase "conferir e registrar" (K2).** Execução, objetivo e tentativa têm tabela no domínio.
  Fora dela, o `Repository` avisa e conta, **sem bloquear**. A etapa continua imposta, como antes.
- **A RESOLVE da I está ligada.** O `_plan` resolve pelo `IntentResolver`: com `skills.enabled`, parâmetro vazio,
  valor que não serve ao tipo e empate entre skills viram pergunta (`needs_input`, sem plano). As etapas por IA
  (semântica e desempate) são portas com o provedor nulo: `not_run`.
- Suíte SQLite no merge A–H: 2115/2115; no branch da fase I: 2252/2252; no branch da H parte 2: 2293/2293
  (`simulated`). Na K2, a suíte inteira rodou com o coletor de transições (2270 testes, ver
  [Máquinas de estado](#máquinas-de-estado-fase-k2)). I, H parte 2 e K2 em PostgreSQL: `not_run`.
- CI com PostgreSQL verde em `793fe00` (run `36324634678`, `workflow_dispatch`, `pytest -q` inteiro): A–E com as
  migrações 042–046 e também os testes da H parte 1, porque o merge `1e69d02` é ancestral de `793fe00`. G em
  PostgreSQL: `not_run`.
- Nada implantado. Execução de skill numa conta real do Instagram: `not_run` (exige autorização).

## O ciclo, fase a fase

O runtime de skills **não é um segundo executor**. A skill entra como `Plan`, e daí em diante o caminho é o mesmo do
planejador: `materialize`, `claim_step`, `run_step`, `_verify`, `_apply`.

| Fase | O que faz | Código de hoje | O que G/H mudou |
|---|---|---|---|
| **RESOLVE** | comando → `IntentResolution` (intenção resolvida, pergunta ou nada) | `taskqueue/service.py::RunService._plan` chama `self.skills.for_command(comando, perfis)` (`modules/skills/infrastructure/run_planning.py::SkillRunPlanner`), que resolve pelo `IntentResolver`: modelos (`CompositeSkillRegistry.candidates`) → tipos → semântica → LLM | **novo (G), refeito (I).** Antes da G, `_plan` chamava `FlowStore.match` direto; na G, o primeiro que casava pelo registro. A trilha vai para `runs` em `RunService._registrar_resolucao` → `Repository.note_run_skill` |
| **COMPILE** | versão + parâmetros → `Plan` | `SkillRunPlanner.plan`: pergunta pendente → sem compilar; `schema_version` 0 → `legacy_flows.py::legacy_plan`; `schema_version` 1 → `SkillPlanCompiler.compilar(..., parameters=...)` com os valores tipados. Sem resolução, o planejador de sempre (`provider.plan` por `StepExecutor._ai`, `role="plan"`) | **novo (G).** Falha de compilação vira `RunPlan(plan=None, issues)`, e pergunta vira `RunPlan(plan=None)` com `questions` (I). Nos dois, a execução para em `needs_input` (`RunService._skill_sem_plano`), nunca plano parcial nem o planejador por fora |
| **PLAN** | persistir e expor | `Repository.save_plan` e `Repository.materialize` → `_insert_steps`. `RunCreate.mode == "plan"` para em `planned` | `_insert_steps` copia `PlanStep.origin` para `steps` (G). `mode=plan` devolve o `plan_report` (H2, `RunService.relatorio_de_recursos`), e `_plan` grava `objectives.resource_plan` logo depois do `materialize` (H2, `RunService._fotografar_recursos`) |
| **PREFLIGHT** | recusar ou esperar antes de gastar | `RunService.pre_voo`; portas do `Scheduler._tick` (`_portas_do_app`); `Scheduler.policy_gate` antes de `claim_step` | nada ligado. A leitura dos recursos (H) só alimenta o relatório e a foto; o `apply` (H2) existe e não é chamado |
| **APPLY** | nó → estratégia | `Repository.claim_step` → `StepExecutor.run_step`/`_run_step`; `Repository.log_intent` antes de cada ação | registro da estratégia exercida em `attempts` (G, `StepExecutor._registrar_estrategia`). A ordem **não** vem de `origin.strategies` |
| **VERIFY** | prova observada | `StepExecutor._verify`: determinística → prova local → modelo | a prova local passa por `CatalogCapabilityProvider.verify` (G, `StepExecutor._prova_local`). O sucesso continua gravado pelo executor |
| **RECONCILE** | efeito sem desfecho | `Repository.commit_state`; `Scheduler._reconciliar`; `SocialService.reconcile_pending_effects`; `CommandStore.reconcile_after_restart`; `ReleaseService.reconcile_after_restart`; `commands/reconciler.py` | o `reconcile` de recurso existe (H2, `shared/convergence.py::reconcile_resource`) e não é chamado. `CatalogCapabilityProvider.reconcile` levanta `NotImplementedError` |
| **COMPLETE** | desfecho | `Scheduler._apply`, `_maybe_complete`, `Repository.recompute_run`, `Scheduler._learn_flow` | `_learn_flow` e `FlowStore.learn_from_run` pulam execução de skill (G). As transições de execução, objetivo e tentativa são conferidas contra a tabela do domínio, sem bloquear (K2) |

A divisão continua a mesma: **o sucesso é gravado pelo executor (VERIFY) e os demais desfechos pelo scheduler
(COMPLETE).** Nenhuma estratégia nova mudou isso.

### RESOLVE e COMPILE: `SkillRunPlanner`

`modules/skills/infrastructure/run_planning.py`:

- `SkillRunPlanner(registry, pacote_do_app, skills=None, *, classifier=None, disambiguator=None)`:
  `resolve_intent(command, profile_ids)`, `plan(resolution)` e `for_command(command, profile_ids)`.
  - `resolve_intent` (fase I) pergunta ao `IntentResolver` e devolve um `IntentResolution`
    ([skills](skills.md#resolução-de-intenção)). `classifier` e `disambiguator` são as etapas 3 e 4; sem eles, o
    provedor nulo, que não chama IA. O `AppState` não os passa.
  - `for_command` devolve `None` só quando nada casa (`no_match`).
- `RunPlan(resolution, plan, issues)`: `ok`, `resolved`, `questions`, `ref`, `legacy_flow_id`, `skill_id`,
  `skill_version`, `skill_hash`, `name`.
  - `resolved` é a habilidade de que se fala: a resolvida, ou a única que precisa de resposta. No empate é `None`, e
    com ela `ref`, `skill_id`, `skill_version`, `skill_hash` e `name`.
  - `skill_id` e `skill_version` são nulos para o fluxo legado (`flow:<id>@1`): a trilha antiga já o diz por
    `runs.flow_id`.
  - `skill_hash` é `resolved.version.content_hash`, o hash do **documento** da versão. Para o fluxo legado, é o hash
    calculado na leitura pelo `LegacyFlowAdapter`.
  - `legacy_flow_id` só existe para `flow:<id>@1`. A v1 de um fluxo **adotado** é da skill: grava `skill_id` e não
    grava `flow_id` nem incrementa `flows.used` (a trilha por fluxo adotado é decisão da fase J).
- **A mesma porta para quatro perguntas** (decisão P2): `RunService._plan`, `RunService.apps_exigidos`,
  `GET /api/flows/match` e `POST /api/skills/resolve` (fase I) usam `AppState.skill_planner`. A estimativa do painel,
  o pré-voo e a prévia não divergem do que a execução faz
  (`backend/tests/test_intencao_chamadores.py::test_os_tres_chamadores_coerentes_para_a_mesma_frase`, `simulated`).
- **Composição** (`state.py::AppState.__init__`):
  - `SqlSkillRepository(db, DslDocumentValidator(...), adoption_enabled=...)`;
  - `CompositeSkillRegistry(skill_repo, LegacyFlowAdapter(db, scheduler.flows), skills_enabled=..., flows_enabled=...)`;
  - `SkillRunPlanner(skill_registry, _pacote_do_app_id, LockedVersions(...))`.
  - Os interruptores são lambdas sobre `cfg.file`, lidas a cada chamada.
  - O atributo é `skill_registry`, e não `skills`: `AppState.skills` já é o modo treinamento.

### A pergunta da RESOLVE (`needs_input`)

Fase I. Quando a resolução devolve pergunta, `RunService._plan` chama `RunService._skill_sem_plano`, que:

- grava a trilha (`Repository.note_run_skill`) só quando há **uma** habilidade (`RunPlan.resolved`). No empate,
  `runs.skill_id`, `skill_version` e `skill_hash` ficam nulos;
- emite um evento `log` de nível `warn` ("… precisa de resposta antes de planejar") com
  `data: {skill, questions, candidates}`:
  - `skill`: a referência (`"ig.abrir_conversa@1"`), ou `null` no empate;
  - `questions`: `MissingInfo.as_dict()` de cada pergunta (`field`, `question`, `reason`, `expected`, `options`,
    `received`, `skill`);
  - `candidates`: as referências empatadas, ou `[]`;
- põe a execução em `needs_input` com `status_detail` = as perguntas unidas por `" | "`, no mesmo formato das
  perguntas do planejador (`plan.missing`).

O que **não** acontece: `runs.plan` fica nulo, nenhum objetivo é materializado e o planejador não é chamado. A pessoa
quis uma habilidade; mandar o comando ao planejador seria escolher às cegas, e pago. A execução também não cai para o
fluxo que casaria o mesmo comando ([skills](skills.md#resolução-de-intenção)).

`RunService.apps_exigidos` e `GET /api/flows/match` tratam a pergunta como "nada casou": `[]` e `null`.

Provas (`simulated`, harness na porta 5640):
`backend/tests/test_intencao_chamadores.py::test_os_tres_chamadores_coerentes_para_a_mesma_frase` (valor inválido,
buraco vazio e empate, com `count("plan")` inalterado) e `::test_empate_na_execucao_pergunta_com_as_opcoes_e_nao_grava_skill`.

### Precedência: skill → fluxo → planejador

| `skills.enabled` | `ai.flows` | O comando vai para |
|---|---|---|
| desligado | desligado | o planejador, como antes |
| desligado | ligado | o fluxo ativo que casar, senão o planejador |
| ligado | desligado | a skill publicada que casar, senão a pergunta de uma skill com `{nome}` vazio, senão o planejador |
| ligado | ligado | a skill publicada, senão o fluxo ativo, senão a pergunta de uma skill com `{nome}` vazio, senão o planejador |

Com `skills.enabled` ligado, a skill que casa ainda pode virar pergunta (valor que não serve ao tipo, ou empate)
em vez de plano ([acima](#a-pergunta-da-resolve-needs_input)).

- `skills.enabled` (`config.py::SkillsCfg.enabled`) tem padrão `false` (decisão P1).
- `ai.flows` (`config.py::AiCfg.flows`) tem padrão `false` no código. O `config/config.example.yaml` traz `true`, e o
  valor de cada instalação não foi lido.
- Publicar uma skill não liga nada sozinho.
- Skill que casa e não compila **não cai para o fluxo nem para o planejador**: vai a `needs_input`, com os
  `CompileIssue` no evento `log` (`data.issues`, via `as_dict()`).

### Com as skills desligadas: o que ficou igual e o que não

Igual ao de antes:

- o fluxo legado dá o mesmo `Plan` de `FlowStore.match`, o mesmo `runs.flow_id`, o mesmo `flows.used` e o mesmo texto
  de decisão ("Plano reaproveitado do fluxo…");
- o planejador é chamado nos mesmos casos, e o plano dele sai sem `origin`, byte a byte igual;
- a fase I não acrescenta nada aqui: o fluxo não tem tipo, não empata e não casa com buraco vazio, e os valores
  passam como vieram (`backend/tests/test_intencao_resolucao.py::test_paridade_com_o_flowstore_match`,
  `::test_interruptores_continuam_mandando`).

O que mudou mesmo com `skills.enabled` desligado (fase G):

- `attempts.strategy` é gravado em **toda** tentativa, e `ai_calls.attempt_id` em toda chamada paga por uma
  tentativa;
- `runs.skill_hash` passa a ser gravado quando um fluxo legado resolve o comando (`ai.flows` ligado);
- os quatro campos novos de `steps` entram no `INSERT` (nulos, sem `origin`);
- `FlowStore.learn_from_run` consulta `skill_versions`;
- a prova local passa pelo provider, com o desvio da marca de falha ([abaixo](#verify-pela-porta-de-capability));
- `GET /api/flows/match` passa a respeitar `ai.flows` ([contrato](../api-contract.md#adendo-v021-27092026--habilidades-no-caminho-dos-fluxos)).

Provas (`simulated`):

- desligado × desligado: `backend/tests/test_fatia_abrir_conversa.py::test_com_as_habilidades_desligadas_o_comando_vai_ao_planejador`;
- ligado, sem skill publicada, com fluxo: `::test_fluxo_legado_grava_a_trilha_de_sempre_e_o_hash`;
- mesmo plano do `FlowStore`: `backend/tests/test_habilidades_na_execucao.py::test_fluxo_legado_pelo_planejador_e_o_mesmo_plano_do_flowstore`.
- Desligado × ligado pelo `_plan` inteiro não tem teste de harness próprio. O caminho é o mesmo do segundo teste, com o
  backend de skills pulado pelo registro.

## Estratégias

`modules/capabilities/domain/strategy.py::StrategyKind` é o vocabulário ([capabilities](capabilities.md#strategykind-e-a-cadeia-de-estratégias)).
O executor exerce duas:

- **`recipe`**, a receita gravada, reproduzida pelo `Replayer` (`taskqueue/recipes.py`). É consultada quando, ao mesmo
  tempo:
  - `ai.recipes` não é `off` e o app tem pacote;
  - o efeito da etapa não disparou em nenhuma tentativa (`Repository.commit_state` na entrada de `run_step`);
  - `RecipeStore.find` achou receita ativa para o pacote, a versão do app, o `template_hash`, a assinatura e a
    variante;
  - dentro do laço de `_run_step`: `ai.recipes == "replay"`, a receita não divergiu e o efeito não disparou.
- **`ai_actor`**, o laço de decisão com o modelo (`provider.decide` por `StepExecutor._ai`).
- A divergência da receita (`RecipeDiverged`) passa a etapa para a IA dentro da mesma tentativa: a cadeia exercida
  é `recipe>ai_actor`.
- Em `ai.recipes == "shadow"`, a receita só é comparada (`StepExecutor._shadow_compare`) e não é exercida: a
  tentativa registra `ai_actor`.

O registro (`StepExecutor._registrar_estrategia`, no `finally` de `run_step`):

- `_RecipeRun.exerceu` é chamado em dois lugares de `_run_step`: antes de `Replayer.next` (`recipe`) e antes de
  `provider.decide` (`ai_actor`). A lista guarda a ordem e não repete.
- `attempts.strategy = ">".join(exercidas)`, ou nulo se nada foi exercido. `attempts.recipe_id` só quando `recipe`
  está na cadeia.
- Vale também quando a tentativa sai por exceção: o que ela já fez aconteceu. Uma falha ao gravar só vai para o log e
  nunca derruba a etapa.
- **`human` nunca aparece em `attempts.strategy`.** `waiting_user` é desfecho (`Outcome.waiting_user`), não
  estratégia exercida.
- **`steps.strategy` é o planejado**: `">".join(origin.strategies)` da etapa que veio de skill, nulo para o
  planejador. É gravado e **não comanda a ordem**. A ordem do executor é fixa: receita, se aplicável, e IA.
- `ExecutionStrategy` (`modules/execution/application/ports.py`) continua sem implementação.
  `RecipeExecutionStrategy` e `AiActorStrategy` são propostas ([ADR-036](../decisoes.md#adr-036--receitas-como-estratégia-de-execução)).

## A trilha (colunas da 045)

Todas anuláveis, sem FK e sem preencher linhas antigas ([banco](../banco.md)).

| Coluna | Quem grava | O que registra |
|---|---|---|
| `runs.skill_id`, `runs.skill_version` | `Repository.note_run_skill`, chamado por `RunService._registrar_resolucao` e `_skill_sem_plano` | a skill **raiz** que resolveu o comando (numa composta, a chamadora). Nulos para fluxo legado e planejador |
| `runs.skill_hash` | o mesmo | `RunPlan.skill_hash`: o hash do documento da versão; no fluxo legado, o calculado na leitura. Nulo no planejador |
| `steps.skill_id`, `steps.skill_version` | `Repository._insert_steps`, de `PlanStep.origin` | o **dono** do nó: numa composta, a filha (`abrir_abrir_conversa` sai com `ig.abrir_conversa@1`) |
| `steps.node_id` | o mesmo | o id do nó, igual a `steps.key` |
| `steps.strategy` | o mesmo | a cadeia planejada (`recipe>ai_actor`) |
| `attempts.strategy`, `attempts.recipe_id` | `Repository.note_attempt_strategy`, por `StepExecutor._registrar_estrategia` | a cadeia exercida (`recipe`, `ai_actor` ou `recipe>ai_actor`) e a receita reproduzida |
| `ai_calls.attempt_id` | `Repository.add_usage(attempt_id=)`, por `StepExecutor._ai(attempt_id=)` nas chamadas `decide` e `verify` e no registro de orçamento estourado | a tentativa que pagou a chamada. Nulo no planejamento e no rascunho social (`AppState._draft_gate`, que roda antes da posse, sem tentativa) |
| `objectives.resource_plan` | `RunService._fotografar_recursos`, no `_plan`, logo depois do `materialize` (H2) | a foto dos recursos declarados, por aparelho: `{skill: {id, version, content_hash}, instance_id, profile_id, resources, resolved_at}`. Só quando a skill declara `spec.resources`; nulo no fluxo legado e no planejador. A refoto no despacho (design §11) é proposta |

- A `origin` mora no próprio `Plan`. Por isso a trilha sobrevive à expansão do `for_each`, à recuperação e à revisão,
  que releem `runs.plan` e passam por `_insert_steps` de novo.
- `note_attempt_strategy` não passa pela cerca de `finish_attempt`: não é desfecho, e a linha é da própria tentativa.
- Trilha antiga: `runs.flow_id` preenchido com `runs.skill_id` nulo quer dizer `flow:<flow_id>@1`. `skill_hash` nulo
  quer dizer "não se sabe", e nunca é inventado.

Prova (`simulated`), em `backend/tests/test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`:

- na primeira execução, `count("plan") == 0`, `planner.provider == "skill"`, `runs.skill_*` preenchidos e
  `runs.skill_hash` igual ao `content_hash` da versão;
- as duas etapas com `steps.strategy == "recipe>ai_actor"`, `attempts.strategy == "ai_actor"` e `recipe_id` nulo;
- toda chamada de IA da etapa aponta a tentativa, e nenhuma chamada da execução fica sem `attempt_id`;
- na segunda execução, as receitas aprendidas reproduzem: `attempts.strategy == "recipe"`, `recipe_id` da receita e
  zero `decide`.

## VERIFY pela porta de capability

- `StepExecutor.__init__` cria `self.capabilities = CatalogCapabilityProvider(CatalogCapabilityRegistry(...))`.
- `StepExecutor._verify` pergunta a `_prova_local(step, capability, obs)`, que monta a `StepView` e chama
  `CatalogCapabilityProvider.verify` com `Observation(obs.tree, obs.package)`. O executor observa; o provider só lê
  a leitura pronta.
- Só `proved` é atalho, exatamente como o `True` de `local_proof_holds` antes. `not_proved` e `unknown` seguem o
  caminho de sempre, o modelo.
- Etapa sem capability do catálogo, ou app sem pacote: `capability` nulo, sem atalho.

**Desvio registrado, mais conservador.** Tela com marca de falha visível ("Not delivered") e prova local positiva:

- antes, era atalho: `local_proof_holds` não olha marca de falha, e a conferência de marca de `_verify`
  (`if ok and failure_marks`) só roda quando `_run_step` passa as `failure_marks`, o que só acontece com o efeito
  disparado (`fired`). Sem o disparo, a etapa passava pela árvore com a marca à vista;
- agora, o modelo julga, porque `CatalogCapabilityProvider.verify` lê as `failure_marks` da própria definição e
  devolve `not_proved` com a marca à vista, disparado ou não. Com o efeito disparado, o desfecho é o mesmo de antes:
  a conferência de marca reprova depois do "sim";
- falha nunca vira sucesso. O desvio vale para toda etapa com capability do catálogo, com ou sem skill.

Provas (`simulated`), em `backend/tests/test_habilidades_na_execucao.py`:

- `::test_a_prova_local_pela_porta_e_a_mesma_de_antes`: sem marca de falha, `proved` ⇔ `local_proof_holds is True`,
  em dez telas;
- `::test_a_unica_diferenca_e_a_marca_de_falha_visivel`: o desvio acima.

## COMPLETE: aprendizado de fluxo

- `Scheduler._learn_flow` sai cedo quando `runs.skill_id` está preenchido.
- `FlowStore.learn_from_run` recusa quando há `flow_id` ou `skill_id`, e também quando uma skill **publicada** já tem a
  mesma `match_key`. É o caso da execução que caiu no planejador por estar fora do escopo da skill (design §15.2).
  Só lê `skill_versions`; fluxo nunca escreve em tabela de skill.
- Prova (`simulated`): `test_habilidades_na_execucao.py::test_fluxo_nao_se_aprende_de_skill_nem_do_comando_que_uma_skill_publicada_cobre`;
  na fatia, com `ai.flows` ligado, a execução de skill deixa `flows` vazio.

## Proteções herdadas

A skill entra como `Plan`, então as regras do executor e do scheduler valem por construção; a fase G não mudou
nenhuma delas. A lista completa, R1–R14, P14 e P15, está no [design](../design/evolucao-arquitetural.md) §14.5. As
principais:

| Proteção | Onde mora | O que garante |
|---|---|---|
| lease e posse | `Repository.claim_step` (`claimed_by`, `claim_expires_at`, índice da 018), `transition_step`, `finish_attempt` | uma etapa ativa por aparelho, a mesma tentativa nunca disparada duas vezes, e `PosseDaEtapaPerdida` quando outro dono assumiu |
| cerca e outbox | `commands/` (ADR-010) | verbo de aparelho só por comando cercado, com diário |
| intenção antes do efeito | `Repository.log_intent` → `finish_action` | toda ação nasce `intended` antes de tocar o aparelho |
| um commit por etapa | `Repository.commit_state` | considera todas as tentativas. A receita não é consultada com efeito disparado (`run_step` e `_run_step`) |
| incerto nunca repetido sozinho | `StepExecutor._run_step`, `Scheduler._apply`; saída só por `RunService.resolve` ou sonda (`commands/reconciler.py`) | falha depois do disparo é `uncertain`, nunca nova tentativa |
| aprovações e portas antes da posse | `Scheduler.policy_gate`, `AppState._draft_gate`, `AppState._approval_gate` | política, limite, rascunho e aprovação sem consumir tentativa nem IA |
| prova por pós-condição | `StepExecutor._verify` | sucesso é observado, não declarado; a estratégia nunca declara o próprio sucesso |

## Recursos declarativos (fase H, parte 1)

Um `ResourceSpec` diz o **estado desejado** de que uma skill precisa. A fase H, parte 1, entrega a leitura, a
diferença e o plano, sem aplicar nada ([ADR-035](../decisoes.md#adr-035--resourcespec-declarativo)). A parte 2
acrescenta `apply`, `verify` e `reconcile` ([abaixo](#recursos-aplicar-verificar-e-reconciliar-fase-h-parte-2)) e
serve o relatório em `mode=plan` ([abaixo](#modeplan-o-planreport-servido-fase-h-parte-2)).

### O modelo

`shared/resources.py`, no kernel, porque três contextos pares implementam os tipos e a execução consome os quatro:

- `ResourceKind`: `device.state`, `app.installation`, `account.binding` e `app.session` (os mesmos da DSL).
- `ResourceSpec(ref, desired, on_missing)`; `ResourceSpec.of(kind, target, desired, on_missing)` normaliza e recusa
  tipo ou `on_missing` desconhecido (`ValueError`).
- `OnMissing`: `wait`, `apply` e `ask`. `wait` e `apply` planejam a mesma convergência; `ask` troca a
  convergência por uma pessoa. A diferença entre `wait` e `apply` (quem dispara) vale no `apply` da parte 2.
- `Target(instance_id, profile_id)`: onde o recurso é resolvido.
- `DriftStatus`: `in_sync`, `diverged`, `pending`, `held`, `unknown`, `blocked` e `unsupported`.
- `ResourceAction(purpose, verb, reason)`, com `ActionPurpose`: `observe` (ler), `converge` (levar ao desejado) e
  `ask` (pessoa). Ação de pessoa não tem verbo; ação de sistema sempre tem.
- `plan_by_rules(drift, *, kind, converge, observe, always_ask=False)`: o plano comum a todos os tipos.
- `known(vocabulario, valor)`: valor lido fora do vocabulário vira `None`, e o `diff` o trata como `unknown`, nunca
  como o estado mais parecido.

As três regras:

1. **`unknown` nunca vira `in_sync`.** A resposta a `unknown` é ler, por um comando de leitura que já existe
   (`app.verify`, `session.verify`). Sem comando de leitura, nenhuma ação, e o recurso continua `unknown`.
2. **Observado = desejado ⇒ zero ações.** A segunda passada não planeja nada.
3. **Só verbo que existe.** `ResourceAction.verb` é de `commands/despacho.py` (`LIFECYCLE_ACTIONS` ou
   `APP_COMMAND_VERBS`). O que nenhum comando faz vira `ask`.

### Os quatro providers

Cada um tem `kind`, `read_current_state(ref, target)`, `diff(spec, observado)` e `plan(drift)`. A leitura é só
`SELECT` ou memória; `diff` e `plan` são as funções puras do domínio de cada contexto. Desde a parte 2, a porta é
`Protocol`: `modules/execution/application/ports.py::ResourceProvider`, que os quatro cumprem por estrutura, sem
importá-la. Todos recebem `bus: CommandBus | None = None`; sem canal, são só leitura (é assim que o `PlanReport` os
monta, em `modules/execution/infrastructure/providers.py::resource_providers`).

| `kind` | Classe | Lê | Converge | Lê de novo (`unknown`) |
|---|---|---|---|---|
| `device.state` (`online`) | `modules/fleet/infrastructure/device_state.py::DeviceStateProvider(db, runtimes, *, bus)` | `instances.desired_state` e, do runtime em memória (`fleet/application/ports.py::DeviceRuntimeView`), `state`, `state_detail`, `readiness_phase`, `store`, `external`, `worker_verbs`, `snapshot_valid` | `start` (parado, parado por decisão, hibernado sem snapshot) e `wake` (hibernado com snapshot) | nenhum comando lê o aparelho: `unobserved` fica sem ação |
| `app.installation` (`release: promoted`) | `modules/applications/infrastructure/app_installation.py::AppInstallationProvider(db, *, bus)` | `apps` (por `AppRepository.obter`), `instances.app_id`, `app_releases`, `device_app_state` | `app.install` (`missing`, `outdated`, `other_build`, `abandoned_version`) | `app.verify` (`unobserved`, `unrecognized_state`, `no_outcome`, `no_version`) |
| `account.binding` (`bound`) | `modules/identity/infrastructure/account_session.py::AccountBindingProvider(db, *, bus)` | `device_profile_bindings` ativo e `instagram_profiles.username` | nunca: vincular é de pessoa (`always_ask=True`) | — |
| `app.session` (`session: ready`, `account: bound_profile` opcional) | `account_session.py::AppSessionProvider(db, *, tem_provedor_de_sessao, session_max_age_s, unknown_retry_cap, agora, bus)` | `apps`, vínculo ativo, `instagram_profiles` (status, `offline_policy`), `instances` (localidade), `instagram_credentials.status`, `instagram_sessions`, `profile_accounts` | `session.connect` (`logged_out`, `relocated`), só com credencial utilizável no cofre | `session.verify` (`unobserved`, `other_device`, `stale`, `account_unproven`). `account_unknown` (app sem provedor de sessão) fica sem ação |

- **`device.state`.** Sem runtime neste processo (papel `api`, ou aparelho ainda não carregado), o observado não
  existe: `unknown`. `DeviceManager.devices` cumpre `Mapping[str, DeviceRuntimeView]` por estrutura. `desired_state =
  stopped` não bloqueia, porque o rodízio de hoje liga sob demanda; vira o código `stopped_by_decision` e aviso nos
  riscos.
- **`app.installation`.** Refaz sem efeito as regras de `ReleaseService.promoted_release` e
  `AppState.release_no_aparelho`, que no legado moram em métodos que também gravam. Mais nova e não voltada é `held`
  (ADR-026). `verifying` sem dono é `unknown`, e a resposta é `app.verify`, nunca instalar. Espalhar o app para quem
  não o tem como principal é `blocked` (`not_distributed`): Distribuir é decisão de pessoa.
- **`app.session`.** Segue a ordem da porta de sessão (`AppState._session_gate`), sem as lambdas que autenticam.
  Perfil fora de `active`, desafio, conta errada, tela não reconhecida no teto e credencial ausente ou recusada são
  `blocked` (ADR-009, ADR-029). Da credencial só sai "tem, utilizável ou recusada", nunca o `secret_ref`. Qual app
  tem provedor de sessão é injetado por quem compõe (`tem_provedor_de_sessao`), porque o catálogo fica abaixo de
  identity no grafo.

### `PlanReport`

`modules/execution/domain/plan_report.py`, puro:

- `build_plan_report(specs, targets, observed, *, skill=None, skill_hash=None)`. Os providers leem antes; o relatório
  só compara e planeja.
- `specs_of(graph)` tira os specs do IR (`ProcessGraph.resources`).
- **Par (recurso, alvo) que ninguém leu** vira `unknown` com o código `not_read` (`shared/resources.py::NOT_READ`),
  sem ação, e entra em `risks` como "não comprovado".
- Ordem fixa: por aparelho e, dentro dele, `KIND_ORDER` (aparelho, app, vínculo, sessão), a ordem das portas do
  `_tick`.
- Propriedades: `ok`, `drifts` (tudo o que não é `in_sync`, inclusive `unknown`), `actions`, `human_interventions`,
  `blockers`, `ready_to_run` (só `in_sync` e `held`) e `risks` (o risco de cada verbo, o que não foi comprovado e os
  avisos).
- `canonical()` e `content_hash()`, pelo mesmo JSON canônico das skills (`modules/skills/domain/document.py`): a
  mesma entrada, em qualquer ordem, dá o mesmo relatório e o mesmo hash.
- Recusa (`ValueError`) o mesmo recurso com dois desejados diferentes e duas leituras do mesmo par.
- Os demais campos do design §14.2 (etapas, efeitos, aprovações, custo estimado) ainda não entram.

## Recursos: aplicar, verificar e reconciliar (fase H, parte 2)

Os quatro providers ganharam `apply`, `verify` e `reconcile`. As regras comuns moram uma vez no kernel; cada provider
só sabe ler o seu recurso e montar os parâmetros do comando. **Nada disto é ligado ao runtime** (ver
[o que não está ligado](#o-que-não-está-ligado)).

### O canal de comandos (`CommandBus`)

- **Porta:** `shared/commands.py::CommandBus` (`Protocol`), com `request`, `latest`, `unsettled`, `settle` e
  `hosts`. Os valores: `CommandRef` (o comando aberto ou reencontrado, ou por que nenhum foi aberto), `RunRef` (a
  execução e o objetivo que pediram) e `CommandStatus` (espelho de `models.CommandState`, conferido por teste).
- **Mora no kernel, não na execução.** Quem pede comando são os providers de fleet, applications e identity, e a
  execução já depende dos três pelo `PlanReport`. Com a porta em `modules/execution`, os contextos fechariam um ciclo
  (`tests/test_arquitetura.py::test_contextos_novos_formam_um_dag`). `modules/execution/application/ports.py`
  reexporta o `CommandBus`, onde o design §7 o lista.
- **Implementação:** `modules/execution/infrastructure/command_bus.py::DespachoCommandBus`, montada por
  `command_bus(s)`. Cada verbo vai pelo caminho que já existe para ele, e só por ele (R10):

| Verbo | Caminho | O que roda |
|---|---|---|
| `start`, `wake`, `stop`, `hibernate` | `commands/despacho.py::pedir_ciclo_de_vida(..., idempotency_key=)` | o pedido do rodízio: pré-voo, outbox, o worker que hospeda |
| `app.install` | `despacho.pedir_trabalho_de_app` | `AppState._entregar` (a promovida; `-d` ao sair de versão voltada), depois de `despacho.conferir_release_para` (o 404/409 da rota de instalação) |
| `app.verify` | `despacho.pedir_trabalho_de_app` | `ReleaseService.verify_on` (relê o `pm`) |
| `session.connect` | `despacho.pedir_trabalho_de_app` | `AppState.instagram.ensure_session(automatic=True)` (`InstagramAuthenticator`): o cofre pelo canal sensível (ADR-025) |
| `session.verify` | `despacho.pedir_trabalho_de_app` | `ensure_session(observe_only=True)`: só relê a tela |

- **Mudança no despacho, sem mudar quem já chamava:** `pedir_ciclo_de_vida` ganhou `idempotency_key` opcional (sem
  ela, cada pedido da remediação e do rodízio continua sendo um comando novo), e dois invólucros públicos:
  `pedir_trabalho_de_app` (o `_despachar_trabalho` da rota, com aparelho ocupado virando `accepted: False` em vez de
  exceção) e `conferir_release_para` (o `_release_pronta_para` da rota).
- **Recusa antes de gravar** (`DespachoCommandBus._aparelho`), o que o despacho só recusaria depois de gravar um
  `rejected`: aparelho que não está neste backend, aparelho-loja, aparelho ocupado (tarefa do `Scheduler` no
  aparelho, `scheduler.workers`, ou comando em voo de `VERBOS_EXCLUSIVOS | APP_COMMAND_VERBS`), worker em manutenção,
  verbo de ciclo de vida não declarado pelo worker, e verbo de trabalho com o aparelho fora de `online`.
  `session.connect` também recusa sem o canal sensível comprovado (`sensitive_input.available()`). Assim uma segunda
  passada sobre um aparelho ocupado não enche o histórico de `rejected`.
- **`requested_by = "recursos"`** (`shared/convergence.py::REQUESTED_BY`), nunca `system`: é por `system` que a escada
  de reparo conta os próprios degraus (`CommandStore.remediacoes_recentes`), e um `start` de recurso viraria degrau.

### As regras comuns (`shared/convergence.py`)

- **`apply_action`**, na ordem:
  1. ação de pessoa (`ask`, sem verbo) não vira comando: `ValueError`;
  2. `on_missing: wait` com ação de convergência: nenhum comando, com o motivo. Quem converge é o despacho de hoje;
  3. o último comando da chave está em voo (`OPEN`) ou `uncertain`: devolve esse, `deduplicated`, sem abrir outro;
  4. **relê e replaneja**: se o recurso não pede mais aquele verbo, nenhum comando;
  5. monta os parâmetros com o observado **lido agora** (ex.: a promovida de agora, não a do plano);
  6. pede com a chave `prefixo#n+1`.
- **Chave de idempotência** (`shared/commands.py::key_prefix`): `res:<aparelho>:<tipo>:<alvo>:<verbo>#n`. É
  determinística: dois pedidos simultâneos do mesmo `n` colidem na chave única de `commands`, e o segundo recebe o
  comando do primeiro. Prefixo com mais de 100 caracteres vira `res:` + 40 hex do sha256, para caber nos 120 de
  `InstanceActionBody.idempotency_key`. Comando terminado (`succeeded`, `failed`, `rejected`, `cancelled`) libera o
  `n` seguinte.
- **`on_missing` decide quem dispara.** `apply` pede o comando de convergência; `wait` o deixa com o rodízio e as
  portas do `_tick`; `ask` já não tem verbo. **Ler (`observe`) vale para os três**, porque é a resposta a `unknown` e
  não muda o alvo.
- **`uncertain` nunca se repete.** Só a prova (`reconcile`) ou uma pessoa o fecham.
- **`verify_resource`** relê e compara: `proved` só com `in_sync` sobre uma leitura (`VerifyStatus`); `unknown`
  continua `unknown`; divergir é `not_proved`, não falha.
- **`reconcile_resource`**:
  - só no hospedeiro: `CommandBus.hosts` é o `so_meu` (`instances.hosted_by` nulo ou o meu). De outro backend, nada é
    lido nem fechado (R11);
  - procura os `uncertain` do recurso pelas chaves dos seus verbos (`unsettled`);
  - fecha como `succeeded` (`settle`, pela máquina de estados do comando, com evento) **só** com `proved` e com a
    leitura **posterior** ao comando (`proof_time(observado) >= created_at`). Sem prova, o incerto continua incerto:
    nunca vira falha.
- `DespachoCommandBus.latest`/`unsettled` procuram por `substr(idempotency_key, 1, CAST(? AS INTEGER)) = ?`, e não
  `LIKE` (id de app pode ter `_`). O `CAST` é para o PostgreSQL; essa consulta em PostgreSQL está `not_run`.

### Por recurso

| `kind` | `apply` | `verify` (prova) | `reconcile` (prova de um `uncertain`) |
|---|---|---|---|
| `device.state` | `start`/`wake` por `pedir_ciclo_de_vida`; sem parâmetros | aparelho `online` e prontidão `ready` | a mesma leitura, **ao vivo** (memória do monitor, `proof_time=None`): no ar e pronto agora |
| `app.installation` | `app.install` com `{package, release_id}` da promovida entregável lida agora; `app.verify` com `{package}` | leitura do `pm` na promovida (`device_app_state`) | `device_app_state.verified_at` **depois** do comando e na promovida |
| `account.binding` | **nunca**: o `plan` só tem `ask`, e `apply` levanta `ValueError` | vínculo ativo relido | nada a fechar (`verbs=()`) |
| `app.session` | `session.connect` e `session.verify` com `{profile_id, app_id}` do perfil vinculado agora; só para app com provedor de sessão (`providers.py::tem_provedor_de_sessao`, hoje o Instagram) | sessão pronta relida | sessão verificada **depois** do comando (`instagram_sessions.verified_at`, ou `profile_accounts.session_verified_at`) |

Desafio, 2FA e CAPTCHA continuam com a pessoa: o `diff` de `app.session` os põe em `blocked` (ADR-009, ADR-029).

### `ResourceConvergence`

`modules/execution/application/resources.py::ResourceConvergence(providers)`:

- `read` e `report`: a leitura e o `PlanReport` (é o que o `RunService` usa, sem canal);
- `apply_next(specs, target, *, run_ref)`: percorre as linhas do relatório na ordem das portas (`KIND_ORDER`:
  aparelho, app, vínculo, sessão), pula `in_sync` e `held`, e aplica **a primeira** que não está certa. Uma coisa por
  aparelho e para; a próxima passada, relendo o mundo, segue dali. Linha sem verbo (em curso, pessoa, sem leitura)
  para a passada sem comando;
- `verify` e `reconcile`: um por recurso.

### O que não está ligado

- **Ninguém chama `apply_next`, `apply` nem `reconcile`** fora dos testes: nem o `_tick`, nem uma rota, nem o
  arranque. O comportamento em execução não mudou. `command_bus(s)` só é montado pelos testes.
- Pôr o `_tick` atrás disto é mudar quem dispara, e fica para quando o dono decidir. Até lá, as portas do `_tick`
  continuam sendo o "apply" destes recursos, e a capacidade do rodízio (quantos no ar) continua sendo do `_tick`.
- As diferenças da porta em relação ao design §7, registradas no docstring de `ResourceProvider`: `apply`, `verify` e
  `reconcile` recebem o `ResourceSpec` (e não só o `ResourceRef`); `apply` e `reconcile` são síncronos, como o
  despacho; `reconcile` devolve `ReconcileOutcome` (o que fechou e o que continua incerto), além da verificação.

Provas (`simulated`), em `backend/tests/test_aplicacao_de_recursos.py` (20 testes; banco de teste, dublês do runtime e
do canal, e o harness na porta 5640 com o `FakeEmulatorBackend`):

- `::test_apply_pede_uma_vez_e_a_segunda_passada_nao_abre_outro`,
  `::test_comando_terminado_libera_nova_tentativa_com_a_chave_seguinte`, `::test_uncertain_nao_se_repete`;
- `::test_on_missing_decide_quem_dispara`, `::test_ler_vale_mesmo_com_wait_e_ask`,
  `::test_apply_relê_e_nao_pede_o_que_o_recurso_nao_pede_mais`, `::test_vinculo_nunca_vira_comando`;
- `::test_verify_so_prova_com_leitura_positiva`, `::test_uncertain_so_fecha_com_prova_posterior_ao_comando`,
  `::test_reconcile_so_no_hospedeiro`;
- pelo despacho de verdade: `::test_apply_de_device_state_pelo_despacho_e_idempotente` (chave
  `res:android-01:device.state:-:start#1`, `requested_by = recursos`), `::test_canal_real_recusa_antes_de_gravar`,
  `::test_app_verify_pelo_despacho_de_verdade`, `::test_reconcile_pelo_despacho_so_no_hospedeiro_e_com_prova`.

## `mode=plan`: o `PlanReport` servido (fase H, parte 2)

`POST /api/runs` com `mode=plan` devolve o resumo de sempre **mais** `plan_report`
([contrato, adendo v0.24](../api-contract.md#adendo-v024-27092026--plan_report-em-modeplan-e-corpos-fora-de-modelspy)).
Com `mode=execute`, a resposta não muda.

- **Quem monta:** `api.py::create_run` chama `taskqueue/service.py::RunService.relatorio_de_recursos(run_id)` depois de
  `RunService.create`.
- **Resolve de novo.** A resposta sai antes de `_plan` terminar (ele roda em segundo plano), então a skill é resolvida
  outra vez (`self.skills.for_command`). A RESOLVE e a COMPILE são puras, e a leitura dos recursos é `SELECT` e
  memória, pelos providers **sem canal**: nada é aplicado nem gravado.
- **Alvos:** o aparelho e o perfil vinculado a ele agora (`RunService._alvos`).
- **`source`** diz de onde vieram os recursos:
  - `skill`: declarados em `spec.resources`;
  - `legacy_flow`: fluxo legado, que não declara recurso;
  - `needs_input`: a skill casou e precisa de resposta, ou não compilou;
  - `planner`: nada casou; o planejador escreve as etapas;
  - `error`: montar o relatório falhou (`373d45f`). A execução já existe e volta normalmente, com
    `plan_report: {source: "error", detail}` e o erro no log, em vez de um 500.
- **Armadilha:** fora de `skill`, `resources` vem vazio, e `summary.ready_to_run` vem `true`, porque não há linha a
  comparar. Quer dizer "nada foi declarado", não "está tudo pronto".
- **`RunPlan.resources`** (`modules/skills/infrastructure/run_planning.py`, aditivo): os recursos que a skill compilada
  declara, com os das filhas compostas (`ProcessGraph.resources`). Ficam fora do `Plan`, que continua byte a byte igual
  em `runs.plan`. Vazio no fluxo legado.
- **A foto:** `RunService._fotografar_recursos` grava `objectives.resource_plan` logo depois do `materialize`, nos dois
  modos, quando `RunPlan.resources` não é vazio (ver [a trilha](#a-trilha-colunas-da-045)).

Provas (`simulated`), em `backend/tests/test_plan_report_na_execucao.py` (harness na porta 5640, `FakeInstagram`,
`ig.abrir_conversa` publicada):

- `::test_mode_plan_serve_o_plan_report_sem_criar_comando`: `source: skill`, três linhas (`device.state`,
  `app.installation`, `app.session`), sessão `blocked` com `no_profile` e ação `ask`, nenhum comando nem saída no
  outbox, zero chamadas do planejador; montar de novo não muda 13 tabelas nem os eventos da execução; a foto;
- `::test_mode_plan_pelo_planejador_diz_que_nada_foi_declarado`, `::test_mode_execute_nao_traz_relatorio_mas_grava_a_foto`,
  `::test_relatorio_que_falha_nao_derruba_a_execucao_criada`.

## Máquinas de estado (fase K2)

`modules/execution/domain/states.py`, puro (sem banco e sem `app.models`): quatro tabelas imutáveis
(`MappingProxyType`) derivadas do comportamento **atual**, cada aresta com o chamador que a produz, e
`MaquinaDeEstados(nome, transicoes)` com `pode(de, para)`, `estados` e `terminais`. `pode` aceita enum ou texto.
Contexto: design §2.4; decisão: [ADR-038](../decisoes.md#adr-038--máquinas-de-estado-de-execução-formais-conferir-antes-de-impor).

| Máquina | Estados | Nasce | Resumo das saídas |
|---|---|---|---|
| `RUN` (`runs.status`) | 10 | `planning` (`create_run`) | `planning → needs_input, planned, running, failed, cancelled`; `needs_input → cancelled`; `planned → running, cancelled`; `running`/`paused` → pausar/retomar, `cancelling` e os três fechamentos; `cancelling → cancelled, completed, completed_with_issues` (e reafirma); `completed` e `failed` terminais |
| `OBJECTIVE` (`objectives.status`) | 7 | `pending` (`materialize`) | `pending → running, waiting_user, succeeded, failed, cancelled`; `running → waiting_user, uncertain, succeeded, failed, cancelled`; `waiting_user → pending, running, failed, cancelled`; `uncertain → pending, running, failed` (só por decisão de pessoa); `failed → pending` (e reafirma); `succeeded` e `cancelled` terminais |
| `STEP` (`steps.status`) | 11 | `pending` (`_insert_steps`) | a tabela que já existia em `taskqueue/states.py`, sem mudança |
| `ATTEMPT` (`attempts.status`) | 6 | `running` (`claim_step`) | `running → succeeded, failed, interrupted, uncertain, cancelled`; os cinco são terminais: nenhuma tentativa reabre |

- **Reafirmação (`x → x`) só onde o código a faz:** execução em `cancelling`, `completed_with_issues` e `cancelled`;
  objetivo `failed`. Liberar `pode(x, x)` em geral esconderia o erro que a tabela existe para mostrar.
- **Reabertura registrada como é:** `completed_with_issues → running, paused, completed, cancelling` e
  `cancelled → running, paused` (`recompute_run` reabre quando um item é retomado). É a reabertura que o design §2.4
  aponta; ela entra na tabela para ser revista no passo "impor", não aprovada.
- **A etapa continua imposta.** `taskqueue/states.py::STEP_TRANSITIONS` agora é derivada de
  `domain/states.py::STEP_TRANSITIONS` (uma fonte só), e `check_transition` segue barrando em
  `Repository.transition_step`.

**Fase "conferir e registrar".** `taskqueue/repository.py::Repository._conferir(maquina, de, para, ...)`:

- é chamado por `set_run_status`, `set_objective`, `finish_attempt` (**depois** da cerca: quem perdeu a posse não
  gravou, então não houve transição) e pela escrita direta de `skipped` em `revise_plan`;
- lê o estado anterior; `de` nulo (linha que não existe) não confere nada;
- fora da tabela: evento `log` de nível `warn` ("Transição de <máquina> fora da tabela: … (registrada, não
  bloqueada)", com `data: {state_machine, entity_id, from, to}`) e contagem em
  `repository.py::TRANSICOES_FORA_DA_TABELA` (`Counter` por `(máquina, de, para)`, no processo). **A transição
  acontece do mesmo jeito.**
- Não conferidos, porque estão na tabela por construção (`WHERE status=` na própria escrita): `claim_step`
  (`ready → running` e o nascimento da tentativa) e `Scheduler._reconciliar` (tentativa `running → interrupted`).
- **A contagem ainda não aparece em `/api/health`** nem em outra rota: em produção, só o evento `log` avisa.

**A suíte é a prova de que a tabela descreve o código.** O fixture automático
`tests/conftest.py::_transicoes_dentro_da_tabela` reprova qualquer teste que faça a contagem subir. Medido antes, com
um coletor sobre a suíte inteira (2270 testes): 3.585 transições reais em 55 pares (máquina, de, para), dois deles
fora da tabela, ambos atalho de arranjo de teste, corrigidos para o caminho real (`48e76ae`):

- `tests/test_capabilities.py` chamava a porta de política sem o `_hold` que o despacho aplica (objetivo
  `running → pending`);
- `tests/test_credenciais_da_execucao.py` levava a execução de `planned` direto a `completed_with_issues`; agora passa
  por `running`.

Testes da tabela: `backend/tests/test_maquinas_de_estado.py` (15), entre eles `::test_vocabulario_igual_ao_do_enum`
(estado novo no enum sem linha na tabela reprova), `::test_todo_estado_e_alcancavel_do_nascimento`,
`::test_mesmo_estado_so_onde_esta_escrito`, `::test_a_fila_impoe_a_mesma_tabela_de_etapa_do_dominio`,
`::test_execucao_fora_da_tabela_avisa_conta_e_nao_bloqueia` e `::test_objetivo_fora_da_tabela_avisa_e_nao_bloqueia`.
Tudo `simulated`.

**O que falta para impor** (proposto, escrito em `_conferir` e no docstring do domínio):

- um ciclo sem aviso na suíte e **na produção**, o que exige a contagem visível (em `/api/health` ou métrica) e o
  código implantado;
- trocar o aviso por `InvalidTransition` em `set_run_status`, `set_objective` e `finish_attempt`, como a etapa já faz;
- rever cada aresta de reabertura, para que reabrir execução terminal em `recompute_run` seja uma aresta decidida, e
  não efeito colateral.

## O que falta

- **Recursos:** ligar o `apply` e o `reconcile` no `_tick` (decisão do dono: muda quem dispara); a refoto de
  `objectives.resource_plan` no despacho; os demais campos do §14.2 no relatório (etapas, efeitos, aprovações, custo
  estimado). Até a ligação, as portas do `_tick` continuam sendo o "apply" desses recursos.
- **Máquinas de estado:** a contagem em `/api/health` e o passo "impor" ([acima](#máquinas-de-estado-fase-k2)).
- **Estratégias:** `RecipeExecutionStrategy`, `AiActorStrategy` e `HumanStrategy` atrás de `ExecutionStrategy`, e a
  ordem vinda de `origin.strategies`.
- **Trilha:** `runs.flow_id` na execução de fluxo adotado (fase J); a conferência de que uma recompilação dá o mesmo
  `runs.plan`.
- **Identidade de receita por capability** (fase K): hoje o mesmo `OPEN_THREAD` avulso e composto não compartilha
  receita.
- **Resolução:** as etapas 3 e 4 com provedor real (classificador e desempate por IA), assíncronas, só no `_plan` e
  com aviso de custo (proposto). A trilha das etapas e o `method` não são gravados na execução; só a rota
  `POST /api/skills/resolve` os mostra.
- **Provas:** G, I, H parte 2 (em especial a consulta com `substr(…, CAST(? AS INTEGER))`) e K2 em PostgreSQL;
  `apply` e `reconcile` com aparelho real; execução de skill numa conta real do Instagram.

## Precondição de deploy

A execução grava nas colunas da 045 **mesmo com as skills e os fluxos desligados**:

- `Repository._insert_steps` inclui `steps.skill_id`, `skill_version`, `node_id` e `strategy` no `INSERT`;
- `Repository.note_attempt_strategy` roda em toda tentativa;
- `Repository.add_usage` inclui `ai_calls.attempt_id`;
- `Scheduler._learn_flow` lê `runs.skill_id`, e `FlowStore.learn_from_run` consulta `skill_versions` (042).

Por isso as migrações 042–046 precisam estar aplicadas antes de o código da fase G subir, com ensaio numa cópia do
banco (ADR-020), a 046 verde em PostgreSQL (`workflow_dispatch`) e autorização do dono.

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| Skill publicada → plano compilado, sem planejador | implementado | `simulated` (`backend/tests/test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`; `test_habilidades_na_execucao.py::test_skill_publicada_vira_o_plano_compilado_com_os_valores_do_comando`) | `run_planning.py`, `RunService._plan` |
| Composta: dependência e alvo da leitura | implementado | `simulated` (`test_fatia_abrir_conversa.py::test_composta_le_a_conversa_que_a_filha_abriu`; `test_habilidades_na_execucao.py::test_composta_liga_a_leitura_a_conversa_aberta_pelo_filho`) | compilador, `_insert_steps` |
| Filha desabilitada: `needs_input`, sem plano | implementado | `simulated` (`test_fatia_abrir_conversa.py::test_filha_desabilitada_poe_a_composta_em_needs_input_sem_plano`) | `RunService._skill_sem_plano` |
| Pergunta da RESOLVE: `needs_input` sem plano e sem planejador; quatro chamadores coerentes | implementado | `simulated` (`backend/tests/test_intencao_chamadores.py::test_os_tres_chamadores_coerentes_para_a_mesma_frase`, `::test_empate_na_execucao_pergunta_com_as_opcoes_e_nao_grava_skill`) | `SkillRunPlanner.resolve_intent`, `RunService._skill_sem_plano` |
| Etapas por IA da RESOLVE | porta com provedor nulo | `not_run` | `intent_ports.py` |
| I em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Desligado = planejador; fluxo legado igual | implementado | `simulated` (`::test_com_as_habilidades_desligadas_o_comando_vai_ao_planejador`, `::test_fluxo_legado_grava_a_trilha_de_sempre_e_o_hash`) | `CompositeSkillRegistry`, `legacy_plan` |
| Trilha da 045 | implementado | `simulated` (`test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`; `resource_plan` em `test_plan_report_na_execucao.py::test_mode_plan_serve_o_plan_report_sem_criar_comando`) | `note_run_skill`, `_insert_steps`, `note_attempt_strategy`, `add_usage`, `_fotografar_recursos` |
| VERIFY pelo provider, com o desvio da marca de falha | implementado | `simulated` (`test_habilidades_na_execucao.py::test_a_prova_local_pela_porta_e_a_mesma_de_antes`, `::test_a_unica_diferenca_e_a_marca_de_falha_visivel`) | `StepExecutor._prova_local` |
| Aprendizado de fluxo não duplica comando | implementado | `simulated` (`test_habilidades_na_execucao.py::test_fluxo_nao_se_aprende_de_skill_nem_do_comando_que_uma_skill_publicada_cobre`) | `_learn_flow`, `learn_from_run` |
| `diff`/`plan` dos quatro recursos; `unknown` nunca certo | implementado | `simulated` (`backend/tests/test_recursos_declarativos.py::test_unknown_nunca_vira_certo_nem_convergencia`, `::test_idempotencia_mesma_entrada_mesmo_plano_e_em_sincronia_nada`, `::test_drift_nao_lido_nao_tem_acao_em_nenhum_tipo`, `::test_todo_verbo_e_de_um_comando_que_existe_e_tem_risco_descrito`) | `shared/resources.py`, `*/domain/resources.py` |
| Leitura sem efeito dos quatro providers | implementado | `simulated` (`backend/tests/test_leitura_de_recursos.py`, inclusive `::test_device_state_le_o_device_runtime_de_verdade` sobre o harness) | `*/infrastructure/*.py` |
| `PlanReport` determinístico, sem gravar | implementado | `simulated` (`backend/tests/test_plan_report.py::test_relatorio_da_skill_abrir_conversa_sobre_o_parque`, `::test_mesma_entrada_em_outra_ordem_da_o_mesmo_relatorio`, `::test_par_nao_lido_e_desconhecido_sem_acao_e_listado_como_risco`) | `plan_report.py` |
| `apply`/`verify`/`reconcile` de recurso pelo canal de comandos: uma vez por chave, `uncertain` não se repete, `on_missing` decide quem dispara, reconcile só no hospedeiro e com prova posterior | implementado, **não ligado** ao `_tick` | `simulated` (`backend/tests/test_aplicacao_de_recursos.py`, 20 testes, inclusive `::test_apply_de_device_state_pelo_despacho_e_idempotente` e `::test_reconcile_pelo_despacho_so_no_hospedeiro_e_com_prova` sobre o harness) | `shared/commands.py`, `shared/convergence.py`, `command_bus.py`, os quatro providers |
| `apply`/`reconcile` chamados pelo `_tick`; com aparelho real | não feito | `not_run` | decisão do dono |
| `mode=plan` com `plan_report`; `source: error` sem 500; `objectives.resource_plan` no `materialize` | implementado | `simulated` (`backend/tests/test_plan_report_na_execucao.py`, 4 testes) | `api.py::create_run`, `RunService.relatorio_de_recursos`, `_fotografar_recursos` |
| Refoto de `resource_plan` no despacho | não feito | `not_run` | design §11 |
| Máquinas de estado de execução, objetivo e tentativa: conferir e registrar, sem bloquear | implementado | `simulated` (`backend/tests/test_maquinas_de_estado.py`, 15 testes; fixture `tests/conftest.py::_transicoes_dentro_da_tabela` na suíte inteira) | `modules/execution/domain/states.py`, `Repository._conferir` |
| Máquinas de estado impostas; contagem em `/api/health` | não feito | `not_run` | próximo passo (ADR-038) |
| H parte 2 e K2 em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| H parte 1 em PostgreSQL | implementado | `simulated` (CI run `36324634678` em `793fe00`) | CI `workflow_dispatch` |
| G em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Execução de skill numa conta real | implementado | `not_run` (exige autorização) | — |
