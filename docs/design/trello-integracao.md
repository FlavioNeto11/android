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
- **Comentário com comando:**
  - `/aprovar`;
  - `/vetar [nota]`;
  - `/responder <texto>`, que vira a sucessora do `needs_input` por `POST /api/runs/{id}/successor`; o 28.15 confirma
    a rota (INFERRED);
  - `/para`, que cancela a execução ou pausa o pedido;
  - `/estado`, que responde com o resumo do §4.

  O alvo é o cartão. O roteamento é por regra fixa, sem IA. O 28.15 ainda não tem gramática em branch nenhum: a
  proposta é um parser comum aos dois canais.
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
- Texto de terceiro em claro. A regra é a do aviso (`mensagem.py:3-9`): tipo, estado, ids opacos e links. O conteúdo só
  vai **redigido** pelo filtro da C3 (`entidades.remover_entidades_com_motivo`), por opção
  (`trello.conteudo: redigido`), desligada de fábrica.
- Duas fontes de verdade. A edição manual fora do §3 é sobrescrita na próxima sincronização, e nada se lê do Trello como
  estado.
- Cartão por evento, IA no roteamento, webhook ou túnel, polling em `/1/members/`.
- Depender do Trello: fora do ar, a fila espera com backoff e a Central segue.

## 6. Desenho para o 32.2 (10 linhas)

1. Bloco `trello:` no `config.yaml` (`enabled: false`, quadros, listas, `membro_dono`, intervalos, `conteudo: tipo`);
   segredo só no `.env`; a saúde acusa `trello_sem_segredo`.
2. Cliente `integrations/trello/` em httpx, com balde de 60 requisições por 10 s e backoff pelos cabeçalhos do 429.
3. Migração 086: `trello_cartoes` (`chave` PK, `card_id`, `lista`, `hash`, `estado`), `trello_cursor` (`quadro` PK,
   `ultima_action`) e `trello_acoes` (`action_id` PK, `operador`, `resultado`).
4. O espelho é o segundo `Canal` do módulo `avisos`: `avisos.canal` vira lista e a chave do aviso é a do cartão. Roda só
   no líder da trava `avisos`.
5. Laço do espelho a cada 60 s: lê os fatos do §2, monta a descrição pela rota do §4, faz upsert só com hash novo e
   arquiva o que se resolveu.
6. Laço de entrada (no líder): actions desde o cursor, dedupe pela action id, `membro_dono`, parser do §3 comum ao 28.15.
7. O comando chama o serviço do painel com `decided_by`/`por` = `trello:<idMember>` e responde no cartão.
8. Testes com Trello falso (`httpx.MockTransport`, sem rede): upsert idempotente, dedupe, identidade, 429 e credencial
   recusada sem eco.
9. Documentação: ADR-072, adendo v0.99 (`/api/trello` e `/api/conhecimento/resumo`), `operacao.md` (§1), `banco.md`
   (086).
10. Rollout: lista de teste em sombra, depois as listas do dono. A prova `real` é um cartão espelhado e um `/aprovar`
    do dono.

**Decisões do dono:**
- o `expiration` do token (`never` com revogação, ou `30days`);
- criar as listas 🤖 Central, ✅ Aprovado e ⛔ Vetado;
- o conteúdo (`tipo` ou `redigido`);
- se o link do painel só na LAN basta.
