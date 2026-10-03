-- A conversa do dono com a Central pelos canais externos (item 28.15, ADR-071; contrato comum em
-- docs/design/canais-externos.md, §6). O Telegram é o primeiro canal; o Trello (32.2) usa as MESMAS tabelas com
-- `canal = 'trello'`, e a 086 dele fica só com o que é do Trello (os cartões e o cursor por quadro).
--
-- O 28.11 só mandava (aviso). Agora o mesmo bot também RECEBE: um laço de long-poll (`getUpdates` com offset), no líder
-- da trava `avisos`, é o único consumidor do bot. Não há webhook nem rota HTTP de entrada.
--
-- Duas tabelas:
-- - `canal_entradas` guarda uma linha por coisa recebida (a update do Telegram, a action do Trello). A chave do dedupe é
--   `(canal, id_externo)`: a releitura cai no `ON CONFLICT DO NOTHING`, então um comando nunca roda duas vezes. No
--   Telegram ela é também a fonte do offset (`MAX(ordem) + 1`, com `ordem` = `update_id`): a update é gravada ANTES de
--   o offset avançar, então reiniciar não repete nem perde.
-- - `canal_enviadas` guarda a referência de tudo o que a Central mandou por um canal (aviso, resposta, ajuda). É o que
--   liga a resposta da pessoa ao fato do aviso. No Telegram, reply a uma mensagem do bot que NÃO está aqui é da
--   orquestradora: fica guardada e não é executada.
--
-- As referências do canal (`id_externo`, `ref_mensagem`, `responde_a`) são TEXT: o Telegram numera (inteiros da Bot
-- API, guardados em texto decimal), o Trello usa ids hexadecimais.
--
-- O que NÃO se guarda:
-- - o chat_id (ou o membro): a v1 aceita só o dono do canal (`TELEGRAM_CHAT_ID` no `.env`), e `do_dono` diz se veio dele;
-- - o texto do que não veio do dono (`texto` NULL; só conta);
-- - o texto que parece credencial ou código (`estado = 'recusada'`, `texto` NULL, só o `tamanho`); o canal ainda apaga
--   a mensagem quando pode.
--
-- Mesmo padrão da 082:
-- - sem CHECK nos vocabulários (`canal`, `tipo`, `intencao`, `estado`, `destino`, `origem` mudam sem migração; quem
--   confere é o domínio);
-- - SEM chave estrangeira: a execução, a aprovação e o aviso podem ser purgados; o registro fica;
-- - tempo em texto ISO-8601 (ordena igual nos dois dialetos).
--
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS canal_entradas (
    id            {{PK_AUTO}},
    canal         TEXT NOT NULL,                  -- 'telegram' | 'trello'
    id_externo    TEXT NOT NULL,                  -- o `update_id` do Telegram; o id da action do Trello
    ordem         BIGINT,                         -- a ordem da fonte quando ela numera (o `update_id`); NULL se não
    tipo          TEXT NOT NULL,                  -- 'mensagem' | 'botao' (Telegram) | 'outro'; o Trello traz os dele
    do_dono       INTEGER NOT NULL DEFAULT 0,     -- 1 = o dono do canal (o chat configurado); 0 = outro (sem texto)
    ref_mensagem  TEXT,                           -- a mensagem da pessoa no canal (no botão, a do bot com o teclado)
    responde_a    TEXT,                           -- a mensagem do canal a que ela responde (o reply do Telegram)
    texto         TEXT,                           -- NULL quando não veio do dono e na recusa por credencial
    tamanho       INTEGER NOT NULL DEFAULT 0,     -- caracteres recebidos (o que fica da mensagem recusada)
    intencao      TEXT,                           -- 'status' | 'pendencias' | 'aprovar' | 'vetar' | 'responder' | 'para' | ...
    destino       TEXT,                           -- 'central' | 'orquestradora'
    estado        TEXT NOT NULL DEFAULT 'recebida',  -- 'recebida' | 'ignorada' | 'recusada' | 'limitada' | 'orquestradora'
                                                     -- | 'pergunta' | 'executando' | 'feita' | 'cancelada' | 'falhou'
    alvo          TEXT,                           -- o fato sobre o qual age: 'approval:<id>', 'run:<id>'
    previa        TEXT,                           -- JSON da prévia dos alvos que espera o Executar/Cancelar
    run_id        TEXT,                           -- a execução criada por esta entrada (sem FK)
    resposta      TEXT,                           -- o que a Central respondeu (curto, redigido)
    erro          TEXT,                           -- por que falhou ou foi recusada (curto, redigido)
    recebida_em   TEXT NOT NULL,
    tratada_em    TEXT,
    resultado_em  TEXT,                           -- quando o desfecho da execução foi contado na conversa
    UNIQUE (canal, id_externo)
);
-- O offset do Telegram (`MAX(ordem)` do canal).
CREATE INDEX IF NOT EXISTS ix_canal_entradas_ordem ON canal_entradas(canal, ordem);
-- A janela do limite de taxa e a leitura da orquestradora (as mais novas primeiro).
CREATE INDEX IF NOT EXISTS ix_canal_entradas_recebida ON canal_entradas(canal, recebida_em);
-- O desfecho que ainda falta contar na conversa.
CREATE INDEX IF NOT EXISTS ix_canal_entradas_resultado ON canal_entradas(run_id, resultado_em);

CREATE TABLE IF NOT EXISTS canal_enviadas (
    id            {{PK_AUTO}},
    canal         TEXT NOT NULL,                  -- 'telegram' | 'trello'
    ref_mensagem  TEXT NOT NULL,                  -- o `message_id` do Telegram; o id do comentário do Trello
    origem        TEXT NOT NULL,                  -- 'aviso' | 'resposta' | 'ajuda' | 'previa' | 'resultado'
    fato          TEXT,                           -- a chave do fato do aviso ('approval:<id>', 'run:<id>:needs_input')
    aviso_id      INTEGER,                        -- `avisos_entregas.id` (sem FK)
    entrada_id    INTEGER,                        -- `canal_entradas.id` respondida (sem FK)
    enviada_em    TEXT NOT NULL,
    UNIQUE (canal, ref_mensagem)
);
