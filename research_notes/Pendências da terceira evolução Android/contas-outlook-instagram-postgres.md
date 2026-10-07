# Pendências da terceira evolução: contas Outlook e consentimento, cenário Outlook → Instagram, validação em PostgreSQL

Notas de pesquisa de 2026-09-30. Fontes internas citadas por caminho e linha no checkout `C:\git\android` (commit
`6997091`, o mesmo que o ambiente central informa em `GET /api/health`). Leituras da API foram só `GET`, em
`http://127.0.0.1:8000`, com todo endereço de e-mail mascarado antes de sair; nenhum segredo foi lido ou impresso.
Nada foi executado no parque (sem adb, sem deploy, sem Docker/WSL, sem chamada paga de IA).

Convenção: **FATO** = lido no código, na API ou numa página oficial datada; **INFERÊNCIA** = conclusão minha, com o
grau de confiança; **LACUNA** = o que não foi possível confirmar.

---

## 1. Contas Outlook das personas: estado por conta e o que ainda falta (por tipo de pendência)

### Takeaway

As três personas vivas (André, Bruno, Lucas) têm conta Outlook cadastrada com senha clonada no cofre, mas **sem
consentimento** e **sem nenhuma prova de que a conta Microsoft existe ou autentica**; o Outlook nem foi inspecionado nos
aparelhos (P15). As 5 personas bloqueadas têm e-mail `outlook.com` no cadastro, mas nenhuma conta Outlook, nenhum
aparelho e Instagram bloqueado; as 6 sem e-mail não têm endereço nenhum. O único passo que a plataforma pode dar sozinha
hoje é "inspecionar app"; depois do P15, o caminho do Outlook é o LIVRE (a IA opera o app e a senha entra por
`type_secret` no campo de senha), porque o app não tem `sessao.yaml` e, portanto, não tem "Conectar" gerenciado — e esse
caminho só existe com o consentimento. Consentimento (P5), endereço (ADR-057 §5) (ADR-009) e a decisão sobre as
bloqueadas (P10) são da pessoa.

### Cited Findings

**Estado lido na API (2026-09-30, `GET`, e-mails mascarados)**

- `GET /api/health`: `commit 6997091…`, `migration 058_chaves_de_rede`, um problema: `ai_balance_low` — "Anthropic
  (Claude Console): saldo estimado US$ 2,46, abaixo do aviso (US$ 3,00)" — [GET /api/health, 30/09](http://127.0.0.1:8000/api/health).
- `GET /api/personas` devolve 14 personas. Por status e domínio do e-mail cadastrado:
  - **3 ativas com aparelho e e-mail `outlook.com`**: André Carvalho (`ig-KW1uWMsISqStNXbU`, android-06), Bruno
    Ferreira (`ig-z7SD2h4FBYKTatLC`, android-03), Lucas Almeida (`ig-CVG2z6c0Dv9dBrsY`, android-01);
  - **5 `blocked`, todas com e-mail `outlook.com` e sem aparelho**: Beatriz Rocha (`ig-xrUqDegJ0ThxbGi9`), Felipe
    Nogueira (`ig-z2bdNiBCIZnjtcWq`), Juliana Mendes (`ig-29Rsf0VXCcX_I6lr`), Mariana Costa (`ig-dd-51aILosDiny6d`),
    Thiago Moreira (`ig-d0OqTRPSIwIGEIMz`);
  - **6 ativas sem e-mail e sem aparelho** (ids `ig-persona-…`): Camila Duarte, Diego Salles, Helena Barbosa,
    Larissa Fontes, Marcos Vinícius Leal, Rafael Pires — [GET /api/personas, 30/09](http://127.0.0.1:8000/api/personas).
- `GET /api/instagram/profiles/{pid}/accounts` nas três ativas: cada uma tem exatamente duas contas, `app_id:
  instagram` e `app_id: outlook`, ambas com `host: null`. A conta **Outlook** das três: `credential.configured:
  true`, `credential.updated_at: 2026-09-29T22:26:01Z` (o clone do 23.9), `consent_at: null`, `consent_by: null`,
  `failed_attempts: 0`, `last_used_at: null`, `session_status: unknown`, `session_actions.phase: app_unknown`,
  detalhe "Não se sabe se o Outlook está instalado em android-0N: o aparelho nunca foi inspecionado. Verifique o app
  antes de conectar."; `connect`, `verify` e `logout` não permitidos; só `inspect_app` permitido —
  [GET …/accounts (André, Bruno, Lucas), 30/09](http://127.0.0.1:8000/api/instagram/profiles/ig-KW1uWMsISqStNXbU/accounts).
- Conta **Instagram** das três: André `session_ready`/`authenticated` (com `failed_attempts: 2`); Bruno
  `session_ready`/`authenticated`; Lucas `session_status: unknown`, `phase: app_missing` ("Instagram não está instalado
  em android-01 (missing)"), `consent_by: "migração 049"` nas três — mesma fonte.
- Rede (`GET /api/network/devices`, 30/09): android-03 (Bruno) e android-06 (André) com `vpn_profile_id:
  vpn-central-wireguard`, `policy: exigida_com_bloqueio`, `state: trafego_verificado` (`verified_at`
  2026-09-30T08:01Z e 07:32Z), `egress_ipv4` compartilhado entre os dois e com android-02/05; android-05 `parcial`
  ("o bloqueio fora da VPN não foi provado", `pending: verificar`); android-01 (Lucas) **sem linha** em
  `device_network` (`network: null`, só `legacy_proxy` aplicado) — [GET /api/network/devices](http://127.0.0.1:8000/api/network/devices).

**Como o código trata credencial clonada, consentimento e login gerenciado**

- `SecretStore.clonar(ref, para=)` copia o valor de uma referência para uma entrada PRÓPRIA (nonce novo, AAD da
  referência nova) e devolve só a referência; o valor "vive só neste quadro, entre decifrar e cifrar de novo" —
  [backend/app/security/secret_store.py:320-338](C:/git/android/backend/app/security/secret_store.py).
- `SocialService.clone_account_credential` grava com `consent_by=None`: "a conta sem consentimento continua sem (e o
  `type_secret` e o provedor não a usam até a pessoa autorizar)"; a mensagem oficial do serviço diz que o consentimento
  "é desta conta, e a conta nova nasce sem ele… autorize-a pela rota de consentimento (…/credential/consent)" —
  [backend/app/social/service.py:1394-1456](C:/git/android/backend/app/social/service.py).
- `consent_account_credential` marca o consentimento numa senha já guardada sem redigitá-la; sem senha guardada →
  409 `no_credential` — [service.py:1384-1388](C:/git/android/backend/app/social/service.py). Criar conta com
  `clonar_de` **e** `consent` ao mesmo tempo é recusado com 422 `consentimento_nao_clonado` (conferido antes de criar) —
  [service.py:1292-1298](C:/git/android/backend/app/social/service.py).
- Rotas: `POST …/accounts/{aid}/credential/clone` (api.py:989), `…/credential/consent` (api.py:1001),
  `…/session/connect` (api.py:1010, 202) e `…/session/verify` (api.py:1020, 202). `_start_session_job` recusa, nesta
  ordem: conta com `host` → 409 `conta_de_site`; sem senha → 409 `no_credential`; senha sem consentimento → 409
  `consentimento_de_credencial` ("marque-o na conta antes de conectar"); depois o portão de sessão —
  [backend/app/api.py:1168-1182](C:/git/android/backend/app/api.py).
- O Outlook é dado (ADR-052): a pasta `backend/app/conhecimento/apps/com.microsoft.office.outlook/` contém **só
  `app.yaml`** (`precisa_de_perfil: true`, `precisa_de_internet: true`, `ancora_do_perfil: false`); o próprio arquivo
  diz que "sem `sessao.yaml`… a IA opera o app, e a senha da conta Outlook da persona, se consentida, só passa pelo
  canal sensível (`type_secret`)"; o login gerenciado "entra com o 23.8" —
  [app.yaml](C:/git/android/backend/app/conhecimento/apps/com.microsoft.office.outlook/app.yaml).
- Consequência no catálogo de dados: a senha de uma conta só é oferecida ao `type_secret` quando "existe, tem
  consentimento e o app NÃO tem login gerenciado" (`c.has_credential and c.consent_at and not c.managed`), com o nome
  `conta_<app>[_<host>]_senha` — [backend/app/modules/identity/domain/available_data.py:115-117 e 148-166](C:/git/android/backend/app/modules/identity/domain/available_data.py).
- ADR-057 (29/09): a credencial clonada é "só entre contas da mesma persona; o consentimento nunca é clonado (o dono dá
  o de cada conta Outlook)"; "o endereço de cada conta Outlook vem do dado conferido pelo dono; nunca é derivado do
  usuário do Instagram.
- Handoff: 23.11 "parcial: contas Outlook de André, Bruno e Lucas criadas, falta o consentimento (P5) e o vínculo com
  aparelho (P15)"; 23.9 `real` (senha clonada nas 3 contas) — [docs/handoffs/terceira-evolucao.md:32](C:/git/android/docs/handoffs/terceira-evolucao.md).
- P15 (29/09, prova `real`): o Outlook 5.2635.3 derruba o emulador 37.1.11/37.2.11 (`0xc0000005`, 9 quedas) e, no
  canary 37.3.2, o próprio app morre em armadilha proposital `UD2` em `libhxcomm.so` (thread `Hx-Storage`); "o app
  recusa operar neste ambiente emulado"; saídas: (a) celular físico por USB, (b) Outlook web no Chrome, (c) esperar
  versão nova — [terceira-evolucao.md:148](C:/git/android/docs/handoffs/terceira-evolucao.md); K-062 —
  [aprendizados.md:1449-1461](C:/git/android/docs/conhecimento/aprendizados.md).
- Decisões pendentes registradas no handoff (não são defeitos): P5 (consentimento por conta Outlook, no painel Persona
  › Contas), P6 (confirmar o propósito citado no ADR-056), P10 (personas bloqueadas e lucas sem Instagram no
  android-01), P12 (troca das senhas das 3 contas vivas; "depois do clone as senhas ficam independentes") —
  [terceira-evolucao.md:138-145](C:/git/android/docs/handoffs/terceira-evolucao.md).

### Inferences

**Mapa por persona (o que falta, por TIPO de pendência)** — confiança alta no estado, média nas consequências:

| Persona | Aparelho | Conta Outlook na plataforma | Falha técnica | Falta endereço | Falta consentimento | Depende da pessoa (ADR-009) |
|---|---|---|---|---|---|---|
| André (ativa) | android-06, rede `trafego_verificado`, Instagram `session_ready` | sim; senha clonada 29/09; `consent_at: null`; app nunca inspecionado | **P15** (Outlook não roda no emulador) | não (e-mail `outlook.com` no cadastro; existência da conta Microsoft **não provada**) | **P5** | novo dispositivo/passkey no 1º login; P12 (senha) |
| Bruno (ativa) | android-03, idem | idem | P15 | idem | P5 | idem |
| Lucas (ativa) | android-01, **sem `device_network`**, Instagram `app_missing` | idem | P15 **e** Instagram ausente (P10) e rede não configurada | idem | P5 | idem + P10 |
| Beatriz, Felipe, Juliana, Mariana, Thiago (`blocked`) | nenhum | **não existe** conta Outlook em `profile_accounts` (só o e-mail no cadastro da persona) | sem aparelho; Instagram bloqueado | não (há e-mail `outlook.com`), mas nada prova que a caixa existe | não se aplica ainda (não há senha guardada) | **P10**: decidir se continuam; sem Instagram vivo, o comando entre apps não tem destino |
| Camila, Diego, Helena, Larissa, Marcos, Rafael (ativas, `ig-persona-…`) | nenhum | não existe | sem aparelho e sem conta Instagram | **sim** (ADR-057 §5: o endereço vem do dono, nunca derivado) | não se aplica | criar/informar a conta é da pessoa |

- Passos que a plataforma pode dar sem decisão nova, por conta ativa (ordem que o código impõe):
  1. `inspect_app` (única ação permitida hoje) — só muda o `phase` de `app_unknown` para `app_missing`/instalado;
     com o P15, instalar o Outlook no aparelho derruba o emulador, então o passo útil só existe depois da saída (a),
     (b) ou (c) do P15.
  2. Consentimento: `POST …/credential/consent` (ou o painel, P5) — decisão da pessoa; sem ela o `connect` responde 409
     `consentimento_de_credencial` e a senha nem aparece no catálogo do `type_secret`.
  3. Login: como o Outlook não tem `sessao.yaml` (sem `SessionProvider`), **não há `connect` gerenciado** para ele
     hoje: a rota existe, mas o provedor de sessão do app não; o caminho é o "livre" (a IA opera o app e digita a
     senha por `type_secret` no campo de senha, só no pacote `com.microsoft.office.outlook`). Confiança média: não
     executei o `connect` para ver qual erro sai sem provedor.
  4. Prova de existência da conta Microsoft: **só o login real prova**; nenhuma tabela ou rota guarda "a conta existe".
- A pendência P12 muda de natureza depois do clone: trocar a senha do Instagram não altera a do Outlook (entradas
  independentes no cofre), então a troca precisa ser feita conta a conta e regravada por conta (com consentimento
  preservado pelo `COALESCE(excluded.consent_at, consent_at)` descrito em persona.md).
- As 5 bloqueadas e as 6 sem e-mail **não devem ser tratadas como contas utilizáveis**: nas bloqueadas, a plataforma
  só tem o e-mail no cadastro (nenhuma linha de conta Outlook, nenhuma senha); nas sem e-mail, nem isso.

### Gaps

- Não há prova em lugar nenhum (código, banco via API, docs) de que as caixas `outlook.com` das 8 personas com e-mail
  existem e aceitam a senha clonada: `last_used_at: null`, `session_status: unknown` nas três ativas. Só um login
  real, depois do P15 e do P5, responde.
- Não confirmei o comportamento de `POST …/session/connect` para um app sem `SessionProvider` (Outlook): a rota é
  genérica por conta, mas o provedor é por app; ficou fora do escopo (nada de POST).
- O painel (Persona › Contas) para o consentimento: o handoff diz "no painel"; não conferi se o botão já existe no
  frontend (fora do escopo desta nota).

---

## 2. Caminhos de autenticação de uma conta Microsoft pessoal (app Outlook, Outlook web, Graph) e o que fica com a pessoa

### Takeaway

Uma conta Microsoft pessoal tem três caminhos: o app nativo (formulário e-mail → Continuar → senha → MFA se houver;
para conta pessoal o MSAL **não** usa o broker do Authenticator/Company Portal), o Outlook na web
(outlook.live.com, mesmo usuário e senha; o host e a sequência exata das telas não estão documentados oficialmente) e
o Microsoft Graph (`Mail.Read` delegado existe para conta pessoal, com registro de app `PersonalMicrosoftAccount` ou
`AzureADandPersonalMicrosoftAccount`, sempre com consentimento do usuário; sem client credentials para caixas MSA).
Em qualquer caminho, o "novo dispositivo/local" pode exigir código enviado aos contatos de recuperação, e desde maio
de 2025 o login "passwordless-preferred" pede cadastro de passkey depois de entrar — a senha continua válida em
contas existentes, e não há fonte de 2026 dizendo que será retirada delas. Tudo isso fica com a pessoa (ADR-009).
Pesquisa web feita por subagente em 30/09/2026, só páginas primárias da Microsoft; onde a página não mostra data,
registrei "sem data exibida".

### Cited Findings

**App Outlook para Android com conta pessoal; broker MSAL/OneAuth**

- Fluxo oficial: "Select Add Account… enter your email address and follow the prompts to authenticate the account…
  Outlook may detect and pre-select your email account. Tap Continue… enter your password and follow the prompts…
  If multi-factor authentication is enabled for your email account, follow the instructions to verify the account". O
  Microsoft Authenticator só é citado para conta corporativa/escolar; o Intune Company Portal só para dispositivo
  gerenciado — [Set up email in the Outlook for Android app, support.microsoft.com (sem data exibida)](https://support.microsoft.com/en-us/outlook/install-mobile/set-up-email-in-the-outlook-for-android-app).
- "The Microsoft Authentication Broker is a component that's included in the Microsoft Authenticator, Intune Company
  Portal and Link to Windows apps"; "If there's no policy requirement, or the user is signing in with Microsoft
  account, then Broker app installation isn't required" — [MSAL Android: single sign-on, Microsoft Learn (ms.date 2025-04-10; atualizado 2025-06-04)](https://learn.microsoft.com/en-us/entra/msal/android/single-sign-on).
- "If you're using the Microsoft Entra Authority with Audience set to `MicrosoftPersonalAccount`, the broker won't be
  used"; audiências: `AzureADandPersonalMicrosoftAccount` → `login.microsoftonline.com/common`;
  `PersonalMicrosoftAccount` → `login.microsoftonline.com/consumers` ("Only Microsoft accounts can use this
  endpoint") — [MSAL configuration, Microsoft Learn (ms.date 2023-09-12; atualizado 2025-05-23)](https://learn.microsoft.com/en-us/entra/identity-platform/msal-configuration).
- MSAL Android suporta "Microsoft Accounts (Outlook.com, hotmail.com, and several others)" —
  [MSAL for Android, Microsoft Learn](https://learn.microsoft.com/en-us/entra/msal/android/).

**Verificação em "novo dispositivo" / "unusual sign-in"**

- "When we notice a sign-in attempt from a new location or device, we help protect the account by sending you an
  email message and an SMS alert… we may need you to provide a security code from one of these contacts… We may have
  blocked your sign-in if you're using a new device, if you installed a new app, or if you're traveling or in any new
  location"; dispositivo confiável: "you can sign in from that device and get back into your account" —
  [What happens if there's an unusual sign-in to your account, support.microsoft.com (sem data exibida)](https://support.microsoft.com/en-us/accounts-billing/security/what-happens-if-there-s-an-unusual-sign-in-to-your-account).
- Sem acesso à informação de segurança: "Submit your request via the account recovery form"; ao substituí-la, "you
  must wait 30 days before being able to sign in"; "Our support agents are not allowed to send verification codes" —
  [Troubleshoot Microsoft verification code issues, support.microsoft.com (sem data exibida)](https://support.microsoft.com/en-us/accounts-billing/manage/troubleshoot-microsoft-verification-code-issues).
- "We'll send a code to the email addresses listed on your account, and when you respond with the code, we know it's
  really you"; a página fala em "phasing out SMS as a method of authentication" —
  [Microsoft account security info & verification codes, support.microsoft.com (sem data exibida)](https://support.microsoft.com/en-us/account-billing/microsoft-account-security-info-verification-codes-bf2505ca-cae5-c5b4-77d1-69d3343a5452).
- Páginas relacionadas (encontradas, não lidas na íntegra): [formulário de recuperação](https://support.microsoft.com/en-us/accounts-billing/manage/help-with-the-microsoft-account-recovery-form);
  [dispositivo confiável](https://support.microsoft.com/en-us/accounts-billing/manage/add-a-trusted-device-to-your-microsoft-account).

**Passkeys / passwordless**

- 1º/05/2025: "Brand new Microsoft accounts will now be 'passwordless by default'"; contas existentes recebem
  "passwordless-preferred sign-in" (o método mais seguro disponível vira padrão); "After you're signed in, you'll be
  prompted to enroll a passkey, and the next time you sign in, you'll be prompted to sign in with your passkey"; quem
  tem senha pode continuar usando e pode removê-la nas configurações —
  [Pushing passkeys forward, Microsoft Security Blog (2025-05-01)](https://www.microsoft.com/en-us/security/blog/2025/05/01/pushing-passkeys-forward-microsofts-latest-updates-for-simpler-safer-sign-ins/).
- 07/05/2026 (World Passkey Day): "hundreds of millions of users sign in with passkeys every day" nos serviços de
  consumidor; "Passkey-preferred authentication in Microsoft Entra ID (preview)"; "Starting in January 2027, security
  questions will be removed as a password reset option in Microsoft Entra ID". **Nada** sobre forçar passkey ou
  remover a senha de contas Microsoft pessoais existentes —
  [World Passkey Day, Microsoft Security Blog (2026-05-07)](https://www.microsoft.com/en-us/security/blog/2026/05/07/world-passkey-day-advancing-passwordless-authentication/).
- 13/07/2026 (só título): "Passkeys are the default authentication method in Entra ID" — escopo Entra ID
  (corporativo), não MSA — [Microsoft Security Blog (2026-07-13)](https://www.microsoft.com/en-us/security/blog/2026/07/13/microsoft-entra-id-security-updates-passkeys-are-the-default-authentication-method-in-entra-id/).

**Microsoft Graph com conta pessoal**

- `List messages`: "Delegated (personal Microsoft account) | Mail.ReadBasic | Mail.ReadWrite, Mail.Read" (ou seja,
  `Mail.Read` delegado vale para conta pessoal) — [user: list messages, Microsoft Learn (ms.date 2024-06-21; atualizado 2026-06-19)](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0).
- "Microsoft Graph lets your app get authorized access to a user's Outlook mail data in a personal or organization
  account" — [Mail API overview, Microsoft Learn (atualizado 2026-09-02)](https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview?view=graph-rest-1.0).
- Tipos de conta do registro: "Accounts in any organizational directory… and personal Microsoft accounts" =
  `AzureADandPersonalMicrosoftAccount`; "Personal Microsoft accounts only" = `PersonalMicrosoftAccount`; restrições:
  "National clouds: Not supported", "Maximum of two client secrets", appRoles "not supported for consumer (MSA)
  users" — [Validation differences by supported account types, Microsoft Learn (ms.date 2026-09-25)](https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation).
- Client credentials: as application permissions "are granted to an application by an organization's administrator,
  and can be used only to access data owned by that organization and its employees"; o padrão ACL "is common for
  daemons and service accounts that need to access data owned by consumer users who have personal Microsoft
  accounts" — [OAuth 2.0 client credentials flow, Microsoft Learn (ms.date 2026-01-30; atualizado 2026-06-15)](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow).
- A referência de permissões marca as delegadas disponíveis para consentimento em conta pessoal (entrada `Mail.Read`
  não extraída; página grande) — [Permissions reference, Microsoft Learn (atualizado 2026-09-15)](https://learn.microsoft.com/en-us/graph/permissions-reference).

**Outlook na web (outlook.live.com)**

- "To sign in to Outlook.com or Hotmail, you'll need your Microsoft Account username and password"; o link "Sign in"
  é um fwlink (`linkid=2185828`); a página não cita o host de login nem descreve as telas —
  [How to sign in to Outlook.com, support.microsoft.com (sem data exibida)](https://support.microsoft.com/en-us/accounts-billing/manage/how-to-sign-in-to-outlook-com).
- Páginas de passkey de consumidor tratam de criação e diagnóstico, não do texto da tela de login —
  [Signing in with a passkey](https://support.microsoft.com/en-us/account-billing/signing-in-with-a-passkey-09a49a86-ca47-406c-8acc-ed0e3c852c6d);
  [Troubleshoot signing in with a passkey](https://support.microsoft.com/en-us/accounts-billing/security/troubleshoot-signing-in-with-a-passkey).

**Automação em conta pessoal**

### Inferences

- **Viabilidade por caminho, para a automação baseada em senha** (confiança média-alta):
  - *App nativo*: o formulário é em etapas (e-mail → Continuar → senha), o que o motor 23.6 (formulário em etapas)
    foi feito para aceitar; sem broker para conta pessoal, então não há dependência do Authenticator/Company Portal no
    aparelho. Bloqueado hoje pelo P15 (o app não roda no emulador), não pela autenticação.
  - *Outlook web no Chrome*: mesmo usuário e senha; provavelmente o mesmo formulário em etapas da conta Microsoft
    (inferência, sem página oficial que o descreva). A senha é digitada num host de login da Microsoft, que **não
    confirmei** ser `login.live.com`; isso decide o `host` da conta de site (seção 3.2).
  - *Graph*: é o único caminho sem tela, mas exige registrar um app (audiência com conta pessoal), fluxo delegado
    com consentimento interativo da pessoa a cada conta, e fica FORA do modelo do produto (ADR-040/057: a senha só
    passa pelo `type_secret`, só no app e no site da conta; um token OAuth de leitura de caixa seria uma credencial
    nova que a execução carregaria). Não recomendo para o 24.9; registro como alternativa para decisão do dono.
- Se as contas outlook.com das personas não tiverem contato de recuperação alcançável pelo dono, o primeiro login num
  aparelho novo pode ficar preso sem saída.
- **Separação dos tipos de pendência por conta, à luz da Microsoft**: "falha técnica" = P15; "falta endereço" = 6
  personas sem e-mail; "falta consentimento" = P5 nas 3 ativas; "depende da pessoa" = código de novo dispositivo,
  passkey, recuperação; e o enquadramento do uso no Services Agreement (3.a.v/3.a.vi) é decisão do dono, não da
  plataforma.

### Gaps

- Sem fonte primária sobre a implementação interna do app Outlook (OneAuth/WebView) para conta pessoal; a Microsoft
  não documenta isso.
- Sem fonte primária para o host da UI de login do Outlook.com (`login.live.com` vs `login.microsoftonline.com`) e a
  sequência exata "Enter your email → Next → Enter password", nem para os prompts "Stay signed in?" e "Sign in with a
  passkey" — só relatos comunitários em Microsoft Q&A (não usados como fonte).
- Sem fonte primária, datada de 2026, sobre contas pessoais existentes serem obrigadas a passkey (não encontrada; o
  post de 07/05/2026 não diz isso).
- Sem fonte primária sobre a existência de "Skip" no prompt de passkey pós-login (só Microsoft Q&A).
- A cronologia da retirada de senhas do Microsoft Authenticator (jun–ago/2025) só apareceu em Microsoft Q&A; a página
  de suporte correspondente hoje fala do Password Manager sem linha do tempo. Não afeta o login com senha da conta
  (inferência a partir do post de 2025-05-01).
- Sem fonte primária para o texto da tela de consentimento do Graph mostrada a um usuário MSA.

---

## 3. Cenário controlado para provar o comando Outlook → Instagram (24.9 / 27.2) dentro dos limites, e a variante Outlook web no Chrome

### Takeaway

O cenário mínimo que prova tudo o que o 24.9/27.2 pede é "ler o assunto (ou o remetente) do último e-mail de um
remetente de teste conhecido no Outlook → abrir a busca de perfis do Instagram com esse valor, sem seguir, curtir ou
comentar", em André/android-06 ou Bruno/android-03 (Lucas está fora: Instagram ausente e sem rede). Ele depende do P15
para o app nativo; a variante interina com Outlook web no Chrome cabe no motor atual **só pelo caminho livre**, com a
conta modelada como conta de site (`app_id` do navegador + `host`), que o código já suporta para `type_secret` e já
recusa, de propósito, no login gerenciado.

### Cited Findings

**O que o contrato C2/ADR-058 e o código já garantem**

- O prompt do planejador: "a etapa que lê declara em `saidas` o nome… As seguintes o citam como `{{saida:assunto}}` no
  goal, na pós-condição, no `commit_guard` ou nos parâmetros. Só etapa ANTERIOR entrega valor. senha e token NUNCA são
  saída"; e "senha ou token lido num app NUNCA é usado em outro" — [backend/app/planning/prompts.py:70-76, 173-176,
  223-225, 276](C:/git/android/backend/app/planning/prompts.py).
- O guarda de valores: `saidas.py` classifica e recusa campo de senha, valor que "fala de código ou verificação",
  formato de segredo (`looks_secret`: JWT, chave, base64, "code: 1234", link com trecho aleatório de 32+ caracteres),
  4–8 dígitos num contexto de código/credencial, e palavra com cara de senha —
  [backend/app/taskqueue/saidas.py:226-262](C:/git/android/backend/app/taskqueue/saidas.py).
- Conta esperada por etapa (24.4): "`{account_label}` da etapa… numa etapa que declara app, resolvido com a conta
  daquele app: 'Conta: {account_label}' no Outlook confere a conta do Outlook. Sem conta esperada, o molde fica SEM
  resolver"; "app que exige persona e não tem provedor (o Outlook), num item sem persona definida, também para" —
  [docs/dominios/execution.md:496-500](C:/git/android/docs/dominios/execution.md).
- O `type_secret` tem três travas antes de o valor sair do cofre: só campo de senha; só no pacote da conta; "no
  navegador, só no SITE DA CONTA: o host da barra de endereço tem de ser o `host` da conta (ou subdomínio dele). Conta
  de navegador sem `host` não recebe a senha em site nenhum" — implementado em `_conferir_destino` (pacote em
  primeiro plano ≠ pacote da conta → recusa; `BARRA_DE_ENDERECO = {"com.android.chrome": "com.android.chrome:id/url_bar"}`;
  barra invisível → recusa; host fora → recusa) — [backend/app/taskqueue/executor.py:102, 355-368, 425-447](C:/git/android/backend/app/taskqueue/executor.py).
- Prova real já existente do mecanismo (30/09 ~02:35–02:41Z, android-05, QA Messenger → Chrome, só leitura):
  `r-20260930023442-bd5c5a` (4/4, saída `primeiro_contato` lida no QA e usada na busca do Chrome; título do primeiro
  resultado gravado como segunda saída), `r-20260930023809-12d329` (conta indisponível → `waiting_user`),
  `r-20260930023901-13ec70` (backend reiniciado no meio; retomou 4/4 sem repetir as etapas do QA). "Custo: ~US$ 0,56
  no livro-caixa da Anthropic desde a âncora de 29/09 17:46Z (planejamentos e as três execuções)" —
  [docs/relatorio-validacao.md:1969-1980](C:/git/android/docs/relatorio-validacao.md).
- Os cenários do aceite 27.2 (mesmo aparelho, nesta ordem, prova `real` de cada passo): 1 rede
  `trafego_verificado` com cobertura do Outlook e do Instagram por UID; 2 Outlook instalado, conta com consentimento,
  login pelo canal sensível, caixa de entrada comprovada, fechar/reabrir mantém sessão, Instagram com a conta certa;
  3 "ler o assunto do último e-mail no Outlook e procurar no Instagram o perfil citado; valor lido aparece no relatório
  com a origem"; 4 interrupção (cancelar na troca de app; reiniciar o backend); 5 sem consentimento → espera a pessoa
  sem digitar — [relatorio-validacao.md:1990-2003](C:/git/android/docs/relatorio-validacao.md).
- Ordem de custo: "≈12 chamadas e ≈80 mil tokens de entrada por aparelho por comando" (medição antiga, seção 5) —
  [relatorio-validacao.md:127](C:/git/android/docs/relatorio-validacao.md).
- Portão de rede: "Só `trafego_verificado` libera tarefa com política exigida (ADR-056 §3)" —
  [057_rede_por_aparelho.sql](C:/git/android/backend/migrations/057_rede_por_aparelho.sql). P16: com
  `exigida_com_bloqueio`, o resultado do teste de vazamento vive só em memória; "depois de cada reinício do backend, a
  primeira verificação… reinicia o aparelho" (android-06 e android-03) — [terceira-evolucao.md:149](C:/git/android/docs/handoffs/terceira-evolucao.md).

**Conta de site, `navegador.hosts` e o login gerenciado (para a variante web)**

- `sessao.yaml` → `navegador`: mapa com `pacotes` (obrigatório: pacote do navegador → id da barra) e `hosts` (lista;
  cada um "sem esquema, porta nem caminho", minúsculo) — [backend/app/integrations/app_declarado/conhecimento.py:434-451](C:/git/android/backend/app/integrations/app_declarado/conhecimento.py).
- `_destino`: fora do pacote do app, a tela só é "do app" se o navegador é declarado e o host da barra está em
  `navegador.hosts` (ou subdomínio); "O `host` de uma conta NÃO soma aqui: conta com `host` é de portal no navegador, e
  o login gerenciado a recusa (`_resolver_conta`)" — [sessao.py:451-463](C:/git/android/backend/app/integrations/app_declarado/sessao.py).
- `_resolver_conta`: conta com `host` → `UNCERTAIN` "é de site…, usada pelo navegador; o login gerenciado… é o da
  conta do app, sem site"; motivo: "a porta de sessão e o despacho nunca a acham (`conta_do_pacote` é a do app
  inteiro)" — [sessao.py:528-534](C:/git/android/backend/app/integrations/app_declarado/sessao.py); a rota responde
  409 `conta_de_site` antes de tocar no aparelho — [api.py:1172-1177](C:/git/android/backend/app/api.py).
- O `sessao.yaml` do Instagram **não declara `navegador`** (grep vazio em `backend/app/conhecimento/apps/*/sessao.yaml`);
  só existem as pastas `com.instagram.android` e `com.microsoft.office.outlook` — [ls backend/app/conhecimento/apps/](C:/git/android/backend/app/conhecimento/apps).
- Modelo de conta: `profile_accounts` = (pessoa, app, `host`); unicidade `(profile_id, app_id, COALESCE(host,''))`;
  `host` normalizado por `_host_da_conta` (minúsculo, sem esquema/caminho/porta) —
  [docs/dominios/persona.md:130-137](C:/git/android/docs/dominios/persona.md), [service.py:1752-1760](C:/git/android/backend/app/social/service.py).
  Catálogo: `conta_<app>_<host>_usuario` / `conta_<app>_<host>_senha`, o segundo "só com credencial guardada,
  consentida e de app sem `SessionProvider`" — [persona.md:272-275](C:/git/android/docs/dominios/persona.md).
- Chrome é "por ele que a automação usa os sites (ADR-025)" — [backend/app/devices/apps_de_fundo.py:74](C:/git/android/backend/app/devices/apps_de_fundo.py).

### Inferences

**3.1 Cenário proposto para o app nativo (depois do P15 e do P5)** — confiança alta no que o código exige, média no
texto exato das etapas (o planejador é quem materializa):

- Persona/aparelho: **Bruno/android-03** ou **André/android-06** (rede `trafego_verificado`, Instagram
  `session_ready`). Lucas não serve (Instagram `app_missing`, sem `device_network`). Preferir Bruno: `failed_attempts:
  0` no Instagram (André tem 2).
- Pré-voo (conferir por `GET` antes de disparar, e registrar a resposta como evidência):
  1. `GET /api/network/devices` → aparelho com `effective_state: trafego_verificado`, `verified_at` recente, e
     `required_apps` contendo `com.microsoft.office.outlook` e `com.instagram.android` (hoje só o Instagram aparece);
     atenção ao P16: o primeiro `verify` depois de um deploy reinicia o aparelho.
  2. `GET …/accounts` → Instagram `session_ready` e Outlook `session_ready` (ou, sem provedor, ao menos
     `credential.consent_at` preenchido e `phase` que permita ação); `consent_at` da conta Outlook ≠ `null`.
  4. Saldo: `GET /api/ai/balances`; hoje a Anthropic está em US$ 2,46 (abaixo do aviso); a prova real é chamada
     paga além de validação pontual → autorização do dono, e ideal recarregar antes.
  5. Remetente de teste conhecido: um e-mail enviado ANTES pelo dono (de uma conta dele) para a caixa da persona, com
     assunto que contenha um nome de perfil público, ex.: "perfil: natgeo" — assim o valor lido é inofensivo e o alvo
     no Instagram é público e conhecido. Evitar assunto com dígitos de 4–8 posições ou palavras como "código",
     "verificação" (o guarda de `saidas.py` recusaria de propósito).
- Etapas (só leitura, nada público no Instagram):
  1. Outlook (`app_id: outlook`; `Conta: {account_label}` resolvida com a conta Outlook): abrir a caixa de entrada,
     localizar o e-mail mais recente do remetente de teste, ler o assunto → `read_value` → `saidas: ["assunto"]`
     (`value_kind: text`). Pós-condição: o assunto lido bate com o e-mail de teste; a conta na tela é a esperada.
  2. Instagram (`app_id: instagram`; `Conta: {account_label}` da conta Instagram): abrir a busca, digitar
     `{{saida:assunto}}` (o valor resolvido pelo 24.3 antes da etapa), confirmar que a lista de resultados mostra o
     perfil citado. Pós-condição: resultado visível; **sem** tocar em seguir/curtir/comentar/enviar (o ator só lê; a
     política de efeito não se aplica a nada porque nenhuma ação de efeito é pedida). Opcionalmente uma segunda saída
     (`primeiro_resultado`) para o relatório, como no `r-…-bd5c5a`.
  3. Retomada: repetir com backend reiniciado entre a etapa 1 e a 2 (como `r-…-13ec70`): a etapa 1 não se repete e a
     saída gravada é reaproveitada; e a variante "cancelar na troca de app e criar a sucessora" (27.2 item 4).
  4. Conta indisponível (27.2 item 5, como CRITÉRIO): a mesma execução ANTES de dar o consentimento deve parar sem
     digitar nada. O que está verificado no código para "senha guardada sem consentimento": 409
     `consentimento_de_credencial` em `session/connect` (api.py:1178-1181) e a senha AUSENTE do catálogo do
     `type_secret` (available_data.py:163, `c.consent_at` exigido). O caminho "recusa do catálogo numa etapa livre →
     `waiting_user`" não foi traçado nesta nota (lacuna); `_conta_indisponivel` (execution.md:500) cobre conta
     inexistente ou desativada, que foi o caso do `r-…-12d329`, não o de conta ativa sem consentimento. Custo: o
     planejamento é chamada paga e acontece antes do despacho — a prova negativa custa pelo menos um planejamento.
- Evidência a registrar (`real`): data/hora, máquina, commit, ids `r-…`, `step_outputs` da execução (`GET` do
  relatório com origem: nome, valor, `app_id`, etapa), estado da rede antes/depois, `session_actions` das duas contas,
  capturas das duas telas, e o custo no livro-caixa (`/api/ai/balances` antes e depois). Nunca o e-mail completo nem
  senha; o assunto é inofensivo por construção.
- Custo esperado (inferência a partir das medições existentes): a prova de 30/09 gastou ~US$ 0,56 para 3 execuções
  + planejamentos com 2 apps; este cenário tem o mesmo desenho (2 apps, 2–4 etapas) → **~US$ 0,20–0,40 por execução,
  ~US$ 1,0–1,5 para o conjunto (3 execuções + a negativa)**. Com US$ 2,46 de saldo, cabe uma rodada, sem folga para
  repetir; recomendar recarga antes.

**3.2 Variante interina com Outlook web (outlook.live.com) no Chrome** — confiança média (o código suporta o modelo;
a operação real do site não foi observada):

- O que muda: a etapa 1 passa a ser `app_id: chrome` (pacote `com.android.chrome`), goal "abrir outlook.live.com e
  ler o assunto do último e-mail de <remetente>"; `read_value` na página; `saidas: ["assunto"]`. A etapa 2 fica igual.
- Apps conferidos por `GET /api/apps` (30/09): `chrome` → `com.android.chrome`, `outlook` →
  `com.microsoft.office.outlook`, `instagram` → `com.instagram.android`; logo o nome no catálogo seria mesmo
  `conta_chrome_<host-em-slug>_senha`, e `_conferir_destino` compararia com `com.android.chrome`. `add_account` só
  exige que o app exista (`_check_app`), que não haja conta duplicada em `(app, host)` e que `clonar_de` venha sem
  `password` (service.py:1283-1296).
- Como modelar a conta: **conta de site** — `POST …/accounts` com `app_id: chrome`, `host: outlook.live.com` (ou
  `live.com`, se a barra mostrar `login.live.com` na hora de digitar a senha: a trava compara host == ou subdomínio),
  `clonar_de: <conta Outlook>` (ou da conta Instagram) e, DEPOIS, `…/credential/consent`. O catálogo então oferece
  `conta_chrome_outlook_live_com_senha` (sigiloso) e `…_usuario` ao `type_secret`, porque o Chrome não tem
  `SessionProvider` (`managed=False`). Isso é o caminho **livre**: sem `session/connect` (a rota responde 409
  `conta_de_site` por desenho, api.py:1172-1177), sem `session_ready` para essa conta, sem tela de login gerenciada.
  O pré-voo "ambas as contas `session_ready`" vira "Instagram `session_ready` + conta de site com `consent_at` e a
  sessão do site provada por uma etapa de observação".
- Por que `app_id: chrome` e não `app_id: outlook` + `host`: a senha só é oferecida a app "sem SessionProvider"; hoje
  o Outlook também não tem, mas passaria a ter com o 23.8 (`sessao.yaml`), e a conta de site do Outlook deixaria de
  ser oferecida ao `type_secret` de um dia para o outro; além disso `_conferir_destino` exige que o pacote em primeiro
  plano seja o pacote da conta (`com.microsoft.office.outlook` ≠ `com.android.chrome`). Com `app_id: chrome` as duas
  travas casam: pacote `com.android.chrome` e host da `url_bar`.
- O host de login: a senha da conta Microsoft é digitada num host de login da Microsoft, e **não** em
  `outlook.live.com` — a seção 2 não achou página oficial que fixe qual (relatos comunitários dizem
  `login.live.com`; o endpoint OAuth documentado é `login.microsoftonline.com/consumers`). Com a trava "host ==
  `host` da conta ou subdomínio dele", uma conta com `host: outlook.live.com` **não** recebe a senha num host de login
  diferente. Passo prévio obrigatório: uma observação real (23.7, "sem digitar") no Chrome do aparelho para ler o host
  da `url_bar` na tela de senha; só então escolher entre `host: live.com` (cobre `login.live.com` e
  `outlook.live.com`, mais largo), `host: microsoftonline.com` (se for esse) ou duas contas de site (uma por host,
  `clonar_de` entre elas, o que espalha consentimento). A decisão é de desenho e do dono, por tocar em onde a senha
  pode ser digitada.
- Custo: igual ao nativo (mesmo número de etapas); talvez maior por hierarquia web mais longa.

### Gaps

- O texto literal das etapas e a aceitação da pós-condição são do planejador; sem uma execução (paga) não há como
  fixar o número de chamadas — a estimativa vem da prova de 30/09.
- Não verifiquei se o `read_value` na página web do Outlook (Chrome, hierarquia de WebView) devolve o assunto como
  elemento de texto legível; a prova de Chrome de 30/09 leu o título de um resultado de busca, não um webmail.
- Não verifiquei o comportamento do login do site (host onde a senha é digitada; "Stay signed in?"; opção de passkey)
  no aparelho — depende da seção 2 e de uma observação real futura (23.7 "observação das telas, sem digitar").

---

## 4. PostgreSQL: migrações 056–058, consultas novas, riscos de dialeto, testes e procedimento

### Takeaway

As três migrações e as consultas novas usam só construções que os dois dialetos aceitam (`TEXT`/`INTEGER`,
`CHECK`, `REFERENCES … ON DELETE CASCADE`, `{{PK_AUTO}}` → `BIGSERIAL`, `INSERT … ON CONFLICT … DO UPDATE SET …
excluded`, `RETURNING` para o id); não achei escrita nova que engula erro dentro de `tx()` (o risco K-061 fica nos
três candidatos pré-existentes). O que falta é a **prova**: Docker está instalado mas o daemon e o WSL estão
parados (ligar exige autorização), e o CI só roda o job de PostgreSQL por `schedule`/`workflow_dispatch`, travado pelo
limite de gasto da conta (K-040) até o ciclo virar.

### Cited Findings

**Migrações e o que criaram**

- 056 `step_outputs` (id TEXT PK `'<objective_id>:<name>'`, 3 FKs `ON DELETE CASCADE`, `CHECK (value_kind IN
  (…))`, `UNIQUE (objective_id, name)`, índice por `step_id`) e `ALTER TABLE steps ADD COLUMN saidas TEXT`; o
  cabeçalho declara "Compatível com SQLite e PostgreSQL… Sem BEGIN/COMMIT: o executor de migrações já abre a
  transação" — [056_saidas_de_etapa.sql](C:/git/android/backend/migrations/056_saidas_de_etapa.sql).
- 057 `network_profiles` (`name UNIQUE`, `CHECK` em `kind`, `protocol`, `endpoint_port BETWEEN 1 AND 65535`,
  `params TEXT DEFAULT '{}'`), `device_network` (`instance_id` PK sem FK; FKs para `network_profiles` **sem** `ON
  DELETE`, "apagar um perfil em uso é recusado pelo banco"; `CHECK` em `policy` e `state`), `network_measurements`
  (`id {{PK_AUTO}}`, `udp_ok`/`leak_blocked INTEGER CHECK (… IS NULL OR … IN (0,1))`, `per_app TEXT DEFAULT '{}'`,
  índice `(instance_id, measured_at)`) — [057_rede_por_aparelho.sql](C:/git/android/backend/migrations/057_rede_por_aparelho.sql).
- 058 `network_keys` (`owner` PK, `CHECK (kind IN ('servidor','aparelho'))`, `address TEXT UNIQUE`) —
  [058_chaves_de_rede.sql](C:/git/android/backend/migrations/058_chaves_de_rede.sql).
- Marcas por dialeto: `PK_AUTO` = `INTEGER PRIMARY KEY AUTOINCREMENT` (SQLite) / `BIGSERIAL PRIMARY KEY`
  (PostgreSQL); `BLOB`/`BYTEA`; blocos `-- @dialect:` — [backend/app/db.py:45-52](C:/git/android/backend/app/db.py).

**Consultas e transações novas**

- `Repository.save_step_output`: validação no domínio (`SAIDA_NOME_RE`, `SAIDA_VALOR_MAX`, `SAIDA_VALUE_KINDS`),
  depois `INSERT INTO step_outputs(…) VALUES (?,…) ON CONFLICT (objective_id, name) DO UPDATE SET run_id=excluded.run_id,
  step_id=…, value=…, value_kind=…, app_id=…, created_at=excluded.created_at` (um `execute` simples, fora de `tx()`
  no próprio método); leituras `step_outputs` (`ORDER BY name`), `saidas_com_tipo`, `saidas_da_etapa` (JSON de
  `steps.saidas`), `saidas_da_execucao` (JOIN `objectives`, `steps`, LEFT JOIN `apps`, `ORDER BY o.instance_id,
  s.seq, so.name`) — [backend/app/taskqueue/repository.py:366-418](C:/git/android/backend/app/taskqueue/repository.py).
- Chamador: `executor.py:861` `self.repo.save_step_output(step.id, nome, valor, value_kind=tipo, app_id=app.id)` —
  [executor.py:861](C:/git/android/backend/app/taskqueue/executor.py).
- Rede: `_registrar_medicao` grava a medição com `st.db.inserted_id(INSERT INTO network_measurements …)` dentro de
  `st.db.tx()` e, na mesma transação, atualiza a linha do aparelho — [backend/app/devices/rede.py:872-886](C:/git/android/backend/app/devices/rede.py);
  `inserted_id` usa `RETURNING`, porque "`cursor.lastrowid` é do SQLite e não existe no PostgreSQL" —
  [db.py:432-435](C:/git/android/backend/app/db.py). Cadastro de perfil: `INSERT INTO network_profiles` e o
  segredo do cofre "na MESMA transação"; o `except (SecretStoreLocked, SecretStoreUnavailable)` fica FORA do `tx()`
  e re-levanta como 503 (não engole) — [rede.py:286-300](C:/git/android/backend/app/devices/rede.py).
- Contagem de `except`/`savepoint`/`tx()` nos arquivos que tocam as tabelas novas: `devices/rede.py` 3/0/7;
  `rede_aplicacao.py` 4/0/0; `rede_convergencia.py` 13/0/0; `rede_medicao.py` 4/0/0; `rede_servidor.py` 7/0/0;
  `taskqueue/repository.py` 4/0/5 (os `except INTEGRITY_ERRORS` em 161 e 629-633 são de `attempts`, pré-existentes) —
  grep de 30/09 sobre o checkout.

**Regras do banco e a armadilha conhecida**

- `tx()`: `BEGIN IMMEDIATE` no SQLite / `BEGIN` no PostgreSQL; antes do COMMIT confere transação abortada e levanta
  `TransacaoAbortada` (500) — [db.py:330-345](C:/git/android/backend/app/db.py). `savepoint()`: `SAVEPOINT` /
  `ROLLBACK TO` / `RELEASE`, reentrante — [db.py:374-412](C:/git/android/backend/app/db.py).
- K-061 (29/09): "No PostgreSQL, erro engolido dentro de `tx()` aborta a transação e o COMMIT vira ROLLBACK calado";
  remédio: savepoint DENTRO do `try`, `TransacaoAbortada` no `tx()` de fora, imitação `tests/aborto_do_postgres.py`
  (`embrulhar(db)`) — [aprendizados.md:1421-1447](C:/git/android/docs/conhecimento/aprendizados.md). Candidatos
  conhecidos (pré-existentes): `commands/outbox.py` (`enqueue`), `social/repository.py` (`marcar_conta_travada`),
  `modules/learning/infrastructure/validacao_de_skills.py` (`add_case`) — [docs/banco.md:200-217](C:/git/android/docs/banco.md).
- Diferenças de dialeto já documentadas: `REAL` é precisão simples no PostgreSQL; ordenação `en_US.utf8` do
  `postgres:17` do CI "compara sem caixa na primeira passada" e difere do `sorted()` do Python; `UNIQUE COLLATE
  NOCASE` não existe no PostgreSQL — [banco.md:172-197](C:/git/android/docs/banco.md).

**Harness e CI**

- `TEST_DATABASE_URL` → cada teste ganha um schema próprio (`CREATE SCHEMA`, `options=-csearch_path%3D<schema>`),
  apagado no fim da sessão (`DROP SCHEMA … CASCADE`) — [backend/tests/conftest.py:110-155](C:/git/android/backend/tests/conftest.py);
  comando documentado: `docker run -d --name farm-pg -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=farm -p 55433:5432
  postgres:17-alpine` e, no PowerShell, `cd backend; $env:TEST_DATABASE_URL = "postgresql://postgres:teste@127.0.0.1:55433/farm";
  .venv\Scripts\python.exe -m pytest -q` — [banco.md:219-234](C:/git/android/docs/banco.md). Duração de referência:
  ~14 min (CLAUDE.md, tabela de comandos).
- CI: job `backend-postgres` ("backend · pytest (PostgreSQL)"), `runs-on: ubuntu-latest` (sempre na GitHub: usa
  contêiner de serviço `postgres:17`), `if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'`,
  cron `17 5 * * *`, `TEST_DATABASE_URL: postgresql://postgres:teste@localhost:5432/farm` —
  [.github/workflows/ci.yml:32-37, 75-97](C:/git/android/.github/workflows/ci.yml).
- K-040 (27/09): todos os jobs falhavam em 1–2 s porque "recent account payments have failed or your spending limit
  needs to be increased"; "A prova em PostgreSQL passou a `not_run` até o dono ajustar Settings → Billing & plans";
  ao voltar, `gh workflow run ci.yml --ref main` — [aprendizados.md:931-950](C:/git/android/docs/conhecimento/aprendizados.md).
  P17: "PostgreSQL não validado nesta evolução… depende de ligar Docker/WSL (autorização) ou do CI depois de 1º/10" —
  [terceira-evolucao.md:150](C:/git/android/docs/handoffs/terceira-evolucao.md).

**Disponibilidade local (leitura, 30/09)**

- `docker --version` → `Docker version 29.7.2, build a7dcaa6`; `docker ps -a` → "failed to connect to the docker API
  at npipe:////./pipe/dockerDesktopLinuxEngine… the daemon is running?" (daemon parado; não dá para saber se o
  contêiner `farm-pg` existe sem ligá-lo); `wsl --status` → distribuição padrão Ubuntu, versão 2; `wsl -l -v` →
  `Ubuntu Stopped (2)`, `docker-desktop Stopped (2)` — leituras de 30/09 nesta sessão.

**Testes que tocam as tabelas/consultas novas** (por grep no checkout, 30/09)

- Saídas de etapa (`save_step_output`, `step_outputs`, `resolver_saidas`, `saidas_da_execucao`):
  `backend/tests/test_valor_entre_etapas.py`, `test_conta_do_app_da_etapa.py`, `test_contratos_terceira_evolucao.py`,
  `test_interrupcao_entre_apps.py` (37 funções nos três primeiros).
- Rede (`network_profiles`, `device_network`, `network_keys`, `network_measurements`): `test_rede_por_aparelho.py`,
  `test_rede_aplicacao.py`, `test_rede_portao.py`, `test_rede_sonda.py`, `test_rede_worker.py`,
  `test_segredo_de_rede.py`, `test_contratos_terceira_evolucao.py` (105 funções nos seis de rede).
- Banco/dialeto: `test_db.py` (marcas por dialeto, blocos `@dialect`, `savepoint`, transação abortada),
  `aborto_do_postgres.py` (imitação), `test_migracao_de_dados.py`.

### Inferences

- **Riscos NOVOS de dialeto: baixos** (confiança alta na leitura, prova `not_run`):
  - `{{PK_AUTO}}` só em `network_measurements.id` → `BIGSERIAL`; o id volta por `RETURNING` (`inserted_id`), não
    por `lastrowid`. OK.
  - `INSERT … ON CONFLICT (objective_id, name) DO UPDATE SET … = excluded.…` é aceito pelo PostgreSQL (≥ 9.5) e pelo
    SQLite (≥ 3.24); a coluna de conflito é a `UNIQUE (objective_id, name)`, existente nos dois. Semântica de upsert
    igual: "a última escrita vence". OK.
  - `CHECK` com `IN (…)`, `BETWEEN`, `IS NULL OR … IN (0,1)`: iguais nos dois; 0/1 em `INTEGER` (não `BOOLEAN`), com
    `int(...)` no Python antes de gravar. OK.
  - JSON em `TEXT` (`params`, `per_app`, `steps.saidas`): sem operadores JSON no SQL; parse no Python. OK.
  - Timestamps ISO em `TEXT` com `ORDER BY measured_at`: ordem lexicográfica = cronológica enquanto o formato for o
    `now_iso()` (UTC, `Z`). OK.
  - `REFERENCES network_profiles(id)` sem ação: `DELETE` de perfil em uso é recusado nos dois (no SQLite, só com
    `PRAGMA foreign_keys=ON`, que o `db.py` liga — confiança média: não reli a linha do PRAGMA nesta sessão).
  - `ORDER BY name` em `step_outputs`: nomes casam `^[a-z][a-z0-9_]{0,39}$`; no `en_US.utf8` a pontuação (`_`) pode
    ordenar diferente do `sorted()` do Python. Os testes de `test_valor_entre_etapas.py` que li comparam dicionários
    e listas de nomes declarados, não a ordem devolvida por `step_outputs` — risco pequeno, a confirmar rodando.
- **Risco K-061 novo: nenhum encontrado**. Nos arquivos de rede não há `savepoint`, mas também não há `except` que
  engula erro de SQL dentro de um `tx()`: os `except` são de parse de IP/data, de cofre (fora do `tx()`, re-levanta) e
  de convergência (sem `tx()`). `save_step_output` valida antes do SQL e o método não abre `tx()`; o contexto do
  chamador (executor.py:861) não foi conferido — se estiver dentro de uma transação alheia com `except` largo, vale
  a regra do K-061. Os candidatos que continuam são os três **pré-existentes** de `banco.md`.
- **Procedimento para o dono autorizar** (toca WSL/Docker, que a invariante reserva à autorização; e carga: a suíte
  em PostgreSQL leva ~14 min e, em 29/09, carga pesada no central fez a escada de reparo resetar o android-01 —
  rodar um trabalho pesado por vez, em prioridade ociosa, fora de horário de tarefa):
  1. Ligar o Docker Desktop (sobe a distro `docker-desktop` do WSL).
  2. `docker ps -a --filter name=farm-pg`: se existir, `docker start farm-pg`; senão o `docker run` de `banco.md`
     (porta 55433, `postgres:17-alpine`).
  3. Fumaça (~1–2 min): um teste com o harness completo (`parque: Harness` → `AppState` + cliente HTTP), que é o que
     aplica TODAS as migrações no schema do teste, mais os testes de banco que usam o DSN da suíte:
     `cd backend; $env:TEST_DATABASE_URL = "postgresql://postgres:teste@127.0.0.1:55433/farm"; .venv\Scripts\python.exe -m pytest -q "tests/test_rede_por_aparelho.py::test_perfil_guarda_o_segredo_no_cofre_e_so_devolve_has_secret" tests/test_db.py tests/test_migracao_de_dados.py`
     Atenção: em `test_db.py` só os testes de conexão (`Database(_dsn_de_teste() or tmp_path/…)`, linha 49) tocam o
     PostgreSQL; os de marcas por dialeto usam `_Falso("postgres")`/`_Falso("sqlite")` sem conexão (linhas 29-45).
     `test_migracao_de_dados.py` também usa `_dsn_de_teste()` (linha 56). O teste do harness é o que prova que
     056–058 aplicam.
  4. Subconjunto da evolução (~3–5 min, estimativa):
     `.venv\Scripts\python.exe -m pytest -q tests/test_valor_entre_etapas.py tests/test_conta_do_app_da_etapa.py tests/test_contratos_terceira_evolucao.py tests/test_interrupcao_entre_apps.py tests/test_rede_por_aparelho.py tests/test_rede_aplicacao.py tests/test_rede_portao.py tests/test_rede_sonda.py tests/test_rede_worker.py tests/test_segredo_de_rede.py`
  5. Suíte inteira em segundo plano (~14 min), com a saída em arquivo; depois `Remove-Item Env:TEST_DATABASE_URL`.
  6. Registrar como `real` (data, máquina, commit, comando, contagem passed/failed) em `docs/relatorio-validacao.md`
     e fechar o P17; parar o contêiner (`docker stop farm-pg`) para devolver RAM ao parque.
- **Caminho pelo CI**: não depende de código; depende do limite de gasto (K-040). Quando o ciclo de cobrança virar
  ("1º/10" no handoff e na memória; inferência: é o reset mensal do limite de gasto/minutos da conta GitHub, não
  uma data do código), o job `backend-postgres` roda sozinho no cron `17 5 * * *` (UTC) do dia seguinte, ou
  imediatamente por `gh workflow run ci.yml --ref main` (workflow_dispatch). Ele cobre as migrações 056–058 porque o
  serviço `postgres:17` nasce vazio e a suíte aplica tudo por schema.

### Gaps

- Nenhuma execução em PostgreSQL foi feita (P17 continua `not_run`); tudo acima é leitura.
- Não confirmei se o contêiner `farm-pg` existe no Docker Desktop (o daemon está parado).
- Não confirmei a data exata em que o limite de gasto da GitHub volta ("1º/10" vem do handoff/memória; K-040 não
  cita a data).
- `PRAGMA foreign_keys` no SQLite e a igualdade de comportamento do `DELETE` de perfil em uso não foram relidos nesta
  sessão.
