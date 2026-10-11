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
- **Máquinas de estado (K2; impostas no 15.15 F7).** Execução, objetivo e tentativa têm tabela no domínio. Fora dela, o
  `Repository` registra (evento `warn` e contagem) e **recusa** (`InvalidTransition`, antes de gravar); a etapa já era
  imposta. Em K2 só avisava; ver [Máquinas de estado](#máquinas-de-estado-fase-k2).
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
| **PLAN** | persistir e expor | `Repository.save_plan` e `Repository.materialize` → `_insert_steps`. `RunCreate.mode == "plan"` para em `planned` | `_insert_steps` copia `PlanStep.origin` para `steps` (G). `mode=plan` devolve o `plan_report` (H2, `RunService.relatorio_de_recursos`), e `_plan` grava `objectives.resource_plan` logo depois do `materialize` (H2, `RunService._fotografar_recursos`). **Onda B (ADR-040):** o planejador recebe `PlanRequest.available_data` (a lista de dados da persona comum aos aparelhos, `modules/identity/application/available_data.py::common_data`: nomes, nunca valores); `instances[].variables` fotografa os não sigilosos por aparelho e `materialize` os resolve (`{perfil_email}`, `{conta_<app>_usuario}`) junto com `{instance_id}`/`{run_id}`/`{account_label}` |
| **PREFLIGHT** | recusar ou esperar antes de gastar | `RunService.pre_voo`; portas do `Scheduler._tick` (`_portas_do_app`); `Scheduler.policy_gate` antes de `claim_step` | recursos: nada ligado (a leitura da H só alimenta o relatório e a foto; o `apply` da H2 existe e não é chamado). **Onda B:** `pre_voo(secret_names=…)` confere os `requires.secrets` da skill casada (`RunPlan.secrets` ← `ProcessGraph.secrets`, `RunService._segredos_exigidos`) contra as contas da persona de cada aparelho (`missing_secrets`: senha guardada, consentida, não recusada, de app sem `SessionProvider`); o que falta recusa o aparelho com `missing_credential`, na criação e ao iniciar |
| **APPLY** | nó → estratégia | `Repository.claim_step` → `StepExecutor.run_step`/`_run_step`; `Repository.log_intent` antes de cada ação | registro da estratégia exercida em `attempts` (G, `StepExecutor._registrar_estrategia`). A ordem **não** vem de `origin.strategies`. **Onda B:** o ator e o verificador recebem `StepContext.available_data` do aparelho; `type_secret(name)` é resolvido por `StepExecutor.preenchedor` pelo perfil do **objetivo** (`resolve_secret`): exige `consent_at`, digita só campo de senha, só no pacote da conta e no `host` da conta, grava `last_used_at` e uma decisão com o nome da conta; `tem_credencial` de `pede_intervencao_humana` vale por app e etapa (`typable_secret_for`; senha sem consentimento → `waiting_user` `consentimento_pendente`); `open_url` aceita os hosts das contas (`ToolContext.allowed_hosts`). A execução não carrega credencial (`run_secrets` sem escritor) |
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
  `POST /api/flows/match` e `POST /api/skills/resolve` (fase I) usam `AppState.skill_planner`. A estimativa do painel,
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

`RunService.apps_exigidos` e `POST /api/flows/match` tratam a pergunta como "nada casou": `[]` e `null`.

### A pergunta sem resposta expira (29.50)

Uma execução em `needs_input` há `NEEDS_INPUT_EXPIRA_H` (24 h) sem resposta é encerrada pelo sistema.

- Quem faz: `RunService.expirar_sem_resposta(agora)`. O laço é `AppState._expiracao_loop`: a primeira volta é na
  subida, depois a cada `EXPIRACAO_INTERVALO_S` (10 min). Roda só no líder da trava `retencao`, porque é faxina do
  mesmo tipo.
- O relógio é a ENTRADA em `needs_input`, o `run.updated` daquela transição, e não a criação:
  - a retenção poupa todo evento de execução sem `finished_at`, e `needs_input` não tem `finished_at`;
  - `needs_input` só sai para `cancelled`, então o último `run.updated` é o da entrada;
  - sem evento nenhum, vale `created_at`.
- O que muda na execução:
  - fecha como `cancelled`, pelo mesmo caminho do cancelamento antes de iniciar (`_cancelar_antes_de_iniciar`:
    etapas abertas, objetivos e aprovações);
  - o `status_detail` é o motivo humano: "Sem resposta em 24 h: a pergunta expirou e a execução foi encerrada pelo
    sistema. Para seguir, faça o pedido de novo.";
  - o `data` do `run.updated` da transição leva `expirada: {motivo: "sem_resposta", horas: 24, desde: <entrada>}`;
  - não grava o sinal `cancelou_execucao`: ninguém fez o gesto (ADR-054).
- A resposta que chega no meio da varredura ganha. O cancelamento é condicional (`UPDATE … WHERE status='needs_input'`),
  e o "Respondida: continua na execução …" da sucessora fica.
- Pedido recorrente: a ocorrência da execução expirada fecha como `cancelada`, como qualquer `cancelled` sem objetivo
  iniciado e sem a intenção do prazo de início (`pedidos/domain/fechamento.py`).
- Prova `simulated`: `backend/tests/test_needs_input_expira.py`.

### A pergunta parada vence sozinha (31.43)

O prazo da 29.50 deixa de ser a constante e passa ao config (`execucao.pergunta_vence_h`, 24 h por padrão; a chave
`execucao.vencimento_ligado`, ligada de fábrica, desliga os dois vencimentos). `NEEDS_INPUT_EXPIRA_H` fica como o padrão.

- Segundo caso: o objetivo em `waiting_user` de uma execução sem trabalho automático (`awaiting_person` desde o 29.93;
  antes, `completed_with_issues`, onde `recompute_run` deixava a execução "para permitir retomada"). Ele ficava `waiting_user` para sempre; no central eram 22.
  `RunService.vencer_objetivos_parados(agora)` roda no mesmo laço (`_expiracao_uma_vez`) e o fecha PELO SISTEMA:
  - o relógio é o mais tardio entre `objectives.finished_at` (a entrada em `waiting_user`) e `runs.finished_at`:
    nunca adianta, e a retomada de outro item da execução recomeça o prazo;
  - o objetivo vira `cancelled`, com as etapas abertas e as aprovações pendentes dele; o `status_detail` é "Sem resposta
    em 24 h: o pedido venceu e foi encerrado pelo sistema. Para seguir, faça o pedido de novo.";
  - `recompute_run` deriva o status da execução, que segue `completed_with_issues`: não é cancelamento da pessoa, sem
    `cancel_requested` e sem o sinal `cancelou_execucao`;
  - a escrita é condicional ao objetivo ainda em `waiting_user` e à execução ainda terminal: a retomada no meio da
    varredura ganha. Execução ainda viva (`running`, `paused`, `needs_input`) não é tocada;
  - fecha o estoque existente no primeiro ciclo, sem escrita manual no banco.
- A marca para máquina, de formato fixo (a Canais a lê por um adaptador), vai nos dois casos em `dados.vencimento` com
  exatamente `regra` ("31.43"), `motivo` ("vencido_sem_resposta"), `horas` e `desde`: no `run.updated` (pergunta; o
  `expirada` da 29.50 continua ao lado) e no `objective.updated` (objetivo parado).
- Só muda estado: nada responde a pergunta, digita, toca o aparelho ou chama IA. Pedido de senha também só são
  encerrados. O "Bloqueado: …" que o objetivo deixou no aparelho sai se nenhum outro objetivo dele espera uma pessoa; os
  outros avisos ficam. O objetivo vencido deixa de contar como aberto (`api._OBJETIVO_ABERTO`).
- Prova `simulated`: `backend/tests/test_pergunta_vence.py`. `not_run`: o primeiro ciclo no central depois do deploy.
- 31.50 (revisão do deploy 30):
  - a guarda e o cancelamento do objetivo vencido são UMA transação: a retomada no meio não é atropelada, e uma falha
    no meio não deixa etapas canceladas com o objetivo esperando;
  - `pergunta_vence_h` tem piso de 1 h: um "0.05" no lugar de "5" encerraria em minutos o que espera uma pessoa;
  - carência ao ligar: a marca `vencimento_ligado_desde` (`settings`) é gravada quando o vencimento é visto ligado e
    apagada quando desligado; nada vence antes da marca mais o prazo. Antes, ligar venceu de uma vez os 21 que já
    estavam parados (primeira volta do deploy 30);
  - lembrete: a mesma volta (`RunService.lembrar_antes_de_vencer`) emite `pendencia.vence_em` UMA vez por item quando
    faltam 2 h ou menos (`LEMBRETE_ANTES_H`). Os dados dizem o que é, o aparelho, a ação de catálogo, a chave da etapa
    e o `vence_em`, nunca o comando nem o título. O texto ao dono é do montador dos avisos (28.31, Canais);
  - `vence_em` (o mais tardio entre a espera e a marca, mais o prazo) vem no `RunSummary` em `needs_input`, no
    `Objective` parado e na lista de aprovações pendentes (que vencem com o objetivo).

### Plano de fluxo salvo prova com o catálogo atual (31.50 a)

OBSERVADO (r-20261004195451-7d3527, 04/10): o plano veio de um fluxo salvo antes do #278, que congelou a pós-condição
`model_judged` antiga do OPEN_MAIL_INBOX, e a etapa foi pelo ator e pelo juiz (13 chamadas). Num plano de fluxo
(`planner.provider == "fluxo"`, inclusive a prova de fluxo), a etapa de catálogo do app do plano recebe a pós-condição
ATUAL do catálogo (`planning.capabilities.atualizar_pos_condicoes`), com uma decisão registrada; a `local_proof` já vinha
do catálogo atual pela ação. No Outlook, a caixa de entrada se prova sem IA pela lista E pelo título
(`selector:id=conversation_list & text==Inbox`, OBSERVADO no android-01); sem o título, o juiz decide. O rejulgamento do
17.10 vai sem a dica da tela (31.46), para continuar uma segunda opinião independente.

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

### O planejador: livre, por catálogo ou entre apps (item 24.1, ADR-058)

Quando nada casa, `RunService._catalogos` escolhe o que vai ao planejador. Os **candidatos** são os apps dos aparelhos
mais os apps que o comando cita fora das aspas, pelo nome do cadastro ou pelo rótulo do manifesto
(`planning/apps_do_comando.py::apps_citados`). "No Outlook" acha o cadastro "Microsoft Outlook".

**Apelidos e acentos (item 31.29, 04/10/2026).** O casamento ignora acento e caixa. Os apps de SISTEMA também têm
apelidos de MAIS DE UMA palavra, sem ambiguidade, por pacote (`APELIDOS_POR_PACOTE`). `com.android.settings` atende por
"Configurações do aparelho/telefone/celular/sistema", "Ajustes do aparelho/telefone/sistema" e "Android Settings".
"Configurações", "Ajustes" e "Settings" SOZINHOS não são apelido, porque sequestrariam comando do Instagram ("faça uns
ajustes na legenda") — revisão da Android. O padrão para o Instagram quando nenhum app é citado NÃO mudou, e é de
propósito.

Motivo medido: na linha de base de 04/10, 11 de 12 comandos com "Configurações" sem "do Android" não casaram app
nenhum. Eles caíram no Instagram, e o planejador, com o catálogo dele, pediu a pessoa (`needs_input`).

| Situação | Planejador | O que recebe |
|---|---|---|
| nenhum candidato tem catálogo | livre (`PLANNER_SYSTEM`) | todos os apps; `catalog` e `catalogs` vazios |
| um candidato só, com catálogo, sem site pedido | por catálogo (`PLANNER_CAPABILITY_SYSTEM`), como antes | `catalog` do app |
| o comando cita outro app ou pede um site (`pede_site`), e algum candidato tem catálogo | **entre apps** (`PLANNER_MULTIAPP_SYSTEM`) | `catalogs = {app_id: catálogo}` de todos os candidatos com catálogo; `apps` com esses e os apps sem catálogo |

- **Antes do 24.1**, citar outro app ou um site punha o plano inteiro no caminho livre: a etapa com efeito no
  Instagram saía sem ação do catálogo, e a porta de política a recusava (R1, R2).
- **O app do aparelho é candidato mesmo quando o comando cita outro.** "Leia o e-mail no Outlook e curta o post de
  @x" não nomeia o Instagram e precisa dele.
- **Um app com catálogo que não é candidato fica fora de `apps`**, para não virar app de etapa livre.
- **No entre apps, cada etapa diz o seu `app_id`** (`parsing.py::_MultiPlanOut`), e `catalog_plan_from_json` a
  monta pelo app dela:
  - num app com catálogo, é **ação do catálogo daquele app** (`capabilities.py::montar_etapa`). A herança de
    argumento (`herdar_argumentos`) roda por app, então a legenda de um post não passa para outro app;
  - num app sem catálogo, é **etapa livre** (`livre`), com as mesmas regras e limites do plano livre (`_etapa_livre`);
  - etapa livre num app com catálogo, app fora da lista, ação que não existe e etapa sem descrição viram
    **pergunta** (`missing`) e zeram as etapas, como no `compose`. A regra da porta de política não muda (T19).
- **O app do plano** é o principal que o modelo disse, se alguma etapa roda nele; senão, o da primeira etapa. Etapa
  no app do plano fica com `app_id` nulo, como no plano livre e no compilador de skills.
- **O prompt entre apps** reusa, recortados, os trechos de regra dos dois planejadores de sempre (`prompts.py::_trecho`).
  `PLANNER_SYSTEM` e `PLANNER_CAPABILITY_SYSTEM` seguem iguais byte a byte, e o custo dos comandos de um app só não
  muda. As ações vão sem dicas nem seletores. O prompt diz que código, senha ou token lido num app nunca é usado em
  outro (D3).
- **`Plan.required_apps` sempre preenchido** (R6), por `parsing.py::apps_do_plano`: os apps em que as etapas rodam,
  não a lista de candidatos. Etapa sem app e plano sem app contam o app do aparelho. Vale para o livre, o por
  catálogo, o entre apps e o simulado. `RunService._plan` completa a lista do provedor que não a preenche.
- O provedor simulado planeja entre apps por regras (`SimulatedProvider._plan_multiapp`): o plano de cada app citado,
  um depois do outro. Com um app só citado, sai o plano de antes.
- **Prova.** `tests/test_planejador_entre_apps.py` é `simulated`, com provedor simulado e clientes falsos da Anthropic
  e do OpenAI. Que a API real aceite o esquema estrito `_MultiPlanOut` está `not_run`, e fica para o 24.9. A porta de
  política por etapa é o 24.2; o roteamento por conjunto de apps, o 24.5; a execução com troca de app, o 24.4.

### A porta de política pelo app da etapa (item 24.2, ADR-058)

`gates.py::Portoes._policy_gate` (o `AppState._policy_gate` delega) julga cada etapa pelo **app dela**, resolvido pelo mesmo `Scheduler._app_context` da
execução: o `app_id` da etapa, senão o do plano, senão o do aparelho. Num comando entre apps, a etapa do Outlook e a
do Instagram passam cada uma pela pergunta do seu app. A regra não muda (T19); nenhum efeito novo é liberado.

| App da etapa | Ação do catálogo do app da etapa | Efeito externo | Veredito |
|---|---|---|---|
| com catálogo | sim | qualquer | política, limite, frota, rascunho e aprovação da ação, como antes |
| com catálogo | não (nenhuma, ou uma chave que esse catálogo não tem) | sim | recusa `manual_only`: "sem a ação do catálogo" |
| com catálogo | não | não | segue |
| sem catálogo | não se aplica | qualquer | segue pela IA livre, sozinho ou num comando entre apps |

- **O que mudou no 24.2:** uma capability que o app da etapa não tem conta como nenhuma. Antes, `cap is None`
  liberava a etapa com o efeito que tivesse: uma chave inventada numa etapa do Instagram passava por fora da
  política. Agora cai na recusa do item 13.2, e o motivo cita a chave estranha.
- A ação do Instagram numa etapa do Outlook não é julgada pelo catálogo do Instagram: no app da etapa ela não existe.
  Desde o 12.3 o Outlook tem catálogo (só leitura), então a etapa com efeito cai na recusa do item 13.2 (antes, sem
  catálogo, seguia como etapa livre do Outlook).
- Etapa com efeito num app sem catálogo, dentro de um comando que também usa um app com catálogo, segue livre. É o
  decidido em `test_modo_treinamento.py::test_portao_recusa_efeito_sem_acao_num_app_com_catalogo`. Fechá-la exige
  decisão (ADR novo), não este item.
- **Prova.** `tests/test_porta_de_politica_por_app.py` é `simulated` (harness, banco de teste, sem aparelho nem IA).
  Cobre a mistura Outlook (leitura) + Instagram (efeito sem capability), recusada na etapa de efeito.

### A mesma porta no planejamento (RA-7, 03/10/2026)

A regra da tabela acima mora em `planning/capabilities.py::efeito_fora_do_catalogo` e vale em dois lugares: no
despacho (`_policy_gate`, a trava de sempre, com a mesma frase) e em `RunService._plan`, para TODO plano
(planejador, fluxo e skill), antes de qualquer etapa existir.

- O app da etapa é resolvido como no `_app_context`, por aparelho: o da etapa, senão o do plano, senão o do aparelho.
- Basta uma etapa recusada para o plano inteiro sair sem etapas, com uma pergunta (`missing`, campo `policy`) por
  etapa recusada. A execução vai a `needs_input`, nada é materializado e nenhuma decisão é gasta. Recusar só a
  etapa com efeito deixaria os preparativos (abrir, preencher destinatário, assunto) rodarem à toa.
- A linha do tempo recebe a frase; o evento `plan.refused` (persistido) leva `{motivo: "efeito_fora_do_catalogo",
  etapas: [{key, title, app_id, app, capability, motivo}]}`, com o motivo de cada etapa no vocabulário fechado
  `sem_acao_do_catalogo` | `acao_de_outro_catalogo`.
- Num plano de FLUXO, a trilha da 045 (`_registrar_resolucao`) grava a resolução antes da porta. O fluxo casou de fato; o
  que se recusou foi o plano dele.
- **O comando que pede o que o catálogo não faz (item 31.33)** não chega a virar etapa: o planejador diz o caso no
  campo fechado `fora_do_catalogo` (`{app_id, pedido}`), e não mais em `missing`. A pergunta antiga ("Como devo fazer
  isso?") não tinha resposta possível. `RunService._recusar_fora_do_catalogo` termina a execução em `failed`, sem
  etapa e sem pergunta. O texto é montado só de dados (`parsing.texto_fora_do_catalogo`): o pedido, o nome do app e os
  títulos das ações oferecidas pelo catálogo dele (ADR-052), no formato "<Pedido> não está disponível no <app>: o
  catálogo dele só tem <títulos>. Faça essa parte você mesmo ou peça só o que está nessa lista." O evento
  `plan.refused` leva `{motivo: "sem_acao_do_catalogo", pedidos: [{app_id, app, pedido, disponiveis}]}`.
- **Recusa por sobreposição (item 31.40, `ai.limpeza_apos_sobreposicao`, sem migração):** o juiz marca
  `sobreposicao=true` quando o "não"/"incerto" é porque um diálogo, banner, aviso ou cookies cobre o alvo. Numa etapa
  SEM efeito (sem `side_effect`, sem `commit_guard`, não opcional), a etapa falha sem nova tentativa e o plano revisado
  ("Recuperação automática (sobreposição)") põe antes dela uma etapa `opcional` de limpeza (`limpar_antes_<chave>`,
  31.36), retomando da tela atual. Uma vez por objetivo; a segunda recusa segue o caminho de sempre. O juiz não
  afrouxa: conteúdo coberto nunca comprova. O orçamento de chamadas da ação (18.3) que corta uma etapa de LEITURA dá o
  desfecho do 31.38 ("Dado ausente: … orçamento de chamadas da etapa esgotado"). Achado real: 3894c1 (US$ 0,306).
  **31.40 b** (achado real 95d10f: a limpeza entrou, o ator deu `step_done` sem tocar e ela nunca se comprovava, porque
  a etapa opcional só tinha prova local):
  - O juiz cita em `cobre` o id (`eN`) do elemento que cobre. Sem o id, a árvore procura uma pista (classe ou
    resource-id com dialog, modal, banner, cookie, popup, overlay, consent, bottom sheet ou snackbar).
  - O elemento achado vai à limpeza em `steps.variables` (`cobre_id`, `cobre_texto`, `cobre_bounds`), sem migração,
    e o objetivo da etapa o nomeia ao ator.
  - **(i)** Com o elemento conhecido, a árvore decide sem IA: ele sumiu, ou saiu da área que cobria, é limpeza feita;
    ainda lá, não comprovada. Sem o elemento, a limpeza tem no máximo UM julgamento.
  - **(iii)** `step_done` sem gesto nenhum (tap, long_press, drag, scroll, press_back) numa limpeza ainda coberta é
    recusado ("limpeza sem toque").
  - A receita fica desligada na etapa opcional: ela repetiria os toques de um diálogo noutro.
  - **31.51** (r-20261004190200-5b56e6, dois diálogos do site em série): antes do ator, a limpeza procura na árvore
    o botão que fecha ou recusa (`taskqueue/dialogos.py`, lista fechada de rótulos: recusar e "só os necessários"
    antes de fechar; "continuar no navegador" no "abra o app") e toca nele sem IA, um diálogo por vez, com teto
    próprio (`LIMITE_DE_DIALOGOS`, 4) que não gasta as `max_actions` da etapa. Nunca toca "aceitar", "permitir",
    "concordo" nem "configurar". Diálogo sem essa saída (o aviso que só aceita, ou um diálogo não reconhecido): a
    etapa falha com "A limpeza não fechou o diálogo do site '…'", sem IA e sem aceitar nada. As ações levam
    `source='regra'`. Revisão do #308: só no NAVEGADOR (`dialogos.NAVEGADORES`; em outro app, como o Instagram, fica
    o comportamento de antes); o veto vale no rótulo e no id (`cookie-accept-and-close`) e pega "Aceptar",
    "Akzeptieren", "Got it", "Entendi" e "OK"; o id só vale para ícone sem rótulo; o diálogo que volta depois de 4
    toques falha dizendo qual ficou; a falha "sem saída" NÃO é pulada como limpeza opcional (o objetivo falha, a etapa
    seguinte não roda com o diálogo na tela); e só conta o diálogo que cobre a tela (área do juiz, ou 15% dela).
- **Relação do valor lido (item 31.41, `ai.relacao_do_valor`, sem migração):** o `read_value` só grava a saída com
  evidência de que o elemento É o que foi pedido (`taskqueue/relacao.py`, determinístico): (a) o seletor que o catálogo
  declara (`saidas_relacao: [nome=seletor]`); (b) um termo do nome, do glossário PT→EN ou de um sinônimo do catálogo
  (`nome~termo`) no resource-id ou na descrição do próprio elemento, no texto dele como rótulo ou num vizinho da mesma
  linha ou logo acima; (c) a forma de um tipo FECHADO inferido do nome (e-mail, data, telefone, URL; número sozinho
  não vale). Valor da IMAGEM, ou saída de catálogo sem seletor nem rótulo na árvore: UMA pergunta de sim ou não ao
  verificador (~US$ 0,005 com o Haiku e imagem), e incerto conta como não. Dúvida recusa a leitura
  (`leitura.sem_relacao`, linha na execução); quatro recusas viram o desfecho do 31.38. Achado real: 03d58e.
  **Nome de PAPEL (item 31.47, sem migração):** `manchete`, `título`, `assunto`, `nome`, `remetente`, `autor`… (lista
  fechada pt/en, `e_nome_de_papel`) rotulam o LUGAR do texto, não uma palavra dele: a manchete de um portal nunca contém
  "manchete". Sem evidência da árvore, o nome de papel vai SEMPRE ao verificador (mesmo em planejamento livre, sem
  catálogo), e a pergunta é "este elemento ocupa o papel X nesta tela?" (`pergunta_de_papel`: id, bounds e tamanho da
  tela; rodapé, item de menu, botão, banner e anúncio nomeados como NÃO), não "o texto tem relação com X". "não" e
  "incerto" recusam como antes. Nome que não é de papel (preço, protocolo…) segue com a pergunta de relação. Custo: uma
  chamada de verificação por leitura de papel sem rótulo na árvore. Achado real: df1212 (g1, `read_headline`): duas
  leituras certas da manchete recusadas, sem juiz nenhum, e a etapa terminou em dado ausente.
- **Leitura sem o dado (item 31.38, `ai.max_decisoes_leitura`, sem migração):** na etapa de LEITURA (declara saídas,
  sem efeito nem commit_guard) o ator pode chamar `step_blocked(kind="dado_ausente")`, e o teto de decisões por
  tentativa (12: p95 de 8 medido em 16 leituras com sucesso desde 27/09, com folga) tem o mesmo desfecho. A etapa não
  se repete nem sobe ao modelo forte: falha com "Dado ausente: procurei '<saída>' na etapa '<título>' do <app> e não
  encontrei (<motivo>)", sem texto da página. O objetivo ganha UM plano revisado (motivo "Recuperação automática (dado
  ausente)"), e o ator dele recebe essa frase no histórico; a segunda vez fecha o objetivo como falha final. Em etapa
  de efeito o teto não vale e `dado_ausente` é recusado. Achado real: ba5ebc (`read_parties`, 21 decisões, verba).
- **Leitura que muda de valor (item 30.78, sobras do 31.78, sem migração):** quando o ator relê uma saída e o valor
  muda, o executor põe no histórico, que também vai ao juiz da verificação, o fato neutro "o valor lido de '<saída>'
  mudou nesta tentativa; vale a última leitura.", sem os valores e sem instrução. Com tudo lido e uma saída cuja última
  leitura diverge da anterior, o ator recebe, só na cópia dele, o pedido de reler essa saída para confirmar antes do
  `step_done`. Sem o aviso, ele relia a saída estável sem saber por que a etapa não andava, até o teto falhar como
  divergente (C1 do 31.78). A releitura que repete o valor encerra a divergência e o aviso some.
- **Etapa de limpeza opcional (item 31.36, `ai.limpeza_opcional`, migração 098):** o planejador marca `opcional` a
  etapa que só limpa a tela (aviso, banner, cookies, dica); o parsing só aceita a marca sem efeito, sem `saidas`, sem
  for_each e sem commit_guard. Ela roda com no máximo 3 decisões do ator, sem juiz (`so_prova_local`) e sem escalar.
  Não comprovada, vira `skipped` como AVISO (`running|verifying → skipped`), sem replano; a dependência e o progresso
  a contam como resolvida, e o objetivo pode fechar `succeeded`. O ator ganhou a regra "feche o aviso que cobre o
  alvo; se não fechar, siga sem ele". Achado real: f8722d (28.12-02).
- App sem catálogo (o QA Messenger) segue livre com efeito. Os 12 fluxos ativos do central com `send_message` livre
  são todos dele (medido em 03/10, só leitura).
- **O caso que motivou** (r-20261001190557-e7bc42, 01/10 19:05Z: "enviar e-mail pelo Outlook", `fill_recipient`
  com 16 e 17 decisões) rodou ANTES de o catálogo do Outlook existir no central: o runtime de 01/10 (`5d8b545`) e
  o deploy de 02/10 ~16:20Z (`f9eed71`) não têm o `catalogo.yaml`. Sem catálogo, a porta não tinha o que recusar.
  Depois que ele chegou, 5 de 5 planos com etapa no Outlook ligaram a capability (`OPEN_MAIL_INBOX`), contra 0 de
  5 antes: o binding do planejador já está feito, e esta porta é a trava para fluxo, skill e provedor que não
  passa pelo parser do 24.1.
- **Prova.** `tests/test_recusa_no_planejamento.py` é `simulated` (harness, provedor por roteiro, sem aparelho nem
  IA). Sem a porta, os 4 testes de execução falham. A prova real (5 leituras no android-01, decisões por etapa) é da
  frente Android: `not_run`.

### Roteamento por conjunto de apps (item 24.5, ADR-058)

Antes do 24.5, quem faz e onde era decidido por **um** app: o dos alvos, quando havia um só, ou o da habilidade que
exigia um só. Um comando entre apps sem habilidade casada roteava sem app nenhum (R5), e as checagens antes de agendar
olhavam só o app principal do aparelho (R7). Agora o comando tem um **conjunto** de apps, e cada peça pergunta por ele.

**O conjunto** (`RunService._app_do_comando`, na ordem; o primeiro é o `app_id` dos alvos, contrato C5):

1. os apps dos alvos da interface (`RunTarget.app_id`);
2. a habilidade ou o fluxo casado: o app do plano dele e os `required_apps`, na ordem do plano;
3. sem nada casado, os apps que o comando cita (`apps_citados`, a leitura do 24.1):
   - dois ou mais citados: todos;
   - um só citado: entra se for **app de conta** (o manifesto pede perfil, como o Outlook, ou declara login gerenciado,
     como o Instagram). "Leia o e-mail no Outlook e curta o post de @x" dá `[outlook]`;
   - um app sem conta citado sozinho ("abra o QA Messenger") não vira conjunto. O comando é o de um app de sempre, o
     app do aparelho decide e a persona num aparelho com duas continua sendo pergunta.

Vazio quer dizer que não se sabe, como antes.

| Peça | Com o conjunto |
|---|---|
| `alvos.Mundo.serve(persona, aparelho, app_ids)` | a persona tem, **no mesmo aparelho**, vínculo com todos os apps de conta do conjunto (a união dos vínculos por app do par). App sem conta (`Mundo.sem_conta`: Chrome, QA) não conta. Conjunto só de apps sem conta: a regra de antes, app a app |
| `resolver_alvos` | o pool da persona e o desempate entre personas de um aparelho usam `serve` com o conjunto; cada `AlvoResolvido` leva `app_ids`, com o app do alvo explícito à frente; `Resolucao.app_ids` é a união |
| `RunService._mundo(app_ids)` | apto: nenhum app do conjunto sabidamente fora de pronto no aparelho. Sessão pronta: a de todos os apps de **login gerenciado** do conjunto; o Outlook antes do 23.8 não tem sessão e não zera a interseção |
| `_mistura_de_apps(ids, app_ids)` | com o conjunto, aparelhos de apps principais diferentes **não** são mistura: todo aparelho roda as mesmas etapas, cada uma no app dela (24.1, 24.2). Sem conjunto, a recusa `mixed_apps` de antes, que agora sugere citar o app de cada parte |
| `_incompativeis(ids, app_ids)` | a versão desejada do app principal **e** de cada app do conjunto (`_pacotes_da_tarefa`); uma frase por aparelho |
| pré-voo (`pre_voo(..., app_ids)` → `state.py::_app_preflight(rt, pacotes)`) | o principal e cada pacote do conjunto, pela mesma regra. A recusa de um app que não é o principal leva o rótulo dele ("Outlook: o aplicativo não está pronto…"). Na criação vale o conjunto; no `start`, os `required_apps` do plano |
| desbravador (`Scheduler._waits_for_pathfinder`) | a chave de compatibilidade é a de **cada** app das etapas que faltam: outra versão do Outlook separa os grupos mesmo com o mesmo QA ou Instagram |
| modo Automático (`orquestrador.py`) | os apps são os de `_app_do_comando` e, se vier vazio, os citados (29.70: o app sem conta citado sozinho, "No QA Messenger, leia …", entra; antes, os apps vazios faziam toda persona candidata e a leitura de QA ia para o aparelho de uma conta real). Candidata é a persona que serve ao conjunto (`serve`). Nenhuma: conjunto só de apps sem conta vai por `previa_sem_conta`: aparelhos com os apps (principal ou sabidamente prontos em `device_app_state`), e o de conta real logada (perfil ativo vinculado) só entra se nenhum sem conta estiver apto; a prévia diz isso nos dois sentidos. Senão `nenhuma`, dizendo quais apps pedem conta. App de conta e app sem conta no mesmo pedido seguem por persona. A sugestão e cada alvo levam `app_ids` |

- **O app principal entra nas checagens de aparelho** mesmo quando o comando cita outro. Ele é candidato do
  planejamento entre apps (`_catalogos`), e "leia o e-mail no Outlook e curta o post de @x" usa o Instagram do
  aparelho sem nomeá-lo.
- **O que não muda:** `RunTarget` segue com um `app_id` (o eco da prévia não carrega o conjunto; a criação o recompõe
  pelo comando). As portas do despacho a cada troca de app são do 24.4, e o painel com os apps de cada alvo é do 24.6.
- **Prova.** `tests/test_roteamento_por_conjunto_de_apps.py` é `simulated`: resolvedor puro, harness na porta 5640,
  planejador e orquestrador simulados. O roteamento com contas reais do Outlook e do Instagram está `not_run` e fica
  para o 24.9.

### O painel sem "um app" (item 24.6, R9)

O painel mostra o app de **cada etapa** no Plano e na Execução (selo quando difere do app do plano; linha "App" nos
detalhes técnicos), os apps exigidos do plano (`required_apps`) e, na prévia dos alvos, o **conjunto** de cada alvo
pelo nome do catálogo ("QA Messenger + Notas", contrato C5).

O Comando deixou de escolher "um app". No modo "Distribuir entre servidores", o painel preenchia o app mais comum do
parque quando a pessoa não escolhia, e o comando entre apps era repartido pelos aparelhos de um app que ninguém pediu.

| Peça | Agora |
|---|---|
| `DistributeSpec.app_id` | opcional. Sem ele, os apps são os que o **comando** usa; com ele, restringe a esse app, como antes |
| `RunService._apps_da_distribuicao` | o app escolhido; senão o conjunto já resolvido (modo Automático); senão `_app_do_comando` e, para o app sem conta citado sozinho, a citação (`apps_citados`). Vazio: prévia vazia com o motivo, e a criação recusa com `distribution_sem_app` (409) |
| `RunService._candidatos_dos_apps` | um app: `Scheduler.candidatos_do_app`, a regra de sempre. Vários: a **união** dos aparelhos cujo app principal é um deles (não os do primeiro citado, que dependeria da ordem do texto); se algum exige conta (provedor de sessão), só aparelho com perfil ativo vinculado; sai o aparelho em que algum app do conjunto se sabe fora de pronto, dito app por app |
| `POST /runs/distribution` (corpo JSON; o GET responde 405) | `app_id` ou `command` (um dos dois; nenhum = 422 `distribution_sem_alvo`); comando com credencial recebe a recusa da criação (`credencial_no_comando`) |
| painel (`DistributeTarget`, `CommandPanel`) | o padrão do seletor é "os que o comando usa"; a prévia vai pelo comando (com o atraso da digitação e nunca com texto que parece senha) e o envio manda `distribute: { count }`; escolher um app manda `app_id` |

- **Prova.** `simulated`: `tests/test_distribuicao_pelo_comando.py` (harness na porta 5640, aparelhos falsos) e
  `features/command/CommandPanel.test.tsx` "Distribuir entre servidores (item 24.6)", mais `PlanTab.test.tsx`,
  `model.test.ts`, `execution.test.tsx` e `alvos.test.ts` para o app por etapa e o conjunto por alvo (backend falso).
  O painel no ambiente central está `not_run`.

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
- `POST /api/flows/match` passa a respeitar `ai.flows` ([contrato](../api-contract.md#adendo-v021-27092026--habilidades-no-caminho-dos-fluxos)).

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

## Caminho rápido 1: pular o ator, nunca a prova (LT-1, LT-2, LT-3; 03/10/2026)

Os três atalhos tiram uma chamada de IA do caminho quando a prova já existe; nenhum converte incerteza em sucesso. Quem
confere é sempre o mesmo `_verify` (ou a prova local de `_postcondition_holds`), e o fim do laço reusa o veredito em vez
de pagar outro.

- **LT-1, pós-condição na entrada.** No laço de `_run_step`, depois de observar e antes de o ator decidir (uma decisão de
  receita que ainda reproduz vem antes): etapa SEM efeito (`side_effect == 0`, a UI otimista de uma etapa com efeito
  mostra o "feito" antes de ele valer), tela não sensível e nenhuma saída por ler (`faltam_saidas()`). Etapa
  determinística com `_postcondition_holds` verdadeira sai do laço para o `_verify` de sempre, com custo zero (a árvore já
  foi lida), em qualquer volta. Etapa julgada sem nível de entrega confere só na ENTRADA (`decisions == 0`, uma vez por
  tentativa, `JULGAMENTOS_ANTES_DO_ATOR`), e por padrão só pela prova local do catálogo
  (`ENTRADA_JULGADA_SO_COM_PROVA_LOCAL`): sem ela, a tela de entrada quase nunca é a final e o julgamento pago subiria
  `verify` por etapa. Com a constante em `False` o juiz barato confere na entrada, como o handoff de latência descreve.
  "Não" ou "incerto" devolve a etapa ao ator na mesma tentativa, com o motivo no `history`.
- **LT-2, `expect_done` em etapa julgada.** O ator marcou que a ação conclui a etapa julgada: em vez de voltar a ele só
  para dizer "pronto", o `_verify` confere a tela agora (`uma_rodada=True`: um só julgamento, sem esperar a tela mudar
  até o fim do orçamento). "Sim" guarda o veredito e sai do laço (uma verificação por etapa, não duas); "não"/"incerto"
  entra no `history` e o laço continua NA MESMA tentativa, como na divergência de receita. Nunca `retry` nem `failed`
  por causa dele: uma tentativa nova custa mais que o decide poupado. Não há guarda de `side_effect` aqui (a
  especificação não pede): a ação com efeito que dispara `is_commit_action` já sai do laço antes.
- **`steps.driven_by = 'sem_ator'`.** A etapa que fecha pelo LT-1 sem o ator decidir nada (e sem ação de receita) grava
  `sem_ator`, nunca `ai` e nunca nulo. `aproveitamento.py` a conta à parte (`sem_ator`) e não a considera "elegível a
  receita" (não há caminho a aprender); o painel a mostra como "Sem o ator". Quem fecha por `expect_done` (LT-2) teve
  o ator agindo e segue `ai`.
- **LT-3, `flows.match` com parâmetro RESERVED.** `account_label`, `instance_id` e `run_id` nunca são capturados do
  comando; o valor é do aparelho e entra na materialização. O fluxo que os carrega deixou de ser recusado pelo molde
  sem valor; um parâmetro NÃO reservado sem valor continua recusando. `_learn_flow` não mudou. O caminho da
  habilidade (`skills/domain/matching.py::bind_template_parameters`) ficou de fora e seguiu recusando até o 30.29.

Prova `simulated`: `tests/test_caminho_rapido_executor.py` (LT-1 e LT-2: contagem de `decide`/`verify`, "não" volta ao ator
com `attempts == 1`, `sem_ator`), `tests/test_flows_account_label.py::test_fluxo_com_account_label_casa` (LT-3) e
`tests/test_aproveitamento.py`. `not_run`: o efeito de latência no ambiente real (`decide` por etapa julgada em
`/api/usage` antes e depois, `uses` dos fluxos com `{account_label}`, `verify` por etapa).

## Caminho rápido 2: falha que sai cedo, abrir o app sem IA e retentativa no barato (LT-5, LT-6, LT-12; item 29.45, 03/10/2026)

Do mesmo relatório de latência. Nenhum dos três converte falha em sucesso nem pula a prova.

- **LT-5, "não" em tela parada sai cedo.** No `_verify`, depois de um "não" do juiz, 3 sondagens seguidas na mesma
  assinatura de árvore (`SONDAGENS_DA_TELA_PARADA`, ~4,5 s com `judge_wait_s` de 1,5) encerram a verificação com o mesmo
  veredito e o motivo na evidência. Antes ela esperava o orçamento inteiro (15 s; 60 s `patient`), mas a 2ª chamada só
  vem com a tela mudada; em 7 d as "NÃO comprovada" tinham parede mediana de 16,9 s. Assinatura nova reabre a contagem.
  Não sai cedo onde a mudança tem dono fora da tela: `patient` com `pending_marks` declaradas no catálogo (ADR-055) e
  nível de entrega acima de `sent` (entregue/lida chega sem a árvore mudar antes). Efeito disparado segue `uncertain`.
- **LT-6, `open_app` sem IA.** A etapa cuja pós-condição é `app_foreground` abre o app pelo executor antes de consultar o
  ator, pelo caminho da reabertura pós-ANR (`open_app` + foco lido), como estratégia `deterministic` em
  `attempts.strategy`. Uma vez por tentativa, só sem efeito, sem receita conduzindo e com o pacote entre os configurados.
  A linha "aberto pelo executor, sem IA" diz o tempo e o foco; a etapa fecha pelo atalho do LT-1 como `sem_ator`. Pedido
  que falha ou foco que não chega: a volta seguinte não comprova e o ator assume na mesma tentativa. A abertura entra na
  regra do ANR como a da IA entrava (uma reabertura; a 2ª morte para a etapa). `esperar_foco` sonda a 0,5 s nos primeiros
  5 s (`INTERVALO_INICIAL_DO_FOCO_S`, `JANELA_INICIAL_DO_FOCO_S`), depois volta aos 2 s.
  - 31.48: vale também para a etapa que prova por um elemento DO app (`element_present` com `id=<pacote>:id/…`,
    `executor.pacote_da_prova`), quando esse app não está na frente. MEDIDO no banco central (04/10): o "abrir o QA
    Messenger" com a prova da lista (modelo 141e) foi à IA em 43 de 49 sucessos (15 só para pedir `open_app`, 19 para
    voltar de dentro de uma conversa); com `app_foreground` (2c35) fechou sem ator em 50 de 50. Aberto o app, a lista à
    vista fecha pelo LT-1 sem ator. O aviso "Novidades da versão" é outra tela (a lista some), então a prova não vale e
    o ator o dispensa. Com o app já na frente (dentro de uma conversa), abrir não muda nada e segue o ator.
  - Em modo sombra essas etapas não alimentam o `_veredito_da_sombra`, porque a IA não decide nelas. Uma candidata de
    `open_app` não promove por sombra.
  - O `open_app` do comando do painel (`manager.open_app`, com HOME e foco pelo adb) não mudou: está fora do caminho da
    etapa.
- **LT-12, retentativa no tier 0.** A nova tentativa inteira subia ao modelo de escalonamento. Agora ela começa no tier 0
  e sobe, até o fim da tentativa, na 1ª decisão do barato que:
  - repetir, na mesma tela estrutural, a última ação da tentativa anterior (onde ela parou); ou
  - dispararia o efeito.

  Essa decisão é descartada antes de agir (não vira linha em `actions`) e refeita no tier 1, com
  `ai_calls.escalate` = `nova_tentativa`. A última ação de cada etapa fica na memória do processo
  (`_ultima_acao_da_etapa`) e some no desfecho final. Depois de reiniciado o backend, a retentativa começa no tier 0 sem
  esse gatilho; os outros (erros seguidos, ciclo, efeito por risco) seguem valendo.

Prova `simulated`: `tests/test_caminho_rapido_2.py` (LT-5 com o `_verify` direto; LT-6 e LT-12 no Harness). Os testes de
`test_anr_sinal_proprio.py` e `test_estados_de_ia.py::test_recusa_na_decisao...`, cujo gancho é a decisão da IA que abre o
app, desligam `OPEN_APP_SEM_IA`. `not_run`, que é o aceite: "NÃO comprovada" mediana 16,9 → ≤ 7 s; `open_app` mediana
2,2 → ≤ 1,5 s; etapa `app_foreground` com decide = 0; motivo "nova tentativa" < 10/semana.

## Espera do juiz adaptativa (item 31.27, 04/10/2026)

A espera ANTES do primeiro julgamento (`patient`, com pós-condição julgada ou nível de entrega) existe para o app sair de
"enviando" e não pagar dois julgamentos. Ela era um sono fixo de `judge_wait_s` (1,5 s). Agora acompanha a tela.

- **Como funciona.** Lê a árvore a cada `PASSO_DA_ESPERA_DO_JUIZ_S` (0,3 s) e sai quando a assinatura fica igual por
  `ai.judge_wait_estavel_s` (0,6 s), sem marca pendente do catálogo. Nunca passa de `judge_wait_s`. A última leitura vira
  a primeira do verificador, que não relê.
- **O que não muda.**
  - A tela que ainda muda espera até o teto, como antes.
  - "Enviando…" declarado e parado não conta como assentado.
  - A leitura que falha cai na espera fixa.
  - A espera ENTRE duas sondagens, que espera a tela MUDAR (LT-5), segue fixa.
  - `judge_wait_estavel_s: 0` volta ao comportamento antigo.
- **Medida.**
  - Na linha de base de 04/10 (`.claude/handoffs/jev-latencia-linha-de-base-2026-10-04.md`), as 4 esperas antes do
    julgamento somaram 6 s em 233 s de parede.
  - No "depois" do deploy 16, foram 6 esperas e 9 s em 149 s.
  - O tempo segue contado em `attempts.juiz_espera_ms` (31.24).
  - Ganho estimado (INFERRED): 0,7 a 1,2 s por espera; medir de novo depois do deploy.
- Teste: `tests/test_juiz_espera_adaptativa.py` (`simulated`).

## Seletor com as partes em elementos diferentes (item 31.32, 04/10/2026)

Em `element_present`, `|` exige tudo no MESMO elemento (`id=chat_title|text={{recipient}}`), e isso é válido: 382 etapas
foram comprovadas assim. Na r-20261004082521-2f21e2, o plano pediu `id=…message_input|text=Suporte QA` com a conversa
aberta. A caixa de texto e o título com o contato estavam na tela, mas em elementos diferentes. A etapa falhou 3 vezes,
a recuperação copiou a etapa igual e falhou mais 3.

- **Diagnóstico** (`UiTree.partes_em_elementos_diferentes`): o seletor composto não casa nenhum elemento, mas CADA parte
  casa sozinha algum elemento da tela.
- **Só na tela final:** o fim do orçamento da verificação, nunca na conferência de uma rodada antes do ator. Não vale
  em tela de outro app, com marca pendente nem com legenda de cartão ausente.
- **Desfecho:** a verificação devolve `unprovable` e a etapa vira defeito do plano (`plan_defect`). Falha na hora, sem
  nova tentativa e sem plano revisado, e os aparelhos irmãos ficam retidos, como no defeito do plano de sempre. O tipo é
  `seletor_em_elementos_diferentes`, na camada do plano.
- **O que não muda:**
  - o composto válido segue comprovando;
  - a parte que não está na tela é a falha de sempre (a tela certa pode não ter chegado);
  - nenhum juiz entra no lugar da pós-condição (decisão da orquestradora: 4 execuções no histórico não pagam um
    caminho novo entre a tela e o "comprovado").
- Teste: `tests/test_seletor_impossivel.py` (`simulated`, com um caso de ponta a ponta no harness).

### Pós-condição com valor vazio falha fechado antes de agir (31.44)

Achado do histórico de erros do portal (29.72). Na r-20261004111836-fec1a1 (validação do fluxo "enviar a mensagem", no
android-04), o molde de `check_account` era `id=…:id/account_label|text={account_label}`; a etapa não declara app e o
aparelho não tinha `account_label`, então o valor virou `""`. `UiTree._partes_do_seletor` degrada `text=` para a busca
do texto literal "text=": a etapa gastou 3 tentativas e 3 chamadas `decide` (32 mil tokens) para chegar a "0 elemento(s)".

- **Guarda** (`_run_step`, junto das do `{{saida:…}}` e do `{account_label}` do 24.4, antes de qualquer observação,
  ação ou IA): `parte_vazia_da_pos_condicao` acha parte do seletor (`element_present`) com chave conhecida e valor vazio
  (`UiTree.parte_sem_valor`), parte em branco, ou `text_visible` em branco.
- **Desfecho:** se o aparelho está sem conta conhecida (`account_label` vazio), `waiting_user` com o motivo do 24.4
  (tipo `conta_errada`, que nunca vira lição; a tentativa é devolvida e a pessoa cadastra a conta). Com conta
  conhecida, é variável do plano que chegou vazia: defeito do plano (`defeito_do_plano`) na 1ª tentativa, sem plano
  revisado.
- **Fora do alcance:** o seletor com valor preenchido mas incoerente (o `message_input|text=<nome da conversa>` da
  r-20261004082521-2f21e2, caso do 31.32). Antes da ação o campo de escrita nem está na tela, então nada decide em
  tempo de entrada; quem fecha é o 31.32, na tela final e em 1 tentativa. Esse seletor veio do planejador real: o QA
  Messenger não tem pasta em `conhecimento/apps/` nem plano escrito à mão (o provedor simulado já usa
  `id=chat_title|text={recipient}`), e o texto do planejador tem snapshot por sha256 (`test_prompts_licoes.py`), então
  a correção fica com a lição do 31.32.
- **Limite conhecido:** o 30.50 já tira do despacho de prova o aparelho sem `account_label`; esta guarda cobre qualquer
  execução. Um molde "Conta: {account_label}" em texto livre ("Conta: " não vazio) segue pelo 24.4.
- **Risco aceito (igual ao 31.32):** o `defeito_do_plano` com conta conhecida retém também os aparelhos irmãos da
  execução, mesmo quando a variável vazia é por aparelho; e o tipo entra no minerador de lições do planejador.
- Teste: `tests/test_pos_condicao_impossivel.py` (`simulated`; prova `real` não executada).

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
- **Só com prova (ADR-053):** `learn_from_run` recusa execução com etapa `succeeded` sem `verified=true` (confirmada
  à mão), em qualquer versão do plano, e congela como modelo também `bindings`, `band_guard` e `success_criteria`.
  Prova (`simulated`): `test_aprendizado_de_fluxo_com_prova.py`.

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

## Lentidão do aparelho: UI ocupada, ANR e recuperação (ADR-053)

Convidado lento vira demora e aviso no aparelho, não falha nem laço (medido em `r-20260928165254-e31953` e
`r-20260928195344-02ee9e`, em que a IA ocupou 4,5% do tempo). O 500 de UI ocupada do UiAutomator2 é `DriverBusy`: a
leitura relê com recuo (até 3 vezes, 4 s) sem recriar a sessão do Appium, e a ação com UI ocupada fica com efeito
incerto, sem repetição (K-049). O ANR tem sinal próprio: com `hide_error_dialogs=1` o app morre calado e o launcher
volta (K-048), então o executor lê `dumpsys activity exit-info` (`Adb.app_deaths`) e o foco da seção viva do
`dumpsys window` (K-047); com morte do app na etapa, reabre uma vez sem IA e, na segunda, a etapa falha dizendo que o
app parou de responder com o convidado sem CPU. Prazo vencido é `kind=step_deadline`, não "IA indisponível". A
recuperação automática não faz force-stop do app vivo em primeiro plano quando a falha foi de prazo, IA ou guarda:
retoma da tela atual, atravessa o efeito comprovado sem repeti-lo (um LIKE repetido descurte), herda `commit_guard`,
`bindings` e `draft_meta`, e falha com motivo se a projeção das etapas refeitas não cabe no tempo restante. Pendências:
a aprovação de texto não acompanha a etapa revisada, e o erro de adb no screencap da observação ainda recria a sessão.
Prova: `simulated` (`test_ui_ocupada.py`, `test_anr_sinal_proprio.py`, `test_recuperacao_preserva_estado.py`) e `real`
em `r-20260928234657-bbdf3c` e `r-20260928235215-6eb84c` ([relatório §21](../relatorio-validacao.md)).

**Falta de informação passa pela revisão antes da pessoa (29.35, RA-9).** 23 % dos objetivos paravam em
`waiting_user`, e o "campo X não existe" do ator chegava à pessoa sem passar pela recuperação. Agora o `missing_info`
que não é credencial (a triagem é a `TriagemDeCredencial.pergunta_sensivel` do 29.52) ganha uma revisão determinística
do plano antes do `waiting_user`. É a mesma da recuperação automática: as mesmas portas (prova não replaneja, efeito
disparado não se refaz, cabe no prazo) e o mesmo teto por objetivo (`MAX_PLAN_REVISIONS`). A versão leva o motivo
`Recuperação automática (falta de informação) após '<etapa>': <razão>`, e a marca `falta de informação`
(`MOTIVO_FALTA_DE_INFORMACAO`) sai também em `plan.revised.data.reason`, que é o contrato para Aprendizado e Jev. A
tentativa conta como falha, não como interrupção. Na segunda falta, com o teto gasto, a pessoa entra como antes.
A marca não é "defeito de plano": a revisão copia as etapas não comprovadas, o plano em si não muda, e por isso não
é lição do planejador. Na medida da meta, cada `missing_info` revisado cai num de dois baldes: "tela errada" (a
versão revisada concluiu a etapa) ou "campo que não existe" (a versão revisada voltou a relatar a falta e foi para
a pessoa). Os dois saem de `plan_versions.reason` com a marca, cruzado com o desfecho do objetivo. A subida ao tier 1 (17.10) continua acontecendo antes.
Prova: `simulated` (`test_falta_de_informacao.py`, `test_cascata_ator_barato.py`); a meta de `waiting_user` do QA
(de 11 para no máximo 5 em 7 dias) é `not_run`.

## Valor lido entre etapas (item 24.3, ADR-058)

Um comando que atravessa apps precisa levar um dado de uma etapa à outra ("leia o assunto do último e-mail no Outlook
e abra no Instagram o perfil citado"). Até aqui só passavam os parâmetros da execução e os itens da coleta
(`{item}`). O contrato C2 (migração 056) criou a forma; o 24.3 liga o comportamento.

| Peça | Onde mora | O que faz |
|---|---|---|
| declaração | `PlanStep.saidas`, `steps.saidas` | os nomes (`^[a-z][a-z0-9_]{0,39}$`) que a etapa entrega às seguintes |
| leitura | ferramenta `read_value` (`automation/tools.py`), tratada em `StepExecutor._run_step` | o executor tira o valor do **texto do elemento** (`taskqueue/saidas.ler_valor`); com `value`, só o trecho, que precisa estar no texto. Tipos: `text`, `number`, `url`, `list` (os textos dentro do contêiner, em JSON) |
| triagem (D3) | `saidas.triagem` | — |
| gravação | `StepExecutor._gravar_saidas` → `Repository.save_step_output` | só quando a etapa é comprovada, na mesma transação do `succeeded`; `StepOutcome.outputs` leva os valores |
| referência | `{{saida:<nome>}}` em título, objetivo, pré e pós-condição, guardas, `bindings` e `variables` | `Repository.resolver_saidas`, chamado por `Scheduler._work` **antes da porta de política** |
| dependência | `repository._dependencias_das_saidas` (na materialização) | a etapa que cita `x` depende da etapa anterior do lote que declara `x` |
| relatório | `RunService.report` → `per_instance[].values_read` e a tabela "Valores lidos entre etapas" do Markdown | nome, valor, tipo, etapa e app de origem; no painel, o bloco do cartão da instância |

Regras:

- **Fato, não alegação.** O ator nunca escreve o valor: `read_value` aponta o elemento e o executor lê a árvore, como
  na coleta. Um trecho que o elemento não tem é chamada rejeitada (o ator tenta de novo).
- **O erro de chamada também não grava valor.** Tipo que não casa, trecho fora do texto, nome ou elemento errado
  (`LeituraInvalida`) acontecem ANTES da triagem, então nada ali foi triado: a mensagem não cita o texto da tela nem o
  que o modelo escreveu, os argumentos gravados saem sem o recorte e sem nome ou id que não tenham forma de nome ou de
  id da tela (`saidas.args_da_chamada_invalida`), e a justificativa do modelo fica de fora — um código lido com o tipo
  errado não chega a `actions` nem ao evento `action.logged`.
- **A etapa que entrega valor não conclui sem ele.** `step_done` é recusado enquanto falta um nome; a pós-condição
  comprovada sem o valor não é sucesso (`retry`/`failed`, ou `uncertain` com o efeito disparado). O ator sabe o que
  ler pela linha "(executor) esta etapa entrega…" do histórico. Receita não roda nem se aprende nessa etapa: ler é
  decisão sobre a tela da vez (`driven_by` fica `ai`).
- **Ler antes do efeito.** Depois do toque de efeito o ator não é mais consultado (só se comprova), então uma etapa
  com efeito e saída só conclui se leu tudo ANTES do commit; senão fica `uncertain`. O planejador deve pôr a leitura
  numa etapa própria, antes, ou depois do efeito, em outra etapa.
- **Código, senha ou token param a etapa.** Recusado pela triagem, o desfecho é `waiting_user` (ADR-009: o código é
  da pessoa), e o `read_value` não grava o valor: nem em `step_outputs`, nem nos argumentos da ação (`**RECUSADO**`),
  nem na justificativa do modelo, em evento ou na evidência dele (que sai em texto, sem captura da tela). A tela em
  si segue as regras de sempre (as capturas das outras evidências, o resultado de um `find_element`): quem decide o
  que é tela sensível é o detector de telas, não a leitura. A triagem é conservadora de propósito: um endereço com
  trecho aleatório de 32+ caracteres (link mágico de entrada, id opaco de mensagem) é recusado como token.
- **Resolvido na linha, antes da porta.** A referência vira o valor em `steps` antes de `policy_gate`: aprovação,
  limite por alvo, coordenação de frota (uma conta por alvo, ADR-055) e o painel enxergam o valor, não o molde. O
  `template_hash` fica o do molde, e as saídas entram nas variáveis da receita como `saida_<nome>`, para a receita
  aprendida com "@ana" digitar "@bia" quando for o valor lido.
- **Saída ausente nunca é inventada.** Se nenhuma etapa do plano a declara, é defeito do plano: o objetivo falha e os
  aparelhos que não começaram ficam retidos. Se a produtora ainda está aberta, a etapa espera com o motivo. Nenhum
  dos dois gasta tentativa.
- **Retomada sem reler.** A recuperação atravessa a leitura comprovada com todos os valores gravados, como atravessa
  um efeito comprovado: o valor é reaproveitado e o caminho até ela volta.
- **`for_each`:** o nome declarado dentro do bloco ganha o sufixo do item (`assunto_i2`), nas saídas e nas referências
  do bloco; a identidade da etapa tira o sufixo, e as cópias seguem com a receita da etapa-modelo. Uma etapa DEPOIS
  do bloco que cita um nome do bloco não diz de qual item: a referência fica sem produtora e é defeito do plano.
- **Referência para a frente** (a etapa que lê vem depois de quem usa) é defeito do plano, não espera: a etapa
  anterior, pronta, seria sempre a escolhida, e a leitura nunca rodaria.

Prova: `simulated` (`test_valor_entre_etapas.py`: triagem, leitura, dependência, `for_each`, ponta a ponta no QA
Messenger falso, código na tela, erro de chamada sem valor no registro, defeito do plano e recuperação). Outlook →
Instagram num aparelho real: `not_run` (24.9).

O planejador declara e cita (`planning/prompts.py`, `planning/parsing.py`): a etapa livre que lê traz `saidas` (no plano
entre apps, `livre.saidas`), e a seguinte, livre ou ação do catálogo, cita `{{saida:<nome>}}` no texto ou num
argumento. O parser normaliza o nome, e um nome citado que nenhuma etapa ANTERIOR lê vira pergunta (`missing`,
`field: "saida"`) com as etapas zeradas, em vez de chegar ao despacho como defeito. O prompt do ator diz quando usar
`read_value`: antes de concluir e antes de qualquer toque de efeito; código, senha e token nunca são valor. Prova:
`simulated` (`test_planejador_entre_apps.py`: Outlook lê o assunto, o Instagram abre o perfil citado, com a referência
ligada à leitura e resolvida). Falta, na DSL, a leitura nomeada, que espera a decisão do dono de estender a v1alpha1
(proposta em [skill-dsl](../skill-dsl.md#saídas-e-casos-de-validação)).

### Saídas obrigatórias (item 12.4)

Etapa que declara saídas só é comprovada com elas preenchidas; a verificação de pós-condição prova a TELA, não a leitura.
Achado real (02/10/2026, r-20261002204347-8c3f6e): `OPEN_MAIL_INBOX` do Outlook declara `saidas: [remetente, assunto]`, o plano
não escolheu nenhuma, e o produto deu "1 de 1 com sucesso comprovado" com a caixa aberta e nenhum remetente ou assunto.

- **O que a etapa exige** (`executor.saidas_exigidas`): os nomes escolhidos pelo planejador (`steps.saidas`) ou, sem escolha, tudo
  o que a ação do catálogo declara (`Capability.saidas`). O planejador continua podendo estreitar para o subconjunto que as
  seguintes usam; o que ele não pode é zerar o que a ação entrega. Vale em `run_step` (receita desligada: ler é decisão sobre a
  tela da vez) e em `_run_step` (o ator é mandado ler com `read_value`, `step_done` sem ler é recusado, e a etapa comprovada pela
  tela mas sem valor é `retry`/`failed`, ou `uncertain` se o efeito já foi disparado). Os valores vão ao dicionário de saídas do
  objetivo (`{{saida:<nome>}}`, item 24.3), no contrato de sempre.
- **Coleta** (`items_collected`): continua só com `collect_list`. Lista que chega ao fim sem item (3 leituras vazias) é falha
  (`A coleta não encontrou nenhum item na lista.`), a menos que o vazio seja COMPROVADO: `_prova_de_vazio` pergunta ao
  julgamento se a tela mostra, de forma explícita, que a lista está vazia (sem item à vista); com "sim" a etapa fecha com
  `StepResult.vazio_comprovado=true` e `items=[]`, e o `for_each` expande para zero cópias. Lista com itens à vista e zero
  casamentos (seletor errado) ou julgamento em dúvida/erro: falha, com "O vazio não foi comprovado" no motivo.
- **Sem saídas e sem coleta** (navegação pura): nada muda.
- **Bloco com efeito e o próprio perfil (30.57):** antes da expansão, o item que é o próprio perfil que executa sai da
  lista (o @ ou o autor de "autor said texto", normalizado contra o `username` e os `handle` do perfil). Ele não vira
  etapa nem conta no `_settle_items`, e o rastro diz quantos saíram. O resto do leque passa pela porta de política
  item a item (ver perfis e Instagram).

Prova: `simulated` (`tests/test_saidas_obrigatorias.py`, QA Messenger falso com catálogo de teste, o caso real reconstruído
inclusive). Real: `not_run` (repetir a leitura do Outlook no android-01).

### Leitura visual de saída de etapa (item 12.5, ADR-070)

Na tela cega que o app declara, o valor que o ator leu NA IMAGEM conta como saída se um segundo leitor concordar às cegas. Liga só
com `ai.leitura_visual.enabled` e com o papel `ai.roles.leitura` ([ia.md §17](../ia.md)). O dado do app é
`leitura_visual.regioes` no `telas.yaml` (`tela`, `dentro_de` com o resource-id do contêiner, `saidas` e, opcional, `conteudo_de_terceiros`,
31.328): o carregador recusa
`dentro_de` vazio, nome de saída fora do alfabeto e tela que o arquivo não declara; `app_declarado/pacote.py` recusa saída que
nenhuma ação do catálogo entrega.

- **Chamada:** `read_value(name, element_id, value=<o que o ator leu>, source="visual")`, só `value_kind=text`. O executor tenta a
  ÁRVORE primeiro: se há texto, grava com `origem=arvore` (mesmo com `source=visual`); só a falha "sem texto nem descrição"
  (`LeituraSemTexto`) abre o caminho visual, e qualquer outra falha é recusa comum.
- `fora_do_app` recebe o valor real (`_tela_fora_do_app`), como defesa em profundidade.
- **`find_row(sender)` (31.340):** o ator acha a linha de uma lista cega pelo remetente sem escrever o valor de nenhuma outra: o executor lê cada linha candidata pelo mesmo
  caminho (`taskqueue/linha_por_remetente.py` + `ler_valor_visual`) e devolve só os `element_id` das que concordam. Detalhe em
  [`aprendizado.md`](aprendizado.md#a-partida-da-exploracao-a-pasta-que-nao-e-a-caixa-e-a-linha-pelo-remetente-31338-31339-31340).
- **Triagem visual:** valor com forma de código (4 a 8 dígitos) ou linha do recorte com número de código e palavra de código
  (`saidas.codigo_na_linha`) é recusado, e a recusa leva a etapa a `waiting_user` (sem nova tentativa do ator e sem lhe dizer que a
  linha tem código), como no caminho da árvore. Orçamento, prazo, crédito e recusa por política do leitor seguem o desfecho do ator
  (`desfecho_de_ia`); só falha do provedor e saída inválida viram `leitor_falhou`.
- **Conteúdo de terceiros (31.328):** a região que declara `conteudo_de_terceiros: true` (no Outlook, a linha da caixa de e-mail) tem
  como recorte o texto de quem escreveu a mensagem, não a tela do app. Nela a frase de verificação humana ("Confirm you're human")
  deixa de ser desafio na triagem do recorte e do valor: a prévia de um e-mail do Instagram parou o pedido misto da prova real do
  P-043 em `waiting_user`. O que não muda: forma de código, número com palavra de código, token, senha e o pedido de código
  (`SUBTIPO_CODIGO`) seguem recusados, a tela de desafio e a conta travada continuam pela árvore (`tela_sensivel`), e o padrão
  de toda região sem a marca é o de antes.
- **Receita:** `variaveis_da_receita` exclui as saídas `origem=visual`.
- **Concordância:** valor do ator normalizado (NFKC, caixa, espaços, pontuação das pontas, acentos mantidos) igual ao campo do
  leitor (grava-se o valor do LEITOR, limpo) E sequência contígua de palavras inteiras de uma das linhas. O leitor recebe só o recorte e os nomes das saídas.
- **Sem eco:** na recusa o ator recebe só o código (histórico, `actions.error`, evento); a transcrição nunca sai de
  `ler_valor_visual`, e o recorte recusado não é guardado.
- **Sucesso:** `step_outputs` com `origem=visual`, `leitor`, `frame_sha256` e `evidence_id` (o recorte vira evidência); a ação não
  leva o valor (`args.value` fica `**OMITIDO**`); o juiz da pós-condição recebe a imagem à força (`_verify(imagem_forcada=True)`).
- **Consumidor** (`Scheduler._valor_visual_sem_a_pessoa`): etapa com `side_effect` ou `commit_guard` que cita valor visual vai para
  `waiting_user` ("valor lido da imagem precisa da sua confirmação"), sem gastar tentativa; navegação e busca seguem. Confirmar na
  árvore do app consumidor não vale (circular). **Limite da v1:** não há resolução própria para a pessoa confirmar o valor; ela
  refaz o comando informando-o ou abandona o item.
- **Junto:** o `step_blocked.reason` (texto do modelo) passa por `razao_sem_segredo` (redação + triagem) antes de
  `steps.status_detail`, `attempts.error`, a nota da evidência e o evento `decision`; e as recusas da barreira de saídas
  (`step_done` recusado e `read_value` rejeitado) têm conta própria que `observe_screen` e `find_element` não zeram: com 4, a etapa
  vai para `fail_or_retry`.

Limites da v1: a retomada de um `waiting_user` com valor visual não avança (não há confirmação do valor; as saídas são abandonar ou
refazer), e uma falha passageira do provedor gasta a tentativa do par (a chave `repetida` é gravada antes da chamada).

Prova: `simulated` (`tests/test_leitura_visual.py`, `tests/test_leitura_visual_papel.py`, `tests/test_leitura_visual_conteudo_de_terceiros.py`). Real: `not_run`.

## Conta e portas do app da etapa (item 24.4)

Num comando que atravessa apps, cada etapa age pela conta da persona NO APP DELA, e as portas do despacho valem para
cada app, inclusive os que o worker só alcança depois de minutos de trabalho. Antes, a "conta esperada" era o rótulo
do aparelho (a conta do Instagram numa etapa do Outlook, R4) e as portas só eram conferidas no despacho (R8).

| Peça | Onde mora | O que faz |
|---|---|---|
| conta esperada | `Repository.conta_esperada`, usada por `Scheduler._app_context(…, profile_id=)` e por `Repository._insert_steps` | três desfechos: a persona tem UMA conta ativa com nome no app da etapa (`profile_accounts`) → essa; senão (nenhuma, ou mais de uma ativa, como as contas de portal da 049), numa etapa do app do aparelho ou sem app declarado → o rótulo do aparelho, que diz qual conta está nele (rótulo vazio → nenhuma); numa etapa de outro app → nenhuma (`None`), sem escolher |
| `{account_label}` da etapa | `Repository._insert_steps` (materialização e revisão) | numa etapa que declara app, resolvido com a conta daquele app: "Conta: {account_label}" no Outlook confere a conta do Outlook. Sem conta esperada, o molde fica SEM resolver: "Conta: " vazio casaria com qualquer conta na tela |
| molde sem conta no despacho | `Scheduler._conta_da_etapa`, em `_work` depois de `resolver_saidas` e antes de `policy_gate` | a etapa que ainda tem `{account_label}`: com a conta conhecida agora (cadastrada depois da materialização), `Repository.resolver_conta` a grava na linha; sem ela, `waiting_user` com o motivo ("não tem UMA conta conhecida em …") e `AJUDA_DA_CONTA_DO_APP`, sem tentativa. O executor tem a mesma checagem, defensiva: o molde que chegar a ele dá `waiting_user`, nunca uma conferência contra conta vazia |
| persona do item | `Repository.persona_do_objetivo` | a do objetivo; sem ela, a única vinculada ao aparelho; com duas, nenhuma (quem recusa a ambiguidade é a porta de sessão) |
| conta indisponível | `Scheduler._conta_indisponivel`, primeira coisa de `_portas_do_app` | app que declara conta (`needs_profile` ou provedor de sessão) sem conta da persona, ou com ela desativada, vira `waiting_user` com o motivo e `AJUDA_DA_CONTA_DO_APP`. App com provedor e SEM linha de conta fica com a porta de sessão, que conhece o legado (o `username` como conta do Instagram antes da 037). App que exige persona e não tem provedor (o Outlook), num item sem persona definida, também para: nada mais perguntaria, e a etapa sairia sem conta esperada |
| portas por app | `Scheduler._apps_do_objetivo` → `_portas_do_app(obj, rt, pacote, app_id)` no `_tick` | conta, app instalado, internet e sessão para cada `(app_id, pacote)` das etapas que faltam, na ordem; o primeiro que não está pronto segura o item |
| portas na troca | `Scheduler._portas_na_troca`, chamada por `_work` quando `(app_id, pacote)` da próxima etapa difere do da última executada | rede do aparelho (contrato C4, `rede_gate`) e depois `_portas_do_app`. A primeira etapa do worker não repete (o despacho acabou de passar) |

Regras:

- **Segurada na troca, a etapa não começa.** Nenhuma tentativa gasta, nada da etapa seguinte acontece; as etapas
  concluídas e as saídas gravadas (24.3) ficam. "Tentar novamente" atravessa a leitura comprovada, então o valor lido
  no primeiro app chega ao segundo sem reler.
- **Espera ou pessoa, como no despacho.** Rede exigida sem verificar é espera (`wait_reason='rede'`, sem
  `waiting_user`). Conta ausente ou desativada, app que só uma pessoa resolve e sessão que precisa de pessoa são
  `waiting_user`. Instalação e login automáticos não rodam de dentro do worker: `run_device_job` recusa o aparelho,
  que é do próprio worker, então `_portas_do_app` anota a espera ("instalando…", "verificando a sessão…") e o worker
  sai; o tick seguinte, com o aparelho livre, roda o trabalho e depois despacha o item de novo.
- **Sem conta esperada, a conferência da conta não passa.** A etapa que confere a conta (`{account_label}`) e não
  tem UMA conta conhecida no app dela para no despacho, antes de assumir a etapa. Enquanto isso, o título, o objetivo
  e a pós-condição dela (e `plan_versions.steps`) mostram o molde `{account_label}`: o painel exibe o molde num item
  parado por isso, e não uma conta. Num app que declara conta (`needs_profile` ou provedor), quem para antes é
  `_conta_indisponivel`, e a pessoa vê o motivo dele. O ator só recebe "conta esperada: —" numa etapa que não
  confere a conta.
- **Sessão vencida na troca devolve o app ao estado conhecido.** A releitura `observe_only` abre o app da etapa
  seguinte na tela inicial dele. Numa troca de app isso é o esperado; na recuperação da tela atual (mesmo app) as
  portas não são repassadas.

Prova: `simulated` (`test_conta_do_app_da_etapa.py`: conta esperada pelo app da etapa no ator e na pós-condição,
conta ausente e item sem persona segurando no despacho sem tentativa, conta desativada no meio parando na troca e a retomada sem reler,
persona sem conta no app de outra etapa sem declaração de conta parando com o molde sem resolver e concluindo depois do
cadastro sem reler, duas contas ativas no app do aparelho usando o rótulo do aparelho e parando sem ele,
rede do aparelho segurando e soltando a etapa do app seguinte, porta com trabalho chamada do worker). O aparelho de
teste encena um app só: o "segundo app" é outro registro no mesmo pacote do QA. Outlook e Instagram num aparelho
real, com conta indisponível, interrupção e retomada: `not_run` (fecha no 24.9). O texto "conta esperada" do prompt
do ator (`planning/prompts.py`) não mudou: é o valor que chega a ele que agora é a conta do app da etapa.

## Interrupção e retomada no meio da troca de app (item 24.7)

A troca de app é o ponto mais longo de um comando que atravessa apps (a etapa do app seguinte pode esperar rede,
sessão ou instalação), e por isso é onde a interrupção mais cai. O que vale ali, conferido no código de hoje com
aparelho e provedor falsos (nenhuma mudança de código foi precisa: os mecanismos gerais já cobrem a troca):

| Interrupção | O que acontece | Onde mora |
|---|---|---|
| reinício do backend com a etapa do app seguinte em curso | a tentativa vira `interrupted` e é devolvida; a etapa volta a `ready` e o despacho retoma dela, com as portas de todos os apps que faltam. A leitura comprovada não volta, e o valor vem de `step_outputs` | `Scheduler.reconcile_after_restart` → `_reconciliar`; `_tick` → `_apps_do_objetivo` |
| reinício depois do efeito no app seguinte | a ação `intended` vira `unknown`; a etapa com efeito disparado só verifica pela tela e nunca reenvia | `_reconciliar`, `StepExecutor` (reconciliação) |
| reinício com a troca segurada pela porta | não há etapa em curso: o objetivo segue `running` com a espera anotada, e o despacho do backend novo repassa as portas antes da etapa do app seguinte | `Repository.dispatchable_objectives` |
| pausa | a etapa cede no ponto seguro (`yielded`, tentativa devolvida) e, retomada, segue do app em que estava | `Scheduler._apply`, `RunService.resume` |
| cancelamento | a etapa em curso para no ponto seguro (`cancelled`) e as seguintes são canceladas; a segurada na troca nem começa. O concluído, a saída gravada e o relatório ("Valores lidos entre etapas") ficam | `_apply`, `_finish_cancel`, `RunService.report` |
| sucessora (ADR-047) | nasce só de `needs_input`, que nunca executou etapa. No meio da troca a execução está `running` (ou `cancelling`/`cancelled`), então a resposta é recusada (`invalid_state`) sem criar execução: nenhuma segunda execução refaz o que a primeira fez | `ComandoAssistido.sucessora` |

Regras:

- **Uma leitura só.** Em todos os casos a etapa de leitura tem uma tentativa e um `read_value` concluído, e o valor que
  chega ao app seguinte é o gravado (a linha da etapa já resolvida, ou `resolver_saidas` depois da retomada).
- **Efeito comprovado nunca é refeito**, nem depois da queda: a reconciliação decide pela tela.
- **Continuar numa execução nova não existe.** Depois de cancelada, a execução não se retoma ("Tentar novamente"
  recusa execução cancelada, e o item `cancelled` não aceita a decisão "repetir"), e o "Repetir" do painel cria outra execução que planeja e executa
  tudo de novo, inclusive a leitura e o efeito: é o gesto de repetir, não o de continuar. Uma sucessora que herde as
  etapas comprovadas e as saídas da antiga exige decisão do dono: que plano ela segue (o da antiga, sem replanejar,
  ou um novo, casado por chave de etapa), por quanto tempo o valor lido ainda vale e o que fazer se a persona ou o
  aparelho mudou.

Prova: `simulated` (`test_interrupcao_entre_apps.py`: reinício com a etapa do segundo app em curso, depois do efeito
no segundo app e com a troca segurada pela rede; pausa e retomada; cancelamento em curso e na troca segurada; sucessora
recusada no meio da troca e depois do cancelamento). Um caso usa o aparelho falso com DOIS pacotes
(`FakeQaDevice.pacotes_extras`): a queda vem com a etapa do segundo app em curso e o primeiro app à frente, e o backend
novo retoma com o contexto do segundo app, que é aberto uma vez e onde a conta é conferida e a mensagem sai. Outlook →
Instagram num aparelho real com interrupção e retomada: `not_run` (24.9).

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
| `app.session` (`session: ready`, `account: bound_profile` opcional) | `account_session.py::AppSessionProvider(db, *, tem_provedor_de_sessao, session_max_age_s, unknown_retry_cap, agora, bus)` | `apps`, vínculo ativo, `instagram_profiles` (status, `offline_policy`), `instances` (localidade), `profile_accounts` (a conta do perfil no app, sem `host`), `account_credentials` (`status`, `consent_at`) e `account_sessions` no aparelho do alvo (sem linha nele, a mais recente: `other_device`) — desde a 049; antes, `instagram_credentials`/`instagram_sessions` | `session.connect` (`logged_out`, `relocated`), só com credencial utilizável no cofre: guardada, **consentida** e não recusada (`CredentialState.unconsented` → `blocked` `no_consent`, ADR-040) | `session.verify` (`unobserved`, `other_device`, `stale`, `account_unproven`). `account_unknown` (app sem provedor de sessão) fica sem ação; `needs_person` e `wrong_account` de app sem provedor são de pessoa |

- **`device.state`.** Sem runtime neste processo (papel `api`, ou aparelho ainda não carregado), o observado não
  existe: `unknown`. `DeviceManager.devices` cumpre `Mapping[str, DeviceRuntimeView]` por estrutura. `desired_state =
  stopped` não bloqueia, porque o rodízio de hoje liga sob demanda; vira o código `stopped_by_decision` e aviso nos
  riscos.
- **`app.installation`.** Refaz sem efeito as regras de `ReleaseService.promoted_release` e
  `AppState.release_no_aparelho`, que no legado moram em métodos que também gravam. Mais nova e não voltada é `held`
  (ADR-026). `verifying` sem dono é `unknown`, e a resposta é `app.verify`, nunca instalar. Espalhar o app para quem
  não o tem como principal é `blocked` (`not_distributed`): Distribuir é decisão de pessoa.
- **`app.session`.** Segue a ordem da porta de sessão (`AppState._session_gate`), sem as lambdas que autenticam. Da
  credencial só sai "tem, utilizável ou recusada", nunca o `secret_ref`. Qual app tem provedor de sessão é injetado por
  quem compõe (`tem_provedor_de_sessao`), porque o catálogo fica abaixo de identity no grafo.

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
| `session.connect` | `despacho.pedir_trabalho_de_app` | `sessoes.for_package(<pacote do app>).ensure_session(automatic=True)` (`SessaoDeclarada`, ADR-052): o cofre pelo canal sensível (ADR-025) |
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

- **Quem monta:** `modules/execution/presentation/router.py::create_run` chama `taskqueue/service.py::RunService.relatorio_de_recursos(run_id)` depois de
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
- **Prévia é `mode='plan' AND started_at IS NULL`** (P12, 03/10/2026; decisão da orquestradora: não é defeito). A
  execução em `mode=plan` para em `planned` e só executa pelo início explícito (`POST /api/runs/{id}/start`), e daí
  em diante gasta decisões como qualquer outra. Contar como prévia toda linha com `mode='plan'` dava "12 de 25 prévias
  executando, 211 decisões" na reavaliação; medido no central em 03/10 (só leitura), as 11 que executaram tinham sido
  iniciadas 12 a 44 s depois de criadas. O `run.updated` do início leva `iniciada_por` (a pessoa da sessão ou `panel`
  pela rota, `sistema` no `mode=execute`; [contrato, adendo v0.79](../api-contract.md)). Prova `simulated`:
  `backend/tests/test_inicio_com_autor.py`.

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
| `RUN` (`runs.status`) | 11 | `planning` (`create_run`) | `planning → needs_input, planned, running, failed, cancelled`; `needs_input → cancelled`; `planned → running, cancelled`; `running`/`paused` → pausar/retomar, `cancelling` e os três fechamentos; `cancelling → cancelled, completed, completed_with_issues` (e reafirma); `running`/`paused`/`cancelling` → `awaiting_person` e `awaiting_person → running, paused, completed, completed_with_issues, cancelling, cancelled` (e reafirma; 29.93); `completed` e `failed` terminais |
| `OBJECTIVE` (`objectives.status`) | 7 | `pending` (`materialize`) | `pending → running, waiting_user, succeeded, failed, cancelled`; `running → waiting_user, uncertain, succeeded, failed, cancelled`; `waiting_user → pending, running, failed, cancelled`; `uncertain → pending, running, failed` (só por decisão de pessoa); `failed → pending` (e reafirma); `succeeded` e `cancelled` terminais |
| `STEP` (`steps.status`) | 11 | `pending` (`_insert_steps`) | a tabela que já existia em `taskqueue/states.py`, sem mudança |
| `ATTEMPT` (`attempts.status`) | 6 | `running` (`claim_step`) | `running → succeeded, failed, interrupted, uncertain, cancelled`; os cinco são terminais: nenhuma tentativa reabre |

- **Reafirmação (`x → x`) só onde o código a faz:** execução em `cancelling`, `awaiting_person`, `completed_with_issues` e `cancelled`;
  objetivo `failed`. Liberar `pode(x, x)` em geral esconderia o erro que a tabela existe para mostrar.
- **Imposição (15.15 F7):** `Repository._conferir` levanta `InvalidTransition` antes de gravar em `set_run_status` (com
  `so_se`, só quando a troca vale), `set_objective` e `finish_attempt` (a cerca `PosseDaEtapaPerdida` vem primeiro). Um handler
  em `main.py` devolve 409 `invalid_transition` ao gesto que chegou depois de o estado mudar (e grava um evento `log` `warn` com o método e o modelo da rota, sem o id: o backend não tem log de acesso). A aresta `completed_with_issues
  → cancelled` está declarada: é o vencimento do 31.50 fechando a execução com cancelamento pedido.
- **`awaiting_person` (29.93):** o trabalho automático acabou e um objetivo espera um gesto da pessoa (`waiting_user`).
  Não é terminal, mas está em `RUN_SEM_TRABALHO` (`app/models.py`): grava `finished_at`, é de onde o vencimento do 31.50
  conta e é o que a retomada reabre. Só `waiting_user` leva a ele; execução só com `uncertain` segue
  `completed_with_issues`. O fechamento de pedido o lê como o `completed_with_issues` de antes; o snapshot o traz por 7
  dias depois de `finished_at`. A purga de eventos e a retenção de evidência por idade o poupam como aberto, mesmo com
  `finished_at`; o `recompute_run` o reafirma quando só o detalhe muda (como o `completed_with_issues`). Diferente de
  `needs_input`, a pergunta antes de agir.
  - **Parada sem digest:** ao parar, o `_settle_run` do worker solta o explorador, a trava de rascunho e acorda os
    pedidos (`Scheduler.on_run_parada` → `AppState._execucao_parada`), na mesma hora em que a main soltava ao parar
    em `completed_with_issues`.
  - **Assentamento exatamente uma vez, pela marca `runs.assentada_em` (#382, migração 113):**
    - Quem assenta grava a marca por compare-and-set (`Repository.marcar_assentada`: `WHERE assentada_em IS NULL` e
      estado final). Só quem gravou assenta; o outro não faz nada, mesmo em outro backend.
    - **Execução comum, pelo worker, em linha:** no `finally` do `Scheduler._work`, o `recompute_run` e a marca vão
      na MESMA `tx()`, e o `_settle_run(venceu=...)` chama o `on_run_settled` só se este worker gravou.
    - **A rede, para quem fecha sem worker:** `Repository.set_run_status`, ao gravar um estado final vindo de um
      estado de trabalho (não de `planning`/`needs_input`/`planned`), agenda `_assentar_sem_worker` para depois do
      COMMIT (`Database.depois_do_commit`). Ele não assenta se há worker deste backend num objetivo da execução
      (`Scheduler._tem_worker_da_execucao`) e só assenta se ganhar a marca. No caminho comum ele chega depois do
      COMMIT do worker, já com a marca, e não faz nada. Sobra para ele a saída da espera da pessoa (abandonar,
      vencer, cancelar) e o cancelamento órfão (29.103: `running`/`paused` sem worker vivo, fechada pelo
      `_finish_cancel`). De outra thread (o vencimento roda em `to_thread`), o gancho é agendado no laço por
      `call_soon_threadsafe`, com a marca já gravada na thread.
    - **Zerar:** só o `set_run_status` grava `runs.status`, e ele zera a marca ao ir a qualquer estado não final que
      não seja `cancelling` (a retomada, a volta a esperar a pessoa): a execução reaberta assenta de novo ao fechar,
      como antes. Cancelar uma execução já assentada (a `completed_with_issues` incerta) não zera e não assenta de
      novo (o D1).
    - **Falha depois de marcar:** o assentamento que estoura depois da marca não se repete (como antes da marca, no
      `_settle_run`): o log diz a execução, e o digest se recupera pelo `backfill_licoes` manual.
    - **Quem perde a marca (29.108):** o worker cujo compare-and-set perdeu não assenta, mas chama o `on_run_parada`
      para soltar o que é do processo dele (a trava de rascunho em memória; acorda os pedidos), sem o digest. Um
      `depois_do_commit` pedido dentro de um `savepoint()` desfeito sai junto com ele.
    - Confirmar a etapa parada devolve o objetivo às etapas seguintes, e quem fecha é o worker.
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

## Efeito marcado pela ação e efeito repetido (29.58, 03/10/2026)

Na execução 5f2de5 a IA, numa etapa SEM efeito declarado, digitou e tocou em enviar. O toque foi gravado sem efeito,
sem a trava de não repetir, sem a guarda e sem a aprovação da etapa que declara o envio, e essa etapa enviou de novo.

- **Recusa antes de agir** (`StepExecutor`, `efeito_fora_da_etapa`). Numa etapa sem efeito, toda ação de
  `EFFECT_CAPABLE` é perguntada: ela parece disparar um efeito externo? Se sim, é recusada sem tocar o aparelho:
  - grava `actions.side_effect=1` com `status=rejected`;
  - o motivo manda o ator terminar a etapa e deixar o efeito para a etapa que o declara;
  - conta em `errors_in_row` (4 → `fail_or_retry`), e a receita diverge;
  - conta na métrica `executor.efeito_fora_da_etapa` (`origem`, `ferramenta`), que serve para vigiar falso positivo.

  Como a recusa decide se a ação "parece efeito":
  - `is_commit_action=true` conta sempre;
  - **com catálogo**, o gatilho é o `commit_selector` das capacidades com efeito do app, casado **EXATO** no texto
    cru em `text=` e `desc=` (o `id=` casa como sempre). Por substring sem caixa, `text=Follow` casaria "followers" e
    `text=Following` o rótulo "following" do perfil, e abrir a lista de seguidores numa leitura seria recusado.
    "New post", que só abre a criação, não é gatilho;
  - **sem catálogo**, ou com um catálogo que não declara nenhum gatilho (o do Outlook, hoje), o vocabulário de
    `looks_like_commit`, como na etapa com efeito sem seletor;
  - `type_text` com Enter conta só num campo de composição (mensagem, comentário, resposta, legenda); num campo de
    busca, não.

  A aprovação (`_approval_gate`) continua por etapa: com a recusa, o efeito só sai na etapa que o declara e passa
  pela política da capacidade dela.
- **Efeito repetido fecha `uncertain`** (`_efeito_repetido`), como defesa em profundidade para o que a recusa não
  pegar. Vale para uma etapa com efeito que disparou, com duas fontes:
  - `Verdict.copias`: o verificador conta as cópias do efeito DESTA execução na tela;
  - `Repository.copias_pelas_acoes`: as etapas da mesma etapa-modelo e do mesmo `item` que dispararam o efeito
    (`done`/`unknown`) no mesmo objetivo e na mesma versão do plano. Outra versão fica de fora, porque "repetir" é
    gesto da pessoa e revisa o plano.

  Com 2 ou mais cópias, a etapa e o objetivo fecham `uncertain` "efeito repetido (N)", nunca "sucesso comprovado" e
  nunca falha (o efeito existe). Nada é reenviado. A etapa grava
  `steps.result.efeito_repetido = {"copias": N, "fonte": "verificador"|"acoes"}`, ausente sem repetição. A prova de
  fluxo do aprendizado (30.42) lê esse campo; o contrato está no api-contract.

  A contagem pelo texto na tela foi descartada: mensagens iguais de execuções anteriores dariam falso positivo.
- **Testes** (`simulated`): `backend/tests/test_efeito_pela_acao.py`. Cobrem a função pura, a 5f2de5 com aparelho
  falso (uma mensagem só), o "Post" do Instagram numa etapa sem efeito (sem toque e sem aprovação aberta), as duas
  cópias vistas pelo verificador e a contagem pelas ações.
- **Prova real**: `not_run`. A reprodução no QA Messenger (android-10, sem conta), com IA pontual, fica para a vez da
  orquestradora.

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
| `mode=plan` com `plan_report`; `source: error` sem 500; `objectives.resource_plan` no `materialize` | implementado | `simulated` (`backend/tests/test_plan_report_na_execucao.py`, 4 testes) | `modules/execution/presentation/router.py::create_run`, `RunService.relatorio_de_recursos`, `_fotografar_recursos` |
| Refoto de `resource_plan` no despacho | não feito | `not_run` | design §11 |
| Máquinas de estado de execução, objetivo e tentativa: conferir e registrar, sem bloquear | implementado | `simulated` (`backend/tests/test_maquinas_de_estado.py`, 15 testes; fixture `tests/conftest.py::_transicoes_dentro_da_tabela` na suíte inteira) | `modules/execution/domain/states.py`, `Repository._conferir` |
| Máquinas de estado impostas; contagem em `/api/health` | não feito | `not_run` | próximo passo (ADR-038) |
| H parte 2 e K2 em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| H parte 1 em PostgreSQL | implementado | `simulated` (CI run `36324634678` em `793fe00`) | CI `workflow_dispatch` |
| G em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Execução de skill numa conta real | implementado | `not_run` (exige autorização) | — |
