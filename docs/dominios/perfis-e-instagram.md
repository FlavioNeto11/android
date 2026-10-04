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
"fleet gate" (achado #114, endurecido pelo ADR-055) que coordena as contas da frota sobre o MESMO alvo: seguir,
mensagem e comentário são de UMA conta por alvo (regra do dono, `UMA_CONTA_POR_ALVO`); curtir tem o teto
`fleet_max_accounts_per_target` (padrão 3). Conta-se qualquer ação de saída das outras contas e os pedidos de
aprovação em aberto, na janela `fleet_target_window_days` (padrão 30); o excedente é RECUSADO, não adiado, e abaixo
do teto vale o espaçamento `fleet_min_spacing_between_accounts_s` (120) mais `fleet_spacing_jitter_s` (180). Os
tetos por dia por balde (`likes_per_day` 150, `comments_per_day` 40, `follows_per_day` 40, `dms_per_day` 60) e o
aquecimento (3 dias a 34%) são **padrão inicial, ajustável pelo dono** (`LimitsCfg` e `automation_policy.limits`).
O que passa do teto de hora ou dia é represado antes de assumir a etapa (`retry_wait`, sem gastar tentativa).
Pendente (decisão do dono): se um perfil pode afrouxar a política do catálogo — hoje afrouxar é permitido e fica
marcado (`ProfilePolicyDTO.loosened`), não recusado (#114 item 3). A política de um perfil vem em três
camadas, nesta ordem: **escolha própria → grupo → padrão** (`_own`/`_group`/`origin_for`); `limits_origin()`
diz de onde veio o limite efetivo, para a UI mostrar a origem por ação.

Migração `036_grupos_de_acesso.sql`: `policy_groups` (capabilities e limits em JSON — só o que DIFERE do
padrão) e `instagram_profiles.policy_group_id` (um grupo por perfil, sem FK — desvínculo é manual no serviço).
A política de ações é por app (23.10): o recorte do âncora no nível de fora, os outros em `por_app.<pacote>`
(`policy.py::politicas_do_app`; detalhe em [persona](persona.md), "Painel de contas por app").
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
  comentário no código (achado #115) registra que a troca automática de conta nunca foi implementada de propósito
  (a flag `auto_switch_account`, que sugeria o contrário, não existe mais na configuração; implementar a troca pelo
  seletor de contas é decisão do dono, ainda aberta).
  `_challenge()` grava `SessionStatus.auth_challenge` e devolve `Outcome.AUTH_CHALLENGE`: **não existe caminho de
  código que resolva desafio ou 2FA automaticamente.** "Confirm you're human" (e variantes: "verify/prove you're
  human", "confirme/comprove que você é humano/uma pessoa") é a conta travada (decisão do dono, 29/09): cai em
  `challenge` nos dois idiomas e em `hierarchy._CONTA_TRAVADA`, sem toque nenhum — a prova é com hierarquia
  SINTÉTICA (`tests/test_detector_conta_travada.py`), porque a tela real não existe (a conta foi perdida). Tela que
  nenhum sinal reconhece não se reobserva para sempre: `session_unknown_retry_cap` (3) bloqueia o perfil com o motivo
  (`tests/test_porta_de_sessao_no_teto.py`). `_needs_person(conta, instance_id)` impede o agendador de
  sequer tentar de novo quando o estado já pede pessoa (lê a sessão da conta **deste app** neste aparelho, 23.4);
  `_blocked_reason()` recusa entrar sem `consent_at` na credencial da conta (ADR-040). Tetos, cooldown e prazos são
  os `ajustes` do `sessao.yaml` com a sobrescrita da instalação por cima (`contas.sessao.<pacote>.<ajuste>` no
  `config.yaml`, entregue por `Config.ajustes_de_sessao`), lidos a cada uso. `Outcome` (StrEnum: `SESSION_READY, INVALID_CREDENTIAL, AUTH_CHALLENGE, WRONG_ACCOUNT, RETRYABLE,
  UNCERTAIN`) mora no mesmo módulo; `Outcome.terminal` inclui `AUTH_CHALLENGE`.
- **Conta aberta** — `sessao.py::ler_conta` lê a conta SÓ na tela de perfil declarada (`conta.tela_de_perfil`),
  depois de tocar na aba de perfil declarada (`conta.aba`), nunca pelo feed (bug histórico de confundir autor de
  post com dono da conta, K-021). A conta lida é comparada ao `handle` e ao `login_identifier` da **conta do app**
  que a chamada abre (`ContaDaSessao.handle`/`aceitos`, sem maiúsculas nem o `@` do começo), não ao `@` de cadastro
  do perfil (item 23.4).
- **Login em etapas, conta fora da barra e Custom Tab (item 23.6, ADR-057 decisão 3)** — três blocos opcionais do
  `sessao.yaml`, lidos pelo mesmo motor (quem não os declara segue igual, como o Instagram):
  - `formulario.etapa_do_usuario: {tela, sinal_do_botao, recusas: [{sinal, detalhe}]}`: o identificador numa tela
    (a `tela` do `telas.yaml` tem de ser do tipo `login` e não a do campo de senha) com um "avançar", a senha na
    seguinte. A geometria (`formulario.py::identifier_form`) exige UM campo de texto, nenhum de senha e UM botão
    abaixo que casa o sinal por inteiro. `SessaoDeclarada._etapa_do_usuario` preenche, confere, avança e espera a tela
    da senha; o cabeçalho clicável com a conta, acima da senha, nunca é tomado por usuário (`LoginForm.usuario_editavel`).
    **A senha só é digitada numa tela que mostra ESTE identificador**, como palavra inteira
    (`formulario.py::mostra_o_identificador`): o app que lembrou outra conta e abriu direto na senha não recebe a
    senha desta. Os desfechos antes da senha — desafio depois do "avançar", recusa declarada do identificador, tela
    da senha sem a conta, tela da senha que não chegou, navegador fora do site — têm etapa própria na tentativa
    (`ETAPA_*` em `sessao.py`), todas em `ETAPAS_ANTES_DO_ENVIO` (não gastam o teto diário). Recusa do identificador,
    tela sem a conta e tela da senha que não chegou param o login em `review` (a credencial não vira `invalid`:
    ninguém julgou a senha). O "avançar" já é efeito no servidor (o identificador saiu; o provedor pode mandar um
    código ou um pedido de aprovação a cada vez): contar falha e esperar o intervalo repetiria esse toque sem fim.
  - `conta.acesso: {ids, rotulos}` no lugar de `conta.aba` (exatamente um dos dois): a conta é aberta por um elemento
    do app fora da barra inferior (avatar, menu), por prefixo de id ou rótulo, com **um candidato só**
    (`formulario.py::account_opener`); a leitura usa a extração declarada, no texto ou na descrição, e só vale com
    **um valor** na tela (`formulario.py::valores_extraidos`: um menu que lista duas contas não diz qual está aberta).
    `conta.ler_ao_entrar: true` lê a conta pelo acesso logo depois do envio, numa tela de casa sem a conta à vista
    (`_ler_ao_entrar`, uma vez por login; a tentativa que termina com outro pacote na frente — a Custom Tab ainda
    fechando — não gasta essa vez): sem isso, o login bem-sucedido de um app cuja tela inicial não mostra a conta
    terminaria incerto. `ler_conta` recebe o reconhecimento do provedor (`reconhecer=`), o mesmo da Custom Tab. `ConhecimentoDeSessao.aba_de_perfil` responde pelos dois (o aprendizado pergunta o
    mesmo).
  - `navegador: {pacotes: {<pacote do navegador>: <sufixo do id da barra de endereço>}, hosts: [...]}`: a Custom Tab
    do login. O navegador declarado só é tela do app (`SessaoDeclarada._reconhecer`) num site de login que o app
    declara (os `hosts`, igual ou subdomínio). O `host` de uma conta não soma: conta com `host` é de portal, pelo
    navegador, e não tem login gerenciado — `_resolver_conta` a recusa sem tocar no aparelho e a rota
    `…/accounts/{aid}/session/{connect|verify}` responde 409 `conta_de_site` (a porta e o despacho só acham a conta
    do app inteiro, `conta_do_pacote`). Fora do site, ou
    sem a barra à vista, a pessoa assume (`_navegador_fora_da_conta`: sessão `auth_required` com o que fazer, login em
    `review`; a leitura só registra) — sem "voltar", sem reabrir, sem digitar; o desafio dentro da Custom Tab é visto
    mesmo assim (o `classificar` devolve "outro app" antes do detector), e nem a recusa se toca numa página alheia.
    A barra de endereço nunca é campo do formulário (`formulario.py::Ignorados`: numa página de senha sem o cabeçalho
    da conta, a geometria a tomaria por usuário). A senha é conferida no instante de digitar,
    na MESMA árvore do campo (`_fill_password`: o `locate` do canal sensível só acha campo no pacote da conta ou no
    navegador no site permitido); fora disso o canal recusa com a mensagem fixa dele.

  Prova `simulated`: `backend/tests/test_sessao_em_etapas.py` (um correio fictício, só em dado: etapas, acesso pela
  gaveta, Custom Tab no site do app e num subdomínio; conta de site recusada; site não declarado; fora do site;
  desafio na Custom Tab; site trocado entre achar o campo e digitar; tela da senha que não chega para o login
  automático; carga recusada) e `backend/tests/test_contas_unificadas_api.py::test_conta_de_site_nao_conecta_pelo_login_gerenciado`. O `type_secret` do executor
  (`taskqueue/executor.py::_conferir_destino`) ainda recusa a Custom Tab (exige o pacote da conta em primeiro plano):
  só o motor de sessão a aceita. O `telas.yaml`/`sessao.yaml` do Outlook veio no 23.8
  ([abaixo](#o-outlook-como-dado-item-238)); aparelho real: `not_run`.
- **Entrada e alternativa do login em etapas (item 23.8)** — mais dois blocos opcionais em
  `formulario.etapa_do_usuario`, para o que o Outlook mostrou e o motor ainda não sabia fazer:
  - `entrada: {tela, sinal_do_botao}`: a tela do app deslogado (tipo `login`, sem campo de senha, diferente da do
    identificador) e o botão que abre a tela do identificador. `SessaoDeclarada._abrir_a_etapa_do_usuario` toca o
    botão (um candidato só, `formulario.py::botao_unico`, sem casar a exclusão) e espera a tela do identificador até
    `submit_wait_s`. É navegação: nenhuma tentativa começa, nada conta falha nem gasta o teto; desafio no caminho vai
    para a pessoa; sem o botão único, ou sem a tela seguinte, incerto e nada digitado.
  - `alternativas: [{tela, sinal_do_botao}]`: telas entre o "avançar" e a senha em que o provedor propõe um código e
    oferece a senha. No laço de `_etapa_do_usuario`, ANTES do desvio de desafio, com o botão declarado na tela (um só,
    no destino permitido, nunca numa tela de conta travada — `ConhecimentoDeSessao.botao_da_alternativa`), escolhe-se
    a senha uma vez por tela e o prazo recomeça; enquanto a mesma tela segue com o botão, espera-se a troca (o WebView
    demora) sem julgá-la desafio nem tocar de novo. Sem o botão, a tela segue o tipo dela: a de código vai para a
    pessoa (`challenge_before_password`). A carga recusa alternativa em tela de tipo `desafio` (ADR-055: nada se toca
    em conta travada), na tela da senha, repetida ou na do identificador. Escolher a senha é o método de entrada do
    titular, não resolver desafio (ADR-009 segue valendo para código, aprovação e CAPTCHA).

  Prova `simulated`: `backend/tests/test_outlook_declarado.py` (os dois blocos na pasta real do Outlook e a carga
  recusada); quem não os declara segue igual (`::test_quem_nao_declara_entrada_nem_alternativa_segue_como_antes`).
- `emit_needs_person_change()` (`modules/identity/application/session_rules.py`, chamada por
  `SessaoDeclarada._save`) dispara o evento `session.needs_person` (fila "Aguardando intervenção");
  `PRECISA_DE_PESSOA = (auth_challenge, wrong_account)` ([abaixo](#sessionprovider-e-o-registro-por-pacote-fase-k1)).

**Desafio bloqueia o perfil sozinho (ADR-029, 27/09).** Na entrada da sessão em `auth_challenge`, o perfil passa
de `active` a `blocked` (`modules/identity/application/session_rules.py::bloquear_por_desafio`, chamado por
`SessaoDeclarada._save` e por `AppState._sessao_desmentida`). A porta de sessão e a distribuição já recusam
perfil fora de `active`. Pausa do dono (`disabled`) não é reescrita. Resolver a tela não reativa sozinho: quem
reativa é a pessoa, na tela do perfil. **Só na conta âncora (item 23.5, decisão do dono P9 de 29/09):** o desafio
num app que não é o âncora (o Outlook) para só a conta daquele app — credencial em `review`, sessão em
`auth_challenge` — sem bloquear a persona nem o Instagram dela; a conta travada daquele app mantém a quarentena do
aparelho, e o marcador (`marcar_conta_travada`) só bloqueia a persona pela conta âncora. A regra é uma só
(`session_rules.py::aplicar_desafio`), para o motor de sessão e para a tela que desmente a sessão na execução; o
detalhe está em [persona.md](persona.md).

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
  antes de mexer num aparelho com Instagram confere-se a tela (K-053). A quarentena só sai por uma PESSOA: reset do disco
  ou `POST /api/instances/{id}/locked-account/resolve` com nota (29.24, ADR-068 item 10; só banco). Conta já retirada (29.23)
  aparece como `[conta removida]` no rótulo, no marcador e nos avisos ("conta retirada (bloqueada)"); conta viva, com o @.
- **Contas nossas entre si (29.28, emenda do ADR-050).** Conta nossa VIVA pode ser alvo de outra conta nossa (Instagram: comentar,
  responder, editar nos posts umas das outras; Outlook: trocar e-mails), em ritmo baixo: o gesto com efeito exige no mínimo
  `limits.fleet_min_spacing_to_own_account_s` (600 s) — ou o `cooldown_between_external_actions_s` do perfil, se maior — desde o último
  gesto com efeito DESTA conta, com `retry_at`; uma interação por vez, sem link, texto natural; a conduta e a regra de desafio/2FA
  continuam. Conta RETIRADA por bloqueio segue recusada. "Uma conta por alvo" e a aprovação valem entre contas nossas como para qualquer
  alvo, e a conta nossa continua fora da elegibilidade de "terceiro" (8.3).
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
  - **Re-toque verificado (29.64).** Envio incerto com o formulário INTACTO na tela de agora (identificador à vista,
    senha ainda no campo, Entrar habilitado, nada carregando, mesmo app) ganha UM toque no botão atual, sem redigitar;
    qualquer outra tela devolve o incerto e o freio acima vale. Motivo: no android-13 (04/10) o toque em Entrar se
    perdeu e um toque manual entrou.
  - **`review` que se reconcilia (29.64).** A conta lida e CONFERIDA aberta no aparelho tira a credencial de `review`
    (volta a `active`, falhas zeradas, evento `login_reconciliado`); `invalid` não sai assim.
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

- `package` (propriedade) e `async ensure_session(rt, profile_id, *, account_id=None, force_login=False,
  automatic=False, observe_only=False)`, que devolve um `SessionOutcome` (`ready`, `detail`). `account_id` (contrato
  C1, item 23.4) é a conta que a chamada abre: do perfil e do pacote do provedor, senão recusa sem tocar no aparelho;
  `None` é a conta do perfil no pacote do provedor (no provedor do app âncora, a conta âncora, como sempre);
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
| `AppState._session_gate(rt, package)` | o provedor do pacote do item e a conta da persona NESSE pacote (`SocialRepository.conta_do_pacote`): sessão, credencial e `account_id` são dela; sem pacote (chamador antigo), `provedor_do_perfil()` e a conta âncora |
| `AppState._reobservar_apos_intervencao` | cada conta da persona em app com provedor cuja sessão no aparelho pede releitura, pelo provedor DO APP dela, com `account_id` e `observe_only=True` |
| `AppState._sessao_apos_mudanca_de_app` | `sessoes.has(pacote)`: só app com provedor invalida sessão, e só as contas daquele app (`_invalidate_sessions(…, package)`) |
| `api.py::_start_session_job` ("Conectar", "Verificar conta", por conta ou pelo apelido por perfil) | `sessoes.for_package(conta.package)` (onda B), com `account_id=conta.id` (23.4); sem provedor, 409 `no_session_provider`; sem consentimento na credencial, 409 `consentimento_de_credencial` |
| `modules/execution/infrastructure/command_bus.py` (recursos, fase H) | `sessoes.for_package` pelo pacote do app e a conta da persona nele (a do pedido, se for daquele app); sem provedor ou sem conta, recusa |

- `AppState.provedor_do_perfil()` é o provedor do pacote de `social_repo.app_package`, que vem do app âncora do
  perfil no registro (`modules/applications/infrastructure/registry.py::pacote_ancora`: o app cujo `app.yaml` diz
  `ancora_do_perfil: true`; no máximo um, e dois é erro — ADR-052, fatia 4). Antes vinha de
  `package_of_provider("instagram")`, com o `cfg.file.instagram.package` de reserva.
- `AppState.instagram` virou propriedade: o nome antigo de `provedor_do_perfil()`, o **mesmo** objeto da porta. Um
  teste que troca um método dele troca para todos.
- O 409 `no_session_provider` passou a ser alcançável na onda B: `POST …/accounts/{aid}/session/connect` numa conta
  de app sem provedor o devolve ([contrato v0.28](../api-contract.md#adendo-v028-27092026--conta-única-credencial-com-consentimento-e-sessão-por-conta-e-aparelho-adr-040)).

**A sessão é da conta num aparelho (049), e o motor é por conta (23.4).** `account_sessions(account_id,
instance_id)` substitui a sessão única por perfil. Desde o item 23.4 (ADR-057) o provedor recebe a conta
(`ensure_session(rt, profile_id, account_id=…)`) e o motor genérico resolve, antes de tocar no aparelho, a
`ContaDaSessao` da chamada (`SessaoDeclarada._resolver_conta`): tudo o que ele lê e grava é dela — credencial e
consentimento (`account_credential_row`), marcação da credencial (`mark_account_credential`,
`touch_account_credential`), tentativa (`start_auth_attempt(account_id=…)`), teto diário (`auth_attempts(account_id=…)`:
os envios de um app não gastam o teto do outro), sessão no aparelho (`account_session_row`/`set_account_session`) e o
marcador de conta travada (`registrar_conta_travada(handle=, app_id=)` com o `@` e o app da conta). O "verificado em"
do cartão do perfil (`last_verified_at`) só muda com a conta âncora. O status do PERFIL (`blocked`/`disabled`) segue
da persona, e só o desafio na conta âncora o muda (item 23.5, abaixo). `SocialRepository.session_row`/`set_session`/
`credential_row` (por `profile_id`) ficam como leitura da conta âncora para o DTO do perfil e os chamadores antigos.
Invalidação: `invalidate_sessions_of_instance(package=…)` só as contas daquele app; `todos_os_apps=True` (o gancho
do aparelho, `AppState._invalidate_sessions` sem pacote: reset e troca de máquina) toda conta ali; sem nenhum dos
dois, a âncora. A tela que desmente a sessão no meio da execução (`AppState._sessao_desmentida`) corrige a conta do
app da tela: o `package=` de quem viu (o executor passa o pacote da tela em que viu a trava, `on_auth_needed`), senão
o da etapa em curso no aparelho (`AppState._pacote_em_curso`), senão a âncora. Mover o aparelho ou a persona de máquina pede confirmação com a sessão pronta de QUALQUER conta dela ali
(`api.py::_recusar_mudanca_de_servidor`, `SocialService._recusar_troca_de_servidor`). Consequências:

- desde a 047 a pessoa vinculada a um aparelho pode **não ter conta** no app do item: `AppState._session_gate` recusa
  com "não tem conta em <app>" sem tocar o aparelho nem autenticar (`state.py::AppState._tem_conta_no_app`: a verdade
  é `profile_accounts`; o `username` de cadastro continua contando como a conta do Instagram para perfis anteriores
  à 037). Teste: `backend/tests/test_persona_unificada.py::test_porta_de_sessao_responde_sem_conta_para_a_pessoa_sem_conta_no_app`;

- dois apps com login gerenciado **no mesmo perfil** convivem: nada do segundo escreve na conta do primeiro, nem no
  sentido contrário (prova `simulated`: `backend/tests/test_sessao_declarada.py` com o correio fictício ao lado da
  conta âncora, e `backend/tests/test_sessao_por_conta.py` na composição). Desde o item 23.8 o Outlook (ADR-057) tem
  o dele (`provedor_de_sessao: microsoft`, [abaixo](#o-outlook-como-dado-item-238)). Prova `real` do login por
  conta: `not_run` (23.13);
- a checagem "tela contradiz a sessão" (`taskqueue/executor.py::StepExecutor._sessao_desmentida`) vale para
  qualquer app com provedor (`session_provider_of(package) is not None`; antes, só o pacote do Instagram). Desde o
  23.8 vale também para o Outlook.

**As regras de sessão do perfil.** `modules/identity/application/session_rules.py`:

- `aplicar_desafio` (item 23.5: âncora → `bloquear_por_desafio` e quarentena; outro app → `parar_conta_por_desafio`
  e, na trava, quarentena sem bloquear a persona) é a porta dos dois chamadores.
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
| `sessao.yaml` (fatia 3) | login: ajustes padrão, formulário (e, desde o 23.6, a etapa do usuário), dispensa, aba de perfil (ou o acesso à conta fora dela), navegador da Custom Tab, desfechos depois do envio, textos de ajuda | `integrations/app_declarado/conhecimento.py`, `sessao.py` |

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

## O Outlook como dado (item 23.8)

`backend/app/conhecimento/apps/com.microsoft.office.outlook/`, lido pelos mesmos motores, sem Python do Outlook:

| Arquivo | O que declara |
|---|---|
| `app.yaml` | `provedor_de_sessao: microsoft`, perfil e internet obrigatórios, `ancora_do_perfil: false` (desafio para só a conta Outlook, item 23.5), `renderizador_recusado: [swiftshader]` (29.11) |
| `telas.yaml` | sinais `en` e `pt`, boas-vindas, "Add account", espera do WebView, "Verify your email" (código, `dois_fatores`), senha, desafios da Microsoft, "Stay signed in?", as intermediárias do primeiro uso, gaveta e caixa; extração do e-mail da conta dentro do painel da gaveta |
| `sessao.yaml` | login em etapas com `entrada` e `alternativas`, recusa do identificador, dispensas (recusas globais, botão por tela e Voltar no diálogo do sistema), conta pela gaveta (`conta.acesso` + `ler_ao_entrar`), desfechos depois do "Next", textos |
| `catalogo.yaml` | 3 ações só de leitura; `OPEN_MAIL_INBOX` e `SEARCH_MAIL` entregam `remetente` e `assunto` (12.3, ADR-065) |

O caminho do login: boas-vindas → "Add account" → e-mail no `auto_complete_input_email` → "Continue" → ~10 s de
`common_auth_webview_progressbar` → WebView `common_auth_webview`; em "Verify your email" com "Use your password",
escolhe a senha; sem essa oferta, é código e vai para a pessoa → senha no `passwordEntry` só pelo canal sensível, numa
página cujo `bannerText` mostra ESTE e-mail → "Next" → aviso da conta Microsoft ("OK") → diálogo de chave de acesso do
sistema (Voltar, nunca o "Continue" dele) → "Authentication in progress" (~20 s) → "Add another account" ("MAYBE
LATER", nunca "ADD") → privacidade ("NEXT") → diagnóstico opcional ("Decline", nunca "Accept") → experiências
("CONTINUE TO OUTLOOK") → caixa → gaveta pelo `account_button` → o e-mail da conta, com um valor só.

**As dispensas declaradas (item 23.8, motor genérico).** Três blocos opcionais do `sessao.yaml`, que quem não declara
não sente (o Instagram segue igual):

- `dispensa.por_tela: [{tela, sinal_do_botao}]`: o botão que fecha UMA tela `intersticial` (a carga recusa qualquer
  outro tipo: desafio, código, login e casa nunca se dispensam), só nela, com um candidato só do próprio app
  (`formulario.py::botao_unico`). É para o botão que não é recusa em lugar nenhum além daquela tela — "OK" e "NEXT"
  como rótulos globais seriam tocados em qualquer tela.
- `dispensa.voltar: [{pacote, sinal}]`: o diálogo de OUTRO pacote (o sistema oferecendo algo) sai pela tecla Voltar,
  sem tocar nele, só com o sinal declarado na tela e nenhuma verificação à vista. A carga recusa o próprio app.
- `ConhecimentoDeSessao.dispensa_declarada` responde pelos dois, nunca numa tela com trava. O motor a aplica depois do
  envio (`_watch_after_submit`, antes da tabela de desfechos, até `intersticiais_max`, e cada dispensa renova o prazo:
  a tela mudando não é envio sem resposta), na abertura (o app reaberto no meio do primeiro uso, ou com o diálogo do
  sistema por cima) e na leitura da conta (`ler_conta`, que ganhou o `voltar`).
- `extracoes.<nome>.dentro_de: [id]` (`telas.yaml`, `conhecimento_de_telas.Extracao.cabe`): a extração vale só para
  elementos dentro do contêiner declarado. A gaveta do Outlook mostra o e-mail num TextView SEM id dentro do
  `drawer_folder_composable`; fora dele ficam a coluna de contas (que pode listar outras), o WebView de autenticação e
  a caixa (o endereço de um remetente viraria "conta errada").

**Observado × suposto.** Observado (Outlook 5.2635.3, UiAutomator2, 30/09/2026, em inglês): no android-10, as telas
até a página da senha; no android-06, o login real do André (22:18–22:24Z) do "Next" até a gaveta, com os ids, textos
e a ordem que os testes usam ("OK" com dois toques; botões em MAIÚSCULAS; o "Continue" com o texto repetido na
descrição; o `account_button` sem descrição). Suposição, marcada linha a linha nos YAML:

- o "Stay signed in?" ("No"/"Yes") e o diálogo de notificação do Android ("Don't allow"), que não apareceram;
- os textos de desafio da Microsoft ("Help us protect your account", "Your account has been locked", "Enter code",
  "Approve sign in request", "Verify your identity") e de recusa ("Your account or password is incorrect", "That
  Microsoft account doesn't exist");
- a tabela `pt` inteira (os aparelhos observados rodam em inglês).

O que a suposição errar termina incerto, com o login automático parado até uma pessoa olhar — nunca sucesso. Os
sinais de desafio e código são títulos de página ancorados na linha (`(?m)`), e o "Verify your email" exige também a
linha "Send code" da mesma página: o detector roda sobre a caixa no meio da execução, e um assunto de e-mail solto não
pode parar a etapa. Só "Help us protect your account" entrou no detector genérico
(`automation/hierarchy.py::_CONTA_TRAVADA`, que omite a captura antes de ela sair): as outras frases, soltas ali,
pegariam DM de golpe no Instagram ("your account has been locked").

Consequências do login gerenciado: a conta Outlook nasce com `automated_login` (a sessão deixa de ser marcada à mão;
é Conectar/Verificar conta), a sessão pronta de um comando Outlook + Instagram exige as duas contas prontas no
aparelho (`RunService._mundo`), e remover a conta recusa só a do app âncora (`SocialService.delete_account`; antes
recusava toda conta com login automático). O painel ainda esconde o botão de remover para conta com login automático
(`GuiaContas.tsx`): a do Outlook sai pela API.

**Catálogo (12.3, ADR-065).** `catalogo.yaml` com `OPEN_MAIL_INBOX`, `COLLECT_MAIL_HEADERS` e `SEARCH_MAIL`, sem
nenhuma ação com efeito (T17 do ADR-057). Com ele o Outlook deixa de ser app de etapa livre no plano entre apps
(ADR-058), e o cenário C1 do dono (ler no Outlook e usar no Instagram) passa pela ação de catálogo que entrega o
valor lido (`saidas`), como descrito em [apps-e-loja](apps-e-loja.md). Provas dos ids da caixa seguem `model_judged`
até a observação real (29.12).

Riscos conhecidos, para a próxima observação real: os sinais genéricos que já existiam ("verify your account",
"security code", "verification code") também casam assunto de e-mail — ler a caixa com um desses assuntos à vista
pode parar a etapa como desafio; e uma tela de carregamento no meio da leitura da conta (`ler_conta`) ainda encerra a
leitura em vez de esperar — depois do envio isso não acontece, porque o "Authentication in progress" passa na
observação do envio, antes da caixa.

Prova `simulated`: `backend/tests/test_outlook_declarado.py` (a pasta real carrega; o login percorre a sequência
inteira observada até a gaveta, com os ids, textos e a ordem dela; "ADD", "Accept", "Continue" do diálogo do sistema e
"saiba mais" nunca são tocados; o app reaberto no meio do primeiro uso ou com o diálogo por cima segue pelas mesmas
dispensas; a coluna de contas não é lida; código sem a oferta da senha, conta segurada e código depois da senha vão
para a pessoa sem bloquear a persona; o `passwordEntry` só recebe a senha, pelo canal sensível; tela desconhecida não
é sucesso; a caixa não é desafio nem mostra a conta; carga recusada) e
`backend/tests/test_sensitive_input.py::test_pagina_da_microsoft_que_segura_a_conta_e_sensivel_mesmo_sem_campo`.
Observação real (relato da sessão da IDE, android-06, 30/09 22:18–22:24Z, sem id de execução registrado aqui): a
senha pelo canal sensível foi aceita, e o motor de então parou incerto no aviso da conta; a sequência seguinte foi
atravessada à mão e é a que este dado declara. O login automático de ponta a ponta com este dado: `not_run` (29.12 e
23.13).

## Extensão para outros apps (item 12.3 — pendente)

O Instagram e, desde o item 23.8, o Outlook ([acima](#o-outlook-como-dado-item-238)) têm `session_provider` (pastas
em `app/conhecimento/apps/`, descobertas por `integrations/app_declarado/pacote.py::descobrir`, o descobridor em
`modules/applications/infrastructure/registry.py::_BUILTINS`) e, portanto, login determinístico e catálogo de ações.
TikTok e Facebook operam pela IA livre, com login feito
pela PESSOA no Foco — sem `session_provider`, `ProfileAccountDTO.automated_login` fica `False` e a sessão é marcada
manualmente via `PATCH /api/instagram/profiles/{id}/accounts/{account_id}` (`session_status`). O ponto de
extensão está pronto: desde o ADR-052, uma pasta de dado com `app.yaml`, `catalogo.yaml` e `telas.yaml` +
`sessao.yaml`, sem Python e sem tocar no núcleo ([apps](apps-e-loja.md#manifesto-de-app-fase-k1)); `register_manifest()`
continua para teste. Provado só com o QA e com o app de e-mail fictício em teste (`simulated`). O primeiro app é o
Outlook (ADR-057, Fase 23), e a sessão por conta que dois apps no mesmo perfil pediam foi feita no item 23.4
([acima](#sessionprovider-e-o-registro-por-pacote-fase-k1)); o login em etapas, a conta fora da barra inferior e a
Custom Tab com site declarado, no item 23.6 ([acima](#instagram-classificador-login-determinístico-sessão-desafios-manuais)).

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
- Responder comentário em aparelho real (`REPLY_COMMENT`, 8.3, `not_run`): ação em conta real de terceiro, exige
  autorização do dono. Roteiro: aparelho com conta viva e o worker ligado; objetivo "responda os comentários da minha
  última publicação" com `for_each` em 2 comentários reais e política `approval_required`; conferir na fila que cada
  pedido traz o comentário de origem, aprovar um e EDITAR outro (o texto digitado na tela tem de ser o editado);
  conferir no aparelho o texto publicado, o `comment_replied` confirmado com a contraparte certa e a memória
  aprendida do autor; registrar ids da execução e dos pedidos no relatório como prova `real`.
- Conferir os padrões de `leitura.mensagem` do `app.yaml` do Instagram (`LeituraDeclarada.message_of`) contra
  árvore de acessibilidade real de DM.

## Conta bloqueada sai, a persona fica (item 29.23, ADR-068)

Decisão do dono de 02/10/2026: bloqueio **confirmado** (a tela de verificação lida, ou o dono declara) tira a conta
da plataforma na hora; a **persona continua viva**, volta a `active` e fica sem @ (como as `ig-persona-*`), pronta para
receber contas novas.

- **Ação** `SocialService.retirar_conta_bloqueada(profile_id, account_id, origem, autor, evidencia)` e rota
  `POST /api/instagram/profiles/{id}/accounts/{conta}/retire` (corpo opcional `{evidencia}`). Uma transação: credencial da
  conta, a legada e o ciphertext do cofre; sessões; vínculo de aparelho do app da conta (e o sem app); a linha da conta,
  mesmo âncora. Idempotente. O aparelho não é tocado e o marcador de quarentena fica.
- **Gatilho:** ao fim de `marcar_conta_travada`, SÓ no Instagram (conta âncora) e SÓ com sinal forte: a `ChallengeActivity` em foco (lida por `DeviceManager.observe`) ou a declaração do dono. Só texto na tela, ou conta de outro app, fica `blocked`/marcada para a pessoa; a rota manual retira qualquer conta. Falha deixa a persona `blocked` e o erro no histórico; a rota refaz.
- **Lápide:** `contas_retiradas` (071) guarda só o hash do @; `eh_conta_nossa()` (`social/contas_nossas.py`) é a consulta
  única de "é conta nossa?", usada pelo filtro de frota: conta RETIRADA nunca é alvo; conta nossa VIVA pode receber a interação de outra conta nossa, em ritmo baixo (emenda do ADR-050, 29.28, abaixo).
- **Memória (29.32):** `memory_items` ficam, de TODAS as personas, com o @, o id e o e-mail da conta trocados por "[conta removida]"
  (`social/memory.py::reescrever_memoria`; casamento seguro em `contas_nossas.sem_o_rastro`: `ana` não casa em `banana`, `ana.silva` nem `ana@x.com`).
  O e-mail só sai se identifica a conta e nenhuma outra conta VIVA o usa (`emails_so_desta_conta`): o Outlook vivo com o mesmo endereço o mantém.
  Retroativo das retiradas anteriores: `scripts/memoria-conta-retirada.py` (`--ensaio`/`--aplicar`; lápide + eventos como fonte; e-mail só por
  `--lista-stdin`), com backup no deploy (`--aplicar` exige `--backup <caminho que exista>`; o `--ensaio` avisa se a migração diverge).
  Cuidados (revisão adversarial): o @ que outra conta VIVA ainda tem (o mesmo @ em outro app, ou o cadastro de outra persona) não se
  redige; um handle em forma de e-mail só some se o endereço é exclusivo da conta que sai; a lista do operador recusa item com menos de
  3 caracteres ou só de dígitos (só contagens). `runs.command` e `actions.args` ficam.
  **Medição de 03/10:** na cópia do backup `20261002-211739` (116 linhas) nenhuma tem rastro de conta nossa; as 40 que citam @ ou e-mail
  são de TERCEIROS (35 @ sem lápide nem conta viva, 5 com e-mail sem relação), não rastro. O passe retroativo cobre o que existe desde
  23/09 (zero contas retiradas sem lápide); conta perdida antes disso só entra pelo `--lista-stdin`, com o handle dado pelo dono. Gancho
  `limpezas_ao_retirar` para outros módulos (Aprendizado), dentro da transação; erro desfaz a retirada.
- **Histórico** (events, runs, steps, approvals, interactions, ai_calls) fica intacto (opção A do dono).
- **Dívida:** `DELETE /api/instagram/profiles/{id}` ainda apaga a persona inteira (os dados da pessoa moram na linha do perfil).
- **Limpeza do app ao retirar (29.27, emenda do ADR-068, decisão do dono de 02/10, relatada às 23:10Z).** Conta de app que declara `limpar_ao_retirar: true`
  no `app.yaml` (hoje o Instagram; lido em `AppDefinition.clear_on_account_retire`) leva os dados do app embora dos aparelhos onde estava
  logada: `pm clear` SÓ desse pacote, captura de tela antes e depois, nenhum toque na tela, e a quarentena do aparelho resolvida pelo caminho do
  29.24 com a nota "limpeza automática autorizada pelo dono em 02/10". Os aparelhos são os marcadores abertos do @ da conta e o vínculo que
  serve ao app, CAPTURADOS antes da retirada (`SocialRepository.aparelhos_da_conta`); a sessão NÃO é pista (sobrevive ao desvínculo e pode ser
  de aparelho que já serve outra persona). Antes do `pm clear` há a trava `outra_conta` (vínculo, sessão em qualquer status ou marcador de outra
  conta do mesmo app no aparelho: recusa, sem limpar, quarentena aberta) e, antes de acordar, a trava de energia (só acorda se todo marcador
  aberto do aparelho é do pedido). **Risco residual (o dono aceita):** conta logada no app fora da plataforma (seletor de contas do Instagram)
  seria apagada pelo `pm clear`. Quem executa é
  `commands/limpeza_ao_retirar.py` (gancho `SocialService.ao_limpar_aparelhos`, ligado no `AppState`): tarefa de fundo, um aparelho por vez,
  comando `session.logout` dentro de `run_device_job`; hibernado ou parado é acordado com a confirmação de quarentena do SISTEMA e devolvido ao
  estado de antes. Falha em qualquer passo: quarentena aberta, evento `device.account_cleanup` em erro, sem nova tentativa. Idempotente (não repete
  se uma pessoa já resolveu), sem retroativo na subida (retirada anterior ao deploy segue pela rota manual do 29.24), sem o @ em evento ou log.
  **Antes do deploy:** um marcador velho e já mascarado (retirada anterior ao deploy) aberto num aparelho faz a limpeza nova recusar ali
  (`outra_conta`, recusa segura, evento de erro); resolva-o antes pela rota do 29.24 (em 03/10: o do android-04).
