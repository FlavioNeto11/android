-- Anexos dos canais (item 28.24, fatia F1): o que o dono manda pelo Telegram (foto, PDF, texto) e o que a Central manda
-- de volta. O arquivo NÃO mora no banco: fica em `data/anexos/<2 primeiros do sha256>/<sha256>.<ext>` (fora do Git), com
-- a retenção do 28.16; aqui moram só os metadados e o estado.
--
-- Uma linha por anexo recebido ou enviado:
-- - `direcao = 'entrada'`: veio do dono. `entrada_id` aponta para a `canal_entradas` da mensagem (sem FK, como a 085);
-- - `direcao = 'saida'`: a Central mandou. `entrada_id` é NULL (ou a mensagem respondida, quando há);
-- - `estado = 'guardado'`: o arquivo existe em data/anexos. `recusado`: nada foi guardado e `motivo_recusa` diz por quê
--   (em português simples, o mesmo que o dono leu); o sha256 e o mime podem ser NULL (recusado antes de baixar).
--   `apagado`: a faxina por retenção apagou o arquivo.
--   `pendente`: a mensagem foi gravada e o anexo AINDA não foi baixado (some em segundos). Se a Central cai nesse intervalo,
--   a linha sobra e a volta seguinte tenta baixar UMA vez; se não der, vira `recusado` e o dono é avisado. Só nesse estado
--   `ref_externa` (a referência opaca do canal, o `file_id` do Telegram) e `mime_declarado` (só se for um tipo da lista)
--   existem; ao resolver, os dois voltam a NULL.
--
-- O nome do arquivo NUNCA vem do remetente: só o sha256 e a extensão da lista de tipos aceitos nomeiam o que está no
-- disco. Dois anexos com o mesmo conteúdo são duas linhas e UM arquivo (deduplicação pelo sha256); a faxina só apaga o
-- arquivo quando nenhuma outra linha `guardado` ainda o usa.
--
-- Mesmo padrão da 085: sem FK e sem CHECK nos vocabulários (`canal`, `direcao`, `estado`, `mime` mudam sem migração;
-- quem confere é o domínio), tempo em texto ISO-8601 (ordena igual nos dois dialetos).
--
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS canal_anexos (
    id             {{PK_AUTO}},
    canal          TEXT NOT NULL,                 -- 'telegram' | 'trello'
    entrada_id     INTEGER,                       -- `canal_entradas.id` da mensagem (sem FK); NULL na saída sem resposta
    direcao        TEXT NOT NULL,                 -- 'entrada' | 'saida'
    sha256         TEXT,                          -- 64 hex minúsculos do conteúdo; NULL no recusado antes de baixar
    mime           TEXT,                          -- o mime DETECTADO pelo conteúdo (nunca só o declarado); NULL se recusado cedo
    bytes          INTEGER NOT NULL DEFAULT 0,    -- tamanho do conteúdo guardado (no recusado, o declarado, se houve)
    estado         TEXT NOT NULL,                 -- 'guardado' | 'recusado' | 'apagado'
    motivo_recusa  TEXT,                          -- por que foi recusado (curto, em português, sem conteúdo do arquivo)
    criado_em      TEXT NOT NULL,
    apagado_em     TEXT,
    ref_externa    TEXT,                          -- só no `pendente`: a referência opaca do canal para baixar
    mime_declarado TEXT                           -- só no `pendente`: o tipo que o remetente declarou, se da lista
);
-- Os anexos de uma mensagem (a leitura por entrada) e a faxina por prazo.
CREATE INDEX IF NOT EXISTS ix_canal_anexos_entrada ON canal_anexos(canal, entrada_id);
CREATE INDEX IF NOT EXISTS ix_canal_anexos_criado ON canal_anexos(canal, criado_em);
-- A faxina pergunta "alguma outra linha guardada ainda usa este arquivo?".
CREATE INDEX IF NOT EXISTS ix_canal_anexos_sha ON canal_anexos(sha256);
