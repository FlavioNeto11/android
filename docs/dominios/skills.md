# Domínio: skills (habilidades versionadas)

Uma skill é um processo **versionado** de nós que referenciam capabilities ou outras skills. Cada versão tem conteúdo
imutável, estado próprio e prova registrada. É a fase D da evolução arquitetural ([design](../design/evolucao-arquitetural.md)
§10 e §15; [ADR-034](../decisoes.md#adr-034--versionamento-de-skill)). O conteúdo é o documento da
[DSL](../skill-dsl.md), e quem o transforma em plano é o [compilador](../skill-runtime.md). As operações que os nós
chamam estão em [capabilities](capabilities.md).

Caminhos relativos a `backend/app/`, salvo indicação. Código da fase D: `75f0186` e `11fb8a3`, integrados em `616889b`.

**Estado (27/09).**

- Domínio, repositório SQL, adaptador do fluxo legado e migrações 042–046: testados em SQLite (`simulated`).
- CI com PostgreSQL verde em `793fe00` (run `36324634678`) para as fases A–E, com 042–046. A fiação da fase G em
  PostgreSQL: `not_run`. Nada disto está implantado.
- **A fiação no runtime está feita (fase G, integrada em `0b736f3`):** o registro é composto em `state.py`, e
  `RunService._plan` resolve por ele ([execution](execution.md)). O que falta está em
  [o que ainda não está ligado](#o-que-ainda-não-está-ligado).

## Definição e versão

**`SkillDefinition`** (`modules/skills/domain/versions.py::SkillDefinition`, tabela `skill_definitions`, 042):

- `id` é um slug estável, `^[a-z][a-z0-9_.-]{2,63}$` (`domain/refs.py::SKILL_ID`), como `ig.abrir_conversa`. O prefixo
  `flow:` é reservado ao adaptador legado.
- `name`, `description` e `app_id` (sem FK, como `flows.app_id`).
- `legacy_flow_id`: preenchido só quando a skill adotou um fluxo (índice único parcial `ux_skill_definitions_fluxo`).
- **Escopo** (`skill_scope`, `SkillScope`): perfis e grupos, com a semântica de `flow_scope`.
  - Sem linha, vale para todos. Com escopo, **todos** os perfis da execução precisam estar dentro
    (`SqlSkillRepository._in_scope`, a mesma regra de `FlowStore._no_escopo`).
  - Escopo é distribuição, não conteúdo: muda sem versão nova (`SqlSkillRepository.set_scope`).
- A definição que tem versão não se apaga: a FK de `skill_versions` não tem cascata. A regra é desligar, nunca apagar.

**`SkillVersion`** (`versions.py::SkillVersion`, tabela `skill_versions`, 042):

- `ref` (`SkillRef`), `state`, `schema_version` (`1` = `automation/v1alpha1`; `0` = plano legado congelado).
- `content_json` (forma canônica) e `content_hash`; `document()` devolve sempre uma cópia.
- `command_template` e `match_key`, copiados do conteúdo para casar comando sem abrir o JSON.
- `app_ids` (espelhados em `skill_version_apps`), `parent_version`, `provenance`.
- `state_at`, `state_by` e `state_detail`: a última transição. O histórico inteiro fica em
  `skill_version_transitions`.
- Construção:
  - `SkillVersion.new_draft`: rascunho novo;
  - `SkillVersion.frozen`: versão que já nasce fora de `draft` (o fluxo legado e a v1 de um fluxo adotado).
- A numeração é a maior versão da skill + 1 (`SqlSkillRepository._next_version`).

## Estados e transições

Estados (`domain/lifecycle.py::SkillState`): `draft`, `candidate`, `validated`, `published`, `deprecated` e
`disabled`. A tabela é `lifecycle.py::TRANSITIONS`, fechada: o que não está nela é proibido.

| De → para | Quem pode | O que o repositório confere, na mesma transação |
|---|---|---|
| `draft` → `candidate` | pessoa ("submeter") | o documento compila sem erro (`_require_compiles`). O `content_hash` fica fixado daqui em diante |
| `candidate` → `validated` | sistema ou pessoa | o veredito da validação está pronto (abaixo). Sem veredito pronto, só pessoa, com `manual=True` e motivo escrito |
| `candidate` → `disabled` | pessoa | abandonar uma candidata congelada |
| `validated` → `published` | pessoa | comando livre e publicada anterior depreciada (`_publish_side_effects`) |
| `validated` → `disabled` | pessoa | — |
| `published` → `deprecated` | pessoa ou sistema | o sistema faz isso quando outra versão da mesma skill é publicada |
| `published` → `disabled` | pessoa | parada de emergência: o registro deixa de casar na hora |
| `deprecated` → `published` | pessoa (rollback) | as mesmas condições de publicar. As validações continuam valendo, porque o conteúdo é o mesmo |
| `deprecated` → `disabled` | pessoa | — |

- **Não existe volta a rascunho.** `candidate → draft` e `validated → draft` são recusados (`TransitionForbidden`). Para
  mudar o conteúdo, cria-se uma `draft` nova com `parent_version = n`.
- **Editar não é transição.** `draft → draft` é recusado com a mensagem "use a edição do rascunho"
  (`SqlSkillRepository.update_draft`).
- **`disabled` é terminal** (`lifecycle.py::TERMINAL`).
- **Toda transição diz quem decidiu.** `by` vazio é recusado. `by == "sistema"` (`SYSTEM_ACTOR`) é o sistema; qualquer
  outro nome é pessoa, que é o nome da sessão do painel (padrão da 035).
- **Uma transição é uma instrução com CAS.** `UPDATE … WHERE id=? AND state=?` mais uma linha em
  `skill_version_transitions`. Se outra sessão chegou antes, sai `StateConflict`.
- **O hash é conferido antes de mover** (`SkillVersion.verify_integrity`). Conteúdo que não bate com o hash gravado
  dá `ContentTampered`, e a transição não acontece.
- O plano de uma execução fica em `runs.plan`, e nenhuma transição o toca.

## Congelamento ao sair de `draft`

O conteúdo congela ao **sair** de `draft`, e não só ao publicar (decisão 3). Senão, a validação de `validated`
provaria um conteúdo e a publicação publicaria outro. São três camadas:

1. **Domínio.** `SkillVersion.revise` recusa fora de `draft` (`FrozenVersion`), e o `schema_version` não muda dentro
   de um rascunho.
2. **Repositório, a camada obrigatória.**
   - `update_draft` grava com `AND state='draft'`.
   - `discard_draft` só apaga rascunho, e rascunho referenciado por ensino ou validação não se apaga.
   - `record_result` recusa rascunho: a prova seria de outro conteúdo.
   - Toda leitura que vai executar ou mover a versão confere o hash.
3. **Banco, a segunda camada (046).** Nada no repositório depende dela.
   - SQLite: `skill_versions_congelada` (antes de `UPDATE` de `content`, `content_hash`, `schema_version`,
     `command_template`, `match_key`, `skill_id` ou `version`) e `skill_versions_sem_apagar` (antes de `DELETE`).
     Os dois com `WHEN OLD.state <> 'draft'`.
   - PostgreSQL: a função `skill_versions_congelada()` num gatilho `BEFORE UPDATE OR DELETE`, com
     `ERRCODE = 'integrity_constraint_violation'`. O psycopg a entrega como `IntegrityError`, e `db.INTEGRITY_ERRORS`
     pega os dois dialetos.
   - Os dois só disparam em mudança real (`IS NOT` / `IS DISTINCT FROM`): `SET content = content` numa transição de
     estado passa nos dois bancos.
   - A 046 ficou separada porque é a primeira migração com corpo entre cifrões no PostgreSQL. Ela só entra na
     produção depois de verde num `workflow_dispatch` do CI (comentário do próprio arquivo). Essa condição foi
     cumprida em `793fe00` (run `36324634678`); falta o ensaio numa cópia (ADR-020) e a autorização.

## O ponteiro lógico da publicada

- Não há coluna "versão publicada" em `skill_definitions`: ela criaria um ciclo de FK que
  `tools/migrate_data.py::ordem_por_fk` não resolve.
- O ponteiro é a única linha com `state='published'` da skill, garantida pelo índice parcial
  `ux_skill_versions_publicada`.
- **Publicar** move o ponteiro numa transação só:
  1. o comando precisa estar livre;
  2. a publicada anterior vai para `deprecated`, com `by = "sistema"`;
  3. a nova vai para `published`.
  - A anterior sai **antes**, porque os índices conferem instrução a instrução, e a anterior quase sempre tem o mesmo
    comando (`ux_skill_versions_comando`).
- **Rollback** é `deprecated → published` da versão antiga, que deprecia a atual pelo mesmo caminho.
- `published(skill_id)` lê o ponteiro, e `resolve` só olha publicadas.

## Validação: casos (043) e a regra P4

- **Caso** (`domain/validation.py::ValidationCase`, tabela `skill_validation_cases`):
  - pertence à **definição** e vale numa faixa de versões (`since_version`/`until_version`). Assim, a regressão da
    v3 roda o que a v2 já passou;
  - `kind`: `replay`, `simulated`, `device` ou `negative`. O caso negativo espera recusa, por exemplo "caixa de
    entrada com a linha do usuário não prova `OPEN_THREAD`";
  - `status`: `active` ou `retired` (`SqlSkillRepository.retire_case`, com ou sem fechar a faixa).
- **Observação** (`ValidationResult`, tabela `skill_validation_results`):
  - `proof` (`real` | `simulated`) e `outcome` (`passed` | `failed` | `uncertain` | `blocked`);
  - `run_id` sem FK (a execução pode ser purgada; a prova fica), `instance_id`, `physical_id`, `app_version`,
    `variant`, `detail`, `observed_at` e `observed_by`;
  - é o mesmo desenho de `app_release_validations` (011): a transição lê observação registrada, nunca um booleano.
  - **`not_run` é a ausência de linha.**
- **Veredito** (`validation.py::validation_verdict`): pronto quando há pelo menos um caso ativo na faixa da versão e
  o **último** resultado de cada caso é `passed`.
  - O caso negativo conta como os outros: `passed` quer dizer que a recusa esperada aconteceu.
  - **P4:** para caso `device`, só entram resultados `proof=real`. Um `simulated` posterior não substitui o último
    real, nem para aprovar nem para reprovar. Sem nenhum real, a pendência é "sem observação com prova real".
- **Validação manual:** `transition(ref, VALIDATED, by=<pessoa>, reason=<motivo>, manual=True)`.
  - Só `validated` admite decisão manual, e só de pessoa com motivo escrito.
  - O `state_detail` fica "validação manual: <motivo> (pendências: …)": o que faltava fica registrado.
- **Segredo não entra:**
  - `add_case` recusa parâmetro com nome de credencial ou valor com formato de credencial (`SecretInParameters`,
    `security/redaction.py::chave_sensivel`/`looks_secret`);
  - o `detail` da observação passa por `redact`.

## Proveniência

`versions.py::Provenance`:

- `kind` (`SourceKind`: `legacy_flow`, `teaching`, `run`, `manual`, `import`) e `ref` viram as colunas `source_kind` e
  `source_ref`.
- O resto vai para o JSON `provenance`: `candidate_id`, `teaching_id`, `generated_by` (`ai:<modelo>` | `person` |
  `merge`), `compiler_version`, `uses_lock` (`[{skill, version, content_hash}]`), `reviewed_by` e `notes`.
- No fluxo legado e na adoção, `notes` guarda `flows.source` e `flows.source_run_id`
  (`legacy_flows.py::legacy_provenance`).

## `SkillRef` e o hash canônico

**`SkillRef`** (`domain/refs.py::SkillRef`) é o nome inequívoco de uma versão:

- skill nova: `<skill_id>@<n>`, com `n ≥ 1` (`ig.abrir_conversa@3`);
- fluxo legado: `flow:<flows.id>@1` (`SkillRef.legacy`). O id do fluxo não segue o slug: nasce do resumo do plano,
  pode começar com dígito e ser curto. Aceita-se qualquer coisa sem `@` e sem espaço, até 120 caracteres. Fluxo não
  tem versões: é sempre a 1;
- `SkillRef.parse` lê o texto; texto sem versão é `InvalidSkillRef`.

**Hash canônico** (`domain/document.py`):

- `canonical_json`: chaves ordenadas, separadores `(",", ":")`, `ensure_ascii=False` e `allow_nan=False`.
  `content_hash` é o sha256 desse texto em UTF-8.
- Não se usa `db.dumps`, porque ele não ordena as chaves: o mesmo documento em outra ordem daria outro hash, e a
  conferência de integridade acusaria adulteração onde não houve.
- O documento precisa ser JSON de verdade (`as_json_value`): NaN, Infinito, chave que não é texto e tipos estranhos
  dão `NotJson`.
- No fluxo legado, o hash é calculado na leitura e nunca gravado: ele reflete o fluxo de agora.
- O IR do compilador tem hash próprio, calculado pela mesma função: `ir.py` reexporta `canonical_json` e
  `content_hash` daqui (fase G, [runtime](../skill-runtime.md#determinismo-e-hash)).

## Um registro, dois backends

`modules/skills/application/registry.py::CompositeSkillRegistry(skills, legacy, *, skills_enabled, flows_enabled)`.
Os dois backends cumprem `application/ports.py::SkillSource`: `SqlSkillRepository` e `LegacyFlowAdapter`.

Na produção, quem compõe é `state.py::AppState.__init__` (fase G): `AppState.skill_repo`, `AppState.skill_registry` e
o `AppState.skill_planner` (`infrastructure/run_planning.py::SkillRunPlanner`), que é o que a execução, o pré-voo e
`GET /api/flows/match` consultam ([execution](execution.md#resolve-e-compile-skillrunplanner)).

**Ordem de resolução** (`resolve(command, profile_ids)`):

1. skill publicada, se `skills_enabled()`;
2. fluxo ativo, se `flows_enabled()`;
3. `None`: o planejador fica com o comando.

- Os interruptores são lidos **a cada chamada**: a configuração muda com o processo no ar.
- **Só `resolve` obedece aos interruptores.** `get`, `published` e `definition` vão ao backend pelo prefixo (`flow:`
  vai ao legado) e não dependem deles, porque servem à trilha de execuções passadas. `list` junta os dois.
- `profile_ids=None` é a prévia sem aparelhos: qualquer escopo casa.
- **Entre várias publicadas que casam** (`SqlSkillRepository.resolve`), ganha a mais específica
  (`matching.py::specificity`: mais texto fixo, depois menos buracos) e, no empate, o `skill_id`.
  - O fluxo ordena por `uses DESC`. A skill não tem contador de uso, e por isso a resposta não muda com o tempo.
- Uma publicada adulterada que casa é recusa (`ContentTampered`). O registro não pula para a próxima: executar outra
  coisa em silêncio seria pior.
- Com conteúdo legado (`schema_version` 0), o comando precisa dar valor a todo parâmetro-modelo do plano
  (`matching.py::bind_template_parameters`); senão, não é esta versão.
- **Casamento de comando:** `domain/matching.py` é a cópia pura de `taskqueue/flows.py` (`_norm`, `_extract`,
  `_squash`, `RESERVED` e o teto de 500 caracteres). Um fluxo adotado continua casando os mesmos comandos. Proposto
  (fase I): `FlowStore` passa a delegar para cá, e a cópia some.

**`LegacyFlowAdapter`** (`infrastructure/legacy_flows.py`), só leitura:

- a resolução delega a `FlowStore.match`: ordem, escopo e extração são exatamente os de hoje;
- o fluxo vira versão assim:
  - estado `published` se `flows.status='active'`, senão `disabled`;
  - conteúdo `{schema_version: 0, command_template, plan, required_apps}`, com os apps de `flow_required_apps`;
- `legacy_plan(resolved)` monta o `Plan` de conteúdo legado sem compilador e sem IA:
  - `flow:<id>@1` dá o mesmo `Plan` que `FlowStore.match` devolve hoje (`planner.model = "fluxo:<id>"`);
  - a v1 de um fluxo adotado dá `planner.model = "skill:<id>@<n>"`, e o resto é igual.

## `skills.enabled` × `ai.flows`

- `config.py::SkillsCfg.enabled`, padrão `false` (decisão P1), liga a resolução por skill publicada antes do fluxo.
- `ai.flows` (`config.py::AiCfg.flows`) continua mandando no fluxo legado, agora como `flows_enabled` do registro
  (antes, `taskqueue/service.py::_plan` o conferia direto). Não foi reaproveitado: já quer dizer "fluxo legado", e o
  valor de cada instalação não foi lido.
- Com os dois desligados, o registro não resolve nada. Publicar uma skill não liga nada sozinho.
- Adotar um fluxo com `skills.enabled` desligado é recusado (`SkillsDisabled`, [abaixo](#recusas-do-repositório)):
  o fluxo seria desligado e o comando ficaria sem resolução.
- `features.skills` no painel é proposto (fase F).

## Adoção de fluxo

`SqlSkillRepository.adopt_flow(flow_id, *, skill_id, by, reason)`, numa só transação:

1. o fluxo precisa estar `active`;
2. a definição ganha `legacy_flow_id`. Readotar depois de desfazer reusa a mesma definição, e dois donos para o mesmo
   fluxo são recusados;
3. nasce uma versão `published` com o conteúdo do fluxo (`schema_version` 0, `source_kind='legacy_flow'`);
4. se outra skill publicada já tem o comando, a adoção é recusada (`DuplicateCommand`);
5. o escopo é copiado de `flow_scope`;
6. `UPDATE flows SET status='disabled'`, com CAS no status anterior.

- **Desfazer:** `release_flow(skill_id, *, by, reason)` desabilita a publicada e religa o fluxo, na mesma transação.
  Recusa se outra skill publicada já usa o comando.
- **Sem escrita dupla.** `flows` só é tocado nessas duas operações, e só no `status`. `FlowStore` nunca escreve em
  tabela de skill.
- **Publicar recusa o comando de um fluxo ativo** (`E_DUPLICATE_COMMAND`), a não ser que seja o fluxo que a própria
  skill adotou. Nesse caso, o fluxo é desligado na mesma transação.
- **Religar pela rota é recusado** (fase G): `PUT /api/flows/{id}` com `status: active` responde 409 `flow_adopted`
  enquanto `SqlSkillRepository.published_adopter(flow_id)` achar a versão publicada que o adotou
  ([contrato](../api-contract.md#adendo-v021-27092026--habilidades-no-caminho-dos-fluxos)). Voltar ao fluxo é
  `release_flow`.
- O rascunho v2 com a edição, gerado por um descompilador `Plan → DSL`, é da fase J.

## Tabelas 042–046

| Migração | Tabelas ou colunas | Para quê |
|---|---|---|
| 042 | `skill_definitions`, `skill_versions`, `skill_version_transitions`, `skill_version_apps`, `skill_scope` | definição, versões, trilha de transições, apps exigidos (sem FK para `apps`, de propósito) e escopo |
| 043 | `skill_validation_cases`, `skill_validation_results` | casos e observações |
| 044 | `teaching_sessions`, `teaching_demonstrations`, `teaching_turns`, `teaching_candidates` | ensino v2 (fase F); nenhum código as usa ainda |
| 045 | `runs` (`skill_id`, `skill_version`, `skill_hash`), `objectives` (`resource_plan`), `steps` (`skill_id`, `skill_version`, `node_id`, `strategy`), `attempts` (`strategy`, `recipe_id`), `ai_calls` (`attempt_id`) | a trilha da execução; tudo nulo, sem FK e sem preencher linhas antigas |
| 046 | gatilhos `skill_versions_congelada` e `skill_versions_sem_apagar` (SQLite) ou `skill_versions_congelada` (PostgreSQL) | segunda camada do congelamento |

- Índices que fazem regra: `ux_skill_versions_publicada` (uma publicada por skill), `ux_skill_versions_comando` (um
  comando publicado), `ux_skill_definitions_fluxo` (um dono por fluxo adotado) e `ux_teaching_demo_gravacao` (uma
  gravação em no máximo um ensino).
- Sem `CHECK` nos estados, como na 041: a lista muda sem migração.
- A unicidade de comando **entre** `skill_versions` e `flows` não cabe no banco. Quem a garante é o repositório, ao
  publicar e ao adotar.
- Trilha antiga sem migração: `runs.flow_id` preenchido com `runs.skill_id` nulo quer dizer `flow:<flow_id>@1`. O
  `skill_hash` nulo quer dizer "não se sabe", e nunca é inventado.
- A lista geral das migrações está em [`banco.md`](../banco.md).

## Recusas do repositório

As recusas são exceções com `code` estável (`lifecycle.py::SkillError` e filhas), que a API vai expor:

| `code` | Exceção | Quando |
|---|---|---|
| `not_found` | `SkillNotFound` | versão, definição, caso ou fluxo inexistente |
| `transition_forbidden` | `TransitionForbidden` | fora da tabela, ator errado, sem `by`, manual sem motivo |
| `frozen_version` | `FrozenVersion` | editar ou apagar versão fora de `draft`; gravar prova num rascunho |
| `content_tampered` | `ContentTampered` | hash gravado não bate com o conteúdo |
| `E_DUPLICATE_COMMAND` | `DuplicateCommand` | comando já publicado em outra skill ou num fluxo ativo não adotado |
| `invalid_document` | `InvalidDocument` | documento não é JSON, `metadata.id` de outra skill, sem nome, ou não compila (com `errors`) |
| `validation_pending` | `ValidationPending` | `validated` sem a prova exigida (com `pending`) |
| `state_conflict` | `StateConflict` | outra sessão mudou a linha antes, ou o banco recusou por índice |
| `secret_in_parameters` | `SecretInParameters` | credencial em caso de validação |
| `skills_disabled` | `SkillsDisabled` | adotar fluxo com `skills.enabled` desligado. Só vale quando o repositório recebe `adoption_enabled`, e a composição do `AppState` sempre o passa (fase G) |

`InvalidSkillRef` (um `ValueError`) recusa id ou referência mal formados antes de chegar ao banco.

## O que ainda não está ligado

**O que a fase G ligou** (integrada em `0b736f3`; o ciclo está em [execution](execution.md)):

- `RunService._plan`, `RunService.apps_exigidos` e `GET /api/flows/match` resolvem pelo registro, pela mesma porta
  (`AppState.skill_planner`).
- **Validador de produção:** `infrastructure/document_validator.py::DslDocumentValidator` cumpre `DocumentValidator`
  com o esquema `automation/v1alpha1` e o mesmo `SkillPlanCompiler` da execução.
  - `inspect` lê do documento cru o que ele declara (id, nome, app, comando-modelo, apps exigidos). Rascunho com erro
    se salva; os erros, em `DocumentFacts.errors` como `E_CODIGO /caminho: mensagem`, só barram `draft → candidate`.
  - Prova: `backend/tests/test_habilidades_na_execucao.py::test_o_validador_cumpre_a_porta`,
    `::test_o_validador_le_os_fatos_do_documento`, `::test_rascunho_com_erro_se_salva_mas_nao_submete` (`simulated`).
- **`SkillLookup` de produção:** `document_validator.py::LockedVersions` lê a versão exata do repositório, com o
  `content_hash` como trava.
  - Filha em `draft` ou `disabled` não se compõe (`NAO_SE_COMPOE`); fluxo legado não se compõe; versão adulterada
    sobe como recusa.
  - Uma filha desabilitada **depois** de a composta ser publicada faz a composta parar em `needs_input` na execução,
    sem plano parcial (`::test_filha_desabilitada_depois_de_publicada_para_a_composta_sem_plano_parcial`;
    `test_fatia_abrir_conversa.py::test_filha_desabilitada_poe_a_composta_em_needs_input_sem_plano`).
- **Trilha da 045:** `runs.skill_*`, `steps.skill_*`/`node_id`/`strategy`, `attempts.strategy`/`recipe_id` e
  `ai_calls.attempt_id` têm escritor ([execution](execution.md#a-trilha-colunas-da-045)).
- **As três guardas apontadas pela fase D:**
  - `adopt_flow` recusa com `skills.enabled` desligado (`SkillsDisabled`,
    `::test_adotar_fluxo_com_as_habilidades_desligadas_e_recusado`);
  - `PUT /api/flows/{id}` recusa religar fluxo adotado com 409 `flow_adopted`
    (`test_fatia_abrir_conversa.py::test_rotas_de_fluxo_respeitam_a_skill`);
  - `GET /api/flows/match` passa a respeitar `ai.flows`
    (`backend/tests/test_perfil_bloqueado_e_capacidades.py::test_estimativa_de_custo_por_fluxo`).
- **Aprendizado de fluxo:** `FlowStore.learn_from_run` não aprende de execução de skill nem de comando que uma skill
  publicada cobre (`::test_fluxo_nao_se_aprende_de_skill_nem_do_comando_que_uma_skill_publicada_cobre`).
- **P4 exercido de ponta a ponta:** na fatia, `ig.abrir_conversa` é validada pelo sistema com os dois casos do
  documento observados no `FakeInstagram` (`simulated`); `ig.ler_conversa`, pela via manual do dono, com motivo.

**O que ainda não está ligado:**

- Os casos de `spec.validation.cases` do documento são validados pelo compilador, mas não viram linhas da 043:
  `add_case` continua sendo uma chamada à parte (a fatia os insere à mão).
- O `uses_lock` que o compilador calcula não é gravado na `provenance` da versão.
- A execução de um fluxo adotado grava `runs.skill_id` e não grava `runs.flow_id` nem `flows.used` (decisão da fase J).
- `DELETE /api/flows/{id}` não tem guarda: apagar um fluxo adotado faz `release_flow` recusar com `SkillNotFound`
  ("o fluxo foi apagado"). A versão adotada continua executável, porque o conteúdo foi copiado.
- Rotas `/api/skills`, ensino v2 e `features.skills`: fase F.

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| Hash canônico, cópia defensiva, `SkillRef` | implementado | `simulated` (`backend/tests/test_habilidades_dominio.py::test_mesmo_conteudo_da_o_mesmo_hash_qualquer_que_seja_a_ordem_das_chaves`, `::test_skill_ref_formato_e_ida_e_volta`, `::test_skill_ref_do_fluxo_legado_aceita_qualquer_id_de_fluxo`) | `document.py`, `refs.py` |
| Tabela de transições estado × estado | implementado | `simulated` (`test_habilidades_dominio.py::test_a_tabela_e_exatamente_a_do_design`, `::test_cada_par_de_estados_pela_pessoa`, `::test_cada_par_de_estados_pelo_sistema`, `::test_congelada_nao_volta_a_rascunho_e_disabled_e_terminal`) | `lifecycle.py` |
| Congelamento no domínio e no repositório | implementado | `simulated` (`test_habilidades_dominio.py::test_fora_de_rascunho_o_conteudo_nao_muda`; `test_habilidades_repositorio.py::test_rascunho_muda_e_versao_submetida_nao_mesmo_sem_o_gatilho`, `::test_versao_adulterada_e_recusada_na_leitura_e_na_transicao`) | `versions.py`, `sql_repository.py` |
| Gatilho da 046 | implementado | `simulated` em SQLite (`test_habilidades_migracoes.py::test_a_046_recusa_mudar_ou_apagar_versao_fora_de_rascunho`, `::test_a_046_divide_em_duas_instrucoes_em_cada_dialeto`); `simulated` em PostgreSQL (CI run `36324634678` em `793fe00`) | `046_versao_congelada.sql` |
| P4 e validação manual | implementado | `simulated` (`test_habilidades_dominio.py::test_caso_device_so_conta_com_prova_real`; `test_habilidades_repositorio.py::test_validated_exige_observacao_e_caso_device_so_com_prova_real`, `::test_validacao_manual_e_de_pessoa_com_motivo_e_fica_registrada`) | `validation.py` |
| Ponteiro da publicada e rollback | implementado | `simulated` (`test_habilidades_repositorio.py::test_publicar_deprecia_a_anterior_na_mesma_transacao_e_rollback_volta`; `test_habilidades_migracoes.py::test_o_banco_recusa_duas_publicadas_da_mesma_habilidade`) | `sql_repository.py`, 042 |
| Comando único entre skills e fluxos | implementado | `simulated` (`test_habilidades_repositorio.py::test_publicar_recusa_o_comando_de_outra_habilidade_e_nao_mexe_em_nada`, `::test_publicar_recusa_o_comando_de_um_fluxo_ativo_que_nao_e_o_adotado`) | `_publish_side_effects` |
| Adoção e desfazer numa transação | implementado | `simulated` (`test_habilidades_repositorio.py::test_adotar_desliga_o_fluxo_na_mesma_transacao`, `::test_adocao_que_falha_nao_deixa_nada_pela_metade`, `::test_desfazer_a_adocao_religa_o_fluxo_e_readotar_usa_a_mesma_definicao`) | `adopt_flow`, `release_flow` |
| Precedência skill → fluxo → nada e interruptores | implementado | `simulated` (`test_habilidades_legado.py::test_precedencia_e_interruptores`, `::test_os_interruptores_sao_lidos_a_cada_chamada`, `::test_leitura_por_nome_vai_ao_backend_certo_e_nao_depende_do_interruptor`) | `registry.py` |
| `flow:<id>@1` igual ao `FlowStore` | implementado | `simulated` (`test_habilidades_legado.py::test_golden_do_mapeamento_fluxo_para_versao`, `::test_o_plano_do_adaptador_e_o_mesmo_do_flowstore`) | `legacy_flows.py` |
| Casamento igual ao do fluxo | implementado | `simulated` (`test_habilidades_dominio.py::test_a_extracao_e_a_mesma_do_fluxo_legado`, `::test_a_chave_de_casamento_e_a_mesma_do_fluxo_legado`) | `matching.py` |
| Segredo fora das tabelas novas | implementado | `simulated` (`test_habilidades_repositorio.py::test_segredo_nao_entra_em_caso_de_validacao_nem_na_observacao`) | `_refuse_secrets` |
| Migrações 041 → 046 | implementado | `simulated` em SQLite (`test_habilidades_migracoes.py::test_atualizacao_de_041_para_046_nao_toca_o_legado`, `::test_banco_novo_e_banco_atualizado_tem_o_mesmo_esquema`, `::test_a_copia_entre_bancos_acha_ordem_por_fk_com_as_tabelas_novas`); `simulated` em PostgreSQL (CI run `36324634678` em `793fe00`) | 042–046 |
| Validador de produção e trava de composição | implementado | `simulated` (`test_habilidades_na_execucao.py::test_rascunho_com_erro_se_salva_mas_nao_submete`, `::test_composta_so_submete_com_a_filha_congelada`) | `document_validator.py` |
| Registro no `_plan` e trilha da 045 | implementado | `simulated` (`test_fatia_abrir_conversa.py::test_abrir_conversa_pela_skill_publicada_sem_planejador_e_com_a_trilha`, `::test_fluxo_legado_grava_a_trilha_de_sempre_e_o_hash`) | `run_planning.py`, `RunService._plan` |
| Guardas de adoção e de `PUT /api/flows/{id}` | implementado | `simulated` (`test_habilidades_na_execucao.py::test_adotar_fluxo_com_as_habilidades_desligadas_e_recusado`; `test_fatia_abrir_conversa.py::test_rotas_de_fluxo_respeitam_a_skill`) | `SkillsDisabled`, `published_adopter`, `api.py::update_flow` |
| Fiação da fase G em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Implantação (ensaio em cópia, ADR-020) | não feito | `not_run` | — |

Backlog (não implementar aqui):

- Rodar a suíte em PostgreSQL com a fase G (`workflow_dispatch`) antes de qualquer deploy.
- Casos do documento para a 043 e `uses_lock` na `provenance`, na publicação.
- Fase J: descompilador `Plan → DSL`, rota v1 → v2, conversão dos fluxos ativos e a trilha por fluxo adotado.
