# Integração da Central com o Trello do dono (estudo 32.1)

Pedido do dono em 03/10 (~17:40Z): a Central acessa e orquestra pelo Trello dele, "vinculando a todo o conhecimento que
existe na plataforma". Este estudo não tem código; o código é o 32.2 (Android, depois do 28.15, ADR-072). Base:
`origin/main` 13211826. Nenhuma chamada ao Trello foi feita (`not_run`).

O Trello é **espelho e canal**. A verdade continua no repositório e no banco.

## 1. Acesso

- **REST com chave + token do dono, só no `.env`:** `TRELLO_API_KEY` e `TRELLO_TOKEN`. Nunca vão no `config.yaml`, no
  Git, em log ou em cartão. O MCP da orquestradora autentica a sessão dela, não o backend. `GET /api/trello` diz só
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
- **Webhook não; polling, como no 28.15.** O webhook pede uma URL pública com 200 ao HEAD e assinatura HMAC-SHA1
  (`X-Trello-Webhook`). Não temos URL, e o túnel não se mexe.

  O polling usa `GET /1/boards/{id}/actions?filter=commentCard,updateCard:idList&since=<última action>&limit=1000` a
  cada 30–60 s: menos de 4 requisições por minuto.

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
- Cartão por evento, IA no roteamento, webhook ou túnel, polling em `/1/members/`.
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
6. **`LeitorDoTrello`** (no líder): `GET /1/boards/{id}/actions?filter=commentCard,updateCard:idList&since=<cursor>`.
   Cada action vira uma `Recebida`. `do_dono` = `idMemberCreator == trello.membro_dono`. O fato é a `chave` do cartão
   em `trello_cartoes`. Mover para ✅ ou ⛔ vira o texto "sim"/"não" com o fato. A `ConversaDoCanal` faz o resto, com o
   operador `trello:<idMember>`.
7. **`SaidaDoTrello`:** `responder` comenta no cartão, `apagar` devolve False (a resposta pede ao dono que apague),
   `botoes` não existe (a prévia vira o comentário "comente `/executar`"). Isso só acontece com
   `trello.comando_livre: true`, que é desligado de fábrica. As portas novas do 28.15 (`pergunta_de`,
   `ha_pergunta_sensivel_aberta`) valem iguais.
8. **Config `trello:`** (`enabled: false`, `quadros`, `listas`, `membro_dono`, `espelho_s`, `leitura_s`,
   `comando_livre: false`). Saúde: `trello_sem_segredo`, `trello_recusado` (401/403) e `trello_limite` (429 seguidos).
   A linha-marco `inicio` do 28.15 vale igual: na 1ª subida, o cursor começa na action mais nova e o histórico do
   quadro não é tratado.
9. **Testes** (`httpx.MockTransport`, sem rede):
   - reconciliador: cria, atualiza só com hash novo, arquiva com desfecho, idempotente;
   - leitor: cursor, dedupe pela action id, membro que não é o dono, 1ª subida;
   - contrato comum: o mesmo `Comando` por telegram e por trello passa pelas mesmas políticas;
   - credencial recusada sem eco; 429.
10. **Ordem dos commits:** (1) a separação; (2) cliente + 087 + config; (3) espelho; (4) leitor + saída; (5) docs
    (ADR-072, adendo v0.99, operacao §1, banco 087). O branch nasce da main depois do merge do #166 (suíte 14). O
    32.3, o portal, vem depois e lê `trello_cartoes` e `canal_entradas`.
