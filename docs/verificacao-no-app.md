# Verificação da conta no app depois do cadastro do igfarm

Procedimento para destravar uma conta que o igfarm criou e que caiu em verificação nativa no app do Instagram. Origem do
pedido: a ponte [`email-do-parque.md`](email-do-parque.md). **Prova real deste fluxo: `not_run`** (motivos no fim).

## Dois nomes para a mesma coisa

| Lado | Nome | Significado |
|---|---|---|
| igfarm | `challenge_manual` | A conta foi criada pela API mobile, mas o Instagram pede verificação que só o app nativo completa. O igfarm a tira do denominador de falhas. |
| Central | `auth_challenge` (`SessionStatus`) | A sessão do app caiu num desafio de segurança. O estado é de "precisa de pessoa" (`PRECISA_DE_PESSOA`, `session_rules.py`). |

## O que a plataforma faz sozinha (ADR-029, decisão do dono de 27/09/2026)

Quando a conta do app **âncora** entra em `auth_challenge`, o perfil passa a `blocked` sozinho, com o log "perfil @x
bloqueado automaticamente"; o agendador para o objetivo em curso e pausa as execuções dela. Isso **não se desfaz
sozinho**: se a pessoa resolver a tela e a sessão voltar a `session_ready`, quem reativa o perfil é uma pessoa, na tela
do perfil, porque reativar é afirmar que a conta voltou a ser usável. A regra nasceu de um fato medido: das oito contas
reais, as cinco que mostraram o aviso de verificação não voltaram. Logo, **este procedimento é uma tentativa de
destravar, sem garantia**; a prova do primeiro caso real diz a taxa.

## Procedimento

1. **Pré-condição.** A conta foi registrada por `POST /api/instagram/contas`: o perfil tem o @, a credencial (login = e-mail)
   está no cofre com consentimento `igfarm` e a caixa está em `caixas_email`. Um aparelho online com o Instagram instalado
   (APK só da Play Store com a conta do dono, ou arquivo que ele fornecer). Veja o aparelho em `GET /api/instances`.
2. **Conectar.** `.\scripts\instagram.ps1 conectar -Perfil @usuario` (equivale a `POST /api/instagram/profiles/{id}/connect`,
   202). A digitação da senha vai só pelo canal sensível (`type_secret`); nada de credencial na execução (ADR-040).
3. **Esperar o desafio.** A sessão vai a `auth_challenge`, o evento `session.needs_person` aparece e o perfil fica
   `blocked` (acima). Isso é esperado neste fluxo.
4. **Assumir o controle.** `POST /api/instances/{id}/control/take` devolve `{status, lease_id}` (ou use "Assumir" no painel).
5. **Completar o checkpoint no app, à mão.** A pessoa faz na tela do aparelho. O código de e-mail vem de
   `GET /api/instagram/contas/{conta_id}/codigo` (ou do webmail da caixa, filtrando pelo destinatário). CAPTCHA, selfie e
   código são da pessoa (ADR-009): a automação não os resolve.
6. **Devolver o controle.** `POST /api/instances/{id}/control/release` com o `lease_id`.
7. **Confirmar.** `.\scripts\instagram.ps1 verificar -Perfil @usuario` (`POST …/verify`, só observa). Verde = a sessão volta a
   `session_ready` com o usuário esperado. Se ficar vermelho, a conta continua travada: nada mais a fazer nela.
8. **Reativar o perfil** na tela do perfil (pessoa), só depois do passo 7 verde.
9. **Registrar a prova** (`real`): data, máquina, commit, aparelho, `profile_id`/`account_id`, ids de comando e de evento,
   resultado. Sem segredo, sem código.

## Por que a prova está `not_run`

- Nenhuma conta do igfarm foi registrada na central ainda (`POST /api/instagram/contas` nunca recebeu dados reais), então
  não há perfil nem credencial para conectar. Inventar uma conta ou credencial para o teste não vale como prova.
- O passo 5 é humano por definição (ADR-009).
- Entrar numa conta real do Instagram em emulador é efeito fora da máquina: pede o aval do dono para aquela conta.

Quando houver a primeira conta registrada, este documento passa a ser o roteiro e o resultado entra no `CHANGELOG.md`.
