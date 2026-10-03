-- Os pedidos de validação automática do curador (item 30.31, Fase 30; desenho aprovado pela orquestradora em 03/10).
--
-- Quando o parecer do curador é `pedir_evidencia` e o que falta pode ser produzido por uma execução (execução real,
-- outro aparelho, a versão viva do app, a sombra), o sistema não devolve a validação ao dono: grava aqui um PEDIDO, e
-- um despachante roda a execução de validação (o comando de origem, com os parâmetros dele, noutro aparelho ocioso).
-- A evidência entra pelos caminhos de sempre (a sombra do fluxo, os contadores da receita); quando ela chega, o pedido
-- fecha e o curador revê o item com o gatilho `evidencia_chegou`.
--
-- O pedido não decide nada: quem transiciona continua sendo o ciclo do livro (classe A pela regra D1; B vai ao lote do
-- dono; C item a item). Aqui fica só a fila e o registro do que se rodou, quanto custou e por que não se rodou.
--
-- Mesmo padrão da 069:
-- - sem CHECK nos vocabulários (`grupo`, `estado`, `motivo` mudam sem migração; quem confere é o domínio);
-- - SEM chave estrangeira: a revisão e a execução podem ser purgadas ou de outra tabela; o pedido fica;
-- - filtro de tempo só por texto ISO-8601 (ordena igual nos dois dialetos);
-- - `usd` em REAL como `learning_reviews.usd`.
--
-- Um pedido VIVO por item (índice único parcial): o curador que pede de novo o mesmo item, numa volta seguinte ou por
-- corrida entre réplicas, perde no INSERT e não duplica a execução.
--
-- Compatível com SQLite e PostgreSQL (índice parcial existe nos dois). Sem BEGIN/COMMIT: o executor já abre a
-- transação.

CREATE TABLE IF NOT EXISTS learning_validations (
    id                TEXT PRIMARY KEY,               -- 'lv-<token>'
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    review_id         TEXT NOT NULL,                  -- `learning_reviews.id` do parecer que pediu (sem FK)
    item_ref          TEXT NOT NULL,                  -- 'receita:<id>' ou 'fluxo:<id>'
    item_kind         TEXT NOT NULL,
    scope_app         TEXT NOT NULL DEFAULT '',
    grupo             TEXT NOT NULL,                  -- 'qa' | 'leitura' | 'efeito_real' (o que a execução pode fazer)
    falta             TEXT NOT NULL DEFAULT '[]',     -- JSON: só os rótulos de `Falta` que uma execução produz
    run_origem        TEXT,                           -- a execução que ensinou o item: de onde vêm o comando e os parâmetros
    comando           TEXT NOT NULL DEFAULT '',       -- o comando de origem, como foi pedido (nada escrito pela IA)
    aparelho_excluido TEXT,                           -- o aparelho de origem: a validação roda noutro
    estado            TEXT NOT NULL DEFAULT 'pendente',  -- 'pendente' | 'rodando' | 'feita' | 'recusada' | 'expirada'
    motivo            TEXT,                           -- por que recusou, expirou ou não provou (vocabulário do domínio)
    run_id            TEXT,                           -- a execução de validação (sem FK)
    aparelho          TEXT,                           -- onde ela rodou
    usd               REAL NOT NULL DEFAULT 0,        -- o custo medido da execução de validação
    expira_em         TEXT NOT NULL,
    feito_em          TEXT,
    revisao_nova_id   TEXT                            -- a revisão `evidencia_chegou` que a evidência disparou (sem FK)
);
-- Um pedido vivo por item: o segundo pedido perde no INSERT.
CREATE UNIQUE INDEX IF NOT EXISTS ux_learning_validations_vivo ON learning_validations(item_ref)
    WHERE estado IN ('pendente', 'rodando');
-- A fila do despachante (o mais antigo primeiro) e o orçamento da janela (soma de `usd` por `created_at`).
CREATE INDEX IF NOT EXISTS ix_learning_validations_fila ON learning_validations(estado, created_at);
-- A execução de validação pelo id (o digest fecha o pedido quando ela assenta).
CREATE INDEX IF NOT EXISTS ix_learning_validations_run ON learning_validations(run_id);
