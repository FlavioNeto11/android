# DSL de skill `automation/v1alpha1`

Referência do documento de skill **como está no código**: `backend/app/contracts/skills/v1alpha1.py`
(`SkillDocument`). É o que o painel vai editar, o que o LLM vai propor no ensino e o que o compilador lê. Desenho em
[`design/evolucao-arquitetural.md`](design/evolucao-arquitetural.md) §12.2 e §12.3; decisão em
[ADR-033](decisoes.md#adr-033--ir-de-skill-e-dsl-automationv1alpha1). O compilador está em
[`skill-runtime.md`](skill-runtime.md); a versão que guarda o documento, em [`dominios/skills.md`](dominios/skills.md).

Caminhos relativos a `backend/app/`, salvo indicação. Código: `8d394e2` (contrato) e `e8c51e0` (compilador),
integrados em `7403a7e`.

**Estado (27/09).** Contrato congelado e testado (`simulated`). Nenhuma rota recebe documento ainda (fase F), e
nenhuma skill foi publicada numa instalação real (`not_run`). Desde a fase I (`00633d5`, integrado em `21b1fff`), os
tipos de `spec.parameters` valem na RESOLVE ([abaixo](#tipos-extração-e-normalização-fase-i)).

## Princípios

- **É contrato.** Mora em `contracts` e só usa stdlib e pydantic (regra D1). Não importa ninguém.
- **`extra="forbid"` e `frozen=True` em todo objeto** (a base `_Estrito`). Campo desconhecido é recusado, nunca
  ignorado: um documento vindo do LLM é dado validado, e um campo que ninguém lê seria instrução sem dono.
- **Texto é texto.** Nenhum campo é avaliado como código. As expressões são só as da [tabela](#expressões),
  reconhecidas por gramática fechada, nunca por `eval`.
- **Não há `local_proof` nem Python** ([por quê](#por-que-não-há-local_proof-nem-python)).
- **Os vocabulários são cópias** (`StrategyName`, `DeliveryLevelName`, `ResourceKind`, `ParameterType`,
  `GoalPostconditionKind`), repetidos como `Literal` em vez de importados. Um teste confere que não divergem dos donos.
- **Três nomes são alias:** o documento usa `apiVersion`, `with` e `from` (os atributos Python são `api_version`,
  `with_` e `from_`).

## Documento

| Campo | Tipo e limites | Obrigatório | O que o compilador faz com ele |
|---|---|---|---|
| `apiVersion` | `"automation/v1alpha1"` exato | sim | outro valor: `E_API_VERSION` |
| `kind` | `"Skill"` | sim | outro valor: `E_KIND` |
| `metadata.id` | `^[a-z][a-z0-9_.-]{2,63}$` (`SKILL_ID`, sem `:`) | sim | `E_ID_FORMAT`; vira `skill_definitions.id` |
| `metadata.name` | 1 a 200 caracteres | sim | `Plan.summary` |
| `metadata.description` | até 2000 | não | vai para a definição |
| `metadata.app` | id de app (tabela `apps`, não o pacote), até 64 | sim | `E_APP_UNKNOWN`; `Plan.app_id` e app padrão dos nós |
| `metadata.labels` | mapa texto → texto | não | aceito; sem consumidor |
| `spec.invocation.command_template` | 1 a 500, com `{parametro}` | sim | `command_template` e `match_key` da versão |
| `spec.invocation.examples` | até 20 comandos | não | aceito; sem consumidor (proposto: casos do `IntentResolver`; a fase I não os usa) |
| `spec.parameters[]` | `ParameterSpec`, até 30 | não | `Plan.parameters`; os tipos valem na RESOLVE (fase I) |
| `spec.requires` | `{apps, secrets, device, ai_roles}` | não | `apps` → `Plan.required_apps`; `secrets` → `ProcessGraph.secrets` → `RunPlan.secrets`, conferido no pré-voo contra as contas da persona de cada aparelho (ADR-040); `device` e `ai_roles` aceitos, sem consumidor |
| `spec.resources[]` | `ResourceSpec` | não | `ProcessGraph.resources`; aplicação só pelas portas atuais até a fase H |
| `spec.uses[]` | `{skill, version}` | quando há nó `skill` | trava de composição (`uses_lock`) |
| `spec.nodes[]` | `NodeSpec`, 1 a 100 | sim | `Plan.steps` |
| `spec.outputs[]` | `{name, from}` | não | `ProcessGraph.outputs` |
| `spec.success_criteria[]` | até 20 textos | não | `Plan.success_criteria` |
| `spec.policy` | só `"inherit"` (padrão) | não | outro valor: `E_FIELD_RESERVED` |
| `spec.validation.cases[]` | `ValidationCase` | não | sem caso: `W_NO_VALIDATION_CASE`; não é copiado para a 043 |

### Parâmetros (`ParameterSpec`)

| Campo | Tipo e limites | Padrão |
|---|---|---|
| `name` | `^[a-z_][a-z0-9_]*$`, até 40 | — |
| `type` | `string`, `text`, `integer`, `boolean`, `enum`, `handle` ou `url` | `string` |
| `required` | booleano | `true` |
| `default` | texto, inteiro ou booleano | nenhum |
| `example`, `description` | até 200 / até 2000 | nenhum |
| `values` | até 50 textos (para `enum`) | nenhum |
| `pattern` | regex, até 200 | nenhum |
| `max_length` | 1 a 500 | nenhum |

- O compilador confere a forma: nome repetido, `enum` sem `values` ou com padrão fora deles, padrão incompatível com
  `boolean`/`integer` e regex inválida dão `E_SCHEMA`. Nome reservado (`instance_id`, `run_id`, `account_label`) dá
  `E_COMMAND_RESERVED`.
- Sem `example`: `W_PARAMETER_NO_EXAMPLE`. Parâmetro que nenhum nó usa: `W_PARAMETER_UNUSED`.
- **Quem confere o valor contra `type`, `pattern` e `max_length` é a RESOLVE, não o compilador** (fase I;
  [abaixo](#tipos-extração-e-normalização-fase-i)). `compiler.py::_Compilacao._ligar` continua ligando o texto que
  recebe, sem conferir. Um caminho que compile com valores sem passar pela RESOLVE não tem tipo conferido.
- **Segredo não é parâmetro.** Nome com cara de credencial dá `E_SECRET_PARAMETER`
  (`compiler.py::_SECRET_NAME`, por pedaço do nome: `senha_do_portal` sim, `opiniao` não). A credencial entra só pelo
  nome, em `requires.secrets` (ex.: `conta_chrome_senha`); o valor vem da **conta da persona**, no cofre, com o
  consentimento dela (ADR-040). O pré-voo (`taskqueue/service.py::RunService.pre_voo(secret_names=…)`, via
  `modules/identity/application/available_data.py::missing_secrets`) confere cada nome contra as contas do perfil de
  cada aparelho — senha guardada, consentida, não recusada e de app sem `SessionProvider` — e recusa o aparelho
  com `missing_credential` antes de planejar (`RunPlan.secrets` vem de `ProcessGraph.secrets`).

### Tipos: extração e normalização (fase I)

O comando dá o valor pelo `{nome}` do modelo (`matching.py::extract_parameters`). A RESOLVE o normaliza pelo tipo
declarado (`domain/intent.py::normalize_value`, chamado por `extract_typed` no `ParameterExtractor`). O que sai é
**texto**, porque é texto que `Plan.parameters` guarda (design §10.4). O valor que não serve vira pergunta
(`invalid_parameter`); nunca é corrigido por palpite. O funcionamento da cadeia está em
[skills](dominios/skills.md#resolução-de-intenção).

| `type` | Aceita | Sai | Vira pergunta |
|---|---|---|---|
| `string`, `text` | qualquer texto | o texto sem espaço nas bordas | só por `max_length` ou `pattern` |
| `integer` | dígitos com sinal (`3`, `+3`, `-2`, `007`); por extenso, sem acento e sem caixa: de zero a dezenove (`um`/`uma`, `dois`/`duas`, `quatorze`/`catorze`), as dezenas exatas, dezena "e" unidade de 1 a 9 (`vinte e cinco`) e `cem`; milhar com ponto (`1.000`) | o decimal (`"3"`, `"25"`, `"1000"`) | `1,5`, `1.5`, `muitos`, `vinte e dez` |
| `boolean` | `sim`, `s`, `verdadeiro`, `true`, `1`, `yes`, `y`, `ligado(a)`, `ativo(a)`, `ativado(a)` e os opostos (`não`, `n`, `falso`, `false`, `0`, `no`, `desligado(a)`, `inativo(a)`, `desativado(a)`), sem acento e sem caixa | `true` ou `false`, o mesmo texto que o compilador dá a um padrão booleano (`compiler.py::_texto_do_valor`) | `talvez` |
| `enum` | o valor declarado, exato; ou igual a um só valor sem acento e sem caixa | o valor **declarado** (`reels` sai `Reels`; `acao` sai `Ação`) | fora das opções; igual a duas opções sem acento e sem caixa |
| `handle` | `@nome` ou `nome`, qualquer caixa, com aspas em volta (as curvas viram retas no casamento, `matching.py::_squash`); link de perfil do app da skill (`instagram.com/ana.teste/`, com `www.`, `m.`, `instagr.am`, com ou sem esquema e com query) | `@nome` em minúsculas | espaço, acento, `@@`, mais de 30 caracteres; link de post, story ou reel; link de outro domínio (inclusive o que só começa igual) |
| `url` | `http` ou `https`; sem esquema, `https://` é acrescentado quando o texto começa por um domínio | esquema e host em minúsculas (salvo com `usuário@` no endereço); o resto como veio | `ftp://…`, texto sem domínio, texto com espaço |

- **Nome de usuário** é `[a-z0-9._]{1,30}` depois de tirar o `@` e baixar a caixa (`domain/intent.py::_HANDLE`, a
  regra do Instagram).
- **Link de perfil** só vale com **um** segmento de caminho que não seja reservado (`p`, `reel`, `stories`,
  `explore`…) e só no app que declara a regra (`infrastructure/profile_links.py::INSTAGRAM_PROFILE_LINKS`, pelo
  pacote). Link de post ou de story não diz de quem se fala com certeza, e na dúvida se pergunta. Hoje só o Instagram
  tem regra.
- **`max_length` e `pattern` valem sobre o valor já normalizado, em qualquer tipo.** Num `handle`, o `pattern` vê o
  `@`. O `pattern` precisa casar o valor inteiro (`re.fullmatch`).
- **Parâmetro sem valor no comando:**
  - com `default`: o padrão aparece na resolução com `origin: default`, e quem o aplica é o compilador (`_ligar`);
  - sem padrão e obrigatório: pergunta (`missing_parameter`);
  - opcional sem padrão: fica de fora da resolução, e o compilador liga texto vazio.
- **`{nome}` vazio no comando** ("abra a conversa com no instagram", `matching.py::extract_with_gaps`) sempre vira
  pergunta, mesmo com padrão.
- **Nome que o documento não declara** passa sem tipo, e o compilador o recusa com `E_UNKNOWN_PARAMETER`, como antes.
- **Conteúdo legado** (`schema_version` 0) não tem tipo: o valor passa como o comando o trouxe.
- A pergunta diz o que serve em português (`domain/intent.py::expected_of`) e, se houver, o `example` do parâmetro.
- Prova: `backend/tests/test_intencao_dominio.py::test_golden_da_normalizacao_por_tipo` (tabela de 52 casos) e
  `backend/tests/test_intencao_resolucao.py::test_golden_de_frases` (22 frases), `simulated`.

### Requisitos e recursos

- `requires`: `apps` e `secrets` (listas de texto); `device` = `{api_level_min ≥ 1, play_store, abis}`; `ai_roles`
  (informativo).
- `ResourceSpec`: `kind` (`device.state`, `app.installation`, `account.binding`, `app.session`), `target` opcional,
  `desired` (texto ou mapa texto → texto) e `on_missing` (`wait`, padrão; `apply`; `ask`).
  - O mesmo `(kind, target)` pedido com outro estado desejado dá `E_RESOURCE_CONFLICT`, inclusive na união com os
    recursos de uma skill composta.
  - Na v1alpha1, o recurso é validado e fica no IR. A aplicação continua nas portas atuais do scheduler (design §11).

### Composição (`uses`)

- `skill` segue `SKILL_ID`; `version` é inteiro ≥ 1.
- A versão é opcional **no esquema** só para o compilador dizer `E_SKILL_VERSION_UNPINNED` em vez de um erro genérico
  de campo ausente. Sem versão exata, publicar uma versão nova do filho mudaria a composta por baixo.

## Nó (`NodeSpec`)

| Campo | Tipo e limites | Regra |
|---|---|---|
| `id` | `^[a-z][a-z0-9_]{1,40}$` (`NODE_ID`) | o mesmo padrão de `PlanStep.key`, porque o id do nó **vira** a chave da etapa |
| `capability` / `goal` / `skill` | chave do catálogo (até 64) / `GoalSpec` / `SKILL_ID` | exatamente um dos três; senão `E_SCHEMA` |
| `app` | até 64 | padrão `metadata.app`; `PlanStep.app_id` só quando difere |
| `with` | mapa texto → texto | argumentos da capability ou parâmetros da skill chamada |
| `depends_on` | lista de ids, ou omitido | [abaixo](#depends_on) |
| `foreach` | `${steps.<coleta>.output}` | [abaixo](#foreach) |
| `when` | até 200 | [abaixo](#when) |
| `timeout_s` | 10 a 900 | padrão: o do catálogo; no nó `goal`, 180 |
| `retries` | 0 a 4 | `max_attempts = retries + 1`; [abaixo](#timeouts-e-retries) |
| `strategies` | lista não vazia de `StrategyName` | padrão `[recipe, ai_actor]` |
| `verification` | `VerificationSpec` | [abaixo](#verificação) |
| `side_effect` | booleano | [abaixo](#efeito-externo) |
| `commit_guard` | até 10 textos | só em nó `goal` |
| `on_failure` | só `"fail"` | reservado: a regra de falha é global do scheduler; outro valor dá `E_FIELD_RESERVED` |

`GoalSpec` é o contrato inline de um nó sem capability: `title` (1 a 200), `goal` (1 a 2000) e `precondition` (até
2000).

**Por tipo de nó:**

- **`capability`:**
  - chave fora do catálogo, ou app sem catálogo: `E_UNKNOWN_CAPABILITY`. Capability `internal`: `E_CAPABILITY_INTERNAL`;
  - chave de `with` que a capability não aceita: `E_UNKNOWN_PARAMETER`. Binding obrigatório sem valor, ou nenhum de
    `content_brief`/`content` numa etapa que escreve: `E_MISSING_BINDING`;
  - `commit_guard` não se aplica (vale o do catálogo): `E_SCHEMA`.
- **`goal`:**
  - `verification.postcondition` é obrigatória: sem pós-condição não há o que comprovar (`E_SCHEMA`);
  - num app com catálogo, `goal` só serve para navegação sem efeito (`E_CAPABILITY_REQUIRED` com
    `side_effect: true`). Num app sem catálogo, o nó `goal` é o caminho.
- **`skill`:**
  - `app`, `timeout_s`, `retries`, `strategies`, `verification`, `side_effect` e `commit_guard` não se aplicam: cada nó
    da skill chamada traz o seu (`E_SCHEMA`);
  - precisa de entrada em `spec.uses`; a composição é [expandida pelo compilador](skill-runtime.md#expansão-de-uses).

### `depends_on`

- **Omitido = depende do nó anterior.** Independência exige `depends_on: []` explícito.
- O compilador **sempre emite** a lista em cada etapa.
  - Fecha a lacuna do treino, em que etapas sem aresta eram promovidas juntas.
  - Faz `READ_MESSAGES` achar o `username`: `AppState._alvo_da_conversa` lê os `bindings` das etapas listadas em
    `depends_on`.
- Id que não é nó desta skill, ou nó declarado depois: `E_DEPENDENCY_UNKNOWN` ("declare os nós na ordem em que
  rodam"). Dois nós que dependem um do outro: `E_DEPENDENCY_CYCLE`.

### `foreach`

- Só `${steps.<coleta>.output}`, e `<coleta>` precisa ser um nó de coleta **anterior** do mesmo documento
  (`E_FOREACH_SOURCE`). Repetição aninhada não existe na v1alpha1 (`E_FOREACH_SOURCE`).
- Vira `PlanStep.for_each = <coleta>`, e o scheduler cria uma cópia por item, como hoje.
- Regras do bloco, as mesmas do validador do `Plan`:
  - nós do mesmo bloco são consecutivos (`E_FOREACH_NOT_CONTIGUOUS`);
  - algum nó usa `${item}` (`E_FOREACH_NO_ITEM`);
  - coleta não entra num bloco `foreach` (`E_COLLECT_WITH_EFFECT`).
- O compilador simula as chaves das cópias até 200 itens (`MAX_FOREACH_ITEMS`), com o mesmo truncamento de
  `taskqueue/foreach.py::_copy_key`. Colisão depois de truncar dá `E_NODE_ID_COLLISION` antes de a execução descobrir.

### `when`

- **Estático:** `${parameters.x}`, `${parameters.x} == 'valor'` ou `${parameters.x} != 'valor'`. Qualquer outra forma
  dá `E_WHEN_UNSUPPORTED`. Sem comparação, é verdadeiro para `true`, `1`, `sim`, `yes` e `verdadeiro`.
- Dentro de uma skill chamada, o valor precisa vir de um parâmetro ou de um literal da chamadora. Vindo de `${item}`,
  ou misturando texto e parâmetro, dá `E_WHEN_UNSUPPORTED`.
- Na compilação sem valores (`draft → candidate`), todos os ramos entram. Na compilação da execução, o nó omitido sai.
- Nó que depende de um nó que o `when` pode omitir (ou repete por ele) precisa repetir a condição
  (`E_WHEN_DEPENDENCY`).

### Timeouts e retries

- `timeout_s` sobrescreve o prazo do catálogo.
- `retries` vira `max_attempts = retries + 1`. Sem `retries`, vale o `max_attempts` do catálogo (capability) ou 3
  (goal).
- **Com efeito externo, uma tentativa só (P5).** `retries` maior que zero num nó com efeito dá `E_RETRY_ON_EFFECT`:
  repetir curtiria duas vezes, ou descurtiria. A etapa sai com `max_attempts = 1` de qualquer forma (`build_step` na
  capability; a baixa no nó `goal`).

### Estratégias

- Subconjunto **ordenado** de `StrategyName`; repetição é descartada. Vira `PlanStep.origin.strategies`.
- Valor fora do vocabulário: `E_STRATEGY_UNKNOWN`.
- Estratégia que não serve ao nó: `E_STRATEGY_UNAVAILABLE`. Na v1alpha1 servem `recipe`, `ai_actor` e `human`
  ([capabilities](dominios/capabilities.md#strategykind-e-a-cadeia-de-estratégias)).

### Verificação

- `VerificationSpec`: `required_delivery_level` (`none`, `appeared`, `sent`, `delivered`, `read`) e `postcondition`.
- **Nó `capability`:** só `required_delivery_level`, que vai para a pós-condição do catálogo. A pós-condição é a do
  catálogo; declarar outra dá `E_VERIFICATION_WEAKENED`.
- **Nó `goal`:** `postcondition` = `{kind, value, description, required_delivery_level}`, com `kind` em
  `text_visible`, `app_foreground`, `element_present` ou `model_judged`. `items_collected` fica de fora: só capability
  de coleta produz itens. Com nível nos dois lugares, vale o mais alto.

### Efeito externo

- Nó `capability`: `side_effect` é opcional e, se vier, tem de bater com o catálogo (`E_SIDE_EFFECT_MISMATCH`).
- Nó `goal`: declara. Num app com catálogo, `true` dá `E_CAPABILITY_REQUIRED`: efeito passaria por fora da aprovação
  e dos limites do perfil.

### Aprovações e políticas

- `spec.policy` só aceita `inherit`: vale a governança inteira do catálogo e do perfil (`PolicyEngine`, `_draft_gate`
  e `_approval_gate`).
- Um campo `approval` ou `approvals` em qualquer lugar dá `E_FIELD_RESERVED`, e não o `E_SCHEMA` genérico.
- Política por nó fica para a v1alpha2 (proposto). Endurecer por nó exigiria um campo em `PlanStep` que o
  `_policy_gate` lesse.

### Saídas e casos de validação

- `outputs[]`: `name` (padrão de parâmetro, até 40) e `from` = `${steps.<coleta>.output}` de uma coleta desta skill.
  Outra coisa dá `E_OUTPUT_REF_UNSUPPORTED`; nome repetido, `E_SCHEMA`. Na v1alpha1, só coleta produz saída.
- `validation.cases[]`: `name` (1 a 80), `kind` (`replay`, `simulated`, `device`, `negative`), `parameters` e
  `preconditions` (mapas texto → texto) e `expected` = `{outcome, proofs}`.
  - `outcome` ∈ `succeeded`, `failed`, `uncertain`, `not_proved`, `waiting_user`.
  - Chave de `parameters` que não é parâmetro da skill: `E_UNKNOWN_PARAMETER`; com cara de credencial:
    `E_SECRET_PARAMETER`.

## Expressões

A gramática é fechada (`compiler.py`: `_EXPR`, `_PARAM_REF`, `_WHEN`, `_STEP_OUTPUT`). Espaço dentro de `${ … }` não
muda nada, nem o hash.

| Expressão | Vale em | Vira |
|---|---|---|
| `${parameters.x}` | `with`, textos do nó `goal` (`title`, `goal`, `precondition`, `postcondition.value`/`description`), `commit_guard`, `success_criteria`, `when` | `{x}` na raiz; dentro de skill chamada, a expressão que a chamadora passou |
| `${item}` | nó com `foreach`, ou dentro de skill chamada por `foreach` | `{item}` |
| `${steps.<id>.output}` | só em `foreach` e `outputs[].from` | `for_each = <id>`, ou a saída declarada |
| `${secrets.…}` | em lugar nenhum | `E_SECRET_INLINE` |
| `{x}` cru | só em `command_template` | fora dele, `E_RAW_PLACEHOLDER` |
| qualquer outra coisa em `${…}` | — | `E_EXPRESSION` (`${parameters.__class__}` é procurado como nome e dá `E_UNKNOWN_PARAMETER`) |

- `${…}` dentro do `command_template` dá `E_EXPRESSION`: o comando usa `{parametro}`.
- `${steps.…}` fora de `foreach`/`outputs` dá `E_OUTPUT_REF_UNSUPPORTED`.

## Por que não há `local_proof` nem Python

- **`local_proof`.** A prova local é atalho **positivo**: `True` dispensa o verificador. Declarada num documento, ela
  afrouxaria a verificação. Só o catálogo, que é código revisado, a declara. O campo não existe no esquema, e o
  compilador aponta a tentativa como `E_VERIFICATION_WEAKENED`, com uma mensagem que diz isso (não como campo
  desconhecido).
- **Python.** Não há campo de script, gancho nem expressão livre.
  - Um campo como `script` ou `__class__` dá `E_SCHEMA`.
  - Um `when` com código dá `E_WHEN_UNSUPPORTED`.
  - Código dentro de um texto (título, objetivo, pós-condição) é **texto inerte**: vai para o `PlanStep` como está, e
    nada o executa.
- A regra D15 (`docs/design/evolucao-arquitetural.md` §9) e os testes que a provam estão em
  [`skill-runtime.md`](skill-runtime.md#a-invariante-e-os-testes-que-a-provam).

## O esquema congelado e como mudar o contrato

- **O snapshot:** `backend/tests/contratos/skill-dsl.v1alpha1.json` é `SkillDocument.model_json_schema()`, com
  `indent=2` e chaves ordenadas.
- **Os testes** (`backend/tests/test_contrato_skill_dsl.py`):
  - `test_esquema_da_dsl_nao_muda_sem_querer`: o esquema atual é igual ao snapshot;
  - `test_todo_objeto_do_esquema_recusa_campo_desconhecido_e_nao_ha_prova_local`;
  - `test_vocabularios_repetidos_no_contrato_batem_com_os_donos`: estratégias, níveis de entrega, tipos de
    pós-condição, tipos de recurso e o padrão do id do nó.
- **Por que congelar:** o esquema é o que o painel valida e o que o prompt de ensino entrega ao LLM. A suíte usa o
  mesmo commit dos dois lados, e uma mudança só apareceria como documento recusado, ou aceito sem querer, do outro
  lado.
- **Para mudar de propósito:**
  1. mude `contracts/skills/v1alpha1.py`;
  2. regere o snapshot: `ATUALIZAR_CONTRATOS=1`, rodando `tests/test_contrato_skill_dsl.py`;
  3. se a compilação mudou, ajuste o compilador e regere os goldens com `ATUALIZAR_GOLDEN=1`, revisando o diff dos
     `*.esperado.json`: ele **é** a mudança de comportamento;
  4. código `E_*` novo entra em `modules/skills/domain/errors.py::Code` com uma fixture em
     `backend/tests/fixtures/dsl/v1alpha1/invalidos/` (o teste reprova código sem fixture);
  5. diga no commit o que mudou e por quê, e atualize este documento.
- **Proposto:** mudança incompatível (tirar um campo, apertar um padrão que versões já publicadas usam) vira um
  `apiVersion` novo num módulo novo. A versão publicada é congelada e precisa continuar compilando.

## Exemplo 1: `ig.abrir_conversa`

Fixture `backend/tests/fixtures/dsl/v1alpha1/validos/ig.abrir_conversa.yaml` (o exemplo da §12.4, literal):

```yaml
apiVersion: automation/v1alpha1
kind: Skill
metadata:
  id: ig.abrir_conversa
  name: Abrir conversa no Instagram
  description: Abre a conversa direta com um usuário, existente ou nova, pronta para escrever.
  app: instagram
spec:
  invocation:
    command_template: "abra a conversa com {username} no instagram"
    examples:
      - "abra a conversa com @ana.teste no instagram"
  parameters:
    - name: username
      type: handle
      required: true
      example: "@ana.teste"
      description: Usuário do Instagram, com ou sem arroba.
  requires:
    apps: [instagram]
  resources:
    - kind: device.state
      desired: online
      on_missing: wait
    - kind: app.installation
      target: instagram
      desired: {release: promoted}
      on_missing: wait
    - kind: app.session
      target: instagram
      desired: {session: ready, account: bound_profile}
      on_missing: ask
  nodes:
    - id: abrir_inbox
      capability: OPEN_INBOX
      depends_on: []
      strategies: [recipe, ai_actor]
    - id: abrir_conversa
      capability: OPEN_THREAD            # sem efeito externo: retries permitido
      depends_on: [abrir_inbox]
      with:
        username: ${parameters.username}
      strategies: [recipe, ai_actor]
      timeout_s: 180
      retries: 2
  outputs: []
  success_criteria:
    - "A conversa com ${parameters.username} está aberta e o campo de escrever está disponível."
  policy: inherit
  validation:
    cases:
      - name: conversa-existente
        kind: simulated
        parameters: {username: "@ana.teste"}
        expected: {outcome: succeeded, proofs: [local_proof]}
      - name: inbox-nao-prova-conversa
        kind: negative
        parameters: {username: "@ana.teste"}
        preconditions: {screen: inbox_com_linha_do_usuario}
        expected: {outcome: not_proved}
```

Compilado com `{"username": "@ana.teste"}` (o plano inteiro está em `ig.abrir_conversa.esperado.json`):

- duas etapas, `abrir_inbox` (`depends_on: []`) e `abrir_conversa` (`depends_on: [abrir_inbox]`);
- `abrir_conversa` sai com `bindings = {"username": "{username}"}`, `title = "Abrir a conversa com {username}"`,
  `max_attempts = 3` e `timeout_s = 180`;
- o valor vai em `Plan.parameters`, e quem o resolve é o `materialize`, como hoje;
- `planner = {provider: "skill", model: "skill:ig.abrir_conversa@1", simulated: false}`.

## Exemplo 2: `ig.ler_conversa` (composta)

Fixture `validos/ig.ler_conversa.yaml` (o exemplo da §12.5, literal):

```yaml
apiVersion: automation/v1alpha1
kind: Skill
metadata:
  id: ig.ler_conversa
  name: Ler a conversa com um usuário
  description: Abre a conversa reusando ig.abrir_conversa e lê as mensagens recentes, sem responder.
  app: instagram
spec:
  invocation:
    command_template: "leia a conversa com {contato} no instagram"
  parameters:
    - name: contato
      type: handle
      required: true
      example: "@ana.teste"
  uses:
    - skill: ig.abrir_conversa
      version: 1
  nodes:
    - id: abrir
      skill: ig.abrir_conversa
      depends_on: []
      with:
        username: ${parameters.contato}
    - id: ler
      capability: READ_MESSAGES          # coleta: sem efeito, não pode ter foreach
      depends_on: [abrir]
      strategies: [recipe, ai_actor]
  outputs:
    - name: mensagens
      from: ${steps.ler.output}
  policy: inherit
```

Compilado com `{"contato": "@ana.teste"}`, são três etapas:

| Etapa | Capability | `depends_on` | Origem |
|---|---|---|---|
| `abrir_abrir_inbox` | `OPEN_INBOX` | `[]` | `ig.abrir_conversa@1` |
| `abrir_abrir_conversa` | `OPEN_THREAD`, `bindings.username = "{contato}"` | `[abrir_abrir_inbox]` | `ig.abrir_conversa@1` |
| `ler` | `READ_MESSAGES` | `[abrir_abrir_conversa]` | `ig.ler_conversa@1` |

- `planner.model = "skill:ig.ler_conversa@1"`, com o aviso `W_NO_VALIDATION_CASE`.
- Os recursos do filho são herdados, e `uses_lock` traz `ig.abrir_conversa@1` com o hash do documento do filho.
- A terceira fixture válida, `validos/ig.conversas_da_caixa.yaml`, mostra `foreach` sobre uma skill composta e um nó
  `goal` com `when`.

## Códigos de erro e avisos

Vocabulário fechado em `modules/skills/domain/errors.py::Code`. Cada problema sai como `CompileIssue` =
`{code, message, path, severity}`, e **nunca** como exceção nem como 500:

- `path` é um ponteiro JSON (RFC 6901) no documento que a pessoa ou o LLM escreveu;
- um erro dentro de uma skill chamada aponta para o nó de chamada da raiz, e a mensagem começa com
  "em `<skill>@<n>`, `<ponteiro>`";
- o mesmo problema visto por dois caminhos sai uma vez só.

Todo `E_*` tem uma fixture em `backend/tests/fixtures/dsl/v1alpha1/invalidos/<CODIGO>.yaml`, com o erro e o caminho
esperados em `<CODIGO>.esperado.json`.

| Código | Quando |
|---|---|
| `E_SCHEMA` | documento fora do esquema: campo desconhecido, tipo ou limite errado, campo obrigatório ausente. Também as regras de forma do compilador: parâmetro repetido, `enum` mal declarado, padrão incompatível com o tipo, regex inválida, nó sem exatamente um de `capability`/`goal`/`skill`, `commit_guard` em nó `capability`, campo que não se aplica a nó `skill`, nó `goal` sem pós-condição, saída repetida |
| `E_API_VERSION` | `apiVersion` diferente de `automation/v1alpha1` |
| `E_KIND` | `kind` diferente de `Skill` |
| `E_ID_FORMAT` | `metadata.id` fora do padrão (inclui o prefixo `flow:`, espaço e maiúscula) |
| `E_NODE_ID_FORMAT` | id de nó fora do padrão |
| `E_NODE_ID_DUPLICATE` | dois nós com o mesmo id no mesmo documento |
| `E_NODE_ID_TOO_LONG` | o id expandido (prefixo do nó de chamada + id do filho) passa de 41 caracteres |
| `E_NODE_ID_COLLISION` | o id expandido repete outro, ou uma cópia de `foreach`, depois do truncamento, colide com outro nó |
| `E_COMMAND_AMBIGUOUS` | dois parâmetros colados no comando (`{a}{b}` ou `{a} {b}`): não dá para separar os valores |
| `E_COMMAND_PARAMETER_MISMATCH` | `{x}` no comando sem parâmetro declarado, ou parâmetro obrigatório sem padrão que não aparece no comando |
| `E_COMMAND_RESERVED` | parâmetro ou `{x}` com nome reservado (`instance_id`, `run_id`, `account_label`) |
| `E_UNKNOWN_PARAMETER` | `${parameters.x}` ou `when` sobre parâmetro que não existe; chave de `with` que a capability não aceita ou que a skill chamada não tem; parâmetro de caso de validação desconhecido; valor, na execução, para parâmetro que não existe |
| `E_MISSING_ARGUMENT` | a skill chamada exige um parâmetro sem padrão e a chamada não o passa; ou a execução não trouxe valor para um parâmetro obrigatório |
| `E_SECRET_PARAMETER` | nome de parâmetro, ou chave de parâmetro de caso de validação, com cara de credencial |
| `E_UNKNOWN_CAPABILITY` | chave fora do catálogo do app, ou app sem catálogo (use um nó `goal`). Também na baixa |
| `E_CAPABILITY_INTERNAL` | capability `internal` (login, leitura da conta): resolvida por código, fora do laço da etapa |
| `E_CAPABILITY_REQUIRED` | nó `goal` com efeito externo num app com catálogo |
| `E_MISSING_BINDING` | binding obrigatório da capability sem valor, ou nenhum de `content_brief`/`content` numa etapa que escreve. Também na baixa (`MissingBinding`) |
| `E_SIDE_EFFECT_MISMATCH` | `side_effect` do nó diverge do catálogo |
| `E_RETRY_ON_EFFECT` | `retries` em nó com efeito externo |
| `E_VERIFICATION_WEAKENED` | `local_proof` no documento, ou pós-condição própria num nó `capability` |
| `E_FIELD_RESERVED` | `policy` diferente de `inherit`, `on_failure` diferente de `fail`, ou campo `approval`/`approvals` |
| `E_DEPENDENCY_UNKNOWN` | `depends_on` com id que não é nó desta skill, ou que vem depois do nó |
| `E_DEPENDENCY_CYCLE` | nós que dependem um do outro |
| `E_FOREACH_SOURCE` | `foreach` fora da forma `${steps.<id>.output}`, fonte que não é coleta anterior, ou repetição aninhada |
| `E_FOREACH_NOT_CONTIGUOUS` | nós do mesmo bloco `foreach` separados por outro nó |
| `E_FOREACH_NO_ITEM` | bloco `foreach` sem `${item}` em nenhum nó: todas as cópias fariam a mesma coisa |
| `E_COLLECT_WITH_EFFECT` | coleta dentro de um bloco `foreach`. O nome vem do validador do `Plan`; coleta **com efeito** o próprio catálogo já recusa, em `CapabilityDefinition.__post_init__` |
| `E_OUTPUT_REF_UNSUPPORTED` | `${steps.…}` fora de `foreach`/`outputs`, ou `outputs[].from` que não aponta para uma coleta desta skill |
| `E_EXPRESSION` | `${…}` fora da gramática, `${item}` fora de `foreach`, `${` malformado, ou `${…}` no comando |
| `E_RAW_PLACEHOLDER` | `{x}` cru fora do `command_template` |
| `E_SECRET_INLINE` | `${secrets…}` em qualquer lugar |
| `E_WHEN_UNSUPPORTED` | `when` fora das três formas estáticas, ou com valor que vem de `${item}` ou mistura texto e parâmetro |
| `E_WHEN_DEPENDENCY` | nó que depende de (ou repete por) um nó que o `when` pode omitir, sem repetir a condição |
| `E_SKILL_NOT_FOUND` | `<skill>@<versão>` de `uses` não existe |
| `E_SKILL_VERSION_UNPINNED` | nó `skill` sem entrada em `uses`, ou entrada sem `version` |
| `E_SKILL_CYCLE` | composição em ciclo (`a → b → a`) |
| `E_COMPOSITION_DEPTH` | composição com mais de 3 níveis |
| `E_APP_UNKNOWN` | `metadata.app`, `app` de um nó ou item de `requires.apps` não cadastrado |
| `E_STRATEGY_UNKNOWN` | estratégia fora do vocabulário |
| `E_STRATEGY_UNAVAILABLE` | estratégia sem provider para o nó na v1alpha1 (`deterministic`, `app_provider`, `ui_generic`) |
| `E_RESOURCE_UNKNOWN_KIND` | `kind` de recurso fora dos quatro |
| `E_RESOURCE_CONFLICT` | o mesmo recurso pedido com dois estados desejados, inclusive na união com o filho |
| `E_DUPLICATE_COMMAND` | o comando, normalizado como `flows.match_key`, já é de uma skill publicada ou de um fluxo ativo (`occupied_match_keys`). O repositório tem a mesma recusa ao publicar (`DuplicateCommand`) |
| `E_PLAN_INVALID` | o documento passou pelo compilador e o validador do `Plan` recusou na baixa (a última porta) |

Avisos, que não impedem `draft → candidate`:

| Código | Quando |
|---|---|
| `W_PARAMETER_NO_EXAMPLE` | parâmetro sem `example`: o painel e o resolvedor de intenção usam o exemplo |
| `W_NO_VALIDATION_CASE` | sem caso de validação: a versão não chega a `validated` sem decisão manual |
| `W_PARAMETER_UNUSED` | parâmetro que nenhum nó usa |
