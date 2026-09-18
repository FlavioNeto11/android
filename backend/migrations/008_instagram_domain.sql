-- Domínio de perfil do Instagram: identidade, credencial protegida, vínculo com aparelho e sessão.
-- Isolamento entre perfis é imposto por RESTRIÇÃO (chave estrangeira e unicidade), não só por índice.

-- Ciphertext das credenciais. Só o cofre escreve aqui; o domínio conhece apenas a referência.
CREATE TABLE secrets (
    ref         TEXT PRIMARY KEY,
    key_id      TEXT NOT NULL,          -- qual chave mestra cifrou (permite rotação futura)
    nonce       BLOB NOT NULL,
    ciphertext  BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Nesta fase a persona é só identidade e associação: editor, regras de comportamento, prévia e geração social
-- entram na fase de persona/memória.
CREATE TABLE personas (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    summary     TEXT,
    traits      TEXT NOT NULL DEFAULT '{}',   -- JSON, preenchido na fase de persona
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE instagram_profiles (
    id                TEXT PRIMARY KEY,
    username          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name      TEXT,
    first_name        TEXT,
    last_name         TEXT,
    birth_date        TEXT,
    email             TEXT,
    persona_id        TEXT REFERENCES personas(id) ON DELETE SET NULL,
    status            TEXT NOT NULL DEFAULT 'active',   -- active | blocked | disabled
    automation_policy TEXT NOT NULL DEFAULT '{}',       -- JSON, usado na fase de políticas
    metadata          TEXT NOT NULL DEFAULT '{}',
    last_verified_at  TEXT,
    last_activity_at  TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Um perfil tem no máximo uma credencial: a unicidade é da tabela, não da aplicação.
CREATE TABLE instagram_credentials (
    profile_id        TEXT PRIMARY KEY REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    login_identifier  TEXT NOT NULL,
    secret_ref        TEXT NOT NULL,
    key_id            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',   -- active | invalid (senha comprovadamente errada)
    failed_attempts   INTEGER NOT NULL DEFAULT 0,
    blocked_until     TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_used_at      TEXT
);

-- Um perfil ativo por aparelho e um aparelho ativo por perfil, garantidos por índice parcial único.
-- Linhas inativas ficam como auditoria do rebinding.
CREATE TABLE device_profile_bindings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    bound_at    TEXT NOT NULL,
    unbound_at  TEXT,
    reason      TEXT
);
CREATE UNIQUE INDEX idx_binding_profile_ativo ON device_profile_bindings(profile_id) WHERE active = 1;
CREATE UNIQUE INDEX idx_binding_device_ativo ON device_profile_bindings(instance_id) WHERE active = 1;

-- Sessão é CACHE do que se observou no aparelho, nunca a verdade.
CREATE TABLE instagram_sessions (
    profile_id        TEXT PRIMARY KEY REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    instance_id       TEXT,
    status            TEXT NOT NULL DEFAULT 'unknown',
    observed_username TEXT,
    verified_at       TEXT,
    detail            TEXT,
    updated_at        TEXT NOT NULL
);

-- Auditoria e métrica de autenticação. Nunca guarda credencial — só o que aconteceu.
CREATE TABLE authentication_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    outcome     TEXT,        -- session_ready | invalid_credential | auth_challenge | wrong_account | retryable | uncertain
    stage       TEXT,        -- até onde foi (útil na reconciliação após queda do backend)
    detail      TEXT
);
CREATE INDEX idx_auth_attempts_profile ON authentication_attempts(profile_id, started_at);
