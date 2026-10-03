-- O Trello do dono como espelho legível e canal de comandos (item 32.2, ADR-072; desenho em
-- docs/design/trello-integracao.md, §7 itens 3 e 4). Só o que é do Trello: as actions recebidas vão para a
-- `canal_entradas` da 085 (`canal = 'trello'`, `id_externo` = o id da action, `ordem` NULL) e as respostas da Central
-- para a `canal_enviadas` (`ref_mensagem` = o id do comentário). A 085 já é genérica por canal.
--
-- Duas tabelas:
-- - `trello_cartoes` liga um FATO da Central a um cartão. A `chave` é `<família>:<fato>` (`approval:<id>`, `run:<id>`,
--   `deploy:<commit>`, `custo:<dia>`...): o espelho é um reconciliador, e é por esta chave que ele sabe se o cartão já
--   existe, se o `hash` do conteúdo mudou (só então atualiza) e se o fato sumiu (comenta o desfecho e arquiva, `estado =
--   'arquivado'`). É também por ela que o comentário do dono num cartão vira o fato sobre o qual ele age.
--   `card_id` é único: um cartão do Trello nunca representa dois fatos.
-- - `trello_cursor` guarda, por quadro, a última action lida pela reconciliação (`GET /1/boards/{id}/actions?since=`).
--   Na 1ª subida o cursor começa na action mais nova, então o histórico do quadro não é tratado como comando.
--
-- Mesmo padrão da 082 e da 085:
-- - sem CHECK nos vocabulários (`estado`, `lista` mudam sem migração; quem confere é o domínio);
-- - SEM chave estrangeira: o fato (aprovação, execução) pode ser purgado, o cartão e o registro ficam;
-- - tempo em texto ISO-8601 (ordena igual nos dois dialetos);
-- - os ids do Trello (cartão, quadro, lista, action) são TEXT hexadecimais.
--
-- O segredo (chave, token, segredo do aplicativo) nunca vem para o banco: mora só no `.env`.
--
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS trello_cartoes (
    chave          TEXT PRIMARY KEY,               -- '<família>:<fato>', p. ex. 'approval:42', 'run:abc', 'deploy:51270b9c'
    card_id        TEXT NOT NULL,                  -- o id do cartão no Trello
    quadro         TEXT NOT NULL,                  -- o id do quadro (um dos `trello.quadros`)
    lista          TEXT,                           -- o id da lista em que a Central o pôs / o viu por último
    hash           TEXT,                           -- impressão do conteúdo desejado; igual = não há o que atualizar
    estado         TEXT NOT NULL,                  -- 'ativo' | 'arquivado'
    criado_em      TEXT NOT NULL,
    atualizado_em  TEXT NOT NULL
);
-- Um cartão, um fato; e é o caminho de volta: a action traz o `card_id`, e dele sai o fato.
CREATE UNIQUE INDEX IF NOT EXISTS ux_trello_cartoes_card ON trello_cartoes(card_id);
-- O reconciliador varre os ativos de um quadro.
CREATE INDEX IF NOT EXISTS ix_trello_cartoes_quadro ON trello_cartoes(quadro, estado);

CREATE TABLE IF NOT EXISTS trello_cursor (
    quadro         TEXT PRIMARY KEY,               -- o id do quadro
    ultima_action  TEXT,                           -- o id da última action lida (NULL antes da 1ª leitura)
    ultima_data    TEXT,                           -- a data dela (ISO-8601), o `since` da próxima leitura
    atualizado_em  TEXT NOT NULL
);
