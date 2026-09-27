# Runtime de skills: compilador e baixa para `Plan`

Como um documento [`automation/v1alpha1`](skill-dsl.md) vira o `Plan` que o runtime de hoje já executa. É a fase E da
evolução arquitetural ([design](design/evolucao-arquitetural.md) §12.1, §12.3 e §18;
[ADR-033](decisoes.md#adr-033--ir-de-skill-e-dsl-automationv1alpha1)). A versão que guarda o documento está em
[`dominios/skills.md`](dominios/skills.md); o catálogo que o compilador lê, em
[`dominios/capabilities.md`](dominios/capabilities.md).

Caminhos relativos a `backend/app/`, salvo indicação. Código: `e8c51e0`, integrado em `7403a7e`.

**Estado (27/09).** O compilador e a baixa estão prontos e testados (`simulated`, sem banco, sem aparelho e sem IA).
**A fiação em `taskqueue/service.py::_plan` está em curso (fase G)**: hoje nenhuma execução passa pelo compilador.

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
- `ProcessGraph.content_hash()`: sha256 do JSON canônico do IR (`ir.py::canonical_json`: chaves ordenadas,
  separadores fixos, sem escapar acento).
- `lowering.py::plan_hash(plan)`: sha256 do JSON canônico de `plan.model_dump(mode="json")`.
- Existem, então, três hashes: o do documento (`skill_versions.content_hash`), o do IR (`ExecutableGraph.ir_hash`) e
  o do plano (`ExecutableGraph.plan_hash`). A 045 descreve `runs.skill_hash` como "sha256 do conteúdo executado";
  qual deles a execução grava é decisão da fiação G (não ligado).

## A invariante e os testes que a provam

**Invariante (decisão 4):** o compilador é o **único** produtor de `Plan` para skill nova, e nunca gera nem executa
Python. A candidata que vier do LLM é dado validado contra o esquema.

- Para conteúdo `schema_version` 1, o único caminho de documento a `Plan` é `SkillPlanCompiler`.
- Conteúdo legado (`schema_version` 0: fluxo, ou v1 de fluxo adotado) não é compilado. Passa direto por
  `modules/skills/infrastructure/legacy_flows.py::legacy_plan`, porque o plano congelado já é um `Plan`.
- O "único" só passa a valer **no runtime** com a fiação G. Hoje nenhum caminho de execução produz plano de skill.

Os testes, todos em `backend/tests/test_compilador_de_skills.py` (`simulated`):

| # | O que a §12.1 pede | Teste | Prova |
|---|---|---|---|
| 1 | regra D15 por AST | `test_compilador_nao_avalia_nem_carrega_codigo_por_ast` | `simulated` |
| 2 | golden: `planner.provider == "skill"` e ida e volta idêntica | `test_golden_valido_compila_para_o_plano_esperado` | `simulated` |
| 3 | Python num texto é texto inerte; campo fora do esquema dá `E_SCHEMA` | `test_codigo_python_num_texto_e_texto_inerte`, `test_campo_de_codigo_no_no_e_recusado`, `test_expressao_fora_da_gramatica_e_recusada` | `simulated` |
| 4 | execução com `runs.skill_id` tem `provider == "skill"`, e o hash bate com uma recompilação | — | `not_run` (fase G) |

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

## Fiação no runtime: em curso (fase G)

Hoje, `taskqueue/service.py::_plan` chama `FlowStore.match` direto, sob `ai.flows`, e cai no planejador quando não há
fluxo. O registro, o compilador e a trilha **não estão ligados**. O que a fase G liga (design §18, G2, proposto):

- `_plan` passa pelo `CompositeSkillRegistry.resolve`:
  - conteúdo `schema_version` 1 → `SkillPlanCompiler.compilar(..., parameters=resolvida.parameters)`;
  - conteúdo `schema_version` 0 → `legacy_plan(resolvida)`;
  - nada → o planejador, como hoje.
- A execução grava `runs.skill_id`, `skill_version` e `skill_hash` (045).
- `_insert_steps` copia a `origin` para `steps.skill_id`, `skill_version`, `node_id` e `strategy`.
- O executor grava `attempts.strategy` e `attempts.recipe_id`; `_ai` passa o `attempt_id`.
- Guardas em `_learn_flow` e `learn_from_run`, para o aprendizado por execução não duplicar comando.
- `GET /api/flows/match` e `apps_exigidos` passam pelo registro no mesmo commit (decisão P2).
- Validador do documento e `SkillLookup` de produção, ligando o repositório ao compilador.

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
| `_plan` pelo registro e trilha da 045 | em curso (fase G) | `not_run` | `taskqueue/service.py::_plan` |
| Execução de skill em aparelho real | não feito | `not_run` (exige autorização: conta real) | — |

Backlog (não implementar aqui):

- Fase G: a fiação acima, com os testes de §18 (`CountingProvider.count("plan") == 0`, colunas da 045 preenchidas,
  receita aprendida na primeira execução e reproduzida na segunda) em `simulated`.
- Fase K: identidade de receita por capability, para o mesmo `OPEN_THREAD` avulso e composto compartilhar receita.
