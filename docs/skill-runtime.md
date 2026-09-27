# Runtime de skills: compilador e baixa para `Plan`

Como um documento [`automation/v1alpha1`](skill-dsl.md) vira o `Plan` que o runtime de hoje já executa. É a fase E da
evolução arquitetural ([design](design/evolucao-arquitetural.md) §12.1, §12.3 e §18;
[ADR-033](decisoes.md#adr-033--ir-de-skill-e-dsl-automationv1alpha1)). A versão que guarda o documento está em
[`dominios/skills.md`](dominios/skills.md); o catálogo que o compilador lê, em
[`dominios/capabilities.md`](dominios/capabilities.md).

Caminhos relativos a `backend/app/`, salvo indicação. Código: `e8c51e0`, integrado em `7403a7e`. Fiação no runtime
(fase G): `aa3575b` e `4e210c4`, integrados em `0b736f3`. Resolução de intenção (fase I): `00633d5` e `0795cc7`,
integrados em `21b1fff`.

**Estado (27/09).**

- O compilador e a baixa estão prontos e testados (`simulated`, sem banco, sem aparelho e sem IA).
- **A fiação está feita (fase G).** Com `skills.enabled`, uma skill publicada que casa o comando vira o plano da
  execução por este compilador ([abaixo](#fiação-no-runtime-fase-g); o ciclo inteiro está em
  [execution](dominios/execution.md)). Prova `simulated`; nada implantado; conta real `not_run`.
- **A RESOLVE passa pelo `IntentResolver` (fase I).** Os valores chegam ao compilador já validados e normalizados pelo
  tipo; o que a resolução não decide vira pergunta, e a execução para em `needs_input` sem compilar
  ([abaixo](#resolve-e-compile-run_planningpy)). Suíte SQLite 2252/2252 no branch da fase (`simulated`); PostgreSQL:
  `not_run`.

## O pipeline

```
documento (mapa JSON/YAML já lido, ou SkillDocument)
  ──parse_document──▶ SkillDocument (pydantic, extra="forbid")          erro → E_SCHEMA, E_API_VERSION, …
  ──_Compilacao.executar──▶
      _documento_raiz   parâmetros, comando, casos de validação, comando ocupado
      _requisitos       apps, segredos, recursos (por união)
      _expandir         nós: capability (CapabilityLookup) · goal · skill (SkillLookup, recursivo)
      _grafo            foreach (cópias, contiguidade, ${item}) e when × dependências
      _saidas           outputs
      _ligar            só com parâmetros: valores ligados, when avaliado, nós omitidos saem
  ──▶ ProcessGraph: o IR, imutável e com hash                                            [domínio]
  ──baixar──▶ um PlanStep por nó (build_step do catálogo, ou PlanStep livre) + o Plan    [infraestrutura]
  ──validador do Plan (models.py), a última porta──▶ ExecutableGraph(plan, ir_hash, plan_hash)
```

- **Domínio:** `modules/skills/domain/compiler.py::SkillCompiler.compilar(documento, *, version, parameters=None,
  occupied_match_keys=())` devolve `CompileResult(graph, issues)`. Qualquer erro deixa `graph = None`.
- **Infraestrutura:** `modules/skills/infrastructure/lowering.py::SkillPlanCompiler(pacote_do_app, skills=None)`.
  Seu `compilar(...)` compila no domínio, baixa e devolve `PlanCompileResult(issues, graph, executable)`.
- **O que o compilador pede ao mundo** chega por duas portas definidas no próprio `compiler.py`, do lado de quem
  consome:
  - `CapabilityLookup` (`app_known`, `has_catalog`, `definition`, `offered`), que
    `modules/capabilities/infrastructure/catalog_registry.py::CatalogCapabilityRegistry` cumpre por estrutura;
  - `SkillLookup.locked(skill_id, version) -> LockedSkill(document, content_hash)`: a versão exata de uma skill
    composta.
- **O IR fica no domínio; a baixa, na infraestrutura.** `Plan` e `build_step` são legado (`app.models`,
  `app.planning`), e a regra D2 os esconde do domínio.
- **Dois momentos de compilação**, os dois determinísticos, sem IA e em milissegundos:
  - sem valores (`parameters=None`), em `draft → candidate`: entram todos os ramos do `when`, para achar erros, e os
    parâmetros ficam como `{nome}` no `Plan`, do mesmo jeito que um fluxo guarda o plano congelado;
  - com os valores do comando, na resolução de cada execução: o `when` é avaliado, os nós omitidos saem e os valores
    vão para `Plan.parameters`.
- **Erro nunca vira exceção.** Tudo sai como `CompileIssue` ([códigos](skill-dsl.md#códigos-de-erro-e-avisos)),
  inclusive na baixa: `E_UNKNOWN_CAPABILITY`, `E_MISSING_BINDING` e `E_PLAN_INVALID`, com o caminho vazio (o
  documento inteiro).

## O IR

`modules/skills/domain/ir.py`, tudo valor imutável:

- `ProcessGraph`: `skill_id`, `skill_version`, `name`, `app`, `command_template`, `parameters` (`ParameterDecl`),
  `arguments` (`None` = compilação sem valores), `required_apps`, `secrets`, `resources` (`ResourceDecl`), `nodes`,
  `outputs` (`OutputDecl`), `success_criteria` e `uses_lock` (`LockEntry`).
- `ProcessNode`: um nó já expandido.
  - `node_id` qualificado (`abrir_abrir_conversa`), `kind` (`capability` ou `goal`) e `app`;
  - `skill_id`/`skill_version` do **dono** do nó: o filho, numa composição;
  - `capability` (`CapabilityRef`) ou `goal` (`GoalContract`), e os `bindings`;
  - `depends_on` sempre explícito, `for_each`, `when` (`WhenCondition`), `timeout_s`, `retries`,
    `required_delivery_level`, `strategies`, `side_effect` e `collects`.
- `TextExpr`: texto com expressão é uma sequência de pedaços tipados (`Lit`, `ParamRef`, `ItemRef`), não uma string
  com marcador.
  - A forma é normal: literais vizinhos se juntam e literais vazios saem. Duas grafias do mesmo texto dão o mesmo IR.
  - `template()` produz o modelo que o runtime de hoje resolve: `{x}` e `{item}`.

## Baixa para `Plan` e `PlanStep`

`lowering.py::baixar(grafo, pacote_do_app)`, nó a nó:

- **Nó `capability`:** `load_catalog(pacote).build_step(CapabilityNode(key=node_id, capability, depends_on,
  bindings, for_each))`.
  - É o **mesmo** `build_step` do planejador. Lá já moram a política de texto, as guardas, o `commit_selector`, a
    pós-condição do catálogo e o `max_attempts = 1` com efeito. O compilador reusa, não reimplementa.
  - Depois, o passo ganha, por `model_copy`: `app_id` (só quando difere do app do plano), `origin`, `timeout_s` (se o
    nó disse), `max_attempts = retries + 1` (se o nó disse e não há efeito) e o nível de entrega na pós-condição (se o
    nó disse).
- **Nó `goal`:** `PlanStep.model_validate` com `title`, `goal`, `precondition`, `postcondition` e `commit_guard` em
  forma de modelo; `max_attempts` = 1 com efeito, senão `retries + 1`, senão 3.
- **O `Plan`:** `summary` = `metadata.name`, `app_id`, `app_package`, `required_apps`, `parameters`,
  `success_criteria`, `steps` e `planner = {provider: "skill", model: "skill:<id>@<n>", simulated: false}`
  (`lowering.py::PLANNER_PROVIDER`).
- **A última porta** é o validador do `Plan` e do `PlanStep` (`models.py`). O que ele recusar vira `E_PLAN_INVALID`.

### `PlanStep.key == node_id` e `PlanStep.origin`

- **A chave da etapa é o id do nó** (decisão 2). O padrão de `contracts/skills/v1alpha1.py::NODE_ID` é o mesmo de
  `PlanStep.key`, e um teste confere.
  - Isso preserva a identidade de receita: `step_template_hash` usa `template_key or key`.
  - Limite aceito na v1alpha1: o mesmo `OPEN_THREAD` com outro id de nó (avulso `abrir_conversa`, composto
    `abrir_abrir_conversa`) não compartilha receita. A identidade por capability fica para a fase K.
- **`models.py::StepOrigin`** = `{skill_id, skill_version, node_id, strategies}`, e
  `PlanStep.origin: StepOrigin | None`.
  - Mora no próprio `Plan` porque a recuperação, a expansão do `for_each` e a revisão releem `runs.plan` e reinserem
    etapas. Sem a origem ali, a trilha se perderia no primeiro replanejamento.
  - As cópias do `for_each` (`taskqueue/foreach.py::expand`, por `model_copy`) mantêm a `origin` do modelo.
  - `Field(default=None, exclude_if=lambda v: v is None)`: plano que não veio de skill não ganha a chave, e
    `runs.plan` e `plan_versions.steps` continuam byte a byte iguais. O painel ignora campo que não conhece.

## Expansão de `uses`

`compiler.py::_Compilacao._chamada`, a partir do nó `skill:` de chamada:

- **Prefixo.** O nó de chamada `abrir`, de uma skill com os nós `n1…nk`, vira `abrir_n1…abrir_nk`. Em composição
  aninhada, os prefixos se acumulam.
  - O id expandido precisa caber em `NODE_ID` (`E_NODE_ID_TOO_LONG`) e não pode repetir outro
    (`E_NODE_ID_COLLISION`).
- **Parâmetros.** Cada `${parameters.p}` do filho é trocado, pedaço a pedaço, pela `TextExpr` que a chamadora passou
  em `with.p`. É substituição de dado, nunca de código.
  - Sem valor na chamada, vale o `default` do filho; parâmetro opcional sem padrão fica vazio.
  - Parâmetro obrigatório sem valor e sem padrão dá `E_MISSING_ARGUMENT`, e a expansão desse nó para ali, sem repetir
    o erro em cada nó do filho.
- **Dependências.**
  - As raízes do filho herdam o `depends_on` do nó de chamada.
  - Quem depende do nó de chamada passa a depender dos **sumidouros** do filho (`_NoLocal.sumidouros`).
  - O compilador **sempre emite** `depends_on`: `[]` explícito na raiz independente, a lista cheia nas demais.
- **`when` e `foreach` da chamada** valem para todos os nós do filho.
- **Requisitos e recursos do filho** entram por união (`_requisitos`). Conflito dá `E_RESOURCE_CONFLICT`.
- **Trava.** Cada filho entra em `ProcessGraph.uses_lock` com a versão e o hash do documento compilado. Publicar uma
  versão nova do filho não muda a composta já publicada.
- **Limites.** Profundidade máxima de 3 (`MAX_COMPOSITION_DEPTH`, `E_COMPOSITION_DEPTH`); ciclo pela pilha de
  expansão (`E_SKILL_CYCLE`).
- **Trilha.** `ProcessNode.skill_id` é o do filho, e `planner.model` é o da raiz: no exemplo `ig.ler_conversa`, as
  etapas `abrir_abrir_*` saem com origem `ig.abrir_conversa@1` e a etapa `ler`, com `ig.ler_conversa@1`.

## Determinismo e hash

- Sem I/O, sem IA e sem relógio: a mesma entrada dá o mesmo IR, o mesmo `Plan` e os mesmos hashes.
  - A ordem das chaves de um mapa não importa, nem a dos parâmetros ligados.
  - Espaço dentro de `${ … }` não importa.
  - Versão, valor de parâmetro e conteúdo mudam o hash.
- **Uma só função de hash.** `ir.py` reexporta `canonical_json` e `content_hash` de `domain/document.py` (`aa3575b`):
  chaves ordenadas, separadores fixos, sem escapar acento. Documento, trava de composição, IR, plano e `PlanReport`
  usam a mesma função (`backend/tests/test_habilidades_na_execucao.py::test_um_so_hash_canonico_para_documento_trava_ir_e_plano`).
- `ProcessGraph.content_hash()`: sha256 do JSON canônico do IR.
- `lowering.py::plan_hash(plan)`: sha256 do JSON canônico de `plan.model_dump(mode="json")`.
- Existem, então, três hashes: o do documento (`skill_versions.content_hash`), o do IR (`ExecutableGraph.ir_hash`) e
  o do plano (`ExecutableGraph.plan_hash`).
- **A execução grava o do documento** em `runs.skill_hash` (`run_planning.py::RunPlan.skill_hash` =
  `resolved.version.content_hash`). No fluxo legado, é o hash calculado na leitura pelo `LegacyFlowAdapter`.

## A invariante e os testes que a provam

**Invariante (decisão 4):** o compilador é o **único** produtor de `Plan` para skill nova, e nunca gera nem executa
Python. A candidata que vier do LLM é dado validado contra o esquema.

- Para conteúdo `schema_version` 1, o único caminho de documento a `Plan` é `SkillPlanCompiler`.
- Conteúdo legado (`schema_version` 0: fluxo, ou v1 de fluxo adotado) não é compilado. Passa direto por
  `modules/skills/infrastructure/legacy_flows.py::legacy_plan`, porque o plano congelado já é um `Plan`.
- **No runtime**, o "único" vale desde a fiação G: `run_planning.py::SkillRunPlanner.plan` é o único caminho de
  `ResolvedSkill` a `Plan`, e só chama `SkillPlanCompiler` ou `legacy_plan`.

Os testes, em `backend/tests/test_compilador_de_skills.py` salvo indicação (`simulated`):

| # | O que a §12.1 pede | Teste | Prova |
|---|---|---|---|
| 1 | regra D15 por AST | `test_compilador_nao_avalia_nem_carrega_codigo_por_ast` | `simulated` |
| 2 | golden: `planner.provider == "skill"` e ida e volta idêntica | `test_golden_valido_compila_para_o_plano_esperado` | `simulated` |
| 3 | Python num texto é texto inerte; campo fora do esquema dá `E_SCHEMA` | `test_codigo_python_num_texto_e_texto_inerte`, `test_campo_de_codigo_no_no_e_recusado`, `test_expressao_fora_da_gramatica_e_recusada` | `simulated` |
| 4a | execução com `runs.skill_id` tem `provider == "skill"`, e `runs.skill_hash` é o `content_hash` da versão | `backend/tests/test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha` | `simulated` |
| 4b | uma recompilação da versão com os mesmos parâmetros dá o mesmo `runs.plan` | — | `not_run` (o determinismo do compilador está provado à parte) |

- **D15** varre `contracts/skills/v1alpha1.py` e todo `modules/skills/**/*.py`:
  - nenhuma chamada ou referência a `eval`, `exec`, `compile` ou `__import__`, e nenhum `FunctionType`;
  - nenhum import de `importlib`, `pickle` ou `marshal`;
  - nenhum import de módulo de IA (`adapters.ai`, `planning.provider`, `planning.routing`, `planning.prompts`,
    `planning.parsing`, `planning.training` e os provedores).
  - Emenda de 27/09: a baixa pode importar `planning.capabilities`, porque usa `build_step` (design §9, linha D15).
- **O golden** confere também que toda etapa tem `origin` com `node_id == key` e que o documento compila sem valores.
- **No teste de texto inerte**, `eval`, `exec` e `compile` são trocados por uma função que explode, e o código no
  título, no objetivo e na pós-condição sai no `PlanStep` como texto.
- **Outros testes do mesmo arquivo:**
  - `test_golden_invalido_da_o_erro_esperado` e `test_todo_codigo_de_erro_tem_fixture_invalida`: cada `E_*` tem
    fixture, com erro e caminho exatos;
  - `test_mesma_entrada_mesmo_ir_e_mesmo_hash` e `test_hash_ignora_grafia_do_texto_mas_nao_o_conteudo`:
    determinismo;
  - `test_abrir_conversa_sai_identica_ao_que_o_planejador_monta_pelo_catalogo`: a etapa é a do planejador, mais a
    `origin`;
  - `test_ler_conversa_expande_a_composicao_e_religa_as_dependencias` e
    `test_foreach_sobre_skill_composta_e_when_estatico`: composição, `foreach` e `when`;
  - `test_plano_legado_continua_byte_a_byte_igual`: `origin` é aditivo;
  - `test_erro_nunca_vira_excecao`, `test_parametros_ligados_na_execucao`,
    `test_copias_do_foreach_que_colidem_depois_de_truncar`, `test_nome_de_parametro_com_cara_de_credencial`,
    `test_match_key_e_a_normalizacao_dos_fluxos` e `test_registro_de_capabilities_cumpre_a_porta_do_compilador`.

## Fiação no runtime (fase G)

O que liga o compilador à execução. O ciclo inteiro, a trilha e as proteções estão em
[execution](dominios/execution.md).

### RESOLVE e COMPILE: `run_planning.py`

`modules/skills/infrastructure/run_planning.py::SkillRunPlanner(registry, pacote_do_app, skills=None, *, classifier=None,
disambiguator=None)`:

- `resolve_intent(command, profile_ids)` (fase I) pergunta ao `IntentResolver.standard`: modelos
  (`CompositeSkillRegistry.candidates`) → tipos (`ParameterExtractor`) → semântica → LLM. Sem `classifier` nem
  `disambiguator`, as duas últimas usam o provedor nulo, que não chama IA. Devolve um `IntentResolution`
  (`resolved`, `needs_input` ou `no_match`; [skills](dominios/skills.md#resolução-de-intenção)).
- `plan(resolution)` devolve `RunPlan(resolution, plan, issues)`:
  - resolução sem intenção escolhida (pergunta ou empate) → `plan = None`, sem compilar. As perguntas ficam em
    `RunPlan.questions`;
  - conteúdo `schema_version` 0 → `legacy_plan(resolvida)`. O plano de `flow:<id>@1` é o mesmo que `FlowStore.match`
    devolvia, com os valores como vieram;
  - conteúdo `schema_version` 1 → `SkillPlanCompiler.compilar(documento, version=n, parameters=...)`, a segunda
    compilação da §12.1. Os `parameters` são só os que o comando deu, já normalizados pelo tipo
    (`ParameterExtraction.command_values`); o padrão continua com o compilador;
  - erro de compilação → `plan = None` com os `issues`. Nunca plano parcial.
- `RunPlan.resolved`, `ref`, `skill_id`, `skill_version`, `skill_hash` e `name` são `None` no empate: não há **uma**
  habilidade de que falar.
- `for_command(command, profile_ids)` junta os dois; `None` só quando nada casa (`no_match`).
- O `ParameterExtractor` recebe as regras de link de perfil pelo pacote do app da skill
  (`infrastructure/profile_links.py::profile_links_for(pacote_do_app(app_id))`).
- `AppState.__init__` (`state.py`) compõe o planejador sem `classifier` nem `disambiguator` e o expõe como
  `AppState.skill_planner`. `RunService` o recebe como `skills=` e o guarda em `self.skills`.

### `taskqueue/service.py::RunService._plan`

- Chama `self.skills.for_command(run["command"], perfis dos aparelhos)` no lugar de `FlowStore.match`.
- Casou e compilou: o plano é esse, e o planejador não é chamado. `RunService._registrar_resolucao` grava a trilha
  (`Repository.note_run_skill`) e:
  - para fluxo legado, o mesmo de antes: `runs.flow_id`, `FlowStore.used` e a decisão "Plano reaproveitado do fluxo…";
  - para skill, a decisão "Plano da habilidade `<ref>` … (sem chamada ao planejador)", com os códigos dos avisos.
- Casou e a RESOLVE devolveu pergunta (fase I: parâmetro vazio, valor que não serve ao tipo ou empate):
  `RunService._skill_sem_plano` põe a execução em `needs_input` com a pergunta, sem plano e sem chamar o planejador
  ([detalhe](dominios/execution.md#a-pergunta-da-resolve-needs_input)).
- Casou e não compilou: `RunService._skill_sem_plano` grava a trilha, emite `log` com os `issues` e põe a execução em
  `needs_input`. Não cai para o fluxo nem para o planejador.
- Nada casou: o planejador, como sempre.
- `RunService.apps_exigidos`, `GET /api/flows/match` e `POST /api/skills/resolve` fazem a mesma pergunta (decisão
  P2). Com pergunta pendente, as duas primeiras respondem como se nada tivesse casado (`[]` e `null`).

### `Repository._insert_steps`

Copia `PlanStep.origin` para `steps.skill_id`, `skill_version`, `node_id` e `strategy` (`">".join(origin.strategies)`).
Etapa sem `origin` grava os quatro nulos. Como a origem mora no `Plan`, a cópia se repete na expansão do `for_each`, na
recuperação e na revisão.

### `taskqueue/executor.py::StepExecutor._verify`

A prova local passa por `StepExecutor._prova_local` → `CatalogCapabilityProvider.verify`. Só `proved` é atalho. Com
marca de falha visível e prova positiva, o modelo julga (mais conservador;
[detalhe](dominios/execution.md#verify-pela-porta-de-capability)).

### Precedência e interruptores

- Skill publicada (atrás de `skills.enabled`) → fluxo ativo (atrás de `ai.flows`) → planejador. Os interruptores são
  lidos a cada chamada. Desde a fase I, entre o fluxo e o planejador entra a skill que casa com um `{nome}` vazio, que
  só serve para perguntar ([skills](dominios/skills.md#um-registro-dois-backends)).
- **Com `skills.enabled` desligado, o comportamento de resolução é o de antes:** o mesmo fluxo, o mesmo plano, o mesmo
  `flow_id`, o mesmo `flows.used`, a mesma decisão; sem fluxo, o planejador. Mudam só colunas de trilha, gravadas
  sempre (a lista está em [execution](dominios/execution.md#com-as-skills-desligadas-o-que-ficou-igual-e-o-que-não)),
  e `GET /api/flows/match`, que passa a respeitar `ai.flows`.
- Prova: `backend/tests/test_fatia_abrir_conversa.py::test_com_as_habilidades_desligadas_o_comando_vai_ao_planejador`
  (`simulated`).
- A fase I também não muda nada com `skills.enabled` desligado: o fluxo não tem tipo, não empata e não casa com buraco
  vazio (`backend/tests/test_intencao_resolucao.py::test_paridade_com_o_flowstore_match`, `simulated`).

### A composição provada

`backend/tests/test_fatia_abrir_conversa.py` (`simulated`: harness na porta 5640, `FakeInstagram`,
`AtorDoInstagram` no `CountingProvider`, `ai.recipes = replay`):

- `ig.abrir_conversa` é publicada pelo repositório com os dois casos do documento (`simulated` e `negative`)
  observados no `FakeInstagram` pela mesma `CatalogCapabilityProvider.verify` da execução, e validada pelo sistema
  (P4: caso não `device` aceita prova `simulated`).
- `ig.ler_conversa`, sem casos próprios, é validada à mão pelo dono, com o motivo na transição (a via manual de P4).
- `::test_composta_le_a_conversa_que_a_filha_abriu`: `count("plan") == 0`; etapas `abrir_abrir_inbox`,
  `abrir_abrir_conversa` e `ler`; `ler` depende de `abrir_abrir_conversa`; `steps.skill_id` é o do dono de cada nó
  (`ig.abrir_conversa` nas duas primeiras, `ig.ler_conversa` na leitura); `OPEN_THREAD` comprovado pela prova local
  também na composta; `_alvo_da_conversa` acha `@ana` e a leitura traz os itens da conversa.
- `::test_filha_desabilitada_poe_a_composta_em_needs_input_sem_plano`: com a filha em `disabled`, a composta para em
  `needs_input` com `E_SKILL_NOT_FOUND`, sem objetivo e sem `runs.plan`.

### Precondição de deploy

A execução grava nas colunas da 045 mesmo com os interruptores desligados. As migrações 042–046 precisam estar
aplicadas antes do código da fase G, com ensaio numa cópia (ADR-020) e autorização
([detalhe](dominios/execution.md#precondição-de-deploy)).

O runtime de skills **reusa** o executor e o scheduler: as proteções R1–R14, P14 e P15 da §14.5 do design continuam
valendo por construção, porque a entrada continua sendo o mesmo `Plan`.

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| Documento → IR → `Plan`, com goldens | implementado | `simulated` (`backend/tests/test_compilador_de_skills.py::test_golden_valido_compila_para_o_plano_esperado`, `::test_golden_invalido_da_o_erro_esperado`) | `compiler.py`, `lowering.py` |
| Etapa idêntica à do planejador | implementado | `simulated` (`::test_abrir_conversa_sai_identica_ao_que_o_planejador_monta_pelo_catalogo`) | `build_step` |
| Composição, `foreach` e `when` | implementado | `simulated` (`::test_ler_conversa_expande_a_composicao_e_religa_as_dependencias`, `::test_foreach_sobre_skill_composta_e_when_estatico`) | `_chamada`, `_grafo` |
| Determinismo e hash | implementado | `simulated` (`::test_mesma_entrada_mesmo_ir_e_mesmo_hash`, `::test_hash_ignora_grafia_do_texto_mas_nao_o_conteudo`) | `ir.py`, `lowering.py::plan_hash` |
| D15: documento é dado, nunca código | implementado | `simulated` (`::test_compilador_nao_avalia_nem_carrega_codigo_por_ast`, `::test_codigo_python_num_texto_e_texto_inerte`) | `modules/skills/**`, `contracts/skills/**` |
| `PlanStep.origin` aditivo | implementado | `simulated` (`::test_plano_legado_continua_byte_a_byte_igual`) | `models.py::StepOrigin` |
| `_plan` pelo registro e trilha da 045 | implementado | `simulated` (`backend/tests/test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`, `::test_composta_le_a_conversa_que_a_filha_abriu`, `::test_filha_desabilitada_poe_a_composta_em_needs_input_sem_plano`) | `run_planning.py`, `taskqueue/service.py::RunService._plan` |
| Skills desligadas: planejador como antes | implementado | `simulated` (`test_fatia_abrir_conversa.py::test_com_as_habilidades_desligadas_o_comando_vai_ao_planejador`) | `CompositeSkillRegistry` |
| RESOLVE pelo `IntentResolver`, valores tipados no plano | implementado | `simulated` (`backend/tests/test_intencao_resolucao.py::test_golden_de_frases`, `::test_tipos_chegam_ao_plano_e_o_padrao_fica_com_o_compilador`, `::test_resultado_identico_ao_da_fase_g_para_o_que_ja_casava`) | `run_planning.py::SkillRunPlanner.resolve_intent` |
| Pergunta: `needs_input` sem plano, sem planejador; chamadores coerentes | implementado | `simulated` (`backend/tests/test_intencao_chamadores.py::test_os_tres_chamadores_coerentes_para_a_mesma_frase`, `::test_empate_na_execucao_pergunta_com_as_opcoes_e_nao_grava_skill`) | `RunService._skill_sem_plano` |
| Fase I em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Recompilação igual ao `runs.plan` | não feito | `not_run` | — |
| G em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Execução de skill em aparelho real | implementado | `not_run` (exige autorização: conta real) | — |

Backlog (não implementar aqui):

- Estratégias atrás de `ExecutionStrategy`, com a ordem vinda de `origin.strategies`
  ([ADR-036](decisoes.md#adr-036--receitas-como-estratégia-de-execução)).
- Fase K: identidade de receita por capability, para o mesmo `OPEN_THREAD` avulso e composto compartilhar receita.
