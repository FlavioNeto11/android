-- A conversa do dono com a Central pelo Telegram (item 28.15, ADR-071; desenho aprovado pela orquestradora em 03/10).
--
-- O 28.11 só mandava (aviso). Agora o mesmo bot também RECEBE: um laço de long-poll (`getUpdates` com offset), no líder
-- da trava `avisos`, é o único consumidor do bot. Não há webhook nem rota HTTP de entrada.
--
-- Duas tabelas:
-- - `telegram_mensagens` guarda uma linha por update recebida e é a fonte do offset (`MAX(update_id) + 1`). A update é
--   gravada ANTES de o offset avançar, então reiniciar não repete nem perde; o `UNIQUE (update_id)` faz a releitura
--   cair no `ON CONFLICT DO NOTHING`.
-- - `telegram_enviadas` guarda o `message_id` de tudo o que a Central mandou (aviso, resposta, ajuda). É o que liga a
--   resposta (reply) da pessoa ao fato do aviso. Reply a uma mensagem do bot que NÃO está aqui é da orquestradora:
--   fica guardada e não é executada.
--
-- O que NÃO se guarda:
-- - o chat_id: a v1 aceita só o `TELEGRAM_CHAT_ID` do `.env`, e `do_chat` diz se a update veio dele;
-- - o texto de update de outro chat (`texto` NULL; só conta);
-- - o texto que parece credencial ou código (`estado = 'recusada'`, `texto` NULL, só o `tamanho`).
--
-- Mesmo padrão da 082:
-- - sem CHECK nos vocabulários (`tipo`, `intencao`, `estado`, `destino`, `origem` mudam sem migração; quem confere é o
--   domínio);
-- - SEM chave estrangeira: a execução, a aprovação e o aviso podem ser purgados; o registro fica;
-- - tempo em texto ISO-8601 (ordena igual nos dois dialetos).
-- Os ids do Telegram vão em BIGINT: `update_id` e `message_id` são inteiros da Bot API sem teto de 32 bits garantido.
--
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS telegram_mensagens (
    id            {{PK_AUTO}},
    update_id     BIGINT NOT NULL UNIQUE,
    tipo          TEXT NOT NULL,                  -- 'mensagem' | 'botao' (callback_query do teclado inline)
    do_chat       INTEGER NOT NULL DEFAULT 0,     -- 1 = o chat configurado; 0 = outro (ignorado, sem texto)
    message_id    BIGINT,                         -- a mensagem da pessoa (ou a do bot que levou o botão)
    responde_a    BIGINT,                         -- `reply_to_message.message_id`, quando é resposta
    texto         TEXT,                           -- NULL em outro chat e na recusa por credencial
    tamanho       INTEGER NOT NULL DEFAULT 0,     -- caracteres recebidos (o que fica da mensagem recusada)
    intencao      TEXT,                           -- 'status' | 'pendencias' | 'aprovar' | 'vetar' | 'responder' | 'para' | ...
    destino       TEXT,                           -- 'central' | 'orquestradora'
    estado        TEXT NOT NULL DEFAULT 'recebida',  -- 'recebida' | 'ignorada' | 'recusada' | 'limitada' | 'orquestradora'
                                                     -- | 'pergunta' | 'feita' | 'cancelada' | 'falhou'
    alvo          TEXT,                           -- o fato sobre o qual age: 'approval:<id>', 'run:<id>'
    previa        TEXT,                           -- JSON da prévia dos alvos que espera o Executar/Cancelar
    run_id        TEXT,                           -- a execução criada por esta mensagem (sem FK)
    resposta      TEXT,                           -- o que a Central respondeu (curto, redigido)
    erro          TEXT,                           -- por que falhou (curto, redigido)
    recebida_em   TEXT NOT NULL,
    tratada_em    TEXT,
    resultado_em  TEXT                            -- quando o desfecho da execução foi contado na conversa
);
-- A janela do limite de taxa e a leitura da orquestradora (as mais novas primeiro).
CREATE INDEX IF NOT EXISTS ix_telegram_mensagens_recebida ON telegram_mensagens(recebida_em);
-- O desfecho que ainda falta contar na conversa.
CREATE INDEX IF NOT EXISTS ix_telegram_mensagens_resultado ON telegram_mensagens(run_id, resultado_em);

CREATE TABLE IF NOT EXISTS telegram_enviadas (
    id            {{PK_AUTO}},
    message_id    BIGINT NOT NULL UNIQUE,
    origem        TEXT NOT NULL,                  -- 'aviso' | 'resposta' | 'ajuda' | 'previa' | 'resultado'
    fato          TEXT,                           -- a chave do fato do aviso ('approval:<id>', 'run:<id>:needs_input')
    aviso_id      INTEGER,                        -- `avisos_entregas.id` (sem FK)
    mensagem_id   INTEGER,                        -- `telegram_mensagens.id` respondida (sem FK)
    enviada_em    TEXT NOT NULL
);
