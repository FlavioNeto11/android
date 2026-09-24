-- Item 12.1 do plano-100 (pedido do dono, 24/09): o perfil deixa de ser "uma conta do Instagram" e vira uma
-- IDENTIDADE com contas em vários aplicativos (Instagram, Outlook, TikTok, Facebook…), com o conhecimento separado
-- por app e comandos que atravessam mais de um app.
--
-- O que NÃO muda, de propósito:
-- - `instagram_profiles` continua sendo a tabela do perfil. Renomear quebraria ~30 pontos e o pacote do agente do
--   worker; o nome "Perfil" (identidade) vive na API e na tela.
-- - Um aparelho continua hospedando UMA identidade (`device_profile_bindings`): a identidade tem vários apps
--   instalados naquele aparelho. É o telefone de uma pessoa, com as contas dela.
-- - `instagram_credentials`/`instagram_sessions` continuam sendo o armazenamento do provedor do Instagram (login
--   determinístico). A conta Instagram de cada perfil ganha a sua linha em `profile_accounts`, apontando para lá.
--
-- Compatível com SQLite e PostgreSQL: só tipos TEXT/INTEGER e índices.

-- Uma conta por (perfil, app). `handle` é o nome de usuário/e-mail da conta NAQUELE app.
CREATE TABLE IF NOT EXISTS profile_accounts (
    id                 TEXT PRIMARY KEY,
    profile_id         TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    app_id             TEXT NOT NULL,
    handle             TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'active',        -- active | disabled
    -- Sessão NESTE app. Para o Instagram, a verdade continua em `instagram_sessions` (provedor determinístico);
    -- para os demais, é o operador quem marca (assumiu o aparelho e entrou) ou a IA que observou a conta na tela.
    session_status     TEXT NOT NULL DEFAULT 'unknown',       -- unknown | session_ready | logged_out | needs_person
    session_detail     TEXT,
    session_verified_at TEXT,
    notes              TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_profile_accounts_app ON profile_accounts(profile_id, app_id);
CREATE INDEX IF NOT EXISTS ix_profile_accounts_app ON profile_accounts(app_id);

-- Senha de conta de app que NÃO é o Instagram: mesmo cofre (`secret_ref`), nunca o texto. O Instagram segue em
-- `instagram_credentials`, que o autenticador determinístico lê.
CREATE TABLE IF NOT EXISTS account_credentials (
    account_id        TEXT PRIMARY KEY REFERENCES profile_accounts(id) ON DELETE CASCADE,
    login_identifier  TEXT NOT NULL,
    secret_ref        TEXT NOT NULL,
    key_id            TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- A conta Instagram que cada perfil já tinha vira a primeira conta dele. Só quando o app está registrado.
INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, session_status, session_detail,
                             session_verified_at, created_at, updated_at)
SELECT 'acc-' || p.id, p.id, a.id, p.username, 'active', COALESCE(s.status, 'unknown'), s.detail, s.verified_at,
       p.created_at, p.updated_at
  FROM instagram_profiles p
  JOIN apps a ON a.package = 'com.instagram.android'
  LEFT JOIN instagram_sessions s ON s.profile_id = p.id;

-- Conhecimento POR APP. `NULL` = fato geral da identidade (vale em qualquer app). O que existia era Instagram.
ALTER TABLE memory_items ADD COLUMN app_id TEXT;
ALTER TABLE social_interactions ADD COLUMN app_id TEXT;
ALTER TABLE pending_approvals ADD COLUMN app_id TEXT;
UPDATE memory_items SET app_id = (SELECT id FROM apps WHERE package = 'com.instagram.android');
UPDATE social_interactions SET app_id = (SELECT id FROM apps WHERE package = 'com.instagram.android');
UPDATE pending_approvals SET app_id = (SELECT id FROM apps WHERE package = 'com.instagram.android');
CREATE INDEX IF NOT EXISTS ix_memory_items_app ON memory_items(profile_id, app_id);
CREATE INDEX IF NOT EXISTS ix_social_interactions_app ON social_interactions(profile_id, app_id);

-- Cada etapa diz o app em que roda: um comando pode atravessar apps. `NULL` = o app do plano.
ALTER TABLE steps ADD COLUMN app_id TEXT;
-- Os apps que uma execução tocou (JSON), para a visão por app sem abrir o plano de cada uma.
ALTER TABLE runs ADD COLUMN app_ids TEXT;
