# Domínio: perfis, Instagram e treinamento

Como uma identidade social é representada, o que ela sabe fazer, com que limites, e como o Instagram — o único
app com login determinístico hoje — é operado. Para o contrato HTTP, ver
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
Resposta a comentário é uma vez por pessoa por conta na mesma janela (30.56, emenda do ADR-055): com resposta
desta conta ao mesmo alvo (ou pedido dela em aberto), o `REPLY_COMMENT` é recusado antes do rascunho e da
aprovação, sem chegar ao dono (`UMA_VEZ_POR_ALVO`; a da própria etapa não conta). Limite conhecido: sem a publicação gravada
na interação, uma resposta legítima ao mesmo alvo noutro comentário dentro da janela também é recusada; a 2ª fatia
do 30.56 grava publicação e comentário e passa a chave a ser por comentário.
Toda ação com efeito declara no catálogo SOBRE O QUÊ age (`objeto_alvo`, 30.64): o post, o comentário, a conversa ou a
mídia. Com isso, o mesmo perfil, a mesma ação e o mesmo objeto em duas execuções não saem duas vezes. A DM com o
MESMO texto pede confirmação (o texto gerado se compara depois do rascunho), e a de texto novo segue a política. Com
objeto inequívoco, seguir, curtir o mesmo post, comentar de novo e publicar a mesma imagem são recusados. O objeto
ambíguo (o "post mais recente", sem legenda) pede aprovação, a curtida também até a prova do seletor exato com uma tela
real. A aprovação revista também só vale para o mesmo objeto.
O teto por hora e por dia conta também os pedidos de aprovação desta conta ainda sem interação (30.57). É ele que
limita o leque de um `for_each` com efeito em app real: com `comments_per_hour` 3, 5 itens viram 3 pedidos e 2
adiados. Os valores são calibráveis em dev (29.75).
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

**Grupo "Liberado" (28.61, por dado):** o dono pediu às 19:46Z de 06/10 um grupo "com tudo liberado para todas as
personas". O grupo `grp-AX6yCTUvW7eZFmn2` foi criado no central às 19:57Z pela rota acima.
- Tudo `autonomous`, exceto `LOGOUT`, que segue manual. Os limites são os do grupo Operação.
- Membros: 11 das 16 personas. As 5 do grupo Recuperação ficaram fora, porque é proteção de conta em recuperação.
- Continuam os pisos de aprovação do código (DM fria, feed, pedido entre personas, família, repetição, exceção do 30.65)
  e as recusas, os tetos, a conduta e a proteção de conta.
- Desfazer é devolver cada persona ao grupo anterior (o backup foi gravado antes) e apagar o grupo.

## Aprovações

`backend/app/social/approvals.py` — `ApprovalStore` guarda o pedido (`pending_approvals`, migração
`010_capabilities_approvals.sql`: `status` `pending|approved|edited|rejected|expired`, com
`generated_content`/`approved_content` separados para auditoria). `ApprovalService.decide()` tem três verbos:
`approve`, `edit` (exige texto novo), `reject` (cancela só as etapas daquele alvo, via
`cancel_target_steps`) — nenhum verbo marca a etapa como concluída sozinho. `textos_irmaos()` evita que duas
contas da mesma execução escrevam o mesmo texto. Rotas: `GET /api/approvals`, `POST /api/approvals/decide`
(lote), `POST /api/approvals/{id}/decide`.

## Instagram: classificador, login determinístico, sessão

Desde o ADR-052 o Instagram não tem código próprio: é a pasta de dado
`backend/app/conhecimento/apps/com.instagram.android/`, lida por motores genéricos, DETERMINÍSTICOS e fora do laço
de IA, que não conhecem app nenhum ([abaixo](#o-instagram-como-dado-adr-052); [design](../design/conhecimento-de-app.md)).
Caminhos relativos a `backend/app/`.

- **Telas** — `telas.yaml` (sinais por idioma en/pt, regras de tela em ordem de precedência, a extração
  `conta_no_cabecalho`, o estado conhecido `feed`/`profile`), lido por
  `automation/conhecimento_de_telas.py::classificar`. Estrutura primeiro, texto só desempata: ids do Instagram são
  ofuscados e mudam de versão, então as regras usam prefixos do final do resource-id e o atributo `password`.
- **Leitura para o rascunho** — a seção `leitura` do `app.yaml`, lida por
  `automation/leitura_de_tela.py::LeituraDeclarada` (`visible_content`, `comment_of`, `message_of`): fala só com o
  autor casado na mesma linha — vazio significa "escreva sem isto", nunca "invente"; tela sensível sempre devolve
  vazio. As três regras são do motor, não do app.
- **Login e sessão** — `sessao.yaml` (rótulo, ajustes padrão, formulário, dispensa, aba de perfil, a tabela
  `depois_do_envio` e os textos de ajuda), validado na carga por `integrations/app_declarado/conhecimento.py`
  (`do_app(pacote)`) e executado por `integrations/app_declarado/sessao.py::SessaoDeclarada`, o `SessionProvider`
  genérico que substituiu o `InstagramAuthenticator`. `_login()` confere o campo de usuário antes de prosseguir (até
  `FILL_TRIES` = 3 tentativas, e só envia com o valor conferido) e preenche a senha pelo canal sensível;
  `_watch_after_submit` só observa, nunca reenvia por timeout, e `classificar_depois_do_envio` aplica a tabela
  declarada. `_wrong_account()` é **sempre** intervenção humana — comentário no código (achado #115) registra que a
  troca automática de conta nunca foi implementada de propósito (a flag `auto_switch_account`, que sugeria o contrário,
  não existe mais na configuração; implementar a troca pelo seletor de contas é decisão do dono, ainda aberta). Tela que
  nenhum sinal reconhece não se reobserva para sempre. Item 29.92: na porta de sessão, aparelho com vínculo ativo (conta
  real, `shared.vinculos.tem_vinculo_ativo`) para no PRIMEIRO `unknown` — a rodada seguinte podia cair no login e
  digitar a senha guardada em cima de uma tela que ninguém reconheceu — e a parada sai uma vez em
  `session.needs_person`. Como a porta só existe com vínculo, o `session_unknown_retry_cap` (3) deixou de agir nela e só
  vale onde não há vínculo (hoje, nenhum caminho automático); segue como limite do contador
  (`tests/test_porta_de_sessao_no_teto.py`). A prévia de recursos (`AppSessionProvider`) usa o mesmo teto por aparelho e
  não planeja `session.verify` sobre a parada. A regra do teto mora num lugar só (`shared.vinculos.teto_de_unknown`),
  para a porta, a prévia e o REST. Item 29.96: o `SessionInfo.unknown_at_cap` (adendo v1.48) põe a parada nas filas
  "Aguardando intervenção" e Pendências, com o rótulo "Tela não reconhecida". Segue a regra do aviso (`unknown_no_teto`,
  sem o desconto do teto velho), e o aviso leva ao Foco do aparelho. Item 29.100: as duas filas mostram e ordenam pela
  hora em que o estado começou (`account_sessions.status_since`, migração 112; `SessionInfo.status_since`, adendo
  v1.51), não pela última verificação, que na parada fica vazia ou velha. A hora é a da mudança de estado e, no
  `unknown`, a da chegada ao teto: um `unknown` administrativo antigo que para hoje mostra hoje. Regravar o mesmo estado
  fora disso mantém a hora. Duas exceções, de propósito:
  - o app fora do primeiro plano (`_fora_do_primeiro_plano` em `sessao.py`) grava `unknown` SEM somar, e a porta
    segue automática: abrir o app de novo não é rodada de sessão, porque a tela nem chegou a ser lida;
  - dentro da MESMA rodada, uma tela desconhecida seguida de voltar ou reabrir que cai numa tela de login
    reconhecida faz o login na hora (ADR-040, com consentimento); o teto 1 só impede a rodada seguinte.

  `_needs_person(conta, instance_id)` impede o agendador de sequer tentar de novo quando o estado já pede pessoa (lê a
  sessão da conta **deste app** neste aparelho, 23.4); `_blocked_reason()` recusa entrar sem `consent_at` na credencial
  da conta (ADR-040). Tetos, cooldown e prazos são os `ajustes` do `sessao.yaml` com a sobrescrita da instalação por
  cima (`contas.sessao.<pacote>.<ajuste>` no `config.yaml`, entregue por `Config.ajustes_de_sessao`), lidos a cada uso.
- **Conta aberta** — `sessao.py::ler_conta` lê a conta SÓ na tela de perfil declarada (`conta.tela_de_perfil`),
  depois de tocar na aba de perfil declarada (`conta.aba`), nunca pelo feed (bug histórico de confundir autor de
  post com dono da conta, K-021). A conta lida é comparada ao `handle` e ao `login_identifier` da **conta do app**
  que a chamada abre (`ContaDaSessao.handle`/`aceitos`, sem maiúsculas nem o `@` do começo), não ao `@` de cadastro
  do perfil (item 23.4).
- **Login em etapas, conta fora da barra e Custom Tab (item 23.6, ADR-057 decisão 3)** — três blocos opcionais do
  `sessao.yaml`, lidos pelo mesmo motor (quem não os declara segue igual, como o Instagram):
  - `formulario.etapa_do_usuario: {tela, sinal_do_botao, recusas: [{sinal, detalhe}]}`: o identificador numa tela (a
    `tela` do `telas.yaml` tem de ser do tipo `login` e não a do campo de senha) com um "avançar", a senha na seguinte.
    A geometria (`formulario.py::identifier_form`) exige UM campo de texto, nenhum de senha e UM botão abaixo que casa o
    sinal por inteiro. `SessaoDeclarada._etapa_do_usuario` preenche, confere, avança e espera a tela da senha; o
    cabeçalho clicável com a conta, acima da senha, nunca é tomado por usuário (`LoginForm.usuario_editavel`). **A senha
    só é digitada numa tela que mostra ESTE identificador**, como palavra inteira
    (`formulario.py::mostra_o_identificador`): o app que lembrou outra conta e abriu direto na senha não recebe a senha
    desta. Recusa do identificador, tela sem a conta e tela da senha que não chegou param o login em `review` (a
    credencial não vira `invalid`: ninguém julgou a senha). O "avançar" já é efeito no servidor (o identificador saiu; o
    provedor pode mandar um código ou um pedido de aprovação a cada vez): contar falha e esperar o intervalo repetiria
    esse toque sem fim.
  - `conta.acesso: {ids, rotulos}` no lugar de `conta.aba` (exatamente um dos dois): a conta é aberta por um elemento
    do app fora da barra inferior (avatar, menu), por prefixo de id ou rótulo, com **um candidato só**
    (`formulario.py::account_opener`); a leitura usa a extração declarada, no texto ou na descrição, e só vale com
    **um valor** na tela (`formulario.py::valores_extraidos`: um menu que lista duas contas não diz qual está aberta).
    `conta.ler_ao_entrar: true` lê a conta pelo acesso logo depois do envio, numa tela de casa sem a conta à vista
    (`_ler_ao_entrar`, uma vez por login; a tentativa que termina com outro pacote na frente — a Custom Tab ainda
    fechando — não gasta essa vez): sem isso, o login bem-sucedido de um app cuja tela inicial não mostra a conta
    terminaria incerto. `ler_conta` recebe o reconhecimento do provedor (`reconhecer=`), o mesmo da Custom Tab. `ConhecimentoDeSessao.aba_de_perfil` responde pelos dois (o aprendizado pergunta o
    mesmo).
  - `navegador: {pacotes: {<pacote do navegador>: <sufixo do id da barra de endereço>}, hosts: [...]}`: a Custom Tab do
    login. O navegador declarado só é tela do app (`SessaoDeclarada._reconhecer`) num site de login que o app declara
    (os `hosts`, igual ou subdomínio). O `host` de uma conta não soma: conta com `host` é de portal, pelo navegador, e
    não tem login gerenciado — `_resolver_conta` a recusa sem tocar no aparelho e a rota
    `…/accounts/{aid}/session/{connect|verify}` responde 409 `conta_de_site` (a porta e o despacho só acham a conta do
    app inteiro, `conta_do_pacote`). A barra de endereço nunca é campo do formulário (`formulario.py::Ignorados`: numa
    página de senha sem o cabeçalho da conta, a geometria a tomaria por usuário). A senha é conferida no instante de
    digitar, na MESMA árvore do campo (`_fill_password`: o `locate` do canal sensível só acha campo no pacote da conta
    ou no navegador no site permitido); fora disso o canal recusa com a mensagem fixa dele.

- **Troca de conta declarada (31.155, [ADR-080](../decisoes.md#adr-080--troca-de-conta-declarada-pelo-app-o-motor-sai-da-conta-aberta-e-entra-na-esperada-pelo-cofre))**:
  `troca: {sair: [{tela, sinal_do_botao}, …]}` no `sessao.yaml`. O primeiro passo é numa tela `autenticada`; os
  seguintes, numa `autenticada` ou `intersticial` (a confirmação). Com ela, a conta errada lida pelo motor deixa de ser
  caso de pessoa: `SessaoDeclarada._trocar_de_conta` primeiro confere, sem tocar, se a esperada pode entrar
  (`_antes_de_sair`: senha com consentimento, teto diário, parada, canal sensível). Depois toca a saída, um candidato
  por passo; invalida as sessões do app no aparelho e só entra pelo `_login` de sempre se a tela for a de login. Qualquer
  desvio vira `wrong_account` com o motivo. O "Verificar conta" nunca troca. O app que declara a troca ganha
  `AppDefinition.account_switch`, e só nele `quem_ja_serve` aceita outra persona do mesmo app no aparelho; o índice
  único da 051 saiu na migração 126. **Nenhum app do parque declara a troca hoje**, nem o Instagram.

  O `type_secret` do executor (`taskqueue/executor.py::_conferir_destino`) ainda recusa a Custom Tab (exige o pacote da
  conta em primeiro plano): só o motor de sessão a aceita. O `telas.yaml`/`sessao.yaml` do Outlook veio no 23.8
  ([abaixo](#o-outlook-como-dado-item-238)); aparelho real: `not_run`.
- **Entrada e alternativa do login em etapas (item 23.8)** — mais dois blocos opcionais em
  `formulario.etapa_do_usuario`, para o que o Outlook mostrou e o motor ainda não sabia fazer:
  - `entrada: {tela, sinal_do_botao}`: a tela do app deslogado (tipo `login`, sem campo de senha, diferente da do
    identificador) e o botão que abre a tela do identificador. `SessaoDeclarada._abrir_a_etapa_do_usuario` toca o botão
    (um candidato só, `formulario.py::botao_unico`, sem casar a exclusão) e espera a tela do identificador até
    `submit_wait_s`.
  - `alternativas: [{tela, sinal_do_botao}]`: telas entre o "avançar" e a senha em que o provedor propõe um código e
    oferece a senha.

  Prova `simulated`: `backend/tests/test_outlook_declarado.py` (os dois blocos na pasta real do Outlook e a carga
  recusada); quem não os declara segue igual (`::test_quem_nao_declara_entrada_nem_alternativa_segue_como_antes`).

A porta de sessão e a distribuição já recusam perfil fora de `active`. Pausa do dono (`disabled`) não é reescrita.
Resolver a tela não reativa sozinho: quem reativa é a pessoa, na tela do perfil.

- **Conta travada = `blocked`.** Um detector único (`automation/hierarchy.py`,
  `conhecimento_de_telas.py::detectar_conta_travada`) casa na união dos idiomas, com o texto normalizado (apóstrofo
  tipográfico, acento), e roda depois de cada observação e na sessão, antes do ANR, da receita e do ator; sai sem tocar.
  Todo status passa por `mudar_status` (`blocked_at`, `blocked_evidence`, `blocked_origin`, evento `profile.status`).
  A troca do grupo de política (`policy_group_id`) pelo PATCH do perfil emite `profile.policy_group` (31.269, adendo
  v1.131): ids dos dois grupos e o autor, sem nome nem @.
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
  gesto com efeito DESTA conta, com `retry_at`; uma interação por vez, sem link, texto natural.
  Conta RETIRADA por bloqueio segue recusada. "Uma conta por alvo" e a aprovação valem entre contas nossas como para qualquer
  alvo, e a conta nossa continua fora da elegibilidade de "terceiro" (8.3).
- **Uma conta por alvo.** Seguir, DM e comentário: no máximo uma conta por pessoa numa janela de 30 dias, com o
  excedente recusado; curtida e comentário ganham o alvo (`post_author`, herdado de OPEN_POST); um pedido igual a várias
  contas na mesma execução segue numa conta só, com aprovação.
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
- as garantias continuam do provedor: nunca repete envio por timeout, nunca segue com conta errada, senha só pelo canal sensível e só com o consentimento da conta (ADR-025/040);
- a implementação de produção é o motor genérico `integrations/app_declarado/sessao.py::SessaoDeclarada`, montado
  com o conhecimento de cada pasta de app que tem `sessao.yaml` pela fábrica do manifesto
  (`integrations/app_declarado/pacote.py::fabrica_de_sessao`; ADR-052, [acima](#instagram-classificador-login-determinístico-sessão));
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

**A sessão é da conta num aparelho (049), e o motor é por conta (23.4).** `account_sessions(account_id, instance_id)`
substitui a sessão única por perfil. Desde o item 23.4 (ADR-057) o provedor recebe a conta (`ensure_session(rt,
profile_id, account_id=…)`) e o motor genérico resolve, antes de tocar no aparelho, a `ContaDaSessao` da chamada
(`SessaoDeclarada._resolver_conta`): tudo o que ele lê e grava é dela — credencial e consentimento
(`account_credential_row`), marcação da credencial (`mark_account_credential`, `touch_account_credential`), tentativa
(`start_auth_attempt(account_id=…)`), teto diário (`auth_attempts(account_id=…)`: os envios de um app não gastam o teto
do outro), sessão no aparelho (`account_session_row`/`set_account_session`) e o marcador de conta travada
(`registrar_conta_travada(handle=, app_id=)` com o `@` e o app da conta). O "verificado em" do cartão do perfil
(`last_verified_at`) só muda com a conta âncora. `SocialRepository.session_row`/`set_session`/ `credential_row` (por
`profile_id`) ficam como leitura da conta âncora para o DTO do perfil e os chamadores antigos. Invalidação:
`invalidate_sessions_of_instance(package=…)` só as contas daquele app; `todos_os_apps=True` (o gancho do aparelho,
`AppState._invalidate_sessions` sem pacote: reset e troca de máquina) toda conta ali; sem nenhum dos dois, a âncora. A
tela que desmente a sessão no meio da execução (`AppState._sessao_desmentida`) corrige a conta do app da tela: o
`package=` de quem viu (o executor passa o pacote da tela em que viu a trava, `on_auth_needed`), senão o da etapa em
curso no aparelho (`AppState._pacote_em_curso`), senão a âncora. Mover o aparelho ou a persona de máquina pede
confirmação com a sessão pronta de QUALQUER conta dela ali (`api.py::_recusar_mudanca_de_servidor`,
`SocialService._recusar_troca_de_servidor`). Consequências:

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

- São regras do perfil (design §6, raiz `Profile`), e o núcleo importava o autenticador só para aplicá-las. Hoje os dois
  chamadores são o motor de sessão (`SessaoDeclarada._save`) e `AppState._sessao_desmentida`;
- as portas que elas usam: `ports.py::ProfileStore` (`profile_row`, `update_profile`) e `ports.py::EventSink`
  (`emit`).

**Desvio do desenho.** O classificador de tela da §7 ficou fora da porta: nenhum código do núcleo o consumiria sem mudar
comportamento. Desde o ADR-052 ele é o motor genérico `automation/conhecimento_de_telas.py::classificar`, sobre o
`telas.yaml` de cada app.

Conta real e PostgreSQL: `not_run`.

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
- **Desfazer a última entrada (item 31.90-D).** `desfazer_a_ultima()` (`POST /api/training/{session_id}/undo`) tira
  da gravação VIVA a última entrada, o toque errado, sem descartar a sessão. Exige o lease do controle (o mesmo do
  `start` e do `stop`). A gravação parada se corrige na revisão (409 `nao_esta_gravando`), e a órfã não tem quem esteja
  ensinando (409 `control_required`). O `seq` opcional confere que a última ainda é a que a pessoa viu (409
  `entrada_mudou`), e a leitura e a remoção ficam na mesma transação, que antes confere de novo o status por um UPDATE
  condicional na sessão (um `stop` no meio não deixa apagar entrada da gravação parada). A próxima entrada gravada
  recebe o número seguinte ao que ficou: o `seq` desfeito é reaproveitado. O aparelho não volta: a entrada sai só da
  gravação.
- **Validação do `save` (item 31.83).** O `propose` normaliza a proposta; o `save` aceita a que o cliente manda, por
  isso `validar_proposta_para_salvar` (`training/skills.py`) a confere ANTES de qualquer escrita (fluxo, escopo,
  receita, status da sessão) e recusa com 400 e a mensagem do que corrigir. Códigos, além dos de sempre:
  - `etapa_invalida`: etapa sem chave (ou com chave fora de `^[a-z][a-z0-9_]{1,40}$`), chave repetida, sem título e
    objetivo, ou pós-condição de tipo inválido (antes era `KeyError`/500).
  - `parametro_fora_do_comando`: declarado em `parameters` e ausente do comando; o fluxo nunca casaria.
  - `parametro_nao_declarado`: `{x}` no comando sem parâmetro declarado (marcadores reservados não contam).
  - `comando_generico`: o comando tem de COMEÇAR por palavra fixa (o fluxo casa com `.+?` e `fullmatch`, então `{pedido} no instagram` sequestraria todo pedido que termine assim) e ter ao menos 2 palavras e 6 letras fixas fora das chaves (`ligue para {contato}` passa; `siga {perfil}` não).
  - `parametro_invalido`: `{…}` no comando que não é nome válido (maiúscula, acento): ficaria literal e o fluxo nunca casaria.
  - `entrada_duplicada`: entrada em duas etapas, em etapa e em `discarded`, ou repetida em `discarded` (o `_receitas` tiraria o toque da etapa calado).
  - `entrada_inexistente`: `seq` em etapa ou em `discarded` que não está entre as gravadas (a receita apontaria para nada; 31.95).
  - `parametro_invalido` vale também para `{` ou `}` sem par; `side_effect` tem de ser booleano (`"false"` virava verdadeiro).
  - `pos_condicao_vazia`: etapa com efeito externo sem `postcondition.value` nem `description` (e sem ação de
    catálogo, que traz a sua). Sem efeito, o objetivo serve de critério e o `save` devolve o aviso em `warnings`.
  - `entradas_sem_etapa`: entrada gravada que não está em nenhuma etapa nem em `discarded` (a lista `#n` vem na
    mensagem); `proposta_invalida`: tipo errado (lista que não é lista, descarte sem `seq` inteiro, `summary`/`app_id`/`parameters`).
  - `inputs` e `discarded` são listas de inteiros; `title`, `goal`, `value`, `bindings` com tipo errado são `etapa_invalida` (400, não 500).
  - A chave de etapa do `propose` (`normalizar_proposta`) é única e sempre cabe no padrão de `PlanStep.key` (31.93: o laço
    antigo não terminava com chave de 40 caracteres repetida).
  - O `TRAINER_SYSTEM` pede cobertura total das entradas e manda para `discarded` as teclas de apagar que só limpam
    o campo e o `back`/`home` que desfazem engano (a reprodução limpa o campo; tecla na etapa derruba a receita).

- **Prévia e reparo das receitas (31.86 B).** `POST /api/training/{id}/preview` (corpo do `save`) devolve, sem
  gravar, o que o `save` faria: por etapa `recipe` e o `reason` literal, e os `warnings`; usa `_preparar`, a mesma
  conferência do `save`. `POST /api/training/{id}/recipes` refaz as receitas de uma sessão já salva (o plano vem do
  fluxo, as entradas da proposta guardada). Fora do ar, o save só grava receita se a versão do app e a variante de
  interface ainda estão lembradas (cache do executor/inventário); senão o motivo diz o que falta e o reparo roda
  quando o aparelho volta. Contrato: adendo v1.58.

Migração `038_modo_treinamento.sql`: `training_sessions` (`status`: `recording|recorded|proposed|saved|discarded`),
`training_inputs` (`type`: `tap|long_press|swipe|text|key|open_app`), `flow_scope`, `flows.source` (`'run'` ou
`'training:<sessão>'`).

**O que o gravador não guarda (31.94, 31.97).** `training_inputs.sensitive` quer dizer "tela sensível OU entrada que não
se guarda" (uma coluna para os dois sentidos até a migração futura do ensino separá-los). Toque em tecla de teclado numérico
ou em tela sensível sem id estrutural, e arraste (`swipe`) com a ORIGEM em contêiner de teclado ou
padrão de bloqueio, ou em tela sensível, saem sem `x`/`y` (e `x2`/`y2`) e com `sensitive=1`: o padrão de bloqueio se desenha
arrastando e a posição da tecla é o dígito. A rolagem comum guarda as quatro coordenadas. Sem árvore da tela, toque e
arraste perdem as coordenadas. Os leitores (`linha_da_entrada`, `distill_training`, `proposta_simulada`) tratam o arraste sem
coordenada como entrada não gravada: a etapa não vira receita.

**A partida e a pós-condição da etapa ensinada (31.121, 31.122).** A pessoa costuma abrir a gravação com o app já na
frente, então a 1ª entrada é um toque dentro dele. Na reprodução o aparelho está em outra tela, e a receita da 1ª etapa
divergia ("tela de partida diferente"): 3 de 7 execuções de fluxos ensinados em 06/10. A destilação (`_destilar`) agora
põe `open_app` do app da sessão antes da 1ª entrada, quando a gravação começou dentro dele sem abri-lo
(`training/partida.py::com_abertura`). A gravação não muda. A prévia e o `save` também conferem cada pós-condição
`text_visible` contra os `screen_lines` da 1ª entrada da etapa (`partida.ja_valem`, a regra do verificador). Se o texto
já está na tela de partida, a etapa passaria sem agir: a prévia avisa e o `save` recusa (400 `pos_condicao_ja_vale`,
adendo v1.83), com até três textos da tela seguinte como sugestão. O dado da persona não é sugerido e sai com o
marcador.

**A etapa que conclui num pacote vizinho (31.123, migração 120, adendo v1.84).** A busca do Configurações é de outro
pacote, e o executor recusava concluir fora do app da etapa (`_tela_fora_do_app`). A execução r-20261006012340-d92795
gastou 33 chamadas por isso. A etapa ganhou `pacotes_aceitos` (coluna `steps.pacotes_aceitos`): os pacotes, além do app
dela, em que a tela comprova a conclusão. O ensino a preenche com os pacotes das entradas da etapa
(`partida.pacotes_vizinhos`), sem o próprio app, o systemui, o lançador e os apps cadastrados (esses já são o app de
uma etapa). A prévia e o `save` avisam. Pacote desconhecido continua não comprovando. A lista vazia é omitida do
plano, e o hash das etapas não muda.

**F2 da partida, do pacote vizinho e a ordem das etapas (31.122 F2, 31.123 F2, 31.127; migração 121, adendos v1.85 e
v1.86; achados da prova conjunta r-20261006102728-1157c6).**
- 31.122 F2: o recorder guarda os elementos compactos de cada tela (`training_inputs.screen_elements`, uso interno), e
  `partida.ja_valem` confere a pós-condição contra a tela INTEIRA da partida pela regra do verificador
  (`contains_text`; `find_selector` para `element_present`, que agora também é conferida). Antes, só as
  `screen_lines` (conteúdo filtrado, 8 linhas): o id `search_action_bar_title` cai no filtro de interface, e
  "Search settings" passou, até como sugestão. A recusa e a prévia trazem `pos_condicoes_ja_valem` estruturado
  (`{etapa, valor, sugestoes, message}`) para o alerta dentro da etapa (31.128). Sessão antiga: `screen_lines` +
  `screen_title`.
- 31.123 F2: a tela em que a etapa TERMINA (a da entrada seguinte, `partida.entrada_seguinte`) também entra em
  `pacotes_vizinhos`. O toque no app que abre a busca de outro pacote termina lá; antes, essa etapa era recusada e a IA
  assumia (US$ 0,039 na prova).
- 31.127: cada etapa do fluxo ensinado nasce com `depends_on = [anterior]` (`skills.em_sequencia`), salvo
  `independente: true` na etapa da proposta. O executor já esperava a dependência; antes, a 2ª etapa rodava com a 1ª em
  `retry_wait`, e o objetivo fechou `completed` na tela inicial.

**A sugestão pronta e a prévia com o comando repetido (31.142, adendo v1.91; achados da prova F2,
trn-YjYU8iobj_V42xXx).** A única sugestão ("Back") era a descrição do botão voltar. Com `element_present`, trocar só o
valor deixaria o seletor puro, que só olha o texto, e a etapa nunca passaria. Cada entrada de `pos_condicoes_ja_valem`
traz `sugestoes_prontas` (`partida.pronta`): `text_visible` fica `text_visible`; `element_present` vira `text==` ou
`desc==` pelo campo em que o texto está na tela seguinte, e o botão aplica `kind` e `value` juntos. A prévia não para
mais no 409 `duplicate_command`: devolve `code` e `message` num 200, com a frase na 1ª linha de `warnings`, junto das
pós-condições e dos avisos. O `save` segue recusando com o 409.

**A proposta que já evita a pós-condição da partida (31.148, adendo v1.93).** O 31.122 e o 31.142 pegavam o erro
depois. O `propose` passa a mandar à IA, por entrada:
- os textos da tela inteira (`screen_elements`, texto e descrição);
- os que apareceram depois dela (`partida.textos_da_proposta`).

Todo dado da persona vira marcador, inclusive o alvo tocado. A regra no papel do sistema: a pós-condição não pode estar
na tela da 1ª entrada da etapa. Se a IA ainda propuser uma que já vale, a proposta já volta com `pos_condicoes_ja_valem`
e as sugestões prontas. A pessoa decide.

**O fluxo nascido de uma prova (31.130, migração 122, adendo v1.87).** Os fluxos ensinados em provas de sessão
ficavam, no Livro e em Salvas, iguais a um fluxo real desligado por uma pessoa. A sessão aberta com
`nascido_de_prova: true` leva a marca (`training_sessions.nascido_de_prova`), e o `save` a passa ao fluxo
(`flows.nascido_de_prova`). Ela sai no GET e na listagem das sessões, em `GET /api/flows` e na `origem` do conteúdo
do fluxo no Livro, com o filtro `nascido_de_prova=true|false` nas duas listagens. As provas das frentes abrem a sessão
com a marca e, ao desligar o fluxo de prova, mandam `motivo` no `PUT /api/flows/{id}` dizendo que é prova (vai à
trilha do livro). Os de antes se marcam pelo id com `scripts/marcar-fluxo-de-prova.py`. Desde o 31.143 (adendo v1.92),
a lista do Livro (`GET /api/aprendizado`) traz `nascido_de_prova` em cada item e aceita o mesmo filtro; antes, a marca
só saía no detalhe, e o selo e o filtro "Prova" da lista (31.131) ficavam sem dado.

**A abertura pelo lançador (31.139).** 2 de 9 fluxos ensinados começavam na tela inicial do aparelho (abrir a gaveta,
tocar no ícone), e a receita guardava o toque no layout do lançador daquele aparelho (a 198 nunca reproduziu). Na
destilação, o trecho do começo feito no lançador, seguido de entrada já no app da sessão, sai das etapas, e a etapa da
1ª entrada ganha `open_app` do app (`training/lancador.py`). O 31.121 cobre a gravação que começa dentro do app. A
gravação não muda.

**A abertura nas receitas antigas (31.138).** As regras do 31.121 e do 31.139 só valem para o que se salva depois
delas: o único fluxo ensinado ativo e três desligados seguiam com a 1ª receita só com o toque. O passe único
`scripts/abertura-nas-receitas-ensinadas.py` (`training/reparo_da_abertura.py`) destila de novo cada fluxo ensinado e
troca a receita viva da etapa que agora abre o app, como a demonstração troca (30.79), na mesma chave e com a trilha
no livro. Ensaio por padrão; a Android aplica como operadora.

**Os pacotes aceitos à vista (31.140, adendo v1.89).** `pacotes_aceitos` existia só no plano salvo e nas etapas da
execução. Agora cada etapa da prévia, do `save` e do reparo das receitas o traz, e a etapa do fluxo no Livro
(`etapa_de_fluxo`) também. A tela usa isso no 31.141.

**A origem do fluxo ensinado (31.135, adendo v1.88).** O fluxo só dizia "Demonstrado no treino". Agora a `origin` de
`GET /api/flows` e a `origem` do conteúdo no Livro trazem, de todo fluxo ensinado, a sessão (`session_id`), o
aparelho, quem ensinou (`operator`) e quando (`ensinado_em`), no molde do v1.81. Os ids da falha seguem `null` quando
não veio de uma.

**Teclas ao ensinar (31.84).** O texto digitado pelo painel acrescenta ao campo (`clear_first=false`), e quem ensina
apagava um caractere por vez com "Apagar". Como a receita digita com `clear_first=True`, que já limpa o campo,
`distill_training` trata assim as teclas gravadas na etapa:
- `delete` é ruído só quando COLADO ao `text` que vem depois (sequência contígua de `delete` e então o `text`, sem
  toque, arraste ou outra tecla no meio: o apagar pode ter sido em outro campo) E esse texto foi gravado com
  `clear_first` (o gravador marca em `training_inputs.key_name = 'clear_first'`, sem coluna nova; sem a marca o apagar
  pode ter sido parcial e a receita, que limpa tudo, divergiria); nos demais casos muda o resultado e a etapa segue
  sem receita;
- `enter` imediatamente depois de um `text` vira `press_enter=True` da própria ação `type_text`; `enter` solto recusa;
- `back`, `home` e `recents` dependem do estado de quem ensinou e recusam, como antes.
`ManualInput.clear_first` (só `type='text'`; o `type_text` do Appium engole a falha do `clear()` e acrescenta, e a
pós-condição da etapa é quem pega) deixa o painel limpar o campo antes de digitar, sem N toques em Apagar;
pelo ADB puro, sem sessão Appium, recusa com `bad_input` (o `input text` só acrescenta). O gravador não mudou.

Numa entrada `text` da sessão de treino, `key_name = 'clear_first'` é a marca de que o texto foi enviado limpando o campo
(a API de leitura passa a mostrar isso); a marca vai para coluna própria numa migração futura.
Se o `stop` da gravação falhar no fim do controle, a linha `recording` fica órfã até a próxima subida ou o próximo
`start` (que o 31.80 já trata); `rt.training_session_id` é zerado de qualquer modo.

**Quadro velho ao gravar (31.85).** Gravando, cada entrada lê a hierarquia antes de agir e o aparelho fica lento; o quadro
que a pessoa vê passava da idade máxima e as teclas seguintes eram recusadas em série (`stale_frame`). Em
`DeviceManager.manual_input`, com gravação ativa, o quadro igual a `rt.frame.info.id` (o mais recente) vale mesmo acima
da idade, com a captura sã e até `TETO_QUADRO_NA_GRAVACAO_MS` (60 s). Quadro antigo com um mais novo disponível, e todo
quadro velho fora da gravação, seguem recusados.
Pela folga, toque e arraste só passam com quadro capturado depois da última entrada (`rt.ultima_entrada_mono`); texto,
Enter e Apagar ainda exigem a hierarquia lida antes da ação: sem ela, com tela sensível ou foco em senha,
voltam `stale_frame`. Falha ao gravar a entrada loga só o tipo da exceção (o DETAIL do PostgreSQL pode trazer o texto).

## O Instagram como dado (ADR-052)

O dono pediu "zero Python por app", e o Instagram deixou de ter código: `integrations/instagram/` e
`planning/catalog/instagram.py` foram apagados. Tudo o que a plataforma sabe dele está em
`backend/app/conhecimento/apps/com.instagram.android/`, lido pelos motores genéricos descritos
[acima](#instagram-classificador-login-determinístico-sessão)
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

### Publicar no próprio feed (item 29.30, PR-A)

Três ações em `catalogo.yaml`, em sequência: `READ_POSTS_COUNT` (sem efeito; entrega `posts_antes`, a contagem lida do
cabeçalho do PRÓPRIO perfil) → `PUT_MEDIA_IN_GALLERY` (`internal`, `image_id` obrigatório: código, não IA) →
`CREATE_POST` (`approval_required`, `risk: high`, `needs_draft`; a legenda é o `content` aprovado, tipo de texto
`post_caption`). A prova de `CREATE_POST` é local, `count_gt:posts_antes:<seletor>`: o número do perfil depois de
publicar é MAIOR que o lido antes (`taskqueue/proofs.py`; abreviado como "1.2K" ou sem o elemento não afirma, e o modelo
julga). Por timeout nunca se toca em Share de novo: a reconciliação confere a contagem.

- **Sem alvo.** Publicar não tem outra pessoa do outro lado: a interação é `post_published`, o balde é `posts`
  (`BALDES_SEM_ALVO` isenta só ele do `counterparty`; a regra de uma conta por alvo, ADR-055, não se aplica) e os tetos
  são `posts_per_hour` e `posts_per_day`, ambos 1 (o aquecimento usa `max(1, …)`: continua 1). Nenhum argumento da ação
  se chama `username` nem `target`, para o `open_effect` não tomá-lo por alvo.
- **A mídia.** `PersonaImageService.obter(persona, imagem)` consulta pelo par: imagem de outra persona, inexistente ou
  não pronta é recusada ANTES de qualquer push. O `Adb.enviar_midia_para_galeria` serve aparelho local e remoto (a
  central alcança o remoto pelo túnel, `docs/worker.md`): não há verbo de agente.
- **Estado.** Contrato NÃO medido: os seletores do editor de publicação do Instagram 447 (botão Share, contador do
  cabeçalho) ainda não foram explorados num aparelho. Prova `simulated` em `test_create_post.py`; real e remoto
  `not_run`, à espera da exploração autorizada pelo dono.
- **Pendente (PR-B).** O planejador só oferece ações não `internal`: falta inserir `PUT_MEDIA_IN_GALLERY` antes de
  `CREATE_POST` no plano, e o painel mostrar a imagem na aprovação (o `image_id` está nos `bindings` da etapa).
- **Endurecido (30.60, revisão do deploy 29).**
  - *Próprio perfil.* A prova é `count_gt:posts_antes:<contagem>&id=action_bar_title|text=={account_label}`. A guarda
    depois de `&` exige o título do perfil da conta esperada (`profile_accounts.handle`, nunca o identificador de login).
    Guarda que não casa ou variável sem valor dá `None`: o modelo julga, e a guarda nunca reprova.
  - *Não fecha sozinha.* A contagem sobe de forma otimista antes de o upload terminar. Com efeito disparado, a prova
    `count_gt` vira fato para o verificador e não atalho: o "sim" passa pelo rejulgamento do 17.10. As marcas
    `pending_marks` ("Posting…", "Finishing up…") e `failure_marks` ("Couldn't post") estão declaradas, com os textos
    NÃO MEDIDOS. O `sent_text` da DM segue como atalho, porque é o critério objetivo do ADR-055.
  - *Galeria só com a imagem da etapa.* O editor abre na mídia mais recente. Antes do push, `Adb.enviar_midia_para_galeria`
    tira do MediaStore e do disco o que estiver em `Pictures/Central/img_*`. Depois do push, o `ls` precisa mostrar só o
    arquivo da etapa. A indexação é conferida por `content query` no MediaStore (até 5 s); sem ela, a etapa falha.
  - *Persona do aparelho.* A persona do objetivo precisa ter vínculo ativo com o aparelho
    (`SocialRepository.binding`). O vínculo secundário basta: o android-13 também é do André.
  - *Aprovação.* A revisão do plano só reaproveita a aprovação com a mesma imagem (N1). O texto editado só se grava na
    etapa se a decisão vencer a corrida pelo `pending` (N3). Publicar passa por aprovação mesmo com perfil autônomo (N4,
    emenda do ADR-055).
  - Prova `simulated` em `tests/test_create_post_endurecido.py`. Ficam `not_run`: os textos das marcas, o `content` do
    MediaStore no aparelho real, o botão Share e o push remoto.

## O Outlook como dado (item 23.8)

`backend/app/conhecimento/apps/com.microsoft.office.outlook/`, lido pelos mesmos motores, sem Python do Outlook:

| Arquivo | O que declara |
|---|---|
| `app.yaml` | — |
| `telas.yaml` | sinais `en` e `pt`, boas-vindas, "Add account", espera do WebView, "Verify your email" (código), senha da Microsoft, "Stay signed in?", as intermediárias do primeiro uso, gaveta e caixa; extração do e-mail da conta dentro do painel da gaveta |
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
  outro tipo: código, login e casa nunca se dispensam), só nela, com um candidato só do próprio app
  (`formulario.py::botao_unico`). É para o botão que não é recusa em lugar nenhum além daquela tela — "OK" e "NEXT" como
  rótulos globais seriam tocados em qualquer tela.
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
- a tabela `pt` inteira (os aparelhos observados rodam em inglês).

O que a suposição errar termina incerto, com o login automático parado até uma pessoa olhar — nunca sucesso. Só "Help us
protect your account" entrou no detector genérico (`automation/hierarchy.py::_CONTA_TRAVADA`, que omite a captura antes
de ela sair): as outras frases, soltas ali, pegariam DM de golpe no Instagram ("your account has been locked").

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

Observação real (relato da sessão da IDE, android-06, 30/09 22:18–22:24Z, sem id de execução registrado aqui): a senha
pelo canal sensível foi aceita, e o motor de então parou incerto no aviso da conta; a sequência seguinte foi atravessada
à mão e é a que este dado declara. O login automático de ponta a ponta com este dado: `not_run` (29.12 e 23.13).

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
Custom Tab com site declarado, no item 23.6 ([acima](#instagram-classificador-login-determinístico-sessão)).

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
- Só texto na tela, ou conta de outro app, fica `blocked`/marcada para a pessoa; a rota manual retira qualquer conta. Falha deixa a persona `blocked` e o erro no histórico; a rota refaz.
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

## A conta âncora ociosa é relida de tempos em tempos (31.302)

- **Por quê.** A validade da sessão (`contas.session_max_age_s`) só reverifica ANTES de uma tarefa. A persona que ninguém usa
  fica `session_ready` mesmo que o app já mostre "Confirm you're human": a conta bloqueada só é vista por observação
  (ADR-055, ADR-068). O laço `VerificacaoPeriodica` (`modules/identity/infrastructure/verificacao_periodica.py`) fecha esse buraco.
- **Como.** A cada `contas.verificacao_periodica_h` horas (0 = desligado, o padrão) escolhe a sessão `session_ready` mais antiga
  de persona ativa e a relê com `ensure_session(observe_only=True)`: sem IA, sem digitar, sem ligar aparelho. Uma conta por volta
  e uma releitura por vez. Os motivos de pulo são fechados (`application/verificacao_periodica.py`): aparelho fora do ar, controle
  manual (o 409 `device_busy`), aparelho ocupado, quarentena, pausa de reparo, worker em manutenção, CPU do host acima do limite
  (a proteção contra funil e suíte), portão do botão "Verificar conta" e a tentativa recente que não leu a tela.
- **O bloqueio não mora aqui.** O desafio visto na releitura cai em `aplicar_desafio` dentro do motor de sessão (a persona vai a
  `blocked`, o aparelho a quarentena, `session.needs_person` avisa): é o caminho único do ADR-068. Evento e relatório:
  [contrato v1.136](../api-contract.md#adendo-v1136-10102026-número-da-orquestradora-item-31302--a-releitura-periódica-da-sessão-da-conta-âncora).

## O egresso é medido DENTRO da janela do login, e o que a bifurcação de 10/10 mostrou (31.329)

- **A regra.** Antes de digitar a senha, o motor de sessão (`_login`, sem abrir tentativa) pede à convergência de rede uma medição NOVA
  da saída do aparelho (`ConvergenciaDeRede.medir_para_o_login`, como uid 2000). Só vale para aparelho com proxy pedido cujo perfil
  declara `egress_esperado` (as contas do igfarm). Saída diferente do esperado, medição sem IP ou medição mais velha que 30 s: o login
  aborta com o motivo `egresso não casou`, sem tocar na tela e sem gastar o teto diário da conta. O resultado é `UNCERTAIN`.
- **O registro.** A medição vira linha em `network_measurements` (`method` "sonda de IP na janela do login (uid 2000)") e o evento
  `session.egresso_na_janela` (`data`: `esperado`, `medido`, `medido_em`, `distancia_s`, `casou`, `medicao_id`, `detalhe`, `profile_id`,
  `account_id`).
- **Por quê.** Em 10/10/2026 a sessão sticky do proxy girou entre uma medição das 18:48Z e o login das 18:58Z (a medição seguinte,
  19:13Z, deu outro IP). Medição de minutos antes não prova o IP no instante em que a senha sai.
- **O que a bifurcação mediu** (3 contas do igfarm em 3 aparelhos, prova `real`, 10/10/2026): a H1 (egresso casado nas duas medições, a
  última 23 s antes do login) caiu em "Confirm you're human" 37 s depois do envio e foi retirada pelo caminho de sempre; a H2 (casado
  10 min antes do login, depois girou) terminou numa tela branca do próprio app, sem desfecho; a H3 não teve saída pelo proxy e não
  logou. n=1 de desfecho: não prova que o IP casado basta nem que não basta. O JSON de prova fica fora do Git, em
  `.claude/handoffs/ponte-bifurcacao-3-contas.json`.

## "Can't find account" é desfecho próprio, não tela desconhecida (31.332)

- **A tela.** Depois do envio, o app pode abrir um diálogo de dois botões (TRY AGAIN, SIGN UP) com o título "Can't find
  account" e o corpo "We can't find an account with <identificador>…". Medido na H2 (android-08, 10/10/2026); a
  hierarquia fica fora do Git, em `.claude/handoffs/bifurcacao/h2-retry-tela-final.json`.
- **O desfecho `conta_nao_encontrada`** (`depois_do_envio` do `sessao.yaml`, sinal `user_not_found` do `telas.yaml`).
  Não é bloqueio: não passa pelo detector de conta travada e **a conta não é retirada** (nenhuma lápide, nenhum
  `profile.account_retired`). Não é senha errada: a credencial vai a `review` (o login automático para até uma pessoa
  conferir o identificador guardado), nunca a `invalid`. A sessão fica `auth_required`; o "Conectar" manual continua
  podendo tentar de novo.
- **O motivo** traz o identificador usado **mascarado** (`f***@dominio` para e-mail, primeiro caractere para os demais);
  o texto da tela não vai para o motivo. Os botões do diálogo nunca são tocados.
- **Prova:** `simulated` (`backend/tests/test_conta_nao_encontrada.py`, 12 casos: a hierarquia da captura real com o
  identificador trocado, a máscara e o motor de sessão contra o aparelho falso). `real`: `not_run`. O português não foi
  medido (o texto é palpite da tradução).

## Por que as contas criadas pela API do igfarm morrem (31.333)

Análise de leitura sobre os dados da bifurcação de 10/10/2026 (prova `real` para os fatos; as hipóteses não são
conclusão). Contas só por A, B e C: os valores crus ficam no JSON de referência, fora do Git.

- **O que se viu.** A conta B nasceu às 18:40Z e foi tocada duas vezes pelo app: com +18 min abriu um modal vazio (a busca da
  CAA estourou o prazo) e com +56 min o login disse "Can't find account" para o e-mail guardado. O dono informou depois que o
  perfil sumiu. A conta A (+22 min) ainda existia e caiu na tela humana. Há um único ponto de sobrevivência de B: n=1 não dá taxa.
- **O furo da medição.** O IP da sessão sticky de B era o da criação até as 18:48Z e girou antes das 19:13Z; o primeiro login
  (18:58Z) caiu num intervalo sem medição. O 31.329 fecha esse furo daqui para frente.
- **Hipóteses, da mais plausível à menos:** (1) o cadastro nunca completou (sem e-mail confirmado) e o próprio Instagram limpou
  a conta; (2) a conta foi criada e derrubada pelo antiabuso (A e B seriam dois estágios); (3) o login pela API do igfarm
  (que devolve 429 em conta nova) queimou a conta antes de o app tocar nela; (4) a rotação do sticky agrava, mas não explica
  sozinha (A manteve o IP e também caiu).
- **O que só o igfarm responde:** qual e-mail e telefone o cadastro submeteu e se a confirmação do e-mail concluiu; o
  `account_info` da conta hoje; quantos logins pela API foram tentados antes do registro e de que IPs; se o IP de criação
  era compartilhado.
- **A visão que a ponte passa a ter.** `GET /api/instagram/contas/{id}/ciclo` (adendo v1.145) devolve criada, registrada,
  cada contato com o app (minutos desde a criação e desfecho, inclusive `conta_nao_encontrada`) e a retirada. Não guarda nada
  novo: junta o que `contas_igfarm`, `authentication_attempts` e a lápide já tinham.
- **Protocolo da próxima conta** (aprovado pela orquestradora; só roda com conta e sessão entregues pelo dono/igfarm e
  depois do deploy 73): `account_info` do igfarm a T+5/15/30/60/120 min sem login; assuntos da caixa de e-mail; UM login pelo
  usuário (antes do e-mail), com a janela de 30 s do 31.329; só observação. Com N≥3 contas por rodada o desfecho ganha taxa.

## A criação de conta pela API do igfarm foi aposentada; o cadastro é no app (31.335)

- **Decisão do dono (10/10/2026).** A API CAA do igfarm cria a conta com `is_active:false` e o login por ela para em 2FA sem
  contexto; as contas nascidas por ali morrem (31.333). O cadastro passa a ser NO APP (o cadastro guiado, 31.324) e o igfarm vira
  apoio: e-mail, código, SMS e proxy.
- **O que mudou.** A flag `contas.criacao_pela_api_do_igfarm` (padrão `false`) desliga o caminho que ENTREGA personas para o
  igfarm criar por API: `GET /api/instagram/personas-pendentes` responde `409 criacao_pela_api_aposentada` e não reserva,
  não sugere e não gera foto paga. Nada foi apagado: `true` reabre o caminho como era.
- **O que fica como apoio.** O registro (`POST /api/instagram/contas`), o consentimento da credencial, o código do e-mail, o
  ciclo da conta (`/ciclo`, 31.333) e o proxy por conta (`igfarm-{account_id}`, 31.317/31.329) seguem valendo para a conta que
  já foi criada e para a que o app criar com apoio do igfarm.

## O proxy sticky da conta planejada e o egresso na janela do cadastro (31.337)

- **Por quê.** No cadastro no app (decisão do dono, 10/10/2026) o IP tem de estar no aparelho ANTES do primeiro toque em "Create new
  account", e o IP de criação é o primeiro medido dentro da janela do cadastro. O perfil `igfarm-<conta>` deixa de nascer no registro do
  igfarm (conta já criada pela API) e passa a nascer no planejamento.
- **O planejamento.** `POST …/accounts/{id}/proxy` recebe o `proxy_url` que o igfarm entregou, cria o perfil sem `egress_esperado` e o
  atribui aos aparelhos da persona. Idempotente; só antes do cadastro.
- **A janela.** O cadastro guiado mede a saída do aparelho (`ConvergenciaDeRede.medir_para_o_cadastro`) antes de abrir o app:
  primeira medição com IPv4 público fixa o `egress_esperado` (com rastro); as seguintes comparam, como no login (31.329). Não casou:
  parada `egresso_nao_casou`, nada tocado. Medição sem IP nunca conta como "casou".
- **O que falta para a prova real.** Uma sessão sticky nova por conta, entregue pelo igfarm SEM criar a conta (pergunta 6 do cartão
  COG65yCO), o `cadastro.yaml` do Instagram declarado da captura (P-050) e o sim do dono para criar uma conta real. Ciclo de vida do
  perfil (a sessão do IPRoyal gira): criar, usar e aposentar ainda não está desenhado além do perfil por conta.

## O ciclo da conta criada no app (31.341)

`GET /api/instagram/contas/{id}/ciclo` (adendo v1.149) deixou de depender de `contas_igfarm`: para a conta planejada e cadastrada no app
ele junta a linha da conta, as tentativas de login, os eventos do cadastro guiado e a retirada. `criada_em` é a confirmação do
cadastro; até lá os minutos contam do planejamento (`referencia: planejamento`). É a visão para medir "criada → 1º login → desfecho" das
contas do caminho principal (31.334/31.337) no mesmo formato das do igfarm, sem @ nem e-mail.

## Roteiro real do 31.337: a próxima conta, com proxy sticky planejado e egresso na janela

Procedimento para a PRIMEIRA prova `real` do perfil por conta planejada. Nada abaixo foi executado: prova `not_run`. Cada passo que toca
conta real ou aparelho só roda com o sim do dono em chat; este roteiro não autoriza nada por si.

**Condições para começar (todas):**
1. P-049 e P-050 respondidas pelo dono (conta e sessão entregues; captura do cadastro feita) e o `cadastro.yaml` do Instagram declarado
   a partir dela (31.334). Sem ele, `POST …/provisioning/signup` devolve `sem_conhecimento_de_cadastro`.
2. A resposta do igfarm à pergunta 6 (cartão COG65yCO) diz SIM: entrega uma sessão sticky NOVA por conta, sem criar a conta.
3. A caixa da conta existe (linha em `caixas_email`): hoje só nasce no registro do igfarm; o cadastro guiado devolve
   `409 sem_caixa_de_email` sem ela. A lacuna L1 do desenho do 31.334 (caixa da conta planejada sem o igfarm) fecha antes.
4. Deploy com o 31.337 no ar (corte 75 em diante), host sem suíte nem funil rodando e um aparelho online, livre e sem conta real de
   outra persona. Painel fechado durante as medições.

**Roteiro (uma conta, uma tentativa por sessão sticky):**
1. Escolher persona e aparelho; conferir `ai_calls` antes (a conta nasce pelo motor declarado, sem IA: esperado 0 chamadas novas).
2. Planejar: `POST /api/instagram/profiles/{id}/accounts/planned` com `app_id` e `desired_handle`; preparar a senha com consentimento:
   `POST …/accounts/{aid}/credential/prepare` (`consent: true`, `modo: gerar`). A senha nunca aparece em resposta, log nem evento.
3. Proxy: `POST …/accounts/{aid}/proxy` com o `proxy_url` que o igfarm entregou. Esperado: `200`, `egress_esperado: null`, um item de
   `egresso` por aparelho da persona (`atribuido`; `pendente_confirmacao` pede confirmação da conta real do aparelho).
4. Conferir a rede SEM fixar o IP: `POST /api/network/devices/{id}/apply` se faltar aplicar, depois `…/verify`. Medição com IP de saída
   e tráfego do app pelo proxy; sem IP, parar aqui (sessão morta ou DNS mudo) e pedir outra sessão ao igfarm.
5. Disparar `POST …/accounts/{aid}/provisioning/signup`. O primeiro passo do comando é a medição na janela: evento
   `session.egresso_na_janela` com `fase: cadastro`, o IP fixado como `egress_esperado` (e `network.updated` com o rastro). Anotar
   `medicao_id`, hora e IP (valores crus só no JSON de prova local, nunca no cartão).
6. Acompanhar `identity.cadastro`: `confirmada` (conta lida pela sessão e @ igual ao desejado) ou `parada` com o código fechado.
   `egresso_nao_casou`: nada foi tocado; NÃO repetir com a mesma sessão (a sticky gira); pedir outra ao igfarm.
7. Qualquer tela humana ("Confirm you're human", CAPTCHA) é bloqueio definitivo: não resolver, não tocar, rotular `bloqueada` e
   registrar o motivo (31.322).

**Evidência para fechar como `real`:** data, máquina, commit, ids de comando, `medicao_id` e o desfecho da conta; `ai_calls` antes e
depois; o JSON de prova fora do Git. Limite do que ela prova: n=1 (uma conta), e a taxa de bloqueio depende de N≥3 por rodada.

**Lacuna conhecida do acompanhamento:** `GET /api/instagram/contas/{id}/ciclo` (31.333) só conhece conta registrada pelo igfarm
(`contas_igfarm`); a conta criada no app não tem essa linha, então o intervalo "criada → 1º login → desfecho" dela só se lê hoje pelas
`authentication_attempts` e pelos eventos. Estender o `/ciclo` à conta planejada é o próximo ganho pequeno.

## A criação pelo igfarm foi reaberta e o consentimento da conta ganhou rota (31.342)

- **Decisão do dono (11/10/2026), que reverte a do 31.335.** Quem cria a conta é o igfarm, pela API dele; o android hospeda e o app usa.
  `contas.criacao_pela_api_do_igfarm` volta a `true` por padrão e `GET /api/instagram/personas-pendentes` volta a entregar as pessoas
  sem conta. `false` fica como chave para desligar (409 `criacao_pela_api_aposentada`).
- **Consentimento.** `POST /api/instagram/contas/{conta_id}/consentimento` (adendo v1.150) consente a credencial da conta que o igfarm
  criou, pelo `account_id` ou pelo `igfarm_account_id`; só conta da ponte; idempotente; sem senha vira 409. É o que o igfarm tentava em
  `POST /api/credential/consent`, rota que nunca existiu.
- **O que segue.** O caminho do app (cadastro guiado 31.324, proxy 31.337, ciclo 31.341) continua para a conta que o app criar; os dois
  caminhos convivem.

## Rastro de cada chamada ao consentimento (31.344)

Cada `POST /api/instagram/contas/{id}/consentimento` emite `identity.consentimento_igfarm` (consentida, já consentida ou recusada, com ids
e código; sem segredo; adendo v1.151). É a prova observável de que o igfarm chegou à rota, inclusive quando a chamada é idempotente.
