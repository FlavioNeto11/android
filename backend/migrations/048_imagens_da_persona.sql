-- Imagens da persona (evolução 2, onda A): a galeria de fotos de uma pessoa, geradas por provedor de imagem,
-- enviadas pelo painel ou herdadas dos 8 avatares que estavam soltos em `data/avatars/<profile_id>.jpg`.
--
-- Por que uma tabela, e não só arquivos: cada imagem tem RECEITA (`spec`: identidade fixa + eixos sorteados por
-- semente), proveniência (provedor, modelo, pedido), custo, estado (a geração pode falhar ou ser recusada pelo
-- filtro do provedor) e a marca de principal — nada disso cabe num nome de arquivo. Os bytes ficam no storage
-- (`personas/<persona_id>/<image_id>.jpg`, o mesmo back-end dos avatares); `storage_key` diz onde.
--
-- `persona_id` referencia `instagram_profiles`: desde a 047 a persona É essa linha. Apagar a pessoa apaga a galeria
-- (os arquivos, o serviço apaga antes). Uma principal por pessoa, garantida por índice parcial.
--
-- A carga dos 8 avatares existentes NÃO é feita aqui: migração não vê disco nem bucket. É um passo de partida
-- idempotente do serviço de imagens (`AppState.start`): pessoa sem imagem + `avatars/<id>.jpg` no storage → linha
-- `imported_legacy` principal.
--
-- Compatível com SQLite e PostgreSQL: só TEXT/INTEGER/REAL e índices parciais (precedente da 008/009).
CREATE TABLE IF NOT EXISTS persona_images (
    id                  TEXT PRIMARY KEY,
    persona_id          TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    storage             TEXT NOT NULL DEFAULT 'disk',      -- back-end de storage em que os bytes estão
    storage_key         TEXT,                              -- chave da imagem servida (pós-processada); nulo até ficar pronta
    original_key        TEXT,                              -- o que o provedor devolveu, intacto (proveniência C2PA)
    spec                TEXT NOT NULL DEFAULT '{}',        -- JSON `PersonaImageSpec` (receita inteira); '{}' em upload/legado
    prompt_sha256       TEXT,
    provider            TEXT,                              -- simulated | openai | upload | legacy
    model               TEXT,
    seed                INTEGER NOT NULL DEFAULT 0,        -- 31 bits: cabe em INTEGER dos dois bancos
    provider_seed       TEXT,
    provider_request_id TEXT,
    width               INTEGER,
    height              INTEGER,
    bytes_sha256        TEXT,
    cost_usd            REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'ready',     -- pending | ready | failed | refused
    error               TEXT,
    source              TEXT NOT NULL,                     -- generated | upload | imported_legacy
    is_primary          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_persona_images_persona ON persona_images(persona_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_persona_images_primaria ON persona_images(persona_id) WHERE is_primary = 1;

-- Custo DECLARADO de uma chamada, em US$: imagem é cobrada por unidade, não por token. `NULL` nas linhas de texto
-- (o custo continua saindo de tokens × `ai.prices`); `planning/costs.py::spent_usd` soma `usd` quando existe.
ALTER TABLE ai_calls ADD COLUMN usd REAL;
