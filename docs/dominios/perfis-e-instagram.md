# Domínio: perfis, Instagram e treinamento

Como uma identidade social é representada, o que ela sabe fazer, com que limites, e como o Instagram — o único
app com login determinístico hoje — é operado sem nunca resolver um desafio sozinho. Para o contrato HTTP, ver
[`../api-contract.md`](../api-contract.md); para o banco, [`../banco.md`](../banco.md).

## A persona é a pessoa

Desde a migração 047 (evolução 2, onda A; [ADR-041](../decisoes.md#adr-041--a-persona-é-a-pessoa-instagram_profiles-como-raiz-personas-dobrada-username-opcional-por-string-vazia-e-reconstrução-com-foreign_keys-off))
**perfil e persona são o mesmo objeto**: a linha de `instagram_profiles`. O documento principal do assunto é
[`persona.md`](persona.md); aqui fica o resumo e o que toca contas e sessão.

- **Um DTO.** `PersonaDTO` (`backend/app/models.py`) é a pessoa inteira; `InstagramProfileDTO` é o mesmo objeto pelo
  nome antigo (`InstagramProfileDTO = PersonaDTO`). `PersonaVoiceDTO` é o recorte que o construtor de contexto vê.
  `persona_id` e `profile_id` do DTO são o próprio `id`; `persona_name` é alias de `name`.
- **Identidade** nas colunas (`username`, `display_name`, `first_name`, `last_name`, `birth_date`, `email`, `gender`,
  `locale`, `status`); **voz** em `summary`, `persona_prompt`, `traits` (`PersonaTraits`, só como a pessoa escreve);
  **biografia** em `biography` (`PersonaBiography`, por seção, com `schema_version`); **visual** em `visual`
  (`PersonaVisual`); **proveniência** em `generation` (`manual` | `ai` | `legacy_persona`); **imagens** em
  `persona_images` (migração 048), com uma principal por pessoa.
- **`username` é opcional pela convenção `''`** (coluna `NOT NULL DEFAULT ''`; índice único parcial
  `ux_instagram_profiles_username ON instagram_profiles(lower(username)) WHERE username <> ''`; `''` ↔ `null` em
  `social/repository.py::campos_de_persona`). Uma pessoa pode existir antes de ter conta em app nenhum.
  `GET /api/instagram/profiles` lista só quem tem conta (`repository.list_profile_ids`); `GET /api/personas` lista
  todas (`list_persona_ids`).
- **O que vai ao prompt** (`social/context.py::SocialContextBuilder._persona_block`, tudo por `sem_marcacao`):
  `@username`, nome, idade calculada (`idade_em`), as linhas de `PERSONA_BIO_FIELDS` (`home.city`,
  `work.profession`, `work.education`, `tastes.hobbies`), resumo, os traços de `PERSONA_VOICE_TRAITS` e
  `persona_prompt`. **Não vai**: `beliefs.religion`/`politics` (decisão do dono pendente), `tastes.interests` (já vai
  pela voz), o resto da biografia, `visual`, `generation`, `email`, `birth_date` cru.
- **A coluna `persona_id` e a FK para `personas` ficam** como rastro da linha legada que a pessoa absorveu (índice
  único parcial `ux_profiles_persona ON instagram_profiles(persona_id)`, migração 009). A tabela `personas` está sem
  leitores e sai numa migração posterior. `repository.persona_row` ainda resolve o id legado.
- **Rotas.** `/api/personas` é a canônica (`list/create/get/patch/delete`, `POST /generate`, `POST /{id}/enrich`,
  `/{id}/images…`, `POST /{id}/preview`). `/api/instagram/profiles*` não são apelidos puros: `ProfileCreate` cadastra
  uma **conta** (exige `username`; `persona_id` de pessoa sem conta adota a pessoa, `repository.adopt_account`),
  `PersonaCreate` cria uma pessoa sem conta; `ProfilePatch.persona_id` absorve uma pessoa sem conta
  (`SocialService._absorver_persona`), `PersonaPatch` mescla por seção (`mesclar_secao`).
- **`persona_in_use` (409)** hoje significa: no `DELETE /api/personas/{id}`, pessoa vinculada a aparelho ou com
  execução em curso (`SocialService.delete_persona`; apagar a persona é apagar a pessoa, com contas, credencial e
  memória); na adoção e na absorção, o `persona_id` é de outra pessoa **com** conta.
- **Conta única** (onda B, migração 049,
  [ADR-040](../decisoes.md#adr-040--a-credencial-pertence-à-conta-da-persona-e-a-execução-não-carrega-credencial)):
  `profile_accounts` + `account_credentials` são a fonte da credencial, e `account_sessions` a da sessão, por (conta,
  aparelho). O documento principal é [persona § Contas e acesso](persona.md#contas-e-acesso); aqui fica o que toca
  o provedor do Instagram e as tabelas legadas.

## Contas por app (item 12.1)

Antes, um "perfil" era só uma conta do Instagram. A migração `037_contas_por_app.sql` cria `profile_accounts`
(perfil × app: `handle`, `status` `active|disabled`, `session_status` `unknown|session_ready|logged_out|needs_person`)
e `account_credentials` (cofre, separado da credencial do Instagram); faz backfill da conta Instagram existente
de cada perfil como a primeira `profile_accounts` (a **conta âncora**, que não pode ser removida —
`delete_account` recusa com `anchor_account`); e acrescenta `app_id` a `memory_items`, `social_interactions`,
`pending_approvals`, `steps`, e `app_ids` a `runs` — memória, aprovação e etapa passam a saber de QUAL app o
fato é.

**Desde a 049 (onda B)** a conta é a entidade única, para app com e sem provedor:

- `profile_accounts` ganha `host` (conta de portal no navegador; unicidade por perfil, app e host);
  `account_credentials` ganha estado, falhas, bloqueio, `last_used_at` e o **consentimento por conta**
  (`consent_at`, `consent_by`); a sessão mora em **`account_sessions`**, uma por (conta, aparelho), com o vocabulário
  único de `models.SessionStatus` (`auth_required` é o antigo `logged_out`; `needs_person` entrou no enum).
  `profile_accounts.session_status` (037) ficou na tabela sem leitor.
- `SocialService._account_dto` lê **uma fonte só**: a credencial em `account_credentials` e a sessão de
  `SocialRepository.session_of_account` (no aparelho vinculado; sem linha nele, a mais recente). `automated_login`
  continua dizendo se o app tem provedor (`session_provider_of(package) is not None`, fase K1; ver
  [`apps-e-loja.md`](apps-e-loja.md#manifesto-de-app-fase-k1)); `update_account` continua recusando `session_status`
  em app com provedor (`session_managed`), grava a marcação da pessoa na sessão do aparelho vinculado (sem vínculo,
  409 `no_binding`) e recusa `logged_out` (422: o vocabulário é o único). É o que diferencia Instagram (login pelo
  sistema) de Outlook/TikTok/Facebook (login pela pessoa, ou pela senha da conta via `type_secret`, ADR-040).
- **Tabelas só leitura**: `instagram_credentials` (008) e `instagram_sessions` não recebem mais `INSERT`/`UPDATE`
  (`security/rekey.py` atualiza `account_credentials.key_id`); saem numa migração posterior, com `run_secrets`.
  Enquanto a linha legada referenciar a mesma `secret_ref`, apagar a credencial da conta não apaga o segredo do
  cofre (`SocialService._apagar_credencial`).
- Rotas: `GET/POST /api/instagram/profiles/{id}/accounts`, `PATCH/DELETE …/accounts/{aid}`, `PUT/DELETE
  …/accounts/{aid}/credential`, `POST …/accounts/{aid}/credential/consent`,
  `POST …/accounts/{aid}/session/{connect|verify|logout}`, `GET …/accounts/{aid}/auth-attempts`; as antigas por
  perfil são apelidos da conta âncora ([contrato v0.28](../api-contract.md#adendo-v028-27092026--conta-única-credencial-com-consentimento-e-sessão-por-conta-e-aparelho-adr-040)).

## Memória

`backend/app/social/memory.py` (`MemoryStore`) — três regras: isolamento por `profile_id`; só interação
`confirmed` vira memória (`learn_from`); segredo nunca é memorizado (`looks_secret`/`mentions_credential`
checados tanto em `remember()` quanto em `absorb_observation()`). `fingerprint()` normaliza assunto+conteúdo — é
o que permite fundir (`merge_memory`) em vez de duplicar. `recall()` recupera por relevância + importância +
confiança + recência, respeitando um orçamento de tokens. Tabelas: `memory_items`, `relationship_summaries`,
`thread_summaries` (migração `009_social_memory.sql`), com busca textual (FTS5 no SQLite, `tsvector` gerado no
PostgreSQL). Rotas: `GET/POST /api/instagram/profiles/{id}/memory`, `DELETE .../memory/{memory_id}`.

## Observação de tela

`backend/app/social/observacao.py` — sem custo de IA, opera sobre a árvore de UI que o executor já tem. Extrai
conteúdo visível (filtrando ruído, campos editáveis/senha, ids de interface), o assunto da tela (de quem ela
fala) e monta um fato ("Vi na tela em DD/MM (etapa): ..."), usando o DIA (não a hora) como parte da chave de
fusão — para fatos do mesmo dia colidirem por fingerprint em vez de duplicar. `SocialService.remember_screen`
(`social/service.py`) chama isto depois que uma etapa é COMPROVADA, descartando tela sensível e aparelho-loja
antes. É genérico por app — não depende do classificador do Instagram.

## Políticas, limites e grupos de acesso (item 11.10)

`backend/app/social/policy.py` (`PolicyEngine`) decide se uma ação pode sair AGORA: teto por hora/dia (com
aquecimento — `warmup_days`/`warmup_percent`), teto por execução, cooldown entre ações externas, e um
"fleet gate" (achado #114) que coordena várias contas da frota sobre o MESMO alvo (`fleet_target_window_s`,
`fleet_max_accounts_per_target`, `fleet_min_spacing_between_accounts_s`). A política de um perfil vem em três
camadas, nesta ordem: **escolha própria → grupo → padrão** (`_own`/`_group`/`origin_for`); `limits_origin()`
diz de onde veio o limite efetivo, para a UI mostrar a origem por ação.

Migração `036_grupos_de_acesso.sql`: `policy_groups` (capabilities e limits em JSON — só o que DIFERE do
padrão) e `instagram_profiles.policy_group_id` (um grupo por perfil, sem FK — desvínculo é manual no serviço).
`SocialService._aplicar_politica` avisa quando um afrouxamento atinge ação de risco alto, tanto para escolha
própria quanto para grupo.

Rotas: `GET/PUT /api/instagram/profiles/{id}/policy`, `GET /api/instagram/policy-groups`,
`GET /api/instagram/policy-defaults` (os limites-padrão que o editor de grupo usa como ponto de partida),
`POST/GET/PUT/DELETE /api/instagram/policy-groups[/{group_id}]`.

## Aprovações

`backend/app/social/approvals.py` — `ApprovalStore` guarda o pedido (`pending_approvals`, migração
`010_capabilities_approvals.sql`: `status` `pending|approved|edited|rejected|expired`, com
`generated_content`/`approved_content` separados para auditoria). `ApprovalService.decide()` tem três verbos:
`approve`, `edit` (exige texto novo), `reject` (cancela só as etapas daquele alvo, via
`cancel_target_steps`) — nenhum verbo marca a etapa como concluída sozinho. `textos_irmaos()` evita que duas
contas da mesma execução escrevam o mesmo texto. Rotas: `GET /api/approvals`, `POST /api/approvals/decide`
(lote), `POST /api/approvals/{id}/decide`.

## Instagram: classificador, login determinístico, sessão, desafios manuais

Desde o ADR-052 o Instagram não tem código próprio: é a pasta de dado
`backend/app/conhecimento/apps/com.instagram.android/`, lida por motores genéricos, DETERMINÍSTICOS e fora do laço
de IA, que não conhecem app nenhum ([abaixo](#o-instagram-como-dado-adr-052); [design](../design/conhecimento-de-app.md)).
Caminhos relativos a `backend/app/`.

- **Telas** — `telas.yaml` (sinais por idioma en/pt, regras de tela em ordem de precedência, a extração
  `conta_no_cabecalho`, o estado conhecido `feed`/`profile`), lido por
  `automation/conhecimento_de_telas.py::classificar`. O enum `Screen` não existe mais: os nomes de tela são os do
  arquivo (`challenge`, `two_factor`, `save_login_prompt`, `account_switcher`, `login`, `loading`, `inbox`,
  `profile`, `feed`, `thread`, `comments`, `post`, `search`; sem regra que case, `desconhecida`), e cada um tem um
  tipo do vocabulário fechado do motor (`TIPOS`: `desafio`, `dois_fatores`, `intersticial`, `login`, `carregando`,
  `autenticada`) — é pelo tipo que o núcleo decide. Estrutura primeiro, texto só desempata: ids do Instagram são
  ofuscados e mudam de versão, então as regras usam prefixos do final do resource-id e o atributo `password`. A
  primeira regra que casar vence, e o arquivo declara `challenge`/`two_factor` ANTES de `login`, de propósito:
  confundir os dois faria o sistema digitar senha numa tela de código.
- **Leitura para o rascunho** — a seção `leitura` do `app.yaml`, lida por
  `automation/leitura_de_tela.py::LeituraDeclarada` (`visible_content`, `comment_of`, `message_of`): fala só com o
  autor casado na mesma linha — vazio significa "escreva sem isto", nunca "invente"; tela sensível sempre devolve
  vazio. As três regras são do motor, não do app.
- **Login e sessão** — `sessao.yaml` (rótulo, ajustes padrão, formulário, dispensa, aba de perfil, a tabela
  `depois_do_envio` e os textos de ajuda), validado na carga por `integrations/app_declarado/conhecimento.py`
  (`do_app(pacote)`) e executado por `integrations/app_declarado/sessao.py::SessaoDeclarada`, o `SessionProvider`
  genérico que substituiu o `InstagramAuthenticator`. `ensure_session()` é a entrada: reaproveita sessão existente,
  dispensa intersticiais (só o botão de RECUSA declarado em `dispensa`, nunca aceitar/permitir; a geometria é de
  `formulario.py`), volta ao estado conhecido e trata desafio/2FA ANTES de tentar qualquer login. `_login()` confere
  o campo de usuário antes de prosseguir (até `FILL_TRIES` = 3 tentativas, e só envia com o valor conferido) e
  preenche a senha pelo canal sensível; `_watch_after_submit` só observa, nunca reenvia por timeout, e
  `classificar_depois_do_envio` aplica a tabela declarada. `_wrong_account()` é **sempre** intervenção humana —
  comentário no código (achado #115) registra que a troca automática de conta nunca foi implementada de propósito.
  `_challenge()` grava `SessionStatus.auth_challenge` e devolve `Outcome.AUTH_CHALLENGE`: **não existe caminho de
  código que resolva desafio ou 2FA automaticamente.** `_needs_person(profile_id, instance_id)` impede o agendador de
  sequer tentar de novo quando o estado já pede pessoa (desde a 049 lê a sessão da conta **neste** aparelho);
  `_blocked_reason()` recusa entrar sem `consent_at` na credencial da conta (ADR-040). Tetos, cooldown e prazos são
  os `ajustes` do `sessao.yaml` com a sobrescrita da instalação por cima (`contas.sessao.<pacote>.<ajuste>` no
  `config.yaml`, entregue por `Config.ajustes_de_sessao`), lidos a cada uso. `Outcome` (StrEnum: `SESSION_READY, INVALID_CREDENTIAL, AUTH_CHALLENGE, WRONG_ACCOUNT, RETRYABLE,
  UNCERTAIN`) mora no mesmo módulo; `Outcome.terminal` inclui `AUTH_CHALLENGE`.
- **Conta aberta** — `sessao.py::ler_conta` lê a conta SÓ na tela de perfil declarada (`conta.tela_de_perfil`),
  depois de tocar na aba de perfil declarada (`conta.aba`), nunca pelo feed (bug histórico de confundir autor de
  post com dono da conta, K-021).
- `emit_needs_person_change()` (`modules/identity/application/session_rules.py`, chamada por
  `SessaoDeclarada._save`) dispara o evento `session.needs_person` (fila "Aguardando intervenção");
  `PRECISA_DE_PESSOA = (auth_challenge, wrong_account)` ([abaixo](#sessionprovider-e-o-registro-por-pacote-fase-k1)).

**Desafio bloqueia o perfil sozinho (ADR-029, 27/09).** Na entrada da sessão em `auth_challenge`, o perfil passa
de `active` a `blocked` (`modules/identity/application/session_rules.py::bloquear_por_desafio`, chamado por
`SessaoDeclarada._save` e por `AppState._sessao_desmentida`). A porta de sessão e a distribuição já recusam
perfil fora de `active`. Pausa do dono (`disabled`) não é reescrita. Resolver a tela não reativa sozinho: quem
reativa é a pessoa, na tela do perfil.

**Proteção de contas (ADR-055, 29/09; integrada em `c359f65`, a implantar).** A regra do dono: parar em "Confirm you're
human" é sinal de conta perdida. O que muda no desafio e em volta dele:

- **Conta travada = `blocked`.** Um detector único (`automation/hierarchy.py`,
  `conhecimento_de_telas.py::detectar_conta_travada`) casa na união dos idiomas, com o texto normalizado (apóstrofo
  tipográfico, acento), e roda depois de cada observação e na sessão, antes do ANR, da receita e do ator; sai sem tocar.
  Subtipos do `auth_challenge`: `conta_travada` bloqueia o perfil sozinho; `codigo` (login por e-mail, 2FA) e
  `verificacao` (o ator relatou com `step_blocked(kind="challenge")`) pedem pessoa SEM bloquear — o código de 18/09
  teria bloqueado bruno e andre, que estão vivos. O bloqueio vale também com a sessão já em `auth_challenge`; reativar é
  da pessoa. Todo status passa por `mudar_status` (`blocked_at`, `blocked_evidence`, `blocked_origin`, evento
  `profile.status`).
- **Quarentena do aparelho.** A conta travada logada vira marcador do APARELHO (`device_locked_accounts`, migração 054),
  que sobrevive ao desvínculo: sem confirmação explícita (`confirm_locked_account`), só parar e hibernar; nenhuma outra
  persona se vincula ali (409 `aparelho_em_quarentena`); nada de entrega, escada de reparo nem reinício por irq;
  `/api/health` acusa `locked_account_on_device`. `account_label` passa a ser derivado do marcador ou do vínculo, e
  antes de mexer num aparelho com Instagram confere-se a tela (K-053).
- **Uma conta por alvo.** Seguir, DM e comentário: no máximo uma conta por pessoa numa janela de 30 dias, com o
  excedente recusado; curtida e comentário ganham o alvo (`post_author`, herdado de OPEN_POST); um pedido igual a várias
  contas na mesma execução segue numa conta só, com aprovação. Quando uma conta cai, o disjuntor pausa as que agiram
  sobre os mesmos alvos nas 48 h anteriores (K-057).
- **DM fria com aprovação.** SEND_MESSAGE para quem nunca escreveu a esta conta é sempre `approval_required`, sem grupo
  nem perfil que afrouxe; a persona não atribui fala a terceiros (`social/conteudo.py`). "Sending…" é pendente, nunca
  enviada, e confirmar à mão uma etapa com efeito exige o print (`evidence_id`).
- **Login com freio.** Perfil `blocked` ou `disabled` nunca recebe a senha; um envio sem sucesso põe a credencial em
  `review` e o login automático para até a pessoa olhar; teto diário `max_logins_per_day` no `sessao.yaml` (3). Nenhum
  reset automático em aparelho com conta (a escada para em "Precisa do dono" + `stop`).
- **Conduta, não disfarce.** Nada de mascarar emulador ou rede, proxy, resolver CAPTCHA ou tocar em "Get support": é
  evasão, proibida (ADR-009).

**Garantia de "desafio sempre manual"**, com os pontos exatos. No motor, valendo para qualquer app, seja qual for o
dado:

- `sessao.py::SessaoDeclarada.ensure_session` checa `estado.tipo in TIPOS_DE_DESAFIO` (`desafio`, `dois_fatores`)
  antes de chegar a `_login`; `_challenge` sempre devolve `AUTH_CHALLENGE`; `_needs_person` barra nova tentativa
  automática; `Outcome.terminal` inclui `AUTH_CHALLENGE`; `_apply_verdict` conta para o teto de tentativas todo desfecho
  depois do envio que não seja sucesso nem senha recusada (essa bloqueia direto, sem contar);
- `conhecimento.py::DESFECHOS_DEPOIS_DO_ENVIO` não tem `retryable` nem `session_ready`: depois que a senha foi
  enviada, nenhuma regra do dado autoriza repetir o envio, e sucesso só sai de `conferir_conta`, com a conta lida na
  tela. O carregador recusa desfecho fora do vocabulário, e o vocabulário não tem nada que responda a um desafio;
- `automation/conhecimento_de_telas.py::voltar_ao_estado_conhecido` nunca "volta" de `NAO_SE_VOLTA` (desafio, 2FA,
  login, intersticial, carregando);
- `automation/hierarchy.py::_DESAFIO` marca como sensível, em qualquer app, a tela de desafio (sem imagem para o
  modelo, dígitos mascarados). Fica no código, e as telas sensíveis declaradas por app ficam em
  `config.yaml: sensitive_screens`, fora do pacote do app: são segurança, e um pacote de dado não as afrouxa.
  `backend/tests/test_sensitive_input.py` confere, string por string, que `_DESAFIO` concorda com os sinais
  `challenge`/`two_factor` do `telas.yaml` do Instagram.

No dado: a ordem `challenge`/`two_factor` antes de `login` é declarada no `telas.yaml` (o carregador não a impõe);
para o Instagram, quem a garante é `backend/tests/test_instagram_auth.py::test_challenge_e_dois_fatores_vem_antes_do_login`.
Testes: `backend/tests/test_instagram_auth.py` (fixture `backend/tests/fake_instagram.py`, pacote lido por
`backend/tests/pacote_instagram.py`) e, para um app novo só em dado,
`backend/tests/test_sessao_declarada.py::test_um_app_novo_para_no_desafio_e_chama_a_pessoa`.

## `SessionProvider` e o registro por pacote (fase K1)

A fase K1 tirou do núcleo as comparações com `"instagram"`: quem abre e confere a conta de um perfil num app é o
provedor de sessão daquele app, achado no registro
([design](../design/evolucao-arquitetural.md) §2.5, §7 e §16;
[ADR-039](../decisoes.md#adr-039--manifesto-de-app-e-registro-de-sessionprovider)). Código: `99d851b` e `40def91`,
integrados em `f06e34a`, mais a correção `3fbe9df`. Caminhos relativos a `backend/app/`.

**A porta.** `modules/identity/application/ports.py::SessionProvider`:

- `package` (propriedade) e `async ensure_session(rt, profile_id, *, force_login=False, automatic=False,
  observe_only=False)`, que devolve um `SessionOutcome` (`ready`, `detail`);
- `automatic=True` é a chamada do agendador; `observe_only=True` é "Verificar conta" e a reobservação (nunca
  autentica); `force_login=True` refaz o login;
- as garantias continuam do provedor: nunca repete envio por timeout, nunca segue com conta errada, nunca resolve
  desafio (ADR-009), senha só pelo canal sensível e só com o consentimento da conta (ADR-025/040);
- a implementação de produção é o motor genérico `integrations/app_declarado/sessao.py::SessaoDeclarada`, montado
  com o conhecimento de cada pasta de app que tem `sessao.yaml` pela fábrica do manifesto
  (`integrations/app_declarado/pacote.py::fabrica_de_sessao`; ADR-052, [acima](#instagram-classificador-login-determinístico-sessão-desafios-manuais));
  em teste, também `backend/tests/fake_dois_apps.py::SessaoDoQa`.

**O registro por pacote.** `modules/identity/application/sessions.py::SessionProviders`:

- o registro de apps é global e guarda a **fábrica** que cada manifesto declara (`registry.py::session_factory_of`);
- `SessionProviders.for_package(pacote)` fabrica o provedor na primeira pergunta, com as dependências da composição
  (`modules/identity/infrastructure/sessions.py::SessionDeps`: config, aparelhos, repositório social, cofre, canal
  sensível, eventos), e devolve a **mesma** instância depois, enquanto a fábrica registrada for a mesma. Fábrica
  nova (registro de novo) → provedor novo; app sem fábrica → `None`;
- `SessionProviders.has(pacote)` responde sem fabricar nada;
- a composição (`state.py::AppState.__init__`) o guarda em `self.sessoes`.

**Quem usa.**

| Chamador | Pergunta |
|---|---|
| `AppState._session_gate(rt, package)` | o provedor do pacote do item; sem pacote (chamador antigo), `provedor_do_perfil()` |
| `AppState._reobservar_apos_intervencao` | `provedor_do_perfil()`, com `observe_only=True` |
| `AppState._sessao_apos_mudanca_de_app` | `sessoes.has(pacote)`: só app com provedor invalida sessão |
| `api.py::_start_session_job` ("Conectar", "Verificar conta", por conta ou pelo apelido por perfil) | `sessoes.for_package(conta.package)` (onda B); sem provedor, 409 `no_session_provider`; sem consentimento na credencial, 409 `consentimento_de_credencial` |
| `modules/execution/infrastructure/command_bus.py` (recursos, fase H) | `sessoes.for_package` pelo pacote do app; sem provedor, recusa |

- `AppState.provedor_do_perfil()` é o provedor do pacote de `social_repo.app_package`, que vem do app âncora do
  perfil no registro (`modules/applications/infrastructure/registry.py::pacote_ancora`: o app cujo `app.yaml` diz
  `ancora_do_perfil: true`; no máximo um, e dois é erro — ADR-052, fatia 4). Antes vinha de
  `package_of_provider("instagram")`, com o `cfg.file.instagram.package` de reserva.
- `AppState.instagram` virou propriedade: o nome antigo de `provedor_do_perfil()`, o **mesmo** objeto da porta. Um
  teste que troca um método dele troca para todos.
- O 409 `no_session_provider` passou a ser alcançável na onda B: `POST …/accounts/{aid}/session/connect` numa conta
  de app sem provedor o devolve ([contrato v0.28](../api-contract.md#adendo-v028-27092026--conta-única-credencial-com-consentimento-e-sessão-por-conta-e-aparelho-adr-040)).

**A sessão é da conta num aparelho (049).** `account_sessions(account_id, instance_id)` substitui a sessão única por
perfil. O provedor continua sendo chamado por `profile_id` (`ensure_session(rt, profile_id, …)`, desvio anotado no
ADR-040): `SocialRepository.session_row(profile_id, instance_id)` e `set_session(profile_id, instance_id=…)` resolvem a
**conta âncora** do perfil (a do app que provê a conta dele) e gravam no par (conta, aparelho); `state.py` lê a
sessão do aparelho do item. `invalidate_sessions_of_instance` invalida, por padrão, só as contas do app âncora
naquele aparelho (com `package`, as do app dito). Consequências:

- desde a 047 a pessoa vinculada a um aparelho pode **não ter conta** no app do item: `AppState._session_gate` recusa
  com "não tem conta em <app>" sem tocar o aparelho nem autenticar (`state.py::AppState._tem_conta_no_app`: a verdade
  é `profile_accounts`; o `username` de cadastro continua contando como a conta do Instagram para perfis anteriores
  à 037). Teste: `backend/tests/test_persona_unificada.py::test_porta_de_sessao_responde_sem_conta_para_a_pessoa_sem_conta_no_app`;

- hoje, um app com login gerenciado por perfil: o Instagram. A sessão por (perfil, app, aparelho) que o ADR-039
  propunha existe (`account_sessions`), mas `ensure_session` ainda acha a conta pelo perfil: dois apps com login
  gerenciado **no mesmo perfil** pedem que o provedor receba a conta (pendente, com decisão do dono no item 12.3);
- a checagem "tela contradiz a sessão" (`taskqueue/executor.py::StepExecutor._sessao_desmentida`) vale para
  qualquer app com provedor (`session_provider_of(package) is not None`; antes, só o pacote do Instagram). Em
  produção é idêntico, porque o Instagram é o único com provedor.

**As regras de sessão do perfil.** `modules/identity/application/session_rules.py`:

- `bloquear_por_desafio` (ADR-029) e `emit_needs_person_change` (achado #106), com o corpo literal que morava no
  autenticador do Instagram. São regras do perfil (design §6, raiz `Profile`), e o núcleo importava o autenticador
  só para aplicá-las. Hoje os dois chamadores são o motor de sessão (`SessaoDeclarada._save`) e
  `AppState._sessao_desmentida`;
- o texto do motivo recebe o rótulo do app (`motivo_do_bloqueio_por_desafio(app_label)`): o motor passa o `rotulo`
  do `sessao.yaml`, e `AppState._sessao_desmentida` passa o `label` do registro;
- `PRECISA_DE_PESSOA` (`auth_challenge`, `wrong_account`) decide quando o evento da fila dispara;
- as portas que elas usam: `ports.py::ProfileStore` (`profile_row`, `update_profile`) e `ports.py::EventSink`
  (`emit`).

**Desvio do desenho.** O classificador de tela da §7 ficou fora da porta: nenhum código do núcleo o consumiria sem
mudar comportamento. Desde o ADR-052 ele é o motor genérico `automation/conhecimento_de_telas.py::classificar`, sobre
o `telas.yaml` de cada app. A detecção genérica de desafio (`automation/hierarchy.py::_DESAFIO`) vale para qualquer
app, de propósito.

Provas (`simulated`): os testes de sessão, desafio e ADR-029 seguiram verdes sem mudar asserção
(`backend/tests/test_instagram_auth.py` e vizinhos), também depois de o login virar dado (ADR-052); a porta por
pacote com dois apps em
`backend/tests/test_app_novo_pelo_manifesto.py::test_app_novo_entra_so_pelo_registro_com_provedor_e_catalogo`; a
forma da porta em `backend/tests/test_dubles_cumprem_as_portas.py` (o `SessaoDeclarada` com o conhecimento do
Instagram e o `SessaoDoQa`). Conta real e PostgreSQL: `not_run`.

## Modo treinamento (itens 13.1–13.3)

A pessoa faz a tarefa no aparelho, pelo Foco, e a IA generaliza a gravação em habilidade reaproveitável.

- **`backend/app/training/recorder.py`** (`TrainingRecorder`) — `start()` exige controle manual do aparelho
  (`lease_id`), recusa loja e gravação dupla; `record()` grava cada toque/texto com o elemento sob o dedo,
  descartando texto de campo sensível/senha/código (`parece_senha_ou_codigo`); devolver o controle encerra a
  gravação. Rota: `POST /api/instances/{id}/training`.
- **`backend/app/planning/training.py`** (`TRAINER_SYSTEM`) — prompt que generaliza a gravação em
  `command_template` + `parameters` + `steps` (com `postcondition` e `capability`/`bindings`) + `discarded` +
  `questions`. Uma chamada de modelo por proposta.
- **`backend/app/training/skills.py`** (`TrainingSkills`) — `propose()` chama o gerador; `save()` valida (comando
  sem parâmetros colados; etapa com efeito externo num app com catálogo TEM que apontar uma capability — senão
  `capability_required`) e grava via `FlowStore.learn_from_plan` + `flow_scope` (escopo por perfis/grupos —
  sem linha, vale para todos). Rotas: `GET /api/training`, `GET/POST /api/training/{session_id}` e
  `/stop`/`/propose`/`/save`/`/discard` (`backend/app/api.py:342-410`).

Migração `038_modo_treinamento.sql`: `training_sessions` (`status`: `recording|recorded|proposed|saved|discarded`),
`training_inputs` (`type`: `tap|long_press|swipe|text|key|open_app`), `flow_scope`, `flows.source` (`'run'` ou
`'training:<sessão>'`).

## O Instagram como dado (ADR-052)

O dono pediu "zero Python por app", e o Instagram deixou de ter código: `integrations/instagram/` e
`planning/catalog/instagram.py` foram apagados. Tudo o que a plataforma sabe dele está em
`backend/app/conhecimento/apps/com.instagram.android/`, lido pelos motores genéricos descritos
[acima](#instagram-classificador-login-determinístico-sessão-desafios-manuais)
([design](../design/conhecimento-de-app.md)):

| Arquivo | O que declara | Quem lê |
|---|---|---|
| `app.yaml` | nome, rótulo, `provedor_de_sessao: instagram`, perfil e internet obrigatórios, `ancora_do_perfil: true`, `links_de_perfil` (hosts e caminhos reservados), `tipos_de_texto`, `leituras_de_conversa`, `leitura` (rascunho) | `integrations/app_declarado/pacote.py`, `automation/leitura_de_tela.py` |
| `telas.yaml` (fatia 1) | sinais por idioma, regras de tela, extrações (conta no cabeçalho), estado conhecido | `automation/conhecimento_de_telas.py` |
| `catalogo.yaml` (fatia 2) | as 23 ações, `contract_version: 1` | `planning/capabilities.py::carregar_catalogo` / `catalogo_do_pacote` |
| `sessao.yaml` (fatia 3) | login: ajustes padrão, formulário, dispensa, aba de perfil, desfechos depois do envio, textos de ajuda | `integrations/app_declarado/conhecimento.py`, `sessao.py` |

- **Fatia 1.** Entraram conversa, post, comentários e busca como telas autenticadas, e a checagem de sessão, fora do
  estado conhecido (feed ou perfil), volta até ele (voltar do Android, no máximo uma reabertura) antes de concluir —
  a execução e31953 chamava uma pessoa com a conta logada numa conversa aberta.
- **Fatias 2 e 3.** O catálogo e o login viraram dado; o registro de apps descobre a pasta sozinho
  (`integrations/app_declarado/pacote.py::descobrir`, [apps](apps-e-loja.md#manifesto-de-app-fase-k1)).
- **Fatia 4.** O app âncora do perfil vem do registro (`registry.pacote_ancora()`: o app cujo `app.yaml` diz
  `ancora_do_perfil: true`; no máximo um, e dois é erro — persona multi-app é o item 12.3) no lugar de
  `package_of_provider("instagram")` ([acima](#sessionprovider-e-o-registro-por-pacote-fase-k1)). O bloco
  `instagram:` do `config.yaml` (`InstagramCfg`) saiu e virou o bloco genérico `contas:` (`config.py::ContasCfg`:
  `contas.session_max_age_s` e `contas.sessao.<pacote>.<ajuste>`); um `instagram:` esquecido no arquivo impede a
  subida com o recado de para onde foi (`config.py::BLOCOS_QUE_SAIRAM`). Os links de perfil (`instagram.com/<usuario>`)
  vêm de `links_de_perfil` do `app.yaml` (`AppDefinition.profile_link_hosts`/`profile_link_reserved`), lidos por
  `modules/skills/infrastructure/profile_links.py::profile_links_for`, que não guarda mais regra de app.

Catracas (`simulated`): `backend/tests/test_apps_fora_do_nucleo.py` (`test_integracoes_so_tem_o_motor_generico`:
`app/integrations/` só tem `app_declarado/`; `TEXTO_LEGADO`: texto "instagram" no código só desce, sem contar nomes
históricos como a tabela `instagram_profiles` e o prefixo `/api/instagram/`), `test_conhecimento_de_telas.py`,
`test_catalogo_como_dado.py`, `test_sessao_declarada.py` e `test_pacote_declarado.py` (um app de e-mail fictício,
só em dado, entra no registro com catálogo, leitura e login). Os testes leem o Instagram por
`backend/tests/pacote_instagram.py`. Aparelho e conta reais depois da troca: `not_run`.

## Extensão para outros apps (item 12.3 — pendente)

Hoje só o Instagram tem `session_provider` (a única pasta em `app/conhecimento/apps/`, descoberta por
`integrations/app_declarado/pacote.py::descobrir`, o descobridor em
`modules/applications/infrastructure/registry.py::_BUILTINS`) e, portanto, login determinístico e catálogo de ações.
Outlook, TikTok e Facebook operam pela IA livre, com login feito
pela PESSOA no Foco — sem `session_provider`, `ProfileAccountDTO.automated_login` fica `False` e a sessão é marcada
manualmente via `PATCH /api/instagram/profiles/{id}/accounts/{account_id}` (`session_status`). O ponto de
extensão está pronto: desde o ADR-052, uma pasta de dado com `app.yaml`, `catalogo.yaml` e `telas.yaml` +
`sessao.yaml`, sem Python e sem tocar no núcleo ([apps](apps-e-loja.md#manifesto-de-app-fase-k1)); `register_manifest()`
continua para teste. Provado só com o QA e com o app de e-mail fictício em teste (`simulated`). Falta a
decisão do dono sobre qual app ganha login determinístico/catálogo primeiro (plano-100 id 12.3, `status:
pending`), e, se for no mesmo perfil do Instagram, a sessão por (perfil, app)
([acima](#sessionprovider-e-o-registro-por-pacote-fase-k1)).

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| Grupos de acesso / políticas em camadas (11.10) | implementado | automatizada (`tests/test_grupos_de_acesso.py`, 6 casos + testes de frontend) | migração 036; `social/policy.py`; commit `a4237da`; plano-100 id 11.10 (proof `tests`) |
| Contas por app (12.1) | implementado | automatizada — backfill conferido numa CÓPIA do banco de produção (8 contas, 18 memórias, 39 interações), não em ambiente real ao vivo | migração 037; `social/service.py`; commit `407cfce`; plano-100 id 12.1 (proof `tests`) |
| Visão por app (apps-overview, 12.2) | implementado | automatizada (`tests/test_perfil_multiapp.py`, 5 casos) | `backend/app/apps_overview.py`; commit `407cfce`; plano-100 id 12.2 (proof `tests`) |
| Extensão de login determinístico a outros apps (12.3) | pendente | não executada | plano-100 id 12.3 (proof `not_run`) — aguardando decisão do dono |
| A persona é a pessoa: 047, `PersonaDTO` unificado, PATCH por seção, `/api/personas` canônico, geração por IA, imagens (048) | implementado | `simulated` (`tests/test_persona_migracao_047.py`, `test_persona_unificada.py`, `test_persona_geracao.py`, `test_persona_imagens.py`); ensaio `real` da 047+048 numa cópia do backup `20260927-222357` (27/09); IA real, OpenAI real, PostgreSQL (CI pendente) e produção `not_run` | [`persona.md`](persona.md); ADR-041 e ADR-042 |
| `SessionProvider` por pacote e regras de sessão em `modules/identity` (K1) | implementado | `simulated` (`tests/test_app_novo_pelo_manifesto.py::test_app_novo_entra_so_pelo_registro_com_provedor_e_catalogo`, `tests/test_dubles_cumprem_as_portas.py`, `tests/test_instagram_auth.py`); PostgreSQL e conta real `not_run` | `modules/identity/application/{ports,sessions,session_rules}.py`; [ADR-039](../decisoes.md#adr-039--manifesto-de-app-e-registro-de-sessionprovider) |
| Modo treinamento: gravação (13.1) | implementado | automatizada (`tests/test_modo_treinamento.py`) | migração 038; `training/recorder.py`; commit `bfffb0d`; plano-100 id 13.1 (proof `tests`) |
| Modo treinamento: generalização em habilidade (13.2) | implementado | automatizada (teste ponta a ponta em processo, sem planejador de novo) | `training/skills.py`, `planning/training.py`; plano-100 id 13.2 (proof `tests`) |
| Modo treinamento: UI de revisão (13.3) | implementado | automatizada (`TrainingReview.test.tsx`) | commit `bfffb0d`; plano-100 id 13.3 (proof `tests`) |
| Voz da persona (8.1) | implementado (código) | não executada em produção — aplicar as 8 personas é gasto do dono, não feito | plano-100 id 8.1 (proof `not_run`) |
| Memória de DM recebida (8.2) | implementado (código) | não executada — conversa real entre duas contas fora do escopo desta rodada; padrões de `leitura.mensagem` do `app.yaml` do Instagram não conferidos contra árvore de acessibilidade real | plano-100 id 8.2 (proof `not_run`) |
| Desafio/2FA sempre manual | implementado (garantia estrutural no motor genérico; a ordem das telas é dado) | automatizada (`tests/test_instagram_auth.py`, `tests/test_sessao_declarada.py::test_um_app_novo_para_no_desafio_e_chama_a_pessoa`) — sem contraexemplo de bypass encontrado na leitura do código | `integrations/app_declarado/sessao.py`, `conhecimento.py::DESFECHOS_DEPOIS_DO_ENVIO`, `automation/hierarchy.py::_DESAFIO`, `telas.yaml` do Instagram ([acima](#instagram-classificador-login-determinístico-sessão-desafios-manuais)) |

Backlog:

- Onda B do design: conta única (credencial e sessão por conta e aparelho); decisão do dono sobre as 5 fotos dos
  perfis bloqueados anexadas às pessoas dobradas e sobre `beliefs` no prompt ([`persona.md`](persona.md)).
- Decisão do dono sobre o próximo app com login determinístico/catálogo (12.3).
- Aplicar e medir as 8 personas com voz completa em produção (8.1) — custo de IA real, decisão do dono.
- Provar memória de DM (8.2) num diálogo real entre duas contas — aceite de nível 2, hoje proibido nesta rodada.
- Conferir os padrões de `leitura.mensagem` do `app.yaml` do Instagram (`LeituraDeclarada.message_of`) contra
  árvore de acessibilidade real de DM.
