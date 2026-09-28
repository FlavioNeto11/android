# Domínio: persona (a pessoa)

A persona é a **pessoa** por trás das contas: identidade, voz, biografia, identidade visual, proveniência e a galeria de
imagens. Desde a migração 047 ela é a linha de `instagram_profiles` (evolução 2, onda A;
[design](../design/persona-e-parque.md) §3, §5, §6 e §10;
[ADR-041](../decisoes.md#adr-041--a-persona-é-a-pessoa-instagram_profiles-como-raiz-personas-dobrada-username-opcional-por-string-vazia-e-reconstrução-com-foreign_keys-off)
e
[ADR-042](../decisoes.md#adr-042--imagens-de-persona-receita-determinística-porta-imagegenerator-simulado-primeiro-openai-atrás-de-chave-custo-em-ai_callsusd)).
Contas, sessão, credencial e o provedor de sessão continuam em
[perfis e Instagram](perfis-e-instagram.md); o contrato HTTP está no
[adendo v0.27](../api-contract.md#adendo-v027-27092026--a-persona-é-a-pessoa-geração-por-ia-e-imagens); o banco, em
[`banco.md`](../banco.md).

Caminhos relativos a `backend/app/`, salvo indicação. Código da onda A: `462d724` (047), `469baae` (modelo e rotas),
`6dcbdc5` (geração por IA), `e68c506` (048 e imagens), integrados em `8c19d5a`.

**Estado (27/09).**

- Migrações 047 e 048, modelo, rotas, geração e imagens: suíte SQLite 2459/2459 no branch (`simulated`).
- Ensaio `real` da 047+048 numa **cópia** do backup de produção `data/backups/20260927-222357` (27/09): ver
  [migração de dados](#migração-de-dados-047). PostgreSQL: `not_run` (o GitHub Actions ficou bloqueado por cobrança em 27/09; K-040).
- IA real (geração/enriquecimento), OpenAI Images real e produção: `not_run`. Nada implantado.

## Um objeto, quatro nomes

| Nome | Onde | O que é |
|---|---|---|
| linha de `instagram_profiles` | `backend/migrations/047_persona_e_a_pessoa.sql` | a pessoa; `username = ''` quando ainda não tem conta de cadastro |
| `PersonaVoiceDTO` | `models.py` | o que o construtor de contexto vê: `id`, `name`, `summary`, `persona_prompt`, `traits`, `biography`, `age` (calculada), `gender`, `locale`, `profile_id`, `profile_username`, `voice_gaps` (computado) |
| `PersonaDTO` | `models.py`, herda de `PersonaVoiceDTO` | a pessoa inteira: mais `visual`, `generation`, identidade (`username`, `display_name`, `first_name`, `last_name`, `birth_date`, `email`), `status`, política e grupo, `instance_id`, `locality`, `credential`, `session`, `app_on_device`, `accounts_count`, `images`, `primary_image_id` |
| `InstagramProfileDTO` | `models.py` | **o mesmo objeto** (`InstagramProfileDTO = PersonaDTO`), pelo nome antigo |

Quem monta os campos é `social/repository.py::campos_de_persona` (uma função para o DTO completo e para o contexto:
a mesma pessoa nos dois). Regras puras em `modules/identity/domain/persona.py`: `idade_em` (idade em `hoje` a
partir de `YYYY-MM-DD`; texto inválido → `None`, nunca chute), `separar_nome` (primeiro espaço), `nome_exibido`
(`display_name` → nome e sobrenome → usuário), `mesclar_secao`, `separar_visual_legado`, `lacunas_da_biografia`.

**`username` opcional pela convenção `''`.** A coluna segue `NOT NULL DEFAULT ''`; o índice único
`ux_instagram_profiles_username ON instagram_profiles(lower(username)) WHERE username <> ''` é parcial. A tradução
`''` ↔ `null` acontece na borda (`campos_de_persona`: `username = row["username"] or None`). Consequências:
`GET /api/instagram/profiles` lista só quem tem conta (`repository.list_profile_ids`, `WHERE username <> ''`);
`GET /api/personas` lista todas as pessoas (`list_persona_ids`, ordenadas por nome); a porta de sessão trata
"pessoa vinculada sem conta neste app" como espera por pessoa, não como erro
(`state.py::AppState._tem_conta_no_app`, [perfis](perfis-e-instagram.md#a-persona-é-a-pessoa)).

**Compatibilidade.** `PersonaDTO.persona_id` e `profile_id` são o próprio `id`; `persona_name` é alias de `name`
(o painel de hoje os lê). O id legado da tabela `personas` continua resolvendo: `repository.persona_row` procura por
`id` e, depois, pela coluna `persona_id` (rastro da 047). A tabela `personas` fica sem leitores no código desta onda
e sai numa migração posterior.

## O modelo, por bloco

Colunas de identidade (estáveis, uma por coluna) e quatro JSONs em `TEXT` (`traits`, `visual`, `biography`,
`generation`), cada um com o seu Pydantic em `models.py`. Todos os modelos de JSON têm `extra="forbid"`, menos
`PersonaGeneration` (`extra="ignore"`): chave desconhecida em `traits`/`visual`/`biography` é dado errado, não dado a
mais.

| Bloco | Coluna(s) | Modelo | Campos |
|---|---|---|---|
| Identidade | `id`, `username`, `display_name`, `first_name`, `last_name`, `birth_date`, `email`, `gender`, `locale`, `status`, `policy_group_id`, `persona_id` (rastro) | colunas | `id` nasce `ig-<token>` (`repository.create_persona`); `birth_date` nunca é inventado; `age` é calculado (`idade_em`) e cai em `biography.approx_age` quando não há nascimento |
| Voz | `summary`, `persona_prompt`, `traits` | `PersonaTraits` | `personality`, `tone`, `formality`, `typical_length`, `emojis`, `slang`, `humor`, `interests`, `dm_style`, `comment_style`, `with_known`, `with_strangers`, `examples`, `common_phrases`, `forbidden_phrases` (a ordem de `PERSONA_VOICE_TRAITS`; `voice_gaps` lista os vazios) |
| Visual | `visual` | `PersonaVisual` | `appearance`, `visual_style`, `photo_scenario` (as três que moravam em `traits` antes da 047), `palette`, `age_presentation`, `gender_presentation`. Não vai ao prompt de texto; alimenta a receita de imagem |
| Biografia | `biography` | `PersonaBiography` | `schema_version` (`BIOGRAPHY_SCHEMA_VERSION = 1`), `approx_age`, `origin` (`birthplace`, `hometown`, `nationality`), `home` (`city`, `state`, `country`, `residence`), `work` (`profession`, `employer`, `education[]`), `life` (`marital_status`, `children`, `history[]`), `beliefs` (`religion`, `politics`), `tastes` (`interests[]`, `hobbies[]`, `preferences[]`, `dislikes[]`) |
| Proveniência | `generation` | `PersonaGeneration` | `source` (`manual` \| `ai` \| `legacy_persona`), `persona_id` (linha de `personas` de origem), `prompt`, `provider`, `model`, `usd`, `at`, `enriched_at` |

`tastes.interests` é espelho de `traits.interests` gravado pela 047; a voz continua lendo `traits.interests` (fonte
única do prompt).

### O que vai ao modelo e o que fica guardado

Três listas distintas, e não se confundem:

| Lista | Onde | Caminhos | Para quê |
|---|---|---|---|
| `PERSONA_VOICE_TRAITS` | `models.py` | os 15 traços de voz | renderizados no bloco `<persona>` e mostrados na conferência do portal |
| `PERSONA_BIO_FIELDS` | `models.py` | `home.city`, `work.profession`, `work.education`, `tastes.hobbies` | o que da **biografia** vai ao modelo, como linha curta |
| `BIOGRAFIA_MINIMA` | `modules/identity/domain/persona.py` | `origin.birthplace`, `home.city`, `work.profession`, `work.education`, `tastes.hobbies` | o mínimo para a biografia contar como **completa** (`generate` e `enrich`) |

O bloco `<persona>` (`social/context.py::SocialContextBuilder._persona_block`) leva, nesta ordem: `perfil: @username`,
`nome da persona`, `idade` (calculada), as linhas de `PERSONA_BIO_FIELDS`, `resumo`, os traços de
`PERSONA_VOICE_TRAITS` preenchidos e `instruções da persona` (`persona_prompt`). **Tudo passa por `sem_marcacao`**,
inclusive nome e resumo: texto de persona gerada por modelo não é mais confiável que uma legenda lida da tela.

Fica guardado e **não** vai ao modelo: `beliefs.religion` e `beliefs.politics` (decisão do dono pendente, design
§15), `tastes.interests` (já vai pela voz), `tastes.preferences`/`dislikes`, `origin.*`, `home.state`/`country`/
`residence`, `work.employer`, `life.*`, `visual`, `generation`, `email`, `birth_date` cru (só a idade).

## Criar, alterar e apagar

- **Criar** (`POST /api/personas`, corpo `PersonaCreate`, `social/service.py::SocialService.create_persona`): pessoa
  sem conta em app nenhum. `first_name`/`last_name` vazios saem de `name` por `separar_nome`; `traits` aceita as três
  chaves visuais antigas (`PersonaTraitsEdit`) e o servidor as move para `visual` (`separar_visual_legado`) em vez de
  responder 422, porque o painel de hoje ainda as escreve dentro de `traits` (até a onda E); `generation` vazio vira
  `{source: manual, at}`.
- **Alterar** (`PATCH /api/personas/{id}`, `PersonaPatch`, `update_persona`): **por seção**. `traits`, `visual` e
  `biography` são mesclados chave a chave sobre o gravado (`mesclar_secao`): dicionário dentro de dicionário mescla
  recursivamente (mudar `home.city` não apaga `home.state`); `null` explícito apaga a chave; lista e escalar
  substituem inteiros. `name` grava `display_name` (e parte nome/sobrenome se ainda vazios); `null` em `name` ou
  `persona_prompt` é "não mexer". Nunca se substitui o JSON inteiro.
- **Apagar** (`DELETE /api/personas/{id}`, `delete_persona`): é apagar a **pessoa**, com contas, credencial (e o
  ciphertext no cofre), sessão, vínculo, memória e histórico (FKs em cascata; `delete_profile`). Recusa com 409
  `persona_in_use` enquanto houver vínculo com aparelho (`repository.binding_row`) ou execução em curso
  (`_execucao_em_curso`: objetivo fora de `OBJECTIVE_SETTLED`).
- **Adoção e absorção pela rota de perfil.** `POST /api/instagram/profiles` com `persona_id` de uma pessoa sem conta
  faz **ela** ganhar a conta, mesma linha e mesmo id (`_pessoa_sem_conta` + `repository.adopt_account`: nome,
  nascimento e e-mail só entram onde a linha estava vazia). `PATCH /api/instagram/profiles/{id}` com `persona_id`
  de uma pessoa sem conta **absorve** a voz, a biografia e o visual dela neste perfil e apaga a linha sem conta
  (`_absorver_persona`, numa transação). Nos dois casos o id de outra pessoa **com** conta é recusado com 409
  `persona_in_use`; id inexistente, 400 `unknown_persona`.

## Geração por IA (`POST /api/personas/generate`)

Chamada **paga**, pelo papel `social`, sem gravar nada: a resposta tem o formato de `PersonaCreate`, para a pessoa
revisar e então criar.

- **Pedido**: `PersonaGenerateBody` (`modules/identity/presentation/schemas.py`): `prompt` (3–2000), `locale?`,
  `constraints` (até 20 pares curtos, ex.: `{"gender": "feminino", "city": "Curitiba", "age": "30-35"}`).
- **Prompt**: `modules/identity/domain/persona_generation.py::PERSONA_GENERATION_SYSTEM` (oito regras: pessoa
  inventada, adulta entre `IDADE_MINIMA_GERADA = 21` e `IDADE_MAXIMA_GERADA = 60`, nenhum segredo ou dado de
  contato, voz completa, biografia coerente, `visual` para fotógrafo, `summary` e `persona_prompt`, e "ao enriquecer
  mantenha o preenchido") e `persona_generation_user_text` (data de hoje, idioma, `<pedido>`, `<restricoes>`,
  `<persona_existente>` no enriquecimento; tudo por `sem_marcacao`). **O que sai da máquina**: o texto do dono, as
  restrições e, no enriquecimento, o que a persona já tem. Nunca tela, memória ou credencial.
- **Provedor**: `planning/provider.py::AIProvider.generate_persona` em todos: `anthropic_provider.py` (papel
  `social`, `strict_schema(PersonaDraft)`, `max_tokens=6000`), `openai_provider.py` (mesmo esquema, com a dica JSON
  do modelo), `simulated_provider.py::persona_simulada` (sorteio determinístico pelo hash do pedido, sempre adulta,
  sem custo). `planning/routing.py::RoutingProvider.generate_persona` roteia pelo papel `social` **sem `run_id`**:
  o teto do dia vale, o da execução não. `persona_draft_from_json` revalida o JSON do modelo (desembrulha cerca de
  código).
- **Validação do rascunho** (`SocialService.generate_persona_draft`): `problemas_do_rascunho` recusa nome que não é
  nome (`nome_ficticio_plausivel`: duas palavras só de letras), idade ausente ou abaixo de `MAIORIDADE = 18`, voz
  incompleta (`voice_gaps`) e biografia abaixo de `BIOGRAFIA_MINIMA`; `_textos_com_segredo` recusa qualquer texto
  com formato de segredo (`security/redaction.py::looks_secret` sobre `textos_de`). Qualquer problema → 422
  `persona_draft_invalid`, com a lista na mensagem.
- **Erros do provedor**: `AIError` vira 503 `ai_budget` | `ai_refusal` | `ai_error`; sem provedor, 503
  `ai_unavailable`.
- **Custo**: `usage_sink` lança o uso no mesmo relatório das demais chamadas; `generation` recebe `source=ai`,
  `prompt`, `provider`, `model`, `at` (`_proveniencia`).
- **Enriquecimento** (`POST /api/personas/{id}/enrich`, `enrich_persona`): completa **só o vazio**. Sem lacuna
  (`_tem_lacuna`: voz, `BIOGRAFIA_MINIMA`, `visual.appearance`, `summary`, `persona_prompt`, idade) devolve a persona
  sem chamar o modelo; com lacuna, `preencher_vazios` garante que o que existia não é reescrito; `birth_date`
  sugerido de menor é recusado; `generation.enriched_at` é gravado e `source` preservado.

Prova: `simulated` (`backend/tests/test_persona_geracao.py`, inclusive os dois provedores pagos por transporte
falso). Geração com modelo real: `not_run` (chamada paga; exige autorização).

## Imagens (`persona_images`, migração 048)

Uma galeria por pessoa: geradas por provedor de imagem, enviadas pelo painel ou herdadas dos avatares legados. Porta
própria, **fora dos cinco papéis de IA** ([ia.md](../ia.md#1-as-cinco-funções)).

### A receita (`modules/identity/domain/persona_image.py`)

- **Identidade que entra** (`PersonaIdentity`, montada por `infrastructure/persona_images.py::identidade_para_foto`):
  `visual.appearance`, `visual_style`, `photo_scenario`, `palette`, `traits.interests[:5]`, `age`, `gender` (ou
  `visual.gender_presentation`), `biography.work.profession`, `biography.home.city`. **O nome nunca entra no
  prompt**; `initials` só pinta o carimbo do simulado.
- **Semente**: `semente(persona_id, indice) = sha256(f"{persona_id}:{indice}:{SPEC_VERSION}")[:4] & 0x7FFFFFFF`
  (31 bits; `SPEC_VERSION = 1`). Mesma pessoa, mesmo índice → a mesma receita; mudar `SPEC_VERSION` muda tudo de
  propósito. Os índices continuam a numeração existente: repetir um pedido não repete uma foto.
- **Eixos sorteados pela semente** (`montar_spec`): `CAMERAS` (5), `EPOCAS` (3), `LUZES` (5), ambiente
  (`photo_scenario`, senão `interests[:3]`, senão `AMBIENTES_PADRAO`), `ENQUADRAMENTOS` (5), `POSES` (5),
  `PRODUCOES` (3), `PROPORCOES` (`1:1`, `4:5`, `3:4`, `9:16`) e o pós-processamento (`jpeg_quality` 55–92,
  `noise_sigma` 0–4, `blur_radius` 0–0,8, `downscale` 0,6–1, `crop_shift` ±0,1).
- **A imagem 0 é a principal**: `head and shoulders`, rosto visível, `1:1`.
- **Prompt** (`prompt_de`): inglês, determinístico, "fictional … adult", atributos limpos (`_limpo`: sem `<`/`>`,
  240 caracteres), e sempre "no text, no logo, no watermark, not a real person". `prompt_sha256` fica na linha.
- **Maioridade**: `montar_spec` levanta `MenorDeIdade` abaixo de `MAIORIDADE`; nem o simulado fotografa.
- **Pós-processamento honesto** (`adapters/pos_processamento.py::pos_processar`): recorte na proporção, redução e
  reampliação, ruído e desfoque leves, JPEG variável — a variação que fotos de gente comum têm. Sem EXIF inventado,
  sem remoção deliberada de proveniência; o original do provedor é guardado ao lado (`original_key`).

### O serviço (`modules/identity/application/persona_images.py::PersonaImageService`)

- `gerar(persona_id, identity, count)`: até `MAX_POR_PEDIDO = 3` por chamada; `conferir_orcamento` **antes** de
  pedir imagem paga (teto `ai_max_usd_per_day`; o simulado não gasta e não confere); a primeira imagem pronta de
  quem não tem principal vira a principal; as seguintes recebem a principal como **referência** (o único dado além
  de atributos que sai da máquina); recusa do filtro → `refused`, falha → `failed`, com a linha no banco e a
  chamada no relatório; **nunca se cai do pago para o simulado**; recusa ou falha interrompe a leva.
- Storage: `personas/<persona_id>/<image_id>.jpg` (servida) e `.orig.png` (original), no mesmo back-end dos
  avatares (`state.py`: `AppState.avatares`, disco ou bucket; `StorageBlobs`).
- Custo: `infrastructure/persona_images.py::AiCallsAccounting.record` grava em `ai_calls` com `role='image'` e a
  coluna nova `usd`; `planning/costs.py::spent_usd` soma `usd` onde existe e tokens × preço onde não; `simulated`
  segue fora do gasto.
- `registrar_upload` (foto enviada, guardada como veio), `definir_principal` (só imagem `ready`), `apagar` (registro e
  arquivos; a chave legada `avatars/<id>.jpg` não é apagada), `importar_legado` (passo de partida idempotente,
  `AppState._importar_avatares_legados`: pessoa sem imagem + `avatars/<id>.jpg` no storage → linha
  `imported_legacy`, principal).
- Evento `persona.image.updated` (`EVENTO_IMAGEM`) a cada mudança de estado (`ready`, `refused`, `failed`, `primary`,
  `deleted`), com `profile_id`, `image_id`, `status`.
- A geração pela rota roda em segundo plano (`AppState.agendar_imagens`, tarefa em `_bg`): a paga leva minutos e a
  rota responde 202.

### Geradores (porta `application/ports.py::ImageGenerator`)

| Gerador | Arquivo | Chave | Sai da máquina | Custo |
|---|---|---|---|---|
| `simulated` (padrão) | `adapters/simulated_images.py::SimulatedImageGenerator` | nenhuma | nada | 0 |
| `openai` | `adapters/openai_images.py::OpenAIImageGenerator` | `OPENAI_API_KEY` (`config.py::EnvSettings.openai_api_key`, `SecretStr`) | prompt de atributos e, da segunda imagem em diante, a principal como referência (`/v1/images/edits`) | `ai.image.price_per_image[quality]` declarado; sem preço para a qualidade, o **mais caro** da tabela, nunca zero |

O simulado é Pillow determinístico (degradê por semente, silhueta, iniciais e o carimbo "SIMULADO"): prova fiação,
determinismo e custo, **não** fidelidade. O OpenAI usa `gpt-image-1-mini` por `/v1/images/generations` (1024×1024 ou
1024×1536 por proporção, `TAMANHO_DO_PROVEDOR`); 400 com `content_policy`/`moderation`/`safety` sobe como
`GeracaoRecusada`; 401/403/404 → `not_configured`, 402 → `billing`, 429 e 5xx → `retryable` (`GeracaoFalhou`).

Configuração (`config.py::ImageCfg`, bloco `ai.image` em `config/config.example.yaml`): `provider`
(`simulated` | `openai`), `model`, `quality` (`low` | `medium` | `high`), `per_persona` (0–3; 0 desliga a geração
automática), `on_create`, `price_per_image` (US$ 0,005 / 0,011 / 0,036 por imagem 1024², preços de 27/09/2026),
`timeout_s`. `GET /api/ai` expõe `image` (`AiImageStatus`: `provider`, `model`, `quality`, `configured`,
`simulated`, `sends_data_externally`, `per_persona`, `on_create`, `price_per_image_usd`).

### Avatar

`GET /api/instagram/profiles/{id}/avatar` (`api.py::profile_avatar`) serve a imagem **principal** da pessoa quando há;
senão o jpg legado `avatars/<id>.jpg`; senão 404 `sem_foto`. A chave sai do id já validado no banco, nunca do texto
da URL.

Prova: `simulated` (`backend/tests/test_persona_imagens.py`, com o OpenAI por `httpx.MockTransport`). Fica `not_run`
sem chave: imagem real gerada, "as imagens refletem os atributos" e "variam entre personas" só se provam com um
provedor pago configurado e autorização de gasto.

## Migração de dados (047)

`backend/migrations/047_persona_e_a_pessoa.sql`, com a diretiva `-- @foreign_keys:off`
([banco](../banco.md#diretiva-foreign_keysoff-reconstrução-de-tabela-pai-no-sqlite)).

| Grupo (retrato de produção em 27/09) | O que a 047 faz |
|---|---|
| (a) 3 perfis **com** `persona_id` | copiam `summary`, `traits` (só voz), `persona_prompt` da persona; `visual` recebe `appearance`/`visual_style`/`photo_scenario`; `biography` nasce com `schema_version`, `tastes.interests` e `approx_age` (os dois dígitos antes de " anos" no resumo, `(\d{2}) anos` no PostgreSQL); `generation = {source: legacy_persona, persona_id}` |
| (b) 5 personas órfãs cujo nome bate (`lower(trim)`) com `display_name` ou `first_name || ' ' || last_name` de um perfil **sem** `persona_id` (os bloqueados do ADR-029) | o perfil ganha o `persona_id` e a mesma dobra de (a); empate de nome → menor id (`_dobra_persona`), nunca duas personas no mesmo perfil |
| (c) as 6 órfãs restantes | viram pessoas novas `ig-<persona_id>` com `username = ''`, `display_name = name`, nome partido no primeiro espaço, `status = active`, `persona_id` apontando para a origem |

`generation.source = 'legacy_persona'` é o predicado de idempotência (`WHERE p.generation = '{}'`): rodar as
instruções de dados de novo não muda nada; banco vazio continua vazio. A FK `persona_id → personas` fica; `personas`
fica intacta e sem leitores.

**Por que reconstruir no SQLite.** O banco de produção nasceu com a 008 antiga, em que `username` é
`TEXT NOT NULL UNIQUE COLLATE NOCASE` inline (lido em `sqlite_master` em 27/09; a 028 registra a divergência). Essa
UNIQUE de coluna não sai com `ALTER` e recusaria a **segunda** pessoa sem conta, só em produção. Daí o molde da 010
(`instagram_profiles_novo` + `DROP` + `RENAME`) e a diretiva: medido antes da 047 que, com `foreign_keys=ON`, o
`DROP TABLE` disparava o `ON DELETE CASCADE` das filhas. No PostgreSQL basta `ALTER` e o índice parcial.

**Ensaio `real` (27/09)**, numa cópia de `data/backups/20260927-222357`, máquina `WIN-7S2UASNLFOP`, código do merge `8c19d5a`: 047, 048 e 050 aplicadas; seis tabelas filhas byte a byte iguais (8 vínculos, 8 sessões, 8 credenciais,
24 memórias, 8 contas, 19 tentativas); `PRAGMA integrity_check` ok; `foreign_key_check` vazio; 14 pessoas (3 ativas,
5 bloqueadas dobradas por nome, 6 sem conta); `visual` em 8; segunda aplicação sem efeito. Produção: `not_run`.

**Decisão do dono pendente**: as 5 fotos dos perfis bloqueados são anexadas às pessoas dobradas pelo importador de
avatares legados; desfazer é possível porque `personas` e `persona_id` ficam (design §15).

## Rotas

`/api/personas` é a canônica; `/api/instagram/profiles*` continua para conta, credencial, sessão e o que sempre foi
do perfil. Códigos e corpos no [adendo v0.27](../api-contract.md#adendo-v027-27092026--a-persona-é-a-pessoa-geração-por-ia-e-imagens).

| Rota | Faz |
|---|---|
| `GET /api/personas` | todas as pessoas, com ou sem conta |
| `POST /api/personas` | cria a pessoa; com `ai.image.on_create` e gerador configurado, agenda `per_persona` imagens |
| `GET/PATCH/DELETE /api/personas/{id}` | pessoa; PATCH por seção; DELETE com as travas |
| `POST /api/personas/{id}/preview` | testa a voz (já existia) |
| `POST /api/personas/generate` | rascunho por IA, não gravado |
| `POST /api/personas/{id}/enrich` | completa o vazio por IA |
| `GET /api/personas/{id}/images` | galeria (`PersonaImageDTO[]`) |
| `POST /api/personas/{id}/images` | JSON `{count}` → gera em segundo plano (202); corpo `image/jpeg`\|`png` → upload (201) |
| `GET /api/personas/{id}/images/{img}` | os bytes, pelo storage |
| `PUT /api/personas/{id}/images/{img}/primary` | define a principal |
| `DELETE /api/personas/{id}/images/{img}` | apaga registro e arquivos |
| `GET /api/instagram/profiles` | só quem tem conta de cadastro |
| `POST /api/instagram/profiles` | cadastra conta; `persona_id` de pessoa sem conta adota |
| `PATCH /api/instagram/profiles/{id}` | `persona_id` de pessoa sem conta absorve |

## Testes

| Arquivo | O que prova (`simulated`) |
|---|---|
| `backend/tests/test_persona_migracao_047.py` | os três grupos e as filhas preservadas; FKs sobrevivem à reconstrução; `username` opcional e único; a 047 sobre o esquema **real** da produção (008 antiga); esquema igual em banco novo e atualizado; empate de nome; os dois dialetos renderizam sem marca sobrando |
| `backend/tests/test_persona_unificada.py` | regras puras; pessoa sem conta com visual separado da voz; id legado resolve; `traits` fora do contrato não derruba a listagem; PATCH por seção e nulo apaga; adoção no cadastro; absorção e recusa no PATCH; apagar com as travas; bloco `<persona>` escapado com o handle do app; porta de sessão sem conta; rotas canônicas e apelidos |
| `backend/tests/test_persona_geracao.py` | persona simulada determinística e adulta; enriquecer preenche só o vazio; regras do rascunho; rascunho vira `PersonaCreate`; erros do provedor traduzidos; OpenAI e Anthropic por transporte falso; rotas |
| `backend/tests/test_persona_imagens.py` | semente e receita determinísticas; simulado pinta os mesmos pixels; OpenAI `generations`/`edits` e erros; gera, guarda, registra custo e define a principal; referência, teto, recusa e falha; upload, principal, apagar e avatar legado; custo declarado no gasto do dia; migração 048; rotas e avatar; importação na partida |

## Desvios do desenho e pendências

- O design (§10.1, risco R1) previa `username` opcional **sem** reconstruir a tabela; a produção obrigou a
  reconstrução, resolvida pela diretiva `@foreign_keys:off`. `generation` da dobra é `{source: legacy_persona,
  persona_id}` (o design dizia `{origin: manual}`); o id das pessoas novas é `'ig-' || persona.id` inteiro.
- `persona_name` mantido no DTO; `persona_id` do DTO é o próprio `id`.
- `ProfileCreate.persona_id` adota e `ProfilePatch.persona_id` absorve (o painel de hoje "cria a persona" e depois a
  aponta no perfil).
- As travas de vínculo e execução valem só no `DELETE`.
- `SocialService.draft_response(app_id=)` existe, mas `state.py` ainda não o passa: o "Você é @…" usa a conta do app
  do perfil.
- `ai_calls.usd` só entra em `costs.spent_usd`; `GET /api/usage` e `GET /api/desempenho` não o leem.
- `dados_disponiveis` (§13 do design) não existe no código.
- Conta única (`profile_accounts` + `account_credentials` como fonte, sessão por conta e aparelho) é a onda B.
- `beliefs` no prompt, remoção de `personas` e da FK `persona_id`, provedor de imagem local (ADR-023): pendentes.
