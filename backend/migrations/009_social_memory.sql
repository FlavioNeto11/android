-- Histórico social, memória com busca textual, relacionamento e resumo de conversa.
-- Isolamento entre perfis continua imposto por RESTRIÇÃO: toda tabela referencia o perfil e cai junto com ele.
-- Nada aqui guarda credencial: a senha não tem caminho até o histórico, a memória ou o contexto do modelo.

-- A persona deixa de ser só identidade: ganha o texto que vai ao modelo; os traços do §9 ficam em `traits` (JSON).
ALTER TABLE personas ADD COLUMN persona_prompt TEXT NOT NULL DEFAULT '';

-- Persona pertence a UM perfil. Sem isto, editar a persona do Lucas mudaria a da Mariana — vazamento por descuido.
CREATE UNIQUE INDEX ux_profiles_persona ON instagram_profiles(persona_id) WHERE persona_id IS NOT NULL;

-- ---------------------------------------------------------------- histórico
-- `seq` dá ordem cronológica confiável; `id` é o identificador estável que sai na API.
CREATE TABLE social_interactions (
    seq              {{PK_AUTO}},
    id               TEXT NOT NULL UNIQUE,
    profile_id       TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    instance_id      TEXT,
    run_id           TEXT,
    objective_id     TEXT,
    step_id          TEXT,
    occurred_at      TEXT NOT NULL,
    type             TEXT NOT NULL,              -- dm_sent, dm_received, comment_replied, post_liked, followed…
    direction        TEXT NOT NULL,              -- inbound | outbound | none
    counterparty     TEXT,                       -- @username da contraparte, normalizado em minúsculas
    thread_key       TEXT,                       -- conversa/post a que a interação pertence
    incoming_content TEXT,                       -- o que veio do app: DADO, nunca instrução
    outgoing_content TEXT,                       -- o que o sistema gerou/enviou
    target           TEXT,
    context          TEXT NOT NULL DEFAULT '{}', -- JSON
    status           TEXT NOT NULL,              -- pending | confirmed | failed | uncertain | cancelled
    evidence         TEXT,
    metadata         TEXT NOT NULL DEFAULT '{}', -- JSON (guarda os candidatos a memória até a confirmação)
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX ix_interactions_profile ON social_interactions(profile_id, seq DESC);
CREATE INDEX ix_interactions_counterparty ON social_interactions(profile_id, counterparty, seq DESC);
CREATE INDEX ix_interactions_thread ON social_interactions(profile_id, thread_key, seq DESC);

-- ---------------------------------------------------------------- memória
-- `seq` inteiro é obrigatório: é o rowid que o índice de texto completo referencia (id TEXT não serve).
CREATE TABLE memory_items (
    seq            {{PK_AUTO}},
    id             TEXT NOT NULL UNIQUE,
    profile_id     TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    subject        TEXT NOT NULL,               -- de quem/do que o fato fala (@username, ou um tema)
    content        TEXT NOT NULL,
    source         TEXT NOT NULL,               -- interaction | operator | system
    interaction_id TEXT REFERENCES social_interactions(id) ON DELETE SET NULL,
    importance     REAL NOT NULL DEFAULT 0.5,
    confidence     REAL NOT NULL DEFAULT 0.5,
    occurrences    INTEGER NOT NULL DEFAULT 1,  -- quantas vezes o mesmo fato foi observado (dedup/merge)
    fingerprint    TEXT NOT NULL,               -- assunto + conteúdo normalizados: é o que faz o merge
    expires_at     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    last_used_at   TEXT
);
-- O mesmo fato não vira duas lembranças do mesmo perfil; perfis diferentes guardam o seu separadamente.
CREATE UNIQUE INDEX ux_memory_fingerprint ON memory_items(profile_id, fingerprint);
CREATE INDEX ix_memory_profile_subject ON memory_items(profile_id, subject);

-- Busca por relevância. O índice é compartilhado por todos os perfis: quem separa é o filtro por profile_id
-- na junção com memory_items — por isso o repositório nunca expõe uma consulta sem perfil.
--
-- Aqui a diferença entre os bancos é REAL e não dá para fingir: o SQLite tem FTS5 com `bm25()`, o PostgreSQL tem
-- `tsvector` com `ts_rank`. Marcar os dois blocos num arquivo só é melhor que manter duas migrações que divergem
-- com o tempo. Quem consulta (social/repository.search_memory) tem o ramo correspondente.

-- @dialect:sqlite
CREATE VIRTUAL TABLE memory_fts USING fts5(
    subject, content, content='memory_items', content_rowid='seq',
    tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER memory_items_ai AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_fts(rowid, subject, content) VALUES (new.seq, new.subject, new.content);
END;
CREATE TRIGGER memory_items_ad AFTER DELETE ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, subject, content) VALUES ('delete', old.seq, old.subject, old.content);
END;
CREATE TRIGGER memory_items_au AFTER UPDATE ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, subject, content) VALUES ('delete', old.seq, old.subject, old.content);
    INSERT INTO memory_fts(rowid, subject, content) VALUES (new.seq, new.subject, new.content);
END;
-- @dialect:end

-- No PostgreSQL, coluna GERADA em vez de tabela espelho com três gatilhos: o banco a mantém sozinho, e some a
-- classe inteira de defeito "o índice ficou fora de sincronia com a tabela".
-- @dialect:postgres
ALTER TABLE memory_items ADD COLUMN busca tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', coalesce(subject,'') || ' ' || coalesce(content,''))) STORED;
CREATE INDEX ix_memory_busca ON memory_items USING GIN (busca);
-- @dialect:end

-- ---------------------------------------------------------------- relacionamento e conversa
CREATE TABLE relationship_summaries (
    profile_id          TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    counterparty        TEXT NOT NULL,
    summary             TEXT NOT NULL DEFAULT '',
    tone                TEXT,                    -- como falar com esta pessoa (vem da persona + histórico)
    interactions        INTEGER NOT NULL DEFAULT 0,
    first_interaction_at TEXT,
    last_interaction_at TEXT,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (profile_id, counterparty)
);

CREATE TABLE thread_summaries (
    profile_id      TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    thread_key      TEXT NOT NULL,
    counterparty    TEXT,
    summary         TEXT NOT NULL DEFAULT '',
    messages        INTEGER NOT NULL DEFAULT 0,
    last_message_at TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (profile_id, thread_key)
);
