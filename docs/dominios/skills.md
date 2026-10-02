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
- **A resolução de intenção está feita (fase I, `00633d5` e `0795cc7`, integrados em `21b1fff`):** a RESOLVE é uma
  cadeia com parâmetros tipados, e o que não se decide vira pergunta
  ([abaixo](#resolução-de-intenção)). Suíte SQLite 2252/2252 no branch da fase (`simulated`); PostgreSQL e prova
  real: `not_run`.
- **O ensino v2 e as rotas `/api/skills` estão feitos (fase F, `474aceb` e `63507ad`, integrados em `6b04164`):** a
  candidata do ensino vira rascunho de versão ([abaixo](#ensino-v2-fase-f)). Prova `simulated`; IA real, PostgreSQL
  e conferência visual: `not_run`.
- **A conversão de fluxos legados está feita (fase J, `9d2b736`, `4ddba1a`, `fa21cec` e `c4f40d6`, integrados em
  `5b1957f`):** descompilador `Plan → automation/v1alpha1`, converter e desfazer numa transação, trilha da v1 adotada
  e a bateria legado × novo ([abaixo](#conversão-de-fluxo-fase-j)). Suíte SQLite 2306 no branch da fase
  (`simulated`, relatado pelo coordenador); PostgreSQL, fluxos reais de produção e conferência visual: `not_run`.
- **Um processo pode atravessar apps (fase K1):** uma skill composta reusa uma filha do Instagram e age no QA, num
  aparelho só ([runtime](../skill-runtime.md#processo-cross-app-fase-k1)). Prova `simulated`, com o QA registrado só
  em teste.

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
Os dois backends cumprem `application/ports.py::SkillSource` e, desde a fase I,
`application/intent_ports.py::SkillCandidateSource` (o método `candidates`): `SqlSkillRepository` e
`LegacyFlowAdapter`.

Na produção, quem compõe é `state.py::AppState.__init__` (fase G): `AppState.skill_repo`, `AppState.skill_registry` e
o `AppState.skill_planner` (`infrastructure/run_planning.py::SkillRunPlanner`), que é o que a execução, o pré-voo,
`POST /api/flows/match` e `POST /api/skills/resolve` consultam
([execution](execution.md#resolve-e-compile-skillrunplanner)).

**Ordem dos candidatos** (`CompositeSkillRegistry.candidates(command, profile_ids)`, fase I):

1. as skills publicadas que casam **inteiras**, se `skills_enabled()`: todas as da força mais alta;
2. senão, o fluxo ativo que casa, se `flows_enabled()`: um só;
3. senão, as skills publicadas que casam com um `{nome}` **vazio** (só conteúdo da DSL), que servem só para
   perguntar o que falta;
4. senão, nenhum: o planejador fica com o comando.

- Quem escolhe entre os candidatos é o `IntentResolver` ([abaixo](#resolução-de-intenção)).
- `CompositeSkillRegistry.resolve` continua existindo como a precedência crua: o primeiro candidato completo, sem
  desempate nem tipo. A execução, o pré-voo e as prévias não o usam mais.
- Os interruptores são lidos **a cada chamada**: a configuração muda com o processo no ar.
- **Só a resolução (`candidates` e `resolve`) obedece aos interruptores.** `get`, `published` e `definition` vão ao
  backend pelo prefixo (`flow:` vai ao legado) e não dependem deles, porque servem à trilha de execuções passadas.
  `list` junta os dois.
- `profile_ids=None` é a prévia sem aparelhos: qualquer escopo casa.
- **Entre várias publicadas que casam** (`SqlSkillRepository.candidates`), só as da força mais alta são candidatas
  (`matching.py::specificity`: mais texto fixo, depois menos buracos), em ordem de `skill_id`. O empate não se
  decide mais pelo id: vira pergunta, a menos que os tipos o desfaçam.
  - O fluxo ordena por `uses DESC`. A skill não tem contador de uso, e por isso a resposta não muda com o tempo.
- Uma publicada adulterada **entre as devolvidas** é recusa (`ContentTampered`), inclusive no empate
  (`backend/tests/test_intencao_resolucao.py::test_adulterada_entre_as_empatadas_e_recusa`). O registro não pula para
  a próxima: executar outra coisa em silêncio seria pior. A que perderia pela força nem é lida.
- Com conteúdo legado (`schema_version` 0), o comando precisa dar valor a todo parâmetro-modelo do plano
  (`matching.py::bind_template_parameters`); senão, não é esta versão.
- **Casamento de comando:** `domain/matching.py` é a cópia pura de `taskqueue/flows.py` (`_norm`, `_extract`,
  `_squash`, `RESERVED` e o teto de 500 caracteres). Um fluxo adotado continua casando os mesmos comandos. Proposto:
  `FlowStore` passar a delegar para cá, e a cópia sumir. A fase I não fez isso; só acrescentou
  `matching.py::extract_with_gaps`, que o fluxo não usa.

**`LegacyFlowAdapter`** (`infrastructure/legacy_flows.py`), só leitura:

- a resolução delega a `FlowStore.match`: ordem, escopo e extração são exatamente os de hoje;
- `candidates` devolve só o fluxo que `FlowStore.match` escolheria. Fluxo não empata (a ordem por uso decide), não tem
  tipo e não casa com buraco vazio: o comando pela metade continua indo ao planejador;
- o fluxo vira versão assim:
  - estado `published` se `flows.status='active'`, senão `disabled`;
  - conteúdo `{schema_version: 0, command_template, plan, required_apps}`, com os apps de `flow_required_apps`;
- `legacy_plan(resolved)` monta o `Plan` de conteúdo legado sem compilador e sem IA:
  - `flow:<id>@1` dá o mesmo `Plan` que `FlowStore.match` devolve hoje (`planner.model = "fluxo:<id>"`);
  - a v1 de um fluxo adotado dá `planner.model = "skill:<id>@<n>"`, e o resto é igual.

## Resolução de intenção

Fase I (design §14.1, RESOLVE, e §10.4). A RESOLVE deixou de ser "o primeiro que casa" e virou uma cadeia explícita:
`modules/skills/application/intent_resolver.py::IntentResolver`. Quem a usa é `SkillRunPlanner.resolve_intent`
([execution](execution.md#resolve-e-compile-skillrunplanner)).

**Três respostas, e só três** (`domain/intent.py::IntentResolution`, `ResolutionStatus`):

- `resolved`: uma habilidade (`ResolvedIntent`), com cada valor validado pelo tipo declarado. Cada
  `ExtractedParameter` traz `name`, `type`, `value`, `origin` (`command` ou `default`) e `raw`;
- `needs_input`: a pergunta estruturada (`MissingInfo`). **Nunca se escolhe às cegas nem se inventa valor**;
- `no_match`: nada casa, e o planejador fica com o comando, como sempre.

**A cadeia** (`IntentResolver.standard(source, extractor, *, classifier, disambiguator)`), em ordem:

| Etapa | Classe | O que faz | Provedor hoje |
|---|---|---|---|
| 1. modelos | `TemplateStage` | pergunta `CompositeSkillRegistry.candidates`: precedência, interruptores e escopo de sempre; todos os candidatos da força mais alta | o registro |
| 2. tipos | `TypedStage` + `ParameterExtractor` | valida cada valor pelo `ParameterSpec` do documento (`domain/intent.py::extract_typed`) | puro |
| 3. semântica | `SemanticStage` | classificador, só quando nenhum modelo casou inteiro | `NullSemanticClassifier` |
| 4. LLM | `LlmDisambiguationStage` | desempata dois ou mais candidatos válidos | `NullDisambiguator` |

`intent_resolver.py::conclude` transforma o estado final numa das três respostas. O que sobra sem decisão é pergunta.

**Como se decide** (`TypedStage.apply` e `conclude`):

- **Um candidato válido** (completo e com todos os valores servindo ao tipo): `resolved`, `method: template`.
- **Um candidato com valor vazio ou inválido:** `needs_input` sobre ele (`subject`), com uma pergunta por parâmetro.
  - `reason: missing_parameter`: obrigatório sem valor, ou `{nome}` vazio no comando;
  - `reason: invalid_parameter`: o valor não serve ao tipo. A pergunta traz o que veio (`received`), o que serve
    (`expected`) e, no `enum`, as opções.
- **Empate em que só um passa nos tipos:** esse ganha, `method: typed` (trilha `tie_broken`). Exemplo: duas skills
  com o mesmo texto fixo, uma com `{n}` inteiro e outra com `{username}` usuário; com `@ana`, só a segunda serve
  (`backend/tests/test_intencao_resolucao.py::test_empate_que_os_tipos_desfazem`).
- **Empate sem desempate** (dois ou mais válidos, ou nenhum válido): `needs_input` com `reason: ambiguous_intent`,
  `field: skill` e as referências em `options` (`domain/intent.py::ambiguity_question`). Com "3", que serve a inteiro
  e a usuário, não há o que desempate. Antes da fase I, ganhava o menor `skill_id`
  (`::test_duas_habilidades_empatadas_viram_pergunta_nunca_o_primeiro_id`).

**Escopo e força:**

- A força é `matching.py::specificity`. Só os candidatos do topo entram na cadeia.
- O escopo por perfil e grupo filtra **antes** do empate: uma empatada fora do escopo não conta
  (`::test_escopo_vale_no_empate`).
- **Buraco vazio** (`matching.py::extract_with_gaps`): "abra a conversa com no instagram" casa o modelo com
  `{username}` vazio.
  - Só entra conteúdo da DSL, e só quando nada casa inteiro. Um fluxo que case inteiro ganha
    (`::test_skill_publicada_ganha_do_fluxo_e_buraco_vazio_perde_para_fluxo_inteiro`).
  - O buraco vazio pergunta mesmo quando o parâmetro tem padrão: o padrão não decide por quem escreveu o comando pela
    metade.
- **Valor inválido não cai para o fluxo.** Se a skill casa inteira, o fluxo nem é consultado, e o valor que não serve
  vira pergunta (`::test_interruptores_continuam_mandando`).

**Tipos.** A tabela de extração e normalização está na [DSL](../skill-dsl.md#tipos-extração-e-normalização-fase-i).

- Conteúdo legado (`schema_version` 0: o fluxo ou a v1 de um fluxo adotado) não declara tipo
  (`intent_resolver.py::parameter_specs` devolve `None`): o valor passa como veio.
- O compilador recebe só os valores que o **comando** deu, já normalizados
  (`ParameterExtraction.command_values`). O padrão é só mostrado (`origin: default`); quem o aplica continua sendo
  `compiler.py::_Compilacao._ligar`.
- **Link de perfil.** O domínio não conhece app nenhum: aplica a `ProfileLinkRule` que a borda injeta
  (`domain/intent.py::handle_from_link`). A regra de cada app é dado, `links_de_perfil` do `app.yaml` do pacote
  (ADR-052, fatia 4), e chega pela definição do app no registro: `infrastructure/profile_links.py::profile_links_for`,
  indexada pelo pacote. O
  `SkillRunPlanner` chega ao pacote pelo `app_id` da definição. Só o Instagram tem regra; link de outro app vira
  pergunta.

**Etapas 3 e 4: portas com o provedor nulo** (`application/intent_ports.py`):

- `SemanticIntentClassifier` e `IntentDisambiguator` são `Protocol`s. A única implementação de cada um é a nula
  (`intent_resolver.py::NullSemanticClassifier`, `NullDisambiguator`, com `available = False`). Ela **não chama
  IA**, e a trilha registra `not_run` ("provedor nulo: sem IA sem autorização").
- `state.py` não passa provedor ao `SkillRunPlanner`. Em produção, as duas etapas nunca decidem: registram `not_run`
  quando teriam o que fazer, e `skipped` quando não (intenção já escolhida, candidato completo, menos de dois válidos).
- Um provedor plugado passa pelos mesmos tipos e só vale se escolher um dos candidatos que recebeu. Isso está provado
  com dublês (`backend/tests/test_intencao_dominio.py::test_desempate_plugado_so_vale_se_escolher_um_dos_candidatos`,
  `::test_classificador_plugado_passa_pelos_mesmos_tipos`, `simulated`).
- Proposto: um provedor real precisa ser assíncrono e rodar só no `_plan`, com aviso de custo. Nunca na prévia
  (`/api/flows/match`, `apps_exigidos`, `/api/skills/resolve`), que o painel chama a cada tecla.

**Trilha das etapas** (`StageTrace`): uma linha por etapa, com `outcome` em `domain/intent.py::StageOutcome`
(`matched`, `partial`, `ambiguous`, `no_match`, `validated`, `invalid`, `tie_broken`, `skipped`, `not_run`). Aparece
só na resposta de `POST /api/skills/resolve` (`stages`). A execução não a grava, nem grava o `method`.

**O que mudou, e com que interruptor.** Com `skills.enabled` desligado, nada: o registro nem consulta as skills, e o
fluxo legado dá a mesma resposta do `FlowStore.match`, com os valores como vieram. Provas (`simulated`): a tabela de
15 casos de `::test_paridade_com_o_flowstore_match` (sem skill publicada), `::test_empate_entre_fluxos_continua_decidido_pelo_uso`
e `::test_interruptores_continuam_mandando`. Com ele ligado:

| Situação | Antes (fase G) | Agora (fase I) |
|---|---|---|
| duas skills empatadas | o menor `skill_id` | pergunta, salvo desempate pelos tipos |
| valor que não serve ao tipo | passava cru ao compilador, que não confere tipo | pergunta (`invalid_parameter`) |
| `{nome}` vazio no comando | não casava: fluxo ou planejador | pergunta (`missing_parameter`), se nenhum fluxo casar inteiro |
| `handle` | como veio | `@nome` em minúsculas; `@ana` sai idêntico (`::test_resultado_identico_ao_da_fase_g_para_o_que_ja_casava`) |
| `integer`, `boolean`, `enum` | como veio | decimal, `true`/`false`, o valor declarado |
| publicada adulterada empatada com outra | só a primeira era lida | recusa (`ContentTampered`) |

A pergunta na execução (`needs_input`, `runs.plan` nulo, evento estruturado) está em
[execution](execution.md#resolve-e-compile-skillrunplanner).

## `skills.enabled` × `ai.flows`

- `config.py::SkillsCfg.enabled`, padrão `false` (decisão P1), liga a resolução por skill publicada antes do fluxo.
- `ai.flows` (`config.py::AiCfg.flows`) continua mandando no fluxo legado, agora como `flows_enabled` do registro
  (antes, `taskqueue/service.py::_plan` o conferia direto). Não foi reaproveitado: já quer dizer "fluxo legado", e o
  valor de cada instalação não foi lido.
- Com os dois desligados, o registro não resolve nada. Publicar uma skill não liga nada sozinho.
- Adotar um fluxo com `skills.enabled` desligado é recusado (`SkillsDisabled`, [abaixo](#recusas-do-repositório)):
  o fluxo seria desligado e o comando ficaria sem resolução.
- `Health.features.skills` é o `skills.enabled` lido a cada `GET /api/health` (fase F). O painel só mostra o ensino v2
  e a lista de habilidades com ele ligado ([produto](../produto.md#3-fluxos-do-usuário)). As rotas `/api/skills`,
  `/api/teaching-sessions` e `/api/skill-candidates` respondem 404 `skills_disabled` com ele desligado.

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
- O rascunho v2 com a edição, gerado pelo descompilador `Plan → DSL`, veio na fase J
  ([abaixo](#conversão-de-fluxo-fase-j)).

## Conversão de fluxo (fase J)

A adoção deixa o fluxo como está (v1 = o plano congelado). A conversão acrescenta a v2 **editável**, no formato da
[DSL](../skill-dsl.md), sem perder a identidade de receita. Design §15.2;
[ADR-037](../decisoes.md#adr-037--compatibilidade-com-o-flow-legado). Código: `9d2b736`, `4ddba1a`, `fa21cec` e
`c4f40d6`, integrados em `5b1957f`.

**O descompilador** (`modules/skills/infrastructure/decompiler.py::PlanDecompiler`; o detalhe da ida e volta está no
[runtime](../skill-runtime.md#descompilador-plan--documento-fase-j)):

- etapa → nó com `id = PlanStep.key`, o que mantém a identidade de receita (`recipes.step_template_hash`);
- etapa com capability → nó `capability` com `with`; etapa livre → nó `goal` com a pós-condição inline;
- parâmetros do comando-modelo, sempre `string`; os fixos do plano viram padrão; `depends_on`, `timeout_s` e
  `retries` sempre explícitos;
- confere o documento pelo compilador **real** da execução (`SkillPlanCompiler`), duas vezes: sem valores e com
  valores de amostra, contra o plano do fluxo ligado aos mesmos valores;
- vocabulário próprio (`DecompileCode`), fora do vocabulário fechado do compilador: `E_ROUNDTRIP` (identidade ou
  comportamento diferente), `W_ROUNDTRIP` (texto que o catálogo reescreveu; a conversão segue), `E_RUNTIME_VARIABLE`
  (`{instance_id}`, `{run_id}`, `{account_label}` ou `{item_index}` num texto) e `E_UNREPRESENTABLE` (coleta
  livre, cópia de `for_each`, `commit_selector`/`band_guard` em etapa livre, parâmetro-modelo fora do comando,
  plano com `missing`, plano sem app, documento que já é da DSL);
- `suggested_skill_id(flow_id, app_id)`: o id padrão `<app>.<fluxo>`, estável e no formato de `SKILL_ID`.

**Converter** (`infrastructure/flow_conversion.py::FlowConverter.convert` → `SqlSkillRepository.convert_flow`), numa
transação:

1. `adopt_flow`: v1 `published` com o plano do fluxo (`schema_version` 0, `source_kind='legacy_flow'`) e o fluxo
   desligado ([acima](#adoção-de-fluxo));
2. o descompilador roda sobre a v1 **já gravada**;
3. `create_draft`: v2 `draft`, com `parent_version` = 1 e proveniência `legacy_flow` (`ref` = o fluxo, nota
   `decompiled_from` = a v1).

- Erro da ida e volta recusa tudo (`InvalidDocument`, 422 na rota) e nada fica: nem a v1, nem o fluxo desligado. Os
  avisos voltam com a conversão.
- Sem `skill_id`, vale a habilidade que já adotou o fluxo (readoção) ou o id sugerido.
- Com `skills.enabled` desligado, a adoção continua recusada (`SkillsDisabled`), e as rotas respondem 404
  `skills_disabled`.

**Desfazer** (`FlowConverter.undo` → `SqlSkillRepository.undo_conversion`), numa transação:

- `release_flow`: a publicada desabilitada e o fluxo religado exatamente como era (`flows` nunca foi reescrito);
- os rascunhos da conversão que ainda são `draft` são apagados, menos o que o ensino referencia (conferido **antes**:
  no PostgreSQL, a recusa da FK abortaria a transação);
- rascunho que já saiu de `draft` fica: é trabalho da pessoa. A definição também fica, e readotar usa a mesma.

**v1 → v2 de quem adotou antes da fase J** (`FlowConverter.draft_from`): o rascunho da DSL a partir de uma versão de
conteúdo legado já gravada, pela rota `POST /api/skills/{id}/versions/{n}/decompile`.

**Um comando, um dono** (critério da fase: nenhum fluxo ativo e habilidade publicada com o mesmo comando). Os caminhos
de escrita que ainda o permitiam foram fechados:

- `taskqueue/flows.py::FlowStore.learn_from_plan` (salvar o treino) recusa comando que uma habilidade publicada já
  tem; a rota do treino devolve o 409 `duplicate_command` de sempre;
- `api.py::update_flow` (`PUT /api/flows/{id}` religando) confere **qualquer** habilidade publicada com o comando
  (409 `command_published`, além do `flow_adopted` da fase G), com a conferência e a escrita numa transação;
- publicar, rollback, desfazer e aprender por execução já recusavam;
- a prova é por caminho de escrita, com a consulta de conflitos vazia depois de cada um
  (`backend/tests/test_conversao_de_fluxo.py::test_publicar_com_o_fluxo_ativo_do_mesmo_comando_e_recusado`,
  `::test_rollback_com_o_fluxo_religado_e_recusado`, `::test_desfazer_a_conversao_com_outra_publicada_e_recusado_sem_mexer`,
  `::test_aprender_fluxo_de_comando_publicado_nao_cria_fluxo` (execução e treino),
  `::test_religar_o_fluxo_pela_rota_com_outra_publicada_e_recusado`).

**Trilha da v1 adotada** (decisão da fase, `fa21cec`):

- a execução da versão cujo conteúdo **é** o plano do fluxo (a v1 adotada, `schema_version` 0) grava `runs.skill_id`,
  `skill_version` e `skill_hash` **e** `runs.flow_id`, e conta em `flows.used`
  (`run_planning.py::RunPlan.flow_id`; `taskqueue/service.py::RunService._registrar_resolucao`);
- a v2 da DSL, e o que vier dela, é outro plano: grava só a skill;
- leitura: `skill_id` e `flow_id` preenchidos = "o plano do fluxo, rodado pela habilidade"; `flow_id` sem `skill_id`
  continua querendo dizer `flow:<id>@1`;
- por quê: sem o `flow_id`, a conversão apagava da vista o histórico do fluxo (capacidades do perfil,
  aproveitamento, "usos" em `/api/flows`) justo quando ele passa a ser usado pela habilidade;
- `social/capacidades.py::capacidades_do_perfil` ganhou a lista `skills` (aditiva): o que o perfil concluiu por
  habilidade (`runs.skill_id`), com versão, vezes, `legacy_flow_id` e a cobertura de receitas do plano da última
  execução. A v1 adotada aparece nas duas listas, e `legacy_flow_id` diz que é a mesma coisa.

**A bateria legado × novo** (`backend/tests/test_equivalencia_fluxo_skill.py`, `simulated`: harness na porta 5640,
`FakeInstagram`, `AtorDoInstagram` no `CountingProvider`):

- `::test_mesmo_plano_mesmas_receitas_e_mesma_conta_de_ia[legado|novo]`: o fluxo aprendido como em produção e a v2
  descompilada e publicada dão o mesmo plano, a mesma identidade de receita (`step_key`/`step_hash`) e a mesma conta
  de IA (1ª execução: plan 0, decide 4, verify 1; 2ª: decide 0, por receita);
- `::test_receitas_aprendidas_pelo_fluxo_servem_a_habilidade_convertida`: sem IA na habilidade;
- `::test_a_v1_adotada_grava_a_skill_e_o_fluxo_e_as_capacidades_enxergam`.

**Riscos conhecidos.**

- **Fluxo com argumento literal e texto em modelo é recusado** (`E_ROUNDTRIP` com a causa): o planejador põe o valor
  no argumento (`username: "@ana"`), e `FlowStore.learn_from_run` troca o valor por `{nome}` nos textos, mas não nos
  `bindings` (`test_descompilador.py::test_argumento_literal_com_texto_em_modelo_e_recusado_com_a_causa`). Quantos
  fluxos de produção têm essa forma não foi medido: ler `flows.plan` antes de converter em lote.
- **Fluxos do QA Messenger não convertem:** o plano confere `{account_label}` na tela, e a v1alpha1 não tem forma para
  variável do runtime (`::test_variavel_do_runtime_no_texto_e_erro_explicito`). Continuam como fluxo.
- **Depois de desfazer, `DELETE /api/flows/{id}` continua 409 `flow_adopted`:** a definição fica com
  `legacy_flow_id`, e `api.py::delete_flow` pergunta por `adopter_id`.
- **Reconverter reusa o número do rascunho apagado:** `SqlSkillRepository._next_version` é `MAX(version) + 1`.
- **Concorrência no PostgreSQL:** em READ COMMITTED, duas escritas concorrentes (publicar e religar) ainda podem
  passar as duas conferências de comando único. No SQLite, o `BEGIN IMMEDIATE` as serializa. Não medido.

## Ensino v2 (fase F)

O ensino é a outra porta de entrada de versões: a candidata aceita vira `skill_versions` em **`draft`**, com
`source_kind='teaching'`, numa transação com a candidata e a sessão. O detalhe está em [`teaching.md`](../teaching.md).

- Quem escreve a versão é o mesmo `SqlSkillRepository.create_draft`, chamado por
  `modules/skills/application/teaching.py::TeachingService.publish` dentro da transação do ensino
  (`SqlTeachingRepository.atomic`).
- A proveniência leva `candidate_id`, `teaching_id`, `generated_by` e `reviewed_by`. O `content_hash` da versão é o
  da candidata, porque só o `document` do envelope vai para a versão.
- O ensino nunca publica: daqui em diante é a [tabela de transições](#estados-e-transições), por `POST
  /api/skills/{id}/versions/{n}/status`.
- Numa skill nova, um id que já existe é recusado: nunca vira, calado, a versão N+1 de outra skill. O ensino que
  melhora uma skill traz `skill_id`, e a versão nova leva `parent_version = base_version`.
- As recusas do repositório chegam à HTTP por `presentation/router.py::_http`
  ([contrato](../api-contract.md#adendo-v023-27092026--ensino-v2-e-habilidades-no-http)). O 404
  `skills_disabled` das rotas é `router.py::_exige_habilidades`, e não a exceção `SkillsDisabled`
  ([abaixo](#recusas-do-repositório)), que continua sendo só da adoção.

## Tabelas 042–046

| Migração | Tabelas ou colunas | Para quê |
|---|---|---|
| 042 | `skill_definitions`, `skill_versions`, `skill_version_transitions`, `skill_version_apps`, `skill_scope` | definição, versões, trilha de transições, apps exigidos (sem FK para `apps`, de propósito) e escopo |
| 043 | `skill_validation_cases`, `skill_validation_results` | casos e observações |
| 044 | `teaching_sessions`, `teaching_demonstrations`, `teaching_turns`, `teaching_candidates` | ensino v2 (fase F): `SqlTeachingRepository` ([ensino](../teaching.md)); `app_snapshot` ainda sempre nulo |
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

As recusas são exceções com `code` estável (`lifecycle.py::SkillError` e filhas). Desde a fase F, a API as expõe
por `presentation/router.py::_http`:

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

- `RunService._plan`, `RunService.apps_exigidos` e `POST /api/flows/match` resolvem pelo registro, pela mesma porta
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
  - `POST /api/flows/match` passa a respeitar `ai.flows`
    (`backend/tests/test_perfil_bloqueado_e_capacidades.py::test_estimativa_de_custo_por_fluxo`).
- **Aprendizado de fluxo:** `FlowStore.learn_from_run` não aprende de execução de skill nem de comando que uma skill
  publicada cobre (`::test_fluxo_nao_se_aprende_de_skill_nem_do_comando_que_uma_skill_publicada_cobre`).
- **P4 exercido de ponta a ponta:** na fatia, `ig.abrir_conversa` é validada pelo sistema com os dois casos do
  documento observados no `FakeInstagram` (`simulated`); `ig.ler_conversa`, pela via manual do dono, com motivo.

**O que ainda não está ligado:**

- Os casos de `spec.validation.cases` do documento são validados pelo compilador, mas não viram linhas da 043:
  `add_case` continua sendo uma chamada à parte (a fatia os insere à mão).
- O `uses_lock` que o compilador calcula não é gravado na `provenance` da versão.
- `spec.invocation.examples` continua sem consumidor. Usá-los como casos do `IntentResolver` é proposto.
- Etapas 3 e 4 da resolução com provedor real (classificador e desempate por IA): proposto, com aviso de custo e
  autorização ([acima](#resolução-de-intenção)).
- Do ensino v2: validação em aparelho, `PATCH` da candidata, `app_snapshot` e o evento `skill.published`
  ([ensino](../teaching.md#o-que-não-foi-feito)). O botão de transição no painel veio na fase J
  ([produto](../produto.md#3-fluxos-do-usuário)).
- A conversão em lote dos fluxos ativos de produção: não feita. A conversão é por fluxo, pela pessoa, e o formato real
  dos planos de produção não foi medido ([riscos](#conversão-de-fluxo-fase-j)).

**O que a fase J ligou:** o descompilador, converter e desfazer (`/api/flows/{id}/adopt` e `/release`), a v1 → v2
(`/api/skills/{id}/versions/{n}/decompile`), `legacy_flow_id` em `GET /api/skills`, o 409 `command_published`, a
trilha da v1 adotada em `runs.flow_id` e `flows.used`, a lista `skills` das capacidades do perfil, e o painel
([acima](#conversão-de-fluxo-fase-j);
[contrato](../api-contract.md#adendo-v025-27092026--conversão-de-fluxo-em-habilidade-e-provedor-de-sessão-por-app)).

**O que a fase F ligou:** as rotas `/api/skills` (lista, detalhe, versão, `status`, `scope`, `rollback`), o ensino v2
e `features.skills` ([acima](#ensino-v2-fase-f)). `GET /api/skills` lista só o SQL: as versões `flow:<id>@1` do
adaptador legado não aparecem.

`DELETE /api/flows/{id}` ganhou guarda depois da G (`f8021ae`): fluxo adotado, em qualquer estado da skill, responde
409 `flow_adopted` e não é apagado (`api.py::delete_flow`, conferência por `SqlSkillRepository.adopter_id`;
[contrato](../api-contract.md#adendo-v021-27092026--habilidades-no-caminho-dos-fluxos)).

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
| Normalização por tipo e link de perfil | implementado | `simulated` (`backend/tests/test_intencao_dominio.py::test_golden_da_normalizacao_por_tipo`, `::test_link_de_perfil_so_vira_usuario_no_app_que_tem_a_regra`, `::test_buraco_vazio_sempre_pergunta_mesmo_com_padrao`, `::test_conteudo_legado_passa_como_veio`) | `domain/intent.py`, `profile_links.py` |
| Cadeia modelos → tipos → semântica → LLM | implementado | `simulated` (`test_intencao_dominio.py::test_um_candidato_valido_resolve_pelo_modelo_com_o_valor_normalizado`, `::test_empate_vira_pergunta_e_as_etapas_por_ia_ficam_not_run`, `::test_entre_empatados_os_tipos_desempatam_quando_so_um_serve`, `::test_etapas_sao_plugaveis_so_modelos_sem_tipos`) | `intent_resolver.py` |
| Golden de frases pelo `SkillRunPlanner` | implementado | `simulated` (`backend/tests/test_intencao_resolucao.py::test_golden_de_frases`, `::test_tipos_chegam_ao_plano_e_o_padrao_fica_com_o_compilador`) | `run_planning.py` |
| Empate vira pergunta; escopo e adulteração no empate | implementado | `simulated` (`test_intencao_resolucao.py::test_duas_habilidades_empatadas_viram_pergunta_nunca_o_primeiro_id`, `::test_empate_que_os_tipos_desfazem`, `::test_escopo_vale_no_empate`, `::test_adulterada_entre_as_empatadas_e_recusa`) | `SqlSkillRepository.candidates` |
| Paridade do fluxo legado com `FlowStore.match` | implementado | `simulated` (`test_intencao_resolucao.py::test_paridade_com_o_flowstore_match`, `::test_empate_entre_fluxos_continua_decidido_pelo_uso`, `::test_interruptores_continuam_mandando`) | `LegacyFlowAdapter.candidates`, `CompositeSkillRegistry.candidates` |
| Etapas 3 e 4 com provedor real | porta com provedor nulo | `not_run` (sem provedor; a cadeia plugável só com dublês em `test_intencao_dominio.py::test_desempate_plugado_so_vale_se_escolher_um_dos_candidatos`) | `intent_ports.py` |
| Fase I em PostgreSQL | implementado | `not_run` | CI `workflow_dispatch` |
| Candidata do ensino → rascunho `draft` numa transação | implementado | `simulated` (`backend/tests/test_ensino_v2.py::test_laco_de_perguntas_e_respostas_ate_o_rascunho`, `::test_submissao_falha_no_meio_e_nao_deixa_lixo`, `::test_rotas_ligadas_do_ensino_ao_rascunho_e_ciclo_da_versao`); detalhe em [ensino](../teaching.md#capacidades--implementação-e-validação) | `TeachingService.publish`, `router.py` |
| Fase F em PostgreSQL e com IA real | implementado | `not_run` | CI `workflow_dispatch`; generalização paga sem autorização |
| Descompilador `Plan → DSL` com ida e volta pelo compilador real | implementado | `simulated` (`backend/tests/test_descompilador.py::test_o_documento_descompilado_compila_de_volta_no_mesmo_plano` em seis fluxos de formato de produção, `::test_variavel_do_runtime_no_texto_e_erro_explicito`, `::test_argumento_literal_com_texto_em_modelo_e_recusado_com_a_causa`, `::test_deriva_do_catalogo_no_texto_e_aviso_e_na_identidade_e_erro`, `::test_coleta_livre_efeito_sem_capability_e_parametro_fora_do_comando`) | `decompiler.py` |
| Converter e desfazer numa transação; v1 → v2 | implementado | `simulated` (`backend/tests/test_conversao_de_fluxo.py::test_converter_adota_e_cria_o_rascunho_descompilado_numa_transacao`, `::test_conversao_recusada_pela_ida_e_volta_nao_deixa_nada`, `::test_desfazer_devolve_o_fluxo_exatamente_como_era`, `::test_desfazer_mantem_o_rascunho_que_ja_saiu_de_draft`, `::test_v1_para_v2_de_quem_adotou_antes_do_descompilador`, `::test_rotas_de_conversao_atras_do_interruptor_e_com_o_tratamento_de_erro`) | `flow_conversion.py`, `SqlSkillRepository.convert_flow`, `undo_conversion` |
| Um comando, um dono (fluxo ativo × habilidade publicada) | implementado | `simulated` (as cinco recusas por caminho de escrita em `test_conversao_de_fluxo.py`); concorrência no PostgreSQL `not_run` | `FlowStore.learn_from_plan`, `api.py::update_flow` |
| Bateria legado × novo e trilha da v1 adotada | implementado | `simulated` (`backend/tests/test_equivalencia_fluxo_skill.py::test_mesmo_plano_mesmas_receitas_e_mesma_conta_de_ia[legado\|novo]`, `::test_receitas_aprendidas_pelo_fluxo_servem_a_habilidade_convertida`, `::test_a_v1_adotada_grava_a_skill_e_o_fluxo_e_as_capacidades_enxergam`) | `RunPlan.flow_id`, `RunService._registrar_resolucao`, `capacidades_do_perfil` |
| Processo cross-app (Instagram + QA) num aparelho | implementado | `simulated` (`backend/tests/test_app_novo_pelo_manifesto.py::test_processo_cross_app_instagram_e_qa_num_aparelho_so`) | fase K1; [runtime](../skill-runtime.md#processo-cross-app-fase-k1) |
| Fase J em PostgreSQL e com fluxos reais de produção | implementado | `not_run` | CI `workflow_dispatch`; conversão real exige autorização |
| Implantação (ensaio em cópia, ADR-020) | não feito | `not_run` | — |

Backlog (não implementar aqui):

- Rodar a suíte em PostgreSQL com a fase G (`workflow_dispatch`) antes de qualquer deploy.
- Casos do documento para a 043 e `uses_lock` na `provenance`, na publicação.
- Converter os fluxos ativos de produção: antes, ler `flows.plan` e contar os que caem nos riscos da fase J
  ([acima](#conversão-de-fluxo-fase-j)); cada conversão é decisão do dono.
