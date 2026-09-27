# Domínio: execution (o ciclo de uma execução)

Como um comando vira etapas executadas e comprovadas, fase a fase, e o que as fases G e H (parte 1) da evolução
arquitetural acrescentaram a esse caminho ([design](../design/evolucao-arquitetural.md) §11, §14 e §18). A skill que
entra no ciclo está em [skills](skills.md); o compilador que a baixa para `Plan`, em
[runtime de skills](../skill-runtime.md); a porta de VERIFY, em [capabilities](capabilities.md). Decisões:
[ADR-035](../decisoes.md#adr-035--resourcespec-declarativo) (recursos declarativos) e
[ADR-036](../decisoes.md#adr-036--receitas-como-estratégia-de-execução) (receitas como estratégia).

Caminhos relativos a `backend/app/`, salvo indicação.

- Fase G: `3a1da01`, `aa3575b`, `4e210c4` e `fb30ef3`, integrados em `0b736f3`; correção de receita em `eb9ba02`.
- Fase H, parte 1: `14362ee`, `37752ed` e `ab211e6`, integrados em `1e69d02`.
- Fase I (resolução de intenção): `00633d5` e `0795cc7`, integrados em `21b1fff`.

**Estado (27/09).**

- **A fiação da G está feita.** Com `skills.enabled` ligado (o padrão é desligado), uma skill publicada vira o plano
  da execução pelo compilador, e o executor de sempre a roda, com a trilha da 045. Prova `simulated` (harness na porta 5640, `FakeInstagram`,
  `CountingProvider`).
- **H parte 1 é só leitura.** Tipos, `diff`/`plan` puros, leitura dos quatro recursos e o `PlanReport`. **Nada disso
  é chamado pelo runtime**: nem `_plan`, nem o `_tick`, nem uma rota.
- **A RESOLVE da I está ligada.** O `_plan` resolve pelo `IntentResolver`: com `skills.enabled`, parâmetro vazio,
  valor que não serve ao tipo e empate entre skills viram pergunta (`needs_input`, sem plano). As etapas por IA
  (semântica e desempate) são portas com o provedor nulo: `not_run`.
- Suíte SQLite no merge A–H: 2115/2115; no branch da fase I: 2252/2252 (`simulated`). I em PostgreSQL: `not_run`.
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
| **PLAN** | persistir e expor | `Repository.save_plan` e `Repository.materialize` → `_insert_steps`. `RunCreate.mode == "plan"` para em `planned` | `_insert_steps` copia `PlanStep.origin` para `steps` (G). `PlanReport` existe como função pura (H), não servida |
| **PREFLIGHT** | recusar ou esperar antes de gastar | `RunService.pre_voo`; portas do `Scheduler._tick` (`_portas_do_app`); `Scheduler.policy_gate` antes de `claim_step` | nada ligado. A leitura dos recursos (H) existe e não é chamada |
| **APPLY** | nó → estratégia | `Repository.claim_step` → `StepExecutor.run_step`/`_run_step`; `Repository.log_intent` antes de cada ação | registro da estratégia exercida em `attempts` (G, `StepExecutor._registrar_estrategia`). A ordem **não** vem de `origin.strategies` |
| **VERIFY** | prova observada | `StepExecutor._verify`: determinística → prova local → modelo | a prova local passa por `CatalogCapabilityProvider.verify` (G, `StepExecutor._prova_local`). O sucesso continua gravado pelo executor |
| **RECONCILE** | efeito sem desfecho | `Repository.commit_state`; `Scheduler._reconciliar`; `SocialService.reconcile_pending_effects`; `CommandStore.reconcile_after_restart`; `ReleaseService.reconcile_after_restart`; `commands/reconciler.py` | nada. `CatalogCapabilityProvider.reconcile` levanta `NotImplementedError` |
| **COMPLETE** | desfecho | `Scheduler._apply`, `_maybe_complete`, `Repository.recompute_run`, `Scheduler._learn_flow` | `_learn_flow` e `FlowStore.learn_from_run` pulam execução de skill (G) |

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
| `objectives.resource_plan` | **ninguém** | proposto: a foto dos recursos resolvidos por aparelho (design §11) |

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
diferença e o plano, sem aplicar nada ([ADR-035](../decisoes.md#adr-035--resourcespec-declarativo)).

### O modelo

`shared/resources.py`, no kernel, porque três contextos pares implementam os tipos e a execução consome os quatro:

- `ResourceKind`: `device.state`, `app.installation`, `account.binding` e `app.session` (os mesmos da DSL).
- `ResourceSpec(ref, desired, on_missing)`; `ResourceSpec.of(kind, target, desired, on_missing)` normaliza e recusa
  tipo ou `on_missing` desconhecido (`ValueError`).
- `OnMissing`: `wait`, `apply` e `ask`. Hoje `wait` e `apply` planejam a mesma convergência; `ask` troca a
  convergência por uma pessoa.
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
`SELECT` ou memória; `diff` e `plan` são as funções puras do domínio de cada contexto. Não há `Protocol`
`ResourceProvider` em código: os quatro cumprem a mesma forma, e a porta é proposta.

| `kind` | Classe | Lê | Converge | Lê de novo (`unknown`) |
|---|---|---|---|---|
| `device.state` (`online`) | `modules/fleet/infrastructure/device_state.py::DeviceStateProvider(db, runtimes)` | `instances.desired_state` e, do runtime em memória (`fleet/application/ports.py::DeviceRuntimeView`), `state`, `state_detail`, `readiness_phase`, `store`, `external`, `worker_verbs`, `snapshot_valid` | `start` (parado, parado por decisão, hibernado sem snapshot) e `wake` (hibernado com snapshot) | nenhum comando lê o aparelho: `unobserved` fica sem ação |
| `app.installation` (`release: promoted`) | `modules/applications/infrastructure/app_installation.py::AppInstallationProvider(db)` | `apps` (por `AppRepository.obter`), `instances.app_id`, `app_releases`, `device_app_state` | `app.install` (`missing`, `outdated`, `other_build`, `abandoned_version`) | `app.verify` (`unobserved`, `unrecognized_state`, `no_outcome`, `no_version`) |
| `account.binding` (`bound`) | `modules/identity/infrastructure/account_session.py::AccountBindingProvider(db)` | `device_profile_bindings` ativo e `instagram_profiles.username` | nunca: vincular é de pessoa (`always_ask=True`) | — |
| `app.session` (`session: ready`, `account: bound_profile` opcional) | `account_session.py::AppSessionProvider(db, *, tem_provedor_de_sessao, session_max_age_s, unknown_retry_cap, agora)` | `apps`, vínculo ativo, `instagram_profiles` (status, `offline_policy`), `instances` (localidade), `instagram_credentials.status`, `instagram_sessions`, `profile_accounts` | `session.connect` (`logged_out`, `relocated`), só com credencial utilizável no cofre | `session.verify` (`unobserved`, `other_device`, `stale`, `account_unproven`). `account_unknown` (app sem provedor de sessão) fica sem ação |

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

## O que falta

- **Recursos:** `apply` (só por `commands`, R10), `verify` e `reconcile` (só pelo hospedeiro) dos quatro tipos; a
  porta `ResourceProvider` como `Protocol`; o escritor de `objectives.resource_plan`. Até lá, as portas do `_tick`
  continuam sendo o "apply" desses recursos.
- **`mode=plan` servindo o `PlanReport`:** hoje `mode=plan` só para em `planned`, com o plano salvo.
- **Estratégias:** `RecipeExecutionStrategy`, `AiActorStrategy` e `HumanStrategy` atrás de `ExecutionStrategy`, e a
  ordem vinda de `origin.strategies`.
- **Trilha:** `runs.flow_id` na execução de fluxo adotado (fase J); a conferência de que uma recompilação dá o mesmo
  `runs.plan`.
- **Identidade de receita por capability** (fase K): hoje o mesmo `OPEN_THREAD` avulso e composto não compartilha
  receita.
- **Resolução:** as etapas 3 e 4 com provedor real (classificador e desempate por IA), assíncronas, só no `_plan` e
  com aviso de custo (proposto). A trilha das etapas e o `method` não são gravados na execução; só a rota
  `POST /api/skills/resolve` os mostra.
- **Provas:** G e I em PostgreSQL; execução de skill numa conta real do Instagram.

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
| Trilha da 045 (menos `resource_plan`) | implementado | `simulated` (`test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`) | `note_run_skill`, `_insert_steps`, `note_attempt_strategy`, `add_usage` |
| VERIFY pelo provider, com o desvio da marca de falha | implementado | `simulated` (`test_habilidades_na_execucao.py::test_a_prova_local_pela_porta_e_a_mesma_de_antes`, `::test_a_unica_diferenca_e_a_marca_de_falha_visivel`) | `StepExecutor._prova_local` |
| Aprendizado de fluxo não duplica comando | implementado | `simulated` (`test_habilidades_na_execucao.py::test_fluxo_nao_se_aprende_de_skill_nem_do_comando_que_uma_skill_publicada_cobre`) | `_learn_flow`, `learn_from_run` |
| `diff`/`plan` dos quatro recursos; `unknown` nunca certo | implementado | `simulated` (`backend/tests/test_recursos_declarativos.py::test_unknown_nunca_vira_certo_nem_convergencia`, `::test_idempotencia_mesma_entrada_mesmo_plano_e_em_sincronia_nada`, `::test_drift_nao_lido_nao_tem_acao_em_nenhum_tipo`, `::test_todo_verbo_e_de_um_comando_que_existe_e_tem_risco_descrito`) | `shared/resources.py`, `*/domain/resources.py` |
| Leitura sem efeito dos quatro providers | implementado | `simulated` (`backend/tests/test_leitura_de_recursos.py`, inclusive `::test_device_state_le_o_device_runtime_de_verdade` sobre o harness) | `*/infrastructure/*.py` |
| `PlanReport` determinístico, sem gravar | implementado | `simulated` (`backend/tests/test_plan_report.py::test_relatorio_da_skill_abrir_conversa_sobre_o_parque`, `::test_mesma_entrada_em_outra_ordem_da_o_mesmo_relatorio`, `::test_par_nao_lido_e_desconhecido_sem_acao_e_listado_como_risco`) | `plan_report.py` |
| `apply`/`reconcile` de recurso, `mode=plan` com relatório | não feito | `not_run` | fase H, parte 2 |
| H parte 1 em PostgreSQL | implementado | `simulated` (CI run `36324634678` em `793fe00`) | CI `workflow_dispatch` |
| G em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Execução de skill numa conta real | implementado | `not_run` (exige autorização) | — |
