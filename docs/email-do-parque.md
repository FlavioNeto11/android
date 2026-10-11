# E-mail do parque

Endereços de e-mail das personas sobre **uma caixa compartilhada** (catch-all), e a leitura do código de confirmação
nessa caixa. Código em `backend/app/modules/email_do_parque/`; é a base das APIs de ponte com o igfarm (personas
pendentes e registro de conta).

> **Prova.** O comportamento descrito é exercitado em `backend/tests/test_email_do_parque.py` com leitor e cliente IMAP
> falsos (`simulated`). Em 09/10/2026, com autorização do dono, passaram no mundo real a geração do @ por IA, a da imagem
> e o login e a busca IMAP na Hostinger (`real`, CHANGELOG de 09/10). A leitura de um código de verdade segue `not_run`.

## Modelo: caixa catch-all

- O domínio (`nvit.com.br`) está configurado na Hostinger para que **todo** `*@nvit.com.br` seja entregue em
  `contato@nvit.com.br`. Não existe uma caixa por persona: o endereço da persona (`nome.sobrenome1234@nvit.com.br`) é
  só o destinatário no cabeçalho `Para:` de mensagens que caem na caixa compartilhada.
- Por isso a "senha do e-mail" de uma persona é a **senha da caixa compartilhada** (`contato@`), não de uma caixa
  própria. A API que entrega personas pendentes **não devolve senha alguma**.
- O igfarm lê o código de confirmação pelo IMAP dele (com a credencial da caixa). A central também sabe ler, por
  `EmailDoParque.codigo_recente`, com a credencial que o dono põe no arquivo de ambiente.
- **Webmail da persona.** Quem opera a conta abre o webmail da Hostinger, entra na caixa `contato@nvit.com.br` e filtra
  pelo destinatário (`Para:`) igual ao endereço da persona; o código chega ali, no assunto ou no corpo.
- IMAP: `imap.hostinger.com`, porta 993 (SSL), usuário `contato@nvit.com.br`.

## Configuração (`EMAIL_*`)

Só do arquivo de ambiente ou do ambiente do processo, nunca do `config.yaml`. Exemplo neutro no `.env.example` da raiz.
Sem `EMAIL_IMAP_HOST`, `EMAIL_IMAP_USER` e `EMAIL_IMAP_PASS`, o leitor fica ausente: gerar e validar endereço funciona,
e ler código responde `email_indisponivel` (503).

| Chave | Padrão | Para quê |
|---|---|---|
| `EMAIL_DOMINIO` | vazio | Domínio padrão dos endereços (`nvit.com.br`). |
| `EMAIL_IMAP_HOST` | vazio | Servidor IMAP (`imap.hostinger.com`). |
| `EMAIL_IMAP_PORT` | `993` | Porta IMAP sobre SSL; em branco vale 993. |
| `EMAIL_IMAP_USER` | vazio | Usuário da caixa compartilhada (`contato@nvit.com.br`). |
| `EMAIL_IMAP_PASS` | vazio | Senha da caixa (`SecretStr`: fora de repr, log, evento e resposta). |
| `EMAIL_ALLOWLIST_DOMINIOS` | vazio | Domínios aceitos pela API, separados por vírgula, minúsculos. Vazio = só `EMAIL_DOMINIO`; sem os dois, nenhum domínio é permitido. |

O serviço fica em `AppState.email_parque`.

**A central só lê o arquivo de ambiente quando sobe.** Depois de editar as chaves, pare o backend com
`pwsh -File scripts\stop.ps1` (emuladores continuam ligados) e espere o supervisor religá-lo (cerca de 1 min). Parar e
iniciar só a tarefa `farm-central` **não** reinicia o backend: ela reinicia o supervisor, e o `app.main` antigo segue
vivo com a configuração velha. Medido em 09/10/2026: depois de `Stop/Start-ScheduledTask` a API ainda respondia
`dominio_nao_permitido`; depois do `stop.ps1` aceitou `nvit.com.br`.

## Interface

- `gerar_endereco(persona_id, primeiro_nome, sobrenome, dominio, existentes)`: parte local `nome.sobrenome` em
  minúsculo, sem acento, só `[a-z0-9.]`, mais um sufixo de 4 dígitos **determinístico** (sha256 de `persona_id` e da
  tentativa). Se o endereço já está em `existentes` (comparação sem distinguir caixa), re-tenta a próxima tentativa.
  Nomes vazios viram `pessoa`; parte local com no máximo 64 caracteres. No serviço, o domínio passa antes por
  `validar_dominio` (erro `dominio_nao_permitido`, 422).
- `confere_dominio(email, dominio)`: o domínio do e-mail tem de ser o declarado (`dominio_divergente`, 422).
- `codigo_recente(endereco, remetente="instagram", janela_min=30)`: a mensagem **mais recente** destinada ao endereço
  (sem distinguir caixa) e de remetente que contenha `remetente`, dentro da janela; extrai o primeiro grupo de 6
  dígitos do assunto e, se não houver, do corpo. Se a mais recente não traz código, vale a anterior que traga. Sem
  mensagem ou sem código: `None`. Sem leitor: `email_indisponivel` (503). Nunca registra corpo de e-mail nem senha.
- Adaptador IMAP (`adapters/imap.py`): `INBOX` só leitura, `SEARCH SINCE <data> TO "<destinatario>" FROM "<remetente>"`
  (num catch-all o `TO` do cabeçalho traz o endereço da persona), `FETCH BODY.PEEK` das últimas 10 mensagens,
  timeout de socket de 15 s. Erro de rede ou de login vira `email_indisponivel` sem texto do servidor nem senha.

## Divisão de responsabilidade

- A API confere só handles **nossos**: contas vivas, lápides e reservas da própria central. Um @ já tomado por terceiro
  no Instagram é problema do igfarm, que re-tenta ao receber `username_is_taken`.
- O endereço sugerido é checado contra o que a central conhece (contas, credenciais, caixas registradas, reservas).

## Transporte e token

A API de registro de conta recebe **senhas no corpo**; as respostas trazem as senhas mascaradas, nunca o valor.

- **Mesma máquina (loopback):** o igfarm em `127.0.0.1` chama a central em `127.0.0.1:8000` **sem token**. Provado em
  09/10/2026: `GET /api/instagram/personas-pendentes` respondeu 200 sem cabeçalho de autorização (igfarm e central rodam em
  `WIN-7S2UASNLFOP`).
- **Outra máquina:** só atende com `API_TOKEN`, `server.public_hosts` e TLS (o middleware `guarda` e `conferir_exposicao`
  exigem os três; sem isso a central nem sobe fora do loopback). O igfarm manda `Authorization: Bearer <API_TOKEN>` por
  HTTPS.
- **Como obter o token:** o dono o gera e o guarda; ele nunca vai para Git, log, chat nem documento. Gere um valor
  aleatório e longo, por exemplo `pwsh -File scripts\portal-gerar-senha.ps1` (24 bytes) ou
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`, ponha `API_TOKEN=<valor>` no arquivo de ambiente da raiz
  da central e reinicie o backend como acima. O mesmo valor vai, à mão, na configuração do igfarm. Para saber se há token
  configurado, a central não o mostra: `GET /api/session` responde `token_required` (em 09/10/2026 o central respondia
  `false`: sem `API_TOKEN`, só atende o loopback).

## Fluxo do igfarm

1. Sempre `GET /api/instagram/personas-pendentes?dominio=...&reservar=true&limite=<N pequeno>`. A **imagem de perfil só
   é gerada na reserva** (`reservar=true`): a listagem sem reservar devolve a imagem já existente ou `null` com
   `imagem_pendente: true`, e `com_imagem=false` nunca gera. Isso evita dezenas de gerações pagas numa listagem.
2. O igfarm cria a conta no Instagram usando o endereço sugerido, lê o código da caixa pelo IMAP dele e, se o @ estiver
   tomado, tenta outro (`username_is_taken`).
3. `POST /api/instagram/contas` registra a conta criada.

## Fluxo da API

Código em `backend/app/modules/identity/{domain,application,infrastructure}/ponte_igfarm.py`; rotas em
`modules/identity/presentation/instagram.py`; tabelas da migração 132 (`persona_reservas`, `caixas_email`,
`contas_igfarm`; não toca `profile_accounts`, a 131 é da conta planejada do ADR-087 e a convergência fica para depois).

- **`GET /api/instagram/personas-pendentes?dominio=&limite=10&locale=&com_imagem=true&reservar=false`**
  - Pendente = persona ativa, maior de 18 anos (sem `birth_date` não sai), sem conta, sem e-mail, sem credencial, fora
    da lápide (`contas_retiradas`) e sem reserva vigente.
  - Em **todo** GET a sugestão (e-mail + @ por IA) é gravada em `persona_reservas` e reaproveitada: duas chamadas
    devolvem o mesmo e-mail e o mesmo @, sem nova chamada de IA.
  - `reservar=true` preenche `reservada_em`/`expira_em` (TTL de 24 h) e tira a persona das outras chamadas.
  - **A imagem só é gerada com `reservar=true`** (e `com_imagem=true`), para uma listagem não disparar até 50 gerações
    pagas. Sem reserva: imagem já existente ou `imagem_perfil: null` com `imagem_pendente: true`. A URL é
    `/api/personas/{persona_id}/images/{image_id}` (não existe `/media/`).
  - **O igfarm sempre chama com `reservar=true` e `limite` pequeno.**
  - IA indisponível: `ai_unavailable` (503), sem @ de reserva; imagem sem gerador: `image_not_configured` (409).
- **`POST /api/instagram/contas`** (idempotente por `persona_id` + `instagram_username`): 201 na primeira vez, 200 com
  `idempotente: true` na repetição. Valida persona (404), domínio (`dominio_nao_permitido`/`dominio_divergente`, 422),
  @ contra conta viva e lápide (409) e e-mail em uso (`email_em_uso`, 409). Cria a conta no app âncora pelo caminho
  normal (`add_account`, com consentimento e senha no cofre), guarda a senha da caixa no cofre (`caixas_email`) e o
  `igfarm_account_id` (`contas_igfarm`) e emite `identity.conta.registrada` (sem senhas). A resposta traz
  `senhas: "••••"`, nunca o valor.
- **`GET /api/instagram/contas/{id}/codigo`** (opcional): `{codigo, recebido_em, remetente}` do e-mail mais recente da
  conta; 404 `sem_codigo`; 503 `email_indisponivel`. Aceita o id da conta nossa ou o `igfarm_account_id`.
- **Transporte:** a API 2 recebe senhas no corpo. Fora do loopback a rota já exige `API_TOKEN` e TLS (middleware
  `guarda`); não exponha sem os dois.
- **Divisão de responsabilidade:** a plataforma só confere @ de contas nossas (viva, lápide, reservas). @ tomado por
  terceiro no Instagram o igfarm resolve, tentando outro em `username_is_taken`.

## Limites

- **Estado das provas, sem ambiguidade (09/10/2026, `WIN-7S2UASNLFOP`, central `7156f0df` + docs `96627654`):**

  | Item | Nível | Prova |
  |---|---|---|
  | Domínio `nvit.com.br` aceito; domínio de fora recusado | `real` | central: 200 e 422 `dominio_nao_permitido`, 18:58Z |
  | @ sugerido por IA pela rota, no central | `real` | `claude-sonnet-5-5`; para `ig-persona-Qud6TL9ZehJggYAT` (valor omitido: contém o nome da persona) |
  | Reserva (`reservar=true`) e não repetição da persona reservada | `real` | 18:58:54Z, a chamada seguinte não devolveu a persona |
  | Imagem da persona servida pela URL da rota | `real` | `img-a-BX4lR85vyevuYI`, JPEG de 84 KB, `gpt-image-2`, gerada em 29/09 na criação da persona |
  | Imagem GERADA dentro da rota (ramo `reservar=true` sem foto) | `simulated` | `test_personas_pendentes_api`; as 6 pendentes já tinham foto (`on_create`), o ramo não disparou |
  | Login e busca IMAP na Hostinger | `real` | 18:03Z, 55 mensagens na caixa |
  | `GET /contas/{id}/codigo` com IMAP real | `real`, com relógio fixado | e-mail real de `no-reply@mail.instagram.com` recebido 13:21:05Z lido de um endereço de teste do igfarm; banco de teste temporário; relógio do serviço em 13:30Z porque o e-mail tem mais de 30 min |
  | Código lido por um cadastro novo, com relógio real | `not_run` | exige o igfarm cadastrar uma conta agora |
  | `POST /api/instagram/contas` com dados reais do igfarm | `not_run` | o igfarm ainda não chamou |
  | Verificação no app (checkpoint) | `not_run` | ver [`verificacao-no-app.md`](verificacao-no-app.md) |
- A caixa é compartilhada: quem tem a senha lê o e-mail de todas as personas. Por isso a senha da caixa nunca sai em
  resposta de API, evento, log ou evidência, e a leitura é sempre filtrada pelo destinatário.

## Cabeçalhos da caixa e a checagem do domínio (31.336)

- **Cabeçalhos, sem corpo.** `GET /api/instagram/contas/{id}/cabecalhos` (adendo v1.147) lista remetente, assunto (seis dígitos
  mascarados), data, SPF/DKIM/DMARC anotados pelo servidor de entrada e se é aviso de devolução, lendo a caixa catch-all em
  somente leitura (`BODY.PEEK[HEADER.FIELDS ...]`). Serve para ver se o Instagram mandou e-mail à conta e se alguém o devolveu.
- **O que se mediu do domínio `nvit.com.br`** (prova `real`, leitura de DNS público, RDAP do Registro.br e do `poc.sqlite3`; 11/10/2026):
  registrado em 21/07/2025; MX Hostinger; SPF válido (`~all`); DKIM por CNAME da Hostinger; DMARC `p=none` (sem `rua`); sem MTA-STS;
  Spamhaus DBL, SURBL e URIBL **sem listagem**. Ausência em lista pública não prova que o Instagram não use uma reputação própria.
- **Hipóteses que ficam como hipótese:** domínio relativamente novo, todas as personas num só domínio, caixa catch-all e nomes
  numerados são o tipo de padrão que antiabuso correlaciona. Com n=2 contas (A e B da bifurcação) não se separa domínio de IP/
  comportamento. Recomendação aceita: não escalar volume em `@nvit.com.br` antes de ler os cabeçalhos da caixa e de o igfarm dizer o
  que o Instagram devolveu nos signups. DMARC `p=none` é DNS do dono: recomendação a ele, não ação nossa.
- **Fora do escopo (decisão da orquestradora):** nenhum envio de e-mail, nenhum signup de teste; o grupo de controle por domínio fica
  para o próximo lote, condicionado a conta e sessão entregues (P-049/P-050).
