# Integração da Central com o Trello do dono (estudo 32.1)

Pedido do dono em 03/10 (~17:40Z): a Central acessa e orquestra pelo Trello dele, "vinculando a todo o conhecimento que
existe na plataforma". Este estudo não tem código; o código é o 32.2 (Android, depois do 28.15, ADR-072). Base:
`origin/main` 13211826. Nenhuma chamada ao Trello foi feita (`not_run`).

O Trello é **espelho e canal**. A verdade continua no repositório e no banco.

## 1. Acesso

- **REST com chave + token do dono, só no `.env`:** `TRELLO_API_KEY` e `TRELLO_TOKEN`, mais `TRELLO_API_SECRET` (o
  segredo do aplicativo, que assina o webhook do §8). Nunca vão no `config.yaml`, no Git, em log ou em cartão. O MCP da orquestradora autentica a sessão dela, não o backend. `GET /api/trello` diz só
  "configurado ou não".
- **Passo a passo (o dono faz):**
  1. Em <https://trello.com/power-ups/admin> (hoje `trello.com/apps/admin`), crie o Power-Up "Central" e gere a chave na
     aba **Trello Auth**.
  2. Logado, abra
     `https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&name=Central&key=<chave>`
     e copie o token. O escopo `account` não é pedido.
  3. Grave os dois no `.env` e reinicie a `farm-central`, o mesmo rito do `TELEGRAM_BOT_TOKEN` (`docs/operacao.md`).
  4. Para revogar: `trello.com/u/<usuário>/account` → Applications.
- **Limite:** 100 requisições por 10 s por token e 300 por chave; acima disso, 429 com `x-rate-limit-*`. `/1/members/`
  tem 100 por 900 s, então não se faz polling nele.
- **Webhook como caminho principal, leitura periódica como reconciliação** (revisto em 03/10 ~19:35Z). Até então era
  "webhook não; polling", porque não havia URL pública. O dono decidiu o portal público (03/10 ~19:20Z: túnel de saída
  da Cloudflare, item 29.54, ADR-073) e disse que ele serve também ao Trello. O desenho do webhook está no §8.

  A reconciliação usa `GET /1/boards/{id}/actions?filter=commentCard,updateCard:idList&since=<cursor>&limit=1000` a
  cada 5 min: cobre o que o webhook perdeu (Central ou túnel fora). Sem webhook ligado, o mesmo laço roda a cada
  30–60 s, como no 28.15.

## 2. O que espelha (automático, sem ruído por evento)

Um cartão por **fato** (chave `<família>:<fato>`). Ele é atualizado no lugar só quando muda o hash do conteúdo, e é
arquivado com um comentário de desfecho quando o fato se resolve.

- **Execução → lista nova 🤖 Central (automático):**
  - as pendências do dono, que são a caixa `#/pendencias` (ADR-062) e as famílias `approval`, `run` (`needs_input`),
    `session`, `pedido` e `learning` de `avisos/domain/mensagem.py`;
  - os pedidos persistentes (067) em `ativo`, `pausado` ou `aguardando_pessoa`;
  - os itens do Livro em validação (082).
- **Programa → marcos:** um cartão por deploy, quando `commit` e `migration` de `/api/health` mudaram na partida.
- **Programa → custos:** um cartão por dia com o custo por conta (`GET /api/usage?days=1`, `by_account`, e
  `/api/ai/balances`), atualizado no máximo de hora em hora.

As listas atuais do Execução são do programa de desenvolvimento (`.claude/trello/estrutura.json`, geradas por
`gerar.py`). O operacional fica numa lista própria para não poluí-las. O Histórico não é tocado.

## 3. O que aceita de volta

- **Mover o cartão** de aprovação para **✅ Aprovado** ou **⛔ Vetado** (listas novas) vira
  `POST /api/approvals/{id}/decide` com `verb` `approve` ou `reject`. O `edit` fica no painel.
- **Comentário com comando:** a gramática comum de `canais-externos.md`, a mesma do Telegram. O fato é o cartão.
  - Num cartão de aprovação, "sim" ou `/aprovar [nota]` aprova, e "não" ou `/vetar [nota]` veta.
  - Num cartão de `needs_input`, o texto ou `/responder <texto>` é a resposta.
  - `/status` (`/estado`), `/pendencias` e `/ajuda` valem em qualquer cartão.
  - O `/para <aparelho|persona> <objetivo>` e o texto livre ficam desligados de fábrica no Trello
    (`trello.comando_livre`).

  O roteamento é por regra fixa, sem IA. Desde 03/10 (~18:15Z), este item foi alinhado ao contrato aprovado: o `/para`
  é pedido com destino, e não há verbo de parar.
- **Identidade:** só vale a ação com `idMemberCreator` igual a `trello.membro_dono` (no `config.yaml`; é id, não
  segredo). As demais são ignoradas, registradas só pelo id. O operador é `trello:<idMember>`, passado em `decided_by`
  (`social/approvals.py:162`).

  A rota de decidir só confere a sessão e o `pending`. Por isso o 32.2 confere a identidade antes e chama o mesmo
  serviço do painel, com as mesmas políticas.
- **Segredo:** um comentário com cara de credencial é recusado e não guardado. A resposta pede que o dono o apague e
  não ecoa o texto. A Central não apaga conteúdo do dono.
- **Dedupe** pela action id (chave única), com o cursor por quadro: um comando nunca roda duas vezes.

## 4. Vínculo ao conhecimento

Cada cartão tem um bloco **Vínculos**. Os links usam `url_painel` (hoje só `avisos.url_painel`; o painel é da LAN).

- **Painel** (`frontend/src/lib/rotas.ts`):
  - `#/execucoes/<run_id>`, `#/pedidos/<id>`, `#/pendencias`;
  - `#/aprendizado?aba=aprendido&item=<kind>:<ref>`, para fluxo, receita e lição, com os pareceres do curador no
    detalhe;
  - `#/aplicativos/<app_id>` e `?foco=<aparelho>`.
- **Repositório:** a linha em `docs/plano-100.md`, o ADR em `docs/decisoes.md`, o K-* em
  `docs/conhecimento/aprendizados.md` e o conhecimento do app em `backend/app/conhecimento/apps/<pacote>/`.
- **Rota proposta:** `GET /api/conhecimento/resumo?ref=<família>:<id>` devolve
  `{ref, titulo, estado, atualizado_em, vinculos:[{rotulo, url}]}`. Só lê, exige sessão e não leva texto livre.

  É montada com o que já existe: `GET /api/aprendizado/{kind}/{ref}` (já traz os pareceres),
  `/api/aprendizado/apps/{pacote}`, `/api/pedidos/{id}` e, para o Livro, o construtor de dossiê
  (`learning/infrastructure/dossies.py:71`, hoje interno) reduzido aos campos sem texto.

  O painel e o espelho usam a mesma rota, então o cartão não diz nada diferente do painel.

## 5. O que não fazer

- Segredo em cartão, comentário ou log, inclusive o token e a credencial que alguém colar.
- Texto de terceiro em claro. A base é a regra do aviso (`mensagem.py:3-9`): tipo, estado, ids opacos e links. O conteúdo
  só vai **redigido** pelo filtro da C3 (`entidades.remover_entidades_com_motivo`), com corte e sem captura (decidido,
  abaixo).
- Duas fontes de verdade. A edição manual fora do §3 é sobrescrita na próxima sincronização, e nada se lê do Trello como
  estado.
- Cartão por evento, IA no roteamento, polling em `/1/members/`. Webhook só pelo §8: rota única, assinatura
  verificada, sem sessão. O Telegram continua no `getUpdates`.
- Depender do Trello: fora do ar, a fila espera com backoff e a Central segue.

## 6. Desenho para o 32.2 (10 linhas)

1. Bloco `trello:` no `config.yaml` (`enabled: false`, quadros, listas, `membro_dono`, intervalos, `conteudo: redigido`);
   segredo só no `.env`; a saúde acusa `trello_sem_segredo`.
2. Cliente `integrations/trello/` em httpx, com balde de 60 requisições por 10 s e backoff pelos cabeçalhos do 429.
3. Migração 087 (era 086, que passou ao 31.22 da Jev em 03/10 ~18:55Z): `trello_cartoes` (`chave` PK, `card_id`,
   `lista`, `hash`, `estado`) e `trello_cursor` (`quadro` PK, `ultima_action`). As actions recebidas vão para a
   `canal_entradas` genérica da 085 (`canal='trello'`, `id_externo` = action id), no lugar de uma `trello_acoes`.
4. O espelho é o segundo `Canal` do módulo `avisos`: `avisos.canal` vira lista e a chave do aviso é a do cartão. Roda só
   no líder da trava `avisos`.
5. Laço do espelho a cada 60 s: lê os fatos do §2, monta a descrição pela rota do §4, faz upsert só com hash novo e
   arquiva o que se resolveu.
6. Laço de entrada (no líder): actions desde o cursor, dedupe pela action id, `membro_dono`, parser do §3 comum ao 28.15.
7. O comando chama o serviço do painel com `decided_by`/`por` = `trello:<idMember>` e responde no cartão.
8. Testes com Trello falso (`httpx.MockTransport`, sem rede): upsert idempotente, dedupe, identidade, 429 e credencial
   recusada sem eco.
9. Documentação: ADR-072, adendo v0.99 (`/api/trello` e `/api/conhecimento/resumo`), `operacao.md` (§1), `banco.md`
   (087).
10. Rollout: lista de teste em sombra, depois as listas do dono. A prova `real` é um cartão espelhado e um `/aprovar`
    do dono.

**Decidido pela orquestradora (03/10, ~18:05Z):**
- token sem expiração (`expiration=never`), com a revogação documentada; o dono pode vetar;
- as listas 🤖 Central (automático), ✅ Aprovado e ⛔ Vetado criadas por ela no quadro Execução, com os ids gravados em
  `.claude/trello/estrutura.json`;
- conteúdo **redigido**, com a regra do Telegram: redação e corte, sem captura. O padrão do 32.2 passa a ser
  `conteudo: redigido`;
- link do painel só na LAN, por enquanto, registrado como limitação no ADR-072.

## 7. Desenho de código do 32.2 sobre o 28.15 (Canais, 03/10 ~19:20Z)

Base: o PR #166 em 69dcea02 (migração 085 genérica, `Recebida`, `SaidaDaConversa`, `PortasDaCentral`). Ele muda duas
coisas do §6:
- o espelho não é um segundo `Canal` da fila de avisos, e sim um reconciliador de estado;
- a entrada reaproveita a parte comum da conversa em vez de repetir o laço.

1. **Separar a conversa do leitor (1º commit, só mudança de lugar).** Hoje `ServicoDeEntrada` junta a parte comum
   (`registrar`, `tratar_pendentes`, a gramática, as políticas, a recusa de credencial) e o laço do `getUpdates`. Ela
   vira `ConversaDoCanal` (comum, recebe `canal`, `operador` e `SaidaDaConversa`) e `LeitorDoTelegram` (a volta, o
   offset, o 409, o descarte do histórico). Os testes do 28.15 passam sem mudança. É a base do contrato do §9 de
   `canais-externos.md`.
2. **`integrations/trello/cliente.py`:** httpx com o cabeçalho `Authorization: OAuth oauth_consumer_key=…,
   oauth_token=…` (nunca na URL), um balde de 60 requisições por 10 s e espera pelo 429. `TRELLO_API_KEY` e
   `TRELLO_TOKEN` entram no `EnvSettings` como `SecretStr` (no molde do `TELEGRAM_BOT_TOKEN`).
3. **Migração 087:** `trello_cartoes` (`chave` PK `<família>:<fato>`, `card_id`, `lista`, `hash`, `estado`,
   `atualizado_em`) e `trello_cursor` (`quadro` PK, `ultima_action`). As actions recebidas vão para a `canal_entradas`
   (`canal='trello'`, `id_externo` = action id, `ordem` NULL, `tipo` = 'mensagem' para comentário, 'botao' para cartão
   movido para ✅/⛔), e as respostas para a `canal_enviadas` (`ref_mensagem` = id do comentário).
4. **Espelho = reconciliador** (`EspelhoDoTrello`, no líder da trava `avisos`, a cada `trello.espelho_s` = 60 s):
   - o conjunto desejado sai das portas que já existem (`pendencias()`, os pedidos em ativo, pausado ou
     aguardando_pessoa, os itens do Livro em validação);
   - a descrição sai da rota do §4, redigida;
   - cartão novo → criar na lista 🤖 Central; hash mudou → atualizar; o fato sumiu → comentário de desfecho e
     arquivar. Nada por evento, então não há ruído.
   - Os campos personalizados (o Power-Up Custom Fields foi aprovado pelo dono às ~19:05Z) recebem Frente e Prova pela
     API.
5. **Marcos e custos:** o mesmo reconciliador mantém um cartão por deploy, quando `commit` e `migration` de
   `/api/health` mudam na partida, e um cartão de custo do dia, no máximo de hora em hora, em Programa.
6. **`LeitorDoTrello`** (no líder), reconciliação do webhook do §8:
   `GET /1/boards/{id}/actions?filter=commentCard,updateCard:idList&since=<cursor>`, a cada `trello.reconciliar_s`
   (300 s com o webhook ligado, 60 s sem ele). O que o webhook já trouxe cai no dedupe pela action id. A
   tradução é a MESMA função do §8 (`recebida_da_action`). Cada action vira uma `Recebida`. `do_dono` = `idMemberCreator == trello.membro_dono`. O fato é a `chave` do cartão
   em `trello_cartoes`. Mover para ✅ ou ⛔ vira o texto "sim"/"não" com o fato. A `ConversaDoCanal` faz o resto, com o
   operador `trello:<idMember>`.
7. **`SaidaDoTrello`:** `responder` comenta no cartão, `apagar` devolve False (a resposta pede ao dono que apague),
   `botoes` não existe (a prévia vira o comentário "comente `/executar`"). Isso só acontece com
   `trello.comando_livre: true`, que é desligado de fábrica. A porta do 28.15 depois da integração da suíte 14
   (7fd72929), `pergunta_sensivel(ref|None)`, vale igual: com o cartão do `needs_input` como fato (ref = o id da
   execução) ou sem ref, para o texto curto com pergunta de senha aberta (E6).
8. **Config `trello:`** (`enabled: false`, `quadros`, `listas`, `membro_dono`, `espelho_s`, `reconciliar_s`,
   `comando_livre: false`, `idade_max_s`, e o bloco `webhook:` do §8). Saúde: `trello_sem_segredo`, `trello_recusado`
   (401/403), `trello_limite` (429 seguidos) e as do §8.
   A linha-marco `inicio` do 28.15 vale igual: na 1ª subida, o cursor começa na action mais nova e o histórico do
   quadro não é tratado.
9. **Testes** (`httpx.MockTransport`, sem rede):
   - reconciliador: cria, atualiza só com hash novo, arquiva com desfecho, idempotente;
   - leitor: cursor, dedupe pela action id, membro que não é o dono, 1ª subida;
   - contrato comum: o mesmo `Comando` por telegram e por trello passa pelas mesmas políticas;
   - credencial recusada sem eco; 429;
   - o webhook (§8.9).
10. **Ordem dos commits:** (1) a separação (com o `registrar` sem saída do §8.5); (2) cliente + 087 + config; (3)
    espelho; (4) leitor + saída; (5) a rota do webhook e o cadastro (§8); (6) docs (ADR-072 com a decisão do portão,
    adendo v0.99, operacao §1, banco 087). O branch nasce da main depois do merge do #166 (suíte 14). Código e testes
    não dependem do portal; o cadastro REAL do webhook depende do 29.54 no ar e provado (o Trello faz HEAD na URL ao
    criar). Até lá, o 32.2 roda só com a reconciliação a 60 s. O 32.3 (painel) vem depois e lê `trello_cartoes` e
    `canal_entradas`.

    **Em aberto para o 32.3 (não decidir aqui):** a execução SUCESSORA de uma resposta pelo canal nasce com a chave
    `sucessora-…` e fica com `origem` None no selo do 30.38 (`app/contracts/origem.py`). Se ela deve herdar o canal da
    mãe, é decisão do desenho do 32.3.

## 8. Webhook do Trello (Canais, 03/10 ~19:50Z; pedido da orquestradora às 19:33Z)

Fonte das regras do Trello: a página oficial de webhooks (developer.atlassian.com, lida em 03/10 19:50Z):
- na criação, o Trello faz um HEAD na `callbackURL`, e sem 200 o webhook não nasce;
- cada chamada traz `X-Trello-Webhook` = base64(HMAC-SHA1(segredo do aplicativo, corpo + callbackURL));
- falha de entrega é repetida 3 vezes, com espera crescente;
- o webhook é desligado só depois de 30 dias E mais de 1000 falhas seguidas;
- as chamadas vêm das faixas de IP do produto `trello` em ip-ranges.atlassian.com.

1. **A rota:** `HEAD` e `POST /api/canais/trello/webhook`, no endereço `https://dev.nvit.com.br/api/canais/trello/webhook`
   (a API fica na raiz do domínio, não sob `/central`). Nada se cadastra no Trello antes do 32.2.
   - HEAD responde 200 só com `trello.webhook.enabled` E `TRELLO_API_SECRET` presente; senão 404. Assim nunca nasce
     um webhook que a Central não consiga verificar. O HEAD não traz assinatura.
   - POST desligado (ou sem o segredo) → 404, como se a rota não existisse.
2. **A assinatura, antes de qualquer outra coisa:**
   - lê os BYTES crus do corpo uma vez, com teto de 256 KB, antes do HMAC (acima disso, 413, sem ler o resto);
   - calcula `base64(HMAC-SHA1(TRELLO_API_SECRET, corpo + callback_url))`, em que `callback_url` é a string
     CONFIGURADA (`trello.webhook.callback_url`), nunca `request.url`: atrás do túnel a Central vê outro host e
     outro esquema;
   - compara com `hmac.compare_digest`. Cabeçalho ausente ou diferente → 401, sem corpo de resposta útil;
   - só depois disso o JSON é lido.
3. **Nada do corpo em log, evento ou resposta:** só a action id, o tipo e o veredito (`ok`, `assinatura`, `grande`,
   `ignorada`). Um contador de assinaturas inválidas por janela vira o problema `trello_webhook_assinatura_invalida`.
   O texto do comentário só fica em `canal_entradas` quando é do dono e passou pela triagem (a mesma política do
   Telegram).
4. **A tradução** (`recebida_da_action`, a mesma da reconciliação):
   - `id_externo` = a action id, `ordem` NULL;
   - `escrita_em` = `action.date`, para a regra de idade (E2 do 28.15) valer também no Trello: a action mais velha que
     `trello.idade_max_s` (900 s) fica `ignorada` sem texto;
   - `do_dono` = `action.idMemberCreator == trello.membro_dono`; o quadro tem de ser um dos `trello.quadros`;
   - `commentCard` → `mensagem`; `updateCard` com `listAfter` ✅/⛔ → `botao` com "sim"/"não"; o resto → `outro`
     (gravado sem texto, não tratado).
5. **O pedido HTTP só grava um AVISO; a verdade vem da API** (requisito da orquestradora, 03/10 20:18Z, com o quadro
   aberto a convidados). O corpo assinado nunca é a fonte da identidade nem do texto. Um convidado com papel de admin
   do workspace pode chegar à página do aplicativo e ao segredo que assina o webhook; com ele, forjaria um corpo
   assinado dizendo que o autor é o dono. O desenho vale mesmo com o segredo vazado:
   - o handler, depois da assinatura (item 2), grava só o id da action (`canal_entradas`, estado `aviso`, sem texto) e
     acorda o laço do líder (`asyncio.Event`). Responde 200 em seguida. Nenhuma chamada ao Trello dentro do pedido: um
     comentário lento atrasaria a resposta, o Trello repetiria e viria ruído;
   - o líder RELÊ a action pela API com o token do dono (`GET /1/actions/{id}`). Dali, e só dali, saem o autor
     (`idMemberCreator`), o cartão, o quadro, o tipo, a data e o texto. A tradução do item 4 roda sobre a action
     relida, e o que veio no corpo é descartado;
   - comando só existe se o autor relido for `trello.membro_dono` e o quadro for um de `trello.quadros`. Qualquer outro
     autor vira registro sem texto; a pergunta de convidado segue a regra do item 11;
   - a action que a API não devolve (apagada, de outro quadro, 404) vira `ignorada` sem texto.
   A reconciliação do §7.6 já lê pela API, então os dois caminhos chegam à mesma tradução. O `registrar(r, saida=None)`
   do §7.1 recebe a `Recebida` montada da action relida.
   - **Mudança na parte comum (§7.1):** `registrar` sem saída não responde nem apaga. A resposta pendente fica
     marcada pelo próprio estado: uma linha `recusada` sem nenhuma `canal_enviadas` com o seu `entrada_id`. O
     `tratar_pendentes` do líder passa a responder essas linhas. No Trello, `apagar` é sempre False, então a resposta
     pede ao dono que apague o comentário.
   - Se o processo não é o líder da trava `avisos`, ele grava do mesmo jeito; o líder trata.
6. **Repetição e replay:** a assinatura do Trello não tem hora. Como o corpo é só um aviso (item 5), repetir ou forjar
   um aviso no máximo faz o líder reler uma action que existe, com o autor verdadeiro. Quem capturasse uma chamada poderia repeti-la, mas a
   chave `(canal, id_externo)` da 085 faz a repetição não gravar nada (200, nenhuma segunda linha), e a regra de
   idade barra a action velha. As 3 repetições do próprio Trello caem no mesmo dedupe.
7. **O cadastro** (no líder, na partida e a cada hora, só com `trello.webhook.enabled` e os três segredos):
   - `GET /1/tokens/{token}/webhooks`; para cada quadro de `trello.quadros`, garante UM webhook com
     `callbackURL = trello.webhook.callback_url` e a descrição `central-de-aparelhos:<quadro>`;
   - cria o que falta (o Trello faz o HEAD do item 1); recria o que estiver `active: false`;
   - desligado o `trello.webhook.enabled`, apaga os webhooks com essa descrição (e só eles);
   - o problema `trello_webhook_inativo` aparece quando o cadastro falha ou o Trello marca o webhook inativo. A
     reconciliação segue cobrindo, a 60 s, enquanto ele estiver inativo.
8. **Config:** `trello.webhook.enabled: false` (separado de `trello.enabled`), `trello.webhook.callback_url` (a URL
   pública inteira, sem parâmetro e sem segredo) e `trello.webhook.max_bytes: 262144`. O segredo do aplicativo só no
   `.env` (`TRELLO_API_SECRET`). Opcional, do lado do dono: na Cloudflare, aceitar nesse caminho só as faixas de IP do
   Trello. É defesa em profundidade, não substitui a assinatura.
9. **Testes** (`httpx.MockTransport` e o cliente de teste do FastAPI, sem rede):
   - assinatura válida → 200 e a linha gravada; segredo errado → 401; segredo certo com `callback_url` diferente no
     HMAC → 401; sem cabeçalho → 401;
   - HEAD: 200 ligado com segredo; 404 desligado ou sem segredo;
   - corpo acima do teto → 413, sem ler o resto;
   - a mesma action duas vezes → 200 e uma linha só;
   - membro que não é o dono → linha sem texto; quadro fora da config → ignorada; action velha → `ignorada`;
   - comentário com cara de senha → `recusada` sem texto, e a resposta sai pelo líder, não pelo pedido;
   - a rota não aceita nem usa sessão: com cookie de sessão válido e assinatura errada → 401; o operador gravado é
     sempre `trello:<idMember>`;
   - **segredo vazado:** corpo assinado CORRETAMENTE, com o autor forjado no corpo (= `membro_dono`), e a leitura da
     API devolvendo outro autor → nada executa, a linha fica sem texto e o operador nunca é o do dono;
   - action relida de quadro fora da config, ou 404 na releitura → `ignorada` sem texto;
   - cartão criado por convidado (autor da action `createCard` relida ≠ `membro_dono`) → pedido de convidado, nada
     se formata nem executa;
   - o resto de `/api/` continua pedindo credencial pelo portão (um teste de regressão do `guarda`);
   - cadastro: cria o que falta, recria o inativo, não toca webhook de outra descrição, apaga os seus ao desligar.
10. **Rollout:** (a) o 32.2 com o webhook desligado: espelho e reconciliação a 60 s, prova real com um cartão
    espelhado e um `/aprovar`; (b) com o 29.54 no ar, com senha e provado: gravar `TRELLO_API_SECRET`, ligar
    `trello.webhook.enabled`, ver o cadastro e um comentário chegar em segundos; (c) a reconciliação desce a 300 s.

11. **Quadros com convidados** (dono, 03/10 ~20:15Z). O dono convidou duas pessoas, e o Trello grátis não deixa
    limitar o papel delas (podem ser admin do workspace). A regra é da Central:
    - quem é quem sai do AUTOR lido pela API (`idMemberCreator` da action), nunca do texto, do prefixo nem de "o id
      não está no mapa". Vale para comentário, cartão novo, cartão movido e arquivado;
    - só `trello.membro_dono` comanda. `trello.membros_autorizados` (vazia de fábrica) é a lista de quem o dono
      autorizou a pedir; até lá, o pedido de convidado vira um aviso ao dono (sim ou não), e nada executa;
    - a pergunta de convidado pode ser respondida no cartão, só com o que os quadros já mostram (decisão do dono; a
      Central só responde com `trello.responder_convidados: true`, desligado de fábrica);
    - texto de convidado é dado, nunca instrução: não vai ao planejador nem vira comando;
    - comentário de QUALQUER autor, inclusive o dono, é PEDIDO, nunca autorização de ação real em conta real. Essa se
      confirma no chat ou no Telegram, com uma pergunta de sim ou não (a porta `pergunta_sensivel` do 28.15 vale);
    - cartão movido ou arquivado por convidado não muda o estado da Central: o espelho é reconciliador (§7.4) e
      devolve o cartão ao lugar na volta seguinte, com um comentário 🤖 do porquê.
    - Antes de ligar a entrada de comandos pelo Trello: a orquestradora recomendou rebaixar convidado admin a membro
      normal; no plano grátis isso pode não existir, e então a garantia é só a do item 5 (releitura pela API).

**Texto para o ADR-072 (decisão do portão; precisa da revisão da Android):**

> **Exceção do portão para o webhook do Trello.** O `guarda` (`backend/app/main.py`) libera sem credencial exatamente
> `HEAD` e `POST` em `/api/canais/trello/webhook`, comparando o caminho exato, sem prefixo e sem curinga. Os demais
> métodos nesse caminho e todo o resto de `/api/` seguem como estão. A rota não exige nem honra sessão: o cookie, se
> vier, é ignorado, e o operador é sempre `trello:<idMember>`, tirado do corpo já verificado. A autenticação é a
> assinatura `X-Trello-Webhook` (HMAC-SHA1 com `TRELLO_API_SECRET` sobre o corpo cru mais a `callback_url`
> configurada), comparada em tempo constante. Ela falha fechada: sem o segredo ou com o webhook desligado, a rota
> responde 404 ao HEAD e ao POST; assinatura ausente ou errada dá 401; corpo acima do teto dá 413. A checagem de
> `Origin` do `guarda` não se aplica: o Trello não manda `Origin`, e a regra só vale com o cabeçalho presente. Nada
> do corpo vai a log, evento ou resposta. A assinatura só autentica um AVISO: o corpo nunca é a fonte da identidade nem
> do texto. O líder relê a action pela API com o token do dono, e só o autor relido igual a `membro_dono`, num dos
> quadros configurados, comanda; assim a decisão vale mesmo com o segredo do aplicativo vazado (o quadro tem
> convidados que podem ser admin do workspace). A idempotência pela chave `(canal, id_externo)` da 085 e a regra de
> idade neutralizam a repetição de uma chamada capturada. O `forbidden_host` continua valendo: o host público entra em
> `publicos` pelo 29.54, não por esta exceção.
