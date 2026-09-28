# Persona, conta, aparelho e parque: segunda evolução

**Documento de design.** 27/09/2026. Base: commit `57f0468` (`main`; worktree `arq`, branch
`claude/arquitetura-habilidades`). Produção no `5c98735`, migração `046`.
**Estado:** proposto. Nada implementado, nenhuma migração escrita, nada implantado. Onde o texto diz **proposto**, a
coisa ainda não existe; o resto foi conferido no código do commit base.

Referências `arquivo:linha` são relativas a `backend/app/` (código), `backend/migrations/` (migrações, citadas pelo
número, ex.: `008:72`) e `frontend/src/` (painel), salvo indicação. Linhas conferidas no commit base.

**Insumos.** Seis relatórios de exploração feitos no mesmo commit (baseline visual, persona e contas, aparelho e
roteamento, credenciais e planejamento, painel, imagens), guardados no scratchpad da sessão e fora do Git; o banco
de produção foi lido em `mode=ro` (só contagens e nomes de chave). **Este documento é a versão consolidada e é ele
que vale.** Onde os relatórios divergiram, a §14 diz o que ficou e por quê.

**Prova.** Os números da §2 são medidos (leitura do código e do banco de produção em 27/09). O resto é desenho e está
`not_run`.

Leitura relacionada: [evolução arquitetural](evolucao-arquitetural.md) (§4, §5, §6, §11, §15),
[perfis e Instagram](../dominios/perfis-e-instagram.md), [parque](../dominios/parque.md), [decisões](../decisoes.md)
(ADR-001, 006, 009, 025, 029, 035, 039), [banco](../banco.md), [contrato HTTP](../api-contract.md),
[produto](../produto.md).

---

## 1. Contexto e o pedido

O dono operou o painel em 27/09 e pediu uma segunda evolução, agora do **modelo de identidade e do parque**, não da
arquitetura interna. O pedido, em treze pontos, com a seção que responde a cada um:

| # | Pedido | Onde |
|---|---|---|
| 1 | Configuração no mesmo design system das outras telas (largura, responsividade), sem ajuste pontual | §9.1, §9.2 |
| 2 | Tirar o campo "Senha para a automação": senha de conta, dados pessoais e dados de contas vinculadas fazem parte do contexto da persona e o plano os usa na etapa certa | §4 |
| 3 | Gerar uma persona COMPLETA a partir de um prompt, por IA | §6 |
| 4 | Ao criar a persona, gerar uma ou mais imagens coerentes com os atributos, com muita variação natural entre personas; modelo e interface preparados para várias imagens | §5 |
| 5 | Modelo rico da persona (nome, sobrenome, nascimento, idade derivada, gênero, cidade, local de nascimento, residência, profissão, formação, estado civil, interesses, hobbies, personalidade, comunicação, preferências, histórico, religião, posicionamento político…), migrando as existentes | §3.2, §10 (047) |
| 6 | Unificar "Autenticação" e "Contas": a credencial pertence à conta da persona em cada app; Instagram não é exceção arquitetural | §3, §4, §10 (049) |
| 7 | Hierarquia Servidor → Aparelho → Persona(s), persona N:N aparelho | §3.1, §7.1, §10 (051) |
| 8 | Visualização nas duas direções (persona → aparelhos, contas, imagens; aparelho → personas, contas, apps, servidor) | §9.4, §9.5 |
| 9 | Comandar por persona ("peça para o André fazer X") e o sistema escolher o aparelho; manter o caminho por aparelho; o texto pode dizer destinos | §7.3–§7.7 |
| 10 | Provisionar nova instância pela plataforma | §8 |
| 11 | Painel lateral do aparelho com a hierarquia | §9.3 |
| 12 | Ações rápidas e Interação manual reestruturadas | §9.3 |
| 13 | Coerência ponta a ponta, migrações validadas, dados preservados, testes | §10, §11, §13, §17 |

O que não muda: os invariantes do `CLAUDE.md` (segredo nunca em prompt, log, evento, evidência, memória, fixture ou
Git; desafio, 2FA e CAPTCHA com a pessoa, ADR-009; nenhuma evasão de detecção; mundo real só com autorização em
chat). O que muda de regra escrita está na §16 (ADR-040 substitui em parte o ADR-025).

---

## 2. ESTADO ATUAL

### 2.1 O modelo: persona, perfil, conta, vínculo e sessão

O que o dono chama de "persona" ("o André") é o **perfil** (`instagram_profiles`). A tabela `personas` guarda só a
**voz** (traços de escrita) e pertence a no máximo um perfil.

```
personas 1 ──(0..1)── instagram_profiles ──1:N── profile_accounts ──1:0..1── account_credentials ──ref──> secrets
 (voz)                  (a identidade)     ├─1:0..1─ instagram_credentials ──ref──> secrets   (só Instagram)
                                           ├─1:0..1─ instagram_sessions                       (UMA sessão por perfil)
                                           ├─1:N──── authentication_attempts
                                           ├─1:N──── device_profile_bindings   (≤1 ativo por perfil E por aparelho)
                                           └─1:N──── memory_items / social_interactions / relationship_summaries /
                                                      thread_summaries / pending_approvals / objectives.profile_id
```

| Tabela | Onde | O que importa aqui |
|---|---|---|
| `personas` | `008:16-23`, `009:6` (`persona_prompt`) | `id` (`persona-…`), `name`, `summary`, `traits` JSON |
| `instagram_profiles` | `008:25-41`; `023:30` (`offline_policy`); `036` (`policy_group_id`) | `username NOT NULL` (`008:27`) único por `lower()` (`008:46`); `first_name`, `last_name`, `birth_date`, `email`; `persona_id` com índice único parcial `ux_profiles_persona` (`009:9`): uma persona em um perfil só |
| `instagram_credentials` | `008:48-59` | PK `profile_id`; `login_identifier`, `secret_ref`, `status`, `failed_attempts`, `blocked_until`, `last_used_at` |
| `instagram_sessions` | `008:76-84`; `034` (`unknown_streak`) | PK `profile_id`: **uma sessão por perfil**, `instance_id` é coluna comum |
| `device_profile_bindings` | `008:63-73`; `023:26-33` | `idx_binding_profile_ativo (profile_id) WHERE active=1` e `idx_binding_device_ativo (instance_id) WHERE active=1` (`008:72-73`): o 1:1 |
| `profile_accounts` | `037:16-31` | `(profile_id, app_id)` único (`037:31`); `handle`; `session_status` é **cópia** feita uma vez na carga (`037:45-51`) |
| `account_credentials` | `037:36-42` | `account_id` PK, `login_identifier`, `secret_ref`, `key_id`; sem estado, falhas, bloqueio ou consentimento |
| `run_secrets` | `040` | credencial por execução (ADR-025), apagada com a execução |

Código que sustenta o modelo: `social/repository.py` (`create_profile:48`, `credential_row:167`, `binding_row:200`,
`profile_id_for_instance:203`, `bind:218`, `session_row:304`, `set_session:307` com `ON CONFLICT(profile_id)`,
`profile_dto:364`, `persona_owner:447`); `social/service.py` (`create_profile:123`, `set_credential:181`,
`_rebind:214`, `create_persona:280`, `_account_dto:753`, `set_account_credential:824`); `PersonaTraits`
(`models.py:576-600`, `extra="forbid"` em `:579`) com 15 chaves de voz (`PERSONA_VOICE_TRAITS`, `:609`) e 3 visuais
(`appearance`, `visual_style`, `photo_scenario`, `:598-600`) que nunca vão ao prompt; `PersonaDTO/Create/Patch`
(`:630-661`); `ProfileCreate` (`modules/identity/presentation/schemas.py:22`, `username` obrigatório, `password`
`SecretStr`).

### 2.2 As duas fontes de verdade da sessão

- `profile_accounts.session_status` é a cópia da carga da 037: em produção, 8 de 8 `session_ready`.
  `instagram_sessions` diz 7 `session_ready` e 1 `unknown`, e 5 desses perfis estão `blocked` desde o ADR-029: são
  **sessões fantasmas** (sem vínculo ativo, sem aparelho).
- `apps_overview.py:80-81` conta "contas prontas" pela cópia; `:103` lista por ela.
- O login lê `instagram_profiles.username` (`integrations/instagram/authentication.py:98`) e
  `instagram_credentials` (`:163`), nunca `profile_accounts.handle`, que é editável pela aba Contas
  (`social/service.py:797-810`).
- `modules/identity/domain/resources.py` tem **dois vocabulários** de sessão (`SessionStatus:51`,
  `AccountSessionStatus:61`), um por tabela.
- As duas abas do perfil gravam credenciais diferentes para a mesma senha: Autenticação manda só `{password}`
  (`ProfileDetail.tsx:845`; o login continua sendo o e-mail), Contas manda `login_identifier: c.handle`
  (`ProfileAccounts.tsx:226` → `social/service.py:831` → `:185`), e o login por `@usuário` falha
  (`service.py:145-146`). Em produção as 8 credenciais usam e-mail: trocar a senha pela aba Contas estraga o login.
- `account_credentials` não tem leitor na automação: `SecretStore.get_secret` é chamado só por
  `authentication.py:445`, `executor.py:226` (via `run_secrets`) e `security/rekey.py`, que ainda copia `key_id` só
  para `instagram_credentials` (`rekey.py:87-91`).

### 2.3 O 1:1 nas três travas

1. **Índices** `008:72-73`. `bind` (`social/repository.py:218-235`) desvincula o próprio perfil (`:227`) e **toma o
   aparelho de outro perfil** (`:228-230`) sem perguntar.
2. **Objetivo `<run>:<instance>`** com `UNIQUE(run_id, instance_id)` (`001:74`; `taskqueue/repository.py:230`): uma
   execução não tem dois objetivos no mesmo aparelho, logo duas personas do mesmo aparelho não cabem numa execução.
3. **Porta de sessão deduz o perfil pelo aparelho:** `AppState._session_gate(rt, package)` (`state.py:808-870`)
   chama `profile_id_for_instance(rt.id)` (`:825`) e não recebe o objetivo; `Scheduler._portas_do_app` repassa só
   `(rt, pacote)` (`taskqueue/scheduler.py:367`); a assinatura está em `:109-110`. O mesmo em
   `_sessao_desmentida` (`state.py:688`), `_reobservar_apos_intervencao` (`:728`), `TrainingRecorder` (`:314`) e
   `modules/identity/infrastructure/account_session.py:57-59` (`db.one`: com N devolveria o primeiro em silêncio).

E a quarta, implícita: `instagram_sessions` tem PK `profile_id`, então a sessão não sabe distinguir aparelhos.

`RunCreate.profile_ids` já existe (`models.py:1167`) e, quando vem, **substitui** `instance_ids` sem aviso
(`taskqueue/service.py:115-116`) pelo único aparelho vinculado (`_instances_of:452-462`, 409 `no_binding`). O painel
não expõe o campo (`api/types.ts:1177-1192`). O objetivo já fotografa `profile_id` (`010`; `service.py:498`) e
`Target` já é (aparelho, perfil) (`shared/resources.py:66-70`). Nada extrai destinos do texto.

### 2.4 Credenciais por execução (ADR-025)

| Passo | Onde |
|---|---|
| Campo "Senha para a automação", estado só em memória, nome fixo `senha` | `features/command/CommandPanel.tsx:113-115`, `:182`, `:273-288` |
| `RunCreate.credentials` (≤ 8 pares, `SecretStr`) e `consent_credentials` | `models.py:1178-1181` |
| Recusa `credencial_no_comando`; cofre antes da execução; `add_run_secret`; consentimento 409 | `taskqueue/service.py:104-166`, `_exigir_consentimento:171`, `_guardar_no_cofre:193` |
| Planejador e ator veem só os nomes; o planejador por catálogo não vê nenhum | `planning/prompts.py:282-287`, `:290-298` (sem `secret_names`), `:309-311` |
| `type_secret` → `preenchedor` com três travas: só campo de senha, só o app da etapa, no navegador só o host de uma URL do comando | `taskqueue/executor.py:184-236`, `_conferir_destino:238`, `urls_da_pessoa:64` |
| `tem_credencial` decide se tela de senha para a execução | `executor.py:71-86`, `:783` |
| Fim da execução apaga a linha **e o segredo**: `DELETE FROM secrets WHERE ref IN (SELECT secret_ref FROM run_secrets …)` | `taskqueue/repository.py:168-173`, `:207-208` |

Consequência estrutural: se um dia `run_secrets` apontasse para o segredo de uma conta, terminar a execução apagaria a
senha da conta. E a "conta de portal" da execução `22d65f` (Chrome, site da CETESB) não tem onde morar:
`profile_accounts` é única por (perfil, app) e não tem campo de site.

### 2.5 O que a IA recebe da persona

| Papel | Persona | Dados pessoais | Credencial |
|---|---|---|---|
| `plan` (`PlanRequest`, `planning/provider.py:64-73`) | nada | só `account_label` (`prompts.py:280`) | só nomes, no plano livre |
| `decide`/`verify` (`StepContext`, `:77-96`) | nada | só `parameters` | só nomes |
| `social` (`SocialRequest`, `:142`) | só a voz: `_persona_block` (`social/context.py:98`), 15 traços, `persona_prompt`, **sem `sem_marcacao`** | não | nunca |

`social_user_text` diz "Você é @{username}" (`prompts.py:228`) em qualquer app. Não existe "cidade" em migração
nenhuma. `birth_date` está vazio em 8 de 8 perfis.

### 2.6 As telas e a baseline visual (27/09, produção `5c98735`, 1440×900 e 375×812)

- **Configuração** (`features/settings/SettingsPage.tsx:44`): a única página com `appStyles.pageNarrow`
  (`App.module.css:54-56`, `max-width: 1280px` contra 2400 das outras), e um `Card` único envolvendo as abas
  (`:52-53`). Em 1440 px sobra ~30 % da tela; em 375 px a barra de status estoura e a barra "Salvar limites" corta o
  botão. Grades com mínimos fixos (`.appList minmax(360px)`, `.limitGroups minmax(420px)`, `.aiGrid` 2 colunas), a
  tabela "Por função" sem contêiner de rolagem. Todas as regras de tela estreita são por **viewport**; o Foco encolhe
  o `main` (`--focus-panel-w: clamp(600px, 44vw, 920px)`, `styles/tokens.css:104`; `.main` com `overflow-x: hidden`,
  `App.module.css:33`), então em 1024 px com Foco aberto o `main` fica com ~400 px e nada dispara. A única container
  query do projeto está em `features/devices/Devices.module.css:5-6, 46-56`. Não existe componente de página:
  `components/` tem Card, Tabs, Field…, mas não `Page`, `PageHeader`, `Section`, `TableWrap`.
- **Painel:** o comando no topo com o campo de senha; a grade de aparelhos com um `@` por cartão
  (`features/devices/DeviceGrid.tsx:49`, `Map` 1:1). O **Foco** (`features/focus/FocusPanel.tsx`) tem a tela à
  esquerda e uma coluna fixa de 300 px (`features/focus/Focus.module.css:98-102`) com oito blocos do mesmo peso:
  Interação manual (`:235`), Ações rápidas (`:276`; "Resetar dados…" em `:328`, na mesma grade de "Abrir app"),
  `OperationalContextCard`, comandos recentes, detalhes técnicos, hierarquia. Sem hierarquia visual, sem
  personas/contas por app.
- **Perfis:** lista sem o invólucro comum (`features/profiles/ProfilesPage.tsx:110`); cadastro exige senha e
  aparelho; detalhe com 11 abas (`ProfileDetail.tsx:68-80`; o comentário em `:45` ainda diz "nove"): Contas e
  Autenticação falam da mesma senha; Aparelho e Autenticação repetem o contexto operacional; a sessão aparece em
  quatro lugares. A guia Aparelho não permite trocar de aparelho. Avatar só por `GET …/avatar`, sem upload nem
  galeria.

### 2.7 Dados de produção (leitura em 27/09)

- **Personas:** 14 (todas com `persona_prompt`, `summary`, `traits`; voz 14/14, visuais 8/14). 3 vinculadas
  (`lucas`, `bruno`, `andre`), 5 desvinculadas pelo ADR-029 em 27/09, 6 criadas em 23/09 sem perfil.
- **Perfis:** 8 (`active` 3, `blocked` 5); nome, sobrenome e e-mail 8/8; `birth_date` 0/8; `persona_id` 3/8.
- **Contas:** `profile_accounts` 8 (todas Instagram, `session_status` `session_ready` 8/8, a cópia);
  `account_credentials` **0**; `instagram_credentials` 8 (`login_identifier` = e-mail 8/8); `secrets` 8.
- **Vínculos:** 8 linhas, 3 ativas (android-01, 03, 06; `reason='cadastro'`, localidade NULL); 5 inativas.
  Nenhum aparelho com mais de um perfil, nenhum perfil em mais de um aparelho (124 objetivos com perfil, 1 aparelho
  cada). `instagram_sessions`: 7 `session_ready` / 1 `unknown`; 5 fantasmas.
- **Parque:** 15 instâncias, todas `origin` NULL (config), `hosted_by` central; central 9 (01–08 e a loja 11),
  `worker-lan-01` 6 (09, 10, 12–15, por túnel). `app_id` Instagram em 14. Apps: `chrome`,
  `configura-es-do-android`, `instagram`, `qa-messenger`. `worker_limits`: só `boot_parallelism=2` no notebook;
  **sem limite de quantidade de aparelhos nem de disco**.
- **Execuções:** 192; 48 (25 %) com mais de um aparelho.
- **Imagens:** 8 `data/avatars/ig-<id>.jpg` (360×360, sem EXIF), copiados à mão em 18/09 (commit `4628c20`),
  servidos por `s.avatares` (`state.py:206`) e `_servir_do_storage` (`api.py`). Os 5 perfis bloqueados ficaram com as
  fotos; as personas deles ficaram órfãs.
- **GPU do central:** RTX 2000 Ada Laptop, **8 GB** (não 16). O saldo da API Anthropic não compra imagem.

---

## 3. MODELO ALVO

### 3.1 A hierarquia

```
Servidor (workers)                       central · worker-lan-01
  └── Aparelho (instances)               android-01 … android-NN; origin config | dynamic
        └── Vínculo (device_profile_bindings, N:N, por app, com principal)
              └── Persona (instagram_profiles = a raiz)
                    ├── Contas (profile_accounts: app, handle, host)      ── credencial (account_credentials, cofre)
                    │     └── Sessão por aparelho (account_sessions: conta × aparelho)
                    ├── Imagens (persona_images: principal + variações, receita, proveniência)
                    ├── Memória, interações, aprovações, política, execuções (como hoje, por profile_id)
                    └── Voz (traits), biografia, visual, geração
```

Leituras nas duas direções: persona → vínculos → aparelhos → servidor; aparelho → vínculos → personas → contas do
app → sessão neste aparelho. A conta é a ponte entre persona e app; a sessão é a ponte entre conta e aparelho.

### 3.2 Entidades e value objects

**Persona** (raiz; tabela `instagram_profiles`, nome mantido, [decisão 1](#14-decisões-tomadas)):

| Grupo | Campos | Forma |
|---|---|---|
| Identidade estável (colunas) | `id`, `first_name`, `last_name`, `display_name`, `birth_date` (ISO), **`gender`**, **`locale`** (padrão `pt-BR`), `status`, `policy_group_id`, `offline_policy` | colunas; `birth_date` existente |
| `username` (legado) | handle da conta Instagram de cadastro; **`''` = persona sem conta** (§10, 047) | coluna mantida `NOT NULL`, índice único parcial |
| **`biography`** JSON (`PersonaBiography`, `schema_version: 1`) | `origin {birthplace, hometown}`, `home {city, state, country, residence}`, `work {profession, education[], employer}`, `life {marital_status, children, history[]}`, `beliefs {religion, politics}`, `tastes {hobbies[], preferences[], dislikes[]}` | Pydantic `extra="forbid"` por seção, PATCH por seção com mescla no servidor |
| **`traits`** JSON (só voz) | as 15 chaves de `PERSONA_VOICE_TRAITS` (`models.py:609-616`), inclusive `interests` (**única casa dos interesses**: `voice_gaps` e `_persona_block` continuam iguais) | `PersonaTraits` sem os três visuais |
| **`visual`** JSON (`PersonaVisual`) | `appearance`, `visual_style`, `photo_scenario` (migrados de `traits`), `palette`, `age_presentation`, `gender_presentation` | `extra="forbid"` |
| `summary`, `persona_prompt` | como hoje, na linha do perfil | texto |
| **`generation`** JSON | `origin` (`manual` ou `ai`), `prompt`, `model`, `provider`, `usd`, `at`, `enriched_at` | proveniência |
| `persona_id` (mantido) | continua apontando para a linha de `personas` dobrada (FK `008:33` e `ux_profiles_persona` intactos): é o marcador da dobra, a chave do apelido `/api/personas/{persona-…}` e o que permite desfazer | coluna existente |
| Derivado | **idade** (`birth_date` → hoje), nunca gravada; `name` = `display_name` ou `first_name + last_name` | no DTO |

**Conta** (`profile_accounts` + `account_credentials`, uma entidade na API):

| Campo | Onde | Novo |
|---|---|---|
| `id`, `profile_id`, `app_id`, `handle`, `status`, `notes` | `profile_accounts` | — |
| **`host`** | `profile_accounts` | conta de portal/site num app navegador (ex.: app `chrome`, `host='mtr.cetesb.sp.gov.br'`); unicidade `(profile_id, app_id, COALESCE(host,''))` |
| `login_identifier`, `secret_ref`, `key_id`, `updated_at` | `account_credentials` | — |
| **`status`** (`active` ou `invalid`), **`failed_attempts`**, **`blocked_until`**, **`created_at`**, **`last_used_at`** | `account_credentials` | os campos que só `instagram_credentials` tinha |
| **`consent_at`**, **`consent_by`** | `account_credentials` | "autorizo a automação a digitar esta senha" |
| `session_status`, `session_detail`, `session_verified_at` | `profile_accounts` | passam a **derivados** de `account_sessions` (a cópia deixa de ser escrita) |

**Sessão** (`account_sessions`, nova, 049): `account_id`, `instance_id`, `status`, `observed_handle`,
`verified_at`, `detail`, `unknown_streak`, `updated_at`, `PK (account_id, instance_id)`. Vocabulário **único**:
`unknown | session_ready | logged_out | auth_required | auth_challenge | wrong_account | needs_person`, a união de
`SessionStatus` (`resources.py:51-58`: `unknown`, `auth_required`, `auth_challenge`, `wrong_account`,
`session_ready`) com `AccountSessionStatus` (`:61-67`: `unknown`, `session_ready`, `logged_out`, `needs_person`),
num só `SessionStatus`. "Sessão é cache do observado" (`008:75`) continua
valendo.

**Vínculo** (`device_profile_bindings`, 051): `profile_id`, `instance_id`, **`app_id`** (NULL = apps sem conta
gerenciada), **`is_primary`**, `active`, `bound_at`, `unbound_at`, `reason`, `worker_id`, `physical_id`,
`locality_at` (023).

**Imagem** (`persona_images`, 048): `id`, `profile_id`, `storage_key`, `original_key`, `content_type`, `width`,
`height`, `bytes`, `sha256`, `kind` (`portrait | half_body | full_body | lifestyle`), `is_primary`, `status`
(`pending | ready | failed | refused`), `source` (`generated | upload | imported_legacy`), `spec` JSON (receita, §5.3),
`prompt_sha256`, `provider`, `model`, `seed`, `provider_seed`, `provider_request_id`, `cost_usd`, `error`,
`parent_id`, `created_at`.

**Aparelho** ganha `android_overrides` JSON (050) para instâncias criadas pela plataforma; **Servidor** ganha
`worker_limits.max_devices` (050).

**Value objects novos** (além de `Handle`, `SecretRef`, `SessionState` da §5 do design anterior):

| VO | Forma | Onde |
|---|---|---|
| `LogicalName` | `^[a-z][a-z0-9_]{0,59}$`; `perfil_<campo>` e `conta_<app>_<campo>` (§4.2) | `modules/identity/domain/dados.py` |
| `DadoDisponivel` | (`name`, `label`, `kind` = `texto` ou `segredo`, `app_id`, `host`) | idem |
| `RunTarget` | (`profile_id`, `instance_ids[]`, `app_id`) | `models.py` |
| `DevicePolicy` | `one`, `primary` ou `all` | idem |
| `AlvoResolvido` | (`instance_id`, `profile_id`, `app_id`, `origem` = `ui`, `texto`, `vinculo` ou `balanceamento`) | `modules/execution/domain/alvos.py` |
| `PersonaImageSpec` | eixos amostrados + prompt + `SPEC_VERSION` (§5.3) | `modules/identity/domain/persona_image.py` |
| `Age` | derivada de `birth_date`; `< 18` recusa geração de imagem e de persona | `modules/identity/domain/persona.py` |

### 3.3 Agregado Persona e invariantes

| Invariante | Como se garante | Onde hoje |
|---|---|---|
| Uma conta por (persona, app, host) | `ux_profile_accounts_app_host` (049) | `037:31` sem host |
| Credencial pertence à conta; sem conta não há credencial | `account_credentials.account_id` PK/FK; `instagram_credentials` só leitura até sair | duas tabelas |
| Sessão é por (conta, aparelho) | PK de `account_sessions` | PK `profile_id` |
| Persona fora de `active` não recebe tarefa; desafio → `blocked` sem reativação automática (ADR-029) | `_session_gate`; `session_rules.py::bloquear_por_desafio` | igual |
| Um vínculo ativo por (persona, aparelho, app) | `ux_binding_par_ativo` (051) | 1:1 |
| No mesmo aparelho, uma conta por app (D2-a) | `ux_binding_conta_do_app_no_aparelho` (051) | implícito no 1:1 |
| Uma persona tem no máximo um aparelho principal | `ux_binding_principal` (051) | — |
| Uma imagem principal por persona; sem duplicata de conteúdo | `ux_persona_images_primaria`, `ux_persona_images_sha` (048) | — |
| Idade nunca gravada; menor de idade nunca gerado | validação no domínio | — |
| Segredo nunca é memorizado, prompt nunca leva valor | `MemoryStore.remember`; `type_secret` só nome | igual |
| A ref da conta nunca entra em `run_secrets` | `RunService` deixa de escrever em `run_secrets` (§4) | — |

### 3.4 O que é dado, o que é segredo, o que vai ao modelo

| Dado | Classe | Vai ao `plan`/`decide` | Vai ao `social` | Como chega ao aparelho |
|---|---|---|---|---|
| nome, sobrenome, idade, gênero, cidade, profissão, formação, hobbies, interesses | dado pessoal não sigiloso | como **lista de nomes lógicos** (`{perfil_nome}`…) e, quando o plano os usa, resolvidos em `parameters` por aparelho | linhas curtas com `sem_marcacao` (§4.6) | `type_text` (aparece na hierarquia, como qualquer parâmetro) |
| e-mail, `login_identifier`, handle | identificador de conta | como nome lógico (`{conta_instagram_usuario}`) | handle do app da etapa no lugar de `@username` | `type_text` |
| **religião, posicionamento político** (`biography.beliefs`) | guardado, **nunca enviado** até decisão do dono (§15) | **não entra no catálogo** (nem nome lógico) | não | — |
| `life.history`, `preferences`, `dislikes` | contexto longo | não | só se couber no teto de tokens (`recall`) | — |
| senha de conta | **segredo** | só o **nome** (`conta_<app>_senha`) | nunca | só `type_secret` → canal sensível, com consentimento |
| `appearance`, `visual_style`, `photo_scenario`, `palette` | dado da receita de imagem | não | não | vai ao provedor de imagem, sem nome |
| imagem principal | dado | não | não | vai ao provedor de imagem como referência (§5), só para a 2ª imagem em diante |

---

## 4. CREDENCIAIS E DADOS NO PLANO

Opção A ([decisão 5](#14-decisões-tomadas)): `RunCreate.credentials`, `consent_credentials` e o campo do painel
**saem**. A fonte única é a credencial da conta da persona, no cofre, referenciada por **nome lógico**, nunca por
valor.

### 4.1 Fluxo ponta a ponta

1. **Cadastro.** A pessoa guarda a senha na guia "Contas e acesso" da persona (`PUT
   /api/personas/{id}/accounts/{aid}/credential`), marcando "autorizo a automação a digitar esta senha"
   (`consent_at`, `consent_by` = sessão do painel). Sem a marca, a senha fica guardada e **não é digitada**.
2. **Criação da execução.** `RunService.create` resolve os alvos (§7) e, para cada perfil alvo, monta
   `dados_disponiveis(profile_id)` (§4.2). A recusa `credencial_no_comando` (`service.py:109`) continua: senha no texto
   nunca. O 409 `consentimento_de_credencial` por execução **sai**; o consentimento é por conta.
3. **Planejamento.** O planejador (livre e por catálogo) recebe a **lista** de dados disponíveis comuns a todos os
   alvos (nome, rótulo, tipo, sigiloso, app), nunca valores. Dado não sigiloso vira variável `{perfil_email}` no
   parâmetro da etapa; segredo vira etapa com `type_secret(name="conta_chrome_senha")`. Dado que falta em algum
   perfil alvo é recusado no pré-voo (`_exigir_pre_voo`) com a lista do que falta e para quem.
4. **Materialização.** `Repository.materialize` (`taskqueue/repository.py:225-244`) já resolve `{instance_id}`,
   `{run_id}` e `{account_label}` por aparelho (`:233`); passa a resolver também os nomes lógicos não sigilosos pelo
   `objective.profile_id`. O mesmo em `:698-699` (parâmetros do ator).
5. **Execução.** `type_secret` chama `preenchedor`, que deixa de receber `run_secret_refs` e passa a receber um
   **resolvedor por (perfil do objetivo, app da etapa)**: `segredos_de(profile_id, app_package, host)` devolve a ref da
   conta daquele app (ou daquele host, no navegador). As três travas ficam (`executor.py:207-213`, `:240-254`); a de
   site passa a usar o `host` da conta (§4.4). Sem consentimento na conta → a etapa vai a `waiting_user` com motivo
   `consentimento_pendente`; a pessoa marca o consentimento na conta e retoma.
6. **Instagram.** App com `SessionProvider` continua com login **determinístico** fora do `type_secret`
   (`InstagramAuthenticator._login`, `authentication.py:162`): a porta de sessão autentica antes do despacho, lendo a
   credencial da conta (§10, 049). O planejador não recebe `conta_instagram_senha` como nome digitável: para apps com
   provedor o catálogo marca `managed: true` e o nome não é oferecido ao `type_secret`.
7. **Fim.** Nada é apagado: a credencial é da conta. `run_secrets` deixa de ter escritor (`add_run_secret` não é mais
   chamado; `run_secret_refs` devolve vazio) e sai numa migração posterior.

### 4.2 Nomes lógicos e o catálogo

`dados_disponiveis(profile_id) -> list[DadoDisponivel]` é função pura sobre as linhas da persona e das contas, em
`modules/identity/domain/dados.py` (**proposto**), sem ler valor de segredo.

| Nome | Origem | Tipo |
|---|---|---|
| `perfil_nome`, `perfil_sobrenome`, `perfil_nome_exibicao`, `perfil_nascimento`, `perfil_idade`, `perfil_genero`, `perfil_email` | colunas da persona (`perfil_idade` derivada) | texto |
| `perfil_cidade`, `perfil_estado`, `perfil_profissao`, `perfil_formacao`, `perfil_estado_civil` | `biography.home`, `.work`, `.life` | texto |
| `conta_<app>_usuario` | `profile_accounts.handle` ou `account_credentials.login_identifier` | texto |
| `conta_<app>_senha` | `account_credentials.secret_ref` | **segredo** |
| `conta_<app>_<host>_usuario` / `_senha` | conta com `host` (slug: pontos e hífens viram `_`, truncado a 60; colisão recebe sufixo `_2`) | texto / segredo |

`<app>` é `apps.id` normalizado para `[a-z0-9_]` (`configura-es-do-android` → `configura_es_do_android`). Os nomes
casam com `TEMPLATE_RE` (`taskqueue/recipes.py:38`) e com `SENSITIVE_PARAM` (`:28`, que já casa `senha`); `type_secret`
já está em `UNSAFE_TO_REPLAY` (`:34`), então receita nunca repete uma digitação de segredo. **`biography.beliefs` não
gera nome nenhum.** `PERSONA_BIO_FIELDS` fica ao lado de `PERSONA_VOICE_TRAITS` e é a lista fechada do que vira
variável.

### 4.3 Consentimento

- Por conta, na tabela: `consent_at`, `consent_by`. Exigido antes de digitar por `type_secret` **e**, a partir da WB,
  pelo `SessionProvider` (uniforme).
- As 8 credenciais do Instagram existentes recebem `consent_at = updated_at`, `consent_by = 'migração 049'` na carga:
  foram cadastradas pelo dono no portal justamente para o "Conectar" automático (o caminho do ADR-009, digitação pelo
  canal sensível), e recusá-las pararia o login de produção. Credencial nova exige a marca explícita. Registrado como
  decisão (§14, decisão 5).
- O aviso de consentimento que hoje diz que o texto do comando vai ao provedor (`service.py:189-190`) vira texto
  fixo da tela de comando: continua verdade e não depende de credencial.

### 4.4 Travas

1. Só campo de senha (`executor.py:207-213`); nome desconhecido → `DriverError(effect_possible=False)`.
2. Só o app da etapa (`_conferir_destino`, `:240-243`): a senha da conta do app X só no pacote de X.
3. No navegador, só o site da conta: `hosts` = URLs escritas no comando (`urls_da_pessoa`, `:64`) **∪ `host` das contas
   do perfil do objetivo no app navegador**. O mesmo conjunto alimenta `OpenUrl.allowed_urls`
   (`automation/tools.py:100`): sem isto o caso `22d65f` regrediria, porque o comando "entre no portal" não traria a
   URL.
4. A ref da conta **nunca** entra em `run_secrets` (`drop_run_secrets` apagaria a senha da conta).
5. App com `SessionProvider` fora do `type_secret` (§4.1, passo 6).
6. `tem_credencial` em `pede_intervencao_humana` (`executor.py:71-86`) passa a valer **por app e etapa**: existe
   credencial consentida da conta do perfil do objetivo no app da etapa.
7. Contexto social sem caminho até o cofre: `test_social_memory.py:224-229` continua valendo.

### 4.5 O que muda, arquivo a arquivo

| Onde | Mudança |
|---|---|
| `models.py:1178-1181` | saem `credentials` e `consent_credentials`; `RunCreate` ganha os campos da §7.3 |
| `taskqueue/service.py:138-166`, `:171-209` | saem `_exigir_consentimento`, `_guardar_no_cofre`, `_descartar`, a ligação `add_run_secret`; entra `dados_disponiveis` por alvo e a recusa de pré-voo por dado ausente |
| `planning/provider.py:64-96` | `PlanRequest.secret_names` → `available_data: list[DadoDisponivel]`; idem `StepContext` |
| `planning/prompts.py:70-73, 129-132, 282-287, 290-298, 309-311` | bloco "Dados da persona disponíveis (nomes; segredos só por `type_secret`)" nos três prompts, inclusive no de catálogo |
| `automation/tools.py:90-97` | docstring de `TypeSecret`: nome lógico da conta |
| `taskqueue/executor.py:184-236`, `:660-672`, `:783`, `:954-964` | resolvedor por (perfil, app, host); `tem_credencial` por etapa |
| `taskqueue/repository.py:144-173`, `:207-208` | `run_secrets` sem escrita; `drop_run_secrets` vira inócuo até a migração que remove a tabela |
| `features/command/CommandPanel.tsx:113-115, 169, 182, 186-191, 193, 273-288` | sai o campo e o estado; a mensagem de `:169` passa a apontar a guia "Contas e acesso" da persona |
| `api/types.ts:1189-1191` | saem `credentials`, `consent_credentials` |
| `social/sessao_gate.py:79`, `features/profiles/sessionGate.ts:15` | o texto deixa de citar "aba Autenticação" |
| `CLAUDE.md` (invariante 1), `.claude/rules/segredos-e-mundo-real.md`, `docs/api-contract.md:1231-1251`, `docs/skill-dsl.md:72-74`, `docs/dominios/perfis-e-instagram.md:17-36`, `docs/roadmap.md:46` | no mesmo commit da WB |
| Testes | `test_credenciais_da_execucao.py` inteiro vira `test_credenciais_da_conta.py`; `test_anthropic_provider.py:126` (17 ferramentas, inalterado); `history.test.ts:38-51`; `_em_lugar_nenhum` estendido a `objectives`, `steps`, `evidence`; fixture `E_SECRET_PARAMETER.yaml` e `skill-dsl.v1alpha1.json:377, 585` |

### 4.6 O que muda no prompt social

`_persona_block` (`social/context.py:98`) ganha `sem_marcacao` em todos os campos (hoje só o resto do contexto o usa,
`:101-122`) e as linhas curtas da biografia: nome, idade, cidade, profissão, formação, hobbies (interesses já vêm de
`traits`). "Você é @{username}" (`prompts.py:228`) passa a usar o handle da **conta do app da etapa**. Religião e
política **não** entram. Teto de tokens: a biografia inteira não vai; `life.history` entra pelo `recall` da memória
só se o dono decidir gravar histórico como `memory_items source='persona'`.

---

## 5. IMAGENS

### 5.1 Porta

`modules/identity/application/ports.py` (**proposto**):

```python
class ImageGenerator(Protocol):
    name: str                      # simulated | openai_images
    model: str
    simulated: bool
    sends_data_externally: bool
    async def generate(self, spec: PersonaImageSpec, *, reference: bytes | None = None) -> GeneratedImage: ...

@dataclass(frozen=True)
class GeneratedImage:
    data: bytes; mime: str; width: int; height: int
    provider_request_id: str | None; provider_seed: str | None; usd: float; ms: int
```

A porta fica fora dos cinco papéis de IA: `AI_ROLES` (`config.py:22`) não muda e `AIProvider`
(`planning/provider.py:172`) não ganha método de imagem.

### 5.2 Adaptadores

Em `modules/identity/adapters/` (**proposto**; pasta nova), a única onde `PIL` e `httpx` passam no teste de
arquitetura sem editar `ONDE_A_INFRA_MORA` (`tests/test_arquitetura.py:208-220`; a exceção `app.modules.*.adapters`
está em `:292`; `infrastructure` **não** está nela).

| Adaptador | Arquivo | Comportamento | Prova |
|---|---|---|---|
| Simulado | `adapters/simulated_images.py` | Pillow determinístico por `seed`: degradê, silhueta, iniciais da receita, carimbo "SIMULADO"; sem rede; sem custo (`usd=0`); `simulated=True` | `simulated`: hash de `Image.tobytes()` estável por seed |
| OpenAI Images | `adapters/openai_images.py` | `POST /v1/images/generations` (`gpt-image-1-mini`, `quality` da config); com `reference`, `/v1/images/edits` para manter o rosto; chave por `EnvSettings.image_api_key: SecretStr` (alias `IMAGE_API_KEY`), nunca `os.environ` (`openai_provider.py:73` é o modelo a **não** copiar); `_raise_for_status` como `openai_provider.py:171`; `content_filter` → `status='refused'` | `simulated` com `httpx.MockTransport` (como `test_openai_provider.py`); real `not_run` até chave e autorização |
| ComfyUI local | — | **adiado**: GPU de 8 GB dividida com emuladores e Ollama; ADR-023 | — |

Regras: provedor pago sem chave → 409 `image_not_configured`, **nunca** cai no simulado (`planning/routing.py:10-12`,
"sem fallback pago silencioso"); sem provedor configurado, `provider: simulated` responde e a imagem aparece com o
selo "simulado" no painel e `source='generated'`, `provider='simulated'` na linha.

### 5.3 Receita visual (`PersonaImageSpec`)

`modules/identity/domain/persona_image.py` (**proposto**, só stdlib). Entrada: `visual` (§3.2), `interests` de
`traits`, profissão e cidade de `biography`, idade derivada (< 18 recusa), `gender`, `locale`. Amostragem por
`random.Random(seed)`, `seed = int(sha256(f"{profile_id}:{indice}:{SPEC_VERSION}").hexdigest()[:8], 16) & 0x7FFFFFFF`
(31 bits, cabe em `INTEGER` dos dois dialetos).

| Eixo | Valores amostrados |
|---|---|
| câmera | frontal de celular, traseira recente, celular antigo, compacta, DSLR amadora |
| época | recente, 2–3 anos, ~2012 |
| luz | janela, fim de tarde, flash à noite, fluorescente, nublado |
| ambiente | derivado de `photo_scenario` e `interests` (casa, rua, trabalho, natureza, evento) |
| enquadramento | close, busto, meio corpo, corpo inteiro, selfie |
| pose e expressão | 6 poses × 5 expressões |
| produção | casual, arrumada, produzida |
| proporção | 1:1, 4:5, 3:4, 9:16 |
| pós-processamento | JPEG 55–92, ruído σ 0–4, desfoque 0–0,8, redução e reampliação, deslocamento do recorte |

- **Imagem 0 é a principal:** busto ou close, rosto visível, 1:1, produção "casual" ou "arrumada".
- **Imagens 1+** usam a principal como `reference` para manter o rosto; sem principal `ready`, a geração de variação é
  recusada (409 `sem_imagem_principal`).
- Prompt determinístico em inglês montado da receita: "adult fictional person", traços de `appearance`, cena, luz,
  câmera, "no text, no logo, no watermark"; **nunca** o nome da persona, memória, credenciais ou telas. Guardam-se a
  receita inteira, `prompt_sha256` e `bytes_sha256`.
- Pós-processamento (Pillow, no adaptador) só para variação: recorte e redimensionamento para a proporção, JPEG
  variável, ruído e desfoque leves. **Não** se grava EXIF falso nem se remove proveniência de propósito; o original do
  provedor fica em `original_key` (C2PA preservado).

### 5.4 Armazenamento

Chaves `personas/<profile_id>/<image_id>.jpg` e `.orig.<ext>` em `s.avatares` (`state.py:206`; `Storage` em
`storage.py:63-78`, chave válida por `:41`; `put_async` `:243`). O avatar do perfil
(`GET /api/instagram/profiles/{id}/avatar`, `api.py:731`) passa a servir a imagem principal; sem principal, o
`avatars/<id>.jpg` antigo. Os 8 avatares atuais viram linhas `source='imported_legacy'`, `is_primary=1`, por um passo
de partida idempotente (`AppState.start`, **proposto**: `importar_avatares_legados`), porque migração SQL não lê
disco. Upload manual: `POST /api/personas/{id}/images/upload` com corpo cru `application/octet-stream`, o padrão de
`POST /releases/upload` (`api.py:1242`).

### 5.5 Custo

- `ai_calls` ganha a coluna `usd REAL` (048) e a linha da imagem entra com `role='image'`, `provider`, `model`, zero
  tokens e `usd` preenchido. `costs.spent_usd` (`planning/costs.py:64-83`) soma `usd` quando não nulo e continua
  ignorando `provider='simulated'`. `role='image'` é texto livre em `ai_calls` (`003:10`) e fica **fora** de `AI_ROLES`.
- Teto: antes de gerar, o serviço consulta `spent_today_usd` contra `ai_max_usd_per_day` (a conta de
  `routing.py:150-170`) e recusa 409 `teto_diario`.
- Preço por imagem em `ai.image.price_per_image` (`low 0,005 · medium 0,011 · high 0,036` para o mini, em US$; retrato
  1024×1536 custa ~1,4×). 14 personas × 1 imagem em `medium` ≈ US$ 0,15; × 3 ≈ US$ 0,46. Variação com referência custa
  também a entrada da imagem. Todos os valores são da tabela pública do provedor em 27/09 e são estimativa até a
  primeira chamada real.

### 5.6 Configuração e status

```yaml
ai:
  image:
    provider: simulated          # simulated | <nome em ai.providers com kind: openai_images>
    model: gpt-image-1-mini
    quality: medium
    per_persona: 1               # geradas ao criar a persona
    on_create: true              # o dono disse que a imagem é obrigatória
    price_per_image: {low: 0.005, medium: 0.011, high: 0.036}
```

`AiStatus` (`models.py:1464`) ganha `image: {provider, model, configured, simulated, sends_data_externally}`, montado
em `AppState.ai_status` (`state.py:1995`) e mostrado na seção IA de Configuração. `GET /api/ai` continua sendo a
forma de saber se a chave existe, sem ler o `.env`.

### 5.7 Rotas e eventos (WA)

| Rota | Efeito |
|---|---|
| `GET /api/personas/{id}/images` | lista (principal primeiro) |
| `GET /api/personas/{id}/images/{image_id}` | bytes, `Cache-Control` por `sha256` |
| `POST /api/personas/{id}/images {count: 1..3, kind?}` | 202 + `job_id`; gera em segundo plano; evento `persona.image.updated` por imagem |
| `POST /api/personas/{id}/images/upload?filename=` | corpo cru; `source='upload'` |
| `PUT /api/personas/{id}/images/{image_id}/primary` | troca a principal (transação: zera e marca) |
| `DELETE /api/personas/{id}/images/{image_id}` | recusa apagar a principal enquanto houver outra (troque antes) |

Cada rota nova precisa de chamada HTTP na suíte (`tests/test_cobertura_de_rotas.py`, `SEM_TESTE_HTTP` vazio).

### 5.8 O que fica `not_run` sem chave

A primeira geração real, o custo real por imagem, a latência, o comportamento do filtro de conteúdo e a qualidade da
referência de rosto. Tudo isso é prova `real` que exige chave de outro provedor, saldo próprio e autorização do dono
(§15). Até lá, o simulado prova o caminho inteiro (receita, tabela, storage, rotas, painel) em `simulated`.

---

## 6. GERAÇÃO DE PERSONA POR IA

### 6.1 Rotas

| Rota | Efeito |
|---|---|
| `POST /api/personas/generate {prompt, locale?, constraints?}` | chamada **paga**; devolve um **rascunho** validado (`PersonaDraft`), **não grava**; `ai_calls.role='persona'` |
| `POST /api/personas` (corpo `PersonaCreate` v2) | grava a persona (rascunho editado ou manual); com `ai.image.on_create`, agenda `per_persona` imagens; `generation.origin` registra `ai` ou `manual` |
| `POST /api/personas/{id}/enrich {sections?: [...]}` | **paga**, **idempotente**: preenche só o que está vazio (`biography`, `visual`, voz em `voice_gaps`), nunca sobrescreve; registra `generation.enriched_at` |

### 6.2 Provedor

`AIProvider` ganha `generate_persona(req: PersonaGenerationRequest) -> PersonaDraft` (`planning/provider.py:172`),
roteado pelo `RoutingProvider` para o provedor do papel `social` (o mesmo modelo que já escreve com a voz; sem
papel novo em `AI_ROLES`), com `ai_calls.role='persona'` (texto livre) para o custo aparecer separado. O simulado
(`planning/simulated_provider.py`) devolve um rascunho determinístico a partir do hash do prompt, para testes e para
o painel sem chave. A saída do modelo é dado, validada por `PersonaCreate` v2 antes de voltar (ADR-033, mesma regra
das skills).

### 6.3 Validações do rascunho

- Esquemas `extra="forbid"` de `biography`, `visual`, `traits`; `voice_gaps(traits) == []`; `visual` completo
  (`appearance`, `visual_style`, `photo_scenario`).
- Idade derivada ≥ 18 e ≤ 90; `birth_date` ISO válida; `locale` conhecido.
- `looks_secret`/`mentions_credential` (`security/redaction.py`) em todos os textos: rascunho com formato de segredo
  é recusado.
- Nome não coincide com persona existente (`first_name`+`last_name`) nem com handle de conta cadastrada; o prompt do
  gerador pede pessoa fictícia e proíbe nome de pessoa pública.
- `beliefs` é gerado (o dono pediu o campo), guardado e não enviado a nenhum outro papel (§3.4).

### 6.4 Enriquecimento das existentes

As 14 personas dobradas pela 047 chegam com voz completa, 8 com visual e nenhuma com `biography`. O backfill
determinístico da migração preenche o que dá (nomes, `visual` a partir de `traits`); o resto é `enrich`, um passo
separado, pago e autorizado, persona a persona ou em lote por `scripts/personas_completar.py` reescrito sobre a API
nova.

### 6.5 Custo

Com o papel `social` no Sonnet (`ai.prices`, `config.py:407`): ~4 mil tokens de entrada e ~1,5 mil de saída por
persona ≈ US$ 0,02–0,03; 14 enriquecimentos ≈ US$ 0,4. Estimativa; o valor real sai de `ai_calls`. Toda chamada
exige autorização em chat (saldo pequeno e separado).

---

## 7. N:N E ROTEAMENTO

### 7.1 Tabelas

`device_profile_bindings` evolui (051, §10): `+app_id`, `+is_primary`, saem os dois índices 1:1, entram
`ux_binding_par_ativo (profile_id, instance_id, COALESCE(app_id,''))`, `ux_binding_conta_do_app_no_aparelho
(instance_id, app_id) WHERE active=1 AND app_id IS NOT NULL` e `ux_binding_principal (profile_id) WHERE active=1 AND
is_primary=1`. A sessão por aparelho é `account_sessions` (049, §3.2): conta já é (persona, app), então a chave
(conta, aparelho) é a mesma coisa que (persona, app, aparelho) sem uma tabela a mais ([decisão 4](#14-decisões-tomadas)).

- **D2-a:** duas contas do mesmo app no mesmo aparelho ficam **proibidas** enquanto a troca de conta no Instagram for
  manual (`authentication.py:256-267`, achado #115). N:N vale entre apps diferentes (persona A no Instagram e persona
  B no Chrome do mesmo aparelho) e uma persona em N aparelhos.
- **D3:** a mesma conta em N aparelhos é **permitida**, com aviso na interface ("o Instagram pode pedir verificação;
  o ADR-029 bloqueia a persona se isso acontecer"); o dono decide caso a caso.
- Localidade continua por linha (023): `_porta_da_localidade` (`state.py:750`) lê o vínculo `(profile_id, rt.id)`.

Repositório (`social/repository.py`): `bind(profile_id, instance_id, *, app_id=None, primary=False, reason=None)`
**deixa de tomar o aparelho de outro** (409 `conta_do_app_ja_no_aparelho` quando o índice recusa);
`unbind(profile_id, instance_id, app_id=None)`; novos `bindings_of_profile(profile_id)` e
`profiles_of_instance(instance_id)` (listas); `binding_row(profile_id)` passa a devolver o **principal**;
`profile_id_for_instance` fica só para compatibilidade e levanta `ValueError` com mais de um.

### 7.2 Ordem segura

Derrubar os índices com chamadores `db.one`/`scalar` ainda no lugar cria ambiguidade silenciosa (o primeiro vínculo
ganha). Então:

1. **Com os índices 1:1 ainda valendo**, todos os pontos do inventário abaixo passam para APIs de lista ou para o
   perfil do objetivo, e a suíte fica verde.
2. Só então entra a 051.

| Onde o 1:1 está embutido | Para onde vai |
|---|---|
| `social/repository.py:200-205, 227-230, 249-252` | `bindings_of_profile`, `profiles_of_instance`, `bind` sem tomar |
| `state.py:314` (`TrainingRecorder`) | perfil da sessão de treino (`training_sessions.profile_id`, 038), escolhido pela pessoa |
| `state.py:688` (`_sessao_desmentida`), `:728` (`_reobservar_apos_intervencao`), `:763` (`_porta_da_localidade`), `:825` (`_session_gate`) | perfil do **objetivo em curso** (etapa ativa do aparelho, índice 018) ou parâmetro explícito; reobservação por conta com sessão naquele aparelho |
| `taskqueue/scheduler.py:109-110`, `:367` | `session_gate(rt, pacote, profile_id=obj["profile_id"])` |
| `taskqueue/service.py:115-116`, `:452-462`, `:498`, `:625` | `resolver_alvos` (§7.4); `_alvos` já carrega `profile_id` |
| `modules/identity/infrastructure/account_session.py:57-59` | `_vinculo(db, instance_id, profile_id)` pelo `Target.profile_id` (`shared/resources.py:70`), que hoje é ignorado (`:168-169`) |
| `001:74`, `taskqueue/repository.py:230` | mantidos na fase 1: o mesmo aparelho duas vezes numa execução é 409 |
| `models.py:540` (`InstagramProfileDTO.instance_id`) | passa a significar "o principal"; `devices: [...]` ao lado |
| `api.py:808-816` (`_profile_device`), `:1526`, `:1632` | aparelho principal por padrão; `?instance_id=` para outro |
| `contexto.py:64` | `profiles` já é lista; passa a iterar `profiles_of_instance` e a trazer `accounts[]` |
| Painel: `DeviceGrid.tsx:49`, `InstancesSection.tsx:84-86`, `ProfilesPage.tsx:149`, `ProfileDetail.tsx:650-690` | mapas de listas, N `@` por cartão, lista de aparelhos na persona |

Testes que travam o 1:1 e mudam na fase 1: `test_social_profiles.py:200-214`, `test_localidade_do_perfil.py:64`,
`test_leitura_de_recursos.py:269`, `test_recursos_declarativos.py:244-252`, `test_sessao_com_validade.py:89`,
`test_aparelho_persona_sessao.py:63`, `test_contrato_http.py:320-324`, `test_memoria_da_tela.py:122`.

### 7.3 `RunCreate` estendido (sem forquilha)

```python
class RunTarget(BaseModel):
    profile_id: str
    instance_ids: list[str] = []          # vazio = o sistema escolhe pela política
    app_id: str | None = None             # qual conta da persona a tarefa usa

class RunCreate(BaseModel):
    command: str
    instance_ids: list[str] = []          # caminho direto por aparelho (mantido)
    profile_ids: list[str] = []           # junto com instance_ids = INTERSEÇÃO (hoje substitui)
    targets: list[RunTarget] = []         # alvos explícitos (o que a prévia devolveu)
    device_policy: Literal["one", "primary", "all"] = "one"
    distribute: DistributeSpec | None = None
    idempotency_key: str; mode: ...; only_ready: bool = False
```

`_um_dos_dois` (`models.py:1188-1198`) vira "informe `instance_ids`, `profile_ids`, `targets` ou `distribute`";
`distribute` continua exclusivo. `device_policy` padrão `one`: "a pessoa faz uma vez"; `all` só explícito (D4).

### 7.4 `resolver_alvos`, pura e testada em tabela

`modules/execution/domain/alvos.py` (**proposto**): `resolver_alvos(pedido, vinculos, aparelhos, sessoes, servidores,
dicas_do_texto) -> Resolucao(alvos: list[AlvoResolvido], perguntas: list[Pergunta], recusas: list[Recusa])`. Sem IO;
o serviço monta as entradas. Desempate por `balanceamento.distribuir(1, …)` (`taskqueue/balanceamento.py:73`), que
já prefere aparelho ligado (`:98-100`).

| Caso | Entrada | Saída |
|---|---|---|
| A | só `instance_ids`; aparelho com 0 personas | `(aparelho, None, origem ui)` |
| B | só `instance_ids`; aparelho com 1 persona | `(aparelho, persona, ui)` |
| C | só `instance_ids`; aparelho com 2+ personas; o app do comando tem conta de **uma** delas ali (`bindings.app_id`) | essa persona |
| D | idem, sem app que desempate | **pergunta** "qual persona neste aparelho?" |
| E | só `profile_ids`, `one` | candidatos = aparelhos vinculados ativos, não-loja, servidor disponível, app do comando instalado; preferência por sessão `session_ready` da conta **naquele aparelho**; desempate por `distribuir(1)`; origem `vinculo` ou `balanceamento` |
| F | só `profile_ids`, `primary` | o `is_primary`; sem principal → como `one` |
| G | só `profile_ids`, `all` | todos os vinculados aptos |
| H | `profile_ids` sem vínculo | 409 `no_binding` (como hoje) |
| I | `instance_ids` ∩ `profile_ids` vazio | 409 `sem_intersecao` |
| J | `targets` explícitos com aparelho não vinculado à persona | 409 `sem_vinculo` |
| K | dois alvos no mesmo aparelho | 409 `aparelho_repetido_na_execucao` (fase 1; `UNIQUE(run_id, instance_id)` mantido) |
| L | seleção da interface não vazia **e** dica do texto fora dela | **pergunta**, nunca escolha silenciosa |
| M | seleção vazia e dica do texto | o texto decide (origem `texto`), só depois de mostrado (§7.6) |
| N | nada em lugar nenhum | 400 |

### 7.5 `TargetExtractor`

`modules/execution/application/alvos_no_texto.py` (**proposto**): determinístico, sem IA, roda **antes** do
`TemplateStage` (`modules/skills/application/intent_resolver.py:117`) e do planejador, sobre um catálogo de nomes
(usernames, `display_name`, primeiros nomes, ids de aparelho). Padrões: "com a persona X", "como @user", "pelo/pela
<Nome>", "peça para o/a <Nome> …", "no(s) aparelho(s) Y e Z", `android-NN`. Devolve `dicas` (personas e aparelhos
achados, com o trecho) e `command_sem_destinos`, que é o texto que vai ao casamento de skill e ao planejador.
`runs.command` guarda o texto como veio. Dois "André" → pergunta com as opções. Nome que não casa com nada → nenhuma
dica (o texto segue inteiro).

### 7.6 Prévia obrigatória

`POST /api/runs/targets/resolve {command, instance_ids?, profile_ids?, targets?, device_policy?}` devolve
`{targets: [{instance_id, profile_id, app_id, origem}], questions: [...], command_sem_destinos, warnings}` sem gravar
nada. O painel mostra os alvos e a origem de cada um antes de "Executar". Para que destino deduzido do texto **nunca**
execute sem ter sido mostrado: `POST /api/runs` re-resolve e, se algum alvo com origem `texto` não estiver em
`targets` do corpo, responde 409 `alvos_nao_confirmados` com a lista. Quem chama a API direto precisa ecoar os alvos.

### 7.7 Precedência

Interface (manda) > texto (só estreita; contradição vira pergunta) > vínculos (principal, sessão pronta) >
balanceamento. `distribute` continua um caminho à parte e exclusivo.

### 7.8 O portador do perfil no despacho

O objetivo já leva `profile_id` (010) e `resource_plan` (045). `_portas_do_app` passa `obj["profile_id"]`;
`_session_gate(rt, package, profile_id=None)` usa o recebido e, sem ele, `profiles_of_instance`: um → usa; zero →
sem porta (QA e caminho antigo); dois ou mais → bloqueia com "aparelho com mais de uma persona e execução sem persona
definida". Já na WA (antes do N:N), a porta passa a exigir **conta do app** e não credencial: persona dobrada sem
conta Instagram (`username=''`) vinculada a um aparelho bloqueia tarefa do Instagram com "a persona vinculada não tem
conta neste app", e não com "cadastre a senha" (`state.py:858-861`). `_sessao_desmentida` recebe o objetivo em curso;
`_reobservar_apos_intervencao` reobserva cada conta com sessão naquele aparelho.

---

## 8. PROVISIONAMENTO

**Local agora; remoto depois** ([decisão 8](#14-decisões-tomadas)). Hoje nenhuma das três origens cria instância
nova pela plataforma: configuração (`instances.count` + `id_prefix`, `config.py:732-734`), adoção de aparelho
anunciado por worker (`api.py:2293-2350` → `DeviceManager.adotar_aparelho`, `devices/manager.py:1604-1635`) e o
verbo `create`, que só cria o AVD de uma instância já declarada (`devices/manager.py:1818-1830`,
`workers/local.py:239-240`, `commands/despacho.py:78-88`).

### 8.1 `POST /api/instances` (local)

```json
{"worker_id": null, "app_id": "instagram", "system_image": null, "ram_mb": 2048,
 "profile_id": null, "create": true, "start": false, "idempotency_key": "…"}
```

1. Guardas: `worker_limits.max_devices` da linha do central (050; sem valor = sem limite, como hoje) contra a
   contagem de instâncias hospedadas; disco livre da máquina (`disk_free_gb` que o `LocalWorker` já mede para a
   batida, `contracts/worker/protocol.py:84`) ≥ `limits.min_free_disk_gb_per_device` (config, padrão 12 GB, uma
   estimativa do tamanho de um AVD com dados; medir na primeira criação real). Recusa 409 `sem_vaga_de_aparelho` ou
   `sem_disco`.
2. Linha em `instances`: `id = _proximo_id_dinamico()` (`manager.py:1637`), `idx = MAX+1`, portas por `idx`
   (`manager.py:1620-1622`), `origin='dynamic'`, `hosted_by = owner`, `worker_id NULL`, `avd_name = id`,
   `android_overrides` JSON com `system_image`/`ram_mb` (050). `seed()` já recarrega linhas `dynamic`
   (`manager.py:562`) e uma linha sem `external_serial` vira emulador local (`:269-274`).
3. `DeviceManager.create` mescla `android_overrides` sobre `cfg.instance_android(id)` (`config.py:741-744`).
4. Runtime vivo e publicado na hora, como `adotar_aparelho` faz (`manager.py:1629-1635`).
5. Com `create: true`, o comando `create` vai pelo despacho normal (`pedir_ciclo_de_vida`), com cerca, outbox e
   diário; a resposta é 201 com o `Instance` e o `command_id`. `start: true` encadeia `start` depois do `succeeded`.
6. Com `profile_id`, `bind(profile_id, id, app_id=app_id, primary=<sem outro>)` na mesma chamada: vincular é
   decisão de pessoa e continua sem virar comando (ADR-035).

### 8.2 `DELETE /api/instances/{id}`

Só instância `origin='dynamic'`, sem linha em `objectives`, sem vínculo ativo, sem `account_sessions`, em
`stopped`/`absent`; senão 409 com o motivo. Apaga o AVD por um `AvdManager.delete` **novo** (`devices/avd.py` só tem
`exists:90` e `create:94`), depois a linha e o runtime. Toca o disco do host: prova real `not_run` até autorização.

### 8.3 Remoto (depois, ADR próprio)

Verbo `provision` anunciado em `Hello.features` (`protocol.py:175`; aceito em `Welcome.accepted_features`,
`:256`); o agente escolhe `console_port` livre, grava o `DeviceSpec` num inventário mutável
(`work_dir/devices.d/<id>.yaml`, hoje `worker.yaml` é estático e o agente recusa lista vazia,
`worker/__main__.py:67-69`), cria o AVD e anuncia; o central registra como adoção, reescreve o mapa do túnel e
`expected_devices`. Fora desta rodada.

---

## 9. PAINEL

Só depois de o dono decidir sobre a branch `claude/android-multiagentes-session-cvv1rp` (§15): ela tem **23
commits** não integrados a partir de `5c98735` (merge-base conferido no commit base) e mexe em `CommandPanel`,
`FocusPanel`, `ProfileDetail`, `ProfileAccounts`, `DeviceGrid`, `InstancesSection`, `OperationalContextCard`.

### 9.1 Contrato de página

`components/Page.tsx` (**proposto**): `Page` (sem variante estreita), `PageHeader` (título, lead, ações),
`PageSection` (Card + CardHeader), `TableWrap` (rolagem horizontal, `th` `sticky`). `.page` vira contêiner
(`container-type: inline-size; container-name: page`) e as faixas passam a ser **`@container page`**: compacto
< 560, médio 560–959, largo 960–1439, ultra ≥ 1440, documentadas em `styles/tokens.css` ao lado de
`--focus-panel-w`. `.autoGrid { grid-template-columns: repeat(auto-fill, minmax(min(100%, var(--col-min)), 1fr)) }`.
O Foco encolhe o `main`, então a viewport não é a medida certa; a container query já é o padrão de
`Devices.module.css:5-6, 46-56`.

### 9.2 Configuração

- **Passo A (CSS e marcação mínima):** sai `pageNarrow` (`SettingsPage.tsx:44`; a classe some de
  `App.module.css:54-56`); `min(100%, …)` em `.appList`, `.limitGroups`, `.instanceGrid`; `.aiGrid` `auto-fit`;
  `.aiRoles` dentro de `TableWrap`; `flex-wrap` em `.sectionIntro`; estilos inline (11 em `AiSection.tsx`, 7 em
  `FlowsRecipesSection.tsx`, 5 em `AppsSection.tsx`, 4 em `InstancesSection.tsx`) viram classes; `.formRow` e
  `.limitFields` por `@container`.
- **Passo B (estrutura):** abas na página, como em Aplicativos (`AppsPage.tsx:60-73`), e cada seção em
  `PageSection`; **rótulos e `role=tab` iguais** aos de hoje, porque `app.integration.test.tsx:478-500, 610-630` os
  procura por nome. "Instâncias e contas" perde a associação instância↔conta (que passa a viver na persona, §9.4) e
  vira "Aparelhos" (app por aparelho, filtros).
- Na mesma leva: lista de Perfis com o invólucro comum (`ProfilesPage.tsx:110`), `ReleasesPage` embutida sem repetir
  `appStyles.page` (`ReleasesPage.tsx:556`), `Diagnostics .grid` por `@container`.

### 9.3 Foco: seções, ações e interação manual

`.content` passa a `minmax(0, 1fr) minmax(300px, 380px)` (`Focus.module.css:102`) com `container-type` no painel;
abaixo de ~720 px de painel, uma coluna. A lateral vira **`FocusSection`s** (título, selo, recolhível), nesta
ordem: identidade (id, servidor, origem, avd); estado e saúde (`state`, `readiness` em português, `stream`,
`connectivity`, `automation`, `attention`, `inventory_state`, `resources`); servidor (`ServerHint`, link para
Infraestrutura); automação e tarefa (`current`, vaga, `CommandSummary`); conectividade; **personas vinculadas**
(avatar, nome, contas neste aparelho, sessão por conta, portão, "Abrir persona"); **contas** (`accounts[]` do
`operational-context`, aditivo em `contexto.py:66-76`); apps (`appState` × versão promovida, "Verificar app");
tela (frame, `sensitive`, app em primeiro plano); sessão; **ações**; comandos recentes, detalhes técnicos (filhos
ansiosos, `Disclosure.tsx:46-48`, para `FocusPanel.test.tsx:87-97` continuar), hierarquia.

**Ações** por uma função pura `focusActionGroups(instance, hibernation, openCmd)` em `features/devices/deviceState.ts`
(ao lado de `QUICK_VERBS:193` e `primaryActionFor:26`): `{primary, lifecycle[], apps[], manual[], observe[],
danger[], unavailable[]}`. Grupos: **Controle** (faixa Assumir/Devolver); **Ciclo de vida** (ação principal,
Reiniciar, Hibernar; Parar confirma se há `current.run_id`); **Apps** (Abrir ▾, Instalar ▾, Verificar); **Controle
manual** (só com controle: barra Voltar/Início/Recentes/Enter/Apagar, campo de texto, motivo do bloqueio uma vez);
**Observação** (frame, contexto, hierarquia, verificar comando); **Zona de perigo** (borda `danger`, separada:
Resetar dados…, Cancelar comando). Não suportadas em "Indisponíveis (n)" com o motivo (o teste exige "Este aparelho
não aceita…"). O Foco passa a bloquear por `motivoDoComando` como o cartão, não só por `busyAction`
(`FocusPanel.tsx:291, 312`).

### 9.4 Persona

Guias: **Visão geral** (faixa de imagens, idade, cidade, profissão, aparelhos, contas); **Persona** (biografia por
seção, voz, "Testar persona", "Enriquecer por IA" com custo e autorização); **Contas e acesso** (funde Contas e
Autenticação: uma linha por conta com app, handle, host, login, senha guardada, consentimento, sessão por aparelho,
Conectar/Verificar/Sair gateados por `session_actions`, tentativas em `Disclosure`; mantém os textos "Senha do
Instagram" e "Guardar senha" que `ProfileDetail.test.tsx:79-125` procura); **Aparelhos** (Servidor → Aparelho →
conta: cartão por vínculo com estado, servidor, conectividade, apps, principal, "Abrir no Foco", vincular e
desvincular, trocar principal, aviso D3); **Imagens** (galeria, principal, gerar N, enviar arquivo, receita e
proveniência de cada uma, selo "simulado"); Memória, Interações, Habilidades, Aprovações, Execuções, Configurações
como hoje. O cadastro deixa de exigir senha e aparelho: persona nasce sem conta; conta e vínculo vêm depois.
`ProfileDetail.tsx` (1420 linhas) é dividido por guia em arquivos próprios.

### 9.5 Comando e Infraestrutura

`CommandPanel.tsx:81-82` ganha o terceiro modo **"Por persona"** ao lado de `selecao` e `distribuir`: seletor de
personas com avatar, política (`one` padrão, `primary`, `all`), prévia dos alvos por `POST /api/runs/targets/resolve`
com a origem de cada um e as perguntas como escolha obrigatória; o comando por aparelho segue igual. Sai o campo de
senha (§4.5). `CreateRunRequest` (`api/types.ts:1177`) ganha `profile_ids`, `targets`, `device_policy`.
Infraestrutura ganha "Criar aparelho" no cartão do servidor central (`InfraPage.tsx:375`), com os campos da §8.1, e
o cartão de aparelho mostra as personas vinculadas. Grade do Painel: N avatares por cartão (`DeviceCard.tsx`).

### 9.6 Aceite

Layout não se prova no vitest (jsdom não calcula layout). Aceite no navegador em 375, 1024, 1366 e 1920, com o Foco
aberto e fechado, para Configuração, Painel, Persona e Infraestrutura, com captura por tela; o vitest prova
comportamento e rótulos.

---

## 10. MIGRAÇÕES 047–051

Regras: migração aplicada não se edita; um arquivo por número; diferença de dialeto no mesmo arquivo (marcas
`{{PK_AUTO}}`/`{{BLOB}}` ou bloco `-- @dialect:`); teste nos dois bancos; `docs/banco.md` cita o número. Cada
arquivo roda inteiro numa transação com `PRAGMA foreign_keys=ON` (`db.py:130`, `migrate:519-541`), e `migrate()`
aplica **qualquer arquivo ausente, em ordem de nome**, independentemente do que já foi aplicado antes. Consequência
para as ondas paralelas (WA 047/048, WB 049, WD 050 em worktrees distintos): um banco de desenvolvimento pode
receber a 049 antes da 047, então **047, 048, 049 e 050 não referenciam colunas umas das outras**; só a 051 pode
supor a 049. Em produção tudo entra num deploy só, em ordem numérica, com autorização.

### 10.1 `047_persona_raiz.sql` (WA)

```sql
ALTER TABLE instagram_profiles ADD COLUMN gender TEXT;
ALTER TABLE instagram_profiles ADD COLUMN locale TEXT;              -- nulo = pt-BR
ALTER TABLE instagram_profiles ADD COLUMN summary TEXT;
ALTER TABLE instagram_profiles ADD COLUMN traits TEXT;              -- JSON, só voz
ALTER TABLE instagram_profiles ADD COLUMN persona_prompt TEXT NOT NULL DEFAULT '';
ALTER TABLE instagram_profiles ADD COLUMN biography TEXT;           -- JSON PersonaBiography
ALTER TABLE instagram_profiles ADD COLUMN visual TEXT;              -- JSON PersonaVisual
ALTER TABLE instagram_profiles ADD COLUMN generation TEXT;          -- JSON
-- persona_id (008:33, FK -> personas) FICA e continua apontando para a linha dobrada: é o marcador da dobra
DROP INDEX IF EXISTS ux_instagram_profiles_username;
CREATE UNIQUE INDEX ux_instagram_profiles_username ON instagram_profiles(lower(username)) WHERE username <> '';
```

- **`username` opcional sem reconstruir a tabela.** Tornar a coluna nula exigiria o padrão da 010
  (`recipes_novo` + `DROP TABLE` + `RENAME`, `010:74-78`); em `instagram_profiles`, com dez tabelas filhas em
  `ON DELETE CASCADE` e `foreign_keys=ON` dentro da transação, o `DROP TABLE` apagaria memória, contas, vínculos e
  sessões. Fica `NOT NULL` com **`''` = persona sem conta** e o índice único parcial (`lower()` em índice vale nos
  dois dialetos, precedente da 028). Consequências: `ProfileCreate.username` (`schemas.py:22`) deixa de ser
  `min_length=1` e o DTO devolve `null` para `''`; a unicidade continua para quem tem handle.
- **Dobra de `personas`** (backfill determinístico, três passos, todos por `UPDATE … FROM`/subconsulta portável):
  1. **Vinculadas (3):** `UPDATE instagram_profiles SET summary, traits (só as 15 chaves de voz), visual (as 3 chaves
     visuais), persona_prompt FROM personas WHERE personas.id = persona_id`; `persona_id` já aponta. A separação das
     chaves em SQL usa `json_extract`/`json_object` no SQLite e `jsonb` no PostgreSQL: **bloco `-- @dialect:`**; o
     teste confere que os dois produzem o mesmo JSON canônico.
  2. **Desvinculadas do ADR-029 (5):** casam por nome com os perfis `blocked` sem `persona_id`
     (`lower(trim(personas.name)) = lower(trim(first_name || ' ' || last_name))`), mesmo `UPDATE`, gravando também
     `persona_id = personas.id` (a FK aceita, porque a linha de `personas` fica). Decisão a confirmar com o dono
     (§15); `persona_id` apontando para a linha dobrada e a tabela `personas` preservada permitem desfazer por
     script sem perda.
  3. **Restantes (6):** `INSERT INTO instagram_profiles (id, username, first_name, last_name, display_name, status,
     summary, traits, visual, persona_prompt, persona_id, generation, created_at, updated_at) SELECT …` para
     toda persona sem perfil depois dos passos 1 e 2: `id = 'ig-' || substr(personas.id, 9)`, `username = ''`,
     nome e sobrenome partidos de `personas.name` no primeiro espaço, `display_name = name`, `status = 'active'`,
     `persona_id = personas.id`, `generation = '{"origin":"manual"}'`.
  - `personas` **não é apagada** nem alterada: fica só leitura até uma migração posterior à WE. Nada grava um id de
    perfil em `persona_id`: a FK `REFERENCES personas(id)` (`008:33`) é conferida na transação (`foreign_keys=ON`)
    e abortaria a 047.
  - `ux_profiles_persona` (`009:9`) e a coluna `persona_id` ficam como estão: depois da 047, **toda** linha de
    `instagram_profiles` que veio da dobra aponta para a sua linha de `personas`, uma para uma. O que o DTO expõe
    como `persona_id`/`persona_name` é escolha da camada de API (§11.2), não valor gravado.
- Nenhuma imagem é gerada pela migração; nenhuma chamada de IA.
- Testes (**propostos**, `tests/test_persona_raiz_migracao.py`): um SQLite sintético no esquema 046 com as
  contagens de produção (8 perfis, 14 personas, 8 credenciais, 8 sessões com 5 fantasmas, 8 vínculos com 3 ativos,
  24 memórias) atualizado por `migrate()`: 14 personas viram 14 linhas de perfil com voz (3 + 5 dobradas, 6 novas),
  nenhuma memória/conta/vínculo perdido (contagem e impressão digital por tabela, como
  `test_migracao_de_dados.py:84`); "banco novo e banco atualizado têm o mesmo esquema"
  (`test_habilidades_migracoes.py:140`); renderiza sem marca sobrando (`:264`); a mesma bateria em PostgreSQL por
  `TEST_DATABASE_URL`.

### 10.2 `048_persona_images.sql` (WA)

```sql
CREATE TABLE IF NOT EXISTS persona_images (
    id                  TEXT PRIMARY KEY,
    profile_id          TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    storage_key         TEXT NOT NULL,
    original_key        TEXT,
    content_type        TEXT NOT NULL,
    width INTEGER, height INTEGER, bytes INTEGER,
    sha256              TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'portrait',   -- portrait | half_body | full_body | lifestyle
    is_primary          INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'ready',      -- pending | ready | failed | refused
    source              TEXT NOT NULL,                      -- generated | upload | imported_legacy
    spec                TEXT,                               -- JSON PersonaImageSpec
    prompt_sha256 TEXT, provider TEXT, model TEXT, seed INTEGER, provider_seed TEXT, provider_request_id TEXT,
    cost_usd REAL, error TEXT, parent_id TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_persona_images_perfil ON persona_images(profile_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_persona_images_primaria ON persona_images(profile_id) WHERE is_primary = 1;
CREATE UNIQUE INDEX IF NOT EXISTS ux_persona_images_sha ON persona_images(profile_id, sha256);
ALTER TABLE ai_calls ADD COLUMN usd REAL;                    -- custo declarado (imagem); nulo = calcular por tokens
```

FK para `instagram_profiles` porque a persona **é** o perfil (a coluna se chama `profile_id` como em todas as
filhas). Carga dos 8 avatares: passo de partida idempotente (§5.4), não SQL. Testes: linha de custo com
`role='image'` soma em `spent_usd`; simulado não soma; `test_migracao_de_dados.py` acha a ordem por FK com a tabela
nova (`:271` do teste de skills é o modelo).

### 10.3 `049_conta_unica.sql` (WB)

```sql
ALTER TABLE account_credentials ADD COLUMN status TEXT NOT NULL DEFAULT 'active';   -- active | invalid
ALTER TABLE account_credentials ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE account_credentials ADD COLUMN blocked_until TEXT;
ALTER TABLE account_credentials ADD COLUMN created_at TEXT;
ALTER TABLE account_credentials ADD COLUMN last_used_at TEXT;
ALTER TABLE account_credentials ADD COLUMN consent_at TEXT;
ALTER TABLE account_credentials ADD COLUMN consent_by TEXT;
ALTER TABLE profile_accounts ADD COLUMN host TEXT;
DROP INDEX IF EXISTS ux_profile_accounts_app;
CREATE UNIQUE INDEX ux_profile_accounts_app_host ON profile_accounts(profile_id, app_id, COALESCE(host, ''));
CREATE TABLE IF NOT EXISTS account_sessions (
    account_id      TEXT NOT NULL REFERENCES profile_accounts(id) ON DELETE CASCADE,
    instance_id     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'unknown',
    observed_handle TEXT, verified_at TEXT, detail TEXT,
    unknown_streak  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (account_id, instance_id)
);
ALTER TABLE authentication_attempts ADD COLUMN account_id TEXT;
```

Backfill (mesmo critério da 037, `037:45-51`: a conta Instagram é a de `apps.package = 'com.instagram.android'`;
literal de app em SQL de migração não é comparação no núcleo, ADR-039):

- `account_credentials` ← `instagram_credentials` com o **mesmo `secret_ref`** (sem recifrar; enquanto as duas
  linhas existirem, apagar uma não apaga o segredo: `delete_account` e `set_credential` conferem), `status`,
  `failed_attempts`, `blocked_until`, `last_used_at`, `created_at = updated_at`, `consent_at = updated_at`,
  `consent_by = 'migração 049'` (§4.3). Só onde ainda não há linha (produção: 0).
- `account_sessions` ← `instagram_sessions` **só onde há vínculo ativo** com o mesmo aparelho: as 5 fantasmas não
  vêm (produção: 3 linhas).
- `authentication_attempts.account_id` ← conta Instagram do `profile_id`.
- `profile_accounts.session_status` deixa de ser escrita; `apps_overview.py:80-81, 103` lê `account_sessions`.

`instagram_credentials` e `instagram_sessions` ficam **só leitura** (nenhum `INSERT/UPDATE` depois da WB) e saem
numa migração posterior à WC, junto com `run_secrets`; `rekey.py:87-91` passa a atualizar `account_credentials.key_id`.
Testes (`tests/test_conta_unica_migracao.py`): mesmo sintético; 8 credenciais copiadas com a mesma ref; 3 sessões;
login determinístico (`test_instagram_auth.py`) verde sem mudar asserção, lendo pela conta.

### 10.4 `050_provisionamento.sql` (WD)

```sql
ALTER TABLE instances ADD COLUMN android_overrides TEXT;   -- JSON: system_image, ram_mb…; nulo = config
ALTER TABLE worker_limits ADD COLUMN max_devices INTEGER;  -- teto de aparelhos hospedados; nulo = sem teto
```

Testes: instância criada por `POST /api/instances` com `EmulatorBackend` falso aparece em `seed()` depois de
reiniciar; portas não colidem com as da configuração (harness em `base_console_port: 5640`); `DELETE` recusa com
objetivo, vínculo ou sessão.

### 10.5 `051_vinculo_n_n.sql` (WC, depois da 049)

```sql
DROP INDEX IF EXISTS idx_binding_profile_ativo;
DROP INDEX IF EXISTS idx_binding_device_ativo;
ALTER TABLE device_profile_bindings ADD COLUMN app_id TEXT;
ALTER TABLE device_profile_bindings ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0;
UPDATE device_profile_bindings SET is_primary = 1 WHERE active = 1;
-- app_id do vínculo vivo = a conta do perfil, quando ele tem exatamente UMA; senão fica NULL (app sem conta gerenciada)
UPDATE device_profile_bindings SET app_id = (SELECT a.app_id FROM profile_accounts a
                                             WHERE a.profile_id = device_profile_bindings.profile_id)
 WHERE active = 1 AND (SELECT COUNT(*) FROM profile_accounts a WHERE a.profile_id = device_profile_bindings.profile_id) = 1;
CREATE UNIQUE INDEX ux_binding_par_ativo ON device_profile_bindings(profile_id, instance_id, COALESCE(app_id, '')) WHERE active = 1;
CREATE UNIQUE INDEX ux_binding_conta_do_app_no_aparelho ON device_profile_bindings(instance_id, app_id) WHERE active = 1 AND app_id IS NOT NULL;
CREATE UNIQUE INDEX ux_binding_principal ON device_profile_bindings(profile_id) WHERE active = 1 AND is_primary = 1;
```

Sem o backfill de `app_id`, `ux_binding_conta_do_app_no_aparelho` não protegeria nenhum dos 3 vínculos vivos.
Só entra depois da fase 1 da §7.2 (chamadores migrados com os índices antigos). Testes
(`tests/test_vinculos_n_n.py`): duas personas em apps diferentes no mesmo aparelho passam; duas contas do mesmo app
no mesmo aparelho recusam; uma persona em dois aparelhos com um principal; `bind` não toma mais o aparelho.

### 10.6 Testes de atualização comuns

Para cada migração: renderiza sem marca sobrando; banco novo = banco atualizado; contagem e impressão digital das
tabelas não tocadas; `docs/banco.md` cita o número (`docs-check`); PostgreSQL por `TEST_DATABASE_URL` no CI
(`ci.yml`), apagando o schema ao fim (K-038). E um **ensaio** contra uma cópia do banco de produção (como o da 042–046,
`simulated`) antes do deploy.

---

## 11. COMPATIBILIDADE

### 11.1 Rotas

| Hoje | Depois | Como |
|---|---|---|
| `/api/instagram/profiles*` (`api.py:692-1123`, `:1624`) | **apelidos** de `/api/personas*` | mesmo handler, mesma resposta; `Deprecation` no cabeçalho; somem numa rodada posterior |
| `GET/POST /api/personas`, `GET/PATCH/DELETE /api/personas/{id}`, `POST …/preview` (`api.py:871-914`) | **rota canônica da persona = perfil**; **quebra de contrato**, não só apelido: o id passa de `persona-…` a `ig-…` e o DTO de voz vira `PersonaDTO` v2 | `PersonaDTO` v2 é **superconjunto** da v1 (`id`, `name`, `summary`, `traits`, `persona_prompt`, `profile_id` = o próprio id, `profile_username`) mais identidade, `biography`, `visual`, `generation`, `accounts[]`, `devices[]`, `images[]`, `age`. `GET /api/personas/{persona-…}` resolve pelo perfil cujo `persona_id` é esse id e devolve a persona dobrada (com `Deprecation`); id legado sem dobra → 404. `POST /api/personas` com corpo v1 (`name`, `summary`, `traits`) continua aceito: vira persona sem conta. Quem se move na WA: `test_contrato_http.py:308-333`, `ProfileDetail.tsx:349-367, 566-585`, `social/context.py:30, 63` (`persona_dto`), `scripts/personas_criar.py`, `scripts/personas_completar.py` |
| `PUT/DELETE /api/instagram/profiles/{id}/credential`, `POST …/connect`, `…/verify`, `…/logout`, `GET …/auth-attempts` | apelidos de `/api/personas/{id}/accounts/{aid}/credential`, `…/session/connect`, `…/session/verify`, `…/session/logout`, `…/auth-attempts`; o apelido age sobre a conta do app do provedor (Instagram) e o aparelho principal | WB |
| `POST /api/runs` | aditivo: `targets`, `device_policy`, interseção; **remove** `credentials`/`consent_credentials` (422 `extra="forbid"`) | WB remove; WC adiciona |
| `POST /api/runs/targets/resolve`, `POST /api/instances`, `DELETE /api/instances/{id}`, imagens (§5.7), `generate`, `enrich` | novas | um adendo do contrato por onda, a partir do v0.26 |
| `PUT /api/instances/{id}` (`api.py:1540`) | inalterada | — |

### 11.2 DTOs e tipos

- `InstagramProfileDTO` (`models.py:526`) mantém todos os campos; `instance_id` passa a ser o principal;
  `persona_id` continua sendo o id da linha de `personas` dobrada (o que está gravado) e `persona_name` vira o nome
  da persona; `credential`/`session` viram os da conta do app do
  provedor no aparelho principal; entram `devices[]`, `accounts[]`, `images[]`, `biography`, `visual`.
- `ProfileAccountDTO` (`:814`) ganha `login_identifier`, `host`, `credential: CredentialInfo` (com `consent_at`),
  `sessions: [{instance_id, status, verified_at, detail}]`, `session_actions` por conta.
- `CredentialUpdate` (`schemas.py:47`) ganha `consent: bool`.
- `types.ts`: `InstagramProfile` (`:559`) e `Persona` (`:644`) convergem em `Persona`; `CredentialInfo` (`:471`),
  `ProfileAccount` (`:1373`), `CreateRunRequest` (`:1177`), `OperationalContext` (`:507`, `accounts[]`).

### 11.3 Scripts, config, docs

- `scripts/personas_criar.py` e `scripts/personas_completar.py` passam a escrever pela API nova; `personas-*.json`
  ganham `biography`/`visual`.
- `config.example.yaml`: `ai.image`, `limits.min_free_disk_gb_per_device`. `.env`: `IMAGE_API_KEY` (opcional).
- Docs no mesmo commit de cada onda: `dominios/perfis-e-instagram.md` (o índice em `personas.persona_id` citado em
  `:10` está errado hoje; fica em `instagram_profiles`), `dominios/parque.md`, `banco.md`, `api-contract.md`, `ia.md`
  (§1 continua com cinco funções; imagem e persona são linhas de custo fora delas), `evidencias.md`, `produto.md`,
  `frontend/README.md`, `CLAUDE.md`, `.claude/rules/segredos-e-mundo-real.md`, `CHANGELOG.md`.

### 11.4 O que se aposenta e quando

| O quê | Quando |
|---|---|
| `test_social_memory.py:274` ("persona pertence a um perfil só") | WA, de propósito: a persona **é** o perfil |
| Campo "Senha para a automação", `RunCreate.credentials`, `test_credenciais_da_execucao.py` | WB |
| Escrita em `instagram_credentials`, `instagram_sessions`, `profile_accounts.session_status`, `run_secrets` | WB (leitura fica) |
| `profile_id_for_instance` como fonte de verdade; `bind` que toma o aparelho | WC fase 1 |
| Índices `idx_binding_*_ativo` | WC (051) |
| Aba "Autenticação", `pageNarrow`, campo de senha no comando | WE |
| Tabelas `personas`, `instagram_credentials`, `instagram_sessions`, `run_secrets`; apelidos `/api/instagram/*` | migração e rodada posteriores, depois de um ciclo em produção |

---

## 12. RISCOS

| # | Risco | Mitigação |
|---|---|---|
| R1 | Reconstruir `instagram_profiles` apagaria as filhas em cascata | não reconstruir: `''` + índice parcial (§10.1) |
| R2 | `extra="forbid"` de `PersonaTraits` quebra `GET /personas` se uma chave visual sobrar em `traits` | a 047 move as três chaves; o DTO tolera `traits` antigo por um leitor de compatibilidade até a WE, com teste |
| R3 | PATCH de `traits` substitui o objeto inteiro (`service.py:289-290`; `ProfileDetail.tsx:349-367`) e apagaria seções | PATCH por seção com mescla no servidor; teste de "PATCH de uma chave não apaga as outras" |
| R4 | Dobra por nome das 5 personas erra o casamento | só casamento exato e só em perfil `blocked` sem persona; `persona_id` apontando para a dobrada + `personas` intacta permitem desfazer; decisão do dono registrada |
| R5 | `secret_ref` compartilhado entre `instagram_credentials` e `account_credentials` durante a transição: apagar uma apaga o segredo da outra | `delete_account`/`set_credential`/`drop_run_secrets` conferem se a ref ainda é usada por outra linha antes de `DELETE FROM secrets`; teste dedicado |
| R6 | Persona dobrada sem conta, vinculada, bloquearia tarefas com motivo errado | porta por existência de conta (§7.8), entregue na WA |
| R7 | `drop_run_secrets` sobre uma ref de conta | `RunService` nunca escreve em `run_secrets` depois da WB; teste `_em_lugar_nenhum` estendido |
| R8 | Dado pessoal em `steps.bindings`/`actions.args`/evidência vai ao modelo | é o desenho para dado **não sigiloso** (como `account_label` hoje); `beliefs` fora do catálogo; segredo só por `type_secret` |
| R9 | Portal `22d65f` regride sem URL no comando | hosts das contas do app navegador entram em `allowed_urls` (§4.4) |
| R10 | Ambiguidade silenciosa ao derrubar os índices 1:1 | ordem segura (§7.2) com a suíte verde antes da 051 |
| R11 | `device_policy: all` duplica efeito externo (a pessoa "curte" duas vezes) | `all` só explícito; aviso na prévia; política do perfil (`PolicyEngine`) já conta por conta |
| R12 | Mesma conta em dois aparelhos dispara desafio (ADR-029) | permitido com aviso; o bloqueio automático protege; principal como padrão |
| R13 | Extrator de destinos casa nome comum no texto ("pelo André" quando André é o destinatário da mensagem) | dica nunca executa sem prévia (409 `alvos_nao_confirmados`); seleção da interface manda; teste em tabela com frases negativas |
| R14 | Imagens: chave paga ausente cai no simulado sem ninguém perceber | 409 `image_not_configured`; selo "simulado" no painel; `provider` na linha |
| R15 | Nome da persona ou dado de tela vaza no prompt de imagem | o prompt sai só da receita; teste de que `first_name`, `last_name`, `username`, memória e handle não aparecem no prompt |
| R16 | Recodificar a imagem apaga C2PA (ADR-022, se for para o Instagram) | original preservado em `original_key`; pós só para variação; publicar foto fica fora do escopo |
| R17 | Merge com `cvv1rp` colide com a WE | decisão do dono antes da WE; até lá, backend e contratos não dependem do painel |
| R18 | `ProfileDetail.tsx` (1420 linhas) e `Settings.module.css` (808) crescem mais | dividir por guia e por seção na WE; catraca de linhas do `test_arquitetura` vale só no backend, então o limite do painel é revisão |
| R19 | Ondas paralelas e numeração de migração | regra da §10; nenhuma referência cruzada entre 047–050 |
| R20 | O checkout de produção (`5c98735`) faz `git pull` depois de a 047 entrar na `main` e `migrate()` roda na partida | não fazer `git pull` na produção; deploy só por `scripts/deploy.ps1 -Ensaio` e autorização (§15) |
| R21 | `POST /api/instances` cria AVD num host sem disco | guarda de disco (§8.1) e `max_devices`; prova real `not_run` até autorização |
| R22 | 100+ rotas: cada rota nova sem chamada HTTP reprova `test_cobertura_de_rotas.py` | toda onda entrega os testes HTTP das rotas que cria |

---

## 13. PLANO POR ONDAS

Ordem: **W0** → **WA ∥ WB ∥ WD** (worktrees distintos, migrações 047/048, 049, 050 sem referência cruzada) →
**WC** (051, depois de WB) → **WE** (painel, depois da decisão sobre `cvv1rp`). Deploy desta rodada exige nova
autorização do dono; até lá nada sai do branch.

| Onda | Entregáveis | Arquivos principais | Testes | Pronto quando | Prova |
|---|---|---|---|---|---|
| **W0** | este documento; linha no índice; ADRs 040–046 rascunhados na §16 | `docs/design/persona-e-parque.md`, `docs/README.md` | `python scripts/docs-check.py` = 0 erros | coordenador aprova | `real` (docs-check) |
| **WA** | 047 e 048; `PersonaDTO` v2, `biography`/`visual`/`generation`, PATCH por seção; `/api/personas` canônico com apelidos; `dados_disponiveis`; porta `ImageGenerator`, adaptadores simulado e OpenAI Images, `PersonaImageSpec`, rotas de imagem, importador dos 8 avatares; `generate`/`enrich`; `ai.image` e `AiStatus.image`; porta de sessão por existência de conta | `social/{repository,service,context}.py`, `models.py`, `modules/identity/{domain,application,adapters,presentation}`, `planning/{provider,routing,simulated_provider}.py`, `config.py`, `state.py`, `api.py`, `scripts/personas_*.py`, `storage.py` | `test_persona_raiz_migracao.py`, `test_persona_modelo_rico.py`, `test_persona_geracao.py`, `test_persona_imagens.py`, `test_arquitetura.py` (adapters), `test_contrato_http.py`, `test_cobertura_de_rotas.py`; `test_social_memory.py:274` aposentado | suíte verde nos dois dialetos; 14 personas dobradas no sintético; imagem simulada gerada pela rota e servida como avatar | `simulated`; imagem real e enriquecimento real `not_run` (chave, autorização) |
| **WB** | 049; conta única na API e no serviço; `credential_row/session_row/set_session` lendo e gravando nas tabelas de conta com a mesma assinatura; `SessionProvider` com consentimento; opção A: `RunCreate` sem `credentials`, catálogo nos prompts, `type_secret` por (perfil, app, host), `open_url` com hosts da conta, `tem_credencial` por etapa; `rekey` na conta; `apps_overview` por `account_sessions`; docs e regras | `taskqueue/{service,repository,executor}.py`, `planning/prompts.py`, `planning/provider.py`, `automation/tools.py`, `social/*`, `integrations/instagram/authentication.py`, `modules/identity/*`, `security/rekey.py`, `apps_overview.py`, `CLAUDE.md`, `.claude/rules/segredos-e-mundo-real.md` | `test_conta_unica_migracao.py`, `test_credenciais_da_conta.py`, `test_instagram_auth.py` e `test_sessao_com_validade.py` sem mudar asserção, `test_perfil_multiapp.py`, `test_previa_tela_sensivel.py`, DSL fixtures | login determinístico verde lendo pela conta; execução com `{perfil_email}` resolvido por aparelho e `type_secret` da conta do Chrome no simulado; nenhuma escrita nas tabelas antigas | `simulated`; login real com credencial da conta e o portal `22d65f` `not_run` (conta real, autorização) |
| **WD** | 050; `POST /api/instances` local com guardas; `android_overrides` no `create`; `AvdManager.delete`; `DELETE /api/instances/{id}`; "Criar aparelho" no contrato | `devices/{manager,avd}.py`, `commands/despacho.py`, `workers/local.py`, `api.py`, `config.py`, `models.py` | `test_provisionamento_local.py`, `test_cobertura_de_rotas.py`, `test_contrato_http.py` | instância dinâmica criada, listada, recarregada por `seed()`, apagada, com `EmulatorBackend` falso na porta 5640 | `simulated`; criação real de AVD `not_run` (host de produção) |
| **WC** | fase 1: chamadores do §7.2 em APIs de lista com os índices 1:1; depois 051; `bind` sem tomar; `RunCreate` com `targets`/`device_policy`/interseção; `resolver_alvos`; `TargetExtractor`; `POST /api/runs/targets/resolve`; 409 `alvos_nao_confirmados`; porta com `profile_id` do objetivo; `_sessao_desmentida` pelo objetivo | `social/repository.py`, `state.py`, `taskqueue/{service,scheduler}.py`, `modules/execution/{domain/alvos.py,application/alvos_no_texto.py}`, `modules/identity/infrastructure/account_session.py`, `contexto.py`, `models.py`, `api.py` | `test_vinculos_n_n.py`, `test_roteamento_por_persona.py` (tabela A–N), `test_alvos_no_texto.py` (frases positivas e negativas), os oito testes da §7.2 atualizados | duas personas num aparelho (apps distintos) e uma persona em dois aparelhos executam pelo caminho novo no simulado; "peça para o André…" resolve e a prévia mostra a origem | `simulated`; execução real por persona `not_run` |
| **WE** | contrato de página; Configuração sem `pageNarrow` e com abas na página; Foco em seções com `focusActionGroups`; persona com as guias da §9.4; modo "Por persona"; "Criar aparelho"; grade com N personas | `components/Page.tsx`, `App.module.css`, `styles/tokens.css`, `features/settings/*`, `features/focus/*`, `features/devices/*`, `features/profiles/*` (dividido), `features/command/*`, `features/infra/*`, `api/{types,client}.ts` | `Page.test.tsx`, `SettingsPage`/`AiSection`/`InstancesSection` tests, `FocusPanel.test.tsx`, `ProfileDetail.test.tsx` (guias renomeadas), `CommandPanel` por persona, `app.integration.test.tsx` | `npm run typecheck && npm test && npm run build`; aceite no navegador (§9.6) com capturas | vitest `simulated`; layout `real` só com servidor de desenvolvimento e autorização; senão `not_run` |

Cada onda fecha com: doc principal do assunto, ADR correspondente em `docs/decisoes.md`, aprendizado se houve
armadilha, `CHANGELOG.md`, `docs-check`, handoff em `estado-atual.md`, commit na `main` (`feat(persona): …`,
`feat(contas): …`, `feat(parque): …`, `feat(roteamento): …`, `feat(painel): …`).

---

## 14. Decisões tomadas

As decisões vêm do coordenador, com as alternativas que os relatórios propuseram.

1. **A persona é a linha de `instagram_profiles`.** Nome de tabela mantido; na API e na interface passa a se chamar
   Persona; `/api/personas` vira a rota canônica e `/api/instagram/profiles*` fica como apelido. A tabela `personas`
   (só voz) é dobrada para dentro do perfil pela 047; `username` passa a ser opcional (`''`); as 3 vinculadas são
   dobradas; as 5 do ADR-029 são dobradas por nome nos perfis bloqueados que ainda têm as fotos (a confirmar com o
   dono); as 6 restantes viram personas sem conta; `test_social_memory.py:274` se aposenta de propósito.
   Alternativas: (a) manter as duas tabelas com o perfil dono de nome e nascimento e a persona dona da biografia
   (relatório 01, §8.i); (b) mudar a raiz para `personas.id` (implícito no vocabulário do dono). Fica a dobra porque
   **todo consumidor** (vínculos, sessões, memória, aprovações, `objectives.profile_id`, escopo de skill, treino,
   rotas) já é por `profile_id`; (b) rechavearia tudo e (a) mantém dois donos para a mesma pessoa.
2. **Modelo rico híbrido.** Colunas para identidade estável, `biography` JSON por seção, `traits` só voz, `visual`
   JSON, `generation` JSON; idade calculada; `biography` entra no prompt só em linhas curtas com `sem_marcacao`;
   religião e política guardadas e **não enviadas** até decisão do dono; backfill determinístico na migração;
   enriquecimento por IA separado, pago e idempotente. Alternativas: tudo em colunas (rígido para o que o dono ainda
   vai pedir); tudo em JSON sem tipo (relatório 03 desaconselha: sem validação, `extra="forbid"` não protege);
   `profile_attributes(key, value)` (relatório 03; bom para catálogo, ruim para leitura de tela). `interests` fica
   em `traits` (relatórios 01 e 05 o tratavam em lugares diferentes).
3. **Imagens.** `persona_images` (048) com receita por seed, `is_primary`, `source`; custo em `ai_calls.usd` com
   `role='image'` fora de `AI_ROLES`; porta `ImageGenerator` e adaptadores em `modules/identity/adapters/`; simulado
   primeiro, OpenAI `gpt-image-1-mini` segundo; `on_create: true` com `per_persona: 1` (o relatório 05 propunha
   `false`; o dono disse que a imagem é obrigatória); imagem 0 principal 1:1; imagens 2+ com referência; nome nunca
   no prompt; pós só para variação; sem chave o simulado responde e se declara; pago sem chave → 409; local (ComfyUI)
   adiado por GPU de 8 GB e ADR-023. Alternativas do relatório 05: Gemini, BFL, Replicate, local primeiro.
4. **Conta = entidade única (049)** e **uma tabela de sessão por (conta, aparelho)**, `account_sessions`. Unifica
   `account_sessions` por conta (relatório 01, PK só `account_id`) e `profile_device_sessions` por (persona, app,
   aparelho) (relatório 02): conta já é (persona, app), e a PK composta dá a dimensão aparelho sem tabela a mais.
   Vocabulário único; `authentication_attempts.account_id`; backfill sem recifrar e sem as 5 fantasmas;
   `credential_row/session_row/set_session` mantêm assinatura numa primeira fase; `instagram_credentials`/`sessions`
   só leitura até sair.
5. **Credenciais, opção A.** Saem `RunCreate.credentials`/`consent_credentials` e o campo do painel; fonte única é a
   credencial da conta, por nome lógico; o planejador recebe só a lista; dado não sigiloso vira variável resolvida por
   aparelho; segredo só por `type_secret` resolvido por (perfil do objetivo, app da etapa, host); ref nunca em
   `run_secrets`; apps com `SessionProvider` fora do `type_secret`; `tem_credencial` por app; consentimento por conta,
   com as 8 existentes marcadas pela 049 (§4.3); trava de site pelo `host` da conta, inclusive para `open_url`.
   Alternativa (B) do relatório 03: manter `credentials` só por API. Fica (A): "sem soluções paralelas" é o pedido, e
   a conta de portal ganha casa (`host`). Substitui em parte o ADR-025 (ADR-040); o ADR-025 não se reescreve.
6. **N:N evoluindo `device_profile_bindings` (051)**, com `app_id`, `is_primary`, os três índices novos, D2-a
   (duas contas do mesmo app no mesmo aparelho proibidas, achado #115), D3 (mesma conta em N aparelhos permitida com
   aviso; o ADR-029 protege), e a **ordem segura** (chamadores primeiro, índices depois). Alternativas do relatório
   02: tabela nova de vínculo (perderia localidade e histórico), troca de conta automática no Instagram (nunca foi
   implementada, de propósito), usuários Android múltiplos (não).
7. **Roteamento estendendo `RunCreate`**, sem forquilha: interseção, `targets`, `device_policy` padrão `one` (D4),
   `resolver_alvos` pura em tabela, `TargetExtractor` determinístico antes do `TemplateStage`, contradição vira
   pergunta, prévia obrigatória com origem por alvo e 409 `alvos_nao_confirmados`, fase 1 recusa o mesmo aparelho
   duas vezes. Alternativas: um endpoint novo "por persona" (forquilha), extração por LLM (não determinística, e o
   nome da persona não é instrução), `all` por padrão (efeito duplicado).
8. **Provisionamento local agora, remoto depois** (ADR próprio para o verbo `provision` e o inventário mutável do
   agente, ADR-031). Alternativa do relatório 02: fazer os dois de uma vez.
9. **Painel só depois da decisão sobre `cvv1rp`**, com contrato de página e container queries, Configuração sem
   `pageNarrow`, Foco em seções com `focusActionGroups`, persona com as guias da §9.4, modo "Por persona", "Criar
   aparelho"; aceite no navegador. Alternativa do relatório 04: só o passo A (CSS) e deixar a estrutura.
10. **Ondas** W0 → WA ∥ WB ∥ WD → WC → WE; deploy com nova autorização; produção sem `git pull`.

**Conflitos entre relatórios resolvidos aqui:**

- **Numeração das migrações.** O relatório 05 chamava `047_persona_images`; o relatório 02 chamava 047 o N:N. Fica
  047 persona-raiz, 048 imagens, 049 conta, 050 provisionamento, 051 N:N, na ordem das ondas.
- **Chave estrangeira das imagens.** O relatório 05 usava `persona_id` → `personas`; com a decisão 1 a FK é
  `profile_id` → `instagram_profiles`.
- **Sessão.** `account_sessions(account_id PK)` (01) × `profile_device_sessions` (02) → `account_sessions(account_id,
  instance_id)`.
- **`on_create`.** `false` (05) × pedido do dono → `true`, com `per_persona: 1`.
- **Base da branch `cvv1rp`.** O relatório 02 diz `730728d`; o merge-base com a `main` no commit base é
  `5c98735`, com 23 commits à frente. Fica o medido.
- **Caminho do Foco.** Os relatórios citam `FocusPanel.tsx` sem pasta ou em `features/devices/`; ele está em
  `features/focus/` (com `Focus.module.css`); `OperationalContextCard.tsx` está em `features/devices/`.
- **Onde `ProfileCreate` mora.** O relatório 01 cita `schemas.py:22-44`; o arquivo é
  `modules/identity/presentation/schemas.py` (K2).
- **Dobra × persona órfã.** O relatório 01 sugeria só as 3 com perfil e as 5 por decisão; o coordenador decidiu as 5
  por nome (a confirmar) e as 6 como personas sem conta.

---

## 15. Decisões pendentes do dono

1. **Provedor e chave de imagem, e o teto.** OpenAI `gpt-image-1-mini` (exige conta, verificação da organização e
   saldo próprio) ou outro; teto diário em `ai_max_usd_per_day` inclui imagem. Sem isto a WA entrega só o simulado.
2. **Autorizar a primeira geração real** de imagem e o primeiro `enrich` (custo estimado: US$ 0,15 para 14 imagens
   `medium`; US$ 0,4 para 14 enriquecimentos).
3. **Branch `claude/android-multiagentes-session-cvv1rp`** (23 commits sobre `5c98735`): integrar antes da WE,
   descartar, ou aproveitar por partes. A WE espera.
4. **Dobrar as 5 personas do ADR-029 por nome** nos perfis bloqueados que têm as fotos (o que a 047 faz), ou
   deixá-las como personas sem conta e as fotos com os perfis bloqueados (desfazer por script).
5. **Religião e posicionamento político:** só guardar (o que este desenho faz) ou também enviar ao modelo
   (`social`, planejador) e em que forma.
6. **Perfil sem conta Instagram** é aceito (é o que a decisão 1 supõe); confirmar que o Instagram deixa de ser
   obrigatório no cadastro.
7. **Duas contas do mesmo app no mesmo aparelho** ficam proibidas (D2-a) até haver troca de conta; confirmar.
8. **Deploy desta rodada**: nova autorização; ensaio com `scripts/deploy.ps1 -Ensaio` sobre cópia do banco; o
   checkout de produção não pode dar `git pull` depois que a 047 entrar na `main`.
9. **Provisionamento remoto**: autorizar ampliar o protocolo do worker (ADR-031) numa rodada seguinte.
10. **Dados pessoais no papel `social`:** hoje ele é proibido de escrever documento e endereço (`prompts.py:189`);
    liberar algum campo (cidade, profissão) é decisão do dono.

---

## 16. ADRs a registrar

Cada um entra em `docs/decisoes.md` quando a onda correspondente é integrada.

- **ADR-040 — A credencial da conta da persona substitui a credencial da execução** (WB; substitui em parte o
  ADR-025, que não se reescreve). Fonte única no cofre por conta; nome lógico; consentimento por conta; `type_secret`
  por (perfil do objetivo, app, host); ref nunca em `run_secrets`; `open_url` e a trava de site pelos hosts da
  conta; apps com `SessionProvider` continuam determinísticos. O que continua do ADR-025: o valor nunca vai ao
  modelo, a log, a evento, a evidência ou a memória; três travas; desafio, 2FA e CAPTCHA com a pessoa (ADR-009).
- **ADR-041 — Persona como raiz e conta única** (WA e WB). A persona é a linha de `instagram_profiles`; `personas`
  dobrada; modelo rico híbrido; religião e política guardadas e não enviadas; conta = `profile_accounts` +
  `account_credentials` com estado e consentimento; sessão por (conta, aparelho); vocabulário único; Instagram não é
  exceção arquitetural (o provedor de sessão é só quem autentica). Atualiza a nota do ADR-039 ("sessão por (perfil,
  app) proposta" → entregue como (conta, aparelho)).
- **ADR-042 — Imagens de persona** (WA). Porta `ImageGenerator` fora dos papéis de IA; receita determinística por
  seed com eixos de variação; principal 1:1 e variações com referência; nome nunca no prompt; simulado sem chave,
  pago sem chave recusa; custo em `ai_calls.usd` `role='image'`; original preservado; local adiado (ADR-023);
  publicar foto no app fora do escopo (ADR-022 quando entrar).
- **ADR-043 — Persona N:N aparelho** (WC). Vínculo por (persona, aparelho, app) com principal; D2-a e D3; ordem
  segura; `bind` não toma o aparelho; o portador do perfil no despacho é o objetivo. Atualiza a invariante da raiz
  `Profile` do design anterior (§6).
- **ADR-044 — Roteamento por persona e alvos no texto** (WC). `RunCreate` estendido, interseção, `device_policy`
  `one`, `resolver_alvos` pura, `TargetExtractor` determinístico antes do `TemplateStage`, prévia obrigatória com
  origem por alvo e 409 `alvos_nao_confirmados`, contradição vira pergunta, mesmo aparelho duas vezes recusado na
  fase 1.
- **ADR-045 — Provisionamento pela plataforma** (WD). `POST /api/instances` local com `origin='dynamic'`,
  `android_overrides`, `max_devices` e guarda de disco, `create` pelo despacho, `DELETE` só para dinâmica sem uso;
  remoto adiado para ADR próprio.
- **ADR-046 — Contrato de página e container queries** (WE). `Page/PageHeader/PageSection/TableWrap`; faixas por
  `@container page`, não por viewport; sem variante estreita; aceite de layout no navegador em quatro larguras com o
  Foco aberto e fechado; Foco em seções com grupos de ação puros.

---

## 17. Mapa dos critérios de validação do dono

O dono enunciou o pedido em treze pontos (§1); não há uma lista separada de 25 critérios, então os treze foram
**decompostos aqui em 25 critérios verificáveis**, para o coordenador substituir pela lista do dono se ela existir.
Nível de prova possível nesta rodada, sem autorização de mundo real, entre parênteses.

| # | Critério | Pedido | Onda | Como se prova | Nível possível |
|---|---|---|---|---|---|
| V1 | Configuração usa o mesmo invólucro e largura das outras telas | 1 | WE | vitest: `pageNarrow` inexistente e `Page` em uso; captura em 1366/1920 sem Foco | `simulated` (vitest); captura `not_run` até autorizar o servidor de desenvolvimento (§13) |
| V2 | Configuração responsiva em 375/1024/1366/1920 com Foco aberto e fechado, sem regra pontual | 1 | WE | vitest: regras só por `@container`; capturas | `simulated` (vitest); capturas `not_run` até autorização |
| V3 | Campo "Senha para a automação" e `RunCreate.credentials` removidos | 2 | WB/WE | `test_credenciais_da_conta.py`; `CommandPanel` sem o campo | `simulated` |
| V4 | Dados da persona e da conta chegam ao plano pela lista de nomes e são usados na etapa certa; segredo só por `type_secret` com consentimento | 2 | WB | execução simulada com `{perfil_email}` resolvido e `type_secret` da conta do Chrome; `_em_lugar_nenhum` | `simulated`; portal real `not_run` |
| V5 | Persona completa gerada a partir de um prompt | 3 | WA | `POST /personas/generate` com provedor simulado e com validações | `simulated`; real `not_run` |
| V6 | Imagem gerada ao criar a persona, coerente com os atributos | 4 | WA | receita contém os atributos; simulado gera; painel mostra | `simulated`; coerência real `not_run` |
| V7 | Variação natural entre personas (câmera, luz, ambiente, pose, expressão, produção, proporção, pós) | 4 | WA | teste: 20 seeds → distribuição dos eixos; receitas distintas por persona | `simulated` |
| V8 | Modelo e interface preparados para várias imagens por persona | 4 | WA/WE | `persona_images` N por perfil, principal única; galeria | `simulated` |
| V9 | Modelo rico com todos os campos pedidos e idade derivada | 5 | WA | esquemas `biography`/`visual`; `age` no DTO; PATCH por seção | `simulated` |
| V10 | Personas existentes migradas sem perda (14 → 14, voz e visuais preservados) | 5 | WA | `test_persona_raiz_migracao.py` no sintético; ensaio na cópia do banco | `simulated` |
| V11 | Autenticação e Contas unificadas; a credencial pertence à conta em cada app | 6 | WB/WE | `account_credentials` com estado e consentimento; uma guia "Contas e acesso" | `simulated` |
| V12 | Instagram não é exceção: mesma tabela de conta e de sessão; nada escrito nas tabelas antigas | 6 | WB | `test_instagram_auth.py` verde lendo pela conta; grep de escrita | `simulated`; login real `not_run` |
| V13 | Hierarquia Servidor → Aparelho → Persona(s) legível na API e no painel | 7 | WC/WE | `operational-context` com `profiles[]` e `accounts[]`; Foco em seções | `simulated` |
| V14 | Persona N:N aparelho (apps distintos no mesmo aparelho; uma persona em N aparelhos) | 7 | WC | `test_vinculos_n_n.py`; 051 | `simulated` |
| V15 | Persona → aparelhos, contas, imagens | 8 | WA/WE | `PersonaDTO` v2 (`devices[]`, `accounts[]`, `images[]`); guias | `simulated` |
| V16 | Aparelho → personas, contas, apps, servidor | 8 | WC/WE | `contexto_do_aparelho`; seções do Foco | `simulated` |
| V17 | Comando por persona com o sistema escolhendo o aparelho | 9 | WC | tabela A–N de `resolver_alvos`; `POST /runs` com `profile_ids` | `simulated`; real `not_run` |
| V18 | Caminho direto por aparelho mantido | 9 | WC | os testes atuais de `POST /runs` com `instance_ids` seguem verdes | `simulated` |
| V19 | O texto pode dizer destinos, sempre com prévia | 9 | WC | `test_alvos_no_texto.py`; 409 `alvos_nao_confirmados` | `simulated` |
| V20 | Provisionar nova instância pela plataforma | 10 | WD | `POST /api/instances` com backend falso; `seed()` recarrega | `simulated`; AVD real `not_run` |
| V21 | Painel lateral do aparelho com a hierarquia | 11 | WE | `FocusPanel.test.tsx`; capturas | `simulated` (vitest); capturas `not_run` até autorização |
| V22 | Ações rápidas e Interação manual reestruturadas em grupos | 12 | WE | `focusActionGroups` puro testado; capturas | `simulated` (vitest); capturas `not_run` até autorização |
| V23 | Coerência ponta a ponta: rotas antigas como apelidos, DTOs superconjunto, docs e regras atualizadas | 13 | todas | `test_contrato_http.py`, `test_cobertura_de_rotas.py`, `docs-check` | `simulated`/`real` (docs) |
| V24 | Migrações 047–051 validadas nos dois dialetos com dados preservados | 13 | WA–WC | testes de atualização; CI PostgreSQL; ensaio na cópia de produção | `simulated` |
| V25 | Suíte verde e testes novos por onda; nada implantado sem autorização | 13 | todas | pytest SQLite e PostgreSQL; vitest; `not_run` registrado onde falta prova real | `simulated` |
