-- Trilha auditável das revisões do curador por IA (item 30.9, Fase 30; design `docs/design/aprendizado-vivo.md` §8.5).
--
-- Uma linha por revisão que a IA fez de um item do livro de aprendizado: o dossiê de fatos que ela viu, o prompt
-- (só a versão), quem respondeu, o custo, o parecer como veio, o que o sistema ou a pessoa decidiu de fato e, depois
-- de 14 e 30 dias, o que aconteceu com o item. É registro de AUDITORIA, não segunda verdade de conhecimento: o
-- parecer nunca transiciona nada sozinho; quem transiciona grava em `learning_transitions` (a 055) e aqui fica só o
-- elo (`transicao_id`).
--
-- Por que tabela própria: `ai_calls` é purgada pela retenção e a auditoria não pode sumir. Esta tabela NUNCA é
-- purgada, como a trilha (`learning_transitions`) e o backlog; a retenção do aprendizado apaga por lista explícita
-- de tabelas e esta não está nela.
--
-- O índice único (item_ref, dossie_hash) é a salvaguarda do orçamento (§8.7): o mesmo item com o mesmo dossiê não é
-- revisado duas vezes, nem por corrida entre duas réplicas nem por laço que repete o disparo. Dossiê novo, hash novo.
--
-- Mesmo padrão da 055:
-- - sem CHECK nos vocabulários (`gatilho`, `validade`, `classe_de_risco`, `politica`, `decisao_final`,
--   `resultado_posterior` mudam sem migração; quem confere é o domínio);
-- - SEM chave estrangeira: o item pode ser de outra tabela (`receita:<id>`, `fluxo:<id>`, `fk-<grupo>` do backlog),
--   e a execução que o motivou é purgada enquanto a revisão fica;
-- - filtro de tempo só por texto ISO-8601 (ordena igual nos dois dialetos);
-- - escopo em TEXT NOT NULL DEFAULT '' ('' = qualquer);
-- - booleano em INTEGER 0/1; `usd` em REAL como `learning_daily.usd`.
--
-- O número é PROVISÓRIO (069, o próximo da main em 02/10); a coordenação confirma antes do merge e quem entrar
-- depois renumera para ficar acima de todas da main.
--
-- Compatível com SQLite e PostgreSQL: só tipos comuns. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS learning_reviews (
    id                  TEXT PRIMARY KEY,               -- 'lr-<token>'
    created_at          TEXT NOT NULL,
    item_ref            TEXT NOT NULL,                  -- 'li-...', 'receita:<id>', 'fluxo:<id>' ou 'fk-...' (grupo do backlog)
    item_kind           TEXT NOT NULL,
    scope_app           TEXT NOT NULL DEFAULT '',
    gatilho             TEXT NOT NULL,                  -- vocabulário fechado do §8.6
    dossie_hash         TEXT NOT NULL,                  -- sha256 do dossiê canônico: com o item, a chave da salvaguarda
    dossie              TEXT NOT NULL DEFAULT '{}',     -- JSON dos fatos usados, sem segredo e sem texto de tela (com limite)
    template_id         TEXT NOT NULL,                  -- o prompt é arquivo versionado no repo; grava-se a versão, não o texto
    template_versao     TEXT NOT NULL,
    provedor            TEXT NOT NULL DEFAULT '',       -- o que respondeu de fato (vazio quando a revisão foi recusada)
    modelo              TEXT NOT NULL DEFAULT '',
    simulated           INTEGER NOT NULL DEFAULT 0,     -- 1 = provedor falso; nunca conta como prova real
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    usd                 REAL NOT NULL DEFAULT 0,        -- custo próprio, mesma conta de `ai.prices`; base da média e de C_W (§8.7)
    ms                  INTEGER NOT NULL DEFAULT 0,
    saida               TEXT,                           -- JSON do §8.3 como veio, já validado; NULO quando inválida ou recusada
    validade            TEXT NOT NULL DEFAULT 'ok',     -- 'ok' | 'invalida:<motivo>' | 'recusada:<motivo>' (ex.: 'recusada:custo')
    classe_de_risco     TEXT,                           -- faixa A/B/C do §8.4; a faixa A nunca chega aqui
    politica            TEXT,                           -- a regra aplicada à classe
    decisao_final       TEXT,                           -- o que o sistema ou a pessoa fez de fato; NULO = ainda sem decisão
    decidido_por        TEXT,                           -- 'sistema' ou a sessão do painel
    transicao_id        INTEGER,                        -- `learning_transitions.id`, quando a decisão virou transição (sem FK)
    override            INTEGER NOT NULL DEFAULT 0,     -- 1 = a pessoa decidiu diferente da sugestão
    override_motivo     TEXT,
    resultado_posterior TEXT,                           -- preenchido pela curadoria após 14 e 30 dias
    resultado_em        TEXT
);
-- Uma revisão por (item, dossiê): a salvaguarda do orçamento (§8.7). A corrida perde no INSERT, não no gasto.
CREATE UNIQUE INDEX IF NOT EXISTS ux_learning_reviews_item_dossie ON learning_reviews(item_ref, dossie_hash);
-- A janela móvel do orçamento (soma de `usd` e mediana por revisão) e a leitura mais recente primeiro.
CREATE INDEX IF NOT EXISTS ix_learning_reviews_criada ON learning_reviews(created_at);
-- A visão por app.
CREATE INDEX IF NOT EXISTS ix_learning_reviews_app ON learning_reviews(scope_app, created_at);
