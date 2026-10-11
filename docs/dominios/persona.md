# Domínio: persona (a pessoa)

A persona é a **pessoa** por trás das contas: identidade, voz, biografia, identidade visual, proveniência e a galeria de
imagens. Desde a migração 047 ela é a linha de `instagram_profiles` (evolução 2, onda A;
[design](../design/persona-e-parque.md) §3, §5, §6 e §10;
[ADR-041](../decisoes.md#adr-041--a-persona-é-a-pessoa-instagram_profiles-como-raiz-personas-dobrada-username-opcional-por-string-vazia-e-reconstrução-com-foreign_keys-off)
e
[ADR-042](../decisoes.md#adr-042--imagens-de-persona-receita-determinística-porta-imagegenerator-simulado-primeiro-openai-atrás-de-chave-custo-em-ai_callsusd)).
As **contas** da pessoa (app ou site), a credencial com consentimento, a sessão por (conta, aparelho) e o
catálogo de dados que o plano pode usar estão em [Contas e acesso](#contas-e-acesso) (onda B;
[ADR-040](../decisoes.md#adr-040--a-credencial-pertence-à-conta-da-persona-e-a-execução-não-carrega-credencial)).
O provedor de sessão do Instagram e as tabelas legadas continuam em [perfis e Instagram](perfis-e-instagram.md); o
contrato HTTP está nos adendos
[v0.27](../api-contract.md#adendo-v027-27092026--a-persona-é-a-pessoa-geração-por-ia-e-imagens) e
[v0.28](../api-contract.md#adendo-v028-27092026--conta-única-credencial-com-consentimento-e-sessão-por-conta-e-aparelho-adr-040);
o banco, em [`banco.md`](../banco.md).

Caminhos relativos a `backend/app/`, salvo indicação. Código da onda A: `462d724` (047), `469baae` (modelo e rotas),
`6dcbdc5` (geração por IA), `e68c506` (048 e imagens), integrados em `8c19d5a`. Onda B: `2ca5344`/`78136db` (049),
`ad0cab6`, `3b5088b`, `2f0952b`, `f2f4684`, integrados em `4b95592`.

**Estado (28/09): implantado no ambiente central em `07fce91`** (relatório de validação §15). O histórico abaixo é
de antes da implantação.

**Estado (27/09).**

- Migrações 047 e 048, modelo, rotas, geração e imagens: suíte SQLite 2459/2459 no branch (`simulated`).
- Ensaio `real` da 047+048 numa **cópia** do backup de produção `data/backups/20260927-222357` (27/09): ver
  [migração de dados](#migração-de-dados-047). PostgreSQL: `not_run` (o GitHub Actions ficou bloqueado por cobrança em 27/09; K-040).
- IA real (geração/enriquecimento), OpenAI Images real e produção: `not_run`. Nada implantado.
- Onda B (049, conta única, credencial da conta no plano): suíte SQLite do branch 2433 passed, 2 flakes
  (`test_instalacao_do_worker`, verdes isolados); suíte do merge: ver CHANGELOG. **Ensaio `real` da 049 (28/09, madrugada; máquina `WIN-7S2UASNLFOP`, código `073475c`)** numa cópia nova de `data/backups/20260927-222357`, junto com 047, 048 e 050: 8 credenciais de conta com os mesmos 8 `secret_ref` de `instagram_credentials` e consentimento datado; 3 sessões em `account_sessions` (android-01 `unknown`, android-03 e android-06 `session_ready`; as 5 fantasmas dos perfis bloqueados não vieram); 19 tentativas apontadas para a conta; `host` presente; 8 contas e nenhuma com handle vazio (o primeiro ensaio criou 6 contas vazias para as personas sem conta — corrigido na própria 049 antes de ir à `main`); `integrity_check` ok, `foreign_key_check` vazio, idempotente.
  PostgreSQL e produção `not_run`.

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
| Biografia | `biography` | `PersonaBiography` | `schema_version` (`BIOGRAPHY_SCHEMA_VERSION = 2`; a v1 é normalizada na leitura, ADR-048), `approx_age`, `origin` (`birthplace`, `hometown`, `nationality`), `home` (`city`, `state`, `country`, `residence`), `work` (`profession`, `employer`, `education[]`), `life` (`marital_status`, `children`, `history[]`), `beliefs` (`religion: BioReligion | null` — `affiliation`, `practice`, `practices[]`, `importance`, `in_speech`, `values[]`, `sensitive_topics[]`, `summary`; `politics: BioPolitics | null` — `orientation`, `engagement`, `issues[{topic, stance}]`, `discussion_style`, `sources[]`, `values[]`, `summary`), `tastes` (`interests[]`, `hobbies[]`, `preferences[]`, `dislikes[]`) |
| Proveniência | `generation` | `PersonaGeneration` | `source` (`manual` \| `ai` \| `legacy_persona`), `persona_id` (linha de `personas` de origem), `prompt`, `provider`, `model`, `usd`, `at`, `enriched_at` |

`tastes.interests` é espelho de `traits.interests` gravado pela 047; a voz continua lendo `traits.interests` (fonte
única do prompt).

### O que vai ao modelo e o que fica guardado

Três listas distintas, e não se confundem:

| Lista | Onde | Caminhos | Para quê |
|---|---|---|---|
| `PERSONA_VOICE_TRAITS` | `models.py` | os 15 traços de voz | renderizados no bloco `<persona>` e mostrados na conferência do portal |
| `PERSONA_BIO_FIELDS` | `models.py` | desde 28/09, a biografia inteira: moradia, trabalho, formação, hobbies, origem, vida, preferências e o que não gosta (16 campos, em ordem de prioridade) | o que da **biografia** vai ao modelo, uma linha por campo, com orçamento (`ORCAMENTO_DA_BIOGRAFIA_TOKENS` = 350, no máximo 6 itens por lista; `filhos: 0` = "não tem") |
| `BIOGRAFIA_MINIMA` | `modules/identity/domain/persona.py` | `origin.birthplace`, `home.city`, `work.profession`, `work.education`, `tastes.hobbies` | o mínimo para a biografia contar como **completa** (`generate` e `enrich`) |
| `PERSONA_RELIGION_FIELDS` / `PERSONA_POLITICS_FIELDS` | `models.py` | os campos de `beliefs.religion` / `beliefs.politics` | seções "religião:" e "política:" do bloco `<persona>`, seguidas da linha de uso das crenças (`USO_DAS_CRENCAS`; sem regra de conteúdo desde 06/10) |
| `CRENCAS_MINIMAS` | `modules/identity/domain/persona.py` | `beliefs.religion.affiliation`, `beliefs.politics.orientation` | lacuna só do `enrich`; crença **não** conta para a biografia completa |

O bloco `<persona>` (`social/context.py::SocialContextBuilder._persona_block`) leva, nesta ordem: `perfil: @username`,
`nome da persona`, `idade` (calculada), a linha `como usar esta persona` (`USO_DA_PERSONA`: o pedido manda no QUE fazer; a persona só dá o jeito), as linhas de `PERSONA_BIO_FIELDS` dentro do orçamento, as crenças e a linha `conduta sobre crenças` (ADR-048; só quando há crença), `resumo`, os traços de
`PERSONA_VOICE_TRAITS` preenchidos e `instruções da persona` (`persona_prompt`). **Tudo passa por `sem_marcacao`**,
inclusive nome e resumo: texto de persona gerada por modelo não é mais confiável que uma legenda lida da tela.

Fica guardado e **não** vai ao modelo: `tastes.interests` (já vai pela voz), `tastes.preferences`/`dislikes`, `origin.*`, `home.state`/`country`/
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
  ciphertext no cofre de **toda** conta, não só da âncora: 23.9), sessão, vínculo, memória e histórico (FKs em
  cascata; `delete_profile`). Recusa com 409
  `persona_in_use` enquanto houver vínculo com aparelho (`repository.binding_row`) ou execução em curso
  (`_execucao_em_curso`: objetivo fora de `OBJECTIVE_SETTLED`).
- **Adoção e absorção pela rota de perfil.** `POST /api/instagram/profiles` com `persona_id` de uma pessoa sem conta
  faz **ela** ganhar a conta, mesma linha e mesmo id (`_pessoa_sem_conta` + `repository.adopt_account`: nome,
  nascimento e e-mail só entram onde a linha estava vazia). `PATCH /api/instagram/profiles/{id}` com `persona_id`
  de uma pessoa sem conta **absorve** a voz, a biografia e o visual dela neste perfil e apaga a linha sem conta
  (`_absorver_persona`, numa transação). Nos dois casos o id de outra pessoa **com** conta é recusado com 409
  `persona_in_use`; id inexistente, 400 `unknown_persona`.

## Contas e acesso

Onda B da segunda evolução ([design](../design/persona-e-parque.md) §3.2, §4, §10.3;
[ADR-040](../decisoes.md#adr-040--a-credencial-pertence-à-conta-da-persona-e-a-execução-não-carrega-credencial)).
Migração `backend/migrations/049_contas_unificadas.sql`.

**Conta única.** A conta é `profile_accounts`: (pessoa, app, `host`). `host` é nulo para a conta do app inteiro e é o
site para conta de portal no navegador (normalizado por `social/service.py::_host_da_conta`: minúsculo, sem esquema,
caminho nem porta); a unicidade é `(profile_id, app_id, COALESCE(host, ''))`. A conta do Instagram é uma conta como as
outras, a **conta âncora** do perfil (`social/repository.py::SocialRepository.conta_ancora`: a conta no app que provê
a conta do perfil, o app âncora do registro — `registry.py::pacote_ancora`, `ancora_do_perfil: true` no `app.yaml`,
ADR-052; nasce no cadastro ou na primeira escrita de senha ou sessão, nunca numa leitura, e só para perfil
com `@`). Criar: `POST /api/instagram/profiles/{id}/accounts` (`ProfileAccountCreate`: `app_id`, `handle`, `host?`,
`password?`, `login_identifier?`, `consent`, `clonar_de?`, `notes`); alterar: `PATCH …/accounts/{aid}` (`host`, `handle`, `status`,
`notes`, `session_status`); apagar: `DELETE …/accounts/{aid}` (a âncora não sai: `anchor_account`).

**Credencial com estado e consentimento.** `account_credentials` (uma por conta): `login_identifier`, `secret_ref`
(referência no cofre; o valor nunca está no banco), `key_id`, `status` (`active` | `invalid`), `failed_attempts`,
`blocked_until`, `created_at`, `updated_at`, `last_used_at`, **`consent_at`/`consent_by`**. O consentimento é da
**conta**: guardar a senha com `consent: true` é a pessoa autorizando a automação a digitá-la, pelo canal sensível,
só no app e no site daquela conta; trocar a senha preserva o consentimento já dado
(`SocialRepository.set_account_credential`: `COALESCE(excluded.consent_at, consent_at)`). Sem a marca, ninguém digita:
nem `type_secret`, nem o provedor de sessão (`integrations/app_declarado/sessao.py::SessaoDeclarada._blocked_reason`
e a porta de sessão `state.py::AppState._session_gate`). Regras em `SocialService._gravar_credencial`: conta que ainda não consentiu e pedido sem `consent` → 409
`consentimento_de_credencial`; cofre sem chave → 503 `secret_store_unavailable`. `_login_da_conta` decide o
identificador: o informado; senão o gravado; senão, em app com provedor, o e-mail do perfil (é o que o Instagram
aceita) e em app comum o `handle`. Transitório: em app com provedor, `login_identifier` igual ao `handle` não
substitui o e-mail já gravado (a aba Contas do painel manda o `@`). A senha dada no cadastro do perfil
(`ProfileCreate.password`) grava `consent_by = 'cadastro do perfil'` (`SocialService._store_password`): o formulário
existe para o login automático. Apagar a credencial apaga o segredo do cofre, salvo enquanto a linha legada de
`instagram_credentials` ou outra linha de `account_credentials` ainda referenciar a mesma `secret_ref`
(`_apagar_credencial`).

**Credencial clonada de outra conta da persona** (23.9; ADR-057, decisão do dono D1: a conta Outlook recebe a senha
do Instagram sem que o valor saia do cofre). `SecretStore.clonar(ref, para=None)` decifra e cifra de novo dentro do
módulo e devolve só a referência: entrada **própria** (nonce novo, AAD da referência nova; com `para`, sobrescreve no
lugar a entrada que a conta destino já tinha, para não deixar ciphertext órfão). Compartilhar a `secret_ref` foi
descartado: trocar ou apagar uma conta mudaria a outra. Dois caminhos, uma regra (`SocialService._origem_do_clone` e
`_clonar_credencial`):

- ao criar: `ProfileAccountCreate.clonar_de = <account_id>`; exclui `password` (422 `clonar_de_com_senha`) e
  `consent` (422 `consentimento_nao_clonado`), tudo conferido **antes** de criar a conta (recusa não deixa conta
  solta);
- numa conta existente: `POST …/accounts/{aid}/credential/clone` (`CredentialClone`: `clonar_de`,
  `login_identifier?`).

A origem tem de ser outra conta **da mesma persona** e com senha guardada: de outra persona, 409
`credencial_de_outra_persona`; inexistente, 404; a própria conta, 409 `clonar_de_si_mesma`; sem senha, 409
`no_credential`; cofre trancado ou chave de outro backend, 503 `secret_store_unavailable` (nada é gravado). O
**consentimento nunca é clonado**: a conta nova nasce sem ele (`consent_by=None`), e nem `type_secret` nem o provedor
de sessão a usam até a pessoa autorizar por `…/credential/consent`; a conta existente que já tinha consentimento
mantém o seu. O `login_identifier` também não vem da origem (ADR-057 §5: o endereço é o dado conferido pelo dono):
vale o informado, o gravado ou o padrão de `_login_da_conta`. A trilha é o evento "Senha de conta clonada…" com
`profile_id`, `account_id`, `origem_account_id` e `by`, sem valor. O campo chama `clonar_de`, e não
`credencial_de`, porque nome com "credencial" é mascarado pela redação e barrado pela guarda dos modelos
(`test_social_profiles.py::test_nenhum_dto_de_resposta_tem_campo_de_credencial`). Painel: guia Contas e acesso,
"Usar a senha de outra conta" no formulário de conta nova e no cartão de cada conta (`GuiaContas.tsx`).

**Painel de contas por app** (23.10). A guia Contas e acesso não compara mais nome nem pacote (`ehInstagram` fixo em
`com.instagram.android`) para saber qual app é a conta de cadastro: `GET /api/app-catalog` ganhou `profile_anchor`
(espelha `AppDefinition.profile_anchor`/`ancora_do_perfil` do `app.yaml`), e o formulário de conta nova
(`GuiaContas.tsx::ehAncora`) pergunta a ele — inclusive o rótulo "Usuário do X" e as mensagens de adoção vêm do
`name` do catálogo, não de um literal. A política de ações (aba Configurações e Grupos de acesso) também deixou de
pegar "o primeiro app com login gerenciado da lista": `GET/PUT …/policy`, `GET/POST/PUT …/policy-groups*` aceitam
`?package=` (sem ele, o app âncora, como sempre); o painel escolhe entre os apps com `has_catalog: true` — o
âncora por padrão, e um seletor "Aplicativo" quando há mais de um (`GuiaConfiguracoes.tsx`, `PolicyGroups.tsx`).
A política é guardada **por app**: a mesma chave de ação existe em mais de um catálogo (o Instagram usa
`SEND_MESSAGE`, e um catálogo do Outlook provavelmente também, T17), e desligá-la num app não pode desligá-la no
outro. Sem migração: em `instagram_profiles.automation_policy.capabilities` e em `policy_groups.capabilities`, o
nível de fora continua sendo o do app âncora (é o formato de todo dado gravado até aqui), e os demais apps ficam
em `por_app.<pacote>` (`{"LIKE_POST": …, "por_app": {"<pacote>": {"SEND_MESSAGE": …}}}`). Quem lê ou grava passa
por `social/policy.py::politicas_do_app` / `com_politicas_do_app`; `policy_for`, `origin_for` e `check` recebem o
`package` da ação, e o despacho (`gates.py::Portoes._policy_gate`; `AppState` delega) passa o pacote da etapa. Nas rotas, `?package=` escolhe
o recorte que se lê E o que se grava: `capabilities`, `own`, `group`, `origin` e `loosened` do DTO são só daquele
app; `limits` valem para o perfil inteiro. No diálogo de grupo, cada app é um rascunho à parte: o do app que a
listagem não trouxe vem de `GET …/policy-groups/{id}?package=`, e salvar faz um pedido por app editado, cada um com
o seu `package`; "Começar a partir de" um perfil SUBSTITUI o rascunho (de todos os apps com catálogo), nunca mescla.
Sem um segundo app com catálogo em produção (o Outlook, 23.8, ainda não tem `catalogo.yaml`), o caminho de dois
apps só está provado por `simulated` (catálogo falso nos testes).

**Sessão por (conta, aparelho).** `account_sessions` tem chave `(account_id, instance_id)` e um só vocabulário
(`models.SessionStatus`): `unknown`, `session_ready`, `auth_required` (o antigo `logged_out`), `wrong_account`,
`needs_person`. Quem grava: o provedor de sessão, em app com `SessionProvider`, na conta DAQUELE app
(`set_account_session`, item 23.4: [abaixo](#sessão-por-conta-item-234)); a pessoa, em app sem provedor (`PATCH
…/accounts/{aid}` com `session_status`: vale para o aparelho vinculado; sem vínculo, 409 `no_binding`; em app com
provedor, 409 `session_managed`; `logged_out` é 422). Leitura: `session_of_account` devolve a sessão no aparelho
vinculado e, sem linha nele, a mais recente (é ela que diz "a sessão pronta é de outro aparelho").
`profile_accounts.session_status` (037) ficou na tabela e ninguém a lê; `apps_overview.py` conta prontas por
`account_sessions`. Sessão é cache do observado, nunca a verdade.

### Conta planejada (31.281, ADR-087, adendo v1.132)

A conta de uma persona num app pode existir só como PLANO, antes de existir no provedor. A linha é a mesma de
`profile_accounts` (o índice único (perfil, app, host) dá a idempotência), com o ciclo `provisioning_state` (migração 131; as
linhas anteriores são `confirmada`):

- **Estados:** `planejada` → `credencial_preparada` → `aguardando_cadastro_externo` → `aguardando_verificacao` → `confirmada`;
  `falha` guarda `resume_state` e `retomar` volta a ele; `cancelar` só antes de `confirmada`. A máquina é função pura
  (`modules/identity/domain/provisionamento.py`); o serviço (`social/provisionamento.py::ProvisionamentoDeContas`, pendurado em
  `SocialService.provisionamento`) lê o banco, mexe no cofre e emite `identity.conta.provisionamento` (só ids e estados: nunca o
  endereço nem a senha). Toda transição leva o `estado_esperado` (comparar e trocar, `set_provisioning` com `WHERE` no estado);
  repetir o evento no estado de destino devolve 200 sem efeito.
- **Desejado x confirmado:** `desired_handle` é o que se quer; `handle` segue sendo o CONFIRMADO e fica vazio até `confirmar`, que exige
  evidência (a sessão observada com o usuário igual ao desejado, ou a marcação nominal da pessoa, gravada como `declarada`).
- **Senha:** `credential/prepare` gera (`security/gerador_de_senha.py`, `secrets`, sem IA), recebe ou reutiliza (o mesmo `clonar`
  do cofre do `credential/clone`). Gerar ou digitar com `consent: true` vale como o consentimento do ADR-040 para aquela conta.
  A senha nunca volta em resposta, log ou evento (`tests/test_conta_planejada.py` varre as tabelas de texto).
- **Quem não a trata como conta real:** `Mundo`/Automático (`taskqueue/service.py::_mundo`, contas e sessões prontas), o
  reconciliador de sessão (`account_session.py::_conta`, `active=False`), o login gerenciado (`sessao.py`, `UNCERTAIN`) e as rotas
  `session/connect|verify|logout` (409 `conta_nao_confirmada`). Os dados ao plano (`profile_data.py`) trazem o desejado no lugar do
  usuário e a senha sigilosa, para o plano que preenche o cadastro. A conta da ponte igfarm nasce `confirmada` com evidência `igfarm`.
- **Login gerenciado × cadastro:** para o app com provedor de sessão (o Outlook, a conta Microsoft), a conta CONFIRMADA é `managed`: o provedor
  entra sozinho e o `type_secret` não recebe a senha. A conta ainda não confirmada não é `managed` (`profile_data.py`): não há login a
  fazer, e `conta_<app>_senha` (sigilosa) e `conta_<app>_usuario` ficam disponíveis ao plano que preenche o formulário de cadastro.
  `provisioning.authenticated` só é verdadeiro com a conta confirmada e a sessão `session_ready` no aparelho VINCULADO.
- **Onde a conta planejada CONTA como conta (de propósito):** `AppState._tem_conta_no_app` (entrega do app ao aparelho) e o portão
  de conta esperada do `Scheduler` (`scheduler.py`, `status='active'`). A entrega precisa do app no aparelho para o cadastro, e a etapa
  que cria a conta precisa passar pelo portão "a persona tem conta neste app"; fora do cadastro, `conta_esperada` não acha handle (vazio)
  e devolve `None`.
- **Refinador por estado (31.282):** `ComandoAssistido.refinar` (`taskqueue/assistente.py`) lê, antes de chamar o modelo, o estado de cada
  par (persona escolhida × app de conta do comando) em `taskqueue/contas_do_comando.py` (só leitura). O prompt recebe só o estado
  (`RefineRequest.contas`; regra "contas e senhas nunca são pergunta"). Depois do modelo, `_sem_pergunta_de_credencial` descarta a pergunta que a
  triagem marca como sensível e devolve `acoes_de_conta` (sem campo de texto, fora da triagem de resposta); com ação em aberto `ready` é falso.
  Sem persona escolhida (Automático) não há item: a nota manda preparar a credencial de cada uma em Contas e acesso depois da escolha.
- **Não faz:** o cadastro no provedor (CAPTCHA, código e e-mail são da pessoa, ADR-009).

### Cadastro guiado da conta planejada (31.310, ADR-087, adendo v1.137)

Fecha o ciclo `credencial_preparada` → `confirmada` sem IA e sem que a plataforma conheça app nenhum.

- **Dado do app:** `app/conhecimento/apps/<pacote>/cadastro.yaml`, ao lado do `sessao.yaml`
  (`integrations/app_declarado/cadastro_conhecimento.py`, validado na carga): telas reconhecidas por texto e por resource-id, e a ação de
  cada uma (`tocar`, `preencher`, `codigo`, `sucesso`, `parar`). O vocabulário do que um campo recebe é fechado (`usuario`, `nome`,
  `primeiro_nome`, `sobrenome`, `email`, `nascimento_dia|mes|ano`, `senha`), e só a `senha` é segredo. Exatamente uma tela `envia: true`
  (`preencher` ou `tocar`), exatamente uma tela de `sucesso`.
  **31.324 (G1), para o app cujo conteúdo vem do servidor e cujo campo não tem id, texto nem descrição:** o `Alvo` (botão, campo, conta, código)
  acha o elemento por critérios que valem juntos: `id`, `texto`, `classe`, `senha: true`, `abaixo_do_rotulo` (regex do rótulo; vale o
  campo do filtro mais próximo logo abaixo dele, pelo CENTRO e com sobreposição horizontal; rótulo ausente, repetido ou empate = nenhum) e
  `ordem` (o n-ésimo na ordem de leitura: de cima para baixo, em faixas de 24 px, e da esquerda para a direita; as rodas de uma data).
  A data de nascimento é a `birth_date` da persona (dado, nunca chute): sem ela ou com menos de 18 anos o serviço recusa com 409 antes de
  tocar no aparelho. O app que manda o código ANTES de criar a conta declara `dispara_codigo: true` na tela cujo botão o pede (uma vez por
  execução; é o piso do `desde` do código) e `antes_do_envio: true` na tela do código; esse código não conta como envio da conta. A
  `acao: data` do desenho virou `preencher` com `nascimento_*` e `ordem`; a roda que só rola (sem campo de texto) precisa de um gesto da
  Mesa e fica para a captura da igfarm. **Nenhum app real o declara ainda** (`test_nenhum_app_real_declara_cadastro_ainda`): D1 e D2 estão com o dono.
- **Motor** (`integrations/app_declarado/cadastro.py`, `MotorDeCadastro`): a TELA decide cada passo, não uma memória. Observa, reconhece, age:
  texto comum pelo caminho comum (conferindo o que ficou no campo), senha pelo canal sensível e só em campo que o Android diz ser de senha,
  código do e-mail pelo mesmo canal, um toque em enviar e um em continuar. Reiniciar no meio retoma pelo estado gravado e pela tela, e o
  formulário nunca é enviado duas vezes (só com a conta em `aguardando_cadastro_externo`, uma vez por execução, e a tela que volta ao
  formulário depois do envio é `tela_desconhecida`). O `enviado` é gravado ANTES do toque em enviar (queda ou cancelamento depois do toque não reabrem o formulário); toda
  parada depois dele guarda `resume_state: aguardando_verificacao`, exceto `usuario_indisponivel`, que volta ao cadastro. O campo do código não é
  conferido como `password` (é texto do e-mail, mas entra pelo canal sensível); e o envio é a tela `envia: true` (31.324: pode ser um `tocar`, no app
  de uma pergunta por tela; o toque que dispara o código não é envio). Limites conhecidos: o e-mail, o @ e o nome vão por `type_text` comum (o log do
  Appium pode mostrá-los; não são credencial); o campo do código normalmente não é de senha, então a hierarquia relida o traz em claro
  em `rt.last_tree` e na prévia ao vivo (nunca vira evidência); retomar com a tela do código ainda aberta digita o código de novo, a pedido
  da pessoa.
- **Paradas** (`identity/domain/cadastro.py::Parada`, códigos fechados): `captcha`, `desafio` ("confirme que você é humano", pela detecção
  genérica de conta travada: nada toca nela; a conta nem existe ainda, então nada a bloqueia), `telefone`, `usuario_indisponivel`,
  `tela_desconhecida`, `codigo_nao_chegou`, `conta_nao_lida`, `app_fora_do_ar`, `falha_interna` (erro nosso; só o nome do tipo vai ao log). Cada uma é `falhar` com o código em `provisioning_detail`,
  e `ProvisioningInfo.proximo_passo` o repete. A tela do PEDIDO de código (subtipo `codigo` da detecção) não é trava: é a tela `codigo` do
  app, ou, sem declarar, tela desconhecida.
- **Ligação ao parque** (`identity/infrastructure/cadastro_guiado.py`): a rota valida (todos os 409 com código) e despacha o verbo
  `session.cadastrar` pelo mesmo caminho de `session.verify`; `MesaDoAparelho` traduz o driver; `CicloNoBanco` faz cada transição por
  `ProvisionamentoDeContas.transicao` (comparar e trocar), e a confirmação grava a sessão observada (`set_account_session`, o @ lido
  na tela) e confirma com evidência `sessao`. `get_secret` ganhou este módulo como consumidor documentado (a mesma função que o canal
  sensível chama no instante da digitação).
- **Uma conta por vez** no parque inteiro (`cadastro_em_andamento`), nunca liga aparelho, nunca usa IA, sem solver de CAPTCHA, sem proxy,
  sem lote.
- **Prova:** `simulated` (`tests/test_cadastro_guiado.py`: ciclo completo, as 8 paradas, retomada, código velho, recusas da rota,
  carga do yaml, varredura de vazamento). `real`: `not_run`, até o dono autorizar criar UMA conta de verdade num provedor.

### O cadastro no app é o caminho principal (31.334, desenho; sem código até a captura)

Decisão do dono (11/10/2026): a conta do Instagram é criada NO APP, no aparelho (o motor do 31.310 com o `cadastro.yaml` do app). A API do igfarm
deixa de ser a fonte da conta e vira APOIO (e-mail, código, SMS, proxy). O que o app signup precisa e de onde vem:

- **Telas do cadastro:** conteúdo do servidor, só com rede. A captura offline de 10/10 tem só a entrada ("Create new account"). **Bloqueio: a
  captura com sessão sticky nova** (P-050), parando antes do envio. Só com ela entra o `cadastro.yaml` do Instagram.
- **Já coberto (G1, 31.324):** nascimento da persona, código por e-mail com o piso no toque que o pediu (`dispara_codigo`/`antes_do_envio`),
  senha pelo canal sensível, confirmação só pela sessão observada.
- **L1, caixa da conta planejada:** o e-mail do parque é catch-all (`docs/email-do-parque.md`), então não há caixa por persona a provisionar; mas
  a linha `caixas_email` só nasce hoje no registro do igfarm (`ponte_igfarm.py`). Falta criá-la para a conta PLANEJADA, sem o igfarm
  (o cadastro exige a linha: 409 `sem_caixa_de_email`). Sem migração; dentro do 31.334 (decisão de 11/10). **Feita (L1):**
  `CadastroGuiado.iniciar` cria a linha da conta planejada (`identity/infrastructure/caixa_planejada.py`: o e-mail do perfil ou a sugestão da ponte
  se forem do parque e livres, senão o endereço gerado sobre o domínio do parque; `secret_ref` = marcador, sem segredo) antes de despachar; o 409
  `sem_caixa_de_email` passa a significar "o parque não tem domínio permitido".
- **L2, proxy sticky por conta antes do primeiro toque (item 31.337, da Ponte):** hoje o perfil `igfarm-<conta>` (`docs/egresso-por-proxy.md`) nasce quando o igfarm
  REGISTRA a conta. No app signup o perfil nasce na conta planejada, é atribuído ao aparelho antes do cadastro, e o `egress_esperado` é o
  primeiro egresso medido DENTRO da janela (31.329). O igfarm só entrega o `proxy_url`.
- **SMS:** sem rota nossa nem do igfarm no repositório. Sem API de SMS, a tela de telefone segue sendo a parada `telefone` (a pessoa assume).
- **Critérios de sucesso:** (1) conta criada no app, `confirmada` por sessão observada, @ lido igual ao desejado; (2) login limpo, medido por
  criada → 1º login → desfecho (`GET /api/instagram/contas/{id}/ciclo`, 31.333); (3) 1 post. A prova `real` exige o sim do dono em chat (conta
  nova no Instagram). Antes disso, tudo é `simulated`/`not_run`.
- **Risco declarado:** no teste de bifurcação de 10/10, IP casado não evitou o bloqueio no único caso com desfecho (n=1); nada garante que o
  app signup escape do mesmo.

### Persona de teste (31.314, adendo v1.139, migração 134)

`instagram_profiles.teste` marca a pessoa que existe só para provar o produto (`PersonaDTO.teste`; `PersonaCreate`/`PersonaPatch` o
aceitam). Ela fica fora do que é automático ou em massa: a sugestão de alvos do comando e a distribuição por app (`Mundo.de_teste`,
`candidatos_do_app`), as operações em lote (pool e criação), a contagem de contas do painel e os avisos ao dono (Telegram:
`ServicoDeAvisos._e_de_persona_de_teste`; Trello: as pendências do espelho). Citada pelo nome ou pelo id, serve como qualquer outra. A
regra mora em `app/contracts/persona_de_teste.py` (fragmentos de SQL e a leitura da linha), para existir em UM lugar.

### Sessão por conta (item 23.4)

[ADR-057](../decisoes.md#adr-057--outlook-como-primeiro-app-novo-conta-por-app-sessão-por-conta-e-credencial-clonada-no-cofre).
Antes, credencial, tentativa, sessão e invalidação do login gerenciado caíam na conta âncora, qualquer que fosse o
app do provedor: o login de um segundo app gravava a sessão, a senha recusada e o teto diário na conta do Instagram.
Agora tudo é da **conta do app**:

- **Porta** (`modules/identity/application/ports.py::SessionProvider.ensure_session`, contrato C1): `account_id` é a
  conta que a chamada abre — do perfil e do pacote do provedor, senão recusa sem tocar no aparelho; `None` é a conta
  do perfil no pacote do provedor (`SocialRepository.conta_do_pacote`: no app âncora, `conta_ancora`; em qualquer
  outro, a conta daquele app, sem nunca cair na âncora). Persona sem conta no app: `invalid_credential` sem tocar.
- **Motor** (`integrations/app_declarado/sessao.py`): `SessaoDeclarada._resolver_conta` monta a `ContaDaSessao`
  (id, app, `handle`, identificadores aceitos, se é a âncora); credencial, consentimento, marcação
  (`mark_account_credential`, `touch_account_credential`), tentativa e teto diário (`authentication_attempts` filtradas
  por `account_id`), sessão (`account_session_row`/`set_account_session`) e o marcador de conta travada (`@` e app da
  conta) são dela. A conta lida na tela é comparada ao `handle` e ao `login_identifier` da conta
  (`normalizar_conta`: sem maiúsculas nem o `@` do começo), não ao `@` de cadastro. `last_verified_at` do perfil só
  muda com a conta âncora. O status do perfil (`blocked`/`disabled`) segue da persona.
- **Site de login e conta de site (23.6)**: a Custom Tab do login só é seguida, e só recebe a senha, nos sites de login
  que o app declara no `sessao.yaml` (`navegador.hosts`, ou subdomínio); fora deles a pessoa assume, e a senha não sai.
  A conta com `host` é de portal, pelo navegador, e não tem login gerenciado: a porta e o despacho só acham a conta do
  app inteiro (`conta_do_pacote`), e o "Conectar" dela recusa sem tocar no aparelho (`_resolver_conta`; na rota, 409
  `conta_de_site`) ([perfis e Instagram](perfis-e-instagram.md#instagram-classificador-login-determinístico-sessão)).
  No login em etapas, a senha só vai para a tela que mostra o `login_identifier` da conta (senão o `handle`).
- Soltar é o de sempre da `review`: guardar a senha de novo ou um login que confirma a conta (Conectar). Limite, o mesmo
  do código na âncora: `review` para o login, não a sessão já pronta da conta noutro aparelho. Sem rota, migração nem
  painel novos: a credencial em `review` já aparece em `CredentialInfo` e a fila "Aguardando intervenção" já traz
  `account_id`.
- **Composição** (`state.py`): a porta de sessão (`_session_gate`) lê a sessão e a credencial da conta da persona no
  pacote do item e passa `account_id` ao provedor, também na releitura do teto (`_releitura_do_teto`, trava por
  conta) e na troca de localidade (`_porta_da_localidade`, que derruba a sessão de toda conta da persona naquele
  disco); `_invalidate_sessions(instance_id, motivo, package=None)` invalida só as contas do app que mudou, e sem
  pacote (reset, troca de máquina) toda conta do aparelho (`invalidate_sessions_of_instance(todos_os_apps=True)`);
  `_sessao_desmentida(…, package=None)` corrige a conta do app da tela (o `package` dito — o executor sempre o diz:
  `StepExecutor._sessao_desmentida` passa o pacote da tela a `on_auth_needed`, `AoDesmentirSessao` —, senão o da
  etapa em curso, `_pacote_em_curso`, senão a âncora); `_reobservar_apos_intervencao` relê cada conta que esperava uma pessoa pelo
  provedor do app dela.
- **Rotas e comandos**: `POST …/accounts/{aid}/session/{connect|verify}` (e os apelidos por perfil) chamam o provedor
  com `account_id=conta.id`; o canal de comandos (`modules/execution/infrastructure/command_bus.py`,
  `session.connect`/`session.verify`) resolve a conta da persona no app do pedido (ou valida o `account_id` dele) e a
  grava nos parâmetros do comando. Mover o aparelho ou a persona de máquina pede confirmação com a sessão pronta de
  qualquer conta dela ali. O evento `session.needs_person` ganhou `data.account_id`.
- Nada mudou no esquema (037/049/051 já eram por conta) nem no contrato HTTP das rotas.

**Catálogo de dados disponíveis** (`modules/identity/domain/available_data.py`, função pura; porta
`application/account_ports.py::ProfileDataStore`; adaptador `infrastructure/profile_data.py::SqlProfileDataStore`,
só `SELECT`). Por perfil, a lista fechada `PROFILE_FIELDS`, só os que têm valor:

| Nome | Origem | Sigiloso |
|---|---|---|
| `perfil_nome`, `perfil_sobrenome`, `perfil_nome_exibicao`, `perfil_nascimento`, `perfil_email` | `instagram_profiles.first_name`, `last_name`, `display_name`, `birth_date`, `email` | não |
| `conta_<app>_usuario`, `conta_<app>_<host>_usuario` | `account_credentials.login_identifier`, senão `profile_accounts.handle` | não |
| `conta_<app>_senha`, `conta_<app>_<host>_senha` | `account_credentials.secret_ref` | **sim**: só existe como nome; oferecido só com credencial guardada, consentida e de app sem `SessionProvider` |

`<app>` e `<host>` viram `[a-z0-9_]` (`slug`, 60 caracteres; colisão recebe `_2`, `_3`…), o alfabeto de
`TEMPLATE_RE` e de `PARAMETER_NAME`; `SENSITIVE_PARAM` já casa `senha`, então receita nunca guarda esses valores.
A biografia não gera nome nenhum nesta onda (o design §4.2 previa `perfil_idade`, `perfil_cidade` etc.).

**O que vai ao modelo e o que é sigiloso.** O planejador (livre e por catálogo) recebe a lista **comum** a todos os
aparelhos da execução (`application/available_data.py::common_data` → `PlanRequest.available_data`); o ator e o
verificador, a do aparelho da etapa (`StepContext.available_data`). O bloco `planning/prompts.py::dados_block` leva
nome, rótulo e tipo; um sigiloso aparece como `SIGILOSO: só com type_secret(name=…)`. Dado não sigiloso é variável
`{perfil_email}` resolvida por aparelho em `taskqueue/repository.py::Repository.materialize` (`profile_variables`,
fotografadas em `instances[].variables` no planejamento). Dado sigiloso só sai do cofre em
`taskqueue/executor.py::StepExecutor.preenchedor`, resolvido pelo perfil do **objetivo** (`resolve_secret`), com
consentimento, só campo de senha, só no pacote da conta e, no navegador, só no `host` da conta (ou subdomínio); a
conta ganha `last_used_at` e a execução registra qual conta entrou (nome, nunca valor). A Custom Tab do login de um app
(o navegador na frente com a conta do app) ainda é recusada aqui: só o motor de sessão a aceita, desde o 23.6. Senha guardada sem
consentimento no app da etapa → `waiting_user` com `consentimento_pendente`. `open_url` aceita os hosts das contas
de portal (`ToolContext.allowed_hosts`). O contexto social (`social/context.py`) não conhece a porta: não há caminho
dele ao cofre (`backend/tests/test_social_memory.py`). A execução **não** carrega credencial: `RunCreate.credentials`
e `consent_credentials` saíram (422 por `extra="forbid"`), e `run_secrets` ficou sem escritor.

**Pré-voo do dado da persona (31.87, F1).** Antes de `materialize`, `RunService._plan` varre o plano (objetivo, pré e
pós-condição, guardas, `bindings` e `parameters`) atrás de `{perfil_*}` e `{conta_<app>_usuario}`
(`taskqueue/dado_da_persona.py`). Aparelho cuja persona não resolve algum (ausente, vazio, ou sem persona vinculada):
a execução vai a `needs_input`, sem etapas, com uma frase por aparelho (só id e rótulo do campo, nunca valor, nome ou
e-mail). A pergunta leva `field: "persona_data"` (cadastre o dado na persona e crie a execução de novo: `needs_input`
só sai para `cancelled`, e dizer o valor no comando não vira parâmetro do fluxo reaproveitado); `profile_id` só quando
o aparelho não tem persona. `materialize` e `revise_plan` repetem a conferência e recusam com `DadoDaPersonaAusente`;
nunca gravam a variável crua (recuperação: recusada com motivo; retomada: `RunError`; expansão do `for_each`: item
bloqueado). Limite conhecido: as `variables` das etapas do `for_each` (item, item_index) não são varridas.
`persona_data` não vai ao livro de aprendizado: o conjunto `CAMPOS_SEM_RESPOSTA_POR_TEXTO` (`taskqueue/perguntas.py`)
junta destino e dado da persona, e a sucessora recusa a resposta por texto quando só há `persona_data`.

**O ensino usa o dado da persona (31.87, F2; decisão do dono, 05/10).** No modo treinamento, o dado não sigiloso da
persona que a pessoa DIGITOU como uma entrada inteira (o campo recebeu exatamente o valor) vira o marcador
(`training/dado_da_persona.py`):
- na proposta (`propose`, e de novo no `save` e na prévia, para a proposta editada à mão): o parâmetro do comando cujo
  exemplo é esse dado sai do comando e da lista, com a palavra de ligação antes dele ("com", "para", lista fechada), e
  `{param}` vira `{perfil_x}` nas etapas. O valor literal vira o marcador por palavra no título, no objetivo e na
  descrição; no que a etapa digita ou confere (`bindings[].value`, `postcondition.value`), só quando é o campo inteiro.
  Dentro de uma frase ("Oi Ana, tudo bem?" para uma destinatária homônima) fica literal;
- na destilação: a receita digita `{perfil_email}`, não o e-mail de quem ensinou (os parâmetros da pessoa vencem no
  mesmo valor);
- na reprodução: o executor passa os dados da persona do objetivo ao `Replayer` (os do objetivo vencem).

O marcador não entra em `plan.parameters` (o casamento recusaria o fluxo); o texto das etapas se resolve na
materialização, e o pré-voo do F1 recusa o aparelho sem o dado. O `save` e a prévia avisam quais marcadores vêm do
perfil. O consumo é genérico por chave (`profile_variables`, da identidade): as chaves novas entram sem mudar o
ensino. Valor com menos de 3 caracteres, ou só dentro de outro texto, não é trocado. Sem persona no treino, nada muda.

**A pergunta da IA também leva o marcador (31.112; achado da prova real do 31.87).** `questions[]` e
`answers[].question` (31.91) trocam o dado por palavra, como o título (`dado_da_persona.nas_perguntas`). A troca vale
ao guardar: no `propose`, para a pergunta nova da IA e para a pergunta que o corpo devolve, e no `save` e na prévia.
Vale também ao mostrar: `TrainingRecorder.get` e `list` mascaram a proposta gravada antes do 31.112, pela mesma regra do
dado digitado inteiro. A resposta da pessoa (`answer`) fica como ela escreveu. A pergunta devolvida com o valor (cliente
aberto antes) casa com a guardada, e a IA passa a receber o marcador na pergunta respondida.

**A gravação salva guarda o marcador (31.118; achado da prova real do 31.87, 06/10).** Enquanto a sessão grava ou espera
a proposta, `training_inputs.text` fica com o que a pessoa digitou, porque a proposta precisa do valor. No `save`, a
entrada de texto cujo campo INTEIRO é um dado da persona que a habilidade usa (o marcador está no plano salvo) passa a
guardar o marcador (`dado_da_persona.marcas_das_entradas`, `TrainingRecorder.marcar_entradas`). `has_text` e
`text_len` ficam. `GET /api/training/{id}` devolve o marcador, sem mudar a forma da resposta. Quem precisa do valor
depois o lê da persona, em memória: o reparo das receitas (`refazer_receitas`), a máscara das perguntas e o
reconhecimento do dado demonstrado (`dado_da_persona.com_valores`). Outro texto, dado dentro de frase, dado que a
habilidade não usa e treino sem persona ficam como estavam. Sem migração: a coluna é a mesma. As sessões salvas antes
do 31.118 recebem a mesma regra por um reparo único (`scripts/gravacao-com-marcador.py`, `training/reparo_da_gravacao.py`).
Ele ensaia numa cópia por padrão, e `--aplicar` exige o backup e a mesma migração do código. É idempotente e só imprime
os ids das sessões e as contagens.

**F2: a tela gravada também (31.118 F2; achado da conferência real de 06/10).** A tela gravada logo depois da digitação
mostra o campo preenchido, e o GET devolvia o valor em `inputs[].screen_lines`. No `save` e no reparo, todo dado não
sigiloso da persona (não só o digitado) troca pelo marcador, por palavra, em `screen_lines` e `screen_title`
(`reparo_da_gravacao.marcar_telas`, com `dado_da_persona.com_marcador`). O relatório do reparo ganha `telas_marcadas`.
O alvo do toque (`target`) não muda: a destilação monta o seletor a partir dele. Desde o 31.122 F2 (migração 121), o
mesmo vale para o texto e a descrição de `screen_elements`, a tela inteira guardada para uso interno (fora do GET).

**O registro da execução guarda o marcador, não o valor (31.113, F1; achado da prova real do 31.87).** A tela segue
com o valor: o executor digita e confere com o que tem em memória. O que FICA troca valor → marcador na fronteira de
escrita (`security/mascara_da_persona.py`, camada irmã de `redact` e de `enderecos_limpos`):
- `Repository.log_intent` (`args`, `rationale`), `finish_action` (`result`, `error`), `note_attempt` e
  `finish_attempt` (`error`, `observed_result`; o tipo da falha é classificado antes, pelo texto), a nota da evidência
  e o `status_detail` da etapa;
- todo evento com execução, objetivo, etapa ou tentativa, pela máscara que o `Repository` liga no `EventBus`.

O mapa é por objetivo: os dados que identificam a persona (nome, sobrenome, nome de exibição, e-mail, nascimento e,
desde o 31.243, o usuário de cada conta, `{conta_<app>_usuario}`, com e sem a arroba) sempre, os outros só quando o plano os cita (senão "Brasil" sumiria de todo texto). Entra também o dado que o ator
digitou INTEIRO, de qualquer chave. Regras do ensino: o valor mais longo primeiro, no mínimo 3 caracteres, palavra
inteira, sem diferença de maiúscula. O valor que está num parâmetro do comando fica, porque o parâmetro vence (regra do
F2) e a receita aprendida da execução continua guardando `{param}`. O `actions.target` fica com o valor: é o seletor da
receita e da lição (o elemento da tela), e o evento não o leva.

**A nota do juiz na evidência leva também o usuário da conta e o texto da etapa (31.242).** Achado da leitura das
evidências da onda 2 de 07/10: a nota do juiz repete o usuário da conta ("@fulano said …") e o texto do comentário,
que o mapa acima não leva. A nota passa pelo mapa da NOTA (`Repository.trocas_da_nota`), que soma três coisas:
- o mapa do registro, que desde o 31.243 leva o usuário de cada conta da persona (com e sem a arroba, porque a borda
  da palavra não casa depois de `@`);
- os textos da etapa que o juiz repete (`content`, `username`, `post_author`, `target`; a legenda `caption_contains`
  fica, porque é o texto público do alvo e a evidência diz qual legenda conferiu), como
  `{chave}`.

O valor curto demais ou que já é marcador fica de fora. Vale na gravação e na saída (`Repository.nota_para_fora`:
detalhe da execução no painel e na API, e o evento `evidence.added`). Assim, a nota gravada antes sai mascarada sem
reescrever o banco.

Limites:
- só o valor INTEIRO: o trecho ou a paráfrase do comentário fica;
- na saída vale o usuário de AGORA: a conta trocada ou retirada deixa o antigo na nota velha;
- o contexto da falha no treino (`training/origem.py`) lê o banco direto; desde o 31.243 ele mascara pelo barramento
  (`EventBus.mascara` e `mascara_da_nota`, que o `Repository` liga), inclusive a linha antiga gravada em claro.

As leituras e o treino seguem casando pela chave: o marcador volta ao valor com as variáveis da persona
(`resolver_texto`), como o nome desde o 31.113 F3. O usuário digitado sem a arroba já era mascarado (o dado digitado
inteiro); com a arroba, agora também.

**A receita aceita o marcador da persona já gravado (31.244).** A destilação lê o `type_text` do banco, que traz o
marcador (`{perfil_nome}`, `@{conta_<app>_usuario}`) e não o valor. Antes, esse texto não ficava 100 % coberto por
parâmetros e a etapa não virava receita: era o custo do 31.113 F1 e do 31.243. Agora `recipes.distill(..., persona=)`
recebe os NOMES das variáveis da persona do objetivo (`_RecipeRun.persona`, sem valor). O marcador cuja variável a
persona tem conta como coberto, e a arroba logo antes dele não sobra como literal. A reprodução digita o valor da persona
da vez, porque o replayer já recebe `{**persona, **rr.variables}` (31.87 F2). O marcador que nem a etapa nem a persona
resolvem segue recusado (a reprodução falharia). Sem `persona`, a destilação é a de antes.

A F1 não cobre o texto das etapas (`steps.postcondition`/`goal`/`title` e `plan_versions.steps`): a materialização ainda
grava o valor resolvido. Isso é a F2 (o molde na linha e a resolução em memória no executor, combinada com a Android).
A aprovação e a porta de política guardam e mostram o marcador; a tela de aprovação resolve o valor ao vivo pela
persona (F2). As execuções anteriores ficam como estão: o registro nasce mascarado a partir do deploy que levar o
commit da F1 (ramo `feat/31-113-f1-registro-mascarado`).

**F2: a etapa guarda o marcador, e o executor resolve num ponto só.** A materialização (e a revisão do plano)
deixa as variáveis da persona como marcador no texto que descreve e confere a etapa: título, objetivo, pré e
pós-condição e guardas (`Repository._insert_steps`, argumento `molde`). A linha, o `plan_versions` e o evento
`step.updated` ficam com o marcador. `StepExecutor.run_step` resolve com a persona do objetivo
(`dado_da_persona.resolver_persona`), e dali em diante o ator, a receita, a conferência da tela (`text_visible`) e
o juiz recebem o valor, só em memória. A mesma leitura da persona serve ao `Replayer` da receita ensinada. Se o dado
sumiu da persona depois da materialização, a etapa espera a pessoa ("Dado ausente: …", só o nome do campo) e nada é
digitado. A identidade da etapa (`template_hash`) não muda: é calculada antes, com todas as variáveis.

**F3: os `bindings` também guardam o marcador, e a porta resolve o valor de agora.** A materialização deixa o marcador
nos argumentos da etapa, como no texto. A porta de política, a frota, a repetição e a chave da aprovação leem os
argumentos por um leitor ÚNICO, `Repository.bindings_da_etapa`. Ele resolve com a persona lida NA HORA, sem cache, e
nunca grava. Uma varredura em teste acusa a leitura crua fora dele
(`test_bindings_com_marcador_da_persona.py::test_a_porta_le_os_bindings_so_pelo_leitor_unico`).
- **A chave é calculada sobre o valor, igual na prévia e na execução.** A persona trocada depois do sim muda a chave, e
  a porta pergunta de novo.
- **`argumentos_da_acao` e `tem_variavel` não mudaram** (parecer da Ferramentas de 06/10, com `VERSAO_DA_CHAVE` igual).
  O dado ausente ou VAZIO deixa o marcador, nunca "" (`resolver_texto`), e a chave falha fechado.
- **A frota (`_mesmo_pedido_noutras_contas`) resolve cada irmã com a persona do objetivo dela.** O mesmo marcador em
  duas personas não é o mesmo alvo.
- **O histórico da repetição (`SocialRepository`) resolve as linhas antigas com a persona do perfil.** Assim, a
  repetição se acha com o marcador novo e com o valor antigo.
- **O pedido de aprovação guarda o marcador.** Alvo e texto passam pela máscara reversível
  (`Repository.texto_reversivel`: palavra inteira, a MESMA caixa, e só se a volta der o texto exato; senão, literal).
  O resumo passa pela máscara do registro. O rascunho da IA e a edição do dono (no painel, na prévia ou no cartão)
  seguem a mesma regra reversível. O 31.49 compara valor com valor.
- **A tela do painel recebe o valor de agora, sem gravar:** `GET /api/approvals`, a resposta da decisão e a prévia
  da porta.
- **O canal leva o marcador:** a prévia do Telegram (`porta_do_plano.previa_para_o_canal`), o evento
  `approval.pending` e as pendências.

O cache da máscara do registro (F1) guarda só o que não muda (perfil, nomes citados, parâmetros, dado digitado); o
valor da persona é lido a cada uso, e o nome trocado no perfil não sai em claro.

Ficam com o valor, de propósito:
- `social_interactions.outgoing_content`: é o texto que SAIU, a prova do efeito, e é com ele que a repetição compara;
- `remember_screen`: a tela comprovada vira memória da PRÓPRIA persona (origem `observation`, 30 dias), lida pela
  voz dela; o valor ali é o que a tela mostrou, e a memória é dela.

As execuções anteriores ficam como estão. O registro nasce mascarado a partir do deploy 46, que leva a F1
(`feat/31-113-f1-registro-mascarado`) e a F2 (`feat/31-113-f2-etapa-com-marcador`); os `bindings` com o marcador, a
partir do corte 47 (F3, `feat/31-113-f3-bindings-com-marcador`).

**A pergunta da IA também leva o marcador (31.112; achado da prova real do 31.87).** `questions[]` e
`answers[].question` (31.91) trocam o dado por palavra, como o título (`dado_da_persona.nas_perguntas`). A troca vale
ao guardar: no `propose`, para a pergunta nova da IA e para a pergunta que o corpo devolve, e no `save` e na prévia.
Vale também ao mostrar: `TrainingRecorder.get` e `list` mascaram a proposta gravada antes do 31.112, pela mesma regra do
dado digitado inteiro. A resposta da pessoa (`answer`) fica como ela escreveu. A pergunta devolvida com o valor (cliente
aberto antes) casa com a guardada, e a IA passa a receber o marcador na pergunta respondida.

**Rotas por conta** (`api.py`; as antigas por perfil são apelidos da conta âncora):

| Rota | Faz |
|---|---|
| `PUT …/accounts/{aid}/credential` | guarda a senha (`CredentialUpdate`: `password`, `login_identifier?`, `consent`); sem `consent` numa conta que nunca consentiu, 409 `consentimento_de_credencial` |
| `DELETE …/accounts/{aid}/credential` | apaga a credencial (e o segredo, salvo referência legada) |
| `POST …/accounts/{aid}/credential/consent` | marca o consentimento sem redigitar; sem senha guardada, 409 `no_credential` |
| `POST …/accounts/{aid}/credential/clone` | usa a senha de outra conta da persona (`CredentialClone`: `clonar_de`, `login_identifier?`), clonada no cofre; outra persona 409 `credencial_de_outra_persona`; o consentimento desta conta não muda (23.9) |
| `POST …/accounts/{aid}/session/connect` | 202; só app com provedor (`s.sessoes.for_package(conta.package)`; senão 409 `no_session_provider`); sem senha 409 `no_credential`; sem consentimento 409 `consentimento_de_credencial`; o provedor recebe `account_id` (23.4) |
| `POST …/accounts/{aid}/session/verify` | 202; só observa, nunca digita; o provedor recebe `account_id` (23.4) |
| `POST …/accounts/{aid}/session/logout` | 202; apaga os dados do app da conta naquele aparelho |
| `GET …/accounts/{aid}/auth-attempts` | tentativas desta conta (`authentication_attempts.account_id`) |
| `PUT/DELETE …/{id}/credential`, `POST …/{id}/{connect\|verify\|logout}`, `GET …/{id}/auth-attempts` | apelidos: a conta âncora; perfil sem conta no app âncora → 409 `no_account` |

`ProfileAccountDTO` traz `host`, `login_identifier`, `credential` (`CredentialInfo`, só metadados, com `consent_at`
e `consent_by`), `consent_at`, `session` (`SessionInfo`, com `stale`), `session_actions` e os escalares antigos
(`session_status`, `session_detail`, `session_verified_at`, `credential_configured`). O contexto do aparelho
(`contexto.py::contexto_do_aparelho`) lista `accounts` do perfil vinculado.

**Painel.** A guia "Contas e acesso" é da onda E. Até lá, o campo "Senha para a automação" da tela de Comando
(`frontend/src/features/command/CommandPanel.tsx`) ainda aparece e quem digitar nele recebe 422 de `POST /api/runs`.

### Conta planejada no painel (31.283, ADR-087, adendo v1.132)

A guia "Contas e acesso" prepara uma conta que ainda NÃO existe no serviço ("Preparar conta nova", `ContaPlanejada.tsx`):
app do catálogo, endereço **desejado** (sugerido pelos dados da pessoa, editável) e a senha, que é **gerada** pelo
servidor (nunca exibida), **digitada** em campo seguro (esvazia depois de cada envio) ou **reaproveitada** de outra
conta da MESMA pessoa (só se escolhida; nunca o padrão). A caixa de autorização é obrigatória. O cartão da conta mostra
o ciclo (`provisioning.state`), desejado x confirmado e os eventos que o servidor aceita agora (`actions`); cada evento
leva `estado_esperado`. Conta fora de `confirmada` não oferece Conectar, Verificar nem Sair (o servidor responde 409
`conta_nao_confirmada`), e `confirmar` só com evidência declarada. Central sem `provisioning` vale como `confirmada`.
O assistente do comando (`AcoesDeConta.tsx`) mostra `acoes_de_conta` em cartões (preparar, abrir Contas e acesso, usar
existente, continuar) e conta cada um como pendência; nenhuma senha entra no corpo do refinamento.

## Geração por IA (`POST /api/personas/generate`)

Chamada **paga**, pelo papel `persona` (item 17.8; sem `ai.roles.persona` herda o `social`), sem gravar nada: a resposta tem o formato de `PersonaCreate`, para a pessoa
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
  sem custo). `planning/routing.py::RoutingProvider.generate_persona` roteia pelo papel `persona` (herda o `social` até ser configurado) **sem `run_id`**:
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

**Em lote** (28/09): `POST /api/personas/generate/batch` gera 1 a 10 pessoas em segundo plano (concorrência 2),
cada uma pelo mesmo caminho do rascunho único; o pedido de cada item leva `avoid` (quem já existe e as irmãs do
lote) e `variation` (o índice), e um nome repetido depois da geração vira falha sem nova chamada. O estado fica
em memória (perdido num reinício). No painel: "Quantidade" no "Nova persona a partir de um prompt", criar direto
ou revisar, custo antes; e operações em lote na lista (fotos, completar, grupo, bloquear/reativar, apagar).

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
- **Prompt** (`prompt_de`): inglês, determinístico, atributos limpos (`_limpo`: sem `<`/`>`,
  240 caracteres), e sempre "no text, no logo, no watermark". `prompt_sha256` fica na linha.
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
determinismo e custo, **não** fidelidade. O OpenAI usa `gpt-image-1-mini` (valor de `ai.image.model`; a OpenAI o descontinua em **01/12/2026**, sucessor
`gpt-image-2`, com preço por imagem ainda a medir — não tratar o mini como permanente) por `/v1/images/generations` (1024×1024 ou
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

## Aparelhos e roteamento

Onda C da segunda evolução ([ADR-043](../decisoes.md#adr-043--persona-nn-aparelho-vínculo-por-app-aparelho-principal-e-uma-conta-por-app-em-cada-aparelho),
[ADR-044](../decisoes.md#adr-044--roteamento-das-execuções-por-persona-alvos-resolvidos-destinos-no-texto-e-prévia-obrigatória);
migração `051_persona_n_aparelho.sql`; contrato no
[adendo v0.29](../api-contract.md#adendo-v029-28092026--persona-nn-aparelho-e-roteamento-por-persona)).

**N:N.** Uma persona tem N aparelhos (`devices[]`) e um **principal** (`instance_id`): o alvo padrão de conectar,
verificar, sair e do contexto. Um aparelho tem N personas, **uma por app** (D2-a: duas contas do mesmo app no mesmo
aparelho são recusadas com 409 `conta_do_app_ja_no_aparelho` enquanto a troca de conta no Instagram for manual).
Vincular não toma o aparelho de ninguém. A D2-a também vale ao **ganhar a conta**: o vínculo sem app serve a todo app em
que a persona tem conta, então cadastrar a conta (ou somar a de outro app) de quem está vinculado sem app a um aparelho
já ocupado por outra persona naquele app é 409, conferido antes de criar qualquer linha (29.29).

**Roteamento.** "Peça para o André …" resolve assim: `TargetExtractor` acha "o André" no texto (padrões fixos, sem
IA) e o tira do comando; `resolver_alvos` escolhe o aparelho. Política `device_policy`: `one` (padrão), `primary`,
`all`. Destino tirado do texto só executa depois de mostrado (prévia `POST /api/runs/targets/resolve` + eco em
`targets`).

| Entrada | Resultado |
|---|---|
| só aparelho, 0 ou 1 persona | a persona do aparelho (ou nenhuma), origem `ui` |
| só aparelho, 2+ personas | a que serve ao app do comando; senão pergunta |
| persona (`one`) | sessão pronta num aparelho apto (com sessão em mais de um: o principal, se ligado ou se nenhum outro com sessão estiver ligado; 29.65) > principal apto > balanceamento entre aptos > principal |
| persona (`primary`/`all`) | o principal / todos os aptos |
| persona + aparelhos | interseção (vazia → 409 `sem_intersecao`); um → `ui`, vários → política |
| `targets` com aparelhos | usados como vieram (não vinculado → 409 `sem_vinculo`) |
| texto dentro da seleção | estreita (origem `texto` → 409 `alvos_nao_confirmados` até o eco) |
| texto fora da seleção, ou homônimos | pergunta (`needs_input`, sem plano) |
| só texto | o texto decide, origem `texto` |
| nada | 400 `sem_alvo` |
| mesmo aparelho duas vezes | 409 `aparelho_repetido_na_execucao` |

Testes (`simulated`): `backend/tests/test_vinculos_n_n.py`, `test_personas_aparelhos_api.py`,
`test_roteamento_por_persona.py`, `test_alvos_no_texto.py`, `test_roteamento_execucao.py`.

## Migração de dados (047)

`backend/migrations/047_persona_e_a_pessoa.sql`, com a diretiva `-- @foreign_keys:off`
([banco](../banco.md#diretiva-foreign_keysoff-reconstrução-de-tabela-pai-no-sqlite)).

| Grupo (retrato de produção em 27/09) | O que a 047 faz |
|---|---|
| (a) 3 perfis **com** `persona_id` | copiam `summary`, `traits` (só voz), `persona_prompt` da persona; `visual` recebe `appearance`/`visual_style`/`photo_scenario`; `biography` nasce com `schema_version`, `tastes.interests` e `approx_age` (os dois dígitos antes de " anos" no resumo, `(\d{2}) anos` no PostgreSQL); `generation = {source: legacy_persona, persona_id}` |
| (b) 5 personas órfãs cujo nome bate (`lower(trim)`) com `display_name` ou `first_name || ' ' || — | o perfil ganha o `persona_id` e a mesma dobra de (a); empate de nome → menor id (`_dobra_persona`), nunca duas personas no mesmo perfil |
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
| `POST /api/personas/generate/batch`, `GET …/batch/{id}` | lote de 1 a 10 em segundo plano (202, evento `persona.batch.updated`), criar direto ou revisar ([adendo v0.34](../api-contract.md#adendo-v034-28092026--personas-em-lote)) |
| `POST /api/personas/{id}/enrich` | completa o vazio por IA; corpo opcional `{instructions}` com o que o dono quer para o que falta (adendo v0.32); painel: "Completar com IA" na guia Persona |
| `GET /api/personas/{id}/images` | galeria (`PersonaImageDTO[]`) |
| `POST /api/personas/{id}/images` | JSON `{count}` → gera em segundo plano (202); corpo `image/jpeg`\|`png` → upload (201) |
| `GET /api/personas/{id}/images/{img}` | os bytes, pelo storage |
| `PUT /api/personas/{id}/images/{img}/primary` | define a principal |
| `DELETE /api/personas/{id}/images/{img}` | apaga registro e arquivos |
| `GET /api/instagram/profiles` | só quem tem conta de cadastro |
| `POST /api/instagram/profiles` | cadastra conta; `persona_id` de pessoa sem conta adota |
| `PATCH /api/instagram/profiles/{id}` | `persona_id` de pessoa sem conta absorve |
| `GET/POST …/{id}/accounts`, `PATCH/DELETE …/accounts/{aid}` | as contas da pessoa (app ou site, com `host`) |
| `PUT/DELETE …/accounts/{aid}/credential`, `POST …/credential/consent` | senha e consentimento por conta ([acima](#contas-e-acesso)) |
| `POST …/accounts/{aid}/session/{connect\|verify\|logout}`, `GET …/accounts/{aid}/auth-attempts` | sessão e tentativas por conta; as rotas por perfil são apelidos da conta âncora |
| `POST …/personas/{id}/devices`, `DELETE …/devices/{iid}`, `PUT …/devices/{iid}/primary` | vínculos N:N e o aparelho principal ([abaixo](#aparelhos-e-roteamento)) |
| `GET /api/instances/{id}/personas` | as personas de um aparelho |
| `POST /api/runs/targets/resolve` | prévia dos alvos de uma execução, sem gravar |

## Testes

| Arquivo | O que prova (`simulated`) |
|---|---|
| `backend/tests/test_persona_migracao_047.py` | os três grupos e as filhas preservadas; FKs sobrevivem à reconstrução; `username` opcional e único; a 047 sobre o esquema **real** da produção (008 antiga); esquema igual em banco novo e atualizado; empate de nome; os dois dialetos renderizam sem marca sobrando |
| `backend/tests/test_persona_unificada.py` | regras puras; pessoa sem conta com visual separado da voz; id legado resolve; `traits` fora do contrato não derruba a listagem; PATCH por seção e nulo apaga; adoção no cadastro; absorção e recusa no PATCH; apagar com as travas; bloco `<persona>` escapado com o handle do app; porta de sessão sem conta; rotas canônicas e apelidos |
| `backend/tests/test_persona_geracao.py` | persona simulada determinística e adulta; enriquecer preenche só o vazio; regras do rascunho; rascunho vira `PersonaCreate`; erros do provedor traduzidos; OpenAI e Anthropic por transporte falso; rotas |
| `backend/tests/test_migracao_contas_unificadas.py` | 049: credencial copiada sem recifrar e só sessão com vínculo ativo; carga idempotente; banco novo = atualizado; unicidade por perfil, app e host; apagar a conta apaga a sessão; os dois dialetos; cópia entre bancos acha a ordem por FK |
| `backend/tests/test_credenciais_da_conta.py` | a execução não aceita mais credencial (422); senha só com consentimento, também pelo apelido por perfil; consentir sem redigitar; segredo preservado enquanto a linha legada o referencia; dados disponíveis listam nomes e nunca valores; a lista do planejador é a comum aos aparelhos; `{perfil_email}` resolve por aparelho; tela de senha só pede pessoa sem senha da conta do app; `type_secret` só campo de senha, só pacote e host da conta, exige consentimento; Instagram fora do `type_secret`; `open_url` aceita o site da conta; pré-voo recusa aparelho sem a credencial exigida |
| `backend/tests/test_contas_unificadas_api.py` | DTO da conta com credencial, consentimento e sessão; marcar sessão sem aparelho é 409; rotas por conta e apelidos por perfil |
| `backend/tests/test_sessao_declarada.py` (23.4) | persona com a conta âncora e a do correio fictício: login, senha recusada e conta errada do correio não mudam uma linha da conta âncora, e o motor da âncora não muda a do correio; `account_id` de outro app ou de outra persona recusado sem tocar; persona sem conta no app não cai na âncora; conta lida confere pelo login da conta; teto diário por conta |
| `backend/tests/test_sessao_por_conta.py` (23.4) | na composição, com o correio registrado só por dado: a porta pede a conta do app do item; mexer no app invalida só as contas dele e o reset, todas; a tela que desmente corrige a conta do app da tela (dito ou da etapa em curso); a reobservação chama o provedor de cada conta; a rota de verificar e o canal de comandos passam o `account_id`; mover de máquina pede confirmação com a sessão do segundo app |
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
- O catálogo do design (§4.2, `dados_disponiveis`) existe como `modules/identity/domain/available_data.py`, só com
  os cinco campos de perfil e as contas; a biografia não gera variável.
- Conta única feita na onda B ([acima](#contas-e-acesso)); sessão por conta no 23.4 (`ensure_session` recebe a
  conta). Desvios: a senha do cadastro consente sem marca explícita; `invalidate_sessions_of_instance` sem `package`
  nem `todos_os_apps` ainda é a âncora (para quem não diz o app);
  `SocialService.delete_account` recusa (`anchor_account`) a conta de QUALQUER app com provedor, não só a âncora
  (item 23.10);
  `teaching.py` e `generalization.py` ainda citam o "campo Credenciais da execução"; `instagram_credentials`,
  `instagram_sessions` e `run_secrets` só leitura até migração posterior.
- `beliefs` no prompt, remoção de `personas` e da FK `persona_id`, provedor de imagem local (ADR-023): pendentes.
