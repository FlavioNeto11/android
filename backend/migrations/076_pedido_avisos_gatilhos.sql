-- Avisos dos gatilhos de evento e de condição do pedido persistente (item 28.8; `docs/design/pedidos-laco.md` §14.5).
--
-- O CHECK de `pedido_avisos.tipo` (072) é fechado: o vocabulário é do contrato e não muda sem migração. Esta acrescenta
-- dois tipos, ambos `warn` e sem `requer_pessoa` (vão à caixa do dono, não às Pendências):
--
--     eventos_perdidos   = a retenção apagou eventos que um gatilho `evento` ainda não tinha lido; nada foi disparado
--                          por eles (chave `eventos_perdidos:<gatilho>:<ate_id>`);
--     condicao_atendida  = a condição de um gatilho `condicao` passou de falsa a verdadeira numa observação
--                          (chave `condicao_atendida:<gatilho>:<ocorrencia>`).
--
-- SQLite: CHECK de coluna não se altera com ALTER; a tabela é RECONSTRUÍDA (molde da 047), com as colunas nomeadas uma a
-- uma, a FK para `pedidos` com CASCADE e o índice `ix_pedido_avisos_pedido`. Nada aponta para `pedido_avisos`, então o
-- DROP não dispara cascata nenhuma e não precisa de `@foreign_keys:off`.
-- PostgreSQL: o CHECK de coluna se chama `pedido_avisos_tipo_check` (nome padrão `<tabela>_<coluna>_check`); troca-se
-- com DROP/ADD na mesma transação.
--
-- Número 076 dado pela coordenação (02/10): 074 e 075 são de outras frentes e entram antes. Sem BEGIN/COMMIT: o executor
-- já abre a transação.

-- @dialect:sqlite
CREATE TABLE pedido_avisos_novo (
    id             TEXT PRIMARY KEY,
    pedido_id      TEXT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    ocorrencia_id  TEXT,
    tipo           TEXT NOT NULL CHECK (tipo IN ('pausa_automatica', 'orcamento_80', 'orcamento_esgotado',
                                                 'ocorrencia_perdida', 'relatorio_pronto', 'encerramento',
                                                 'aprovacao_pendente', 'pergunta', 'ocorrencia_incerta',
                                                 'eventos_perdidos', 'condicao_atendida')),
    nivel          TEXT NOT NULL CHECK (nivel IN ('info', 'warn', 'error')),
    mensagem       TEXT NOT NULL,
    dados          TEXT NOT NULL DEFAULT '{}',
    requer_pessoa  INTEGER NOT NULL DEFAULT 0 CHECK (requer_pessoa IN (0, 1)),
    chave_dedupe   TEXT NOT NULL UNIQUE,
    criado_em      TEXT NOT NULL,
    lido_em        TEXT
);
INSERT INTO pedido_avisos_novo (id, pedido_id, ocorrencia_id, tipo, nivel, mensagem, dados, requer_pessoa, chave_dedupe,
                                criado_em, lido_em)
SELECT id, pedido_id, ocorrencia_id, tipo, nivel, mensagem, dados, requer_pessoa, chave_dedupe, criado_em, lido_em
  FROM pedido_avisos;
DROP TABLE pedido_avisos;
ALTER TABLE pedido_avisos_novo RENAME TO pedido_avisos;
CREATE INDEX ix_pedido_avisos_pedido ON pedido_avisos(pedido_id, lido_em);
-- @dialect:end

-- @dialect:postgres
ALTER TABLE pedido_avisos DROP CONSTRAINT pedido_avisos_tipo_check;
ALTER TABLE pedido_avisos ADD CONSTRAINT pedido_avisos_tipo_check
    CHECK (tipo IN ('pausa_automatica', 'orcamento_80', 'orcamento_esgotado', 'ocorrencia_perdida', 'relatorio_pronto',
                    'encerramento', 'aprovacao_pendente', 'pergunta', 'ocorrencia_incerta', 'eventos_perdidos',
                    'condicao_atendida'));
-- @dialect:end
