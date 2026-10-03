# Canais externos: o contrato comum do Telegram (28.15) e do Trello (32.2)

Este é o contrato que os dois canais de conversa da Central cumprem. O ADR-071 (Telegram, item 28.15) e o ADR-072
(Trello, item 32.2) citam este arquivo e não repetem o texto. Aprovado pela orquestradora em 03/10 (~18:15Z), com a
gramática alinhada ao que a Android já codificou no 28.15 (`feat/28-15-telegram-entrada` @cf7303ba,
`backend/app/modules/avisos/application/entrada.py`). O espelho do Trello está em `trello-integracao.md`.

## 1. Uma entrada comum

Cada canal só **traduz** o que chegou num `Comando(canal, autor, id_externo, alvo, verbo, argumento)`:

- o Telegram traduz um update;
- o Trello traduz uma action: um comentário, ou um cartão movido para ✅ Aprovado ou ⛔ Vetado.

A gramática (`rotear`), as políticas, a auditoria e a resposta são **um módulo só**, junto do `modules/avisos/`, que já
é o canal de saída. A gramática não conhece canal nem `app.security`; a triagem de credencial é do serviço de entrada.

## 2. Gramática fechada, por regra fixa e sem IA

**Mensagem solta (sem fato):**

| Comando | O que faz |
|---|---|
| `/ajuda` (`/start`) | a ajuda |
| `/status` (alias `/estado`) | o parque e o que está rodando |
| `/pendencias` | o que espera o dono |
| `/aprovar <id> [nota]` e `/vetar <id> [nota]` | decidem uma aprovação; `<id>` é o id inteiro ou o fim dele, como a `/pendencias` mostra |
| `/responder <id> <texto>` | responde à pergunta de uma execução |
| `/para <aparelho\|persona> <objetivo>` (ou "para o X: <objetivo>") | um pedido com destino |
| texto livre | um objetivo, com o destino tirado pelo extrator do painel |
| `/orq <texto>` | recado para a orquestradora, guardado e não executado |

**Com fato** (a resposta a um aviso, ou o comentário num cartão), o id é o do fato e não se escreve:

- numa aprovação, "sim" ou `/aprovar [nota]` aprova, e "não" ou `/vetar [nota]` veta;
- numa execução que espera resposta, o texto, ou `/responder <texto>`, é a resposta.

Comando fora da gramática recebe a ajuda com o motivo, e nada executa. **O `/para` e o texto livre (o comando livre)
valem por canal:** ligados no Telegram e desligados de fábrica no Trello (`trello.comando_livre: false`).

## 3. O alvo (o fato)

O fato é a chave `<família>:<fato>` do aviso, de `avisos/domain/mensagem.py`, com as famílias `approval`, `run`,
`session`, `pedido` e `learning`.

- **No Telegram:** é o `reply_to` da mensagem do aviso a que o dono respondeu. Sem reply, o alvo é explícito
  (`<id>`).
- **No Trello:** é o cartão comentado ou movido. A chave do cartão é a do fato.

## 4. Identidade e operador

- Há um dono por canal: o usuário do `TELEGRAM_CHAT_ID` e o `trello.membro_dono`.
- A identidade é conferida **na entrada comum, antes de qualquer serviço**. A rota de decidir hoje confere só a sessão
  e o `pending` (`social/approvals.py:162`).
- Autor que não é o dono é ignorado e registrado só pelo id.
- O operador vai como valor em `decided_by`/`por`: no Telegram, `telegram:dono` (como o cf7303ba grava); no Trello,
  `trello:<id do membro>` ou `trello:dono`, que o ADR-072 fixa.

## 5. Políticas iguais às do painel

O comando chama os **mesmos serviços das rotas do painel**:

- a decisão de aprovação (`approve` ou `reject`; o `edit` fica só no painel);
- a resposta ao `needs_input`;
- a criação do pedido ou da execução, no `/para` e no texto livre.

Não há atalho de política, e o `approval_required` continua sendo aplicado na criação.

## 6. Dedupe e cursor

- **Migração 085 = tabela genérica `canal_entradas`**, com chave única `(canal, id_externo)`: o `update_id` no Telegram
  e a action id no Trello. Ela fica no lugar de uma `telegram_mensagens` própria. Assim a **086 do 32.2 encolhe** para
  `trello_cartoes` e o cursor.
- O cursor de cada fonte é durável: o offset no Telegram e a última action de cada quadro no Trello.
- Só o líder consome. Um comando nunca roda duas vezes.

## 7. Credencial

O detector é **por formato** (a regra da redação de log), aplicado pelo serviço de entrada antes de gravar.

- A mensagem com cara de credencial é recusada e não é guardada.
- O canal **apaga a mensagem quando pode**: `deleteMessage` em chat privado do Telegram. O Trello não apaga conteúdo do
  dono.
- A resposta não ecoa o texto: "não guardei; apaguei; grave no painel". Quando o canal não pode apagar, a resposta pede
  ao dono que apague.

## 8. Conteúdo que sai

A regra é a mesma nos dois canais: **redação** pelo filtro da C3, **corte** e nada de captura de tela. A resposta a um
comando leva o desfecho:

- `ok`;
- `já decidida`, o 409;
- `não achado`, o 404;
- `recusado`;
- a ajuda com o motivo.

Ela nunca leva conteúdo de terceiro em claro.

## 9. Testes de contrato

Uma bateria comum roda contra os dois adaptadores, com update e action falsos. Ela cobre:

- a gramática, solta e com fato;
- a identidade;
- o dedupe;
- a credencial;
- as políticas do painel.

Cada canal só testa a própria tradução. Nenhum teste toca a rede (`httpx.MockTransport`).
