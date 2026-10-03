-- Sombra da porta `DecisaoFechada` (Fase 31, item 31.5, ADR-069): o registro DURÁVEL de cada decisão por conjunto fechado
-- que o Jev (TypeSafe System One) tomou ou deixou de tomar, para medir concordância com o caminho de hoje ANTES de qualquer
-- `on`. `ai_calls` morre em `log_retention_days` (055); a sombra tem retenção própria (`ai.decisao_fechada.retencao_dias`,
-- padrão 180) e o agregado diário fica.
--
-- PRIVACIDADE (ADR-069 item 5; roteiro §3 item 5): a linha guarda SÓ ids opacos, categorias do vocabulário fechado e
-- números. NUNCA o estado enviado, nem o texto das opções, nem as instruções da pergunta. `escolha`, `decisao_real`,
-- `pergunta_id` e as chaves de `probabilidades` são ids (`opt:...`, `sim`/`nao`); quem grava confere o formato antes.
--
-- Mesmo padrão da 055:
-- - sem CHECK nos vocabulários (a lista muda sem migração; quem confere é o domínio: `planning/decisao_fechada/sombra.py`);
-- - SEM chave estrangeira: a execução é purgada e a prova fica (`run_id`, `step_id` e `ref` são texto solto);
-- - filtro de tempo só por texto ISO-8601 (ordena igual nos dois dialetos);
-- - agregado diário recalculado POR INTEIRO (DELETE do dia + INSERT), só para dia cujas linhas ainda estão todas aqui:
--   a purga leva dias inteiros, então um dia nunca é recalculado pela metade.
--
-- Custo: a chamada ao Jev é UMA para N perguntas (fan-out). `usd` e `tokens` ficam só na PRIMEIRA linha da chamada (as outras
-- levam 0), para nenhuma soma contar duas vezes. `chamada` agrupa as linhas de uma mesma chamada.
--
-- As migrações 071 a 073 estão em outros branches da Fase 31 e entram antes desta; a lacuna na numeração é esperada.
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE IF NOT EXISTS decisao_fechada_sombra (
    id              {{PK_AUTO}},
    ts              TEXT NOT NULL,                    -- ISO-8601 UTC
    chamada         TEXT NOT NULL,                    -- agrupa as perguntas de uma mesma chamada (fan-out)
    origem          TEXT NOT NULL,                    -- curador | intencao | desempate | apps
    classe          TEXT NOT NULL,                    -- C0..C3 (a classe do dado que SAIRIA; nunca o dado)
    modo            TEXT NOT NULL,                    -- shadow | on
    pergunta_id     TEXT NOT NULL,                    -- id da pergunta dentro da origem
    escolha         TEXT,                             -- id opaco escolhido; NULL em fallback ou em score
    probabilidades  TEXT,                             -- JSON {id_opaco: probabilidade}
    confianca       REAL,
    decisao_real    TEXT,                             -- id opaco do caminho ATUAL; preenchido depois (31.8/31.9)
    desfecho        TEXT,                             -- vocabulário fechado; preenchido depois
    usd             REAL NOT NULL DEFAULT 0,          -- declarado: só na primeira linha da chamada
    tokens          INTEGER NOT NULL DEFAULT 0,       -- idem
    ms              REAL NOT NULL DEFAULT 0,          -- latência da chamada, em todas as linhas dela
    fallback_reason TEXT,                             -- vocabulário fechado de `contrato.FALLBACKS`; NULL = respondeu
    run_id          TEXT,
    step_id         TEXT,
    ref             TEXT
);
CREATE INDEX IF NOT EXISTS ix_decisao_sombra_ts ON decisao_fechada_sombra(ts);
CREATE INDEX IF NOT EXISTS ix_decisao_sombra_ref ON decisao_fechada_sombra(ref);
CREATE INDEX IF NOT EXISTS ix_decisao_sombra_step ON decisao_fechada_sombra(step_id);

-- Agregado diário DURÁVEL por (dia, origem, pergunta). Um fallback nunca conta como acerto: ele só entra em `n` e `fallbacks`.
--   n                = linhas do dia (respondidas + fallbacks)
--   com_decisao_real = linhas com o caminho atual já casado (base da concordância)
--   concordancia     = linhas com escolha e decisão real IGUAIS
--   acima_do_limiar  = respondidas sem fallback e com escolha diferente de `nenhuma`
--   aceite_errado    = acima do limiar, com decisão real casada e DIFERENTE da escolha (a métrica que veta o `on`)
--   fallbacks        = linhas com `fallback_reason`
--   usd, tokens      = custo declarado do dia; ms_p95 = p95 da latência por linha
CREATE TABLE IF NOT EXISTS decisao_fechada_diario (
    day              TEXT NOT NULL,                   -- AAAA-MM-DD (UTC)
    origem           TEXT NOT NULL,
    pergunta_id      TEXT NOT NULL,
    n                INTEGER NOT NULL DEFAULT 0,
    com_decisao_real INTEGER NOT NULL DEFAULT 0,
    concordancia     INTEGER NOT NULL DEFAULT 0,
    acima_do_limiar  INTEGER NOT NULL DEFAULT 0,
    aceite_errado    INTEGER NOT NULL DEFAULT 0,
    fallbacks        INTEGER NOT NULL DEFAULT 0,
    usd              REAL NOT NULL DEFAULT 0,
    tokens           INTEGER NOT NULL DEFAULT 0,
    ms_p95           REAL,
    computed_at      TEXT NOT NULL,
    PRIMARY KEY (day, origem, pergunta_id)
);
