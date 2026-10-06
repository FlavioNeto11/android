-- Conhecimento da OPERAÇÃO na memória e nas observações do pedido (prova30 A1/A2, 06/10/2026).
--
-- A operação (`operacoes`, 124, da Jev) junta N agentes sobre um objetivo, e o que eles precisam saber em comum mora
-- onde a tarefa já guarda o que sabe: `pedido_memoria` (o fato consolidado) e `pedido_observacoes` (o que foi visto,
-- com fonte, sha256 e instante). Sem tabela nova de conhecimento: a linha é de um pedido OU de uma operação.
--
--   * `operacao_id`: sem chave estrangeira (como `runs.pedido_id` na 067): a 125 não depende da ordem em que a 124
--     chega, e a observação sobrevive a qualquer limpeza da operação. `pedido_id` deixa de ser obrigatório; o CHECK
--     exige exatamente um dos dois.
--   * Na memória, o que o dono pediu para todo fato: `origem` (quem afirmou: operador, ocorrência, leitura do alvo,
--     pesquisa externa), `confianca` (`confirmado` | `hipotese`), `evidencia` (JSON: ids das observações que sustentam
--     o fato) e `frescor_ate` (UTC; NULL = não vence). As linhas que já existem nascem `ocorrencia`/`confirmado`,
--     que é o que a regra "só fato confirmado entra" da 070 garantia.
--   * Unicidade por operação com índice único PARCIAL: `(operacao_id, chave)` na memória e `(operacao_id, alvo, nome)`
--     nas observações. É o segundo que faz a leitura do alvo ser gravada UMA vez por operação: o primeiro agente
--     insere, os outros batem no índice (`ON CONFLICT DO NOTHING`) e conferem o sha256.
--
-- SQLite: NOT NULL e CHECK não se alteram com ALTER; as duas tabelas são RECONSTRUÍDAS (molde da 076), com as colunas
-- nomeadas uma a uma, a FK de `pedido_id` com CASCADE e os índices da 070. Nada aponta para elas, então o DROP não
-- dispara cascata e não precisa de `@foreign_keys:off`. O `UNIQUE (pedido_id, chave)` continua: NULL não colide.
-- PostgreSQL: DROP NOT NULL e ADD COLUMN/CONSTRAINT na mesma transação.
--
-- Número 125 dado pela orquestradora (06/10). Sem BEGIN/COMMIT: o executor já abre a transação.

-- @dialect:sqlite
CREATE TABLE pedido_memoria_nova (
    id                  TEXT PRIMARY KEY,
    pedido_id           TEXT REFERENCES pedidos(id) ON DELETE CASCADE,
    operacao_id         TEXT,
    chave               TEXT NOT NULL,
    tipo                TEXT NOT NULL CHECK (tipo IN ('progresso', 'descoberta', 'decisao', 'pendencia', 'fonte')),
    valor               TEXT NOT NULL,
    versao              INTEGER NOT NULL DEFAULT 1 CHECK (versao >= 1),
    ocorrencia_id       TEXT,
    resolvida           INTEGER NOT NULL DEFAULT 0 CHECK (resolvida IN (0, 1)),
    atualizada_em       TEXT NOT NULL,
    origem              TEXT NOT NULL DEFAULT 'ocorrencia' CHECK (origem IN ('operador', 'ocorrencia', 'leitura', 'pesquisa')),
    confianca           TEXT NOT NULL DEFAULT 'confirmado' CHECK (confianca IN ('confirmado', 'hipotese')),
    evidencia           TEXT,
    frescor_ate         TEXT,
    CHECK ((pedido_id IS NULL) <> (operacao_id IS NULL)),
    UNIQUE (pedido_id, chave)
);
INSERT INTO pedido_memoria_nova (id, pedido_id, chave, tipo, valor, versao, ocorrencia_id, resolvida, atualizada_em)
SELECT id, pedido_id, chave, tipo, valor, versao, ocorrencia_id, resolvida, atualizada_em FROM pedido_memoria;
DROP TABLE pedido_memoria;
ALTER TABLE pedido_memoria_nova RENAME TO pedido_memoria;
CREATE INDEX ix_pedido_memoria_pedido ON pedido_memoria(pedido_id, tipo);

CREATE TABLE pedido_observacoes_nova (
    id              TEXT PRIMARY KEY,
    pedido_id       TEXT REFERENCES pedidos(id) ON DELETE CASCADE,
    operacao_id     TEXT,
    pedido_versao   INTEGER NOT NULL DEFAULT 1,
    ocorrencia_id   TEXT NOT NULL,
    run_id          TEXT,
    step_id         TEXT,
    alvo            TEXT NOT NULL DEFAULT '',
    nome            TEXT NOT NULL,
    tipo            TEXT NOT NULL DEFAULT 'text' CHECK (tipo IN ('text', 'number', 'url', 'list', 'resultado')),
    situacao        TEXT NOT NULL CHECK (situacao IN ('observado', 'incerto', 'ausente')),
    valor           TEXT,
    fonte           TEXT NOT NULL DEFAULT '',
    trecho          TEXT,
    sha256          TEXT,
    capturado_em    TEXT NOT NULL,
    CHECK ((pedido_id IS NULL) <> (operacao_id IS NULL)),
    UNIQUE (ocorrencia_id, alvo, nome)
);
INSERT INTO pedido_observacoes_nova (id, pedido_id, pedido_versao, ocorrencia_id, run_id, step_id, alvo, nome, tipo,
                                     situacao, valor, fonte, trecho, sha256, capturado_em)
SELECT id, pedido_id, pedido_versao, ocorrencia_id, run_id, step_id, alvo, nome, tipo, situacao, valor, fonte, trecho,
       sha256, capturado_em
  FROM pedido_observacoes;
DROP TABLE pedido_observacoes;
ALTER TABLE pedido_observacoes_nova RENAME TO pedido_observacoes;
CREATE INDEX ix_pedido_observacoes_pedido ON pedido_observacoes(pedido_id, capturado_em);
-- @dialect:end

-- @dialect:postgres
ALTER TABLE pedido_memoria ALTER COLUMN pedido_id DROP NOT NULL;
ALTER TABLE pedido_memoria ADD COLUMN operacao_id TEXT;
ALTER TABLE pedido_memoria ADD COLUMN origem TEXT NOT NULL DEFAULT 'ocorrencia'
    CHECK (origem IN ('operador', 'ocorrencia', 'leitura', 'pesquisa'));
ALTER TABLE pedido_memoria ADD COLUMN confianca TEXT NOT NULL DEFAULT 'confirmado'
    CHECK (confianca IN ('confirmado', 'hipotese'));
ALTER TABLE pedido_memoria ADD COLUMN evidencia TEXT;
ALTER TABLE pedido_memoria ADD COLUMN frescor_ate TEXT;
ALTER TABLE pedido_memoria ADD CONSTRAINT pedido_memoria_dono_check CHECK ((pedido_id IS NULL) <> (operacao_id IS NULL));

ALTER TABLE pedido_observacoes ALTER COLUMN pedido_id DROP NOT NULL;
ALTER TABLE pedido_observacoes ADD COLUMN operacao_id TEXT;
ALTER TABLE pedido_observacoes ADD CONSTRAINT pedido_observacoes_dono_check
    CHECK ((pedido_id IS NULL) <> (operacao_id IS NULL));
-- @dialect:end

CREATE UNIQUE INDEX ux_pedido_memoria_operacao ON pedido_memoria(operacao_id, chave) WHERE operacao_id IS NOT NULL;
CREATE UNIQUE INDEX ux_pedido_observacoes_operacao ON pedido_observacoes(operacao_id, alvo, nome)
    WHERE operacao_id IS NOT NULL;
