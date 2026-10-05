# Ensino v2: da instrução ao rascunho de habilidade

O ensino v2 transforma o que a pessoa diz e mostra numa **candidata** de habilidade versionada. O que só ela sabe
vira pergunta, e a candidata aceita vira um **rascunho** em `skill_versions`. É a fase F da evolução arquitetural
([design](design/evolucao-arquitetural.md) §13 e §15.3). O rascunho segue o ciclo de vida de
[skills](dominios/skills.md#estados-e-transições), o documento é o da [DSL](skill-dsl.md), e quem o confere é o
[compilador](skill-runtime.md). O treino v1 (gravador, `training_sessions`, fluxos) continua como estava
([perfis e Instagram](dominios/perfis-e-instagram.md)).

Caminhos relativos a `backend/app/`, salvo indicação; `teaching.py` sem pasta é `modules/skills/domain/teaching.py`,
e `generalization.py`, `modules/skills/domain/generalization.py`. Código: `474aceb` (backend) e `63507ad` (painel), integrados
em `6b04164` no branch `claude/arquitetura-habilidades`, mais `578fe36` (o 404 de `/api/skills/resolve`). Ainda não
está na `main`.

**Estado (27/09).**

- Domínio, serviço, repositório SQL, rotas e painel prontos, atrás de `skills.enabled` (padrão `false`).
- Prova `simulated`: `backend/tests/test_ensino_v2.py`, 16 testes verdes neste worktree em `578fe36`, com o provedor
  simulado por baixo do `CountingProvider` e o harness na porta 5640.
- Vitest 480/480, typecheck e build: commit `63507ad` (`simulated`). Suíte SQLite 2269/2269 com as fases A–I:
  relatada na integração, não medida neste worktree (`simulated`).
- `not_run`: generalização com IA real (chamada paga, sem autorização), PostgreSQL, conferência visual do painel e
  qualquer aparelho real. Nada implantado.

## O caminho, em uma tela

```text
start ─► open ─(gravação ainda gravando)─► demonstrating ─(a gravação parou; lido na próxima chamada)─► open
open ─propose─► proposing ─► asking       (a candidata tem perguntas)
                          ├► validating   (compilou, sem pergunta)
                          └► open         (não compilou, tinha segredo, ou o generalizador falhou)
asking ─(todas respondidas) propose─► proposing
validating ─validate─► ready | open
ready ─publish─► published   (virou rascunho em skill_versions; terminal)
qualquer estado não terminal ─discard─► discarded   (terminal)
```

Quem conduz é `modules/skills/application/teaching.py::TeachingService`, sobre portas (`application/ports.py`): sem
banco, sem HTTP e sem IA no serviço. O repositório é `infrastructure/sql_teaching_repository.py::SqlTeachingRepository`,
sobre as tabelas da 044 ([banco](banco.md)).

## `TeachingSession` v2

`modules/skills/domain/teaching.py::TeachingSession`, tabela `teaching_sessions` (044):

- `instruction`: o texto da pessoa, até 2000 caracteres (`application/teaching.py::MAX_TEXT`). Pode ser vazio; sem
  instrução e sem gravação, `propose` recusa com `nothing_to_teach`.
- `skill_id` e `base_version`: nulos numa habilidade nova. Com `skill_id`, o ensino melhora a habilidade existente, e
  `base_version` sem `skill_id` é recusado (`invalid_input`).
- `app_id`: conferido em `apps` (`unknown_app`). Se vier nulo, `TeachingService._app_of` o tira da gravação (o
  `app_id` dela, o `open_app` gravado ou o pacote). Sem app, `propose` recusa com `app_required`.
- `profile_id`, `operator`, `status`, `validation_status` (`none`, `running`, `passed`, `failed`, `partial`),
  `result_version_id` e `closed_at`.

### Fontes

A fonte **não é coluna**: `teaching.py::source_of` a deriva do conteúdo a cada leitura, porque uma sessão muda de
fonte ao ganhar uma demonstração ou uma correção. A primeira regra que casa vence:

| Fonte | Como se reconhece |
|---|---|
| `correction` | a sessão tem `skill_id` e um turno `correction` |
| `successful_execution` | há demonstração `kind='run'` e nenhuma gravação |
| `hybrid` | há gravação e a instrução não é vazia (é o que o treino v1 faz) |
| `demonstration` | há gravação e a instrução é vazia |
| `instruction` | nenhuma das anteriores |

Prova: `backend/tests/test_ensino_v2.py::test_fonte_e_derivada_do_conteudo` (`simulated`).

### Estados e transições

A tabela é `teaching.py::TRANSITIONS`, fechada: o que não está nela é recusado (`check_move`, 409 `teaching_state`).
Os terminais são `published` e `discarded` (`TERMINAL`).

| De → para | Quem move |
|---|---|
| `open` → `demonstrating` | `attach_recording`, quando a gravação ligada ainda está `recording` |
| `demonstrating` → `open` | `TeachingService._settle`, na próxima chamada, quando nenhuma gravação ligada grava mais. O gravador não avisa o ensino |
| `open` → `proposing` | `propose` |
| `proposing` → `asking` | `_record_candidate`: candidata compilou e tem perguntas |
| `proposing` → `validating` | `_record_candidate`: compilou e não tem pergunta |
| `proposing` → `open` | candidata que não compila, candidata com segredo, ou falha do generalizador |
| `asking` → `proposing` | `propose`, com todas as perguntas da candidata atual respondidas |
| `validating` → `ready` ou `open` | `validate`, sem erro ou com erro |
| `ready` → `published` | `publish` |
| qualquer não terminal → `discarded` | `discard` |

- **Toda transição é CAS.** `SqlTeachingRepository.move` faz `UPDATE … WHERE id=? AND status=?` e levanta
  `TeachingStateConflict` se a linha já mudou. Dois cliques em "propor" não geram duas chamadas pagas: o segundo
  perde o CAS.
- `propose` numa sessão `demonstrating` recusa com `still_recording`. Numa `asking` com pergunta aberta, recusa com
  `questions_pending`.
- A sessão nunca fica presa em `proposing`: `propose` volta a `open` em qualquer exceção (`except BaseException`).
- De `validating` e de `ready` não se pede outra candidata. O caminho é validar (a reprovada volta a `open`) ou
  descartar.

Prova: `test_ensino_v2.py::test_tabela_de_estados_do_ensino`,
`::test_gravacao_ainda_gravando_deixa_o_ensino_demonstrando` (`simulated`).

### Demonstrações e correções

`teaching.py::Demonstration`, tabela `teaching_demonstrations`. Só entram em `open` ou `demonstrating`
(`ACCEPTS_DEMONSTRATION`): depois da candidata, uma demonstração nova mudaria o que ela generalizou.

- **Gravação v1** (`kind='recording'`, `attach_recording`): a demonstração **aponta** para a `training_sessions`.
  - Uma gravação pertence a no máximo um ensino (índice `ux_teaching_demo_gravacao`): a segunda ligação recusa com
    409 `recording_taken`.
  - Gravação descartada recusa com `recording_discarded`; inexistente, 404.
- **Execução comprovada** (`kind='run'`, `attach_run`): só `runs.status='completed'` (`run_not_completed`). Exemplo de
  quem falhou ensinaria o erro. O comando dela vai ao prompt da IA real como exemplo (no simulado, não vai).
- **Correção** (`add_correction`): turno `correction` com `target: {run_id, step_id}`.
  - Só numa sessão com `skill_id` (`correction_needs_skill`).
  - Só de etapa daquela execução em `failed` ou `uncertain` (`CORRECTABLE_STEP`; senão `step_not_correctable`).
- `app_snapshot` existe na 044, mas é sempre gravado nulo.

Prova: `test_ensino_v2.py::test_demonstracao_so_liga_gravacao_valida_e_uma_vez` (`simulated`).

### A conversa (turnos)

`teaching.py::TeachingTurn`, tabela `teaching_turns`. O `id` inteiro dá a ordem.

| `kind` | `author` | Quem escreve | O que leva |
|---|---|---|---|
| `instruction` | `person` | `start`, se a instrução não é vazia | o texto |
| `question` | `ai` ou `compiler` | `_record_candidate` | o texto, `target`, `candidate_id` e `payload: {key, kind}` |
| `answer` | `person` | `answer` | o texto, `reply_to` (a pergunta) e `candidate_id` |
| `correction` | `person` | `add_correction` | o texto, `target: {run_id, step_id}` e o `payload` |
| `note` | `compiler` | `_compiler_note` | "A candidata não compila:" e `payload: {errors}` |
| `note` | `system` | `_record_candidate` | a recusa `secret_in_candidate` com `payload: {paths}`, ou "candidata pedida" com `payload: {op: "propose", idempotency_key}` |

`author='ai'` quer dizer "do generalizador", inclusive nas perguntas que a regra pura acrescenta (abaixo).

## `SkillCandidate`

`teaching.py::SkillCandidate`, tabela `teaching_candidates`. `content` é o envelope
`teaching.py::CandidateEnvelope`: `{document, annotations}`.

- **`document`** é um `SkillDocument` `automation/v1alpha1`. É **só ele** que vai para `skill_versions.content`.
- **`content_hash`** é o hash do documento (`CandidateEnvelope.document_hash`), o mesmo `content_hash` que a versão terá.
  O envelope não entra no hash.
- **`annotations`** (`teaching.py::CandidateAnnotations`) acompanha o documento sem fazer parte dele: não é validado e
  não entra no hash.

| Campo de `annotations` | O que guarda |
|---|---|
| `evidence` | nó → seqs das entradas gravadas que o sustentam |
| `discarded` | `[{seq, why}]`: entradas que a proposta descartou |
| `assumptions` | o app principal, "candidata do modo SIMULADO" quando for o caso, cada pergunta respondida e cada correção |
| `parameters` | `[{name, type, examples, description, required}]` (`InferredParameter`) |
| `preconditions` | app instalado; conta conectada, quando o app tem catálogo |
| `postconditions` | `[{node, kind, value}]` por nó; `kind='catalog'` quando o nó é uma capability |
| `suggested_proofs` | um caso `simulated` com os exemplos da demonstração e um caso `device` por efeito externo (P4) |
| `effects` | `[{node, capability, description}]` dos nós com efeito externo |
| `risks` | nó conferido por `model_judged`, nó sem entrada gravada, texto sigiloso digitado |

- `generated_by`: `ai:<modelo>` (o modelo que a `Usage` informa, ou o do provedor).
- `status`: `proposed`, `rejected`, `accepted` ou `superseded`. Pedir uma candidata nova marca a anterior `proposed`
  como `superseded` (`SqlTeachingRepository.supersede_candidates`). A rejeitada não gasta número de versão.

### Como o documento é montado

`domain/generalization.py::envelope_from_proposal` recebe a proposta do provedor como **dado não confiável** e a
confere campo a campo. Nada ali é avaliado como código (decisão 4 do design).

- **Comando:** o `command_template` da proposta, ou a instrução.
- **Parâmetros:** só os que o comando usa (`{nome}`). O tipo sai do exemplo, por `generalization.py::infer_type`:
  - `integer` para até 9 dígitos;
  - `url` para `http(s)://`;
  - `handle` para `@nome` ou `nome.sobrenome`;
  - `text` quando há espaço;
  - `string` no resto, na dúvida.
- **Nós:** um por etapa, com id normalizado (`_node_id`) e `depends_on` sempre emitido, em cadeia.
  - Etapa com capability vira `capability` com `with`; `{p}` vira `${parameters.p}`.
  - Etapa sem capability vira `goal` com `verification.postcondition`. Pós-condição fora de `text_visible`,
    `app_foreground` e `element_present` vira `model_judged` com o objetivo, e o nó entra em `risks`.
- **Só instrução** (nenhuma etapa): um nó `executar`, conduzido pela IA e conferido por `model_judged`, mais a pergunta
  `etapas:instrucao`. Se o app tem catálogo, também a pergunta `efeito:instrucao`. Não se inventa passo nem prova de
  tela.
- `metadata.id` é o `skill_id` do ensino, ou `teaching.py::suggest_skill_id(app, instrução)`: `<app>.<primeiras
  palavras>`, determinístico. O id é só sugestão; um id que já existe é recusado na submissão.

Prova: `test_ensino_v2.py::test_candidata_do_provedor_simulado_a_partir_da_gravacao`,
`::test_so_instrucao_pergunta_em_vez_de_inventar`, `::test_id_sugerido_e_valido_e_deterministico` (`simulated`).

## Perguntas em vez de inventar

Cada pergunta tem uma **chave estável** (`ProposedQuestion.key`, gravada em `payload.key`). A mesma dúvida numa
geração seguinte tem a mesma chave, e a que já foi respondida não volta (`teaching.py::answered_questions`).

| Chave | `kind` | Origem | Quando |
|---|---|---|---|
| `efeito:<nó>` | `effect_confirmation` | `ai` | etapa com `side_effect` |
| `etapas:instrucao` | `scope` | `ai` | nenhuma etapa: só instrução |
| `efeito:instrucao` | `policy` | `ai` | nenhuma etapa, e o app tem catálogo de capabilities |
| `sigilo:<seq>` | `missing_parameter` | `ai` | entrada gravada com texto sigiloso que o gravador não guardou (`RecordedInput.secret_text`) |
| `ai:<sha1 de 12>` | `ambiguity` | `ai` | cada dúvida que a proposta traz em `questions`, até 8 |
| `catalogo:<nó>` | `policy` | `compiler` | erro `E_CAPABILITY_REQUIRED` |
| `comando:ambiguo` | `ambiguity` | `compiler` | erro `E_COMMAND_AMBIGUOUS` |

- Só esses dois erros do compilador viram pergunta (`generalization.py::PERSON_RESOLVABLE`). Os outros rejeitam a
  candidata (`generalization.py::compiler_questions`).
- Um erro "resolvível" cuja pergunta já foi respondida volta a ser erro e rejeita a candidata: a resposta não bastou,
  e perguntar de novo seria um laço.

## O laço de perguntas e respostas

1. `propose` gera a candidata. Com pergunta, a sessão fica `asking`.
2. `answer(teaching_id, question_id, body)` grava uma resposta por pergunta. Recusa quando:
   - a sessão não está `asking`;
   - a pergunta é de uma candidata antiga;
   - a pergunta já foi respondida.
   A sessão continua `asking` até o próximo pedido.
3. `propose` de novo, com as respostas. Com pergunta aberta, recusa com `questions_pending`.
4. A candidata nova não refaz as perguntas respondidas. Cada resposta vira uma suposição anotada ("Pergunta “…”
   respondida: “…”").

- **A resposta não reescreve o documento por regra.** Quem muda o documento é o generalizador.
  - Na IA real, as respostas, as correções, os exemplos e a versão de base vão ao prompt
    (`training/generalizer.py::_intencao_com_contexto`).
  - No simulado, só a instrução vai, para o comando não mudar a cada geração. A resposta aparece só nas suposições.
- Candidata que não compila fica `rejected`, com a nota do compilador, e a sessão volta a `open`. Gerar de novo é
  pedido explícito da pessoa, porque custa.

Prova: `test_ensino_v2.py::test_laco_de_perguntas_e_respostas_ate_o_rascunho` (`simulated`).

## Credencial nunca entra em habilidade

O texto do ensino vai ao provedor de IA e fica na conversa. A credencial da pessoa entra só pelo campo `credentials`
da execução, pelo nome (ADR-025), nunca na habilidade. A regra mora no serviço, não na borda: `TeachingService._person_text`
para o texto da pessoa e `TeachingService._secret_paths` para a candidata. As duas usam
`infrastructure/secret_screen.py::RedactionSecretScreen`.

**Duas réguas:**

- **Formato** (`value_is_secret` → `security/redaction.py::looks_secret`): par `senha: …`, token, JWT, blob longo.
- **Palavra** (`text_has_credential`): o formato, mais cada palavra com cara de senha
  (`security/redaction.py::parece_senha_ou_codigo`):
  - palavra única de 8 ou mais caracteres que mistura três tipos (minúscula, maiúscula, dígito, símbolo fora de
    `._-@`);
  - ou só dígitos, de 6 a 8.

  Endereços `http://` e `https://` ficam isentos. É mais rígida de propósito: recusar uma frase inofensiva custa pouco.

| Onde | Régua | O que acontece |
|---|---|---|
| instrução (`start`) | palavra | 400 `credential_in_text`; nada gravado |
| nota da demonstração | palavra | idem |
| resposta (`answer`) | palavra | idem; a pergunta continua aberta |
| texto da correção | palavra | idem |
| `payload` da correção | formato (`_screen_json`) | idem |
| intenção da gravação v1, quando a instrução é vazia | palavra (`_request`) | 400 no `propose`, antes de mover a sessão e de chamar o provedor |
| documento e anotações da candidata | formato em toda folha; palavra também nas folhas de dado (`example`, `default`, `examples[i]`, `with.<x>`, `parameters.<x>`, por `_FOLHA_DE_DADO`) | candidata `rejected` e `validation_status='failed'`, gravada com `**REDACTED**` no lugar do valor; nota `secret_in_candidate` com os caminhos; a sessão volta a `open` |
| texto sigiloso digitado na gravação | o gravador v1 já não o grava (`training/recorder.py`) | vira pergunta `sigilo:<seq>`, nunca parâmetro |

- A régua de formato vale sozinha no resto do documento: senão `automation/v1alpha1` (minúscula, dígito e barra)
  seria "senha".
- A candidata rejeitada fica registrada para a pessoa ver que foi recusada e por quê. O hash é o do documento
  mascarado.

Prova: `test_ensino_v2.py::test_credencial_recusada_na_instrucao_e_na_resposta`,
`::test_candidata_com_valor_de_segredo_e_rejeitada_e_gravada_mascarada` (duas variantes: senha e JWT); nos dois, o
despejo das tabelas do ensino e das habilidades não contém o segredo (`simulated`).

## Da candidata ao rascunho

**Validar** (`TeachingService.validate`):

- Só `mode="static"` (senão `validation_mode`). A validação em aparelho não existe.
- Exige a sessão `validating` e a candidata `proposed`. Repetir depois de passar devolve a mesma visão.
- Quem confere é `infrastructure/document_validator.py::DslDocumentValidator.inspect`, com o mesmo compilador da
  execução.
  - **Com erro:** candidata `rejected`/`failed`, nota do compilador, sessão `open`.
  - **Sem erro:** candidata continua `proposed` com `passed`, e a sessão vai a `ready`.
  - Os dois casos numa transação só.

**Publicar** (`TeachingService.publish`), que aqui quer dizer **virar rascunho**:

- Candidata já `accepted` devolve a mesma visão, com a mesma versão (idempotente pela candidata).
- Exige a sessão `ready` e a candidata `proposed` com `passed`. Senão, 409.
- O id que o documento declara (`DocumentFacts.skill_id`) precisa ser:
  - o `skill_id` do ensino, quando ele melhora uma habilidade existente (senão `invalid_input`);
  - um id que ainda não existe, numa habilidade nova (senão 409 `teaching_state`, "ensine como melhoria dela"). Nunca
    vira, calado, a versão N+1 de outra habilidade.
- **Numa transação** (`SqlTeachingRepository.atomic` é o `Database.tx()`, reentrante):
  1. `SqlSkillRepository.create_draft(skill_id, document, source=…, parent_version=base_version)`;
  2. a candidata `proposed → accepted`, com `version_id`;
  3. a sessão `ready → published`, com `result_version_id`.

  Se qualquer um falha, nada entra: nem definição, nem versão, nem transição.
- A proveniência é `Provenance(kind=teaching, ref=<ensino>, candidate_id, teaching_id, generated_by,
  reviewed_by=<quem>)`.

**O ensino nunca publica a habilidade.** A versão nasce `draft`. Publicá-la é o ciclo de vida
([skills](dominios/skills.md#estados-e-transições)), por `POST /api/skills/{id}/versions/{n}/status`: `draft →
candidate` exige compilar, e `validated` exige a prova da P4 ou a via manual.

Prova: `test_ensino_v2.py::test_laco_de_perguntas_e_respostas_ate_o_rascunho` (versão em `draft`, nada publicado,
`content_hash` igual ao da candidata), `::test_submissao_falha_no_meio_e_nao_deixa_lixo`,
`::test_id_existente_nao_vira_versao_de_outra_habilidade` (`simulated`).

## Relação com o treino v1

- **O gravador não muda.** `SqlTeachingRepository.recording` só **lê** `training_sessions` e `training_inputs`; nada
  no ensino escreve nelas. A gravação continua `recorded` depois de gerar a candidata
  (`test_ensino_v2.py::test_candidata_do_provedor_simulado_a_partir_da_gravacao`).
- **As rotas `/api/training*` ficam como estavam** e fora do interruptor. Com `skills.enabled` desligado, `GET
  /api/training` responde 200 (`::test_rotas_desligadas_respondem_404_explicito_e_o_treino_segue`).
- **`TrainingSkills.save` continua produzindo fluxos.** O "Salvar como fluxo" de sempre (até a fase L, "Salvar habilidade") fica ao lado do painel do
  ensino v2, sem ponte: uma ponte seria escrita dupla (decisão 1 do design).
- **Uma sessão v1 não aparece como v2 na leitura.** `GET /api/teaching-sessions?training_session_id=` devolve o ensino
  que usa aquela gravação, ou `[]`; nenhuma visão sintética. No painel, o ensino nasce quando a pessoa pede a
  candidata.
- O generalizador é a mesma função do treino v1: `generalize` do provedor, com o `TrainingRequest` de
  `planning/training.py` (abaixo).

## A gravação do treino v1: reinício e segredo

- **Reinício do backend no meio de uma gravação** (31.80): ao subir, toda sessão `recording` vira `recorded` (as entradas
  já gravadas valem) e sai um evento `log` por sessão dizendo que o reinício a encerrou. Quem ensinava precisa abrir
  outra gravação e continuar dali; o gravador **não religa sozinho** (gravar sem a pessoa saber é pior que encerrar).
- Um `start` que encontra uma gravação "viva" só no banco (o aparelho não a está gravando) encerra a antiga como
  `recorded` e aceita a nova. `already_recording` continua só para a gravação que o aparelho realmente grava.
- **O que a gravação não guarda** (31.82): texto digitado quando a árvore da tela não veio (aparelho lento: sem ver a tela
  não se sabe se o campo era de senha; fica `has_text` e o tamanho); o conteúdo de um campo editável tocado (o alvo
  gravado leva `resource_id`, rótulo e classe, não o que estava escrito; a receita segue por `resource_id`); `text`/`desc`
  do alvo, linhas e título de tela que falem de código ou senha ("Seu código é 123456"). Um campo editável nunca guarda `text` no alvo, mesmo sem `resource_id` nem rótulo: sem identificador o alvo fica sem seletor e a etapa não vira receita (a IA conduz).
  Também: o texto digitado só é guardado com um campo editável, que não é de senha, em foco na árvore (sem foco, senha
  revelada e WebView ficam só com `has_text` e o tamanho); filho do alvo cuja classe é de campo de texto (`EditText`,
  `AutoCompleteTextView` e variantes) perde o `text`; código de 4 a 8 dígitos com espaço ou hífen ("123 456", "8845-12")
  sai do alvo, do título e das linhas; em tela sensível o alvo (e os filhos) guarda só `resource_id`, `class_name` e o
  estrutural, sem `text` nem `desc`.
- **Limite conhecido do filtro** (não se inventou regra nova): ainda escapam código de 4 a 5 dígitos sozinho no meio de
  uma frase e palavra-chave longe do número ("código" numa linha, o número na outra); token com hífen, e-mail e senha
  na mesma linha também passam.

## O generalizador e o custo

- **A porta** é `application/ports.py::SkillGeneralizer`: `async generalize(GeneralizationRequest) -> Generalization`.
- **O adaptador** é `training/generalizer.py::ProviderSkillGeneralizer`, composto em `state.py` com o provedor da
  instalação.
  - Mora em `app/training`, e não em `app/modules/skills`, por causa da D15: o contexto de habilidades não importa
    módulo de IA (`backend/tests/test_compilador_de_skills.py::test_compilador_nao_avalia_nem_carrega_codigo_por_ast`).
  - O `AIProvider` legado não declara `generalize`. O adaptador declara o que usa em `GeneralizingProvider`.
- **O pedido** leva:
  - os apps cadastrados;
  - o catálogo de capabilities do app principal (`planning/capabilities.py::load_catalog`);
  - as entradas no formato de `training_inputs`;
  - a intenção.
- **No simulado** (`SimulatedProvider`), a proposta sai de `planning/training.py::proposta_simulada`: regras fixas,
  determinística e de graça. Os testes contam as chamadas pelo `CountingProvider` (`harness.ai.count("generalize")`).
- **Na IA real**, `planning/routing.py::RoutingProvider.generalize` faz **uma chamada paga** por `propose`, no papel
  `plan` (mesmo modelo e orçamento do planejador).
  - O uso entra em `ai_calls` por `Repository.add_usage(None, None, usage)`: sem execução, conta no relatório de custo
    por função.
  - O painel não estima o custo antes; o texto ao lado do botão avisa "Uma chamada do modelo do planejador".
  - **`not_run`:** nenhum teste faz essa chamada, e ela exige autorização do dono.
- **Falha:** `AIError` ou proposta que não é JSON viram `GeneralizerFailed` (502 `generalizer_error`), e a sessão volta
  a `open`.
- **Idempotência do pedido:** `idempotency_key` (8 a 120 caracteres).
  - A 044 não tem coluna para ela. A chave vai num turno `note` `{op: "propose", idempotency_key}`, gravado na mesma
    transação da candidata.
  - A mesma chave devolve a visão sem chamar o generalizador (`::test_idempotencia_do_pedido_de_candidata`).
  - Se o generalizador falhou, não há turno, e a mesma chave chama de novo: na IA real, é outra chamada paga.

## HTTP, eventos e painel

- **Rotas:** `modules/skills/presentation/router.py`, registrado em `main.py::create_app` depois do router principal,
  para que `POST /api/skills/resolve` (fase I, em `api.py`) case antes de `/api/skills/{skill_id}`. Contrato no
  [adendo v0.23](api-contract.md#adendo-v023-27092026--ensino-v2-e-habilidades-no-http). Com `skills.enabled`
  desligado, toda rota daqui responde 404 `skills_disabled` (`router.py::_exige_habilidades`).
- **Evento:** `teaching.updated`, com `data: {teaching_id}`, a cada mudança (`TeachingService._notify`, ligado ao
  `EventBus` em `state.py`). É gravado no banco. Nenhuma tela o escuta ainda.
- **Painel:** a revisão do treino e a lista de habilidades, só com `health.features.skills`
  ([produto](produto.md#3-fluxos-do-usuário)).

## O que não foi feito

Desvios do design (§13 e §15.3), todos conscientes:

- **Sem `PATCH /api/skill-candidates/{id}`.** A pessoa não edita o documento. Ele muda por resposta, correção e nova
  geração.
- **`validate` só `static`.** Não há `device`: pela HTTP, `mode` diferente de `static` é 422
  (`ValidateBody.mode: Literal["static"]`), antes do serviço. `suggested_proofs` não viram casos da 043.
- **A sessão v1 não é lida como v2** (§13.3 previa `hybrid` com uma demonstração, sem linha nova).
- **`app_snapshot` sempre nulo.** A versão do app no momento da gravação não é lida.
- **Sem o evento `skill.published`.** Só `teaching.updated`, e sem `data.training_session_id`, que a `TrainingBar`
  usaria para recarregar.
- **`idempotency_key`:**
  - no `propose`, vai num turno `note`, porque a 044 não tem coluna;
  - em `validate` e `publish`, é aceita e não usada. As duas são idempotentes pelo estado: validar de novo em `ready`
    e publicar uma candidata `accepted` devolvem o mesmo resultado.
- **`published` no ensino quer dizer "virou rascunho".** Nada é publicado sozinho.
- **Só instrução gera um nó só**, `executar`, com `model_judged`, e pergunta `etapas:instrucao` em vez de inventar
  etapas.
- **A recusa de credencial é mais rígida que "a mesma regra de `service.py`"** do §13.1: vale a régua da palavra
  (inclusive número de 6 a 8 dígitos), com URLs isentas.
- **`GET /api/skills` lista só o SQL** (`SqlSkillRepository.list`). As versões `flow:<id>@1` do adaptador legado não
  aparecem.
- **As anotações não seguem o nome do design.** O design fala em `parameter_examples`. O código tem `parameters`, com
  tipo e exemplos, e acrescenta `preconditions`, `postconditions`, `suggested_proofs`, `effects` e `risks`.
- **Ficaram fora:**
  - a destilação de receitas na publicação e a função única de identidade de etapa (§13.3);
  - `capacidades.trained` incluir as skills (§15.3);
  - a correção e a execução como exemplo no painel, que existem só na API.
- **Divergência no código:** a docstring de `domain/teaching.py` cita `infrastructure/teaching_rows.py`, que não
  existe. A conversão de linha é feita em `sql_teaching_repository.py` (`_sessao`, `_turno`, `_candidata`,
  `_entrada`), sobre `infrastructure/rows.py`.

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| Fonte derivada do conteúdo | implementado | `simulated` (`backend/tests/test_ensino_v2.py::test_fonte_e_derivada_do_conteudo`) | `teaching.py::source_of` |
| Tabela de estados e CAS | implementado | `simulated` (`test_ensino_v2.py::test_tabela_de_estados_do_ensino`, `::test_gravacao_ainda_gravando_deixa_o_ensino_demonstrando`) | `teaching.py::TRANSITIONS`, `SqlTeachingRepository.move` |
| Candidata `{document, annotations}` do provedor simulado | implementado | `simulated` (`test_ensino_v2.py::test_candidata_do_provedor_simulado_a_partir_da_gravacao`) | `generalization.py::envelope_from_proposal`, `training/generalizer.py` |
| Perguntas com chave estável e laço até o rascunho | implementado | `simulated` (`test_ensino_v2.py::test_laco_de_perguntas_e_respostas_ate_o_rascunho`, `::test_so_instrucao_pergunta_em_vez_de_inventar`) | `TeachingService.propose`, `.answer` |
| Idempotência do pedido de candidata | implementado | `simulated` (`test_ensino_v2.py::test_idempotencia_do_pedido_de_candidata`) | `TeachingService._replayed` |
| Credencial recusada no texto e na candidata | implementado | `simulated` (`test_ensino_v2.py::test_credencial_recusada_na_instrucao_e_na_resposta`, `::test_candidata_com_valor_de_segredo_e_rejeitada_e_gravada_mascarada`) | `RedactionSecretScreen`, `TeachingService._secret_paths` |
| Candidata → rascunho numa transação | implementado | `simulated` (`test_ensino_v2.py::test_submissao_falha_no_meio_e_nao_deixa_lixo`, `::test_id_existente_nao_vira_versao_de_outra_habilidade`) | `TeachingService.publish` |
| Demonstração: uma gravação, um ensino | implementado | `simulated` (`test_ensino_v2.py::test_demonstracao_so_liga_gravacao_valida_e_uma_vez`) | `ux_teaching_demo_gravacao` |
| Rotas atrás de `skills.enabled`, `/api/training*` intactas | implementado | `simulated` (`test_ensino_v2.py::test_rotas_desligadas_respondem_404_explicito_e_o_treino_segue`, `::test_rotas_ligadas_do_ensino_ao_rascunho_e_ciclo_da_versao`) | `router.py` |
| Painel atrás de `features.skills` | implementado | `simulated` (`frontend/src/features/training/TrainingReview.test.tsx`, `frontend/src/features/settings/FlowsRecipesSection.test.tsx`: desligado e ligado) | `TeachingPanel.tsx`, `FlowsRecipesSection.tsx` |
| Generalização com IA real | implementado (o caminho existe) | `not_run`: chamada paga, sem autorização | `RoutingProvider.generalize` |
| Ensino em PostgreSQL | implementado | `not_run` | `SqlTeachingRepository` |
| Conferência visual do painel | implementado | `not_run` | `TeachingPanel.tsx` |
| Ensino com aparelho real | implementado | `not_run` | gravação v1 num aparelho do parque |
