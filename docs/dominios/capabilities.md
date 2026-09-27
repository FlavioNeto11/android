# Domínio: capabilities

O que um app sabe fazer, como operação semântica, e as portas por onde a execução vai pedir essa operação. É a fase
C da evolução arquitetural ([design](../design/evolucao-arquitetural.md) §5, §7, §14.3 e §15.1;
[ADR-032](../decisoes.md#adr-032--capability-skill-e-process)). Quem usa isto é o compilador de skills
([skills](skills.md), [DSL](../skill-dsl.md), [runtime](../skill-runtime.md)) e, no VERIFY, o executor
([execution](execution.md)).

Caminhos relativos a `backend/app/`, salvo indicação. Código da fase C: `662a7e8`, integrado em `7403a7e`.

**Estado (27/09).** Tipado e testado em `simulated`. **Desde a fase G (integrada em `0b736f3`), o executor usa o
`verify` do provider** na prova local ([execution](execution.md#verify-pela-porta-de-capability)). O resto continua
fora do runtime: o executor lê o catálogo legado direto para montar e executar a etapa. Ver
[o que ainda não está ligado](#o-que-ainda-não-está-ligado).

## O que é uma `CapabilityDefinition`

- É a **operação semântica** de um app: "abrir a conversa com {username}", "curtir a publicação".
  - Não é gesto. Toque, rolagem e digitação são problema da estratégia que executa a operação.
- É um valor imutável: `modules/capabilities/domain/definition.py::CapabilityDefinition`
  (`dataclass(frozen=True, slots=True)`).
- A identidade é `CapabilityRef` = (app, chave, versão do contrato), impressa como
  `com.instagram.android:OPEN_THREAD@1`.
  - `app` é o **pacote** Android, que é a chave do catálogo. O id de app (`instagram`) é configuração de cada
    instalação (tabela `apps`), e a mesma capability precisa ter a mesma identidade em todas.
  - `contract_version` é sempre `LEGACY_CONTRACT_VERSION = 1`, porque o catálogo em código não tem versão.
  - Proposto (fase K): com o catálogo virando dado, mudar o contrato sobe o número, e a trava das skills
    (`uses_lock`) passa a enxergar a mudança.

Os campos ficam agrupados pelo que governam:

| Grupo | Tipo | O que diz |
|---|---|---|
| contrato | `title`, `goal`, `ParameterContract` (`required`, `optional`, `one_of`), `precondition`, `PostconditionContract` | que argumentos recebe, o que vale antes, o que prova depois |
| prova local | `local_proof` | atalho positivo da verificação ([abaixo](#provas-locais)) |
| efeito | `SideEffectContract` (`external`, `commit_selector`, `commit_guard`, `band_guard`, `interaction_type`, `failure_marks`) | se há efeito externo, quem o dispara, o que o guarda e o que o desmente |
| governança | `Governance` (`risk`, `default_policy`, `limit_bucket`, `needs_draft`) | política padrão, limite por hora, texto gerado antes de agir |
| execução | `ExecutionContract` (`internal`, `timeout_s`, `max_attempts`, `strategies`) | prazo, tentativas e que estratégias servem |
| saída | `CollectOutput` (`collects`, `limit`, `from_top`, `rewind`, `item_key`) | coleta: a lista de itens lida da tela |
| requisitos do app | `AppRequirements` (`session_provider`, `needs_profile`, `requires_internet`) | vêm do registro de apps, não da capability |
| reconciliação | `reconciliation` | prosa; nenhum código a lê |

Regras conferidas na construção (`CapabilityDefinition.__post_init__`):

- `collect` e a pós-condição `items_collected` andam juntos;
- coleta não tem efeito externo (a mesma regra do validador do `Plan`).

`ParameterContract.one_of` vale `("content_brief", "content")` quando `needs_draft`: é a regra que `build_step`
aplica à etapa que escreve.

## Mapeamento 1:1 com o catálogo legado

- **Fonte:** `planning/capabilities.py::Capability`, 29 campos. Só o Instagram tem catálogo: 23 capabilities em
  `planning/catalog/instagram.py`.
- **Conversão:** `modules/capabilities/infrastructure/catalog_registry.py::definicao(cap, package)`.
- **Declaração do mapeamento:** `catalog_registry.py::CAMPOS` (campo do catálogo → destino na definição). O teste
  reprova quando:
  - o catálogo ganha um campo sem destino, ou `CAMPOS` cita um campo que sumiu;
  - dois campos caem no mesmo destino;
  - um valor não chega inteiro em alguma das 23 capabilities.
- **Campos sem consumidor:** `catalog_registry.py::SEM_CONSUMIDOR`.
  - `reconciliation`: prosa, até um provider reconciliar (fase G/H);
  - `collect`: redundante com `post_kind == "items_collected"`, que é o que o executor e o validador do `Plan` olham.
  - O teste reprova quando alguém passa a ler um deles sem tirá-lo da lista.
- **Estratégias por capability** (em `definicao`):
  - capability `internal=True` (`AUTHENTICATE_INSTAGRAM`, `VERIFY_ACCOUNT`) só tem `deterministic`;
  - as demais têm `STRATEGIES_OF_A_NODE`.
- **Leitura:** `catalog_registry.py::CatalogCapabilityRegistry`, só leitura e sem cache (o registro de apps muda em
  teste por `register`/`unregister`).
  - `definition(app_id, key)`, `offered(app_id)` (sem as internas, como `CapabilityCatalog.offered`), `app_known`,
    `has_catalog` e `package_of`.
  - `by_ref(ref)`: versão de contrato diferente da atual devolve `None`. Ninguém verifica uma etapa pelo contrato de
    outra.
  - A tradução id de app → pacote é injetada (`pacote_do_app`), porque a tabela `apps` é de outro contexto.
- O catálogo em código **continua sendo a fonte** até a fase K. `planning/catalog.register` segue sendo o ponto de
  extensão: um app registrado lá aparece aqui sem nada novo.

## `StrategyKind` e a cadeia de estratégias

`modules/capabilities/domain/strategy.py::StrategyKind` é um vocabulário fechado:

| Valor | O que é | Hoje | Em nó de skill (v1alpha1) |
|---|---|---|---|
| `deterministic` | código que resolve sozinho | só capability `internal`, fora do laço da etapa | `E_STRATEGY_UNAVAILABLE` |
| `recipe` | receita gravada, reproduzida pelo `Replayer` | sim (`taskqueue/recipes.py`) | permitida; primeira do padrão |
| `app_provider` | provider do app no molde do `InstagramAuthenticator` | não | `E_STRATEGY_UNAVAILABLE` |
| `ui_generic` | heurística de UI sem IA | reservado | `E_STRATEGY_UNAVAILABLE` |
| `ai_actor` | laço de decisão com o modelo | sim (`taskqueue/executor.py`) | permitida; segunda do padrão |
| `human` | desfecho `waiting_user`: só uma pessoa resolve (ADR-009) | sim, como desfecho | permitida, **explicitamente** |

- `STRATEGIES_OF_A_NODE = (recipe, ai_actor, human)`. `DEFAULT_STRATEGIES = (recipe, ai_actor)`: receita primeiro,
  sem custo de IA, como o executor faz hoje.
- A ordem desejada vem do nó (`PlanStep.origin.strategies`), não do enum.
  - Hoje ela é **gravada e não comanda**: vai para `steps.strategy`, e o executor segue a ordem fixa de sempre
    (receita, se aplicável, e IA).
  - Proposto: a cada tentativa, percorrer a lista em ordem, pulando as estratégias inaplicáveis
    ([ADR-036](../decisoes.md#adr-036--receitas-como-estratégia-de-execução)).
- O mesmo vocabulário aparece em mais dois lugares:
  - `contracts/skills/v1alpha1.py::StrategyName`;
  - as colunas `steps.strategy` (planejada) e `attempts.strategy` (exercida), da 045, com a cadeia separada por `>`
    (`recipe>ai_actor`). O executor grava em `attempts.strategy` só `recipe` e `ai_actor`: `human` é desfecho
    (`waiting_user`), não aparece ali ([execution](execution.md#estratégias)).
  - `backend/tests/test_contrato_skill_dsl.py::test_vocabularios_repetidos_no_contrato_batem_com_os_donos` confere
    que a cópia do contrato não diverge.
- **Nenhuma estratégia decide sucesso.**
  - `StrategyResult.status` (`StrategyStatus`: `acted`, `diverged`, `waiting_user`, `failed`) diz o que a estratégia
    fez. Quem diz se a etapa valeu é o VERIFY.
  - `StrategyResult.fired` marca efeito disparado: falha depois disso é `uncertain`, nunca nova tentativa (R3).
  - `StrategyContext.effect_fired`: depois do disparo, só VERIFY e RECONCILE (R2).

## Portas: `CapabilityProvider` e `ExecutionStrategy`

Moram em `modules/execution/application/ports.py`, do lado de quem consome.

- Quem implementa não importa o `Protocol`: a tipagem estrutural basta.
- Isso mantém os contextos num DAG (regra D5: capabilities não enxerga execution). Por isso os tipos das assinaturas
  moram em `modules/capabilities/domain` (`strategy.py` e `verification.py`).

| Porta | Assinatura |
|---|---|
| `CapabilityProvider` | `supports(cap: CapabilityRef) -> bool`; `async observe(ctx) -> Observation`; `async execute(node, ctx) -> StrategyResult`; `async verify(node, obs) -> VerifyResult`; `async reconcile(node, ctx) -> VerifyResult` |
| `ExecutionStrategy` | `kind` (propriedade); `applicable(node, ctx) -> bool`; `async run(node, ctx) -> StrategyResult`. **Sem implementação** até a fase G |

Tipos de apoio, em `modules/capabilities/domain/verification.py`:

- `VerifyOutcome`: `proved`, `not_proved`, `unknown`.
  - `not_proved` é a tela **desmentindo** a etapa.
  - `unknown` é "não dá para afirmar": quem julga, então, é o verificador de sempre.
- `VerifyResult`: o veredito mais o motivo (`detail`), em português, para a trilha.
- `StepView`: a etapa já materializada (`node_id`, `capability`, `bindings`, `band_guard`,
  `required_delivery_level`).
- `Observation`: `screen` e `package`. `screen` é `object` de propósito: `UiTree` (`automation/hierarchy.py`) é
  legado, e a regra D2 o esconde do domínio. Quem lê confere o tipo e, se não o reconhece, não afirma nada.

### `CatalogCapabilityProvider`: o que é real e o que não é

`modules/capabilities/infrastructure/catalog_provider.py::CatalogCapabilityProvider`:

| Operação | Estado | Por quê |
|---|---|---|
| `supports` | real | `registry.by_ref(cap) is not None` |
| `verify` | real, **usado pelo executor** desde a fase G | embrulha `taskqueue/proofs.py::local_proof_holds`. `StepExecutor._prova_local` o chama de dentro de `_verify` |
| `observe` | `NotImplementedError` | observar exige o driver, que é do executor durante a etapa; um segundo leitor disputaria o driver. O executor entrega a observação pronta: `StepExecutor._prova_local` passa `Observation(obs.tree, obs.package)` ao `verify` |
| `execute` | `NotImplementedError` | é a cadeia de estratégias do nó, que mora em `run_step`/`_run_step` até sair atrás de `ExecutionStrategy` (fase G). Reimplementar seria um segundo executor |
| `reconcile` | `NotImplementedError` | é `commit_state` mais o reconciliador do scheduler, com escrita no banco e posse da etapa (R4, R11): só o hospedeiro reconcilia |

As três recusas levam o motivo na mensagem. Uma fiação apressada falha alto, em vez de "verificar" sem ter
observado. Nenhuma das cinco operações toca aparelho.

A escada do `verify`, em ordem:

1. etapa sem capability do catálogo, ou com contrato de outra versão → `unknown`;
2. `screen` que não é `UiTree` → `unknown`;
3. tela sem elementos → `unknown` (tela carregando não prova nada);
4. alguma `failure_marks` visível ("Not delivered") → **`not_proved`**. É a única negativa que se afirma sem modelo;
5. pós-condição que não é `model_judged` → `unknown` (quem confere é `StepExecutor._deterministic`);
6. etapa com `required_delivery_level` → `unknown` ("enviado" não prova "entregue");
7. capability sem `local_proof` → `unknown`;
8. `local_proof_holds(...) is True` → `proved`. Qualquer outra resposta → `unknown`.

## Provas locais

A gramática mora em `taskqueue/proofs.py`. Os prefixos aceitos estão em `planning/capabilities.py::LOCAL_PROOFS`, e
`local_proof_error` recusa uma prova mal escrita ao montar o catálogo.

| Forma | Prova quando | Exemplo no catálogo |
|---|---|---|
| `sent_text` | o `content` da etapa aparece num elemento não editável e sumiu do campo de escrita (`UiTree.sent_as_message`) | `SEND_MESSAGE` |
| `selector:<seletor>` | algum elemento casa o seletor: `id=`, `desc=`, `text=`; `==` casa exato; `\|` une partes no mesmo elemento; `{username}` vem dos bindings; `@ana` também casa `ana` | `OPEN_PROFILE`: `selector:id=action_bar_title\|text=={username}` |
| `&` dentro de `selector:` | todos os seletores na mesma tela, cada um no seu elemento. O `&` é separado **antes** de resolver os bindings: um valor nunca vira operador | `OPEN_THREAD`: `selector:text=={username}&id=row_thread_composer_edittext` (G0, `8a2fca5`) |
| `selector_band:<seletor>` | como `selector:`, e o elemento casado fica na mesma faixa vertical de cada `band_guard` da etapa. Sem guarda, não prova | `LIKE_COMMENT`: `selector_band:desc==Liked` |

Regras:

- **A prova local nunca reprova sozinha.** `local_proof_holds` devolve `True`, `False` ou `None`, e só `True` muda
  alguma coisa: dispensa o modelo. Variável sem valor na etapa dá `None`.
- É atalho **positivo**. Por isso só o catálogo, que é código revisado, a declara. A DSL não tem o campo
  (`E_VERIFICATION_WEAKENED`, [DSL](../skill-dsl.md)).
- No runtime de hoje, quem a usa é `StepExecutor._verify` (`taskqueue/executor.py`), só para pós-condição
  `model_judged` sem nível de entrega exigido. Desde a fase G, a pergunta passa por `StepExecutor._prova_local` →
  `CatalogCapabilityProvider.verify`, e só `proved` dispensa o modelo.
- Não confundir com `failure_marks`: a marca de falha visível é negativa afirmável, e o provider a devolve como
  `not_proved` (passo 4 da escada).
- **Desvio da fase G, mais conservador.** Com a marca de falha visível e a prova positiva, antes era atalho
  (`local_proof_holds` não olha a marca, e a conferência de marca de `_verify` só roda com o efeito disparado); agora
  o provider devolve `not_proved` e o modelo julga
  (`backend/tests/test_habilidades_na_execucao.py::test_a_unica_diferenca_e_a_marca_de_falha_visivel`).

## Relação com o catálogo legado

- `CapabilityDefinition` não substitui `Capability`. É a vista tipada que o código novo (compilador, provider,
  trilha) enxerga sem importar o legado, que a regra D2 proíbe ao domínio.
- `steps.capability` (010) mantém o sentido: é a ação do catálogo, chave de política e de limite.
- O compilador de skills lê o catálogo pela porta `modules/skills/domain/compiler.py::CapabilityLookup` (`app_known`,
  `has_catalog`, `definition`, `offered`), que `CatalogCapabilityRegistry` cumpre por estrutura.
- A baixa para `PlanStep` usa o `CapabilityCatalog.build_step` do legado ([runtime](../skill-runtime.md)).
- **Onde o código difere do desenho da §7** (vale o código):
  - `supports` recebe só a `CapabilityRef`; o desenho tinha também o app;
  - `verify` e `reconcile` devolvem `VerifyResult` (veredito e motivo), não só `VerifyOutcome`;
  - não há `Protocol` `CapabilityCatalogPort`; o papel é de `CapabilityLookup`, no compilador;
  - `human` é estratégia de nó explícita, e não só "último recurso implícito" (§14.3).

## O que ainda não está ligado

**O que a fase G ligou:**

- `StepExecutor.__init__` cria `self.capabilities = CatalogCapabilityProvider(CatalogCapabilityRegistry(...))`, com
  a tradução id de app → pacote lida da tabela `apps` (`StepExecutor._pacote_do_app_id`).
- `StepExecutor._verify` usa `CatalogCapabilityProvider.verify` na prova local (`StepExecutor._prova_local`). A
  equivalência com a `local_proof_holds` direta, sem marca de falha na tela, está provada em dez telas
  (`backend/tests/test_habilidades_na_execucao.py::test_a_prova_local_pela_porta_e_a_mesma_de_antes`).
- `CatalogCapabilityRegistry` também é instanciado pelo compilador (`modules/skills/infrastructure/lowering.py::SkillPlanCompiler`),
  que a execução agora usa por `run_planning.py::SkillRunPlanner`.
- A fatia da fase G usa o mesmo `verify` para observar os casos de validação da skill no `FakeInstagram`
  (`backend/tests/test_fatia_abrir_conversa.py::publicar_abrir`).

**O que ainda não está ligado:**

- `observe`, `execute` e `reconcile` do provider continuam levantando `NotImplementedError`. Nenhum provider toca
  aparelho (decisão 7).
- `ExecutionStrategy` não tem implementação. `RecipeExecutionStrategy`, `AiActorStrategy` e `HumanStrategy`:
  propostos (design §8, [ADR-036](../decisoes.md#adr-036--receitas-como-estratégia-de-execução)). A fase G gravou a
  trilha das estratégias sem extrair a cadeia de `run_step`/`_run_step`.
- `DeterministicStrategy`: proposta. `app_provider` fica fora da v1alpha1.
- `reconciliation` segue prosa até um provider consumi-la.
- As portas legadas do `Scheduler` e do `StepExecutor` (atributos `Callable`) ainda não viraram `Protocol`. Entram
  quando o código que as chama sair do legado (docstring de `ports.py`).

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| `CapabilityDefinition` e mapeamento 1:1 das 23 capabilities do Instagram | implementado | `simulated` (`backend/tests/test_capabilities_do_dominio.py::test_todo_campo_do_catalogo_tem_destino_na_definicao`, `::test_as_23_capabilities_do_instagram_chegam_inteiras`) | `definition.py`, `catalog_registry.py` |
| Imutabilidade e coleta junto com a pós-condição | implementado | `simulated` (`::test_definicao_e_imutavel_e_coleta_anda_com_a_pos_condicao`) | `CapabilityDefinition.__post_init__` |
| Campos sem consumidor sinalizados | implementado | `simulated` (`::test_campos_sem_consumidor_estao_sinalizados_e_continuam_sem_consumidor`) | `SEM_CONSUMIDOR` |
| Estratégias por capability; internas fora da oferta | implementado | `simulated` (`::test_estrategias_por_capability_e_o_que_se_oferece`) | `definicao` |
| Registro por id de app e por referência | implementado | `simulated` (`::test_registro_por_id_de_app_e_por_referencia`) | `CatalogCapabilityRegistry` |
| `verify` pela prova local, com marca de falha e faixa | implementado | `simulated` (`::test_verify_prova_a_conversa_aberta_pela_arvore`, `::test_verify_negativa_da_prova_local_nunca_reprova`, `::test_verify_marca_de_falha_desmente_o_envio`, `::test_verify_nao_afirma_fora_do_alcance_da_prova_local`, `::test_verify_prova_por_faixa_usa_as_guardas_da_etapa`) | `catalog_provider.py` |
| `observe`, `execute` e `reconcile` falham alto | implementado (recusa) | `simulated` (`::test_operacoes_que_tocariam_o_aparelho_falham_alto`) | `catalog_provider.py` |
| Provider cumpre a porta por estrutura | implementado | `simulated` (`::test_provider_cumpre_a_porta_por_estrutura`) | `execution/application/ports.py` |
| `ExecutionStrategy` com implementação | não feito | `not_run` | proposto (ADR-036) |
| `verify` do provider no caminho da execução | implementado | `simulated` (`backend/tests/test_habilidades_na_execucao.py::test_a_prova_local_pela_porta_e_a_mesma_de_antes`, `::test_a_unica_diferenca_e_a_marca_de_falha_visivel`; `backend/tests/test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`) | `StepExecutor._prova_local` |
| Qualquer uso em aparelho real | não feito | `not_run` | — |

Backlog (não implementar aqui):

- `RecipeExecutionStrategy` e `AiActorStrategy` sobre o executor atual, com a ordem de `origin.strategies`.
- Fase K: catálogo como dado, com versão de contrato por capability e identidade de receita por capability.
